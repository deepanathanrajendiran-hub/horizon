---
name: horizon
description: Use when a task needs to survive across turns or sessions - long research sweeps, multi-step refactors, "keep working until X passes". Establishes a goal with a verification gate, checkpoints after every increment, and continues under a bounded budget until the gate passes.
---

# Horizon: goal, gate, bounded continuation

Long-horizon work fails for one reason: state lives in context, and
context ends. This skill moves state to disk after every increment, using
three modules at `.claude/harness/bin/` — `goal.py` (goal state),
`gate.py` (gate execution), `tree_hash.py` (change detection). The skill
calls these modules; it does not reimplement their logic. If a step below
can't be done by calling one of them, stop and say so rather than
inventing an inline substitute.

## Setup

Every invocation is an inline Python block. It finds the harness modules
at runtime rather than hardcoding a path: nearest `.claude/harness/bin`
walking up from the working directory, falling back to
`~/.claude/harness/bin` for a promoted install. Never hardcode a project
path here — a skill promoted to `~/.claude/skills/` is shared by every
project, and a baked-in path would send another project's goals, ledger
and undo entries into whichever project the path happened to name.

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

`paths.harness_root()` resolves per project the same way, so goal state
and the changelog always belong to the project being worked on, never to
wherever the modules happen to live.

`goal.py` has no function to list goals. To find an existing slug, list
the state directory directly:

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

Ask `paths` rather than `git rev-parse`: in a monorepo with `.claude/` in
a subpackage the two disagree, and a listing that says "no goals yet"
while `goal.load` can see one makes the agent start a duplicate goal.

To load one: `goal.load(slug)`. It raises **`goal.GoalStateError`** if the
file is missing, unreadable, not a JSON object, or missing any of the
required keys (`slug, goal, cwd, gate, watch_paths, budget, used, status,
checkpoints, subagents`). Never guess a goal's state around this error —
report it and ask whether to fix the file or start a fresh goal.
`goal.GoalClosed` (raised by the mutators below) is a **subclass** of
`GoalStateError`, so a bare `except goal.GoalStateError` also catches it;
catch `GoalClosed` first if you need to tell the two apart.

## One turn, end to end

Steps 1-5 below explain each piece. This is the whole turn as one runnable
block — copy it, fill in the three marked places, run it. Do not
reassemble the steps by hand: the ordering they describe is load-bearing,
and hand-assembly is where it gets lost.

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

try:
    result, output, h = gate.run_gate_at(state)
except gate.CoverageGate:
    raise SystemExit("coverage gate: dispatch a fresh judge subagent, then "
                     "call goal.checkpoint yourself with its verdict")
except gate.GateUnrunnable as e:
    goal.abandon(SLUG, f"gate unrunnable: {e}")
    raise SystemExit(f"BROKEN GOAL, not work remaining: {e}")

# Checkpoint FIRST. The work described by DID is already on disk; checking
# the budget before recording it means the turn that exhausts the budget
# loses its own increment, and a resuming session is told to redo finished
# work — the exact failure the "unknown" sentinel exists to prevent.
goal.checkpoint(SLUG, DID, NEXT, h, result, output)
state = goal.load(SLUG)
print(f"turn {state['used']['turns']}: gate={result}")

if result != "pass" and goal.budget_exhausted(state):
    print(f"BUDGET EXHAUSTED after {state['used']['turns']} turns — stopping.")
    print(f"  done so far : {DID}")
    print(f"  NOT done    : {NEXT}")
    raise SystemExit(0)              # never report this as success

if result == "pass":
    goal.complete(SLUG, output)
    print("COMPLETE. evidence:", output.strip()[:200] or "(gate produced no output)")
else:
    print("continue. gate said:", (output.strip() or "(no output)")[-500:])
    print("next:", NEXT)
