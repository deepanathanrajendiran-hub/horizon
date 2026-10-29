"""Claude Code hooks that keep Horizon's saved state honest at zero model calls.

SessionStart records the session id, reclaims attempts whose lease went stale
and reports each active goal in one line. PostToolUse renews the leases this
session owns. Stop completes or finalizes goals whose evidence allows it.
Every hook returns quickly and never raises to the host.
"""

import json
import os
from pathlib import Path

import subprocess
import time

import evidence
import gate
import goal
import leases
import paths
import steps
import sys
import tree_hash
import re

MARK = "horizon-hook"
_EVENTS = {"SessionStart": None, "PostToolUse": "*", "Stop": None}
# SessionStart runs each active goal's gate on changed files (SESSION_GATE_S at most), so it needs longer.
_TIMEOUTS = {"SessionStart": 300}

_CODE = (".py", ".pyi", ".js", ".ts", ".go", ".rs", ".java", ".rb", ".c", ".h", ".cpp", ".toml", ".cfg")
SESSION_GATE_S = 240  # all goals together; HORIZON_SESSION_GATE_S overrides (tests)


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
    # `|| true`: a stale path or interpreter must never exit 2, which Claude Code treats as blocking.
    return (f'"{sys.executable}" "{entrypoint}" --runtime claude --project "$CLAUDE_PROJECT_DIR" '
            f'hook {event} || true # {MARK}')


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
        current = [h for g in groups if _ours(g) for h in g["hooks"]
                   if isinstance(h, dict) and MARK in str(h.get("command", ""))]
        # keep a longer timeout the user set
        raised = max((h.get("timeout") for h in current if isinstance(h.get("timeout"), int)), default=0)
        wanted = {"type": "command", "command": _command(entrypoint, event),
                  "timeout": max(_TIMEOUTS.get(event, 30), raised)}
        if current == [wanted]:
            continue
        # Drop entries written for another path or interpreter, then write the current one.
        for group in list(groups):
            if _ours(group):
                inner = [h for h in group["hooks"]
                         if not (isinstance(h, dict) and MARK in str(h.get("command", "")))]
                groups.remove(group)
                if inner:
                    groups.append({**group, "hooks": inner})
        group = {"hooks": [wanted]}
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

def _optout_path() -> Path:
    return paths.harness_root() / "hooks-optout"


def opted_out() -> bool:
    """True after uninstall-hooks, until install-hooks: start then leaves settings alone."""
    return _optout_path().exists()


def set_opted_out(value: bool) -> None:
    marker = _optout_path()
    if value:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("uninstall-hooks was run; start will not reinstall the hooks.\n")
    else:
        marker.unlink(missing_ok=True)


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


_FAILED = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.M)
_SUMMARY = re.compile(r"^=*\s*(\d+ (?:failed|passed|errors?)\b[^\n=]*?)\s*=*\s*$", re.M)


def _gate_digest(output: str) -> str:
    """The failing checks by name when the output lists them, else the output's tail."""
    output = output or ""
    ids = []
    for found in _FAILED.findall(output):
        name = found.split("::")[-1] if "::" in found else found
        if name not in ids:
            ids.append(name)
    if ids:
        summary = _SUMMARY.findall(output)
        more = f" and {len(ids) - 12} more" if len(ids) > 12 else ""
        return f". Failing: {', '.join(ids[:12])}{more}" + (f" ({summary[-1]})." if summary else ".")
    tail = " ".join(output.split())[-300:]
    return f": {tail}" if tail else "."


def _files_line(root: Path, limit: int = 40) -> str:
    """The project's files with their line counts, so a session need not list them."""
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return ""
    if out.returncode:
        return ""
    names = sorted({n for n in out.stdout.decode(errors="replace").split("\0")
                    if n and not n.startswith((".claude/", ".agents/")) and "__pycache__" not in n})
    extra = ""
    if len(names) > limit:
        code = [n for n in names if n.endswith(_CODE)]
        extra = f" (and {len(names) - limit} more)"
        names = (code + [n for n in names if n not in code])[:limit]
    newline = b"\n"
    shown = []
    for name in names:
        try:
            count = (root / name).read_bytes().count(newline)
        except OSError:
            continue
        shown.append(f"{name} {count}")
    return ("Files (lines): " + ", ".join(shown) + extra + ".") if shown else ""

def _goal_line(state: dict, fresh: bool = False) -> str:
    row = goal.row(state)
    info = goal.summary(state)
    budget = f"{row['turns_used']}/{state['budget'].get('max_turns')} turns"
    if info["minutes_left"] is not None:
        budget += f", {info['minutes_left']} min left (deadline {info['deadline'][:16]})"
    if info["budget_exhausted"]:
        budget += ", budget exhausted"
    line = (f"Horizon goal {row['slug']} ({row['status']}; {budget}; gate dir {row['gate_path'] or 'none'}): "
            f"{row['goal']} Next: {row['next_action'] or 'not set'}.")
    if state["gate"]["kind"] == "command":
        line += f" Gate command: {state['gate']['value']}."
    observed = goal.latest_observation(state)
    last = state["checkpoints"][-1] if state["checkpoints"] else None
    # The newest gate result, whether the hook's own run, an `observe` or a `check`.
    record = None
    if observed and (fresh or last is None or observed["at"] > (last.get("at") or "")):
        record = (observed["gate"], observed["role"], observed["at"], observed["gate_output"])
    elif last and last.get("gate") is not None:
        record = (last["gate"], "check", last.get("at") or "", last.get("gate_output") or "")
    if record:
        result, role, at, output = record
        label = "Gate now" if fresh else "Last gate run"
        line += f" {label}: {result} ({role}, {at[:16]}){_gate_digest(output)}"
    return line


