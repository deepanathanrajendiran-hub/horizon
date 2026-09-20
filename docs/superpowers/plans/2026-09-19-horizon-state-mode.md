# Horizon State Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional, bounded task-state handoffs to Horizon for coding and refactoring, with fresh workers, recoverable attempts, and unchanged acceptance requirements.

**Architecture:** Keep one shared Python CLI and separate Codex/Claude Code skill instructions. A versioned execution block in each goal stores compact knowledge and an append-only attempt history; fresh native workers consume bounded packets and return validated updates. The coordinator reserves attempts and invokes the existing gate/review machinery through an idempotent step protocol.

**Tech Stack:** Python 3.10+ standard library, local macOS/Linux filesystems, POSIX advisory locks, pytest, Markdown skills, and the host's native worker tools. No model API client or new runtime dependency.

**Spec:** [Approved Horizon state-mode design](</Users/deeps/Documents/NEW STUFF/fun /docs/superpowers/specs/2026-09-19-horizon-state-mode-design.md>). The user approved proceeding on 2026-09-19.

## Global Constraints

- “V1 needs no new model API keys, service, scheduler, or automatic memory system.”
- “It supports one active worker per goal.”
- “`start --execution state` opts in. Omission means `conversation`, the existing behavior.”
- “Goals without an execution field remain conversation goals.”
- “Mode is fixed at creation; v1 does not convert an active legacy goal.”
- “The **entire serialized packet is limited to 24 KiB of UTF-8**, including its fixed fields.”
- “Checkpoint and review excerpts each have a 2 KiB serialized allowance.”
- Summary: 2 KiB; next action: 1 KiB; at most 64 active entries; each statement: 1 KiB; each entry: at most four evidence references; update input: 64 KiB.
- “Never silently drop active entries to fit the limit.”
- “In state mode, `max_turns` counts **reserved worker attempts**, including crashes and launch failures.”
- “No filesystem transaction covers both a worker's source edits and its JSON checkpoint.”
- “Other platforms cannot opt into state mode until an equivalent locking implementation is tested.”
- “Do not install or overwrite global skills as part of this project change.”
- Preserve legacy gates, reviews, fingerprints, runtime paths, and conversation-mode budget semantics. No changes to Refine's behavior.
- Use the user's AST editor for Python/config edits: discover `list_symbols` before structural edits, then use the narrowest structural operation. Markdown may use patches. If that editor is unavailable, report the specific missing capability rather than silently changing editing methods.

---

## Execution baseline and file map

The source root is `/Users/deeps/Documents/NEW STUFF/fun `, including the trailing space. Git HEAD when this plan was written is `b144f5d`; the implementation this plan extends includes uncommitted runtime-separation changes. A worktree made from HEAD alone will **not** contain `harness/` or the current Codex skills. At execution time use the worktree skill and carry the current working-tree source snapshot, including symlinks and untracked implementation files, into the isolated checkout. Verify its source fingerprints before editing. Keep the original checkout intact. Preserve the three pre-existing additions to `.claude/harness/CHANGELOG.md`; they are not part of this feature.

Record the copied runtime-separation baseline separately from this feature's commits. Do not stage unrelated user changes into any task commit. Stop or finish active goal writers before switching their lock implementation; old and new versions must not concurrently mutate the same goals. Do not terminate unrelated processes or alter global installations to achieve this.

Paths below are repository-relative Git paths, resolved against the execution checkout. Source files to read first: [CLI](</Users/deeps/Documents/NEW STUFF/fun /harness/cli.py>), [goal lifecycle](</Users/deeps/Documents/NEW STUFF/fun /harness/goal.py>), [gates](</Users/deeps/Documents/NEW STUFF/fun /harness/gate.py>), [locking](</Users/deeps/Documents/NEW STUFF/fun /harness/filelock.py>), and [existing CLI tests](</Users/deeps/Documents/NEW STUFF/fun /tests/test_horizon_cli.py>).

| File | Responsibility |
| --- | --- |
| `harness/task_state.py` — new | Strict data validation, update merge, evidence checks, packet construction and limits |
| `harness/steps.py` — new | Attempt reservation, binding, finishing, replay, and recovery |
| `harness/filelock.py` | Add process-owned goal/runner locks; preserve the existing Refine lock API |
| `harness/goal.py` | Add execution-mode creation/loading, revisioned saves, and lifecycle guards |
| `harness/gate.py` | Optional live-process callback, preserving verification/timeout behavior |
| `harness/cli.py` | Add mode selection and four step commands; report compact status |
| `tests/conftest.py` — new | Isolated, two-runtime CLI client for the new integration tests |
| `tests/test_task_state.py` — new | Schema, evidence, update, and packet invariants |
| `tests/test_horizon_steps.py` — new | CLI reservation, handoff, finishing, recovery, and lifecycle tests |
| `tests/test_state_concurrency.py` — new | Real-process lock, duplicate-call, and crash cases |
| `tests/test_filelock.py`, `test_goal.py`, `test_gate.py`, `test_tree_hash.py`, `test_skill_contract.py` | Extend existing contracts where behavior is integrated |
| `.agents/skills/horizon/SKILL.md`, `.claude/skills/horizon/SKILL.md` | Host-specific coordinator/worker instructions |
| `docs/harness-cli.md`, `README.md` | Mode selection, schema, examples, recovery, and limits |
| `docs/evaluations/horizon-state-pilot/` — new | Frozen fixture definitions, manifest, raw outcomes, and pilot report |

The new core modules may depend on `goal`, `gate`, `paths`, `tree_hash`, and `filelock`. Keep the import direction `cli -> steps -> goal -> task_state`; `task_state` must not import `goal` or `steps`. `gate` must not import `steps`. The lifecycle callback below avoids that cycle.

### Test environment

Use the same interpreter for the test runner and CLI subprocesses. This machine currently has Python 3.14.4 and a cached pytest installation. These shell settings make the existing tests runnable without installing packages:

```bash
export PYTHONDONTWRITEBYTECODE=1
export PYTEST_DISABLE_PLUGIN_AUTOLOAD=1
export PYTHONPATH='/Users/deeps/.cache/uv/archive-v0/044nI_HLHtvNp2AOxHKcK/lib/python3.14/site-packages'
python3 -c 'import pytest; print(pytest.__version__)'
python3 -m pytest -q -p no:cacheprovider --tb=short
```

If that cache has moved at execution time, resolve an available pytest environment before continuing; do not label a missing test runner as a feature failure. The prior 306 passes are historical. Capture a fresh baseline once, then run focused tests after each task and the full suite at integration. Observe each new behavior test fail before its implementation; use a temporary source snapshot to prove regression discrimination if necessary.

## Shared data and CLI contracts

These names are fixed for all tasks. All dictionaries are JSON-compatible; integer validators reject booleans. JSON decoding rejects duplicate keys and non-finite numbers. `canonical_bytes(value)` uses sorted keys, compact separators, `ensure_ascii=False`, `allow_nan=False`, and UTF-8.

```python
# Added to a state-mode goal; legacy goals need no new field.
execution = {
    "mode": "state", "schema_version": 1, "revision": 0,
    "task": {"summary": "", "entries": [],
             "next_action": "Inspect the project and establish the next useful increment."},
    "active_attempt": None,
    "attempts": [],
}

# An entry supplied by a worker. Accepted entries add provenance separately.
entry = {
    "id": "parser-empty-input", "kind": "observation",
    "statement": "The parser rejects an empty input before validation.",
    "evidence": [{"path": "evidence/parser-result.txt", "sha256": "0" * 64}],
}

# Concrete shape; values are checked against the actual attempt and files.
update = {
    "schema_version": 1, "attempt_id": "request-001", "base_revision": 1,
    "did": "Added a failing regression case for empty input.",
    "ops": [{"op": "set", "field": "next_action",
             "value": "Fix the parser's empty-input branch."}],
}
```

An attempt record contains `id`, `turn`, `base_revision`, `status`, `packet` (`path`, `sha256`), `procedure_sha256`, `worker` (null or `runtime`/`handle`), `gate_process` (null or diagnostic process metadata), `finish_nonce`, `update_sha256`, `pending`, `events`, and `result`. `pending` is initially null and, during finishing, contains content-addressed `update`, `candidate`, `audit`, and `worker_evidence` references; retain these references in terminal history. Status is exactly `reserved`, `attached`, `finishing`, `accepted`, or `recovered`. At most one nonterminal attempt exists and its ID must equal `active_attempt`. IDs match `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`. The request ID is the attempt ID, avoiding a second idempotency namespace.

