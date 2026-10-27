# Horizon Stage A Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Horizon resume cleanly after a cut-off session and finish as soon as recorded evidence says the work is done. This plan fixes failures 1, 2 and 4 of the spec.

**Architecture:**
- Gate runs outside increments become recorded **observations**, kept in a separate `observations` list so state-mode checkpoint invariants still hold.
- Completion is a rule over recorded evidence keyed by tree hash (`harness/evidence.py`), not over `checkpoints[-1]`.
- State-mode attempts get **leases**, kept in a side file so renewals don't bump the execution revision. Once a lease is stale, a new `step-reclaim` closes the attempt; the existing status check fences the dead worker.
- Claude Code hooks (`harness/hooks.py`) reclaim, renew and finalize at zero model calls.

**Tech Stack:** Python 3.10+ standard library only; pytest via `uv run --with pytest pytest -q`.

**Spec:** `docs/superpowers/specs/2026-10-27-horizon-redesign-design.md` (Stage A). Evidence: `docs/research/2026-10-27-horizon-redesign-evidence.md`.

## Global Constraints

- Runtime code uses only the Python standard library; Python 3.10+, POSIX.
- Edit `.py` files with the ast-editor MCP tools. New files may be written whole.
- Every bug fix or behavior change starts with a failing test.
- The existing suite (`uv run --with pytest pytest -q`, 496 tests at branch start) stays green. Tests that assert the *old* behaviors this plan removes are updated, each named in the task that removes the behavior:
  - "skipped blocks completion";
  - "review must match the last checkpoint's turn".
- Hooks never call a model, never block a session (always exit 0), and only write Horizon's own state and `.claude/settings.json`.
- Old goal files without the new fields must still load.
- The reclaim window for another session is **300 s** of silence, and the lease term is **600 s** (gate `DEFAULT_TIMEOUT` 480 + 120).
- Commit after each task, with a plain message and no attribution lines.

## Rulings against the spec (made while planning, from reading the code)

1. **Observations live in `state["observations"]`, not `state["checkpoints"]`.**
   - Why: `_validate_state_execution` requires checkpoint turns to match terminal attempts one-to-one.
   - What stays the same: the record shape (`gate`, `tree_hash`, `gate_spec`, `gate_output`, plus `kind`/`role`).
2. **No `epoch` field.**
   - Reclaim moves the attempt to `recovered`. `step-finish` and `step-attach` already refuse any attempt that isn't `attached`/`reserved` and active, so a dead worker's late finish is fenced by that status check.
   - The spec's purpose holds; the extra counter is not needed.
3. **Leases live in `<goals_dir>/<slug>.leases.json`, not in the attempt.**
   - Why: `task_state._validate_attempt` requires an exact key set, and renewals on every tool call would bump `execution.revision` and break the coordinator's `--expected-revision`.
4. **The SessionStart hook does not run the gate.**
   - Hooks have short timeouts, and a gate can take minutes. `close` observes the current tree itself when no pass covers it, so a resumed session still gets a fresh observation before anything completes.
   - The baseline observation runs inside `start`.
5. **No `--request-file`/`--requirements-file` on `start`.** One Bash call can already write the gate files and run `start`. SKILL.md documents that pattern instead.
6. **The dead attempt's diff is not saved as an observation.** The working tree stays as the dead worker left it, and the next `close` or `observe` records its gate result.

## Review Focus

