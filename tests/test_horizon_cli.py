"""Exercise real skill CLI processes instead of obsolete pasted-Python fakes."""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
import tree_hash


@pytest.fixture(params=["codex", "claude"])
def client(tmp_path, request):
    project = tmp_path / "working project "
    project.mkdir()
    runtime = request.param
    (project / (".agents" if runtime == "codex" else ".claude")).mkdir()
    folder = ".agents" if runtime == "codex" else ".claude"
    entry = ROOT / folder / "skills/horizon/scripts/harness.py"
    home = tmp_path / "isolated home"
    home.mkdir()
    env = dict(os.environ, HOME=str(home), CODEX_HOME=str(home / ".codex"),
               PYTHONDONTWRITEBYTECODE="1")

    def run(*args, skill="horizon", cwd=None, scope="project"):
        executable = entry if skill == "horizon" else entry.parent.parent.parent / "refine/scripts/harness.py"
        result = subprocess.run(
            [sys.executable, str(executable), "--runtime", runtime,
             "--scope", scope, *args], cwd=cwd or project, env=env,
            capture_output=True, text=True, timeout=10)
        report = json.loads(result.stdout or result.stderr)
        return result.returncode, report
    return project, runtime, run


def start(run, *args):
    code, state = run("start", "Real CLI integration", *args)
    assert code == 0, state
    return state["slug"]


def check(run, slug, *args):
    return run("check", slug, "--did", "Performed a useful increment",
               "--next", "Inspect evidence and finish", *args)


def test_gate_runs_once_and_keeps_pre_gate_hash(client):
    project, _, run = client
    artifact = project / "counter.txt"
    artifact.write_text("")
    before = tree_hash.compute(project)
    slug = start(run, "--gate", "printf x >> counter.txt")
    code, report = check(run, slug)
    assert code == 0, report
    assert artifact.read_text() == "x"
    assert report["used"]["turns"] == 1
    assert report["last_checkpoint"]["tree_hash"] == before
    assert tree_hash.compute(project) != before
    assert run("complete", slug)[0] == 2


def test_unreadable_snapshot_records_work_and_refuses_completion(client):
    project, _, run = client
    os.mkfifo(project / "unreadable-input")
    slug = start(run, "--gate", "true")
    code, report = check(run, slug)
    assert code == 2, report
    assert report["used"]["turns"] == 1
    assert report["last_checkpoint"]["gate"] == "fail"
    assert report["last_checkpoint"]["tree_hash"] == "unknown"
    assert "fingerprint" in report["error"]
    assert run("complete", slug)[0] == 2


def test_coverage_records_work_before_review_and_completes(client):
    project, _, run = client
    (project / "report.md").write_text("All three acceptance items are covered.")
    slug = start(run, "--coverage", "Cover every acceptance item.", "--max-turns", "1")
    code, report = check(run, slug)
    assert code == 1
    assert report["review_required"] is True
    assert report["used"]["turns"] == 1
    assert report["budget_exhausted"] is True
    assert run("complete", slug)[0] == 2
    evidence = project.parent / "review.txt"
    evidence.write_text(f"Reviewed checkpoint: {report['last_checkpoint']['tree_hash'][:12]}. "
                        "Inspected report.md: all three required acceptance items are present.")
    code, reviewed = run("review", slug, "--result", "pass", "--judge", "independent-test-fixture",
                         "--hash", report["last_checkpoint"]["tree_hash"],
                         "--evidence-file", str(evidence))
    assert code == 0, reviewed
    assert reviewed["used"]["turns"] == 1
    assert run("complete", slug)[1]["status"] == "complete"
    assert check(run, slug)[0] == 2


def test_missing_review_evidence_is_not_a_pass(client):
    _, _, run = client
    slug = start(run, "--coverage", "Check the actual artifact.")
    check(run, slug)
    code, error = run("review", slug, "--result", "pass", "--judge", "judge",
                      "--hash", "0" * 64, "--evidence-file", "/missing/evidence.txt")
    assert code == 2
    assert error["error"] == "FileNotFoundError"
    assert run("complete", slug)[0] == 2


def test_timeout_is_checkpointed_and_can_be_retried(client):
    _, _, run = client
    cmd = shlex.quote(sys.executable) + " -c 'import time; time.sleep(1.1)'"
    slug = start(run, "--gate", cmd)
    code, report = check(run, slug, "--timeout", "1")
    assert code == 2
    assert "timed out" in report["error"]
    assert report["status"] == "active"
    assert report["used"]["turns"] == 1
    code, report = check(run, slug, "--timeout", "3")
    assert code == 0, report
    assert report["used"]["turns"] == 2
    assert run("complete", slug)[0] == 0


