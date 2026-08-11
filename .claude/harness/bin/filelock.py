"""A small advisory file lock for read-modify-write on harness state.

`goal.checkpoint` and `refine_log.apply_edit` both read a file, change it,
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
                age = time.time() - lock_path.stat().st_mtime
            except FileNotFoundError:
                continue                      # released between the two calls
            if age > STALE_AFTER:
                try:
                    lock_path.unlink()        # abandoned by a killed process
                except FileNotFoundError:
                    pass
                continue
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

    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        fd = None
        yield
    finally:
        if fd is not None:
            os.close(fd)
        try:
            lock_path.unlink()
        except FileNotFoundError:
            pass
