"""One defect read from the E2E-SWE thin16 run (9519263, 5 tasks, 2026-11-14).

sapp's first session wrote the whole SPEC.md (357 lines) into the goal's request.md, under one line of its own
("Build the product described in SPEC.md."). The request check lists the parts of request.md and of each Markdown
file it names, so it listed the same 7 parts twice, and the pass ran all 14: 8 sessions and 16 of the goal's 20
turns, where the thin15 build of the task (a one-line request.md) took 7 parts and 6 sessions. 23 of the 735
requests saved over all the runs hold such a copy, each of them the whole of the file.
"""
import json
import re
import shlex
import sys

from installable_project import installable
from test_e2eswe_fixes import project  # noqa: F401  (the fixture is used by name)
from test_thin import PASSING, SLUG, _build
from test_thin14_fixes import MANY_SHORT

import goal
import sections

OWN_LINE = "Build the product described in SPEC.md.\n\n"


def _gate(p, request, spec=MANY_SHORT):
    """A project whose SPEC.md is `spec` and a gate folder whose request.md is `request`."""
    (p / "SPEC.md").write_text(spec)
    gates = p / ".claude" / "gates" / "g"
    gates.mkdir(parents=True, exist_ok=True)
    (gates / "request.md").write_text(request)
    return gates


def _ranges(p, gates, at_start=None):
    return sections.request_part_ranges(str(gates), str(p), at_start, floor=goal.PART_LINES)


def test_a_copy_is_blanked_and_every_other_line_keeps_its_number():
    spec = "# Tool\n\nIt parses.\n\n## Use\n\nCall it.\n"
    text = "Build what SPEC.md says.\n\n" + spec + "\n## Also\n\nKeep the log.\n"
    left = sections.without_copies(text, [spec]).splitlines()
    assert left[0] == "Build what SPEC.md says." and left[2:9] == [""] * 7, left
    assert left.index("## Also") == text.splitlines().index("## Also"), "the lines after the copy keep their numbers"


def test_a_request_with_no_copy_is_left_as_it_is():
    text = "Build what SPEC.md says.\n\n# Tool\n\nIt parses lines.\n"
    assert sections.without_copies(text, ["# Tool\n\nIt reads files.\n"]) is text
    assert sections.without_copies(text, [""]) is text


def test_lines_that_only_begin_like_the_file_are_no_copy_of_it():
    # a one-line NOTES.md, and a request whose own section has the same heading and a body under it
    text = "# Notes\nKeep every warning.\n"
    assert sections.without_copies(text, ["# Notes\n"]) is text
    # followed by a blank line, a heading or the end, it is a copy
    assert sections.without_copies("# Notes\n\n# Tool\n", ["# Notes\n"]) != "# Notes\n\n# Tool\n"


def test_spaces_at_line_ends_and_blank_lines_around_the_file_do_not_hide_a_copy():
    spec = "\n# Tool  \n\nIt parses.\n\n\n"
    text = "See SPEC.md.\n# Tool\n\nIt parses.   \n"
    assert sections.without_copies(text, [spec]).strip() == "See SPEC.md."


def test_the_parts_of_a_copied_file_are_listed_once_under_the_file(tmp_path):
    plain = _ranges(tmp_path, _gate(tmp_path, "Build the product described in SPEC.md.\n"))
    copied = _ranges(tmp_path, _gate(tmp_path, OWN_LINE + MANY_SHORT))
    assert [name for name, _ in plain] == ["SPEC.md"] and len(plain[0][1]) >= 3, plain
    assert copied == plain, "the copy in request.md adds no part"


def test_sections_of_its_own_beside_the_copy_are_still_parts_and_end_where_they_end(tmp_path):
    own = "# House rules\n\n" + "Every error names the line it came from.\n" * 44
    gates = _gate(tmp_path, own + "\nBuild the product described in SPEC.md.\n\n" + MANY_SHORT)
    listed = _ranges(tmp_path, gates)
    assert [name for name, _ in listed] == ["request.md", "SPEC.md"], listed
    assert listed[0][1] == [(1, 48, "House rules")], "not to the end of the file, through the blanked copy"
    assert listed[1] == _ranges(tmp_path, _gate(tmp_path, "Build the product described in SPEC.md.\n"))[0]


def test_a_copy_of_a_file_the_work_has_since_changed_stays_in_the_request(tmp_path):
    # a named file that changed since the goal started is an output of the work and is not walked: then the
    # request's copy of it as it was is the only one
    gates = _gate(tmp_path, OWN_LINE + MANY_SHORT)
    at_start = {str(tmp_path / "SPEC.md"): sections._digest(MANY_SHORT)}
    (tmp_path / "SPEC.md").write_text(MANY_SHORT + "\n## Changelog\n\nAdded by the work.\n")
    listed = _ranges(tmp_path, gates, at_start)
    assert [name for name, _ in listed] == ["request.md"] and len(listed[0][1]) >= 3, listed


def test_close_asks_for_each_part_of_a_copied_request_once(project):
    p, cli = project
    cli.new_session("s1")
    gates = _gate(p, OWN_LINE + MANY_SHORT)
    (p / "tests").mkdir(exist_ok=True)
    (p / "tests" / "test_tool.py").write_text(
        PASSING + "".join(f"\n\ndef test_parse_case_{i}():\n    assert True\n" for i in range(1, 10)))
    command = f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider tests"
    assert cli("start", "Build the tool", "--gate-path", str(gates), "--gate", command)[0] == 0
    _build(p)
    installable(p)
    code, out = cli("close", SLUG)
    asked = [m for m in out["missing"] if "part by part" in m]
    assert code == 1 and asked, out
    numbers = [int(n) for n in re.findall(r"(?:: |, )(\d+) [^,;:]*?\(lines \d+ to \d+\)", asked[0])]
    assert "request.md:" not in asked[0] and "SPEC.md: 1 " in asked[0], asked[0]
    assert numbers == list(range(1, len(numbers) + 1)) and 3 <= len(numbers) <= 5, numbers
    state = json.loads((p / ".claude" / "harness" / "goals" / f"{SLUG}.json").read_text())
    assert {part["file"] for part in state["request_check"]["parts"]} == {"SPEC.md"}, "and it keeps them once"
