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


def test_start_does_not_list_the_sections_so_the_first_session_builds(project):
    # genericfix: listing 181 sections at start made More-Itertools' session 1 write 193 requirement lines and
    # build nothing; only close checks the sections now.
    p, cli, _ = project
    slug, gates, out = _start(p, cli, 'R1 [gate] "Parsing: parse() reads a line"\n')
    assert "uncited_sections" not in out


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
def test_the_skill_says_close_lists_the_sections_neither_cited_nor_built(folder):
    core = " ".join((ROOT / folder / "skills/horizon/SKILL.md").read_text().split())
    assert "cite every section heading" not in core  # no citing up front
    assert "`close` lists each section heading" in core
    assert "the work does not define" in core
    assert "saying why, if it is out of scope" in core  # a prose section is cited, not ruled out
    assert "matches the code the request gives" in core
    assert "Disputed Rk:" in core


# --- fresh review of 1c8d4df ---------------------------------------------------------------------------

def test_only_named_markdown_files_are_checked_and_not_ones_the_goal_changes(project):
    p, cli, _ = project
    (p / "src" / "app.py").write_text("# Copyright 2024 Acme\n" + "x = 1\n" * 250 + "# helpers\n")
    (p / "CHANGELOG.md").write_text("# Changelog\n" + FILLER + "## 1.0.0 (2024-01-01)\n- first\n")
    slug, gates, out = _start(p, cli, 'R1 [gate] "parse works"\n',
                              request="Fix the crash in src/app.py and add an entry to CHANGELOG.md\n")
    code, out = cli("close", slug)
    text = " ".join(out["missing"])
    assert "1.0.0 (2024-01-01)" in text and "Copyright" not in text and "helpers" not in text  # no .py comments
    (p / "CHANGELOG.md").write_text("# Changelog\n" + FILLER + "## 1.0.1\n- fix\n\n## 1.0.0 (2024-01-01)\n- first\n")
    code, out = cli("close", slug)
    assert not any("cite" in m for m in out["missing"]), out  # a file the goal writes is not an input


def test_only_requirement_lines_cite_and_by_whole_words():
    import sections
    text = "# T\n" + FILLER + "## Use\nx\n## Install\ny\n"
    assert [h for _, h in sections.uncited(text, 'R1 [gate] "users reinstall it"\n')] == ["Use", "Install"]
    assert [h for _, h in sections.uncited(text, '# Use\n# Install\nR1 [gate] "works"\n')] == ["Use", "Install"]
    assert sections.uncited(text, 'R1 [gate] "Use: it works"\nR2 [review] "Install it"\n') == []


def test_a_tilde_fence_inside_a_backtick_fence_does_not_hide_later_headings():
    import sections
    text = "# T\n## A\n```\n~~~\n## Not a heading\n```\n## After\n   ## Indented\n"
    assert [h for _, h in sections.leaf_headings(text)] == ["A", "After", "Indented"]


def test_a_fix_in_a_hidden_file_or_a_test_counts_as_changing_the_work(project):
    p, cli, _ = project
    slug, gates = _failed_round(p, cli)
    (p / ".github").mkdir()
    (p / ".github" / "ci.yml").write_text("on: push\n")
    code, out = cli("close", slug)
    assert code == 0 and out["complete"], out


def test_a_requirements_txt_edit_clears_the_plateau(project):
    p, cli, start_message = project
    cli("start", "Build the tool", "--gate", "true")
    start_message()
    start_message()
    (p / "requirements.txt").write_text("requests\n")
    assert "Plateau" not in start_message()

# --- genericfix (0519b6e): a section the work defines is covered ------------------------------------------
# More-Itertools' 181 sections were mostly one function each; citing each one in a requirement line cost a
# session and a 193-line requirements file re-read every session after.

CODE_SPEC = ("# Lib\n\n" + FILLER + "\n## `parse()` Function\n\nReads a line.\n\n## `render_table()` Function\n\n"
             "Prints rows.\n\n## `Store` Class\n\nKeeps rows.\n\n## `pkg/_vendor` Layout\n\nThe vendored copy.\n\n"
             "## Overview\n\nWhat it is.\n")


