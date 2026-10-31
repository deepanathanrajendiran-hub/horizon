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
    cli.env = env

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


def test_session_start_shows_the_last_explicit_examples_run_and_never_runs_spec_code_itself(project):
    p, cli, start_message, *_ = project
    slug, gates, _ = _start_spec_goal(p, cli)
    assert "Spec examples" not in start_message()
    cli("examples", slug)
    assert "Spec examples: 1 agree, 1 disagree" in start_message()


# --- fix 1: review off the critical path ---------------------------------------------------------------

def test_a_goal_needs_at_most_one_review_round_then_close_completes_and_lists_what_went_unreviewed(project):
    p, cli, _, _, review_dir = project
    slug, gates, _ = _start_spec_goal(p, cli)
    (p / "pkg" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    assert code == 1 and out["review_brief"], out
    # the reviewer is dispatched and cut off by the session's call cap after writing only its header
    Path(out["review_file"]).write_text(f"Reviewed checkpoint: {out['observation']['tree_hash']}\n"
                                        "VERDICTS\nR1: UNVERIFIED\nEND VERDICTS\n")
    code, out = cli("close", slug)
    assert code == 0 and out["complete"], out
    assert "one review round" in out["unreviewed"].lower()


def test_a_fail_the_one_reviewer_found_must_be_fixed_before_close(project):
    p, cli, _, _, review_dir = project
    slug, gates, _ = _start_spec_goal(p, cli)
    (p / "pkg" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    digest = out["observation"]["tree_hash"]
    Path(out["review_file"]).write_text(f"Reviewed checkpoint: {digest}\nR1: FAIL - double(3) - got 5\n"
                                        "VERDICTS\nR1: FAIL\nEND VERDICTS\n")
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
    assert "Plateau: no source file changed in the last 2 sessions" in third
    # the gate (`true`) passes: a finished library is sent to close, not into more edits
    assert "if every part of the request is built, run `close` now" in third
    assert "Run the work the way a user would" in third


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


# --- the skill carries the rules -----------------------------------------------------------------------

HELDOUT_RULES = (
    "A goal gets one review round",
    "never in a session's last calls",
    "/spec_examples.py`: the spec's own runnable",
    "`waive-example SLUG ID --reason",
    "Where the request is silent or only illustrative, follow the conventions of the project and its language",
    "carries a `# judgment`",
    "tag `[review]` only a line a reviewer can judge by running the work",
)


def held_out_rules_missing(text: str) -> list[str]:
    flat = " ".join(text.split())
    return [rule for rule in HELDOUT_RULES if " ".join(rule.split()) not in flat]


@pytest.mark.parametrize("folder", [".claude", ".agents"])
def test_the_skill_carries_the_held_out_rules(folder):
    skill = ROOT / folder / "skills/horizon"
    full = (skill / "SKILL.md").read_text() + "\n" + (skill / "references/guide.md").read_text()
    assert held_out_rules_missing(full) == []
    if folder == ".claude":
        assert "When the session-start message says `Plateau`" in " ".join(full.split())
    else:
        assert "When two sessions in a row changed no source file" in " ".join(full.split())


# --- review findings on the branch (fresh reviewer, 2026-10-31) ---------------------------------------

def _round_reply(digest, *lines, end=True):
    body = f"Reviewed checkpoint: {digest}\n" + "".join(line + "\n" for line in lines)
    return body + ("VERDICTS\nR1: ?\nEND VERDICTS\n" if end else "")


def _brief(p, cli):
    slug, gates, _ = _start_spec_goal(p, cli)
    (p / "pkg" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    assert out.get("review_file"), out
    return slug, out["observation"]["tree_hash"], Path(out["review_file"])


def test_a_recorded_fail_on_the_current_files_blocks_close(project):
    p, cli, *_ = project
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(_round_reply(digest, "Verdict: FAIL", "The output is wrong."))
    cli("review", slug, "--result", "fail", "--judge", "reviewer-1", "--hash", digest,
        "--evidence-file", str(review_file))
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"], out


def test_a_fail_headline_in_the_round_file_blocks_close_without_rk_lines(project):
    p, cli, *_ = project
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(_round_reply(digest, "Verdict: FAIL", "Broken."))
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"], out


def test_an_empty_file_with_a_round_name_does_not_spend_the_review(project):
    p, cli, _, _, review_dir = project
    slug, digest, review_file = _brief(p, cli)
    (review_dir / f"horizon-review-{slug}-000000000000-1.md").write_text("")
    code, out = cli("close", slug)
    assert code == 1 and out.get("review_brief"), out


def test_a_reviewer_still_writing_does_not_spend_the_round(project):
    p, cli, *_ = project
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(_round_reply(digest, "R1: PASS - ran it - ok", end=False))
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"], out


def test_a_cut_off_round_counts_once_it_has_gone_quiet():
    import reviewing
    assert not reviewing.round_finished("Reviewed checkpoint: abc\nR1: PASS\n", age_s=60)
    assert reviewing.round_finished("Reviewed checkpoint: abc\nR1: PASS\n", age_s=reviewing.ROUND_QUIET_S + 1)
    assert reviewing.round_finished("Reviewed checkpoint: abc\nVERDICTS\nR1: PASS\nEND VERDICTS\n", age_s=1)
    assert not reviewing.round_finished("", age_s=10 ** 6)


def test_the_stop_hook_never_completes_a_goal_without_its_review(project):
    p, cli, _, hook, _ = project
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(_round_reply(digest, "R1: UNVERIFIED - could not run"))
    hook("Stop", {"session_id": "s"})
    code, out = cli("status", slug)
    assert out["status"] == "active", out
    code, out = cli("close", slug)
    assert out["complete"], out


def test_unreviewed_keeps_the_fail_lines_of_an_earlier_checkpoint(project):
    p, cli, *_ = project
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(_round_reply(digest, "R1: FAIL - double(3) - got 5"))
    (p / "pkg" / "extra.py").write_text("X = 2\n")
    code, out = cli("close", slug)
    assert out["complete"], out
    assert "R1: FAIL - double(3) - got 5" in out["unreviewed"]


def test_more_review_rounds_can_be_allowed(project):
    p, cli, *_ = project
    cli.env["HORIZON_REVIEW_ROUNDS_PER_GOAL"] = "2"
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(_round_reply(digest, "R1: UNVERIFIED - could not run"))
    code, out = cli("close", slug)
    assert code == 1 and out.get("review_brief"), out


def test_running_the_examples_leaves_the_checkpoint_unchanged(project):
    p, cli, *_ = project
    slug, gates, out = _start_spec_goal(p, cli)
    before = out["observation"]["tree_hash"]
    cli("examples", slug)
    code, out = cli("observe", slug)
    assert out["observation"]["tree_hash"] == before


def test_the_plateau_says_build_when_the_gate_fails(project):
    p, cli, start_message, *_ = project
    cli("start", "Build pkg", "--gate", "false")
    start_message()
    start_message()
    third = start_message()
    assert "Plateau" in third and "run `close` now" not in third


def test_a_fact_silences_a_placeholder_only_when_it_says_by_design_about_that_exact_file(project):
    p, cli, start_message, *_ = project
    (p / "pkg" / "a.py").write_text("def f():\n    raise NotImplementedError\n")
    cli("start", "Build pkg", "--gate", "true")
    cli("note", "build-pkg", "--fact", "pkg/a.py f() is still a stub, build it next")
    assert "pkg/a.py (f)" in start_message()


def test_classes_that_only_start_with_base_are_still_checked(project):
    p, cli, start_message, *_ = project
    (p / "pkg" / "codec.py").write_text("class Base64Codec:\n    def encode(self):\n        raise NotImplementedError\n")
    cli("start", "Build pkg", "--gate", "true")
    assert "pkg/codec.py (Base64Codec.encode)" in start_message()


def test_notes_written_without_a_blank_line_after_the_header_are_kept(project):
    p, cli, start_message, *_ = project
    cli("start", "Build pkg", "--gate", "true")
    import glob
    path = Path(glob.glob(str(p / ".claude" / "**" / "notes" / "build-pkg.md"), recursive=True)[0])
    path.write_text(path.read_text().rstrip("\n") + "\n## Map\nmodule X <- lines 1-9\n")
    assert "module X <- lines 1-9" in start_message()


# --- the slim skill (fix check, 2026-10-31) -------------------------------------------------------------
# The full skill was 525 lines (about 8k tokens) loaded every session; on the fix check Haiku applied its rules
# mechanically and missed the ones that mattered. SKILL.md keeps what every session needs; the rest is the guide.

SLIM_RULES = (
    "[the full guide](references/guide.md)",
    "## Starting a goal",
    "**Run what you wrote, in the same session.**",
    "**Build before you verify.**",
    "**Grow the gate with the work.**",
    "Never assert your own guess in the gate",
    "Take expected values from the request and its inputs, never from your own code",
    "fix the code, not the caller",
    "A goal gets one review round",
    "never in a session's last calls",
    "Never approve a changed test on your own judgment",
    "no one will answer a question",
    "never spend a session only reading",
)


def slim_missing(text: str) -> list[str]:
    flat = " ".join(text.split())
    return [rule for rule in SLIM_RULES if " ".join(rule.split()) not in flat]


@pytest.mark.parametrize("folder", [".claude", ".agents"])
def test_the_slim_skill_holds_the_core_rules_and_stays_small(folder):
    text = (ROOT / folder / "skills/horizon/SKILL.md").read_text()
    assert slim_missing(text) == []
    assert len(text.splitlines()) <= 160 and len(text.split()) <= 1600

# --- the skill is task-neutral (2026-10-31) -------------------------------------------------------------
# The rules tuned on NL2Repo (recall the real library over the spec, package first, docs last, the spec-build
# setup) fit a benchmark that rebuilds existing libraries and grades them with their own tests. For a real user
# they would override the user's own spec, so they are gone; the spec tools remain as optional CLI features.

FITTED = ("real library", "Real library", "real project", "spec's silence", "Docs, translations",
          "Breadth first", "package `__init__.py`", "namespace package", "pkg-build", "SPEC.md lines",
          "Make the package exist first", "Starting a build from a spec", "### Building from a spec",
          "library source", "unbuilt part of the library", "fix the library")


@pytest.mark.parametrize("folder", [".claude", ".agents"])
def test_the_skill_is_task_neutral(folder):
    skill = ROOT / folder / "skills/horizon"
    full = (skill / "SKILL.md").read_text() + "\n" + (skill / "references/guide.md").read_text()
    flat = " ".join(full.split())
    assert [phrase for phrase in FITTED if phrase in flat] == []
    core = " ".join((skill / "SKILL.md").read_text().split())
    for phrase in ("## Starting a goal", "--gate-path", "[unverifiable]", "# Still passes:",
                   "Take expected values from the request", "baseline `observation`"):
        assert phrase in core, phrase


def test_the_plateau_message_is_task_neutral():
    import hooks
    assert [phrase for phrase in FITTED if phrase in hooks.PLATEAU] == []
    assert "spec" not in hooks.PLATEAU.lower()

def test_a_non_python_source_change_clears_the_plateau(project):
    # Task-neutral: a goal on a TypeScript app or a config change makes progress without touching a .py file.
    p, cli, start_message, *_ = project
    cli("start", "Build the app", "--gate", "true")
    start_message()
    start_message()
    (p / "src").mkdir()
    (p / "src" / "app.ts").write_text("export const run = () => 1;\n")
    third = start_message()
    assert "Plateau" not in third
