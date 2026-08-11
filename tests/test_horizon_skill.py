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
    snippet = _extract_fenced_block("## Step 3 — Run the gate")

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
    snippet = _extract_fenced_block("## Step 3 — Run the gate")

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
    snippet = _extract_fenced_block("## Step 3 — Run the gate")

    class fake_gate:
        CoverageGate = _FakeCoverageGate
        GateUnrunnable = _FakeGateUnrunnable

        @staticmethod
        def run_gate_at(state):
            return "fail", "", "unknown"   # what run_gate_at yields on an unreadable tree

    namespace = {"gate": fake_gate, "state": {"cwd": "/x", "watch_paths": ["/x"]}}
    exec(compile(snippet, "<horizon Step 3>", "exec"), namespace)
    assert namespace["h"] == "unknown"


def test_the_gate_is_called_exactly_once_per_turn():
    """Steps 3 and 4 were once separate sections that each called the gate,
    so a turn ran the full test suite twice, every turn, for the whole
    budget."""
    import re
    text = SKILL.read_text()

    step3 = text[text.index("## Step 3 — Run the gate"):text.index("## Step 4 — Checkpoint")]
    assert step3.count("run_gate_at(state)") == 1, "Step 3 calls the gate more than once"

    i = text.index("## One turn, end to end")
    turn = re.search(r"```bash\n(.*?)```", text[i:], re.S).group(1)
    assert turn.count("run_gate_at(state)") == 1, "the turn block calls the gate more than once"

    # Steps 4 and 5 must not call it again — that was the original defect,
    # where two consecutive steps each ran the full gate in one turn.
    rest = text[text.index("## Step 4 — Checkpoint"):text.index("## Errors at a glance")]
    assert "run_gate_at" not in rest, "a later step calls the gate again in the same turn"


def test_the_one_turn_block_is_valid_python():
    """The skill must ship a runnable turn, not steps an agent reassembles.
    Hand-assembly is where the load-bearing hash ordering gets lost."""
    import re
    text = SKILL.read_text()
    i = text.index("## One turn, end to end")
    bash = re.search(r"```bash\n(.*?)```", text[i:], re.S).group(1)
    body = re.search(r"<<'PY'\n(.*?)\nPY", bash, re.S)
    assert body, "the turn block is not a self-contained heredoc"
    compile(body.group(1), "<horizon one-turn>", "exec")


def test_the_turn_block_does_not_recompute_the_hash():
    """It must pass run_gate_at's returned `h` straight to checkpoint."""
    import re
    text = SKILL.read_text()
    i = text.index("## One turn, end to end")
    block = re.search(r"```bash\n(.*?)```", text[i:], re.S).group(1)
    assert "tree_hash.compute" not in block
    assert "result, output, h = gate.run_gate_at(state)" in block
    assert "goal.checkpoint(SLUG, DID, NEXT, h," in block


def test_the_turn_block_refuses_to_report_exhaustion_as_success():
    import re
    text = SKILL.read_text()
    i = text.index("## One turn, end to end")
    block = re.search(r"```bash\n(.*?)```", text[i:], re.S).group(1)
    assert "budget_exhausted" in block
    assert "never report this as success" in block
    assert block.index("budget_exhausted") < block.index("run_gate_at"), (
        "budget is checked after the gate runs — a turn past the cap would "
        "still burn a full gate run"
    )
