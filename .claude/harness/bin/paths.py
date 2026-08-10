"""Filesystem locations for the continual harness.

Everything the harness writes — goal state, the changelog, undo entries —
belongs to the *project being worked on*, not to wherever these modules
happen to live. That distinction only matters once the modules are
promoted to `~/.claude/harness/bin/` and shared across projects: resolving
from `__file__` would then give every project one shared goals directory,
one shared changelog, and one shared daily edit cap. Two projects with a
similarly-named goal would silently overwrite each other, and edits made
in one project would exhaust another project's budget.

So the root is discovered at runtime by walking up from the working
directory for a `.claude/` directory. Project-local and promoted installs
both land on the current project.

The project directory name may contain spaces — quote every path built
from these.
"""

from pathlib import Path


def project_root() -> Path:
    """The nearest ancestor of the working directory containing `.claude/`.

    Falls back to the working directory itself when no `.claude/` is found,
    so a fresh project gets its own harness rather than borrowing another's.
    """
    cwd = Path.cwd().resolve()
    for candidate in (cwd, *cwd.parents):
        if (candidate / ".claude").is_dir():
            return candidate
    return cwd


def harness_root() -> Path:
    """This project's `.claude/harness` directory."""
    return project_root() / ".claude" / "harness"


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
