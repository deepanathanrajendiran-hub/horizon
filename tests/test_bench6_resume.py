"""NL2Repo bench6 transcript review (fun repo: docs/evaluations/nl2repo-bench6/transcript-findings.md).

- A stale next action in 14 of 16 Horizon builds: `check`/`note --next` ran at the end of an increment and the
  call cap came first, so the next session was told to write a file it had already written (2-9 calls each).
- Bugs found just before the cap were lost (bleach's CSS bug, Rich-Click's `_title_for`, Verifiers' contextlib).
- `$HARNESS`/`$PROJECT` do not persist between shell calls: 2-4 failed harness calls per build.
- A green names-only gate hid placeholder bodies (deepdiff: "192 passed" with 5 placeholder modules).
- The same environment problem was re-diagnosed every session (tsfresh's scipy: ~25 calls)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = str(ROOT / ".claude/skills/horizon/scripts/harness.py")


def _git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                   env=env, capture_output=True)


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj "
    (p / ".claude").mkdir(parents=True)
    (p / "pkg").mkdir()
    (p / "pkg" / "__init__.py").write_text("")
    _git(p, "init", "-q")
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "init")

    def cli(*args):
        done = subprocess.run([sys.executable, ENTRY, "--runtime", "claude", "--project", str(p), *args],
                              cwd=p, capture_output=True, text=True, timeout=60)
        return done.returncode, (json.loads(done.stdout) if done.stdout.strip().startswith("{") else done.stderr)

    def start_message():
        done = subprocess.run([sys.executable, ENTRY, "--runtime", "claude", "--project", str(p), "hook",
                               "SessionStart"], input=json.dumps({"session_id": "s2", "source": "startup"}),
                              cwd=p, capture_output=True, text=True, timeout=120)
        return json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]

    return p, cli, start_message


def test_resume_lists_files_changed_since_the_next_action_and_flags_it_when_its_file_changed(project):
    p, cli, start_message = project
    cli("start", "Build pkg", "--gate", "true")
    cli("note", "build-pkg", "--next", "Write pkg/core.py from SPEC.md lines 10-80")
    # the session writes the file and is cut off before `check`; the runner commits
    (p / "pkg" / "core.py").write_text("def run():\n    pass\n")
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "session 1 (auto-commit)")
    message = start_message()
    assert "Changed since the next action was recorded: pkg/core.py" in message
    assert "The next action may already be done: pkg/core.py changed after it was recorded" in message


def test_resume_says_nothing_about_changes_when_nothing_changed(project):
    p, cli, start_message = project
    cli("start", "Build pkg", "--gate", "true")
    cli("note", "build-pkg", "--next", "Write pkg/core.py")
    message = start_message()
    assert "Changed since the next action" not in message and "may already be done" not in message


def test_findings_and_facts_are_kept_and_shown_until_cleared(project):
    p, cli, start_message = project
    cli("start", "Build pkg", "--gate", "true")
    code, _ = cli("note", "build-pkg", "--finding", "RichPanel.render calls undefined _title_for: --help crashes")
    assert code == 0
    cli("note", "build-pkg", "--fact", "scipy.stats fails to import here (dlopen); not a code bug")
    message = start_message()
    assert "Open findings: 1) RichPanel.render calls undefined _title_for: --help crashes." in message
    assert "Known facts: scipy.stats fails to import here (dlopen); not a code bug." in message
    cli("note", "build-pkg", "--clear-findings")
    message = start_message()
    assert "Open findings" not in message and "Known facts: scipy.stats" in message


def test_note_needs_something_to_record(project):
    p, cli, _ = project
    cli("start", "Build pkg", "--gate", "true")
    code, _ = cli("note", "build-pkg")
    assert code != 0


def test_resume_gives_the_literal_harness_command(project):
    p, cli, start_message = project
    cli("start", "Build pkg", "--gate", "true")
    message = start_message()
    root = os.path.realpath(p)
    assert f'Run Horizon as: "{sys.executable}" "{ENTRY}" --runtime claude --project "{root}" <command>' in message
    assert "shell variables do not carry over between commands" in message


def test_resume_lists_placeholder_bodies_as_unbuilt(project):
    p, cli, start_message = project
    (p / "pkg" / "search.py").write_text(
        '"""Placeholder: behaviour is added by the implementation step."""\n'
        "class DeepSearch:\n    def __init__(self, obj):\n        raise NotImplementedError\n")
    (p / "pkg" / "base.py").write_text(
        "import abc\nclass Base(abc.ABC):\n    @abc.abstractmethod\n    def run(self):\n"
        "        raise NotImplementedError\n")
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "stubs")
    cli("start", "Build pkg", "--gate", "true")
    listed = start_message().split("Placeholders still to build: ", 1)[1].split("\n")[0]
    assert listed.startswith("pkg/search.py (DeepSearch.__init__, module marked placeholder)")
    assert "pkg/base.py" not in listed  # an abstract method is a real interface, not a placeholder