PY
```

Run the work for the increment *before* this block — the block records
what happened and decides whether to continue. Two failure modes it exists
to prevent: calling the gate twice per turn, and computing the tree hash
after the gate has already run.

## Step 1 — Establish the goal

Never start without a gate. A loop with no termination criterion is an
infinite loop, and `goal.create` enforces this itself: it raises
**`goal.GateRequired`** if `gate_value` is missing or blank, or if
`gate_kind` is anything other than `"command"` or `"coverage"`.

Gate kinds:
- `command` — a shell command that must exit 0 (`npm test`, `pytest -q`).
  Judged mechanically by `gate.run_gate`.
- `coverage` — a written completeness criterion for research goals, e.g.
  "every claim carries a source; no sub-question left unanswered". Judged
  by a freshly spawned subagent (Step 4) — never mechanically.

If the user gave no gate, propose one and get explicit confirmation
before calling `create`.

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

`create` writes `.claude/harness/goals/<slug>.json` and returns the new
state: `status: "active"`, `used: {"turns": 0, ...}`, `checkpoints: []`,
`subagents: []`.

## Step 2 — Work one increment

Reload state fresh at the top of every turn — never carry an in-memory
`state` object across a turn boundary:

```python
state = goal.load(slug)
```

Do the smallest unit of real work that moves toward the goal. Not the
whole thing. The loop is what makes progress, not any single turn.

## Step 3 — Run the gate

**Call the gate exactly once per turn**, with the block below.
`gate.run_gate_at` hashes the tree and then runs the gate, returning
`(result, output, h)` where `h` is the hash of the tree the gate
**actually evaluated**. Pass that exact value to `goal.checkpoint` in
Step 4, and never recompute it afterwards.

Why the single call exists: a checkpoint must record the state the gate
*judged*, not the state left behind after it ran. A gate can write into
the watched tree — a counter file, a coverage report, a build artifact —
so hashing after the run captures that write, the next turn sees an
unchanged tree, and `fail → fail → pass` collapses into
`fail → skipped → skipped`. That bug shipped once in this project's own
test suite precisely because the hash and the gate were two separate
calls and the ordering was left to the caller. `run_gate_at` removes the
opportunity.

If the watched tree is unreadable, `run_gate_at` returns the sentinel
`"unknown"` rather than raising, so the turn still checkpoints. An
unreadable tree must not kill the turn *after* the increment's work has
landed but *before* the record of it — a resuming session would be told
to redo finished work. `"unknown"` can never equal a real digest, so the
next turn is guaranteed to run the gate rather than skip.

This one call site handles both gate kinds — do not branch on
`state["gate"]["kind"]` yourself first; let `run_gate_at` tell you what it
needs via the exception:

```python
try:
    result, output, h = gate.run_gate_at(state)  # "pass" | "fail" | "skipped"
except gate.CoverageGate:
    # run_gate raises this unconditionally for gate_kind == "coverage",
    # before it even looks at tree_hash — coverage gates have no skip
    # path; every checkpoint gets a fresh judgment. Dispatch via the
    # Agent tool (any subagent_type other than "fork", so it starts with
    # no memory of this goal's work) with a prompt containing the
    # coverage criterion (state["gate"]["value"]) plus what actually
    # happened this turn — never a summary written by the agent that did
    # the work. Self-grading is the weakest link in any autonomous loop,
    # and an agent that graded its own research will pass itself. Its
    # reply becomes result ("pass" or "fail" — never "skipped", coverage
    # gates always get a fresh judgment) and output (its reasoning,
    # bounded to a few thousand characters).
    #
    # A placeholder in a verification path must never be the passing
    # value: an agent running this verbatim without dispatching the judge
    # would otherwise auto-complete every coverage goal on a fabricated
    # verdict, silently. Fail loud instead.
    raise NotImplementedError("dispatch the judge subagent here")
except gate.GateUnrunnable as e:
    # A broken goal, not work still to do: command not found (exit 127),
    # not executable (exit 126), or it timed out. Stop — do not retry
    # blindly, that burns the whole turn budget on a command that was
    # never going to run. Report the exact cause from `e`.
    goal.abandon(slug, f"gate unrunnable: {e}")
    raise
