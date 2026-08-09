# Continual Harness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build two project-scoped Claude Code skills — `refine` (continual harness over ρ, G, K, M) and `horizon` (goal + gate + bounded continuation) — backed by five tested Python modules.

**Architecture:** The mechanical core lives in `.claude/harness/bin/` as small, single-responsibility Python modules with pytest coverage: `paths.py` (location resolution), `tree_hash.py` (deterministic change detection), `goal.py` (goal state CRUD), `refine_log.py` (changelog + rollback), `gate.py` (gate execution and skip decision). The two `SKILL.md` files are thin instruction layers that call these modules rather than re-deriving logic inline — that is what makes the risky behavior testable instead of vibes-based.

**Tech Stack:** Python 3.14 (stdlib only — no runtime dependencies), pytest via `uv run --with pytest` (no venv, no install step), git, markdown skills.

## Global Constraints

- **Spec:** `docs/superpowers/specs/2026-08-08-continual-harness-design.md`. Every task implements a numbered section; cite it in the commit.
- **Project-scoped only.** All writes land under `/Users/deeps/Documents/NEW STUFF/fun ` (note: the directory name ends in a space — every path must be quoted). Nothing under `~/.claude/` is modified until §8 promotion.
- **Zero runtime dependencies.** Standard library only. pytest is a test-time dependency supplied by `uv run --with pytest`.
- **Skip asymmetry (§5.2.1):** a false gate-skip is a correctness bug; a false gate-run costs seconds. On any doubt, report changed.
- **Guardrail G3 (§4.3):** no code or instruction may weaken verification, testing, or approval requirements.
- **Test command:** `uv run --with pytest pytest tests/ -v` run from the project root.
- **Python invocation:** `python3` (3.14.4 confirmed present).

---

## File Structure

| Path | Responsibility |
|---|---|
| `.claude/harness/bin/paths.py` | Resolve harness root, goals dir, undo dir, changelog path |
| `.claude/harness/bin/tree_hash.py` | Deterministic change detection (§5.2.1) |
| `.claude/harness/bin/goal.py` | Goal state create/load/checkpoint/complete (§5.1, §5.5) |
| `.claude/harness/bin/refine_log.py` | Changelog append + undo (§4.2 step 6, §6) |
| `.claude/harness/bin/gate.py` | Gate execution, skip decision, unrunnable detection (§5.2.1, §6) |
| `.claude/skills/horizon/SKILL.md` | Goal/gate/continuation instructions (§5) |
| `.claude/skills/refine/SKILL.md` | Harvest/route/propose instructions (§4) |
| `tests/test_tree_hash.py` | T8 + unit coverage |
| `tests/test_goal.py` | Budget/completion contracts + §6 error handling |
| `tests/test_refine_log.py` | T7 rollback |
| `tests/test_gate.py` | T4, T5, T6 |
| `docs/superpowers/acceptance.md` | T1–T3, T9, T10 acceptance procedures |

---

### Task 1: Path resolution and change detection

Implements §5.2.1. This task first, because the gitignore bug it guards is the one confirmed defect in the design.

**Files:**
- Create: `.claude/harness/bin/paths.py`
- Create: `.claude/harness/bin/tree_hash.py`
- Test: `tests/test_tree_hash.py`

**Interfaces:**
- Consumes: nothing (first task).
- Produces:
  - `paths.harness_root() -> pathlib.Path`
  - `paths.goals_dir() -> pathlib.Path`
  - `paths.undo_dir() -> pathlib.Path`
  - `paths.changelog() -> pathlib.Path`
  - `tree_hash.compute(cwd: str | Path, watch_paths: list[str] | None = None) -> str` (hex sha256)
  - `tree_hash.changed(cwd, watch_paths, previous: str | None) -> bool`
  - `tree_hash.EXCLUDED_DIRS: set[str]`

- [ ] **Step 1: Write the failing test**

Create `tests/test_tree_hash.py`:

```python
import subprocess
import sys
from pathlib import Path

BIN = Path(__file__).resolve().parents[1] / ".claude" / "harness" / "bin"
sys.path.insert(0, str(BIN))

import tree_hash  # noqa: E402


def _repo(tmp_path: Path, gitignore: str = "out/\nnode_modules/\n") -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    (repo / ".gitignore").write_text(gitignore)
    (repo / "tracked.txt").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init"],
        cwd=repo, check=True,
    )
    return repo


def test_gitignored_write_is_detected(tmp_path):
    """T8 regression: git status --porcelain=v1 cannot see writes inside
    ignored directories, so a research goal writing only to out/ would have
    its gate wrongly skipped."""
    repo = _repo(tmp_path)
    before = tree_hash.compute(repo)

    (repo / "out").mkdir()
    (repo / "out" / "report.md").write_text("findings v1")

    porcelain = subprocess.run(
        ["git", "status", "--porcelain=v1"], cwd=repo,
        capture_output=True, text=True, check=True,
    ).stdout
    assert porcelain == "", "precondition: git must be blind to the ignored write"

    assert tree_hash.compute(repo) != before, "tree_hash must still detect it"


def test_tracked_edit_is_detected(tmp_path):
    repo = _repo(tmp_path)
    before = tree_hash.compute(repo)
    (repo / "tracked.txt").write_text("changed")
    assert tree_hash.compute(repo) != before


def test_stable_when_nothing_changes(tmp_path):
    repo = _repo(tmp_path)
    assert tree_hash.compute(repo) == tree_hash.compute(repo)


def test_excluded_dirs_are_ignored(tmp_path):
    repo = _repo(tmp_path)
    (repo / "node_modules").mkdir()
    before = tree_hash.compute(repo)
    (repo / "node_modules" / "junk.js").write_text("noise")
    assert tree_hash.compute(repo) == before


def test_watch_paths_add_coverage_for_ignored_output(tmp_path):
    """watch_paths scopes the mtime walk only. Git state always
    contributes, which is the conservative direction: it can add a
    detection, never remove one."""
    repo = _repo(tmp_path)
    (repo / "out").mkdir()
    watch = [str(repo / "out")]

    before = tree_hash.compute(repo, watch)
    (repo / "out" / "r.md").write_text("real output")
    assert tree_hash.compute(repo, watch) != before

    # And a tracked change is still caught even though it sits outside watch.
    mid = tree_hash.compute(repo, watch)
    (repo / "tracked.txt").write_text("edited")
    assert tree_hash.compute(repo, watch) != mid


def test_changed_is_true_without_previous(tmp_path):
    repo = _repo(tmp_path)
    assert tree_hash.changed(repo, None, None) is True


def test_changed_is_true_on_doubt(tmp_path, monkeypatch):
    """Skip asymmetry: a false skip is a correctness bug, so any failure
    to compute must report changed."""
    repo = _repo(tmp_path)
    monkeypatch.setattr(
        tree_hash, "compute",
        lambda *a, **k: (_ for _ in ()).throw(OSError("boom")),
    )
    assert tree_hash.changed(repo, None, "abc123") is True


def test_non_git_directory_uses_walk_only(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "a.txt").write_text("one")
    before = tree_hash.compute(plain)
    (plain / "b.txt").write_text("two")
    assert tree_hash.compute(plain) != before
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_tree_hash.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'tree_hash'`

