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

import re
from pathlib import Path


def project_root() -> Path:
    """The project containing the working directory.

    Nearest ancestor with a `.claude/`, then nearest ancestor with a `.git/`,
    then the working directory itself.

    `$HOME` is excluded from the walk, and this is the whole point of the
    function. Every Claude Code user has `~/.claude/`, so a naive upward walk
    resolves *any* project without its own `.claude/` — the common case — to
    the home directory. Every such project would then share one goals
    directory, one changelog and one daily edit cap, and a goal in one would
    silently overwrite a similarly-named goal in another. Falling back to the
    working directory keeps a fresh project's state in the fresh project.
    """
    cwd = Path.cwd().resolve()
    home = Path.home().resolve()

    def _walk(marker: str) -> Path | None:
        for candidate in (cwd, *cwd.parents):
            if candidate == home or candidate == candidate.parent:
                return None
            if (candidate / marker).exists():
                return candidate
        return None

    return _walk(".claude") or _walk(".git") or cwd


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


def project_key() -> str:
    """Claude Code's key for this project: the absolute path, `/` and space -> `-`.

    Worked example for a directory whose name ends in a space:
    `/Users/deeps/Documents/NEW STUFF/fun ` -> `-Users-deeps-Documents-NEW-STUFF-fun-`.
    """
    return re.sub(r"[/ ]", "-", str(project_root()))


def memory_dir() -> Path:
    """Where Claude Code actually reads this project's memories from.

    Derived rather than passed in, so a mistyped key cannot be approved:
    a memory written one character off lands in a directory that
    `mkdir -p` happily creates and recall never looks at, and the approval
    prompt shows a plausible path either way.
    """
    return Path.home() / ".claude" / "projects" / project_key() / "memory"


# --------------------------------------------------------------------------
# the global ledger
# --------------------------------------------------------------------------
#
# Everything above is deliberately per-project. `/refine --global` breaks
# that assumption in one direction only: its *targets* are shared
# (`~/.claude/CLAUDE.md`, `~/.claude/skills/`, `~/.claude/agents/`), so
# bookkeeping keyed to whichever project happened to run the command left
# two holes that only appear once you work in more than one repo.
#
#   The cap stopped bounding anything. Three edits per project per day
#   against one shared store is 3N edits a day, and every pre-flight
#   honestly reported a number under three.
#
#   Undo was stranded. A global edit made from project A was revertible
#   only from project A, so noticing a bad global rule a week later in
#   project B left no way to reach it.
#
# One shared ledger under `~/.claude/harness/` closes both: global writes
# count against each other wherever they were made, and `--undo --global`
# reaches them from anywhere. Project writes are untouched and still land
# in the project.


def global_harness_root() -> Path:
    """The shared harness directory, for `--global` bookkeeping only."""
    return Path.home() / ".claude" / "harness"


def global_undo_dir() -> Path:
    d = global_harness_root() / "undo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def global_changelog() -> Path:
    return global_harness_root() / "CHANGELOG.md"
