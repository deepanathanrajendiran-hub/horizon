import os
import subprocess
import sys
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
    state = _goal(repo, "python3 -c \"print('E' * 20000)\"; exit 1")
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


def test_an_orphaned_gate_group_is_reaped_on_the_next_run(tmp_path, monkeypatch):
    """start_new_session detaches the gate so a timeout can kill its whole
    tree; the same detachment means nothing reaps it if the PARENT dies.
    A leftover pgid marker is that case, and the next run must clear it."""
    import signal as _signal
    repo = _repo(tmp_path)
    state = _goal(repo, "true")

    harness = tmp_path / "harness"
    (harness / "goals").mkdir(parents=True)
    monkeypatch.setattr(gate.paths, "harness_root", lambda: harness)

    victim = subprocess.Popen(["sleep", "60"], start_new_session=True)
    pgid = os.getpgid(victim.pid)
    (harness / "goals" / f".{state['slug']}.gate-pgid").write_text(str(pgid))

    gate.run_gate(state)

    victim.wait(timeout=5)
    assert victim.returncode is not None, "the orphaned process group survived"
    assert not (harness / "goals" / f".{state['slug']}.gate-pgid").exists()


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
