"""Fixes from the NL2Repo bench3 transcripts (fun repo: docs/evaluations/nl2repo-bench3/report.md)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

import surface  # noqa: E402

ENTRY = str(ROOT / ".claude/skills/horizon/scripts/harness.py")


def _git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                   env=env, capture_output=True)


@pytest.fixture
def project(tmp_path):
    p = tmp_path / "proj "
    (p / ".claude").mkdir(parents=True)

    def cli(*args):
        done = subprocess.run([sys.executable, ENTRY, "--runtime", "claude", "--project", str(p), *args],
                              cwd=p, capture_output=True, text=True, timeout=60)
        return done.returncode, (json.loads(done.stdout) if done.stdout.strip().startswith("{") else done.stderr)
    return p, cli


SPEC = """\
# pkg
Docs at https://www.pkg.org and https://github.com/me/pkg/blob/main/x.py; see pkg.org too.

```python
import pkg
from pkg.dev import ConsoleRenderer
from pkg.processors import (
    JSONRenderer,
    TimeStamper as TS,
)
from pkg import get_logger, configure
import pkg.stdlib
pkg.PrintLoggerFactory()
log = pkg.dev.ConsoleRenderer.get_default_level_styles()
pkg.testing.capture_logs
from pkg.contextvars import bind_contextvars
class Foo:
    pass
```

