"""The skills' markdown is executable prose; drift makes it a lie.

A skill is only ever read by an agent that trusts it. When a module's API
moves and the markdown does not, the agent calls a function that no longer
exists — mid-task, with the user watching — or worse, follows prose that
describes behaviour the code no longer has.

This is not hypothetical here: `refine_log.count_edits_today` was renamed
and one reference survived in the skill's opening paragraph, where no test
looked, because only `horizon`'s snippets were ever executed.
"""

import ast
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN = REPO_ROOT / ".claude" / "harness" / "bin"
SKILLS = REPO_ROOT / ".claude" / "skills"
sys.path.insert(0, str(BIN))

import gate  # noqa: E402
import goal  # noqa: E402
import refine_log  # noqa: E402
import tree_hash  # noqa: E402

MODULES = {"refine_log": refine_log, "goal": goal, "gate": gate,
           "tree_hash": tree_hash}

# `refine_log.py` in prose is a filename, not an attribute reference.
NOT_ATTRIBUTES = {"py"}

# Names that were removed or renamed. None may appear in a skill again.
#
# The `module.attr` scan below cannot catch these on its own: the straggler
# that motivated this file was written as a bare `count_edits_today` in a
# backticked list, with no module prefix, so nothing tied it to a module.
# Guessing which backticked words are API names from their shape does not
# work either — `watch_paths` and `max_turns` are parameters, not
# attributes, and a heuristic that flags them cries wolf on every run.
#
# So this is a tombstone list, and keeping it current is part of removing a
# function, not an optional extra. It is deliberately literal: a name here
# may not appear in either skill in any form, including inside a sentence
# explaining what it used to do. Write the history in the module docstring
# or the README, where an agent will not mistake it for a live call.
REMOVED_NAMES = [
    "count_edits_today",   # -> count_recent_edits (rolling window)
    "commit_entry",        # two-phase record/commit -> single apply_edit
]

CODE_BLOCK = re.compile(r"```python\n(.*?)```", re.S)
ATTRIBUTE = re.compile(r"\b(refine_log|goal|gate|tree_hash)\.([A-Za-z_]\w*)")


def _skill(name: str) -> str:
    return (SKILLS / name / "SKILL.md").read_text()


@pytest.mark.parametrize("skill_name", ["refine", "horizon"])
def test_every_module_reference_in_the_prose_exists(skill_name):
    """Catches the rename that leaves one straggler behind."""
    missing = []
    for module_name, attribute in ATTRIBUTE.findall(_skill(skill_name)):
        if attribute in NOT_ATTRIBUTES:
            continue
        if not hasattr(MODULES[module_name], attribute):
            missing.append(f"{module_name}.{attribute}")
    assert not missing, (
        f"{skill_name}/SKILL.md references names that no longer exist: "
        f"{sorted(set(missing))}"
    )


@pytest.mark.parametrize("skill_name", ["refine", "horizon"])
def test_no_removed_api_name_survives_in_a_skill(skill_name):
    text = _skill(skill_name)
    found = [name for name in REMOVED_NAMES if name in text]
    assert not found, (
        f"{skill_name}/SKILL.md still names removed API(s): {found}. An agent "
        f"reading this will call something that does not exist."
    )


@pytest.mark.parametrize("skill_name", ["refine", "horizon"])
def test_every_python_block_parses(skill_name):
    """A block an agent is told to paste must at least be Python."""
    for i, block in enumerate(CODE_BLOCK.findall(_skill(skill_name))):
        try:
            ast.parse(block)
        except SyntaxError as e:
            pytest.fail(f"{skill_name}/SKILL.md python block {i} does not parse: {e}")


def test_the_refine_preflight_snippet_runs_and_reports_the_count(tmp_path, monkeypatch, capsys):
    """The cap pre-flight is the one snippet the skill promises to run on
    every invocation. If it raises, the agent's fallback is to guess."""
    blocks = CODE_BLOCK.findall(_skill("refine"))
    preflight = next(b for b in blocks if "count_recent_edits" in b)

    root = tmp_path / "harness"
    (root / "undo").mkdir(parents=True)
    monkeypatch.setattr(refine_log.paths, "undo_dir", lambda: root / "undo")
    monkeypatch.setattr(refine_log.paths, "changelog", lambda: root / "CHANGELOG.md")
    refine_log.apply_edit("M", [(tmp_path / "a.md", "x")], "r", "e")

    exec(compile(preflight, "<refine preflight>", "exec"), {"__name__": "__main__"})

    out = capsys.readouterr().out
    assert "1 of 3" in out, out
    assert "24h" in out, out


