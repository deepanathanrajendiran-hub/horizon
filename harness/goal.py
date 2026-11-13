"""Goal state for horizon (spec §5.1, §5.5).

Checkpointing after every increment is the mechanism behind "long
horizon" — it is what lets work survive context loss, session restart, or
a /loop firing. Nothing here trusts in-memory state.
"""

import json
import os
import re
import shlex
from datetime import datetime, timedelta, timezone

import filelock
import paths
import tree_hash
import copy
import task_state
from typing import Callable
import reviewing
from pathlib import Path
import evidence
import gate
import evidence as evidence_rules  # _apply_review has a parameter named evidence
import sections
import subprocess
import surface
import install_gate
import hookguard
import stubs
import unbuilt

MAX_GATE_OUTPUT = 4000

# A reviewer's reply leads with the checkpoint echo and the first requirement
# verdicts; the pilot's longest replies ran past 5,000 characters, so the
# 4,000-character tail kept for gate output would have dropped exactly those.
MAX_REVIEW_EVIDENCE = 16000
SLUG_MAX = 60
# Long enough to require a sentence, short enough never to reject a real
# verdict. Calibrated against the degenerate ones actually observed ("Test").
MIN_COVERAGE_EVIDENCE = 40
# Matched case-insensitively against a subagent's `role`, so both
# "coverage-judge" and a bare "judge" register. Substring rather than an
# exact string because the cost of the two disagreeing is a refusal on a
# judge that genuinely ran -- the one direction that teaches an agent to
# route around the check.
JUDGE_ROLE_MARKER = "judge"

OBSERVATION_ROLES = ("baseline", "resume", "final")


class GateRequired(Exception):
    """Raised when a goal is created without a termination criterion."""


class GateUnsound(GateRequired):
    """The gate command cannot report the failure it exists to detect.

    `run_gate` judges a command gate by its exit status, and `/bin/sh` has
    no `pipefail`. So a gate the agent writes without thinking —

        pytest -q | tail -30        exit status of `tail`, always 0
        pytest -q; echo done        exit status of `echo`, always 0
        pytest -q || true           always 0

    — reports `pass` against a suite printing `3 failed`, and the goal is
    marked complete with stored "evidence" that reads like a success. Every
    other safeguard here is downstream of this one string: the checkpoint
    honestly records what the gate honestly returned, and `GateNotPassed`
    is satisfied, so nothing further can catch it.

    The pipeline shape is the likely one, not an exotic one — an agent
    wanting bounded output reaches for `| tail` first. `run_gate` already
    bounds output to `gate.MAX_OUTPUT`, so it is also unnecessary.
    """


class GoalStateError(Exception):
    """Raised when goal state is missing or unreadable. Never guess."""

class RevisionConflict(GoalStateError):
    """The on-disk execution revision changed before a commit."""


class AttemptOpen(GoalStateError):
    """A lifecycle mutation requires the active attempt to be closed."""


class GateNotPassed(Exception):
    """Raised when completion is attempted without a passing gate."""


class EvidenceInsufficient(GateNotPassed):
    """A coverage goal is completing on a verdict with no substance in it.

    A command gate's evidence can legitimately be empty -- `test -f x` and
    most linters print nothing on success, and the exit status is the
    proof. A coverage gate has no exit status: the judge's written verdict
    IS the entire proof, so a blank or contentless one means the judge did
    not actually run, or returned garbage that nobody read.

    Not hypothetical. In a nine-judge trial two returned the literal
    string "Test" as their reasoning, with a verdict attached. Stored
    unchecked, that is a goal marked complete on the word of a judge that
    plainly never looked -- and it reads, in the record, exactly like a
    real verdict.

    A subclass of GateNotPassed so existing handlers still catch it.
    """


class JudgeNotRecorded(GateNotPassed):
    """A coverage goal is completing with no judge recorded for the turn.

    MIN_COVERAGE_EVIDENCE checks that a verdict has substance; nothing
    checked where it came from. A worker that graded its own work and
    wrote a thoughtful paragraph produced a record byte-indistinguishable
    from one an independent judge returned -- and the skill's rule that
    the worker "must not grade its own work" lived only in prose.

    So provenance is now a precondition: `add_subagent` must carry an
    entry whose role names a judge, recorded during the turn being
    completed (`last_seen_turn >= that turn - 1`, since the judge is
    dispatched before the checkpoint increments the count). A judge from
    an earlier turn does not carry forward: it graded an older tree, and
    accepting it would let one real judgement certify every later turn.

    This cannot prove independence -- a worker may still record an entry
    and write the verdict itself. It makes the claim explicit and dated
    in the durable record instead of absent from it, which is the most
    the state file can do; the skill still carries the rule.

    Consequence worth stating: in an environment with no subagent
    dispatch, a coverage goal cannot be completed at all. That is the
    intended reading of "judged by a fresh subagent", not a regression.

    A subclass of GateNotPassed so existing handlers still catch it.
    """


class BudgetInvalid(Exception):
    """Raised when a goal is created with a budget that cannot bound it."""

class BudgetExhausted(GoalStateError):
    """A checkpoint would record a turn beyond max_turns.

    The exhausting turn itself (turn == max_turns) always records — the
    checkpoint-FIRST invariant protects its increment. Refused is the turn
    AFTER it: work past the cap is spend nobody granted, and a module that
    records it forever leaves the budget purely advisory. Recovery:
    abandon ("budget spent, work remains"), reopen with an explicit
    extra_turns grant, and the checkpoint lands.

    Wall-clock exhaustion never refuses: a turn that started before the
    deadline has its work on disk by checkpoint time, and losing that
    increment is the exact failure the "unknown" sentinel exists to
    prevent. The clock is a query (budget_exhausted) the loop stops on.
    """


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


def _bounded_review(text: str) -> str:
    """Keep a long reviewer reply's head and tail; both carry verdicts."""
    if len(text) <= MAX_REVIEW_EVIDENCE:
        return text
    half = MAX_REVIEW_EVIDENCE // 2
    return text[:half] + "\n[truncated]...\n" + text[-half:]


# Shell operators that replace the gate's exit status with something
# else's. `&&` is absent deliberately: `a && b` fails when `a` fails, so a
# chain still reports the failure. `2>&1` is a redirect, not an operator,
# and shlex never yields it as a bare token.
EXIT_MASKING_OPERATORS = {"|", "|&", ";", "||", "&"}


def _assert_sound_gate(value: str) -> None:
    """Refuse a command gate whose exit status is not the work's verdict.

    Tokenised with shlex rather than searched as a substring, so an
    operator *inside a quoted argument* — `sh -o pipefail -c 'pytest | tail'`,
    which is sound — is not mistaken for one at the top level.

    `punctuation_chars=True` is what makes the operators their own tokens.
    Plain `shlex.split` leaves `pytest -q; echo done` as `['pytest', '-q;',
    …]`, hiding the `;` inside a word — the check silently passed the very
    shape it exists to catch until a test caught it.
    """
    try:
        lexer = shlex.shlex(value, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return  # unbalanced quotes; the shell reports it, run_gate raises
    masking = sorted({t for t in tokens if t in EXIT_MASKING_OPERATORS})
    if masking:
        raise GateUnsound(
            f"gate {value!r} discards the exit status of the work it judges "
            f"(operator {', '.join(masking)}). The gate must BE the command "
            f"whose failure matters. run_gate already bounds output to 4000 "
            f"chars, so `| tail` buys nothing; if a pipeline is genuinely "
            f"needed, make the failure propagate — "
            f"sh -o pipefail -c 'cmd | filter' — and quote it as one argument."
        )


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


def _covers(root: str, path: str) -> bool:
    return os.path.commonpath([root, path]) == root


def _resolve_gate_path(cwd, gate_path) -> str:
    """The goal's gate directory, refused unless the request exists; requirement lines too, when it has that file.

    C1 asks for the request verbatim before `start`; checking for it here is the one place prose can be enforced.
    A directory with no requirements.md starts a thin goal (`_is_thin`): its gate is the project's own tests plus
    Horizon's install and surface checks, with no requirement lines and no reviewer.
    """
    resolved = os.path.abspath(os.path.join(os.path.abspath(str(cwd)), str(gate_path)))
    directory = Path(resolved)
    if not directory.is_dir():
        raise GateRequired(
            f"--gate-path {resolved} is not a directory; write request.md there before start")
    try:
        request = (directory / "request.md").read_text()
    except (OSError, UnicodeError) as exc:
        raise GateRequired(f"{directory / 'request.md'} is missing or unreadable: {exc}") from exc
    if not request.strip():
        raise GateRequired(f"{directory / 'request.md'} is blank; save the request verbatim")
    if (directory / "requirements.md").exists():
        try:
            requirements = (directory / "requirements.md").read_text()
        except (OSError, UnicodeError) as exc:
            raise GateRequired(f"{directory / 'requirements.md'} is unreadable: {exc}") from exc
        if not reviewing.requirement_lines(requirements):
            raise GateRequired(
                f"{directory / 'requirements.md'} has no requirement lines (R1, R2, ...)")
    return resolved


def _requirements(state: dict) -> dict | None:
    """The goal's requirement lines, or None when it has no gate directory or started without the file (thin)."""
    gate_path = state.get("gate_path")
    if not gate_path or state.get("thin"):
        return None
    target = Path(gate_path) / "requirements.md"
    try:
        lines = reviewing.requirement_lines(target.read_text())
    except (OSError, UnicodeError) as exc:
        raise EvidenceInsufficient(
            f"{target} is unreadable ({exc}); restore it, run check, and review again"
        ) from exc
    if not lines:
        # Otherwise rewriting the file as a table would switch the check off.
        raise EvidenceInsufficient(
            f"{target} has no requirement lines (R1, R2, ...); restore them, run check, "
            f"and review again")
    return lines


def _earlier_goals(cwd) -> list[dict]:
    """The other goals this project already has, oldest first, as saved. Never raises."""
    found = []
    try:
        here = os.path.abspath(str(cwd))
        for path in paths.goals_dir().glob("*.json"):
            if path.name.endswith(".leases.json"):
                continue
            try:
                earlier = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(earlier, dict) and os.path.abspath(str(earlier.get("cwd") or here)) == here:
                found.append(earlier)
    except Exception:
        return []
    return sorted(found, key=lambda state: str((state.get("used") or {}).get("started") or ""))


TESTS_BEFORE = "tests-before.json"  # in Horizon's state folder: the test files as a session with no goal began

def save_tests_before(cwd=None) -> None:
    """Record the project's test files, with their digests, as a session that has no goal yet begins. The
    SessionStart hook calls this: it reads files and runs nothing. `_baseline_tests` reads it back at `start`, so the
    tests the session then writes before it calls `start` are told apart from the ones that were already there,
    whoever wrote those and whether or not they were committed. Never raises."""
    try:
        root = os.path.abspath(str(cwd or paths.project_root()))
        record = {"at": _now(), "cwd": root, "tests": evidence.test_manifest(root, None)}
        path = paths.harness_root() / TESTS_BEFORE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record))
    except Exception:
        pass


