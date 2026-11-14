"""Defects found by reading the E2E-SWE thin4 to thin12 transcripts (2026-11-13, exp/thin ca5a21d; the builds ran
Claude Haiku 5.5, ten sessions of 16 turns each).

Each test names the build it comes from. In short:

1. `close` held a goal on "baseline" test files the session had written itself (thin6 django-modelcluster lost
   sessions 5 to 8; thin8 protobuf, thin9 huey and inline-snapshot, thin12 tremolo, thin7 pysmt).
2. The install check had no way out when the request says something else supplies the setup script (thin6 fiddle:
   three sessions of bookkeeping, then a question to nobody), and blamed the project for a sandbox's permission error
   (thin11 mpmath).
3. After a goal completed, the next session was told only "No active Horizon goal": it started another goal on the
   finished files, which closed at once with no request check (thin10 claripy: sessions 8 to 10, no commit).
4. The reminder and the Plateau line repeated the saved next action as an order when it was stale, impossible, or
   when the right step was `check` and `close` (thin8 and thin10 sapp wrote test files "because the hook needs a
   file changed").
5. Names the session had recorded as not the work's own were listed again at every session start (20 of 20).
6. The surface check listed a class's methods as names of the package when an object in the spec has the package's
   name (thin10 huey), and message codes such as `pkg.E001` (thin5 and thin6 django-modelcluster).
7. One `check --parts 1-99` saved every part of the request check.
8. A `check` on unchanged files reported the gate as skipped with exit code 1, and the next session start said
   "Last gate run: skipped".
9. "Where the last session stopped" quoted a Write's raw result: the file's text before the write (thin12 protobuf).
10. `check` and `close` printed the baseline observation beside a newer checkpoint (thin12 tremolo reported a fixed
    failure as open).
11. What `check --did` recorded was shown to no later session (thin10 mpmath re-derived a diagnosis).
12. The spec examples read no `# value == expected` comment, the form several specs give their results in.
13. A gate that skipped tests counted as a passing gate with nothing said (thin11 pgqueuer: 25 passed, 10 skipped).
"""
import json
import re
import shlex
import sys

from installable_project import installable
from test_e2eswe_fixes import _git, project  # noqa: F401  (the fixture is used by name)
from test_thin import PASSING, SLUG, SPEC, _build, _context, _thin

import examples
import goal
import hooks
import install_gate
import surface
import unbuilt


def _ready(p, cli):
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)
    installable(p)


def _start_with(p, cli, spec, name="g", objective="Build the tool"):
    """`_thin` with the spec given: a goal the way the thin SKILL.md starts one."""
    (p / "SPEC.md").write_text(spec)
    (p / "tests").mkdir(exist_ok=True)
    (p / "tests" / "test_tool.py").write_text(
        PASSING + "".join(f"\n\ndef test_parse_case_{i}():\n    assert True\n" for i in range(1, 10)))
    gates = p / ".claude" / "gates" / name
    gates.mkdir(parents=True, exist_ok=True)
    (gates / "request.md").write_text("Build the library described in SPEC.md\n")
    command = f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider tests"
    code, out = cli("start", objective, "--gate-path", str(gates), "--gate", command)
    assert code == 0, out
    return out, gates


def _complete(cli, slug=SLUG):
    code, out = cli("close", slug)
    while code and any("part" in m for m in out.get("missing") or []):
        saved, said = cli("check", slug, "--did", "Ran the parts", "--next", "Close", "--parts", "1-99")
        assert saved == 0, said
        code, out = cli("close", slug)
        if code and not any("still to run" in m for m in out.get("missing") or []):
            break
    assert code == 0 and out["complete"], out
    return out


def _calls(cli, session, count, tool="Read"):
    """`count` tool calls that change no file; the reminders the PostToolUse hook gave."""
    said = []
    for _ in range(count):
        out = cli.hook("PostToolUse", {"session_id": session, "tool_name": tool,
                                       "tool_input": {"file_path": "SPEC.md"}, "tool_response": {"numLines": 3}})
        if out:
            said.append(out["hookSpecificOutput"]["additionalContext"])
    return said


# --- 1. baseline tests ---------------------------------------------------------------------------------------