```

`gate.CoverageGate` is deliberately **not** a `ValueError` — catch it by
name. `run_gate` owns the skip decision, spawning the gate command,
output bounding (`gate.MAX_OUTPUT` = 4000 chars), and the
unrunnable-vs-failing distinction, all unit tested in
`tests/test_gate.py`. Do not reimplement any of it inline.

## Step 4 — Checkpoint

Record what happened using the hash from Step 3 — not a freshly computed
one — and the result from Step 4:

```python
state = goal.checkpoint(slug, did, next_action, h, result, output)
```

Write `did` and `next_action` in plain language for a stranger: `next` is
read by a future session that has none of your current context.
`checkpoint` reloads state from disk itself (it takes `slug`, not your
in-memory `state`), appends the checkpoint, increments `used.turns`, and
returns the fresh saved state — reassign `state` to it.

`checkpoint` raises **`goal.GoalClosed`** if the goal's status is not
`"active"` (already `"complete"` or `"abandoned"`). In the normal loop
this shouldn't happen — Step 6 stops the loop once status leaves
`"active"` — but if it does (a stale slug, a concurrent process), do not
assume the checkpoint landed: catch it, reload, and report the real
status.

## Step 5 — Continue or stop

```python
if result == "pass":
    done = goal.complete(slug, output)
    # report success with `output` (the gate evidence) attached
elif goal.budget_exhausted(state):
    # stop. report honestly: what is done, what is not, what the next
    # action would be. Never describe an exhausted budget as success.
    pass
else:
    # result was "fail" or "skipped" and budget remains — feed the
    # bounded output back as the next increment's input. Return to Step 2.
    pass
