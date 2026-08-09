"""Gate execution for horizon (spec §5.2 step 4, §5.2.1, §6).

Separated from the skill markdown so the loop's decision logic is unit
tested once rather than re-derived in prose on every run.
"""

import subprocess

import tree_hash

MAX_OUTPUT = 4000
DEFAULT_TIMEOUT = 900
SHELL_NOT_FOUND = 127


class GateUnrunnable(Exception):
    """The gate command could not execute at all.

    Distinct from a failing gate. An unrunnable gate is a broken goal — a
    typo, a missing tool — and must stop the loop. A failing gate is work
    still to do and must continue it. Conflating them spends the entire
    turn budget re-running a command that was never going to run.
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
        raise ValueError(
            "run_gate handles command gates only; a coverage gate is judged "
            "by a fresh subagent that did not do the work"
        )

    if not tree_hash.changed(state["cwd"], state.get("watch_paths"), last_hash(state)):
        return "skipped", ""

    command = state["gate"]["value"]
    try:
        proc = subprocess.run(
            command, shell=True, cwd=state["cwd"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise GateUnrunnable(f"gate timed out after {timeout}s: {command}") from e
    except OSError as e:
        raise GateUnrunnable(f"gate could not execute: {command}: {e}") from e

    if proc.returncode == SHELL_NOT_FOUND:
        raise GateUnrunnable(
            f"gate command not found (exit 127): {command}. "
            f"stderr: {proc.stderr.strip()[:200]}"
        )

    output = (proc.stdout + proc.stderr)[-MAX_OUTPUT:]
    return ("pass" if proc.returncode == 0 else "fail"), output
