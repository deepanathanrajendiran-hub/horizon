import os
import subprocess
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / ".claude" / "harness" / "bin"
sys.path.insert(0, str(BIN))

import tree_hash  # noqa: E402


def _repo(tmp_path: Path, gitignore: str = "out/\nnode_modules/\n") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / ".gitignore").write_text(gitignore)
    (repo / "tracked.txt").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"],
        cwd=repo, check=True,
    )
    return repo


def test_gitignored_write_is_detected(tmp_path):
    """T8 regression: git status --porcelain=v1 cannot see writes inside
    ignored directories, so a research goal writing only to out/ would have
    its gate wrongly skipped."""
    repo = _repo(tmp_path)
    before = tree_hash.compute(repo)

    (repo / "out").mkdir()
    (repo / "out" / "report.md").write_text("findings v1")

    porcelain = subprocess.run(
        ["git", "status", "--porcelain=v1"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout
    assert porcelain == "", "precondition: git must be blind to the ignored write"

    assert tree_hash.compute(repo) != before, "tree_hash must still detect it"


def test_tracked_edit_is_detected(tmp_path):
    repo = _repo(tmp_path)
    before = tree_hash.compute(repo)
    (repo / "tracked.txt").write_text("changed")
    assert tree_hash.compute(repo) != before


def test_stable_when_nothing_changes(tmp_path):
    repo = _repo(tmp_path)
    assert tree_hash.compute(repo) == tree_hash.compute(repo)


def test_excluded_dirs_are_ignored(tmp_path):
    repo = _repo(tmp_path)
    (repo / "node_modules").mkdir()
    before = tree_hash.compute(repo)
    (repo / "node_modules" / "junk.js").write_text("noise")
    assert tree_hash.compute(repo) == before


def test_watch_paths_add_coverage_for_ignored_output(tmp_path):
    """watch_paths scopes the mtime walk only. Git state always
    contributes, which is the conservative direction: it can add a
    detection, never remove one."""
    repo = _repo(tmp_path)
    (repo / "out").mkdir()
    watch = [str(repo / "out")]

    before = tree_hash.compute(repo, watch)
    (repo / "out" / "r.md").write_text("real output")
    assert tree_hash.compute(repo, watch) != before

    # And a tracked change is still caught even though it sits outside watch.
    mid = tree_hash.compute(repo, watch)
    (repo / "tracked.txt").write_text("edited")
    assert tree_hash.compute(repo, watch) != mid


def test_changed_is_true_without_previous(tmp_path):
    repo = _repo(tmp_path)
    assert tree_hash.changed(repo, None, None) is True


def test_changed_is_true_on_doubt(tmp_path, monkeypatch):
    """Skip asymmetry: a false skip is a correctness bug, so any failure
    to compute must report changed."""
    repo = _repo(tmp_path)
    monkeypatch.setattr(
        tree_hash, "compute",
        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")),
    )
    assert tree_hash.changed(repo, None, "abc123") is True


def test_changed_is_true_when_subtree_unreadable(tmp_path):
    """Skip-asymmetry hole: os.walk swallows scandir errors unless given an
    onerror callback, so an unreadable subtree used to be silently dropped
    from the walk instead of raising. That would let compute() return a
    normal-looking digest built from incomplete data -- an unknown state
    reported as "nothing changed", which changed()'s doubt-path must never
    do. This exercises the real internal failure (a permission-denied
    directory), not a monkeypatched one, so it would have passed unchanged
    against the bug that test_changed_is_true_on_doubt does not catch."""
    repo = _repo(tmp_path)
    locked = repo / "locked"
    locked.mkdir()
    (locked / "secret.txt").write_text("v1")

    previous = tree_hash.compute(repo)

    os.chmod(locked, 0o000)
    try:
        assert tree_hash.changed(repo, None, previous) is True
    finally:
        os.chmod(locked, 0o755)


def test_compute_refuses_to_guess_when_subtree_unreadable(tmp_path):
    """compute() must not silently absorb an unreadable subtree.

    Pre-fix, os.walk's default onerror=None dropped the subtree and
    compute() returned a confident digest built from incomplete data.
    changed() then compared two such digests and could report False --
    an unknown state reported as "nothing changed". The fix makes the
    failure propagate so changed()'s doubt-path can catch it.
    """
    repo = _repo(tmp_path)
    locked = repo / "locked"
    locked.mkdir()
    (locked / "secret.txt").write_text("v1")
    os.chmod(locked, 0o000)
    try:
        with pytest.raises(OSError):
            tree_hash.compute(repo)
    finally:
        os.chmod(locked, 0o755)


def test_compute_raises_when_watch_root_unstattable(tmp_path):
    """IMPORTANT 4: pre-fix, _walk_entries() used root.exists(), which
    swallows any OSError (not just ENOENT) and returns False -- so an
    unstattable watch ROOT (distinct from Task 1's unreadable-subtree fix)
    produced a stable MISSING sentinel. Two consecutive computes would then
    agree, changed() would report False, and the gate would be skipped
    forever. The fix must let genuine permission-denied errors on the root
    itself propagate, not collapse to MISSING.

    Removing read/execute from the watch root's PARENT (not the root
    itself) makes root.stat() raise PermissionError while leaving the root
    directory's own permissions untouched -- this isolates the root-level
    failure from Task 1's already-fixed child-subtree failure.
    """
    repo = _repo(tmp_path)
    locked_parent = tmp_path / "locked_parent"
    locked_parent.mkdir()
    watch_root = locked_parent / "watched"
    watch_root.mkdir()
    (watch_root / "f.txt").write_text("v1")

    os.chmod(locked_parent, 0o000)
    try:
        with pytest.raises(OSError):
            tree_hash.compute(repo, [str(watch_root)])
    finally:
        os.chmod(locked_parent, 0o755)


def test_non_git_directory_uses_walk_only(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.txt").write_text("one")
    before = tree_hash.compute(plain)
    (plain / "b.txt").write_text("two")
    assert tree_hash.compute(plain) != before


def test_an_unstattable_file_is_not_hashed_as_a_constant(tmp_path):
    """"Every failure path in this module reports changed" — but a constant
    UNREADABLE marker is deterministic, so two hashes over the same
    unstattable file agreed and the gate was skipped against a tree the
    harness cannot see. Mode 0o444 is listable by scandir and not
    traversable by stat; 0o000 (already tested) raises inside os.walk."""
    root = tmp_path / "watched"
    sub = root / "sub"
    sub.mkdir(parents=True)
    (sub / "f.txt").write_text("before")

    os.chmod(sub, 0o444)
    try:
        with pytest.raises(OSError):
            tree_hash.compute(root, [str(root)])
        assert tree_hash.changed(root, [str(root)], "any-previous-hash") is True
    finally:
        os.chmod(sub, 0o755)

def test_goal_bookkeeping_does_not_change_hash(make_state_goal):
    import goal
    import steps

    state = make_state_goal()
    before = tree_hash.compute(state["cwd"], state["watch_paths"])
    steps.begin(state["slug"], "r1")
    assert tree_hash.compute(state["cwd"], state["watch_paths"]) == before
    source = Path(state["cwd"]) / "real-source.txt"
    source.write_text("meaningful work")
    assert tree_hash.compute(state["cwd"], state["watch_paths"]) != before
