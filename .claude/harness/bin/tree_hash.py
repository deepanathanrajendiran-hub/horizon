"""Deterministic change detection for horizon gates (spec §5.2.1).

Combines three inputs:
  1. git HEAD          — catches commits
  2. git porcelain     — catches tracked edits and untracked files
  3. mtime+size walk   — catches everything git cannot see

Input 3 is not redundant. `git status --porcelain=v1` does not report
writes inside .gitignore'd directories, so a goal whose only output lands
in out/ or dist/ would otherwise have its gate wrongly skipped.

Skip asymmetry: a false skip is a correctness bug, a false run costs
seconds. Every failure path in this module reports "changed".
"""

import hashlib
import os
import stat
import subprocess
from pathlib import Path

EXCLUDED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "target",
    "__pycache__", ".mypy_cache", ".pytest_cache",
}


def _git(cwd, *args) -> str | None:
    """Run a git command, returning stdout or None if git is unusable here."""
    try:
        r = subprocess.run(
            ["git", *args], cwd=str(cwd),
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _reraise(err: OSError) -> None:
    """os.walk swallows scandir errors by default, silently dropping the
    affected subtree. Re-raise so the failure reaches changed()'s
    except-clause instead of producing an incomplete, normal-looking hash."""
    raise err


def _walk_entries(root: Path) -> list[str]:
    """One line per file: path, mtime_ns, size. Sorted for determinism."""
    try:
        root_stat = root.stat()
    except FileNotFoundError:
        return [f"{root}\x00MISSING"]
    if stat.S_ISREG(root_stat.st_mode):
        return [f"{root}\x00{root_stat.st_mtime_ns}\x00{root_stat.st_size}"]

    entries: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=_reraise):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for name in filenames:
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                entries.append(f"{p}\x00UNREADABLE")
                continue
            entries.append(f"{p}\x00{st.st_mtime_ns}\x00{st.st_size}")
    return sorted(entries)


def compute(cwd, watch_paths: list[str] | None = None) -> str:
    """Hex sha256 over git state plus an mtime walk of watch_paths.

    watch_paths scopes the *walk* only; git state always contributes.
    That is deliberate and conservative: narrowing the walk can add a
    detection (an ignored output dir) but never remove one.
    """
    cwd = Path(cwd)
    h = hashlib.sha256()

    h.update(b"HEAD\x00" + (_git(cwd, "rev-parse", "HEAD") or "NONE").encode() + b"\x00")
    h.update(b"STATUS\x00" + (_git(cwd, "status", "--porcelain=v1") or "NONE").encode() + b"\x00")

    for wp in sorted(str(p) for p in (watch_paths or [cwd])):
        h.update(b"WATCH\x00" + wp.encode() + b"\x00")
        for entry in _walk_entries(Path(wp)):
            h.update(entry.encode() + b"\x00")

    return h.hexdigest()


def changed(cwd, watch_paths: list[str] | None, previous: str | None) -> bool:
    """True if the tree may have changed. Any doubt reports True."""
    if not previous:
        return True
    try:
        return compute(cwd, watch_paths) != previous
    except Exception:
        return True


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    watch = sys.argv[2:] or None
    print(compute(target, watch))