def _configured_timeout() -> int | None:
    """The timeout settings.json gives Horizon's SessionStart hook, if it is there."""
    try:
        groups = _load(_settings_path(paths.project_root())).get("hooks", {}).get("SessionStart", [])
        return next(int(h.get("timeout", 60)) for g in groups if _ours(g) for h in g["hooks"]
                    if isinstance(h, dict) and MARK in str(h.get("command", "")))
    except Exception:
        return None


def _gate_budget() -> int:
    """Seconds this SessionStart hook may spend on gates, all goals together.

    An install from before the session-start gate run has a 30-second hook: upgrade it for the next
    session, and run no gate in this one rather than be killed mid-run."""
    override = os.environ.get("HORIZON_SESSION_GATE_S")
    if override:
        return int(override)
    configured = _configured_timeout()
    if configured is not None and configured < _TIMEOUTS["SessionStart"]:
        try:
            if not opted_out() and sys.argv and sys.argv[0].endswith(".py"):
                install(paths.project_root(), os.path.abspath(sys.argv[0]))
        except Exception:
            pass
    budget = SESSION_GATE_S if configured is None else min(SESSION_GATE_S, configured - 30)
    return max(0, budget)

def _fresh_gate(state: dict, deadline: float) -> str | None:
    """Run a conversation-mode goal's command gate when no record covers the current files.

    A session cut off before `check` (the NL2Repo benchmark's call cap) left the next session with a
    gate result from session 1; running it here costs wall time, not model calls. Returns "ran", a
    reason it did not finish, or None when no run was needed. All goals share one deadline, kept below
    the hook's own timeout, so the host never kills the hook and loses the whole message."""
    if state.get("execution") or state["gate"]["kind"] != "command":
        return None
    try:
        current = tree_hash.compute(state["cwd"], state.get("watch_paths"))
    except Exception:
        return None
    observed = goal.latest_observation(state)
    last = state["checkpoints"][-1] if state["checkpoints"] else None
    if ((observed and observed.get("tree_hash") == current) or (last and last.get("tree_hash") == current)
            or evidence.passing_record(state, current)):
        return None
    left = int(deadline - time.monotonic())
    if left < 2:
        return "did not run: no time left in this hook"
    try:
        goal.observe(state["slug"], "resume", timeout=left, keep_timeout=False)
    except gate.GateUnrunnable:
        return f"did not finish within the {left} s this hook had left"
    return "ran"


def _notes_line(slug: str) -> str:
    path = goal.notes_path(slug)
    if not path.exists():
        return ""
    sections = goal.notes_sections(slug)
    return f" Notes: {path}" + (f" (parts: {'; '.join(sections)})." if sections else " (no parts yet).")


def _paths_line() -> str:
    entry = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0].endswith(".py") else None
    return (f"Horizon: HARNESS={entry} PROJECT={paths.project_root()} (use these; no need to run `doctor`)."
            if entry else f"Horizon: PROJECT={paths.project_root()}.")


def _last_commit_files(limit: int = 12) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(paths.project_root()), "show", "--name-only", "--format=", "HEAD"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    names = [n for n in out.stdout.splitlines() if n.strip()] if out.returncode == 0 else []
    return names[:limit] + (["…"] if len(names) > limit else [])


def _session_start(session: str | None, source: str | None = None) -> dict | None:
    if session:
        root = paths.harness_root()
        root.mkdir(parents=True, exist_ok=True)
        (root / "session").write_text(session + "\n")
    lines = []
    # Gates run only when a session really starts: not on resume, clear or compaction.
    startup = source in (None, "startup")
    deadline = time.monotonic() + _gate_budget()
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
            try:
                ran = _fresh_gate(state, deadline) if startup else None
            except Exception as exc:
                ran = f"could not run: {exc}"
            line = _goal_line(goal.load(state["slug"]), fresh=ran == "ran")
            if ran and ran != "ran":
                line += f" The gate on the current files {ran}; run `observe` for a result."
            lines.append(f"{line}{note}{_notes_line(state['slug'])} Run `close {state['slug']}` "
                         "when you think it is done.")
        except Exception:
            continue
    if not lines:
        return None
    lines.insert(0, _paths_line())
    changed = _last_commit_files()
    if changed:
        lines.append("The last commit changed: " + ", ".join(changed) + ".")
    files = _files_line(paths.project_root())
    if files:
        lines.append(files)
    return {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                   "additionalContext": "\n".join(lines)}}


def _post_tool_use(session: str | None) -> None:
    if not session:
        return
    for state in _active_goals():
        try:
            attempt = (state.get("execution") or {}).get("active_attempt")
            if not attempt:
                continue
            lease = leases.read(state["slug"]).get(attempt) or {}
            owner = lease.get("session")
            # A lease begun without an owner belongs to the first session that works on it.
            if owner is None or owner == session:
                # The host process lets a later session see at once that this one died.
                host = None if owner == session and lease.get("host") else leases.host_process()
                leases.renew(state["slug"], attempt, session, host=host)
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
        source = payload.get("source") if isinstance(payload, dict) else None
        return _session_start(session, source if isinstance(source, str) else None)
    if event == "PostToolUse":
        _post_tool_use(session)
    elif event == "Stop":
        _stop()
    return None