def test_the_refine_undo_snippet_runs(tmp_path, monkeypatch, capsys):
    blocks = CODE_BLOCK.findall(_skill("refine"))
    undo_block = next(b for b in blocks if "NothingToUndo" in b)

    root = tmp_path / "harness"
    (root / "undo").mkdir(parents=True)
    monkeypatch.setattr(refine_log.paths, "undo_dir", lambda: root / "undo")
    monkeypatch.setattr(refine_log.paths, "changelog", lambda: root / "CHANGELOG.md")
    target = tmp_path / "CLAUDE.md"
    target.write_text("before\n")
    refine_log.apply_edit("rho", [(target, "after\n")], "r", "e")

    exec(compile(undo_block, "<refine undo>", "exec"), {"__name__": "__main__"})

    assert target.read_text() == "before\n"
    assert "reverted entry 0001" in capsys.readouterr().out

def test_horizon_documents_the_gate_timeout_and_the_coverage_floor():
    """Two numbers decide real outcomes and were documented nowhere: a gate
    slower than the default timeout is misclassified as a broken goal
    unless the skill says the knob exists, and a coverage verdict shorter
    than the evidence floor refuses completion with no warning. Tied to
    the constants so the prose rots loudly when they move."""
    text = _skill("horizon")
    assert "gate.DEFAULT_TIMEOUT" in text
    assert str(gate.DEFAULT_TIMEOUT) in text
    assert "goal.MIN_COVERAGE_EVIDENCE" in text
    assert str(goal.MIN_COVERAGE_EVIDENCE) in text


def test_refine_states_the_tripwire_scope_the_code_enforces():
    """The prose claimed the content scan fired only on G/K edits while the
    module scans every layer, so a skill-compliant M or rho proposal was
    refused AFTER user approval -- and the error table forbids rewording,
    leaving no in-skill recovery. The prose must state the enforced scope,
    including the G/K-only memory-write patterns."""
    text = _skill("refine")
    assert "when a G or K edit's text references" not in text
    section = text[text.index("content tripwire"):text.index("G4 —")]
    assert "every layer" in section
    assert "MEMORY.md" in section
    assert ".claude/projects/" in section


def test_refine_gives_dedupe_an_index_update_mechanism():
    """'Update the matching entry' was unimplementable when the update
    touched an index line: AppendLine only appends and rendered bodies are
    forbidden, so the exact case dedupe exists for had no permitted path."""
    text = _skill("refine")
    assert "refine_log.ReplaceLine" in text

def test_the_skills_stay_lean():
    """Both cold audits converged on the same criticism: two of the last
    three Criticals hid in the prose, and '390 lines of prose for
    load -> work -> gate -> checkpoint -> repeat is where the
    contradictions live'. The executable blocks, the tables, and the
    operative rules carry the skill; rationale gets one line each.

    The ceilings are pinned AT the lean rewrite's achieved size, with no
    headroom, so any future addition must consciously raise them here
    with a justification — re-bloat fails the suite instead of waiting
    for the next audit. (The horizon figure is above the audit's
    '40% of 487' arithmetic because the coverage completion block and
    the Heartbeat section did not exist when that arithmetic was done;
    prose outside code blocks and tables is what actually got cut.)"""
    horizon_lines = _skill("horizon").count("\n")
    refine_lines = _skill("refine").count("\n")
    assert horizon_lines <= 380, (
        f"horizon/SKILL.md is {horizon_lines} lines; the ceiling is 380"
    )
    assert refine_lines <= 430, (
        f"refine/SKILL.md is {refine_lines} lines; the ceiling is 430"
    )


def test_horizon_documents_the_heartbeat():
    """Spec §5.2 step 7 — 'Heartbeat via /loop or ScheduleWakeup for runs
    spanning sessions' — was never built, so a goal survived interruption
    but nothing ever resumed it: cross-session continuation was manual.
    The skill must carry the recipe that closes that gap, and the loop
    must be bounded by the same gate and budget as a manual turn."""
    text = _skill("horizon")
    assert "## Heartbeat" in text
    section = text[text.index("## Heartbeat"):]
    end = section.find("\n## ")
    if end != -1:
        section = section[:end]
    assert "/loop" in section
    assert "budget" in section.lower()
    assert "active" in section.lower()  # a firing on a closed goal must stop