def test_a_test_file_the_session_wrote_before_start_is_not_a_baseline_test(project):
    p, cli = project
    _ready(p, cli)  # tests/test_tool.py was written, not committed, just before `start`
    with open(p / "tests" / "test_tool.py", "a") as handle:
        handle.write("\n\ndef test_parse_keeps_spaces():\n    assert True\n")
    code, out = cli("close", SLUG)
    assert not any("Baseline test files changed" in m for m in out["missing"]), out


def test_a_test_file_the_project_had_before_the_goal_is_still_held(project):
    p, cli = project
    (p / "tests").mkdir()
    (p / "tests" / "test_tool.py").write_text(
        PASSING + "".join(f"\n\ndef test_parse_case_{i}():\n    assert True\n" for i in range(1, 10)))
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "the project's own tests")
    cli.new_session("s1")  # the session begins with the tests already there
    _thin(p, cli)  # writes the same text again
    _build(p)
    installable(p)
    with open(p / "tests" / "test_tool.py", "a") as handle:
        handle.write("\n\ndef test_parse_keeps_spaces():\n    assert True\n")
    code, out = cli("close", SLUG)
    held = [m for m in out["missing"] if "Baseline test files changed" in m]
    assert code == 1 and held and "tests/test_tool.py" in held[0], out
    # an unattended run has no one to approve: the message says what the session itself can do
    assert "git checkout" in held[0] and "new test file" in held[0], held[0]


def test_a_later_goal_takes_the_first_goals_baseline_not_the_tests_the_first_goal_wrote(project):
    # thin12 tremolo: goal 2 took goal 1's own tests/test_spec_gaps.py as baseline and held `close` on it
    p, cli = project
    _ready(p, cli)
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "session 1 (auto-commit)")
    out, _ = _start_with(p, cli, SPEC, name="g2")
    with open(p / "tests" / "test_tool.py", "a") as handle:
        handle.write("\n\ndef test_parse_keeps_spaces():\n    assert True\n")
    code, out = cli("close", out["slug"])
    assert not any("Baseline test files changed" in m for m in out["missing"]), out


# --- 2. the install check ------------------------------------------------------------------------------------

def test_an_install_fact_does_not_release_the_hold_for_a_missing_script(project):
    # 11dc6c7 let the fact release it; thin14 fiddle recorded it in session 3, wrote no setup.sh and scored 0 of 58
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)  # no pyproject.toml, no setup.sh
    code, out = cli("close", SLUG)
    held = [m for m in out["missing"] if "does not install" in m]
    assert code == 1 and held and "setup.sh" in held[0], out
    assert "install check:" not in held[0], "no fact is offered for a file the project lacks"
    assert "write it" in held[0] and "something else runs it" in held[0], held
    assert cli("note", SLUG, "--fact", "install check: SPEC line 3 says the environment supplies setup.sh")[0] == 0
    code, out = cli("close", SLUG)
    assert code == 1 and any("does not install" in m for m in out["missing"]), out
    assert "A recorded fact answers" not in out.get("install_problem", ""), out
    assert "does not install" in _context(cli.new_session("s2")), "and each session's start still says it"


def test_a_fact_about_something_else_does_not_release_it(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)
    assert cli("note", SLUG, "--fact", "Tests run with python -m pytest tests -q")[0] == 0
    code, out = cli("close", SLUG)
    assert code == 1 and any("does not install" in m for m in out["missing"]), out


def test_a_permission_error_is_not_called_the_projects_fault():
    # thin11 mpmath s6: the wheel built and installed; `mktemp` was refused by the sandbox the check ran in
    found = {"scripts": ["setup.sh"], "script_exit": 1, "script_failed": "setup.sh", "pyproject": True,
             "script_output": "Successfully installed mpmath-0.1.0 | mktemp: mkdtemp failed on /var/folders/x: "
                              "Operation not permitted"}
    text = install_gate.problem(found)
    assert "is not the cause" not in text, text
    assert "permission" in text.lower() and "install check:" in text, text
    plain = install_gate.problem({**found, "script_output": "error: no such option --frob"})
    assert "is not the cause" in plain, plain


# --- 3. after a goal completes ---------------------------------------------------------------------------------

def test_the_session_after_completion_is_told_of_the_goal_and_how_to_carry_on(project):
    p, cli = project
    _ready(p, cli)
    _complete(cli)
    text = _context(cli.new_session("s2"))
    assert SLUG in text and "complete" in text and f"reopen {SLUG}" in text, text


