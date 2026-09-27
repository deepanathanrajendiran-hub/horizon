import hashlib
from pathlib import Path

import pytest

import goal
import paths
import steps
import task_state
import tree_hash


import json

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
                             "attempt_closed": True, "checkpoint": {"turn": 1, "gate": "fail"}}
        current["execution"]["active_attempt"] = None
        current["status"] = "complete"
        current["checkpoints"].append({"turn": 1, "tree_hash": "0" * 64,
                                       "gate": "pass"})
        goal.commit_revision(current, expected_revision=1)
    replay = steps.begin(state["slug"], "r1")
    assert replay["replayed"] is True
    assert replay["checkpoint"] == {"turn": 1, "gate": "fail"}
    assert replay["last_checkpoint"]["gate"] == "pass"
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

def test_attach_rejects_path_like_slug_before_creating_lock_file(make_state_goal):
    state = make_state_goal()
    escaped = Path(state["cwd"]) / "escaped"
    assert not escaped.exists()

    with pytest.raises(steps.StepError):
        steps.attach("../../../escaped/nested", "r1", "worker-1",
                     expected_revision=0)

    assert not escaped.exists()

@pytest.mark.parametrize("title", ["修复 解析器", "__parser"])
def test_created_unicode_and_underscore_slugs_round_trip(make_state_goal, title):
    state = make_state_goal(goal=title)
    begun = steps.begin(state["slug"], "r1")
    attached = steps.attach(state["slug"], "r1", "worker-1",
                            expected_revision=begun["execution_revision"])
    assert attached["attempt_status"] == "attached"
    assert goal.load(state["slug"])["execution"]["attempts"][0]["worker"]["handle"] == "worker-1"

def test_finish_is_replayable_and_charges_only_the_reserved_turn(make_state_goal, tmp_path):
    import json
    import shlex

    counter = tmp_path / "gate-counter"
    state = make_state_goal(max_turns=1,
                           gate_value="printf x >> " + shlex.quote(str(counter)))
    begun = steps.begin(state["slug"], "r1")
    attached = steps.attach(state["slug"], "r1", "worker-fixture",
                            expected_revision=begun["execution_revision"])
    update_file = tmp_path / "update.json"
    update_file.write_text(json.dumps({
        "schema_version": 1, "attempt_id": "r1",
        "base_revision": begun["packet"]["base_revision"],
        "did": "Inspected the stable project and requested its acceptance check.",
        "ops": [{"op": "set", "field": "next_action", "value": "Review the result."}],
    }))
    evidence = tmp_path / "worker-stopped.txt"
    evidence.write_text("The fixture host reports worker-fixture completed and released its workspace.")
    kwargs = {"expected_revision": attached["execution_revision"],
              "update_file": update_file, "worker_evidence_file": evidence}
    result = steps.finish(state["slug"], "r1", **kwargs)
    replay = steps.finish(state["slug"], "r1", **kwargs)
    assert result["exit_code"] == 0
    assert result["attempt_closed"] is True
    assert replay["replayed"] is True
    assert counter.read_text() == "x"
    saved = goal.load(state["slug"])
    assert saved["used"]["turns"] == 1
    assert len(saved["checkpoints"]) == 1
    assert saved["checkpoints"][0]["turn"] == 1
    assert saved["execution"]["task"]["next_action"] == "Review the result."
    assert saved["execution"]["attempts"][0]["pending"]["worker_evidence"]

def _finish_fixture(make_state_goal, tmp_path, **goal_kwargs):
    import json
    state = make_state_goal(**goal_kwargs)
    begun = steps.begin(state["slug"], "r1")
    attached = steps.attach(state["slug"], "r1", "worker-fixture",
                            expected_revision=begun["execution_revision"])
    update_file = tmp_path / "update.json"
    update_file.write_text(json.dumps({
        "schema_version": 1, "attempt_id": "r1",
        "base_revision": begun["packet"]["base_revision"],
        "did": "Completed an increment and requested verification.",
        "ops": [{"op": "set", "field": "next_action", "value": "Review result."}],
    }))
    evidence = tmp_path / "terminal-evidence.txt"
    evidence.write_text("Worker fixture has stopped and released its workspace.")
    return state, attached, update_file, evidence


def test_finish_replay_rejects_changed_update(make_state_goal, tmp_path):
    import json
    state, attached, update_file, evidence = _finish_fixture(make_state_goal, tmp_path)
    steps.finish(state["slug"], "r1", expected_revision=attached["execution_revision"],
                 update_file=update_file, worker_evidence_file=evidence)
    changed = json.loads(update_file.read_text())
    changed["did"] = "Different claim."
    update_file.write_text(json.dumps(changed))
    with pytest.raises(steps.StepConflict, match="differs"):
        steps.finish(state["slug"], "r1", expected_revision=attached["execution_revision"],
                     update_file=update_file, worker_evidence_file=evidence)
    assert len(goal.load(state["slug"])["checkpoints"]) == 1


@pytest.mark.parametrize("bad_op", [
    {"op": "set", "field": [], "value": "x"},
    {"op": "upsert", "entry": {"id": "q", "kind": [], "statement": "Question", "evidence": []}},
    {"op": "archive", "id": "missing", "reason": "No longer needed"},
])
def test_invalid_finish_update_keeps_attempt_attached(make_state_goal, tmp_path, bad_op):
    import json
    state, attached, update_file, evidence = _finish_fixture(make_state_goal, tmp_path)
    update = json.loads(update_file.read_text())
    update["ops"] = [bad_op]
    update_file.write_text(json.dumps(update))
    with pytest.raises(steps.StepError):
        steps.finish(state["slug"], "r1", expected_revision=attached["execution_revision"],
                     update_file=update_file, worker_evidence_file=evidence)
    saved = goal.load(state["slug"])
    assert saved["execution"]["attempts"][0]["status"] == "attached"
    assert saved["execution"]["revision"] == attached["execution_revision"]
    assert saved["execution"]["task"]["next_action"] != "Review result."
    assert saved["checkpoints"] == []


