---
name: horizon
description: Use when a task needs to survive across turns or sessions - long research sweeps, multi-step refactors, "keep working until X passes". Establishes a goal with a verification gate, checkpoints after every increment, and continues under a bounded budget until the gate passes.
---

# Horizon: goal, gate, bounded continuation

Long-horizon work fails because state lives in context, and context ends.
This skill moves state to disk after every increment, through four
modules at `.claude/harness/bin/` — `goal.py`, `gate.py`, `tree_hash.py`,
`paths.py`. Call them; never reimplement their logic inline. If a step
can't be done by calling one of them, stop and say so.

## Setup

Find the modules at runtime — nearest `.claude/harness/bin` walking up
from the working directory, else `~/.claude/harness/bin`. Never hardcode
a project path: a promoted skill is shared by every project.

```bash
python3 - <<'PY'
import sys, pathlib
_c = pathlib.Path.cwd().resolve()
_bin = next((c / ".claude/harness/bin" for c in (_c, *_c.parents)
             if (c / ".claude/harness/bin").is_dir()),
            pathlib.Path.home() / ".claude/harness/bin")
sys.path.insert(0, str(_bin))
import goal, gate, tree_hash, paths
print('modules load OK, harness at', paths.harness_root())
PY
```

To find an existing goal's slug, list the state directory — ask `paths`,
not `git rev-parse`; in a monorepo the two disagree, and a wrong "no
goals yet" starts a duplicate goal:

```bash
python3 - <<'PY'
import sys, pathlib
_c = pathlib.Path.cwd().resolve()
_bin = next((c / ".claude/harness/bin" for c in (_c, *_c.parents)
             if (c / ".claude/harness/bin").is_dir()),
            pathlib.Path.home() / ".claude/harness/bin")
sys.path.insert(0, str(_bin))
import paths
found = sorted(paths.goals_dir().glob("*.json"))
print("\n".join(p.stem for p in found) or "no goals yet")
PY
```

`goal.load(slug)` raises **`goal.GoalStateError`** for a missing,
unreadable, or malformed state file — report it; never guess a goal's
state around this error. `goal.GoalClosed` (from the mutators) is a
subclass of it; catch `GoalClosed` first to tell them apart.

## One turn, end to end

**Do the increment's work first** — these blocks record and decide; they
do no work themselves. A command-gate turn is the first block alone; a
coverage-gate turn is the first block **plus** the completion block (the
first stops at `COVERAGE GATE` so a judge can rule). Copy, fill the
marked places, run — never reassemble the steps by hand; the ordering is
load-bearing.

```bash
python3 - <<'PY'
import sys, pathlib
_c = pathlib.Path.cwd().resolve()
_bin = next((c / ".claude/harness/bin" for c in (_c, *_c.parents)
             if (c / ".claude/harness/bin").is_dir()),
            pathlib.Path.home() / ".claude/harness/bin")
sys.path.insert(0, str(_bin))
import goal, gate

SLUG = "<slug>"                      # (1) from Step 1's create()
DID  = "<what this increment did>"   # (2) plain language, past tense
NEXT = "<what the next turn does>"   # (3) written for someone with no context

state = goal.load(SLUG)
if state["status"] != "active":
    raise SystemExit(f"GOAL ALREADY {state['status'].upper()} — report the stored outcome; do not work")
if goal.budget_exhausted(state):
    raise SystemExit("BUDGET ALREADY EXHAUSTED before this turn began — its work is "
                     "unrecorded; abandon and reopen with extra_turns before any more work")

try:
    result, output, h = gate.run_gate_at(state)
except gate.CoverageGate as e:
    # A coverage gate has no command to run: a fresh judge subagent rules.
    # Stop HERE, dispatch the judge, then run the completion block with
    # its verdict and this exact hash. Never recompute the hash after the
    # judge has run -- by then the tree has moved, and the next turn
    # would skip against a state nothing judged.
    print(f"COVERAGE GATE. run the completion block with H={e.tree_hash}")
    print(f"  did : {DID}\n  next: {NEXT}")
    raise SystemExit(0)
except gate.GateUnrunnable as e:
    # exit 126/127 is a broken goal. A timeout may just be a slow gate:
    # re-run this block passing gate.run_gate_at(state, timeout=590) --
    # the hosting Bash tool tops out at 600s -- and abandon only if the
    # gate cannot finish inside that ceiling.
    if "timed out" in str(e):
        raise SystemExit(f"GATE TIMED OUT (default {gate.DEFAULT_TIMEOUT}s). "
                         f"Raise the timeout and re-run this block: {e}")
    goal.abandon(SLUG, f"gate unrunnable: {e}")
    raise SystemExit(f"BROKEN GOAL, not work remaining: {e}")

# Checkpoint FIRST. The work described by DID is already on disk; checking
# the budget before recording it means the turn that exhausts the budget
# loses its own increment, and a resuming session redoes finished work.
goal.checkpoint(SLUG, DID, NEXT, h, result, output)
state = goal.load(SLUG)
print(f"turn {state['used']['turns']}: gate={result}")

if result != "pass" and goal.budget_exhausted(state):
    print(f"BUDGET EXHAUSTED after {state['used']['turns']} turns — stopping.")
    print(f"  done so far : {DID}\n  NOT done    : {NEXT}")
    raise SystemExit(0)              # never report this as success

if result == "pass":
    goal.complete(SLUG, output)
    # The TAIL, like every truncation here: the diagnosis comes last.
    print("COMPLETE. evidence:", (output.strip() or "(gate produced no output)")[-500:])
else:
    print("continue. gate said:", (output.strip() or "(no output)")[-500:])
    print("next:", NEXT)
PY
```

