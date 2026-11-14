"""Two defects read from the E2E-SWE thin15 run (a8c1a6e, 5 tasks, 2026-11-14).

1. a8c1a6e joined short sections into one part and named the part by the heading it starts with. A part before the
   last was given with its lines ("lines 109 to 157") and was read to its end. The last was given as "from line
   158", and its name was one section's heading. In all three builds whose last part held more than one section
   the pass read the first and saved the part: websockets ran lines 158 to 163 of 158 to 199 (Extensions, CLI,
   Exceptions and setup.sh not run), huey 175 to 182 of 175 to 229 (Serializer, Storage Backends, Exceptions,
   Contrib Modules), fiddle 282 to 295 of 282 to 357 (Serialization, Code generation, auto_config).
2. protobuf-to-pydantic ran no `check` in its first five sessions. Each of sessions 2 to 6 was told "Next: not
   set" and no more, read the request and every source file again, and made its first edit at call 9 to 13 of
   15 (session 4 made none). Over the thin13 to thin15 runs, 24 of the 64 building sessions after the first began
   with no next action; their first edit came at call 10 at the median, against 7 with one. Until the first
   `check`, none of Horizon's checks of the work has run either.
"""
import json
import re

from installable_project import installable
from test_e2eswe_fixes import project  # noqa: F401  (the fixture is used by name)
from test_thin import SLUG, _build, _context, _thin
from test_thin13_fixes import _save
from test_thin14_fixes import MANY_SHORT
from test_transcript_fixes import _calls, _start_with

import goal
import sections


# --- 1. the lines of a part, the last one too -----------------------------------------------------------------------

def _ranges(message):
    """[(number, first line, last line)] of the parts a request-check message lists
    (`SPEC.md: 1 Build (lines 1 to 44), 2 Parsing (lines 45 to 90)`)."""
    return [(int(n), int(first), int(last))
            for n, first, last in re.findall(r"(?:: |, )(\d+) [^,;:]*?\(lines (\d+) to (\d+)\)", message)]


def _asked(p, cli, spec=MANY_SHORT):
    cli.new_session("s1")
    _start_with(p, cli, spec)
    _build(p)
    installable(p)
    code, out = cli("close", SLUG)
    asked = [m for m in out["missing"] if "part by part" in m]
    assert code == 1 and asked, out
    _calls(cli, "s1", 1)  # the `close` itself
    return asked[0]


def test_a_part_ends_on_the_line_before_the_next_and_the_last_on_the_last_line():
    assert sections.part_ends([(1, "A"), (45, "B"), (90, "C")], 130) == [44, 89, 130]
    assert sections.part_ends([(7, "Only")], 7) == [7]
    assert sections.part_ends([], 10) == []


def test_every_part_is_listed_with_its_lines_and_the_last_runs_to_the_end_of_the_file(project):
    p, cli = project
    listed = _ranges(_asked(p, cli))
    last_line = len(MANY_SHORT.splitlines())
    assert len(listed) >= 3, listed
    assert listed[0][1] == 1 and listed[-1][2] == last_line, (listed, last_line)
    assert all(earlier[2] + 1 == later[1] for earlier, later in zip(listed, listed[1:])), listed
    start = listed[-1][1]
    inside = [n for n, line in enumerate(MANY_SHORT.splitlines(), 1) if line.startswith("#") and start < n]
    assert inside, "the last part holds more than one headed section"


def test_the_session_start_gives_the_last_part_to_its_last_line(project):
    p, cli = project
    listed = _ranges(_asked(p, cli))
    for number, _, _ in listed[:-1]:
        _calls(cli, "s1", 1)  # a script
        _save(cli, str(number))
    text = _context(cli.new_session("s2"))
    number, first, last = listed[-1]
    assert f"start with part {number} (SPEC.md lines {first} to {last})" in text, text
    assert "from line" not in text, text
    assert f"(lines {first} to {last})" in text, "and the list of parts left says the same"


def test_the_ask_says_a_part_is_more_than_the_heading_it_is_named_by():
    assert "named by the heading it starts with" in goal.RUN_A_PART
    assert "whatever headings lie between" in goal.RUN_A_PART


def test_a_goal_asked_before_the_last_lines_were_kept_is_still_listed(project):
    p, cli = project
    _asked(p, cli)
    path = p / ".claude" / "harness" / "goals" / f"{SLUG}.json"
    state = json.loads(path.read_text())
    for part in state["request_check"]["parts"]:
        part.pop("end", None)
    path.write_text(json.dumps(state))
    assert "(line 1)" in _context(cli.new_session("s2")), "the session start still lists them"
    listed = goal._numbered(state["request_check"]["parts"])
    assert "(line 1)" in listed and "lines" not in listed, listed
    later = state["request_check"]["parts"][1]["line"]
    assert goal.next_part(state) == f"part 1 (SPEC.md lines 1 to {later - 1})"
    state["request_check"]["done"] = list(range(1, len(state["request_check"]["parts"])))
    assert "from line" in goal.next_part(state)


# --- 2. a goal no `check` has saved ----------------------------------------------------------------------------------

NOT_SAVED = "No `check` has saved this goal yet"


def test_a_session_is_told_when_no_check_has_saved_the_goal(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)  # session 1 ends here, before any `check`
    text = _context(cli.new_session("s2"))
    assert "Next: not set." in text and NOT_SAVED in text, text
    assert "run `check` as soon as something you built this session runs" in text, text
    assert "`--next`" in text, text


def test_once_a_check_has_saved_it_the_session_is_told_the_next_action_and_no_more(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)
    assert cli("check", SLUG, "--did", "Wrote the parser", "--next", "Write reader.py: Reader.lines")[0] == 0
    text = _context(cli.new_session("s2"))
    assert "Next: Write reader.py: Reader.lines" in text and NOT_SAVED not in text, text


def test_while_the_request_check_is_under_way_only_the_pass_is_asked_for(project):
    p, cli = project
    _asked(p, cli)  # `close` asked, and no `check` has run
    text = _context(cli.new_session("s2"))
    assert "start with part 1" in text and NOT_SAVED not in text, text
