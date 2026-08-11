"""Goal state for horizon (spec §5.1, §5.5).

Checkpointing after every increment is the mechanism behind "long
horizon" — it is what lets work survive context loss, session restart, or
a /loop firing. Nothing here trusts in-memory state.
"""

import json
import os
import re
from datetime import datetime, timezone

import filelock
import paths

MAX_GATE_OUTPUT = 4000
SLUG_MAX = 60


class GateRequired(Exception):
    """Raised when a goal is created without a termination criterion."""


class GoalStateError(Exception):
    """Raised when goal state is missing or unreadable. Never guess."""


class GateNotPassed(Exception):
    """Raised when completion is attempted without a passing gate."""


class BudgetInvalid(Exception):
    """Raised when a goal is created with a budget that cannot bound it."""


class GoalClosed(GoalStateError):
    """Raised when an operation is attempted on a goal that is no longer active."""


REQUIRED_KEYS = (
    "slug", "goal", "cwd", "gate", "watch_paths", "budget",
    "used", "status", "checkpoints", "subagents",
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _bounded(text: str) -> str:
    """Keep the END of oversized text, matching gate.py's truncation.

    A failing gate's actionable diagnosis and a coverage judge's verdict
    both come last. Keeping the head discards precisely the part a reader
    needs, and the two modules disagreeing about direction is worse than
    either choice.
    """
    if len(text) > MAX_GATE_OUTPUT:
        return "[truncated]...\n" + text[-MAX_GATE_OUTPUT:]
    return text


def slugify(text: str) -> str:
    r"""A filesystem-safe slug that preserves non-Latin scripts.

    `[^a-z0-9]` stripped every non-ASCII character, so every Chinese,
    Cyrillic, Japanese or Greek goal title collapsed to the same slug and
    the user could not name their own goal to resume it. `\w` under
    Unicode keeps letters and digits in any script; only separators and
    punctuation are replaced.
    """
    s = re.sub(r"[^\w]+", "-", text, flags=re.UNICODE).strip("-").lower()
    s = s[:SLUG_MAX].strip("-")
    # An all-punctuation title still reduces to nothing, which would name
    # the state file ".json" — hidden, and shared by every such goal.
    return s or "goal"


def _unique_slug(base: str) -> str:
    """`base`, or `base-2`, `base-3`... — whichever is free.

    Two goals with the same title must not share a state file: the second
    `create()` would silently overwrite the first, destroying its
    checkpoints and its record of what had already been done.
    """
    d = paths.goals_dir()
    if not (d / f"{base}.json").exists():
        return base
    n = 2
    while (d / f"{base}-{n}.json").exists():
        n += 1
    return f"{base}-{n}"


def _path(slug: str):
    return paths.goals_dir() / f"{slug}.json"


def _save(state: dict) -> dict:
    """Write the state file atomically.

    The checkpoints are the only thing that lets a future session resume, so
    a torn write loses the goal. Write beside the target and rename over it:
    POSIX rename is atomic, so the file is either entirely the old state or
    entirely the new one, never half of each.
    """
    target = _path(state["slug"])
    tmp = target.parent / f".{target.name}.tmp-{os.getpid()}"
    try:
        tmp.write_text(json.dumps(state, indent=2) + "\n")
        os.replace(tmp, target)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return state


def create(goal, cwd, gate_kind, gate_value,
           max_turns=20, max_minutes=None, watch_paths=None) -> dict:
    if not gate_value or not str(gate_value).strip():
        raise GateRequired(
            "horizon requires a gate. A loop with no termination criterion "
            "is an infinite loop. Pass --gate '<command>' or a coverage criterion."
        )
    if gate_kind not in ("command", "coverage"):
        raise GateRequired(f"gate_kind must be 'command' or 'coverage', got {gate_kind!r}")

    # An unvalidated budget defeats the same invariant the gate protects:
    # max_turns=None makes budget_exhausted() return False forever, and a
    # string raises TypeError mid-loop instead of at creation.
    for name, value in (("max_turns", max_turns), ("max_minutes", max_minutes)):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise BudgetInvalid(
                f"{name} must be a positive integer or None, got {value!r}"
            )
    if max_turns is None and max_minutes is None:
        raise BudgetInvalid(
            "a goal needs at least one of max_turns or max_minutes; with "
            "neither, budget_exhausted() can never become True and the loop "
            "is unbounded — the thing a gate exists to prevent"
        )

    # _unique_slug is check-then-write like every other mutator here, and was
    # the one left unlocked: eight concurrent creates of one title produced
    # five state files, three goals silently losing theirs to a collision.
    with filelock.locked(paths.goals_dir() / "CREATE"):
        return _create_locked(goal, cwd, gate_kind, gate_value,
                              max_turns, max_minutes, watch_paths)


def _create_locked(goal, cwd, gate_kind, gate_value,
                   max_turns, max_minutes, watch_paths) -> dict:
    state = {
        "slug": _unique_slug(slugify(goal)),
        "goal": goal,
        "cwd": str(cwd),
        "gate": {"kind": gate_kind, "value": gate_value},
        "watch_paths": [os.path.abspath(str(p)) for p in (watch_paths or [cwd])],
        "budget": {"max_turns": max_turns, "max_minutes": max_minutes},
        "used": {"turns": 0, "started": _now()},
        "status": "active",
        "subagents": [],
        "checkpoints": [],
    }
    return _save(state)


def load(slug: str) -> dict:
    p = _path(slug)
    if not p.exists():
        raise GoalStateError(f"no goal state at {p}")
    try:
        state = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise GoalStateError(f"goal state at {p} is unreadable: {e}") from e
    if not isinstance(state, dict):
        raise GoalStateError(
            f"goal state at {p} is not a JSON object (got {type(state).__name__})"
        )
    for key, kind in (("gate", dict), ("budget", dict), ("used", dict),
                      ("checkpoints", list), ("subagents", list),
                      ("watch_paths", list), ("slug", str), ("status", str)):
        if key in state and not isinstance(state[key], kind):
            raise GoalStateError(
                f"goal state at {p} has {key!r} as {type(state[key]).__name__}, "
                f"expected {kind.__name__} — refusing to guess"
            )
    missing = [k for k in REQUIRED_KEYS if k not in state]
    if missing:
        raise GoalStateError(
            f"goal state at {p} is missing required fields: {', '.join(missing)}"
        )
    return state


def checkpoint(slug, did, next_action, tree_hash_value,
               gate_result, gate_output="") -> dict:
    """Append a checkpoint. Serialised: this is a read-modify-write, and two
    concurrent turns would otherwise each read turn N and both write N+1,
    losing one turn's record entirely."""
    if gate_result not in ("pass", "fail", "skipped"):
        raise ValueError(f"gate_result must be pass|fail|skipped, got {gate_result!r}")
    with filelock.locked(_path(slug)):
        return _checkpoint_locked(slug, did, next_action, tree_hash_value,
                                  gate_result, gate_output)


def _checkpoint_locked(slug, did, next_action, tree_hash_value,
                       gate_result, gate_output) -> dict:
    state = load(slug)
    if state["status"] != "active":
        raise GoalClosed(
            f"cannot checkpoint: goal status is {state['status']!r}, not active"
        )
    gate_output = _bounded(gate_output)
    state["checkpoints"].append({
        "turn": state["used"]["turns"] + 1,
        "at": _now(),
        "did": did,
        "next": next_action,
        "tree_hash": tree_hash_value,
        "gate": gate_result,
        "gate_output": gate_output,
    })
    state["used"]["turns"] += 1
    return _save(state)


def budget_exhausted(state: dict) -> bool:
    max_turns = state["budget"].get("max_turns")
    if max_turns is not None and state["used"]["turns"] >= max_turns:
        return True
    max_minutes = state["budget"].get("max_minutes")
    if max_minutes is not None:
        started = datetime.fromisoformat(state["used"]["started"])
        elapsed = (datetime.now(timezone.utc) - started).total_seconds() / 60
        if elapsed >= max_minutes:
            return True
    return False


def _complete_locked(slug: str, gate_output: str) -> dict:
    state = load(slug)
    if state["status"] != "active":
        raise GoalClosed(
            f"cannot complete: goal status is {state['status']!r}, not active"
        )
    last = state["checkpoints"][-1] if state["checkpoints"] else None
    if last is None or last["gate"] != "pass":
        raise GateNotPassed(
            "cannot complete: the last gate did not pass. Budget exhaustion "
            "is reported as a partial result, never as success."
        )
    state["status"] = "complete"
    state["gate_evidence"] = _bounded(gate_output)
    state["completed_at"] = _now()
    return _save(state)


def _abandon_locked(slug: str, reason: str) -> dict:
    state = load(slug)
    if state["status"] != "active":
        raise GoalClosed(
            f"cannot abandon: goal status is {state['status']!r}, not active"
        )
    state["status"] = "abandoned"
    state["abandoned_reason"] = reason
    state["abandoned_at"] = _now()
    return _save(state)


def _add_subagent_locked(slug: str, role: str, name: str, brief: str,
                  last_seen_turn: int | None = None) -> dict:
    """Append a subagent entry, guarded the same way as every other mutator.

    Writing to the state file any other way (e.g. reaching for the
    private save helper directly) skips this terminal-state guard,
    letting bookkeeping for a subagent silently land on a goal that
    already completed or was abandoned.
    """
    state = load(slug)
    if state["status"] != "active":
        raise GoalClosed(
            f"cannot add subagent: goal status is {state['status']!r}, not active"
        )
    if last_seen_turn is None:
        last_seen_turn = state["used"]["turns"]
    state["subagents"].append({
        "role": role,
        "name": name,
        "brief": _bounded(brief),
        "last_seen_turn": last_seen_turn,
    })
    return _save(state)


def _with_lock(fn):
    def wrapper(slug, *a, **kw):
        with filelock.locked(_path(slug)):
            return fn(slug, *a, **kw)
    wrapper.__name__ = fn.__name__.strip("_").replace("_locked", "")
    wrapper.__doc__ = fn.__doc__
    return wrapper


# Every mutator is a read-modify-write and must be serialised for the same
# reason checkpoint() is.
complete = _with_lock(_complete_locked)
abandon = _with_lock(_abandon_locked)
add_subagent = _with_lock(_add_subagent_locked)