def _tests_before(cwd) -> dict | None:
    """The record `save_tests_before` made for this project folder, or None."""
    try:
        record = json.loads((paths.harness_root() / TESTS_BEFORE).read_text())
    except (OSError, ValueError):
        return None
    if (not isinstance(record, dict) or not isinstance(record.get("tests"), dict)
            or not isinstance(record.get("at"), str)
            or os.path.abspath(str(record.get("cwd") or "")) != os.path.abspath(str(cwd))):
        return None
    return record


def _watched(cwd, rel: str, watch_paths) -> bool:
    """Whether a project-relative path lies in the watched paths."""
    path = (Path(cwd) / rel).resolve()
    for watch in watch_paths or [cwd]:
        base = Path(watch).resolve()
        if path == base or base in path.parents:
            return True
    return False


def _own_tests(state: dict) -> list[str]:
    """The test files a goal's work added: the ones there now that its baseline does not hold. Recorded when the
    goal completes or is abandoned, so a later goal of the project does not take them as baseline. A goal that
    kept no baseline (a coverage gate) records none."""
    if not isinstance(state.get("test_manifest"), dict):
        return []
    try:
        return sorted(set(evidence.test_manifest(state["cwd"], state.get("watch_paths")))
                      - set(state["test_manifest"]))
    except Exception:
        return []


def _baseline_tests(cwd, watch_paths) -> dict[str, str]:
    """The test files a goal may not change without approval: the ones the project had before the work began.

    A test file the session wrote before it called `start` is the goal's own work, and so is one an earlier goal of
    the project added. What was there before is read from the record the SessionStart hook made when the session
    began with no goal (`save_tests_before`), with the digests the files had then:

    - no such record (no hook ran): every test file there now is baseline, as before;
    - a goal of the project began after the record was made: the files the record names, as they are now. What was
      added since is that goal's work;
    - otherwise: the files the record names, as they were when the session began. One the session changed or
      deleted before `start` shows as changed.

    Less, in each case, the tests an earlier goal recorded as its own (`_own_tests`).

    Git is not asked: a test a person wrote and has not committed is theirs all the same, and the first version of
    this rule, which took every uncommitted file for the session's work, let such a test be weakened with no hold.
    Nor is the first goal's list taken for good: a test added to the project after that goal is baseline for the
    next one.

    E2E-SWE thin6 run, django-modelcluster: the session wrote tests/models.py and a smoke test, then called `start`.
    Marking its own smoke test `django_db` in session 2 made `close` ask for "a reviewer's or the user's approval"
    in a run with neither; sessions 5 to 8 went on that, and the build ended at 82.4 (plain 93.4). thin12 tremolo:
    goal 2 took the test file goal 1 had written as baseline. Also thin8 protobuf, thin9 huey and inline-snapshot."""
    manifest = evidence.test_manifest(cwd, watch_paths)
    earlier = _earlier_goals(cwd)
    own = {rel for g in earlier for rel in g.get("own_tests") or () if isinstance(rel, str)}
    before = _tests_before(cwd)
    if before is None:
        base = manifest
    elif any(str((g.get("used") or {}).get("started") or "") >= before["at"] for g in earlier):
        base = {rel: digest for rel, digest in manifest.items() if rel in before["tests"]}
    else:
        base = {rel: digest for rel, digest in before["tests"].items()
                if rel in manifest or (not (Path(cwd) / rel).exists() and _watched(cwd, rel, watch_paths))}
    return {rel: digest for rel, digest in base.items() if rel not in own}


def _carried_facts(cwd, limit: int = 20) -> list[dict]:
    """The facts earlier goals of this project recorded: an environment fact holds for the next goal too (thin12
    tremolo: a fact of goal 1 was worked out again in sessions 8, 9 and 10 under goal 2).

    A fact that releases a hold is not carried: `install check: ...` and `... by design` answer for one goal's
    request, and the next goal's request may say otherwise."""
    seen, out = set(), []
    for earlier in _earlier_goals(cwd):
        for fact in earlier.get("facts") or []:
            text = fact.get("text") if isinstance(fact, dict) else None
            if not text or text in seen or install_gate.waived([text]) or "by design" in text.lower():
                continue
            seen.add(text)
            out.append({"at": fact.get("at") or _now(), "text": text})
    return out[-limit:]

def _initial_state(goal, cwd, gate_kind, gate_value, max_turns,
                   max_minutes, watch_paths, review_criterion, gate_path=None):
    cwd = os.path.abspath(str(cwd))
    state = {
        "slug": slugify(goal), "goal": goal, "cwd": cwd,
        "gate": {"kind": gate_kind, "value": gate_value},
        "watch_paths": [os.path.abspath(os.path.join(cwd, str(p)))
                        for p in (watch_paths or [cwd])],
        "budget": {"max_turns": max_turns, "max_minutes": max_minutes},
        "used": {"turns": 0, "started": _now()}, "status": "active",
        "subagents": [], "checkpoints": [],
    }
    if review_criterion is not None:
        state["gate"]["review_criterion"] = review_criterion.strip()
    if gate_path is not None:
        # Top-level, never in state["gate"]: checkpoints compare that dict.
        state["gate_path"] = gate_path
        if not (Path(gate_path) / "requirements.md").exists():
            # Fixed at start: a goal that began with requirement lines keeps them when the file goes missing.
            state["thin"] = True
            state["placeholders_at_start"] = _placeholders_at_start(cwd)
        if not any(_covers(watch, gate_path) for watch in state["watch_paths"]):
            state["watch_paths"].append(gate_path)
    if gate_kind == "command":
        # Recorded before any gate run, so a later edit to a test or to the
        # gate's own scripts shows up as a change rather than as the baseline.
        state["test_manifest"] = _baseline_tests(cwd, state["watch_paths"])
        state["gate_manifest"] = evidence.gate_manifest(cwd, str(gate_value), state["watch_paths"])
        head = _git_head()
        if head:
            state["base_head"] = head  # the commit the goal began on: where a changed baseline test is put back from
    facts = _carried_facts(cwd)
    if facts:
        state["facts"] = facts
    if any(g.get("thin") for g in _earlier_goals(cwd)):
        # Not the project's first goal: its files at `start` are an earlier goal's work, so a `close` on them
        # unchanged still goes through the request check (`request_check`).
        state["follows_goal"] = True
    return state


def _validate_state_execution(state: dict) -> None:
    if "execution" not in state:
        return
    task_state.validate_execution(state["execution"])
    attempts = state["execution"]["attempts"]
    turns = [attempt["turn"] for attempt in attempts]
    if turns != list(range(1, len(attempts) + 1)):
        raise task_state.StateError("attempt turn numbers must be sequential and unique")
    used_turns = state["used"]["turns"]
    if isinstance(used_turns, bool) or not isinstance(used_turns, int) or used_turns != len(attempts):
        raise task_state.StateError("used.turns must equal the number of reserved attempts")
    terminal_turns = [attempt["turn"] for attempt in attempts
                      if attempt["status"] in {"accepted", "recovered"}]
    checkpoint_turns = [checkpoint["turn"] for checkpoint in state["checkpoints"]]
    if (any(isinstance(turn, bool) or not isinstance(turn, int) for turn in checkpoint_turns)
            or checkpoint_turns != terminal_turns):
        raise task_state.StateError("terminal attempts must correspond one-to-one with checkpoint turns")

