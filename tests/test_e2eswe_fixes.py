"""E2E-SWE paper run (docs/evaluations/e2eswe-paper on branch results/e2eswe; transcripts on transcripts/e2eswe-paper):
Horizon 457791e against plain Claude Code, Haiku 5.5, 15 calls per session counting subagents, 94 tasks. Horizon
lost (mean d -1.70) because it ended builds while the hidden tests still failed:

- The review almost never ran. Of 65 reviewer dispatches, 46 came back only as "Async agent launched" and 11 were
  cut off by the call cap; 8 returned verdicts. A round handed out and never answered counted as spent once a later
  session started, so 79 goals closed on the agent's own gate with only an `unreviewed` note, 57 of them below 100%
  on the hidden tests (reader 5 of 94, pysmt 0 of 27).
- Nothing checked that the gate checked the requirement lines: pysmt's gate named R1-R4, R13 and R14 of 19.
- With the goal still active, sessions stopped to wait for a person: the Stop hook lets the second stop through,
  and django_cqrs, pytermgui, monkeytype and websockets ended sessions under the cap asking for help."""
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


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj "
    (p / ".claude").mkdir(parents=True)
    (p / "src").mkdir()
    (p / "src" / "tool.py").write_text("def parse(line):\n    return line\n")
    _git(p, "init", "-q")
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "init")
    review_dir = tmp_path / "reviews"
    review_dir.mkdir()
    env = {**os.environ, "TMPDIR": str(review_dir)}
    env.pop("HORIZON_UNATTENDED", None)
    env.pop("HORIZON_REVIEW_ROUNDS_PER_GOAL", None)

    def cli(*args):
        done = subprocess.run([sys.executable, ENTRY, "--runtime", "claude", "--project", str(p), *args],
                              cwd=p, capture_output=True, text=True, timeout=120, env=env)
        return done.returncode, (json.loads(done.stdout) if done.stdout.strip().startswith("{") else done.stderr)
    cli.env = env

    def hook(event, payload):
        done = subprocess.run([sys.executable, ENTRY, "--runtime", "claude", "--project", str(p), "hook", event],
                              input=json.dumps(payload), cwd=p, capture_output=True, text=True, timeout=120,
                              env=env)
        assert done.returncode == 0 and "Traceback" not in done.stderr, done.stderr
        return json.loads(done.stdout) if done.stdout.strip() else None
    cli.hook = hook
    cli.new_session = lambda session: hook("SessionStart", {"session_id": session, "source": "startup"})
    return p, cli


REQUIREMENTS = 'R1 [gate] "parse() returns the line"\nR2 [review] "the output reads well"\n'
GATE_TEST = "def test_r1_parse_returns_the_line():\n    assert True\n"


def _start(p, cli, requirements=REQUIREMENTS, gate_test=GATE_TEST, gate=None, extra=()):
    gates = p / ".claude" / "gates" / "g"
    gates.mkdir(parents=True, exist_ok=True)
    (gates / "request.md").write_text("Build the tool\n")
    (gates / "requirements.md").write_text(requirements)
    (gates / "test_request.py").write_text(gate_test)
    command = gate or f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider {shlex.quote(str(gates))}"
    code, out = cli("start", "Build the tool", "--gate-path", str(gates), "--gate", command,
                    "--review", "Every line of requirements.md holds", *extra)
    assert code == 0, out
    return out["slug"], gates


