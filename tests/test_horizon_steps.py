import hashlib
from pathlib import Path

import pytest

import goal
import paths
import steps
import task_state
import tree_hash


def test_begin_and_attach_replay_without_spending_another_turn(make_state_goal):
    state = make_state_goal(max_turns=1)
    begun = steps.begin(state["slug"], "r1")
    again = steps.begin(state["slug"], "r1")
    assert again["replayed"] is True
    assert again["packet"] == begun["packet"]
    attached = steps.attach(state["slug"], "r1", "worker-fixture",
                            expected_revision=begun["execution_revision"])
    replay = steps.attach(state["slug"], "r1", "worker-fixture",
                          expected_revision=begun["execution_revision"])
    assert replay["execution_revision"] == attached["execution_revision"]
    assert replay["replayed"] is True
    assert goal.load(state["slug"])["used"]["turns"] == 1
    assert begun["packet"]["base_revision"] < attached["execution_revision"]


def test_begin_reserves_one_turn_and_content_addressed_packet(make_state_goal):
    state = make_state_goal()
    before = tree_hash.compute(state["cwd"], state["watch_paths"])
    begun = steps.begin(state["slug"], "r1")
    stored = goal.load(state["slug"])
    attempt = stored["execution"]["attempts"][0]
    assert stored["used"]["turns"] == 1
    assert stored["execution"]["active_attempt"] == "r1"
    assert attempt["status"] == "reserved"
    assert attempt["worker"] is None
    assert attempt["base_revision"] == 1
    assert begun["packet"]["base_revision"] == 1
    raw = steps.read_blob(attempt["packet"], Path(state["cwd"]))
    assert raw == task_state.canonical_bytes(begun["packet"])
    assert hashlib.sha256(raw).hexdigest() == attempt["packet"]["sha256"]
    assert attempt["packet"]["path"].startswith(
        f"{paths.harness_root().relative_to(Path(state['cwd']))}/goals/{state['slug']}.d/objects/"
    )
    assert tree_hash.compute(state["cwd"], state["watch_paths"]) == before
    assert begun["packet"]["audit_path"] == str(goal._path(state["slug"]).relative_to(Path(state["cwd"])))


def test_other_request_cannot_begin_while_attempt_open(make_state_goal):
    state = make_state_goal()
    steps.begin(state["slug"], "r1")
    with pytest.raises(steps.StepBusy):
        steps.begin(state["slug"], "r2")
    assert goal.load(state["slug"])["used"]["turns"] == 1


def test_begin_rejects_spent_and_expired_budget_without_charge(make_state_goal):
    state = make_state_goal(max_turns=1)
    first = steps.begin(state["slug"], "r1")
    assert first["execution_revision"] == 1
    with pytest.raises(steps.StepBusy):
        steps.begin(state["slug"], "r2")

    state2 = make_state_goal(max_minutes=1)
    import filelock
    from datetime import datetime, timedelta, timezone
    with filelock.goal_locked(goal._path(state2["slug"])):
        stale = goal.load(state2["slug"])
        stale["used"]["started"] = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        goal.commit_revision(stale, expected_revision=0)
    with pytest.raises(goal.BudgetExhausted):
        steps.begin(state2["slug"], "r1")
    assert goal.load(state2["slug"])["used"]["turns"] == 0


def test_attach_rejects_conflicting_handle_and_revision(make_state_goal):
    state = make_state_goal()
    begun = steps.begin(state["slug"], "r1")
    with pytest.raises(goal.RevisionConflict):
        steps.attach(state["slug"], "r1", "worker-1", expected_revision=0)
    attached = steps.attach(state["slug"], "r1", "worker-1",
                            expected_revision=begun["execution_revision"])
    assert goal.load(state["slug"])["execution"]["attempts"][0]["worker"] == {
        "runtime": paths.runtime(), "handle": "worker-1"}
    with pytest.raises(steps.StepConflict):
        steps.attach(state["slug"], "r1", "worker-2",
                     expected_revision=attached["execution_revision"])
    assert goal.load(state["slug"])["execution"]["revision"] == 2


def test_attach_rejects_changed_procedure_without_binding(make_state_goal, monkeypatch):
    state = make_state_goal()
    begun = steps.begin(state["slug"], "r1")
    original = task_state.procedure
    monkeypatch.setattr(task_state, "procedure",
                        lambda runtime: {**original(runtime), "sha256": "0" * 64})
    with pytest.raises(steps.StepConflict):
        steps.attach(state["slug"], "r1", "worker-1",
                     expected_revision=begun["execution_revision"])
    saved = goal.load(state["slug"])
    assert saved["execution"]["attempts"][0]["status"] == "reserved"
    assert saved["execution"]["revision"] == 1


