"""A goal for a Python package completes only once the package installs and imports from outside the project
(exp/install-gate, 2026-11-06).

E2E-SWE paper run: 6 of 94 plain builds scored 0, and plain lost 61% of its points to tests that could not load. In
each task's grading image, plain's h2 and rpyc failed `pip install -e . --no-build-isolation` (h2 had only setup.py,
rpyc broken metadata), and plain's transitions and mpmath had no packaging file at all: their setup.sh only checked
the import from inside the folder. Horizon 457791e's mpmath and fiddle had neither setup.sh nor packaging. Every such
build scored 0. Agents test imports from the project folder, where they always work, and their environments have no
pip. The check installs a copy of the project in a throwaway environment that sees the project's own packages, then
imports the package from elsewhere. Since the E2E-SWE full run (2026-11-08) a setup script the request names is the
install, run in the copy; otherwise pip is."""
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


PIP_ONLY = "# Build `toolpkg`\n\nInstall it with `pip install -e .`.\n"  # names no setup script


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
    found = install_gate.run(p, "toolpkg", sys.executable, request_text=PIP_ONLY)
    assert not found["ok"] and found["exit"] not in (0, None), found
    assert "pip install" in install_gate.message(found), found


def test_an_install_that_leaves_the_package_unimportable_from_elsewhere_fails(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    _build(p)
    installable(p)
    (p / "backend.py").write_text(BACKEND.replace('os.path.dirname(os.path.abspath(__file__)) + "\\n"', '"/nowhere\\n"'))
    found = install_gate.run(p, "toolpkg", sys.executable, request_text=PIP_ONLY)
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


def test_the_named_setup_script_runs_in_a_copy_with_the_throwaway_environment_first_and_writes_nothing_else(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    _build(p)
    installable(p)
    (p / "setup.sh").write_text((p / "setup.sh").read_text() + "touch ran-here\n")
    before = _site_files()
    found = install_gate.run(p, "toolpkg", sys.executable, request_text=SPEC)
    assert found["ok"] and found.get("script_exit") == 0, found
    assert not (p / "ran-here").exists()  # it ran in the copy
    assert _site_files() == before  # its pip installed into the throwaway environment


# --- the setup script the request names is how the project installs (E2E-SWE full run, 2026-11-08) ---------------
# The candidate's sapp a wrote a setup.sh that tried pip, failed on `import pip, setuptools` in the grading image,
# checked the import from src/ and exited 0 with "no install performed"; authlib b's and mpmath a's only checked
# imports from the project folder. The grader runs setup.sh, then imports from elsewhere: each scored 0. The check
# never ran setup.sh, and the agents took setup.sh, which "passes", as the install.

NOT_INSTALLING = "#!/bin/sh\ncd \"$(dirname \"$0\")\"\npython -c 'import toolpkg' && echo 'toolpkg is ready'\n"


def test_a_setup_script_that_exits_0_without_installing_fails_the_check(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    _build(p)
    installable(p)
    (p / "setup.sh").write_text(NOT_INSTALLING)
    found = install_gate.run(p, "toolpkg", sys.executable, request_text=SPEC)
    assert not found["ok"] and found.get("script_exit") == 0, found
    assert "ModuleNotFoundError" in (found.get("script_import_error") or ""), found
    text = install_gate.message(found)
    assert "setup.sh" in text and "installs nothing" in text, text


def test_a_setup_script_that_fails_is_reported_with_its_exit_and_output(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    _build(p)
    installable(p)
    (p / "setup.sh").write_text("#!/bin/sh\necho 'error: no backend here' >&2\nexit 3\n")
    found = install_gate.run(p, "toolpkg", sys.executable, request_text=SPEC)
    assert not found["ok"] and found.get("script_exit") == 3, found
    text = install_gate.problem(found)
    assert "setup.sh" in text and "exit 3" in text and "no backend here" in text, text


def test_a_setup_script_that_installs_is_the_install_even_when_pip_alone_would_fail(tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    _build(p)
    installable(p)
    (p / "backend.py").write_text("def get_requires_for_build_editable(config_settings=None):\n"
                                  "    raise RuntimeError('broken metadata')\n")
    (p / "setup.sh").write_text(
        "#!/bin/sh\nset -e\ncd \"$(dirname \"$0\")\"\npython -c \"import os, sysconfig; "
        "open(os.path.join(sysconfig.get_paths()['purelib'], 'toolpkg.pth'), 'w').write(os.getcwd() + chr(10))\"\n")
    found = install_gate.run(p, "toolpkg", sys.executable, request_text=SPEC)
    assert found["ok"], found

def test_a_setup_script_gets_the_throwaway_python_as_PYTHON(tmp_path):
    # mpmath a's setup.sh: PY=${PYTHON:-/Users/.../venvs/mpmath/bin/python}, the agent's own interpreter, which the
    # grading image does not have; scripts that take $PYTHON get the throwaway one, not the agent's environment
    p = tmp_path / "proj"
    p.mkdir()
    _build(p)
    installable(p)
    (p / "setup.sh").write_text("#!/bin/sh\nset -e\ncd \"$(dirname \"$0\")\"\n"
                                "\"${PYTHON:?PYTHON is not set}\" -m pip install -e . --no-build-isolation\n")
    found = install_gate.run(p, "toolpkg", sys.executable, request_text=SPEC)
    assert found["ok"], found

def test_a_setup_script_that_installs_needs_no_pyproject(tmp_path):
    # plain barf a (70% of the hidden tests) and b: setup.sh wrote a .pth into the interpreter's site-packages and
    # the package imported from anywhere in the grading image; the check refused both for want of a pyproject.toml
    p = tmp_path / "proj"
    p.mkdir()
    _build(p)
    (p / "setup.sh").write_text(
        "#!/bin/sh\nset -e\ncd \"$(dirname \"$0\")\"\n\"${PYTHON:-python3}\" -c \"import os, site; "
        "open(os.path.join(site.getsitepackages()[0], 'toolpkg-dev.pth'), 'w').write(os.getcwd() + chr(10))\"\n")
    found = install_gate.run(p, "toolpkg", sys.executable, request_text=SPEC)
    assert found["ok"] and not found["pyproject"], found
    assert install_gate.problem(found) == "", install_gate.problem(found)


def test_no_pyproject_and_a_setup_script_that_installs_nothing_are_both_reported(tmp_path):
    # authlib b: "no pyproject.toml, so pip install -e cannot work here; setup.sh (the SPEC's installer) is the
    # install path and it passes" (its note, session 6 of 10, each of which the check refused)
    p = tmp_path / "proj"
    p.mkdir()
    _build(p)
    (p / "setup.sh").write_text(NOT_INSTALLING)
    found = install_gate.run(p, "toolpkg", sys.executable, request_text=SPEC)
    assert not found["ok"], found
    text = install_gate.problem(found)
    assert "pyproject.toml" in text and "installs nothing" in text, text


def test_the_problem_says_the_check_has_its_own_environment_so_the_agents_is_not_the_cause():
    # authlib b and mpmath a recorded the refusal as an environment limitation and built on for 4-6 sessions
    text = install_gate.problem({"pyproject": False, "missing_scripts": []})
    assert "own throwaway environment" in text and "has pip" in text and "not the cause" in text, text
    assert "build-backend" in text, text


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
