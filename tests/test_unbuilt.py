"""Names the request gives in code that the work defines nowhere, and every name of a list entry that gives several.

E2E-SWE thin9 run (2026-11-11, exp/thin 250581a): the three builds that fell furthest below the builds before them
had each closed without names their request gives, and none of those names was on Horizon's list:

- mpmath 75.0 to 0: `bessely`, `besseli`, `besselk` and `airybi`. The request lists them as "`besselj(n, z)`,
  `bessely(n, z)`, `besseli(n, z)`, `besselk(n, z)` — the Bessel functions"; the surface gate read the first name
  of an entry. All 8 graded mpmath builds without those four scored 0, plain's among them.
- pysmt 92.6 to 70.4: 11 names (`BVNand`, `Tokenizer`, `get_closer_logic`, ...), given in running text.
- barf 82.6 to 56.5: `recover_cfg_all`.

The run before had the request check, and its first printed probe hit `NameError: name 'besseli' is not defined`;
the session then built all four. Read from the source, the same names can be listed without running anything."""
import shlex
import subprocess
import sys

import pytest

from installable_project import installable
from test_e2eswe_fixes import project  # noqa: F401  (the fixture is used by name)
from test_thin import _context

import surface
import unbuilt

SLUG = "build-the-tool"


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "work"
    (root / "pkg").mkdir(parents=True)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)

    def write(name, text):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    write("pkg/__init__.py", "def parse(line):\n    return line\n")
    return root, write


# --- which names of the request are read ----------------------------------------------------------------------

def test_a_call_and_a_class_like_name_in_code_are_read():
    text = "Use `parse(line)` and `Reader`. `Reader.read(path)` reads; `pkg.io.load(path)` loads.\n"
    assert unbuilt.request_names(text, "pkg") == ["parse", "Reader", "read", "load"]


def test_names_in_running_text_are_read_wherever_they_stand():
    # pysmt: "Functions: `get_logic_by_name(name)`, `get_logic(**kwargs)`, `most_generic_logic(...)`."
    text = "Functions: `get_logic_by_name(name)`, `get_logic(**kwargs)`, `most_generic_logic(logics)`.\n"
    assert unbuilt.request_names(text) == ["get_logic_by_name", "get_logic", "most_generic_logic"]


def test_a_call_inside_a_call_is_read_too():
    # pysmt: "`BVNand(a,b)` becomes `BVNot(BVAnd(a,b))`"
    assert unbuilt.request_names("`BVNand(a,b)` becomes `BVNot(BVAnd(a,b))`\n") == ["BVNand", "BVNot", "BVAnd"]


def test_code_blocks_are_not_read():
    text = "Example:\n\n```python\nresult = compute(load_rows())\nprint(result)\n```\n\nCall `parse(line)`.\n"
    assert unbuilt.request_names(text) == ["parse"]


def test_builtins_typing_private_short_and_constant_names_are_left_out():
    text = ("`len(x)`, `isinstance(a, b)`, `Optional`, `ValueError`, `_helper(x)`, `f(x)`, `GET`, `MAX_SIZE`, "
            "`True`, `self.run()`, `parse(x)`\n")
    assert unbuilt.request_names(text) == ["run", "parse"]


def test_another_librarys_dotted_call_is_left_out():
    text = "`np.array(rows)`, `os.path.join(a, b)`, `asyncpg.create_pool(dsn)`, `pkg.connect(dsn)`\n"
    assert unbuilt.request_names(text, "pkg") == ["connect"]


def test_a_formula_is_not_code():
    assert unbuilt.request_names("`Γ(z)`, `exp(iθ)`, `zeta(s)`\n") == ["zeta"]


# --- what counts as defined ------------------------------------------------------------------------------------

def test_a_name_no_source_file_defines_is_listed(repo):
    root, write = repo
    text = "`parse(line)`, `bessely(n, z)`, `Tokenizer`\n"
    assert unbuilt.find(text, root, "pkg") == ["bessely", "Tokenizer"]


def test_a_function_class_assignment_import_or_attribute_defines_a_name(repo):
    root, write = repo
    write("pkg/more.py",
          "from collections import OrderedDict as Table\n\n\nclass Tokenizer:\n    def feed(self, text):\n"
          "        self.position = 0\n\n\nbessely = lambda n, z: 0\n")
    text = "`Tokenizer`, `feed(text)`, `bessely(n, z)`, `Table`, `position()`, `missing(x)`\n"
    assert unbuilt.find(text, root, "pkg") == ["missing"]


def test_a_name_registered_as_a_string_counts(repo):
    # A table of handlers or `setattr(cls, "is_" + state, ...)`: the name is in the source as text.
    root, write = repo
    write("pkg/ops.py", "OPERATORS = {'to_equal': lambda a, b: a == b}\n")
    assert unbuilt.find("`to_equal(value)`, `to_contain(value)`\n", root, "pkg") == ["to_contain"]


def test_a_test_that_calls_the_name_does_not_build_it(repo):
    root, write = repo
    write("tests/test_pkg.py", "def bessely(n, z):\n    return 0\n")
    write("pkg/test_inline.py", "def airybi(z):\n    return 0\n")
    assert unbuilt.find("`bessely(n, z)`, `airybi(z)`\n", root, "pkg") == ["bessely", "airybi"]


def test_nothing_is_listed_before_the_work_has_any_source(tmp_path):
    root = tmp_path / "empty"
    root.mkdir()
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    (root / "SPEC.md").write_text("`parse(line)`\n")
    assert unbuilt.find("`parse(line)`, `Reader`\n", root, "pkg") == []


