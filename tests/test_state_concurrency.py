"""Real-process concurrency and crash-boundary coverage for state mode."""

import hashlib
import json
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

import pytest

import gate
import goal
import paths
import steps


HARNESS = Path(__file__).resolve().parents[1] / "harness"


class InjectedCrash(BaseException):
    pass


def _child(project: Path, runtime: str, body: str, *args: object) -> subprocess.Popen:
    env = dict(
        os.environ,
        CONTINUAL_HARNESS_RUNTIME=runtime,
        PYTHONDONTWRITEBYTECODE="1",
        PYTHONPATH=str(HARNESS) + os.pathsep + os.environ.get("PYTHONPATH", ""),
    )
    return subprocess.Popen(
        [sys.executable, "-u", "-c", body, *(str(arg) for arg in args)],
        cwd=project,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _wait_for(path: Path, *processes: subprocess.Popen) -> None:
    deadline = time.monotonic() + 5
    while not path.exists():
        assert all(process.poll() is None for process in processes), (
            f"child exited before handshake: {[process.returncode for process in processes]}"
        )
        assert time.monotonic() < deadline, f"timed out waiting for {path}"
        time.sleep(0.01)


def _collect(process: subprocess.Popen) -> tuple[str, str]:
    try:
        return process.communicate(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.communicate(timeout=5)
        raise


def _cleanup(*processes: subprocess.Popen) -> None:
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=5)


def _finish_inputs(make_state_goal, tmp_path: Path, *, gate_value: str = "true",
                   max_turns: int = 3):
    state = make_state_goal(gate_value=gate_value, max_turns=max_turns)
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
        "did": "Recorded the worker result.",
        "ops": [{"op": "set", "field": "summary", "value": "Worker result"}],
    }))
    evidence = tmp_path / "worker-stopped.txt"
    evidence.write_text(
        "The fixture host observed worker-fixture exit normally before finishing."
    )
    return state, begun, attached, update, evidence


def _waiting_finish(make_state_goal, tmp_path: Path):
    started = tmp_path / "gate-started"
    release = tmp_path / "gate-release"
    counter = tmp_path / "gate-counter"
    gate_script = tmp_path / "waiting_gate.py"
    gate_script.write_text(
        "import sys\n"
        "import time\n"
        "from pathlib import Path\n"
        "started, release, counter = map(Path, sys.argv[1:4])\n"
        "with counter.open('ab') as stream:\n"
        "    stream.write(b'x')\n"
        "started.write_text('running')\n"
        "deadline = time.monotonic() + 5\n"
        "while not release.exists():\n"
        "    if time.monotonic() >= deadline:\n"
        "        raise SystemExit('fixture release timed out')\n"
        "    time.sleep(0.01)\n"
    )
    command = shlex.join([
        sys.executable, str(gate_script), str(started), str(release), str(counter)
    ])
    state, begun, attached, update, evidence = _finish_inputs(
        make_state_goal, tmp_path, gate_value=command
    )
    body = """
import json
import sys
from pathlib import Path
import steps
try:
    result = steps.finish(
        sys.argv[1], "r1", expected_revision=int(sys.argv[2]),
        update_file=Path(sys.argv[3]), worker_evidence_file=Path(sys.argv[4]),
    )
    print(json.dumps({"result": result}), flush=True)
except Exception as error:
    print(json.dumps({"error": type(error).__name__, "message": str(error)}), flush=True)
"""
    process = _child(
        Path(state["cwd"]), paths.runtime(), body, state["slug"],
        attached["execution_revision"], update, evidence,
    )
    _wait_for(started, process)
    return state, attached, update, evidence, release, counter, process, body

