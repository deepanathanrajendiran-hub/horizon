"""NL2Repo held-out rerun, transcript review (fun docs/evaluations/nl2repo-heldout2/transcripts.md).

- The review round was lost or looped in about 14 of 24 builds (about 70 of 193 sessions): the run's call cap
  counts the reviewer's calls, so a foreground reviewer was cut off and a background one died with the session,
  leaving a reply file with only its header. `close` handed out the brief again every session.
- A reply whose only open lines were UNVERIFIED (a tool the sandbox lacked) was recorded as a fail and blocked
  `close` on the same files.
- `close`'s section list drove low-value or harmful work in about 11 builds: working re-exports replaced,
  modules the request's own layout does not list, requirement lines written only to quote headings.
- About 10 builds stopped to ask a person in a run with no person; about 7 wrote the entry point last and
  scored 0 for early sessions; the interpreter path was retyped wrong in 9+ builds."""
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


FILLER = "".join(f"Background sentence {i}.\n" for i in range(220))
LONG_SPEC = ("# Tool\n\n" + FILLER + "\n## Parsing\n\nparse() reads a line.\n\n## Usage Guide\n\n"
             "### Project Layout\n\nsrc/tool.py and src/extra.py.\n\n### Output Format\n\nOne line per record.\n")


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj "
    (p / ".claude").mkdir(parents=True)
    (p / "src").mkdir()
    (p / "src" / "tool.py").write_text("def parse(line):\n    return line\n")
    (p / "DESIGN.md").write_text(LONG_SPEC)
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

    def new_session(session):
        subprocess.run([sys.executable, ENTRY, "--runtime", "claude", "--project", str(p), "hook", "SessionStart"],
                       input=json.dumps({"session_id": session, "source": "startup"}), cwd=p, capture_output=True,
                       text=True, timeout=120, env=env)
    cli.new_session = new_session
    return p, cli


