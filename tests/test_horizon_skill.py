"""Executes horizon/SKILL.md's own turn blocks -- the command-gate turn
and the coverage completion block -- against fake modules, so a
regression in the skill's *prose-adjacent code*, not just the harness
modules it calls, is caught by the test suite rather than only by a
human reading the markdown.
"""

import re
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parents[1] / ".claude" / "skills" / "horizon" / "SKILL.md"




class _FakeCoverageGate(Exception):
    pass


class _FakeGateUnrunnable(Exception):
    pass

class _FakeGoal:
    """Records every mutator call so a test can assert on order and args."""

    def __init__(self, exhausted=False, status="active"):
        self.calls = []
        self._exhausted = exhausted
        self._state = {
            "slug": "x", "goal": "g", "cwd": "/x", "watch_paths": ["/x"],
            "gate": {"kind": "command", "value": "true"},
            "budget": {"max_turns": 20, "max_minutes": None},
            "used": {"turns": 1}, "status": status,
            "checkpoints": [], "subagents": [],
        }

    def load(self, slug):
        return dict(self._state)

    def checkpoint(self, slug, did, next_action, h, result, output):
        self.calls.append(("checkpoint", slug, did, next_action, h, result, output))
        return dict(self._state)

    def budget_exhausted(self, state):
        return self._exhausted

    def complete(self, slug, output):
        self.calls.append(("complete", slug, output))
        return dict(self._state)

    def abandon(self, slug, reason):
        self.calls.append(("abandon", slug, reason))
        return dict(self._state)

    def add_subagent(self, slug, role, name, brief):
        self.calls.append(("add_subagent", slug, role, name, brief))
        return dict(self._state)


def _fake_gate(run_gate_at):
    class fake:
        CoverageGate = _FakeCoverageGate
        GateUnrunnable = _FakeGateUnrunnable
        DEFAULT_TIMEOUT = 480
    fake.run_gate_at = staticmethod(run_gate_at)
    return fake


def _turn_blocks() -> list:
    """The bash heredoc bodies in 'One turn, end to end', in order:
    [0] the gate turn, [1] the coverage completion block."""
    text = SKILL.read_text()
    section = text[text.index("## One turn, end to end"):text.index("## Step 1")]
    bodies = []
    for block in re.findall(r"```bash\n(.*?)```", section, re.S):
        m = re.search(r"<<'PY'\n(.*?)\nPY", block, re.S)
        assert m, "a turn block is not a self-contained heredoc"
        bodies.append(m.group(1))
    assert len(bodies) >= 2, (
        "the coverage turn has no completion block: nothing in the "
        "prescribed flow can checkpoint the judge's verdict, stop at the "
        "budget, or call goal.complete for a coverage goal"
    )
    return bodies


def _run(body: str, namespace: dict) -> None:
    """Execute a turn block from its first fill-in onward, skipping the
    sys.path bootstrap -- the fakes are already in the namespace."""
    exec(compile(body[body.index("SLUG"):], "<horizon turn>", "exec"), namespace)


def _filled(body: str, result: str = "pass", verdict: str = None) -> str:
    verdict = verdict or ("every row was checked against its cited line; "
                          "verdicts recorded per row, none sampled")
    return (body.replace("<pass-or-fail>", result)
                .replace("<hash-from-part-one>", "cov-hash-1234")
                .replace("<judge-reasoning>", verdict)
                .replace("<same DID as the first block>", "did the thing")
                .replace("<what the next turn does>", "next thing")
                .replace("<slug>", "x"))








def test_the_gate_is_called_exactly_once_per_turn():
    """Steps 3 and 4 were once separate sections that each called the gate,
    so a turn ran the full test suite twice, every turn, for the whole
    budget."""
    text = SKILL.read_text()

    step3 = text[text.index("## Step 3 — Run the gate"):text.index("## Step 4 — Checkpoint")]
    assert step3.count("run_gate_at(state)") == 1, "Step 3 calls the gate more than once"

    blocks = _turn_blocks()
    assert blocks[0].count("run_gate_at(state)") == 1, (
        "the turn block calls the gate more than once"
    )
    assert "run_gate_at" not in blocks[1], (
        "the coverage completion block must not run the gate again -- the "
        "judge already ruled on the tree part one hashed"
    )

    # Steps 4 and 5 must not call it again — that was the original defect,
    # where two consecutive steps each ran the full gate in one turn.
    rest = text[text.index("## Step 4 — Checkpoint"):text.index("## Errors at a glance")]
    assert "run_gate_at" not in rest, "a later step calls the gate again in the same turn"


