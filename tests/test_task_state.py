import sys
from pathlib import Path
import json
import hashlib
import pytest
import os
def test_update_preserves_omitted_fields_and_records_archives(tmp_path):
    from copy import deepcopy

    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))
    import task_state

    task = {"summary": "Parser investigation", "next_action": "Inspect branch",
            "entries": [{"id": "q1", "kind": "question", "statement": "Empty input?",
                         "evidence": [], "provenance": {"attempt_id": "r0",
                         "tree_hash": "a" * 64, "at": "2026-09-18T00:00:00Z"}}]}
    before = deepcopy(task)
    update = {"schema_version": 1, "attempt_id": "r1", "base_revision": 3,
              "did": "Resolved the input question.",
              "ops": [{"op": "archive", "id": "q1",
                       "reason": "Covered by the new regression test."}]}
    candidate, audit = task_state.merge_update(
        task, update, attempt_id="r1", base_revision=3, project=tmp_path,
        provenance={"attempt_id": "r1", "tree_hash": "a" * 64, "at": "2026-09-19T00:00:00Z"})
    assert task == before
    assert candidate["summary"] == before["summary"]
    assert candidate["next_action"] == before["next_action"]
    assert candidate["entries"] == []
    assert audit[0]["before"] == before["entries"][0]
    assert audit[0]["reason"] == "Covered by the new regression test."

def _state_module():
    source = str(Path(__file__).resolve().parents[1] / "harness")
    if source not in sys.path:
        sys.path.insert(0, source)
    import task_state
    return task_state


def _provenance():
    return {"attempt_id": "r1", "tree_hash": "a" * 64,
            "at": "2026-09-19T00:00:00Z"}


def _task(entries=None):
    return {"summary": "Parser investigation", "next_action": "Inspect branch",
            "entries": [] if entries is None else entries}


def _goal_state(task):
    return {"goal": "Keep parser input stable", "gate": {"kind": "command", "value": "pytest -q"},
            "watch_paths": ["/project"], "budget": {"max_turns": 3, "max_minutes": None},
            "used": {"turns": 1, "started": "2026-09-19T00:00:00Z"},
            "execution": {"mode": "state", "schema_version": 1, "revision": 2,
                          "task": task, "active_attempt": None, "attempts": []},
            "checkpoints": [], "reviews": [], "slug": "parser"}


def test_task_text_limits_use_utf8_bytes():
    state = _state_module()
    task = _task()
    task["summary"] = "é" * 1024
    task["next_action"] = "x" * 1024
    state.validate_task(task)
    task["summary"] += "é"
    with pytest.raises(state.StateError, match=r"task.summary.*2048"):
        state.validate_task(task)
    task["summary"] = "ok"
    task["next_action"] = "é" * 513
    with pytest.raises(state.StateError, match=r"task.next_action.*1024"):
        state.validate_task(task)


@pytest.mark.parametrize("update", [
    {"schema_version": 1, "attempt_id": "r1", "base_revision": 3, "did": "x", "ops": [], "extra": 1},
    {"schema_version": 1, "attempt_id": "r1", "base_revision": 3, "did": None, "ops": []},
    {"schema_version": 1, "attempt_id": "r1", "base_revision": True, "did": "x", "ops": []},
    {"schema_version": 1, "attempt_id": "r1", "base_revision": 3, "did": "x", "ops": [{"op": "set", "field": "entries", "value": []}]},
    {"schema_version": 1, "attempt_id": "r1", "base_revision": 3, "did": "x", "ops": [{"op": "upsert", "entry": {"id": "e1", "kind": "question", "statement": "why", "evidence": [], "provenance": _provenance()}}]},
])
def test_merge_rejects_malformed_or_immutable_worker_updates(tmp_path, update):
    state = _state_module()
    before = _task()
    with pytest.raises(state.StateError):
        state.merge_update(before, update, attempt_id="r1", base_revision=3,
                           project=tmp_path, provenance=_provenance())
    assert before == _task()


def test_merge_rejects_duplicate_and_contradictory_entry_operations(tmp_path):
    state = _state_module()
    task = _task([{"id": "e1", "kind": "question", "statement": "why", "evidence": [], "provenance": _provenance()}])
    update = {"schema_version": 1, "attempt_id": "r1", "base_revision": 3, "did": "x", "ops": [
        {"op": "archive", "id": "e1", "reason": "done"},
        {"op": "archive", "id": "e1", "reason": "again"},
    ]}
    with pytest.raises(state.StateError, match="contradictory"):
        state.merge_update(task, update, attempt_id="r1", base_revision=3,
                           project=tmp_path, provenance=_provenance())


