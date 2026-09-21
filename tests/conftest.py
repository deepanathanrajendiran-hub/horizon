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
