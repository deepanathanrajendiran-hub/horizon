"""Work that is not a new Python package built from a spec: another language, a change to an existing codebase, a
request in a sentence. Horizon's checks of the request (names import, the package installs, no placeholders, the gate
is not too small, the request check) were written from spec-to-package builds; here they have nothing to say, and a
false report costs a session what a true one saves (E2E-SWE big run: 32 of 62 builds were told to build interface
methods, and one spent a whole session on them).

What still applies anywhere is the saved goal, the gate the session gave, the notes and the next action."""
import shlex
import subprocess
import sys

import pytest

from test_e2eswe_fixes import project  # noqa: F401  (the fixture is used by name)
from test_thin import _context

import hooks

SLUG = "do-the-work"
QUIET = ("surface_missing", "install_problem", "placeholders", "small_gate")
TRUE = f"{shlex.quote(sys.executable)} -c pass"


def _git(p, *args):
    subprocess.run(["git", "-C", str(p), *args], check=True, capture_output=True)


def _goal(p, cli, request, gate=TRUE):
    gates = p / ".claude" / "gates" / "g"
    gates.mkdir(parents=True, exist_ok=True)
    (gates / "request.md").write_text(request)
    code, out = cli("start", "Do the work", "--gate-path", str(gates), "--gate", gate)
    assert code == 0, out
    return out, gates


def _said(out):
    return {key: out[key] for key in QUIET if out.get(key)}


def _go_project(p):
    (p / "src" / "tool.py").unlink()
    (p / "go.mod").write_text("module example.com/tool\n\ngo 1.22\n")
    (p / "cmd" / "tool").mkdir(parents=True)
    (p / "cmd" / "tool" / "main.go").write_text(
        'package main\n\nimport "fmt"\n\nfunc main() {\n\tfmt.Println(Parse("a"))\n}\n')
    (p / "parser.go").write_text(
        'package tool\n\n// Parse returns the line.\nfunc Parse(line string) string {\n\tpanic("not implemented")\n}\n')
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "go project")


GO_REQUEST = """# Add JSON output to `tool`

Install the dependencies with `go mod download`, then build with `go build ./cmd/tool`.

## The flag

`tool --json` prints each parsed line as a JSON object. `tool.Parse(line)` in `parser.go` returns the line.

```go
import "example.com/tool"
fmt.Println(tool.Parse("a"))  // Output: a
```

## Errors

`tool.ParseError` is returned for an empty line.
"""


# --- another language -----------------------------------------------------------------------------------------

def test_a_go_project_gets_no_python_reports(project):
    p, cli = project
    cli.new_session("s1")
    _go_project(p)
    out, _ = _goal(p, cli, GO_REQUEST)
    assert not _said(out), _said(out)
    (p / "parser.go").write_text("package tool\n\nfunc Parse(line string) string {\n\treturn line\n}\n")
    code, out = cli("check", SLUG, "--did", "Parse returns the line", "--next", "Add the flag")
    assert code == 0 and not _said(out), out
    context = _context(cli.new_session("s2"))
    assert "did not import" not in context and "Placeholders" not in context and "install" not in context.lower(), \
        context


def test_a_go_goal_closes_after_the_request_check_alone(project):
    # The request has headed parts, so the request check applies in any language; nothing else holds the goal.
    p, cli = project
    cli.new_session("s1")
    _go_project(p)
    _goal(p, cli, GO_REQUEST)
    (p / "parser.go").write_text("package tool\n\nfunc Parse(line string) string {\n\treturn line\n}\n")
    code, out = cli("close", SLUG)
    assert code == 1 and len(out["missing"]) == 1 and "part by part" in out["missing"][0], out
    assert cli("check", SLUG, "--did", "Ran each part", "--next", "Close")[0] == 0
    code, out = cli("close", SLUG)
    assert code == 0 and out["complete"], out


# --- a change to an existing codebase --------------------------------------------------------------------------

def _existing(p):
    """A project that was there before the goal, with functions that raise NotImplementedError for its own reasons."""
    (p / "src" / "__init__.py").write_text("")
    (p / "src" / "legacy.py").write_text(
        "def export_xml(rows):\n    raise NotImplementedError\n\n\ndef export_pdf(rows):\n    raise NotImplementedError\n")
    (p / "tests").mkdir()
    (p / "tests" / "test_tool.py").write_text(
        "from src.tool import parse\n\n\ndef test_parse():\n    assert parse('a') == 'a'\n")
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "existing project")


FIX_REQUEST = "`src.tool.parse` keeps the trailing newline of the line it is given. Strip it. Do not install anything.\n"
PYTEST = f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider tests"


