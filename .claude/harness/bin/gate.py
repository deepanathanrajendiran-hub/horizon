"""Gate execution for horizon (spec §5.2 step 4, §5.2.1, §6).

Separated from the skill markdown so the loop's decision logic is unit
tested once rather than re-derived in prose on every run.
"""

import os
import signal
import subprocess

import paths
import tree_hash

MAX_OUTPUT = 4000
# Below the Bash tool's 600s ceiling that hosts these calls. At 900 the
# host killed the turn first -- after the increment's work had landed and
# before the checkpoint recording it -- and because the gate runs in its
# own process group (start_new_session below), nothing reaped it: the test
# suite kept running, and every killed turn added another.
DEFAULT_TIMEOUT = 480
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


def _pgid_marker(state: dict):
    d = paths.harness_root() / "goals"
    d.mkdir(parents=True, exist_ok=True)
    return d / f".{state.get('slug', 'unknown')}.gate-pgid"


def _reap_orphans(state: dict) -> list[int]:
    """Kill any gate process group this goal left running.

    `start_new_session=True` detaches the gate so a timeout can kill the
    whole tree, but it also means nothing reaps it if the *parent* dies --
    the host killing a long turn, or a crash. The pgid is recorded while
    the gate runs and cleared when it returns, so a leftover file means a
    previous turn was interrupted mid-gate.
    """
    marker = _pgid_marker(state)
    killed = []
    try:
        pgid = int(marker.read_text().strip())
    except (FileNotFoundError, ValueError):
        return killed
    try:
        os.killpg(pgid, signal.SIGKILL)
        killed.append(pgid)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        marker.unlink()
    except FileNotFoundError:
        pass
    return killed


def run_gate(state: dict, timeout: int = DEFAULT_TIMEOUT) -> tuple[str, str]:
    """Run the goal's gate. Returns (result, output).

    result is "pass", "fail", or "skipped". Skipping happens only when the
    tree is provably unchanged since the last checkpoint — see tree_hash,
    where every uncertain path reports changed.

    **Never two skips in a row.** A skip does no work, so the tree is still
    unchanged on the next turn, so it skips again: skip is an absorbing
    state and the loop burns its entire budget without ever running the
    gate. Observed as fail, fail, then eight consecutive skips on a gate
    that would have passed on its third run — the gate's own counter lived
    outside `watch_paths`, so neither git nor the mtime walk could see it
    advancing. One forced run breaks the cycle, which costs a single gate
    invocation and matches this module's stated asymmetry: a false skip is
    a correctness bug, a false run costs seconds.
    """
    if state["gate"]["kind"] != "command":
        raise CoverageGate(
            "run_gate handles command gates only; a coverage gate is judged "
            "by a fresh subagent that did not do the work"
        )

    checkpoints = state.get("checkpoints") or []
    skipped_last_turn = bool(checkpoints) and checkpoints[-1].get("gate") == "skipped"
    if (not skipped_last_turn
            and not tree_hash.changed(state["cwd"], state.get("watch_paths"), last_hash(state))):
        return "skipped", ""

    command = state["gate"]["value"]
    try:
        # start_new_session=True makes the child the leader of its own
        # process group, so a timeout can kill the whole group — including
        # any grandchildren the shell forked off (backgrounded jobs,
        # pipeline stages) — not just the immediate shell process.
        _reap_orphans(state)
        proc = subprocess.Popen(
            command, shell=True, cwd=state["cwd"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            start_new_session=True,
        )
    except OSError as e:
        raise GateUnrunnable(f"gate could not execute: {command}: {e}") from e

    marker = _pgid_marker(state)
    try:
        marker.write_text(str(os.getpgid(proc.pid)))
    except OSError:
        marker = None  # best effort; reaping is a safety net, not a contract

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except ProcessLookupError:
            pass  # already dead
        proc.wait()  # reap; avoid leaving a zombie behind
        raise GateUnrunnable(f"gate timed out after {timeout}s: {command}") from e
    finally:
        if marker is not None:
            try:
                marker.unlink()
            except FileNotFoundError:
                pass

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


def run_gate_at(state: dict, timeout: int = DEFAULT_TIMEOUT) -> tuple[str, str, str]:
    """`run_gate`, plus the tree hash the gate was actually evaluated against.

    A checkpoint must record the state the gate *judged*, not the state left
    behind after it ran. Getting that backwards makes the next turn see an
    unchanged tree and skip — turning fail/fail/pass into
    fail/skipped/skipped. That ordering bug shipped once in this project's
    own test suite precisely because `run_gate` returned no hash and left the
    timing to the caller's discipline.

    Returns (result, output, evaluated_hash). Pass `evaluated_hash` straight
    to `goal.checkpoint()` — never recompute it after the gate has run.

    On an unreadable watch tree the hash cannot be computed; rather than
    losing the checkpoint entirely, the sentinel `"unknown"` is returned. It
    can never equal a real digest, so the next turn is guaranteed to run the
    gate rather than skip.
    """
    try:
        evaluated = tree_hash.compute(state["cwd"], state.get("watch_paths"))
    except Exception:
        evaluated = "unknown"
    result, output = run_gate(state, timeout=timeout)
    return result, output, evaluated
