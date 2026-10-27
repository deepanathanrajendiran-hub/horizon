"""Claude Code hooks that keep Horizon's saved state honest at zero model calls.

SessionStart records the session id, reclaims attempts whose lease went stale
and reports each active goal in one line. PostToolUse renews the leases this
session owns. Stop completes or finalizes goals whose evidence allows it.
Every hook returns quickly and never raises to the host.
"""

import json
import os
from pathlib import Path

import goal
import leases
import paths
import steps

MARK = "horizon-hook"
_EVENTS = {"SessionStart": None, "PostToolUse": "*", "Stop": None}


class HookInstallError(Exception):
    pass


def _settings_path(project: Path) -> Path:
    return Path(project) / ".claude" / "settings.json"


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except ValueError as exc:
        raise HookInstallError(f"{path} is not valid JSON; Horizon left it untouched") from exc
    if not isinstance(data, dict):
        raise HookInstallError(f"{path} is not a JSON object; Horizon left it untouched")
    return data


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp-{os.getpid()}"
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def _command(entrypoint: str, event: str) -> str:
    return f'python3 "{entrypoint}" --runtime claude hook {event} # {MARK}'


def _hook_table(settings: dict, path: Path) -> dict:
    table = settings.setdefault("hooks", {})
    if not isinstance(table, dict):
        raise HookInstallError(f"{path} has a non-object \"hooks\" entry; Horizon left it untouched")
    return table


def _ours(group) -> bool:
    inner = group.get("hooks", []) if isinstance(group, dict) else []
    return any(isinstance(h, dict) and MARK in str(h.get("command", "")) for h in inner)


def install(project: Path, entrypoint: str) -> bool:
    path = _settings_path(project)
    settings = _load(path)
    table = _hook_table(settings, path)
    changed = False
    for event, matcher in _EVENTS.items():
        groups = table.setdefault(event, [])
        if not isinstance(groups, list):
            raise HookInstallError(f"{path} has a non-list hooks.{event}; Horizon left it untouched")
        if any(_ours(g) for g in groups):
            continue
        group = {"hooks": [{"type": "command", "command": _command(entrypoint, event), "timeout": 30}]}
        if matcher:
            group["matcher"] = matcher
        groups.append(group)
        changed = True
    if changed:
        _write(path, settings)
    return changed


def uninstall(project: Path) -> bool:
    path = _settings_path(project)
    settings = _load(path)
    table = settings.get("hooks", {})
    if not isinstance(table, dict):
        raise HookInstallError(f"{path} has a non-object \"hooks\" entry; Horizon left it untouched")
    changed = False
    for event in list(table):
        if not isinstance(table[event], list):
            continue
        kept = []
        for group in table[event]:
            if not _ours(group):
                kept.append(group)
                continue
            inner = [h for h in group["hooks"]
                     if not (isinstance(h, dict) and MARK in str(h.get("command", "")))]
            changed = True
            if inner:
                kept.append({**group, "hooks": inner})
        if kept:
            table[event] = kept
        else:
            del table[event]
    if changed:
        _write(path, settings)
    return changed


def _active_goals() -> list[dict]:
    out = []
    for path in sorted(paths.goals_dir().glob("*.json")):
        if path.name.endswith(".leases.json"):
            continue
        try:
            state = goal.load(path.stem)
        except Exception:
            continue
        if state["status"] == "active":
            out.append(state)
    return out


def _goal_line(state: dict) -> str:
    row = goal.row(state)
    return (f"Horizon goal {row['slug']} ({row['status']}, {row['turns_used']} turns used): "
            f"{row['goal']} Next: {row['next_action'] or 'not set'}.")


def _session_start(session: str | None) -> dict | None:
    if session:
        root = paths.harness_root()
        root.mkdir(parents=True, exist_ok=True)
        (root / "session").write_text(session + "\n")
    lines = []
    for state in _active_goals():
        try:
            note = ""
            attempt = (state.get("execution") or {}).get("active_attempt")
            if attempt:
                why = leases.reclaimable(leases.read(state["slug"]).get(attempt), session)
                if why:
                    try:
                        steps.reclaim(state["slug"], attempt,
                                      expected_revision=state["execution"]["revision"], session=session)
                        note = f" Reclaimed attempt {attempt} ({why})."
                    except Exception as exc:
                        note = f" Attempt {attempt} could not be reclaimed: {exc}."
                else:
                    note = f" Attempt {attempt} is still owned by a live session."
            line = _goal_line(goal.load(state["slug"]))
            lines.append(f"{line}{note} Run `close {state['slug']}` when you think it is done.")
        except Exception:
            continue
    if not lines:
        return None
    return {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                   "additionalContext": "\n".join(lines)}}


def _post_tool_use(session: str | None) -> None:
    if not session:
        return
    for state in _active_goals():
        try:
            attempt = (state.get("execution") or {}).get("active_attempt")
            if attempt and (leases.read(state["slug"]).get(attempt) or {}).get("session") == session:
                leases.renew(state["slug"], attempt, session)
        except Exception:
            continue


def _stop() -> None:
    for state in _active_goals():
        try:
            goal.close(state["slug"], run_gate=False)
        except Exception:
            continue


def run(event: str, payload: dict) -> dict | None:
    session = payload.get("session_id") if isinstance(payload, dict) else None
    if not isinstance(session, str):
        session = None
    if event == "SessionStart":
        return _session_start(session)
    if event == "PostToolUse":
        _post_tool_use(session)
    elif event == "Stop":
        _stop()
    return None
