"""Three defects read from the E2E-SWE thin13 run (11dc6c7, 10 tasks, 2026-11-13).

1. websockets, 27 of 35: the request's heading reads "Sans-I/O Protocol (`websockets.protocol`, `.server`,
   `.client`)". The surface list held `websockets.protocol` and neither of the other two, and the build put both
   classes in `protocol.py`. Of twelve graded builds of the task, the ten with `websockets/server.py` and
   `websockets/client.py` passed 32 to 35 hidden tests; the two without them passed 27.
2. Four of the nine builds that were asked for the request check saved every part with `check --parts` calls made
   one after another, nothing run between them (sapp: three scripts for 17 parts, closed in session 3).
3. protobuf-to-pydantic, sessions 8 to 10: with the request check under way the session start also said
   "Plateau ... Build this session; do not start a review" and "change a file of the work within your first 5
   tool calls", and its shorter form of the check left out "not a new set of tests". The build wrote a 648-line
   test file for the parts and ran none of them.
"""
import re

from installable_project import installable
from test_e2eswe_fixes import project  # noqa: F401  (the fixture is used by name)
from test_thin import SLUG, _build, _context
from test_transcript_fixes import LONG_SPEC, _calls, _start_with

import surface


# --- 1. a module written short after one written in full ----------------------------------------------------------

def test_a_module_written_short_after_a_full_one_is_a_name_of_the_package():
    spec = "# websockets\n\n## Sans-I/O Protocol (`websockets.protocol`, `.server`, `.client`)\n\n`Protocol`: ...\n"
    found = surface.refs(spec, "websockets")
    assert {"websockets.protocol", "websockets.server", "websockets.client"} <= set(found), found


def test_a_short_name_is_read_from_the_module_the_full_one_is_in():
    spec = "# pkg\n\nThe helpers live in `pkg.markup.tokens`, `.macros` and `.aliases`; `pkg`, `.cli` too.\n"
    found = set(surface.refs(spec, "pkg"))
    assert {"pkg.markup.tokens", "pkg.markup.macros", "pkg.markup.aliases", "pkg.cli"} <= found, found
    assert "pkg.macros" not in found and "pkg.markup.tokens.macros" not in found, found


def test_a_dotted_word_with_no_full_name_before_it_is_not_a_name_of_the_package():
    spec = ("# pkg\n\nA connection has `.send()` and `.recv()`; an enum member has `.name`, `.value`.\n"
            "`pkg.connect` returns one whose `.state` is open. Settings are read from `pkg.settings`, `.json` "
            "or `.toml` files.\nCall `pkg.load`, `.dump()` after it.\n")
    found = set(surface.refs(spec, "pkg"))
    assert not found & {"pkg.send", "pkg.recv", "pkg.name", "pkg.value", "pkg.state", "pkg.json", "pkg.toml",
                        "pkg.dump"}, found


# --- 2. a part of the request check takes a call --------------------------------------------------------------------

def _asked(p, cli, session="s1"):
    cli.new_session(session)
    _start_with(p, cli, LONG_SPEC)
    _build(p)
    installable(p)
    code, out = cli("close", SLUG)
    assert code == 1 and any("part by part" in m for m in out["missing"]), out
    _calls(cli, session, 1)  # the `close` itself, counted when it returns


def _left(out):
    return int(re.search(r"(\d+) of (\d+) parts of the request are still to run", out["request_check"]).group(1))


def _total(out):
    return int(re.search(r"(\d+) of (\d+) parts of the request are still to run", out["request_check"]).group(2))


def _save(cli, parts, session="s1"):
    code, out = cli("check", SLUG, "--did", "Ran them", "--next", "The next part", "--parts", parts)
    assert code == 0, out
    _calls(cli, session, 1)  # the `check` itself
    return out


def test_a_check_saves_no_more_parts_than_the_calls_made_since_close_asked(project):
    p, cli = project
    _asked(p, cli)
    _calls(cli, "s1", 3)  # three scripts
    out = _save(cli, "1-8")
    assert _left(out) == _total(out) - 3, out
    assert "saved 3 of the parts it named and left 5" in out["request_check"], out


