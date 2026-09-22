import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / ".claude" / "harness" / "bin"
sys.path.insert(0, str(BIN))

import gate  # noqa: E402
import goal  # noqa: E402
import tree_hash  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_goals(tmp_path, monkeypatch):
    d = tmp_path / "goals"
    d.mkdir()
    monkeypatch.setattr(goal.paths, "goals_dir", lambda: d)
    return d


def _goal(repo: Path, gate_value: str, kind: str = "command"):
    return goal.create(
        goal="toy goal", cwd=str(repo), gate_kind=kind,
        gate_value=gate_value, max_turns=5, watch_paths=[str(repo)],
    )


def _repo(tmp_path: Path) -> Path:
    repo = tmp_path / "proj"
    repo.mkdir()
    (repo / "a.txt").write_text("start")
    return repo


def test_skips_when_tree_unchanged(tmp_path):
    """T5: an unchanged tree cannot produce a different gate result."""
    repo = _repo(tmp_path)
    state = _goal(repo, "exit 1")
    h = tree_hash.compute(repo, [str(repo)])
    goal.checkpoint(state["slug"], "did", "next", h, "fail", "")

    assert gate.run_gate(goal.load(state["slug"])) == ("skipped", "")


def test_runs_when_tree_changed(tmp_path):
    repo = _repo(tmp_path)
    state = _goal(repo, "exit 1")
    goal.checkpoint(state["slug"], "did", "next", "stale-hash", "fail", "")

    result, _ = gate.run_gate(goal.load(state["slug"]))
    assert result == "fail"


def test_runs_on_first_attempt_with_no_checkpoints(tmp_path):
    repo = _repo(tmp_path)
    state = _goal(repo, "exit 0")
    assert gate.run_gate(state)[0] == "pass"


def test_fails_then_passes_on_third_attempt(tmp_path):
    """T4: the loop terminates exactly when the gate starts passing."""
    repo = _repo(tmp_path)
    script = repo / "gate.sh"
    script.write_text(
        "#!/bin/sh\n"
        "n=$(cat n 2>/dev/null || echo 0)\n"
        "n=$((n+1))\n"
        "echo $n > n\n"
        "[ $n -ge 3 ] && exit 0\n"
        "exit 1\n"
    )
    script.chmod(0o755)

    state = _goal(repo, "./gate.sh")
    results = []
    for i in range(3):
        current = goal.load(state["slug"])
        # Hash the tree the gate is about to evaluate, before running it.
        # gate.sh mutates the watched tree as a side effect (it writes its
        # own attempt counter into the repo); hashing after the run would
        # capture that side effect and make the *next* iteration's skip
        # check compare against a hash that already includes it, since
        # nothing else touches the tree between this checkpoint and the
        # next run_gate call. That produces a false skip on iteration 2,
        # not the pass/fail progression this test is asserting.
        pre_run_hash = tree_hash.compute(repo, [str(repo)])
        result, output = gate.run_gate(current)
        goal.checkpoint(
            state["slug"], f"attempt {i}", "retry",
            pre_run_hash, result, output,
        )
        results.append(result)

    assert results == ["fail", "fail", "pass"]


def test_unrunnable_command_raises(tmp_path):
    """Spec 6: an unrunnable gate is a broken goal, not work still to do.
    Returning 'fail' here would burn the whole turn budget on a typo."""
    repo = _repo(tmp_path)
    state = _goal(repo, "definitely-not-a-real-command-xyz")
    with pytest.raises(gate.GateUnrunnable):
        gate.run_gate(state)


def test_timeout_raises_unrunnable(tmp_path):
    repo = _repo(tmp_path)
    state = _goal(repo, "sleep 30")
    with pytest.raises(gate.GateUnrunnable):
        gate.run_gate(state, timeout=1)


def test_output_is_bounded(tmp_path):
    repo = _repo(tmp_path)
    # One command, not `…; exit 1`: goal.create refuses a gate whose exit
    # status comes from anything other than the work it judges.
    state = _goal(repo, "python3 -c \"import sys; sys.stdout.write('E'*20000); sys.exit(1)\"")
    result, output = gate.run_gate(state)
    assert result == "fail"
    assert len(output) <= gate.MAX_OUTPUT
    # Bounded is not enough: a head- or middle-truncating implementation
    # would also pass a length-only assertion, while inverting the contract.
    # A failing gate puts its actionable diagnosis at the END, so keeping the
    # head is keeping the useless part.
    assert output.rstrip().endswith("E"), "truncation dropped the tail"


