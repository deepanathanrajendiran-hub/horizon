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
    # Review files live in the temporary directory; a shared one would carry rounds between tests.
    temp = tmp_path / "temp"
    temp.mkdir()
    env = dict(os.environ, HOME=str(home), CODEX_HOME=str(home / ".codex"),
               PYTHONDONTWRITEBYTECODE="1", TMPDIR=str(temp))

    def run(*args, cwd=None):
        result = subprocess.run(
            [sys.executable, str(entry), "--runtime", runtime, *args],
            cwd=cwd or project, env=env, capture_output=True, text=True, timeout=10)
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
    slug = start(run, "--gate", "printf x >> counter.txt")  # the baseline run appends once
    assert artifact.read_text() == "x"
    before = tree_hash.compute(project)
    code, report = check(run, slug)
    assert code == 0, report
    assert artifact.read_text() == "xx"
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
    assert counter.read_text() == "xx"  # the baseline run at start plus the first check
    assert check(run, slug, "--force")[0] == 0
    assert counter.read_text() == "xxx"




def test_nested_directories_resolve_to_the_project(client):
    project, runtime, run = client
    nested = project / "src/deep"
    nested.mkdir(parents=True)
    code, doctor = run("doctor", cwd=nested)
    assert code == 0
    assert doctor["project"] == str(project)
    assert doctor["runtime"] == runtime


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


def test_status_lists_short_rows_and_start_shows_other_goals(client):
    _, _, run = client
    first = start(run, "--gate", "true")
    code, second = run("start", "Second request", "--gate", "true")
    assert code == 0, second
    assert [row["slug"] for row in second["other_goals"]] == [first]
    code, listing = run("status")
    assert code == 0
    assert sorted(row["slug"] for row in listing["goals"]) == sorted([first, second["slug"]])
    assert all("last_checkpoint" not in row for row in listing["goals"])


def test_check_returns_a_filled_review_brief(client):
    project, runtime, run = client
    gates = _gate_dir(run, project, runtime)
    (project / "ready.txt").write_text("ready")
    slug = start(run, "--gate", "true", "--gate-path", str(gates),
                 "--review", "Every line of requirements.md holds")
    code, report = check(run, slug)
    assert code == 0, report
    digest = report["last_checkpoint"]["tree_hash"]
    brief = report["review_brief"]
    assert brief.startswith(f"Review checkpoint {digest}.")
    assert "Make ready.txt say ready." in brief and 'R1 [gate] "ready.txt says ready"' in brief
    assert "\nVERDICTS\nR1: ?\nEND VERDICTS\n" in brief
    assert report["review_file"] in brief
    assert Path(report["review_file"]).name == f"horizon-review-{slug}-{digest[:12]}-1.md"
    assert not Path(report["review_file"]).resolve().is_relative_to(project.resolve())
    assert (".codex/harness" if runtime == "codex" else ".claude/harness") in brief


def test_check_has_no_brief_when_no_review_is_due(client):
    _, _, run = client
    slug = start(run, "--gate", "false")
    code, report = check(run, slug)
    assert code == 1 and "review_brief" not in report and "review_file" not in report


def test_brief_after_a_failed_review_quotes_its_open_lines(client):
    project, _, run = client
    slug = start(run, "--gate", "true", "--review", "Every line holds")
    _, report = check(run, slug)
    digest = report["last_checkpoint"]["tree_hash"]
    reply = project.parent / "reply.md"
    reply.write_text(f"Reviewed checkpoint: {digest[:12]}\nR1: FAIL - ready.txt is missing\n")
    code, _ = run("review", slug, "--result", "fail", "--judge", "judge-1",
                  "--hash", digest, "--evidence-file", str(reply))
    assert code == 1
    (project / "ready.txt").write_text("ready")
    _, report = check(run, slug)
    assert report["review_brief"].endswith("Previous:\n> R1: FAIL - ready.txt is missing\n")
    assert report["review_file"].endswith("-2.md")
    assert "<<REQUEST>>" in report["review_brief"]

