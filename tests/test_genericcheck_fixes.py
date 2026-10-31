"""NL2Repo generic check (fun docs/evaluations/nl2repo-genericcheck/transcripts.md).

- The agent's requirement lines covered part of a long request (20 lines for a 776-line spec, 26 for 1,720 lines):
  bleach never built the layout one section listed, and fastapi-users never compared the code with the
  one-by-one reference section. Nothing checked that the requirements cover the request.
- After the one review round failed (bleach R11), `close` completed once any file changed, though only the
  agent's own checks had.
- A README-only session counted as progress, so Plateau never fired (fastapi-users sessions 4-6)."""
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = str(ROOT / ".claude/skills/horizon/scripts/harness.py")
sys.path.insert(0, str(ROOT / "harness"))


def _git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                   env=env, capture_output=True)


FILLER = "".join(f"Background sentence {i}.\n" for i in range(220))
LONG_SPEC = ("# Tool\n\n" + FILLER + "\n## Parsing\n\nparse() reads a line.\n\n## Usage Guide\n\n"
             "### Project Layout\n\nsrc/tool.py and src/extra.py.\n\n```python\n# A comment, not a heading\n```\n\n"
             "### Output Format\n\nOne line per record.\n")


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj "
    (p / ".claude").mkdir(parents=True)
    (p / "src").mkdir()
    (p / "src" / "tool.py").write_text("def parse(line):\n    return line\n")
    (p / "DESIGN.md").write_text(LONG_SPEC)
    _git(p, "init", "-q")
    _git(p, "add", "-A")
    _git(p, "commit", "-qm", "init")
    review_dir = tmp_path / "reviews"
    review_dir.mkdir()
    env = {**os.environ, "TMPDIR": str(review_dir)}

    def cli(*args):
        done = subprocess.run([sys.executable, ENTRY, "--runtime", "claude", "--project", str(p), *args],
                              cwd=p, capture_output=True, text=True, timeout=120, env=env)
        return done.returncode, (json.loads(done.stdout) if done.stdout.strip().startswith("{") else done.stderr)

    def start_message():
        done = subprocess.run([sys.executable, ENTRY, "--runtime", "claude", "--project", str(p), "hook",
                               "SessionStart"], input=json.dumps({"session_id": "s", "source": "startup"}),
                              cwd=p, capture_output=True, text=True, timeout=120, env=env)
        return json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]

    return p, cli, start_message