def test_coverage_gate_is_rejected(tmp_path):
    """Coverage gates are judged by a fresh subagent, not by subprocess."""
    repo = _repo(tmp_path)
    state = _goal(repo, "every claim has a source", kind="coverage")
    with pytest.raises(gate.CoverageGate):
        gate.run_gate(state)


def test_coverage_gate_exception_is_not_a_value_error(tmp_path):
    """CoverageGate must be distinct from ValueError, or a coincidental
    ValueError anywhere else in the call chain would be swallowed by the
    same handler and silently misrouted as ordinary coverage handling."""
    assert not issubclass(gate.CoverageGate, ValueError)


def test_permission_denied_raises_unrunnable(tmp_path):
    """Exit 126 (found but not executable) is a broken goal too — a script
    checked out of git or unpacked from a tarball without the execute bit
    is far more common than a 127 typo, and must not be treated as 'fail'
    (work still to do), which would burn the whole turn budget re-running
    a command that will deterministically 126 forever."""
    repo = _repo(tmp_path)
    script = repo / "noexec.sh"
    script.write_text("#!/bin/sh\necho hi\n")
    script.chmod(0o644)  # deliberately not executable

    state = _goal(repo, "./noexec.sh")
    with pytest.raises(gate.GateUnrunnable):
        gate.run_gate(state)


def test_timeout_kills_process_group(tmp_path):
    """A timed-out gate must not leave grandchildren running. Background a
    child that sleeps briefly then writes a sentinel file; if the process
    group survives the timeout, the sentinel appears after we return."""
    import time

    repo = _repo(tmp_path)
    sentinel = repo / "sentinel"
    command = f'sh -c \'sleep 3 && touch "{sentinel}" & wait\''
    state = _goal(repo, command)

    with pytest.raises(gate.GateUnrunnable):
        gate.run_gate(state, timeout=1)

    time.sleep(4)  # past the backgrounded sleep's delay
    assert not sentinel.exists()


def test_run_gate_at_returns_the_hash_it_evaluated(tmp_path):
    """The caller must checkpoint the tree the gate JUDGED. run_gate_at
    returns it so caller-side timing cannot be got wrong -- the ordering bug
    that shipped in this suite's own T4 test."""
    repo = _repo(tmp_path)
    state = _goal(repo, "true")
    expected = tree_hash.compute(str(repo), [str(repo)])

    result, output, evaluated = gate.run_gate_at(state)

    assert result == "pass"
    assert evaluated == expected, "returned hash is not the pre-gate tree state"


def test_run_gate_at_uses_a_sentinel_when_the_tree_is_unreadable(tmp_path):
    """An unreadable watch tree must not lose the checkpoint. "unknown" can
    never equal a real digest, so the next turn is guaranteed to run."""
    repo = _repo(tmp_path)
    locked = repo / "locked"
    locked.mkdir()
    state = _goal(repo, "true")
    os.chmod(locked, 0o000)
    try:
        result, _, evaluated = gate.run_gate_at(state)
        assert evaluated == "unknown"
        assert tree_hash.changed(str(repo), [str(repo)], evaluated) is True
    finally:
        os.chmod(locked, 0o755)


def test_default_timeout_is_below_the_host_ceiling():
    """The Bash tool hosting these calls caps at 600s. A longer gate
    timeout means the host kills the turn first -- after the increment's
    work landed and before the checkpoint recording it -- and the detached
    gate keeps running."""
    assert gate.DEFAULT_TIMEOUT < 600