def test_in_another_language_the_word_anywhere_in_the_source_counts(repo):
    root, write = repo
    write("parser.go", "package tool\n\nfunc ParseLine(line string) string {\n\treturn line\n}\n")
    assert unbuilt.find("`ParseLine(line)`, `ParseAll(lines)`\n", root) == ["ParseAll"]


def test_a_long_list_is_cut_with_a_count():
    assert unbuilt.listing([f"name_{i}" for i in range(45)], limit=40).endswith("name_39, and 5 more")


# --- a goal is told, and `close` never waits for it ------------------------------------------------------------

REQUEST = """# Build `toolpkg`

`toolpkg` parses lines. Install it with `pip install -e .`.

## Parsing

`toolpkg.parse(line)` returns the line. `parse_all(lines)` parses each of them, and `Tokenizer` splits a line.

```python
from toolpkg import parse
print(parse("a"))  # Output: a
```
"""
PASSING = "from toolpkg import parse\n\n\ndef test_parse():\n    assert parse('a') == 'a'\n"


def _goal(p, cli):
    (p / "tests").mkdir(exist_ok=True)
    (p / "tests" / "test_tool.py").write_text(
        PASSING + "".join(f"\n\ndef test_case_{i}():\n    assert True\n" for i in range(1, 10)))
    gates = p / ".claude" / "gates" / "g"
    gates.mkdir(parents=True, exist_ok=True)
    (gates / "request.md").write_text(REQUEST)
    command = f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider tests"
    code, out = cli("start", "Build the tool", "--gate-path", str(gates), "--gate", command)
    assert code == 0, out
    (p / "toolpkg").mkdir(exist_ok=True)
    (p / "toolpkg" / "__init__.py").write_text("def parse(line):\n    return line\n")
    installable(p)


def test_check_and_the_next_session_list_the_names(project):
    p, cli = project
    cli.new_session("s1")
    _goal(p, cli)
    code, out = cli("check", SLUG, "--did", "Built parse", "--next", "Build the rest")
    assert code == 0, out
    said = out.get("unbuilt_names", "")
    assert "(2): parse_all, Tokenizer." in said and "defines them nowhere" in said, out
    context = _context(cli.new_session("s2"))
    assert "parse_all, Tokenizer" in context, context


def test_the_names_are_advice_and_close_does_not_wait_for_them(project):
    p, cli = project
    cli.new_session("s1")
    _goal(p, cli)
    code, out = cli("close", SLUG)
    # Only the request check holds, and it starts with the names.
    assert code == 1 and len(out["missing"]) == 1, out
    assert "part by part" in out["missing"][0] and "Start with these" in out["missing"][0], out
    assert "parse_all, Tokenizer" in out["missing"][0] and "unbuilt_names" not in out, out
    assert cli("check", SLUG, "--did", "Ran each part", "--next", "Close")[0] == 0
    code, out = cli("close", SLUG)
    assert code == 0 and out["complete"], out


def test_nothing_is_said_once_every_name_is_built(project):
    p, cli = project
    cli.new_session("s1")
    _goal(p, cli)
    (p / "toolpkg" / "__init__.py").write_text(
        "def parse(line):\n    return line\n\n\ndef parse_all(lines):\n    return [parse(x) for x in lines]\n\n\n"
        "class Tokenizer:\n    def split(self, line):\n        return line.split()\n")
    code, out = cli("check", SLUG, "--did", "Built all three", "--next", "Close")
    assert code == 0 and "unbuilt_names" not in out, out
    assert "defines them nowhere" not in _context(cli.new_session("s2"))


# --- a list entry that gives several names ---------------------------------------------------------------------

def test_every_call_that_opens_a_list_entry_is_read():
    line = "- `besselj(n, z)`, `bessely(n, z)`, `besseli(n, z)`, `besselk(n, z)` — the Bessel functions"
    assert surface._listed_with(line) == ["besselj", "bessely", "besseli", "besselk"]
    assert surface._listed_with("- `airyai(z)` and `airybi(z)` — the Airy functions") == ["airyai", "airybi"]
    assert surface._listed_with("- `to_name(n)` / `from_name(name)` — e.g. `60`") == ["to_name", "from_name"]


def test_code_further_into_the_sentence_is_not_a_name_of_the_entry():
    line = "- `struveh(n, z)` — the Struve function; `struveh(0, 0)` is `zero(x)`"
    assert surface._listed_with(line) == ["struveh"]
    # No separator: the second span says what the first is ("`FeedSort` `StrEnum`: `TITLE`, `ADDED`").
    assert surface._listed_with("- `FeedSort` `StrEnum`: `TITLE`, `ADDED`.") == ["FeedSort"]
    # A bare lower-case name is a parameter or an attribute, as it is when it opens the entry.
    assert surface._listed_with("- `connect(dsn)`, `timeout`, `Pool` — the entry points") == ["connect", "Pool"]


BLANKET = """# Build `mathpkg`

All functions below are importable from the top-level `mathpkg` package.

## Bessel and Airy functions

- `besselj(n, z)`, `bessely(n, z)`, `besseli(n, z)`, `besselk(n, z)` — the Bessel functions.
- `airyai(z)`, `airybi(z)` — the Airy functions Ai and Bi.
- `struveh(n, z)` — the Struve function.

```python
from mathpkg import besselj
```
"""


def test_the_surface_gate_holds_every_name_of_such_an_entry():
    names = set(surface.refs(BLANKET, "mathpkg"))
    assert {"mathpkg.besselj", "mathpkg.bessely", "mathpkg.besseli", "mathpkg.besselk", "mathpkg.airyai",
            "mathpkg.airybi", "mathpkg.struveh"} <= names, names