def test_missing_command_is_diagnosed_without_losing_checkpoint(client):
    _, _, run = client
    slug = start(run, "--gate", "no-such-executable-harness-fixture")
    code, report = check(run, slug)
    assert code == 2
    assert "not found" in report["error"]
    assert report["status"] == "active"
    assert report["used"]["turns"] == 1
    assert run("complete", slug)[0] == 2


def test_force_reruns_gate_with_unchanged_local_inputs(client):
    project, _, run = client
    counter = project.parent / "external-counter"
    cmd = "printf x >> " + shlex.quote(str(counter))
    slug = start(run, "--gate", cmd)
    check(run, slug)
    assert check(run, slug)[1]["last_checkpoint"]["gate"] == "skipped"
    assert counter.read_text() == "x"
    assert check(run, slug, "--force")[0] == 0
    assert counter.read_text() == "xx"


def test_refinement_apply_recall_observe_and_undo(client):
    project, _, run = client
    _, doctor = run("doctor", skill="refine")
    memory = Path(doctor["targets"]["M"])
    note = memory / "quoting.md"
    index = memory / "MEMORY.md"
    proposal = project.parent / "proposal.json"
    proposal.write_text(json.dumps({
        "layer": "M", "rationale": "Repeated shell path failure",
        "evidence": "Two task commands failed because a path with spaces was unquoted.",
        "edits": [{"path": str(note), "content": "Quote shell paths that contain spaces.\n"},
                  {"path": str(index), "kind": "append_line",
                   "content": "- [Path quoting](quoting.md)"}]}))
    code, applied = run("refine-apply", str(proposal), "--approved", skill="refine")
    assert code == 0, applied
    assert run("refine-count", skill="refine")[1]["used"] == 1
    recalled = run("recall", "--query", "quoting shell", skill="refine")[1]
    assert recalled["notes"][0]["path"] == str(note)
    assert "[Path quoting]" in recalled["index"]
    evidence = project.parent / "observation.txt"
    evidence.write_text("Task two used the stored quoting lesson and its command succeeded.")
    code, observed = run("refine-outcome", applied["id"], "--outcome", "helpful",
                         "--task", "task-two", "--evidence-file", str(evidence), skill="refine")
    assert code == 0, observed
    history = run("refine-history", skill="refine")[1]["entries"]
    assert history[0]["outcomes"][0]["task"] == "task-two"
    assert run("refine-count", skill="refine")[1]["used"] == 1
    assert run("refine-undo", "--entry", applied["id"], skill="refine")[0] == 0
    assert not note.exists()
    assert not index.exists()


def test_nested_projects_keep_runtime_and_global_routes(client):
    project, runtime, run = client
    nested = project / "src/deep"
    nested.mkdir(parents=True)
    code, doctor = run("doctor", cwd=nested)
    assert code == 0
    assert doctor["project"] == str(project)
    _, global_doctor = run("doctor", cwd=nested, scope="global")
    assert doctor["targets"]["M"] == global_doctor["targets"]["M"]
    assert global_doctor["targets"]["rho"] != doctor["targets"]["rho"]
    if runtime == "codex":
        assert Path(global_doctor["targets"]["rho"]).parent.name == ".codex"
        assert Path(global_doctor["targets"]["G"]).parent.name == ".agents"

def _gate_dir(run, project, runtime, name="ready"):
    _, doctor = run("doctor")
    gates = Path(doctor["gates"]) / name
    assert gates.parent == project / (".codex" if runtime == "codex" else ".claude") / "harness" / "gates"
    gates.mkdir(parents=True)
    (gates / "request.md").write_text("Make ready.txt say ready.\n")
    (gates / "requirements.md").write_text('R1 [gate] "ready.txt says ready"\n')
    return gates


def test_start_takes_a_gate_path_from_doctor(client):
    project, runtime, run = client
    gates = _gate_dir(run, project, runtime)
    code, state = run("start", "Ready file", "--gate", "true", "--gate-path", str(gates))
    assert code == 0, state
    assert state["gate_path"] == str(gates)
    code, error = run("start", "Missing gates", "--gate", "true", "--gate-path", "nowhere")
    assert code == 2 and error["error"] == "GateRequired"

def test_status_prints_a_deadline_for_timed_goals(client):
    _, _, run = client
    slug = start(run, "--gate", "true", "--max-minutes", "25")
    code, report = run("status", slug)
    assert code == 0
    assert report["deadline"] and 24.0 <= report["minutes_left"] <= 25.0
