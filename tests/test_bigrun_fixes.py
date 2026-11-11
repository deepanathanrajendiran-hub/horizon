"""Fixes from the E2E-SWE big run (2026-11-10: plain and Horizon exp/thin 197f5c5, 31 tasks, two builds each).

Horizon ended 3.7 points above plain, not a clear difference, and 2.0 below on the 16 tasks with no collapse to
prevent. 24 builds were read against their plain pairs. These are the defects that reading found in Horizon's own
reports; each test names the build it comes from.

- `Placeholders still to build` listed interface methods and deliberate raises in 32 of 62 final builds.
- A name that imports passed the surface test while its function only raised, and `close` completed (authlib a).
- A gate run the host killed left no trace, so four sessions started from the same stale state (mpmath a).
- The session-start message gave 8 of 48 missing names (mpmath a).
- The same next action opened four sessions in a row (django-modelcluster a).
- The surface test was written for a package called `should` (grappa a and b)."""
import json
import os
import subprocess
import sys

import pytest

from installable_project import installable
from test_e2eswe_fixes import project  # noqa: F401  (the fixture is used by name)
from test_thin import SLUG, _build, _close, _context, _thin

import gate
import stubs
import surface


# --- placeholders: what is listed ----------------------------------------------------------------------------

@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "work"
    (root / "pkg").mkdir(parents=True)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)

    def write(name, text):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    return root, write


def _names(root, facts=()):
    return {name: hits for name, hits in stubs.find(root, facts)}


def test_a_function_that_does_work_and_raises_on_one_path_is_not_a_placeholder(repo):
    # invoke a: MockContext._matching_key looked a command up and raised when none was registered.
    root, write = repo
    write("pkg/context.py",
          "def matching_key(table, command):\n    for key in table:\n        if key == command:\n"
          "            return key\n    raise NotImplementedError('No mocked result for %r' % command)\n")
    assert _names(root) == {}


def test_a_function_with_no_body_of_its_own_is_a_placeholder(repo):
    root, write = repo
    write("pkg/parser.py",
          'def parse(text):\n    """Parse ARM assembly."""\n    raise NotImplementedError\n\n'
          "def later(x):\n    '''A placeholder until the translator exists.'''\n    pass\n\n"
          "def hook(x):\n    pass\n")
    assert _names(root) == {"pkg/parser.py": ["parse", "later"]}


def test_a_method_another_class_overrides_is_an_interface(repo):
    # grappa, hojichar, cement, lark, mpmath: `Operator.match`, `Filter.apply`, `_LazyConstant._eval`.
    root, write = repo
    write("pkg/core.py", "class Operator:\n    def match(self, subject):\n        raise NotImplementedError\n\n"
                         "    def explain(self):\n        raise NotImplementedError\n")
    write("pkg/operators/equal.py", "from ..core import Operator\n\n\nclass Equal(Operator):\n"
                                    "    def match(self, subject):\n        return subject == self.expected\n")
    write("pkg/operators/deep.py", "from .equal import Equal\n\n\nclass Deep(Equal):\n"
                                   "    def explain(self):\n        return 'deep'\n")
    assert _names(root) == {}, "match is overridden by a subclass, explain by a subclass of a subclass"


def test_a_method_no_class_overrides_is_still_listed(repo):
    # barf a: ArmParser.parse and ArmTranslator.translate were never built.
    root, write = repo
    write("pkg/arm.py", "class ArmParser:\n    def __init__(self):\n        raise NotImplementedError\n\n"
                        "    def parse(self, text):\n        raise NotImplementedError\n")
    write("pkg/x86.py", "class X86Parser:\n    def parse(self, text):\n        return text\n")
    assert _names(root) == {"pkg/arm.py": ["ArmParser.__init__", "ArmParser.parse"]}


def test_an_override_that_is_itself_a_placeholder_does_not_count(repo):
    root, write = repo
    write("pkg/base.py", "class Reader:\n    def read(self):\n        raise NotImplementedError\n\n\n"
                         "class FileReader(Reader):\n    def read(self):\n        raise NotImplementedError\n")
    assert _names(root) == {"pkg/base.py": ["Reader.read", "FileReader.read"]}


def test_a_special_method_that_raises_is_deliberate(repo):
    # wrapt: ObjectProxy.__copy__ raises by the request's own design.
    root, write = repo
    write("pkg/proxy.py", "class Proxy:\n    def __copy__(self):\n"
                          "        raise NotImplementedError('object proxy must define __copy__()')\n\n"
                          "    def get(self):\n        return 1\n")
    assert _names(root) == {}