Accepted entry provenance is `{"attempt_id": str, "tree_hash": str, "at": str}` and is required on persisted entries. Workers cannot supply that field. Unchanged entries preserve original provenance; only upserted entries receive new provenance. Diagnostic `gate_process` fields are exactly `pid`, `pgid`, `started_at`, and `finish_nonce`. Update `did` and archive/recovery reasons are nonempty strings limited to 1 KiB each. The initial create has revision 0; every subsequent state-mode lifecycle mutation increments it exactly once through the shared commit helper. Replaying a saved result does not increment it.

The four step commands use these arguments after the existing global options:

```text
start OBJECTIVE --execution state [existing gate/review/budget/watch options]
step-begin SLUG --request-id ID
step-attach SLUG --attempt ID --worker HANDLE --expected-revision N
step-finish SLUG --attempt ID --expected-revision N --update-file PATH
            --worker-evidence-file PATH [--timeout SECONDS] [--force]
step-recover SLUG --attempt ID --expected-revision N --reason TEXT
             --worker-state stopped|not-started --evidence-file PATH
```

`step-finish` requires a bound worker and evidence of its terminal state. Recovery evidence must describe both worker and gate liveness when a gate was launched. These are coordinator-supplied observations, not identity authentication. Never automatically signal a PID read from a saved record.

Every successful step response contains `slug`, `attempt_id`, `execution_revision`, `attempt_status`, `replayed`, `update_accepted`, `attempt_closed`, and `exit_code`; begin additionally returns `packet` and its path/digest. Finish/recovery include the usual compact goal status and checkpoint. The CLI uses the response's `exit_code`. Protocol errors exit 2 with `error`, `message`, and available attempt/revision identifiers. A rejection states `update_accepted: false` and does not mutate accepted task state. A duplicate in-progress finish is an explicit `StepBusy` error, not another gate run.

## Task 1: Serialize goal writes with process-owned locks

**Files:** Modify `harness/filelock.py`, goal lock call sites in `harness/goal.py`; extend `tests/test_filelock.py` and `tests/test_concurrency.py`.

**Interfaces:** Preserve `filelock.locked(target, timeout=10.0)` for Refine. Add `filelock.goal_locked(target: Path, timeout: float = 10.0) -> ContextManager[None]` and `filelock.state_supported() -> bool`. All goal creation and mutation paths use `goal_locked`, including `_with_lock`. The same primitive will hold a separate finish-runner ownership lock in Task 5.

- [ ] **Step 1: Capture the working baseline and add lock regression tests.** Use an actual second process, not two threads sharing a descriptor. Add this permission test and process cases asserting that an aged lock cannot be stolen and killing a holder releases it:

```python
def test_goal_lock_permission_error_never_enters(tmp_path, monkeypatch):
    import errno
    import filelock
    import pytest

    entered = []
    def denied(*args, **kwargs):
        raise PermissionError(errno.EACCES, "blocked fixture")
    monkeypatch.setattr(filelock.os, "open", denied)
    with pytest.raises(PermissionError):
        with filelock.goal_locked(tmp_path / "goal.json"):
            entered.append(True)
    assert entered == []
```

- [ ] **Step 2: Run the new tests and observe failure.** Run `python3 -m pytest tests/test_filelock.py tests/test_concurrency.py -q -p no:cacheprovider`. The new tests must expose the absent strict API or unwanted unlocked entry.

- [ ] **Step 3: Add the strict lock and route all goal lock sites to it.** Use a persistent lock inode; never unlink it on release. Implement the supported-platform check before creating a state-mode goal. On other platforms, legacy goal operations retain their prior lock behavior while state creation is refused.

```python
@contextmanager
def goal_locked(target: Path, timeout: float = 10.0):
    if not state_supported():
        with locked(target, timeout):
            yield
        return
    import fcntl
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    lock_path = target.parent / f".{target.name}.flock"
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    acquired = False
    try:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise LockTimeout(f"could not lock {target} within {timeout}s")
                time.sleep(POLL)
        yield
    finally:
        if acquired:
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def state_supported() -> bool:
    import sys
    return sys.platform == "darwin" or sys.platform.startswith("linux")
```

Do not change Refine's `locked` calls. The goal lock may not enclose model calls, commands, or tree hashing; Task 6 moves the existing completion/review hash checks out of their critical sections.

- [ ] **Step 4: Run focused tests.** Confirm the real-process tests preserve every checkpoint/create and that a lock waiter fails promptly on permission errors. Explicitly test release after a process is killed while holding the lock.
- [ ] **Step 5: Review and commit only this task's files.** Commit message: `fix(horizon): serialize goal writes with process-owned locks`.

## Task 2: Implement validated task state and bounded packets

**Files:** Create `harness/task_state.py`, `tests/test_task_state.py`.

**Interfaces:** All functions below belong to `task_state`. `StateError` derives from `ValueError`; field/size failures include the field and measured limit in their message.

```text
canonical_bytes(value: object) -> bytes
read_update(path: Path) -> dict
new_execution() -> dict
validate_execution(execution: dict) -> None
validate_task(task: dict) -> None
merge_update(task: dict, update: dict, *, attempt_id: str,
             base_revision: int, project: Path,
             provenance: dict) -> tuple[dict, list[dict]]
inspect_evidence(entry: dict, project: Path, current_hash: str) -> dict
procedure(runtime: str) -> dict
build_packet(goal_state: dict, *, attempt_id: str, base_revision: int,
             current_hash: str, runtime: str, reserve_verdicts: bool = False) -> dict
```

`procedure` returns the short worker instructions, schema version, and SHA-256 of those instructions plus the selected Horizon skill bytes. Resolve that skill from this shared checkout, not a guessed home path. `inspect_evidence` returns derived `freshness` (`current`, `stale`, or `unverified`) and specific reasons without rewriting persisted entries. `merge_update` returns the candidate task and an audit list of changed IDs, before/after values, and archive reasons. It never mutates its input.

- [ ] **Step 1: Write exact update-preservation and rejection tests.** Import the module under the test harness's source path. Include this case, then parameterize rejection of unknown keys, null values, duplicate IDs, contradictory operations, booleans as revisions, and immutable lifecycle fields:

```python
def test_update_preserves_omitted_fields_and_records_archives(tmp_path):
    from copy import deepcopy
    import task_state

    task = {"summary": "Parser investigation", "next_action": "Inspect branch",
            "entries": [{"id": "q1", "kind": "question", "statement": "Empty input?",
                         "evidence": [], "provenance": {"attempt_id": "r0",
                         "tree_hash": "a" * 64, "at": "2026-09-18T00:00:00Z"}}]}
    before = deepcopy(task)
    update = {"schema_version": 1, "attempt_id": "r1", "base_revision": 3,
              "did": "Resolved the input question.",
              "ops": [{"op": "archive", "id": "q1",
                       "reason": "Covered by the new regression test."}]}
    candidate, audit = task_state.merge_update(
        task, update, attempt_id="r1", base_revision=3, project=tmp_path,
        provenance={"attempt_id": "r1", "tree_hash": "a" * 64, "at": "2026-09-19T00:00:00Z"})
    assert task == before
    assert candidate["summary"] == before["summary"]
    assert candidate["next_action"] == before["next_action"]
    assert candidate["entries"] == []
    assert audit[0]["before"] == before["entries"][0]
    assert audit[0]["reason"] == "Covered by the new regression test."
```

Also test changed bytes with preserved mtime, missing evidence, symlink escape, overlong multi-byte text, duplicate JSON keys, and a packet exceeding 24 KiB despite valid individual fields. An observation/failed approach without evidence fails; a hypothesis/question may have no evidence. Evidence supplied on a new upsert must exist and match its hash. An unchanged historical entry may have stale or missing evidence and must survive with a derived warning.

- [ ] **Step 2: Run `python3 -m pytest tests/test_task_state.py -q -p no:cacheprovider` and observe the missing behavior.**
- [ ] **Step 3: Implement the module with exact limits and operations.** Define these constants and the serializer. Reject duplicate keys using `object_pairs_hook`; reject `NaN`/`Infinity` using `parse_constant`; read at most `UPDATE_LIMIT + 1` bytes before parsing. The supported operations are `set` for `summary` or `next_action`, `upsert` with one worker entry, and `archive` with ID/reason. Unknown operations are errors.