def test_a_section_whose_code_name_the_work_defines_needs_no_citing():
    import sections
    reqs = 'R1 [gate] "Overview: what it is"\n'
    assert [h for _, h in sections.uncited(CODE_SPEC, reqs)] == [
        "`parse()` Function", "`render_table()` Function", "`Store` Class", "`pkg/_vendor` Layout"]
    defined = {"parse", "Store", "pkg/_vendor", "Overview"}
    assert [h for _, h in sections.uncited(CODE_SPEC, reqs, defined)] == ["`render_table()` Function"]
    # a heading with no code name still needs its words in a requirement line
    assert [h for _, h in sections.uncited(CODE_SPEC, 'R1 [gate] "works"\n', defined)] == [
        "`render_table()` Function", "Overview"]
    # a dotted name is covered when the work defines each part
    text = "# T\n" + FILLER + "## `more_itertools.chunked`\nx\n"
    assert sections.uncited(text, "R1 x\n", {"more_itertools", "chunked"}) == []
    assert sections.uncited(text, "R1 x\n", {"chunked"}) != []


def test_the_work_defines_what_its_source_defines_and_the_paths_it_has(tmp_path):
    import sections
    p = tmp_path / "w "
    (p / "pkg" / "_vendor" / "lib").mkdir(parents=True)
    (p / "pkg" / "_vendor" / "lib" / "__init__.py").write_text("")
    (p / "pkg" / "core.py").write_text("import os\n\nMAX_ROWS = 3\n\nclass Store:\n    def render(self):\n"
                                       "        return chunked(1)\n\nasync def fetch():\n    pass\n")
    (p / "web.js").write_text("export function mountRoutes(app) {}\nconst helperFn = () => 1;\n")
    (p / "tests").mkdir()
    (p / "tests" / "test_core.py").write_text("def only_in_tests():\n    pass\n")
    (p / "docs").mkdir()
    (p / "docs" / "notes.md").write_text("def only_in_docs():\n")
    (p / "gate").mkdir()
    (p / "gate" / "check.py").write_text("def only_in_gate():\n    pass\n")
    _git(p, "init", "-q")
    names = sections.defined_names(p, skip=[p / "gate"])
    assert {"MAX_ROWS", "Store", "render", "fetch", "mountRoutes", "helperFn"} <= names
    assert {"pkg/_vendor", "pkg/_vendor/lib", "pkg/core.py", "core.py", "_vendor", "pkg.core",
            "pkg._vendor.lib"} <= names
    assert not {"chunked", "only_in_tests", "only_in_docs", "only_in_gate", "os"} & names


def test_close_counts_a_section_the_work_defines_as_covered(project):
    p, cli, _ = project
    (p / "DESIGN.md").write_text(CODE_SPEC)
    slug, gates, out = _start(p, cli, 'R1 [gate] "Overview: what it is"\n')
    (p / "pkg" / "_vendor").mkdir(parents=True)
    (p / "pkg" / "_vendor" / "__init__.py").write_text("")
    (p / "src" / "store.py").write_text("class Store:\n    pass\n")
    code, out = cli("close", slug)
    text = " ".join(out["missing"])
    assert code == 1 and "render_table" in text, out
    assert "Store" not in text and "_vendor" not in text
    assert "Build or fix each" in text
    assert not out.get("review_brief"), out
    (p / "src" / "table.py").write_text("def render_table(rows):\n    return rows\n")
    code, out = cli("close", slug)
    assert not any("render_table" in m for m in out["missing"]), out


def test_a_section_the_project_defined_before_the_goal_still_needs_a_line(project):
    # review of d0513fe: in a long spec of changes to an existing codebase, every section names code that is
    # already there, so a definition alone must not cover it.
    p, cli, _ = project
    (p / "DESIGN.md").write_text(CODE_SPEC)  # the fixture's src/tool.py already defines parse
    slug, gates, out = _start(p, cli, 'R1 [gate] "Overview: what it is"\n')
    code, out = cli("close", slug)
    assert "`parse()` Function" in " ".join(out["missing"]), out


