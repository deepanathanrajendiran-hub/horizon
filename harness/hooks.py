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

import evidence
import goal
import leases
import paths
import steps
import sys
import tree_hash
import re
import examples
import filelock
import surface
import install_gate
import hookguard
import reviewing

MARK = "horizon-hook"
# PostToolUse fires only after a tool succeeds; PostToolUseFailure after one fails (a Bash command exiting non-zero),
# and both count toward a capped session (reviewing.count_call).
_EVENTS = {"SessionStart": None, "PostToolUse": "*", "PostToolUseFailure": "*", "Stop": None}
# SessionStart once ran each goal's gate and needed longer; installs keep 300 s so their settings are not rewritten.
_TIMEOUTS = {"SessionStart": 300}

_CODE = (".py", ".pyi", ".js", ".ts", ".go", ".rs", ".java", ".rb", ".c", ".h", ".cpp", ".toml", ".cfg")

_NOT_SOURCE = {"venv", "env", "site-packages", "node_modules", "build", "dist", "__pycache__"}


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


_FAILED = re.compile(r"^(?:FAILED|ERROR)\s+(\S+::\S+)", re.M)

_FAILED_BODY = re.compile(r"^E\s+Failed: (.+)$", re.M)
_FAILED_LINE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+::\S+)(?:\s+-\s+(.*))?$", re.M)

_SUMMARY = re.compile(r"^=*\s*(\d+ (?:failed|passed|errors?)\b[^\n=]*?)\s*=*\s*$", re.M)


def _gate_digest(output: str) -> str:
    """The failing checks by name when the output lists them, else the output's tail.

    NL2Repo bench6: one ImportError failed all 241 surface names and the message listed names alphabetically,
    never the error; ydata's missing `utils` package hid in a truncated list of private helpers. So a message
    shared by three or more failures leads as the root cause, and surface names are counted by module."""
    output = output or ""
    # pytest's own list; captured log lines such as `ERROR    lib:x.py:4 boom` come before it
    summary_at = output.rfind("short test summary info")
    listed = output[summary_at:] if summary_at >= 0 else output
    ids, messages = [], {}
    for found, message in _FAILED_LINE.findall(listed):
        name = found.split("::")[-1]
        if name not in ids:
            ids.append(name)
            if message:
                text = re.sub(r"^Failed:\s*", "", message.strip())
                messages[text] = messages.get(text, 0) + 1
    if not ids:
        tail = " ".join(output.split())[-300:]
        return f": {tail}" if tail else "."
    parts = []
    # pytest cuts each summary line to the terminal width at a different point; the failure bodies keep the
    # whole message (`E   Failed: ...`)
    body = {}
    for text in _FAILED_BODY.findall(output):
        body[text.strip()] = body.get(text.strip(), 0) + 1
    shared = max([*messages.items(), *body.items()], key=lambda kv: kv[1], default=(None, 0))
    if shared[1] >= 3:
        parts.append(f'Root cause: {shared[1]} checks fail with "{shared[0]}"; fix that first.')
    names = [i[len("test_spec_name_exists["):-1] for i in ids
             if i.startswith("test_spec_name_exists[") and i.endswith("]")]
    if names:
        groups = {}
        for name in names:
            module = name.rsplit(".", 1)[0] if "." in name else name
            groups[module] = groups.get(module, 0) + 1
        ranked = sorted(groups.items(), key=lambda kv: -kv[1])  # stable: first seen first among equals
        more = f" and {len(ranked) - 10} more modules" if len(ranked) > 10 else ""
        parts.append("Missing names by module: " + ", ".join(f"{m} {n}" for m, n in ranked[:10]) + more + ".")
    # The package's own code before the packaging and docs files the spec's tree lists (NL2Repo held-out
    # flask-restful and tablib: nine packaging files were written before the package's __init__.py).
    others = sorted((i for i in ids if not i.startswith("test_spec_name_exists[")),
                    key=lambda i: i.startswith(("test_spec_file_exists[", "test_packaging_references")))
    summary = _SUMMARY.findall(output)
    tail = f" ({summary[-1]})." if summary else "."
    if others or not names:
        shown = others or ids
        more = f" and {len(shown) - 12} more" if len(shown) > 12 else ""
        files_last = (" (packaging and docs files last)" if any(i.startswith("test_spec_file_exists[") for i in shown)
                      and not all(i.startswith("test_spec_file_exists[") for i in shown) else "")
        parts.append(f"Failing{files_last}: {', '.join(shown[:12])}{more}{tail}")
    elif summary:
        parts.append(f"({summary[-1]}).")
    return ". " + " ".join(parts)


