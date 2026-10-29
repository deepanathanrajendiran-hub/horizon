"""A session cut off mid-work still hands the next one a next action and the last gate run.

In the NL2Repo benchmark every Horizon session hit its call cap before `check`, so `next_action` stayed empty
and the SessionStart hook said "Next: not set" for ten sessions; each session re-learned the same state.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

import goal  # noqa: E402
import hooks  # noqa: E402

ENTRY = str(ROOT / ".claude/skills/horizon/scripts/harness.py")


@pytest.fixture(autouse=True)
def isolated_goals(tmp_path, monkeypatch):
    d = tmp_path / "goals"
    d.mkdir()
    (tmp_path / "project").mkdir()
    monkeypatch.setattr(goal.paths, "goals_dir", lambda: d)
    return d


def _make(gate_value="true"):
    return goal.create(goal="Build the library", cwd=str(goal.paths.goals_dir().parent / "project"),
                       gate_kind="command", gate_value=gate_value, max_turns=5)


def test_a_note_sets_the_next_action_without_a_turn_or_a_gate_run():
    slug = _make()["slug"]
    state = goal.note(slug, "write windowed() in more.py")
    assert goal.summary(state)["next_action"] == "write windowed() in more.py"
    assert goal.row(state)["next_action"] == "write windowed() in more.py"
    assert state["used"]["turns"] == 0 and state["checkpoints"] == [] and not state.get("observations")


def test_the_newest_of_checkpoint_note_and_observation_wins():
    slug = _make()["slug"]
    goal.checkpoint(slug, "did a", "from check", "h", "pass")
    assert goal.summary(goal.note(slug, "from note"))["next_action"] == "from note"
    goal.checkpoint(slug, "did b", "from a later check", "h", "pass")
    assert goal.summary(goal.load(slug))["next_action"] == "from a later check"
    state = goal.observe(slug, "resume", next_action="from observe")
    assert goal.summary(state)["next_action"] == "from observe"
    # an observation without --next leaves the next action alone
    assert goal.summary(goal.observe(slug, "resume"))["next_action"] == "from observe"


def test_note_refuses_a_closed_goal_and_an_empty_note():
    slug = _make()["slug"]
    with pytest.raises(ValueError):
        goal.note(slug, "  ")
    goal.abandon(slug, "stop")
    with pytest.raises(goal.GoalClosed):
        goal.note(slug, "too late")


def test_status_and_row_show_the_last_gate_run():
    (goal.paths.goals_dir().parent / "project" / "g.sh").write_text("echo 3 passed, 2 failed\nexit 1\n")
    slug = _make("sh g.sh")["slug"]
    state = goal.observe(slug, "resume")
    last = goal.summary(state)["last_observation"]
    assert last["gate"] == "fail" and last["role"] == "resume" and "2 failed" in last["gate_output"]
    assert goal.row(state)["last_observed"]["gate"] == "fail"


def test_session_start_names_the_next_action_and_the_last_gate_run(tmp_path):
    project = tmp_path / "proj "
    (project / ".claude").mkdir(parents=True)

    def cli(*args):
        return subprocess.run([sys.executable, ENTRY, "--runtime", "claude", "--project", str(project), *args],
                              cwd=project, capture_output=True, text=True, timeout=20)

    (project / "g.sh").write_text("echo 7 passed, 4 failed\nexit 1\n")
    started = json.loads(cli("start", "Build the library", "--gate", "sh g.sh").stdout)
    slug = started["slug"]
    noted = cli("note", slug, "--next", "fix windowed step handling")
    assert noted.returncode == 0, noted.stderr
    proc = subprocess.run([sys.executable, ENTRY, "--runtime", "claude", "--project", str(project),
                           "hook", "SessionStart"], input=json.dumps({"session_id": "s2"}),
                          capture_output=True, text=True, timeout=20)
    context = json.loads(proc.stdout)["hookSpecificOutput"]["additionalContext"]
    assert "Next: fix windowed step handling" in context
    assert "Last gate run: fail" in context and "4 failed" in context
