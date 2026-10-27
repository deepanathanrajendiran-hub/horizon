import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

import goal  # noqa: E402
import hooks  # noqa: E402
import leases  # noqa: E402
import steps  # noqa: E402
import time  # noqa: E402

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
    (project / "work.py").write_text("x = 1\n")  # the baseline alone never completes on Stop
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


def test_install_rewrites_a_stale_entrypoint_and_keeps_user_hooks(tmp_path):
    project = tmp_path / "p"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude/settings.json").write_text(json.dumps(
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}))
    hooks.install(project, "/old/place/harness.py")
    assert hooks.install(project, ENTRY) is True
    assert hooks.install(project, ENTRY) is False
    settings = json.loads((project / ".claude/settings.json").read_text())
    commands = [h["command"] for groups in settings["hooks"].values() for g in groups for h in g["hooks"]]
    assert "echo mine" in commands
    ours = [c for c in commands if hooks.MARK in c]
    assert len(ours) == 3 and all(f'"{ENTRY}"' in c and "/old/place" not in c for c in ours)


def test_installed_command_exits_zero_with_a_missing_entrypoint(tmp_path):
    project = tmp_path / "my project "
    (project / ".claude").mkdir(parents=True)
    hooks.install(project, str(tmp_path / "gone dir" / "harness.py"))
    settings = json.loads((project / ".claude/settings.json").read_text())
    for groups in settings["hooks"].values():
        for g in groups:
            for h in g["hooks"]:
                proc = subprocess.run(["sh", "-c", h["command"]], input="{}", capture_output=True,
                                      text=True, timeout=20,
                                      env=dict(os.environ, CLAUDE_PROJECT_DIR=str(project)))
                assert proc.returncode == 0, proc.stderr


def _begin(make_state_goal):
    state = make_state_goal()
    slug = state["slug"]
    steps.begin(slug, "req-1")
    return slug


def _age_lease(slug, owner, seconds):
    leases._path(slug).write_text(json.dumps(
        {"req-1": {"session": owner, "renewed_at": time.time() - seconds}}))


def test_post_tool_use_claims_an_unowned_lease(make_state_goal):
    slug = _begin(make_state_goal)
    assert leases.read(slug)["req-1"]["session"] is None
    hooks.run("PostToolUse", {"session_id": "s1"})
    assert leases.read(slug)["req-1"]["session"] == "s1"
    hooks.run("PostToolUse", {"session_id": "s2"})
    assert leases.read(slug)["req-1"]["session"] == "s1"


def test_other_session_start_leaves_a_recent_unowned_attempt_alone(make_state_goal):
    slug = _begin(make_state_goal)
    _age_lease(slug, None, 400)
    hooks.run("SessionStart", {"session_id": "s2"})
    assert goal.load(slug)["execution"]["active_attempt"] == "req-1"
    _age_lease(slug, None, 601)
    hooks.run("SessionStart", {"session_id": "s2"})
    assert goal.load(slug)["execution"]["active_attempt"] is None


def test_stop_does_not_complete_from_the_baseline_alone(state_client):
    project, runtime, run = state_client
    if runtime != "claude":
        return
    run("install-hooks")
    run("start", "Keep the build green", "--gate", "true")
    proc = _hook(project, "Stop", {"session_id": "s1"})
    assert proc.returncode == 0
    assert run("status", "keep-the-build-green")[1]["status"] == "active"
    code, closed = run("close", "keep-the-build-green")
    assert code == 0 and closed["complete"] is True
    assert closed["unchanged_since_baseline"] is True


def test_first_start_baseline_covers_the_tree_after_installing_hooks(state_client):
    project, runtime, run = state_client
    if runtime != "claude":
        return
    code, started = run("start", "Keep the build green", "--gate", "true")
    assert started["hooks"] == "installed"
    code, closed = run("close", started["slug"], "--if-ready")
    assert closed["complete"] is False
    assert not any("No passing gate result" in m for m in closed["missing"])
    assert any("baseline" in m for m in closed["missing"])


def test_start_respects_uninstall_hooks(state_client):
    project, runtime, run = state_client
    if runtime != "claude":
        return
    run("start", "Keep the build green", "--gate", "true")
    assert run("uninstall-hooks")[1] == {"changed": True}
    before = (project / ".claude/settings.json").read_text()
    code, started = run("start", "Another goal", "--gate", "true")
    assert code == 0 and "hooks" not in started
    assert (project / ".claude/settings.json").read_text() == before
    assert run("install-hooks")[1] == {"changed": True}
    run("uninstall-hooks")
    run("install-hooks")
    code, started = run("start", "Third goal", "--gate", "true")
    assert "hooks" not in started  # already installed, and the opt-out was cleared
    assert not (project / ".claude/harness/hooks-optout").exists()


def test_start_reports_an_unwritable_settings_file_and_keeps_the_goal(state_client):
    project, runtime, run = state_client
    if runtime != "claude":
        return
    (project / ".claude/settings.json").mkdir()
    code, started = run("start", "Keep the build green", "--gate", "true")
    assert code == 0 and started["hooks_error"]
    assert run("status")[1]["goals"][0]["slug"] == "keep-the-build-green"
    assert len(run("status")[1]["goals"]) == 1
