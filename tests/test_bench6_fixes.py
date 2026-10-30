"""NL2Repo bench6 (fun repo: docs/evaluations/nl2repo-bench6/report.md): with the goal active and budget left,
Horizon ended capped sessions itself at 12-14 of 15 calls to give a handoff report ("the next action is recorded
for the following session"; once "a scope question that only you can answer"), and the runner read each early
end as the build being finished. Autopep8 lost 5 of its 10 sessions, fastapi-users 3 and wsgidav 4."""
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = str(ROOT / ".claude/skills/horizon/scripts/harness.py")


def _stop(project, active=False):
    proc = subprocess.run(
        [sys.executable, ENTRY, "--runtime", "claude", "--project", str(project), "hook", "Stop"],
        input=json.dumps({"session_id": "s1", "stop_hook_active": active}), capture_output=True, text=True,
        timeout=20)
    assert proc.returncode == 0 and "Traceback" not in proc.stderr
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def test_stop_sends_an_active_goal_back_to_work_once(state_client):
    project, runtime, run = state_client
    if runtime != "claude":
        return
    run("start", "Keep the build green", "--gate", "true")
    run("note", "keep-the-build-green", "--next", "Write pkg/core.py from SPEC.md lines 10-80")
    out = _stop(project)
    assert out["decision"] == "block"
    reason = out["reason"]
    assert "keep-the-build-green" in reason
    assert "Write pkg/core.py from SPEC.md lines 10-80" in reason
    assert "not the goal's deadline" in reason
    # The second stop in a row always goes through: a person can still be answered.
    assert _stop(project, active=True) is None
    assert run("status", "keep-the-build-green")[1]["status"] == "active"


def test_stop_lets_a_completed_goal_end(state_client):
    project, runtime, run = state_client
    if runtime != "claude":
        return
    run("start", "Keep the build green", "--gate", "true")
    (project / "work.py").write_text("x = 1\n")
    run("observe", "keep-the-build-green", "--role", "final")
    assert _stop(project) is None
    assert run("status", "keep-the-build-green")[1]["status"] == "complete"


def test_stop_lets_a_goal_with_no_budget_left_end(state_client):
    project, runtime, run = state_client
    if runtime != "claude":
        return
    run("start", "Keep the build green", "--gate", "false", "--max-turns", "1")
    (project / "work.py").write_text("x = 1\n")
    run("check", "keep-the-build-green", "--did", "Wrote work.py", "--next", "Fix the gate")
    assert run("status", "keep-the-build-green")[1]["budget_exhausted"] is True
    assert _stop(project) is None


def test_stop_leaves_state_mode_goals_to_their_workers(state_client):
    project, runtime, run = state_client
    if runtime != "claude":
        return
    run("start", "Keep the build green", "--gate", "true", "--execution", "state")
    assert _stop(project) is None


def _skill(folder):
    return " ".join((ROOT / folder / "skills/horizon/SKILL.md").read_text().split())


@pytest.mark.parametrize("folder", [".claude", ".agents"])
def test_skills_keep_building_until_the_session_is_cut_off(folder):
    text = _skill(folder)
    for phrase in ("A session's length is not the goal's deadline",
                   "keep building until the session is cut off",
                   "Never end a session to hand off",
                   "When no one is there to answer, do not stop to ask",
                   "record your ruling"):
        assert phrase in text, phrase
    # The old wording sent Haiku to its final report near the end of every capped session.
    assert "Near the deadline, leave the work buildable" not in text
    if folder == ".claude":
        assert "the Stop hook sends you back once" in text


def test_stop_reason_names_every_active_goal(state_client):
    project, runtime, run = state_client
    if runtime != "claude":
        return
    run("start", "Keep the build green", "--gate", "true")
    run("start", "Ship the parser", "--gate", "true")
    reason = _stop(project)["reason"]
    assert re.search(r"keep-the-build-green", reason) and re.search(r"ship-the-parser", reason)

@pytest.mark.parametrize("folder", [".claude", ".agents"])
def test_skills_build_breadth_first_and_before_verifying(folder):
    # bench6 transcript review: entry points built last or never (pdfplumber.open, jinja Environment, ydata
    # ProfileReport); 4-7 sessions of checks and reviews with the spec unbuilt (bleach, fastapi-users, Cachier,
    # Verifiers); bugs lost at the cap; `$HARNESS` gone in the next shell; a final report while budget remained.
    text = _skill(folder)
    for phrase in ("Breadth first. In the first session, make the whole package exist",
                   "the spec's main entry point",
                   "the packaging files the tree lists, with every file they name, such as the README",
                   "deepen the least-built part first", "not the next section in spec order",
                   "Build before you verify", "A check that passes the first time it runs is not progress",
                   "change the code, not the check",
                   "`note SLUG --finding", "`--fact`", "may already be done",
                   "never `$HARNESS`",
                   "go back to building rather than dispatching another",
                   "never comes before an unbuilt part of the library",
                   "Give the final report only when the goal is complete, its budget is spent, or a person "
                   "ends the work"):
        assert phrase in text, phrase
    for gone in ("End in the same turn with one complete report", "If you stop without completing",
                 "in dependency order, and the package"):
        assert gone not in text, gone

@pytest.mark.parametrize("folder", [".claude", ".agents"])
def test_skills_write_into_the_real_file_not_scratch_parts(folder):
    # Sonnet pilot 2, Autopep8: session 1 wrote the library as _parts/p1.py ... p5.py to join at the end; the
    # call cap came first, so nothing importable existed and session 1 scored 0 (plain: 545).
    text = _skill(folder)
    assert "Write straight into the real files" in text
    assert "never into scratch parts to join later" in text