1. **Bad hook input.** A hook gets malformed or empty stdin, a missing goals directory, or a corrupt goal file. Expected: exit 0, no output, no traceback. Pinned in Task 6.
2. **Existing settings.** The project's `.claude/settings.json` already holds the user's own hooks, or isn't valid JSON. Expected: the user's hooks are preserved, a second install adds nothing, and invalid JSON is left untouched with an error in the report. Pinned in Task 6.
3. **Paths with spaces.** The project path contains spaces. Expected: the installed hook command still runs. Pinned in Task 6 (the CLI fixture's project is named `"cli project "`).
4. **Old goals.** A goal file created before this change has no `observations`, `test_manifest` or lease file. Expected: it loads, and `close` observes and then completes. Pinned in Task 3; the leases side is in Task 4.
5. **Corrupt lease file.** The lease file is corrupt or missing for an attached attempt. Expected: treated as "no lease" (reclaimable), never a crash. Pinned in Task 4.

---

### Task 1: Kill-9 probe (does a background worker outlive its session?)

**Files:**
- Create: `docs/research/2026-10-27-kill9-probe.md`

**Interfaces:** none (an experiment; its result decides whether Option B's worktrees are needed later).

- [ ] **Step 1: Run the probe.** In a scratch directory outside any repo:

```bash
mkdir -p /tmp/h-kill9 && cd /tmp/h-kill9 && rm -f beat.txt
claude -p --model claude-haiku-5-5 --permission-mode bypassPermissions \
  "Use the Agent tool with run_in_background true. The agent must run this Bash command and nothing else: for i in \$(seq 1 60); do date +%s >> /tmp/h-kill9/beat.txt; sleep 2; done. After dispatching it, run: sleep 120" &
PARENT=$!
until [ -s beat.txt ]; do sleep 1; done; sleep 6
kill -9 "$PARENT"; BEFORE=$(wc -l < beat.txt); sleep 20; AFTER=$(wc -l < beat.txt)
echo "before=$BEFORE after=$AFTER"; pgrep -fl "h-kill9" || echo "no survivors"
```

Expected: either `after == before` and "no survivors" (the worker dies with the session), or `after > before` (it survives).

- [ ] **Step 2: Record the result.** Write `docs/research/2026-10-27-kill9-probe.md` with:
  - the command;
  - `before`, `after` and the `pgrep` output;
  - one conclusion line: "Workers die with the session: Stage A's lease rules suffice", or "Workers survive: shorten LEASE_TERM_S and plan Option B worktrees".

  Then also kill the process group (`kill -9 -- -$PGID`) and record whether survivors remain. That is what our study harness does.

- [ ] **Step 3: Commit.**

```bash
git add docs/research/2026-10-27-kill9-probe.md
git commit -m "docs: kill-9 probe of background worker survival"
```

---

### Task 2: Observations and the test-file manifest

**Files:**
- Create: `harness/evidence.py`
- Modify: `harness/goal.py` (new `observe`, `OBSERVATION_ROLES`; import `gate`, `evidence`)
- Test: `tests/test_evidence.py` (new)

**Interfaces:**
- Produces:
  - `evidence.test_manifest(cwd: str, watch_paths: list[str]) -> dict[str, str]`, mapping project-relative POSIX path to sha256;
  - `goal.observe(slug: str, role: str, timeout: int = gate.DEFAULT_TIMEOUT) -> dict`, which returns the saved state;
  - `goal.OBSERVATION_ROLES = ("baseline", "resume", "final")`;
  - the observation record `{"kind": "observation", "role", "at", "tree_hash", "gate", "gate_output", "gate_spec"}`;
  - `state["test_manifest"]`, set by the first observation only.

- [ ] **Step 1: Write the failing tests** in `tests/test_evidence.py`:

```python
import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / "harness"
sys.path.insert(0, str(BIN))

import evidence  # noqa: E402
import goal  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_goals(tmp_path, monkeypatch):
    d = tmp_path / "goals"
    d.mkdir()
    (tmp_path / "project").mkdir()
    monkeypatch.setattr(goal.paths, "goals_dir", lambda: d)
    return d


def _project():
    return goal.paths.goals_dir().parent / "project"


def _make(gate_value="true", **kw):
    args = dict(goal="Fix the parser", cwd=str(_project()), gate_kind="command",
                gate_value=gate_value, max_turns=3)
    args.update(kw)
    return goal.create(**args)


def test_manifest_hashes_test_files_only():
    p = _project()
    (p / "tests").mkdir()
    (p / "tests" / "test_a.py").write_text("def test_a(): pass\n")
    (p / "src.py").write_text("x = 1\n")
    (p / "lib_test.go").write_text("package lib\n")
    manifest = evidence.test_manifest(str(p), [str(p)])
    assert set(manifest) == {"tests/test_a.py", "lib_test.go"}
    assert all(len(v) == 64 for v in manifest.values())


def test_manifest_skips_harness_state_and_excluded_dirs():
    p = _project()
    (p / ".claude" / "harness" / "gates" / "g").mkdir(parents=True)
    (p / ".claude" / "harness" / "gates" / "g" / "test_gate.py").write_text("x\n")
    (p / "node_modules" / "m").mkdir(parents=True)
    (p / "node_modules" / "m" / "test_x.py").write_text("x\n")
    assert evidence.test_manifest(str(p), [str(p)]) == {}


def test_observe_records_baseline_without_charging_a_turn():
    state = _make()
    saved = goal.observe(state["slug"], "baseline")
    obs = saved["observations"]
    assert [o["role"] for o in obs] == ["baseline"]
    assert obs[0]["gate"] == "pass" and obs[0]["kind"] == "observation"
    assert obs[0]["gate_spec"] == saved["gate"]
    assert saved["used"]["turns"] == 0 and saved["checkpoints"] == []
    assert saved["test_manifest"] == {}


def test_observe_runs_even_when_tree_unchanged():
    state = _make()
    goal.observe(state["slug"], "baseline")
    saved = goal.observe(state["slug"], "resume")
    assert [o["gate"] for o in saved["observations"]] == ["pass", "pass"]


def test_first_manifest_is_kept():
    p = _project()
    (p / "test_a.py").write_text("one\n")
    state = _make()
    first = goal.observe(state["slug"], "baseline")["test_manifest"]["test_a.py"]
    (p / "test_a.py").write_text("two\n")
    saved = goal.observe(state["slug"], "resume")
    assert saved["test_manifest"]["test_a.py"] == first


def test_observe_in_state_mode_is_allowed_when_idle_and_bumps_revision(make_state_goal):
    state = make_state_goal()
    before = state["execution"]["revision"]
    saved = goal.observe(state["slug"], "baseline")
    assert saved["execution"]["revision"] == before + 1
    assert saved["checkpoints"] == []


def test_observe_refuses_coverage_gates():
    state = _make(gate_kind="coverage", gate_value="The parser rejects empty input")
    with pytest.raises(goal.GateNotPassed):
        goal.observe(state["slug"], "baseline")


def test_observe_rejects_unknown_role():
    state = _make()
    with pytest.raises(ValueError):
        goal.observe(state["slug"], "whenever")
```

Note: `make_state_goal` (in `tests/conftest.py`) creates its own project and goals dir through the real `paths`, so the autouse `isolated_goals` fixture does not interfere. If it does, give that test its own module.

- [ ] **Step 2: Run them to verify they fail.**

Run: `uv run --with pytest pytest -q tests/test_evidence.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'evidence'`.

- [ ] **Step 3: Create `harness/evidence.py`.**

```python
"""Evidence rules for completion.

Which recorded gate result covers the files as they are now, which test files
changed since the baseline, and whether a reviewer is still required. Kept
apart from goal.py so the rules are tested once, without the lifecycle.
"""

import hashlib
import os
import re
from pathlib import Path

import tree_hash

_TEST_PATTERNS = (
    re.compile(r"(^|/)tests?/"),
    re.compile(r"(^|/)test_[^/]*\.py$"),
    re.compile(r"_test\.[^/]+$"),
    re.compile(r"\.(test|spec)\.[^/]+$"),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _is_harness_state(rel: str) -> bool:
    parts = rel.split("/")
    return any(parts[i].lower() in (".claude", ".codex") and parts[i + 1:i + 2] == ["harness"]
               for i in range(len(parts)))


def _is_test(rel: str) -> bool:
    return any(p.search(rel) for p in _TEST_PATTERNS)


def test_manifest(cwd: str, watch_paths: list[str] | None) -> dict[str, str]:
    """sha256 of every test file in the watched paths, keyed by project-relative path."""
    root = Path(cwd).resolve()
    out: dict[str, str] = {}
    for watch in watch_paths or [cwd]:
        base = Path(watch).resolve()
        if base.is_file():
            files = [base]
        else:
            files = []
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in tree_hash.EXCLUDED_DIRS]
                files += [Path(dirpath) / name for name in filenames]
        for path in files:
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = path.as_posix()
            if _is_harness_state(rel) or not _is_test(rel):
                continue
            try:
                out[rel] = _sha(path)
            except OSError:
                continue
    return out
```

- [ ] **Step 4: Add `observe` to `harness/goal.py`.**
  - Add `import gate` and `import evidence` beside the existing imports. Neither module imports `goal`, so there is no cycle.
  - Add the top-level constant `OBSERVATION_ROLES = ("baseline", "resume", "final")` after `JUDGE_ROLE_MARKER`.
  - Add the function after `budget_exhausted`:

```python
def observe(slug: str, role: str, timeout: int = gate.DEFAULT_TIMEOUT) -> dict:
    """Run the command gate now and record what it found, outside any increment.

    Charges no turn and never skips: an unchanged tree is exactly when a
    resumed session needs a fresh reading. The first observation also saves
    the test-file manifest that completion compares against.
    """
    if role not in OBSERVATION_ROLES:
        raise ValueError(f"role must be one of {', '.join(OBSERVATION_ROLES)}")
    before = load(slug)
    if before["status"] != "active":
        raise GoalClosed(f"cannot observe: goal status is {before['status']!r}")
    if before["gate"]["kind"] != "command":
        raise GateNotPassed("Coverage gates are judged by a reviewer; observe runs command gates only.")
    require_idle(before)
    try:
        result, output, digest = gate.run_gate_at(dict(before, checkpoints=[]), timeout=timeout)
    except gate.GateUnrunnable as exc:
        result, output = "fail", str(exc)
        digest = getattr(exc, "tree_hash", gate.UNKNOWN_HASH)
    manifest = evidence.test_manifest(before["cwd"], before.get("watch_paths"))
    with filelock.goal_locked(_path(slug)):
        state = load(slug)
        if state["status"] != "active":
            raise GoalClosed(f"cannot observe: goal status is {state['status']!r}")
        require_idle(state)
        state.setdefault("observations", []).append({
            "kind": "observation", "role": role, "at": _now(), "tree_hash": digest,
            "gate": result, "gate_output": _bounded(output), "gate_spec": dict(state["gate"]),
        })
        state.setdefault("test_manifest", manifest)
        return _persist_mutation_locked(state)
```

- [ ] **Step 5: Run the tests and the suite.**

Run: `uv run --with pytest pytest -q tests/test_evidence.py && uv run --with pytest pytest -q`
Expected: the new tests PASS; the suite is green (496 + 8).

- [ ] **Step 6: Commit.**

```bash
git add harness/evidence.py harness/goal.py tests/test_evidence.py
git commit -m "feat: observe records gate results outside increments, with a baseline test manifest"
```

---

### Task 3: Evidence-based completion and review

**Files:**
- Modify: `harness/evidence.py`. Add:
  - `passing_record`;
  - `changed_baseline_tests`;
  - `added_tests`;
  - `gate_is_strong`;
  - `review_reasons`.
- Modify: `harness/goal.py`:
  - split `_apply_completion` (the coverage branch moves to `_apply_coverage_completion`);
  - rewrite the command branch of `_apply_review`;
  - add `approve_test_change`.
- Test: `tests/test_evidence.py` (extend); update the old-behavior tests in `tests/test_goal.py` and `tests/test_runtime_separation.py`.

**Interfaces:**
- Consumes: `observe` and the observation record from Task 2.
- Produces:
  - `evidence.passing_record(state: dict, observed_hash: str) -> dict | None`;
  - `evidence.changed_baseline_tests(state: dict) -> list[str]`;
  - `evidence.added_tests(state: dict) -> list[str]`;
  - `evidence.gate_is_strong(state: dict) -> bool`;
  - `evidence.review_reasons(state: dict, requirements: dict | None) -> list[str]`;
  - `goal.approve_test_change(slug: str, path: str, reason: str) -> dict`;
  - the review record keeps `turn` but is matched on `tree_hash` + `criterion`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_evidence.py`):

```python
import tree_hash  # noqa: E402


def _digest(state):
    return tree_hash.compute(state["cwd"], state.get("watch_paths"))


def test_pass_from_earlier_observation_completes_unchanged_tree():
    state = _make()
    goal.observe(state["slug"], "baseline")
    goal.checkpoint(state["slug"], "nothing", "stop", _digest(state), "skipped")
    done = goal.complete(state["slug"], "")
    assert done["status"] == "complete"


def test_skipped_checkpoint_does_not_block_completion():
    state = _make()
    d = _digest(state)
    goal.checkpoint(state["slug"], "work", "next", d, "pass")
    goal.checkpoint(state["slug"], "again", "next", d, "skipped")
    assert goal.complete(state["slug"], "")["status"] == "complete"


def test_changed_tree_invalidates_pass():
    state = _make()
    goal.observe(state["slug"], "baseline")
    (_project() / "new.py").write_text("x = 2\n")
    with pytest.raises(goal.GateNotPassed):
        goal.complete(state["slug"], "")


def test_changed_baseline_test_blocks_until_approved():
    p = _project()
    (p / "test_a.py").write_text("one\n")
    state = _make()
    goal.observe(state["slug"], "baseline")
    (p / "test_a.py").write_text("two\n")
    goal.observe(state["slug"], "final")
    with pytest.raises(goal.GateNotPassed, match="test_a.py"):
        goal.complete(state["slug"], "")
    goal.approve_test_change(state["slug"], "test_a.py", "Reviewer agreed the old assertion was wrong")
    assert goal.complete(state["slug"], "")["status"] == "complete"


def test_strong_gate_skips_review_weak_gate_requires_it():
    p = _project()
    (p / "test_a.py").write_text("def test(): pass\n")
    strong = _make(review_criterion="Every requirement holds")
    goal.observe(strong["slug"], "baseline")
    assert evidence.review_reasons(goal.load(strong["slug"]), None) == []
    assert goal.complete(strong["slug"], "")["status"] == "complete"

    (p / "test_new.py").write_text("def test(): pass\n")
    weak = _make(goal="Another goal", review_criterion="Every requirement holds")
    goal.observe(weak["slug"], "baseline")
    (p / "test_more.py").write_text("def test(): pass\n")
    goal.observe(weak["slug"], "final")
    reasons = evidence.review_reasons(goal.load(weak["slug"]), None)
    assert reasons and "not strong" in reasons[0]
    with pytest.raises(goal.GateNotPassed, match="review"):
        goal.complete(weak["slug"], "")


def test_review_is_matched_by_tree_hash_not_turn():
    p = _project()
    (p / "test_more.py").write_text("x\n")
    state = _make(review_criterion="Every requirement holds")
    d = _digest(state)
    goal.checkpoint(state["slug"], "work", "next", d, "pass")
    goal.checkpoint(state["slug"], "again", "next", d, "skipped")
    evidence_text = (f"Reviewed {d[:goal.reviewing.HASH_ECHO]}. "
                     "All requirements hold; nothing missing in the tree.")
    goal.record_review(state["slug"], "pass", evidence_text, "agent-123", d)
    assert goal.complete(state["slug"], "")["status"] == "complete"


def test_old_goal_without_new_fields_completes_after_observe(isolated_goals):
    state = _make()
    raw = goal.load(state["slug"])
    raw.pop("observations", None)
    raw.pop("test_manifest", None)
    goal._save(raw)
    goal.observe(state["slug"], "final")
    assert goal.complete(state["slug"], "")["status"] == "complete"
```

The weak case needs a test file that is absent from that goal's baseline. `test_more.py` is created after the weak goal's baseline, so `added_tests` is non-empty and the gate is weak. In the review test there is no baseline manifest, so the gate is weak and review is required.

- [ ] **Step 2: Run them to verify they fail.**

Run: `uv run --with pytest pytest -q tests/test_evidence.py`
Expected: FAIL. `complete` raises "The last gate did not pass" for the skipped and observation cases, and `approve_test_change` doesn't exist.

- [ ] **Step 3: Add the evidence rules** to `harness/evidence.py`:

```python
_HASH = re.compile(r"[0-9a-f]{64}")
_REVIEW_TAGS = {"review", "unverifiable", "judgment"}


def passing_record(state: dict, observed_hash: str) -> dict | None:
    """The latest pass recorded for exactly these files and this gate, or None."""
    if not isinstance(observed_hash, str) or not _HASH.fullmatch(observed_hash):
        return None
    records = [*state.get("checkpoints", []), *state.get("observations", [])]
    matches = [r for r in records
               if r.get("gate") == "pass" and r.get("tree_hash") == observed_hash
               and r.get("gate_spec") == state["gate"]]
    return max(matches, key=lambda r: r.get("at", "")) if matches else None


def _current_tests(state: dict) -> dict[str, str]:
    return test_manifest(state["cwd"], state.get("watch_paths"))


def changed_baseline_tests(state: dict) -> list[str]:
    """Baseline test files now changed or deleted, minus approved changes."""
    base = state.get("test_manifest") or {}
    now = _current_tests(state)
    approved = {(a["path"], a["sha256"]) for a in state.get("approvals", [])}
    return sorted(rel for rel, digest in base.items()
                  if now.get(rel) != digest and (rel, now.get(rel, "deleted")) not in approved)


def added_tests(state: dict) -> list[str]:
    """Test files that did not exist at the baseline."""
    if "test_manifest" not in state:
        return sorted(_current_tests(state))
    return sorted(set(_current_tests(state)) - set(state["test_manifest"]))


def gate_is_strong(state: dict) -> bool:
    """A command gate over tests that existed before the work and are unchanged.

    A gate directory means the agent wrote the gate, so it is never strong.
    """
    return (state["gate"]["kind"] == "command" and not state.get("gate_path")
            and bool(state.get("test_manifest"))
            and not changed_baseline_tests(state) and not added_tests(state))


def review_reasons(state: dict, requirements: dict | None) -> list[str]:
    """Why a reviewer must still pass this snapshot; empty means no review is needed."""
    if state["gate"]["kind"] == "coverage":
        return ["coverage gates are judged by a reviewer"]
    if not state["gate"].get("review_criterion"):
        return []
    reasons = []
    if not gate_is_strong(state):
        reasons.append("the gate is not strong: its tests were written or changed during this goal")
    tagged = sorted(rid for rid, tags in (requirements or {}).items() if set(tags) & _REVIEW_TAGS)
    if tagged:
        reasons.append("requirement lines need review: " + ", ".join(tagged))
    return reasons
```

- [ ] **Step 4: Rewrite completion in `harness/goal.py`.**
  - Move the body of the current `_apply_completion` unchanged into a new function `_apply_coverage_completion(state, observed_hash, gate_output)`.
  - Replace `_apply_completion` with:

```python
def _apply_completion(state: dict, observed_hash: str, gate_output: str) -> None:
    if state["status"] != "active":
        raise GoalClosed(f"cannot complete: goal status is {state['status']!r}")
    if state["gate"]["kind"] == "coverage":
        return _apply_coverage_completion(state, observed_hash, gate_output)
    record = evidence.passing_record(state, observed_hash)
    if record is None:
        raise GateNotPassed(
            "No passing gate result covers the current files; run close (or observe) first.")
    changed = evidence.changed_baseline_tests(state)
    if changed:
        raise GateNotPassed(
            "Baseline test files changed: " + ", ".join(changed)
            + ". Completion needs a reviewer's or the user's approval: approve-test-change.")
    reasons = evidence.review_reasons(state, _requirements(state))
    if reasons:
        criterion = state["gate"]["review_criterion"]
        reviews = [r for r in state.get("reviews", [])
                   if r["tree_hash"] == observed_hash and r["criterion"] == criterion]
        if not reviews or reviews[-1]["result"] != "pass":
            raise GateNotPassed(
                "An independent review is required (" + "; ".join(reasons)
                + ") and none has passed for this snapshot.")
    text = (record.get("gate_output") or "").strip() or (
        f"{state['gate']['value']} -> exit 0 (gate produced no output)")
    state["status"] = "complete"
    state["gate_evidence"] = _bounded(text)
    state["completed_at"] = _now()
```

- [ ] **Step 5: Rewrite the command-gate check in `_apply_review`.** Replace the block from `last = state["checkpoints"][-1] ...` through the `if observed_hash != digest:` check with:

```python
    if (not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)):
        raise GateNotPassed("No verified snapshot; run the gate again.")
    if observed_hash != digest:
        raise GateNotPassed("Artifacts changed after verification; run the gate again.")
    if state["gate"]["kind"] == "command":
        record = evidence.passing_record(state, digest)
        if record is None:
            raise GateNotPassed("The command gate must pass on this snapshot before review")
        turn = record.get("turn", state["used"]["turns"])
        last = None
    else:
        last = state["checkpoints"][-1] if state["checkpoints"] else None
        if not last or last["tree_hash"] != digest or last.get("gate_spec") != state["gate"]:
            raise GateNotPassed("The review does not match the current checkpoint")
        turn = last["turn"]
```

  Then:
  - In the appended review dict, use `"turn": turn`.
  - Leave the coverage tail (`if state["gate"]["kind"] == "coverage": last["gate"] ...`) as is; `last` is set on that branch.

- [ ] **Step 6: Add `approve_test_change`** after `record_review`:

```python
def _approve_test_change_locked(slug: str, path: str, reason: str) -> dict:
    state = load(slug)
    if state["status"] != "active":
        raise GoalClosed(f"Goal is {state['status']}")
    if not isinstance(reason, str) or len(reason.strip()) < 10:
        raise EvidenceInsufficient("Say who approved the test change and why")
    target = Path(state["cwd"]) / path
    digest = evidence._sha(target) if target.is_file() else "deleted"
    state.setdefault("approvals", []).append(
        {"path": path, "sha256": digest, "reason": reason.strip(), "at": _now()})
    return _persist_mutation_locked(state)


approve_test_change = _with_lock(_approve_test_change_locked)
```

Place the `approve_test_change = ...` line with the other `_with_lock` assignments.

- [ ] **Step 7: Update the tests that asserted the removed behavior.**
  - Run `uv run --with pytest pytest -q -x`. For each failure that asserts that a `skipped` last checkpoint, or a review on an earlier turn, blocks completion, change the assertion to the new rule.
  - Name each changed test in the commit message.
  - Do not change any test that fails for another reason: investigate it.

- [ ] **Step 8: Run the suite.**

Run: `uv run --with pytest pytest -q`
Expected: all green.

- [ ] **Step 9: Commit.**

```bash
git add harness/evidence.py harness/goal.py tests/
git commit -m "feat: completion from recorded evidence at the current tree; strong gates skip review; baseline test edits need approval"
```

---

### Task 4: Leases and `step-reclaim`

**Files:**
- Create: `harness/leases.py`
- Modify:
  - `harness/paths.py`: add `current_session()`;
  - `harness/steps.py`: renew the lease in `begin`/`attach`; extract `_close_interrupted` from `recover`; add `reclaim`.
- Test: `tests/test_leases.py` (new)

**Interfaces:**
- Produces:
  - `leases.LEASE_TERM_S = 600`, `leases.OTHER_SESSION_STALE_S = 300`;
  - `leases.read(slug) -> dict[str, dict]`;
  - `leases.renew(slug, attempt_id, session: str | None, now: float | None = None) -> None`;
  - `leases.reclaimable(lease: dict | None, session: str | None, now: float | None = None) -> str | None`;
  - `paths.current_session() -> str | None`, which reads `<harness_root>/session`;
  - `steps.reclaim(slug, attempt_id, *, expected_revision: int, session: str | None = None, now: float | None = None) -> dict`, with the same report shape as `recover`.

- [ ] **Step 1: Write the failing tests** in `tests/test_leases.py`:

```python
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))