def _brief(p, cli, session="s1"):
    cli.new_session(session)
    slug, gates = _start(p, cli)
    (p / "src" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    assert code == 1 and out.get("review_brief"), out
    return slug, out["observation"]["tree_hash"], Path(out["review_file"])


# --- an unanswered review does not spend the round ------------------------------------------------------

def test_a_review_handed_out_and_never_answered_is_handed_out_again_in_the_next_session(project):
    p, cli = project
    slug, digest, review_file = _brief(p, cli)
    cli.new_session("s2")  # the reviewer launched in the background died with session 1
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"], out
    assert out.get("review_brief") and "unreviewed" not in out, out


def test_a_reply_with_only_its_header_does_not_spend_the_round(project):
    p, cli = project
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(f"Reviewed checkpoint: {digest}\n")
    cli.new_session("s2")
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"] and out.get("review_brief"), out


def test_after_two_unanswered_hand_outs_close_sends_no_third_and_does_not_complete(project):
    p, cli = project
    slug, digest, review_file = _brief(p, cli)
    cli.new_session("s2")
    code, out = cli("close", slug)
    assert out.get("review_brief"), out
    cli.new_session("s3")
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"], out
    assert not out.get("review_brief") and out.get("review_unavailable"), out
    assert "keep building" in out["review_unavailable"].lower(), out
    cli.new_session("s4")
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"] and not out.get("review_brief"), out


def test_with_both_hand_outs_unanswered_a_spent_budget_finalizes_instead_of_completing(project):
    p, cli = project
    cli.new_session("s1")
    slug, gates = _start(p, cli, extra=("--max-turns", "1"))
    (p / "src" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    assert out.get("review_brief"), out
    cli.new_session("s2")
    assert cli("close", slug)[1].get("review_brief")
    cli.new_session("s3")
    code, out = cli("check", slug, "--did", "Ran the gate", "--next", "Run close")
    code, out = cli("close", slug)
    assert not out["complete"] and out["finalized"]["reason"] == "budget_exhausted", out


def test_an_answered_round_still_lets_close_complete_after_its_fail_is_fixed(project):
    p, cli = project
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(f"Reviewed checkpoint: {digest}\nR1: PASS - ran parse - ok\n"
                           "R2: FAIL - the message is unclear\nVERDICTS\nR1: PASS\nR2: FAIL\nEND VERDICTS\n")
    code, out = cli("review", slug, "--result", "fail", "--judge", "reviewer-1", "--hash", digest,
                    "--evidence-file", str(review_file))
    (p / "src" / "tool.py").write_text("def parse(line):\n    return line.strip()\n")
    cli.new_session("s2")
    code, out = cli("close", slug)
    assert code == 0 and out["complete"] and "unreviewed" in out, out


def test_review_pending_tells_the_agent_to_keep_editing(project):
    p, cli = project
    slug, digest, review_file = _brief(p, cli)
    code, out = cli("close", slug)
    assert out.get("review_pending"), out
    text = out["review_pending"].lower()
    assert "keep building" in text and "edit" in text and "never wait" in text, out


# --- close needs a check for every [gate] line --------------------------------------------------------

def test_close_refuses_while_a_gate_line_has_no_check_naming_it(project):
    p, cli = project
    cli.new_session("s1")
    slug, gates = _start(p, cli, requirements=REQUIREMENTS + 'R3 [gate] "parse() strips spaces"\n'
                         'R10 [gate] "parse() keeps tabs"\n')
    (p / "src" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"], out
    assert out.get("unchecked_gate_lines") == ["R3", "R10"], out
    assert any("R3, R10" in m for m in out["missing"]), out
    assert not out.get("review_brief"), out  # no reviewer is spent while the gate lacks checks


def test_a_check_named_in_any_spelling_covers_its_line(project):
    p, cli = project
    cli.new_session("s1")
    test = (GATE_TEST + "def test_r3_strips():\n    assert True\n\n"
            "def test_tabs():  # R10\n    assert True\n")
    slug, gates = _start(p, cli, requirements=REQUIREMENTS + 'R3 [gate] "parse() strips spaces"\n'
                         'R10 [gate] "parse() keeps tabs"\n', gate_test=test)
    (p / "src" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    assert "unchecked_gate_lines" not in out and out.get("review_brief"), out


def test_r1_does_not_cover_r10(project):
    p, cli = project
    cli.new_session("s1")
    slug, gates = _start(p, cli, requirements='R1 [gate] "a"\nR10 [gate] "b"\n')
    (p / "src" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    assert out.get("unchecked_gate_lines") == ["R10"], out


def test_a_check_in_a_project_test_file_the_gate_runs_covers_its_line(project):
    p, cli = project
    cli.new_session("s1")
    (p / "tests").mkdir()
    (p / "tests" / "test_tool.py").write_text("def test_r3_strips():\n    assert True\n")
    gates = p / ".claude" / "gates" / "g"
    command = (f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider {shlex.quote(str(gates))} "
               f"tests/test_tool.py")
    slug, gates = _start(p, cli, requirements=REQUIREMENTS + 'R3 [gate] "parse() strips spaces"\n', gate=command)
    (p / "src" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    assert "unchecked_gate_lines" not in out, out


# --- an unattended run never stops to wait for a person ------------------------------------------------

def test_unattended_stop_keeps_an_active_goal_working_after_the_first_stop(project):
    p, cli = project
    cli.env["HORIZON_UNATTENDED"] = "1"
    cli.new_session("s1")
    slug, gates = _start(p, cli)
    first = cli.hook("Stop", {"session_id": "s1", "stop_hook_active": False})
    again = cli.hook("Stop", {"session_id": "s1", "stop_hook_active": True})
    assert first and first["decision"] == "block", first
    assert again and again["decision"] == "block", again
    assert "no one will answer" in again["reason"].lower(), again


def test_unattended_stop_is_bounded_per_session(project):
    import hooks
    p, cli = project
    cli.env["HORIZON_UNATTENDED"] = "1"
    cli.new_session("s1")
    slug, gates = _start(p, cli)
    blocks = [cli.hook("Stop", {"session_id": "s1", "stop_hook_active": True})
              for _ in range(hooks.UNATTENDED_STOP_BLOCKS + 1)]
    assert all(b and b["decision"] == "block" for b in blocks[:-1]), blocks
    assert blocks[-1] is None
    assert cli.hook("Stop", {"session_id": "s2", "stop_hook_active": True})["decision"] == "block"


def test_attended_stop_still_lets_the_second_stop_through(project):
    p, cli = project
    cli.new_session("s1")
    slug, gates = _start(p, cli)
    assert cli.hook("Stop", {"session_id": "s1", "stop_hook_active": False})["decision"] == "block"
    assert cli.hook("Stop", {"session_id": "s1", "stop_hook_active": True}) is None


# --- the skill carries the rules -----------------------------------------------------------------------

E2ESWE_RULES = (
    "out once more",
    "`review_unavailable`",
    "keep building and editing",
    "`close` refuses while a `[gate]` line has no check naming it",
    "never through `PYTHONPATH` or the source tree",
    "If no command can run at all, write the code anyway",
    "Baseline tests are the project's own tests from before the goal",
    "the checks in the gate directory are yours",
    "Take a check the environment blocks",
    "retag its line `[review]`",
)
E2ESWE_GONE = (
    "Once handed out, it counts as the round",
    "If it runs in the background, keep building while it runs; never wait for its reply",
    '(`subagent_type: "general-purpose"`, in the foreground)',
)


def _flat(text):
    return " ".join(text.split())


@pytest.mark.parametrize("folder", [".claude", ".agents"])
@pytest.mark.parametrize("name", ["SKILL.md", "references/guide.md"])
def test_the_skill_and_guide_carry_the_e2eswe_rules(folder, name):
    text = _flat((ROOT / folder / "skills/horizon" / name).read_text())
    assert [r for r in E2ESWE_RULES if _flat(r) not in text] == []
    assert [g for g in E2ESWE_GONE if _flat(g) in text] == []


def test_the_claude_skill_dispatches_the_reviewer_in_the_foreground():
    # 46 of 65 reviewers came back only as "Async agent launched": "in the foreground" did not set the parameter.
    text = _flat((ROOT / ".claude/skills/horizon/SKILL.md").read_text())
    assert "`run_in_background: false`" in text


def test_two_answered_rounds_close_and_name_both_rounds(project):
    # The multi-round note still counts the rounds that came back.
    p, cli = project
    cli.env["HORIZON_REVIEW_ROUNDS_PER_GOAL"] = "2"
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(f"Reviewed checkpoint: {digest}\nR1: PASS - ran parse - ok\nR2: FAIL - unclear\n"
                           "VERDICTS\nR1: PASS\nR2: FAIL\nEND VERDICTS\n")
    (p / "src" / "tool.py").write_text("def parse(line):\n    return line.strip()\n")
    cli.new_session("s2")
    code, out = cli("close", slug)
    assert out.get("review_brief"), out
    second = Path(out["review_file"])
    digest2 = out["observation"]["tree_hash"]
    second.write_text(f"Reviewed checkpoint: {digest2}\nR2: FAIL - still unclear\n"
                      "VERDICTS\nR1: NOT REVIEWED\nR2: FAIL\nEND VERDICTS\n")
    (p / "src" / "tool.py").write_text("def parse(line):\n    return line.strip() + ''\n")
    cli.new_session("s3")
    code, out = cli("close", slug)
    assert code == 0 and out["complete"] and "2 review rounds" in out["unreviewed"], out


# --- review of 632fbb8 -----------------------------------------------------------------------------------

def _named_gate(p, cli, test_files, gate_tail, requirements=REQUIREMENTS + 'R3 [gate] "parse() strips spaces"\n'):
    cli.new_session("s1")
    (p / "tests").mkdir(exist_ok=True)
    for name, body in test_files.items():
        (p / "tests" / name).write_text(body)
    gates = p / ".claude" / "gates" / "g"
    command = f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider {shlex.quote(str(gates))} {gate_tail}"
    return _start(p, cli, requirements=requirements, gate=command)


def test_a_symlink_loop_in_a_named_directory_does_not_crash_close(project):
    # Python 3.10-3.12 raise RuntimeError from resolve() on a symlink loop; close crashed and never finalized.
    p, cli = project
    slug, gates = _named_gate(p, cli, {"test_tool.py": "def test_r3_strips():\n    assert True\n"}, "tests")
    os.symlink("loop", p / "tests" / "loop")
    (p / "src" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    assert code in (0, 1) and isinstance(out, dict), out
    assert "unchecked_gate_lines" not in out, out


def test_a_fifo_in_a_named_directory_does_not_hang_close(project):
    p, cli = project
    slug, gates = _named_gate(p, cli, {"test_tool.py": "def test_r3_strips():\n    assert True\n"}, "tests")
    os.mkfifo(p / "tests" / "test_pipe.py")
    (p / "src" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)  # cli's own timeout fails the test on a hang
    assert isinstance(out, dict) and "unchecked_gate_lines" not in out, out


def test_a_pytest_node_id_and_an_absolute_path_with_a_space_name_their_files(project):
    p, cli = project
    tail = f"tests/test_node.py::test_r3_strips {shlex.quote(str(p / 'tests' / 'test_abs.py'))}"
    slug, gates = _named_gate(p, cli, {"test_node.py": "def test_r3_strips():\n    assert True\n",
                                       "test_abs.py": "def test_r4_keeps():\n    assert True\n"}, tail,
                              requirements=REQUIREMENTS + 'R3 [gate] "strips"\nR4 [gate] "keeps"\n')
    (p / "src" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    assert "unchecked_gate_lines" not in out, out


def test_a_variable_or_a_binary_file_does_not_name_a_line(project):
    p, cli = project
    test = GATE_TEST + "def test_parse():\n    r3 = 'x'\n    assert r3\n"
    (p / ".claude" / "gates" / "g").mkdir(parents=True)
    (p / ".claude" / "gates" / "g" / "blob.bin").write_bytes(b"\0R3 test_r3_x")
    cli.new_session("s1")
    slug, gates = _start(p, cli, requirements=REQUIREMENTS + 'R3 [gate] "parse() strips spaces"\n', gate_test=test)
    (p / "src" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    assert out.get("unchecked_gate_lines") == ["R3"], out


def test_a_dotted_line_is_suggested_as_a_valid_name_and_named_by_it(project):
    p, cli = project
    cli.new_session("s1")
    slug, gates = _start(p, cli, requirements=REQUIREMENTS + 'R3.1 [gate] "strips"\n')
    (p / "src" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    assert out.get("unchecked_gate_lines") == ["R3.1"] and any("test_r3_1_" in m for m in out["missing"]), out
    (gates / "test_request.py").write_text(GATE_TEST + "def test_r3_1_strips():\n    assert True\n")
    code, out = cli("close", slug)
    assert "unchecked_gate_lines" not in out, out


def test_check_hands_out_no_brief_while_a_gate_line_is_unchecked(project):
    p, cli = project
    cli.new_session("s1")
    slug, gates = _start(p, cli, requirements=REQUIREMENTS + 'R3 [gate] "parse() strips spaces"\n')
    (p / "src" / "extra.py").write_text("X = 1\n")
    code, out = cli("check", slug, "--did", "Added extra.py", "--next", "Name R3 in the gate")
    assert out.get("unchecked_gate_lines") == ["R3"] and not out.get("review_brief"), out


def test_unattended_stop_without_a_session_id_still_blocks_and_is_bounded(project):
    import hooks
    p, cli = project
    cli.env["HORIZON_UNATTENDED"] = "1"
    cli.new_session("s1")
    slug, gates = _start(p, cli)
    blocks = [cli.hook("Stop", {"stop_hook_active": True}) for _ in range(hooks.UNATTENDED_STOP_BLOCKS + 1)]
    assert all(b and b["decision"] == "block" for b in blocks[:-1]) and blocks[-1] is None, blocks


def test_unattended_sessions_that_stop_in_turn_keep_their_own_counts(project):
    import hooks
    p, cli = project
    cli.env["HORIZON_UNATTENDED"] = "1"
    cli.new_session("s1")
    slug, gates = _start(p, cli)
    for _ in range(hooks.UNATTENDED_STOP_BLOCKS):
        assert cli.hook("Stop", {"session_id": "a", "stop_hook_active": True})["decision"] == "block"
        assert cli.hook("Stop", {"session_id": "b", "stop_hook_active": True})["decision"] == "block"
    assert cli.hook("Stop", {"session_id": "a", "stop_hook_active": True}) is None
    assert cli.hook("Stop", {"session_id": "b", "stop_hook_active": True}) is None