def _start(p, cli, requirements, request="Build the tool described in DESIGN.md\n", review=False):
    gates = p / ".claude" / "gates" / "g"
    gates.mkdir(parents=True, exist_ok=True)
    (gates / "request.md").write_text(request)
    (gates / "requirements.md").write_text(requirements)
    (gates / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    args = ["start", "Build the tool", "--gate-path", str(gates), "--gate",
            f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider {shlex.quote(str(gates))}"]
    if review:
        args += ["--review", "Every line of requirements.md holds"]
    code, out = cli(*args)
    assert code == 0, out
    return out["slug"], gates, out


# --- fix 1: the requirement lines cover the request ----------------------------------------------------

def test_the_sections_of_a_long_input_are_its_leaf_headings_outside_code():
    import sections
    found = [h for _, h in sections.leaf_headings(LONG_SPEC)]
    assert found == ["Parsing", "Project Layout", "Output Format"]  # not the parents "Tool", "Usage Guide"
    assert sections.leaf_headings("# Short\n\n## Part\n") != []
    assert sections.uncited("# Short\n\n## Part\ntext\n", "R1 nothing") == []  # a short input is not checked


def test_a_heading_is_cited_by_its_words_whatever_the_numbering_and_case():
    import sections
    text = ("# T\n" + FILLER + "## 2. clean() Function - HTML Cleaning\nx\n## `Linker` Class\ny\n"
            "## 8.: Output Format\nz\n")
    assert sections.uncited(text, 'R1 [gate] "clean() function - html cleaning escapes tags"\n'
                                  'R2 [review] "The Linker class links URLs"\nR3 [gate] "Output format"\n') == []
    assert [h for _, h in sections.uncited(text, 'R1 [gate] "cleaning escapes tags"')] == [
        "2. clean() Function - HTML Cleaning", "`Linker` Class", "8.: Output Format"]


def test_a_heading_with_a_code_name_is_cited_by_that_name():
    import sections
    text = ("# T\n" + FILLER + "## 2. `chunked()` Function - Data Chunking\nx\n## BaseUserManager Class\ny\n"
            "## get_auth_router() Function - Routes\nz\n## Module Import\nw\n")
    reqs = 'R1 [gate] "chunked() splits"\nR2 [gate] "BaseUserManager hashes"\nR3 [gate] "get_auth_router mounts"\n'
    assert [h for _, h in sections.uncited(text, reqs)] == ["Module Import"]  # a plain heading needs its words
    assert [h for _, h in sections.uncited(text, 'R1 "chunkedx() and UserManager"')] == [
        "2. `chunked()` Function - Data Chunking", "BaseUserManager Class", "get_auth_router() Function - Routes",
        "Module Import"]


def test_close_lists_the_sections_of_a_long_input_no_requirement_line_cites(project):
    p, cli, _ = project
    slug, gates, out = _start(p, cli, 'R1 [gate] "Parsing: parse() reads a line"\n')
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"], out
    text = " ".join(out["missing"])
    assert "DESIGN.md" in text and "Project Layout" in text and "Output Format" in text
    assert '"Parsing"' not in text
    assert not out.get("review_brief"), out  # no reviewer is sent while the requirements leave parts out


def test_start_reports_the_uncited_sections_so_the_requirements_can_be_fixed_first(project):
    p, cli, _ = project
    slug, gates, out = _start(p, cli, 'R1 [gate] "Parsing: parse() reads a line"\n')
    assert [s["heading"] for s in out["uncited_sections"]] == ["Project Layout", "Output Format"]
    assert out["uncited_sections"][0]["file"].endswith("DESIGN.md")


def test_citing_every_section_or_marking_it_out_of_scope_lets_close_finish(project):
    p, cli, _ = project
    slug, gates, _ = _start(p, cli, 'R1 [gate] "Parsing: parse() reads a line"\n'
                                    'R2 [gate] "Project Layout: src/tool.py and src/extra.py"\n'
                                    'R3 [unverifiable] "Output Format": out of scope, the user owns it\n')
    code, out = cli("close", slug)
    assert not any("cite" in m for m in out["missing"]), out


def test_a_short_request_with_no_named_input_is_not_checked(project):
    p, cli, _ = project
    slug, gates, out = _start(p, cli, 'R1 [gate] "parse works"\n', request="Make parse() strip spaces\n")
    assert out["uncited_sections"] == []
    code, out = cli("close", slug)
    assert not any("cite" in m for m in out["missing"]), out


# --- fix 2: a failed review is fixed in the work, not only in the checks --------------------------------

def _failed_round(p, cli):
    slug, gates, _ = _start(p, cli, 'R1 [gate] "Parsing, Project Layout, Output Format: parse() works"\n',
                            review=True)
    (p / "src" / "extra.py").write_text("X = 1\n")
    code, out = cli("close", slug)
    assert out.get("review_file"), out
    digest = out["observation"]["tree_hash"]
    Path(out["review_file"]).write_text(f"Reviewed checkpoint: {digest}\nR1: FAIL - parse(' a') - got ' a'\n"
                                        "VERDICTS\nR1: FAIL\nEND VERDICTS\n")
    return slug, gates


def test_after_a_failed_review_changing_only_the_checks_does_not_close(project):
    p, cli, _ = project
    slug, gates = _failed_round(p, cli)
    (gates / "test_more.py").write_text("def test_more():\n    assert True\n")
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"], out
    text = " ".join(out["missing"])
    assert "R1: FAIL" in text and "no file of the work changed" in text
    (p / "src" / "tool.py").write_text("def parse(line):\n    return line.strip()\n")
    code, out = cli("close", slug)
    assert code == 0 and out["complete"], out


def test_a_disputed_fail_line_recorded_as_a_finding_lets_close_finish(project):
    p, cli, _ = project
    slug, gates = _failed_round(p, cli)
    (gates / "test_more.py").write_text("def test_more():\n    assert True\n")
    cli("note", slug, "--finding", "Disputed R1: the request keeps leading spaces as written")
    code, out = cli("close", slug)
    assert code == 0 and out["complete"], out


# --- fix 3: documentation edits are not progress for Plateau -------------------------------------------

def test_a_documentation_only_session_does_not_clear_the_plateau(project):
    p, cli, start_message = project
    cli("start", "Build the tool", "--gate", "true")
    start_message()
    (p / "README.md").write_text("# Tool\n")
    start_message()
    (p / "docs").mkdir()
    (p / "docs" / "usage.rst").write_text("Usage\n=====\n")
    (p / "README.md").write_text("# Tool\n\nMore.\n")
    assert "Plateau" in start_message()


# --- the skill says so ---------------------------------------------------------------------------------

@pytest.mark.parametrize("folder", [".claude", ".agents"])
def test_the_skill_says_requirement_lines_cite_every_section_of_a_long_input(folder):
    core = " ".join((ROOT / folder / "skills/horizon/SKILL.md").read_text().split())
    assert "cite every section heading" in core
    assert "uncited_sections" in core
    assert "Disputed Rk:" in core