def test_a_complete_goal_reopens_and_its_request_check_is_asked_again(project):
    p, cli = project
    _ready(p, cli)
    _complete(cli)
    code, out = cli("reopen", SLUG, "--reason", "The request asks for more work on the same library")
    assert code == 0 and out["status"] == "active", out
    code, out = cli("close", SLUG)
    assert code == 1 and any("part by part" in m for m in out["missing"]), out


def test_a_second_goal_on_the_finished_files_does_not_close_at_once(project):
    # thin10 claripy s8 to s10: start, close, complete with `unchanged_since_baseline`, three sessions running
    p, cli = project
    _ready(p, cli)
    _complete(cli)
    out, _ = _start_with(p, cli, SPEC, name="g2")
    assert "reopen" in json.dumps(out), "start says the earlier goal can be reopened instead"
    code, closed = cli("close", out["slug"])
    assert code == 1 and not closed["complete"], closed
    assert any("part by part" in m for m in closed["missing"]), closed


def test_a_later_goal_keeps_the_facts_of_the_earlier_one(project):
    # thin12 tremolo: a fact recorded in goal 1 was re-derived in sessions 8, 9 and 10 under goal 2
    p, cli = project
    _ready(p, cli)
    assert cli("note", SLUG, "--fact", "The sandbox refuses to bind a port")[0] == 0
    _complete(cli)
    _start_with(p, cli, SPEC, name="g2")
    assert "The sandbox refuses to bind a port" in _context(cli.new_session("s2"))


# --- 4. the reminder and the next action -----------------------------------------------------------------------

def _resumed(p, cli, next_action="Build reader.py"):
    _ready(p, cli)
    assert cli("check", SLUG, "--did", "Built parse", "--next", next_action)[0] == 0
    cli.new_session("s2")


def test_the_reminder_asks_for_no_file_to_be_added_for_its_own_sake(project):
    p, cli = project
    _resumed(p, cli)
    said = _calls(cli, "s2", 6)
    assert len(said) == 1 and "no file of the work has changed" in said[0], said
    assert "Build reader.py" in said[0] and "only to change one" in said[0], said[0]


def test_no_reminder_while_the_request_check_is_under_way(project):
    # thin8 sapp s3: "The Horizon hook needs a file of the work changed now, so I'll add a regression test file"
    p, cli = project
    _ready(p, cli)
    assert cli("close", SLUG)[0] == 1  # asks for the request check
    assert cli("check", SLUG, "--did", "Ran part 1", "--next", "Run the rest", "--parts", "1")[0] == 0
    cli.new_session("s2")
    assert _calls(cli, "s2", 11) == []


def test_no_reminder_once_the_goal_is_complete(project):
    # thin10 protobuf s7: the reminder came after `close` had completed the goal
    p, cli = project
    _resumed(p, cli)
    _complete(cli)
    assert _calls(cli, "s2", 11) == []


def test_a_next_action_that_keeps_opening_sessions_is_not_repeated_as_an_order(project):
    # thin11 protobuf s10: "Write now: A person corrects test_surface.py ...", in a run with no person
    p, cli = project
    _resumed(p, cli, next_action="A person decides about setup.sh")
    cli.new_session("s3")
    text = _context(cli.new_session("s4"))
    assert "opened 3 sessions in a row" in text, text
    assert "Start with the next action" not in text, text
    said = _calls(cli, "s4", 6)
    assert said and "A person decides" not in said[0], said


# --- 5. names recorded as not the work's own -------------------------------------------------------------------

NAMES_SPEC = SPEC + "\nA `Timestamp` from the protobuf library is accepted, and `frobnicate(x)` returns x.\n"


def test_a_name_recorded_as_not_ours_is_not_listed_again(project):
    p, cli = project
    cli.new_session("s1")
    _start_with(p, cli, NAMES_SPEC)
    _build(p)
    code, out = cli("check", SLUG, "--did", "Built parse", "--next", "Build the rest")
    assert "Timestamp" in out["unbuilt_names"] and "frobnicate" in out["unbuilt_names"], out
    assert "Not ours:" in out["unbuilt_names"], "the message says how to record one that needs nothing"
    assert cli("note", SLUG, "--fact", "Not ours: Timestamp (protobuf's own class)")[0] == 0
    code, out = cli("check", SLUG, "--did", "Recorded it", "--next", "Build the rest")
    assert "Timestamp" not in out["unbuilt_names"] and "frobnicate" in out["unbuilt_names"], out
    text = _context(cli.new_session("s2"))
    assert "frobnicate" in text and "defines them nowhere (1)" in text, text