@pytest.mark.parametrize(("command", "exit_code", "gate_result"), [
    ("false", 1, "fail"),
    ("command_that_does_not_exist_horizon", 2, "fail"),
])
def test_finish_records_gate_failure_without_losing_useful_state(make_state_goal, tmp_path,
                                                                  command, exit_code, gate_result):
    state, attached, update_file, evidence = _finish_fixture(
        make_state_goal, tmp_path, gate_value=command)
    result = steps.finish(state["slug"], "r1", expected_revision=attached["execution_revision"],
                          update_file=update_file, worker_evidence_file=evidence)
    saved = goal.load(state["slug"])
    assert result["exit_code"] == exit_code
    assert result["checkpoint"]["gate"] == gate_result
    assert saved["execution"]["task"]["next_action"] == "Review result."
    assert saved["execution"]["attempts"][0]["status"] == "accepted"
    assert saved["checkpoints"][0] == result["checkpoint"]
    if exit_code == 2:
        assert result["error"]


def test_finish_timeout_saves_failed_checkpoint(make_state_goal, tmp_path):
    state, attached, update_file, evidence = _finish_fixture(
        make_state_goal, tmp_path, gate_value="sleep 30")
    result = steps.finish(state["slug"], "r1", expected_revision=attached["execution_revision"],
                          update_file=update_file, worker_evidence_file=evidence, timeout=1)
    assert result["exit_code"] == 2
    assert "timed out" in result["error"]
    assert goal.load(state["slug"])["checkpoints"][0]["gate"] == "fail"


def test_finish_coverage_awaits_review(make_state_goal, tmp_path):
    state, attached, update_file, evidence = _finish_fixture(
        make_state_goal, tmp_path, gate_kind="coverage", gate_value="Independent review")
    result = steps.finish(state["slug"], "r1", expected_revision=attached["execution_revision"],
                          update_file=update_file, worker_evidence_file=evidence)
    assert result["exit_code"] == 1
    assert result["review_required"] is True
    assert result["checkpoint"]["gate"] == "fail"


def test_finish_after_deadline_closes_reserved_attempt(make_state_goal, tmp_path):
    import filelock
    from datetime import datetime, timedelta, timezone
    state, attached, update_file, evidence = _finish_fixture(
        make_state_goal, tmp_path, max_minutes=1)
    with filelock.goal_locked(goal._path(state["slug"])):
        current = goal.load(state["slug"])
        current["used"]["started"] = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
        current = goal.commit_revision(current, expected_revision=attached["execution_revision"])
    result = steps.finish(state["slug"], "r1", expected_revision=current["execution"]["revision"],
                          update_file=update_file, worker_evidence_file=evidence)
    assert result["attempt_closed"] is True
    assert result["budget_exhausted"] is True


def test_finish_records_pre_gate_hash_when_gate_changes_watched_file(make_state_goal, tmp_path):
    import shlex
    state, attached, update_file, evidence = _finish_fixture(
        make_state_goal, tmp_path,
        gate_value="printf changed > " + shlex.quote(str(tmp_path / "working project " / "source.txt")))
    before = tree_hash.compute(state["cwd"], state["watch_paths"])
    result = steps.finish(state["slug"], "r1", expected_revision=attached["execution_revision"],
                          update_file=update_file, worker_evidence_file=evidence)
    assert result["checkpoint"]["tree_hash"] == before
    assert tree_hash.compute(state["cwd"], state["watch_paths"]) != before
    with pytest.raises(goal.GateNotPassed):
        goal.complete(state["slug"], "")


def test_finish_rejects_oversized_candidate_before_gate(make_state_goal, tmp_path):
    import json
    import shlex
    counter = tmp_path / "counter"
    state, attached, update_file, evidence = _finish_fixture(
        make_state_goal, tmp_path,
        gate_value="printf x >> " + shlex.quote(str(counter)))
    update = json.loads(update_file.read_text())
    update["ops"] = [
        {"op": "set", "field": "summary", "value": "x" * 2048},
        *[{"op": "upsert", "entry": {
            "id": f"q{i}", "kind": "question", "statement": "s" * 1024,
            "evidence": []}} for i in range(22)],
    ]
    update_file.write_text(json.dumps(update))
    with pytest.raises(steps.StepError, match="packet"):
        steps.finish(state["slug"], "r1", expected_revision=attached["execution_revision"],
                     update_file=update_file, worker_evidence_file=evidence)
    assert not counter.exists()
    saved = goal.load(state["slug"])
    assert saved["execution"]["attempts"][0]["status"] == "attached"
    assert saved["execution"]["task"]["entries"] == []

def test_fast_gate_passes_even_if_spawn_recording_is_delayed(make_state_goal, tmp_path, monkeypatch):
    import time
    import filelock
    state, attached, update_file, evidence = _finish_fixture(
        make_state_goal, tmp_path, gate_value="true")
    original = filelock.goal_locked
    def delayed_lock(target, timeout=10.0):
        if target == goal._path(state["slug"]):
            time.sleep(0.05)
        return original(target, timeout)
    monkeypatch.setattr(filelock, "goal_locked", delayed_lock)
    result = steps.finish(state["slug"], "r1", expected_revision=attached["execution_revision"],
                          update_file=update_file, worker_evidence_file=evidence)
    assert result["exit_code"] == 0
    assert result["checkpoint"]["gate"] == "pass"

def test_unknown_gate_hash_saves_failed_checkpoint(make_state_goal, tmp_path, monkeypatch):
    state, attached, update_file, evidence = _finish_fixture(make_state_goal, tmp_path)
    def unreadable(*args, **kwargs):
        raise OSError("cannot read watched tree")
    monkeypatch.setattr(tree_hash, "compute", unreadable)
    result = steps.finish(state["slug"], "r1", expected_revision=attached["execution_revision"],
                          update_file=update_file, worker_evidence_file=evidence)
    assert result["exit_code"] == 2
    assert result["checkpoint"]["gate"] == "fail"
    assert result["checkpoint"]["tree_hash"] == "unknown"
    assert result["error"]