def test_the_word_placeholder_in_a_docstring_of_working_code_is_not_a_mark(repo):
    # inline-snapshot: "``snapshot()`` is a placeholder"; lark: "compiled with ``maybe_placeholders=False``".
    root, write = repo
    write("pkg/snap.py", '"""Snapshots. An empty call is a placeholder for a value."""\n\n\n'
                         'def snapshot(value=None):\n    """``snapshot()`` is a placeholder."""\n    return value\n')
    write("pkg/rebuild.py", '"""Needs ``maybe_placeholders=False``."""\n\n\ndef rebuild(tree):\n    return str(tree)\n')
    assert _names(root) == {}


def test_a_module_that_calls_itself_a_placeholder_and_does_nothing_is_listed(repo):
    root, write = repo
    write("pkg/later.py", '"""Placeholder for the language filter."""\n')
    assert _names(root) == {"pkg/later.py": ["module marked placeholder"]}


def test_tests_and_files_a_fact_calls_by_design_are_left_out(repo):
    root, write = repo
    write("pkg/hooks.py", "def hook():\n    raise NotImplementedError\n")
    write("tests/test_pkg.py", "def helper():\n    raise NotImplementedError\n")
    assert list(_names(root)) == ["pkg/hooks.py"]
    assert _names(root, ["pkg/hooks.py hook raises NotImplementedError by design"]) == {}


# --- placeholders: `close` waits for them ---------------------------------------------------------------------

def test_close_waits_while_a_named_module_only_raises(project):
    # authlib a: 40 of 40 names imported, a dozen modules only raised, and `close` completed the goal.
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p, reader=False)
    (p / "toolpkg" / "reader.py").write_text(
        "class Reader:\n    def read(self, path):\n        raise NotImplementedError\n")
    installable(p)
    code, out = cli("close", SLUG)
    assert code == 1 and not out["complete"], out
    assert any("still placeholders" in m and "toolpkg/reader.py (Reader.read)" in m for m in out["missing"]), out
    (p / "toolpkg" / "reader.py").write_text(
        "class Reader:\n    def read(self, path):\n        return open(path).read()\n")
    code, out = _close(cli)
    assert code == 0 and out["complete"], out


def test_check_names_the_placeholders_before_close_holds_on_them(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p, reader=False)
    (p / "toolpkg" / "reader.py").write_text(
        "class Reader:\n    def read(self, path):\n        raise NotImplementedError\n")
    code, out = cli("check", SLUG, "--did", "Built parse", "--next", "Build Reader.read")
    assert code == 0 and "toolpkg/reader.py (Reader.read)" in out.get("placeholders", ""), out


def test_a_fact_that_a_raise_is_by_design_lets_close_complete(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p, reader=False)
    (p / "toolpkg" / "reader.py").write_text(
        "class Reader:\n    def on_line(self, line):\n        raise NotImplementedError\n")
    installable(p)
    assert cli("close", SLUG)[0] == 1
    cli("note", SLUG, "--fact", "toolpkg/reader.py Reader.on_line raises NotImplementedError by design")
    code, out = _close(cli)
    assert code == 0 and out["complete"], out


# --- a gate run that never returned ---------------------------------------------------------------------------

def _left_running(p, pid, slug=SLUG):
    notes = p / ".claude" / "harness" / "notes"
    notes.mkdir(parents=True, exist_ok=True)
    (notes / gate.RUNNING).write_text(json.dumps(
        {"pid": pid, "slug": slug, "command": "pytest tests -q", "started": "2026-11-10T06:09:00+00:00"}))
    return notes / gate.RUNNING


def _dead_pid():
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    return done.pid


def test_the_next_session_is_told_when_a_gate_run_was_killed(project):
    # mpmath a: a hanging test made every `check` outlive its call; sessions 7 to 10 started from the same state.
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _left_running(p, _dead_pid())
    text = _context(cli.new_session("s2"))
    assert "never finished" in text and f"check {SLUG} --timeout 100" in text, text
    assert "older than the files" in text, text


