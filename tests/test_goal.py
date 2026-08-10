import json
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / ".claude" / "harness" / "bin"
sys.path.insert(0, str(BIN))

import goal  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_goals(tmp_path, monkeypatch):
    d = tmp_path / "goals"
    d.mkdir()
    monkeypatch.setattr(goal.paths, "goals_dir", lambda: d)
    return d


def _make(**kw):
    args = dict(
        goal="Refactor the auth module",
        cwd="/tmp/proj",
        gate_kind="command",
        gate_value="pytest -q",
        max_turns=3,
    )
    args.update(kw)
    return goal.create(**args)


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
    goal.checkpoint(state["slug"], "tried", "retry", "h1", "fail", "2 failed")
    with pytest.raises(goal.GateNotPassed):
        goal.complete(state["slug"], "2 failed")


def test_complete_succeeds_after_passing_gate():
    state = _make()
    goal.checkpoint(state["slug"], "fixed it", "none", "h1", "pass", "ok")
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
    """Spec 6: a runaway gate must not bloat the state file, even on the
    passing/completing call."""
    state = _make()
    goal.checkpoint(state["slug"], "fixed it", "none", "h1", "pass", "ok")
    done = goal.complete(state["slug"], "E" * 20000)
    assert len(done["gate_evidence"]) <= goal.MAX_GATE_OUTPUT + 32
    assert "truncated" in done["gate_evidence"]


def test_complete_after_abandon_raises_goal_closed():
    state = _make()
    goal.checkpoint(state["slug"], "fixed it", "none", "h1", "pass", "ok")
    goal.abandon(state["slug"], "cancelled")
    with pytest.raises(goal.GoalClosed):
        goal.complete(state["slug"], "ok")


def test_checkpoint_after_complete_raises_goal_closed():
    state = _make()
    goal.checkpoint(state["slug"], "fixed it", "none", "h1", "pass", "ok")
    goal.complete(state["slug"], "ok")
    with pytest.raises(goal.GoalClosed):
        goal.checkpoint(state["slug"], "more", "next", "h2", "pass", "ok")


def test_abandon_after_complete_raises_goal_closed(isolated_goals):
    """Spec 6, symmetric case: a terminal status must not be silently
    overwritten by any mutator, not just checkpoint()/complete(). A
    rejected call must leave the on-disk file untouched."""
    state = _make()
    goal.checkpoint(state["slug"], "fixed it", "none", "h1", "pass", "ok")
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
    goal.checkpoint(state["slug"], "fixed it", "none", "h1", "pass", "ok")
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
