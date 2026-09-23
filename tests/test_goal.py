import json
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / ".claude" / "harness" / "bin"
sys.path.insert(0, str(BIN))

import goal  # noqa: E402
import tree_hash
import os
import subprocess


@pytest.fixture(autouse=True)
def isolated_goals(tmp_path, monkeypatch):
    d = tmp_path / "goals"
    d.mkdir()
    (tmp_path / "project").mkdir()
    monkeypatch.setattr(goal.paths, "goals_dir", lambda: d)
    return d


def _make(**kw):
    args = dict(
        goal="Refactor the auth module",
        cwd=str(goal.paths.goals_dir().parent / "project"),
        gate_kind="command", gate_value="pytest -q", max_turns=3,
    )
    args.update(kw)
    return goal.create(**args)


def _digest(state):
    return tree_hash.compute(state["cwd"], state.get("watch_paths"))



def test_slugify_is_filesystem_safe():
    assert goal.slugify("Refactor the AUTH module!") == "refactor-the-auth-module"
    assert goal.slugify("a" * 100) == "a" * 60


def test_create_writes_state(isolated_goals):
    state = _make()
    assert (isolated_goals / f"{state['slug']}.json").exists()
    assert state["status"] == "active"
    assert state["used"]["turns"] == 0
    assert state["subagents"] == []


def test_create_refuses_without_gate():
    """Spec 6: a long-horizon loop with no termination criterion is an
    infinite loop."""
    with pytest.raises(goal.GateRequired):
        _make(gate_value="")


def test_checkpoint_increments_turns():
    state = _make()
    goal.checkpoint(state["slug"], "did a thing", "do next thing", "hash1", "fail", "boom")
    reloaded = goal.load(state["slug"])
    assert reloaded["used"]["turns"] == 1
    assert reloaded["checkpoints"][0]["gate"] == "fail"
    assert reloaded["checkpoints"][0]["tree_hash"] == "hash1"


def test_budget_exhausted_at_max_turns():
    state = _make(max_turns=2)
    assert goal.budget_exhausted(state) is False
    for i in range(2):
        goal.checkpoint(state["slug"], f"turn {i}", "next", f"h{i}", "fail")
    assert goal.budget_exhausted(goal.load(state["slug"])) is True


def test_complete_requires_passing_gate():
    """Spec 5.5: budget exhaustion is never reported as success."""
    state = _make()
    goal.checkpoint(state["slug"], "tried", "retry", _digest(state), "fail", "2 failed")
    with pytest.raises(goal.GateNotPassed):
        goal.complete(state["slug"], "2 failed")


def test_complete_succeeds_after_passing_gate():
    state = _make()
    goal.checkpoint(state["slug"], "fixed it", "none", _digest(state), "pass", "ok")
    done = goal.complete(state["slug"], "ok")
    assert done["status"] == "complete"
    assert done["gate_evidence"] == "ok"


def test_load_missing_raises():
    with pytest.raises(goal.GoalStateError):
        goal.load("no-such-goal")


def test_load_malformed_raises(isolated_goals):
    """Spec 6: never silently continue under a guessed goal."""
    (isolated_goals / "broken.json").write_text("{not json")
    with pytest.raises(goal.GoalStateError):
        goal.load("broken")


def test_gate_output_is_bounded():
    state = _make()
    goal.checkpoint(state["slug"], "x", "y", "h", "fail", "E" * 10000)
    stored = goal.load(state["slug"])["checkpoints"][0]["gate_output"]
    assert len(stored) <= goal.MAX_GATE_OUTPUT + 32
    assert "truncated" in stored


def test_budget_exhausted_at_max_minutes():
    """Spec 5.5: wall-clock budget must terminate the loop too, not just
    turn count. Backdate started rather than sleeping."""
    state = _make(max_turns=1000, max_minutes=1)
    state["used"]["started"] = "2000-01-01T00:00:00+00:00"
    goal._save(state)
    assert goal.budget_exhausted(goal.load(state["slug"])) is True


def test_load_non_dict_raises_goal_state_error(isolated_goals):
    """Valid JSON that isn't an object (e.g. a bare number) must still
    surface as GoalStateError, not a raw TypeError from 'in' on a non-dict."""
    (isolated_goals / "notadict.json").write_text("42")
    with pytest.raises(goal.GoalStateError):
        goal.load("notadict")


def test_load_missing_keys_raises(isolated_goals):
    """Spec 6: every field the module depends on must be validated at
    load time, not discovered later as a raw KeyError."""
    partial = {"slug": "partial", "gate": {"kind": "command", "value": "x"}}
    (isolated_goals / "partial.json").write_text(json.dumps(partial))
    with pytest.raises(goal.GoalStateError):
        goal.load("partial")


def test_checkpoint_rejects_invalid_gate_result():
    state = _make()
    with pytest.raises(ValueError):
        goal.checkpoint(state["slug"], "did", "next", "h", "maybe")


def test_complete_bounds_gate_output():
    state = _make()
    goal.checkpoint(state["slug"], "fixed it", "none", _digest(state), "pass", "E" * 20000)
    done = goal.complete(state["slug"], "caller text cannot replace recorded evidence")
    assert len(done["gate_evidence"]) <= goal.MAX_GATE_OUTPUT + 32
    assert "truncated" in done["gate_evidence"]