`pkg.__nonexistent__` raises AttributeError.
Tests live in pkg.tests.test_dev and the module is pkg/dev.py.
"""


def test_surface_takes_the_names_a_spec_gives_and_leaves_out_noise():
    refs = surface.refs(SPEC, "pkg")
    for want in ("pkg.dev.ConsoleRenderer", "pkg.processors.JSONRenderer", "pkg.processors.TimeStamper",
                 "pkg.get_logger", "pkg.configure", "pkg.stdlib", "pkg.PrintLoggerFactory",
                 "pkg.testing.capture_logs", "pkg.contextvars.bind_contextvars"):
        assert want in refs, want
    joined = " ".join(refs)
    assert "org" not in joined and "github" not in joined  # URLs
    assert "class" not in refs and not any(r.endswith((".class", ".import", ".from")) for r in refs)
    assert not any(".tests" in r or "test_" in r for r in refs)  # test modules, not the `testing` module
    assert "pkg.__nonexistent__" not in refs  # only on an AttributeError line
    assert "pkg.dev.ConsoleRenderer.get_default_level_styles" not in refs  # module plus one name, no deeper
    assert refs == sorted(set(refs))


def _write_pkg(root, init="", dev="class ConsoleRenderer:\n    pass\n"):
    (root / "pkg").mkdir(exist_ok=True)
    if init is not None:
        (root / "pkg" / "__init__.py").write_text(init)
    (root / "pkg" / "dev.py").write_text(dev)


def _run_gate(root, gate):
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(gate)],
                          cwd=root, capture_output=True, text=True, timeout=120)


def test_a_generated_surface_gate_fails_on_a_missing_top_level_name_until_it_is_added(tmp_path):
    # structlog bench3: every module imported, but `structlog.PrintLoggerFactory` and other spec names were
    # missing; 391 official tests errored in setup and the score stayed 0.
    spec = tmp_path / "SPEC.md"
    spec.write_text("Use pkg.PrintLoggerFactory and pkg.dev.ConsoleRenderer.\n")
    gate = tmp_path / "gates" / "test_surface.py"
    gate.parent.mkdir()
    assert surface.write_gate(spec, "pkg", gate) == 2
    _write_pkg(tmp_path)
    failed = _run_gate(tmp_path, gate)
    assert failed.returncode == 1 and "1 failed, 1 passed" in failed.stdout
    assert "PrintLoggerFactory" in failed.stdout
    _write_pkg(tmp_path, init="from pkg.dev import ConsoleRenderer\nclass PrintLoggerFactory:\n    pass\n")
    assert _run_gate(tmp_path, gate).returncode == 0


def test_a_generated_surface_gate_fails_on_a_namespace_package(tmp_path):
    # plain structlog, all three runs: no __init__.py, so `import structlog.dev` worked but nothing installed.
    spec = tmp_path / "SPEC.md"
    spec.write_text("Use pkg.dev.ConsoleRenderer.\n")
    gate = tmp_path / "gates" / "test_surface.py"
    gate.parent.mkdir()
    surface.write_gate(spec, "pkg", gate)
    _write_pkg(tmp_path, init=None)
    failed = _run_gate(tmp_path, gate)
    assert failed.returncode == 1 and "__init__.py" in failed.stdout


def test_a_generated_surface_gate_finds_a_src_layout(tmp_path):
    spec = tmp_path / "SPEC.md"
    spec.write_text("Use pkg.dev.ConsoleRenderer.\n")
    gate = tmp_path / ".claude" / "gates" / "test_surface.py"
    gate.parent.mkdir(parents=True)
    surface.write_gate(spec, "pkg", gate)
    (tmp_path / "src").mkdir()
    _write_pkg(tmp_path / "src")
    assert _run_gate(tmp_path, gate).returncode == 0


def test_the_surface_command_writes_the_gate(project):
    p, cli = project
    (p / "SPEC.md").write_text("Use pkg.PrintLoggerFactory.\n")
    code, out = cli("surface", "--spec", str(p / "SPEC.md"), "--package", "pkg", "--out", str(p / "g" / "test_surface.py"))
    assert code == 0 and out["names"] == 1 and (p / "g" / "test_surface.py").is_file()


def _session_start(p, session="s2"):
    done = subprocess.run([sys.executable, ENTRY, "--runtime", "claude", "--project", str(p), "hook", "SessionStart"],
                          input=json.dumps({"session_id": session}), capture_output=True, text=True, timeout=120)
    return json.loads(done.stdout)["hookSpecificOutput"]["additionalContext"]


GATE_SH = ('test -f pkg/a.py && echo "1 passed" && exit 0\n'
           'echo "FAILED tests/test_gate.py::test_a_exists - AssertionError"\n'
           'echo "FAILED tests/test_gate.py::test_b_works - NameError"\n'
           'echo "2 failed in 0.01s"; exit 1\n')


def test_after_check_the_next_session_is_shown_that_gate_result(project):
    # pyjwt bench3 s7, s8: the session before ended with `check`, so the message showed no gate result at all.
    p, cli = project
    _git(p, "init", "-q")
    (p / "gate.sh").write_text(GATE_SH)
    _, started = cli("start", "Build pkg", "--gate", "sh gate.sh")
    slug = started["slug"]
    (p / "pkg").mkdir()
    (p / "pkg" / "a.py").write_text("x = 1\n")
    cli("check", slug, "--did", "wrote a", "--next", "write b")
    context = _session_start(p)
    assert "Last gate run: pass" in context and "1 passed" in context


def test_the_session_start_message_gives_the_gate_command_failing_checks_and_files(project):
    # Most resumed bench3 sessions still ran `status` (to get the gate command and failures) and `ls`/`wc -l`.
    # Most resumed bench3 sessions still ran `status` (to get the gate command and failures) and `ls`/`wc -l`.
    p, cli = project
    _git(p, "init", "-q")
    (p / "gate.sh").write_text(GATE_SH)
    _, started = cli("start", "Build pkg", "--gate", "sh gate.sh")
    (p / "pkg").mkdir()
    (p / "pkg" / "b.py").write_text("y = 2\nz = 3\n")
    context = _session_start(p)
    assert "Gate command: sh gate.sh" in context
    assert "Failing: test_a_exists, test_b_works (2 failed in 0.01s)" in context
    files = next(line for line in context.splitlines() if line.startswith("Files (lines): "))
    assert "pkg/b.py 2" in files and "gate.sh 4" in files
    assert ".claude/" not in files

PROSE_SPEC = """\
Users must be able to write `from pkg import get_logger, configure, JSONRenderer` at the top level.