import goal  # noqa: E402
import leases  # noqa: E402
import steps  # noqa: E402


def test_reclaimable_rules():
    assert leases.reclaimable(None, "s2", now=100.0).startswith("no lease")
    lease = {"session": "s1", "renewed_at": 1000.0}
    assert leases.reclaimable(lease, "s1", now=1000.0 + 599) is None
    assert "expired" in leases.reclaimable(lease, "s1", now=1000.0 + 600)
    assert leases.reclaimable(lease, "s2", now=1000.0 + 299) is None
    assert "silent" in leases.reclaimable(lease, "s2", now=1000.0 + 300)
    assert leases.reclaimable(lease, None, now=1000.0 + 300) is None


def test_corrupt_lease_file_reads_as_empty(make_state_goal):
    state = make_state_goal()
    leases._path(state["slug"]).write_text("{not json")
    assert leases.read(state["slug"]) == {}


def _attached(make_state_goal):
    state = make_state_goal()
    slug = state["slug"]
    begun = steps.begin(slug, "req-1")
    attached = steps.attach(slug, "req-1", "worker-1", expected_revision=begun["execution_revision"])
    return slug, attached


def test_attach_records_a_lease(make_state_goal, monkeypatch):
    monkeypatch.setattr(steps.paths, "current_session", lambda: "s1")
    slug, _ = _attached(make_state_goal)
    assert leases.read(slug)["req-1"]["session"] == "s1"