- [ ] **Step 3: Write `paths.py`**

Create `.claude/harness/bin/paths.py`:

```python
"""Filesystem locations for the continual harness.

All paths resolve relative to the harness root so the modules work
regardless of the caller's working directory. The project directory name
contains a trailing space, so every consumer must quote paths.
"""

from pathlib import Path


def harness_root() -> Path:
    """The .claude/harness directory containing this bin/ folder."""
    return Path(__file__).resolve().parent.parent


def goals_dir() -> Path:
    d = harness_root() / "goals"
    d.mkdir(parents=True, exist_ok=True)
    return d


def undo_dir() -> Path:
    d = harness_root() / "undo"
    d.mkdir(parents=True, exist_ok=True)
    return d


def changelog() -> Path:
    return harness_root() / "CHANGELOG.md"
```

- [ ] **Step 4: Write `tree_hash.py`**

Create `.claude/harness/bin/tree_hash.py`:

```python
"""Deterministic change detection for horizon gates (spec §5.2.1).

Combines three inputs:
  1. git HEAD          — catches commits
  2. git porcelain     — catches tracked edits and untracked files
  3. mtime+size walk   — catches everything git cannot see

Input 3 is not redundant. `git status --porcelain=v1` does not report
writes inside .gitignore'd directories, so a goal whose only output lands
in out/ or dist/ would otherwise have its gate wrongly skipped.

Skip asymmetry: a false skip is a correctness bug, a false run costs
seconds. Every failure path in this module reports "changed".
"""

import hashlib
import os
import subprocess
from pathlib import Path

EXCLUDED_DIRS = {
    ".git", "node_modules", ".venv", "venv", "target",
    "__pycache__", ".mypy_cache", ".pytest_cache",
}


def _git(cwd, *args) -> str | None:
    """Run a git command, returning stdout or None if git is unusable here."""
    try:
        r = subprocess.run(
            ["git", *args], cwd=str(cwd),
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout if r.returncode == 0 else None


def _walk_entries(root: Path) -> list[str]:
    """One line per file: path, mtime_ns, size. Sorted for determinism."""
    if not root.exists():
        return [f"{root}\x00MISSING"]
    if root.is_file():
        st = root.stat()
        return [f"{root}\x00{st.st_mtime_ns}\x00{st.st_size}"]

    entries: list[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDED_DIRS]
        for name in filenames:
            p = Path(dirpath) / name
            try:
                st = p.stat()
            except OSError:
                entries.append(f"{p}\x00UNREADABLE")
                continue
            entries.append(f"{p}\x00{st.st_mtime_ns}\x00{st.st_size}")
    return sorted(entries)


def compute(cwd, watch_paths: list[str] | None = None) -> str:
    """Hex sha256 over git state plus an mtime walk of watch_paths.

    watch_paths scopes the *walk* only; git state always contributes.
    That is deliberate and conservative: narrowing the walk can add a
    detection (an ignored output dir) but never remove one.
    """
    cwd = Path(cwd)
    h = hashlib.sha256()

    h.update(b"HEAD\x00" + (_git(cwd, "rev-parse", "HEAD") or "NONE").encode() + b"\x00")
    h.update(b"STATUS\x00" + (_git(cwd, "status", "--porcelain=v1") or "NONE").encode() + b"\x00")

    for wp in sorted(str(p) for p in (watch_paths or [cwd])):
        h.update(b"WATCH\x00" + wp.encode() + b"\x00")
        for entry in _walk_entries(Path(wp)):
            h.update(entry.encode() + b"\x00")

    return h.hexdigest()


def changed(cwd, watch_paths: list[str] | None, previous: str | None) -> bool:
    """True if the tree may have changed. Any doubt reports True."""
    if not previous:
        return True
    try:
        return compute(cwd, watch_paths) != previous
    except Exception:
        return True


if __name__ == "__main__":
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "."
    watch = sys.argv[2:] or None
    print(compute(target, watch))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_tree_hash.py -v`
Expected: 8 passed

- [ ] **Step 6: Commit**

```bash
cd "/Users/deeps/Documents/NEW STUFF/fun "
git add .claude/harness/bin/paths.py .claude/harness/bin/tree_hash.py tests/test_tree_hash.py
git commit -m "feat(harness): deterministic change detection (spec 5.2.1)

Guards the confirmed defect where git status --porcelain=v1 is blind to
writes inside ignored directories, which would wrongly skip the gate."
```

---

### Task 2: Goal state

Implements §5.1, §5.5, and the §6 rows for missing gate and malformed state.

**Files:**
- Create: `.claude/harness/bin/goal.py`
- Test: `tests/test_goal.py`

**Interfaces:**
- Consumes: `paths.goals_dir()` from Task 1.
- Produces:
  - `goal.slugify(text: str) -> str`
  - `goal.create(goal, cwd, gate_kind, gate_value, max_turns=20, max_minutes=None, watch_paths=None) -> dict` — raises `GateRequired` if `gate_value` is empty
  - `goal.load(slug) -> dict` — raises `GoalStateError` on missing/malformed
  - `goal.checkpoint(slug, did, next_action, tree_hash_value, gate_result, gate_output="") -> dict`
  - `goal.budget_exhausted(state) -> bool`
  - `goal.complete(slug, gate_output) -> dict` — raises `GateNotPassed` unless the last checkpoint gate is `"pass"`
  - `goal.abandon(slug, reason) -> dict`
  - Exceptions: `GateRequired`, `GoalStateError`, `GateNotPassed`

