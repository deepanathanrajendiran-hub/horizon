"""The request check at `close`, and the narrower "by design" note (E2E-SWE thin4 runs, 2026-11-10: exp/thin a461068,
one build of each of 31 tasks).

14 of 31 builds closed their goal with hidden tests still failing and sessions left; plain did that in 8 of 62.
websockets closed after 4 of 10 sessions at 27 of 35 hidden tests with its own 25 tests passing, and pgqueuer after
4 at 19 of 21. The gate is the build's own tests, so a passing gate agrees with the build's reading of the request.
The first `close` that would complete a thin goal now asks for the work to be gone through against the request, part
by part. `check --parts` saves the parts that were run, and `close` completes when none is left (thin12 run: any
`check` used to release it, and protobuf-to-pydantic closed without the pass).

mapillary_tools recorded its last two unbuilt functions as "by design" and closed: the note now covers only the
functions it names."""
import re
import subprocess

import pytest

from installable_project import installable
from test_e2eswe_fixes import project  # noqa: F401  (the fixture is used by name)
from test_thin import SLUG, _build, _context, _thin

import sections
import stubs


def _ready(p, cli):
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)
    installable(p)


def _asked(out):
    return [m for m in out.get("missing") or [] if "part by part" in m]


# --- the first close that would complete -----------------------------------------------------------------------

def test_the_first_passing_close_asks_for_the_request_to_be_gone_through(project):
    p, cli = project
    _ready(p, cli)
    code, out = cli("close", SLUG)
    assert code == 1 and not out["complete"], out
    asked = _asked(out)
    assert asked and "SPEC.md: " in asked[0] and "Parsing (lines " in asked[0], out


def test_a_second_close_with_no_check_between_asks_again(project):
    p, cli = project
    _ready(p, cli)
    assert cli("close", SLUG)[0] == 1
    code, out = cli("close", SLUG)
    assert code == 1 and "still to run" in out["missing"][0] and "Parsing (lines " in out["missing"][0], out


def _numbers(message):
    """The part numbers a request-check message lists (`SPEC.md: 1 Build (lines 1 to 4), 2 Parsing (lines 5 to
    9)`)."""
    return [int(n) for n in re.findall(r"(?:: |, )(\d+) [^,;:]*?\(lines? \d+(?: to \d+)?\)", message)]


def test_a_check_that_names_every_part_lets_close_complete(project):
    p, cli = project
    _ready(p, cli)
    code, out = cli("close", SLUG)
    numbers = _numbers(_asked(out)[0])
    assert code == 1 and len(numbers) >= 2 and numbers == list(range(1, len(numbers) + 1)), out
    code, out = cli("check", SLUG, "--did", "Ran Parsing: parse('a') gives a", "--next", "Close",
                    "--parts", f"1-{len(numbers)}")
    assert code == 0 and "request_check" not in out, out
    code, out = cli("close", SLUG)
    assert code == 0 and out["complete"], out


def test_a_check_that_names_no_part_does_not_release_it(project):
    # thin12 run, protobuf-to-pydantic (52.4): `close` asked at session 5 with few calls left; the build went on
    # with its own tests, saved an ordinary `check` in session 7 whose next action was still "close-check spec
    # parts", and ran `close` "to see which spec parts remain". It completed: any `check` after the ask released
    # it. Of 44 builds asked since the thin5 run, 9 ran the work at most once before the releasing `check`.
    p, cli = project
    _ready(p, cli)
    assert cli("close", SLUG)[0] == 1
    code, out = cli("check", SLUG, "--did", "Fixed a test fixture", "--next", "Close-check the spec parts")
    assert code == 0 and "still to run" in out["request_check"], out
    code, out = cli("close", SLUG)
    assert code == 1 and not out["complete"] and "still to run" in out["missing"][0], out


