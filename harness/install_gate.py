"""The package must install the way its request says and import from outside the project.

E2E-SWE paper run (exp/install-gate, 2026-11-06): 6 of 94 plain builds scored 0, and tests that could not load cost
plain 61% of its points. In each task's grading image, plain's h2 and rpyc failed `pip install -e .
--no-build-isolation` (h2 shipped only setup.py; rpyc's metadata did not build), and plain's transitions and mpmath had
no packaging file at all: their setup.sh only imported the package from inside the folder and exited 0. Horizon
457791e's mpmath and fiddle had neither setup.sh nor packaging. Each scored 0. Agents test imports from the project
folder, where they always work, and their environments have no pip.

So the check installs a copy of the project, offline, into a throwaway environment that sees the project
interpreter's own packages, and then imports the package from another folder.

The install is the one the request names. E2E-SWE full run (Horizon 5f3bb6a, 2026-11-08): the check ran only pip, so
three of the candidate's four builds at 0 passed or misread it. sapp a's setup.sh tried pip, failed on
`import pip, setuptools` in the grading image, checked the import from src/ and exited 0 ("no install performed");
authlib b's and mpmath a's setup.sh only checked imports from the project folder, and their agents, refused for 4-6
sessions over the missing pyproject.toml, recorded it as their environment's limitation. The grader runs setup.sh,
then imports from elsewhere. So a setup script the request names, when the project has it, runs in the copy with the
throwaway environment's python and pip first on PATH, in place of pip; otherwise pip installs it. The scripts run
only inside the agent's own commands: inside a hook, which Claude Code runs outside the agent's sandbox, the check
refuses to run (E2E-SWE install run: the SessionStart hook ran a gate, which ran ldaptor's setup.sh, which wrote
an install pointer into the task's venv)."""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import surface
import hookguard

RUN_TIMEOUT_S = 240
MAX_COPY_BYTES = 300 * 1024 * 1024
_SKIP = shutil.ignore_patterns(".git", ".claude", "__pycache__", "*.egg-info", ".pytest_cache", ".mypy_cache",
                               ".tox", ".nox", ".venv", "venv", "node_modules")
_INSTALL_WORDS = re.compile(r"\bpip\s+install\b|\bpyproject\.toml\b|\bsetup\.(?:sh|py|cfg)\b|\binstall(?:ed|s|ing|able)?\b",
                            re.I)
_SCRIPT = re.compile(r"(?<![\w./-])((?:setup|install|bootstrap)\.sh)\b")
PIP = ["-m", "pip", "install", "-e", ".", "--no-build-isolation", "--use-pep517"]


def applies(request_text: str) -> bool:
    """Whether the request says the project gets installed (an install command, a packaging file, the word)."""
    return bool(_INSTALL_WORDS.search(request_text or ""))


def named_scripts(request_text: str) -> list[str]:
    """Setup scripts the request names at the project's top level (`setup.sh`)."""
    return sorted(set(_SCRIPT.findall(request_text or "")))


def _size(root: Path) -> int:
    total = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not _SKIP(dirpath, [d])]
        for name in filenames:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
        if total > MAX_COPY_BYTES:
            break
    return total


def _tail(text: str, lines: int = 6) -> str:
    kept = [line.rstrip() for line in text.splitlines() if line.strip()]
    errors = [line for line in kept if re.search(r"\berror\b|ERROR|Error:", line)]
    shown = (errors[:3] + kept[-lines:]) if errors else kept[-lines:]
    return " | ".join(dict.fromkeys(line.strip()[:200] for line in shown))[:900]

_NOT_PACKAGE_DIRS = {"tests", "test", "testing", "docs", "doc", "examples", "example", "scripts", "benchmarks",
                     "benchmark", "build", "dist", "tools", "site-packages"}


def import_names(root: Path, package: str | None) -> list[str]:
    """What to import after installing: the spec's package when the project has it (a package directory or a module,
    at the top or under src/), else every top-level package directory the project has. A guessed name the project
    lacks is not imported (E2E-SWE: specs whose package was guessed as `a`, `should` or `cache` installed fine)."""
    roots = [root / "src", root] if (root / "src").is_dir() else [root]
    found = []
    for base in roots:
        for d in sorted(base.iterdir()):
            if (d.is_dir() and d.name.isidentifier() and d.name not in _NOT_PACKAGE_DIRS
                    and (d / "__init__.py").is_file() and d.name not in found):
                found.append(d.name)
    if package and (package in found or any((base / f"{package}.py").is_file() for base in roots)):
        return [package]
    return found