def _start(p, cli, requirements='R1 [gate] "Parsing, Project Layout, Output Format: parse() works"\n',
           review=True):
    gates = p / ".claude" / "gates" / "g"
    gates.mkdir(parents=True, exist_ok=True)
    (gates / "request.md").write_text("Build the tool described in DESIGN.md\n")
    (gates / "requirements.md").write_text(requirements)
    # E2E-SWE paper run: close needs a check that names every [gate] line.
    named = " ".join(w for w in requirements.split() if w[:1] == "R" and w[1:2].isdigit())
    (gates / "test_ok.py").write_text(f"def test_ok():  # checks {named}\n    assert True\n")
    args = ["start", "Build the tool", "--gate-path", str(gates), "--gate",
            f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider {shlex.quote(str(gates))}"]
    if review:
        args += ["--review", "Every line of requirements.md holds"]
    code, out = cli(*args)
    assert code == 0, out
    return out["slug"], gates


def _brief(p, cli):
    cli.new_session("s1")
    slug, gates = _start(p, cli)
    (p / "src" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    assert code == 1 and out.get("review_brief"), out
    return slug, out["observation"]["tree_hash"], Path(out["review_file"])


# --- fix 1: the review round survives a cut-off reviewer ----------------------------------------------

def test_a_reviewer_cut_off_after_its_header_gets_its_round_handed_out_again(project):
    # E2E-SWE paper run: counting this round as spent closed 79 goals on the agent's own gate, so the round is now
    # handed out once more (tests/test_e2eswe_fixes.py).
    p, cli = project
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(f"Reviewed checkpoint: {digest}\n")
    cli.new_session("s2")  # the capped session ended with its reviewer
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"], out
    assert "unreviewed" not in out and out.get("review_brief"), out


def test_a_second_close_in_the_same_session_waits_for_the_reviewer(project):
    # review of fe5740f: the reviewer writes its header first and its verdicts only after testing; a second close
    # in the same session must not complete under it.
    p, cli = project
    slug, digest, review_file = _brief(p, cli)
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"] and out.get("review_pending"), out
    review_file.write_text(f"Reviewed checkpoint: {digest}\n")
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"] and out.get("review_pending"), out
    assert not out.get("review_brief"), out


def test_a_review_handed_out_and_never_answered_goes_out_once_more_and_then_no_more(project):
    # E2E-SWE paper run: no longer completes the goal; the hand-outs are bounded instead, so the review cannot
    # loop as it did on heldout2.
    p, cli = project
    slug, digest, review_file = _brief(p, cli)
    cli.new_session("s2")
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"] and out.get("review_brief"), out
    cli.new_session("s3")
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"], out
    assert out.get("review_unavailable") and not out.get("review_brief"), out


def test_a_brief_from_check_does_not_spend_the_review(project):
    p, cli = project
    slug, gates = _start(p, cli)
    (p / "src" / "extra.py").write_text("X = 1\n")
    code, out = cli("check", slug, "--did", "Added extra.py and ran the gate", "--next", "Run close")
    assert code == 0, out
    code, out = cli("close", slug)
    assert code == 1 and out.get("review_brief"), out


def test_a_reviewer_still_writing_verdicts_holds_the_goal(project):
    p, cli = project
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(f"Reviewed checkpoint: {digest}\nR1: PASS - ran parse - ok\n")
    cli.new_session("s2")
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"] and out.get("review_pending"), out
    code, out = cli("check", slug, "--did", "Ran the gate again", "--next", "Run close")
    assert out.get("review_pending") and not out.get("review_brief"), out


def test_more_rounds_still_need_each_round_to_finish(project):
    p, cli = project
    cli.env["HORIZON_REVIEW_ROUNDS_PER_GOAL"] = "2"
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(f"Reviewed checkpoint: {digest}\n")
    cli.new_session("s2")
    code, out = cli("close", slug)
    assert code == 1 and out.get("review_brief"), out


def test_with_more_rounds_a_reviewer_that_never_writes_does_not_loop(project):
    # review of fe5740f: the brief kept the same file name while it did not exist, so the round count never grew.
    p, cli = project
    cli.env["HORIZON_REVIEW_ROUNDS_PER_GOAL"] = "2"
    slug, digest, review_file = _brief(p, cli)
    cli.new_session("s2")
    code, out = cli("close", slug)
    assert code == 1 and out.get("review_brief"), out
    cli.new_session("s3")
    code, out = cli("close", slug)
    # E2E-SWE paper run: two rounds allow three hand-outs, then close sends no more and does not complete.
    assert code == 1 and out.get("review_brief"), out
    cli.new_session("s4")
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"], out
    assert out.get("review_unavailable") and not out.get("review_brief"), out


def test_an_unverified_only_reply_recorded_as_fail_does_not_block_close(project):
    p, cli = project
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(f"Reviewed checkpoint: {digest}\nR1: UNVERIFIED - mypy is not installed\n"
                           "VERDICTS\nR1: UNVERIFIED\nEND VERDICTS\n")
    cli("review", slug, "--result", "fail", "--judge", "reviewer-1", "--hash", digest,
        "--evidence-file", str(review_file))
    code, out = cli("close", slug)
    assert code == 0 and out["complete"], out
    assert "R1" in out["unreviewed"], out


def test_a_recorded_fail_whose_failure_is_in_prose_still_blocks(project):
    # review of fe5740f: a reply with an UNVERIFIED line and a failure stated in plain words, recorded as a fail
    p, cli = project
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(f"Reviewed checkpoint: {digest}\nR1: UNVERIFIED - mypy missing\n"
                           "Verdict: the parser drops leading spaces; R1 does not hold.\n")
    cli("review", slug, "--result", "fail", "--judge", "reviewer-1", "--hash", digest,
        "--evidence-file", str(review_file))
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"], out


def test_a_fail_line_still_blocks_close_on_the_same_files(project):
    p, cli = project
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(f"Reviewed checkpoint: {digest}\nR1: FAIL - parse(' a') - got ' a'\n"
                           "R2: UNVERIFIED - no mypy\nVERDICTS\nR1: FAIL\nEND VERDICTS\n")
    cli("review", slug, "--result", "fail", "--judge", "reviewer-1", "--hash", digest,
        "--evidence-file", str(review_file))
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"], out


# --- fix 2: the section list is advisory -------------------------------------------------------------

def test_unbuilt_sections_are_listed_but_do_not_stop_close(project):
    p, cli = project
    slug, gates = _start(p, cli, 'R1 [gate] "Parsing: parse() reads a line"\n', review=False)
    code, out = cli("close", slug)
    assert code == 0 and out["complete"], out
    text = out["sections_advisory"]
    assert "Project Layout" in text and "Output Format" in text and '"Parsing"' not in text
    assert "never replace working code" in text


def test_a_section_whose_given_code_differs_still_blocks_close(project):
    p, cli = project
    (p / "DESIGN.md").write_text("# Lib\n\n" + FILLER + "\n## `Store` Class\n\n```python\n"
                                 "class Store(Base):\n    def put(self, key, value): ...\n```\n")
    (p / "src" / "base.py").write_text("class Base:\n    pass\n")
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "base")
    slug, gates = _start(p, cli, 'R1 [gate] "the tool works"\n', review=False)
    (p / "src" / "store.py").write_text("class Store:\n    def put(self, key, value):\n        pass\n")
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"], out
    assert "defines differently" in " ".join(out["missing"]), out


def test_imported_names_count_as_defined(tmp_path):
    import sections
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "__init__.py").write_text(
        "from typing import overload, cast as typed_cast\n"
        "from .core import (\n    Alpha,\n    Beta as Gamma,  # renamed\n)\nimport json as fast_json\n")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "add", "-A")
    (tmp_path / "pkg" / "more.py").write_text(
        '"""Usage: from fake import Store, or from the import of goods."""\n'
        "from .core import (\n    a1,  # see f(x)\n    b1,\n)\nfrom m import c1; import n\n"
        "from m import d1, \\\n    e1\nimport os.path as osp, json as js\n")
    _git(tmp_path, "add", "-A")
    names = sections.defined_names(tmp_path)
    assert {"overload", "typed_cast", "Alpha", "Gamma", "fast_json"} <= names
    assert "cast" not in names and "Beta" not in names
    assert {"a1", "b1", "c1", "d1", "e1", "osp", "js"} <= names
    assert not {"Store", "of"} & names


# --- fixes 1, 3, 4, 5: the skill says so -------------------------------------------------------------

RULES = (
    # E2E-SWE paper run: "keep building while it runs" and "it counts as the round" became these.
    "keep building and editing",
    "Send each brief once",
    "only when it has a FAIL line",
    "never replace working code",
    "never stop to ask",
    "A check you wrote that contradicts the request is yours to fix",
    "importing or running it works",
    "the exact command that worked",
    "Never read Horizon's own source",
)


@pytest.mark.parametrize("folder", [".claude", ".agents"])
def test_the_skill_carries_the_heldout2_rules(folder):
    core = " ".join((ROOT / folder / "skills/horizon/SKILL.md").read_text().split())
    assert [rule for rule in RULES if rule not in core] == []
    assert "ask the user whether the change is legitimate" not in core
