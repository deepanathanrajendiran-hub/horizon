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


def test_step3_survives_unreadable_watch_tree():
    """IMPORTANT 6: tree_hash.changed() absorbs an unreadable watched tree
    into its doubt-path (spec §5.2.1), but Step 3 called bare
    tree_hash.compute(), which raises on the same condition. That would
    kill the turn after the increment's work landed on disk but before the
    checkpoint recording it -- a resumed session would be told to redo
    completed work. Step 3 must catch the exception and fall back to the
    sentinel hash "unknown", which can never equal a future real hash, so
    the next turn's changed() cannot mistake it for "unchanged"."""
    snippet = _extract_fenced_block(
        "## Step 3 — Hash the tree, before anything else touches it"
    )

    class fake_tree_hash:
        @staticmethod
        def compute(cwd, watch_paths):
            raise OSError("simulated: watch root unstattable")

    namespace = {
        "tree_hash": fake_tree_hash,
        "state": {"cwd": "/x", "watch_paths": ["/x"]},
    }
    exec(compile(snippet, "<horizon Step 3>", "exec"), namespace)
    assert namespace["h"] == "unknown"


def test_step3_still_uses_the_real_hash_on_success():
    """Sanity: the try/except must not mask a healthy compute() -- the
    real hash must still flow through to `h` when nothing fails."""
    snippet = _extract_fenced_block(
        "## Step 3 — Hash the tree, before anything else touches it"
    )

    class fake_tree_hash:
        @staticmethod
        def compute(cwd, watch_paths):
            return "real-hash-abc123"

    namespace = {
        "tree_hash": fake_tree_hash,
        "state": {"cwd": "/x", "watch_paths": ["/x"]},
    }
    exec(compile(snippet, "<horizon Step 3>", "exec"), namespace)
    assert namespace["h"] == "real-hash-abc123"
