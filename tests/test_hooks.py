import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

import hooks  # noqa: E402

ENTRY = str(ROOT / ".claude/skills/horizon/scripts/harness.py")


def test_install_merges_and_is_idempotent(tmp_path):
    project = tmp_path / "my project "
    (project / ".claude").mkdir(parents=True)
    user_hook = {"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}]}}
    (project / ".claude/settings.json").write_text(json.dumps(user_hook))
    assert hooks.install(project, ENTRY) is True
    assert hooks.install(project, ENTRY) is False
    settings = json.loads((project / ".claude/settings.json").read_text())
    commands = [h["command"] for groups in settings["hooks"].values() for g in groups for h in g["hooks"]]
    assert "echo mine" in commands
    assert sum(hooks.MARK in c for c in commands) == 3
    assert all(f'"{ENTRY}"' in c for c in commands if hooks.MARK in c)


def test_install_leaves_invalid_settings_alone(tmp_path):
    project = tmp_path / "p"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude/settings.json").write_text("{oops")
    try:
        hooks.install(project, ENTRY)
        raised = False
    except hooks.HookInstallError:
        raised = True
    assert raised and (project / ".claude/settings.json").read_text() == "{oops"


def test_uninstall_removes_only_horizon(tmp_path):
    project = tmp_path / "p"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude/settings.json").write_text(json.dumps(
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}))
    hooks.install(project, ENTRY)
    assert hooks.uninstall(project) is True
    settings = json.loads((project / ".claude/settings.json").read_text())
    assert settings["hooks"] == {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}


def test_hook_command_never_fails_on_bad_input(tmp_path):
    project = tmp_path / "hook project "
    (project / ".claude").mkdir(parents=True)
    for event in ("SessionStart", "PostToolUse", "Stop"):
        for stdin in ("", "{not json", json.dumps({"session_id": "s1"})):
            proc = subprocess.run(
                [sys.executable, ENTRY, "--runtime", "claude", "--project", str(project), "hook", event],
                input=stdin, capture_output=True, text=True, timeout=20)
            assert proc.returncode == 0, proc.stderr
            assert "Traceback" not in proc.stderr


def test_session_start_records_session_and_reports_goals(state_client):
    project, runtime, run = state_client
    if runtime != "claude":
        return
    run("start", "Keep the build green", "--gate", "true")
    proc = subprocess.run(
        [sys.executable, ENTRY, "--runtime", "claude", "--project", str(project), "hook", "SessionStart"],
        input=json.dumps({"session_id": "s9"}), capture_output=True, text=True, timeout=20)
    out = json.loads(proc.stdout)
    assert "keep-the-build-green" in out["hookSpecificOutput"]["additionalContext"]
    assert (project / ".claude/harness/session").read_text().strip() == "s9"


def test_first_start_installs_hooks(state_client):
    project, runtime, run = state_client
    code, started = run("start", "Keep the build green", "--gate", "true")
    if runtime == "claude":
        settings = json.loads((project / ".claude/settings.json").read_text())
        assert any(hooks.MARK in h["command"] for g in settings["hooks"]["Stop"] for h in g["hooks"])
        assert started["hooks"] == "installed"
    else:
        assert "hooks" not in started


def _hook(project, event, payload):
    return subprocess.run(
        [sys.executable, ENTRY, "--runtime", "claude", "--project", str(project), "hook", event],
        input=json.dumps(payload), capture_output=True, text=True, timeout=20)


def test_hook_survives_corrupt_goal_files_and_missing_project(state_client, tmp_path):
    project, runtime, run = state_client
    if runtime != "claude":
        return
    run("start", "Keep the build green", "--gate", "true")
    goals = project / ".claude/harness/goals"
    (goals / "broken.json").write_text("{nope")
    (goals / "keep-the-build-green.leases.json").write_text("[]")
    for event in ("SessionStart", "PostToolUse", "Stop"):
        proc = _hook(project, event, {"session_id": "s1"})
        assert proc.returncode == 0 and "Traceback" not in proc.stderr
    proc = _hook(tmp_path / "missing dir", "Stop", {"session_id": "s1"})
    assert proc.returncode == 0 and "Traceback" not in proc.stderr


def test_post_tool_use_renews_only_this_sessions_lease(state_client):
    project, runtime, run = state_client
    if runtime != "claude":
        return
    run("start", "Keep the build green", "--gate", "true", "--execution", "state")
    code, begun = run("step-begin", "keep-the-build-green", "--request-id", "r1")
    attempt = begun["active_attempt"]["id"]
    lease_file = project / ".claude/harness/goals/keep-the-build-green.leases.json"
    lease_file.write_text(json.dumps({attempt: {"session": "mine", "renewed_at": 1.0}}))
    _hook(project, "PostToolUse", {"session_id": "other"})
    assert json.loads(lease_file.read_text())[attempt]["renewed_at"] == 1.0
    _hook(project, "PostToolUse", {"session_id": "mine"})
    assert json.loads(lease_file.read_text())[attempt]["renewed_at"] > 1.0


def test_stop_completes_goal_whose_evidence_allows_it(state_client):
    project, runtime, run = state_client
    if runtime != "claude":
        return
    run("start", "Keep the build green", "--gate", "true")
    run("observe", "keep-the-build-green", "--role", "final")
    proc = _hook(project, "Stop", {"session_id": "s1"})
    assert proc.returncode == 0
    code, state = run("status", "keep-the-build-green")
    assert state["status"] == "complete"


def test_install_and_uninstall_commands(state_client):
    project, runtime, run = state_client
    if runtime != "claude":
        return
    assert run("install-hooks")[1] == {"changed": True}
    assert run("install-hooks")[1] == {"changed": False}
    assert run("uninstall-hooks")[1] == {"changed": True}
    assert run("uninstall-hooks")[1] == {"changed": False}
