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


def test_coverage_gate_is_rejected(tmp_path):
    """Coverage gates are judged by a fresh subagent, not by subprocess."""
    repo = _repo(tmp_path)
    state = _goal(repo, "every claim has a source", kind="coverage")
    with pytest.raises(ValueError):
        gate.run_gate(state)