def _files_line(root: Path, limit: int = 40) -> str:
    """The project's files with their line counts, so a session need not list them. Never raises."""
    try:
        listed = []
        for extra in ([], ["--others", "--exclude-standard"]):  # tracked files first
            out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", *extra],
                                 capture_output=True, timeout=10)
            if out.returncode:
                return ""
            listed += [n for n in out.stdout.decode(errors="replace").split("\0") if n]
        names = []
        for name in dict.fromkeys(listed):
            parts = name.split("/")
            if any(p.startswith(".") or p in _NOT_SOURCE or p.endswith(".egg-info") for p in parts[:-1]):
                continue
            names.append(name)
        more = ""
        if len(names) > limit:
            code = [n for n in names if n.endswith(_CODE)]
            more = f" (and {len(names) - limit} more)"
            names = (code + [n for n in names if n not in code])[:limit]
        shown = []
        for name in names:
            path = root / name
            size = path.stat().st_size
            if size > 1024 * 1024:
                shown.append(f"{name} ({size // (1024 * 1024)} MB)")
                continue
            data = path.read_bytes()
            count = data.count(b"\n") + (1 if data and not data.endswith(b"\n") else 0)
            shown.append(f"{name} {count}")
        return ("Files (lines): " + ", ".join(shown) + more + ".") if shown else ""
    except Exception:
        return ""

_PATHLIKE = re.compile(r"[\w./-]+\.[A-Za-z]\w*")


def _resume_line(state: dict) -> str:
    """What changed since the next action was recorded, open findings and known facts (NL2Repo bench6: a
    stale next action in 14 of 16 builds; bugs and environment facts lost at the call cap)."""
    out = ""
    changed = goal.changed_since_next(state)
    if changed:
        more = f" (and {len(changed) - 15} more)" if len(changed) > 15 else ""
        out += f" Changed since the next action was recorded: {', '.join(changed[:15])}{more}."
        named = _PATHLIKE.findall(goal.row(state)["next_action"] or "")
        done = [c for c in changed if any(c == n or c.endswith("/" + n) or n.endswith("/" + c) for n in named)]
        if done:
            out += (f" The next action may already be done: {', '.join(done)} changed after it was recorded; "
                    "check before redoing it.")
    findings = [f["text"] for f in state.get("findings") or []]
    if findings:
        out += " Open findings: " + "; ".join(f"{i}) {t}" for i, t in enumerate(findings, 1)) + "."
    facts = [f["text"] for f in state.get("facts") or []]
    if facts:
        out += " Known facts: " + "; ".join(facts) + "."
    return out