### The coverage turn

Between the two blocks, dispatch the judge: a **fresh subagent via the
Agent tool**, any `subagent_type` other than `"fork"` — the worker must
not grade its own work, and a fork is the same grader with the same
blind spots. Its prompt carries the criterion verbatim
(`state["gate"]["value"]`) **plus the primary artifacts** (diff, paths,
raw outputs) — never a worker-written summary; a judge fed a summary is
grading the summary. Ask for a one-word verdict plus reasoning: verdict
→ `RESULT` ("pass"/"fail", never "skipped"), reasoning → `VERDICT`,
quoted. Verdicts under `goal.MIN_COVERAGE_EVIDENCE` (= 40) chars are
refused — re-dispatch the judge rather than padding its words.

```bash
python3 - <<'PY'
import sys, pathlib
_c = pathlib.Path.cwd().resolve()
_bin = next((c / ".claude/harness/bin" for c in (_c, *_c.parents)
             if (c / ".claude/harness/bin").is_dir()),
            pathlib.Path.home() / ".claude/harness/bin")
sys.path.insert(0, str(_bin))
import goal

SLUG    = "<slug>"
DID     = "<same DID as the first block>"
NEXT    = "<what the next turn does>"
H       = "<hash-from-part-one>"   # paste exactly as printed -- bare, no quotes of your own
RESULT  = "<pass-or-fail>"         # the judge's verdict, one word
VERDICT = "<judge-reasoning>"      # the judge's own words, quoted -- not your summary

if RESULT not in ("pass", "fail"):
    raise SystemExit("fill RESULT with the judge's verdict -- a placeholder "
                     "must never be the passing value, so this refuses to run unfilled")
if not H.strip() or H.startswith("<"):
    raise SystemExit("fill H with the exact hash the first block printed")

# Checkpoint FIRST, exactly like the command turn: the increment is already
# on disk, and the turn that exhausts the budget must not lose its record.
goal.checkpoint(SLUG, DID, NEXT, H, RESULT, VERDICT)
state = goal.load(SLUG)
print(f"turn {state['used']['turns']}: judge={RESULT}")

if RESULT != "pass" and goal.budget_exhausted(state):
    print(f"BUDGET EXHAUSTED after {state['used']['turns']} turns — stopping.")
    print(f"  done so far : {DID}\n  NOT done    : {NEXT}")
    raise SystemExit(0)              # never report this as success

if RESULT == "pass":
    # Raises goal.EvidenceInsufficient if VERDICT is under
    # goal.MIN_COVERAGE_EVIDENCE (= 40) chars: re-dispatch the judge and
    # pass what it actually said -- never pad the verdict yourself.
    goal.complete(SLUG, VERDICT)
    print("COMPLETE. evidence:", VERDICT.strip()[-500:])
else:
    print("continue. judge said:", VERDICT.strip()[-500:])
    print("next:", NEXT)
PY
```

## Step 1 — Establish the goal

Never start without a gate — `goal.create` raises **`goal.GateRequired`**
on a missing/blank gate or unknown `gate_kind`; a loop with no
termination criterion is an infinite loop.

- `command` — a shell command that must exit 0, judged mechanically.
  **The gate must BE the command whose failure matters** — no pipe, `;`,
  `||`, or trailing `&` (`/bin/sh` has no pipefail; `pytest -q | tail`
  completes a goal against a failing suite). `create` raises
  **`goal.GateUnsound`** on those shapes; output is already bounded
  (`gate.MAX_OUTPUT` = 4000 chars), and a genuinely needed pipeline is
  quoted `sh -o pipefail -c 'cmd | filter'`. `&&` is fine.