def test_read_update_rejects_duplicate_keys_nonfinite_and_oversized_input(tmp_path):
    state = _state_module()
    duplicate = tmp_path / "duplicate.json"
    duplicate.write_text('{"schema_version":1,"schema_version":1}')
    with pytest.raises(state.StateError, match="duplicate key"):
        state.read_update(duplicate)
    nonfinite = tmp_path / "nonfinite.json"
    nonfinite.write_text('{"value":NaN}')
    with pytest.raises(state.StateError, match="non-finite"):
        state.read_update(nonfinite)
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"x" * (64 * 1024 + 1))
    with pytest.raises(state.StateError, match=r"update.*65536"):
        state.read_update(oversized)


def test_upsert_requires_matching_regular_project_evidence(tmp_path):
    state = _state_module()
    evidence = tmp_path / "result.txt"
    evidence.write_text("original")
    digest = hashlib.sha256(b"original").hexdigest()
    update = {"schema_version": 1, "attempt_id": "r1", "base_revision": 3, "did": "recorded result", "ops": [{"op": "upsert", "entry": {
        "id": "e1", "kind": "observation", "statement": "Result recorded", "evidence": [{"path": "result.txt", "sha256": digest}]}}]}
    candidate, _ = state.merge_update(_task(), update, attempt_id="r1", base_revision=3,
                                      project=tmp_path, provenance=_provenance())
    assert candidate["entries"][0]["provenance"] == _provenance()
    before = os.stat(evidence).st_mtime_ns
    evidence.write_text("changed")
    os.utime(evidence, ns=(before, before))
    with pytest.raises(state.StateError, match="sha256"):
        state.merge_update(_task(), update, attempt_id="r1", base_revision=3,
                           project=tmp_path, provenance=_provenance())


def test_evidence_rejects_missing_and_symlink_escape_but_historical_state_survives(tmp_path):
    state = _state_module()
    outside = tmp_path.parent / "outside-evidence.txt"
    outside.write_text("outside")
    link = tmp_path / "escaped.txt"
    link.symlink_to(outside)
    digest = hashlib.sha256(b"outside").hexdigest()
    update = {"schema_version": 1, "attempt_id": "r1", "base_revision": 3, "did": "x", "ops": [{"op": "upsert", "entry": {
        "id": "e1", "kind": "observation", "statement": "Observed", "evidence": [{"path": "escaped.txt", "sha256": digest}]}}]}
    with pytest.raises(state.StateError, match="project"):
        state.merge_update(_task(), update, attempt_id="r1", base_revision=3,
                           project=tmp_path, provenance=_provenance())
    historical = {"id": "old", "kind": "observation", "statement": "Old", "evidence": [{"path": "missing.txt", "sha256": "b" * 64}], "provenance": _provenance()}
    task = _task([historical])
    state.validate_task(task)
    inspected = state.inspect_evidence(historical, tmp_path, "a" * 64)
    assert inspected["freshness"] == "unverified"
    assert "missing" in " ".join(inspected["reasons"])
    assert historical == task["entries"][0]


@pytest.mark.parametrize("kind", ["observation", "failed_approach"])
def test_evidence_required_for_observations_and_failed_approaches(tmp_path, kind):
    state = _state_module()
    update = {"schema_version": 1, "attempt_id": "r1", "base_revision": 3, "did": "x", "ops": [{"op": "upsert", "entry": {
        "id": "e1", "kind": kind, "statement": "No evidence", "evidence": []}}]}
    with pytest.raises(state.StateError, match="evidence"):
        state.merge_update(_task(), update, attempt_id="r1", base_revision=3,
                           project=tmp_path, provenance=_provenance())


def test_questions_and_hypotheses_can_be_recorded_without_evidence(tmp_path):
    state = _state_module()
    for kind in ("question", "hypothesis"):
        update = {"schema_version": 1, "attempt_id": "r1", "base_revision": 3, "did": "x", "ops": [{"op": "upsert", "entry": {
            "id": kind, "kind": kind, "statement": "Investigate", "evidence": []}}]}
        candidate, _ = state.merge_update(_task(), update, attempt_id="r1", base_revision=3,
                                          project=tmp_path, provenance=_provenance())
        assert candidate["entries"][0]["kind"] == kind


def test_new_execution_schema_rejects_boolean_revision():
    state = _state_module()
    execution = state.new_execution()
    state.validate_execution(execution)
    execution["revision"] = True
    with pytest.raises(state.StateError, match="revision"):
        state.validate_execution(execution)


