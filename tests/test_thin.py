"""The thin variant (exp/thin, 2026-11-09).

E2E-SWE full run (candidate 5f3bb6a, 62 builds): Horizon's lead over plain was rescued collapses (builds that did not
install or import); on the 23 tasks where neither arm collapsed it was 2.49 points behind. About half of each
session's calls went to Horizon's own files and commands, and 30 of 31 completed goals closed without a passing
review. The thin variant keeps what has evidence (the install check, the surface gate, the notes) and drops the rest
from the default path:

- a goal starts from request.md alone: no requirements.md, no gate file of the agent's own, no reviewer;
- the surface gate and the install check hold with the Python the gate command names, by a bare name or a relative
  path too (about 5% of goals named no absolute Python, and both checks then only advised);
- `start` writes the spec's own examples whenever it finds the spec (no build ever passed `--surface-spec`)."""
import os
import shlex
import sys
from pathlib import Path

from installable_project import installable
from test_e2eswe_fixes import project  # noqa: F401  (the fixture is used by name)

import examples
import surface

SPEC = """# Build `toolpkg`

`toolpkg` parses lines. Install it with `pip install -e .` (see setup.sh).

## Parsing

`toolpkg.parse(line)` returns the line. `toolpkg.reader.Reader` reads files.

```python
from toolpkg import parse
from toolpkg.reader import Reader
print(parse("a"))  # Output: a
```
"""
PASSING = "def test_parse_returns_the_line():\n    assert True\n"
SLUG = "build-the-tool"


def _thin(p, cli, gate=None):
    """A goal the way the thin SKILL.md starts one: the request, and the project's own tests as the gate."""
    (p / "SPEC.md").write_text(SPEC)
    (p / "tests").mkdir(exist_ok=True)
    (p / "tests" / "test_tool.py").write_text(PASSING)
    gates = p / ".claude" / "gates" / "g"
    gates.mkdir(parents=True, exist_ok=True)
    (gates / "request.md").write_text("Build the library described in SPEC.md\n")
    command = gate or f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider tests"
    code, out = cli("start", "Build the tool", "--gate-path", str(gates), "--gate", command)
    assert code == 0, out
    return out, gates


def _build(p, reader=True):
    (p / "toolpkg").mkdir(exist_ok=True)
    (p / "toolpkg" / "__init__.py").write_text("def parse(line):\n    return line\n")
    if reader:
        (p / "toolpkg" / "reader.py").write_text("class Reader:\n    pass\n")


# --- a goal from the request alone ----------------------------------------------------------------------------

def test_start_needs_only_the_request(project):
    p, cli = project
    cli.new_session("s1")
    out, gates = _thin(p, cli)
    assert out["status"] == "active" and not (gates / "requirements.md").exists(), out
    assert (gates / surface.FILE).is_file(), "the surface gate is still written from the spec"


def test_start_still_refuses_a_gate_directory_without_the_request(project):
    p, cli = project
    gates = p / ".claude" / "gates" / "g"
    gates.mkdir(parents=True)
    code, out = cli("start", "Build the tool", "--gate-path", str(gates), "--gate", "true")
    assert code == 2 and "request.md" in str(out), out


def test_a_requirements_file_that_has_no_lines_is_still_refused(project):
    p, cli = project
    gates = p / ".claude" / "gates" / "g"
    gates.mkdir(parents=True)
    (gates / "request.md").write_text("Build it\n")
    (gates / "requirements.md").write_text("| id | text |\n")
    code, out = cli("start", "Build the tool", "--gate-path", str(gates), "--gate", "true")
    assert code == 2 and "requirement lines" in str(out), out


def test_check_on_a_thin_goal_passes_and_asks_for_no_reviewer(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)
    code, out = cli("check", SLUG, "--did", "Built parse and Reader", "--next", "Make it install")
    assert code == 0, out
    assert out["review_required"] is False and "review_brief" not in out, out