- `coverage` — a written completeness criterion, judged by a freshly
  spawned subagent, never mechanically.

Choosing: a **command gate** answers "did it run?" — a proxy you can
write to is a proxy you can satisfy without doing the work (measured: an
A/B trial saw a gamed `__eq__` turn a red suite green, the command gate
certified it, and blind judges caught it 3/3 with 0 false alarms). A
**coverage gate** answers "is it right?" — use one when the deliverable
is prose or judgement, **and whenever you have write access to whatever
the command gate measures**; for a test suite in the repo you are
editing, always. **Both, for anything that matters**: command gate as
the loop's driver, a coverage judge before `complete()`. If the user
gave no gate, propose one and get explicit confirmation before `create`.

```python
state = goal.create(
    goal="<the objective>",
    cwd="<absolute path>",
    gate_kind="command",
    gate_value="npm run check",
    max_turns=20,
    watch_paths=["<output dir>"],   # narrow this for research goals
)
slug = state["slug"]
```

`create` writes `.claude/harness/goals/<slug>.json`. It raises
**`goal.BudgetInvalid`** unless `max_turns`/`max_minutes` is a positive
integer or `None`, with at least one set — an unbounded goal is the same
infinite loop by another door.

## Step 2 — Work one increment

Reload fresh at the top of every turn (`state = goal.load(slug)`); never
carry in-memory state across a turn boundary. Do the smallest unit of
real work that moves toward the goal — the loop makes progress, not any
single turn.

## Step 3 — Run the gate

The turn block calls the gate exactly once per turn:
`gate.run_gate_at(state)` hashes the tree, runs the gate, and returns
`(result, output, h)` where `h` is the hash of the tree the gate
**actually evaluated** — the value Step 4 records, never a recomputed
one, because a gate can write into the watched tree and a post-run hash
collapses `fail → fail → pass` into `fail → skipped → skipped`. There is
no second implementation of this step; the turn block is the only
canonical form.

An unreadable watched tree returns the sentinel `"unknown"` — the turn
still checkpoints, and since `"unknown"` never equals a real digest the
next turn must run the gate. A *missing* watch directory instead hashes
to a deterministic MISSING marker and can legitimately skip.

**The timeout is a parameter, not a law:** `timeout=` defaults to
`gate.DEFAULT_TIMEOUT` (480 s; the hosting Bash tool tops out at 600),
and the turn block treats a timeout as retry-with-higher-timeout — only
exit 126/127 abandons. `gate.CoverageGate` (deliberately not a
`ValueError`) carries `e.tree_hash` only via `run_gate_at`. `run_gate`
owns the skip decision, process spawning, and output bounding — do not
reimplement any of it inline.

## Step 4 — Checkpoint

`state = goal.checkpoint(slug, did, next_action, h, result, output)` —
with Step 3's `h`, never a fresh hash. Write `did`/`next` for a stranger:
`next` is read by a future session with none of your context. It raises
**`goal.GoalClosed`** if the goal is no longer `"active"` — reload and
report the real status rather than assuming the checkpoint landed.

## Step 5 — Continue or stop

- **pass** → `goal.complete(slug, output)`. It raises
  **`goal.GateNotPassed`** unless the *last checkpoint's* gate is exactly
  `"pass"` — budget exhaustion never bypasses it — and
  **`goal.EvidenceInsufficient`** when a coverage verdict has no substance.
- **budget exhausted** → stop and report honestly: done, not done, next
  action. Never describe an exhausted budget as success.
- **fail or skipped** → feed the bounded output back as the next
  increment's input; return to Step 2. A skip counts against the budget
  the same — it is not progress either.

For a coverage goal this whole step happens inside the completion block.

## Unachievable goals, and reopening

A gate that runs fine against a goal that cannot be reached —
contradictory requirements, an API that does not exist — is neither
failing nor broken: `goal.abandon(slug, "provably unachievable: <the
proof, in one sentence>")`. The reason string is the deliverable; never
report it as success. A goal abandoned for a blocker that later cleared
reopens with checkpoints intact — `goal.reopen(slug, "the credential
arrived", extra_turns=5)` — only from `"abandoned"` (a completed goal
keeps its passing gate on record; start a new one). The budget is not
refreshed implicitly: `extra_turns` raises the ceiling, `extra_minutes`
restarts the wall-clock; how much more the goal is worth is the user's
decision.

## Errors at a glance