def _import_error(env_python: str, names: list[str], elsewhere: Path, env: dict) -> str | None:
    """The last line of the error `import <names>` raises in the throwaway environment from another folder."""
    code = "; ".join(f"import {name}" for name in names)
    imported = subprocess.run([env_python, "-I", "-B", "-c", code], cwd=str(elsewhere), capture_output=True,
                              text=True, timeout=120, env=env)
    if imported.returncode == 0:
        return None
    lines = [x for x in (imported.stderr or "").splitlines() if x.strip()]
    return (lines[-1] if lines else "the import failed")[:300]


def run(project: Path, package: str, python: str, request_text: str = "", timeout: int = RUN_TIMEOUT_S) -> dict:
    """{"ok", "missing_scripts", "pyproject", "scripts", "script_exit", "script_failed", "script_output",
    "script_import_error", "exit", "output", "import_error", "error"}. The setup scripts the request names, when the
    project has them, are its install; otherwise `pip install -e . --no-build-isolation` is. `error` is set only when
    the check itself could not run (no venv module, a project too large to copy, a timeout): then it never blocks."""
    project = Path(project)
    result = {"ok": False, "package": package, "python": python, "missing_scripts": [], "pyproject": True,
              "scripts": [], "script_exit": None, "script_failed": None, "script_output": "",
              "script_import_error": None, "exit": None, "output": "", "imported": [], "import_error": None,
              "error": None}
    named = named_scripts(request_text)
    result["missing_scripts"] = [s for s in named if not (project / s).is_file()]
    result["scripts"] = [s for s in named if (project / s).is_file()]
    result["pyproject"] = (project / "pyproject.toml").is_file()
    if not result["pyproject"] and not result["scripts"]:
        return result
    if _size(project) > MAX_COPY_BYTES:
        result["error"] = "the project is too large to copy for the install check"
        return result
    hookguard.refuse("The install check")
    with tempfile.TemporaryDirectory(prefix="horizon-install-") as tmp:
        tmp = Path(tmp)
        copy, env, elsewhere = tmp / "project", tmp / "env", tmp / "elsewhere"
        elsewhere.mkdir()
        bare = {"PATH": f"{env / 'bin'}{os.pathsep}/usr/bin{os.pathsep}/bin", "HOME": str(tmp),
                "TMPDIR": str(tmp), "PIP_NO_INDEX": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1",
                "PIP_NO_INPUT": "1", "PYTHONDONTWRITEBYTECODE": "1", "PYTHONNOUSERSITE": "1",
                "VIRTUAL_ENV": str(env)}
        try:
            shutil.copytree(project, copy, symlinks=True, ignore=_SKIP)
            made = subprocess.run([python, "-m", "venv", str(env)], capture_output=True, text=True,
                                  timeout=timeout, env={**bare, "PATH": os.environ.get("PATH", "")})
            if made.returncode != 0:
                result["error"] = "could not make a throwaway environment: " + _tail(made.stderr or made.stdout, 2)
                return result
            sites = subprocess.run([python, "-c", "import site; print('\\n'.join(site.getsitepackages()))"],
                                   capture_output=True, text=True, timeout=60).stdout
            env_python = str(env / "bin" / "python")
            env_site = subprocess.run([env_python, "-c", "import sysconfig; print(sysconfig.get_paths()['purelib'])"],
                                      capture_output=True, text=True, timeout=60, env=bare).stdout.strip()
            # the project's own packages, read through a path file: nothing is written there
            Path(env_site, "_horizon_project_packages.pth").write_text(sites)
            result["imported"] = import_names(copy, package)
            if result["scripts"]:
                # the install the request names: its own scripts, run in the copy with the throwaway environment's
                # python and pip first on PATH, as whoever uses the project runs them before importing it
                shell = shutil.which("bash") or "/bin/sh"
                script_env = {**bare, "PYTHON": env_python}  # scripts that take $PYTHON get the throwaway one
                outputs = []
                result["script_exit"] = 0
                for script in result["scripts"]:
                    done = subprocess.run([shell, script], cwd=str(copy), capture_output=True, text=True,
                                          timeout=timeout, env=script_env)
                    outputs.append((done.stdout or "") + "\n" + (done.stderr or ""))
                    if done.returncode != 0:
                        result["script_exit"], result["script_failed"] = done.returncode, script
                        break
                result["script_output"] = _tail("\n".join(outputs))
                if result["script_exit"] == 0 and result["imported"]:
                    result["script_import_error"] = _import_error(env_python, result["imported"], elsewhere, bare)
            else:
                done = subprocess.run([env_python, *PIP], cwd=str(copy), capture_output=True, text=True,
                                      timeout=timeout, env=bare)
                result["exit"] = done.returncode
                result["output"] = _tail((done.stdout or "") + "\n" + (done.stderr or ""))
                if done.returncode == 0 and result["imported"]:
                    result["import_error"] = _import_error(env_python, result["imported"], elsewhere, bare)
        except subprocess.TimeoutExpired:
            result["error"] = f"the install check did not finish in {timeout} s"
            return result
        except OSError as exc:
            result["error"] = f"the install check could not run: {exc}"
            return result
    installed = ((result["script_exit"] == 0 and not result["script_import_error"]) if result["scripts"]
                 else (result["exit"] == 0 and not result["import_error"]))
    # a setup script that installs needs no pyproject.toml (E2E-SWE full run: plain barf's setup.sh wrote a .pth and
    # scored 70%); pip, run only without one, does
    result["ok"] = not result["missing_scripts"] and installed
    return result