def test_a_path_heading_needs_that_path_and_a_dotted_name_each_part(tmp_path):
    # review of d0513fe: a js/ folder covered every missing .js file, a folder named in a path covered the path,
    # and any close method covered Server.close.
    import sections
    p = tmp_path / "w "
    for name in ("static/js/app.js", "more_itertools/more.py", "src/main/java/com/x/Store.java"):
        (p / name).parent.mkdir(parents=True, exist_ok=True)
        (p / name).write_text("")
    (p / "client.py").write_text("class Client:\n    def close(self):\n        pass\n")
    _git(p, "init", "-q")
    defined = sections.defined_names(p)
    headings = ["`static/js/cart.js` Module", "`checkout.js`", "`com/x/Missing.java`", "`more_itertools/missing.py`",
                "`Server.close()`", "`static/js/app.js`", "`app.js`", "`Store` Class", "`Client.close()`",
                "`more_itertools.more`", "`com/x/Store.java`"]
    text = "# T\n" + FILLER + "".join(f"## {h}\nx\n" for h in headings)
    assert [h for _, h in sections.uncited(text, "R1 x\n", defined)] == headings[:5]


def test_definitions_are_read_in_other_languages_and_not_from_config(tmp_path):
    import sections
    p = tmp_path / "w "
    (p / "src" / "lib_pkg").mkdir(parents=True)
    files = {"Main.java": "public class Store {\n    private static final int LIMIT = 3;\n}\n",
             "app.kt": "fun mountRoutes() {}\ninternal class Router\n",
             "view.swift": "public func render() {}\n",
             "api.php": "<?php\nclass Api {\n    public function fetchAll() {}\n}\n",
             "lib.rs": "pub const fn limit() -> u8 { 3 }\npub(crate) struct Row;\n",
             "pyproject.toml": "[project]\nname = \"x\"\nversion = \"1\"\n",
             "src/lib_pkg/core.py": ""}
    for name, body in files.items():
        (p / name).write_text(body)
    _git(p, "init", "-q")
    names = sections.defined_names(p)
    assert {"Store", "mountRoutes", "Router", "render", "Api", "fetchAll", "limit", "Row", "lib_pkg.core"} <= names
    assert not {"fn", "name", "version", "public"} & names

# --- coverfix (ee28273): "Name Class" headings, and code the request gives ----------------------------------
# fastapi-users: close listed "Strategy Class" six sessions running, because "Strategy" read as a plain word; the
# spec gives `class Strategy(Protocol, Generic[...])` and the build had `class Strategy(ABC)`, so the official
# tests' setup failed. About half the listed headings were prose, which buried the ones that name code.

def test_a_name_before_class_or_function_in_a_heading_is_a_code_name():
    import sections
    text = ("# T\n" + FILLER + "## Authenticator Class\nx\n## Supported Transport Methods\ny\n"
            "## The Class Hierarchy\nz\n## JWT Strategy Function\nw\n")
    assert [h for _, h in sections.uncited(text, "R1 x\n", {"Authenticator", "Strategy"})] == [
        "Supported Transport Methods", "The Class Hierarchy"]
    # the name finds the work's code, but citing still needs the heading's words: "strategy" is a common word
    assert [h for _, h in sections.uncited(text, 'R1 [gate] "Authenticator handles login"\n')] == [
        "Authenticator Class", "Supported Transport Methods", "The Class Hierarchy", "JWT Strategy Function"]
    assert [h for _, h in sections.uncited(text, 'R1 [gate] "Authenticator class: handles login"\n')] == [
        "Supported Transport Methods", "The Class Hierarchy", "JWT Strategy Function"]


