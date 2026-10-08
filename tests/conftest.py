import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
sys.path.append(str(ROOT))  # the bench package; appended so harness modules keep priority


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