def test_a_cut_off_review_is_continued_by_the_next_check(client):
    # Mini GPT Stage A pilot: every session's reviewer hit the call cap before returning.
    project, runtime, run = client
    gates = _gate_dir(run, project, runtime)
    (gates / "requirements.md").write_text('R1 [gate] "ready.txt says ready"\nR2 [review] "it reads well"\n')
    (project / "ready.txt").write_text("ready")
    slug = start(run, "--gate", "true", "--gate-path", str(gates), "--review", "Every line holds")
    _, first = check(run, slug)
    digest = first["last_checkpoint"]["tree_hash"]
    assert first["review_carried"] == []
    Path(first["review_file"]).write_text(f"Reviewed checkpoint: {digest}\nR1: PASS - cat ready.txt - ready\n")
    code, second = run("close", slug)  # the next session, on the unchanged tree
    assert code == 1 and not second["complete"]
    assert second["review_file"].endswith("-2.md")
    assert second["review_carried"] == ["R1"]
    assert second["review_carried_from"] == [first["review_file"]]
    assert "\nVERDICTS\nR1: PASS\nR2: ?\nEND VERDICTS\n" in second["review_brief"]
    reply = (f"Reviewed checkpoint: {digest}\nR2: reads well.\n\n"
             "VERDICTS\nR1: PASS\nR2: PASS\nEND VERDICTS\n")
    Path(second["review_file"]).write_text(reply)
    code, _ = run("review", slug, "--result", "pass", "--judge", "judge-2",
                  "--hash", digest, "--evidence-file", second["review_file"])
    assert code == 0
    code, closed = run("close", slug)
    assert code == 0 and closed["complete"], closed

def test_start_survives_an_unreadable_sibling_goal(client):
    project, runtime, run = client
    first = start(run, "--gate", "true")
    harness = project / (".codex" if runtime == "codex" else ".claude") / "harness"
    (harness / "goals" / "broken.json").write_text("{not json")
    code, second = run("start", "Second request", "--gate", "true")
    assert code == 0, second
    rows = {row["slug"]: row for row in second["other_goals"]}
    assert set(rows) == {first, "broken"}
    assert "unreadable" in rows["broken"]["error"]
    code, listing = run("status")
    assert code == 0 and len(listing["goals"]) == 3


def test_start_records_baseline_and_close_completes_in_one_call(state_client):
    project, runtime, run = state_client
    code, started = run("start", "Keep the build green", "--gate", "true")
    assert code == 0
    slug = started["slug"]
    state_dir = project / (".codex" if runtime == "codex" else ".claude") / "harness" / "goals"
    saved = json.loads((state_dir / f"{slug}.json").read_text())
    assert [o["role"] for o in saved["observations"]] == ["baseline"]
    code, closed = run("close", slug)
    assert code == 0 and closed["complete"] is True


def test_close_reports_missing_review_with_brief(state_client):
    project, runtime, run = state_client
    (project / "test_new.py").write_text("def test(): pass\n")
    code, started = run("start", "Add a feature", "--gate", "true",
                        "--review", "Every requirement holds")
    slug = started["slug"]
    (project / "test_more.py").write_text("def test(): pass\n")
    code, closed = run("close", slug)
    assert code == 1 and closed["complete"] is False
    assert any("review" in m for m in closed["missing"])
    assert closed["review_file"] and closed["review_brief"]


def test_close_if_ready_finalizes_exhausted_budget(state_client):
    project, runtime, run = state_client
    code, started = run("start", "Make the gate pass", "--gate", "false", "--max-turns", "1")
    slug = started["slug"]
    run("check", slug, "--did", "tried", "--next", "stop")
    code, closed = run("close", slug, "--if-ready")
    assert closed["complete"] is False
    assert closed["finalized"]["reason"] == "budget_exhausted"