```

`goal.complete(slug, output)` raises **`goal.GateNotPassed`** unless the
*last checkpoint's* `gate` field is exactly `"pass"` — so it will refuse
if called after a fail, a skip, or on a goal with no checkpoints. It
raises **`goal.GoalClosed`** if the goal is already complete or
abandoned. Budget exhaustion never bypasses this: if the last gate result
is not `"pass"`, `complete` refuses regardless of `budget_exhausted`.

Note that `"fail"` and `"skipped"` are handled identically here: a skip
means the tree didn't change since the last attempt, which is not
evidence of progress either — it still counts against the budget and
still needs a real next increment.

## Reopening an abandoned goal

Abandonment is not the end of the record. A goal abandoned for a blocker
that later cleared — a missing credential, a dependency that landed — can
be returned to `"active"` with its checkpoints intact:

```python
goal.reopen(slug, "the credential arrived", extra_turns=5)
```

Only from `"abandoned"`. A completed goal keeps a passing gate on record,
and reopening it would let that evidence stand for later work it never
judged — start a new goal instead (`goal.GoalClosed` if you try).

**The budget is not refreshed implicitly.** A goal abandoned at its turn
limit reopens still exhausted, and the very next `budget_exhausted` check
stops the loop again. That is deliberate: how much more the goal is worth
is the user's decision, not a side effect of reopening. `extra_turns`
raises the ceiling; `extra_minutes` replaces the wall-clock allowance and
restarts it from now, since the original window has usually long elapsed.
Without `goal.reopen`, the only way forward was a new goal that started
with no checkpoints — the record of what had already been done stayed on
disk and became unreachable.

## Errors at a glance

| Call | Exception | Meaning | Action |
|---|---|---|---|
| `goal.create` | `GateRequired` | no/blank gate, or bad `gate_kind` | fix the call; never create a gate-less goal |
| `goal.load` | `GoalStateError` | file missing, unreadable, not an object, or missing required keys | report; do not guess state |
| `goal.checkpoint` / `complete` / `abandon` | `GoalClosed` (subclass of `GoalStateError`) | goal status is not `"active"` | reload and report the real status |
| `goal.checkpoint` | `ValueError` | `gate_result` not one of `pass/fail/skipped` | fix the caller — this is a bug in the skill's own call, not a runtime condition to handle |
| `goal.complete` | `GateNotPassed` | last checkpoint's gate isn't `"pass"` | don't call complete yet; budget exhaustion is not success |
| `gate.run_gate` | `CoverageGate` | gate kind is `"coverage"` | dispatch a fresh subagent (Step 4) |
| `gate.run_gate` | `GateUnrunnable` | exit 126 (not executable), exit 127 (not found), or timeout | `goal.abandon` and stop; this is a broken goal, not work left to do |
| `goal.add_subagent` | `GoalClosed` | goal status is not `"active"` | same as `checkpoint`/`complete`/`abandon`: reload and report the real status |
| `goal.reopen` | `GoalClosed` | the goal is `"active"` or `"complete"`, not `"abandoned"` | only an abandoned goal reopens; for a completed one, start a new goal |
| `goal.reopen` | `BudgetInvalid` | `extra_turns`/`extra_minutes` is not a positive integer | fix the call; a grant that cannot bound is the same bug `create` rejects |
| `tree_hash.compute` (Step 3) | any `Exception` | watched tree unreadable — same condition `tree_hash.changed()` absorbs per spec §5.2.1 | do not let it kill the turn; catch it and checkpoint with the sentinel hash `"unknown"` so the increment's work already on disk is still recorded, and the next turn's `changed()` cannot mistake `"unknown"` for a match |

## Subagents

Store role and briefing, never a durable handle, via `goal.add_subagent`
— **never write the goal state file directly.** Any lower-level write
path bypasses the terminal-state guard and would silently persist
bookkeeping onto a goal that already completed or was abandoned.
`add_subagent` is guarded the same way `checkpoint`/`complete`/`abandon`
are:

```python
goal.add_subagent(
    slug, role="auth-reviewer", name="auth-reviewer-1",
    brief="Review the auth module for OWASP top-10 issues; "
          "report findings with file:line citations.",
)
```

`add_subagent(slug, role, name, brief, last_seen_turn=None)` reloads
state itself (like `checkpoint`, it takes `slug`, not an in-memory
`state`), raises `goal.GoalClosed` if the goal isn't `"active"`, bounds
`brief` the same way `checkpoint` bounds gate output, defaults
`last_seen_turn` to the goal's current turn count if omitted, appends the
entry, and saves. Call it any time after Step 1 — it does not need to be
sequenced with a checkpoint call.

Dispatch the subagent itself via the `Agent` tool, using the same `brief`
text for both the prompt and the recorded entry, and give it the `name`
you recorded so it can be addressed later:

```python
# Agent(subagent_type="general-purpose", name="auth-reviewer-1",
#       prompt="Review the auth module for OWASP top-10 issues; "
#              "report findings with file:line citations.")
```

- **Within a session:** address a live agent by name via `SendMessage`
  instead of respawning it cold.
- **After a restart:** treat every entry as dead, no exceptions. Respawn
  from `brief` plus the relevant checkpoints. Agent-listing tools are
  scoped to in-process subagents, so a persisted handle does not resolve
  after a restart — and nothing under `.claude/harness/` records which
  turn a session began at, so there is no way to compute staleness more
  precisely than "this is a new session."

  This deliberately deviates from the design spec, whose `subagents`
  schema listed a `session` field for staleness detection: it is dropped
  here because nothing populates it from a real source, so a rule built
  on it could never actually be checked. `last_seen_turn` is kept as a
  plain record of when the entry was made, not as a staleness signal.

## Relationship to other skills

This is the outer loop. It re-enters `superpowers:executing-plans` and
`superpowers:verification-before-completion` rather than replacing them,
and defers to `superpowers:brainstorming` / `writing-plans` for deciding
*what* to build.

## Resuming

```bash
python3 - <<'PY'
import sys, pathlib
_c = pathlib.Path.cwd().resolve()
_bin = next((c / ".claude/harness/bin" for c in (_c, *_c.parents)
             if (c / ".claude/harness/bin").is_dir()),
            pathlib.Path.home() / ".claude/harness/bin")
sys.path.insert(0, str(_bin))
import json
import goal
state = goal.load('<slug>')
print('status:', state['status'])
print('turns used:', state['used']['turns'], '/', state['budget']['max_turns'])
for cp in state['checkpoints'][-3:]:
    print(cp['turn'], cp['gate'], '-', cp['next'])
PY
```

This restores the goal, the budget spent, and the last `next` action. If
`goal.load` raises `GoalStateError`, the slug is wrong or the file is
damaged — do not fabricate a fresh goal under the same slug; report it.
Read the last three checkpoints before acting — they are the context you
no longer have. If `state["subagents"]` is non-empty, every entry is dead
per the restart rule above; respawn from `brief` only if the goal
actually still needs that line of work.