def test_close_then_lists_only_the_parts_still_to_run(project):
    p, cli = project
    _ready(p, cli)
    first = _asked(cli("close", SLUG)[1])[0]
    numbers = _numbers(first)
    code, out = cli("check", SLUG, "--did", "Ran part 1", "--next", "Run the rest", "--parts", "1")
    assert code == 0 and _numbers(out["request_check"]) == numbers[1:], out
    code, out = cli("close", SLUG)
    assert code == 1 and _numbers(out["missing"][0]) == numbers[1:], out
    assert len(out["missing"][0]) < len(first), "the second message is the list, not the whole instruction again"
    assert _numbers(_context(cli.new_session("s2"))) == numbers[1:]
    cli("check", SLUG, "--did", "Ran the rest", "--next", "Close", "--parts", ",".join(map(str, numbers[1:])))
    code, out = cli("close", SLUG)
    assert code == 0 and out["complete"], out


def test_part_numbers_are_ranges_and_lists_and_nothing_else(project):
    p, cli = project
    _ready(p, cli)
    assert cli("close", SLUG)[0] == 1
    code, out = cli("check", SLUG, "--did", "Ran them", "--next", "Close", "--parts", "every one")
    assert code != 0 and "--parts" in str(out), out
    # a number past the end of the list names nothing
    code, out = cli("check", SLUG, "--did", "Ran them", "--next", "Close", "--parts", "40-45")
    assert code == 0 and "still to run" in out["request_check"], out


def test_a_goal_asked_before_parts_were_kept_is_released_by_its_next_check():
    import goal
    asked = {"status": "active", "used": {"turns": 3}, "request_check": {"at": "x", "turn": 3}}
    assert goal.request_check_open(asked)
    assert not goal.request_check_open({**asked, "used": {"turns": 4}})


def test_a_check_before_the_first_close_does_not_count(project):
    # The check has to follow the ask: an ordinary checkpoint during the work is not the request check.
    p, cli = project
    _ready(p, cli)
    assert cli("check", SLUG, "--did", "Built parse and Reader", "--next", "Close")[0] == 0
    code, out = cli("close", SLUG)
    assert code == 1 and _asked(out), out


def test_another_hold_is_reported_before_the_request_check(project):
    # A missing name or a placeholder is the more useful thing to hear first, and the ask is not spent on it.
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p, reader=False)
    installable(p)
    code, out = cli("close", SLUG)
    assert code == 1 and not _asked(out), out
    assert cli("check", SLUG, "--did", "Looked at the reader", "--next", "Build Reader")[0] in (0, 1)
    _build(p)
    code, out = cli("close", SLUG)
    assert code == 1 and _asked(out), out


def test_the_stop_hooks_close_neither_completes_nor_starts_the_check(project):
    p, cli = project
    _ready(p, cli)
    assert cli("check", SLUG, "--did", "Built parse and Reader", "--next", "Close")[0] == 0
    code, out = cli("close", SLUG, "--if-ready")
    assert not out["complete"], out
    # Not started: the explicit close still asks, and a check saved before it does not release it.
    code, out = cli("close", SLUG)
    assert code == 1 and _asked(out), out


