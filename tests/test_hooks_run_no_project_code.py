"""Hooks never run the project's code (exp/hook-sandbox, 2026-11-07).

Claude Code runs hooks outside the sandbox it puts the agent's own commands in. In the E2E-SWE install run (Horizon
67c11e6) the SessionStart hook ran each goal's gate on changed files, and the agents' gates ran their setup.sh, which
wrote an install pointer into the task's environment and a path file and two scripts into the system Python: writes
the sandbox refuses the agent. The Stop hook's automatic `close` could likewise run the surface gate (an import of the
package) and the install check (pip runs the project's build). Now a hook only reads saved results; the agent's own
`check` and `close`, inside its sandbox, run the code."""
import json
import shlex
import sys

import pytest

from test_e2eswe_fixes import project  # noqa: F401  (the fixture is used by name)
from installable_project import installable
from test_surface_check import _begin, _build

import examples
import gate
import hookguard
import install_gate
import surface


def _goal(p, slug):
    return json.loads((p / ".claude" / "harness" / "goals" / f"{slug}.json").read_text())


def test_session_start_never_runs_the_gate_and_says_when_its_result_is_older_than_the_files(project, tmp_path):
    p, cli = project
    marker = tmp_path / "outside" / "ran"
    marker.parent.mkdir()
    (p / "gate.sh").write_text(f"touch {shlex.quote(str(marker))}\necho ALL-OK\n")
    code, started = cli("start", "Build the package", "--gate", "sh gate.sh")
    assert code == 0, started
    slug = started["slug"]
    marker.unlink(missing_ok=True)  # start's own run is the agent's command, inside its sandbox
    before = _goal(p, slug).get("observations", [])
    (p / "src" / "tool.py").write_text("def parse(line):\n    return line.strip()\n")
    context = cli.new_session("s2")["hookSpecificOutput"]["additionalContext"]
    assert not marker.exists()
    assert _goal(p, slug).get("observations", []) == before
    assert "files changed after that run" in context and "`check`" in context, context
    cli("check", slug, "--did", "Stripped lines", "--next", "Write the reader")
    context = cli.new_session("s3")["hookSpecificOutput"]["additionalContext"]
    assert "files changed after that run" not in context, context


def test_the_stop_hooks_close_never_runs_the_surface_gate_or_the_install_check(project):
    p, cli = project
    cli.new_session("s1")
    _begin(p, cli)
    _build(p)
    installable(p)
    code, out = cli("observe", "build-the-tool")  # a gate pass on these files, with neither check run on them
    assert code == 0, out
    notes = p / ".claude" / "harness" / "notes"
    cli.hook("Stop", {"session_id": "s1"})
    assert not (notes / "surface-build-the-tool.json").exists()
    assert not (notes / "install-build-the-tool.json").exists()
    assert _goal(p, "build-the-tool")["status"] == "active"
    code, out = cli("close", "build-the-tool")  # the agent's own close runs both
    assert code == 0 and out["complete"], out


def test_inside_a_hook_the_gate_the_spec_examples_the_surface_gate_and_the_install_check_refuse_to_run(tmp_path):
    marker = tmp_path / "ran"
    state = {"gate": {"kind": "command", "value": f"touch {shlex.quote(str(marker))}"}, "cwd": str(tmp_path),
             "checkpoints": [], "watch_paths": None}
    gates = tmp_path / "g"
    gates.mkdir()
    (gates / surface.FILE).write_text(f"open({str(marker)!r}, 'w').close()\n\ndef test_x():\n    pass\n")
    (gates / examples.FILE).write_text(f"open({str(marker)!r}, 'w').close()\n\ndef test_x():\n    pass\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'p'\n")
    with hookguard.active():
        with pytest.raises(hookguard.Refused):
            gate.run_gate(state)
        with pytest.raises(hookguard.Refused):
            examples.run(gates, tmp_path, sys.executable)
        with pytest.raises(hookguard.Refused):
            surface.run(gates, tmp_path, sys.executable)
        with pytest.raises(hookguard.Refused):
            install_gate.run(tmp_path, "p", sys.executable)
    assert not marker.exists()
    assert gate.run_gate(state)[0] == "pass" and marker.exists()  # outside a hook it runs as before