def _uncommitted(cwd) -> set[str] | None:
    """Files that are new or differ from the last commit, or None when git cannot say."""
    try:
        out = subprocess.run(["git", "-C", str(cwd), "status", "--porcelain", "-z", "--untracked-files=all"],
                             capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode:
        return None
    return {entry[3:] for entry in out.stdout.decode(errors="replace").split("\0") if len(entry) > 3}


def _placeholders_at_start(cwd) -> list[str]:
    """The placeholder functions the project had before its first thin goal (`file::name`), which no goal is held
    to: an existing codebase's own `raise NotImplementedError` is not the work of a goal that changes something
    else. Only committed files count: what the session wrote before it called `start` is the goal's own work
    (E2E-SWE thin6 run, barf: the session laid out 29 placeholder functions two calls before `start`, they were
    taken as the project's own, and the goal completed with 12 of them unbuilt). A later goal of the same project
    takes the first goal's list, so what an earlier goal left unbuilt still counts."""
    first = None
    try:
        for path in paths.goals_dir().glob("*.json"):
            if path.name.endswith(".leases.json"):
                continue
            try:
                earlier = json.loads(path.read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(earlier, dict) or not earlier.get("thin"):
                continue
            started = str((earlier.get("used") or {}).get("started") or "")
            if first is None or started < first[0]:
                first = (started, list(earlier.get("placeholders_at_start") or []))
    except Exception:
        first = None
    if first is not None:
        return first[1]
    try:
        changed = _uncommitted(cwd)
        if changed is None:
            return []
        return stubs.keys([(name, hits) for name, hits in stubs.find(cwd) if name not in changed])
    except Exception:
        return []

def create(goal, cwd, gate_kind, gate_value,
           max_turns=20, max_minutes=None, watch_paths=None, review_criterion=None,
           *, execution: str = "conversation", gate_path=None) -> dict:
    if execution not in ("conversation", "state"):
        raise GoalStateError(f"unsupported execution mode: {execution!r}")
    if execution == "state" and not filelock.state_supported():
        raise GoalStateError("state execution is unsupported on this platform")
    if not gate_value or not str(gate_value).strip():
        raise GateRequired("A goal requires a command or coverage criterion.")
    if gate_kind not in ("command", "coverage"):
        raise GateRequired(f"gate_kind must be command or coverage, got {gate_kind!r}")
    if gate_kind == "command":
        _assert_sound_gate(str(gate_value))
    if review_criterion is not None and (
            not isinstance(review_criterion, str) or not review_criterion.strip()):
        raise GateRequired("review_criterion must be nonempty text when supplied")
    for name, value in (("max_turns", max_turns), ("max_minutes", max_minutes)):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value <= 0):
            raise BudgetInvalid(f"{name} must be a positive integer or None")
    if max_turns is None and max_minutes is None:
        raise BudgetInvalid("A goal needs at least one of max_turns or max_minutes")
    if gate_path is not None:
        gate_path = _resolve_gate_path(cwd, gate_path)
    if execution == "state":
        state = _initial_state(goal, cwd, gate_kind, gate_value, max_turns,
                               max_minutes, watch_paths, review_criterion, gate_path)
        state["execution"] = task_state.new_execution()
        try:
            task_state.build_packet(
                state, attempt_id="A" * 64, base_revision=1,
                current_hash="0" * 64, runtime=paths.runtime(),
                reserve_verdicts=True,
            )
        except task_state.StateError as error:
            raise GoalStateError(f"initial packet cannot be created: {error}") from error
        with filelock.goal_locked(paths.goals_dir() / "CREATE"):
            state["slug"] = _unique_slug(state["slug"])
            return _save(state)
    with filelock.goal_locked(paths.goals_dir() / "CREATE"):
        return _create_locked(goal, cwd, gate_kind, gate_value, max_turns,
                              max_minutes, watch_paths, review_criterion, gate_path)


def _create_locked(goal, cwd, gate_kind, gate_value,
                   max_turns, max_minutes, watch_paths, review_criterion=None,
                   gate_path=None) -> dict:
    state = _initial_state(goal, cwd, gate_kind, gate_value, max_turns,
                           max_minutes, watch_paths, review_criterion, gate_path)
    state["slug"] = _unique_slug(state["slug"])
    return _save(state)


def load(slug: str) -> dict:
    p = _path(slug)
    if not p.exists():
        raise GoalStateError(f"no goal state at {p}")
    try:
        state = json.loads(
            p.read_text(),
            object_pairs_hook=task_state._no_duplicates,
            parse_constant=task_state._nonfinite,
            parse_float=task_state._finite_float,
        )
    except (UnicodeError, json.JSONDecodeError, OSError, task_state.StateError) as e:
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

    # Existing callers index these checkpoint fields directly.
    for i, cp in enumerate(state["checkpoints"]):
        if not isinstance(cp, dict):
            raise GoalStateError(
                f"goal state at {p}: checkpoint {i} is "
                f"{type(cp).__name__}, expected object — refusing to guess"
            )
        absent = [k for k in ("turn", "tree_hash", "gate") if k not in cp]
        if absent:
            raise GoalStateError(
                f"goal state at {p}: checkpoint {i} is missing "
                f"{', '.join(absent)} — refusing to guess"
            )

    budget = state["budget"]
    if budget.get("max_turns") is None and budget.get("max_minutes") is None:
        raise GoalStateError(
            f"goal state at {p} has no max_turns and no max_minutes, so "
            f"budget_exhausted() can never become True — the unbounded loop "
            f"create() refuses to build"
        )
    if "turns" not in state["used"] or "started" not in state["used"]:
        raise GoalStateError(
            f"goal state at {p} has an incomplete 'used' block: {state['used']!r}"
        )
    try:
        _validate_state_execution(state)
    except task_state.StateError as error:
        raise GoalStateError(f"goal state at {p} has invalid execution: {error}") from error
    return state


def checkpoint(slug, did, next_action, tree_hash_value,
               gate_result, gate_output="", parts=None) -> dict:
    """Append a checkpoint. Serialised: this is a read-modify-write, and two
    concurrent turns would otherwise each read turn N and both write N+1,
    losing one turn's record entirely. `parts` (`1-4,7`) marks parts of an
    open request check as run."""
    if gate_result not in ("pass", "fail", "skipped"):
        raise ValueError(f"gate_result must be pass|fail|skipped, got {gate_result!r}")
    numbers = _part_numbers(parts) if parts else set()
    with filelock.goal_locked(_path(slug)):
        return _checkpoint_locked(slug, did, next_action, tree_hash_value,
                                  gate_result, gate_output, numbers)


def _checkpoint_locked(slug, did, next_action, tree_hash_value,
                       gate_result, gate_output, parts=()) -> dict:
    state = load(slug)
    if execution_mode(state) == "state":
        raise AttemptOpen(
            "state-mode goals use step commands; legacy checkpoint is unavailable"
        )
    if state["status"] != "active":
        raise GoalClosed(f"cannot checkpoint: goal status is {state['status']!r}")
    max_turns = state["budget"].get("max_turns")
    if max_turns is not None and state["used"]["turns"] >= max_turns:
        raise BudgetExhausted(
            "Turn budget exhausted; stop work and request an explicit extension.")
    state["checkpoints"].append({
        "turn": state["used"]["turns"] + 1, "at": _now(),
        "did": did, "next": next_action, "head": _git_head(), "tree_hash": tree_hash_value,
        "gate": gate_result, "gate_output": _bounded(gate_output),
        "gate_spec": dict(state["gate"]), "source_hash": _work_digest(state),
    })
    asked = state.get("request_check")
    if parts and asked and "parts" in asked:
        known = set(range(1, len(asked["parts"]) + 1))
        done = set(asked.get("done") or ())
        new = sorted((set(parts) & known) - done)
        asked["done"] = sorted(done | set(new[:PARTS_PER_CHECK]))
        state["checkpoints"][-1]["parts_left_over"] = len(new[PARTS_PER_CHECK:])
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


def observe(slug: str, role: str, timeout: int = gate.DEFAULT_TIMEOUT, next_action: str | None = None, keep_timeout: bool = True) -> dict:
    """Run the command gate now and record what it found, outside any increment.

    Charges no turn and never skips: an unchanged tree is exactly when a
    resumed session needs a fresh reading. Goals record their test-file
    manifest at creation; a baseline observation of a goal that has none
    saves it then. Any other role leaves it absent, so an old goal stays
    fail-closed instead of adopting tests edited during the work.
    """
    if role not in OBSERVATION_ROLES:
        raise ValueError(f"role must be one of {', '.join(OBSERVATION_ROLES)}")
    before = load(slug)
    if before["status"] != "active":
        raise GoalClosed(f"cannot observe: goal status is {before['status']!r}")
    if before["gate"]["kind"] != "command":
        raise GateNotPassed("Coverage gates are judged by a reviewer; observe runs command gates only.")
    require_idle(before)
    manifest = None
    if role == "baseline" and "test_manifest" not in before:
        manifest = evidence.test_manifest(before["cwd"], before.get("watch_paths"))
    try:
        result, output, digest = gate.run_gate_at(dict(before, checkpoints=[]), timeout=timeout)
    except gate.GateUnrunnable as exc:
        if not keep_timeout and str(exc).startswith("gate timed out"):
            raise  # a run cut short by the caller's own time limit says nothing about the files
        result, output = "fail", str(exc)
        digest = getattr(exc, "tree_hash", gate.UNKNOWN_HASH)
    with filelock.goal_locked(_path(slug)):
        state = load(slug)
        if state["status"] != "active":
            raise GoalClosed(f"cannot observe: goal status is {state['status']!r}")
        require_idle(state)
        state.setdefault("observations", []).append({
            "kind": "observation", "role": role, "at": _now(), "tree_hash": digest,
            "gate": result, "gate_output": _bounded(output), "gate_spec": dict(state["gate"]),
            "source_hash": _work_digest(state),
            **({"next": next_action.strip(), "head": _git_head()} if next_action and next_action.strip() else {}),
        })
        if manifest is not None:
            state.setdefault("test_manifest", manifest)
        if role == "baseline":
            state.setdefault("request_inputs", sections.input_hashes(state.get("gate_path"), state["cwd"]))
            state.setdefault("predefined_sections",
                             sections.defined_sections(state.get("gate_path"), state["cwd"], _not_work(state)))
        return _persist_mutation_locked(state)

def _git_head() -> str | None:
    """The project's HEAD commit when it is a Git repository, so a later session can see what changed."""
    try:
        out = subprocess.run(["git", "-C", str(paths.project_root()), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    head = out.stdout.strip()
    return head if out.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", head) else None

def _not_work(state: dict) -> list[str]:
    """The folders that are not the work: the gate directory and Horizon's own state."""
    return [p for p in [state.get("gate_path"), str(paths.harness_root())] if p]

def _work_digest(state: dict) -> str | None:
    """The fingerprint of the work at a checkpoint: every project file, hidden files and tests included, outside
    the gate directory and Horizon's own state, so a FAIL is known to have been fixed in the work, not only in the
    agent's checks."""
    return tree_hash.source_digest(state["cwd"], hidden=True, tests=True, skip=_not_work(state))

def uncited_sections(state: dict) -> list[dict]:
    """Sections of a long request, or of a Markdown file it names, that no requirement line cites and whose code
    name the work does not define; a named file the work has changed since the baseline is an output, and code
    the project had at the baseline does not cover a section."""
    return sections.uncited_in_goal(state.get("gate_path"), state["cwd"], state.get("request_inputs"),
                                    skip=_not_work(state), predefined=state.get("predefined_sections", ()))

def differing_sections(state: dict) -> list[dict]:
    """The sections whose given code the work defines differently: the only part of the section list that stops
    `close`. The rest is advisory (heldout2)."""
    return [m for m in uncited_sections(state) if m.get("kind") == "differs"]


def unchecked_gate_lines(state: dict) -> list[str]:
    """`[gate]` requirement lines that no file the gate runs names by id (`R3`, `test_r3_...`, `# R3`).

    E2E-SWE paper run, pysmt: the gate named R1-R4, R13 and R14 of 19 lines, and `close` completed while the agent's
    own report said R5-R12 and R15-R19 were unbuilt and untested (0 of 27 hidden tests)."""
    if not state.get("gate_path") or state["gate"]["kind"] != "command":
        return []
    try:
        requirements = _requirements(state) or {}
    except EvidenceInsufficient:
        return []
    ids = [rid for rid, tags in requirements.items() if "gate" in tags]
    if not ids:
        return []
    try:
        text = "\n".join(evidence.gate_texts(state))
    except Exception:  # the check never stops `close` on a read error of its own
        return []
    return [rid for rid in ids if not _names_line(rid, text)]


def _names_line(rid: str, text: str) -> bool:
    """Whether text names requirement `rid`: as written (`R3`, `R03`, `R1.2`, in a comment or a string), or inside an
    identifier after an underscore (`test_r3_...`, `test_r1_2_...`). A bare `r3` (a variable) does not count, nor
    does R1 inside R10, R1a, R1.2 or `test_r1_2`."""
    match = re.fullmatch(r"R(\d+)(.*)", rid)
    digits, rest = (match.group(1).lstrip("0") or "0", match.group(2)) if match else ("", rid)
    written = (rf"(?<![A-Za-z0-9])R0*{digits}{re.escape(rest)}(?![A-Za-z0-9]|\.\d)" if match
               else rf"(?<![A-Za-z0-9]){re.escape(rid)}(?![A-Za-z0-9]|\.\d)")
    ident = re.escape(rest.replace(".", "_"))
    in_name = (rf"(?<=_)[rR]0*{digits}{ident}(?![A-Za-z0-9]|_\d)" if match
               else rf"(?<=_){re.escape(rid.replace('.', '_'))}(?![A-Za-z0-9]|_\d)")
    return bool(re.search(written, text) or re.search(in_name, text))


def unchecked_message(unchecked: list[str]) -> str:
    return ("Requirement lines tagged [gate] that no check of the gate names: " + ", ".join(unchecked)
            + ". Write a check for each, naming its line (as in `test_" + unchecked[0].lower().replace(".", "_")
            + "_...`), in the gate directory or a test file the gate command runs. A line no command can check "
            "is a [review] line: retag it and say why.")

def surface_check(state: dict, observed_hash: str | None) -> dict | None:
    """The goal's surface gate (every module and public name the request gives imports), run with the project's own
    interpreter whatever the gate command runs, or None without one. It never raises but for a hook, which runs none of
    the project's code: then a result not saved for these files stops completion (GateNotPassed). A check that cannot
    run reports why and does not block (E2E-SWE spectester: two builds scored 0 on a module their spec named)."""
    try:
        return surface.check(state, None if observed_hash in (None, gate.UNKNOWN_HASH) else observed_hash)
    except hookguard.Refused as exc:
        raise GateNotPassed(str(exc)) from exc
    except Exception:
        return None

def install_check(state: dict, observed_hash: str | None) -> dict | None:
    """The goal's install check (the package installs with pip, offline, from a clean copy and imports from another
    folder), when its request gives a Python package and says it gets installed; None otherwise. It never raises but
    for a hook, as surface_check (E2E-SWE paper run: builds that did not install scored 0, plain's h2, rpyc,
    transitions and mpmath among them)."""
    try:
        return install_gate.check(state, None if observed_hash in (None, gate.UNKNOWN_HASH) else observed_hash)
    except hookguard.Refused as exc:
        raise GateNotPassed(str(exc)) from exc
    except Exception:
        return None

SMALL_GATE_NAMES = 10
_TESTS_PASSED = re.compile(r"\b(\d+) passed\b")
_SURFACE_NAMES = re.compile(r"^NAMES = \[(.*?)^\]", re.M | re.S)


def small_gate(state: dict, gate_output: str) -> str | None:
    """What to say when a thin goal's gate ran fewer tests than its request names modules and public names (at most
    SMALL_GATE_NAMES are asked for), or None. Only a pytest count is read, and only a request that names a package's
    surface is held to it: "make this test pass" closes on one test.

    E2E-SWE thin run, pdfminer: the gate was one import test for ten sessions, text extraction never ran in it, and
    `close` completed the goal at 2 of 33 hidden tests (9a72fb4's own gate ran 29 to 31 tests and scored 97)."""
    if not state.get("thin") or not state.get("gate_path"):
        return None
    try:
        body = _SURFACE_NAMES.search((Path(state["gate_path"]) / surface.FILE).read_text(errors="replace"))
    except OSError:
        return None
    names = len(re.findall(r"^\s*['\"]", body.group(1), re.M)) if body else 0
    ran = _TESTS_PASSED.findall(gate_output or "")
    if not names or not ran:
        return None
    count, wanted = int(ran[-1]), min(names, SMALL_GATE_NAMES)
    if count >= wanted:
        return None
    return (f"The gate ran {count} test{'' if count == 1 else 's'}, and the request names {names} modules and "
            "public names: a gate that small cannot show the request is met. Add tests beside it that run the "
            "deliverable's main use end to end on a real input, with every expected value taken from the request, "
            f"until the gate runs at least {wanted}; fix what they find, then run close again.")


def _hold_small_gate(state: dict, observed_hash: str) -> None:
    """Stop completion on a small gate, once the gate passes and every name the request gives imports (a failing
    gate or a missing name is the more useful thing to hear first)."""
    if state["status"] != "active" or state["gate"]["kind"] != "command":
        return
    record = evidence.passing_record(state, observed_hash)
    message = small_gate(state, record.get("gate_output") or "") if record else None
    if not message:
        return
    found = surface_check(state, observed_hash)
    if found and found["blocking"] and found["failed"]:
        return
    raise GateNotPassed(message)

def placeholders(state: dict) -> str | None:
    """What to say when a thin goal's work still has placeholder functions, or None. Read from the source; nothing
    is run.

    E2E-SWE big run, authlib: every name the request gives imported, so the surface test passed 40 of 40, while a
    dozen of those modules were 10 to 25 lines of `raise NotImplementedError`; `close` completed the goal."""
    if not state.get("thin"):
        return None
    try:
        found = stubs.find(state["cwd"], [f.get("text", "") for f in state.get("facts") or []],
                           skip=state.get("placeholders_at_start") or ())
    except Exception:
        return None
    if not found:
        return None
    return ("These functions are still placeholders: " + stubs.listing(found) + ". A name that imports and does "
            "nothing fails the first caller that uses it. Build each one, then run close again. Only if the "
            "request itself says the caller supplies one (a hook the library's user fills in), record that "
            "function with `note --fact \"<file> <name> raises NotImplementedError by design\"`; a part you "
            "have not got to is not by design.")


def unbuilt_list(state: dict) -> list[str]:
    """The names a thin goal's request gives in code that the work defines nowhere (see `unbuilt`), less the ones a
    recorded fact says are not the work's own (`unbuilt.dismissed`). Read from the request and the source; nothing
    is run. Never raises.

    thin10 and thin11 runs: in the 8 builds where the session recorded such a fact, all 20 later session starts
    listed the same names again (wrapt: `Signature`, in sessions 4 to 8; protobuf: `IntEnum`, `Timestamp`)."""
    if not state.get("thin") or not state.get("gate_path"):
        return []
    try:
        inputs = sections._goal_inputs(state.get("gate_path"), state["cwd"], state.get("request_inputs"))
        text = "\n".join(body for _, body in inputs)
        found = surface.find_spec(state.get("gate_path"), state["cwd"])
        names = unbuilt.find(text, state["cwd"], found[1] if found else None)
        if not names:
            return []
        gone = unbuilt.dismissed(names, [f.get("text", "") for f in state.get("facts") or [] if isinstance(f, dict)])
        return [name for name in names if name not in gone]
    except Exception:
        return []


def unbuilt_names(state: dict) -> str | None:
    """What `check`, `close` and the session-start message say about them, or None. Advice: it never stops `close`."""
    names = unbuilt_list(state)
    if not names:
        return None
    return (f"The request gives these names in code and the work defines them nowhere ({len(names)}): "
            f"{unbuilt.listing(names)}. Find each in the request and build the ones it asks for. One that belongs "
            "to another library, or is only an example's own name, needs nothing: record those once, with "
            "`note --fact \"Not ours: NAME, NAME\"`, and they are not listed again.")

def _hold_placeholders(state: dict) -> None:
    """Stop completion while the work has placeholder functions."""
    if state["status"] != "active":
        return
    message = placeholders(state)
    if message:
        raise GateNotPassed(message)

class RequestCheckDue(GateNotPassed):
    """Every check holds, and the work has not yet been gone through against the request (see request_check)."""


REQUEST_CHECK_PARTS = 2  # a request with fewer headed parts is read whole and needs no walk


KEPT_BY_HORIZON = "(kept by Horizon)"  # how the heading over a carried-over request ends
# One `check` saves this many parts of the request check at most. A pass that is really run saves as it goes; before
# this, `check --parts 1-99` saved every part in one call, with nothing run (`close` then completed).
PARTS_PER_CHECK = 8

def _request_part_list(state: dict) -> list[dict]:
    """The request's headed parts in order, each `{"file", "line", "heading"}`; [] when it has too few to walk.
    The heading Horizon itself writes above a carried-over request (`cli.EARLIER_REQUEST`) is not a part of it."""
    try:
        found = sections.request_parts(state.get("gate_path"), state["cwd"], state.get("request_inputs"))
    except Exception:
        return []
    parts = [{"file": name, "line": number, "heading": heading} for name, listed in found for number, heading in listed
             if not heading.endswith(KEPT_BY_HORIZON)]
    return parts if len(parts) >= REQUEST_CHECK_PARTS else []


def _numbered(parts: list[dict], only=None) -> str:
    """`SPEC.md: 1 Parsing (line 40), 2 Errors (line 88); README.md: 3 Usage (line 5)`, the numbers being the ones
    `check --parts` takes; `only` keeps those numbers."""
    by_file = {}
    for number, part in enumerate(parts, 1):
        if only is None or number in only:
            by_file.setdefault(part["file"], []).append(
                f"{number} {_clip(part['heading'], 60)} (line {part['line']})")
    return "; ".join(f"{name}: " + ", ".join(listed) for name, listed in by_file.items())


def request_parts_left(state: dict) -> list[int]:
    """The numbers of the parts the request check still has to run."""
    asked = state.get("request_check") or {}
    done = set(asked.get("done") or ())
    return [n for n in range(1, len(asked.get("parts") or ()) + 1) if n not in done]


def _part_numbers(text: str) -> set[int]:
    """`1-4,7` as {1, 2, 3, 4, 7}."""
    numbers = set()
    for item in re.split(r"[,\s]+", text.strip()):
        found = re.fullmatch(r"(\d+)(?:-(\d+))?", item)
        if not found:
            raise ValueError(f"--parts takes the numbers of the request's parts, such as 1-4,7; got {text!r}")
        first, last = int(found.group(1)), int(found.group(2) or found.group(1))
        numbers.update(range(first, min(last, first + 1000) + 1))
    return numbers


def _request_parts_note(state: dict) -> str:
    """What is left of the request check, for `check`, `close` and the session start."""
    asked = state.get("request_check") or {}
    left = request_parts_left(state)
    return (f"{len(left)} of {len(asked.get('parts') or ())} parts of the request are still to run: "
            f"{_numbered(asked['parts'], set(left))}") if left else ""


def request_check_open(state: dict) -> bool:
    """True from the `close` that asked for the request check until `check --parts` has saved every part. (A goal
    asked before parts were kept is released by its next `check`, as it was then.)"""
    asked = state.get("request_check")
    if not asked:
        return False
    if "parts" not in asked:
        return state["used"]["turns"] <= asked.get("turn", 0)
    return bool(request_parts_left(state))


def request_check(state: dict, observed_hash: str | None = None) -> str | None:
    """What to say when a thin goal would complete and its work has not been gone through against the request since
    the gate first passed everything, or None. The first `close` that would complete asks for it and keeps the list
    of parts; `check --parts` saves the ones that were run, and `close` completes when none is left.

    E2E-SWE thin4 run: 14 of 31 builds closed the goal with hidden tests still failing and sessions left (plain: 8
    of 62). websockets closed after 4 of 10 sessions at 27 of 35, its own 25 tests passing; pgqueuer after 4 at 19
    of 21. The gate is the build's own tests, so it agrees with the build's own reading of the request. The big
    run's reading found what closed that gap for plain: short scripts written from the request late in the work.

    thin12 run: any `check` after the ask used to release it. protobuf-to-pydantic was asked late in session 5,
    went on with its own tests, saved an ordinary `check` in session 7 and ran `close` "to see which spec parts
    remain": it completed, at 22 of 42. Nothing on disk said which parts had been run, so each session was shown
    the whole list again.

    A goal that needed no change (its files are the ones its baseline run saw) completes without the pass, but not
    one that follows another goal of the project or was reopened: those files are the earlier work, and the pass
    is what a session asked to carry it on has to do (thin10 claripy: three sessions of start, close, complete)."""
    if not state.get("thin") or state["status"] != "active" or budget_exhausted(state):
        return None
    asked = state.get("request_check")
    if asked and not request_check_open(state):
        return None
    carried_on = state.get("follows_goal") or state.get("reopened_complete")
    if observed_hash is not None and not carried_on and _unchanged_since_baseline(state, observed_hash):
        return None
    if asked and "parts" in asked:
        return ("The request check is under way: " + _request_parts_note(state) + ". Run the work the way each "
                "describes, print what comes back and read it against the request's own words; where they differ, "
                "change the code, never a test's expectation. Save the ones you ran with `check --parts NUMBERS` "
                f"(such as `--parts 3-6,9`; one `check` saves at most {PARTS_PER_CHECK}); a part with nothing to "
                "run is done once read. `close` completes when none is left.")
    parts = _request_part_list(state)
    if not parts:
        return None
    names = unbuilt_names(state)
    return ("The gate passes, and the gate is your own tests: they agree with your reading of the request, not "
            "with the request. Before this goal completes, go through the request once more, part by part: "
            f"{_numbered(parts)}. This is one pass through the request, not a new set of tests. For each part, "
            "read it, then run a short script or command that uses the work the way that part describes and "
            "prints what comes back; write no expected values into it. Read what it printed against the "
            "request's own words: names, arguments and their order, defaults, returned values, error types, "
            "output format. Where they differ, the request is right: change the code, never a test's "
            "expectation, and add one test for each difference you fixed, its value copied from the request. "
            "Where the request does not settle a point, leave the code and record it with `note --finding`. "
            "Save the parts you ran with `check --parts NUMBERS` (such as `--parts 1-4,7`, with `--did` saying "
            f"what differed; one `check` saves at most {PARTS_PER_CHECK} parts, so save as you go); a part with "
            "nothing to run is done once read. The pass need not fit in this "
            "session: the next one is shown the parts still to run. When none is left, `close` completes."
            + (f" Start with these: {names}" if names else ""))


def _hold_request_check(state: dict, observed_hash: str) -> None:
    message = request_check(state, observed_hash)
    if message:
        raise RequestCheckDue(message)


def _ask_request_check(slug: str) -> None:
    """Record that `close` asked for the request check, with the parts it listed: `check --parts` marks them."""
    with filelock.goal_locked(_path(slug)):
        state = load(slug)
        if state["status"] != "active" or state.get("request_check"):
            return
        state["request_check"] = {"at": _now(), "turn": state["used"]["turns"],
                                  "parts": _request_part_list(state), "done": []}
        _persist_mutation_locked(state)


def advisory_sections(state: dict) -> str:
    """The `close` note listing unbuilt or unquoted sections, or "" when there are none or they cannot be read."""
    try:
        return sections.advisory([m for m in uncited_sections(state) if m.get("kind") != "differs"])
    except Exception:
        return ""


def _unfixed_fail_lines(state: dict) -> list[str]:
    """FAIL lines from this goal's review rounds whose checkpoint had the work as it is now, less the lines a
    `Disputed Rk:` finding answers.

    NL2Repo generic check, bleach: the one round failed R11 (an unquoted javascript: URL kept in CSS); the next
    session changed only the gate file, and `close` completed with R11 still broken."""
    current = _work_digest(state)
    if current is None:
        return []
    recorded = {}
    for record in (state.get("checkpoints") or []) + (state.get("observations") or []):
        if record.get("tree_hash") and record.get("source_hash"):
            recorded[str(record["tree_hash"])[:reviewing.HASH_ECHO]] = record["source_hash"]
    disputes = [f.get("text", "") for f in state.get("findings") or [] if isinstance(f, dict)]
    disputes = [d for d in disputes if d.lstrip().lower().startswith("disputed")]
    out = []
    for path in reviewing.goal_rounds(state):
        match = re.search(r"-([0-9a-f]{%d})-\d+\.md$" % reviewing.HASH_ECHO, path)
        if not match or recorded.get(match.group(1)) != current:
            continue
        for line in reviewing._fail_lines(reviewing._text(path)):
            decided = reviewing._DECIDED.match(line)
            rid = reviewing._rid(decided.group(1)) if decided else None
            answered = any(re.search(rf"\b{re.escape(rid)}\b", d, re.I) for d in disputes) if rid else bool(disputes)
            if not answered:
                out.append(line)
    return list(dict.fromkeys(out))


def changed_since_next(state: dict) -> list[str] | None:
    """Project files changed since the current next action was recorded, or None when that cannot be told.

    NL2Repo bench6: a session cut off by the call cap after writing a file but before `check` left the next
    session told to write the same file again (14 of 16 builds)."""
    if state.get("execution"):
        return None
    records = [state["checkpoints"][-1]] if state["checkpoints"] else []
    records += [r for r in (state.get("notes") or [])[-1:] + (state.get("observations") or []) if r.get("next")]
    latest = max(records, key=lambda r: r.get("at") or "", default=None)
    head = (latest or {}).get("head")
    if not head:
        return None
    root = str(paths.project_root())
    try:
        diff = subprocess.run(["git", "-C", root, "diff", "--name-only", head], capture_output=True, text=True,
                              timeout=10)
        new = subprocess.run(["git", "-C", root, "ls-files", "--others", "--exclude-standard"],
                             capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if diff.returncode:
        return None
    names = diff.stdout.splitlines() + (new.stdout.splitlines() if new.returncode == 0 else [])
    return sorted({n for n in names if n.strip() and not n.startswith((".claude/", ".agents/"))
                   and "__pycache__" not in n})

def note(slug: str, next_action: str | None = None, finding: str | None = None, fact: str | None = None,
         clear_findings: bool = False) -> dict:
    """Record the next action, an open finding or an environment fact now, with no turn and no gate run.

    `check --next` comes at the end of an increment; a session cut off before
    it (the NL2Repo benchmark's call cap) left the next session "Next: not set".
    A finding (a bug seen but not fixed yet) and a fact (an environment problem that is not a code bug) are
    shown at every session start: bench6 lost bugs found just before the cap and re-diagnosed the same
    broken scipy in six sessions.
    """
    text, finding, fact = ((v or "").strip() for v in (next_action, finding, fact))
    if not (text or finding or fact or clear_findings):
        raise ValueError("A note needs --next, --finding, --fact or --clear-findings")
    with filelock.goal_locked(_path(slug)):
        state = load(slug)
        if execution_mode(state) == "state":
            raise GoalStateError("state-mode goals carry their next action in the task packet; note is unavailable")
        if state["status"] != "active":
            raise GoalClosed(f"cannot note: goal status is {state['status']!r}")
        if text:
            state.setdefault("notes", []).append({"at": _now(), "next": _bounded(text), "head": _git_head()})
        if clear_findings:
            state["findings"] = []
        if finding:
            state.setdefault("findings", []).append({"at": _now(), "text": _clip(finding, 400)})
        if fact and fact not in [f["text"] for f in state.get("facts", [])]:
            state.setdefault("facts", []).append({"at": _now(), "text": _clip(fact, 400)})
        return _save(state)

def issue_review(slug: str, digest: str, review_file: str, via_check: bool = False) -> None:
    """Record that `close` (or, with `via_check`, `check`) handed out the review brief for these files, with the
    session it went to. Once a goal's rounds have all been handed out and the last can no longer come back
    (`reviewing.issue_expired`), a round that never came back (a reviewer cut off by a call cap, or a background one
    that died with its session) no longer holds the goal open: heldout2 lost about 70 of 193 sessions re-sending
    one review. The same file handed out again in a later session is another round. Since the E2E-SWE paper run such
    a round is handed out at most `reviewing.handouts_per_goal()` times and never lets the goal complete unreviewed;
    since the E2E-SWE fix test a brief no reviewer took up is not pending, and which hand-outs count is
    `reviewing._handouts_used` (a brief from `check` only once a reviewer took it up)."""
    with filelock.goal_locked(_path(slug)):
        state = load(slug)
        if execution_mode(state) == "state" or state["status"] != "active":
            return
        issued = state.setdefault("review_issued", [])
        live = [i for i in issued if i.get("file") == review_file and not reviewing.issue_expired(i)]
        if live:
            if not via_check and live[-1].get("check"):
                live[-1].pop("check")  # `close` handed it out too
                state.pop("review_deferred", None)
                _save(state)
            return
        entry = {"at": _now(), "digest": digest, "file": review_file, "session": paths.current_session()}
        if via_check:
            entry["check"] = True
        else:
            state.pop("review_deferred", None)
        issued.append(entry)
        _save(state)

def defer_review(slug: str) -> None:
    """Record that `close` or `check` held the review brief back because the session had too few calls left for a
    reviewer (`reviewing.calls_short`), so the next session starts with `close`. No hand-out is used."""
    with filelock.goal_locked(_path(slug)):
        state = load(slug)
        if execution_mode(state) == "state" or state["status"] != "active":
            return
        state["review_deferred"] = {"at": _now(), "session": paths.current_session()}
        _save(state)

def latest_observation(state: dict) -> dict | None:
    """The most recent recorded gate run, trimmed to what an agent needs to act on."""
    records = state.get("observations") or []
    if not records:
        return None
    last = records[-1]
    return {"role": last["role"], "at": last["at"], "gate": last["gate"],
            "gate_output": last["gate_output"], "tree_hash": last["tree_hash"]}

def fresh_observation(state: dict) -> dict | None:
    """The latest observation, unless a checkpoint is newer: then the checkpoint is the result to read.

    `check` and `close` used to print the latest observation whatever its age, so the baseline run of session 1 sat
    beside every later passing check. thin12 tremolo, session 10: the session read that old failure as a live one
    ("the baseline run also failed one SIGTERM test") and reported a bug it had fixed in session 7 as open."""
    observed = latest_observation(state)
    last = state["checkpoints"][-1] if state.get("checkpoints") else None
    if observed and last and (last.get("at") or "") > observed["at"]:
        return None
    return observed


def standing_gate(state: dict) -> dict | None:
    """The newest gate run that gave a result (pass or fail), checkpoint or observation: a `check` on unchanged
    files runs nothing (`skipped`), and the result before it still stands."""
    records = [r for r in (state.get("checkpoints") or []) + (state.get("observations") or [])
               if r.get("gate") in ("pass", "fail")]
    return max(records, key=lambda r: r.get("at") or "", default=None)


_TESTS_SKIPPED = re.compile(r"\b(\d+) skipped\b")


def skipped_tests(gate_output: str) -> str | None:
    """What to say when the gate's test run skipped tests (a pytest count), or None. Advice: it never stops `close`.

    thin11 pgqueuer closed on "25 passed, 10 skipped": the ten were every test of the PostgreSQL backend."""
    found = _TESTS_SKIPPED.findall(gate_output or "")
    if not found or int(found[-1]) == 0:
        return None
    count = int(found[-1])
    return (f"The gate skipped {count} test{'' if count == 1 else 's'}: a skipped test checks nothing, so the part "
            "of the work it covers is unchecked. Make it run here if it can; if the environment cannot run it, say "
            "in the final report which part of the request that leaves unchecked.")

def _unchanged_since_baseline(state: dict, observed: str) -> bool:
    """True when the files are exactly those a baseline observation recorded."""
    return (isinstance(observed, str) and observed != gate.UNKNOWN_HASH
            and any(o.get("role") == "baseline" and o.get("tree_hash") == observed
                    for o in state.get("observations") or []))


def close(slug: str, *, run_gate: bool = True, timeout: int = gate.DEFAULT_TIMEOUT) -> dict:
    """Finish in one call when the evidence allows; otherwise say exactly what is missing.

    With run_gate, the current files are observed first when no pass covers
    them. Without it (`close --if-ready`, the Stop hook) a goal never
    completes on files identical to its baseline observation: that pass
    predates any work. An explicit close may complete them, and says so with
    `unchanged_since_baseline`. When the budget is spent, the current tree is
    recorded as the deliverable (anytime semantics) instead of being left open.
    Sections of a long request the work has not built are listed in `sections_advisory` and never stop it.
    `observation` is the gate run this call made, or one newer than the last `check`; an older one is not printed
    (`fresh_observation`).
    """
    state = load(slug)
    if state["status"] != "active":
        return {"complete": state["status"] == "complete", "missing": [],
                "summary": summary(state), "finalized": state.get("finalized"),
                "observation": fresh_observation(state)}
    advisory = advisory_sections(state)
    try:
        observed = tree_hash.compute(state["cwd"], state.get("watch_paths"))
    except Exception:
        observed = gate.UNKNOWN_HASH
    unchanged = _unchanged_since_baseline(state, observed)
    observed_now = False
    try:
        if not run_gate and unchanged:
            raise GateNotPassed(
                "The files are unchanged since the baseline observation, so an automatic "
                "close does not complete the goal. Run close (without --if-ready) to "
                "complete a goal that needed no change.")
        if (run_gate and state["gate"]["kind"] == "command"
                and evidence.passing_record(state, observed) is None
                and not budget_exhausted(state)):
            observe(slug, "final", timeout=timeout)
            observed_now = True
        done = complete(slug, "", allow_unreviewed=run_gate)
        result = {"complete": True, "missing": [], "summary": summary(done), "finalized": None,
                  "observation": fresh_observation(done)}
        if done.get("unreviewed"):
            result["unreviewed"] = done["unreviewed"]
        if unchanged:
            result["unchanged_since_baseline"] = True
        if advisory:
            result["sections_advisory"] = advisory
        found = surface_check(done, observed)
        if found and found["failed"]:
            # only a check run with another Python than the project's reaches here: it could not block
            result["surface_advisory"] = (surface.listing(found["failed"]) + ". These did not import with "
                                          f"{found['python']}, not the project's own Python (the gate command "
                                          "names none): run the surface gate with it.")
        found = install_check(done, observed)
        if found and not found.get("ok") and (not found.get("blocking") or install_waived(done, found)):
            result["install_advisory"] = install_gate.problem(found) or found.get("error") or ""
        skipped = skipped_tests(done.get("gate_evidence") or "")
        if skipped:
            result["skipped_tests"] = skipped
        return result
    except (GateNotPassed, AttemptOpen, RevisionConflict) as exc:
        missing = [str(exc)]
        if isinstance(exc, RequestCheckDue):
            if run_gate:  # only a close the session ran itself: the Stop hook's is never read as the request
                try:
                    _ask_request_check(slug)
                except Exception:
                    pass
        elif observed_now and isinstance(exc, GateNotPassed):
            latest = latest_observation(load(slug))
            if latest and latest["gate"] != "pass":
                missing = ["The gate failed on the current files; see observation."]
    state = load(slug)
    if budget_exhausted(state) and not state.get("finalized"):
        with filelock.goal_locked(_path(slug)):
            state = load(slug)
            if not state.get("finalized"):
                try:
                    require_idle(state)  # an open attempt owns the state; leave it be
                except AttemptOpen:
                    pass
                else:
                    record = evidence.passing_record(state, observed)
                    state["finalized"] = {"reason": "budget_exhausted", "at": _now(),
                                          "tree_hash": observed,
                                          "gate": record["gate"] if record else "not passed"}
                    state = _persist_mutation_locked(state)
    result = {"complete": False, "missing": missing, "summary": summary(state),
              "finalized": state.get("finalized"), "observation": fresh_observation(state)}
    if advisory:
        result["sections_advisory"] = advisory
    unchecked = unchecked_gate_lines(state)
    if unchecked:
        result["unchecked_gate_lines"] = unchecked
    return result




def _persist_mutation_locked(state: dict) -> dict:
    if execution_mode(state) == "state":
        return commit_revision(
            state, expected_revision=state["execution"]["revision"]
        )
    return _save(state)


def _snapshot_transaction(
        slug: str, apply: Callable[[dict, str], None]) -> dict:
    before = load(slug)
    require_idle(before)
    try:
        observed = tree_hash.compute(before["cwd"], before.get("watch_paths"))
    except Exception as exc:
        raise GateNotPassed(f"Cannot verify the current snapshot: {exc}") from exc
    with filelock.goal_locked(_path(slug)):
        current = load(slug)
        if task_state.canonical_bytes(current) != task_state.canonical_bytes(before):
            raise RevisionConflict("Goal changed while its artifacts were checked")
        require_idle(current)
        apply(current, observed)
        return _persist_mutation_locked(current)


def _apply_review(state: dict, observed_hash: str, *, result: str,
                  evidence: str, judge: str, digest: str) -> None:
    if result not in ("pass", "fail"):
        raise ValueError("Review result must be pass or fail")
    if not isinstance(judge, str) or not judge.strip():
        raise JudgeNotRecorded("Supply the fresh reviewer's actual handle")
    if not isinstance(evidence, str) or len(evidence.strip()) < MIN_COVERAGE_EVIDENCE:
        raise EvidenceInsufficient("Supply the reviewer's substantive reasoning")
    if state["status"] != "active":
        raise GoalClosed(f"Goal is {state['status']}")
    criterion = (
        state["gate"]["value"] if state["gate"]["kind"] == "coverage"
        else state["gate"].get("review_criterion")
    )
    if not criterion:
        raise GateNotPassed("This goal has no configured review criterion")
    if (not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise GateNotPassed("No verified snapshot; run the gate again.")
    if observed_hash != digest:
        raise GateNotPassed("Artifacts changed after verification; run the gate again.")
    if state["gate"]["kind"] == "command":
        record = evidence_rules.passing_record(state, digest)
        if record is None:
            raise GateNotPassed("The command gate must pass on this snapshot before review")
        turn = record.get("turn", state["used"]["turns"])
        last = None
    else:
        last = state["checkpoints"][-1] if state["checkpoints"] else None
        if not last or last["tree_hash"] != digest or last.get("gate_spec") != state["gate"]:
            raise GateNotPassed("The review does not match the current checkpoint")
        turn = last["turn"]
    evidence = evidence.strip()
    # A fail is kept whatever its shape: an unrecorded FAIL is the pilot's
    # failure, and a fail on the wrong snapshot still only blocks completion.
    if result == "pass":
        if not reviewing.echoes(evidence, digest):
            raise EvidenceInsufficient(
                "The evidence must be the reviewer's reply naming the reviewed checkpoint "
                f"(the first {reviewing.HASH_ECHO} hash characters, "
                f"{digest[:reviewing.HASH_ECHO]}). Record --result fail, or review again."
            )
        refusals = reviewing.pass_refusals(evidence, _requirements(state))
        if refusals:
            raise EvidenceInsufficient(
                "This reply does not support a pass: " + "; ".join(refusals)
                + ". A pass needs the reply's VERDICTS block to give every requirement "
                "line PASS (NOT REVIEWED only on lines tagged [unverifiable]), and no "
                "`Rk: FAIL` line or FAIL headline elsewhere in the reply. Record "
                "--result fail, or fix and review again."
            )
    state.setdefault("reviews", []).append({
        "turn": turn, "tree_hash": digest, "at": _now(),
        "judge": judge.strip(), "criterion": criterion,
        "result": result, "evidence": _bounded_review(evidence),
    })
    if state["gate"]["kind"] == "coverage":
        last["gate"], last["gate_output"] = result, _bounded(evidence)
        state["subagents"].append({
            "role": "coverage-judge", "name": judge.strip(),
            "brief": criterion, "last_seen_turn": last["turn"] - 1,
        })


def _apply_coverage_completion(state: dict, observed_hash: str, gate_output: str) -> None:
    last = state["checkpoints"][-1] if state["checkpoints"] else None
    if last is None or last["gate"] != "pass":
        raise GateNotPassed(
            "The coverage review has not passed for the current files; "
            "run check for a review brief, then record the reviewer's verdict with review."
        )
    evidence = (last.get("gate_output", gate_output) or "").strip()
    if state["gate"]["kind"] == "coverage":
        floor = last["turn"] - 1
        if not any(
                JUDGE_ROLE_MARKER in (item.get("role") or "").lower()
                and (item.get("last_seen_turn") or 0) >= floor
                for item in state.get("subagents", [])):
            raise JudgeNotRecorded("No independent judge recorded for this turn")
        if len(evidence) < MIN_COVERAGE_EVIDENCE:
            raise EvidenceInsufficient(
                "The coverage verdict has no substantive reasoning"
            )
    criterion = state["gate"].get("review_criterion")
    if criterion:
        reviews = [
            review for review in state.get("reviews", [])
            if review["turn"] == last["turn"]
            and review["tree_hash"] == last["tree_hash"]
            and review["criterion"] == criterion
        ]
        if not reviews or reviews[-1]["result"] != "pass":
            raise GateNotPassed(
                "The required independent review has not passed for this snapshot."
            )
    if last.get("gate_spec") != state["gate"]:
        raise GateNotPassed(
            "The gate definition changed or predates verification; run it again."
        )
    digest = last["tree_hash"]
    if (not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise GateNotPassed("No verified snapshot; run the gate again.")
    if observed_hash != digest:
        raise GateNotPassed("Artifacts changed after verification; run the gate again.")
    if not evidence:
        evidence = f"{state['gate']['value']} -> exit 0 (gate produced no output)"
    state["status"] = "complete"
    state["gate_evidence"] = _bounded(evidence)
    state["completed_at"] = _now()

def _baseline_changed(state: dict, changed: list[str]) -> str:
    """What `close` says when baseline test files changed. The first sentence is the one earlier versions gave; the
    rest is what a session can do with no one there.

    The command names the commit the goal began on: `git checkout -- FILE` puts back the last commit's version,
    which is the changed one as soon as anything has been committed since `start`."""
    first = shlex.quote(changed[0])
    head = state.get("base_head")
    back = (f"`git checkout {head[:12]} -- {first}` restores a file that was committed when this goal began"
            if isinstance(head, str) and head else
            f"`git log --oneline -- {first}` lists the versions of a file that was committed")
    return ("Baseline test files changed: " + ", ".join(changed) + ". Completion needs a reviewer's or the user's "
            "approval: approve-test-change. These are tests the project had before this goal, so with no one to "
            f"approve, put them back as they were ({back}), fix the code they test, and put your own tests in a new "
            "test file.")


def install_waived(state: dict, found: dict | None) -> bool:
    """Whether a recorded fact answers for what the install check found: there is an `install check:` fact
    (`install_gate.waived`) and the problem is one such a fact can answer for (`install_gate.waivable`)."""
    return bool(found) and install_gate.waivable(found) and install_gate.waived(
        [f.get("text", "") for f in state.get("facts") or [] if isinstance(f, dict)])

def _apply_completion(state: dict, observed_hash: str, gate_output: str, allow_unreviewed: bool = False) -> None:
    _hold_small_gate(state, observed_hash)
    if state["status"] != "active":
        raise GoalClosed(f"cannot complete: goal status is {state['status']!r}")
    if state["gate"]["kind"] == "coverage":
        return _apply_coverage_completion(state, observed_hash, gate_output)
    record = evidence.passing_record(state, observed_hash)
    if record is None:
        raise GateNotPassed(
            "No passing gate result covers the current files; run close (or observe) first.")
    changed = evidence.changed_baseline_tests(state)
    if changed:
        raise GateNotPassed(_baseline_changed(state, changed))
    differing = differing_sections(state)
    if differing:
        raise GateNotPassed(sections.message(differing))
    unchecked = unchecked_gate_lines(state)
    if unchecked:
        raise GateNotPassed(unchecked_message(unchecked))
    found = surface_check(state, observed_hash)
    if found and found["blocking"] and found["failed"]:
        raise GateNotPassed(surface.message(found))
    found = install_check(state, observed_hash)
    if found and found.get("blocking") and not found.get("ok") and not install_waived(state, found):
        raise GateNotPassed(install_gate.message(found))
    _hold_placeholders(state)
    reasons = evidence.review_reasons(state, _requirements(state))
    if reasons:
        criterion = state["gate"]["review_criterion"]
        reviews = [r for r in state.get("reviews", [])
                   if r["tree_hash"] == observed_hash and r["criterion"] == criterion]
        if not reviews or reviews[-1]["result"] != "pass":
            # Only an explicit close may finish without a passing review: never the Stop hook's
            # `close --if-ready`, which could fire while a reviewer is still writing.
            if not (allow_unreviewed and reviewing.review_spent(state)):
                raise GateNotPassed(
                    "An independent review is required (" + "; ".join(reasons)
                    + ") and none has passed for this snapshot.")
            failed = reviewing.failed_on(state, observed_hash, _requirements(state))
            if failed:
                raise GateNotPassed(
                    "This goal's review failed these exact files: " + "; ".join(failed)
                    + ". Fix them; the review does not run again, so the changed files close without one.")
            unfixed = _unfixed_fail_lines(state)
            if unfixed:
                raise GateNotPassed(
                    "The review failed " + "; ".join(unfixed) + ", and no file of the work changed since that "
                    "round: only the gate's checks or Horizon's files did. Fix the work; if a FAIL "
                    "line is wrong, record why with `note --finding \"Disputed Rk: <reason>\"`.")
            earlier = reviewing.goal_fail_lines(state)
            unverified = reviewing.goal_unverified_lines(state)
            rounds = reviewing.rounds_per_goal()
            state["unreviewed"] = ("Closed after this goal's "
                                   + ("one review round" if rounds == 1 else f"{rounds} review rounds")
                                   + ", without a passing review: "
                                   + "; ".join(reasons) + "."
                                   + (" FAIL lines from that round, fixed but not re-checked by a reviewer: "
                                      + "; ".join(earlier) + "." if earlier else "")
                                   + (" Lines the reviewer could not check: " + "; ".join(unverified) + "."
                                      if unverified else ""))
    _hold_request_check(state, observed_hash)
    text = (record.get("gate_output") or "").strip() or (
        f"{state['gate']['value']} -> exit 0 (gate produced no output)")
    state["status"] = "complete"
    state["gate_evidence"] = _bounded(text)
    state["completed_at"] = _now()
    state["own_tests"] = _own_tests(state)

def record_review(slug: str, result: str, evidence: str, judge: str,
                  digest: str) -> dict:
    """Record the fresh reviewer's verdict for one exact checkpoint."""
    return _snapshot_transaction(
        slug,
        lambda state, observed: _apply_review(
            state, observed, result=result, evidence=evidence,
            judge=judge, digest=digest,
        ),
    )

def _approve_test_change_locked(slug: str, path: str, reason: str) -> dict:
    state = load(slug)
    if state["status"] != "active":
        raise GoalClosed(f"Goal is {state['status']}")
    require_idle(state)
    if not isinstance(reason, str) or len(reason.strip()) < 10:
        raise EvidenceInsufficient("Say who approved the test change and why")
    target = Path(state["cwd"]) / path
    digest = evidence._sha(target) if target.is_file() else "deleted"
    state.setdefault("approvals", []).append(
        {"path": path, "sha256": digest, "reason": reason.strip(), "at": _now()})
    return _persist_mutation_locked(state)



def _complete_locked(slug: str, gate_output: str, allow_unreviewed: bool = False) -> dict:
    return _snapshot_transaction(
        slug,
        lambda state, observed: _apply_completion(
            state, observed, gate_output, allow_unreviewed
        ),
    )


def _abandon_locked(slug: str, reason: str) -> dict:
    state = load(slug)
    if state["status"] != "active":
        raise GoalClosed(
            f"cannot abandon: goal status is {state['status']!r}, not active"
        )
    require_idle(state)
    state["status"] = "abandoned"
    state["abandoned_reason"] = reason
    state["abandoned_at"] = _now()
    state["own_tests"] = _own_tests(state)
    return _persist_mutation_locked(state)


REOPEN_TURNS = 10  # the turns a complete goal has, at the least, once it is reopened

def _reopen_locked(slug: str, reason: str,
                   extra_turns: int | None = None,
                   extra_minutes: int | None = None) -> dict:
    """Return an abandoned goal to active without implicitly refreshing its budget, or a complete one when its
    request is carried on.

    A complete goal that is reopened keeps its gate, facts, findings and checks. Its request check is asked again
    at the next `close` that would complete it, and it is left at least REOPEN_TURNS turns to work with.

    E2E-SWE thin10 run, claripy: the goal completed in session 7, the run went on, and sessions 8, 9 and 10 each
    started a new goal on the finished files, closed it at once and committed nothing."""
    for name_, value in (("extra_turns", extra_turns),
                         ("extra_minutes", extra_minutes)):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise BudgetInvalid(
                f"{name_} must be a positive integer or None, got {value!r}"
            )

    state = load(slug)
    # A complete goal reopens only when it is thin: its next `close` then asks for the request check again. Any
    # other complete goal stays closed, so its passing gate and review never stand for work they did not judge.
    if state["status"] != "abandoned" and not (state["status"] == "complete" and state.get("thin")):
        raise GoalClosed(
            f"cannot reopen: goal status is {state['status']!r}, not abandoned"
        )
    require_idle(state)
    was = state["status"]
    if was == "complete" and extra_minutes is None and state["budget"].get("max_minutes") is not None:
        started = datetime.fromisoformat(state["used"]["started"])
        if (datetime.now(timezone.utc) - started).total_seconds() / 60 >= state["budget"]["max_minutes"]:
            # With its time spent, the goal's next `close` would finish it at once, with no request check.
            raise GoalClosed("cannot reopen: this goal's time limit is spent, so it would close again at once; "
                             "pass --extra-minutes N to give it time")
    state["status"] = "active"
    now = _now()
    state.setdefault("reopen_history", []).append({
        "at": now,
        "reason": reason,
        "was": was,
        "was_abandoned_for": state.pop("abandoned_reason", ""),
        "extra_turns": extra_turns,
        "extra_minutes": extra_minutes,
    })
    state.pop("abandoned_at", None)
    if was == "complete":
        state.pop("completed_at", None)
        state.pop("request_check", None)
        state.pop("finalized", None)
        state["reopened_complete"] = True
        turns = state["budget"].get("max_turns")
        if extra_turns is None and turns is not None and turns - state["used"]["turns"] < REOPEN_TURNS:
            state["budget"]["max_turns"] = state["used"]["turns"] + REOPEN_TURNS
    if extra_turns is not None:
        if state["budget"].get("max_turns") is None:
            state["budget"]["max_turns"] = state["used"]["turns"] + extra_turns
        else:
            state["budget"]["max_turns"] += extra_turns
    if extra_minutes is not None:
        state["budget"]["max_minutes"] = extra_minutes
        state["used"]["started"] = now
    return _persist_mutation_locked(state)


def _add_subagent_locked(slug: str, role: str, name: str, brief: str,
                  last_seen_turn: int | None = None) -> dict:
    """Append one subagent entry through the guarded lifecycle writer."""
    state = load(slug)
    if state["status"] != "active":
        raise GoalClosed(
            f"cannot add subagent: goal status is {state['status']!r}, not active"
        )
    require_idle(state)
    if last_seen_turn is None:
        last_seen_turn = state["used"]["turns"]
    state["subagents"].append({
        "role": role,
        "name": name,
        "brief": _bounded(brief),
        "last_seen_turn": last_seen_turn,
    })
    return _persist_mutation_locked(state)


def _with_lock(fn):
    def wrapper(slug, *a, **kw):
        with filelock.goal_locked(_path(slug)):
            return fn(slug, *a, **kw)
    wrapper.__name__ = fn.__name__.strip("_").replace("_locked", "")
    wrapper.__doc__ = fn.__doc__
    return wrapper


# Every mutator is a read-modify-write and must be serialised for the same
# reason checkpoint() is.
complete = _with_lock(_complete_locked)
abandon = _with_lock(_abandon_locked)
reopen = _with_lock(_reopen_locked)
add_subagent = _with_lock(_add_subagent_locked)

approve_test_change = _with_lock(_approve_test_change_locked)


# Completion performs its fingerprint before acquiring the goal lock.
complete = _complete_locked

def execution_mode(state: dict) -> str:
    block = state.get("execution")
    return "conversation" if block is None else block["mode"]


def require_idle(state: dict) -> None:
    if execution_mode(state) == "state" and state["execution"]["active_attempt"] is not None:
        raise AttemptOpen("Close or recover the active attempt first")


def commit_revision(state: dict, *, expected_revision: int) -> dict:
    """Commit one state-mode mutation while the caller holds the strict goal lock."""
    current = load(state["slug"])
    if execution_mode(current) != "state":
        raise GoalStateError("revision commits require a state-mode goal")
    if current["execution"]["revision"] != expected_revision:
        raise RevisionConflict("Execution revision changed; inspect status")
    try:
        candidate = copy.deepcopy(state)
        candidate["execution"]["revision"] = expected_revision + 1
        _validate_state_execution(candidate)
    except (KeyError, TypeError, task_state.StateError) as error:
        raise GoalStateError(f"invalid execution candidate: {error}") from error
    return _save(candidate)


def _clip(text, limit: int):
    if text is None:
        return None
    text = str(text)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _next_action(state: dict) -> str:
    block = state.get("execution")
    if block:
        return block["task"]["next_action"]
    # The newest of the last checkpoint, note and observation that names one.
    records = [state["checkpoints"][-1]] if state["checkpoints"] else []
    records += [r for r in (state.get("notes") or [])[-1:] + (state.get("observations") or []) if r.get("next")]
    latest = max(records, key=lambda r: r.get("at") or "", default=None)
    return latest.get("next", "") if latest else ""


def row(state: dict) -> dict:
    """One goal in a listing: enough to decide whether to read it in full.

    A full summary carries up to 4,000 characters of gate output and 16,000
    of review evidence; listing every goal that way made `status` 2-5 KB per
    goal on the pilot ledgers.
    """
    last = state["checkpoints"][-1] if state["checkpoints"] else None
    reviews = state.get("reviews") or []
    observed = latest_observation(state)
    return {
        "slug": state["slug"], "status": state["status"],
        "goal": _clip(state["goal"], 200),
        "gate": {"kind": state["gate"]["kind"], "value": _clip(state["gate"]["value"], 200)},
        "review_criterion": _clip(state["gate"].get("review_criterion"), 300),
        "gate_path": state.get("gate_path"),
        "next_action": _clip(_next_action(state), 300),
        "abandoned_reason": _clip(state.get("abandoned_reason"), 300),
        "started": state["used"]["started"],
        "completed_at": state.get("completed_at"),
        "turns_used": state["used"]["turns"],
        "last_gate": last["gate"] if last else None,
        "last_observed": {k: observed[k] for k in ("gate", "role", "at")} if observed else None,
        "last_review": reviews[-1]["result"] if reviews else None,
    }


def deadline(state: dict) -> datetime | None:
    """When the wall-clock budget ends: `used.started` plus `max_minutes`.

    The agent otherwise computes this by hand, and in the pilot three Horizon
    trials ran past the 30-minute mark that EvoCode kills a round at.
    """
    max_minutes = state["budget"].get("max_minutes")
    if max_minutes is None:
        return None
    return datetime.fromisoformat(state["used"]["started"]) + timedelta(minutes=max_minutes)


def notes_path(slug: str):
    """The goal's notes file: what the agent learned from large inputs, part by part. Bookkeeping, so
    writing it never changes the checkpoint (tree_hash.BOOKKEEPING)."""
    return paths.harness_root() / "notes" / f"{slug}.md"


NOTES_HEADER = (
    "# Notes for {slug}\n\n"
    "What you learned from large inputs, part by part, so later sessions read this instead of the input.\n"
    "Give each part a level-2 heading: the input and its line range, what it covers, the file it maps to,\n"
    "and done or todo. Under it, keep the names, signatures, rules and examples you need.\n"
)


def ensure_notes(slug: str):
    path = notes_path(slug)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(NOTES_HEADER.format(slug=slug))
    return path


def notes_sections(slug: str, limit: int = 600) -> list[str]:
    """The notes file's level-2 headings, up to `limit` characters in all."""
    try:
        lines = notes_path(slug).read_text().splitlines()
    except (OSError, UnicodeError):
        return []
    out, used = [], 0
    for line in lines:
        if line.startswith("## "):
            heading = line[3:].strip()
            used += len(heading)
            if used > limit:
                out.append("…")
                break
            out.append(heading)
    return out

def _compact_review(review: dict) -> dict:
    """A review without its evidence, but with the FAIL and UNVERIFIED lines a resumed session acts on."""
    row = {k: v for k, v in review.items() if k not in ("evidence", "criterion")}
    if review.get("result") == "fail":
        row["fail_lines"] = [_clip(line, 300) for line in reviewing.previous_lines(review.get("evidence") or "")[:12]]
    return row

def summary(state: dict, compact: bool = False) -> dict:
    last = state["checkpoints"][-1] if state["checkpoints"] else None
    block = state.get("execution")
    active = None
    if block and block["active_attempt"] is not None:
        item = next(a for a in block["attempts"] if a["id"] == block["active_attempt"])
        active = {key: item[key] for key in ("id", "status", "worker", "packet")}
    end = deadline(state)
    observed = fresh_observation(state)
    return {
        "slug": state["slug"], "goal": state["goal"], "status": state["status"],
        "runtime": paths.runtime(), "gate": state["gate"], "gate_path": state.get("gate_path"),
        "budget": state["budget"],
        "used": state["used"], "budget_exhausted": budget_exhausted(state),
        "deadline": end.isoformat() if end else None,
        "minutes_left": (round(max(0.0, (end - datetime.now(timezone.utc)).total_seconds() / 60), 1)
                         if end else None),
        # Compact for `status SLUG`: a full status (16,000 characters of review evidence, 4,000 of gate
        # output) spilled to a file in the NL2Repo benchmark, and reading it back cost calls. `check` and
        # the other commands keep the full output the agent acts on.
        "last_checkpoint": (dict(last, gate_output=(last.get("gate_output") or "")[-600:])
                            if compact and last else last),
        "reviews": ([_compact_review(r) for r in state.get("reviews", [])[-1:]] if compact
                    else state.get("reviews", [])[-1:]),
        # The newest gate run when no checkpoint is newer: a session cut off before `check` leaves only this.
        "last_observation": (dict(observed, gate_output=observed["gate_output"][-600:])
                             if observed else None),
        "gate_evidence": ((state.get("gate_evidence") or "")[-600:] or None) if compact
                         else state.get("gate_evidence"),
        "notes_file": str(notes_path(state["slug"])),
        "notes_sections": notes_sections(state["slug"]),
        "execution_mode": execution_mode(state),
        "turn_unit": "worker_attempt" if block else "checkpoint",
        "execution_revision": block["revision"] if block else None,
        "active_attempt": active,
        "next_action": _next_action(state),
    }
