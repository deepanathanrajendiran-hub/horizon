"""Evidence rules for completion.

Which recorded gate result covers the files as they are now, which test files
changed since the baseline, and whether a reviewer is still required. Kept
apart from goal.py so the rules are tested once, without the lifecycle.
"""

import hashlib
import os
import re
from pathlib import Path

import tree_hash

_TEST_PATTERNS = (
    re.compile(r"(^|/)tests?/"),
    re.compile(r"(^|/)test_[^/]*\.py$"),
    re.compile(r"_test\.[^/]+$"),
    re.compile(r"\.(test|spec)\.[^/]+$"),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _is_harness_state(rel: str) -> bool:
    parts = rel.split("/")
    return any(parts[i].lower() in (".claude", ".codex") and parts[i + 1:i + 2] == ["harness"]
               for i in range(len(parts)))


def _is_test(rel: str) -> bool:
    return any(p.search(rel) for p in _TEST_PATTERNS)


def test_manifest(cwd: str, watch_paths: list[str] | None) -> dict[str, str]:
    """sha256 of every test file in the watched paths, keyed by project-relative path."""
    root = Path(cwd).resolve()
    out: dict[str, str] = {}
    for watch in watch_paths or [cwd]:
        base = Path(watch).resolve()
        if base.is_file():
            files = [base]
        else:
            files = []
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in tree_hash.EXCLUDED_DIRS]
                files += [Path(dirpath) / name for name in filenames]
        for path in files:
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = path.as_posix()
            if _is_harness_state(rel) or not _is_test(rel):
                continue
            try:
                out[rel] = _sha(path)
            except OSError:
                continue
    return out