def test_the_way_a_session_already_words_it_is_read_too():
    # thin12 protobuf: "unbuilt_names checked: IntEnum, ... belong to enum/protobuf/pydantic ... None needs building."
    facts = ["unbuilt_names checked: IntEnum, Timestamp belong to enum/protobuf; option is the proto keyword. "
             "None needs building.", "Tests run with python -m pytest"]
    assert unbuilt.dismissed(["IntEnum", "Timestamp", "option", "pytest", "frobnicate"], facts) == {
        "IntEnum", "Timestamp", "option"}


def test_subscript_notation_is_not_a_name():
    # thin10 and thin11 mpmath: `B_n`, `Li_s` and `Re` stayed on the list for nine sessions
    names = unbuilt.request_names("The numbers `B_n`, the function `Li_s(z)` and the class `Widget`; `to_b(x)`.")
    assert "B_n" not in names and "Li_s" not in names, names
    assert "Widget" in names and "to_b" in names, names


# --- 6. the surface check ------------------------------------------------------------------------------------

HUEY = """# Huey

```python
from huey import crontab
from huey.consumer import Consumer
```

`Consumer(huey, workers=1)` starts a consumer. Decorate with `huey.task()` and `huey.periodic_task(crontab())`;
`huey.immediate` can be toggled. Disconnect with `huey.disconnect_signal(handler)`.
"""


def test_a_method_of_an_object_named_like_the_package_is_not_a_name_of_the_package():
    found = surface.refs(HUEY, "huey")
    assert "huey.crontab" in found and "huey.consumer.Consumer" in found, found
    assert not {"huey.task", "huey.periodic_task", "huey.disconnect_signal"} & set(found), found


def test_a_name_such_a_spec_writes_without_a_call_may_be_a_module_and_stays():
    # the `reader` spec: `reader = make_reader(...)` is an object, and `reader.exceptions` is still a module
    found = surface.refs("```python\nreader = make_reader('db.sqlite')\nreader.update_feeds()\n```\n"
                         "Errors live in `reader.exceptions`.", "reader")
    assert "reader.exceptions" in found and "reader.update_feeds" not in found, found


def test_a_keyword_argument_or_an_entry_point_is_not_such_an_object():
    text = ("`Machine(states=None, transitions=None)` and `transitions.add(x)`.\n"
            "Console script: `transitions = transitions.cli:main`.")
    assert "transitions.add" in surface.refs(text, "transitions")


def test_without_such_an_object_a_dotted_name_is_still_the_packages():
    found = surface.refs("`toolpkg.parse(line)` returns the line. `toolpkg.reader.Reader` reads files.", "toolpkg")
    assert "toolpkg.parse" in found and "toolpkg.reader.Reader" in found, found


def test_a_message_code_is_not_a_name_of_the_package():
    found = surface.refs("- **`modelcluster.E001`** when the target is not a ClusterableModel.\n"
                         "`modelcluster.fields.ParentalKey` is the field.", "modelcluster")
    assert "modelcluster.E001" not in found and "modelcluster.fields.ParentalKey" in found, found


