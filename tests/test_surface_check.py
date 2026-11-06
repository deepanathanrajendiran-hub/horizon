"""Every module and public name the request gives must import before a goal completes (exp/surface-check, 2026-11-06).

E2E-SWE spectester (Horizon 16675ae, fixcheck's 10 tasks): dnachisel and mapillary_tools scored 0 in all 10 sessions,
claripy for 7 and barf for 3. Each time the hidden tests stopped at collection on an ImportError: a module the spec
names (mapillary_tools: `uploader`, `exif_read`, `geotag.image_extractors.exif`) or a name the package should export
was missing. One missing module fails every test that imports it, whatever else works. Horizon's `surface` gate checks
exactly that, but only with `start --surface-spec`, and no build in keepworking or spectester passed it. Now `start`
writes it whenever the request names a spec for a Python package, and `close` runs it with the gate's interpreter, so
it holds whatever the gate command runs."""
import shlex
import sys

from test_e2eswe_fixes import project  # noqa: F401  (the fixture is used by name)

import surface

SPEC = """# Build `toolpkg`

`toolpkg` parses lines. Install it with `pip install -e .` (see setup.sh).

## Parsing

`toolpkg.parse(line)` returns the line. `toolpkg.reader.Reader` reads files.

```python
from toolpkg import parse
from toolpkg.reader import Reader
```
"""
PASSING = "def test_r1_parse_returns_the_line():\n    assert True\n"


def _begin(p, cli, request="Build the library described in SPEC.md\n", spec=SPEC, gate=None, extra=()):
    if spec is not None:
        (p / "SPEC.md").write_text(spec)
    gates = p / ".claude" / "gates" / "g"
    gates.mkdir(parents=True, exist_ok=True)
    (gates / "request.md").write_text(request)
    (gates / "requirements.md").write_text('R1 [gate] "parse() returns the line"\n')
    (gates / "test_request.py").write_text(PASSING)
    command = gate or (f"{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider "
                       f"{shlex.quote(str(gates / 'test_request.py'))}")
    code, out = cli("start", "Build the tool", "--gate-path", str(gates), "--gate", command, *extra)
    assert code == 0, out
    return out, gates


def _build(p, reader=True):
    (p / "toolpkg").mkdir(exist_ok=True)
    (p / "toolpkg" / "__init__.py").write_text("def parse(line):\n    return line\n")
    if reader:
        (p / "toolpkg" / "reader.py").write_text("class Reader:\n    pass\n")


# --- which package, from which file ---------------------------------------------------------------------------

def test_the_package_is_the_name_the_spec_uses_most_in_imports_and_dotted_code():
    assert surface.guess_package(SPEC) == "toolpkg"


def test_a_file_name_or_a_standard_module_is_never_the_package():
    text = "Run `setup.sh` and `setup.sh` again. Use `os.path` and `json.dumps`, `json.loads`.\n"
    assert surface.guess_package(text) is None


def test_a_prose_spec_with_no_import_lines_still_names_its_package():
    text = ("# Build `geo_tools`\n\n- `geo_tools.uploader` uploads.\n- `geo_tools.exif_read` reads EXIF.\n"
            "- `geo_tools.camm.camm_parser` parses CAMM.\n")
    assert surface.guess_package(text) == "geo_tools"


def test_an_all_block_under_a_heading_that_names_a_submodule_lists_that_submodules_names():
    # kafka-python's spec: `__all__` under "## 13. Admin API (`kafka.admin`)", after a constructor's `__init__`,
    # was read as the top-level package's, so 25 admin names were wrongly required of `kafka` itself
    text = ("# Build `kaf`\n\n## Admin API (`kaf.admin`)\n\n`AdminClient.__init__(self, config)` takes a config.\n\n"
            "Public exports:\n```python\n# the module's names\n__all__ = [\n    'AdminClient', 'NewTopic',\n]\n```\n")
    names = surface.refs(text, "kaf")
    assert "kaf.admin.AdminClient" in names and "kaf.admin.NewTopic" in names, names
    assert "kaf.AdminClient" not in names and "kaf.NewTopic" not in names, names