@pytest.mark.parametrize("damage", ["missing", "corrupt"])
def test_replay_refuses_missing_or_corrupt_saved_packet(make_state_goal, damage):
    state = make_state_goal()
    begun = steps.begin(state["slug"], "r1")
    path = Path(state["cwd"]) / begun["packet_ref"]["path"]
    if damage == "missing":
        path.unlink()
    else:
        path.write_bytes(b"wrong bytes")
    with pytest.raises(steps.StepConflict):
        steps.begin(state["slug"], "r1")
    with pytest.raises(steps.StepConflict):
        steps.attach(state["slug"], "r1", "worker-1",
                     expected_revision=begun["execution_revision"])
    assert goal.load(state["slug"])["execution"]["revision"] == 1


def test_begin_refuses_stale_snapshot_without_reservation(make_state_goal, monkeypatch):
    state = make_state_goal()
    original = steps.store_blob

    def change_after_preflight(slug, kind, payload):
        ref = original(slug, kind, payload)
        (Path(state["cwd"]) / "changed.txt").write_text("source changed")
        return ref

    monkeypatch.setattr(steps, "store_blob", change_after_preflight)
    with pytest.raises(goal.RevisionConflict):
        steps.begin(state["slug"], "r1")
    saved = goal.load(state["slug"])
    assert saved["used"]["turns"] == 0
    assert saved["execution"]["revision"] == 0


def test_begin_refuses_stale_revision_without_reservation(make_state_goal, monkeypatch):
    import filelock

    state = make_state_goal()
    original = steps.store_blob

    def revise_after_preflight(slug, kind, payload):
        ref = original(slug, kind, payload)
        with filelock.goal_locked(goal._path(slug)):
            current = goal.load(slug)
            current["execution"]["task"]["next_action"] = "Changed while preparing."
            goal.commit_revision(current, expected_revision=0)
        return ref

    monkeypatch.setattr(steps, "store_blob", revise_after_preflight)
    with pytest.raises(goal.RevisionConflict):
        steps.begin(state["slug"], "r1")
    saved = goal.load(state["slug"])
    assert saved["used"]["turns"] == 0
    assert saved["execution"]["revision"] == 1


def test_replay_keeps_original_attempt_result(make_state_goal):
    # A completed goal must still serve the original attempt's saved result.
    state = make_state_goal()
    steps.begin(state["slug"], "r1")
    import filelock
    with filelock.goal_locked(goal._path(state["slug"])):
        current = goal.load(state["slug"])
        attempt = current["execution"]["attempts"][0]
        attempt["status"] = "recovered"
        attempt["result"] = {"update_accepted": False, "exit_code": 1,
                             "attempt_closed": True, "last_checkpoint": {"turn": 1}}
        current["execution"]["active_attempt"] = None
        current["status"] = "complete"
        current["checkpoints"].append({"turn": 1, "tree_hash": "0" * 64,
                                       "gate": {"result": "fail"}})
        goal.commit_revision(current, expected_revision=1)
    replay = steps.begin(state["slug"], "r1")
    assert replay["replayed"] is True
    assert replay["last_checkpoint"] == {"turn": 1}
    assert replay["exit_code"] == 1

def test_attach_accepts_worker_edits_after_reservation(make_state_goal):
    state = make_state_goal()
    begun = steps.begin(state["slug"], "r1")
    (Path(state["cwd"]) / "worker-edit.txt").write_text("work started")
    attached = steps.attach(state["slug"], "r1", "worker-1",
                            expected_revision=begun["execution_revision"])
    assert attached["attempt_status"] == "attached"
    assert goal.load(state["slug"])["execution"]["attempts"][0]["worker"]["handle"] == "worker-1"

def test_closed_attempt_still_spends_its_turn(make_state_goal):
    import filelock

    state = make_state_goal(max_turns=1)
    steps.begin(state["slug"], "r1")
    with filelock.goal_locked(goal._path(state["slug"])):
        current = goal.load(state["slug"])
        current["execution"]["attempts"][0]["status"] = "recovered"
        current["execution"]["attempts"][0]["result"] = {"exit_code": 1}
        current["execution"]["active_attempt"] = None
        current["checkpoints"].append({"turn": 1, "tree_hash": "0" * 64,
                                       "gate": {"result": "fail"}})
        goal.commit_revision(current, expected_revision=1)
    with pytest.raises(goal.BudgetExhausted):
        steps.begin(state["slug"], "r2")
    assert goal.load(state["slug"])["used"]["turns"] == 1