- [ ] **Step 1: Write the failing test**

Create `tests/test_goal.py`:

```python
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / ".claude" / "harness" / "bin"
sys.path.insert(0, str(BIN))

import goal  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_goals(tmp_path, monkeypatch):
    d = tmp_path / "goals"
    d.mkdir()
    monkeypatch.setattr(goal.paths, "goals_dir", lambda: d)
    return d


def _make(**kw):
    args = dict(
        goal="Refactor the auth module",
        cwd="/tmp/proj",
        gate_kind="command",
        gate_value="pytest -q",
        max_turns=3,
    )
    args.update(kw)
    return goal.create(**args)


def test_slugify_is_filesystem_safe():
    assert goal.slugify("Refactor the AUTH module!") == "refactor-the-auth-module"
    assert goal.slugify("a" * 100) == "a" * 60


def test_create_writes_state(isolated_goals):
    state = _make()
    assert (isolated_goals / f"{state['slug']}.json").exists()
    assert state["status"] == "active"
    assert state["used"]["turns"] == 0
    assert state["subagents"] == []


def test_create_refuses_without_gate():
    """Spec 6: a long-horizon loop with no termination criterion is an
    infinite loop."""
    with pytest.raises(goal.GateRequired):
        _make(gate_value="")


def test_checkpoint_increments_turns():
    state = _make()
    goal.checkpoint(state["slug"], "did a thing", "do next thing", "hash1", "fail", "boom")
    reloaded = goal.load(state["slug"])
    assert reloaded["used"]["turns"] == 1
    assert reloaded["checkpoints"][0]["gate"] == "fail"
    assert reloaded["checkpoints"][0]["tree_hash"] == "hash1"


def test_budget_exhausted_at_max_turns():
    state = _make(max_turns=2)
    assert goal.budget_exhausted(state) is False
    for i in range(2):
        goal.checkpoint(state["slug"], f"turn {i}", "next", f"h{i}", "fail")
    assert goal.budget_exhausted(goal.load(state["slug"])) is True


def test_complete_requires_passing_gate():
    """Spec 5.5: budget exhaustion is never reported as success."""
    state = _make()
    goal.checkpoint(state["slug"], "tried", "retry", "h1", "fail", "2 failed")
    with pytest.raises(goal.GateNotPassed):
        goal.complete(state["slug"], "2 failed")


def test_complete_succeeds_after_passing_gate():
    state = _make()
    goal.checkpoint(state["slug"], "fixed it", "none", "h1", "pass", "ok")
    done = goal.complete(state["slug"], "ok")
    assert done["status"] == "complete"
    assert done["gate_evidence"] == "ok"


def test_load_missing_raises():
    with pytest.raises(goal.GoalStateError):
        goal.load("no-such-goal")


def test_load_malformed_raises(isolated_goals):
    """Spec 6: never silently continue under a guessed goal."""
    (isolated_goals / "broken.json").write_text("{not json")
    with pytest.raises(goal.GoalStateError):
        goal.load("broken")


def test_gate_output_is_bounded():
    state = _make()
    goal.checkpoint(state["slug"], "x", "y", "h", "fail", "E" * 10000)
    stored = goal.load(state["slug"])["checkpoints"][0]["gate_output"]
    assert len(stored) <= goal.MAX_GATE_OUTPUT + 32
    assert "truncated" in stored
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_goal.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'goal'`

- [ ] **Step 3: Write the implementation**

Create `.claude/harness/bin/goal.py`:

```python
"""Goal state for horizon (spec §5.1, §5.5).

Checkpointing after every increment is the mechanism behind "long
horizon" — it is what lets work survive context loss, session restart, or
a /loop firing. Nothing here trusts in-memory state.
"""

import json
import re
from datetime import datetime, timezone

import paths

MAX_GATE_OUTPUT = 4000
SLUG_MAX = 60


class GateRequired(Exception):
    """Raised when a goal is created without a termination criterion."""


class GoalStateError(Exception):
    """Raised when goal state is missing or unreadable. Never guess."""


class GateNotPassed(Exception):
    """Raised when completion is attempted without a passing gate."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return s[:SLUG_MAX].strip("-")


def _path(slug: str):
    return paths.goals_dir() / f"{slug}.json"


def _save(state: dict) -> dict:
    _path(state["slug"]).write_text(json.dumps(state, indent=2) + "\n")
    return state


def create(goal, cwd, gate_kind, gate_value,
           max_turns=20, max_minutes=None, watch_paths=None) -> dict:
    if not gate_value or not str(gate_value).strip():
        raise GateRequired(
            "horizon requires a gate. A loop with no termination criterion "
            "is an infinite loop. Pass --gate '<command>' or a coverage criterion."
        )
    if gate_kind not in ("command", "coverage"):
        raise GateRequired(f"gate_kind must be 'command' or 'coverage', got {gate_kind!r}")

    state = {
        "slug": slugify(goal),
        "goal": goal,
        "cwd": str(cwd),
        "gate": {"kind": gate_kind, "value": gate_value},
        "watch_paths": [str(p) for p in (watch_paths or [cwd])],
        "budget": {"max_turns": max_turns, "max_minutes": max_minutes},
        "used": {"turns": 0, "started": _now()},
        "status": "active",
        "subagents": [],
        "checkpoints": [],
    }
    return _save(state)


def load(slug: str) -> dict:
    p = _path(slug)
    if not p.exists():
        raise GoalStateError(f"no goal state at {p}")
    try:
        state = json.loads(p.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise GoalStateError(f"goal state at {p} is unreadable: {e}") from e
    if "slug" not in state or "gate" not in state:
        raise GoalStateError(f"goal state at {p} is missing required fields")
    return state


def checkpoint(slug, did, next_action, tree_hash_value,
               gate_result, gate_output="") -> dict:
    if gate_result not in ("pass", "fail", "skipped"):
        raise ValueError(f"gate_result must be pass|fail|skipped, got {gate_result!r}")
    state = load(slug)
    if len(gate_output) > MAX_GATE_OUTPUT:
        gate_output = gate_output[:MAX_GATE_OUTPUT] + "\n...[truncated]"
    state["checkpoints"].append({
        "turn": state["used"]["turns"] + 1,
        "at": _now(),
        "did": did,
        "next": next_action,
        "tree_hash": tree_hash_value,
        "gate": gate_result,
        "gate_output": gate_output,
    })
    state["used"]["turns"] += 1
    return _save(state)


def budget_exhausted(state: dict) -> bool:
    max_turns = state["budget"].get("max_turns")
    if max_turns is not None and state["used"]["turns"] >= max_turns:
        return True
    max_minutes = state["budget"].get("max_minutes")
    if max_minutes is not None:
        started = datetime.fromisoformat(state["used"]["started"])
        elapsed = (datetime.now(timezone.utc) - started).total_seconds() / 60
        if elapsed >= max_minutes:
            return True
    return False


def complete(slug: str, gate_output: str) -> dict:
    state = load(slug)
    last = state["checkpoints"][-1] if state["checkpoints"] else None
    if last is None or last["gate"] != "pass":
        raise GateNotPassed(
            "cannot complete: the last gate did not pass. Budget exhaustion "
            "is reported as a partial result, never as success."
        )
    state["status"] = "complete"
    state["gate_evidence"] = gate_output
    state["completed_at"] = _now()
    return _save(state)


def abandon(slug: str, reason: str) -> dict:
    state = load(slug)
    state["status"] = "abandoned"
    state["abandoned_reason"] = reason
    state["abandoned_at"] = _now()
    return _save(state)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_goal.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
cd "/Users/deeps/Documents/NEW STUFF/fun "
git add .claude/harness/bin/goal.py tests/test_goal.py
git commit -m "feat(harness): goal state with gate and budget contracts (spec 5.1, 5.5)

Refuses creation without a gate; refuses completion without a passing
gate; raises rather than guessing on malformed state."
```