def test_check_says_where_a_name_the_request_does_not_mean_is_deleted(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    code, out = cli("check", SLUG, "--did", "Nothing yet", "--next", "Build parse")
    assert surface.FILE in out["surface_missing"] and "delete" in out["surface_missing"], out


# --- 7. the request check ------------------------------------------------------------------------------------

# Twelve parts of the request check: each "## Part" section is longer than `goal.PART_LINES`, so none is joined to
# its neighbour (the short sections of SPEC and "## Part 1" make the first part together).
LONG_SPEC = SPEC + "".join(f"\n## Part {i}\n\n`toolpkg.parse(line)` again.\n" + "It reads one line at a time.\n" * 40
                           for i in range(1, 13))


def test_one_check_saves_a_few_parts_not_the_whole_list(project):
    p, cli = project
    cli.new_session("s1")
    _start_with(p, cli, LONG_SPEC)
    _build(p)
    installable(p)
    code, out = cli("close", SLUG)
    assert code == 1 and any("part by part" in m for m in out["missing"]), out
    code, out = cli("check", SLUG, "--did", "Ran them all", "--next", "Close", "--parts", "1-99")
    assert "still to run" in out["request_check"] and "at most" in out["request_check"], out
    code, out = cli("close", SLUG)
    assert code == 1 and not out["complete"], out


def test_check_says_when_every_part_is_saved(project):
    p, cli = project
    _ready(p, cli)
    assert cli("close", SLUG)[0] == 1
    code, out = cli("check", SLUG, "--did", "Ran each part", "--next", "Close", "--parts", "1-9")
    assert code == 0 and "close" in out["parts_saved"], out


def test_the_request_horizon_carried_over_is_not_a_part_of_it(project):
    p, cli = project
    _ready(p, cli)
    _complete(cli)
    out, gates = _start_with(p, cli, SPEC, name="g2")
    (gates / "request.md").write_text("Continue the library\n\n## Earlier request for this project (kept by "
                                      "Horizon)\n\nBuild the library described in SPEC.md\n")
    parts = goal._request_part_list(goal.json.loads((p / ".claude/harness/goals" / f"{out['slug']}.json").read_text()))
    assert parts and not any("kept by Horizon" in part["heading"] for part in parts), parts


# --- 8. a check on unchanged files -----------------------------------------------------------------------------

def test_a_check_on_unchanged_files_keeps_the_last_result_and_its_exit_code(project):
    p, cli = project
    _ready(p, cli)
    assert cli("check", SLUG, "--did", "Built parse", "--next", "Build reader.py")[0] == 0
    code, out = cli("check", SLUG, "--did", "Read the spec again", "--next", "Build reader.py")
    assert code == 0, "the gate passed on these files; a run that was not repeated is no failure"
    assert "pass" in out["gate_skipped"], out
    text = _context(cli.new_session("s2"))
    assert "Last gate run: pass" in text and "Last gate run: skipped" not in text, text


def test_a_failing_result_is_kept_with_its_failing_tests(project):
    p, cli = project
    _ready(p, cli)
    (p / "tests" / "test_reader.py").write_text("def test_reader_reads():\n    assert False\n")
    assert cli("check", SLUG, "--did", "Wrote a reader test", "--next", "Build reader.py")[0] == 1
    code, out = cli("check", SLUG, "--did", "Looked again", "--next", "Build reader.py")
    assert code == 1 and "fail" in out["gate_skipped"], out
    text = _context(cli.new_session("s2"))
    assert "Last gate run: fail" in text and "test_reader_reads" in text, text


# --- 9. where the last session stopped -------------------------------------------------------------------------

def test_a_write_is_reported_as_the_file_it_wrote_not_as_its_raw_result(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    cli.hook("PostToolUse", {"session_id": "s1", "tool_name": "Write",
                             "tool_input": {"file_path": str(p / "toolpkg" / "gen.py"), "content": "x = 1\n"},
                             "tool_response": {"type": "update", "filePath": str(p / "toolpkg" / "gen.py"),
                                               "content": "x = 1\n", "structuredPatch": [],
                                               "originalFile": "raise NotImplementedError('placeholder')\n",
                                               "userModified": False}})
    text = _context(cli.new_session("s2"))
    assert "Where the last session stopped" in text and "gen.py" in text, text
    assert "NotImplementedError" not in text and "userModified" not in text and "which gave" not in text, text


# --- 10. the observation beside a newer checkpoint -------------------------------------------------------------

def test_check_does_not_print_an_observation_older_than_its_own_result(project):
    p, cli = project
    _ready(p, cli)
    code, out = cli("check", SLUG, "--did", "Built parse", "--next", "Build reader.py")
    assert code == 0 and out["last_checkpoint"]["gate"] == "pass", out
    assert out["last_observation"] is None, "the baseline run is older than this check"


def test_close_prints_only_the_run_it_made_itself(project):
    p, cli = project
    _ready(p, cli)
    assert cli("check", SLUG, "--did", "Built parse", "--next", "Close")[0] == 0
    code, out = cli("close", SLUG)  # a passing check covers these files: close runs nothing
    assert code == 1 and out["observation"] is None, out


# --- 11. what the last check did -------------------------------------------------------------------------------

def test_the_next_session_is_told_what_the_last_check_did(project):
    # thin10 mpmath s2 -> s3: "agm(1,2) expected 1.4567910310469068... digit 18 differs; our agm is correct"
    p, cli = project
    _ready(p, cli)
    assert cli("check", SLUG, "--did", "The spec's agm value is off at digit 18; ours is right",
               "--next", "Build gamma")[0] == 0
    assert "digit 18" in _context(cli.new_session("s2"))


# --- 12. spec examples -----------------------------------------------------------------------------------------

COMMENT_SPEC = """# Machines

```python
from machines import Machine
m = Machine(states=['A', 'B'], initial='A')
m.to_B()
# m.state == 'B'
# the machine is now in B
m.is_B()  # True
```
"""


def test_a_comment_that_states_a_value_is_an_example_to_check():
    found, _ = examples.extract(COMMENT_SPEC, "machines")
    assert len(found) == 1, found
    assert "assert m.state == 'B'" in found[0]["source"], found[0]["source"]
    assert "# the machine is now in B" in found[0]["source"], "prose stays a comment"
    assert len(found[0]["source"].splitlines()) == len(COMMENT_SPEC.split("```")[1].splitlines()) - 1


def test_a_comment_that_is_prose_or_gives_no_value_is_not():
    spec = "```python\nfrom machines import Machine\nm = Machine()\n# m.state == other.state\n# x == 3 when ready\n```\n"
    assert examples.extract(spec, "machines")[0] == []


# --- 13. skipped tests -----------------------------------------------------------------------------------------

def test_a_gate_that_skips_tests_says_so(project):
    p, cli = project
    _ready(p, cli)
    (p / "tests" / "test_backend.py").write_text(
        "import pytest\n\n\n@pytest.mark.skip(reason='needs a database')\ndef test_backend():\n    assert False\n")
    code, out = cli("check", SLUG, "--did", "Added the backend test", "--next", "Close")
    assert code == 0 and "1" in out["skipped_tests"] and "final report" in out["skipped_tests"], out


def test_a_gate_that_skips_none_says_nothing(project):
    p, cli = project
    _ready(p, cli)
    code, out = cli("check", SLUG, "--did", "Built parse", "--next", "Close")
    assert code == 0 and "skipped_tests" not in out, out


# --- small ones ----------------------------------------------------------------------------------------------

def test_the_last_commit_line_leaves_out_compiled_files(project):
    p, cli = project
    _ready(p, cli)
    (p / "toolpkg" / "__pycache__").mkdir()
    (p / "toolpkg" / "__pycache__" / "reader.cpython-314.pyc").write_bytes(b"\0")
    _git(p, "add", "-A", "-f")
    _git(p, "commit", "-qm", "session 1 (auto-commit)")
    text = _context(cli.new_session("s2"))
    assert "The last commit changed" in text and ".pyc" not in text, text


def test_the_call_text_of_a_tool_with_no_output_is_empty():
    ran, back = hooks._call_text({"tool_name": "Edit", "tool_input": {"file_path": "a.py"},
                                  "tool_response": {"structuredPatch": [], "originalFile": "old"}})
    assert ran == "Edit a.py" and back == ""

# --- after review (2026-11-13) -----------------------------------------------------------------------------------
# The first version of fixes 1, 2, 5 and 6 above was wider than the builds it came from. Each test here is a case it
# got wrong: a person's own tests lost their hold, a waiver outlived its reason, and real names were dropped from
# the surface check and the unbuilt list.

WANTED = "from toolpkg import parse\n\n\ndef test_wanted():\n    assert parse('a') == 'a'\n"


def _held(out):
    return [m for m in out["missing"] if "Baseline test files changed" in m]


def test_a_test_a_person_wrote_and_did_not_commit_is_a_baseline_test(project):
    # "I wrote failing tests, keep working until they pass": the tests are in the folder, uncommitted, when the
    # session begins. Read from git, they were the session's own work and could be weakened with no hold.
    p, cli = project
    (p / "tests").mkdir()
    (p / "tests" / "test_wanted.py").write_text(WANTED)
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)
    installable(p)
    (p / "tests" / "test_wanted.py").write_text("def test_wanted():\n    assert True\n")
    code, out = cli("close", SLUG)
    assert code == 1 and _held(out) and "tests/test_wanted.py" in _held(out)[0], out
    assert "tests/test_tool.py" not in _held(out)[0], "the test the session wrote itself is not held"


def test_a_persons_test_the_session_changed_before_start_is_held_too(project):
    p, cli = project
    (p / "tests").mkdir()
    (p / "tests" / "test_wanted.py").write_text(WANTED)
    cli.new_session("s1")
    (p / "tests" / "test_wanted.py").write_text("def test_wanted():\n    assert True\n")  # before `start`
    _thin(p, cli)
    _build(p)
    installable(p)
    code, out = cli("close", SLUG)
    assert code == 1 and _held(out) and "tests/test_wanted.py" in _held(out)[0], out


def test_a_test_added_to_the_project_after_its_first_goal_is_baseline_for_the_next(project):
    # Taking the first goal's list for good left every test written after that goal unheld.
    p, cli = project
    _ready(p, cli)
    _complete(cli)
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "goal 1")
    (p / "tests" / "test_wanted.py").write_text(WANTED)
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "a test of the user's own")
    cli.new_session("s2")
    out, _ = _start_with(p, cli, SPEC, name="g2", objective="Carry the tool on")
    (p / "tests" / "test_wanted.py").write_text("def test_wanted():\n    assert True\n")
    with open(p / "tests" / "test_tool.py", "a") as handle:
        handle.write("\n\ndef test_parse_keeps_spaces():\n    assert True\n")
    code, closed = cli("close", out["slug"])
    assert code == 1 and _held(closed) and "tests/test_wanted.py" in _held(closed)[0], closed
    assert "tests/test_tool.py" not in _held(closed)[0], "the first goal's own test stays the work's"