def test_the_one_turn_block_is_valid_python():
    """The skill must ship runnable turns, not steps an agent reassembles.
    Hand-assembly is where the load-bearing hash ordering gets lost."""
    for i, body in enumerate(_turn_blocks()):
        compile(body, f"<horizon turn block {i}>", "exec")


def test_the_turn_block_does_not_recompute_the_hash():
    """It must pass run_gate_at's returned `h` straight to checkpoint."""
    import re
    text = SKILL.read_text()
    i = text.index("## One turn, end to end")
    block = re.search(r"```bash\n(.*?)```", text[i:], re.S).group(1)
    assert "tree_hash.compute" not in block
    assert "result, output, h = gate.run_gate_at(state)" in block
    assert "goal.checkpoint(SLUG, DID, NEXT, h," in block


def test_the_turn_block_records_the_work_before_checking_the_budget():
    """Two orderings, both load-bearing. BEFORE the gate: an exhausted or
    closed goal must stop the turn before any work-spend (the guard).
    AFTER the gate: the checkpoint must land before the budget stop, so
    the exhausting turn's own increment is never lost."""
    text = SKILL.read_text()
    block = _turn_blocks()[0]

    gate_i = block.index("result, output, h = gate.run_gate_at(state)")
    head, tail = block[:gate_i], block[gate_i:]

    assert "budget_exhausted" in head, (
        "no pre-work guard: an already-exhausted goal burns a full "
        "increment before anything refuses"
    )
    assert 'status' in head and 'active' in head, (
        "no pre-work guard for a completed/abandoned goal"
    )
    assert "budget_exhausted" in tail
    assert "never report this as success" in tail
    assert tail.index("goal.checkpoint(") < tail.index("budget_exhausted"), (
        "the budget is checked before the work is recorded — the turn that "
        "exhausts the budget loses its own increment"
    )


def test_the_turn_block_does_not_hardcode_a_project_root():
    """`cd $(git rev-parse --show-toplevel)` contradicts the skill's own
    advice 20 lines above it, and in a monorepo with .claude/ in a
    subpackage it lands somewhere the bootstrap cannot find the modules."""
    import re
    text = SKILL.read_text()
    i = text.index("## One turn, end to end")
    block = re.search(r"```bash\n(.*?)```", text[i:], re.S).group(1)
    assert "git rev-parse" not in block
    assert "cd " not in block

def test_the_turn_block_binds_the_gate_evaluated_hash():
    """The checkpoint must record the hash the gate ACTUALLY evaluated,
    straight from run_gate_at -- getting the ordering wrong turns
    fail/fail/pass into fail/skipped/skipped."""
    goal = _FakeGoal()
    gate = _fake_gate(lambda state: ("fail", "gate output", "hash-the-gate-judged"))
    _run(_turn_blocks()[0], {"goal": goal, "gate": gate})
    assert [c[0] for c in goal.calls] == ["checkpoint"]
    assert goal.calls[0][4] == "hash-the-gate-judged"
    assert goal.calls[0][5] == "fail"


def test_the_turn_block_sentinel_survives_an_unreadable_tree():
    """An unreadable tree must still checkpoint, with the sentinel that can
    never equal a real digest, so the next turn runs the gate."""
    goal = _FakeGoal()
    gate = _fake_gate(lambda state: ("fail", "", "unknown"))
    _run(_turn_blocks()[0], {"goal": goal, "gate": gate})
    assert goal.calls[0][4] == "unknown"


def test_the_turn_block_stops_cleanly_at_a_coverage_gate(capsys):
    """A coverage turn stops for the judge: no checkpoint here (the
    completion block records the verdict), exit 0, and the hash printed
    bare -- a repr-quoted hash pasted into the completion block's quoted
    template would store a doubly-quoted digest."""
    def raise_coverage(state):
        e = _FakeCoverageGate("coverage gate")
        e.tree_hash = "cov-hash-1234"
        raise e

    goal = _FakeGoal()
    with pytest.raises(SystemExit) as exit_info:
        _run(_turn_blocks()[0], {"goal": goal, "gate": _fake_gate(raise_coverage)})
    assert exit_info.value.code == 0
    assert goal.calls == []
    out = capsys.readouterr().out
    assert "cov-hash-1234" in out
    assert "'cov-hash-1234'" not in out


