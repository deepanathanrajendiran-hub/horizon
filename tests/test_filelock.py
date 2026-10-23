"""The lock's own failure modes.

Every other module's concurrency guarantee is this file's correctness. A
lock that silently stops locking is worse than no lock at all, because
its callers keep behaving as if it works.
"""

import os
import sys
import time
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / "harness"
sys.path.insert(0, str(BIN))

import filelock  # noqa: E402
import subprocess


def _lock_path(target: Path) -> Path:
    return target.parent / f".{target.name}.lock"


def test_a_stale_lock_is_reclaimed(tmp_path):
    target = tmp_path / "state.json"
    lock = _lock_path(target)
    lock.write_text("99999")
    old = time.time() - filelock.STALE_AFTER - 5
    os.utime(lock, (old, old))

    with filelock.locked(target, timeout=1):
        assert lock.exists()
    assert not lock.exists()


def test_a_live_lock_makes_a_second_holder_time_out(tmp_path):
    target = tmp_path / "state.json"
    with filelock.locked(target):
        with pytest.raises(filelock.LockTimeout):
            with filelock.locked(target, timeout=0.1):
                pass


def test_a_reclaimed_holder_does_not_release_the_new_holder(tmp_path):
    """The collapse mode: A is declared stale, B takes the lock, and then A
    finishes and unlinks the *path* — deleting B's lock while B is still
    inside its critical section. From there every waiter walks straight in,
    and B never learns it lost anything.
    """
    target = tmp_path / "state.json"
    lock = _lock_path(target)

    a = filelock.locked(target)
    a.__enter__()
    old = time.time() - filelock.STALE_AFTER - 5
    os.utime(lock, (old, old))                      # A now looks abandoned

    b = filelock.locked(target, timeout=1)
    b.__enter__()                                   # B reclaims it
    b_inode = lock.stat().st_ino

    a.__exit__(None, None, None)                    # A finishes, late

    assert lock.exists(), "the stale holder deleted the new holder's lock"
    assert lock.stat().st_ino == b_inode

    with pytest.raises(filelock.LockTimeout):       # B still holds it
        with filelock.locked(target, timeout=0.1):
            pass

    b.__exit__(None, None, None)
    assert not lock.exists()


def test_the_lock_is_released_when_the_body_raises(tmp_path):
    target = tmp_path / "state.json"
    with pytest.raises(RuntimeError):
        with filelock.locked(target):
            raise RuntimeError("boom")
    assert not _lock_path(target).exists()

    with filelock.locked(target, timeout=0.5):      # acquirable again
        pass


def test_different_targets_do_not_serialise_against_each_other(tmp_path):
    with filelock.locked(tmp_path / "a.json"):
        with filelock.locked(tmp_path / "b.json", timeout=0.5):
            pass


def test_a_stale_lock_that_cannot_be_reclaimed_still_times_out(tmp_path):
    """The stale branch used to `continue` without checking the deadline,
    and `_unlink_if_inode` swallows OSError — so on a read-only or
    unwritable directory the reclaim never succeeded and this spun at 100%
    CPU forever, ignoring the timeout. A hang, with no error, in the module
    every other module's correctness rests on."""
    if os.geteuid() == 0:
        pytest.skip("root ignores directory permissions")
    d = tmp_path / "ro"
    d.mkdir()
    target = d / "state.json"
    lock = _lock_path(target)
    lock.write_text("999999")
    old = time.time() - filelock.STALE_AFTER - 5
    os.utime(lock, (old, old))
    os.chmod(d, 0o500)
    try:
        started = time.monotonic()
        with pytest.raises(filelock.LockTimeout):
            with filelock.locked(target, timeout=0.5):
                pass
        assert time.monotonic() - started < 10, "the lock spun past its timeout"
    finally:
        os.chmod(d, 0o700)

def test_goal_lock_permission_error_never_enters(tmp_path, monkeypatch):
    import errno

    entered = []

    def denied(*args, **kwargs):
        raise PermissionError(errno.EACCES, "blocked fixture")

    monkeypatch.setattr(filelock.os, "open", denied)
    with pytest.raises(PermissionError):
        with filelock.goal_locked(tmp_path / "goal.json"):
            entered.append(True)
    assert entered == []

def test_goal_lock_aged_holder_survives_and_kill_releases(tmp_path):
    if not filelock.state_supported():
        pytest.skip("process-owned goal lock is available only on tested POSIX platforms")
    target = tmp_path / "goal.json"
    lock = target.parent / f".{target.name}.flock"
    script = "\n".join([
        "import sys",
        f"sys.path.insert(0, {str(BIN)!r})",
        "import filelock",
        "from pathlib import Path",
        f"with filelock.goal_locked(Path({str(target)!r})):",
        "    print('ready', flush=True)",
        "    sys.stdin.read()",
    ])
    child = subprocess.Popen(
        [sys.executable, "-u", "-c", script],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert child.stdout.readline().strip() == "ready", child.stderr.read()
        inode = lock.stat().st_ino
        old = time.time() - filelock.STALE_AFTER - 5
        os.utime(lock, (old, old))
        with pytest.raises(filelock.LockTimeout):
            with filelock.goal_locked(target, timeout=0.15):
                pass
        assert lock.stat().st_ino == inode
    finally:
        child.kill()
        child.communicate(timeout=5)

    with filelock.goal_locked(target, timeout=0.5):
        assert lock.stat().st_ino == inode
    assert lock.exists()
    assert lock.stat().st_ino == inode

def test_goal_create_fails_closed_when_lock_open_is_denied(tmp_path, monkeypatch):
    import errno
    import goal
    import paths

    monkeypatch.setattr(paths, "goals_dir", lambda: tmp_path)

    def denied(*args, **kwargs):
        raise PermissionError(errno.EACCES, "blocked fixture")

    monkeypatch.setattr(filelock.os, "open", denied)
    with pytest.raises(PermissionError):
        goal.create("blocked", tmp_path, "command", "true")
    assert list(tmp_path.glob("*.json")) == []

@pytest.mark.parametrize("operation", [
    "checkpoint", "record_review", "complete", "abandon", "reopen", "add_subagent",
])
def test_goal_mutators_fail_closed_when_lock_open_is_denied(tmp_path, monkeypatch, operation):
    import errno
    import goal
    import paths

    monkeypatch.setattr(paths, "goals_dir", lambda: tmp_path)
    state = goal.create("blocked", tmp_path, "command", "true")
    target = tmp_path / f"{state['slug']}.json"
    before = target.read_bytes()
    slug = state["slug"]

    def denied(*args, **kwargs):
        raise PermissionError(errno.EACCES, "blocked fixture")

    monkeypatch.setattr(filelock.os, "open", denied)
    actions = {
        "checkpoint": lambda: goal.checkpoint(slug, "did", "next", "hash", "fail"),
        "record_review": lambda: goal.record_review(
            slug, "pass", "A detailed and substantive independent review verdict.",
            "judge", "0" * 64,
        ),
        "complete": lambda: goal.complete(slug, ""),
        "abandon": lambda: goal.abandon(slug, "reason"),
        "reopen": lambda: goal.reopen(slug, "reason"),
        "add_subagent": lambda: goal.add_subagent(slug, "worker", "name", "brief"),
    }
    with pytest.raises(PermissionError):
        actions[operation]()
    assert target.read_bytes() == before
