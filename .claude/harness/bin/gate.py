"""Gate execution for horizon (spec §5.2 step 4, §5.2.1, §6).

Separated from the skill markdown so the loop's decision logic is unit
tested once rather than re-derived in prose on every run.
"""

import os
import signal
import subprocess

import tree_hash

MAX_OUTPUT = 4000
DEFAULT_TIMEOUT = 900
SHELL_NOT_FOUND = 127
SHELL_NOT_EXECUTABLE = 126


class GateUnrunnable(Exception):
    """The gate command could not execute at all.

    Distinct from a failing gate. An unrunnable gate is a broken goal — a
    typo, a missing tool — and must stop the loop. A failing gate is work
    still to do and must continue it. Conflating them spends the entire
    turn budget re-running a command that was never going to run.
    """


class CoverageGate(Exception):
    """Raised when run_gate is asked to execute a coverage gate.

    A coverage gate is judged by a fresh subagent that did not do the
    work, not by running a subprocess — run_gate has nothing to execute.
    Deliberately not a ValueError: callers catch this to route coverage
    gates to subagent judgment, and ValueError is one of the most
    overloaded builtins in the standard library. A coincidental
    ValueError from anywhere else in the call chain would otherwise be
    swallowed by that same handler and silently misrouted as ordinary
    coverage handling, masking a real bug.
    """


def last_hash(state: dict) -> str | None:
    """The tree_hash recorded by the most recent checkpoint, if any."""
    checkpoints = state.get("checkpoints") or []
    return checkpoints[-1]["tree_hash"] if checkpoints else None


def run_gate(state: dict, timeout: int = DEFAULT_TIMEOUT) -> tuple[str, str]:
    """Run the goal's gate. Returns (result, output).

    result is "pass", "fail", or "skipped". Skipping happens only when the
    tree is provably unchanged since the last checkpoint — see tree_hash,
    where every uncertain path reports changed.
    """
    if state["gate"]["kind"] != "command":
        raise CoverageGate(
            "run_gate handles command gates only; a coverage gate is judged "
            "by a fresh subagent that did not do the work"
        )

    if not tree_hash.changed(state["cwd"], state.get("watch_paths"), last_hash(state)):
        return "skipped", ""

    command = state["gate"]["value"]
    try:
        # start_new_session=True makes the child the leader of its own
        # process group, so a timeout can kill the whole group — including
        # any grandchildren the shell forked off (backgrounded jobs,
        # pipeline stages) — not just the immediate shell process.
        proc = subprocess.Popen(
            command, shell=True, cwd=state["cwd"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
        )
    except OSError as e:
        raise GateUnrunnable(f"gate could not execute: {command}: {e}") from e

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass  # already dead
        proc.wait()  # reap; avoid leaving a zombie behind
        raise GateUnrunnable(f"gate timed out after {timeout}s: {command}") from e

    if proc.returncode in (SHELL_NOT_FOUND, SHELL_NOT_EXECUTABLE):
        reason = (
            "not found" if proc.returncode == SHELL_NOT_FOUND else "not executable"
        )
        raise GateUnrunnable(
            f"gate command {reason} (exit {proc.returncode}): {command}. "
            f"stderr: {stderr.strip()[:200]}"
        )

    output = (stdout + stderr)[-MAX_OUTPUT:]
    return ("pass" if proc.returncode == 0 else "fail"), output