def test_parts_saved_one_after_another_with_nothing_run_between_are_not_saved(project):
    p, cli = project
    _asked(p, cli)
    _calls(cli, "s1", 8)
    first = _save(cli, "1-8")
    assert _left(first) == _total(first) - 8, first
    second = _save(cli, "9-16")  # the next call, as sapp, huey, fiddle and pgqueuer made it
    assert _left(second) == _left(first), second
    assert "saved 0 of the parts it named" in second["request_check"], second
    code, out = cli("close", SLUG)
    assert code == 1 and not out["complete"], out
    _calls(cli, "s1", 2)  # that `close`, and one script
    third = _save(cli, "9-16")
    assert _left(third) == _left(first) - 2, third


def test_two_saves_on_one_command_line_save_once(project):
    p, cli = project
    _asked(p, cli)
    _calls(cli, "s1", 4)
    code, first = cli("check", SLUG, "--did", "Ran them", "--next", "More", "--parts", "1-4")
    code, second = cli("check", SLUG, "--did", "Ran them", "--next", "More", "--parts", "5-8")  # no call between
    assert _left(second) == _left(first) == _total(first) - 4, second


def test_a_later_session_counts_its_own_calls(project):
    p, cli = project
    _asked(p, cli)
    _calls(cli, "s1", 5)
    first = _save(cli, "1-5")
    cli.new_session("s2")
    _calls(cli, "s2", 2)  # the skill, then one script
    second = _save(cli, "6-12", session="s2")
    assert _left(second) == _left(first) - 2, second


def test_without_a_count_of_calls_only_the_cap_applies(project):
    p, cli = project
    cli.new_session("s1")
    _start_with(p, cli, LONG_SPEC)
    _build(p)
    installable(p)
    assert cli("close", SLUG)[0] == 1
    code, out = cli("check", SLUG, "--did", "Ran them all", "--next", "Close", "--parts", "1-99")  # no call counted
    assert _left(out) == _total(out) - 8, out


# --- 3. the session start while the request check is under way -------------------------------------------------------

def test_the_session_start_sends_a_session_to_the_next_part_and_its_lines(project):
    p, cli = project
    _asked(p, cli)
    _calls(cli, "s1", 2)
    _save(cli, "1-2")
    text = _context(cli.new_session("s2"))
    lines = LONG_SPEC.splitlines()
    headings = [n for n, line in enumerate(lines, 1) if line.startswith("#")]
    assert f"start with part 3 (SPEC.md lines {headings[2]} to {headings[3] - 1})" in text, text
    assert "not a new set of tests" in text and "do not write the pass as a test file" in text, text
    assert "no more parts than the tool calls made since the last save" in text, text


def test_no_line_sends_a_session_to_build_while_the_request_check_is_under_way(project):
    p, cli = project
    _asked(p, cli)
    said = [_context(cli.new_session(f"s{n}")) for n in (2, 3, 4, 5)]  # no file changes, one next action
    for text in said:
        assert "still to run" in text, text
        assert "Plateau" not in text, text
        assert "change a file of the work within your first 5 tool calls" not in text, text
        assert "has now opened" not in text, text


def test_those_lines_come_back_when_the_request_check_is_done(project):
    p, cli = project
    _asked(p, cli)
    for n in (2, 3, 4):
        cli.new_session(f"s{n}")
    total = _total(cli("check", SLUG, "--did", "Looked", "--next", "The next part")[1])
    done = 0
    while done < total:  # no call is counted in this test, so each `check` saves the cap
        cli("check", SLUG, "--did", "Ran them", "--next", "The next part", "--parts", f"{done + 1}-{done + 8}")
        done += 8
    text = _context(cli.new_session("s5"))
    assert "still to run" not in text, text
    assert "change a file of the work within your first 5 tool calls" in text, text


def test_close_says_how_a_part_is_run_while_the_check_is_under_way(project):
    p, cli = project
    _asked(p, cli)
    code, out = cli("close", SLUG)
    under_way = [m for m in out["missing"] if "under way" in m]
    assert code == 1 and under_way, out
    assert "not a new set of tests" in under_way[0] and "no expected values" in under_way[0], under_way