def test_the_gate_never_signals_a_process_group_it_did_not_start(tmp_path):
    """The inverse of the test that used to live here.

    An orphan reaper stored each gate's pgid in a marker file and SIGKILLed
    whatever the file named on the next run. Two demonstrated consequences:
    two concurrent turns of one goal killed each other's gates (recorded as
    an honest `fail`, since -9 is neither 126 nor 127), and a marker
    surviving a reboot named a recycled pid — so the next turn killed an
    unrelated process group. This asserts that no marker is written and
    nothing outside the gate's own tree is signalled."""
    repo = _repo(tmp_path)
    state = _goal(repo, "true")

    bystander = subprocess.Popen(["sleep", "5"], start_new_session=True)
    try:
        gate.run_gate(state)
        assert bystander.poll() is None, "the harness killed an unrelated process"
        markers = list(tmp_path.rglob("*.gate-pgid"))
        assert not markers, f"a pgid marker is still being written: {markers}"
    finally:
        bystander.kill()
        bystander.wait(timeout=5)


def test_a_timed_out_gate_still_kills_its_own_tree(tmp_path):
    """Removing the reaper must not cost the one kill that is sound: the
    timeout path holds a live handle on the group it signals."""
    repo = _repo(tmp_path)
    marker = repo / "child-finished"
    script = repo / "slow.sh"
    script.write_text(f"#!/bin/sh\n(sleep 20; touch {marker}) &\nsleep 20\n")
    script.chmod(0o755)
    state = _goal(repo, "./slow.sh")

    with pytest.raises(gate.GateUnrunnable):
        gate.run_gate(state, timeout=1)

    time.sleep(0.3)
    assert not marker.exists(), "a backgrounded grandchild outlived the timeout"


def test_skip_is_never_absorbing(tmp_path):
    """A skip does no work, so the tree is still unchanged next turn, so it
    skips again — forever. Observed as fail, fail, then eight consecutive
    skips against a gate that passes on its third run, with the gate's own
    counter living outside watch_paths where neither git nor the mtime walk
    could see it advance. The loop burned its whole budget looking busy."""
    repo = _repo(tmp_path)
    watched = repo / "src"
    watched.mkdir()
    (watched / "a.py").write_text("x = 1\n")

    script = repo / "check.sh"
    script.write_text(
        "#!/bin/sh\n"
        "n=$(cat .n 2>/dev/null || echo 0)\n"
        "n=$((n+1))\n"
        "echo $n > .n\n"           # side effect OUTSIDE watch_paths
        "[ $n -ge 3 ] && exit 0\n"
        "exit 1\n"
    )
    script.chmod(0o755)

    state = goal.create(goal="narrowed", cwd=str(repo), gate_kind="command",
                        gate_value="./check.sh", max_turns=10,
                        watch_paths=[str(watched)])
    results = []
    for i in range(10):
        current = goal.load(state["slug"])
        if goal.budget_exhausted(current):
            break
        r, o, h = gate.run_gate_at(current)
        goal.checkpoint(state["slug"], f"turn {i}", "next", h, r, o)
        results.append(r)
        if r == "pass":
            break

    assert "pass" in results, f"the loop never reached a passing gate: {results}"
    assert not any(a == "skipped" and b == "skipped"
                   for a, b in zip(results, results[1:])), \
        f"two consecutive skips — skip is absorbing again: {results}"


def test_run_gate_at_hashes_the_tree_once(tmp_path, monkeypatch):
    """run_gate_at hashed the tree to record what the gate judged, and
    run_gate hashed the same tree again to decide whether to skip: two full
    walks per turn, of which one was pure waste."""
    repo = _repo(tmp_path)
    state = _goal(repo, "exit 1")
    goal.checkpoint(state["slug"], "did", "next", "stale-hash", "fail", "")

    calls = []
    real = tree_hash.compute
    monkeypatch.setattr(tree_hash, "compute",
                        lambda *a, **kw: (calls.append(1), real(*a, **kw))[1])

    result, _, evaluated = gate.run_gate_at(goal.load(state["slug"]))
    assert result == "fail"
    assert len(calls) == 1, f"tree hashed {len(calls)} times in one turn"
    assert evaluated != "stale-hash"


def test_a_precomputed_hash_still_skips_an_unchanged_tree(tmp_path):
    """The optimisation must not cost the skip: same decision, one walk."""
    repo = _repo(tmp_path)
    state = _goal(repo, "exit 1")
    h = tree_hash.compute(repo, [str(repo)])
    goal.checkpoint(state["slug"], "did", "next", h, "fail", "")

    assert gate.run_gate_at(goal.load(state["slug"]))[0] == "skipped"


