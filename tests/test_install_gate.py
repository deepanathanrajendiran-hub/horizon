"""A goal for a Python package completes only once the package installs and imports from outside the project
(exp/install-gate, 2026-11-06).

E2E-SWE paper run: 6 of 94 plain builds scored 0, and plain lost 61% of its points to tests that could not load. In
each task's grading image, plain's h2 and rpyc failed `pip install -e . --no-build-isolation` (h2 had only setup.py,
rpyc broken metadata), and plain's transitions and mpmath had no packaging file at all: their setup.sh only checked
the import from inside the folder. Horizon 457791e's mpmath and fiddle had neither setup.sh nor packaging. Every such
build scored 0. Agents test imports from the project folder, where they always work, and their environments have no
pip. The check installs a copy of the project with pip in a throwaway environment that sees the project's own
packages, then imports the package from elsewhere. It never runs the project's own scripts, which can name and write
to the agent's real environment."""
import shlex
import sys
from pathlib import Path

from test_e2eswe_fixes import project  # noqa: F401  (the fixture is used by name)
from installable_project import BACKEND, installable
from test_surface_check import SPEC, _begin, _build

import install_gate
import surface

def _site_files() -> set:
    import site
    return {f for d in site.getsitepackages() for f in Path(d).rglob("*")}


# --- which package: a spec's import alias and names imported from it ---------------------------------------------

def test_an_import_alias_counts_for_the_module_it_names():
    spec = ("# Fiddle\n\n```python\nimport fiddle as fdl\n```\n\n" + "".join(f"`fdl.Name{i}` " for i in range(9))
            + "\n`fdl.Config` and `fdl.Partial` build things.\n")
    assert surface.guess_package(spec) == "fiddle"
    assert "fiddle.Config" in surface.refs(spec, "fiddle")


def test_a_name_imported_from_the_package_is_not_the_package():
    spec = ("# mpmath\n\n```python\nfrom mpmath import mp, mpf\n```\n\n" + "".join(f"`mp.attr{i}` " for i in range(9))
            + "\n`mpmath.sqrt` and `mpmath.pi` exist.\n")
    assert surface.guess_package(spec) == "mpmath"


# --- the check itself --------------------------------------------------------------------------------------------

def test_a_project_with_a_working_pyproject_installs_and_imports_from_elsewhere(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    _build(p)
    installable(p)
    found = install_gate.run(p, "toolpkg", sys.executable, request_text=SPEC)
    assert found["ok"], found


def test_a_project_with_no_pyproject_does_not_install_even_if_its_setup_script_succeeds(tmp_path):
    # plain transitions and mpmath: setup.sh exited 0 after importing the package from inside the folder
    p = tmp_path / "proj"
    p.mkdir()
    _build(p)
    (p / "setup.sh").write_text("#!/bin/sh\npython -c 'import toolpkg' && echo ok\n")
    found = install_gate.run(p, "toolpkg", sys.executable, request_text=SPEC)
    assert not found["ok"] and "pyproject.toml" in install_gate.message(found), found


def test_a_setup_script_the_request_names_must_exist(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    _build(p)
    installable(p)
    (p / "setup.sh").unlink()
    found = install_gate.run(p, "toolpkg", sys.executable, request_text=SPEC)
    assert not found["ok"] and "setup.sh" in install_gate.message(found), found


def test_a_pyproject_pip_cannot_build_is_reported_with_pips_error(tmp_path):
    # plain rpyc: "Encountered error while generating package metadata"
    p = tmp_path / "proj"
    p.mkdir()
    _build(p)
    installable(p)
    (p / "backend.py").write_text("def get_requires_for_build_editable(config_settings=None):\n"
                                  "    raise RuntimeError('broken metadata')\n")
    found = install_gate.run(p, "toolpkg", sys.executable, request_text=SPEC)
    assert not found["ok"] and found["exit"] not in (0, None), found
    assert "pip install" in install_gate.message(found), found


def test_an_install_that_leaves_the_package_unimportable_from_elsewhere_fails(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    _build(p)
    installable(p)
    (p / "backend.py").write_text(BACKEND.replace('os.path.dirname(os.path.abspath(__file__)) + "\\n"', '"/nowhere\\n"'))
    found = install_gate.run(p, "toolpkg", sys.executable, request_text=SPEC)
    assert not found["ok"] and "ModuleNotFoundError" in (found.get("import_error") or ""), found

def test_a_wrongly_guessed_package_name_imports_the_projects_own_package_instead(tmp_path):
    # E2E-SWE: the spec's package was guessed as `a` (confection), `should` (grappa), `cache` (diskcache); those
    # builds installed and scored 82-100, so importing the guess would have refused a working project
    p = tmp_path / "proj"
    p.mkdir()
    _build(p)
    installable(p)
    found = install_gate.run(p, "should", sys.executable, request_text=SPEC)
    assert found["ok"] and found["imported"] == ["toolpkg"], found


def test_the_check_never_runs_the_projects_own_scripts_or_writes_to_the_interpreters_environment(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    _build(p)
    installable(p)
    marker = tmp_path / "setup-ran"
    (p / "setup.sh").write_text(f"#!/bin/sh\ntouch {shlex.quote(str(marker))}\n")
    before = _site_files()
    found = install_gate.run(p, "toolpkg", sys.executable, request_text=SPEC)
    assert found["ok"], found
    assert not marker.exists()
    assert _site_files() == before


def test_a_request_that_says_nothing_about_installing_gets_no_install_check(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    _build(p)
    assert not install_gate.applies("# Build `toolpkg`\n\n`toolpkg.parse(line)` returns the line.\n")
    assert install_gate.applies(SPEC)

def test_the_message_sends_the_agent_to_check_and_warns_off_a_handmade_install():
    # the install run's smoke build: the agent made its own venv and ran its setup.sh with pip and no --no-index;
    # offline, pip retried the network until the session ended and its processes outlived it
    text = install_gate.message({"pyproject": False, "missing_scripts": []})
    assert "`check`" in text and "--no-index" in text, text


# --- close, check and the next session ---------------------------------------------------------------------------

def test_close_refuses_while_the_package_does_not_install(project):
    p, cli = project
    cli.new_session("s1")
    _begin(p, cli)
    _build(p)
    code, out = cli("close", "build-the-tool")
    assert code == 1 and not out["complete"], out
    assert any("pyproject.toml" in m for m in out["missing"]), out


def test_close_completes_once_the_package_installs(project):
    p, cli = project
    cli.new_session("s1")
    _begin(p, cli)
    _build(p)
    installable(p)
    code, out = cli("close", "build-the-tool")
    assert code == 0 and out["complete"], out


def test_check_and_the_next_session_start_report_the_install_problem(project):
    p, cli = project
    cli.new_session("s1")
    _begin(p, cli)
    _build(p)
    code, out = cli("check", "build-the-tool", "--did", "Built parse", "--next", "Package it")
    assert "pyproject.toml" in out.get("install_problem", ""), out
    context = cli.new_session("s2")["hookSpecificOutput"]["additionalContext"]
    assert "does not install" in context, context