| Call | Exception | Meaning | Action |
|---|---|---|---|
| `goal.create` | `GateRequired` | no/blank gate, or bad `gate_kind` | fix the call; never create a gate-less goal |
| `goal.create` | `GateUnsound` | the gate discards the exit status of the work it judges — a pipe, `;`, `\|\|`, or trailing `&` | rewrite the gate as the test command itself |
| `goal.create` / `goal.reopen` | `BudgetInvalid` | budget not a positive integer, or no bound at all | fix the call; an unbounded budget is the loop a gate exists to prevent |
| `goal.load` | `GoalStateError` | file missing, unreadable, not a JSON object, missing required keys, a malformed checkpoint element, or a budget that cannot bound | report; do not guess state |
| `goal.checkpoint` / `complete` / `abandon` / `add_subagent` | `GoalClosed` (subclass of `GoalStateError`) | goal status is not `"active"` | reload and report the real status |
| `goal.checkpoint` | `ValueError` | `gate_result` not one of pass/fail/skipped | a bug in the caller, not a runtime condition to handle |
| `goal.checkpoint` | `goal.BudgetExhausted` (subclass of `GoalStateError`) | the turn would exceed `max_turns` — only the exhausting turn itself records; the wall clock never refuses | abandon ("budget spent, work remains") and `goal.reopen` with `extra_turns` to record the increment |
| `goal.complete` | `GateNotPassed` | last checkpoint's gate isn't `"pass"` | budget exhaustion is not success; don't call complete yet |
| `goal.complete` | `EvidenceInsufficient` (subclass of `GateNotPassed`) | a coverage goal completing on a contentless verdict | the judge never really looked — re-dispatch it and pass what it actually said |
| `gate.run_gate_at` | `CoverageGate` | gate kind is `"coverage"` | dispatch a fresh judge, then run the completion block with its verdict and `e.tree_hash` (set only by `run_gate_at`) |
| `gate.run_gate_at` | `GateUnrunnable` | exit 126, exit 127, or timeout | 126/127: `goal.abandon` — a broken goal. Timeout: raise `timeout=` (default 480s) and re-run |
| any locked mutator | `filelock.LockTimeout` | another process holds this goal's state file | retry once; if it persists, tell the user which lock file to delete — the exception names it |

Nothing in this skill calls `tree_hash` directly — `run_gate_at` absorbs
an unreadable tree into the `"unknown"` sentinel itself.

## Subagents

Record role and briefing via `goal.add_subagent` — never write the state
file directly; any lower-level write bypasses the terminal-state guard.

```python
goal.add_subagent(
    slug, role="auth-reviewer", name="auth-reviewer-1",
    brief="Review the auth module for OWASP top-10 issues; "
          "report findings with file:line citations.",
)
```

Dispatch via the `Agent` tool with the same `brief` and the recorded
`name`; within a session, address a live agent by name (`SendMessage`)
instead of respawning cold. **After a restart, treat every entry as
dead** — handles do not survive the process; respawn from `brief` plus
the relevant checkpoints.

## Heartbeat — runs that span sessions

A goal survives interruption; nothing above makes a session come back.
To run unattended, hand the resumption to `/loop`:

- Ask the user to start `/loop <interval> resume goal <slug>: run one
  horizon turn` — or `/loop` with no interval for dynamic pacing, where
  the model schedules its own wakeups between increments. Each firing is
  one full turn: load, work one increment, gate, checkpoint.
- **Every firing starts at the Resuming block below.** A fired prompt
  has no memory of the previous firing; the checkpoints are the context.
- **A firing that finds `status != "active"` ends the loop** — report
  the stored outcome rather than starting new work. The budget rules the
  same way: a turn that prints BUDGET EXHAUSTED ends the loop honestly.
- For resumption on a calendar instead of an interval, a scheduled agent
  (`/schedule`) with the same one-turn prompt is the same recipe on a
  different clock.

The heartbeat adds no machinery, deliberately: the gate decides
completion, the budget bounds spend, and every firing leaves the goal
state consistent for whoever fires next.

## Resuming

```bash
python3 - <<'PY'
import sys, pathlib
_c = pathlib.Path.cwd().resolve()
_bin = next((c / ".claude/harness/bin" for c in (_c, *_c.parents)
             if (c / ".claude/harness/bin").is_dir()),
            pathlib.Path.home() / ".claude/harness/bin")
sys.path.insert(0, str(_bin))
import goal
state = goal.load('<slug>')
print('status:', state['status'])
print('turns used:', state['used']['turns'], '/', state['budget']['max_turns'])
for cp in state['checkpoints'][-3:]:
    print(cp['turn'], cp['gate'], '-', cp['next'])
PY
```

If `goal.load` raises `GoalStateError`, do not fabricate a fresh goal
under the same slug; report it. Read the last three checkpoints before
acting: they are the context you no longer have. This skill is the outer
loop — it re-enters `superpowers:executing-plans` and
`superpowers:verification-before-completion` rather than replacing them.