def test_an_unknown_hash_never_skips(tmp_path):
    """"unknown" is the sentinel for an unreadable watch tree; it must never
    compare equal to a real digest, or a hashing failure would skip forever."""
    repo = _repo(tmp_path)
    state = _goal(repo, "exit 1")
    goal.checkpoint(state["slug"], "did", "next", "unknown", "fail", "")

    assert gate.run_gate(goal.load(state["slug"]), current_hash="unknown")[0] == "fail"


def test_an_exit_masking_gate_would_have_passed_a_failing_suite(tmp_path):
    """Why goal.create refuses these: the gate itself is not detectably
    broken at run time. run_gate returns `pass`, the checkpoint records
    `pass`, and goal.complete accepts it."""
    repo = _repo(tmp_path)
    failing = repo / "runtests.sh"
    failing.write_text("#!/bin/sh\necho '3 failed'\nexit 1\n")
    failing.chmod(0o755)

    honest = _goal(repo, "./runtests.sh")
    assert gate.run_gate(honest)[0] == "fail"

    # Same failing suite, one pipe. goal.create refuses to build this state,
    # so construct it by hand to show what it would have done.
    masked = dict(honest, gate={"kind": "command", "value": "./runtests.sh | tail -30"})
    assert gate.run_gate(masked)[0] == "pass", "the hole this refusal closes"

    with pytest.raises(goal.GateUnsound):
        goal.create(goal="masked", cwd=str(repo), gate_kind="command",
                    gate_value="./runtests.sh | tail -30", max_turns=5)


def test_a_coverage_gate_carries_the_hash_it_was_evaluated_against(tmp_path):
    """The turn block checkpoints the work once the judge returns, and the
    hash is the one value it cannot recompute correctly by then — the judge
    has run and the tree may have moved."""
    repo = _repo(tmp_path)
    state = _goal(repo, "every claim cites a source", kind="coverage")

    with pytest.raises(gate.CoverageGate) as excinfo:
        gate.run_gate_at(state)

    assert excinfo.value.tree_hash == tree_hash.compute(repo, [str(repo)])

def test_spawn_callback_failure_reaps_its_owned_child(tmp_path):
    state = {"cwd": str(tmp_path), "gate": {"kind": "command", "value": "sleep 30"},
             "watch_paths": [str(tmp_path)], "checkpoints": []}
    spawned = []
    def reject(proc):
        spawned.append(proc)
        raise RuntimeError("metadata unavailable")
    with pytest.raises(gate.GateUnrunnable, match="Could not record"):
        gate.run_gate(state, on_spawn=reject)
    assert len(spawned) == 1
    assert spawned[0].poll() is not None

def test_unrunnable_gate_carries_pre_gate_hash(tmp_path):
    (tmp_path / "source.txt").write_text("before")
    state = {"cwd": str(tmp_path), "gate": {"kind": "command", "value": "missing_horizon_command"},
             "watch_paths": [str(tmp_path)], "checkpoints": []}
    expected = tree_hash.compute(str(tmp_path), [str(tmp_path)])
    with pytest.raises(gate.GateUnrunnable) as raised:
        gate.run_gate_at(state)
    assert raised.value.tree_hash == expected

def test_callback_failure_signals_owned_group_after_shell_exits(tmp_path, monkeypatch):
    import os
    import time

    state = {"cwd": str(tmp_path), "gate": {"kind": "command", "value": "true"},
             "watch_paths": [str(tmp_path)], "checkpoints": []}
    owned = []
    signaled = []
    original = os.killpg
    def record_signal(pgid, sig):
        signaled.append(pgid)
        try:
            return original(pgid, sig)
        except (ProcessLookupError, PermissionError):
            return None
    monkeypatch.setattr(os, "killpg", record_signal)
    def reject(proc):
        owned.append(proc)
        time.sleep(0.05)  # The shell exits, but this invocation has not reaped it.
        assert proc.returncode is None
        raise RuntimeError("metadata unavailable")
    with pytest.raises(gate.GateUnrunnable, match="Could not record"):
        gate.run_gate(state, on_spawn=reject)
    assert signaled == [owned[0].pid]