def test_packet_has_fixed_fields_and_never_drops_entries_to_fit():
    state = _state_module()
    entry = {"id": "e", "kind": "question", "statement": "x" * 1024,
             "evidence": [], "provenance": _provenance()}
    task = _task([dict(entry, id=f"e{i}") for i in range(64)])
    goal_state = _goal_state(task)
    with pytest.raises(state.StateError, match=r"packet.*24576"):
        state.build_packet(goal_state, attempt_id="a1", base_revision=2,
                           current_hash="a" * 64, runtime="codex")
    assert len(goal_state["execution"]["task"]["entries"]) == 64


def test_packet_limit_counts_escaped_control_characters_and_reservations():
    state = _state_module()
    packet = state.build_packet(_goal_state(_task()), attempt_id="a1", base_revision=2,
                                current_hash="a" * 64, runtime="codex", reserve_verdicts=True)
    assert set(packet) == {"schema_version", "attempt_id", "base_revision", "objective", "gate", "watch_paths", "budget", "used", "procedure", "task", "snapshot", "latest_checkpoint", "latest_review", "audit_path"}
    assert len(json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()) <= 24 * 1024
    state.validate_task(packet["task"])
    assert packet["snapshot"]["tree_hash"] == "a" * 64


def test_canonical_bytes_are_compact_sorted_utf8_and_procedure_is_bound_to_runtime_skill():
    state = _state_module()
    assert state.canonical_bytes({"z": "é", "a": 1}) == b'{"a":1,"z":"\\xc3\\xa9"}'.decode("unicode_escape").encode("latin1")
    result = state.procedure("codex")
    assert result["schema_version"] == 1
    assert result["instructions"]
    assert len(result["sha256"]) == 64
    with pytest.raises(state.StateError, match="runtime"):
        state.procedure("other")

def test_unknown_snapshot_never_reports_historical_evidence_as_current(tmp_path):
    state = _state_module()
    entry = {"id": "old", "kind": "question", "statement": "Question", "evidence": [],
             "provenance": _provenance()}
    inspection = state.inspect_evidence(entry, tmp_path, "unknown")
    assert inspection["freshness"] == "unverified"
    assert inspection["reasons"]


def test_text_boundaries_preserve_limit_minus_one_and_limit_values():
    state = _state_module()
    for size in (2047, 2048):
        task = _task()
        task["summary"] = "x" * size
        state.validate_task(task)
    task["summary"] = "x" * 2049
    with pytest.raises(state.StateError, match=r"task.summary.*2048.*2049"):
        state.validate_task(task)


def test_update_input_boundaries_are_measured_in_bytes(tmp_path):
    state = _state_module()
    at_limit = tmp_path / "at-limit.json"
    at_limit.write_bytes(b" " * (64 * 1024 - 2) + b"{}")
    assert state.read_update(at_limit) == {}
    over_limit = tmp_path / "over-limit.json"
    over_limit.write_bytes(b" " * (64 * 1024 - 1) + b"{}")
    with pytest.raises(state.StateError, match=r"update.*65536.*65537"):
        state.read_update(over_limit)