def test_reclaim_refuses_live_lease_then_closes_stale_one(make_state_goal, monkeypatch):
    monkeypatch.setattr(steps.paths, "current_session", lambda: "s1")
    slug, attached = _attached(make_state_goal)
    rev = attached["execution_revision"]
    renewed = leases.read(slug)["req-1"]["renewed_at"]
    with pytest.raises(steps.StepBusy):
        steps.reclaim(slug, "req-1", expected_revision=rev, session="s2", now=renewed + 10)
    out = steps.reclaim(slug, "req-1", expected_revision=rev, session="s2", now=renewed + 301)
    state = goal.load(slug)
    assert state["execution"]["active_attempt"] is None
    assert state["execution"]["attempts"][0]["status"] == "recovered"
    assert out["attempt_status"] == "recovered" and out["attempt_closed"] is True


def test_dead_worker_cannot_finish_after_reclaim(make_state_goal, monkeypatch, tmp_path):
    monkeypatch.setattr(steps.paths, "current_session", lambda: "s1")
    slug, attached = _attached(make_state_goal)
    renewed = leases.read(slug)["req-1"]["renewed_at"]
    steps.reclaim(slug, "req-1", expected_revision=attached["execution_revision"],
                  session="s2", now=renewed + 301)
    update = tmp_path / "u.json"
    update.write_text("{}")
    with pytest.raises(steps.StepConflict):
        steps.finish(slug, "req-1", expected_revision=goal.load(slug)["execution"]["revision"],
                     update_file=update, worker_evidence_file=update)