def test_close_completes_a_thin_goal_with_no_review(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)
    installable(p)
    code, out = cli("close", SLUG)
    assert code == 0 and out["complete"], out
    assert "review_brief" not in out and "unreviewed" not in out, out


def test_close_on_a_thin_goal_still_waits_for_a_module_the_spec_names(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p, reader=False)
    code, out = cli("close", SLUG)
    assert code == 1 and not out["complete"], out
    assert any("toolpkg.reader" in m for m in out["missing"]), out


def test_close_on_a_thin_goal_still_waits_for_the_install(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)  # every name imports from the folder, but there is no pyproject.toml
    code, out = cli("close", SLUG)
    assert code == 1 and not out["complete"], out
    assert any("install" in m for m in out["missing"]), out


def test_a_goal_started_with_requirements_keeps_them_when_the_file_goes_missing(project):
    # the check a full goal asked for is not switched off by deleting its file
    p, cli = project
    cli.new_session("s1")
    out, gates = _thin(p, cli)
    code, out = cli("abandon", SLUG, "--reason", "restart as a full goal")
    assert code == 0, out
    (gates / "requirements.md").write_text('R1 [gate] "parse() returns the line"\n')
    (p / "tests" / "test_tool.py").write_text("def test_r1_parse():\n    assert True\n")
    code, out = cli("start", "Build the tool again", "--gate-path", str(gates),
                    "--gate", f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider tests")
    assert code == 0, out
    (gates / "requirements.md").unlink()
    _build(p)
    installable(p)
    code, out = cli("close", out["slug"])
    assert code == 1 and not out["complete"], out
    assert any("requirements.md" in m for m in out["missing"]), out


# --- the Python the gate names, however it names it -----------------------------------------------------------

def _state(project_dir, command):
    return {"gate": {"kind": "command", "value": command}, "cwd": str(project_dir)}


def _fake_bin(tmp_path, *names):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for name in names:
        (bin_dir / name).write_text("#!/bin/sh\n")
        (bin_dir / name).chmod(0o755)
    return bin_dir


def test_a_python_named_by_a_bare_name_is_the_one_on_path(tmp_path, monkeypatch):
    bin_dir = _fake_bin(tmp_path, "python3")
    monkeypatch.setenv("PATH", str(bin_dir))
    assert surface.interpreter(_state(tmp_path, "python3 -m pytest -q tests")) == str(bin_dir / "python3")


def test_a_bare_pytest_gives_the_python_beside_it(tmp_path, monkeypatch):
    bin_dir = _fake_bin(tmp_path, "pytest", "python")
    monkeypatch.setenv("PATH", str(bin_dir))
    assert surface.interpreter(_state(tmp_path, "pytest -q tests")) == str(bin_dir / "python")


def test_a_python_named_by_a_relative_path_is_found_from_the_project(tmp_path, monkeypatch):
    project_dir = tmp_path / "proj "
    (project_dir / ".venv" / "bin").mkdir(parents=True)
    python = project_dir / ".venv" / "bin" / "python"
    python.write_text("#!/bin/sh\n")
    python.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    assert surface.interpreter(_state(project_dir, ".venv/bin/python -m pytest -q")) == str(python)


def test_an_absolute_python_still_wins_over_a_bare_one(tmp_path, monkeypatch):
    bin_dir = _fake_bin(tmp_path, "python3")
    monkeypatch.setenv("PATH", str(bin_dir))
    command = f"python3 -c pass && {shlex.quote(sys.executable)} -m pytest -q"
    assert surface.interpreter(_state(tmp_path, command)) == sys.executable


def test_a_command_that_names_no_python_still_gives_none(tmp_path, monkeypatch):
    bin_dir = _fake_bin(tmp_path, "python3")
    monkeypatch.setenv("PATH", str(bin_dir))
    assert surface.interpreter(_state(tmp_path, "make test")) is None
    assert surface.interpreter(_state(tmp_path, "sh run-python3-tests.sh")) is None


def test_a_bare_name_that_is_not_on_path_gives_none(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    assert surface.interpreter(_state(tmp_path, "python3 -m pytest -q")) is None


def test_close_waits_for_the_surface_when_the_gate_names_python_by_a_bare_name(project):
    p, cli = project
    cli.new_session("s1")
    name = os.path.basename(sys.executable)
    cli.env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + cli.env.get("PATH", "")
    _thin(p, cli, gate=f"{name} -m pytest -q -p no:cacheprovider tests")
    _build(p, reader=False)
    code, out = cli("close", SLUG)
    assert code == 1 and not out["complete"], out
    assert any("toolpkg.reader" in m for m in out["missing"]), out
    assert "surface_advisory" not in out, out


# --- the spec's own examples, without a flag ------------------------------------------------------------------

def test_start_writes_the_spec_examples_when_it_finds_the_spec(project):
    p, cli = project
    cli.new_session("s1")
    out, gates = _thin(p, cli)
    assert (gates / examples.FILE).is_file(), sorted(f.name for f in gates.iterdir())
    assert out["spec_examples"] == {"examples": 1, "signatures": 0}, out


def test_check_reports_a_spec_example_the_work_disagrees_with(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)
    (p / "toolpkg" / "__init__.py").write_text("def parse(line):\n    return line.upper()\n")
    code, out = cli("check", SLUG, "--did", "Built parse", "--next", "Compare with the spec's examples")
    assert "1 disagree" in out.get("spec_examples", ""), out


def test_start_keeps_spec_examples_already_there(project):
    p, cli = project
    cli.new_session("s1")
    gates = p / ".claude" / "gates" / "g"
    gates.mkdir(parents=True)
    (gates / examples.FILE).write_text("# mine\n")
    _thin(p, cli)
    assert (gates / examples.FILE).read_text() == "# mine\n"


def test_a_request_with_no_spec_gets_no_examples(project):
    p, cli = project
    cli.new_session("s1")
    gates = p / ".claude" / "gates" / "g"
    gates.mkdir(parents=True)
    (gates / "request.md").write_text("Make parse() return the line it is given\n")
    code, out = cli("start", "Fix parse", "--gate-path", str(gates), "--gate", "true")
    assert code == 0 and "spec_examples" not in out, out
    assert not (gates / examples.FILE).exists()


# --- the skill text -------------------------------------------------------------------------------------------

SKILL = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "horizon" / "SKILL.md"


def test_the_thin_skill_asks_for_no_requirements_file_no_gate_file_and_no_reviewer():
    text = SKILL.read_text()
    for gone in ("requirements.md", "test_request.py", "review_brief", "--review", "Agent` tool"):
        assert gone not in text, gone


def test_the_thin_skill_documents_what_the_reports_say():
    text = SKILL.read_text()
    for key in ("surface_missing", "install_problem", "spec_examples", "waive-example", "by design"):
        assert key in text, key


def test_the_thin_skill_is_shorter_than_the_full_one_it_replaced():
    assert len(SKILL.read_text().splitlines()) <= 130  # the full skill at 9a72fb4 had 163 lines


def test_the_thin_skill_is_task_neutral():
    from test_heldout_fixes import FITTED  # the benchmark-fitted phrases removed on 2026-10-31
    flat = " ".join(SKILL.read_text().split())
    assert [phrase for phrase in FITTED if phrase in flat] == []


def test_the_thin_skill_keeps_the_rules_with_evidence():
    flat = " ".join(SKILL.read_text().split())
    for phrase in ("Make the deliverable usable first", "in a clean copy", "Write straight into the real files",
                   "Build before you verify", "never from your own code", "never stop to ask",
                   "Make your first edit within your first 5 tool calls", "baseline `observation`"):
        assert phrase in flat, phrase