def test_complete_after_abandon_raises_goal_closed():
    state = _make()
    goal.checkpoint(state["slug"], "fixed it", "none", _digest(state), "pass", "ok")
    goal.abandon(state["slug"], "cancelled")
    with pytest.raises(goal.GoalClosed):
        goal.complete(state["slug"], "ok")


def test_checkpoint_after_complete_raises_goal_closed():
    state = _make()
    goal.checkpoint(state["slug"], "fixed it", "none", _digest(state), "pass", "ok")
    goal.complete(state["slug"], "ok")
    with pytest.raises(goal.GoalClosed):
        goal.checkpoint(state["slug"], "more", "next", _digest(state), "pass", "ok")


def test_abandon_after_complete_raises_goal_closed(isolated_goals):
    """Spec 6, symmetric case: a terminal status must not be silently
    overwritten by any mutator, not just checkpoint()/complete(). A
    rejected call must leave the on-disk file untouched."""
    state = _make()
    goal.checkpoint(state["slug"], "fixed it", "none", _digest(state), "pass", "ok")
    goal.complete(state["slug"], "ok")
    path = isolated_goals / f"{state['slug']}.json"
    before = path.read_text()
    with pytest.raises(goal.GoalClosed):
        goal.abandon(state["slug"], "cancelled")
    assert path.read_text() == before
    reloaded = goal.load(state["slug"])
    assert reloaded["status"] == "complete"
    assert reloaded["gate_evidence"] == "ok"


def test_abandon_after_abandon_raises_goal_closed():
    state = _make()
    goal.abandon(state["slug"], "first reason")
    with pytest.raises(goal.GoalClosed):
        goal.abandon(state["slug"], "second reason")
    reloaded = goal.load(state["slug"])
    assert reloaded["abandoned_reason"] == "first reason"

def test_add_subagent_after_complete_raises_goal_closed(isolated_goals):
    """add_subagent must be guarded the same as checkpoint/complete/abandon:
    bookkeeping for a subagent must not silently land on a closed goal."""
    state = _make()
    goal.checkpoint(state["slug"], "fixed it", "none", _digest(state), "pass", "ok")
    goal.complete(state["slug"], "ok")
    path = isolated_goals / f"{state['slug']}.json"
    before = path.read_text()
    with pytest.raises(goal.GoalClosed):
        goal.add_subagent(state["slug"], "auth-reviewer", "auth-reviewer-1", "brief")
    assert path.read_text() == before
    reloaded = goal.load(state["slug"])
    assert reloaded["subagents"] == []


def test_add_subagent_appends_and_persists():
    state = _make()
    goal.add_subagent(state["slug"], "auth-reviewer", "auth-reviewer-1",
                       "review the auth module", last_seen_turn=0)
    reloaded = goal.load(state["slug"])
    assert reloaded["subagents"] == [{
        "role": "auth-reviewer",
        "name": "auth-reviewer-1",
        "brief": "review the auth module",
        "last_seen_turn": 0,
    }]

    # default last_seen_turn falls back to the goal's current turn count
    goal.checkpoint(state["slug"], "did", "next", "h1", "fail")
    goal.add_subagent(state["slug"], "second-reviewer", "second-reviewer-1", "brief 2")
    reloaded = goal.load(state["slug"])
    assert reloaded["subagents"][1]["last_seen_turn"] == 1


def test_add_subagent_bounds_oversized_brief():
    state = _make()
    goal.add_subagent(state["slug"], "role", "name", "B" * 10000)
    stored = goal.load(state["slug"])["subagents"][0]["brief"]
    assert len(stored) <= goal.MAX_GATE_OUTPUT + 32
    assert "truncated" in stored


def test_slugify_never_produces_an_empty_slug():
    """An all-punctuation goal would otherwise name the file ".json" --
    hidden, and shared by every other such goal."""
    assert goal.slugify("???") == "goal"
    assert goal.slugify("!!! ???") == "goal"


def test_two_goals_with_the_same_title_do_not_collide(isolated_goals):
    """The second create() must not overwrite the first's checkpoints."""
    a = _make(goal="Fix the login bug")
    goal.checkpoint(a["slug"], "did work in A", "next", "h1", "fail", "")
    b = _make(goal="Fix the login bug")

    assert a["slug"] != b["slug"], "second goal reused the first's state file"
    assert goal.load(a["slug"])["checkpoints"][0]["did"] == "did work in A"
    assert goal.load(b["slug"])["checkpoints"] == []