def _placeholders(root: Path, limit: int = 10, facts=()) -> str:
    """Source files whose functions only raise NotImplementedError (abstract methods aside) or that call
    themselves placeholders (NL2Repo bench6 deepdiff: a green gate over five placeholder modules).

    Methods of abstract base classes (named Abstract*/Base*, or based on ABC/Protocol) are interfaces, not
    placeholders, and a file named in a recorded fact is the agent's settled judgment (NL2Repo held-out:
    DictDataBase and python-jose re-checked the same abstract stubs in 4-5 sessions each)."""
    import ast
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, timeout=10)
        if out.returncode:
            return ""
        names = [n for n in out.stdout.decode(errors="replace").split("\0") if n.endswith(".py")]
    except (OSError, subprocess.SubprocessError):
        return ""
    found = []
    for name in dict.fromkeys(names):
        parts = name.split("/")
        if (any(p.startswith(".") or p in _NOT_SOURCE or p in ("tests", "test") for p in parts[:-1])
                or parts[-1].startswith("test_") or parts[-1] == "conftest.py"):
            continue
        if any("by design" in fact.lower() and re.search(r"(?<![\w/.-])" + re.escape(name) + r"(?![\w/.-])", fact)
               for fact in facts):
            continue
        try:
            tree = ast.parse((root / name).read_text(errors="replace"))
        except (OSError, SyntaxError, ValueError):
            continue
        hits = []
        stack = [(node, "") for node in tree.body]
        while stack:
            node, prefix = stack.pop(0)
            if isinstance(node, ast.ClassDef):
                bases = {getattr(b, "attr", None) or getattr(b, "id", None) for b in node.bases}
                if re.match(r"(?:Abstract|Base)(?:[A-Z_]|$)", node.name) or bases & {"ABC", "Protocol"} or any(
                        (getattr(k.value, "attr", None) or getattr(k.value, "id", None)) == "ABCMeta"
                        for k in node.keywords):
                    continue
                stack += [(child, f"{prefix}{node.name}.") for child in node.body]
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                abstract = any((getattr(d, "attr", None) or getattr(d, "id", None)) == "abstractmethod"
                               for d in node.decorator_list)
                raises = any(isinstance(s, ast.Raise) and "NotImplementedError" in ast.unparse(s.exc or s)
                             for s in node.body)
                doc = (ast.get_docstring(node) or "").lower()
                if (raises and not abstract) or "placeholder" in doc:
                    hits.append(prefix + node.name)
        if "placeholder" in (ast.get_docstring(tree) or "").lower():
            hits.append("module marked placeholder")
        if hits:
            shown = ", ".join(hits[:4]) + (f" and {len(hits) - 4} more" if len(hits) > 4 else "")
            found.append(f"{name} ({shown})")
    if not found:
        return ""
    more = f"; and {len(found) - limit} more files" if len(found) > limit else ""
    return "Placeholders still to build: " + "; ".join(found[:limit]) + more + "."

def _files_changed_since_gate(state: dict) -> bool:
    """Whether no gate result covers the current files of a conversation-mode goal with a command gate.

    The hook used to run the gate here (NL2Repo bench2: sessions cut off before `check` showed the session-1 result
    for ten sessions), but hooks run outside the sandbox, and the agents' gates ran their setup scripts (E2E-SWE
    install run). Now it only says the result is old, and the session's own `check` runs the gate."""
    if state.get("execution") or state["gate"]["kind"] != "command":
        return False
    try:
        current = tree_hash.compute(state["cwd"], state.get("watch_paths"))
    except Exception:
        return False
    observed = goal.latest_observation(state)
    last = state["checkpoints"][-1] if state["checkpoints"] else None
    return not ((observed and observed.get("tree_hash") == current) or (last and last.get("tree_hash") == current)
                or evidence.passing_record(state, current))


def _goal_line(state: dict) -> str:
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
    # The newest gate result, whether an `observe` or a `check`.
    record = None
    if observed and (last is None or observed["at"] > (last.get("at") or "")):
        record = (observed["gate"], observed["role"], observed["at"], observed["gate_output"])
    elif last and last.get("gate") is not None:
        record = (last["gate"], "check", last.get("at") or "", last.get("gate_output") or "")
    if record:
        result, role, at, output = record
        line += f" Last gate run: {result} ({role}, {at[:16]}){_gate_digest(output)}"
        if _files_changed_since_gate(state):
            line += " The files changed after that run: `check` runs the gate on them."
    return line + _resume_line(state)







