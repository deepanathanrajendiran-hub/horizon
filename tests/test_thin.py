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


def _thin(p, cli, gate=None, tests=10):
    """A goal the way the thin SKILL.md starts one: the request, and the project's own tests as the gate."""
    (p / "SPEC.md").write_text(SPEC)
    (p / "tests").mkdir(exist_ok=True)
    (p / "tests" / "test_tool.py").write_text(
        PASSING + "".join(f"\n\ndef test_parse_case_{i}():\n    assert True\n" for i in range(1, tests)))
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

# --- after the thin run (E2E-SWE thin, 2026-11-09) ------------------------------------------------------------
# pdfminer scored 6.1 (9a72fb4: 97.0). Session 1 wrote a bug that left every page's text empty. Session 4 found the
# symptom ("Pages are found now, but the text is still empty", then a probe printing `contents []`) and was cut off
# by the call cap on that call; no `check` had run yet, so session 5 started from the baseline and never went back.
# The gate was one import test from start to finish, and `close` completed the goal on it in session 8.

def _surface_names(gates):
    import re
    body = re.search(r"^NAMES = \[(.*?)^\]", (gates / surface.FILE).read_text(), re.M | re.S).group(1)
    return len(re.findall(r"^\s*'", body, re.M))


def test_close_waits_when_the_gate_runs_fewer_tests_than_the_request_names_things(project):
    p, cli = project
    cli.new_session("s1")
    _, gates = _thin(p, cli, tests=1)
    assert _surface_names(gates) >= 2
    _build(p)
    installable(p)
    code, out = cli("close", SLUG)
    assert code == 1 and not out["complete"], out
    assert any("gate ran 1 test" in m and "end to end" in m for m in out["missing"]), out

def test_check_says_the_gate_is_small_before_close_holds_on_it(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli, tests=1)
    _build(p)
    code, out = cli("check", SLUG, "--did", "Built parse and Reader", "--next", "Make it install")
    assert code == 0 and "gate ran 1 test" in out.get("small_gate", ""), out
    (p / "tests" / "test_more.py").write_text("".join(f"def test_use_{i}():\n    assert True\n\n" for i in range(9)))
    code, out = cli("check", SLUG, "--did", "Added tests of the main use", "--next", "Make it install")
    assert code == 0 and "small_gate" not in out, out


def test_close_completes_once_the_gate_runs_a_test_for_each_name(project):
    p, cli = project
    cli.new_session("s1")
    _, gates = _thin(p, cli, tests=1)
    _build(p)
    installable(p)
    assert cli("close", SLUG)[0] == 1
    (p / "tests" / "test_more.py").write_text(
        "".join(f"def test_use_{i}():\n    assert True\n\n" for i in range(_surface_names(gates))))
    code, out = cli("close", SLUG)
    assert code == 0 and out["complete"], out


def test_a_gate_that_is_not_pytest_is_not_counted(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli, gate="true")
    _build(p)
    installable(p)
    code, out = cli("close", SLUG)
    assert code == 0 and out["complete"], out