def test_with_no_record_of_the_session_start_every_test_file_is_baseline(project):
    # No SessionStart hook ran (hooks not installed): nothing says what the session wrote, so nothing is let go.
    p, cli = project
    _thin(p, cli)
    _build(p)
    installable(p)
    with open(p / "tests" / "test_tool.py", "a") as handle:
        handle.write("\n\ndef test_parse_keeps_spaces():\n    assert True\n")
    code, out = cli("close", SLUG)
    assert code == 1 and _held(out), out


def test_the_restore_command_names_the_commit_the_goal_began_on(project):
    # `git checkout -- FILE` restores the last commit's version: the changed one, once a session has been committed.
    p, cli = project
    (p / "tests").mkdir()
    (p / "tests" / "test_wanted.py").write_text(WANTED)
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "the user's test")
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)
    installable(p)
    (p / "tests" / "test_wanted.py").write_text("def test_wanted():\n    assert True\n")
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "session 1 (auto-commit)")
    code, out = cli("close", SLUG)
    command = re.search(r"`(git checkout [0-9a-f]{12} -- \S+)`", _held(out)[0])
    assert command, out
    _git(p, *shlex.split(command.group(1))[1:])
    assert (p / "tests" / "test_wanted.py").read_text() == WANTED
    code, out = cli("close", SLUG)
    assert not _held(out), out