def test_reclaim_replay_is_harmless(make_state_goal, monkeypatch):
    monkeypatch.setattr(steps.paths, "current_session", lambda: "s1")
    slug, attached = _attached(make_state_goal)
    renewed = leases.read(slug)["req-1"]["renewed_at"]
    steps.reclaim(slug, "req-1", expected_revision=attached["execution_revision"],
                  session="s2", now=renewed + 301)
    again = steps.reclaim(slug, "req-1", expected_revision=0, session="s2", now=renewed + 400)
    assert again["replayed"] is True
```

`steps.report()` returns `attempt_status`, `attempt_closed`, `execution_revision` and `replayed`; `begin` and `attach` reports carry `execution_revision`.

- [ ] **Step 2: Run them to verify they fail.**

Run: `uv run --with pytest pytest -q tests/test_leases.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'leases'`.

- [ ] **Step 3: Create `harness/leases.py`.**

```python
"""Leases on state-mode attempts.

A worker's claim on an attempt expires on a clock: nothing can prove a worker
died with its session, but a lease that stopped being renewed can be taken
over. Kept beside the goal file, not in it, because renewals happen on every
tool call and must not bump the goal's execution revision.
"""

import json
import os
import time

import filelock
import paths

LEASE_TERM_S = 600          # gate.DEFAULT_TIMEOUT (480) plus a margin
OTHER_SESSION_STALE_S = 300  # longer than a slow model call


def _path(slug: str):
    return paths.goals_dir() / f"{slug}.leases.json"


def read(slug: str) -> dict:
    try:
        data = json.loads(_path(slug).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def renew(slug: str, attempt_id: str, session: str | None, now: float | None = None) -> None:
    target = _path(slug)
    with filelock.goal_locked(target):
        data = read(slug)
        data[attempt_id] = {"session": session, "renewed_at": time.time() if now is None else now}
        tmp = target.parent / f".{target.name}.tmp-{os.getpid()}"
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, target)


def reclaimable(lease: dict | None, session: str | None, now: float | None = None) -> str | None:
    """Why the attempt may be taken over, or None while its lease holds."""
    if not isinstance(lease, dict) or not isinstance(lease.get("renewed_at"), (int, float)):
        return "no lease recorded: the attempt predates leases or never renewed"
    age = (time.time() if now is None else now) - lease["renewed_at"]
    if age >= LEASE_TERM_S:
        return f"lease expired {int(age)} s after its last renewal"
    owner = lease.get("session")
    if session and owner and session != owner and age >= OTHER_SESSION_STALE_S:
        return f"owning session {owner} has been silent for {int(age)} s"
    return None
```

- [ ] **Step 4: Add `current_session` to `harness/paths.py`.**

```python
def current_session() -> str | None:
    """The session id the SessionStart hook last recorded, if any."""
    try:
        value = (harness_root() / "session").read_text().strip()
    except OSError:
        return None
    return value or None
```

- [ ] **Step 5: Change `harness/steps.py`.**
  - Add `import leases` and `import time` beside the existing imports.
  - **Renew on reserve and bind.** In `begin`, just before `return _begin_report(state, attempt, packet, replayed=False)`, add `leases.renew(slug, request_id, paths.current_session())`. In `attach`, just before the final `return report(state, attempt)`, add `leases.renew(slug, attempt_id, paths.current_session())`.
  - **Extract `_close_interrupted`.**
    - Move the whole `runner = ...` / `try:` block at the end of `recover` (from `runner = goal._path(slug)...` through `except filelock.LockTimeout ... raise StepBusy(...)`) into a new function:

      ```python
      def _close_interrupted(slug, attempt_id, *, expected_revision, reason, worker_state,
                             evidence_ref, evidence_text, observed_hash):
      ```

    - `recover` then ends with:

      ```python
      return _close_interrupted(slug, attempt_id, expected_revision=expected_revision,
                                reason=reason, worker_state=worker_state,
                                evidence_ref=evidence_ref, evidence_text=evidence_text,
                                observed_hash=observed_hash)
      ```

    - Inside it, change the finishing check to `if attempt["status"] == "finishing" and evidence_text is not None and not _gate_liveness_resolved(evidence_text, attempt["gate_process"]):`.
  - **Add `reclaim`:**

```python
def reclaim(slug: str, attempt_id: str, *, expected_revision: int,
            session: str | None = None, now: float | None = None) -> dict:
    """Close an attempt whose lease went stale; no liveness evidence is needed."""
    _require_slug(slug)
    _require_id(attempt_id, "attempt_id")
    state = _state(slug)
    attempt = _attempt(state, attempt_id)
    if attempt is None:
        raise StepConflict(f"unknown attempt: {attempt_id}")
    if attempt["status"] in {"accepted", "recovered"}:
        return report(state, attempt, replayed=True)
    lease = leases.read(slug).get(attempt_id)
    why = leases.reclaimable(lease, session or paths.current_session(), now)
    if why is None:
        raise StepBusy("the attempt's lease is still live; wait or let its worker finish")
    clock = time.time() if now is None else now
    process = attempt.get("gate_process")
    if attempt["status"] == "finishing" and process:
        started = goal.datetime.fromisoformat(process["started_at"]).timestamp()
        if clock < started + gate.DEFAULT_TIMEOUT + 120:
            raise StepBusy("the attempt's gate may still be running; wait for its timeout")
    worker_state = ("not-started" if attempt["status"] == "reserved" and attempt["worker"] is None
                    else "stopped")
    payload = json.dumps({"reclaim": why, "lease": lease, "session": session},
                         sort_keys=True).encode()
    evidence_ref = store_blob(slug, "recovery_evidence", payload)
    try:
        observed_hash = tree_hash.compute(state["cwd"], state["watch_paths"])
    except Exception:
        observed_hash = gate.UNKNOWN_HASH
    return _close_interrupted(slug, attempt_id, expected_revision=expected_revision,
                              reason="Reclaimed: " + why, worker_state=worker_state,
                              evidence_ref=evidence_ref, evidence_text=None,
                              observed_hash=observed_hash)
```

  `goal` imports `datetime` (from the `datetime` module), so `goal.datetime.fromisoformat` works. `steps.py` already imports `json`, `gate` and `tree_hash`; only `leases` and `time` are new.

- [ ] **Step 6: Run the tests and the suite.**

Run: `uv run --with pytest pytest -q tests/test_leases.py && uv run --with pytest pytest -q`
Expected: all green. The existing `recover` tests are unchanged and still pass.

- [ ] **Step 7: Commit.**

```bash
git add harness/leases.py harness/paths.py harness/steps.py tests/test_leases.py
git commit -m "feat: attempt leases and step-reclaim for attempts whose session died"
```

---

### Task 5: `close`, budget finalization, and the CLI commands

**Files:**
- Modify:
  - `harness/goal.py`: add `close`;
  - `harness/cli.py`:
    - new subcommands `observe`, `close`, `approve-test-change`, `step-reclaim`;
    - `start` records the baseline;
    - `check`'s reviewer hand-off is extracted into `_review_handoff`.
- Test: `tests/test_horizon_cli.py` (extend, using the `state_client` fixture)

**Interfaces:**
- Consumes: `observe`, `passing_record`, `review_reasons`, `approve_test_change`, `steps.reclaim`.
- Produces:
  - `goal.close(slug: str, *, run_gate: bool = True, timeout: int = gate.DEFAULT_TIMEOUT) -> dict`, returning `{"complete": bool, "missing": list[str], "summary": dict, "finalized": dict | None}`;
  - CLI `close SLUG [--if-ready] [--timeout N]`, with exit 0 when complete and 1 otherwise. When review is required and a pass covers the tree, the report includes `review_file` and `review_brief`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_horizon_cli.py`):