def _notes_line(slug: str, limit: int = 3000) -> str:
    """The notes file and its text, so a session need not read it (NL2Repo held-out: a notes `cat` opened every
    session). A long file is cut, and the message says where the rest is."""
    path = goal.notes_path(slug)
    if not path.exists():
        return ""
    try:
        text = path.read_text(errors="replace").strip()
    except OSError:
        text = ""
    header = goal.NOTES_HEADER.format(slug=slug).rstrip("\n")
    body = text[len(header):].strip() if text.startswith(header) else text
    if not body:
        return f" Notes: {path} (no parts yet)."
    cut = f"\n[cut at {limit} characters; the rest is in {path}]" if len(body) > limit else ""
    return f" Notes ({path}):\n{body[:limit]}{cut}\n"

def _examples_line(state: dict) -> str:
    """The latest result of the goal's advisory spec examples. The hook never runs them itself: spec code runs
    only through the `examples`, `check` and `close` commands, which the agent's own tool calls start."""
    gate_path = state.get("gate_path")
    if not gate_path or not (Path(gate_path) / examples.FILE).is_file():
        return ""
    line = examples.summary_line(examples.last(goal.notes_path(state["slug"]).with_suffix(".examples.json")))
    return f" {line}" if line else ""

def _surface_line(state: dict) -> str:
    """The names the goal's surface gate last found missing (from its saved result; the hook runs nothing more)."""
    last = surface._last(surface.record_path(state["slug"]))
    if not last or not last.get("failed"):
        return ""
    return (f" Names the request gives that did not import at the last check ({len(last['failed'])}): "
            f"{surface.listing(last['failed'], limit=8)}; `close` waits for them.")

def _install_line(state: dict) -> str:
    """What the goal's last install check found wrong (from its saved result; the hook runs nothing more)."""
    last = install_gate.last(state["slug"])
    if not last or last.get("ok") or last.get("error"):
        return ""
    return f" The package does not install the way the request says: {install_gate.problem(last)}; `close` waits for it."

def _review_line(state: dict) -> str:
    """When an earlier session held the goal's review brief back for lack of calls, or (with the session's call
    budget known) its hand-out went unanswered: start with `close`, which hands the brief out while a reviewer has
    the session's calls (from saved state; the hook runs nothing more)."""
    if reviewing.review_spent(state):
        return ""
    if isinstance(state.get("review_deferred"), dict):
        return (f" Start this session with `close {state['slug']}`: an earlier session held its review brief back "
                "for lack of calls, and a reviewer sent now has the calls to finish.")
    if reviewing.fresh_brief_due(state):
        return (f" Start this session with `close {state['slug']}`: no reviewer has answered its review brief yet, "
                "and a reviewer sent now has the calls to finish.")
    return ""




PLATEAU = ("Plateau: no source file changed in the last 2 sessions while this goal is open. LEAD "
           "(1) Start with the next action: NEXT. (2) Build the largest part of the request that is still unbuilt "
           "or a placeholder. (3) Run the work the way a user would, on the request's own examples and inputs, and "
           "fix what differs from what the request asks.")

# NL2Repo given fix: resumed sessions re-read the request, the gate and their own code until the 15-call cap
RESUME = (" Resume from the next action and the notes: change a file of the work within your first 5 tool calls "
          "this session, and read only what that change needs.")
REMIND_AT = (6, 11)  # tool calls into a resumed session at which an unchanged work gets a reminder
REMINDER = ("Horizon: {calls} tool calls this session and no file of the work has changed. Write now{next}; read "
            "only what that edit needs.")