def test_a_request_that_names_no_package_closes_on_one_test(project):
    # "make this test pass" is a whole request: the count only applies when the request names a public surface
    p, cli = project
    cli.new_session("s1")
    (p / "tests").mkdir()
    (p / "tests" / "test_tool.py").write_text(PASSING)
    gates = p / ".claude" / "gates" / "g"
    gates.mkdir(parents=True)
    (gates / "request.md").write_text("Make parse() return the line it is given\n")
    code, out = cli("start", "Fix parse", "--gate-path", str(gates), "--gate",
                    f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider tests")
    assert code == 0, out
    (p / "src" / "tool.py").write_text("def parse(line):\n    return line  # fixed\n")
    code, out = cli("close", out["slug"])
    assert code == 0 and out["complete"], out


def _transcript(tmp_path, *said):
    import json
    path = tmp_path / "transcript.jsonl"
    path.write_text("".join(json.dumps({"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "text", "text": text}]}}) + "\n" for text in said))
    return str(path)


def _call(cli, session, command, stdout, transcript=None, event="PostToolUse", **extra):
    payload = {"session_id": session, "tool_name": "Bash", "tool_input": {"command": command},
               "tool_response": {"stdout": stdout, "stderr": ""}, **extra}
    if transcript:
        payload["transcript_path"] = transcript
    return cli.hook(event, payload)


def _context(out):
    return out["hookSpecificOutput"]["additionalContext"]


def test_the_next_session_is_told_where_a_cut_off_session_stopped(project, tmp_path):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    said = _transcript(tmp_path, "I'll read the parser.", "Pages are found now, but the text is still empty.")
    _call(cli, "s1", "python probe.py hello.pdf", "contents [] res {'Font': 1}", said)
    text = _context(cli.new_session("s2"))
    assert "Where the last session stopped" in text, text
    assert "the text is still empty" in text and "python probe.py hello.pdf" in text and "contents []" in text, text
    assert "I'll read the parser" not in text  # only the last thing it said


def test_a_session_is_not_told_about_its_own_calls(project, tmp_path):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _call(cli, "s1", "python probe.py", "contents []", _transcript(tmp_path, "The text is still empty."))
    out = cli.hook("SessionStart", {"session_id": "s1", "source": "compact"})
    assert "Where the last session stopped" not in _context(out)


def test_a_session_that_ended_on_check_leaves_nothing_more_to_tell(project, tmp_path):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    said = _transcript(tmp_path, "The text is still empty.")
    _call(cli, "s1", "python probe.py", "contents []", said)
    _call(cli, "s1", f'python3 "/x/scripts/harness.py" --runtime claude --project "{p}" check {SLUG} --did a --next b',
          "{}", said)
    assert "Where the last session stopped" not in _context(cli.new_session("s2"))


def test_a_failed_call_is_kept_too(project, tmp_path):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    cli.hook("PostToolUseFailure", {"session_id": "s1", "tool_name": "Bash",
                                    "tool_input": {"command": "python probe.py"},
                                    "error": "Traceback: KeyError: 'Contents'"})
    text = _context(cli.new_session("s2"))
    assert "python probe.py" in text and "KeyError: 'Contents'" in text, text


def test_a_reviewer_or_other_subagent_call_is_not_kept(project, tmp_path):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _call(cli, "s1", "cat notes.txt", "subagent output", agent_id="a1")
    assert "Where the last session stopped" not in _context(cli.new_session("s2"))


def test_long_output_is_cut_to_its_end_and_a_bad_transcript_is_ignored(project, tmp_path):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _call(cli, "s1", "python probe.py", "x" * 5000 + " THE END", str(tmp_path / "missing.jsonl"))
    text = _context(cli.new_session("s2"))
    assert "THE END" in text and "x" * 1000 not in text, text


def test_no_goal_no_trail(project, tmp_path):
    p, cli = project
    cli.new_session("s1")
    _call(cli, "s1", "ls", "a b c", _transcript(tmp_path, "Looking around."))
    assert "Where the last session stopped" not in _context(cli.new_session("s2"))


def test_a_later_goal_keeps_the_first_goals_request(project):
    # mpmath: the second goal's request.md read "Remaining placeholder: lambertw in mpmath/_elem.py", and `close`
    # completed that goal while the hidden score was 0.
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)
    installable(p)
    assert cli("close", SLUG)[0] == 0
    later = p / ".claude" / "gates" / "later"
    later.mkdir(parents=True)
    (later / "request.md").write_text("Remaining placeholder: Reader.read\n")
    code, out = cli("start", "Finish Reader", "--gate-path", str(later), "--gate",
                    f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider tests")
    assert code == 0, out
    text = (later / "request.md").read_text()
    assert text.startswith("Remaining placeholder: Reader.read") and "Build the library described in SPEC.md" in text
    assert "earlier_request" in out and (later / surface.FILE).is_file(), out  # the spec is still found through it


def test_a_later_goal_with_the_same_request_is_left_alone(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)
    installable(p)
    assert cli("close", SLUG)[0] == 0
    later = p / ".claude" / "gates" / "later"
    later.mkdir(parents=True)
    (later / "request.md").write_text("Build the library  described in SPEC.md\n\n")
    code, out = cli("start", "Build the tool", "--gate-path", str(later), "--gate",
                    f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider tests")
    assert code == 0 and "earlier_request" not in out, out
    assert (later / "request.md").read_text() == "Build the library  described in SPEC.md\n\n"


def test_the_thin_skill_asks_for_a_test_of_the_main_use_and_names_the_trail():
    flat = " ".join(SKILL.read_text().split())
    for phrase in ("main use end to end", "Where the last session stopped", "gate ran"):
        assert phrase in flat, phrase