```python
import json

PACKET_LIMIT = 24 * 1024
UPDATE_LIMIT = 64 * 1024
SUMMARY_LIMIT = 2 * 1024
TEXT_LIMIT = 1024
EXCERPT_LIMIT = 2 * 1024
ENTRY_LIMIT = 64
EVIDENCE_LIMIT = 4


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def new_execution() -> dict:
    return {"mode": "state", "schema_version": 1, "revision": 0,
            "task": {"summary": "", "entries": [],
                     "next_action": "Inspect the project and establish the next useful increment."},
            "active_attempt": None, "attempts": []}


def validate_task(task: dict) -> None:
    if set(task) != {"summary", "entries", "next_action"}:
        raise StateError("task fields must be summary, entries, next_action")
    for key, limit in (("summary", SUMMARY_LIMIT), ("next_action", TEXT_LIMIT)):
        value = task[key]
        if not isinstance(value, str) or len(value.encode("utf-8")) > limit:
            raise StateError(f"task.{key} must fit {limit} UTF-8 bytes")
    if not task["next_action"].strip():
        raise StateError("task.next_action must be nonempty")
    if not isinstance(task["entries"], list) or len(task["entries"]) > ENTRY_LIMIT:
        raise StateError(f"task.entries must contain at most {ENTRY_LIMIT} entries")
    seen = set()
    for entry in task["entries"]:
        validate_entry(entry, accepted=True)
        if entry["id"] in seen:
            raise StateError(f"duplicate entry ID: {entry['id']}")
        seen.add(entry["id"])
```

Add private `validate_entry(entry: dict, *, accepted: bool) -> None`: check exact keys, ID syntax, kind, statement length, evidence cardinality and exact reference keys; require runtime-generated provenance on accepted entries and reject it on worker entries. New entries in `merge_update` call it with `accepted=False`. Resolve evidence with `Path.resolve(strict=True)`, require `is_relative_to(project.resolve())`, reject special files before reading, and stream SHA-256. Source references are never promoted to instructions.

Packet keys are `schema_version`, `attempt_id`, `base_revision`, `objective`, `gate`, `watch_paths`, `budget`, `used`, `procedure`, `task`, `snapshot`, `latest_checkpoint`, `latest_review`, and `audit_path`. Include only the current state and latest verification, not the attempt history. Label verdict excerpts with `truncated` and a record reference. When reserving space, substitute maximum-sized serialized excerpt slots and reserve room for freshness labels; test escaping/control characters so field bytes are not confused with serialized bytes. Never shorten objective/gate/watch paths or remove entries to meet the packet limit.

- [ ] **Step 4: Run the task-state tests and verify byte boundaries at limit, limit minus one, and limit plus one.** Confirm the input dictionary and accepted state remain byte-for-byte unchanged after rejected operations.
- [ ] **Step 5: Review and commit.** Commit message: `feat(horizon): validate compact task state and worker packets`.

## Task 3: Create and load versioned state-mode goals

**Files:** Modify `harness/goal.py`; extend `tests/test_goal.py`.

**Interfaces:** Add keyword-only `execution: str = "conversation"` to `goal.create` after its existing arguments. Add `goal.execution_mode(state: dict) -> str`, `goal.commit_revision(state: dict, *, expected_revision: int) -> dict`, `goal.require_idle(state: dict) -> None`, and `goal.summary(state: dict) -> dict`. `commit_revision` requires the caller to hold the strict goal lock and uses existing atomic `_save`; it checks the on-disk revision, validates the candidate execution block, increments once, and saves. `RevisionConflict` and `AttemptOpen` derive from `GoalStateError`.

- [ ] **Step 1: Add create/load and atomic-save tests.** Existing fixtures in `test_goal.py` provide `_make` and `isolated_goals`.

```python
def test_state_mode_is_explicit_and_does_not_rewrite_legacy():
    legacy = _make()
    assert "execution" not in legacy
    state = _make(execution="state")
    assert state["execution"]["mode"] == "state"
    assert state["execution"]["revision"] == 0
    assert state["used"]["turns"] == 0
    assert goal.load(legacy["slug"]) == legacy


def test_unknown_state_version_is_not_silently_loaded():
    import pytest
    state = _make(execution="state")
    state["execution"]["schema_version"] = 99
    goal._save(state)
    with pytest.raises(goal.GoalStateError, match="schema"):
        goal.load(state["slug"])
```

Add cases for unsupported platform, oversized immutable objective/criterion, malformed execution/attempt records, failed atomic replacement preserving the old goal, and legacy loads staying byte-identical.

- [ ] **Step 2: Run `python3 -m pytest tests/test_goal.py -q -p no:cacheprovider` and observe the absent mode/version behavior.**
- [ ] **Step 3: Extend creation and revisioned persistence.** Check mode and platform before creating directories. Build a provisional goal and size-check its initial packet outside the creation lock; inside the CREATE lock assign a unique slug and save once. Do not create a legacy goal and then attach execution state in a second write. Packet size validation uses a maximum-length legal attempt ID and reserved verdict slots.

```python
def execution_mode(state: dict) -> str:
    block = state.get("execution")
    return "conversation" if block is None else block["mode"]


def require_idle(state: dict) -> None:
    if execution_mode(state) == "state" and state["execution"]["active_attempt"] is not None:
        raise AttemptOpen("Close or recover the active attempt first")


def commit_revision(state: dict, *, expected_revision: int) -> dict:
    current = load(state["slug"])
    if current["execution"]["revision"] != expected_revision:
        raise RevisionConflict("Execution revision changed; inspect status")
    candidate = copy.deepcopy(state)
    candidate["execution"]["revision"] = expected_revision + 1
    task_state.validate_execution(candidate["execution"])
    return _save(candidate)
```

Imports are `copy` and `task_state`; `load` translates `StateError` into `GoalStateError`. For state mode it also checks that `used.turns` equals the number of reserved attempts, attempt turn numbers are sequential and unique, and terminal attempts correspond one-to-one with checkpoint turn numbers. An open attempt has no checkpoint. `_save` stays a private filesystem primitive and does not itself invent revisions. Never expose it through the CLI. Task 6 routes the remaining public mutators through the revision helper.

Move the current `cli.status` representation into `goal.summary` as a shared formatter, adding the mode fields without importing `cli` into `goal`:

```python
def summary(state: dict) -> dict:
    last = state["checkpoints"][-1] if state["checkpoints"] else None
    block = state.get("execution")
    active = None
    if block and block["active_attempt"] is not None:
        item = next(a for a in block["attempts"] if a["id"] == block["active_attempt"])
        active = {key: item[key] for key in ("id", "status", "worker", "packet")}
    return {
        "slug": state["slug"], "goal": state["goal"], "status": state["status"],
        "runtime": paths.runtime(), "gate": state["gate"], "budget": state["budget"],
        "used": state["used"], "budget_exhausted": budget_exhausted(state),
        "last_checkpoint": last, "reviews": state.get("reviews", [])[-1:],
        "gate_evidence": state.get("gate_evidence"),
        "execution_mode": execution_mode(state),
        "turn_unit": "worker_attempt" if block else "checkpoint",
        "execution_revision": block["revision"] if block else None,
        "active_attempt": active,
        "next_action": block["task"]["next_action"] if block else (last.get("next", "") if last else ""),
    }
```

- [ ] **Step 4: Run goal and task-state tests.** Inspect an actual temporary JSON file for the single-write create contract.
- [ ] **Step 5: Review and commit.** Commit message: `feat(horizon): persist explicit versioned execution mode`.

## Task 4: Reserve attempts and bind fresh workers

**Files:** Create `harness/steps.py`, `tests/conftest.py`, `tests/test_horizon_steps.py`; extend `tests/test_tree_hash.py`.

**Interfaces:** Add `steps.begin(slug: str, request_id: str) -> dict`, `steps.attach(slug: str, attempt_id: str, worker: str, *, expected_revision: int) -> dict`, and `steps.report(state: dict, attempt: dict, *, replayed: bool = False) -> dict`. `report` combines `goal.summary` with the shared step fields and saved result; a replay retains its original attempt outcome even if a later independent review changed the goal's latest checkpoint. Define `StepError` as a `GoalStateError` subclass and `StepBusy`/`StepConflict` as its subclasses. Private `steps.store_blob(slug: str, kind: str, payload: bytes) -> dict` and `steps.read_blob(ref: dict, project: Path) -> bytes` write/read content-addressed supporting files and verify SHA-256; the reference is `{"path": project_relative_path, "sha256": digest}`.

- [ ] **Step 1: Add an isolated state-goal fixture and reservation tests.** Configure both runtime markers without changing the user's real home. Use this fixture in the new test files:

```python
# tests/conftest.py
import sys
from pathlib import Path
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))


@pytest.fixture(params=["codex", "claude"])
def make_state_goal(tmp_path, monkeypatch, request):
    import goal
    project = tmp_path / "working project "
    project.mkdir()
    (project / (".agents" if request.param == "codex" else ".claude")).mkdir()
    monkeypatch.chdir(project)
    monkeypatch.setenv("CONTINUAL_HARNESS_RUNTIME", request.param)
    def make(**overrides):
        args = {"goal": "Repair the parser", "cwd": str(project),
                "gate_kind": "command", "gate_value": "true",
                "max_turns": 2, "execution": "state"}
        args.update(overrides)
        return goal.create(**args)
    return make


def test_begin_and_attach_replay_without_spending_another_turn(make_state_goal):
    import goal
    import steps
    state = make_state_goal(max_turns=1)
    begun = steps.begin(state["slug"], "r1")
    again = steps.begin(state["slug"], "r1")
    assert again["replayed"] is True
    assert again["packet"] == begun["packet"]
    attached = steps.attach(state["slug"], "r1", "worker-fixture",
                            expected_revision=begun["execution_revision"])
    replay = steps.attach(state["slug"], "r1", "worker-fixture",
                          expected_revision=begun["execution_revision"])
    assert replay["execution_revision"] == attached["execution_revision"]
    assert goal.load(state["slug"])["used"]["turns"] == 1
    assert begun["packet"]["base_revision"] < attached["execution_revision"]
```

The test function belongs in `test_horizon_steps.py`; the fixture belongs in `conftest.py`. Add failures for a second request while an attempt is open, spent/expired budget, a conflicting handle, unsupported procedure changes, and missing/corrupt saved packet bytes. Hashes must stay identical when only goal bookkeeping is written.

- [ ] **Step 2: Run `python3 -m pytest tests/test_horizon_steps.py tests/test_tree_hash.py -q -p no:cacheprovider` and observe absent reservation/binding behavior.**
- [ ] **Step 3: Implement reservation and binding.** First look for an existing request ID and return its stored packet/result before preparing any new packet. For a new request, build a packet against a loaded snapshot outside the goal lock. Use the predicted next revision as its immutable base revision. Write its bytes under `goals/<slug>.d/objects/<sha256>.json` with a unique temporary file and atomic replacement; two writers producing the same digest must produce the same bytes. Then enter the goal lock, reload, check again for an existing request ID before admission checks, compare the revision, reserve one turn, and commit. The relevant transition is:

```python
attempt = {
    "id": request_id, "turn": state["used"]["turns"] + 1,
    "base_revision": state["execution"]["revision"] + 1,
    "status": "reserved", "packet": packet_ref,
    "procedure_sha256": packet["procedure"]["sha256"],
    "worker": None, "gate_process": None, "finish_nonce": None,
    "update_sha256": None, "pending": None, "events": [], "result": None,
}
state["used"]["turns"] = attempt["turn"]
state["execution"]["active_attempt"] = request_id
state["execution"]["attempts"].append(attempt)
state = goal.commit_revision(state, expected_revision=expected_revision)
```

Variables in this block are local to `begin`: `state` is the reloaded record, `expected_revision` is the preflight revision, `packet` is from `build_packet`, and `packet_ref` is from `store_blob`. Validate snapshot/procedure/packet identity before dispatch; if preparation becomes stale, return `RevisionConflict` rather than silently rebuilding and consuming work. A failed preflight never charges a turn. A committed reservation is never refunded.

`attach` records `{"runtime": paths.runtime(), "handle": worker}` and transitions reserved to attached. It increments the execution revision, preserving the saved packet base revision. A repeated binding to the same handle returns the saved/current outcome before checking the old expected revision; a different binding is an error. Neither command invokes a host worker tool.

- [ ] **Step 4: Run the focused tests and inspect saved packet references/digests.** Verify request replay after goal completion is also read-only once Tasks 5–6 are integrated.
- [ ] **Step 5: Review and commit.** Commit message: `feat(horizon): reserve durable worker attempts before dispatch`.

## Task 5: Finish one attempt through the existing gate

**Files:** Modify `harness/steps.py`, `harness/gate.py`; extend `tests/test_horizon_steps.py`, `tests/test_gate.py`.

**Interfaces:** Add `steps.finish(slug: str, attempt_id: str, *, expected_revision: int, update_file: Path, worker_evidence_file: Path, timeout: int = gate.DEFAULT_TIMEOUT, force: bool = False) -> dict`. Extend both `gate.run_gate` and `gate.run_gate_at` with a keyword-only `on_spawn: Callable[[subprocess.Popen], None] | None = None`; preserve their current positional arguments and return types. Add private `gate._stop_owned_process(proc: subprocess.Popen) -> None` for callback failure/timeout cleanup using only the live handle owned by that call.

- [ ] **Step 1: Add the real gate replay test.** The counter lives outside the watched project, so a second invocation would be visible even when artifact hashes are unchanged.

```python
def test_finish_is_replayable_and_charges_only_the_reserved_turn(make_state_goal, tmp_path):
    import json
    import shlex
    import goal
    import steps

    counter = tmp_path / "gate-counter"
    state = make_state_goal(max_turns=1,
                           gate_value="printf x >> " + shlex.quote(str(counter)))
    begun = steps.begin(state["slug"], "r1")
    attached = steps.attach(state["slug"], "r1", "worker-fixture",
                            expected_revision=begun["execution_revision"])
    update_file = tmp_path / "update.json"
    update_file.write_text(json.dumps({
        "schema_version": 1, "attempt_id": "r1",
        "base_revision": begun["packet"]["base_revision"],
        "did": "Inspected the stable project and requested its acceptance check.",
        "ops": [{"op": "set", "field": "next_action", "value": "Review the result."}],
    }))
    evidence = tmp_path / "worker-stopped.txt"
    evidence.write_text("The fixture host reports worker-fixture completed and released its workspace.")
    kwargs = {"expected_revision": attached["execution_revision"],
              "update_file": update_file, "worker_evidence_file": evidence}
    result = steps.finish(state["slug"], "r1", **kwargs)
    replay = steps.finish(state["slug"], "r1", **kwargs)
    assert result["exit_code"] == 0
    assert result["attempt_closed"] is True
    assert replay["replayed"] is True
    assert counter.read_text() == "x"
    saved = goal.load(state["slug"])
    assert saved["used"]["turns"] == 1
    assert len(saved["checkpoints"]) == 1
    assert saved["checkpoints"][0]["turn"] == 1
```

Add cases for changed update on replay; invalid update leaving the attempt attached; command failure accepting useful state; missing command/timeout/unknown hash saving a failed checkpoint; coverage awaiting review; finish after deadline; and a gate modifying watched artifacts retaining its pre-gate hash. Add a callback-failure test proving its live subprocess is reaped.

- [ ] **Step 2: Run `python3 -m pytest tests/test_horizon_steps.py tests/test_gate.py -q -p no:cacheprovider` and observe the absent finish/callback behavior.**
- [ ] **Step 3: Add the gate callback without changing verdict rules.** Invoke the callback immediately after `Popen` succeeds. If it raises, stop/reap the owned process and raise `GateUnrunnable`; a failure to save process metadata must not leave the CLI treating that command as successfully tracked.

```python
# In run_gate, immediately after Popen; proc is the current call's live handle.
if on_spawn is not None:
    try:
        on_spawn(proc)
    except Exception as exc:
        _stop_owned_process(proc)
        raise GateUnrunnable(f"Could not record the running gate: {exc}") from exc
```

Pass `on_spawn` through `run_gate_at`. Keep the current command skip/force logic, timeout default, coverage exception, and **pre-gate** fingerprint. Diagnostic process metadata includes PID, process group, timestamp, and the finishing nonce; it is never an instruction to kill that PID in another session.

- [ ] **Step 4: Implement finish with preflight, an ownership guard, and one terminal commit.** Acquire a separate per-attempt runner lock with `timeout=0`, translating lock contention to `StepBusy`; then read/validate the update (canonical digest), worker terminal evidence, procedure, saved packet, and candidate state. This runner lock protects one finish invocation; it is distinct from the short goal-state write lock. Preflight reads/hashes and the gate run occur outside the goal-state lock.

First handle terminal replay. Otherwise require attached status and the supplied current revision. Persist `status="finishing"`, the canonical update digest, a random `finish_nonce`, and references to the candidate/audit/evidence before running the gate. In the spawn callback, briefly acquire the goal lock and save process metadata only if the nonce still matches. Track the updated execution revision returned by that metadata commit.

After the gate, under the goal lock, require the same nonce and latest finishing revision. Build the checkpoint using the reserved turn and gate's returned fingerprint, then commit the candidate task, audit event, checkpoint, and terminal attempt in one atomic save:

```python
# Locals: state/attempt are reloaded under lock; candidate and audit came from
# merge_update; result/output/digest came from the gate or its recorded error.
checkpoint = {"turn": attempt["turn"], "at": goal._now(), "did": update["did"],
              "next": candidate["next_action"], "tree_hash": digest,
              "gate": result, "gate_output": goal._bounded(output),
              "gate_spec": dict(state["gate"])}
state["checkpoints"].append(checkpoint)
state["execution"]["task"] = candidate
state["execution"]["active_attempt"] = None
attempt["status"] = "accepted"
attempt["events"].append({"type": "accepted_update", "at": goal._now(),
                          "changes": audit, "update": update_ref})
attempt["result"] = {"exit_code": exit_code, "checkpoint": checkpoint,
                      "update_accepted": True, "attempt_closed": True}
state = goal.commit_revision(state, expected_revision=finishing_revision)
```

`update_ref` is the stored original update; `exit_code` is 0 for command pass, 1 for failed/skipped/coverage awaiting review, 2 for gate execution/fingerprint error. Add the diagnostic `error` string to the saved result when exit code is 2. Include `review_required` based on the configured gate/review criterion and accepted checkpoint. `finishing_revision` is refreshed only by this finish's own process-metadata commit. Coverage records fail/awaiting-review until the existing reviewer updates it. Never call the legacy `goal.checkpoint` from finish: it would charge the attempt again.

Preserve historical evidence provenance if the gate itself changes a source file; completion must reject the resulting stale checkpoint and the next packet must label old entries stale. Store supplied terminal evidence under the attempt's audit directory. It is an operational assertion by the coordinator, not verified model identity. Rejecting an update must not publish the candidate or rewrite active entries.

- [ ] **Step 5: Run the tests.** Verify raw counter bytes, goal JSON, and pre-gate hash agreement rather than trusting the step report alone. Confirm timeout and callback exceptions retain honest error outcomes.
- [ ] **Step 6: Review and commit.** Commit message: `feat(horizon): atomically finish attempts with replay-safe verification`.

## Task 6: Recover interruptions and close lifecycle bypasses

**Files:** Modify `harness/steps.py`, `harness/goal.py`; extend `tests/test_horizon_steps.py`, `tests/test_goal.py`.

**Interfaces:** Add `steps.recover(slug: str, attempt_id: str, *, expected_revision: int, reason: str, worker_state: str, evidence_file: Path) -> dict`. Add private `goal._persist_mutation_locked(state: dict) -> dict` for legacy/state saves and `goal._snapshot_transaction(slug: str, apply: Callable[[dict, str], None]) -> dict` for review/completion. Public `record_review`, `complete`, `abandon`, `reopen`, and `add_subagent` retain their signatures. Add a state-mode refusal to the public `checkpoint` API as well as its CLI caller.

- [ ] **Step 1: Add recovery and bypass tests.** This test proves that interruption neither restores budget nor discards workspace edits:

```python
def test_recovery_keeps_work_and_spends_the_attempt(make_state_goal, tmp_path):
    from pathlib import Path
    import goal
    import steps
    import pytest

    state = make_state_goal(max_turns=1)
    begun = steps.begin(state["slug"], "r1")
    attached = steps.attach(state["slug"], "r1", "worker-fixture",
                            expected_revision=begun["execution_revision"])
    artifact = Path(state["cwd"]) / "partial.txt"
    artifact.write_text("Preserve this partial result.\n")
    evidence = tmp_path / "stopped.txt"
    evidence.write_text("The fixture host confirms worker-fixture stopped; no gate was launched.")
    kwargs = {"expected_revision": attached["execution_revision"],
              "reason": "Worker stopped before returning its update.",
              "worker_state": "stopped", "evidence_file": evidence}
    recovered = steps.recover(state["slug"], "r1", **kwargs)
    replay = steps.recover(state["slug"], "r1", **kwargs)
    assert recovered["exit_code"] == 1
    assert replay["replayed"] is True
    assert artifact.read_text() == "Preserve this partial result.\n"
    saved = goal.load(state["slug"])
    assert saved["used"]["turns"] == 1
    assert saved["execution"]["active_attempt"] is None
    assert saved["checkpoints"][-1]["gate"] == "fail"
    with pytest.raises(goal.BudgetExhausted):
        steps.begin(state["slug"], "r2")
```

Add tests that open attempts block review, completion, abandon, and legacy checkpoint/add-subagent calls; that a stopped recovery is required before abandon/reopen; and that required review cannot be forged through an update. An accepted attempt cannot later be recovered. A repeated recovery may return its terminal result but must reject a contradictory worker identity/reason/evidence digest.

- [ ] **Step 2: Run `python3 -m pytest tests/test_horizon_steps.py tests/test_goal.py -q -p no:cacheprovider` and observe the missing guards/recovery behavior.**
- [ ] **Step 3: Implement recovery.** Require nonempty reason and substantive liveness evidence. `not-started` is allowed only for a reserved attempt with no bound worker and no gate record; the evidence must also resolve the dispatch-before-binding uncertainty. For attached/finishing attempts require `stopped`. If a finish runner still holds its ownership lock, return `StepBusy`. If gate liveness is unresolved, leave the attempt open; do not infer that a saved PID disappearing proves all descendants stopped.

After validating evidence outside the goal lock, commit this recovery transition under revision/attempt checks:

```python
task = state["execution"]["task"]
task["next_action"] = "Inspect the current workspace and saved evidence before continuing."
checkpoint = {"turn": attempt["turn"], "at": goal._now(),
              "did": "Recovered interrupted attempt: " + reason,
              "next": task["next_action"], "tree_hash": observed_hash,
              "gate": "fail", "gate_output": "Interrupted work; no accepted verification.",
              "gate_spec": dict(state["gate"])}
state["checkpoints"].append(checkpoint)
state["execution"]["active_attempt"] = None
attempt["status"] = "recovered"
attempt["events"].append({"type": "recovery", "reason": reason,
                          "worker_state": worker_state, "evidence": evidence_ref})
attempt["result"] = {"exit_code": 1, "checkpoint": checkpoint,
                      "update_accepted": False, "attempt_closed": True}
state = goal.commit_revision(state, expected_revision=expected_revision)
```

`observed_hash` is computed outside the lock, or `gate.UNKNOWN_HASH` if unreadable. `evidence_ref` comes from `store_blob`. Never accept a worker candidate as part of recovery; retain original summary/entries and append an explicit next-action audit change.

- [ ] **Step 4: Route every public goal writer through the mode guards and revision protocol.** `_persist_mutation_locked` calls `commit_revision` for state goals and `_save` for legacy goals. Add `require_idle` before all ordinary state-mode lifecycle mutations; the step functions own active attempts. Keep `reopen` restricted to abandoned goals, as in the current implementation.

Move completion/review fingerprint work outside the goal-state lock with this transaction pattern. The callback must perform the existing semantic checks and mutate the supplied record without doing I/O or saving it:

```python
def _snapshot_transaction(slug: str, apply) -> dict:
    before = load(slug)
    require_idle(before)
    try:
        observed = tree_hash.compute(before["cwd"], before.get("watch_paths"))
    except Exception as exc:
        raise GateNotPassed(f"Cannot verify the current snapshot: {exc}") from exc
    with filelock.goal_locked(_path(slug)):
        current = load(slug)
        if task_state.canonical_bytes(current) != task_state.canonical_bytes(before):
            raise RevisionConflict("Goal changed while its artifacts were checked")
        require_idle(current)
        apply(current, observed)
        return _persist_mutation_locked(current)
```

Extract the current completion and review checks into private `goal._apply_completion(state: dict, observed_hash: str, gate_output: str) -> None` and `goal._apply_review(state: dict, observed_hash: str, *, result: str, evidence: str, judge: str, digest: str) -> None`. Preserve gate-spec, latest-turn, judge/evidence, last-review verdict, and current-snapshot comparisons. Preserve the existing public call signatures by closing over their arguments. Compare against the supplied observed hash instead of calling `_assert_current_snapshot` under the lock. No change can make a failed exploratory review satisfy a completion requirement.

- [ ] **Step 5: Run focused lifecycle tests.** Include a concurrent state mutation during hashing to prove completion rejects a changed goal. Recheck legacy command-only, command-plus-review, coverage, abandon, and reopen cases.
- [ ] **Step 6: Review and commit.** Commit message: `feat(horizon): recover interrupted attempts without weakening lifecycle gates`.

## Task 7: Expose the protocol through both real CLI entry points

**Files:** Modify `harness/cli.py`, `tests/conftest.py`, `tests/test_horizon_steps.py`, `tests/test_skill_contract.py`.

**Interfaces:** Existing global options and command names remain unchanged. `cli.status(state)` delegates to `goal.summary(state)` so `steps.report` and the CLI share one compact representation. Doctor adds platform support and states that worker availability is determined by the host, not the Python CLI.

