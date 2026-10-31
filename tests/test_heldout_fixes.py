"""NL2Repo held-out loss review (fun docs/evaluations/nl2repo-heldout/transcripts/review.md).

- In 9 of 12 losses the library stopped changing by session 3 and the rest went to close/review rounds that a
  15-call session never finished: the gate dir made the gate "not strong", so every close needed a reviewer;
  the reason named tox.ini/conftest.py as if they were gate files; schedule ended "waiting on your ruling".
- Nothing told the agent it had stopped building: "Next: run close" repeated for 3-7 sessions.
- Abstract-method stubs were re-flagged as placeholders every session (DictDataBase, python-jose).
- Packaging files were written before the package's own __init__.py (flask-restful, tablib)."""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = str(ROOT / ".claude/skills/horizon/scripts/harness.py")
sys.path.insert(0, str(ROOT / "harness"))


def _git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                   env=env, capture_output=True)


SPEC = """# pkg

```python
from pkg import double
print(double(2))  # Output: 4
```

```python
from pkg import double
print(double(3))  # Output: 7
```
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    p = tmp_path / "proj "
    (p / ".claude").mkdir(parents=True)
    (p / "pkg").mkdir()
    (p / "pkg" / "__init__.py").write_text("def double(x):\n    return 2 * x\n")
    (p / "SPEC.md").write_text(SPEC)
    _git(p, "init", "-q")
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "init")
    review_dir = tmp_path / "reviews"
    review_dir.mkdir()
    env = {**os.environ, "TMPDIR": str(review_dir)}

    def cli(*args):
        done = subprocess.run([sys.executable, ENTRY, "--runtime", "claude", "--project", str(p), *args],
                              cwd=p, capture_output=True, text=True, timeout=120, env=env)
        return done.returncode, (json.loads(done.stdout) if done.stdout.strip().startswith("{") else done.stderr)

    def hook(event, payload):
        done = subprocess.run([sys.executable, ENTRY, "--runtime", "claude", "--project", str(p), "hook", event],
                              input=json.dumps(payload), cwd=p, capture_output=True, text=True, timeout=120,
                              env=env)
        return json.loads(done.stdout) if done.stdout.strip() else None

    def start_message():
        out = hook("SessionStart", {"session_id": "s", "source": "startup"})
        return out["hookSpecificOutput"]["additionalContext"]

    return p, cli, start_message, hook, review_dir


def _start_spec_goal(p, cli, review=True):
    gates = p / ".claude" / "gates" / "g"
    gates.mkdir(parents=True)
    (gates / "request.md").write_text("Build pkg from SPEC.md\n")
    (gates / "requirements.md").write_text('R1 [gate] "double doubles"\n')
    (gates / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    args = ["start", "Build pkg", "--gate", f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider "
            f"{shlex.quote(str(gates))}",
            "--gate-path", str(gates), "--surface-spec", str(p / "SPEC.md"), "--package", "pkg"]
    if review:
        args += ["--review", "Every line of requirements.md holds"]
    code, out = cli(*args)
    assert code == 0, out
    return out["slug"], gates, out


# --- fix 2: the spec's examples, advisory ------------------------------------------------------------

def test_start_writes_the_advisory_examples_beside_the_surface_gate(project):
    p, cli, *_ = project
    slug, gates, out = _start_spec_goal(p, cli)
    assert (gates / "spec_examples.py").is_file() and (gates / "test_surface.py").is_file()
    assert out["spec_examples"] == {"examples": 2, "signatures": 0}
    # advisory: the baseline gate passes although one example disagrees
    assert out["observation"]["gate"] == "pass"


def test_examples_command_reports_each_disagreement(project):
    p, cli, *_ = project
    slug, gates, _ = _start_spec_goal(p, cli)
    code, out = cli("examples", slug)
    assert code == 0, out
    assert out["summary"].startswith("Spec examples: 1 agree, 1 disagree")
    assert "printed '6'" in out["summary"]


def test_waive_example_records_a_cited_reason_and_refuses_an_uncited_one(project):
    p, cli, *_ = project
    slug, gates, _ = _start_spec_goal(p, cli)
    code, out = cli("examples", slug)
    wrong = out["result"]["failed"][0]["id"]
    code, err = cli("waive-example", slug, wrong, "--reason", "wrong")
    assert code == 2 and "spec line" in err.lower()
    code, out = cli("waive-example", slug, wrong, "--reason", "SPEC line 9 contradicts the prose on line 2")
    assert code == 0, out
    code, out = cli("examples", slug)
    assert out["summary"].startswith("Spec examples: 1 agree, 0 disagree")


def test_session_start_shows_the_spec_examples(project):
    p, cli, start_message, *_ = project
    slug, gates, _ = _start_spec_goal(p, cli)
    assert "Spec examples: 1 agree, 1 disagree" in start_message()


# --- fix 1: review off the critical path ---------------------------------------------------------------

def test_a_goal_needs_at_most_one_review_round_then_close_completes_and_lists_what_went_unreviewed(project):
    p, cli, _, _, review_dir = project
    slug, gates, _ = _start_spec_goal(p, cli)
    (p / "pkg" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    assert code == 1 and out["review_brief"], out
    # the reviewer is dispatched and cut off by the session's call cap after writing only its header
    Path(out["review_file"]).write_text(f"Reviewed checkpoint: {out['observation']['tree_hash']}\n")
    code, out = cli("close", slug)
    assert code == 0 and out["complete"], out
    assert "one review round" in out["unreviewed"].lower()


def test_a_fail_the_one_reviewer_found_must_be_fixed_before_close(project):
    p, cli, _, _, review_dir = project
    slug, gates, _ = _start_spec_goal(p, cli)
    (p / "pkg" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    digest = out["observation"]["tree_hash"]
    Path(out["review_file"]).write_text(f"Reviewed checkpoint: {digest}\nR1: FAIL - double(3) - got 5\n")
    code, out = cli("close", slug)
    assert code == 1 and "R1: FAIL" in " ".join(out["missing"]), out
    # the fix changes the files: the goal closes without a second review
    (p / "pkg" / "extra.py").write_text("X = 2\n")
    code, out = cli("close", slug)
    assert code == 0 and out["complete"], out


def test_the_not_strong_reason_names_the_agents_gate_not_project_config(project):
    p, cli, *_ = project
    slug, gates, _ = _start_spec_goal(p, cli)
    (p / "tox.ini").write_text("[tox]\n")
    (p / "pkg" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    text = " ".join(out["missing"])
    assert "tox.ini" not in text
    assert "written during this goal" in text


def test_the_review_brief_gives_the_reviewer_a_call_budget(project):
    p, cli, *_ = project
    slug, gates, _ = _start_spec_goal(p, cli)
    (p / "pkg" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    assert "six tool calls" in out["review_brief"]


def test_the_stop_hook_says_no_one_will_answer_in_an_unattended_run(project):
    p, cli, _, hook, _ = project
    cli("start", "Build pkg", "--gate", "true")
    (p / "pkg" / "extra.py").write_text("X = 1\n")
    out = hook("Stop", {"session_id": "s"})
    assert out and out["decision"] == "block"
    assert "no one will answer" in out["reason"].lower()


# --- fix 3: plateau --------------------------------------------------------------------------------

def test_two_sessions_without_a_library_change_raise_the_plateau_alarm(project):
    p, cli, start_message, *_ = project
    cli("start", "Build pkg", "--gate", "true")
    first = start_message()
    second = start_message()
    assert "Plateau" not in first and "Plateau" not in second
    third = start_message()
    assert "Plateau: no library source changed in the last 2 sessions" in third
    assert "do not run close" in third


def test_a_library_change_clears_the_plateau(project):
    p, cli, start_message, *_ = project
    cli("start", "Build pkg", "--gate", "true")
    start_message()
    start_message()
    (p / "pkg" / "core.py").write_text("def run():\n    return 1\n")
    assert "Plateau" not in start_message()


def test_gate_and_test_edits_do_not_count_as_library_changes(project):
    p, cli, start_message, *_ = project
    cli("start", "Build pkg", "--gate", "true")
    start_message()
    (p / "tests").mkdir()
    (p / "tests" / "test_more.py").write_text("def test_x():\n    pass\n")
    start_message()
    (p / "tests" / "test_more.py").write_text("def test_y():\n    pass\n")
    assert "Plateau" in start_message()


# --- fix 6: placeholder noise, notes inline -----------------------------------------------------------

def test_abstract_base_classes_are_not_placeholders(project):
    p, cli, start_message, *_ = project
    (p / "pkg" / "locks.py").write_text(
        "class AbstractLock:\n    def _lock(self):\n        raise NotImplementedError\n\n"
        "class BaseSession:\n    def load(self):\n        raise NotImplementedError\n\n"
        "class Real:\n    def run(self):\n        raise NotImplementedError\n")
    cli("start", "Build pkg", "--gate", "true")
    message = start_message()
    assert "pkg/locks.py (Real.run)" in message
    assert "AbstractLock" not in message and "BaseSession" not in message


def test_a_fact_that_names_a_file_silences_its_placeholder_warning(project):
    p, cli, start_message, *_ = project
    (p / "pkg" / "hooks.py").write_text("def hook():\n    raise NotImplementedError\n")
    cli("start", "Build pkg", "--gate", "true")
    assert "pkg/hooks.py" in start_message().split("Placeholders")[-1]
    cli("note", "build-pkg", "--fact", "pkg/hooks.py hook() raises NotImplementedError by design (spec line 40)")
    assert "Placeholders still to build" not in start_message()


def test_session_start_carries_the_notes_text(project):
    p, cli, start_message, *_ = project
    code, out = cli("start", "Build pkg", "--gate", "true")
    notes = Path(out["notes"]) if "notes" in out else None
    import glob
    path = notes or Path(glob.glob(str(p / ".claude" / "**" / "notes" / "build-pkg.md"), recursive=True)[0])
    path.write_text(path.read_text() + "\n## Spec map\npkg/core.py <- SPEC lines 10-80\n")
    assert "pkg/core.py <- SPEC lines 10-80" in start_message()


# --- fix 5: the package before its packaging -----------------------------------------------------------

def test_the_digest_lists_code_failures_before_packaging_and_docs_files():
    import hooks
    output = ("FAILED gates/test_surface.py::test_spec_file_exists[README.rst] - assert False\n"
              "FAILED gates/test_surface.py::test_spec_file_exists[LICENSE] - assert False\n"
              "FAILED gates/test_behaviour.py::test_detect_json - AssertionError\n"
              "========= short test summary info =========\n"
              "FAILED gates/test_surface.py::test_spec_file_exists[README.rst] - assert False\n"
              "FAILED gates/test_surface.py::test_spec_file_exists[LICENSE] - assert False\n"
              "FAILED gates/test_behaviour.py::test_detect_json - AssertionError\n"
              "3 failed in 0.1s\n")
    digest = hooks._gate_digest(output)
    assert digest.index("test_detect_json") < digest.index("README.rst")
    assert "packaging and docs files last" in digest