def _plateau_line(state: dict) -> str:
    """Record this session's library fingerprint; two sessions in a row without a change raise the alarm. When
    the gate passes on the current files the library may simply be done, so the alarm sends it to close first."""
    digest = tree_hash.source_digest(paths.project_root(), docs=False,
                                     skip=[p for p in [state.get("gate_path")] if p])
    if digest is None:
        return ""
    path = goal.notes_path(state["slug"]).with_suffix(".sessions.json")  # bookkeeping: never hashed
    try:
        marks = json.loads(path.read_text())
    except (OSError, ValueError):
        marks = []
    marks = (marks + [digest])[-10:]
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(marks))
    except OSError:
        pass
    if not (len(marks) >= 3 and marks[-1] == marks[-2] == marks[-3]):
        return ""
    try:
        current = tree_hash.compute(state["cwd"], state.get("watch_paths"))
        passing = evidence.passing_record(goal.load(state["slug"]), current) is not None
    except Exception:
        passing = False
    lead = ("The gate passes on these files: if every part of the request is built, run `close` now. "
            "Otherwise, build this session." if passing else
            "Build this session; do not start a review. Make your first tool call this session an edit to the work, "
            "and read only what that edit needs.")
    next_action = goal.row(state)["next_action"] or "the part of the request you would write first"
    return " " + PLATEAU.replace("SLUG", state["slug"]).replace("LEAD", lead).replace("NEXT", next_action)

def _work_fingerprint() -> str | None:
    """Every project file a session can change, tests, docs, hidden files and the gate directory included, but not
    Horizon's bookkeeping; None outside a Git repository, where no reminder is given."""
    root = paths.harness_root()
    return tree_hash.source_digest(paths.project_root(), docs=True, hidden=True, tests=True,
                                   skip=[root / name for name in tree_hash.BOOKKEEPING])


def _worked_on(state: dict) -> bool:
    """Whether a session has worked on the goal: it has a next action or a checkpoint. A goal started from the
    command line before session 1 has neither."""
    return bool(goal.row(state)["next_action"] or state.get("checkpoints"))


def _progress_path() -> Path:
    path = paths.harness_root() / "notes" / "session-progress.json"  # bookkeeping: never hashed
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _mark_session(session: str | None, states: list[dict]) -> None:
    """At a session's start, after the gate ran, the work's fingerprint, when the session resumes a goal some
    session has worked on, so PostToolUse can tell when it has changed no file of the work. Session 1, a project
    outside Git, and a compacted or resumed session (which keeps its count) get no new mark."""
    resumed = [s["slug"] for s in states if _worked_on(s)]
    if not session or not resumed:
        return
    digest = _work_fingerprint()
    if digest is None:
        return
    path = _progress_path()
    with filelock.locked(path):
        try:
            old = json.loads(path.read_text())
        except (OSError, ValueError):
            old = {}
        if old.get("session") == session:
            return
        path.write_text(json.dumps({"session": session, "calls": 0, "changed": False, "digest": digest,
                                    "goals": resumed}))


def _reading_reminder(session: str | None, payload: dict | None = None) -> str:
    """After REMIND_AT tool calls in a resumed session that has changed no file of the work, a reminder that
    names the next action. A subagent's calls (a reviewer's reads) are not counted and get no reminder."""
    if not session or (isinstance(payload, dict) and (payload.get("agent_id") or payload.get("agent_type"))):
        return ""
    path = _progress_path()
    with filelock.locked(path):
        try:
            record = json.loads(path.read_text())
        except (OSError, ValueError):
            return ""
        if record.get("session") != session or record.get("changed") or not record.get("digest"):
            return ""
        record["calls"] = record.get("calls", 0) + 1
        out = ""
        if record["calls"] in REMIND_AT:
            if _work_fingerprint() != record["digest"]:
                record["changed"] = True
            else:
                states = {s["slug"]: s for s in _active_goals()}
                actions = [goal.row(states[g])["next_action"] for g in record.get("goals", []) if g in states]
                actions = [a for a in actions if a]
                out = REMINDER.format(calls=record["calls"], next=f": {'; '.join(actions)}" if actions else "")
        path.write_text(json.dumps(record))
    return out


def _differs_line(state: dict) -> str:
    """Sections of a long request whose Python code the work defines differently, so a session sees them before
    `close` (NL2Repo given fix: 3 of 5 builds never ran close)."""
    found = [m for m in goal.uncited_sections(state) if m.get("kind") == "differs"]
    if not found:
        return ""
    return (" The work defines differently the code these sections of the request give; fix these first: "
            + "; ".join(f'"{m["heading"]}" ({Path(m["file"]).name} line {m["line"]}): {m["detail"]}'
                        for m in found[:10]) + ".")