def test_finish_replay_keeps_original_checkpoint_after_coverage_review(make_state_goal, tmp_path):
    state, attached, update_file, evidence = _finish_fixture(
        make_state_goal, tmp_path,
        gate_kind="coverage", gate_value="Independent review",
    )
    first = steps.finish(
        state["slug"], "r1",
        expected_revision=attached["execution_revision"],
        update_file=update_file, worker_evidence_file=evidence,
    )
    reviewed = goal.record_review(
        state["slug"], "pass",
        "Independent reviewer inspected the exact checkpoint and accepts it.",
        "reviewer-fixture", first["checkpoint"]["tree_hash"],
    )
    replay = steps.finish(
        state["slug"], "r1",
        expected_revision=attached["execution_revision"],
        update_file=update_file, worker_evidence_file=evidence,
    )
    assert replay["replayed"] is True
    assert replay["exit_code"] == 1
    assert replay["checkpoint"] == first["checkpoint"]
    assert replay["checkpoint"]["gate"] == "fail"
    assert replay["last_checkpoint"]["gate"] == "pass"
    assert reviewed["execution"]["revision"] == first["execution_revision"] + 1
    assert replay["execution_revision"] == reviewed["execution"]["revision"]

def test_next_packet_keeps_actual_gate_and_review_verdicts(make_state_goal, tmp_path):
    state, attached, update_file, evidence = _finish_fixture(
        make_state_goal, tmp_path, max_turns=2,
        gate_value="printf verified-output", review_criterion="Independent review")
    first = steps.finish(state["slug"], "r1", expected_revision=attached["execution_revision"],
                         update_file=update_file, worker_evidence_file=evidence)
    review_text = "Independent reviewer examined the verified source and accepted the result."
    goal.record_review(state["slug"], "pass", review_text,
                       "reviewer-fixture", first["checkpoint"]["tree_hash"])
    next_begin = steps.begin(state["slug"], "r2")
    assert next_begin["packet"]["latest_checkpoint"]["verdict"]["text"] == "verified-output"
    assert next_begin["packet"]["latest_review"]["verdict"]["text"] == review_text