- [ ] **Step 1: Add a real CLI fixture to `conftest.py` and command-flow tests.** Return the project, runtime, and a callable that starts a new process through the actual skill symlink. The fixture uses an isolated home and always passes the project explicitly:

```python
@pytest.fixture(params=["codex", "claude"])
def state_client(tmp_path, request):
    import json
    import os
    import subprocess
    project = tmp_path / "cli project "
    project.mkdir()
    runtime = request.param
    folder = ".agents" if runtime == "codex" else ".claude"
    (project / folder).mkdir()
    isolated_home = tmp_path / "isolated home"
    isolated_home.mkdir()
    env = dict(os.environ, HOME=str(isolated_home),
               CODEX_HOME=str(isolated_home / ".codex"), PYTHONDONTWRITEBYTECODE="1")
    entrypoint = ROOT / folder / "skills/horizon/scripts/harness.py"
    def run(*args):
        proc = subprocess.run(
            [sys.executable, str(entrypoint), "--runtime", runtime,
             "--project", str(project), *args], cwd=project, env=env,
            capture_output=True, text=True, timeout=10)
        return proc.returncode, json.loads(proc.stdout or proc.stderr)
    return project, runtime, run


def test_real_cli_reserves_before_work_and_refuses_legacy_check(state_client):
    project, runtime, run = state_client
    code, state = run("start", "Create a ready marker", "--execution", "state",
                      "--gate", "test -f ready.txt", "--max-turns", "1")
    assert code == 0, state
    code, begun = run("step-begin", state["slug"], "--request-id", "r1")
    assert code == 0, begun
    assert not (project / "ready.txt").exists()
    assert run("status", state["slug"])[1]["used"]["turns"] == 1
    code, error = run("check", state["slug"], "--did", "Unreserved work", "--next", "Continue")
    assert code == 2
    assert "step" in error["message"].lower()
```

Place the test function in `test_horizon_steps.py`. Extend the CLI test with attach, an actual ready marker, update/terminal evidence files outside the watched project, finish, required review, and complete. Use the exact shared update shape and arguments above. Test every argument form through the real parser, including invalid nonpositive timeout and stale expected revision.

- [ ] **Step 2: Run `python3 -m pytest tests/test_horizon_steps.py tests/test_skill_contract.py -q -p no:cacheprovider` and observe the missing CLI arguments/commands.**
- [ ] **Step 3: Add parser arguments and thin dispatch.** Use `Path` for file arguments and `int` for revision/timeout. Keep mode-specific logic inside `steps`/`goal`. The parser additions for beginning and binding are:

```python
start.add_argument("--execution", choices=("conversation", "state"), default="conversation")
begin = commands.add_parser("step-begin", help="Reserve one state-mode worker attempt")
begin.add_argument("slug")
begin.add_argument("--request-id", required=True)
attach = commands.add_parser("step-attach", help="Bind a real host worker handle")
attach.add_argument("slug")
attach.add_argument("--attempt", required=True)
attach.add_argument("--worker", required=True)
attach.add_argument("--expected-revision", required=True, type=int)
```

Add `step-finish` and `step-recover` with the exact shared CLI contract. Dispatch forwards parsed values and returns `(report, report["exit_code"])`. Start forwards `execution=args.execution`. Preserve error exit 2 and print structured protocol fields when a `StepError` exposes them. Rejected step commands include `update_accepted: false`; catch schema/file/revision errors at the step boundary and attach known attempt/revision fields without fabricating an outcome when state cannot be loaded. `goal.summary` already supplies the mode fields from Task 3; `cli.status` now delegates to it. `step-begin` alone includes the bounded packet contents.

Add a pre-gate refusal to `cli.check` for state-mode goals so an obsolete command cannot run a gate before discovering the mode mismatch. Public `goal.checkpoint` remains guarded independently.

- [ ] **Step 4: Run both runtime entry points through the full CLI test cycle.** Verify failure, skipped gate, external-input force, coverage/review, stale artifacts, replay, recovery, and unknown schema. Confirm doctor remains read-only, including when asked about state support.
- [ ] **Step 5: Review and commit.** Commit message: `feat(cli): expose Horizon state-mode step commands`.

## Task 8: Teach the separate skills to coordinate the shared protocol

**Files:** Modify `.agents/skills/horizon/SKILL.md`, `.claude/skills/horizon/SKILL.md`, `docs/harness-cli.md`, `README.md`, and `tests/test_skill_contract.py`.

**Interfaces:** Both skills continue using their own `scripts/harness.py` and `references/commands.md` links. The user-visible opt-in is “use Horizon state mode.” Detailed schemas live in the shared reference; each skill's instructions name only host tools relevant to its runtime.

- [ ] **Step 1: Add contract checks for the documented mode examples.** Extend the existing parser-backed example test to cover state commands in the shared CLI reference for both runtimes. Retain resolvable-link/symlink checks. Before parsing, substitute the fixture's actual runtime and integer revision variables so shell-variable examples exercise argparse's real types:

```python
from string import Template
expanded = Template(line).safe_substitute(
    RUNTIME=runtime, CURRENT_REVISION="2", PACKET_REVISION="1")
args = cli.parser().parse_args(shlex.split(expanded)[2:])
assert args.runtime == runtime
```

Here `line` is one extracted `python3 "$HARNESS"` command from the existing fenced-example loop and `runtime` is its parameterized fixture. A documented example must parse with the actual CLI, not a text-pattern substitute. Record a behavioral baseline with the old skill before editing it: present a state-mode coding handoff request in a disposable project and save the worker's actual tool calls and resulting goal records. Do not call missing mode support a model failure.
- [ ] **Step 2: Run `python3 -m pytest tests/test_skill_contract.py -q -p no:cacheprovider` and record which new examples/instructions are absent.**
- [ ] **Step 3: Add the shared instructions and runtime-specific dispatch paragraphs.** Use this shared behavior in both skills, adjusting existing text to retain a short entry point:

```markdown
## Optional state mode

Use state mode when the user requests it. Add `--execution state` at goal creation;
existing goals keep their original mode. Read `status` before dispatching work.
For state goals, follow `step-begin`, fresh worker dispatch, `step-attach`, and
`step-finish` in the shared CLI reference. The reservation counts against the budget.

Give each worker the saved packet and its referenced artifacts. Its role is to
perform one increment and return the update JSON; the coordinator owns lifecycle
commands. Do not copy this conversation into the worker. Bind the actual returned
handle immediately. Resume by inspecting that handle, not by launching a duplicate.

After the worker stops, inspect its evidence and proposed changes, save its terminal
status evidence, and finish the attempt. Allow at most one formatting-only repair
request to that worker. Further implementation requires another reserved attempt.
Use existing independent review and completion checks after the attempt closes.

If interrupted, establish worker and gate liveness before `step-recover`. Preserve
workspace edits and inspect them on the next increment. Never kill a process using
only a PID saved by a previous session. Missing fresh-worker tools or uncertain
liveness must be reported; neither is permission to start duplicate work.
```

Codex dispatch paragraph:

```markdown
In Codex, dispatch a new `collaboration.spawn_agent` with `fork_turns: "none"` for
each reserved increment. Give the packet in its task message and retain the returned
agent handle. Follow the user's model preference and use host status/wait tools to
establish that the worker stopped before finishing. The Python CLI cannot dispatch
or inspect native workers for you.
```

Claude dispatch paragraph:

```markdown
In Claude Code, use a fresh supported `Agent` invocation with a general-purpose
worker for each reserved increment. Retain its actual host handle and terminal
result. Follow the user's model preference. If the current Agent interface cannot
provide the required fresh context or observable lifecycle, report that state mode
is unavailable in that session.
```

Keep the fresh independent reviewer separate from the editing worker. A packet's instruction to return an update must not cause recursive Horizon goal creation inside the worker. Neither skill creates a native goal, task, schedule, or API client merely because state mode is selected.

Add a complete shared CLI example using the exact four commands, a valid update JSON file, explicit terminal/recovery evidence, and the worker's packet base revision versus the current execution revision. Document the five attempt statuses, replay results, byte limits, archived entry retrieval by file reference, stale evidence, opt-in compatibility, and the absence of automatic transcript export. Replace the README's unconditional “Each skill is under 100 lines” claim if the resulting measured line count no longer supports it; prefer concise instructions linked to the reference over duplicating the schema.