---

### Task 3: Refine changelog and rollback

Implements §4.2 step 6 and the §6 empty-changelog row. Delivers T7.

**Files:**
- Create: `.claude/harness/bin/refine_log.py`
- Test: `tests/test_refine_log.py`

**Interfaces:**
- Consumes: `paths.changelog()`, `paths.undo_dir()` from Task 1.
- Produces:
  - `refine_log.record(layer, target_path, rationale, evidence) -> dict` — snapshots current content **before** the edit; call it first, then write
  - `refine_log.commit_entry(entry_id) -> None` — appends the CHANGELOG line once the edit succeeded
  - `refine_log.undo() -> dict` — reverts the most recent un-reverted entry
  - Exception: `NothingToUndo`

- [ ] **Step 1: Write the failing test**

Create `tests/test_refine_log.py`:

```python
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / ".claude" / "harness" / "bin"
sys.path.insert(0, str(BIN))

import refine_log  # noqa: E402


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    root = tmp_path / "harness"
    (root / "undo").mkdir(parents=True)
    monkeypatch.setattr(refine_log.paths, "undo_dir", lambda: root / "undo")
    monkeypatch.setattr(refine_log.paths, "changelog", lambda: root / "CHANGELOG.md")
    return root


def test_undo_removes_a_created_file(tmp_path, isolated):
    """T7: a created memory file is removed and the entry retired."""
    target = tmp_path / "memory" / "no-api-keys.md"
    target.parent.mkdir()

    entry = refine_log.record("M", target, "user prefers no-key solutions", "turn 12")
    target.write_text("---\nname: no-api-keys\n---\nbody")
    refine_log.commit_entry(entry["id"])

    assert target.exists()
    refine_log.undo()
    assert not target.exists()
    assert "REVERTED" in refine_log.paths.changelog().read_text()


def test_undo_restores_previous_content(tmp_path, isolated):
    target = tmp_path / "CLAUDE.md"
    target.write_text("original\n")

    entry = refine_log.record("rho", target, "add routing rule", "turn 4")
    target.write_text("original\nnew rule\n")
    refine_log.commit_entry(entry["id"])

    refine_log.undo()
    assert target.read_text() == "original\n"


def test_changelog_line_format(tmp_path, isolated):
    target = tmp_path / "x.md"
    entry = refine_log.record("G", target, "promote retry helper", "turns 3,7,11")
    target.write_text("skill")
    refine_log.commit_entry(entry["id"])

    line = refine_log.paths.changelog().read_text().strip().splitlines()[-1]
    parts = [p.strip() for p in line.split("|")]
    assert parts[1] == "G"
    assert parts[3] == "promote retry helper"
    assert parts[4] == "turns 3,7,11"


def test_undo_with_empty_changelog_raises():
    with pytest.raises(refine_log.NothingToUndo):
        refine_log.undo()


def test_undo_twice_walks_back_two_entries(tmp_path, isolated):
    a = tmp_path / "a.md"
    b = tmp_path / "b.md"
    for target in (a, b):
        entry = refine_log.record("M", target, f"add {target.name}", "turn 1")
        target.write_text("content")
        refine_log.commit_entry(entry["id"])

    refine_log.undo()
    assert not b.exists() and a.exists()
    refine_log.undo()
    assert not a.exists()


def test_uncommitted_entry_is_not_undone(tmp_path, isolated):
    """A recorded-but-never-committed entry means the edit failed; undo
    must not act on it."""
    target = tmp_path / "never.md"
    refine_log.record("M", target, "aborted edit", "turn 2")
    with pytest.raises(refine_log.NothingToUndo):
        refine_log.undo()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_refine_log.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'refine_log'`

- [ ] **Step 3: Write the implementation**

Create `.claude/harness/bin/refine_log.py`:

```python
"""Changelog and rollback for refine (spec §4.2 step 6).

Two-phase by design: record() snapshots the target before the edit,
commit_entry() appends the CHANGELOG line only after the edit succeeded.
An entry that was recorded but never committed represents a failed edit
and is invisible to undo().
"""

import json
from datetime import datetime, timezone

import paths


class NothingToUndo(Exception):
    """Raised when undo() finds no un-reverted committed entry."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _entries() -> list[dict]:
    out = []
    for p in sorted(paths.undo_dir().glob("*.json")):
        try:
            out.append(json.loads(p.read_text()))
        except (json.JSONDecodeError, OSError):
            continue
    return out


def _write(entry: dict) -> None:
    (paths.undo_dir() / f"{entry['id']}.json").write_text(
        json.dumps(entry, indent=2) + "\n"
    )


def record(layer: str, target_path, rationale: str, evidence: str) -> dict:
    """Snapshot the target before an edit. Call this BEFORE writing."""
    target = str(target_path)
    existing = None
    try:
        with open(target, encoding="utf-8") as fh:
            existing = fh.read()
    except FileNotFoundError:
        existing = None

    seq = len(list(paths.undo_dir().glob("*.json"))) + 1
    entry = {
        "id": f"{seq:04d}",
        "at": _now(),
        "layer": layer,
        "target": target,
        "rationale": rationale,
        "evidence": evidence,
        "previous_content": existing,
        "created_file": existing is None,
        "committed": False,
        "reverted": False,
    }
    _write(entry)
    return entry


def commit_entry(entry_id: str) -> None:
    """Mark the edit as applied and append its CHANGELOG line."""
    path = paths.undo_dir() / f"{entry_id}.json"
    entry = json.loads(path.read_text())
    entry["committed"] = True
    _write(entry)

    log = paths.changelog()
    if not log.exists():
        log.write_text("# Harness changelog\n\n")
    with open(log, "a", encoding="utf-8") as fh:
        fh.write(
            f"{entry['at'][:10]} | {entry['layer']} | {entry['target']} | "
            f"{entry['rationale']} | {entry['evidence']}\n"
        )


def undo() -> dict:
    """Revert the most recent committed, un-reverted entry."""
    candidates = [e for e in _entries() if e["committed"] and not e["reverted"]]
    if not candidates:
        raise NothingToUndo("no committed harness edits left to revert")

    entry = candidates[-1]
    target = entry["target"]
    if entry["created_file"]:
        try:
            import os
            os.remove(target)
        except FileNotFoundError:
            pass
    else:
        with open(target, "w", encoding="utf-8") as fh:
            fh.write(entry["previous_content"])

    entry["reverted"] = True
    entry["reverted_at"] = _now()
    _write(entry)

    with open(paths.changelog(), "a", encoding="utf-8") as fh:
        fh.write(f"{entry['reverted_at'][:10]} | REVERTED | {target} | "
                 f"undo of entry {entry['id']} | -\n")
    return entry
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_refine_log.py -v`
Expected: 6 passed

- [ ] **Step 5: Run the whole suite**

Run: `uv run --with pytest pytest tests/ -v`
Expected: 43 passed (35 existing + 8 new)

- [ ] **Step 6: Commit**

```bash
cd "/Users/deeps/Documents/NEW STUFF/fun "
git add .claude/harness/bin/refine_log.py tests/test_refine_log.py
git commit -m "feat(harness): changelog and rollback for refine (spec 4.2, T7)

Two-phase record/commit so a failed edit leaves no undoable entry."
```

---

### Task 4: Gate execution

Implements §5.2 step 4, §5.2.1, and the §6 unrunnable-gate row. Delivers T4, T5, T6 as automated tests.

**Files:**
- Create: `.claude/harness/bin/gate.py`
- Test: `tests/test_gate.py`

**Interfaces:**
- Consumes: `tree_hash.changed/compute` (Task 1), `goal.create/load/checkpoint` (Task 2).
- Produces:
  - `gate.last_hash(state: dict) -> str | None`
  - `gate.run_gate(state: dict, timeout: int = 900) -> tuple[str, str]` — returns `(result, output)` where result is `"pass" | "fail" | "skipped"`
  - `gate.GateUnrunnable` — raised when the command cannot execute at all
  - `gate.MAX_OUTPUT: int = 4000`

- [ ] **Step 1: Write the failing test**

Create `tests/test_gate.py`:

```python
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
        result, output = gate.run_gate(current)
        goal.checkpoint(
            state["slug"], f"attempt {i}", "retry",
            tree_hash.compute(repo, [str(repo)]), result, output,
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --with pytest pytest tests/test_gate.py -v`
Expected: collection error — `ModuleNotFoundError: No module named 'gate'`

- [ ] **Step 3: Write the implementation**

Create `.claude/harness/bin/gate.py`:

```python
"""Gate execution for horizon (spec §5.2 step 4, §5.2.1, §6).

Separated from the skill markdown so the loop's decision logic is unit
tested once rather than re-derived in prose on every run.
"""

import subprocess

import tree_hash

MAX_OUTPUT = 4000
DEFAULT_TIMEOUT = 900
SHELL_NOT_FOUND = 127


class GateUnrunnable(Exception):
    """The gate command could not execute at all.

    Distinct from a failing gate. An unrunnable gate is a broken goal — a
    typo, a missing tool — and must stop the loop. A failing gate is work
    still to do and must continue it. Conflating them spends the entire
    turn budget re-running a command that was never going to run.
    """


def last_hash(state: dict) -> str | None:
    """The tree_hash recorded by the most recent checkpoint, if any."""
    checkpoints = state.get("checkpoints") or []
    return checkpoints[-1]["tree_hash"] if checkpoints else None


def run_gate(state: dict, timeout: int = DEFAULT_TIMEOUT) -> tuple[str, str]:
    """Run the goal's gate. Returns (result, output).

    result is "pass", "fail", or "skipped". Skipping happens only when the
    tree is provably unchanged since the last checkpoint — see tree_hash,
    where every uncertain path reports changed.
    """
    if state["gate"]["kind"] != "command":
        raise ValueError(
            "run_gate handles command gates only; a coverage gate is judged "
            "by a fresh subagent that did not do the work"
        )

    if not tree_hash.changed(state["cwd"], state.get("watch_paths"), last_hash(state)):
        return "skipped", ""

    command = state["gate"]["value"]
    try:
        proc = subprocess.run(
            command, shell=True, cwd=state["cwd"],
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        raise GateUnrunnable(f"gate timed out after {timeout}s: {command}") from e
    except OSError as e:
        raise GateUnrunnable(f"gate could not execute: {command}: {e}") from e

    if proc.returncode == SHELL_NOT_FOUND:
        raise GateUnrunnable(
            f"gate command not found (exit 127): {command}. "
            f"stderr: {proc.stderr.strip()[:200]}"
        )

    output = (proc.stdout + proc.stderr)[-MAX_OUTPUT:]
    return ("pass" if proc.returncode == 0 else "fail"), output
```

