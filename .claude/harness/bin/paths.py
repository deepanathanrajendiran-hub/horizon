"""Filesystem locations for the continual harness.

All paths resolve relative to the harness root so the modules work
regardless of the caller's working directory. The project directory name
contains a trailing space, so every consumer must quote paths.
"""

from pathlib import Path


def harness_root() -> Path:
    """The .claude/harness directory containing this bin/ folder."""
    return Path(__file__).resolve().parent.parent


def goals_dir() -> Path:
    d = harness_root() / "goals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def undo_dir() -> Path:
    d = harness_root() / "undo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def changelog() -> Path:
    return harness_root() / "CHANGELOG.md"
