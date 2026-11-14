"""Two defects read from the E2E-SWE thin14 run (f616abb, 5 tasks, 2026-11-13).

1. fiddle, 0 of 58: the request said "its setup.sh is run there to install the project", and its SPEC.md "do not
   write any install or network steps yourself". 11dc6c7 let a recorded fact answer for a setup script the request
   names and the project lacks. Session 3 recorded one, `close` stopped waiting, and sessions 4 to 10 each read
   "the request names setup.sh, and the project has none" and left it. 11dc6c7's own build of the task wrote the
   script and scored 100.
2. The request check, one part saved per `check`, took every session left after `close` asked: 35 sessions over
   the 5 builds, about two parts a session, 4 builds still in it at the last session, one at 19 of its 20 turns.
   The parts were the request's sections as headed: 17 to 22 a request, 12 lines at the median. Of 215 saved
   requests, 76 had as many parts as a goal has turns.
"""
import re

from installable_project import installable
from test_e2eswe_fixes import project  # noqa: F401  (the fixture is used by name)
from test_thin import SLUG, SPEC, _build, _context, _thin
from test_transcript_fixes import _calls, _start_with

import goal
import install_gate
import sections


# --- 1. a setup script the request names and the project lacks -----------------------------------------------------

def test_no_fact_answers_for_a_missing_setup_script(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)  # no setup.sh
    assert cli("note", SLUG, "--fact", "install check: SPEC.md says the project is built and installed by a "
               "setup.sh that the environment runs offline; do not write install or network steps")[0] == 0
    code, out = cli("close", SLUG)
    held = [m for m in out["missing"] if "does not install" in m]
    assert code == 1 and not out["complete"] and held, out
    assert "setup.sh" in held[0] and "write it" in held[0], held
    code, out = cli("check", SLUG, "--did", "Recorded the fact", "--next", "Go on")
    assert "`close` waits for it" in out["install_problem"], out
    assert "A recorded fact answers" not in out["install_problem"], out


def test_the_missing_script_is_said_with_its_reason_wherever_it_is_reported(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)
    cli("close", SLUG)
    said = _context(cli.new_session("s2"))
    for text in (said, install_gate.problem({"missing_scripts": ["setup.sh"], "scripts": [], "pyproject": True})):
        assert "the project has none: write it" in text, text
        assert "something else runs it" in text and "install nothing yourself" in text, text
        assert "no recorded fact answers for a missing file" in text, text


def test_writing_the_script_ends_the_hold(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)
    code, out = cli("close", SLUG)
    assert any("does not install" in m for m in out["missing"]), out
    installable(p)  # pyproject.toml and a setup.sh that installs
    code, out = cli("close", SLUG)
    assert not any("does not install" in m for m in out["missing"]), out


def test_a_refused_script_is_still_answered_by_a_fact_but_not_beside_a_missing_one():
    refused = {"scripts": ["setup.sh"], "script_exit": 1, "pyproject": True,
               "script_output": "mktemp: mkdtemp failed: Operation not permitted"}
    state = {"facts": [{"text": "install check: mktemp was refused by the sandbox"}]}
    assert goal.install_waived(state, refused)
    assert "install check:" in install_gate.problem(refused)
    beside = {**refused, "missing_scripts": ["install.sh"]}
    assert not goal.install_waived(state, beside)
    assert "install check:" not in install_gate.problem(beside), "no way out is offered that would not be taken"


# --- 2. the size of a part of the request check ---------------------------------------------------------------------

def _outline(sizes):
    """An outline whose sections have these line counts, and its last line."""
    starts, line = [], 1
    for number, size in enumerate(sizes, 1):
        starts.append((line, f"Section {number}"))
        line += size
    return starts, line - 1


def _sizes(parts, last_line):
    starts = [line for line, _ in parts] + [last_line + 1]
    return [later - earlier for earlier, later in zip(starts, starts[1:])]