def test_save_is_atomic(isolated_goals, monkeypatch):
    """A failure mid-write must leave the previous state intact, not a
    truncated file -- the checkpoints are the only thing that lets a future
    session resume."""
    st = _make()
    goal.checkpoint(st["slug"], "real work", "next", "h1", "fail", "")
    before = (isolated_goals / f"{st['slug']}.json").read_text()

    monkeypatch.setattr(goal.os, "replace",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    with pytest.raises(OSError):
        goal.checkpoint(st["slug"], "doomed", "next", "h2", "fail", "")

    assert (isolated_goals / f"{st['slug']}.json").read_text() == before
    assert not list(isolated_goals.glob(".*tmp*")), "temp file left behind"


def test_non_latin_titles_keep_their_own_slugs(isolated_goals):
    """Stripping every non-ASCII character collapsed each of these to the
    same slug, so a user could not name their own goal to resume it."""
    slugs = {goal.slugify(s) for s in
             ("重构认证模块", "Рефакторинг авторизации", "認証をなおす", "Refactor auth")}
    assert len(slugs) == 4, f"titles collapsed together: {slugs}"
    assert "goal" not in slugs


@pytest.mark.parametrize("bad", [0, -1, "20", 3.5, True])
def test_create_rejects_a_budget_that_cannot_bound(bad):
    with pytest.raises(goal.BudgetInvalid):
        _make(max_turns=bad)


def test_create_rejects_a_goal_with_no_budget_at_all():
    """max_turns=None with no max_minutes makes budget_exhausted() return
    False forever -- an unbounded loop, which is what the gate exists to
    prevent."""
    with pytest.raises(goal.BudgetInvalid):
        _make(max_turns=None)


def test_load_rejects_wrong_types_not_just_missing_keys(isolated_goals):
    """Key presence alone let 'checkpoints': 'x' load cleanly, then blow up
    later inside gate.last_hash -- defeating 'never guess state' exactly
    where it matters."""
    st = _make()
    p = isolated_goals / f"{st['slug']}.json"
    raw = json.loads(p.read_text())
    raw["checkpoints"] = "not a list"
    p.write_text(json.dumps(raw))
    with pytest.raises(goal.GoalStateError):
        goal.load(st["slug"])


def test_bounded_keeps_the_tail_like_gate_does(isolated_goals):
    """A coverage judge's verdict and a failing gate's diagnosis both come
    last. The two modules disagreeing on direction is worse than either."""
    st = _make()
    goal.checkpoint(st["slug"], "x", "y", "h", "fail", "H" * 9000 + "THE-VERDICT")
    stored = goal.load(st["slug"])["checkpoints"][0]["gate_output"]
    assert stored.endswith("THE-VERDICT")
    assert len(stored) <= goal.MAX_GATE_OUTPUT + 32


# ------------------------------------------------------------ reopen (I6/M4)

def test_reopen_returns_an_abandoned_goal_to_active():
    """Abandonment was terminal, so a goal abandoned by mistake could only
    be replaced by a new one that started with no checkpoints — the record
    of what had been done was still on disk and no longer reachable."""
    st = _make()
    goal.checkpoint(st["slug"], "did some work", "next", "h", "fail", "")
    goal.abandon(st["slug"], "blocked on a missing credential")

    goal.reopen(st["slug"], "the credential arrived")
    state = goal.load(st["slug"])
    assert state["status"] == "active"
    assert len(state["checkpoints"]) == 1, "reopening lost the work record"
    assert state["reopen_history"][0]["was_abandoned_for"] == "blocked on a missing credential"

    goal.checkpoint(st["slug"], "more work", "next", "h2", "pass", "ok")
    assert goal.load(st["slug"])["used"]["turns"] == 2


def test_reopen_refuses_a_completed_goal():
    """A completed goal has a passing gate on record; reopening would let
    that evidence stand for later work it never judged."""
    st = _make()
    goal.checkpoint(st["slug"], "did", "next", _digest(st), "pass", "ok")
    goal.complete(st["slug"], "ok")
    with pytest.raises(goal.GoalClosed):
        goal.reopen(st["slug"], "one more thing")


def test_reopen_does_not_silently_refresh_the_budget():
    st = _make(max_turns=1)
    goal.checkpoint(st["slug"], "did", "next", "h", "fail", "")
    goal.abandon(st["slug"], "out of turns")

    goal.reopen(st["slug"], "worth another look")
    assert goal.budget_exhausted(goal.load(st["slug"])) is True

    goal.abandon(st["slug"], "again")
    goal.reopen(st["slug"], "grant more", extra_turns=2)
    assert goal.budget_exhausted(goal.load(st["slug"])) is False
    assert goal.load(st["slug"])["budget"]["max_turns"] == 3


def test_reopen_rejects_a_budget_grant_that_cannot_bound():
    st = _make()
    goal.abandon(st["slug"], "r")
    with pytest.raises(goal.BudgetInvalid):
        goal.reopen(st["slug"], "r", extra_turns=0)
    with pytest.raises(goal.BudgetInvalid):
        goal.reopen(st["slug"], "r", extra_turns=True)


# ------------------------------------------------------- gate soundness (H1)

@pytest.mark.parametrize("unsound", [
    "./runtests.sh | tail -30",       # exit status of tail
    "pytest -q; echo done",           # exit status of echo
    "pytest -q || true",              # always 0
    "pytest -q &",                    # backgrounded: exits immediately
    "cd sub && pytest | head",        # a pipe anywhere in the chain
])
def test_a_gate_that_discards_its_exit_status_is_refused(unsound):
    """run_gate judges by exit status and /bin/sh has no pipefail, so these
    all report `pass` against a suite printing `3 failed` — and every
    safeguard downstream is satisfied, because the checkpoint honestly
    records what the gate honestly returned."""
    with pytest.raises(goal.GateUnsound):
        _make(gate_value=unsound)


@pytest.mark.parametrize("sound", [
    "pytest -q",
    "cd sub && pytest -q",                      # && propagates failure
    "sh -o pipefail -c 'pytest -q | tail -30'",  # operator inside one argument
    "pytest -q 2>&1",                            # a redirect, not an operator
    "make test",
])
def test_sound_gates_are_not_refused(sound):
    assert _make(gate_value=sound)["gate"]["value"] == sound


def test_a_coverage_gate_is_not_shell_checked():
    """A coverage criterion is prose for a judge, not a command; `;` and
    `|` are ordinary punctuation in it."""
    st = _make(gate_kind="coverage",
               gate_value="every public function has a test; edge cases covered")
    assert st["gate"]["kind"] == "coverage"


@pytest.mark.parametrize("broken,fragment", [
    ({"checkpoints": [{"turn": 1}]}, "tree_hash"),
    ({"checkpoints": ["not an object"]}, "expected object"),
    ({"budget": {}}, "unbounded loop"),
    ({"used": {}}, "incomplete 'used'"),
])
def test_load_validates_the_parts_callers_index(broken, fragment):
    """Validating only the eight top-level types let a malformed checkpoint
    load cleanly and raise a bare KeyError inside gate.last_hash and
    goal.complete — an exception neither skill's error table mentions."""
    st = _make()
    goal.checkpoint(st["slug"], "did", "next", "h", "fail", "")
    path = goal._path(st["slug"])
    state = json.loads(path.read_text())
    state.update(broken)
    path.write_text(json.dumps(state))

    with pytest.raises(goal.GoalStateError) as excinfo:
        goal.load(st["slug"])
    assert fragment in str(excinfo.value)


def test_extra_turns_is_honoured_on_a_minutes_only_goal():
    """It used to be silently dropped while reopen_history recorded the
    grant as honoured: the user granted five turns, the record agreed, and
    the goal stopped on the very next check."""
    st = _make(max_turns=None, max_minutes=1)
    goal.checkpoint(st["slug"], "did", "next", "h", "fail", "")
    goal.abandon(st["slug"], "blocked")

    goal.reopen(st["slug"], "unblocked", extra_turns=5)
    state = goal.load(st["slug"])
    assert state["budget"]["max_turns"] == 6      # 1 used + 5 granted
    assert goal.budget_exhausted(state) is False


def test_a_silent_passing_gate_still_records_evidence():
    """`test -f x`, `make -s check` and most linters print nothing on
    success, so gate_evidence — the only thing a completed goal offers as
    proof it was verified — was stored as the empty string. Found by
    running T10, whose own gate was silent."""
    st = _make(gate_value="test -f something")
    goal.checkpoint(st["slug"], "did", "next", _digest(st), "pass", "")
    done = goal.complete(st["slug"], "")

    assert done["gate_evidence"].strip(), "a completed goal recorded no evidence"
    assert "test -f something" in done["gate_evidence"]


def test_real_gate_output_is_preserved_verbatim():
    st = _make()
    goal.checkpoint(st["slug"], "did", "next", _digest(st), "pass", "42 passed in 3.1s")
    done = goal.complete(st["slug"], "different caller text")
    assert done["gate_evidence"] == "42 passed in 3.1s"


def test_a_coverage_goal_cannot_complete_on_a_contentless_verdict():
    """A command gate's silence is proof enough — the exit status carries it.
    A coverage gate has no exit status: the judge's written verdict IS the
    proof. In a nine-judge trial two returned the literal string "Test" as
    their reasoning, which would have been stored as evidence and read, in
    the record, exactly like a real verdict."""
    st = _make(gate_kind="coverage", gate_value="every claim carries a source")
    # A judge IS recorded here, so the refusal under test stays the verdict's
    # emptiness rather than the provenance check that now runs before it.
    goal.add_subagent(st["slug"], "coverage-judge", "judge-1", "criterion + sources")
    goal.checkpoint(st["slug"], "did", "next", _digest(st), "pass", "Test")
    with pytest.raises(goal.EvidenceInsufficient):
        goal.complete(st["slug"], "Test")
    assert goal.load(st["slug"])["status"] == "active", "closed on a bad verdict"


def test_a_coverage_goal_completes_on_a_real_verdict():
    st = _make(gate_kind="coverage", gate_value="every claim carries a source")
    verdict = ("PASS - all 12 claims carry a source; spot-checked 4 against the "
               "cited files and each quotation matches.")
    goal.add_subagent(st["slug"], "coverage-judge", "judge-1", "criterion + sources")
    goal.checkpoint(st["slug"], "did", "next", _digest(st), "pass", verdict)
    assert goal.complete(st["slug"], verdict)["status"] == "complete"


def test_a_command_goal_still_completes_on_silence():
    """The new guard must not leak into command gates, where an empty output
    is the normal, correct case."""
    st = _make(gate_value="test -f something")
    goal.checkpoint(st["slug"], "did", "next", _digest(st), "pass", "")
    assert goal.complete(st["slug"], "")["status"] == "complete"

def test_checkpoint_beyond_the_turn_budget_is_refused():
    """The exhausting turn (== max_turns) must still record — that is the
    checkpoint-FIRST invariant. But the turn after it used to record too:
    checkpoint() incremented past the cap forever and the goal stayed
    active, so a loop that ignored budget_exhausted() burned unbounded
    spend with the module never refusing. Beyond the cap it now refuses,
    loudly, with the goal and the on-disk record untouched."""
    state = _make(max_turns=1)
    goal.checkpoint(state["slug"], "d", "n", "h1", "fail")
    with pytest.raises(goal.BudgetExhausted):
        goal.checkpoint(state["slug"], "d2", "n2", "h2", "fail")
    s = goal.load(state["slug"])
    assert s["used"]["turns"] == 1
    assert len(s["checkpoints"]) == 1
    assert s["status"] == "active"


def test_reopen_with_extra_turns_lets_the_refused_checkpoint_land():
    """The recovery for a refused checkpoint is the one that already
    exists: abandon (budget spent, work remains), reopen with an explicit
    grant, then the record lands."""
    state = _make(max_turns=1)
    slug = state["slug"]
    goal.checkpoint(slug, "d", "n", "h1", "fail")
    with pytest.raises(goal.BudgetExhausted):
        goal.checkpoint(slug, "d2", "n2", "h2", "fail")
    goal.abandon(slug, "budget spent, work remains")
    goal.reopen(slug, "user granted more turns", extra_turns=1)
    goal.checkpoint(slug, "d2", "n2", "h2", "fail")
    assert goal.load(slug)["used"]["turns"] == 2


def test_wall_clock_exhaustion_does_not_refuse_the_in_flight_checkpoint():
    """The asymmetry is deliberate: a turn that started before the clock
    ran out has its work on disk by checkpoint time, and refusing would
    lose that increment — the exact failure the 'unknown' sentinel exists
    to prevent. Only the turn ceiling refuses; the wall clock stays a
    query the loop stops on."""
    state = _make(max_turns=1000, max_minutes=1)
    state["used"]["started"] = "2000-01-01T00:00:00+00:00"
    goal._save(state)
    assert goal.budget_exhausted(goal.load(state["slug"])) is True
    goal.checkpoint(state["slug"], "d", "n", "h", "fail")
    assert goal.load(state["slug"])["used"]["turns"] == 1


def test_budget_exhausted_exception_is_a_goal_state_error():
    """The skill documents that a bare `except goal.GoalStateError` also
    catches the protocol-refusal subclasses; the new one must not be the
    odd family member."""
    assert issubclass(goal.BudgetExhausted, goal.GoalStateError)


# --------------------------------------------------------------------------
# a coverage verdict must say where it came from
# --------------------------------------------------------------------------

_VERDICT = ("PASS — read src/api.py and docs/reference.md; all three endpoints "
            "carry their method, path and every status code named in the source.")


def _coverage(**kw):
    return _make(gate_kind="coverage",
                 gate_value="does the doc cover every endpoint?", **kw)


def test_a_coverage_goal_cannot_complete_with_no_judge_recorded():
    """MIN_COVERAGE_EVIDENCE checks the verdict has substance; nothing
    checked who wrote it. A worker that graded its own work and wrote a
    thoughtful paragraph left a record byte-indistinguishable from an
    independent judge's, so 'the worker must not grade its own work' was
    prose with nothing behind it."""
    state = _coverage()
    goal.checkpoint(state["slug"], "wrote the doc", "gate", _digest(state), "pass", _VERDICT)
    with pytest.raises(goal.JudgeNotRecorded):
        goal.complete(state["slug"], _VERDICT)
    assert goal.load(state["slug"])["status"] == "active"


@pytest.mark.parametrize("role", ["coverage-judge", "judge", "Coverage-Judge"])
def test_a_judge_recorded_for_the_turn_satisfies_it(role):
    """Substring and case-insensitive: the cost of the role string and the
    check disagreeing is a refusal on a judge that genuinely ran, which is
    the one direction that teaches an agent to route around it."""
    state = _coverage()
    goal.add_subagent(state["slug"], role, "judge-1", "criterion + raw artifacts")
    goal.checkpoint(state["slug"], "wrote the doc", "gate", _digest(state), "pass", _VERDICT)
    assert goal.complete(state["slug"], _VERDICT)["status"] == "complete"


def test_a_non_judge_subagent_does_not_satisfy_it():
    state = _coverage()
    goal.add_subagent(state["slug"], "researcher", "r", "find the endpoints")
    goal.checkpoint(state["slug"], "wrote the doc", "gate", _digest(state), "pass", _VERDICT)
    with pytest.raises(goal.JudgeNotRecorded):
        goal.complete(state["slug"], _VERDICT)


def test_a_judge_from_an_earlier_turn_does_not_carry_forward():
    """It graded an older tree. Accepting it would let one real judgement
    certify every later turn — the same staleness the tree hash exists to
    catch, one layer over."""
    state = _coverage()
    goal.add_subagent(state["slug"], "coverage-judge", "judge-1", "turn-1 judge")
    goal.checkpoint(state["slug"], "turn 1", "add DELETE", _digest(state), "fail", "DELETE missing")
    goal.checkpoint(state["slug"], "turn 2", "gate", _digest(state), "pass", _VERDICT)
    with pytest.raises(goal.JudgeNotRecorded):
        goal.complete(state["slug"], _VERDICT)


def test_a_command_goal_needs_no_judge():
    """The exit status is the proof; a command gate never had a judge."""
    state = _make()
    goal.checkpoint(state["slug"], "fixed it", "gate", _digest(state), "pass", "")
    assert goal.complete(state["slug"], "")["status"] == "complete"


def test_judge_not_recorded_is_a_gate_not_passed():
    """The skill's error table groups the completion refusals; a bare
    `except goal.GateNotPassed` must still catch this one."""
    assert issubclass(goal.JudgeNotRecorded, goal.GateNotPassed)


def _reserved_attempt():
    return {
        "id": "request-001", "turn": 1, "base_revision": 0,
        "status": "reserved", "packet": {"path": "packet.json", "sha256": "0" * 64},
        "procedure_sha256": "0" * 64, "worker": None,
        "gate_process": None, "finish_nonce": None, "update_sha256": None,
        "pending": None, "events": [], "result": None,
    }


def test_state_mode_is_explicit_and_does_not_rewrite_legacy(isolated_goals):
    legacy = _make()
    legacy_path = isolated_goals / f"{legacy['slug']}.json"
    before = legacy_path.read_bytes()
    assert "execution" not in legacy
    state = _make(execution="state")
    assert state["execution"]["mode"] == "state"
    assert state["execution"]["schema_version"] == 1
    assert state["execution"]["revision"] == 0
    assert state["used"]["turns"] == 0
    assert goal.load(legacy["slug"]) == legacy
    assert legacy_path.read_bytes() == before


def test_unknown_state_version_is_not_silently_loaded():
    state = _make(execution="state")
    state["execution"]["schema_version"] = 99
    goal._save(state)
    with pytest.raises(goal.GoalStateError, match="schema"):
        goal.load(state["slug"])


def test_state_create_rejects_unsupported_platform_before_mkdir(tmp_path, monkeypatch):
    destination = tmp_path / "new-goals"
    monkeypatch.setattr(goal.paths, "goals_dir", lambda: destination)
    monkeypatch.setattr(goal.filelock, "state_supported", lambda: False)
    with pytest.raises(goal.GoalStateError, match="platform"):
        goal.create("Objective", tmp_path, "command", "true", execution="state")
    assert not destination.exists()


@pytest.mark.parametrize("overrides", [
    {"goal": "O" * 30000},
    {"review_criterion": "R" * 30000},
])
def test_state_create_rejects_oversized_immutable_packet_without_goal_file(
        isolated_goals, overrides):
    with pytest.raises(goal.GoalStateError, match="packet"):
        _make(execution="state", **overrides)
    assert list(isolated_goals.glob("*.json")) == []


def test_state_create_saves_once_after_packet_preview(monkeypatch):
    calls = []
    original = goal._save

    def counted(state):
        calls.append(state["execution"]["revision"])
        return original(state)

    monkeypatch.setattr(goal, "_save", counted)
    state = _make(execution="state")
    assert calls == [0]
    assert goal.load(state["slug"]) == state


def test_state_load_rejects_malformed_execution_and_attempt():
    state = _make(execution="state")
    state["execution"]["task"]["entries"] = "broken"
    goal._save(state)
    with pytest.raises(goal.GoalStateError):
        goal.load(state["slug"])
    state["execution"]["task"]["entries"] = []
    state["execution"]["attempts"] = [{"id": "request-001"}]
    goal._save(state)
    with pytest.raises(goal.GoalStateError):
        goal.load(state["slug"])


def test_state_load_checks_attempt_turn_and_checkpoint_consistency():
    state = _make(execution="state")
    state["execution"]["attempts"] = [_reserved_attempt()]
    state["execution"]["active_attempt"] = "request-001"
    state["used"]["turns"] = 1
    goal._save(state)
    assert goal.load(state["slug"]) == state
    state["used"]["turns"] = 0
    goal._save(state)
    with pytest.raises(goal.GoalStateError, match="turn"):
        goal.load(state["slug"])
    state["used"]["turns"] = 1
    state["checkpoints"] = [{
        "turn": 1, "tree_hash": "0" * 64, "gate": "fail",
    }]
    goal._save(state)
    with pytest.raises(goal.GoalStateError, match="checkpoint"):
        goal.load(state["slug"])


def test_state_revision_commit_checks_disk_and_preserves_input():
    state = _make(execution="state")
    candidate = goal.load(state["slug"])
    candidate["execution"]["task"]["summary"] = "Made progress"
    with goal.filelock.goal_locked(goal._path(state["slug"])):
        committed = goal.commit_revision(candidate, expected_revision=0)
        assert committed["execution"]["revision"] == 1
        assert candidate["execution"]["revision"] == 0
        with pytest.raises(goal.RevisionConflict):
            goal.commit_revision(candidate, expected_revision=0)
    assert goal.load(state["slug"]) == committed


def test_state_revision_failed_atomic_replace_preserves_old_goal(
        isolated_goals, monkeypatch):
    state = _make(execution="state")
    target = isolated_goals / f"{state['slug']}.json"
    before = target.read_bytes()
    monkeypatch.setattr(goal.os, "replace",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")))
    with goal.filelock.goal_locked(goal._path(state["slug"])):
        with pytest.raises(OSError, match="boom"):
            goal.commit_revision(state, expected_revision=0)
    assert target.read_bytes() == before
    assert not list(isolated_goals.glob(".*tmp*"))


def test_state_summary_and_idle_guard():
    state = _make(execution="state")
    info = goal.summary(state)
    assert info["execution_mode"] == "state"
    assert info["turn_unit"] == "worker_attempt"
    assert info["execution_revision"] == 0
    assert info["next_action"] == state["execution"]["task"]["next_action"]
    goal.require_idle(state)
    state["execution"]["attempts"] = [_reserved_attempt()]
    state["execution"]["active_attempt"] = "request-001"
    assert goal.summary(state)["active_attempt"]["id"] == "request-001"
    with pytest.raises(goal.AttemptOpen):
        goal.require_idle(state)


def test_legacy_bin_import_can_create_and_load_state_goal(tmp_path):
    project = tmp_path / "unrelated-project"
    project.mkdir()
    script = (
        "import goal; "
        "s = goal.create('Objective', '.', 'command', 'true', execution='state'); "
        "assert goal.load(s['slug']) == s"
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(BIN)
    process = subprocess.run([sys.executable, "-c", script], cwd=project,
                             env=env, text=True, capture_output=True)
    assert process.returncode == 0, process.stderr


@pytest.mark.parametrize("execution", ["conversation", "state"])
def test_load_rejects_duplicate_keys_in_saved_goal(isolated_goals, execution):
    state = _make(execution=execution)
    path = isolated_goals / f"{state['slug']}.json"
    raw = path.read_text()
    path.write_text(raw.replace('"turns": 0', '"turns": 0, "turns": 0', 1))
    with pytest.raises(goal.GoalStateError, match="duplicate"):
        goal.load(state["slug"])


@pytest.mark.parametrize("number", ["NaN", "Infinity", "-Infinity", "1e999"])
def test_load_rejects_nonfinite_saved_numbers(isolated_goals, number):
    state = _make(execution="state")
    path = isolated_goals / f"{state['slug']}.json"
    raw = path.read_text()
    path.write_text(raw.replace('"turns": 0', f'"turns": {number}', 1))
    with pytest.raises(goal.GoalStateError, match="non-finite"):
        goal.load(state["slug"])

def _make_step_state(isolated_goals, monkeypatch, **overrides):
    project = isolated_goals.parent / "state-project"
    project.mkdir(exist_ok=True)
    directory = project / ".claude" / "harness" / "goals"
    directory.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(goal.paths, "project_root", lambda: project)
    monkeypatch.setattr(goal.paths, "goals_dir", lambda: directory)
    args = {
        "goal": "Refactor the auth module",
        "cwd": str(project),
        "gate_kind": "command",
        "gate_value": "true",
        "max_turns": 3,
        "execution": "state",
    }
    args.update(overrides)
    return goal.create(**args)

def test_state_public_writers_require_recovery_and_revisioned_persistence(
        isolated_goals, monkeypatch):
    import steps

    state = _make_step_state(isolated_goals, monkeypatch)
    begun = steps.begin(state["slug"], "r1")
    digest = _digest(state)
    evidence_text = (
        "Independent fixture review inspected every requested behavior and found it correct."
    )
    guarded_calls = [
        lambda: goal.checkpoint(state["slug"], "did", "next", digest, "pass", "ok"),
        lambda: goal.record_review(
            state["slug"], "pass", evidence_text, "judge-fixture", digest
        ),
        lambda: goal.complete(state["slug"], ""),
        lambda: goal.abandon(state["slug"], "Stopping."),
        lambda: goal.add_subagent(
            state["slug"], "judge", "judge-fixture", "Inspect the snapshot."
        ),
    ]
    for call in guarded_calls:
        with pytest.raises(goal.AttemptOpen):
            call()
    assert goal.load(state["slug"])["execution"]["revision"] == begun["execution_revision"]

    evidence_file = isolated_goals.parent / "dispatch-evidence.txt"
    evidence_file.write_text(
        "Dispatch records confirm no worker was started or bound, and no gate was launched."
    )
    recovered = steps.recover(
        state["slug"], "r1", expected_revision=begun["execution_revision"],
        reason="Dispatch did not create a worker.", worker_state="not-started",
        evidence_file=evidence_file,
    )
    abandoned = goal.abandon(state["slug"], "Stopping after recovery.")
    reopened = goal.reopen(state["slug"], "Continue later.")
    assert abandoned["execution"]["revision"] == recovered["execution_revision"] + 1
    assert reopened["execution"]["revision"] == abandoned["execution"]["revision"] + 1
    assert reopened["status"] == "active"
    subagented = goal.add_subagent(
        state["slug"], "worker", "worker-fixture", "Inspect the next increment."
    )
    assert subagented["execution"]["revision"] == reopened["execution"]["revision"] + 1


def test_state_checkpoint_and_cli_check_refuse_even_when_idle(
        isolated_goals, monkeypatch):
    import cli
    from types import SimpleNamespace

    state = _make_step_state(isolated_goals, monkeypatch)
    with pytest.raises(goal.GoalStateError, match="step"):
        goal.checkpoint(state["slug"], "did", "next", _digest(state), "pass", "ok")

    def must_not_run(*args, **kwargs):
        raise AssertionError("state-mode check must refuse before running a gate")

    monkeypatch.setattr(cli.gate, "run_gate_at", must_not_run)
    args = SimpleNamespace(
        slug=state["slug"], timeout=30, force=False,
        did="did", next_action="next",
    )
    with pytest.raises(goal.GoalStateError, match="step"):
        cli.check(args)


def test_completion_rejects_goal_mutated_while_snapshot_is_hashed(
        monkeypatch, tmp_path, isolated_goals):
    import steps

    state = _make_step_state(isolated_goals, monkeypatch, gate_value="true")
    begun = steps.begin(state["slug"], "r1")
    attached = steps.attach(
        state["slug"], "r1", "worker-fixture",
        expected_revision=begun["execution_revision"],
    )
    update = tmp_path / "update.json"
    update.write_text(json.dumps({
        "schema_version": 1,
        "attempt_id": "r1",
        "base_revision": begun["packet"]["base_revision"],
        "did": "Prepared completion.",
        "ops": [],
    }))
    evidence = tmp_path / "terminal.txt"
    evidence.write_text("The fixture host confirms worker-fixture exited normally.")
    steps.finish(
        state["slug"], "r1",
        expected_revision=attached["execution_revision"],
        update_file=update, worker_evidence_file=evidence,
    )

    original_compute = goal.tree_hash.compute
    mutated = False

    def mutate_during_hash(cwd, watch_paths):
        nonlocal mutated
        digest = original_compute(cwd, watch_paths)
        if not mutated:
            mutated = True
            with goal.filelock.goal_locked(goal._path(state["slug"])):
                current = goal.load(state["slug"])
                current["execution"]["task"]["summary"] = "Concurrent mutation"
                goal.commit_revision(
                    current,
                    expected_revision=current["execution"]["revision"],
                )
        return digest

    monkeypatch.setattr(goal.tree_hash, "compute", mutate_during_hash)
    with pytest.raises(goal.RevisionConflict, match="changed"):
        goal.complete(state["slug"], "")
    assert goal.load(state["slug"])["status"] == "active"


def test_state_review_and_completion_use_revisioned_transactions(
        tmp_path, isolated_goals, monkeypatch):
    import steps

    state = _make_step_state(
        isolated_goals, monkeypatch, gate_value="true",
        review_criterion="Inspect the exact accepted snapshot.",
    )
    begun = steps.begin(state["slug"], "r1")
    attached = steps.attach(
        state["slug"], "r1", "worker-fixture",
        expected_revision=begun["execution_revision"],
    )
    update = tmp_path / "update.json"
    update.write_text(json.dumps({
        "schema_version": 1,
        "attempt_id": "r1",
        "base_revision": begun["packet"]["base_revision"],
        "did": "Prepared reviewed completion.",
        "ops": [],
    }))
    terminal = tmp_path / "terminal.txt"
    terminal.write_text("The fixture host confirms worker-fixture exited normally.")
    finished = steps.finish(
        state["slug"], "r1",
        expected_revision=attached["execution_revision"],
        update_file=update, worker_evidence_file=terminal,
    )
    checkpoint = finished["checkpoint"]
    failed = goal.record_review(
        state["slug"], "fail",
        "The independent fixture found one unresolved issue in the accepted snapshot.",
        "judge-fixture", checkpoint["tree_hash"],
    )
    with pytest.raises(goal.GateNotPassed):
        goal.complete(state["slug"], "")
    passed = goal.record_review(
        state["slug"], "pass",
        "The independent fixture rechecked the accepted snapshot and found the issue resolved.",
        "judge-fixture", checkpoint["tree_hash"],
    )
    completed = goal.complete(state["slug"], "")
    assert failed["execution"]["revision"] == finished["execution_revision"] + 1
    assert passed["execution"]["revision"] == failed["execution"]["revision"] + 1
    assert completed["execution"]["revision"] == passed["execution"]["revision"] + 1
    assert completed["status"] == "complete"


def test_commit_revision_wraps_malformed_candidate_execution():
    state = _make(execution="state")
    candidate = goal.load(state["slug"])
    candidate["execution"] = None
    with goal.filelock.goal_locked(goal._path(state["slug"])):
        with pytest.raises(goal.GoalStateError, match="invalid execution candidate"):
            goal.commit_revision(candidate, expected_revision=0)
    assert goal.load(state["slug"]) == state
