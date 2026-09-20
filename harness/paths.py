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
import os


def runtime() -> str:
    """The CLI always selects explicitly; legacy Python callers default to Claude."""
    value = os.environ.get('CONTINUAL_HARNESS_RUNTIME', 'claude')
    if value not in ('claude', 'codex'):
        raise ValueError(f'Unknown harness runtime: {value!r}')
    return value


def runtime_home() -> Path:
    if runtime() == 'codex':
        return Path(os.environ.get('CODEX_HOME', str(Path.home() / '.codex'))).expanduser().resolve()
    return Path.home() / '.claude'


def project_root() -> Path:
    """Nearest runtime marker, then Git root, then CWD; never capture HOME."""
    cwd = Path.cwd().resolve()
    home = Path.home().resolve()
    ancestors = []
    for candidate in (cwd, *cwd.parents):
        if candidate == home or candidate == candidate.parent:
            break
        ancestors.append(candidate)
    markers = ('.codex', '.agents') if runtime() == 'codex' else ('.claude',)
    for candidate in ancestors:
        if any((candidate / marker).is_dir() for marker in markers):
            return candidate
    return next((c for c in ancestors if (c / '.git').exists()), cwd)


def harness_root() -> Path:
    """Runtime-specific project state; the shared implementation lives elsewhere."""
    return project_root() / ('.codex' if runtime() == 'codex' else '.claude') / 'harness'


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
    """Codex notes are read explicitly by recall; Claude retains its native store."""
    if runtime() == 'codex':
        return project_root() / '.codex' / 'memory'
    return runtime_home() / 'projects' / project_key() / 'memory'


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
    """One global ledger per runtime, shared across its projects."""
    return runtime_home() / 'harness'


def global_undo_dir() -> Path:
    d = global_harness_root() / "undo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def global_changelog() -> Path:
    return global_harness_root() / "CHANGELOG.md"

def targets(scope: str = 'project') -> dict:
    """Routing information shared by doctor, proposals, and skill instructions."""
    if scope not in ('project', 'global'):
        raise ValueError(f'Unknown scope: {scope!r}')
    root = project_root() if scope == 'project' else runtime_home()
    if runtime() == 'codex':
        skills = project_root() / '.agents/skills' if scope == 'project' else Path.home() / '.agents/skills'
        agents = project_root() / '.codex/agents' if scope == 'project' else root / 'agents'
        prompt = root / 'AGENTS.md'
    else:
        base = root / '.claude' if scope == 'project' else root
        skills, agents, prompt = base / 'skills', base / 'agents', root / 'CLAUDE.md'
    return {'rho': str(prompt), 'G': str(skills), 'K': str(agents), 'M': str(memory_dir())}
