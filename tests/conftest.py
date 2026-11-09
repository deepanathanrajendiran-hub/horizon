import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))


@pytest.fixture(params=["codex", "claude"])
def make_state_goal(tmp_path, monkeypatch, request):
    import goal

    project = tmp_path / "working project "
    project.mkdir()
    (project / (".agents" if request.param == "codex" else ".claude")).mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("CONTINUAL_HARNESS_RUNTIME", request.param)

    def make(**overrides):
        args = {
            "goal": "Repair the parser",
            "cwd": str(project),
            "gate_kind": "command",
            "gate_value": "true",
            "max_turns": 2,
            "execution": "state",
        }
        args.update(overrides)
        return goal.create(**args)

    return make

@pytest.fixture(params=["codex", "claude"])
def state_client(tmp_path, request):
    import json
    import os
    import subprocess

    project = tmp_path / "cli project "
    project.mkdir()
    runtime = request.param
    folder = ".agents" if runtime == "codex" else ".claude"
    (project / folder).mkdir()
    isolated_home = tmp_path / "isolated home"
    isolated_home.mkdir()
    env = dict(
        os.environ,
        HOME=str(isolated_home),
        CODEX_HOME=str(isolated_home / ".codex"),
        PYTHONDONTWRITEBYTECODE="1",
    )
    entrypoint = ROOT / folder / "skills/horizon/scripts/harness.py"

    def run(*args):
        proc = subprocess.run(
            [sys.executable, str(entrypoint), "--runtime", runtime,
             "--project", str(project), *args],
            cwd=project, env=env, capture_output=True, text=True, timeout=10,
        )
        return proc.returncode, json.loads(proc.stdout or proc.stderr)

    return project, runtime, run


# exp/thin (2026-11-09): the Claude SKILL.md is the thin variant, with no requirement lines, no gate file of the
# agent's own and no reviewer. These tests pin the full protocol's wording in that file; the Codex skill and the
# guide still carry it and are still tested. tests/test_thin.py holds what the thin text must and must not say.
FULL_PROTOCOL_TEXT_PINS = (
    "test_e2eswe_fixes.py::test_the_skill_and_guide_carry_the_e2eswe_rules[SKILL.md-.claude]",
    "test_e2eswe_fixes.py::test_the_claude_skill_dispatches_the_reviewer_in_the_foreground",
    "test_genericcheck_fixes.py::test_the_skill_says_close_lists_the_sections_neither_cited_nor_built[.claude]",
    "test_heldout2_fixes.py::test_the_skill_carries_the_heldout2_rules[.claude]",
    "test_heldout_fixes.py::test_the_slim_skill_holds_the_core_rules_and_stays_small[.claude]",
    "test_heldout_fixes.py::test_the_skill_is_task_neutral[.claude]",
    "test_skill_contract.py::test_horizon_skills_share_their_sections",
    "test_skill_contract.py::test_a_goal_sets_up_in_one_copied_call[.claude]",
)


def pytest_collection_modifyitems(config, items):
    skip = pytest.mark.skip(reason="exp/thin: pins the full protocol's wording in the Claude SKILL.md")
    for item in items:
        if item.nodeid.endswith(FULL_PROTOCOL_TEXT_PINS):
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def _attended_by_default(monkeypatch):
    """HORIZON_UNATTENDED in the shell running the suite would change the Stop hook under every test; the tests of
    unattended runs set it themselves."""
    monkeypatch.delenv("HORIZON_UNATTENDED", raising=False)