def test_an_install_fact_does_not_answer_for_a_package_that_does_not_build():
    missing = {"missing_scripts": ["setup.sh"], "scripts": [], "pyproject": True, "exit": 0}
    refused = {"scripts": ["setup.sh"], "script_exit": 1, "script_output": "mktemp: Operation not permitted"}
    both = {**refused, "missing_scripts": ["install.sh"]}
    broken = {"scripts": [], "pyproject": True, "exit": 1, "output": "TOMLDecodeError"}
    unimportable = {"scripts": ["setup.sh"], "script_exit": 0, "import_error": "No module named toolpkg"}
    failed = {"scripts": ["setup.sh"], "script_exit": 2, "script_output": "error: no such option --frob"}
    assert install_gate.waivable(refused)
    assert not any(install_gate.waivable(found) for found in (missing, both, broken, unimportable, failed))
    state = {"facts": [{"text": "install check: mktemp was refused by the sandbox"}]}
    assert goal.install_waived(state, refused) and not goal.install_waived(state, broken)
    assert not goal.install_waived(state, missing) and not goal.install_waived(state, both)
    assert not goal.install_waived({"facts": []}, refused)
    # the way out is offered only where it would be taken
    assert "install check:" in install_gate.message(refused)
    assert not any("install check:" in install_gate.message(found) for found in (missing, both, broken))


def test_a_waived_install_problem_is_said_again_when_the_package_itself_breaks(project):
    p, cli = project
    _ready(p, cli)
    assert cli("note", SLUG, "--fact", "install check: mktemp was refused by the sandbox")[0] == 0
    (p / "pyproject.toml").write_text("this is not toml [[[\n")
    cli("close", SLUG)  # runs the install check on the broken file
    assert "does not install" in _context(cli.new_session("s2"))