def test_a_gate_run_that_returns_leaves_nothing_to_tell(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    marker = _left_running(p, _dead_pid())
    _build(p)
    code, _ = cli("check", SLUG, "--did", "Built parse and Reader", "--next", "Make it install")
    assert code == 0 and not marker.exists()
    assert "never finished" not in _context(cli.new_session("s2"))


def test_a_gate_run_still_going_is_not_called_unfinished(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _left_running(p, os.getpid())
    text = _context(cli.new_session("s2"))
    assert "still going in the background" in text and "never finished" not in text, text


def test_another_goals_gate_run_is_not_reported(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _left_running(p, _dead_pid(), slug="some-other-goal")
    assert "never finished" not in _context(cli.new_session("s2"))


def test_a_gate_that_times_out_says_how_far_it_got(tmp_path, monkeypatch):
    import paths
    monkeypatch.setattr(paths, "harness_root", lambda: tmp_path / "harness")
    script = "import sys, time; print('tests/test_a.py ...'); sys.stdout.flush(); time.sleep(60)"
    state = {"slug": "g", "cwd": str(tmp_path), "checkpoints": [],
             "gate": {"kind": "command", "value": f"{sys.executable} -c \"{script}\""}}
    with pytest.raises(gate.GateUnrunnable) as caught:
        gate.run_gate(state, timeout=2, current_hash="x")
    message = str(caught.value)
    assert message.startswith("gate timed out after 2s") and "tests/test_a.py ..." in message, message
    assert not (tmp_path / "harness" / "notes" / gate.RUNNING).exists(), "a timeout is a return: no marker stays"


# --- every missing name is shown ------------------------------------------------------------------------------

def test_a_long_list_of_missing_names_is_given_whole_by_module():
    # mpmath a: 8 of 48 were shown, alphabetically, "and 40 more".
    failed = [{"id": f"mathpkg.{n}", "message": "no such name"} for n in
              ("quad", "diff", "taylor", "limit", "nsum", "findroot", "eig", "svd_r", "hyp2f1", "ellipk")]
    failed += [{"id": "mathpkg.calculus", "message": "no module"}, {"id": "mathpkg.calculus.Quadrature", "message": ""}]
    text = surface.grouped(failed)
    assert "more" not in text, text
    assert "mathpkg: quad, diff, taylor, limit, nsum, findroot, eig, svd_r, hyp2f1, ellipk, calculus" in text, text
    assert "mathpkg.calculus: Quadrature" in text, text


def test_a_short_list_keeps_each_names_message():
    failed = [{"id": "toolpkg.reader", "message": "No module named 'toolpkg.reader'"}]
    assert surface.grouped(failed) == surface.listing(failed)


def test_the_session_start_message_lists_every_missing_name(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    notes = p / ".claude" / "harness" / "notes"
    names = [f"toolpkg.name_{i}" for i in range(30)]
    (notes / f"surface-{SLUG}.json").write_text(json.dumps(
        {"passed": 0, "failed": [{"id": n, "message": "missing"} for n in names], "error": None}))
    text = _context(cli.new_session("s2"))
    assert all(n.split(".")[1] in text for n in names), text
    assert "working first version" in text, text


# --- the same next action, session after session --------------------------------------------------------------

def test_a_next_action_that_opens_three_sessions_is_called_out(project):
    # django-modelcluster a: "write tests/test_queryset.py ... fix queryset.py" opened sessions 6, 7, 8 and 9.
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)
    cli("check", SLUG, "--did", "Built parse and Reader", "--next", "Verify the reader against the request")
    assert "sessions in a row" not in _context(cli.new_session("s2"))
    assert "sessions in a row" not in _context(cli.new_session("s3"))
    text = _context(cli.new_session("s4"))
    assert "has now opened 3 sessions in a row" in text and "largest part of the request" in text, text


def test_a_new_next_action_starts_the_count_again(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)
    cli("check", SLUG, "--did", "Built parse and Reader", "--next", "Verify the reader against the request")
    cli.new_session("s2")
    cli.new_session("s3")
    cli("note", SLUG, "--next", "Build the command line entry point")
    assert "sessions in a row" not in _context(cli.new_session("s4"))


def test_one_session_starting_twice_counts_once(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)
    cli("check", SLUG, "--did", "Built parse and Reader", "--next", "Verify the reader against the request")
    for _ in range(4):
        text = _context(cli.new_session("s2"))
    assert "sessions in a row" not in text, text


# --- the package a spec states --------------------------------------------------------------------------------

GRAPPA_LIKE = """# asserts

## Public API

The following symbols must be importable from the top-level `asserts` package:

- `should` and `expect`

## Semantics

`value | should.be.equal.to(1)`, `value | should.have.length(2)`, `value | should.be.a('int')`,
`should.be.none`, `should.be.true`, `should.contain('a')`, `should.have.key('k')`, `asserts.config`.
"""


def test_the_package_a_spec_states_beats_the_object_it_writes_most():
    # grappa: `should.<chain>` in fifty code spans; the surface test was written for a package called `should`.
    assert surface.guess_package(GRAPPA_LIKE) == "asserts"


def test_a_dependency_imported_in_a_code_span_is_not_the_package():
    text = GRAPPA_LIKE.replace("top-level `asserts` package", "`asserts` library") + \
        "\nCompare times with `from dirty_values import IsNow`.\n"
    assert surface.guess_package(text) != "dirty_values"


def test_a_raise_that_says_who_must_supply_the_method_is_an_interface(repo):
    # grappa a: `Operator.match` raises "%s must implement match()"; the request's own operators define it.
    root, write = repo
    write("pkg/core.py", "class Operator:\n    def match(self, subject):\n"
                         "        raise NotImplementedError('%s must implement match()' % type(self).__name__)\n\n"
                         "    def run(self):\n        raise NotImplementedError('Operator.run')\n")
    assert _names(root) == {"pkg/core.py": ["Operator.run"]}
