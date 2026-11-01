"""Deterministic change detection for horizon gates (spec §5.2.1).

Hashes the content and mode of every file git can see (tracked or
untracked, not ignored) and of every watched file (a walk that also sees
writes inside .gitignore'd directories, which git does not list).

Git state is deliberately left out: a commit that changes no file is not
task work. With HEAD and `git status` in the hash, the auto-commit after a
session made every checkpoint stale, and gate passes and carried review
lines were lost (NL2Repo bench2, unidecode s8 -> s9).

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

# Harness records under <runtime>/harness that change without any task work: goal
# state, undo data, the changelog, and what the hooks write at every session start.
BOOKKEEPING = ("goals", "undo", "CHANGELOG.md", "session", "hooks-optout", "notes")


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
            return bool(tail) and tail[0] in BOOKKEEPING
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
    """Hash task content; writing a checkpoint or committing is not task work."""
    cwd = Path(cwd).resolve()
    h = hashlib.sha256()
    # Every file git can see in the whole repository (tracked or untracked, not ignored), by content, so an
    # edit outside narrow watch paths, or outside a project that is a subdirectory, still counts. The set
    # and the contents are the same before and after a commit.
    top = (_git(cwd, 'rev-parse', '--show-toplevel') or '').strip()
    covered = not watch_paths and top and Path(top).resolve() == cwd  # the walk below sees every file
    exclusions = [f':(glob,exclude)**/{runtime}/harness/{part}{suffix}'
                  for runtime in ('.claude', '.codex')
                  for part in BOOKKEEPING
                  for suffix in ('', '/**')]
    exclusions += [f':(glob,exclude)**/{name}/**' for name in sorted(EXCLUDED_DIRS - {'.git'})]
    listed = None if covered or not top else _git(
        Path(top), 'ls-files', '-z', '--cached', '--others', '--exclude-standard', '--', *exclusions)
    if listed is None and not covered:
        h.update(b'GIT\x00NONE\x00')
    for rel in sorted(set(filter(None, (listed or '').split('\0')))):
        path = Path(top) / rel.rstrip('/')
        if _bookkeeping(path):
            continue
        try:
            if path.is_symlink():
                entry = f'{path}\x00LINK\x00{os.readlink(path)}'
            elif path.is_dir():
                # a submodule or a nested repository: its own commit and its own changes
                state = (_git(path, 'rev-parse', 'HEAD') or '') + (_git(path, 'status', '--porcelain') or '')
                entry = f'{path}\x00REPO\x00{hashlib.sha256(state.encode()).hexdigest()}'
            else:
                entry = _file_entry(path)
        except FileNotFoundError:
            entry = f'{path}\x00MISSING'
        h.update(b'GIT\x00' + entry.encode() + b'\x00')
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

NOT_SOURCE = {"venv", "env", "site-packages", "node_modules", "build", "dist", "__pycache__"}
DOC_SUFFIXES = (".md", ".rst", ".adoc")  # not .txt: requirements.txt and CMakeLists.txt are work
LARGE_FILE_BYTES = 20_000_000


def is_test(name: str) -> bool:
    """Whether a project-relative path is a test: under tests/ or test/, test_*, or conftest.py."""
    parts = name.split("/")
    return (any(p in ("tests", "test") for p in parts[:-1]) or parts[-1].startswith("test_")
            or parts[-1] == "conftest.py")


def is_doc(name: str) -> bool:
    """Whether a project-relative path is documentation: under docs/ or doc/, or a .md/.rst/.adoc file."""
    parts = name.split("/")
    return parts[-1].lower().endswith(DOC_SUFFIXES) or any(p in ("docs", "doc") for p in parts[:-1])


def source_files(root, docs: bool = True, skip=(), hidden: bool = False, tests: bool = False) -> list[str] | None:
    """The project's tracked or new files outside build folders, as sorted paths relative to `root`, filtered as
    `source_digest` says. None when the project is not a Git repository."""
    root = Path(root)
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode:
        return None
    skipped = []
    for folder in skip or ():
        try:
            skipped.append(Path(folder).resolve().relative_to(root.resolve()).as_posix().rstrip("/") + "/")
        except (OSError, ValueError):
            continue
    names = []
    for name in sorted(set(n for n in out.stdout.decode(errors="replace").split("\0") if n)):
        parts = name.split("/")
        if any(p in NOT_SOURCE for p in parts[:-1]) or any(name.startswith(s) for s in skipped):
            continue
        if not hidden and (any(p.startswith(".") for p in parts[:-1]) or parts[-1].startswith(".")):
            continue
        if (not tests and is_test(name)) or (not docs and is_doc(name)):
            continue
        names.append(name)
    return names


def source_digest(root, docs: bool = True, skip=(), hidden: bool = False, tests: bool = False) -> str | None:
    """A fingerprint of the project's work: every tracked or new file outside build folders, in any language.
    By default it also leaves out tests and hidden folders and files; `hidden=True` and `tests=True` keep them.
    `docs=False` leaves out documentation (doc folders and .md/.rst/.adoc files), so a README-only session is no
    progress for Plateau. `skip` names folders to leave out, such as the gate directory. A file over
    LARGE_FILE_BYTES counts by its size and modification time. None when the project is not a Git repository."""
    names = source_files(root, docs=docs, skip=skip, hidden=hidden, tests=tests)
    if names is None:
        return None
    digest = hashlib.sha256()
    for name in names:
        path = Path(root) / name
        try:
            info = path.stat()
            data = (f"{info.st_size}:{info.st_mtime_ns}".encode() if info.st_size > LARGE_FILE_BYTES
                    else path.read_bytes())
        except OSError:
            continue
        digest.update(name.encode() + b"\0" + data + b"\0")
    return digest.hexdigest()