def _paths_line() -> str:
    entry = os.path.abspath(sys.argv[0]) if sys.argv and sys.argv[0].endswith(".py") else None
    gates = paths.harness_root() / "gates"
    if not entry:
        return f"Horizon: PROJECT={paths.project_root()} GATES_ROOT={gates}."
    # NL2Repo bench6: `$HARNESS` and `$PROJECT` set in one shell call were gone in the next, so 2-4 harness
    # calls per build failed; an old system python3 failed too. The hook's own interpreter runs the harness.
    runtime = paths.runtime()
    return (f"Horizon: HARNESS={entry} PROJECT={paths.project_root()} GATES_ROOT={gates} (use these; no need to "
            f"run `doctor`). Run Horizon as: \"{sys.executable}\" \"{entry}\" --runtime {runtime} --project "
            f"\"{paths.project_root()}\" <command>; shell variables do not carry over between commands, so "
            "paste the paths, not $HARNESS.")


def _last_commit_files(limit: int = 12) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(paths.project_root()), "show", "--name-only", "--format=", "HEAD"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    names = [n for n in out.stdout.splitlines() if n.strip()] if out.returncode == 0 else []
    return names[:limit] + (["…"] if len(names) > limit else [])


TRAIL_SAID, TRAIL_COMMAND, TRAIL_OUTPUT = 400, 300, 400
TRANSCRIPT_TAIL = 200_000  # bytes read from the end of the session's transcript
# A `check` or `close` saves the next action itself, so a session that ended on one has nothing more to tell.
_SAVED_BY_HORIZON = re.compile(r"harness\.py\b.*?\s(?:check|close)\s", re.S)


def _trail_path() -> Path:
    path = paths.harness_root() / "notes" / "last-call.json"  # bookkeeping: never hashed
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _flat(text, limit: int, end: bool = False) -> str:
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    return "…" + text[-limit:] if end else text[:limit] + "…"


def _last_said(transcript) -> str:
    """The last thing the session's own agent said, read from the end of its transcript; "" when it cannot be."""
    if not isinstance(transcript, str) or not transcript:
        return ""
    try:
        with open(transcript, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - TRANSCRIPT_TAIL))
            lines = handle.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict) or entry.get("type") != "assistant" or entry.get("isSidechain"):
            continue
        content = (entry.get("message") or {}).get("content")
        texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"] \
            if isinstance(content, list) else []
        texts = [t for t in texts if t.strip()]
        if texts:
            return texts[-1]
    return ""


def _call_text(payload: dict) -> tuple[str, str]:
    """A tool call as (what was run, what came back)."""
    given = payload.get("tool_input")
    if isinstance(given, dict):
        ran = given.get("command") or given.get("file_path") or given.get("pattern") or json.dumps(given)
    else:
        ran = str(given or "")
    name = payload.get("tool_name")
    if isinstance(name, str) and name != "Bash":
        ran = f"{name} {ran}"
    back = payload.get("error") or payload.get("tool_response")
    if isinstance(back, dict):
        parts = [back.get(k) for k in ("stdout", "stderr") if isinstance(back.get(k), str) and back.get(k).strip()]
        back = "\n".join(parts) if parts else ("" if "stdout" in back else json.dumps(back))
    return str(ran), str(back or "")


def _record_trail(session: str | None, payload: dict) -> None:
    """Keep the session's latest call and what its agent last said, at no model call, so the next session can be
    told where this one stopped. A subagent's calls are not kept.

    E2E-SWE thin run, pdfminer: session 4 said "Pages are found now, but the text is still empty", ran a probe that
    printed `contents []`, and was cut off by the call cap on that call. No `check` had run, so session 5 started
    from the baseline, worked on something else, and the goal closed with every page's text still empty."""
    if not session or not isinstance(payload, dict) or payload.get("agent_id") or payload.get("agent_type"):
        return
    if not _active_goals():
        return
    path = _trail_path()
    ran, back = _call_text(payload)
    if _SAVED_BY_HORIZON.search(ran + " "):
        path.unlink(missing_ok=True)
        return
    record = {"session": session, "said": _flat(_last_said(payload.get("transcript_path")), TRAIL_SAID),
              "command": _flat(ran, TRAIL_COMMAND), "output": _flat(back, TRAIL_OUTPUT, end=True)}
    path.write_text(json.dumps(record))


