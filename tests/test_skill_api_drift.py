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