@pytest.mark.parametrize("request_ids", [("request-a", "request-b"), ("same", "same")])
def test_begin_races_reserve_exactly_one_attempt(make_state_goal, tmp_path, request_ids):
    state = make_state_goal(max_turns=3)
    project = Path(state["cwd"])
    release = tmp_path / "release"
    body = """
import json
import sys
import time
from pathlib import Path
import steps
ready, release = map(Path, sys.argv[3:5])
ready.write_text("ready")
deadline = time.monotonic() + 5
while not release.exists():
    if time.monotonic() >= deadline:
        raise SystemExit("barrier timed out")
    time.sleep(0.01)
try:
    print(json.dumps({"result": steps.begin(sys.argv[1], sys.argv[2])}), flush=True)
except Exception as error:
    print(json.dumps({"error": type(error).__name__, "message": str(error)}), flush=True)
"""
    ready = [tmp_path / f"ready-{index}" for index in range(2)]
    processes = [
        _child(project, paths.runtime(), body, state["slug"], request_id, ready[index], release)
        for index, request_id in enumerate(request_ids)
    ]
    try:
        for marker in ready:
            _wait_for(marker, *processes)
        release.write_text("go")
        records = []
        for process in processes:
            stdout, stderr = _collect(process)
            assert process.returncode == 0, stderr
            records.append(json.loads(stdout))
    finally:
        _cleanup(*processes)

    saved = goal.load(state["slug"])
    assert saved["used"]["turns"] == 1
    assert len(saved["execution"]["attempts"]) == 1
    if request_ids[0] == request_ids[1]:
        assert all("result" in record for record in records)
        assert {record["result"]["packet_sha256"] for record in records} == {
            saved["execution"]["attempts"][0]["packet"]["sha256"]
        }
        assert sum(record["result"]["replayed"] for record in records) == 1
    else:
        assert sum("result" in record for record in records) == 1
        loser = next(record for record in records if "error" in record)
        assert loser["error"] in {"StepBusy", "RevisionConflict"}


def test_duplicate_finish_cannot_start_a_second_gate(make_state_goal, tmp_path):
    state, attached, update, evidence, release, counter, first, body = _waiting_finish(
        make_state_goal, tmp_path
    )
    second = _child(
        Path(state["cwd"]), paths.runtime(), body, state["slug"],
        attached["execution_revision"], update, evidence,
    )
    try:
        second_out, second_err = _collect(second)
        second_record = json.loads(second_out)
        assert second.returncode == 0, second_err
        assert second_record["error"] == "StepBusy"
        release.write_text("release")
        first_out, first_err = _collect(first)
        first_record = json.loads(first_out)
        assert first.returncode == 0, first_err
        assert first_record["result"]["attempt_status"] == "accepted"
    finally:
        release.write_text("release")
        _cleanup(first, second)
    assert counter.read_bytes() == b"x"
    assert len(goal.load(state["slug"])["checkpoints"]) == 1


def test_recovery_is_refused_while_finish_runner_is_live(make_state_goal, tmp_path):
    state, _attached, _update, _terminal, release, _counter, process, _body = (
        _waiting_finish(make_state_goal, tmp_path)
    )
    before = goal.load(state["slug"])
    gate_pid = before["execution"]["attempts"][0]["gate_process"]["pid"]
    evidence = tmp_path / "recovery.txt"
    evidence.write_text(
        f"gate-state: stopped\ngate-pid: {gate_pid}\n"
        "The coordinator assertion is syntactically valid, but the finish runner is live."
    )
    try:
        with pytest.raises(steps.StepBusy):
            steps.recover(
                state["slug"], "r1",
                expected_revision=before["execution"]["revision"],
                reason="Attempted recovery while finish was live.",
                worker_state="stopped", evidence_file=evidence,
            )
        after = goal.load(state["slug"])
        assert after == before
    finally:
        release.write_text("release")
        _collect(process)
        _cleanup(process)


def test_reserved_attempt_survives_crash_before_dispatch(make_state_goal, tmp_path):
    state = make_state_goal(max_turns=1)
    marker = tmp_path / "reserved"
    body = """
import os
import sys
from pathlib import Path
import steps
steps.begin(sys.argv[1], "r1")
Path(sys.argv[2]).write_text("reserved")
os._exit(73)
"""
    process = _child(Path(state["cwd"]), paths.runtime(), body, state["slug"], marker)
    try:
        _wait_for(marker, process)
        _collect(process)
    finally:
        _cleanup(process)
    assert process.returncode == 73
    saved = goal.load(state["slug"])
    assert saved["used"]["turns"] == 1
    assert saved["execution"]["attempts"][0]["status"] == "reserved"
    assert saved["checkpoints"] == []

    evidence = tmp_path / "no-launch.txt"
    evidence.write_text(
        "Dispatch records show no worker handle was created and no gate was launched."
    )
    recovered = steps.recover(
        state["slug"], "r1", expected_revision=saved["execution"]["revision"],
        reason="Coordinator crashed before native dispatch.",
        worker_state="not-started", evidence_file=evidence,
    )
    assert recovered["attempt_status"] == "recovered"
    assert recovered["update_accepted"] is False
    assert recovered["checkpoint"]["gate"] == "fail"

