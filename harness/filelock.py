"""A small advisory file lock for read-modify-write on harness state.

`goal.checkpoint` and the state-mode step commands read a file, change it,
and write it back. Without a lock those are check-then-write races, and
they lose data rather than erroring: two terminals in one project, or a
`/loop` firing beside a live session, silently drop checkpoints and mint
colliding entry ids. That is ordinary usage for a harness whose entire
premise is surviving across sessions.

`os.open(..., O_CREAT | O_EXCL)` is atomic on POSIX and on Windows, needs
no third-party dependency, and works across processes rather than only
across threads.

Stale locks are reclaimed by age rather than by checking whether the
holder is alive: a pid check is wrong across containers and mount
namespaces, where a pid may be reused or invisible.

Reclaiming a stale lock is where the naive version collapsed rather than
degrading. A slow holder A is declared stale, B unlinks A's lock file and
creates its own — and then A finishes and unlinks the *path*, deleting
B's lock while B still believes it holds it. From there every waiter
walks straight in. Both the reclaim and the release therefore check
**inode identity**, not the path: a lock file is only ever removed by the
process whose open descriptor still refers to that exact inode. A holder
that overstays loses its lock, which is the documented consequence of
being stale; it can no longer take someone else's down with it.
"""

import errno
import os
import time
from contextlib import contextmanager
from pathlib import Path

STALE_AFTER = 60.0     # seconds; longer than any legitimate state write
POLL = 0.02


class LockTimeout(Exception):
    """The lock could not be acquired before the timeout elapsed."""


@contextmanager
def locked(target: Path, timeout: float = 10.0):
    """Hold an exclusive lock for `target` for the duration of the block.

    The lock file sits beside the target so it lands on the same
    filesystem, and is named after it so unrelated state files do not
    serialise against each other.
    """
    target = Path(target)
    lock_path = target.parent / f".{target.name}.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    deadline = time.monotonic() + timeout
    fd = None
    while True:
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            break
        except FileExistsError:
            try:
                st = lock_path.stat()
            except FileNotFoundError:
                continue                      # released between the two calls
            if time.time() - st.st_mtime > STALE_AFTER:
                _unlink_if_inode(lock_path, st.st_ino)
                # Falls through to the deadline check rather than looping
                # straight back. `_unlink_if_inode` swallows OSError, so on a
                # read-only or unwritable directory the reclaim never
                # succeeds and this branch used to spin at 100% CPU forever,
                # ignoring the timeout entirely — a hang, with no error, in
                # the module every other module's correctness rests on.
            if time.monotonic() > deadline:
                raise LockTimeout(
                    f"could not lock {target} within {timeout}s; another "
                    f"process is holding {lock_path}. If nothing else is "
                    f"running, delete that file."
                )
            time.sleep(POLL)
        except OSError as e:                  # read-only fs, permissions
            if e.errno in (errno.EROFS, errno.EACCES, errno.EPERM):
                yield                         # degrade to unlocked rather than fail
                return
            raise

    # The descriptor stays open for the whole critical section purely so
    # release can fstat it: it is the only handle on *our* inode that
    # survives someone else reclaiming the path.
    ino = os.fstat(fd).st_ino
    try:
        os.write(fd, str(os.getpid()).encode())
        yield
    finally:
        os.close(fd)
        _unlink_if_inode(lock_path, ino)


def _unlink_if_inode(lock_path: Path, ino: int) -> None:
    """Remove `lock_path` only while it still names inode `ino`.

    Between the stat that judged a lock stale (or the work that held it)
    and this unlink, another process may have reclaimed the path and
    created a *new* lock file there. Unlinking by name would delete that
    one — releasing a lock the caller does not hold, which is worse than
    never locking, because the new holder is still acting as if it does.
    """
    try:
        if lock_path.stat().st_ino == ino:
            lock_path.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        pass

def state_supported() -> bool:
    import sys
    return sys.platform == "darwin" or sys.platform.startswith("linux")

@contextmanager
def goal_locked(target: Path, timeout: float = 10.0):
    """Hold a process-owned goal lock without stale-file reclamation."""
    if not state_supported():
        with locked(target, timeout):
            yield
        return
    import fcntl
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.parent / f".{target.name}.flock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"could not lock {target} within {timeout}s")
                time.sleep(POLL)
        yield
    finally:
        try:
            if acquired:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
