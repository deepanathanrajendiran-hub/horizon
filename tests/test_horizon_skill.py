"""Executes horizon/SKILL.md's own embedded snippets against fake
modules, so a regression in the skill's *prose-adjacent code* -- not just
the harness modules it calls -- is caught by the test suite rather than
only by a human reading the markdown.
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
        def run_gate(state):
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
