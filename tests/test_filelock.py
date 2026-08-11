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

BIN = Path(__file__).resolve().parents[1] / ".claude" / "harness" / "bin"
sys.path.insert(0, str(BIN))

import filelock  # noqa: E402


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
