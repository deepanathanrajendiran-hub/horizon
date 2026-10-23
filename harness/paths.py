"""Filesystem locations for Horizon.

Everything Horizon writes — goal state, gates, reviews — belongs to the
*project being worked on*, not to wherever these modules happen to live.
Resolving from `__file__` would give every project that shares one
installation a single goals directory, and two projects with a
similarly-named goal would silently overwrite each other.

So the root is discovered at runtime by walking up from the working
directory for the runtime's marker directory. Project-local and personal
installs both land on the current project.

The project directory name may contain spaces — quote every path built
from these.
"""

from pathlib import Path
import os


def runtime() -> str:
    """The CLI always selects explicitly; legacy Python callers default to Claude."""
    value = os.environ.get('CONTINUAL_HARNESS_RUNTIME', 'claude')
    if value not in ('claude', 'codex'):
        raise ValueError(f'Unknown harness runtime: {value!r}')
    return value


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