def test_the_turn_block_distinguishes_a_timeout_from_a_broken_gate():
    """exit 126/127 is a broken goal; a timeout may just be a slow suite.
    Abandoning on the first timeout misclassifies the workload horizon was
    built for -- the timeout is adjustable and the block must say so
    instead of abandoning."""
    goal = _FakeGoal()

    def timeout(state):
        raise _FakeGateUnrunnable("gate timed out after 480s: pytest -q")

    with pytest.raises(SystemExit) as exit_info:
        _run(_turn_blocks()[0], {"goal": goal, "gate": _fake_gate(timeout)})
    assert [c[0] for c in goal.calls] == [], (
        "a slow gate was abandoned as a broken goal"
    )
    assert "timed out" in str(exit_info.value.code).lower()

    goal2 = _FakeGoal()

    def broken(state):
        raise _FakeGateUnrunnable("exit 127: command not found")

    with pytest.raises(SystemExit):
        _run(_turn_blocks()[0], {"goal": goal2, "gate": _fake_gate(broken)})
    assert [c[0] for c in goal2.calls] == ["abandon"]


def test_the_coverage_completion_block_fails_loud_on_unfilled_placeholders():
    """A placeholder in a verification path must never be the passing
    value: run verbatim, without a judge verdict, the block refuses --
    no checkpoint, no complete, no fabricated pass."""
    goal = _FakeGoal()
    with pytest.raises(SystemExit) as exit_info:
        _run(_turn_blocks()[1], {"goal": goal})
    assert goal.calls == []
    assert exit_info.value.code not in (0, None)


def test_the_coverage_completion_block_checkpoints_then_completes_on_pass():
    """The judge's verdict is checkpointed with the part-one hash, and only
    then does complete() run -- the prescribed coverage flow terminates.
    The judge is registered ahead of the checkpoint, because complete()
    refuses a coverage goal with no judge recorded for the turn."""
    goal = _FakeGoal()
    _run(_filled(_turn_blocks()[1]), {"goal": goal})
    assert [c[0] for c in goal.calls] == ["add_subagent", "checkpoint", "complete"]
    assert "judge" in goal.calls[0][2].lower(), "the role must register as a judge"
    checkpoint = goal.calls[1]
    assert checkpoint[4] == "cov-hash-1234"
    assert checkpoint[5] == "pass"


def test_the_coverage_completion_block_records_before_the_budget_stop():
    """The turn that exhausts the budget must not lose its own record, and
    an exhausted coverage goal must stop -- the same bounded-budget promise
    the command turn keeps."""
    goal = _FakeGoal(exhausted=True)
    with pytest.raises(SystemExit) as exit_info:
        _run(_filled(_turn_blocks()[1], result="fail"), {"goal": goal})
    assert exit_info.value.code == 0
    assert [c[0] for c in goal.calls] == ["add_subagent", "checkpoint"], (
        "the exhausting turn lost its own increment, or completed anyway"
    )

def test_the_turn_block_refuses_to_work_an_exhausted_or_closed_goal():
    """checkpoint() now refuses beyond the cap, but by then the gate has
    already run — a whole increment of spend on a goal nobody granted
    more budget. The block must stop at load time, before the gate."""
    def gate_must_not_run(state):
        raise AssertionError("the gate ran on an exhausted goal")

    goal = _FakeGoal(exhausted=True)
    with pytest.raises(SystemExit) as exit_info:
        _run(_turn_blocks()[0], {"goal": goal, "gate": _fake_gate(gate_must_not_run)})
    assert goal.calls == []
    assert exit_info.value.code not in (0, None) or "EXHAUST" in str(exit_info.value.code).upper()

    goal2 = _FakeGoal(status="complete")
    with pytest.raises(SystemExit):
        _run(_turn_blocks()[0], {"goal": goal2, "gate": _fake_gate(gate_must_not_run)})
    assert goal2.calls == []