def test_step_reclaim_cli_refuses_live_lease(state_client):
    project, runtime, run = state_client
    code, started = run("start", "State goal", "--gate", "true", "--execution", "state")
    slug = started["slug"]
    code, begun = run("step-begin", slug, "--request-id", "r1")
    code, out = run("step-reclaim", slug, "--attempt", "r1",
                    "--expected-revision", str(begun["execution_revision"]))
    assert code == 2 and out["error"] == "StepBusy"


def test_step_reclaim_refuses_a_non_integer_revision_at_the_parser():
    sys.path.insert(0, str(ROOT / "harness"))
    import cli
    base = ["--runtime", "claude", "step-reclaim", "g", "--attempt", "r1", "--expected-revision"]
    assert cli.parser().parse_args([*base, "3"]).expected_revision == 3
    with pytest.raises(SystemExit) as raised:
        cli.parser().parse_args([*base, "abc"])
    assert raised.value.code == 2


def test_check_skips_review_when_a_strong_gate_needs_none(client):
    project, _, run = client
    (project / "test_old.py").write_text("def test(): pass\n")
    slug = start(run, "--gate", "true", "--review", "Every line holds")
    code, report = check(run, slug)
    assert code == 0, report
    assert report["review_required"] is False
    assert "review_file" not in report


def test_check_requires_review_when_tests_were_added(client):
    project, _, run = client
    (project / "test_old.py").write_text("def test(): pass\n")
    slug = start(run, "--gate", "true", "--review", "Every line holds")
    (project / "test_added.py").write_text("def test(): pass\n")
    code, report = check(run, slug)
    assert code == 0, report
    assert report["review_required"] is True
    assert report["review_file"] and report["review_brief"]


def test_close_hands_out_no_brief_when_the_block_is_a_changed_test(state_client):
    project, runtime, run = state_client
    (project / "test_old.py").write_text("def test(): pass\n")
    code, started = run("start", "Keep it green", "--gate", "true")
    slug = started["slug"]
    (project / "test_old.py").write_text("def test(): assert True\n")
    code, closed = run("close", slug)
    assert code == 1 and closed["complete"] is False
    assert any("test_old.py" in m for m in closed["missing"])
    assert "review_file" not in closed and "review_brief" not in closed


def test_close_on_a_failing_gate_shows_the_gate_output(state_client):
    project, runtime, run = state_client
    (project / "g.sh").write_text("echo BOOM-OUTPUT\nexit 1\n")
    code, started = run("start", "Make it pass", "--gate", "sh g.sh")
    slug = started["slug"]
    code, closed = run("close", slug)
    assert code == 1 and closed["complete"] is False
    assert closed["observation"]["gate"] == "fail"
    assert closed["observation"]["role"] == "final"
    assert "BOOM-OUTPUT" in closed["observation"]["gate_output"]
    assert closed["observation"]["tree_hash"]
    assert not any("run close" in m for m in closed["missing"])
    assert any("gate failed" in m for m in closed["missing"])


def test_observe_exit_code_follows_the_gate(state_client):
    project, runtime, run = state_client
    (project / "g.sh").write_text("echo BOOM-OUTPUT\nexit 1\n")
    code, started = run("start", "Make it pass", "--gate", "sh g.sh")
    slug = started["slug"]
    code, seen = run("observe", slug)
    assert code == 1
    assert seen["observation"]["gate"] == "fail"
    assert "BOOM-OUTPUT" in seen["observation"]["gate_output"]
    (project / "g.sh").write_text("exit 0\n")
    code, seen = run("observe", slug)
    assert code == 0 and seen["observation"]["gate"] == "pass"

def test_start_report_shows_the_baseline_observation(state_client):
    project, runtime, run = state_client
    (project / "gate.sh").write_text("echo BASELINE-MARKER\nexit 1\n")
    code, started = run("start", "Fix the parser", "--gate", "sh gate.sh")
    assert code == 0
    observation = started["observation"]
    assert observation["role"] == "baseline"
    assert observation["gate"] == "fail"
    assert "BASELINE-MARKER" in observation["gate_output"]
