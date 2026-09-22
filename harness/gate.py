"""Gate execution for horizon (spec §5.2 step 4, §5.2.1, §6).

Separated from the skill markdown so the loop's decision logic is unit
tested once rather than re-derived in prose on every run.
"""

import os
import signal
import subprocess

import tree_hash
from typing import Callable

MAX_OUTPUT = 4000
# Below the Bash tool's 600s ceiling that hosts these calls. At 900 the
# host killed the turn first -- after the increment's work had landed and
# before the checkpoint recording it -- and because the gate runs in its
# own process group (start_new_session below), nothing reaped it: the test
# suite kept running, and every killed turn added another.
DEFAULT_TIMEOUT = 480
SHELL_NOT_FOUND = 127
SHELL_NOT_EXECUTABLE = 126

# Recorded when the watch tree cannot be hashed. It must never compare
# equal to anything, including itself: two unreadable turns in a row would
# otherwise look like an unchanged tree and skip the gate, which is the
# one direction this module may not fail in.
UNKNOWN_HASH = "unknown"


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

    Carries `.tree_hash`: the digest the turn's work was evaluated
    against, when raised from `run_gate_at`. The caller needs it for the
    checkpoint it makes once the judge returns, and there is no way to
    recompute it correctly — by then the judge has run and the tree may
    have moved. Without it, a coverage goal's every turn ended in an
    exception carrying no way to record the work it had just done.
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


# There was an orphan reaper here: each run recorded its gate's process
# group id in a marker file, and the next run SIGKILLed whatever that file
# named, on the theory that a leftover marker meant a killed turn had
# abandoned a gate.
#
# It was removed rather than repaired. It killed a bare integer with no
# check that the group still belonged to this goal — the exact unsoundness
# `filelock` refuses to rely on ("a pid check is wrong across containers and
# mount namespaces, where a pid may be reused or invisible"). Two demonstrated
# consequences: two concurrent turns of one goal killed each other's gates,
# and the survivor's SIGKILLed gate was recorded as an honest `fail` because
# returncode -9 is not 126 or 127; and a marker outliving a reboot named a
# recycled pid, so the next turn SIGKILLed an unrelated process group — a dev
# server, a build, or the session itself.
#
# What it bought was reaping a leaked `sleep` after a killed turn. What it
# risked was killing the user's own processes. A best-effort safety net does
# not get to do that, so the timeout path below — which holds a real handle
# on the process it kills — is now the only thing here that signals anyone.


def _stop_owned_process(proc: subprocess.Popen) -> None:
    # Only an unreaped process handle pins this PID to the child we started.
    if proc.returncode is None:
        try:
            # The child leads its new session; its PID is the owned group.
            # Signal before wait/poll so an exited shell cannot hide descendants.
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    proc.wait()

def run_gate(state: dict, timeout: int = DEFAULT_TIMEOUT,
             current_hash: str | None = None, *,
             on_spawn: Callable[[subprocess.Popen], None] | None = None) -> tuple[str, str]:
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

    `current_hash` lets a caller that has already hashed the tree — which
    `run_gate_at` must, to record what the gate judged — pass that result
    in rather than paying for a second full walk of the same tree in the
    same turn. Anything unequal to the last checkpoint's hash, including
    the `"unknown"` sentinel, runs the gate.
    """
    if state["gate"]["kind"] != "command":
        raise CoverageGate(
            "run_gate handles command gates only; a coverage gate is judged "
            "by a fresh subagent that did not do the work"
        )

    checkpoints = state.get("checkpoints") or []
    skipped_last_turn = bool(checkpoints) and checkpoints[-1].get("gate") == "skipped"
    if not skipped_last_turn:
        previous = last_hash(state)
        if current_hash is None:
            unchanged = not tree_hash.changed(
                state["cwd"], state.get("watch_paths"), previous)
        else:
            unchanged = (bool(previous)
                         and UNKNOWN_HASH not in (previous, current_hash)
                         and current_hash == previous)
        if unchanged:
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

    if on_spawn is not None:
        try:
            on_spawn(proc)
        except Exception as exc:
            _stop_owned_process(proc)
            raise GateUnrunnable(f"Could not record the running gate: {exc}") from exc

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        _stop_owned_process(proc)
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


def run_gate_at(state: dict, timeout: int = DEFAULT_TIMEOUT, *,
                on_spawn: Callable[[subprocess.Popen], None] | None = None) -> tuple[str, str, str]:
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
    losing the checkpoint entirely, the sentinel `UNKNOWN_HASH` is returned. It
    can never equal a real digest, nor another `UNKNOWN_HASH`, so the next turn is guaranteed to run the
    gate rather than skip.
    """
    try:
        evaluated = tree_hash.compute(state["cwd"], state.get("watch_paths"))
    except Exception:
        evaluated = UNKNOWN_HASH
    # Handed to run_gate rather than recomputed there: the two calls hashed
    # the same tree twice per turn, and on this repo the second walk was
    # pure waste.
    try:
        result, output = run_gate(state, timeout=timeout, current_hash=evaluated, on_spawn=on_spawn)
    except GateUnrunnable as e:
        e.tree_hash = evaluated
        raise
    except CoverageGate as e:
        # The hash is the one thing this call produced that the caller
        # cannot get back any other way. Attach it rather than losing it
        # inside the exception.
        e.tree_hash = evaluated
        raise
    return result, output, evaluated
