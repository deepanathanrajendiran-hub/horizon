"""Executes horizon/SKILL.md's own embedded snippets (Steps 3 and 4)
against fake modules, so a regression in the skill's *prose-adjacent
code* -- not just the harness modules it calls -- is caught by the test
suite rather than only by a human reading the markdown.
"""

import re
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "horizon" / "SKILL.md"


def _extract_fenced_block(header: str) -> str:
    text = SKILL.read_text()
    idx = text.index(header)
    m = re.search(r"```python\n(.*?)```", text[idx:], re.S)
    assert m, f"no python fence found after {header!r}"
    return m.group(1)


class _FakeCoverageGate(Exception):
    pass


class _FakeGateUnrunnable(Exception):
    pass


def test_step4_coverage_placeholder_raises_not_implemented():
    """IMPORTANT 5: `result, output = "pass", "<subagent verdict + reasoning>"`
    was executable as written and always "passing" -- an agent running
    Step 4 verbatim, without actually dispatching the judge subagent,
    would silently fabricate a passing verdict for every coverage goal.
    A placeholder in a verification path must never be the passing value:
    it must raise, not return "pass"."""
    snippet = _extract_fenced_block("## Step 4 — Run the gate")

    class fake_gate:
        CoverageGate = _FakeCoverageGate
        GateUnrunnable = _FakeGateUnrunnable

        @staticmethod
        def run_gate_at(state):
            raise _FakeCoverageGate("coverage gate dispatched")

    class fake_goal:
        @staticmethod
        def abandon(slug, reason):
            raise AssertionError("should not reach the GateUnrunnable branch")

    namespace = {
        "gate": fake_gate,
        "goal": fake_goal,
        "state": {"slug": "x", "gate": {"value": "criterion"}},
    }
    with pytest.raises(NotImplementedError):
        exec(compile(snippet, "<horizon Step 4>", "exec"), namespace)
    # observable state: no fabricated passing verdict was ever assigned
    assert namespace.get("result") != "pass"


def test_step3_calls_run_gate_at_and_binds_the_returned_hash():
    """Step 3 must take its hash FROM the gate call, not compute one itself.

    The two-call form (tree_hash.compute, then gate.run_gate) leaves the
    ordering to the caller's discipline, and getting it backwards turns
    fail/fail/pass into fail/skipped/skipped -- a bug that shipped once in
    this project's own T4 test. run_gate_at returns the hash it actually
    evaluated so the ordering is structural. This guards against anyone
    reintroducing the split form in the skill text.
    """
    snippet = _extract_fenced_block(
        "## Step 3 — Run the gate and capture the hash it judged"
    )

    class fake_tree_hash:
        @staticmethod
        def compute(cwd, watch_paths):
            raise AssertionError("Step 3 recomputed the hash instead of using run_gate_at's")

    class fake_gate:
        CoverageGate = _FakeCoverageGate
        GateUnrunnable = _FakeGateUnrunnable

        @staticmethod
        def run_gate_at(state):
            return "fail", "gate output", "hash-the-gate-judged"

    namespace = {
        "gate": fake_gate,
        "tree_hash": fake_tree_hash,
        "state": {"cwd": "/x", "watch_paths": ["/x"]},
    }
    exec(compile(snippet, "<horizon Step 3>", "exec"), namespace)

    assert namespace["h"] == "hash-the-gate-judged"
    assert namespace["result"] == "fail"


def test_step3_sentinel_survives_an_unreadable_tree():
    """An unreadable watched tree must not kill the turn after the
    increment's work has landed on disk but before the checkpoint recording
    it -- a resumed session would be told to redo completed work. The
    sentinel can never equal a real digest, so the next turn runs the gate
    rather than skipping."""
    snippet = _extract_fenced_block(
        "## Step 3 — Run the gate and capture the hash it judged"
    )

    class fake_gate:
        CoverageGate = _FakeCoverageGate
        GateUnrunnable = _FakeGateUnrunnable

        @staticmethod
        def run_gate_at(state):
            return "fail", "", "unknown"   # what run_gate_at yields on an unreadable tree

    namespace = {"gate": fake_gate, "state": {"cwd": "/x", "watch_paths": ["/x"]}}
    exec(compile(snippet, "<horizon Step 3>", "exec"), namespace)
    assert namespace["h"] == "unknown"