```python
def test_start_records_baseline_and_close_completes_in_one_call(state_client):
    project, runtime, run = state_client
    code, started = run("start", "Keep the build green", "--gate", "true")
    assert code == 0
    slug = started["slug"]
    state_dir = project / (".codex" if runtime == "codex" else ".claude") / "harness" / "goals"
    saved = json.loads((state_dir / f"{slug}.json").read_text())
    assert [o["role"] for o in saved["observations"]] == ["baseline"]
    code, closed = run("close", slug)
    assert code == 0 and closed["complete"] is True


def test_close_reports_missing_review_with_brief(state_client):
    project, runtime, run = state_client
    (project / "test_new.py").write_text("def test(): pass\n")
    code, started = run("start", "Add a feature", "--gate", "true",
                        "--review", "Every requirement holds")
    slug = started["slug"]
    (project / "test_more.py").write_text("def test(): pass\n")
    code, closed = run("close", slug)
    assert code == 1 and closed["complete"] is False
    assert any("review" in m for m in closed["missing"])
    assert closed["review_file"] and closed["review_brief"]


def test_close_if_ready_finalizes_exhausted_budget(state_client):
    project, runtime, run = state_client
    code, started = run("start", "Make the gate pass", "--gate", "false", "--max-turns", "1")
    slug = started["slug"]
    run("check", slug, "--did", "tried", "--next", "stop")
    code, closed = run("close", slug, "--if-ready")
    assert closed["complete"] is False
    assert closed["finalized"]["reason"] == "budget_exhausted"


def test_step_reclaim_cli_refuses_live_lease(state_client):
    project, runtime, run = state_client
    code, started = run("start", "State goal", "--gate", "true", "--execution", "state")
    slug = started["slug"]
    code, begun = run("step-begin", slug, "--request-id", "r1")
    code, out = run("step-reclaim", slug, "--attempt", "r1",
                    "--expected-revision", str(begun["execution_revision"]))
    assert code == 2 and out["error"] == "StepBusy"
```

  Add `import json` at the top of `tests/test_horizon_cli.py` if it isn't there. The `step-begin` report carries `execution_revision`.

- [ ] **Step 2: Run them to verify they fail.**

Run: `uv run --with pytest pytest -q tests/test_horizon_cli.py -k "close or reclaim or baseline"`
Expected: FAIL with argparse "invalid choice: 'close'".

- [ ] **Step 3: Add `close` to `harness/goal.py`** after `observe`:

```python
def close(slug: str, *, run_gate: bool = True, timeout: int = gate.DEFAULT_TIMEOUT) -> dict:
    """Finish in one call when the evidence allows; otherwise say exactly what is missing.

    With run_gate, the current files are observed first when no pass covers
    them. When the budget is spent, the current tree is recorded as the
    deliverable (anytime semantics) instead of being left open.
    """
    state = load(slug)
    if state["status"] != "active":
        return {"complete": state["status"] == "complete", "missing": [],
                "summary": summary(state), "finalized": state.get("finalized")}
    try:
        observed = tree_hash.compute(state["cwd"], state.get("watch_paths"))
    except Exception:
        observed = gate.UNKNOWN_HASH
    if (run_gate and state["gate"]["kind"] == "command"
            and evidence.passing_record(state, observed) is None
            and not budget_exhausted(state)):
        observe(slug, "final", timeout=timeout)
    try:
        done = complete(slug, "")
        return {"complete": True, "missing": [], "summary": summary(done), "finalized": None}
    except (GateNotPassed, AttemptOpen, RevisionConflict) as exc:
        missing = [str(exc)]
    state = load(slug)
    if budget_exhausted(state) and not state.get("finalized"):
        with filelock.goal_locked(_path(slug)):
            state = load(slug)
            if not state.get("finalized"):
                record = evidence.passing_record(state, observed)
                state["finalized"] = {"reason": "budget_exhausted", "at": _now(),
                                      "tree_hash": observed,
                                      "gate": record["gate"] if record else "not passed"}
                state = _persist_mutation_locked(state)
    return {"complete": False, "missing": missing, "summary": summary(state),
            "finalized": state.get("finalized")}
```

  If the state-mode `_persist_mutation_locked` call refuses while an attempt is open, skip finalization in that case: check `require_idle` first and catch `AttemptOpen`.

- [ ] **Step 4: Wire the CLI** in `harness/cli.py`.

  **Parser**, after `complete`:

```python
    observe = commands.add_parser("observe", help="Run the gate now and record it; charges no turn")
    observe.add_argument("slug")
    observe.add_argument("--role", choices=("baseline", "resume", "final"), default="resume")
    observe.add_argument("--timeout", type=int, default=gate.DEFAULT_TIMEOUT)
    close = commands.add_parser("close", help="Complete when evidence allows, else report what is missing")
    close.add_argument("slug")
    close.add_argument("--if-ready", action="store_true", help="Never run the gate; complete or finalize only")
    close.add_argument("--timeout", type=int, default=gate.DEFAULT_TIMEOUT)
    approve = commands.add_parser("approve-test-change", help="Record a reviewer's or the user's approval of a changed baseline test")
    approve.add_argument("slug")
    approve.add_argument("--path", required=True)
    approve.add_argument("--reason", required=True)
    reclaim = commands.add_parser("step-reclaim", help="Close an attempt whose lease went stale")
    reclaim.add_argument("slug")
    reclaim.add_argument("--attempt", required=True)
    reclaim.add_argument("--expected-revision", required=True, type=int)
    reclaim.add_argument("--session")
```

  **Extract the hand-off.** Move the `report["review_file"] = ...` and `report["review_brief"] = ...` lines from `check` into:

```python
def _review_handoff(report: dict, state: dict, digest: str) -> None:
    report["review_file"] = reviewing.review_file(
        state["slug"], digest, len(state.get("reviews") or []) + 1,
        watch_paths=state.get("watch_paths") or [state["cwd"]])
    report["review_brief"] = reviewing.brief_for(
        state, digest, report["review_file"], str(paths.harness_root()))
```

  `check` calls `_review_handoff(report, state, digest)` where it used to set them.

  **Dispatch**:

```python
    if args.action == "observe":
        return status(goal.observe(args.slug, args.role, timeout=args.timeout)), 0
    if args.action == "close":
        result = goal.close(args.slug, run_gate=not args.if_ready, timeout=args.timeout)
        if not result["complete"] and any("review" in m for m in result["missing"]):
            state = goal.load(args.slug)
            digest = tree_hash.compute(state["cwd"], state.get("watch_paths"))
            if evidence.passing_record(state, digest):
                _review_handoff(result, state, digest)
        return result, 0 if result["complete"] else 1
    if args.action == "approve-test-change":
        return status(goal.approve_test_change(args.slug, args.path, args.reason)), 0
```

  - Add `import evidence` to the CLI imports.
  - Add `"step-reclaim"` to the step action set in `dispatch`.
  - In `_dispatch_step`, add a branch: `elif args.action == "step-reclaim": report = steps.reclaim(args.slug, args.attempt, expected_revision=args.expected_revision, session=args.session)`.
  - If `steps.StepBusy` isn't already a `goal.GoalStateError` subclass caught by `_dispatch_step`, add it to that except tuple. (`StepError` subclasses `GoalStateError`, so it is caught.)

  **`start` baseline.** After `state = goal.create(...)`:

```python
        if state["gate"]["kind"] == "command":
            state = goal.observe(state["slug"], "baseline")
```

- [ ] **Step 5: Run the tests and the suite.**

Run: `uv run --with pytest pytest -q`
Expected: all green.

- [ ] **Step 6: Commit.**

```bash
git add harness/goal.py harness/cli.py tests/test_horizon_cli.py
git commit -m "feat: one-call close with budget finalization; observe, approve-test-change and step-reclaim commands; baseline at start"
```

---

### Task 6: Claude Code hooks

**Files:**
- Create: `harness/hooks.py`
- Modify: `harness/cli.py`:
  - a `hook EVENT` subcommand handled in `main` before normal dispatch, which reads stdin and always exits 0;
  - `install-hooks` and `uninstall-hooks` subcommands;
  - an automatic install on the first `start` for the claude runtime.
- Test: `tests/test_hooks.py` (new)

**Interfaces:**
- Consumes:
  - `leases.read`, `leases.renew`, `leases.reclaimable`;
  - `steps.reclaim`;
  - `goal.close`, `goal.load`, `goal.summary`;
  - `paths.goals_dir`, `paths.harness_root`.
- Produces:
  - `hooks.MARK = "horizon-hook"`;
  - `hooks.install(project: Path, entrypoint: str) -> bool` (True if it changed anything);
  - `hooks.uninstall(project: Path) -> bool`;
  - `hooks.run(event: str, payload: dict) -> dict | None`, where the value printed for `SessionStart` is `{"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": str}}`.

- [ ] **Step 1: Write the failing tests** in `tests/test_hooks.py`:

```python
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

import hooks  # noqa: E402

ENTRY = str(ROOT / ".claude/skills/horizon/scripts/harness.py")


def test_install_merges_and_is_idempotent(tmp_path):
    project = tmp_path / "my project "
    (project / ".claude").mkdir(parents=True)
    user_hook = {"hooks": {"PostToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo mine"}]}]}}
    (project / ".claude/settings.json").write_text(json.dumps(user_hook))
    assert hooks.install(project, ENTRY) is True
    assert hooks.install(project, ENTRY) is False
    settings = json.loads((project / ".claude/settings.json").read_text())
    commands = [h["command"] for groups in settings["hooks"].values() for g in groups for h in g["hooks"]]
    assert "echo mine" in commands
    assert sum(hooks.MARK in c for c in commands) == 3
    assert all(f'"{ENTRY}"' in c for c in commands if hooks.MARK in c)


def test_install_leaves_invalid_settings_alone(tmp_path):
    project = tmp_path / "p"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude/settings.json").write_text("{oops")
    try:
        hooks.install(project, ENTRY)
        raised = False
    except hooks.HookInstallError:
        raised = True
    assert raised and (project / ".claude/settings.json").read_text() == "{oops"


def test_uninstall_removes_only_horizon(tmp_path):
    project = tmp_path / "p"
    (project / ".claude").mkdir(parents=True)
    (project / ".claude/settings.json").write_text(json.dumps(
        {"hooks": {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}}))
    hooks.install(project, ENTRY)
    assert hooks.uninstall(project) is True
    settings = json.loads((project / ".claude/settings.json").read_text())
    assert settings["hooks"] == {"Stop": [{"hooks": [{"type": "command", "command": "echo mine"}]}]}


def test_hook_command_never_fails_on_bad_input(tmp_path):
    project = tmp_path / "hook project "
    (project / ".claude").mkdir(parents=True)
    for event in ("SessionStart", "PostToolUse", "Stop"):
        for stdin in ("", "{not json", json.dumps({"session_id": "s1"})):
            proc = subprocess.run(
                [sys.executable, ENTRY, "--runtime", "claude", "--project", str(project), "hook", event],
                input=stdin, capture_output=True, text=True, timeout=20)
            assert proc.returncode == 0, proc.stderr
            assert "Traceback" not in proc.stderr


def test_session_start_records_session_and_reports_goals(state_client):
    project, runtime, run = state_client
    if runtime != "claude":
        return
    run("start", "Keep the build green", "--gate", "true")
    proc = subprocess.run(
        [sys.executable, ENTRY, "--runtime", "claude", "--project", str(project), "hook", "SessionStart"],
        input=json.dumps({"session_id": "s9"}), capture_output=True, text=True, timeout=20)
    out = json.loads(proc.stdout)
    assert "keep-the-build-green" in out["hookSpecificOutput"]["additionalContext"]
    assert (project / ".claude/harness/session").read_text().strip() == "s9"


def test_first_start_installs_hooks(state_client):
    project, runtime, run = state_client
    code, started = run("start", "Keep the build green", "--gate", "true")
    if runtime == "claude":
        settings = json.loads((project / ".claude/settings.json").read_text())
        assert any(hooks.MARK in h["command"] for g in settings["hooks"]["Stop"] for h in g["hooks"])
        assert started["hooks"] == "installed"
    else:
        assert "hooks" not in started
```

- [ ] **Step 2: Run them to verify they fail.**

Run: `uv run --with pytest pytest -q tests/test_hooks.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'hooks'`.

- [ ] **Step 3: Create `harness/hooks.py`.**

```python
"""Claude Code hooks that keep Horizon's saved state honest at zero model calls.

SessionStart records the session id, reclaims attempts whose lease went stale
and reports each active goal in one line. PostToolUse renews the leases this
session owns. Stop completes or finalizes goals whose evidence allows it.
Every hook returns quickly and never raises to the host.
"""

import json
import os
from pathlib import Path

import goal
import leases
import paths
import steps

MARK = "horizon-hook"
_EVENTS = {"SessionStart": None, "PostToolUse": "*", "Stop": None}


class HookInstallError(Exception):
    pass


def _settings_path(project: Path) -> Path:
    return Path(project) / ".claude" / "settings.json"


def _load(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except ValueError as exc:
        raise HookInstallError(f"{path} is not valid JSON; Horizon left it untouched") from exc
    if not isinstance(data, dict):
        raise HookInstallError(f"{path} is not a JSON object; Horizon left it untouched")
    return data


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.parent / f".{path.name}.tmp-{os.getpid()}"
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def _command(entrypoint: str, event: str) -> str:
    return f'python3 "{entrypoint}" --runtime claude hook {event} # {MARK}'


def install(project: Path, entrypoint: str) -> bool:
    path = _settings_path(project)
    settings = _load(path)
    hooks = settings.setdefault("hooks", {})
    changed = False
    for event, matcher in _EVENTS.items():
        groups = hooks.setdefault(event, [])
        if any(MARK in h.get("command", "") for g in groups for h in g.get("hooks", [])):
            continue
        group = {"hooks": [{"type": "command", "command": _command(entrypoint, event), "timeout": 30}]}
        if matcher:
            group["matcher"] = matcher
        groups.append(group)
        changed = True
    if changed:
        _write(path, settings)
    return changed


def uninstall(project: Path) -> bool:
    path = _settings_path(project)
    settings = _load(path)
    hooks = settings.get("hooks", {})
    changed = False
    for event in list(hooks):
        kept = []
        for group in hooks[event]:
            inner = [h for h in group.get("hooks", []) if MARK not in h.get("command", "")]
            changed |= len(inner) != len(group.get("hooks", []))
            if inner:
                kept.append({**group, "hooks": inner})
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    if changed:
        _write(path, settings)
    return changed


def _active_goals() -> list[dict]:
    out = []
    for path in sorted(paths.goals_dir().glob("*.json")):
        if path.name.endswith(".leases.json"):
            continue
        try:
            state = goal.load(path.stem)
        except Exception:
            continue
        if state["status"] == "active":
            out.append(state)
    return out


def _session_start(session: str | None) -> dict | None:
    if session:
        root = paths.harness_root()
        root.mkdir(parents=True, exist_ok=True)
        (root / "session").write_text(session + "\n")
    lines = []
    for state in _active_goals():
        note = ""
        attempt = (state.get("execution") or {}).get("active_attempt")
        if attempt:
            why = leases.reclaimable(leases.read(state["slug"]).get(attempt), session)
            if why:
                try:
                    steps.reclaim(state["slug"], attempt, expected_revision=state["execution"]["revision"],
                                  session=session)
                    note = f" Reclaimed attempt {attempt} ({why})."
                except Exception as exc:
                    note = f" Attempt {attempt} could not be reclaimed: {exc}."
            else:
                note = f" Attempt {attempt} is still owned by a live session."
        row = goal.row(goal.load(state["slug"]))
        lines.append(f"Horizon goal {state['slug']}: {json.dumps(row)}.{note} "
                     f"Run `close {state['slug']}` when you think it is done.")
    if not lines:
        return None
    return {"hookSpecificOutput": {"hookEventName": "SessionStart",
                                   "additionalContext": "\n".join(lines)}}


def _post_tool_use(session: str | None) -> None:
    if not session:
        return
    for state in _active_goals():
        attempt = (state.get("execution") or {}).get("active_attempt")
        if attempt and (leases.read(state["slug"]).get(attempt) or {}).get("session") == session:
            leases.renew(state["slug"], attempt, session)


def _stop() -> None:
    for state in _active_goals():
        goal.close(state["slug"], run_gate=False)


def run(event: str, payload: dict) -> dict | None:
    session = payload.get("session_id") if isinstance(payload, dict) else None
    if event == "SessionStart":
        return _session_start(session)
    if event == "PostToolUse":
        _post_tool_use(session)
    elif event == "Stop":
        _stop()
    return None
```

  Check that `goal.row` exists with that name (it does at line 821). If it renders too long a line, use `goal.summary` fields `status` and `next` instead.