def test_packet_reservation_rejects_state_that_only_fits_without_future_verdicts():
    state = _state_module()
    goal_state = _goal_state(_task())
    goal_state["goal"] = "x" * 23000
    packet = state.build_packet(goal_state, attempt_id="a1", base_revision=2,
                                current_hash="a" * 64, runtime="codex")
    assert len(json.dumps(packet, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()) < 24 * 1024
    with pytest.raises(state.StateError, match=r"packet.*24576"):
        state.build_packet(goal_state, attempt_id="a1", base_revision=2,
                           current_hash="a" * 64, runtime="codex", reserve_verdicts=True)

def test_rejected_later_operation_does_not_mutate_accepted_task(tmp_path):
    state = _state_module()
    task = _task([{"id": "q1", "kind": "question", "statement": "Question", "evidence": [],
                   "provenance": _provenance()}])
    before = state.canonical_bytes(task)
    update = {"schema_version": 1, "attempt_id": "r1", "base_revision": 3, "did": "x", "ops": [
        {"op": "set", "field": "summary", "value": "Changed candidate"},
        {"op": "archive", "id": "absent", "reason": "not present"},
    ]}
    with pytest.raises(state.StateError, match="unknown entry"):
        state.merge_update(task, update, attempt_id="r1", base_revision=3,
                           project=tmp_path, provenance=_provenance())
    assert state.canonical_bytes(task) == before

def _attempt(status="reserved", **changes):
    result = {"id": "a1", "turn": 1, "base_revision": 0, "status": status,
              "packet": {"path": "attempts/a1.json", "sha256": "a" * 64},
              "procedure_sha256": "b" * 64, "worker": None, "gate_process": None,
              "finish_nonce": None, "update_sha256": None, "pending": None,
              "events": [], "result": None}
    result.update(changes)
    return result


def test_execution_rejects_boolean_schema_version_and_invalid_attempt_lifecycle():
    state = _state_module()
    execution = state.new_execution()
    execution["schema_version"] = True
    with pytest.raises(state.StateError, match="schema_version"):
        state.validate_execution(execution)
    execution = state.new_execution()
    execution["attempts"] = [_attempt(status="reserved")]
    execution["active_attempt"] = None
    with pytest.raises(state.StateError, match="active_attempt"):
        state.validate_execution(execution)
    execution["active_attempt"] = "a1"
    state.validate_execution(execution)
    execution["attempts"].append(_attempt(id="a2", status="attached",
                                           worker={"runtime": "codex", "handle": "h"}))
    with pytest.raises(state.StateError, match="nonterminal"):
        state.validate_execution(execution)
    execution = state.new_execution()
    execution["attempts"] = [{"id": "a1"}]
    with pytest.raises(state.StateError, match="attempt"):
        state.validate_execution(execution)


def test_update_rejects_boolean_schema_version_and_overflow_float(tmp_path):
    state = _state_module()
    update = {"schema_version": True, "attempt_id": "r1", "base_revision": 3,
              "did": "x", "ops": []}
    with pytest.raises(state.StateError, match="schema_version"):
        state.merge_update(_task(), update, attempt_id="r1", base_revision=3,
                           project=tmp_path, provenance=_provenance())
    overflow = tmp_path / "overflow.json"
    overflow.write_text('{"value":1e999}')
    with pytest.raises(state.StateError, match="non-finite"):
        state.read_update(overflow)


def test_set_and_upsert_use_separate_operation_namespaces(tmp_path):
    state = _state_module()
    update = {"schema_version": 1, "attempt_id": "r1", "base_revision": 3,
              "did": "x", "ops": [
                  {"op": "set", "field": "summary", "value": "Revised"},
                  {"op": "upsert", "entry": {"id": "summary", "kind": "question",
                      "statement": "An entry may share a field name", "evidence": []}},
              ]}
    candidate, audit = state.merge_update(_task(), update, attempt_id="r1", base_revision=3,
                                          project=tmp_path, provenance=_provenance())
    assert candidate["summary"] == "Revised"
    assert candidate["entries"][0]["id"] == "summary"
    assert [item["id"] for item in audit] == ["summary", "summary"]


def test_packet_bounds_verdict_metadata_and_points_to_durable_records():
    state = _state_module()
    goal_state = _goal_state(_task())
    goal_state["checkpoints"] = [{"turn": 1, "tree_hash": "a" * 64, "gate": "pass",
                                  "gate_output": "ok" * 3000, "metadata": "x" * 23000}]
    goal_state["reviews"] = [{"turn": 1, "tree_hash": "a" * 64,
                              "result": "pass", "evidence": "y" * 4000,
                              "metadata": "y" * 1000}]
    packet = state.build_packet(goal_state, attempt_id="a1", base_revision=2,
                                current_hash="a" * 64, runtime="codex",
                                reserve_verdicts=True)
    assert "metadata" not in packet["latest_checkpoint"]
    assert packet["latest_checkpoint"]["verdict"]["truncated"]
    assert packet["latest_checkpoint"]["verdict"]["record"] == "checkpoint:1"
    assert packet["latest_review"]["verdict"]["record"] == "review:1"
    assert len(state.canonical_bytes(packet)) <= state.PACKET_LIMIT

def test_execution_rejects_nonstring_status_and_worker_runtime_as_state_errors():
    state = _state_module()
    execution = state.new_execution()
    execution["active_attempt"] = "a1"
    execution["attempts"] = [_attempt(status=["reserved"])]
    with pytest.raises(state.StateError, match="status"):
        state.validate_execution(execution)
    execution["attempts"] = [_attempt(status="attached", worker={"runtime": ["codex"], "handle": "h"})]
    with pytest.raises(state.StateError, match="runtime"):
        state.validate_execution(execution)

def test_reserved_packet_covers_future_checkpoint_and_review():
    state = _state_module()
    goal_state = _goal_state(_task())
    goal_state["gate"]["review_criterion"] = "Independent review"
    before = state.build_packet(goal_state, attempt_id="a1", base_revision=2,
                                current_hash="a" * 64, runtime="codex",
                                reserve_verdicts=True)
    reservation = state._with_packet_reservations(before)
    goal_state["checkpoints"] = [{
        "turn": 1, "at": "2026-09-19T00:00:00Z", "did": "x" * 1024,
        "next": "x" * 1024, "tree_hash": "a" * 64, "gate": "pass",
        "gate_output": "x" * 4000, "gate_spec": dict(goal_state["gate"]),
    }]
    goal_state["reviews"] = [{
        "turn": 1, "tree_hash": "a" * 64, "at": "2026-09-19T00:00:00Z",
        "judge": "reviewer", "criterion": "Independent review", "result": "pass",
        "evidence": "x" * 4000,
    }]
    after = state.build_packet(goal_state, attempt_id="a1", base_revision=2,
                               current_hash="a" * 64, runtime="codex")
    assert len(state.canonical_bytes(after)) <= len(state.canonical_bytes(reservation))
    assert after["latest_checkpoint"]["verdict"]["truncated"]
    assert after["latest_review"]["verdict"]["truncated"]


def test_reserved_packet_covers_future_missing_evidence_reasons(tmp_path):
    state = _state_module()
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("present")
    digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
    entry = {"id": "q", "kind": "question", "statement": "Question",
             "evidence": [{"path": "evidence.txt", "sha256": digest}],
             "provenance": _provenance()}
    goal_state = _goal_state(_task([entry]))
    goal_state["cwd"] = str(tmp_path)
    before = state.build_packet(goal_state, attempt_id="a1", base_revision=2,
                                current_hash="a" * 64, runtime="codex",
                                reserve_verdicts=True)
    reservation = state._with_packet_reservations(before)
    evidence.unlink()
    after = state.build_packet(goal_state, attempt_id="a1", base_revision=2,
                               current_hash="a" * 64, runtime="codex")
    assert after["task"]["entries"][0]["freshness"] == "unverified"
    assert after["task"]["entries"][0]["freshness_reasons"]
    assert len(state.canonical_bytes(after["task"])) + len(state.canonical_bytes(after["snapshot"])) <= len(state.canonical_bytes(reservation["task"])) + len(state.canonical_bytes(reservation["snapshot"]))

def test_freshness_reservation_covers_longer_untruncated_boolean(monkeypatch):
    state = _state_module()
    entry = {"id": "q", "kind": "question", "statement": "Question",
             "evidence": [], "provenance": _provenance()}
    goal_state = _goal_state(_task([entry]))
    before = state.build_packet(goal_state, attempt_id="a1", base_revision=2,
                                current_hash="a" * 64, runtime="codex",
                                reserve_verdicts=True)
    reservation = state._with_packet_reservations(before)
    monkeypatch.setattr(state, "inspect_evidence",
                        lambda item, project, digest: {
                            "id": item["id"], "freshness": "unverified",
                            "reasons": ["x" * 252]})
    after = state.build_packet(goal_state, attempt_id="a1", base_revision=2,
                               current_hash="a" * 64, runtime="codex")
    assert after["task"]["entries"][0]["freshness_reasons_truncated"] is False
    actual = len(state.canonical_bytes(after["task"])) + len(state.canonical_bytes(after["snapshot"]))
    reserved = len(state.canonical_bytes(reservation["task"])) + len(state.canonical_bytes(reservation["snapshot"]))
    assert actual <= reserved

def test_truncated_freshness_reasons_point_to_durable_entry(monkeypatch):
    state = _state_module()
    entry = {"id": "q", "kind": "question", "statement": "Question",
             "evidence": [], "provenance": _provenance()}
    goal_state = _goal_state(_task([entry]))
    goal_state["audit_path"] = ".codex/horizon/goals/parser.json"
    monkeypatch.setattr(state, "inspect_evidence",
                        lambda item, project, digest: {
                            "id": item["id"], "freshness": "unverified",
                            "reasons": ["x" * 1000]})
    packet = state.build_packet(goal_state, attempt_id="a1", base_revision=2,
                                current_hash="a" * 64, runtime="codex",
                                reserve_verdicts=True)
    task_entry = packet["task"]["entries"][0]
    annotation = packet["snapshot"]["evidence"][0]
    assert task_entry["freshness_reasons_truncated"] is True
    assert annotation["reasons_truncated"] is True
    assert task_entry["freshness_reasons_record"] == "execution.task.entries[q]"
    assert annotation["reasons_record"] == "execution.task.entries[q]"
    assert packet["audit_path"] == goal_state["audit_path"]