Note on exit 127: POSIX shells reserve it for "command not found", so it
is the reliable signal here. A gate that legitimately exits 127 would be
misread as unrunnable — an acceptable trade, since stopping on a
suspicious gate is the conservative direction.

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run --with pytest pytest tests/test_gate.py -v`
Expected: 8 passed

- [ ] **Step 5: Run the whole suite**

Run: `uv run --with pytest pytest tests/ -v`
Expected: 43 passed (35 existing + 8 new)

- [ ] **Step 6: Commit**

```bash
cd "/Users/deeps/Documents/NEW STUFF/fun "
git add .claude/harness/bin/gate.py tests/test_gate.py
git commit -m "feat(harness): gate execution with skip and unrunnable handling

Automates T4/T5/T6. Distinguishes an unrunnable gate (broken goal, stop)
from a failing gate (work to do, continue)."
```

---

### Task 5: The `horizon` skill

Implements §5 in full. Delivers acceptance test T10.

**Files:**
- Create: `.claude/skills/horizon/SKILL.md`
- Create: `docs/superpowers/acceptance.md`

**Interfaces:**
- Consumes: `goal.create/load/checkpoint/budget_exhausted/complete` (Task 2), `tree_hash.compute/changed` (Task 1).
- Produces: the `/horizon` skill trigger. No code interface.

- [ ] **Step 1: Write the skill**

Create `.claude/skills/horizon/SKILL.md`:

```markdown
---
name: horizon
description: Use when a task needs to survive across turns or sessions - long research sweeps, multi-step refactors, "keep working until X passes". Establishes a goal with a verification gate, checkpoints after every increment, and continues under a bounded budget until the gate passes.
---

# Horizon: goal, gate, bounded continuation

Long-horizon work fails for one reason: state lives in context, and
context ends. This skill moves state to disk after every increment.

## Setup

The harness modules live at `.claude/harness/bin/`. Every invocation is a
`python3 -c` one-liner from the project root. Quote the path — the
project directory name ends in a space.

```bash
cd "/Users/deeps/Documents/NEW STUFF/fun " && python3 -c "
import sys, json; sys.path.insert(0, '.claude/harness/bin')
import goal
print(json.dumps(goal.load('<slug>'), indent=2))
"
```

## Step 1 — Establish the goal

Never start without a gate. A loop with no termination criterion is an
infinite loop, and `goal.create` will refuse.

Gate kinds:
- `command` — a shell command that must exit 0 (`npm test`, `pytest -q`).
- `coverage` — a written completeness criterion for research goals, e.g.
  "every claim carries a source; no sub-question left unanswered".

If the user gave no gate, propose one and confirm before starting.

```python
state = goal.create(
    goal="<the objective>",
    cwd="<absolute path>",
    gate_kind="command",
    gate_value="npm run check",
    max_turns=20,
    watch_paths=["<output dir>"],   # narrow this for research goals
)
```

## Step 2 — Work one increment

Do the smallest unit of real work that moves toward the goal. Not the
whole thing. The loop is what makes progress, not any single turn.

## Step 3 — Checkpoint

Immediately after the increment, before running the gate:

```python
h = tree_hash.compute(state["cwd"], state["watch_paths"])
```

Record what you did and what comes next in plain language. `next` is
read by a future session that has none of your context — write it for a
stranger.

## Step 4 — Run the gate

Skip the run only if `tree_hash.changed(...)` is False against the
previous checkpoint's hash. **The two failure modes are not symmetric:** a
false skip is a correctness bug, a false run costs seconds. On any doubt,
run it.

```python
import gate

try:
    result, output = gate.run_gate(state)   # "pass" | "fail" | "skipped"
except gate.GateUnrunnable as e:
    # A broken goal, not work still to do. Stop and report the exact cause.
    goal.abandon(state["slug"], f"gate unrunnable: {e}")
    raise
```

`run_gate` owns the skip decision, the subprocess, output bounding, and
the unrunnable-vs-failing distinction — all unit tested in
`tests/test_gate.py`. Do not reimplement any of it inline.

For a `coverage` gate, dispatch a **fresh subagent that did not do the
work** to judge it. Self-grading is the weakest link in any autonomous
loop, and an agent that graded its own research will pass itself.

Then: `goal.checkpoint(slug, did, next_action, h, result, output)`

## Step 5 — Continue or stop

- Gate **pass** → `goal.complete(slug, output)` and report with the gate
  output as evidence.
- Gate **fail** and budget remains → feed the bounded output back as the
  next increment's input. Return to Step 2.
- `goal.budget_exhausted(state)` → **stop**. Report honestly: what is
  done, what is not, what the next action would be. Never describe an
  exhausted budget as success. `goal.complete` will refuse anyway.

## Subagents

Store role and briefing, never a durable handle:

```python
state["subagents"].append({
    "role": "auth-reviewer", "name": "<agent name>",
    "brief": "<full task description, sufficient to respawn cold>",
    "last_seen_turn": state["used"]["turns"], "session": "<session id>",
})
```

- **Within a session:** address a live agent by name via `SendMessage`
  instead of respawning it cold.
- **After a restart:** treat every entry as dead. `ListAgents` is scoped
  to in-process subagents, so persisted handles do not resolve. Respawn
  from `brief` plus the relevant checkpoints.

Never attempt `SendMessage` against an entry whose `last_seen_turn`
predates the current session.

## Relationship to other skills

This is the outer loop. It re-enters `superpowers:executing-plans` and
`superpowers:verification-before-completion` rather than replacing them,
and defers to `superpowers:brainstorming` / `writing-plans` for deciding
*what* to build.

## Resuming

`python3 -c "...; import goal; print(goal.load('<slug>'))"` restores the
goal, the budget spent, and the last `next` action. Read the last three
checkpoints before acting — they are the context you no longer have.
```

- [ ] **Step 2: Write the acceptance procedures**

Create `docs/superpowers/acceptance.md`:

```markdown
# Acceptance tests

Unit-tested behavior lives in `tests/` and runs with
`uv run --with pytest pytest tests/ -v`. **T4, T5, T6, T7, and T8 are
automated there** — see `tests/test_gate.py`, `tests/test_goal.py`,
`tests/test_refine_log.py`, `tests/test_tree_hash.py`.

The tests below exercise skill *behavior*, which cannot be asserted in
pytest — they are run by invoking the skill and inspecting artifacts.
Each states its pass criterion.

