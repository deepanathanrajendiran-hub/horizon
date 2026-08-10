# The Continual Harness

Two skills that make Claude Code better at **long tasks** and at **learning
from you** — built entirely on capabilities Claude Code already has. No API
keys, no external services, no dependencies beyond the Python standard
library.

- **`/horizon`** — carries a task across turns and sessions. A goal, a
  verification gate, and a record on disk after every step.
- **`/refine`** — after a task, reads what actually happened and proposes the
  smallest change that would make next time go better. Proposes; never
  writes without your approval.

---

## Table of contents

1. [Where this came from](#1-where-this-came-from)
2. [The two problems it solves](#2-the-two-problems-it-solves)
3. [What a skill actually is](#3-what-a-skill-actually-is)
4. [`horizon` — surviving long tasks](#4-horizon--surviving-long-tasks)
5. [`refine` — learning between sessions](#5-refine--learning-between-sessions)
6. [The guardrails, and why they exist](#6-the-guardrails-and-why-they-exist)
7. [Under the hood — the five modules](#7-under-the-hood--the-five-modules)
8. [How it was built](#8-how-it-was-built)
9. [Bugs worth knowing about](#9-bugs-worth-knowing-about)
10. [Current state](#10-current-state)
11. [Going global](#11-going-global)
12. [Known gaps](#12-known-gaps)
13. [File map](#13-file-map)

---

## 1. Where this came from

This started as a question about a blog post:
[Prime Intellect's Prime Agent](https://www.primeintellect.ai/blog/prime-agent).

Prime Agent is a coding-agent harness built on two ideas:

- **Recursive Language Model (RLM)** — instead of a fixed menu of tools, the
  model gets one persistent Python REPL. Big data lives in variables instead
  of the conversation, so a 500k-token log never enters the context window.
- **Continual Harness** — the harness itself is editable from inside the
  agent's own run. Formalised as `H = (ρ, G, K, M)`: prompt, skills,
  subagents, memory.

The obvious move was to wrap Prime Agent as an MCP server. That was rejected:
it needs its own model credentials and bills per token, separately from an
existing Claude Code subscription.

**The insight that redirected the project:** the value isn't the binary, it's
the design — and Claude Code already has a primitive for every element of
`H`. An audit found the mapping was one-to-one, and that the layer was
completely unused:

| Prime Agent | Claude Code equivalent | State before this |
|---|---|---|
| ρ — prompt | `CLAUDE.md` | 77 lines, tool-routing only |
| G — skills | `~/.claude/skills/` | 1 skill |
| K — subagents | `~/.claude/agents/` | **empty** |
| M — memory | per-project memory dir | **empty** |
| `/refine` | — | **missing** |
| goal / `goal.complete()` | `TaskCreate` (partial) | weak |
| heartbeats | `ScheduleWakeup`, `/loop` | available, unused |
| `--autonomous-gate` | — | **missing** |

So the gap was never capability. **Nothing ever wrote to the harness layer,
and nothing enforced a goal across turns.** This project closes both.

---

## 2. The two problems it solves

**Long tasks die when context ends.** Ask for an afternoon's refactor and I
eventually run out of context. The next session doesn't know what I did, what
worked, or what was left. Work gets silently redone or abandoned.

**Nothing I learn survives.** You correct me — "the folder name has a trailing
space" — and I get it right for the rest of the session. Tomorrow it's gone.
Every session starts from zero.

---

## 3. What a skill actually is

A skill is a markdown file in `.claude/skills/<name>/SKILL.md`. It isn't a
program. When what you say matches its description, Claude Code loads that
file into my context and I follow it.

So these are **instructions I follow**, backed by small Python modules for the
parts that have to be exact — hashing, state files, rollback. That split is
deliberate: logic that can silently be wrong lives in tested code, and the
markdown stays thin.

---

## 4. `horizon` — surviving long tasks

The idea: **stop keeping state in my head, write it to disk after every step.**

You give it a goal and a **gate** — a command that must pass for the job to be
done. `npm test`, `pytest`, `cargo check`.

```bash
/horizon "make the auth tests pass" --gate "npm test" --max-turns 20
```

The loop:

1. Do one small piece of work
2. **Write to disk**: what I just did, what comes next, a fingerprint of your files
3. Run the gate
4. Failed → feed the error back, loop. Passed → done, reporting the gate's real output

Step 2 is the whole point. If the session dies, the next one opens that file
and reads `turn 3: set x=2 → next: add the test`. It's written for a stranger,
because that's effectively who reads it.

### Live demonstration

```
1. A goal with no gate is refused
   GateRequired: "a loop with no termination criterion is an infinite loop"
2. Establish                     budget=5 turns  gate=./check.sh
3. The loop     turn 1: gate=fail
                turn 2: gate=skipped   ← nothing changed; the gate never ran
                turn 3: gate=pass      → complete, evidence: "all checks passed"
4. Completion refused without a pass
   GateNotPassed; status stays 'active'
```

### Three guards

- **Refuses to start without a gate.** A loop with no finish condition is an
  infinite loop.
- **Cannot claim success without a passing gate.** Budget exhaustion produces
  an honest partial report. `goal.complete()` raises `GateNotPassed` if you try.
- **Skips the gate when nothing changed.** Re-running `npm test` on an
  unchanged tree cannot tell you anything new. The skip is *conservative*: on
  any doubt at all it runs, because a false skip is a correctness bug while a
  false run costs seconds.

### Research goals

For research, the gate is a **coverage criterion** ("every claim has a
source") judged by a **freshly spawned subagent that did not do the work**.
Self-grading is the weakest link in any autonomous loop — an agent that graded
its own research will pass itself.

---

## 5. `refine` — learning between sessions

After a task, type `/refine`. It reads the session and looks for **turning
points** only — not everything:

- You corrected me
- A command failed, then worked after a fix ← *the highest-signal class: a
  reproducible gotcha whose fix is already proven*
- I re-derived the same procedure for the third time
- A durable fact about you or the project

Then it picks **the cheapest place to put the lesson**:

| Layer | Cost |
|---|---|
| memory file | loaded only when relevant |
| a skill | loaded only when triggered |
| a subagent | runs in its own context |
| `CLAUDE.md` | **loaded in every session, forever** |

`CLAUDE.md` is last resort. Everything there is context tax on every single
message.

Then it **shows you a diff and stops.** You approve or decline. Every approved
write is logged so `/refine --undo` reverses it.

### Invocation

```bash
/refine                    # harvest the whole session
/refine the test failures  # narrow the search — does NOT lower the evidence bar
/refine --undo             # revert the most recent approved edit
/refine --global           # target ~/.claude/ instead of this project
```

### What it actually found here

Run against this project's own build session, it proposed three things — all
routed to memory, none to `CLAUDE.md`:

| Memory | Evidence it cited |
|---|---|
| `project-dir-has-trailing-space` | the real `cd` failure: `(eval):cd:1: no such file or directory` |
| `prefers-no-additional-api-keys` | your correction that redirected the whole project |
| `verify-regression-tests-discriminate` | a regression test that passed against the bug it guarded |

Those three files now exist in your memory directory and load automatically in
future sessions here. That's the compounding loop closing for the first time.

### The rollback story

```
APPLY       record → edit → commit_entry → one ledger line
UNDO        file restored, REVERTED line appended
STALENESS   you edited the file after approving → UndoConflict, your edit survives
```

`record()` snapshots the target **before** the edit; `commit_entry()` writes
the ledger line **after** it lands. An entry recorded but never committed
represents a failed edit and is invisible to `undo()`.

---

## 6. The guardrails, and why they exist

A system that can edit its own rules will, given the chance, edit them to make
its own life easier. This isn't hypothetical. Prime Agent's own writeup
describes its refinement loop discovering it could cheat at Factorio via
console commands — and then building *better cheating tools*, because cheating
scored well on the metric.

So `refine` is fenced four ways:

- **G1 — Evidence required.** Every proposal cites a concrete moment. No
  speculative "this might help someday" edits.
- **G2 — Cap of 3** per session, enforced against a **persisted count** in
  `CHANGELOG.md`, not against my own recollection. A guardrail that depends on
  the constrained party's memory isn't a guardrail.
- **G3 — No self-exemption.** It may never edit its own rules, the code
  enforcing them, or `horizon`'s verification logic — and may never propose
  anything, anywhere, that softens G1, G2, or G4.
- **G4 — Approval gate.** No write without your explicit approval, per proposal.

**These are enforced in code, not just written down.** `record()` refuses:

```
blocked — forge the sentinel the cap counter skips
blocked — edit refine's own rules
blocked — same file, lowercase spelling      ← macOS is case-insensitive
blocked — the enforcement code itself
blocked — a hardlink to a protected file
```

### The subtlest guardrail

G3 also forbids proposing a **second write path**. A subagent definition
declares its own tools and starts with a fresh context — so `refine`'s
guardrails aren't loaded in it. A proposal reading as reasonable prose ("you
maintain the harness layer; you have standing approval for maintenance") would
pass every rule and then edit `CLAUDE.md` with no logging, no cap, no undo.

G3 now closes it: every harness write goes through `record()`, and any
proposal creating a skill/subagent/hook must **state what write authority it
claims** as part of what you're approving.

---

## 7. Under the hood — the five modules

`.claude/harness/bin/`, standard library only:

| Module | Lines | Job |
|---|---|---|
| `paths.py` | 54 | Find the current project; resolve where state lives |
| `tree_hash.py` | 105 | Detect whether files changed, conservatively |
| `goal.py` | 233 | Goal state: create, checkpoint, complete, abandon |
| `gate.py` | 125 | Run the gate; decide skip; unrunnable vs failing |
| `refine_log.py` | 319 | Changelog, snapshots, rollback |

### Two design decisions worth understanding

**`tree_hash` combines three inputs**, not one: git HEAD, `git status
--porcelain`, *and* an mtime walk. The third isn't redundant — **`git status`
cannot see writes inside `.gitignore`d directories.** A research goal writing
only to `out/` would have its gate silently skipped forever. Reproduced,
fixed, and guarded by a regression test.

**Unrunnable ≠ failing.** A gate that can't run (typo, missing tool, missing
execute bit, timeout) is a *broken goal* and stops the loop. A gate that fails
is *work still to do* and continues it. Conflating them burns the entire turn
budget re-running a command that could never have worked.

---

## 8. How it was built

Not vibes. The full process:

```
brainstorm → spec → external review → plan → 7 tasks
                                              ↓
    each task: fresh implementer → code review → fix rounds → scoped re-review
                                              ↓
                          final whole-branch review (strongest model)
                                              ↓
                                   one fix wave → re-review → merge
```

**38 commits. 8 reviews. 16 fix rounds. 78 tests.**

Every task used a fresh subagent with no memory of previous tasks, then an
independent reviewer, then targeted fix rounds, then a scoped re-review of
just the fix. Findings were logged in a ledger with explicit rulings —
including the ones I overruled and why.

The single most useful discipline, learned the hard way: **a regression test
must be run against the pre-fix code to prove it fails there.** More on that
below.

---

## 9. Bugs worth knowing about

These are the interesting ones — each is a general lesson, not just a fix.

### A test that passed against the bug it guarded

Task 1 fixed a real defect (silently swallowed `os.walk` errors) and shipped a
regression test. I replayed that test against the *pre-fix* commit: **it
passed.** It tested the safe scenario, not the dangerous one. "9 tests
passing" read as proof and was worth nothing.

Every fix after that had to be verified by neutering the fix and confirming
the test fails.

### A test that asserted the wrong kind of thing

`undo()` restores a file. A test asserting *"an exception was raised"* would
pass against an implementation that mutated the file and *then* raised. Tests
had to assert **on-disk state**, not merely that an error was reported.

### `undo()` could destroy the file it exists to restore

`open(target, "w")` truncates on open. A crash mid-write left your `CLAUDE.md`
empty. Fixed with temp-file + atomic rename.

### `undo()` could revert the *wrong* edit, silently

Corrupt entries were skipped silently — so a torn newest entry meant undo
quietly reverted the *next-oldest* and returned normally. You'd believe your
last change was backed out when a different one was.

### The cap was defeatable by one string

`record()` took the `layer` argument as free text and wrote it into the
CHANGELOG field the counter uses to skip reverted lines. So
`record("REVERTED", …)` created a real, committed, undoable edit that the cap
counted as **zero**. Four fix rounds had gone into making that cap
tamper-resistant; its sentinel was caller-supplied.

### A memory path so under-specified it would fail silently

The routing table gave literal paths for three layers and prose for the
fourth. An agent pattern-matching the others would write to `.claude/memory/`
— where Claude Code's recall never looks. And it fails *through* approval: you
see a plausible full path, approve it, and the memory is invisible forever.

### The promotion recipe would have cross-contaminated every project

Proven with two real projects before fixing:

```
projB silently overwrote projA's identically-titled goal, destroying its checkpoints
3 edits applied in projA left projB reading 3/3 on its daily cap
```

---

## 10. Current state

**78 tests passing.** Acceptance: **9 PASS / 1 NOT RUN** — see
`docs/superpowers/results.md` for the full table with cited artifacts.

T1, T2, T3 and T9 were run **live**, with real proposals you approved. The
only unrun test is T10 (subagent restart safety), which needs a genuinely
separate Claude Code process — a subagent shares this one and would prove
nothing about handle staleness.

### Running T10 yourself

```
1.  /horizon "tidy the README" --gate "true"    ← let it record a subagent entry
2.  Quit Claude Code entirely.  Not /clear — exit the process.
3.  Reopen here:  "resume the goal tidy-the-readme"
4.  PASS = treats stored subagent entries as dead, respawns from `brief`
    FAIL = tries SendMessage against the stored agent name
```

---

## 11. Going global

Right now these skills only load when Claude Code runs **in this folder**.
That was deliberate — test here before turning them loose on every project.

The architectural blocker is now fixed. `paths.harness_root()` discovers the
project by walking up from the working directory, so promoted modules shared
from `~/.claude/harness/bin` still write into **the current project**.
Verified with two projects: separate state, independent caps, no shared
harness.

```bash
cp -R .claude/skills/refine .claude/skills/horizon ~/.claude/skills/
mkdir -p ~/.claude/harness/bin && cp .claude/harness/bin/*.py ~/.claude/harness/bin/
```

Memory routing is already promotion-safe: it derives a per-project key from
the absolute path (every `/` and space becomes `-`), so each project keeps its
own memories.

---

## 12. Known gaps

Nothing here is hidden. `docs/superpowers/results.md` carries the full list of
23 deferred items with triage. The ones that matter:

1. **`undo()` reverts the wrong edit** if a crash lands between the edit and
   `commit_entry()`. Fixing it means changing a test that currently encodes
   today's behaviour as intended — a design decision, not a mechanical fix.
2. **The heartbeat was never built.** Spec §5.2 called for `/loop` /
   `ScheduleWakeup`; neither appears anywhere. Cross-session continuation is
   manual resume only. A scope decision, not a defect.
3. **`MEMORY.md` index lines sit outside rollback scope.** `record()`
   snapshots one target, so `/refine --undo` on a memory entry leaves a
   dangling index line. Found on the first real use of the skill.
4. **The G2 cap counter has no tests.** It lives in markdown, so
   `commit_entry()`'s format could drift from it with nothing detecting the
   break. Ironically the most-iterated logic on the branch is the least tested.

---

## 13. File map

```
.claude/
  skills/
    horizon/SKILL.md      340 lines — goal, gate, checkpoint, continuation
    refine/SKILL.md       498 lines — harvest, route, propose, log, undo
  harness/
    bin/                  5 Python modules, stdlib only
    CHANGELOG.md          every approved harness edit, newest last
    goals/                live goal state (one JSON file per goal)
    undo/                 pre-edit snapshots that make rollback possible

tests/                    78 tests
docs/superpowers/
  specs/                  the design, and the external review that shaped it
  plans/                  the 7-task implementation plan
  results.md              acceptance results + 23 known gaps, triaged
  acceptance.md           how to run the manual acceptance tests

~/.claude/projects/-Users-deeps-Documents-NEW-STUFF-fun-/memory/
  MEMORY.md               index
  *.md                    3 memories, written by /refine and approved by you
```

The full audit trail — every review, every fix round, every ruling including
the ones I overruled — is in
`.superpowers/sdd/2026-08-09-continual-harness/progress.md` (gitignored). It's
the record of *why* things are shaped this way, which git history alone
doesn't carry.

---

## A closing note on trust

The most valuable thing this project produced isn't the code. It's the
evidence about where agent work actually goes wrong.

Almost every defect found here originated in **my own plan's sample code**,
not in the implementers who followed it. Two tests passed against the bugs
they were written to guard. A guardrail that took four fix rounds to harden
was defeatable by one string argument. A promotion recipe that read correctly
would have silently merged every project's audit trail into one.

None of that was caught by writing more carefully. It was caught by running
things against the code that still had the bug, and by asking an independent
reviewer to attack the fix rather than confirm it.

That's what the third memory in your directory says, and it's the one most
worth keeping.