def record_path(slug: str) -> Path:
    import paths
    return paths.harness_root() / "notes" / f"install-{slug}.json"  # bookkeeping: never hashed


def last(slug: str) -> dict | None:
    return surface._last(record_path(slug))


def check(state: dict, key: str | None) -> dict | None:
    """Run the install check for a goal whose request gives a Python package and says it gets installed, or None.
    Reused while `key` (the watched files' hash) and the interpreter are unchanged. `blocking`: only with the
    project's own interpreter, and only when the check ran."""
    root = Path(state["cwd"])
    found = surface.find_spec(state.get("gate_path"), root)
    if not found:
        return None
    spec, package = found
    text = Path(spec).read_text(errors="replace")
    if not applies(text):
        return None
    python = surface.interpreter(state)
    previous = last(state["slug"])
    if (key is not None and previous and previous.get("key") == key and previous.get("python") == python
            and previous.get("package") == package):
        result = previous
    else:
        if python is None:
            return {"ok": True, "blocking": False, "package": package, "error": "no project interpreter named"}
        result = {**run(root, package, python, request_text=text), "key": key}
        try:
            record_path(state["slug"]).parent.mkdir(parents=True, exist_ok=True)
            record_path(state["slug"]).write_text(json.dumps(result, indent=1) + "\n")
        except OSError:
            pass
    return {**result, "blocking": python is not None and not result.get("error")}


NOT_THE_ENVIRONMENT = ("this check runs in its own throwaway environment, which has pip and sees the project "
                       "interpreter's packages, offline, so your own environment lacking pip or a network is not the "
                       "cause: the fix is in the project's files")


def problem(found: dict) -> str:
    """Everything that is wrong, in one line, so one round of work can fix it all."""
    problems = []
    if found.get("missing_scripts"):
        problems.append(f"the request names {', '.join(found['missing_scripts'])}, and the project has none")
    script_ok = bool(found.get("scripts")) and found.get("script_exit") == 0 and not found.get("script_import_error")
    if not found.get("pyproject", True) and not script_ok:
        problems.append("the project has no pyproject.toml, so pip cannot install it: add one with a [build-system] "
                        "table whose backend is already installed (`requires = [\"setuptools\"]` and "
                        "`build-backend = \"setuptools.build_meta\"` when setuptools is) and a [project] table "
                        "giving its name and version")
    if found.get("script_exit") not in (0, None):
        problems.append(f"`{found.get('script_failed') or 'the setup script'}` fails (exit {found['script_exit']}) "
                        f"when it runs in a clean copy of the project: {found.get('script_output', '')}")
    if found.get("script_import_error"):
        scripts = ", ".join(f"`{s}`" for s in found.get("scripts") or []) or "the setup script"
        names = ", ".join(found.get("imported") or [str(found.get("package"))])
        problems.append(f"{scripts} exits 0 but installs nothing: after it runs in a clean copy, `import {names}` "
                        f"from another folder fails ({found['script_import_error']}). A script that only checks the "
                        "import from inside the project folder does not install the project; make it install it, "
                        "e.g. with `python -m pip install -e . --no-build-isolation`, which needs no network")
    if found.get("exit") not in (0, None):
        problems.append("`pip install -e . --no-build-isolation` fails on a clean copy, offline, with the project's "
                        f"own packages: {found.get('output', '')}")
    if found.get("import_error"):
        names = ", ".join(found.get("imported") or [str(found.get("package"))])
        problems.append(f"it installs, but `import {names}` fails from another folder: {found['import_error']}")
    if problems:
        problems.append(NOT_THE_ENVIRONMENT)
    return "; ".join(problems)


def message(found: dict) -> str:
    return ("The package does not install the way the request says: " + problem(found) + ". Whoever uses the "
            "project installs it first, so until it installs, every test that imports it fails, whatever works from "
            "inside the folder. Give it a pyproject.toml whose build backend is already installed (setuptools when "
            "it is), and make a setup script the request names run that install. Then run `check`: it repeats this "
            "install for you. Do not make an environment of your own for it: there is no network, and pip without "
            "--no-index retries it for minutes, past the session's end. `close` waits.")