def test_a_fact_that_releases_a_hold_is_not_carried_to_the_next_goal(project):
    p, cli = project
    _ready(p, cli)
    assert cli("note", SLUG, "--fact", "install check: the environment supplies setup.sh")[0] == 0
    assert cli("note", SLUG, "--fact", "toolpkg/reader.py Reader.read raises NotImplementedError by design")[0] == 0
    assert cli("note", SLUG, "--fact", "The sandbox refuses to bind a port")[0] == 0
    _complete(cli)
    out, _ = _start_with(p, cli, SPEC, name="g2", objective="Carry the tool on")
    kept = [f["text"] for f in json.loads(
        (p / ".claude" / "harness" / "goals" / f"{out['slug']}.json").read_text()).get("facts") or []]
    assert kept == ["The sandbox refuses to bind a port"], kept


def test_a_module_passed_to_a_function_is_not_an_object_named_like_the_package():
    # NL2Repo fsutil: these two calls dropped 42 real functions from the surface check
    spec = ("```python\nimport fsutil\nresult = getattr(fsutil, operation)(*args)\n"
            "for name, obj in inspect.getmembers(fsutil):\n    pass\n```\n"
            "`fsutil.copy_file(path, dest)` copies a file and `fsutil.list_dirs(path)` lists folders.\n")
    found = surface.refs(spec, "fsutil")
    assert "fsutil.copy_file" in found and "fsutil.list_dirs" in found, found
    # with `import pkg` in its code, a spec that passes the package on is passing the module
    spec = "```python\nimport fsutil\nwatch(fsutil, depth=2)\n```\n`fsutil.copy_file(path, dest)` copies a file.\n"
    assert "fsutil.copy_file" in surface.refs(spec, "fsutil")


def test_a_name_that_only_looks_like_a_message_code_stays():
    found = surface.refs("`six.PY34` is true on Python 3.4+. `six.crypto.X509` and `six.SHA256` are classes; "
                         "`six.E001` is reported when the check fails.", "six")
    assert {"six.PY34", "six.crypto.X509", "six.SHA256"} <= set(found) and "six.E001" not in found, found


def test_a_fact_that_says_a_name_is_still_to_build_does_not_take_it_off_the_list():
    names = ["Reader", "parse_header", "Widget"]
    for fact in ("parse_header is not built yet; Reader waits for it",
                 "The timeout option belongs to Reader, set in its constructor",
                 "Widget uses the spec's notation for sizes",
                 "Reader is not code I have run yet"):
        assert unbuilt.dismissed(names, [fact]) == set(), fact
    assert unbuilt.dismissed(names, ["Not ours: Widget (another library's class)"]) == {"Widget"}


def test_a_complete_goal_whose_time_is_spent_does_not_reopen_without_more(project):
    # Reopened with its time limit spent, the goal's next `close` finished it at once, with no request check.
    p, cli = project
    _ready(p, cli)
    _complete(cli)
    path = p / ".claude" / "harness" / "goals" / f"{SLUG}.json"
    state = json.loads(path.read_text())
    state["budget"]["max_minutes"] = 30
    state["used"]["started"] = "2026-01-01T00:00:00+00:00"
    path.write_text(json.dumps(state))
    code, out = cli("reopen", SLUG, "--reason", "More work on the same library")
    assert code != 0 and "--extra-minutes" in str(out), out
    code, out = cli("reopen", SLUG, "--reason", "More work on the same library", "--extra-minutes", "30")
    assert code == 0 and out["status"] == "active", out
    code, out = cli("close", SLUG)
    assert code == 1 and any("part by part" in m for m in out["missing"]), out


def test_start_names_the_goal_completed_last_as_the_session_start_does(project):
    p, cli = project
    _ready(p, cli)
    _complete(cli)
    second, _ = _start_with(p, cli, SPEC, name="g2", objective="Add the reader")  # sorts before build-the-tool
    _complete(cli, second["slug"])
    text = _context(cli.new_session("s2"))
    assert f"reopen {second['slug']}" in text, text
    third, _ = _start_with(p, cli, SPEC, name="g3", objective="Carry the tool on")
    assert f"reopen {second['slug']}" in third["earlier_goal"], third["earlier_goal"]


def test_a_comment_that_gives_a_float_is_not_checked_with_equals():
    spec = "```python\nfrom machines import area\nr = area(2)\n# r == 12.57\n```\n"
    assert examples.extract(spec, "machines")[0] == []
