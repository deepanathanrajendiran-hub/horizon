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
import examples

MARK = "horizon-hook"
_EVENTS = {"SessionStart": None, "PostToolUse": "*", "Stop": None}
# SessionStart runs each active goal's gate on changed files (SESSION_GATE_S at most), so it needs longer.
_TIMEOUTS = {"SessionStart": 300}

_CODE = (".py", ".pyi", ".js", ".ts", ".go", ".rs", ".java", ".rb", ".c", ".h", ".cpp", ".toml", ".cfg")

_NOT_SOURCE = {"venv", "env", "site-packages", "node_modules", "build", "dist", "__pycache__"}
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
    return line + _resume_line(state)


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


def _source_digest(root: Path) -> str | None:
    """A fingerprint of the project's library source: .py files outside tests, hidden and build folders."""
    import hashlib
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode:
        return None
    digest = hashlib.sha256()
    for name in sorted(set(n for n in out.stdout.decode(errors="replace").split("\0") if n.endswith(".py"))):
        parts = name.split("/")
        if (any(p.startswith(".") or p in _NOT_SOURCE or p in ("tests", "test") for p in parts[:-1])
                or parts[-1].startswith("test_") or parts[-1] == "conftest.py"):
            continue
        try:
            data = (root / name).read_bytes()
        except OSError:
            continue
        digest.update(name.encode() + b"\0" + data + b"\0")
    return digest.hexdigest()


PLATEAU = ("Plateau: no library source changed in the last 2 sessions while this goal is open. LEAD "
           "(1) Run `examples SLUG` if this goal has spec examples, and fix "
           "each disagreement the spec's prose supports. (2) If the spec describes an existing library, its users "
           "and its own tests call its whole public API, not only the names the spec lists: write down the public "
           "functions, classes, methods, parameters, return types and exception classes you know the real project "
           "has, and build what is missing or different. (3) Re-read a part of the spec you have not read in the "
           "last three sessions and compare it with the code.")


def _plateau_line(state: dict) -> str:
    """Record this session's library fingerprint; two sessions in a row without a change raise the alarm. When
    the gate passes on the current files the library may simply be done, so the alarm sends it to close first."""
    digest = _source_digest(paths.project_root())
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
            "Otherwise, build this session." if passing else "Build this session; do not start a review.")
    return " " + PLATEAU.replace("SLUG", state["slug"]).replace("LEAD", lead)


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


def _session_start(session: str | None, source: str | None = None) -> dict | None:
    if session:
        root = paths.harness_root()
        root.mkdir(parents=True, exist_ok=True)
        (root / "session").write_text(session + "\n")
    lines = []
    # Gates run only when a session really starts: not on resume, clear or compaction.
    startup = source in (None, "startup")
    deadline = time.monotonic() + _gate_budget()
    known = []
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
            try:
                line += _examples_line(state)
            except Exception:
                pass
            plateau = _plateau_line(state) if startup else ""
            known += [f["text"] for f in state.get("facts") or []]
            lines.append(f"{line}{plateau}{note}{_notes_line(state['slug'])} Run `close {state['slug']}` "
                         "when you think it is done.")
        except Exception:
            continue
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


def _stop(again: bool = False) -> dict | None:
    for state in _active_goals():
        try:
            goal.close(state["slug"], run_gate=False)
        except Exception:
            continue
    if again:
        # The second stop in a row always goes through, so a person can still be answered.
        return None
    # NL2Repo bench6: Haiku ended capped sessions itself to hand off, and an unattended run reads that as done.
    open_goals = []
    for state in _active_goals():
        try:
            if goal.execution_mode(state) == "conversation" and not goal.budget_exhausted(state):
                open_goals.append(f"{state['slug']} (next: {goal.row(state)['next_action'] or 'not set'})")
        except Exception:
            continue
    if not open_goals:
        return None
    return {"decision": "block",
            "reason": ("Horizon goal still active with budget left: " + "; ".join(open_goals) + ". A session's "
                       "length is not the goal's deadline, and your work is committed whenever the session is "
                       "cut off, so keep building: do the next action now and keep `note --next` current. "
                       "Stop only if the goal is done (run `close`), or someone is here and you need them; "
                       "then stop again. In an unattended run no one will answer a question: if you need a "
                       "decision, make it, record it with `note --fact`, and keep building.")}


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
        return _stop(isinstance(payload, dict) and payload.get("stop_hook_active") is True)
    return None