def test_short_sections_in_a_row_are_one_part():
    # the sapp request as thin14 had it: 17 headed sections over 357 lines
    outline, last = _outline([14, 19, 18, 12, 27, 18, 27, 7, 10, 14, 22, 5, 26, 17, 39, 33, 49])
    parts = sections.join_short(outline, last, 40)
    assert _sizes(parts, last) == [51, 57, 44, 41, 43, 72, 49], parts
    assert parts[0][1] == "Section 1" and parts[1][1] == "Section 4", "a part keeps the heading it starts with"


def test_a_short_last_section_joins_the_part_before_it():
    outline, last = _outline([45, 50, 7])
    assert _sizes(sections.join_short(outline, last, 40), last) == [45, 57]


def test_sections_already_long_enough_stay_as_they_are():
    outline, last = _outline([40, 120, 64, 41])
    assert sections.join_short(outline, last, 40) == outline


def test_a_short_request_is_still_gone_through_in_two_parts():
    outline, last = _outline([6, 6, 6, 6, 6, 6, 6, 6])  # 48 lines: one part of 40 would leave nothing to walk
    parts = sections.join_short(outline, last, 40)
    assert len(parts) == 2 and _sizes(parts, last) == [24, 24], parts
    one, last = _outline([30])
    assert sections.join_short(one, last, 40) == one


def test_a_heading_that_stands_alone_is_joined_to_nothing():
    outline = [(1, "Tool"), (6, "Use"), (12, "Earlier request for this project (kept by Horizon)"), (16, "Parsing"),
               (22, "Errors")]
    parts = sections.join_short(outline, 30, 40, alone=lambda heading: heading.endswith("(kept by Horizon)"))
    assert [heading for _, heading in parts] == ["Tool", "Earlier request for this project (kept by Horizon)",
                                                 "Parsing"], parts


MANY_SHORT = SPEC + "".join(f"\n## Step {i}\n\n`toolpkg.parse(line)` again.\n" + "It reads one line at a time.\n" * 7
                            for i in range(1, 19))  # 18 sections of 11 lines


def _listed(message):
    """[(number, line)] of the parts a request-check message lists (`SPEC.md: 1 Build (line 1), 2 Parsing (line 5)`)."""
    return [(int(n), int(line)) for n, line in re.findall(r"(?:: |, )(\d+) [^,;:]*?\(line (\d+)\)", message)]


def test_close_asks_for_parts_of_forty_lines_not_for_every_heading(project):
    p, cli = project
    cli.new_session("s1")
    _start_with(p, cli, MANY_SHORT)
    _build(p)
    installable(p)
    code, out = cli("close", SLUG)
    asked = [m for m in out["missing"] if "part by part" in m]
    assert code == 1 and asked, out
    listed = _listed(asked[0])
    headings = sum(1 for line in MANY_SHORT.splitlines() if line.startswith("#"))
    assert headings >= 18 and 2 <= len(listed) <= len(MANY_SHORT.splitlines()) // goal.PART_LINES, (headings, listed)
    lines = [line for _, line in listed]
    assert all(later - earlier >= goal.PART_LINES for earlier, later in zip(lines, lines[1:])), listed
    assert "whatever headings lie between" in asked[0], asked


def test_the_next_part_runs_to_the_next_part_not_to_the_next_heading(project):
    p, cli = project
    cli.new_session("s1")
    _start_with(p, cli, MANY_SHORT)
    _build(p)
    installable(p)
    code, out = cli("close", SLUG)
    listed = _listed(next(m for m in out["missing"] if "part by part" in m))
    _calls(cli, "s1", 3)  # the `close`, and two scripts
    assert cli("check", SLUG, "--did", "Ran it", "--next", "Part 2", "--parts", "1")[0] == 0
    text = _context(cli.new_session("s2"))
    second, third = listed[1][1], listed[2][1]
    assert f"start with part 2 (SPEC.md lines {second} to {third - 1})" in text, text
    between = [n for n, line in enumerate(MANY_SHORT.splitlines(), 1) if line.startswith("#") and second < n < third]
    assert between, "the part holds more than one headed section"


def test_a_part_is_checked_for_what_its_lines_state():
    assert "what its lines state, not cases they do not mention" in goal.RUN_A_PART
    assert "one at a time" in goal.RUN_A_PART and "not a new set of tests" in goal.RUN_A_PART