def test_placeholders_that_were_there_before_the_goal_are_not_the_goals(project):
    p, cli = project
    cli.new_session("s1")
    _existing(p)
    out, _ = _goal(p, cli, FIX_REQUEST, gate=PYTEST)
    assert not _said(out), _said(out)
    (p / "src" / "tool.py").write_text("def parse(line):\n    return line.rstrip('\\n')\n")
    code, out = cli("check", SLUG, "--did", "parse strips the newline", "--next", "Close")
    assert code == 0 and not _said(out), out
    assert "Placeholders" not in _context(cli.new_session("s2"))
    code, out = cli("close", SLUG)
    assert code == 0 and out["complete"], out


def test_a_placeholder_the_work_adds_to_an_existing_project_still_holds(project):
    p, cli = project
    cli.new_session("s1")
    _existing(p)
    _goal(p, cli, FIX_REQUEST, gate=PYTEST)
    (p / "src" / "tool.py").write_text(
        "def parse(line):\n    return line.rstrip('\\n')\n\n\ndef parse_all(lines):\n    raise NotImplementedError\n")
    code, out = cli("close", SLUG)
    assert code == 1 and "src/tool.py (parse_all)" in out["missing"][0] and "legacy" not in out["missing"][0], out
    assert "src/tool.py (parse_all)" in _context(cli.new_session("s2"))


def test_a_one_sentence_request_closes_on_its_gate(project):
    p, cli = project
    cli.new_session("s1")
    _existing(p)
    _goal(p, cli, "Make the uploader retry three times before it gives up.\n", gate=PYTEST)
    (p / "src" / "tool.py").write_text("def parse(line):\n    return line\n\n\nRETRIES = 3\n")
    code, out = cli("close", SLUG)
    assert code == 0 and out["complete"] and not _said(out), out


# --- a request that says "install" about something else --------------------------------------------------------

def test_an_install_step_of_another_toolchain_is_not_checked_with_pip(project):
    p, cli = project
    cli.new_session("s1")
    (p / "package.json").write_text('{"name": "tool", "version": "1.0.0"}\n')
    (p / "index.js").write_text("module.exports.parse = (line) => line\n")
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "node project")
    request = ("Run `npm install`, then make `parse` in `index.js` trim the line.\n")
    out, _ = _goal(p, cli, request)
    (p / "index.js").write_text("module.exports.parse = (line) => line.trim()\n")
    code, out = cli("close", SLUG)
    assert code == 0 and out["complete"] and not _said(out), out
    assert "install_advisory" not in out and "surface_advisory" not in out, out


# --- a project too large to list -------------------------------------------------------------------------------

@pytest.fixture
def large(tmp_path):
    root = tmp_path / "big"
    for folder, count in (("src/core", 120), ("src/api", 60), ("tests", 90), ("docs", 15)):
        (root / folder).mkdir(parents=True)
        for i in range(count):
            (root / folder / f"file_{i:03}.py").write_text("x = 1\n")
    (root / "README.md").write_text("# Big\n")
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-qm", "init")
    return root


def test_a_large_project_is_summed_up_by_folder(large):
    line = hooks._files_line(large)
    assert "286 files" in line and "src/core (120)" in line and "tests (90)" in line, line
    assert "file_000.py" not in line and len(line) < 600, line


def test_a_large_project_still_lists_the_files_the_work_touched(large):
    (large / "src" / "core" / "file_007.py").write_text("x = 1\ny = 2\n")
    (large / "src" / "api" / "new_handler.py").write_text("def handle():\n    return 1\n")
    line = hooks._files_line(large)
    assert "src/core/file_007.py 2" in line and "src/api/new_handler.py 2" in line, line
    assert "file_008.py" not in line, line


def test_a_small_project_is_still_listed_file_by_file(project):
    p, _ = project
    assert hooks._files_line(p) == "Files (lines): src/tool.py 2."

# --- what the session wrote before `start` is the goal's work --------------------------------------------------

def test_placeholders_written_before_start_are_still_held(project):
    # thin6 run, barf: 29 placeholder functions laid out two calls before `start` were taken as the project's own,
    # and the goal completed with 12 of them unbuilt (56.5; the version before scored 78.3).
    p, cli = project
    cli.new_session("s1")
    _existing(p)
    (p / "src" / "scaffold.py").write_text("def build(rows):\n    raise NotImplementedError\n")
    (p / "src" / "tool.py").write_text(
        "def parse(line):\n    return line\n\n\ndef parse_all(lines):\n    raise NotImplementedError\n")
    _goal(p, cli, FIX_REQUEST, gate=PYTEST)
    code, out = cli("close", SLUG)
    assert code == 1, out
    held = out["missing"][0]
    assert "src/scaffold.py (build)" in held and "src/tool.py (parse_all)" in held and "legacy" not in held, out
