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
    """The goal's gate directory, refused unless the request and its lines exist.

    C1 asks for the request verbatim and a quoted requirement list before
    `start`; checking for them here is the one place prose can be enforced.
    """
    resolved = os.path.abspath(os.path.join(os.path.abspath(str(cwd)), str(gate_path)))
    directory = Path(resolved)
    if not directory.is_dir():
        raise GateRequired(
            f"--gate-path {resolved} is not a directory; write request.md and "
            f"requirements.md there before start")
    texts = {}
    for name in ("request.md", "requirements.md"):
        try:
            texts[name] = (directory / name).read_text()
        except (OSError, UnicodeError) as exc:
            raise GateRequired(f"{directory / name} is missing or unreadable: {exc}") from exc
    if not texts["request.md"].strip():
        raise GateRequired(f"{directory / 'request.md'} is blank; save the request verbatim")
    if not reviewing.requirement_lines(texts["requirements.md"]):
        raise GateRequired(
            f"{directory / 'requirements.md'} has no requirement lines (R1, R2, ...)")
    return resolved


def _requirements(state: dict) -> dict | None:
    """The goal's requirement lines, or None when it has no gate directory."""
    gate_path = state.get("gate_path")
    if not gate_path:
        return None
    target = Path(gate_path) / "requirements.md"
    try:
        return reviewing.requirement_lines(target.read_text())
    except (OSError, UnicodeError) as exc:
        raise EvidenceInsufficient(
            f"{target} is unreadable ({exc}); restore it, run check, and review again"
        ) from exc

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
        if not any(_covers(watch, gate_path) for watch in state["watch_paths"]):
            state["watch_paths"].append(gate_path)
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
               gate_result, gate_output="") -> dict:
    """Append a checkpoint. Serialised: this is a read-modify-write, and two
    concurrent turns would otherwise each read turn N and both write N+1,
    losing one turn's record entirely."""
    if gate_result not in ("pass", "fail", "skipped"):
        raise ValueError(f"gate_result must be pass|fail|skipped, got {gate_result!r}")
    with filelock.goal_locked(_path(slug)):
        return _checkpoint_locked(slug, did, next_action, tree_hash_value,
                                  gate_result, gate_output)


def _checkpoint_locked(slug, did, next_action, tree_hash_value,
                       gate_result, gate_output) -> dict:
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
        "did": did, "next": next_action, "tree_hash": tree_hash_value,
        "gate": gate_result, "gate_output": _bounded(gate_output),
        "gate_spec": dict(state["gate"]),
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
    last = state["checkpoints"][-1] if state["checkpoints"] else None
    if not last or last["tree_hash"] != digest or last.get("gate_spec") != state["gate"]:
        raise GateNotPassed("The review does not match the current checkpoint")
    if state["gate"]["kind"] == "command" and last["gate"] != "pass":
        raise GateNotPassed("The command gate must pass before review")
    if (not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise GateNotPassed("No verified snapshot; run the gate again.")
    if observed_hash != digest:
        raise GateNotPassed("Artifacts changed after verification; run the gate again.")
    evidence = evidence.strip()
    if not reviewing.echoes(evidence, digest):
        raise EvidenceInsufficient(
            "The evidence must be the reviewer's reply naming the reviewed checkpoint "
            f"(the first {reviewing.HASH_ECHO} hash characters, {digest[:reviewing.HASH_ECHO]})."
        )
    if result == "pass":
        refusals = reviewing.pass_refusals(evidence, _requirements(state))
        if refusals:
            raise EvidenceInsufficient(
                "This reply does not support a pass: " + "; ".join(refusals)
                + ". A pass needs every requirement line PASS (NOT REVIEWED only on lines "
                "tagged [unverifiable]) and no FAIL headline. Record --result fail, "
                "or fix and review again."
            )
    state.setdefault("reviews", []).append({
        "turn": last["turn"], "tree_hash": digest, "at": _now(),
        "judge": judge.strip(), "criterion": criterion,
        "result": result, "evidence": _bounded_review(evidence),
    })
    if state["gate"]["kind"] == "coverage":
        last["gate"], last["gate_output"] = result, _bounded(evidence)
        state["subagents"].append({
            "role": "coverage-judge", "name": judge.strip(),
            "brief": criterion, "last_seen_turn": last["turn"] - 1,
        })


def _apply_completion(state: dict, observed_hash: str, gate_output: str) -> None:
    if state["status"] != "active":
        raise GoalClosed(f"cannot complete: goal status is {state['status']!r}")
    last = state["checkpoints"][-1] if state["checkpoints"] else None
    if last is None or last["gate"] != "pass":
        raise GateNotPassed(
            "The last gate did not pass; budget exhaustion is not success."
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



def _complete_locked(slug: str, gate_output: str) -> dict:
    return _snapshot_transaction(
        slug,
        lambda state, observed: _apply_completion(
            state, observed, gate_output
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
    return _persist_mutation_locked(state)


def _reopen_locked(slug: str, reason: str,
                   extra_turns: int | None = None,
                   extra_minutes: int | None = None) -> dict:
    """Return an abandoned goal to active without implicitly refreshing its budget."""
    for name_, value in (("extra_turns", extra_turns),
                         ("extra_minutes", extra_minutes)):
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise BudgetInvalid(
                f"{name_} must be a positive integer or None, got {value!r}"
            )

    state = load(slug)
    if state["status"] != "abandoned":
        raise GoalClosed(
            f"cannot reopen: goal status is {state['status']!r}, not abandoned"
        )
    require_idle(state)
    state["status"] = "active"
    now = _now()
    state.setdefault("reopen_history", []).append({
        "at": now,
        "reason": reason,
        "was_abandoned_for": state.pop("abandoned_reason", ""),
        "extra_turns": extra_turns,
        "extra_minutes": extra_minutes,
    })
    state.pop("abandoned_at", None)
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
    last = state["checkpoints"][-1] if state["checkpoints"] else None
    return last.get("next", "") if last else ""


def row(state: dict) -> dict:
    """One goal in a listing: enough to decide whether to read it in full.

    A full summary carries up to 4,000 characters of gate output and 16,000
    of review evidence; listing every goal that way made `status` 2-5 KB per
    goal on the pilot ledgers.
    """
    last = state["checkpoints"][-1] if state["checkpoints"] else None
    reviews = state.get("reviews") or []
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

def summary(state: dict) -> dict:
    last = state["checkpoints"][-1] if state["checkpoints"] else None
    block = state.get("execution")
    active = None
    if block and block["active_attempt"] is not None:
        item = next(a for a in block["attempts"] if a["id"] == block["active_attempt"])
        active = {key: item[key] for key in ("id", "status", "worker", "packet")}
    end = deadline(state)
    return {
        "slug": state["slug"], "goal": state["goal"], "status": state["status"],
        "runtime": paths.runtime(), "gate": state["gate"], "gate_path": state.get("gate_path"),
        "budget": state["budget"],
        "used": state["used"], "budget_exhausted": budget_exhausted(state),
        "deadline": end.isoformat() if end else None,
        "minutes_left": (round(max(0.0, (end - datetime.now(timezone.utc)).total_seconds() / 60), 1)
                         if end else None),
        "last_checkpoint": last, "reviews": state.get("reviews", [])[-1:],
        "gate_evidence": state.get("gate_evidence"),
        "execution_mode": execution_mode(state),
        "turn_unit": "worker_attempt" if block else "checkpoint",
        "execution_revision": block["revision"] if block else None,
        "active_attempt": active,
        "next_action": _next_action(state),
    }