def test_artifact_edit_survives_crash_and_marks_old_evidence_stale(
        make_state_goal, tmp_path):
    artifact = tmp_path / "working project " / "artifact.txt"
    artifact.write_text("version one")
    state, _begun, attached, update, terminal = _finish_inputs(
        make_state_goal, tmp_path, max_turns=3
    )
    first_update = json.loads(update.read_text())
    first_update["ops"] = [{"op": "upsert", "entry": {
        "id": "artifact", "kind": "observation",
        "statement": "Artifact is version one.",
        "evidence": [{
            "path": "artifact.txt",
            "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        }],
    }}]
    update.write_text(json.dumps(first_update))
    steps.finish(
        state["slug"], "r1", expected_revision=attached["execution_revision"],
        update_file=update, worker_evidence_file=terminal,
    )
    second = steps.begin(state["slug"], "r2")
    attached_second = steps.attach(
        state["slug"], "r2", "crashing-worker",
        expected_revision=second["execution_revision"],
    )
    marker = tmp_path / "artifact-edited"
    body = """
import os
import sys
from pathlib import Path
Path(sys.argv[1]).write_text("version two")
Path(sys.argv[2]).write_text("edited")
os._exit(74)
"""
    process = _child(Path(state["cwd"]), paths.runtime(), body, artifact, marker)
    try:
        _wait_for(marker, process)
        _collect(process)
    finally:
        _cleanup(process)
    assert process.returncode == 74
    assert artifact.read_text() == "version two"
    interrupted = goal.load(state["slug"])
    assert interrupted["execution"]["task"]["entries"][0]["statement"] == (
        "Artifact is version one."
    )
    evidence = tmp_path / "worker-crash.txt"
    evidence.write_text(
        "The fixture process exited after editing the artifact and before writing an update."
    )
    recovered = steps.recover(
        state["slug"], "r2",
        expected_revision=attached_second["execution_revision"],
        reason="Worker crashed before producing an update.",
        worker_state="stopped", evidence_file=evidence,
    )
    assert recovered["update_accepted"] is False
    next_packet = steps.begin(state["slug"], "r3")["packet"]
    assert next_packet["task"]["entries"][0]["freshness"] == "stale"
    after_recovery = goal.load(state["slug"])
    assert artifact.read_text() == "version two"
    assert after_recovery["execution"]["task"]["entries"][0]["statement"] == (
        "Artifact is version one."
    )


def test_crash_after_finishing_marker_records_only_recovery_failure(
        make_state_goal, tmp_path):
    state, _begun, attached, update, terminal = _finish_inputs(make_state_goal, tmp_path)
    marker = tmp_path / "finishing"
    body = """
import os
import sys
from pathlib import Path
import gate
import steps
def crash_before_spawn(*args, **kwargs):
    Path(sys.argv[5]).write_text("finishing")
    os._exit(75)
gate.run_gate_at = crash_before_spawn
steps.finish(
    sys.argv[1], "r1", expected_revision=int(sys.argv[2]),
    update_file=Path(sys.argv[3]), worker_evidence_file=Path(sys.argv[4]),
)
"""
    process = _child(
        Path(state["cwd"]), paths.runtime(), body, state["slug"],
        attached["execution_revision"], update, terminal, marker,
    )
    try:
        _wait_for(marker, process)
        _collect(process)
    finally:
        _cleanup(process)
    assert process.returncode == 75
    interrupted = goal.load(state["slug"])
    attempt = interrupted["execution"]["attempts"][0]
    assert attempt["status"] == "finishing"
    assert attempt["gate_process"] is None
    assert interrupted["checkpoints"] == []

    evidence = tmp_path / "gate-not-started.txt"
    evidence.write_text(
        "gate-state: not-started\n"
        "The fixture exited at the saved finishing marker before invoking Popen."
    )
    recovered = steps.recover(
        state["slug"], "r1",
        expected_revision=interrupted["execution"]["revision"],
        reason="Finish crashed before gate dispatch.",
        worker_state="stopped", evidence_file=evidence,
    )
    assert recovered["attempt_status"] == "recovered"
    assert recovered["checkpoint"]["gate"] == "fail"
    assert recovered["update_accepted"] is False


def test_spawn_persistence_failure_is_uncertain_and_reaps_owned_gate(
        make_state_goal, tmp_path, monkeypatch):
    state, _begun, attached, update, terminal = _finish_inputs(
        make_state_goal, tmp_path, gate_value="sleep 30"
    )
    marker = tmp_path / "spawn-save-attempted"
    spawned = []
    original_popen = gate.subprocess.Popen
    original_save = goal._save

    def capture_popen(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        if kwargs.get("start_new_session"):
            spawned.append(process)
        return process

    def fail_gate_record(candidate):
        attempt = candidate["execution"]["attempts"][0]
        if attempt["status"] == "finishing" and attempt["gate_process"] is not None:
            marker.write_text("spawned-before-persistence")
            raise OSError("injected gate metadata persistence failure")
        return original_save(candidate)

    monkeypatch.setattr(gate.subprocess, "Popen", capture_popen)
    monkeypatch.setattr(goal, "_save", fail_gate_record)
    with pytest.raises(steps.StepConflict, match="Could not record"):
        steps.finish(
            state["slug"], "r1", expected_revision=attached["execution_revision"],
            update_file=update, worker_evidence_file=terminal,
        )
    assert marker.read_text() == "spawned-before-persistence"
    assert len(spawned) == 1
    assert spawned[0].poll() is not None
    interrupted = goal.load(state["slug"])
    assert interrupted["execution"]["attempts"][0]["status"] == "finishing"
    assert interrupted["execution"]["attempts"][0]["gate_process"] is None
    assert interrupted["checkpoints"] == []

    monkeypatch.setattr(goal, "_save", original_save)
    evidence = tmp_path / "spawn-uncertain.txt"
    evidence.write_text(
        "gate-state: stopped\n"
        "The owned live handle was signaled and reaped after metadata persistence failed."
    )
    recovered = steps.recover(
        state["slug"], "r1",
        expected_revision=interrupted["execution"]["revision"],
        reason="Gate spawn metadata could not be persisted.",
        worker_state="stopped", evidence_file=evidence,
    )
    assert recovered["attempt_status"] == "recovered"

def test_crash_during_gate_uses_live_handle_for_cleanup(
        make_state_goal, tmp_path, monkeypatch):
    state, _begun, attached, update, terminal = _finish_inputs(make_state_goal, tmp_path)
    started = tmp_path / "owned-gate-started"
    release = tmp_path / "owned-gate-release"
    gate_script = tmp_path / "owned_gate.py"
    gate_script.write_text(
        "import sys, time\n"
        "from pathlib import Path\n"
        "started, release = map(Path, sys.argv[1:3])\n"
        "started.write_text('running')\n"
        "deadline = time.monotonic() + 5\n"
        "while not release.exists():\n"
        "    if time.monotonic() >= deadline:\n"
        "        raise SystemExit('release timed out')\n"
        "    time.sleep(0.01)\n"
    )
    owned = []

    def crash_while_running(_state, timeout, on_spawn):
        process = subprocess.Popen(
            [sys.executable, str(gate_script), str(started), str(release)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
        )
        owned.append(process)
        on_spawn(process)
        _wait_for(started, process)
        raise InjectedCrash("finish process crashed during the gate")

    monkeypatch.setattr(gate, "run_gate_at", crash_while_running)
    try:
        with pytest.raises(InjectedCrash):
            steps.finish(
                state["slug"], "r1", expected_revision=attached["execution_revision"],
                update_file=update, worker_evidence_file=terminal,
            )
        interrupted = goal.load(state["slug"])
        gate_process = interrupted["execution"]["attempts"][0]["gate_process"]
        assert gate_process["pid"] == owned[0].pid
        assert owned[0].poll() is None
    finally:
        release.write_text("release")
        for process in owned:
            _collect(process)
            assert process.poll() is not None

    monkeypatch.setattr(
        gate.os, "killpg",
        lambda *_args: (_ for _ in ()).throw(
            AssertionError("recovery must not signal the diagnostic saved PID")
        ),
    )
    evidence = tmp_path / "gate-stopped.txt"
    evidence.write_text(
        f"gate-state: stopped\ngate-pid: {gate_process['pid']}\n"
        "The fixture released and reaped the complete owned gate process group."
    )
    recovered = steps.recover(
        state["slug"], "r1",
        expected_revision=interrupted["execution"]["revision"],
        reason="Finish crashed while its gate was running.",
        worker_state="stopped", evidence_file=evidence,
    )
    assert recovered["attempt_status"] == "recovered"


def test_crash_after_gate_return_leaves_old_state_authoritative(
        make_state_goal, tmp_path):
    counter = tmp_path / "gate-returned"
    command = "printf x >> " + shlex.quote(str(counter))
    state, _begun, attached, update, terminal = _finish_inputs(
        make_state_goal, tmp_path, gate_value=command
    )
    marker = tmp_path / "before-terminal-commit"
    body = """
import os
import sys
from pathlib import Path
import goal
import steps
original_save = goal._save
def crash_before_commit(candidate):
    attempt = candidate["execution"]["attempts"][0]
    if attempt["status"] == "accepted":
        Path(sys.argv[5]).write_text("gate-returned")
        os._exit(76)
    return original_save(candidate)
goal._save = crash_before_commit
steps.finish(
    sys.argv[1], "r1", expected_revision=int(sys.argv[2]),
    update_file=Path(sys.argv[3]), worker_evidence_file=Path(sys.argv[4]),
)
"""
    process = _child(
        Path(state["cwd"]), paths.runtime(), body, state["slug"],
        attached["execution_revision"], update, terminal, marker,
    )
    try:
        _wait_for(marker, process)
        _collect(process)
    finally:
        _cleanup(process)
    assert process.returncode == 76
    assert counter.read_text() == "x"
    interrupted = goal.load(state["slug"])
    attempt = interrupted["execution"]["attempts"][0]
    assert attempt["status"] == "finishing"
    assert interrupted["execution"]["task"]["summary"] == ""
    assert interrupted["checkpoints"] == []

    evidence = tmp_path / "returned-gate-stopped.txt"
    evidence.write_text(
        f"gate-state: stopped\ngate-pid: {attempt['gate_process']['pid']}\n"
        "The gate returned and its live handle was reaped before the injected commit crash."
    )
    recovered = steps.recover(
        state["slug"], "r1",
        expected_revision=interrupted["execution"]["revision"],
        reason="Finish crashed before the atomic terminal commit.",
        worker_state="stopped", evidence_file=evidence,
    )
    assert recovered["update_accepted"] is False
    assert recovered["checkpoint"]["gate"] == "fail"


def test_crash_after_atomic_commit_replays_without_gate_or_checkpoint(
        make_state_goal, tmp_path):
    counter = tmp_path / "committed-gate"
    command = "printf x >> " + shlex.quote(str(counter))
    state, _begun, attached, update, terminal = _finish_inputs(
        make_state_goal, tmp_path, gate_value=command
    )
    marker = tmp_path / "committed-before-response"
    body = """
import os
import sys
from pathlib import Path
import steps
original_report = steps.report
def crash_after_commit(state, attempt, **kwargs):
    if attempt["status"] == "accepted":
        Path(sys.argv[5]).write_text("committed")
        os._exit(77)
    return original_report(state, attempt, **kwargs)
steps.report = crash_after_commit
steps.finish(
    sys.argv[1], "r1", expected_revision=int(sys.argv[2]),
    update_file=Path(sys.argv[3]), worker_evidence_file=Path(sys.argv[4]),
)
"""
    process = _child(
        Path(state["cwd"]), paths.runtime(), body, state["slug"],
        attached["execution_revision"], update, terminal, marker,
    )
    try:
        _wait_for(marker, process)
        _collect(process)
    finally:
        _cleanup(process)
    assert process.returncode == 77
    committed = goal.load(state["slug"])
    assert committed["execution"]["attempts"][0]["status"] == "accepted"
    assert len(committed["checkpoints"]) == 1
    assert counter.read_bytes() == b"x"

    replay = steps.finish(
        state["slug"], "r1", expected_revision=attached["execution_revision"],
        update_file=update, worker_evidence_file=terminal,
    )
    assert replay["replayed"] is True
    assert counter.read_bytes() == b"x"
    assert len(goal.load(state["slug"])["checkpoints"]) == 1