GIVEN_SPEC = ("# Lib\n\n" + FILLER + "\n## Strategy Class\n\nThe protocol.\n\n```python\n"
              "class Strategy(Protocol, Generic[UP, ID]):\n    async def read_token(self, token, user_manager): ...\n"
              "```\n\n## JWTStrategy Class\n\n```python\nclass JWTStrategy(Strategy[UP, ID], Generic[UP, ID]):\n"
              "    def __init__(self, secret, lifetime_seconds, token_audience=None): ...\n```\n\n"
              "## `chunked()` Function\n\n```python\ndef chunked(iterable, n, strict=False): ...\n```\n\n"
              "## `build()` Function\n\n```python\ndef build(name, *, verbose=False): ...\n```\n\n"
              "## Notes\n\nProse.\n")


def _given_project(tmp_path, work, spec=GIVEN_SPEC):
    p = tmp_path / "w "
    (p / "pkg").mkdir(parents=True)
    (p / "pkg" / "core.py").write_text(work)
    (p / "SPEC.md").write_text(spec)
    gates = p / ".claude" / "gates" / "g"
    gates.mkdir(parents=True)
    (gates / "request.md").write_text("Build what SPEC.md describes\n")
    (gates / "requirements.md").write_text('R1 [review] "Notes: prose"\n')
    _git(p, "init", "-q")
    return p, gates


def test_a_section_whose_given_code_the_work_defines_differently_is_listed_with_both(tmp_path):
    import sections
    p, gates = _given_project(tmp_path, "from abc import ABC\nfrom typing import Generic\n\n"
                                        "class Strategy(ABC):\n    pass\n\n"
                                        "class JWTStrategy(Strategy, Generic[UP, ID]):\n"
                                        "    def __init__(self, secret, lifetime_seconds, token_audience=None):\n"
                                        "        pass\n\n"
                                        "def chunked(iterable, n):\n    pass\n\n"
                                        "def build(name, **options):\n    pass\n")
    found = {m["heading"]: m for m in sections.uncited_in_goal(str(gates), str(p), skip=[str(gates)])}
    assert set(found) == {"Strategy Class", "`chunked()` Function"}  # JWTStrategy and build() agree
    assert found["Strategy Class"]["kind"] == "differs"
    assert "Protocol" in found["Strategy Class"]["detail"] and "ABC" in found["Strategy Class"]["detail"]
    assert "strict" in found["`chunked()` Function"]["detail"]
    text = sections.message(list(found.values()))
    assert text.index("defines differently") < text.index("chunked")
    assert "Build or fix each" in text


def test_sections_are_listed_as_differing_code_then_missing_code_then_prose():
    import sections
    missing = [{"file": "S.md", "line": 3, "heading": "Notes", "kind": "prose"},
               {"file": "S.md", "line": 2, "heading": "`render()` Function", "kind": "code"},
               {"file": "S.md", "line": 1, "heading": "Strategy Class", "kind": "differs",
                "detail": "the request has `class Strategy(Protocol)`, the work `class Strategy(ABC)`"}]
    text = sections.message(missing)
    assert text.index("Strategy Class") < text.index("render()") < text.index("Notes")


def test_a_base_the_work_inherits_through_its_own_class_agrees_and_a_method_shown_alone_is_a_method(tmp_path):
    # dry run on the coverfix builds: the work's own exception base, and a spec showing `def parse(self, url)`
    # outside its class, were reported as differing
    import sections
    spec = ("# Lib\n\n" + FILLER + "\n## `LibError` Exception\n\n```python\nclass LibError(Exception): ...\n```\n\n"
            "## `parse()` Method\n\n```python\ndef parse(self, url: str) -> str: ...\n```\n")
    p, gates = _given_project(tmp_path, "class Base(Exception):\n    pass\n\nclass LibError(Base):\n    pass\n\n"
                                        "def parse(file_path):\n    pass\n\n"
                                        "class UrlParser:\n    def parse(self, url):\n        pass\n", spec)
    assert sections.uncited_in_goal(str(gates), str(p), skip=[str(gates)]) == []