def test_gate_edit_keeps_historical_provenance_and_marks_next_packet_stale(make_state_goal, tmp_path):
    import json
    import shlex
    source = tmp_path / "working project " / "evidence.txt"
    state, attached, update_file, evidence = _finish_fixture(
        make_state_goal, tmp_path, max_turns=2,
        gate_value="printf changed > " + shlex.quote(str(source)))
    source.write_text("before")
    update = json.loads(update_file.read_text())
    update["ops"] = [{"op": "upsert", "entry": {
        "id": "observed", "kind": "observation", "statement": "Source was before.",
        "evidence": [{"path": "evidence.txt",
                      "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}]}}]
    update_file.write_text(json.dumps(update))
    pre_gate_hash = tree_hash.compute(state["cwd"], state["watch_paths"])
    result = steps.finish(state["slug"], "r1", expected_revision=attached["execution_revision"],
                          update_file=update_file, worker_evidence_file=evidence)
    saved = goal.load(state["slug"])
    entry = saved["execution"]["task"]["entries"][0]
    assert entry["provenance"]["tree_hash"] == pre_gate_hash
    assert result["checkpoint"]["tree_hash"] == pre_gate_hash
    with pytest.raises(goal.GateNotPassed):
        goal.complete(state["slug"], "")
    next_packet = steps.begin(state["slug"], "r2")["packet"]
    assert next_packet["task"]["entries"][0]["freshness"] == "stale"
    assert next_packet["snapshot"]["evidence"][0]["reasons"]

def test_second_unchanged_attempt_saves_skipped_checkpoint(make_state_goal, tmp_path):
    import json
    import shlex
    counter = tmp_path / "counter"
    state, attached, update_file, evidence = _finish_fixture(
        make_state_goal, tmp_path, max_turns=2,
        gate_value="printf x >> " + shlex.quote(str(counter)))
    steps.finish(state["slug"], "r1", expected_revision=attached["execution_revision"],
                 update_file=update_file, worker_evidence_file=evidence)
    begun = steps.begin(state["slug"], "r2")
    attached2 = steps.attach(state["slug"], "r2", "worker-two",
                             expected_revision=begun["execution_revision"])
    update = json.loads(update_file.read_text())
    update["attempt_id"] = "r2"
    update["base_revision"] = begun["packet"]["base_revision"]
    update_file.write_text(json.dumps(update))
    second = steps.finish(state["slug"], "r2", expected_revision=attached2["execution_revision"],
                          update_file=update_file, worker_evidence_file=evidence)
    assert counter.read_text() == "x"
    assert second["exit_code"] == 1
    assert second["checkpoint"]["gate"] == "skipped"
    assert [item["turn"] for item in goal.load(state["slug"])["checkpoints"]] == [1, 2]


def test_runner_lock_rejects_concurrent_finish_without_state_change(make_state_goal, tmp_path):
    import filelock
    state, attached, update_file, evidence = _finish_fixture(make_state_goal, tmp_path)
    runner = goal._path(state["slug"]).with_name(f"{state['slug']}.r1.runner")
    with filelock.goal_locked(runner):
        with pytest.raises(steps.StepBusy):
            steps.finish(state["slug"], "r1", expected_revision=attached["execution_revision"],
                         update_file=update_file, worker_evidence_file=evidence)
    saved = goal.load(state["slug"])
    assert saved["execution"]["attempts"][0]["status"] == "attached"
    assert saved["execution"]["revision"] == attached["execution_revision"]

def test_finish_keeps_spawn_metadata_as_terminal_diagnostics(make_state_goal, tmp_path):
    state, attached, update_file, evidence = _finish_fixture(make_state_goal, tmp_path)
    result = steps.finish(state["slug"], "r1", expected_revision=attached["execution_revision"],
                          update_file=update_file, worker_evidence_file=evidence)
    attempt = goal.load(state["slug"])["execution"]["attempts"][0]
    process = attempt["gate_process"]
    assert result["exit_code"] == 0
    assert set(process) == {"pid", "pgid", "started_at", "finish_nonce"}
    assert process["pid"] == process["pgid"]
    assert process["finish_nonce"] == attempt["finish_nonce"]
    assert attempt["status"] == "accepted"

def test_force_runs_unchanged_second_gate(make_state_goal, tmp_path):
    import json
    import shlex
    counter = tmp_path / "counter"
    state, attached, update_file, evidence = _finish_fixture(
        make_state_goal, tmp_path, max_turns=2,
        gate_value="printf x >> " + shlex.quote(str(counter)))
    steps.finish(state["slug"], "r1", expected_revision=attached["execution_revision"],
                 update_file=update_file, worker_evidence_file=evidence)
    begun = steps.begin(state["slug"], "r2")
    attached2 = steps.attach(state["slug"], "r2", "worker-two",
                             expected_revision=begun["execution_revision"])
    update = json.loads(update_file.read_text())
    update["attempt_id"] = "r2"
    update["base_revision"] = begun["packet"]["base_revision"]
    update_file.write_text(json.dumps(update))
    second = steps.finish(state["slug"], "r2", expected_revision=attached2["execution_revision"],
                          update_file=update_file, worker_evidence_file=evidence, force=True)
    assert counter.read_text() == "xx"
    assert second["exit_code"] == 0
    assert second["checkpoint"]["gate"] == "pass"

def test_finish_requires_terminal_worker_evidence_before_gate(make_state_goal, tmp_path):
    import shlex
    counter = tmp_path / "counter"
    state, attached, update_file, evidence = _finish_fixture(
        make_state_goal, tmp_path, gate_value="printf x >> " + shlex.quote(str(counter)))
    evidence.unlink()
    with pytest.raises(steps.StepError, match="terminal evidence"):
        steps.finish(state["slug"], "r1", expected_revision=attached["execution_revision"],
                     update_file=update_file, worker_evidence_file=evidence)
    assert not counter.exists()
    saved = goal.load(state["slug"])
    assert saved["execution"]["attempts"][0]["status"] == "attached"
    assert saved["execution"]["revision"] == attached["execution_revision"]

def test_terminal_evidence_can_exceed_worker_update_limit(make_state_goal, tmp_path):
    state, attached, update_file, evidence = _finish_fixture(make_state_goal, tmp_path)
    evidence.write_text("Worker stopped. " + "x" * (64 * 1024))
    result = steps.finish(state["slug"], "r1", expected_revision=attached["execution_revision"],
                          update_file=update_file, worker_evidence_file=evidence)
    assert result["exit_code"] == 0
    ref = goal.load(state["slug"])["execution"]["attempts"][0]["pending"]["worker_evidence"]
    assert steps.read_blob(ref, Path(state["cwd"])) == evidence.read_bytes()

def test_recovery_keeps_work_and_spends_the_attempt(make_state_goal, tmp_path):
    state = make_state_goal(max_turns=1)
    begun = steps.begin(state["slug"], "r1")
    attached = steps.attach(
        state["slug"], "r1", "worker-fixture",
        expected_revision=begun["execution_revision"],
    )
    artifact = Path(state["cwd"]) / "partial.txt"
    artifact.write_text("Preserve this partial result.\n")
    evidence = tmp_path / "stopped.txt"
    evidence.write_text(
        "The fixture host confirms worker-fixture stopped; no gate was launched."
    )
    kwargs = {
        "expected_revision": attached["execution_revision"],
        "reason": "Worker stopped before returning its update.",
        "worker_state": "stopped",
        "evidence_file": evidence,
    }
    recovered = steps.recover(state["slug"], "r1", **kwargs)
    replay = steps.recover(state["slug"], "r1", **kwargs)
    assert recovered["exit_code"] == 1
    assert replay["replayed"] is True
    assert artifact.read_text() == "Preserve this partial result.\n"
    saved = goal.load(state["slug"])
    assert saved["used"]["turns"] == 1
    assert saved["execution"]["active_attempt"] is None
    assert saved["execution"]["task"]["next_action"] == (
        "Inspect the current workspace and saved evidence before continuing."
    )
    assert saved["checkpoints"][-1]["gate"] == "fail"
    with pytest.raises(goal.BudgetExhausted):
        steps.begin(state["slug"], "r2")


def test_recovery_replay_rejects_contradictions_and_accepted_attempts(
        make_state_goal, tmp_path):
    state = make_state_goal()
    begun = steps.begin(state["slug"], "r1")
    attached = steps.attach(
        state["slug"], "r1", "worker-fixture",
        expected_revision=begun["execution_revision"],
    )
    evidence = tmp_path / "stopped.txt"
    evidence.write_text(
        "The fixture host confirms worker-fixture stopped; no gate was launched."
    )
    recovered = steps.recover(
        state["slug"], "r1", expected_revision=attached["execution_revision"],
        reason="Worker stopped before returning its update.",
        worker_state="stopped", evidence_file=evidence,
    )
    revision = recovered["execution_revision"]

    changed = tmp_path / "changed.txt"
    changed.write_text(
        "The fixture host confirms worker-fixture stopped after a different observation; "
        "no gate was launched."
    )
    contradictions = [
        {"reason": "A different reason.", "worker_state": "stopped", "evidence_file": evidence},
        {"reason": "Worker stopped before returning its update.",
         "worker_state": "not-started", "evidence_file": evidence},
        {"reason": "Worker stopped before returning its update.",
         "worker_state": "stopped", "evidence_file": changed},
    ]
    for values in contradictions:
        with pytest.raises(steps.StepConflict):
            steps.recover(
                state["slug"], "r1", expected_revision=revision, **values
            )
    assert goal.load(state["slug"])["execution"]["revision"] == revision

    second = steps.begin(state["slug"], "r2")
    attached_second = steps.attach(
        state["slug"], "r2", "worker-two",
        expected_revision=second["execution_revision"],
    )
    update_file = tmp_path / "update.json"
    update_file.write_text(json.dumps({
        "schema_version": 1,
        "attempt_id": "r2",
        "base_revision": second["packet"]["base_revision"],
        "did": "Finished the reserved work.",
        "ops": [],
    }))
    terminal = tmp_path / "terminal.txt"
    terminal.write_text("The fixture host confirms worker-two exited normally.")
    steps.finish(
        state["slug"], "r2",
        expected_revision=attached_second["execution_revision"],
        update_file=update_file, worker_evidence_file=terminal,
    )
    accepted_evidence = tmp_path / "accepted-stopped.txt"
    accepted_evidence.write_text(
        "The fixture host confirms worker-two stopped; no gate remains running."
    )
    with pytest.raises(steps.StepConflict):
        steps.recover(
            state["slug"], "r2",
            expected_revision=goal.load(state["slug"])["execution"]["revision"],
            reason="Trying to rewrite an accepted attempt.",
            worker_state="stopped", evidence_file=accepted_evidence,
        )


def test_not_started_recovery_requires_reserved_unbound_attempt_and_dispatch_evidence(
        make_state_goal, tmp_path):
    state = make_state_goal()
    begun = steps.begin(state["slug"], "r1")
    weak = tmp_path / "weak.txt"
    weak.write_text("Not started.")
    with pytest.raises(steps.StepError):
        steps.recover(
            state["slug"], "r1", expected_revision=begun["execution_revision"],
            reason="Dispatch did not start.", worker_state="not-started",
            evidence_file=weak,
        )
    assert goal.load(state["slug"])["execution"]["active_attempt"] == "r1"

    evidence = tmp_path / "not-started.txt"
    evidence.write_text(
        "Dispatch records confirm no worker was started or bound, and no gate was launched."
    )
    recovered = steps.recover(
        state["slug"], "r1", expected_revision=begun["execution_revision"],
        reason="Dispatch did not create a worker.", worker_state="not-started",
        evidence_file=evidence,
    )
    assert recovered["attempt_status"] == "recovered"

    second = steps.begin(state["slug"], "r2")
    attached = steps.attach(
        state["slug"], "r2", "worker-two",
        expected_revision=second["execution_revision"],
    )
    with pytest.raises(steps.StepConflict):
        steps.recover(
            state["slug"], "r2", expected_revision=attached["execution_revision"],
            reason="Dispatch uncertainty.", worker_state="not-started",
            evidence_file=evidence,
        )


def _force_finishing(state, attached, *, gate_process):
    import filelock

    with filelock.goal_locked(goal._path(state["slug"])):
        current = goal.load(state["slug"])
        attempt = current["execution"]["attempts"][0]
        reference = {"path": "objects/pending.json", "sha256": "a" * 64}
        attempt["status"] = "finishing"
        attempt["finish_nonce"] = "nonce"
        attempt["update_sha256"] = "b" * 64
        attempt["pending"] = {
            "update": reference,
            "candidate": reference,
            "audit": reference,
            "worker_evidence": reference,
        }
        attempt["gate_process"] = gate_process
        return goal.commit_revision(
            current, expected_revision=attached["execution_revision"]
        )

def test_finishing_recovery_requires_free_runner_and_resolved_gate_liveness(
        make_state_goal, tmp_path):
    import filelock

    state = make_state_goal()
    begun = steps.begin(state["slug"], "r1")
    attached = steps.attach(
        state["slug"], "r1", "worker-fixture",
        expected_revision=begun["execution_revision"],
    )
    current = _force_finishing(
        state, attached,
        gate_process={
            "pid": 12345, "pgid": 12345,
            "started_at": goal._now(), "finish_nonce": "nonce",
        },
    )
    evidence = tmp_path / "stopped.txt"
    evidence.write_text(
        "The fixture host confirms worker-fixture stopped, but gate state is unknown."
    )
    runner = goal._path(state["slug"]).with_name(f"{state['slug']}.r1.runner")
    with filelock.goal_locked(runner):
        with pytest.raises(steps.StepBusy):
            steps.recover(
                state["slug"], "r1",
                expected_revision=current["execution"]["revision"],
                reason="Finish was interrupted.", worker_state="stopped",
                evidence_file=evidence,
            )
    with pytest.raises(steps.StepError):
        steps.recover(
            state["slug"], "r1",
            expected_revision=current["execution"]["revision"],
            reason="Finish was interrupted.", worker_state="stopped",
            evidence_file=evidence,
        )
    assert goal.load(state["slug"])["execution"]["active_attempt"] == "r1"

    evidence.write_text(
        "gate-state: stopped\n"
        "gate-pid: 12345\n"
        "The fixture host confirms worker-fixture stopped and the gate process group "
        "and all descendants stopped."
    )
    recovered = steps.recover(
        state["slug"], "r1",
        expected_revision=current["execution"]["revision"],
        reason="Finish was interrupted.", worker_state="stopped",
        evidence_file=evidence,
    )
    assert recovered["attempt_status"] == "recovered"


def test_worker_update_cannot_forge_required_review(make_state_goal, tmp_path):
    state = make_state_goal(review_criterion="Inspect the exact accepted snapshot.")
    begun = steps.begin(state["slug"], "r1")
    attached = steps.attach(
        state["slug"], "r1", "worker-fixture",
        expected_revision=begun["execution_revision"],
    )
    update_file = tmp_path / "update.json"
    update_file.write_text(json.dumps({
        "schema_version": 1,
        "attempt_id": "r1",
        "base_revision": begun["packet"]["base_revision"],
        "did": "Tried to forge review state.",
        "ops": [{"op": "set", "field": "reviews", "value": [{
            "result": "pass", "judge": "worker-fixture",
        }]}],
    }))
    terminal = tmp_path / "terminal.txt"
    terminal.write_text("The fixture host confirms worker-fixture exited normally.")
    with pytest.raises(steps.StepError):
        steps.finish(
            state["slug"], "r1",
            expected_revision=attached["execution_revision"],
            update_file=update_file, worker_evidence_file=terminal,
        )
    saved = goal.load(state["slug"])
    assert saved.get("reviews", []) == []
    assert saved["execution"]["attempts"][0]["status"] == "attached"

@pytest.mark.parametrize("observation", [
    (
        "Native host confirms the worker stopped. "
        "The gate is still running with live descendants."
    ),
    (
        "Native host confirms the worker stopped. "
        "I cannot establish whether the gate stopped."
    ),
    (
        "gate-state: stopped\n"
        "Native host confirms the worker stopped and observed the gate process group."
    ),
    (
        "gate-state: stopped\n"
        "gate-state: stopped\n"
        "gate-pid: 12345\n"
        "Native host confirms the worker and gate process group stopped."
    ),
    (
        "gate-state: stopped\n"
        "gate-state: not-started\n"
        "gate-pid: 12345\n"
        "Native host confirms the worker stopped after contradictory gate observations."
    ),
    (
        "gate-state: stopped\n"
        "gate-pid: 12345\n"
        "gate-pid: 12345\n"
        "Native host confirms the worker and gate process group stopped."
    ),
    (
        "gate-state: stopped\n"
        "gate-pid: 99999\n"
        "Native host confirms the worker and gate process group stopped."
    ),
    "gate-state: stopped\ngate-pid: 12345\n",
])
def test_finishing_recovery_rejects_unbound_or_ambiguous_gate_assertions(
        make_state_goal, tmp_path, observation):
    state = make_state_goal()
    begun = steps.begin(state["slug"], "r1")
    attached = steps.attach(
        state["slug"], "r1", "worker-fixture",
        expected_revision=begun["execution_revision"],
    )
    current = _force_finishing(
        state, attached,
        gate_process={
            "pid": 12345, "pgid": 12345,
            "started_at": goal._now(), "finish_nonce": "nonce",
        },
    )
    evidence = tmp_path / "gate-evidence.txt"
    evidence.write_text(observation)
    with pytest.raises(steps.StepError):
        steps.recover(
            state["slug"], "r1",
            expected_revision=current["execution"]["revision"],
            reason="Finish was interrupted.", worker_state="stopped",
            evidence_file=evidence,
        )
    saved = goal.load(state["slug"])
    assert saved["execution"]["active_attempt"] == "r1"
    assert saved["execution"]["attempts"][0]["status"] == "finishing"


def test_finishing_recovery_without_gate_record_accepts_explicit_not_started(
        make_state_goal, tmp_path):
    state = make_state_goal()
    begun = steps.begin(state["slug"], "r1")
    attached = steps.attach(
        state["slug"], "r1", "worker-fixture",
        expected_revision=begun["execution_revision"],
    )
    current = _force_finishing(state, attached, gate_process=None)
    evidence = tmp_path / "no-gate.txt"
    evidence.write_text(
        "gate-state: not-started\n"
        "The native host confirms the worker stopped before a gate process or "
        "any gate descendants were launched."
    )
    recovered = steps.recover(
        state["slug"], "r1",
        expected_revision=current["execution"]["revision"],
        reason="Finish stopped before gate launch.", worker_state="stopped",
        evidence_file=evidence,
    )
    assert recovered["attempt_status"] == "recovered"

def test_near_limit_recovery_still_allows_the_next_packet(
        make_state_goal, tmp_path):
    import filelock

    state = make_state_goal(max_turns=2)
    current_hash = tree_hash.compute(state["cwd"], state["watch_paths"])
    candidate = goal.load(state["slug"])
    candidate["audit_path"] = str(
        goal._path(state["slug"]).relative_to(Path(state["cwd"]))
    )
    preview = task_state.build_packet(
        candidate, attempt_id="r1", base_revision=2,
        current_hash=current_hash, runtime=paths.runtime(),
        reserve_verdicts=True,
    )
    reserved_size = len(task_state.canonical_bytes(
        task_state._with_packet_reservations(preview)
    ))
    candidate.pop("audit_path")
    candidate["goal"] += "x" * (task_state.PACKET_LIMIT - reserved_size)
    with filelock.goal_locked(goal._path(state["slug"])):
        state = goal.commit_revision(candidate, expected_revision=0)

    begun = steps.begin(state["slug"], "r1")
    evidence = tmp_path / "not-started.txt"
    evidence.write_text(
        "Dispatch records confirm no worker was started or bound, and no gate was launched."
    )
    recovered = steps.recover(
        state["slug"], "r1", expected_revision=begun["execution_revision"],
        reason="Dispatch did not create a worker.", worker_state="not-started",
        evidence_file=evidence,
    )
    following = steps.begin(state["slug"], "r2")
    assert recovered["attempt_status"] == "recovered"
    assert following["attempt_status"] == "reserved"
    assert len(task_state.canonical_bytes(following["packet"])) <= (
        task_state.PACKET_LIMIT
    )

def test_finishing_recovery_rejects_not_started_for_saved_gate(
        make_state_goal, tmp_path):
    state = make_state_goal()
    begun = steps.begin(state["slug"], "r1")
    attached = steps.attach(
        state["slug"], "r1", "worker-fixture",
        expected_revision=begun["execution_revision"],
    )
    current = _force_finishing(
        state, attached,
        gate_process={
            "pid": 12345, "pgid": 12345,
            "started_at": goal._now(), "finish_nonce": "nonce",
        },
    )
    evidence = tmp_path / "contradictory-gate.txt"
    evidence.write_text(
        "gate-state: not-started\n"
        "gate-pid: 12345\n"
        "The native host confirms the worker stopped but contradicts the saved "
        "record showing that a gate process launched."
    )
    with pytest.raises(steps.StepError):
        steps.recover(
            state["slug"], "r1",
            expected_revision=current["execution"]["revision"],
            reason="Finish was interrupted.", worker_state="stopped",
            evidence_file=evidence,
        )


def test_recovery_remains_available_when_the_procedure_changes(
        make_state_goal, tmp_path, monkeypatch):
    state = make_state_goal()
    begun = steps.begin(state["slug"], "r1")
    attached = steps.attach(
        state["slug"], "r1", "worker-fixture",
        expected_revision=begun["execution_revision"],
    )
    monkeypatch.setattr(
        steps.task_state, "procedure",
        lambda runtime: (_ for _ in ()).throw(
            AssertionError("recovery must not validate the current procedure")
        ),
    )
    evidence = tmp_path / "stopped-after-procedure-change.txt"
    evidence.write_text(
        "The native host confirms worker-fixture stopped before returning an "
        "update and no gate was launched."
    )
    recovered = steps.recover(
        state["slug"], "r1",
        expected_revision=attached["execution_revision"],
        reason="Worker stopped under an older procedure.",
        worker_state="stopped", evidence_file=evidence,
    )
    assert recovered["attempt_status"] == "recovered"

def test_real_cli_reserves_attaches_finishes_reviews_and_completes(state_client, tmp_path):
    project, _, run = state_client
    code, state = run(
        "start", "Create a ready marker", "--execution", "state",
        "--gate", "test -f ready.txt", "--review", "Inspect the ready marker.",
        "--max-turns", "1",
    )
    assert code == 0, state
    assert state["execution_mode"] == "state"
    assert state["turn_unit"] == "worker_attempt"
    code, begun = run("step-begin", state["slug"], "--request-id", "r1")
    assert code == 0, begun
    assert begun["packet"]["attempt_id"] == "r1"
    assert not (project / "ready.txt").exists()
    assert run("status", state["slug"])[1]["used"]["turns"] == 1

    code, error = run(
        "check", state["slug"], "--did", "Unreserved work", "--next", "Continue",
    )
    assert code == 2
    assert "step" in error["message"].lower()

    code, stale = run(
        "step-attach", state["slug"], "--attempt", "r1", "--worker", "worker-1",
        "--expected-revision", "0",
    )
    assert code == 2
    assert stale["attempt_id"] == "r1"
    assert stale["update_accepted"] is False

    code, attached = run(
        "step-attach", state["slug"], "--attempt", "r1", "--worker", "worker-1",
        "--expected-revision", str(begun["execution_revision"]),
    )
    assert code == 0, attached
    (project / "ready.txt").write_text("ready")
    update_file = tmp_path / "update.json"
    update_file.write_text(json.dumps({
        "schema_version": 1,
        "attempt_id": "r1",
        "base_revision": begun["packet"]["base_revision"],
        "did": "Created the ready marker.",
        "ops": [{"op": "set", "field": "next_action", "value": "Review ready marker."}],
    }))
    terminal = tmp_path / "terminal.txt"
    terminal.write_text("The native host confirms worker-1 stopped and released its workspace.")

    code, bad_timeout = run(
        "step-finish", state["slug"], "--attempt", "r1",
        "--expected-revision", str(attached["execution_revision"]),
        "--update-file", str(update_file), "--worker-evidence-file", str(terminal),
        "--timeout", "0",
    )
    assert code == 2
    assert bad_timeout["attempt_id"] == "r1"
    assert bad_timeout["update_accepted"] is False

    code, finished = run(
        "step-finish", state["slug"], "--attempt", "r1",
        "--expected-revision", str(attached["execution_revision"]),
        "--update-file", str(update_file), "--worker-evidence-file", str(terminal),
        "--force",
    )
    assert code == 0, finished
    assert finished["update_accepted"] is True
    assert finished["review_required"] is True
    code, replay = run(
        "step-finish", state["slug"], "--attempt", "r1",
        "--expected-revision", str(attached["execution_revision"]),
        "--update-file", str(update_file), "--worker-evidence-file", str(terminal),
    )
    assert code == 0, replay
    assert replay["replayed"] is True

    review_evidence = tmp_path / "review.txt"
    review_evidence.write_text("Independent reviewer inspected ready.txt and the accepted checkpoint.")
    code, reviewed = run(
        "review", state["slug"], "--result", "pass", "--judge", "reviewer-1",
        "--hash", finished["checkpoint"]["tree_hash"],
        "--evidence-file", str(review_evidence),
    )
    assert code == 0, reviewed
    (project / "ready.txt").write_text("stale")
    assert run("complete", state["slug"])[0] == 2
    (project / "ready.txt").write_text("ready")
    code, complete = run("complete", state["slug"])
    assert code == 0, complete
    assert complete["status"] == "complete"


def test_real_cli_recovers_reserved_attempt_and_doctor_is_read_only(state_client, tmp_path):
    project, runtime, run = state_client
    before = sorted(project.rglob("*"))
    code, doctor = run("doctor")
    assert code == 0, doctor
    assert doctor["state_supported"] is True
    assert "host" in doctor["worker_availability"].lower()
    assert sorted(project.rglob("*")) == before

    code, state = run(
        "start", "Recover a missing worker", "--execution", "state",
        "--gate", "true", "--max-turns", "2",
    )
    assert code == 0, state
    code, begun = run("step-begin", state["slug"], "--request-id", "r1")
    assert code == 0, begun
    evidence = tmp_path / "dispatch.txt"
    evidence.write_text(
        "Dispatch records confirm no worker was started or bound, and no gate was launched."
    )
    code, recovered = run(
        "step-recover", state["slug"], "--attempt", "r1",
        "--expected-revision", str(begun["execution_revision"]),
        "--reason", "Dispatch did not create a worker.", "--worker-state", "not-started",
        "--evidence-file", str(evidence),
    )
    assert code == 1, recovered
    assert recovered["attempt_status"] == "recovered"
    assert recovered["update_accepted"] is False

    folder = ".codex" if runtime == "codex" else ".claude"
    goal_path = project / folder / "harness" / "goals" / f'{state["slug"]}.json'
    malformed = json.loads(goal_path.read_text())
    malformed["execution"]["schema_version"] = 99
    goal_path.write_text(json.dumps(malformed))
    code, error = run("step-begin", state["slug"], "--request-id", "r2")
    assert code == 2
    assert error["attempt_id"] == "r2"
    assert error["update_accepted"] is False
    assert "execution_revision" not in error

def _seed_cli_finishing_attempt(project, runtime, slug, attempt_id):
    folder = ".codex" if runtime == "codex" else ".claude"
    path = project / folder / "harness" / "goals" / f"{slug}.json"
    state = json.loads(path.read_text())
    attempt = next(item for item in state["execution"]["attempts"]
                   if item["id"] == attempt_id)
    reference = {"path": "objects/pending.json", "sha256": "a" * 64}
    attempt.update({
        "status": "finishing",
        "gate_process": {
            "pid": 12345,
            "pgid": 12345,
            "started_at": "2026-09-23T00:00:00+00:00",
            "finish_nonce": "fixture-finish",
        },
        "finish_nonce": "fixture-finish",
        "update_sha256": "b" * 64,
        "pending": {
            "update": reference,
            "candidate": reference,
            "audit": reference,
            "worker_evidence": reference,
        },
    })
    state["execution"]["revision"] += 1
    path.write_text(json.dumps(state))
    return state["execution"]["revision"]


def test_real_cli_finishing_recovery_requires_bound_gate_headers(state_client, tmp_path):
    project, runtime, run = state_client
    code, state = run(
        "start", "Recover a stopped gate", "--execution", "state",
        "--gate", "true", "--max-turns", "1",
    )
    assert code == 0, state
    code, begun = run("step-begin", state["slug"], "--request-id", "r1")
    assert code == 0, begun
    code, attached = run(
        "step-attach", state["slug"], "--attempt", "r1", "--worker", "worker-1",
        "--expected-revision", str(begun["execution_revision"]),
    )
    assert code == 0, attached
    revision = _seed_cli_finishing_attempt(project, runtime, state["slug"], "r1")
    evidence = tmp_path / "gate-liveness.txt"
    rejected = [
        "gate-state: running\ngate-pid: 12345\n"
        "The native host confirms worker-1 stopped but reports an invalid gate state.",
        "gate-state: stopped\ngate-state: stopped\ngate-pid: 12345\n"
        "The native host confirms worker-1 and the gate process group stopped.",
        "gate-state: stopped\ngate-state: not-started\ngate-pid: 12345\n"
        "The native host confirms worker-1 and the gate process group stopped.",
        "gate-state: stopped\ngate-pid: 12345\ngate-pid: 12345\n"
        "The native host confirms worker-1 and the gate process group stopped.",
        "gate-state: stopped\ngate-pid: 99999\n"
        "The native host confirms worker-1 and the gate process group stopped.",
    ]
    for observation in rejected:
        evidence.write_text(observation)
        code, error = run(
            "step-recover", state["slug"], "--attempt", "r1",
            "--expected-revision", str(revision), "--reason", "Finish was interrupted.",
            "--worker-state", "stopped", "--evidence-file", str(evidence),
        )
        assert code == 2
        assert error["error"] == "StepError"
        assert error["message"]
        assert error["attempt_id"] == "r1"
        assert error["expected_revision"] == revision
        assert error["execution_revision"] == revision
        assert error["attempt_status"] == "finishing"
        assert error["update_accepted"] is False
        status = run("status", state["slug"])[1]
        assert status["active_attempt"]["status"] == "finishing"

    evidence.write_text(
        "GATE-STATE: StOpPeD\n"
        "gate-pid: 12345\n"
        "The native host confirms worker-1 and the complete gate process group, "
        "including descendants, stopped before recovery."
    )
    code, recovered = run(
        "step-recover", state["slug"], "--attempt", "r1",
        "--expected-revision", str(revision), "--reason", "Finish was interrupted.",
        "--worker-state", "stopped", "--evidence-file", str(evidence),
    )
    assert code == 1, recovered
    assert recovered["attempt_status"] == "recovered"
    assert recovered["update_accepted"] is False

@pytest.mark.parametrize("field,value", [
    ("gate", {"kind": "command", "value": "false"}),
    ("review_criterion", "Worker says review passed."),
    ("budget", {"max_turns": 999, "max_minutes": None}),
    ("watch_paths", []),
])
def test_worker_update_cannot_overwrite_goal_control_fields(
        make_state_goal, tmp_path, field, value):
    import shlex

    counter = tmp_path / f"gate-counter-{field}"
    state, attached, update_file, evidence = _finish_fixture(
        make_state_goal, tmp_path,
        review_criterion="Independent reviewer must inspect the checkpoint.",
        gate_value="printf x >> " + shlex.quote(str(counter)),
    )
    before = goal.load(state["slug"])
    update = json.loads(update_file.read_text())
    update["ops"] = [{"op": "set", "field": field, "value": value}]
    update_file.write_text(json.dumps(update))

    with pytest.raises(steps.StepError):
        steps.finish(
            state["slug"], "r1",
            expected_revision=attached["execution_revision"],
            update_file=update_file, worker_evidence_file=evidence,
        )

    after = goal.load(state["slug"])
    assert not counter.exists()
    assert after["gate"] == before["gate"]
    assert after["budget"] == before["budget"]
    assert after["watch_paths"] == before["watch_paths"]
    assert after.get("reviews", []) == before.get("reviews", [])
    assert after["execution"]["attempts"][0]["status"] == "attached"