def _trail_line(session: str | None) -> str:
    """Where another session stopped, for a session's start; "" for the session's own calls."""
    try:
        record = json.loads(_trail_path().read_text())
    except (OSError, ValueError):
        return ""
    if not isinstance(record, dict) or record.get("session") == session or not record.get("command"):
        return ""
    said = f' it said "{record["said"]}";' if record.get("said") else ""
    printed = f', which gave: {record["output"]}' if record.get("output") else ""
    return ("Where the last session stopped (it ended after this call, before `check` could save it):"
            f"{said} its last call was `{record['command']}`{printed}. If that left a problem open, take it up "
            "first.")

def _session_start(session: str | None, source: str | None = None) -> dict | None:
    if session:
        root = paths.harness_root()
        root.mkdir(parents=True, exist_ok=True)
        (root / "session").write_text(session + "\n")
    # A new start, a resume (which keeps its session id) or a clear gets a new call count; a compaction does not.
    fresh = source != "compact"
    if fresh:
        try:
            reviewing.reset_calls(session)
        except Exception:
            pass
    lines = []
    # A session really starts: not a resume, clear or compaction.
    startup = source in (None, "startup")
    known = []
    active = _active_goals()
    for state in active:
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
            try:
                line += (_examples_line(state) + _surface_line(state) + _install_line(state)
                         + (_review_line(state) if fresh else ""))
            except Exception:
                pass
            plateau = _plateau_line(state) if startup else ""
            try:
                differ = _differs_line(state)
            except Exception:
                differ = ""
            known += [f["text"] for f in state.get("facts") or []]
            resume = RESUME if _worked_on(state) else ""
            lines.append(f"{line}{plateau}{differ}{resume}{note}{_notes_line(state['slug'])} Run "
                         f"`close {state['slug']}` when you think it is done.")
        except Exception:
            continue
    if startup:
        try:
            _mark_session(session, _active_goals())  # after the gate ran, which may write files
        except Exception:
            pass
    if not lines:
        # Session 1 of a goal still needs the paths and the files (bench4 pilot: `doctor`, `status`, `ls`).
        lines.append("No active Horizon goal.")
    lines.insert(0, _paths_line())
    changed = _last_commit_files()
    if changed:
        lines.append("The last commit changed: " + ", ".join(changed) + ".")
    files = _files_line(paths.project_root())
    if files:
        lines.append(files)
    stubs = _placeholders(paths.project_root(), facts=known)
    if stubs:
        lines.append(stubs)
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


UNATTENDED_ENV = "HORIZON_UNATTENDED"
# E2E-SWE paper run: with the goal active, django_cqrs, pytermgui, monkeytype and websockets ended capped sessions
# asking for a person ("blocked on you", "I need your help") in a run with no person, because the second stop in a
# row always went through. With HORIZON_UNATTENDED set, the Stop hook sends an active goal back to work this many
# times a session; the bound keeps a run with no call cap from looping.
UNATTENDED_STOP_BLOCKS = 5