- [ ] **Step 4: Wire it into `harness/cli.py`.**
  - Add `import hooks`.
  - In `parser()`, add `hook = commands.add_parser("hook"); hook.add_argument("event", choices=("SessionStart", "PostToolUse", "Stop"))`, plus `commands.add_parser("install-hooks")` and `commands.add_parser("uninstall-hooks")`.
  - In `main`, right after `os.environ[...] = args.runtime` and the `--project` chdir:

```python
    if args.action == "hook":
        try:
            raw = sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
            out = hooks.run(args.event, payload)
            if out:
                print(json.dumps(out))
        except Exception:
            pass
        return 0
```

  - Wrap the `--project` chdir so that a failure there in hook mode also returns 0. If that's simpler, move the hook branch before the chdir and chdir inside its own `try`.
  - **Auto-install.** In `dispatch`, at the end of the `start` branch, before `return report, 0`:

```python
        if paths.runtime() == "claude":
            try:
                if hooks.install(paths.project_root(), args.entrypoint):
                    report["hooks"] = "installed"
            except hooks.HookInstallError as exc:
                report["hooks_error"] = str(exc)
```

  - Add the `install-hooks` / `uninstall-hooks` dispatch: return `{"changed": hooks.install(paths.project_root(), args.entrypoint)}` and `{"changed": hooks.uninstall(paths.project_root())}`.
  - In `main`, set `args.entrypoint = os.path.abspath(sys.argv[0]) if argv is None else os.path.abspath(sys.argv[0])`. For in-process tests that call `main([...])`, a non-existent entrypoint is harmless; the CLI tests run the real entrypoint as a subprocess.

- [ ] **Step 5: Run the tests and the suite.**

Run: `uv run --with pytest pytest -q`
Expected: all green.

- [ ] **Step 6: Commit.**

```bash
git add harness/hooks.py harness/cli.py tests/test_hooks.py
git commit -m "feat: Claude Code hooks for session start, lease renewal and stop; installed on first start"
```

---

### Task 7: Skill text and reference docs

**Files:**
- Modify: `.claude/skills/horizon/SKILL.md`, `.agents/skills/horizon/SKILL.md`, `docs/harness-cli.md`, `README.md`
- Test: `tests/test_skill_contract.py`. It checks that documented commands exist; extend it for `close`, `observe`, `step-reclaim` and `approve-test-change`.

**Interfaces:**
- Consumes: the CLI commands from Tasks 5–6.

- [ ] **Step 1: Write the failing contract test.** Add `close`, `observe`, `step-reclaim` and `approve-test-change` to the list of commands `test_skill_contract.py` asserts appear in both SKILL.md files and in `docs/harness-cli.md`. Follow that file's existing pattern for listed commands.

Run: `uv run --with pytest pytest -q tests/test_skill_contract.py`
Expected: FAIL (the docs don't mention them yet).

- [ ] **Step 2: Rewrite both SKILL.md files.** Keep each runtime's existing differences.
  - **`## Before start`.** Add one sentence: write `request.md`, `requirements.md` and the gate in the same Bash call that runs `start`. `start` runs the gate once as the baseline; in Claude Code, the first `start` in a project also installs Horizon's hooks and reports `"hooks": "installed"`.
  - **Replace the end of `## Conversation-mode increments` and all of `## Independent review` up to `## Limits and resumption`** with:

    > When you believe the work is done, run `close SLUG`. It completes the goal if a passing gate result covers the current files and nothing else is missing; otherwise it lists what is missing.
    > - If it says a review is required, dispatch the fresh reviewer with the `review_brief` it printed, record the verdict with `review`, then run `close` again.
    > - If it says a baseline test file changed, stop and ask the user (or the reviewer) whether the change is legitimate. Only then run `approve-test-change` with who approved it and why.
    >
    > A skipped check never blocks completion. A pass recorded in an earlier session still counts if the files are unchanged.

  - **`## Optional state mode`:**
    - State mode is for long unattended runs or parallel work; use conversation mode otherwise.
    - If `status` shows an attempt you didn't start, its session may have died. The session-start hook reclaims it automatically once its lease is stale.
    - Without hooks (Codex), run `step-reclaim` when `status` shows an attempt that no live worker owns. It refuses while the lease is live.
  - **Codex variant only.** Add: at session start, run `status`, and for any active attempt run `step-reclaim` (one call), since Codex has no hooks.
  - **Remove** the sentence "Another `check` on unchanged files records `skipped` and blocks both."

- [ ] **Step 3: Update `docs/harness-cli.md` and `README.md`.**
  - `docs/harness-cli.md`: add a short section for each new command, giving its arguments (from Task 5 and 6 parsers), exit codes, and the leases/observations files.
  - `README.md` "What it does": add evidence-based `close`, automatic recovery, and the hooks. Keep the status line "no measured improvement yet".

- [ ] **Step 4: Run the suite.**

Run: `uv run --with pytest pytest -q`
Expected: all green.

- [ ] **Step 5: Commit.**

```bash
git add .claude/skills/horizon/SKILL.md .agents/skills/horizon/SKILL.md docs/harness-cli.md README.md tests/test_skill_contract.py
git commit -m "docs: skill protocol for close, observations, leases and hooks"
```

---

## After this plan

- **Stage A gate:** the cap-study re-run in the spec's Testing section, step 2. That study's harness lives in the archived `continual-harness` repo (`bench/minigpt`, `MINIGPT_STUDY=cap`). It gets its own short plan once this branch is reviewed: a bundle from this branch, plus a Horizon-A arm and a kill-injection arm.
- **Stage C** gets its own plan after the Stage A gate passes.