- [ ] **Step 4: Run a Codex smoke handoff and a native Claude smoke handoff.** Use a tiny parser bug with a real failing test, two increments, one fresh worker per increment, and a separate reviewer if configured. Inspect the packet, host handle, changes, goal JSON, and final acceptance check independently. Do not count Claude CLI routing exercised by a Codex worker as a native Claude result. If native Claude is unavailable, save the attempted capability check and mark support experimental in the report.
- [ ] **Step 5: Run contract tests and validate the two edited skills.** Confirm their existing entrypoint/reference links still resolve. Do not install them globally.
- [ ] **Step 6: Review and commit.** Commit message: `docs(skills): coordinate fresh Horizon workers in Codex and Claude`.

## Task 9: Exercise real concurrency and interruption boundaries

**Files:** Create `tests/test_state_concurrency.py`; extend `tests/test_horizon_steps.py`, `tests/test_gate.py`, and `tests/test_tree_hash.py` where existing fixtures are applicable.

**Interfaces:** Tests use the public Python or CLI protocol. Subprocess tests may monkeypatch `_save`, the gate callback, or a callable in the child process to pause at a known boundary; do not add production environment-variable failure switches. Every spawned process has bounded waits and cleanup through its current live process handle.

- [ ] **Step 1: Add the adversarial matrix below.** Use a child marker file or pipe handshake so crashes occur at the intended boundary; a timed sleep alone is not proof that the process reached that point.

| Scenario | Disk assertion |
| --- | --- |
| Two distinct begin requests race | One active attempt, one spent turn; loser reports busy/conflict |
| Two identical begin requests race | One reservation and identical stored packet; no extra charge |
| Duplicate finish while a gate is running | Second invocation cannot start another gate |
| Recovery while the finish runner is live | Refused without changing accepted state |
| Crash after reservation, before dispatch | Charged reservation survives; recovery requires no-launch evidence |
| Crash after artifact edit, before update | Artifact survives; old state preserved and marked stale |
| Crash after finishing marker, before Popen | No fabricated checkpoint; recovery records failure |
| Crash after Popen, before handle persistence | Report uncertainty; no blind reaper or automatic duplicate |
| Crash during the gate | Saved PID is diagnostic only; test reaps its own live handle |
| Crash after gate return, before atomic commit | Old state remains authoritative; uncertain result cannot complete |
| Crash after atomic commit, before response | Replayed finish does not rerun command or append checkpoint |
| Corrupt packet/update/goal or unknown schema | No guessed state, partial merge, or auto-created replacement goal |
| Oversized update and explicit archive | Oversize rejected; successful archive retains old value/reason |
| Attempt to overwrite gate/review/budget/watch list | Rejected and immutable fields unchanged |
| Harness bookkeeping versus real source edits | Bookkeeping hash-neutral; real source changes invalidate checks |

For the duplicate-finish test, make the gate wait on a release file and append one byte to an external counter when it actually starts. Launch a second finish only after observing the start marker. Release the first gate, collect both processes, and require the counter to contain exactly one byte. This distinguishes an idempotent protocol from two fast sequential gates.

```python
# Child-process gate body; the test passes actual absolute paths as argv.
import sys
import time
from pathlib import Path

started, release, counter = map(Path, sys.argv[1:4])
with counter.open("ab") as stream:
    stream.write(b"x")
started.write_text("running")
deadline = time.monotonic() + 5
while not release.exists():
    if time.monotonic() >= deadline:
        raise SystemExit("fixture release timed out")
    time.sleep(0.01)
```

- [ ] **Step 2: Run each new test against the relevant pre-fix snapshot and confirm its assertion fails for the intended reason.** Existing tests passing are not evidence that a new crash case discriminates. Limit reruns to new cases and fixes.
- [ ] **Step 3: Fix protocol defects exposed by those tests in the responsible module.** The approved transitions in Tasks 4–6 are the contract; do not weaken tests by dropping crash cases or silently increasing budgets. Inspect shared locking, revision matching, and terminal replay first.
- [ ] **Step 4: Run the integrated suite once fixes are complete.** Run `python3 -m pytest -q -p no:cacheprovider --tb=short`. Save the actual count, interpreter, command, source fingerprints, and all failures. Run both CLI doctor/command flows and check `git diff --check`. Do not rerun the entire suite repeatedly without a new change or unresolved failure.
- [ ] **Step 5: Review and commit.** Commit message: `test(horizon): verify state-mode concurrency and crash recovery`.

## Task 10: Freeze and run the Sol/Terra comparative pilot

**Files:** Create `docs/evaluations/horizon-state-pilot/README.md`, `fixtures.md`, `manifest.csv`, `results.jsonl`, `report.md`, and per-run evidence directories. These are evaluation artifacts; no model API runner is added to the core.

**Interfaces:** A run is identified by `fixture`, `model`, `mode`, and `repeat`. Models are `gpt-5.6-sol` and `gpt-5.6-terra`, using the same available reasoning setting (high) in each paired comparison. Modes are conversation/state. Use three repetitions of six fixtures: 72 goal runs. Each run has four increments/attempts and a 15-minute work allowance. Any tighter user limit takes precedence and is recorded as a protocol deviation.

- [ ] **Step 1: Freeze six concrete fixtures before tuning.** Use Python standard-library `unittest` as each fixture's acceptance command so worker projects need no installed package. Store exact source/test bytes and SHA-256 in `fixtures.md` and the run evidence. The seed behavior and required assertions are:

| Fixture | Seed and requested change | Acceptance assertions | Split |
| --- | --- | --- | --- |
| `module-bug` | `pricing.py` subtracts a discount twice; `checkout.py` sums discounted item prices; fix both integration behavior and the underlying bug | `[100, 200]` at 10% yields 270, empty cart yields 0, zero discount preserves total | tuning |
| `api-refactor` | Rename `normalize` to `normalize_name` in `names.py`, update its two project callers, retain `normalize` as a compatibility alias | Both names trim/case-fold the same string, both callers work, existing imports remain valid | tuning |
| `test-repair` | `parse_count` calls `int(value)` and accepts booleans/negative values; add correct validation with regression tests | Positive decimal strings and zero pass; booleans, negative values, and malformed strings raise `ValueError`; no tests are removed | tuning |
| `checkpoint-handoff` | Provide `foundation.py` with a completed slug function; add `delivery.py` exposing a list transformation | Correct mapped output and empty-list output; foundation bytes and mtime remain unchanged across a forced fresh-coordinator handoff | held out |
| `interrupted-edit` | Seed a CSV renderer that fails to escape embedded commas; stop the first worker after a source edit and before its checkpoint | Correct quoting and empty output; edits survive recovery; no duplicate reservation/gate and no false completion | held out |
| `stale-fact` | Fixed goal: make a consumer follow the parser's current behavior. Record the parser's old empty-input behavior, then change that dependency before the next worker | Consumer matches the changed parser, valid numbers still parse, stale state is not promoted as current evidence; acceptance files stay unchanged | held out |

Fixture modules and tests must be authored with the AST editor. This is the literal core seed/acceptance content to expand into the named files; supply imports and unittest classes without changing these cases:

```python
# module-bug: pricing.py and checkout.py
def discounted(amount, percent):
    return amount * (100 - percent * 2) // 100

# checkout.py imports discounted from pricing.
def total(amounts, percent):
    return sum(discounted(amount, percent) for amount in amounts)

# unittest assertions, with total imported from checkout:
self.assertEqual(total([100, 200], 10), 270)
self.assertEqual(total([], 10), 0)
self.assertEqual(total([100, 200], 0), 300)

# api-refactor: names.py; callers greet.py and tags.py import normalize.
def normalize(value):
    return value.strip().casefold()

# greet.py
def greet(value):
    return "Hello " + normalize(value)

# tags.py
def tag(value):
    return "#" + normalize(value)

# unittest imports normalize, normalize_name, greet, tag after the requested change.
self.assertEqual(normalize_name(" ALICE "), "alice")
self.assertEqual(normalize(" ALICE "), normalize_name(" ALICE "))
self.assertEqual(greet(" ALICE "), "Hello alice")
self.assertEqual(tag(" ALICE "), "#alice")

# test-repair: counts.py
def parse_count(value):
    return int(value)

self.assertEqual(parse_count("12"), 12)
self.assertEqual(parse_count("0"), 0)
for value in (True, False, -1, "-1", "bad"):
    with self.assertRaises(ValueError):
        parse_count(value)

# checkpoint-handoff: foundation.py. Workers must preserve this file.
def slug(value):
    return "-".join(value.strip().lower().split())

# delivery.py must expose slugs(values), using foundation.slug.
self.assertEqual(slugs([" Hello World ", "Two"]), ["hello-world", "two"])
self.assertEqual(slugs([]), [])

# interrupted-edit: render.py
def render_row(values):
    return ",".join(values)

self.assertEqual(render_row(["a,b", "c"]), '"a,b",c')
self.assertEqual(render_row([]), "")

# stale-fact: parser.py initially contains this definition.
def parse_number(value):
    return int(value or "0")

# consumer.py initially imports parse_number from parser and duplicates its old fallback.
def parse(value):
    return 0 if value == "" else parse_number(value)

# The evaluator changes this dependency between increments, within the fixed
# goal of making consumer.parse follow the parser's current behavior.
# Replace parser.py with this exact definition after the first saved observation.
def parse_number(value):
    if value == "":
        raise ValueError("empty input is invalid")
    return int(value)

# Acceptance imports the parser and consumer modules; these tests never change.
self.assertEqual(consumer.parse("7"), parser.parse_number("7"))
try:
    expected = parser.parse_number("")
except ValueError:
    with self.assertRaises(ValueError):
        consumer.parse("")
else:
    self.assertEqual(consumer.parse(""), expected)
```