def _unattended() -> bool:
    return os.environ.get(UNATTENDED_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def _stop_block_allowed(session: str | None) -> bool:
    """Count one more unattended block for this session; False once it has had UNATTENDED_STOP_BLOCKS. Counts are
    kept per session, so sessions of one project that stop in turn do not reset each other. When the count cannot
    be kept, the stop goes through, as without HORIZON_UNATTENDED: an uncounted block could loop."""
    key = session or "unknown"
    path = paths.harness_root() / "notes" / "stop-blocks.json"  # bookkeeping: never hashed
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with filelock.locked(path):
            try:
                record = json.loads(path.read_text())
            except (OSError, ValueError):
                record = {}
            counts = record.get("sessions") if isinstance(record, dict) else None
            counts = {k: v for k, v in counts.items() if isinstance(v, int)} if isinstance(counts, dict) else {}
            blocks = counts.pop(key, 0)
            if blocks >= UNATTENDED_STOP_BLOCKS:
                return False
            counts[key] = blocks + 1
            path.write_text(json.dumps({"sessions": dict(list(counts.items())[-50:])}))
            return True
    except Exception:
        return False


def _stop(again: bool = False, session: str | None = None) -> dict | None:
    for state in _active_goals():
        try:
            goal.close(state["slug"], run_gate=False)
        except Exception:
            continue
    unattended = _unattended()
    if again and not unattended:
        # The second stop in a row always goes through, so a person can still be answered.
        return None
    # NL2Repo bench6: Haiku ended capped sessions itself to hand off, and an unattended run reads that as done.
    open_goals, lines = [], False
    for state in _active_goals():
        try:
            if goal.execution_mode(state) == "conversation" and not goal.budget_exhausted(state):
                lines = lines or not state.get("thin")  # a thin goal has no requirement lines to retag
                open_goals.append(f"{state['slug']} (next: {goal.row(state)['next_action'] or 'not set'})")
        except Exception:
            continue
    if not open_goals:
        return None
    if unattended:
        if not _stop_block_allowed(session):
            return None
        return {"decision": "block",
                "reason": ("Horizon goal still active with budget left: " + "; ".join(open_goals) + ". This run "
                           "is unattended: no one will answer a question or approve anything, so do not stop "
                           "to wait for a person or a reviewer. Keep building: do the next action now and keep "
                           "`note --next` current. If you need a decision, make it and record it with `note "
                           "--fact`; if the environment blocks a check, record that with `note --fact`, take "
                           "the check out of the gate" + (", retag its line `[review]`" if lines else "")
                           + ", and go on. Stop only once "
                           "`close` completes the goal.")}
    return {"decision": "block",
            "reason": ("Horizon goal still active with budget left: " + "; ".join(open_goals) + ". A session's "
                       "length is not the goal's deadline, and your work is committed whenever the session is "
                       "cut off, so keep building: do the next action now and keep `note --next` current. "
                       "Stop only if the goal is done (run `close`), or someone is here and you need them; "
                       "then stop again. In an unattended run no one will answer a question: if you need a "
                       "decision, make it, record it with `note --fact`, and keep building.")}


def _with_trail(out: dict | None, session: str | None) -> dict | None:
    """Add where the last session stopped to a session-start message, while a goal is active."""
    try:
        line = _trail_line(session) if out and _active_goals() else ""
        if line:
            out["hookSpecificOutput"]["additionalContext"] += "\n" + line
    except Exception:
        pass
    return out


def _dispatch(event: str, payload: dict) -> dict | None:
    session = payload.get("session_id") if isinstance(payload, dict) else None
    if not isinstance(session, str):
        session = None
    if event == "SessionStart":
        source = payload.get("source") if isinstance(payload, dict) else None
        return _with_trail(_session_start(session, source if isinstance(source, str) else None), session)
    if event in ("PostToolUse", "PostToolUseFailure"):
        try:
            reviewing.count_call(session)
        except Exception:
            pass
        try:
            _record_trail(session, payload)
        except Exception:
            pass
        if event == "PostToolUseFailure":
            return None
        _post_tool_use(session)
        try:
            reminder = _reading_reminder(session, payload)
        except Exception:
            reminder = ""
        if reminder:
            return {"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": reminder}}
    elif event == "Stop":
        return _stop(isinstance(payload, dict) and payload.get("stop_hook_active") is True, session)
    return None


def run(event: str, payload: dict) -> dict | None:
    """Claude Code runs hooks outside the agent's sandbox, so nothing a hook does may run the project's code."""
    with hookguard.active():
        return _dispatch(event, payload)