def test_a_request_with_no_headed_parts_closes_at_once(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    (p / "SPEC.md").write_text(
        "`toolpkg.parse(line)` returns the line. `toolpkg.reader.Reader` reads files. Install it with "
        "`pip install -e .`.\n")
    _build(p)
    installable(p)
    code, out = cli("close", SLUG)
    assert code == 0 and out["complete"], out


def test_a_goal_with_requirement_lines_is_not_asked(project):
    # Only thin goals: one with requirement lines has a reviewer for this.
    import goal
    state = {"thin": False, "status": "active", "budget": {}, "used": {"turns": 0}}
    assert goal.request_check(state) is None


# --- a session that starts in the middle of it -----------------------------------------------------------------

def test_the_next_session_is_told_the_check_is_under_way(project):
    p, cli = project
    _ready(p, cli)
    assert cli("close", SLUG)[0] == 1
    context = _context(cli.new_session("s2"))
    assert "gone through against the request" in context and "Parsing (lines " in context, context


def test_the_session_is_not_told_once_a_check_answered(project):
    p, cli = project
    _ready(p, cli)
    assert cli("close", SLUG)[0] == 1
    assert cli("check", SLUG, "--did", "Ran each part", "--next", "Close", "--parts", "1-99")[0] == 0
    assert "gone through against the request" not in _context(cli.new_session("s2"))


def test_no_session_is_told_before_any_close(project):
    p, cli = project
    _ready(p, cli)
    assert "gone through against the request" not in _context(cli.new_session("s2"))


# --- the parts listed ------------------------------------------------------------------------------------------

def test_a_short_input_is_listed_by_every_section():
    text = "# Tool\n\n## Parsing\n\ntext\n\n### Errors\n\ntext\n\n## Output\n"
    assert [heading for _, heading in sections.outline(text)] == ["Tool", "Parsing", "Errors", "Output"]


def test_a_long_input_is_listed_by_its_chapters():
    text = "# Tool\n\n" + "".join(
        f"## Chapter {c}\n\n" + "".join(f"### Function {c}.{f}\n\ntext\n\n" for f in range(12)) for c in range(5))
    listed = [heading for _, heading in sections.outline(text, limit=30)]
    assert listed == ["Tool"] + [f"Chapter {c}" for c in range(5)]


def test_a_heading_inside_a_code_block_is_not_a_part():
    text = "# Tool\n\n```bash\n# install\npip install tool\n```\n\n## Use\n"
    assert [heading for _, heading in sections.outline(text)] == ["Tool", "Use"]


def test_line_numbers_point_at_the_headings():
    text = "# Tool\n\ntext\n\n## Use\n"
    assert sections.outline(text) == [(1, "Tool"), (5, "Use")]


# --- "by design" covers the functions it names -----------------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "work"
    (root / "pkg").mkdir(parents=True)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    (root / "pkg" / "upload.py").write_text(
        "class Service:\n    def fetch_offset(self):\n        raise NotImplementedError\n\n"
        "    def upload_chunks(self):\n        raise NotImplementedError\n\n"
        "def on_progress(done):\n    raise NotImplementedError\n")
    return root


def _names(root, facts=()):
    return {name: hits for name, hits in stubs.find(root, facts)}


def test_a_note_that_names_only_the_file_covers_nothing(repo):
    # mapillary_tools: a file-wide note would have waved through every function in the file.
    found = _names(repo, ["pkg/upload.py raises NotImplementedError by design"])
    assert found == {"pkg/upload.py": ["on_progress", "Service.fetch_offset", "Service.upload_chunks"]}, found


def test_a_note_covers_the_function_it_names_and_no_other(repo):
    found = _names(repo, ["pkg/upload.py on_progress raises NotImplementedError by design"])
    assert found == {"pkg/upload.py": ["Service.fetch_offset", "Service.upload_chunks"]}, found


def test_a_method_is_named_with_or_without_its_class(repo):
    facts = ["pkg/upload.py Service.fetch_offset raises NotImplementedError by design",
             "pkg/upload.py upload_chunks() and on_progress raise NotImplementedError by design"]
    assert _names(repo, facts) == {}


def test_a_note_about_another_file_covers_nothing_here(repo):
    found = _names(repo, ["pkg/other.py on_progress raises NotImplementedError by design"])
    assert "on_progress" in found["pkg/upload.py"], found


def test_the_file_name_is_not_read_as_a_function_name(repo):
    (repo / "pkg" / "upload.py").write_text("def upload():\n    raise NotImplementedError\n")
    assert _names(repo, ["pkg/upload.py raises NotImplementedError by design"]) == {"pkg/upload.py": ["upload"]}

# --- what the check asks for -----------------------------------------------------------------------------------

def test_the_check_asks_for_printed_output_and_for_the_code_to_change(project):
    # thin5 run: sessions turned the check into new tests and, where a test disagreed with the code, changed the
    # test (django-modelcluster twice, dnachisel twice; dnachisel gained one hidden test in five sessions).
    p, cli = project
    _ready(p, cli)
    asked = _asked(cli("close", SLUG)[1])[0]
    assert "prints what comes back" in asked and "no expected values" in asked, asked
    assert "change the code, never a test's expectation" in asked and "one pass" in asked, asked
    context = _context(cli.new_session("s2"))
    assert "never a test's expectation" in context, context