>>> from pkg.jwks import PyJWKClient
# or from pkg.client import Client

The package `__init__.py`:

```python
__all__ = [
    "decode_complete",
    "register_algorithm",
]
```

Files: pkg.dev.py, pkg.api_jwt.md and the docs at pkg.readthedocs.io.
"""


def test_surface_takes_names_from_prose_doctests_comments_and_the_init_all():
    # review of 5268e44: the structlog spec's `from structlog import configure, ..., JSONRenderer` (prose) and
    # the jwt spec's `__init__` `__all__` were missed, though they are the top-level names `surface` exists for.
    refs = surface.refs(PROSE_SPEC, "pkg")
    for want in ("pkg.get_logger", "pkg.configure", "pkg.JSONRenderer", "pkg.jwks.PyJWKClient",
                 "pkg.client.Client", "pkg.decode_complete", "pkg.register_algorithm"):
        assert want in refs, want
    assert not any(r.endswith((".py", ".md", ".io", ".readthedocs")) for r in refs), refs


def test_surface_refuses_to_write_an_empty_gate(project):
    # review of 5268e44: `--package pyjwt` on the jwt spec gave 0 names and a gate that passed.
    p, cli = project
    (p / "SPEC.md").write_text("Use jwt.encode.\n")
    code, err = cli("surface", "--spec", str(p / "SPEC.md"), "--package", "pyjwt", "--out", str(p / "t.py"))
    assert code != 0 and "no names" in str(err)


def test_a_generated_surface_gate_finds_the_package_from_the_gate_directory_and_a_single_module(tmp_path):
    spec = tmp_path / "SPEC.md"
    spec.write_text("Use pkg.helper.\n")
    gate = tmp_path / ".claude" / "harness" / "gates" / "g" / "test_surface.py"
    gate.parent.mkdir(parents=True)
    surface.write_gate(spec, "pkg", gate)
    (tmp_path / "pkg.py").write_text("def helper():\n    pass\n")
    done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "test_surface.py"],
                          cwd=gate.parent, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stdout[-500:]


def test_the_failing_list_ignores_captured_log_lines():
    # review of 5268e44: `ERROR    lib:test.py:4 boom` lines from captured logging pushed the real ids out.
    import hooks
    output = ("----- Captured log call -----\n" + "ERROR    lib0:test_l.py:4 boom\n" * 15 +
              "=========== short test summary info ===========\n"
              "FAILED tests/test_l.py::test_one - AssertionError\n"
              "ERROR tests/test_l.py::test_two - RuntimeError\n"
              "=========== 1 failed, 1 error in 0.1s ===========\n")
    assert hooks._gate_digest(output) == ". Failing: test_one, test_two (1 failed, 1 error in 0.1s)."


def test_the_files_line_skips_environments_and_does_not_read_big_files(tmp_path):
    # review of 5268e44: an un-ignored .venv filled the 40 slots, and a 1.5 GB file was read whole.
    import hooks
    _git(tmp_path, "init", "-q")
    (tmp_path / ".venv" / "lib").mkdir(parents=True)
    (tmp_path / ".venv" / "lib" / "m.py").write_text("x\n")
    (tmp_path / "pkg.egg-info").mkdir()
    (tmp_path / "pkg.egg-info" / "PKG-INFO").write_text("x\n")
    (tmp_path / "a.py").write_text("x = 1\ny = 2")  # no final newline: still 2 lines
    with open(tmp_path / "big.bin", "wb") as f:
        f.truncate(3 * 1024 * 1024)
    line = hooks._files_line(tmp_path)
    assert ".venv" not in line and "egg-info" not in line
    assert "a.py 2" in line and "big.bin (3 MB)" in line