def test_an_all_block_for_the_package_init_still_lists_top_level_names():
    text = "# Build `kaf`\n\nThe package's `__init__.py`:\n```python\n__all__ = ['Producer', 'Consumer']\n```\n"
    names = surface.refs(text, "kaf")
    assert "kaf.Producer" in names and "kaf.Consumer" in names, names


# --- start writes the gate -------------------------------------------------------------------------------------

def test_start_writes_the_surface_gate_from_the_spec_the_request_names(project):
    p, cli = project
    cli.new_session("s1")
    out, gates = _begin(p, cli)
    text = (gates / surface.FILE).read_text()
    assert "toolpkg.reader.Reader" in text and "toolpkg.parse" in text, text
    assert out["surface_gate"]["package"] == "toolpkg" and out["surface_gate"]["names"] >= 2, out


def test_start_writes_no_surface_gate_when_the_request_names_no_python_package(project):
    p, cli = project
    cli.new_session("s1")
    out, gates = _begin(p, cli, request="Make parse() return the line it is given\n", spec=None)
    assert not (gates / surface.FILE).exists() and "surface_gate" not in out, out


def test_start_keeps_a_surface_gate_the_agent_wrote(project):
    p, cli = project
    cli.new_session("s1")
    gates = p / ".claude" / "gates" / "g"
    gates.mkdir(parents=True)
    (gates / surface.FILE).write_text("def test_mine():\n    assert True\n")
    _begin(p, cli)
    assert (gates / surface.FILE).read_text() == "def test_mine():\n    assert True\n"


# --- close holds it, whatever the gate command runs ----------------------------------------------------------

def test_close_refuses_while_a_module_the_spec_names_does_not_import(project):
    p, cli = project
    cli.new_session("s1")
    _begin(p, cli)
    _build(p, reader=False)
    code, out = cli("close", "build-the-tool")
    assert code == 1 and not out["complete"], out
    assert any("toolpkg.reader" in m for m in out["missing"]), out


def test_close_completes_once_every_name_imports(project):
    p, cli = project
    cli.new_session("s1")
    _begin(p, cli)
    _build(p)
    code, out = cli("close", "build-the-tool")
    assert code == 0 and out["complete"], out


def test_the_interpreter_is_found_inside_a_gate_script(project):
    p, cli = project
    cli.new_session("s1")
    gates = p / ".claude" / "gates" / "g"
    gates.mkdir(parents=True)
    script = gates / "gate.sh"
    script.write_text(f"#!/bin/sh\n{shlex.quote(sys.executable)} -m pytest -q -p no:cacheprovider "
                      f"{shlex.quote(str(gates / 'test_request.py'))}\n")
    _begin(p, cli, gate=f"sh {shlex.quote(str(script))}")
    _build(p, reader=False)
    code, out = cli("close", "build-the-tool")
    assert not out["complete"] and any("toolpkg.reader" in m for m in out["missing"]), out


def test_with_no_interpreter_in_sight_the_surface_is_reported_and_never_blocks(project):
    p, cli = project
    cli.new_session("s1")
    gates = p / ".claude" / "gates" / "g"
    gates.mkdir(parents=True)
    (gates / "gate.sh").write_text("#!/bin/sh\ntrue\n")
    _begin(p, cli, gate=f"sh {shlex.quote(str(gates / 'gate.sh'))}")
    _build(p, reader=False)
    code, out = cli("close", "build-the-tool")
    assert out["complete"], out
    assert "toolpkg.reader" in out.get("surface_advisory", ""), out


def test_the_next_session_start_lists_the_names_the_last_check_found_missing(project):
    p, cli = project
    cli.new_session("s1")
    _begin(p, cli)
    _build(p, reader=False)
    cli("check", "build-the-tool", "--did", "Built parse", "--next", "Build the reader")
    context = cli.new_session("s2")["hookSpecificOutput"]["additionalContext"]
    assert "did not import" in context and "toolpkg.reader" in context, context


def test_check_lists_the_names_that_do_not_import(project):
    p, cli = project
    cli.new_session("s1")
    _begin(p, cli)
    _build(p, reader=False)
    code, out = cli("check", "build-the-tool", "--did", "Built parse", "--next", "Build the reader")
    assert "toolpkg.reader" in out.get("surface_missing", ""), out