## T1 — refine routing

Run `/refine` against a session whose known lesson is *"user prefers
solutions requiring no additional API keys"*.

**Pass:** proposed, and routed to **memory (M)** — not CLAUDE.md.

## T2 — refine dedupe

Run `/refine` twice with no activity between.

**Pass:** the second run proposes nothing rather than duplicating T1.

## T3 — guardrail G3

Run `/refine` on a trajectory in which tests failed.

**Pass:** never proposes weakening, skipping, or disabling tests.

## T9 — memory contract conformance

Inspect the file written by T1.

**Pass:** kebab-case filename matching its `name`; frontmatter carries
`name`, `description`, and a `metadata.type` from
`{user, feedback, project, reference}`; exactly one new line in
`MEMORY.md`; no numbered or category-prefixed filename.

## T10 — subagent restart safety

Populate `subagents` in a goal, then resume it in a fresh session.

**Pass:** entries are treated as dead and respawned from `brief` plus
checkpoints; no `SendMessage` is attempted against a stale handle.
```

- [ ] **Step 3: Verify the skill loads**

Run: `ls -1 .claude/skills/horizon/SKILL.md && head -5 .claude/skills/horizon/SKILL.md`
Expected: file exists, frontmatter shows `name: horizon`.

Restart Claude Code in this directory and confirm `horizon` appears in the available-skills list.

- [ ] **Step 4: Verify the skill calls gate.run_gate rather than reimplementing it**

Run: `grep -n "subprocess" .claude/skills/horizon/SKILL.md || echo "OK: no inline subprocess"`
Expected: `OK: no inline subprocess`. The loop's decision logic lives in
`gate.py` where it is tested; the skill must call it.

- [ ] **Step 5: Run acceptance test T10**

Follow `docs/superpowers/acceptance.md` T10: populate `subagents` in a
goal, resume in a fresh session.
Expected: entries treated as dead and respawned from `brief` plus
checkpoints; no `SendMessage` against a stale handle.

- [ ] **Step 6: Commit**

```bash
cd "/Users/deeps/Documents/NEW STUFF/fun "
git add .claude/skills/horizon/SKILL.md docs/superpowers/acceptance.md
git commit -m "feat(horizon): goal/gate/continuation skill (spec 5)

Checkpoints to disk after every increment; refuses to start without a
gate; refuses to report exhausted budget as success."
```

---

### Task 6: The `refine` skill

Implements §4 in full. Delivers acceptance tests T1, T2, T3, T9.

**Files:**
- Create: `.claude/skills/refine/SKILL.md`
- Create: `.claude/harness/CHANGELOG.md`

**Interfaces:**
- Consumes: `refine_log.record/commit_entry/undo` (Task 3).
- Produces: the `/refine` skill trigger. No code interface.

- [ ] **Step 1: Seed the changelog**

Create `.claude/harness/CHANGELOG.md`:

```markdown
# Harness changelog

Every edit `refine` applies, newest last. Format:

`YYYY-MM-DD | layer | target | rationale | evidence`

Layers: `rho` (CLAUDE.md), `G` (skills), `K` (subagents), `M` (memory).
`REVERTED` lines are written by `/refine --undo`.
```

- [ ] **Step 2: Write the skill**

Create `.claude/skills/refine/SKILL.md`:

```markdown
---
name: refine
description: Use after a substantial task, or when the user types /refine - reads what actually happened this session and proposes the smallest edit to the harness layer (memory, skills, subagents, CLAUDE.md) that would make the next session go better. Proposes diffs; never writes without approval.
---

# Refine: the continual harness

The harness is `H = (rho, G, K, M)` — prompt, skills, subagents, memory.
Every one is editable. This skill closes the loop that otherwise never
closes: nothing in a normal session ever writes back to it.

## Step 1 — Harvest turning points

Trajectories are mostly noise: file reads, tool output, exploration that
led nowhere. Scan for **turning points only**:

- A direct user correction ("no, don't do X", "use Y instead").
- **A command that failed, then succeeded after a fix.** The highest-signal
  class — a reproducible gotcha whose fix is already proven.
- A procedure re-derived or repeated for the third time.
- A research or exploration shape run repeatedly with the same structure.
- A durable fact about the user or project not derivable from the repo.

If a candidate cannot be anchored to one of those moments, it is not a
candidate. This is what stops refine inventing plausible-sounding patterns
that reflect nothing.

## Step 2 — Route to the narrowest layer

| Evidence | Layer | Target |
|---|---|---|
| Durable fact about user or project | M | project memory dir |
| Command failed then fixed | M | project memory dir |
| Procedure repeated 3x | G | `.claude/skills/<name>/SKILL.md` |
| Repeated exploration shape | K | `.claude/agents/<name>.md` |
| Correction that must apply *everywhere* | rho | `./CLAUDE.md` |

**Narrowest layer wins.** CLAUDE.md is loaded into every session, so it is
the most expensive real estate in the system — last resort, never the
default. A fact belongs in memory unless it must fire unconditionally.

## Step 3 — Dedupe

Search the target layer for an entry already covering this. Update it
rather than creating a near-duplicate. Two memories saying almost the
same thing are worse than one, because retrieval surfaces the weaker one
half the time.

## Step 4 — Rank and cap

**At most 3 proposals per invocation.** If you found more, rank by how
often the situation will recur and drop the tail. An unbounded
self-editor bloats the harness until it is worse than no harness.

## Step 5 — Propose, do not write

For each proposal show: the target file, the exact text to be written,
and the trajectory moment justifying it. Then stop and wait.

## Step 6 — On approval, write and log

For each approved edit, in order:

```python
import sys; sys.path.insert(0, '.claude/harness/bin')
import refine_log
entry = refine_log.record(layer, target, rationale, evidence)  # BEFORE the edit
# ... apply the edit ...
refine_log.commit_entry(entry["id"])                            # AFTER it succeeds
```

`record` first, always. It snapshots the previous content, which is the
only thing that makes `--undo` possible.

## Memory write contract

Do not invent a format. The existing one is what recall actually reads:

- **Location:** the per-project memory directory.
- **Filename:** the kebab-case `name` slug. Never numbered or
  category-prefixed — retrieval keys off `description`, so `USER_PREF_001`
  adds nothing and breaks dedupe by name.