The acceptance files are evaluator-owned and watched; preserve their hashes. Use `python3 -m unittest discover -s tests -v` as the normal task gate. For the API rename, the acceptance file intentionally fails to import the new name before the change. The independent evaluator also checks diffs for removed tests, edited acceptance files, and foundation modification. For the stale-fact case, keep the original objective, command, review criterion, and acceptance test bytes fixed; change only the parser dependency at the declared handoff in both modes and record the event outside worker-owned state. Require the worker to preserve that evaluator-supplied dependency change while repairing the consumer.

For the checkpoint-handoff fixture, the initial brief explicitly reserves the first increment for inspecting and checkpointing the completed foundation. It introduces delivery implementation only after the forced fresh-coordinator handoff. This makes the interruption point reproducible instead of relying on a worker to leave a small task unfinished by chance.

- [ ] **Step 2: Verify each seed fails its intended acceptance condition before a worker runs.** The handoff and stale-fact setup may begin with a passing foundation/old contract; record the explicit transition that introduces the remaining work. Confirm reference repairs pass, then restore identical seed bytes before each measured run. Keep held-out fixtures frozen and do not tune skill wording against their outcomes.
- [ ] **Step 3: Generate and save the 72-run manifest.** Generate records in fixture order with alternating mode order; repeat index is 1–3:

```python
import csv
from pathlib import Path

fixtures = ["module-bug", "api-refactor", "test-repair", "checkpoint-handoff",
            "interrupted-edit", "stale-fact"]
models = ["gpt-5.6-sol", "gpt-5.6-terra"]
rows = []
for fixture_index, fixture in enumerate(fixtures):
    for model in models:
        for repeat in range(1, 4):
            modes = ["conversation", "state"]
            if (fixture_index + repeat) % 2:
                modes.reverse()
            for mode in modes:
                rows.append({"fixture": fixture, "model": model, "mode": mode,
                             "repeat": repeat, "max_turns": 4, "max_minutes": 15})
assert len(rows) == 72
with Path("docs/evaluations/horizon-state-pilot/manifest.csv").open("w", newline="") as stream:
    writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
```

- [ ] **Step 4: Run the measured comparisons using native worker tools.** Both arms use the same frozen implementation/skills and model settings; only execution mode differs. Use separate temporary projects and homes, with the same initial files and task brief. The coordinator launches editing workers only when allowed by its mode. Keep actual host handles and role boundaries in the record. A fresh-coordinator handoff receives the goal slug and workspace location, not the previous coordinator conversation. Run one measured trial at a time so its coordinator, worker, and reviewer fit the available agent slots.

For `interrupted-edit`, use a visible synchronization point in the worker brief: after the first relevant file edit, report the changed path and wait before returning its update or invoking `check`, as appropriate to the mode. The evaluator verifies the edit, interrupts that worker using its actual host handle, confirms terminal status, then starts the recovery comparison. This tests a precisely observed interruption during work; the process-kill cases in Task 9 cover narrower atomic-write windows.

- [ ] **Step 5: Score artifacts independently and record honest metrics.** Each `results.jsonl` record has the following fields:

```json
{"fixture":"module-bug","model":"gpt-5.6-sol","mode":"state","repeat":1,
 "gate_passed":false,"reported_complete":false,"false_completion":false,
 "regressions":0,"repeated_work_events":[],"state_omissions":[],
 "stale_fact_events":[],"handoff_recovered":null,"worker_launches":0,
 "reserved_attempts":0,"checkpoints":0,"format_repairs":0,
 "elapsed_seconds":0,"packet_bytes":[],"total_tokens":null,"cost":null,
 "usage_source":"unavailable","evidence_dir":"runs/module-bug-sol-state-1",
 "protocol_deviations":[]}
```

Those values illustrate the schema, not a completed measurement. Fill every measured value from raw evidence. Treat unobserved quantities as null, not zero. `false_completion` means the agent reported complete while evaluator acceptance or a required review failed. A repeated-work event records an actual duplicated edit/investigation with references; merely rereading a stale file is not automatically wasted work. Inspect source, acceptance outputs, state/audit JSON, file hashes, and worker records; self-reports alone are insufficient.

Record complete model usage only when the host reports coordinator, worker, and reviewer usage consistently for both arms. Otherwise leave total tokens/cost null. A packet byte count is not a billed-token estimate. Report the two modes' different turn semantics, aborted launches, unavailable hosts, and fixture deviations.

- [ ] **Step 6: Write the comparison report and retain opt-in status.** Show raw success counts, false completions, recovery outcomes, repeated work, elapsed time, and available cost/context measurements by model and mode. Treat this as a pilot of the combined structured-state/fresh-worker feature; do not attribute gains to one component or generalize beyond the sample. If correctness or recovery regresses, fix the defect and rerun the affected cases before recommending the mode. Do not tune on held-out outcomes and then describe the same results as held out.
- [ ] **Step 7: Review and commit the frozen protocol and completed evidence.** Commit messages: `test(horizon): freeze Sol and Terra state-mode pilot` for fixtures/manifest before runs, and `docs(horizon): report state-mode pilot outcomes` for actual results. Never commit invented or partially filled records as completed results.

## Completion and handoff

This plan is complete only when the approved design has a concrete implementation task, verification case, or explicit runtime limitation for every requirement. Feature execution is complete when Tasks 1–9 pass and Task 10 reports its actual outcome or a specific external limitation; an incomplete pilot must remain labeled incomplete. The mode stays optional regardless of the first result.

At implementation handoff, report the code/test commits, source snapshot used, actual regression result, native hosts tested, pilot outcome, remaining limitations, and the goal's state-mode CLI example. Any decision about merging the isolated implementation or installing personal skills follows the user's authorization for that action; this plan does not add a deployment step.

## Spec coverage and plan review

| Approved requirement | Implementation | Verification |
| --- | --- | --- |
| Shared core, separate host adapters, no API keys | Tasks 2–8 | Native smoke runs and entrypoint checks |
| Explicit mode and legacy compatibility | Tasks 3, 6, 7 | Legacy suite and both CLI runtimes |
| Strict schema, byte bounds, explicit archives | Task 2 | Boundary, malformed input, and audit tests |
| Evidence/provenance and conservative staleness | Tasks 2, 5 | Hash changes, missing evidence, stale-fact fixture |
| Reservation before work and idempotent binding | Task 4 | Single-turn replay and two-process races |
| One charge per attempt, no free crash retry | Tasks 4–6 | Exhaustion, late finish, and recovery tests |
| Atomic accepted state and gate checkpoint | Task 5 | Before/after commit crashes and gate counter |
| Distinct packet/current revisions | Tasks 4–6 | Attach/finish/replay and stale-revision tests |
| Honest recovery and no saved-PID reaper | Tasks 5, 6, 9 | Liveness/runner tests and interrupted-edit fixture |
| Existing review/completion semantics | Tasks 5–7 | Coverage, semantic review, current snapshot tests |
| No silent fallback when fresh workers unavailable | Task 8 | Native capability report |
| One bounded format repair, no extra implementation | Task 8 | Worker trace and pilot repair count |
| Pilot: six fixtures, two models, two modes, three repeats | Task 10 | Frozen 72-row manifest and raw results |
| Honest usage limits and opt-in rollout | Tasks 8, 10 | Published report and explicit unavailable metrics |

Self-review must check shared function signatures, JSON keys, revision ownership, real subprocess cleanup, exact test commands, evidence links, and coverage above. Fix contradictions in this document before handing it to an implementation worker.

Planning review completed on 2026-09-19: all ten tasks map to the approved design; the 22 Python example blocks parse; six local source/spec links resolve; no unfinished markers or trailing whitespace remain. These checks validate the plan, not the proposed implementation. Implementation tests and behavioral runs have not been executed for this feature yet.
