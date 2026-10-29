"""Fixes from the NL2Repo bench2 transcripts (fun repo: docs/evaluations/nl2repo-bench2/transcript-findings.md)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

import goal  # noqa: E402
import reviewing  # noqa: E402
import tree_hash  # noqa: E402

ENTRY = str(ROOT / ".claude/skills/horizon/scripts/harness.py")


def _git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                   env=env, capture_output=True)


def test_a_commit_that_changes_no_file_keeps_the_checkpoint(tmp_path):
    # unidecode s8 -> s9: the auto-commit made a new checkpoint and the carried review line was lost.
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "one")
    (repo / "a.py").write_text("x = 2\n")  # the session's edit, not yet committed
    (repo / "b.py").write_text("y = 1\n")
    before = tree_hash.compute(str(repo))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "session 1 (auto-commit)")
    assert tree_hash.compute(str(repo)) == before
    _git(repo, "commit", "-q", "--allow-empty", "-m", "empty")
    assert tree_hash.compute(str(repo)) == before
    (repo / "a.py").write_text("x = 3\n")
    assert tree_hash.compute(str(repo)) != before


def test_the_notes_file_is_bookkeeping(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".claude" / "harness" / "notes").mkdir(parents=True)
    (repo / "a.py").write_text("x = 1\n")
    before = tree_hash.compute(str(repo))
    (repo / ".claude" / "harness" / "notes" / "goal.md").write_text("## SPEC lines 1-90: core API\n")
    assert tree_hash.compute(str(repo)) == before


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj "
    (p / ".claude").mkdir(parents=True)

    def cli(*args):
        done = subprocess.run([sys.executable, ENTRY, "--runtime", "claude", "--project", str(p), *args],
                              cwd=p, capture_output=True, text=True, timeout=60)
        return done.returncode, (json.loads(done.stdout) if done.stdout.strip().startswith("{") else done.stderr)
    return p, cli


def test_start_takes_a_timeout_and_makes_a_notes_file(project):
    # pyjwt s1 c14, s2 c7: `start --timeout 600` was rejected, costing session 1.
    p, cli = project
    code, started = cli("start", "Build the library", "--gate", "true", "--timeout", "30")
    assert code == 0, started
    notes = Path(started["notes_file"])
    assert notes.is_file() and notes.parent.name == "notes"
    notes.write_text(notes.read_text() + "## SPEC 1-120: package layout -> done\n## SPEC 121-400: processors\n")
    _, status = cli("status", started["slug"])
    assert status["notes_sections"] == ["SPEC 1-120: package layout -> done", "SPEC 121-400: processors"]


def test_status_of_one_goal_stays_small(project):
    # DBUtils s3-s9, unidecode s3: `status SLUG` returned 43-45 KB and spilled to a file.
    p, cli = project
    (p / "big.sh").write_text("python3 -c \"print('x' * 9000)\"\nexit 0\n")
    _, started = cli("start", "Keep it green", "--gate", "sh big.sh", "--review", "Every line holds")
    slug = started["slug"]
    _, checked = cli("check", slug, "--did", "d", "--next", "n")
    digest = checked["last_checkpoint"]["tree_hash"]
    evidence = p.parent / "reply.md"
    evidence.write_text("FAIL: " + "detail " * 3000)
    cli("review", slug, "--result", "fail", "--judge", "r", "--hash", digest, "--evidence-file", str(evidence))
    done = subprocess.run([sys.executable, ENTRY, "--runtime", "claude", "--project", str(p), "status", slug],
                          cwd=p, capture_output=True, text=True, timeout=60)
    assert len(done.stdout) < 6000, len(done.stdout)
    status = json.loads(done.stdout)
    assert status["reviews"][-1]["result"] == "fail" and "evidence" not in status["reviews"][-1]


def test_review_scope_starts_with_the_lines_that_need_a_reviewer():
    # unidecode s6-s10: reviewers spent their few calls on [gate] lines R1-R4.
    requirements = reviewing.requirement_lines(
        'R1 [gate] "a"\nR2 [gate] "b"\nR3 [gate] "c"\nR4 [gate] "d"\nR5 [review] "e"\nR6 [judgment] "f"\n')
    assert reviewing.review_scope(requirements, [], 2) == ["R5", "R6"]
    assert reviewing.review_scope(requirements, ["R5", "R6"], 2) == ["R1", "R2"]


def _session_start(p, session="s2"):
    done = subprocess.run([sys.executable, ENTRY, "--runtime", "claude", "--project", str(p), "hook", "SessionStart"],
                          input=json.dumps({"session_id": session}), capture_output=True, text=True, timeout=120)
    return json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]


def test_session_start_runs_the_gate_on_changed_files_and_hands_over_everything(project):
    # All five bench2 tasks: sessions ended before `check`, so the hook showed the session-1 gate result for
    # ten sessions, and each session spent 3-6 of its 15 calls on doctor, status and re-reading.
    p, cli = project
    _git(p, "init", "-q")
    (p / "gate.sh").write_text('test -f pkg/__init__.py || { echo "MISSING pkg/__init__.py"; exit 1; }\necho ALL-OK\n')
    _, started = cli("start", "Build the package", "--gate", "sh gate.sh")
    slug = started["slug"]
    cli("note", slug, "--next", "write pkg/testing.py from SPEC lines 1599-1741")
    (p / "pkg").mkdir()
    (p / "pkg" / "__init__.py").write_text("")
    _git(p, "add", "-A")
    _git(p, "commit", "-q", "-m", "session 1 (auto-commit)")
    context = _session_start(p)
    assert f"HARNESS={ENTRY}" in context and f"PROJECT={p}" in context
    assert "Next: write pkg/testing.py from SPEC lines 1599-1741" in context
    assert "Gate now: pass" in context and "ALL-OK" in context
    assert "notes/" in context and "pkg/__init__.py" in context  # notes file, and the last commit's files
    before = len(goal_observations(p, slug))
    _session_start(p, "s3")  # nothing changed since: the gate is not run again
    assert len(goal_observations(p, slug)) == before


def goal_observations(p, slug):
    return json.loads((p / ".claude" / "harness" / "goals" / f"{slug}.json").read_text()).get("observations", [])


def test_the_session_start_hook_gets_time_to_run_the_gate(tmp_path):
    import hooks
    project = tmp_path / "p"
    (project / ".claude").mkdir(parents=True)
    hooks.install(project, ENTRY)
    settings = json.loads((project / ".claude" / "settings.json").read_text())
    start = [h for g in settings["hooks"]["SessionStart"] for h in g["hooks"] if hooks.MARK in h["command"]]
    assert start[0]["timeout"] >= 300
    assert hooks.install(project, ENTRY) is False