- **Frontmatter:** `name`, `description`, and `metadata.type` from
  `{user, feedback, project, reference}`.
- **Body:** for `feedback` and `project`, follow with `**Why:**` and
  `**How to apply:**`. Link related memories with `[[slug]]`.
- **Index:** append one line to `MEMORY.md` — `- [Title](file.md) — hook`.
  Never put content in the index.

## Guardrails

- **G1 — Evidence required.** Every proposal cites a concrete moment. No
  speculative or anticipatory edits.
- **G2 — Cap of 3.** Forces ranking.
- **G3 — No self-exemption.** Never edit this skill. Never propose
  anything that weakens verification, testing, or approval requirements.
  An agent that learns "skip the tests" is optimizing the harness against
  its user. Prime Agent's own Factorio run did exactly this: the same
  refinement loop that built legitimate skills turned to building
  efficient cheating skills once cheating scored better.
- **G4 — Approval gate.** No write without explicit approval.

## Anti-patterns

- Recording what the repo already states (structure, git history).
- Recording conversation-local trivia with no future bearing.
- Writing a skill where a CLAUDE.md line suffices, or a CLAUDE.md line
  where a memory suffices.
- Proposing something when the session taught nothing. Silence is a valid
  result — say so and stop.

## `/refine --undo`

```python
import sys; sys.path.insert(0, '.claude/harness/bin')
import refine_log; refine_log.undo()
```

Reverts the most recent committed edit and appends a `REVERTED` line.
Raises `NothingToUndo` on an empty changelog.
```

- [ ] **Step 3: Verify the skill loads**

Run: `head -5 .claude/skills/refine/SKILL.md`
Expected: frontmatter shows `name: refine`.

Restart Claude Code in this directory and confirm `refine` appears in the
available-skills list.

- [ ] **Step 4: Run acceptance tests T1, T2, T3, T9**

Follow `docs/superpowers/acceptance.md`. T1 is run against the design
session itself, whose known lesson is *"user prefers solutions requiring
no additional API keys."*
Expected: T1 routes to memory; T2 is silent; T3 never proposes weakening
tests; T9 conforms to the memory contract.

- [ ] **Step 5: Verify rollback end-to-end**

Run `/refine --undo` after T1, then:
Run: `uv run --with pytest pytest tests/ -v && git status --short`
Expected: 43 passed; the T1 memory file is gone; `CHANGELOG.md` carries a
`REVERTED` line.

- [ ] **Step 6: Commit**

```bash
cd "/Users/deeps/Documents/NEW STUFF/fun "
git add .claude/skills/refine/SKILL.md .claude/harness/CHANGELOG.md
git commit -m "feat(refine): continual harness skill (spec 4)

Turning-point harvesting, narrowest-layer routing, cap of 3, approval
gate, and binding to the existing memory contract."
```

---

### Task 7: Promotion readiness

Implements §8. Produces the evidence needed to decide whether to promote to user scope.

**Files:**
- Create: `docs/superpowers/results.md`
- Modify: `.claude/skills/refine/SKILL.md` (add the `--global` flag section)

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: a pass/fail record for T1–T10.

- [ ] **Step 1: Run the full unit suite**

Run: `uv run --with pytest pytest tests/ -v`
Expected: 32 passed.

- [ ] **Step 2: Record every acceptance result**

Create `docs/superpowers/results.md` with one row per test: id, date run,
pass/fail, and the observed artifact (file path, checkpoint, or quoted
output). A test with no recorded artifact counts as not run.

- [ ] **Step 3: Add the `--global` flag to refine**

Append to `.claude/skills/refine/SKILL.md`:

```markdown
## Scope

Default targets are project-local: `./CLAUDE.md`, `.claude/skills/`,
`.claude/agents/`, and the project memory dir.

`/refine --global` retargets to `~/.claude/CLAUDE.md`, `~/.claude/skills/`,
and `~/.claude/agents/`. Use it only for lessons that genuinely apply to
every project — a project-specific gotcha written globally is noise in
every future session.
```

- [ ] **Step 4: Verify no writes escaped the project**

Check for the specific artifacts promotion would create, not for general
mtime activity — Claude Code writes to `~/.claude/` continuously (logs,
history, daemon state), so an mtime sweep returns noise and proves
nothing.

```bash
test ! -e ~/.claude/skills/refine   && echo "OK: no global refine skill"
test ! -e ~/.claude/skills/horizon  && echo "OK: no global horizon skill"
test ! -e ~/.claude/harness         && echo "OK: no global harness dir"
grep -c "harness" ~/.claude/CLAUDE.md 2>/dev/null || echo "OK: global CLAUDE.md untouched"
```

Expected: all four OK lines. Nothing outside this project should have
been modified before promotion is approved.

- [ ] **Step 5: Commit**

```bash
cd "/Users/deeps/Documents/NEW STUFF/fun "
git add docs/superpowers/results.md .claude/skills/refine/SKILL.md
git commit -m "chore(harness): record T1-T10 results and add --global scope (spec 8)"
```

- [ ] **Step 6: Report and stop**

Present the results table. **Do not promote to `~/.claude/` without
explicit approval** — promotion changes behavior in every project on the
machine, which is exactly the blast radius the project-scoped trial was
designed to avoid.

---

## Spec Coverage

| Spec section | Task |
|---|---|
| §3 Architecture | 1, 4, 5 |
| §4.1 Signal routing | 5 |
| §4.2 Procedure | 5 |
| §4.2.1 Memory contract | 5, acceptance T9 |
| §4.3 Guardrails G1–G4 | 5, acceptance T3 |
| §4.4 Anti-patterns | 5 |
| §5.1 State schema | 2 |
| §5.2 The loop | 4, 5 |
| §5.2.1 tree_hash / skip rule | 1, 4 (T5) |
| §5.3 Gate kinds | 2 (validation), 4 (execution), 5 (usage) |
| §5.4 Fan-out / subagents | 5, acceptance T10 |
| §5.5 Completion contract | 2 (T6) |
| §6 Error handling | 1, 2, 3, 4 |
| §7 Tests T1–T10 | 1 (T8), 2 (T6), 3 (T7), 4 (T4/T5), 5 (T10), 6 (T1/T2/T3/T9) |
| §8 Promotion | 7 |
