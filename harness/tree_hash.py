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


def _bookkeeping(path: Path) -> bool:
    parts = path.parts
    for i, part in enumerate(parts):
        if part.lower() in ('.claude', '.codex') and parts[i + 1:i + 2] == ('harness',):
            tail = parts[i + 2:]
            return bool(tail) and tail[0] in ('goals', 'undo', 'CHANGELOG.md')
    return False


def _file_entry(path: Path) -> str:
    before = path.stat()
    if not stat.S_ISREG(before.st_mode):
        raise OSError(f"cannot fingerprint special file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    after = path.stat()
    if (before.st_mtime_ns, before.st_size, before.st_ino) != (after.st_mtime_ns, after.st_size, after.st_ino):
        raise OSError(f"file changed while hashing: {path}")
    return f"{path}\x00{after.st_mode}\x00{digest.hexdigest()}"


def _walk_entries(root: Path) -> list[str]:
    """Fingerprint content and metadata, excluding only mutable harness records."""
    if _bookkeeping(root):
        return []
    try:
        root_stat = root.stat()
    except FileNotFoundError:
        return [f'{root}\x00MISSING']
    if stat.S_ISREG(root_stat.st_mode):
        return [_file_entry(root)]
    entries = []
    for dirpath, dirnames, filenames in os.walk(root, onerror=_reraise):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS
                       and not _bookkeeping(Path(dirpath) / d)]
        for name in filenames:
            path = Path(dirpath) / name
            if not _bookkeeping(path):
                entries.append(_file_entry(path))
    return sorted(entries)


def compute(cwd, watch_paths: list[str] | None = None) -> str:
    """Hash task content and Git state; writing a checkpoint is not task work."""
    cwd = Path(cwd).resolve()
    h = hashlib.sha256()
    h.update(b'HEAD\x00' + (_git(cwd, 'rev-parse', 'HEAD') or 'NONE').encode() + b'\x00')
    exclusions = [f':(glob,exclude)**/{runtime}/harness/{part}'
                  for runtime in ('.claude', '.codex')
                  for part in ('goals/**', 'undo/**', 'CHANGELOG.md')]
    # The walk skips cache directories; git must too, or the caches a gate or
    # a reviewer's own checks write make the checkpoint stale.
    exclusions += [f':(glob,exclude)**/{name}/**' for name in sorted(EXCLUDED_DIRS - {'.git'})]
    status = _git(cwd, 'status', '--porcelain=v1', '--untracked-files=all', '--', *exclusions)
    h.update(b'STATUS\x00' + (status or 'NONE').encode() + b'\x00')
    for wp in sorted(str(p) for p in (watch_paths or [cwd])):
        root = Path(wp)
        if not root.is_absolute():
            root = cwd / root
        h.update(b'WATCH\x00' + str(root).encode() + b'\x00')
        for entry in _walk_entries(root):
            h.update(entry.encode() + b'\x00')
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
