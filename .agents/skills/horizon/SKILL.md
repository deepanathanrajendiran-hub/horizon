---
name: horizon
description: Use for explicitly requested bounded work that must resume across turns or sessions, such as "keep working until the tests pass". Saves progress and verifies completion against a command or review criterion.
---

# Horizon for Codex

Keep the objective, evidence, budget, and next action on disk using the shared CLI.
Resolve `HARNESS` to the absolute `scripts/harness.py` beside the **SKILL.md
you actually loaded**, even for a global installation. Set `PROJECT` to the
absolute path of the directory this session started in (the session's working
directory, not the shell's directory after a `cd`), unless the user names
another project. Never use a subdirectory you create or enter during the
work. Quote both paths.

```bash
python3 "$HARNESS" --runtime codex --project "$PROJECT" doctor
python3 "$HARNESS" --runtime codex --project "$PROJECT" status
```

Run `doctor` first and use the `state` and `gates` paths it prints for the whole
session. Codex has no hooks, so at session start run `status`, and for any active
attempt run `step-reclaim` (see Optional state mode).

## Earlier goals

Read the goal listing that `status` prints. Run `status SLUG` for every active goal
and for every goal whose abandon reason or next action lists open lines. Read
request.md and requirements.md of the most recent goal and of any goal whose
deliverables overlap the current request. Their requirements still apply unless
the current request changes them.

Resume a goal only for the same request; never continue an earlier goal under a
different one. Read its last checkpoint and raw artifacts before resuming. When
the current request replaces or extends an earlier goal's work, start a new goal
first: copy every still-applicable earlier line into the new requirements.md as
`Rk (from OLD_SLUG Rj)`, copy the checks behind them into the new goal's `$GATES`
(see Before `start`), and never edit an earlier goal's gate files. Then, if the
old goal is still active, `abandon` it with reason `continued in NEW_SLUG`. Do
the same when a goal you would resume is exhausted and the request states a
budget; if it states none, stop and report instead. Leave unrelated goals alone.

Where the current request contradicts or corrects an earlier requirement, it
governs that point only: retire only the checks it supersedes, keep the rest,
and add `Rk supersedes OLD_SLUG Rj: old -> new` to requirements.md. If you are
unsure whether an earlier requirement still applies, keep it and name the doubt
in the final report. Never `reopen` without explicit user authorization.
Create a goal when the user requests bounded continuation or invokes `$horizon`.

## Before `start`

Do these steps before changing any deliverable; scratch experiments elsewhere are
fine. Note the time when you first read the request: a time limit the user states
counts from their message, so give the goal the minutes that remain when you
create it. Let `GATES` be the `gates` path `doctor` printed, plus `/NAME`, where NAME
is a short label for this request that no directory there already uses.

1. Save the request verbatim to `$GATES/request.md`. Read the inputs it names, the
   code it concerns, and that code's existing tests and callers.
2. Write `$GATES/requirements.md` with one numbered line (R1, R2, ...) for each
   requirement, reported symptom, deliverable and constraint in the request, quoted
   in the request's words. Keep qualifiers such as "exact", "all" and "unchanged",
   and keep stated limits. When the request names a class ("all X", "every Y"),
   list each member. Tag each line `[gate]`, `[review]` or `[unverifiable]`. If a
   line admits more than one reasonable reading, also tag it `[judgment]` and
   record the reading you chose. This file states what was asked: leave out your
   diagnosis, method and planned fix. The reviewer tests `[unverifiable]` lines it
   can and answers the rest `NOT REVIEWED`; the final report lists those lines
   first.
3. Write the gate in `$GATES` as checks of the `[gate]` lines, each check naming its
   line. Exercise the entry points the request names, called the way existing
   callers and tests call them. If your change will add an interface or change a
   consumer, also check the old interface's observable behaviour. When the request
   says existing behaviour must stay the same, capture the current outputs now and
   have the gate compare against them. Copy into `$GATES` the checks of earlier
   goals that still apply (see "Earlier goals"); never edit an earlier goal's gate
   files. Take expected values from the request and its inputs, never from your
   solution's code or model. Put a `# Still passes:` line at the top of the gate
   naming one wrong artifact it would accept, or `none`; if there is one, add a
   check that does not depend on how the artifact is produced, or retag that line
   `[review]` or `[unverifiable]`.
4. Start the goal before editing any deliverable; a gate written after the answer
   tends to check the answer's form. Write `request.md`, `requirements.md` and the
   gate in the same Bash call that runs `start`. `start` runs the gate once as the
   baseline observation and charges no turn.

```bash
python3 "$HARNESS" --runtime codex --project "$PROJECT" start "Requested outcome" \
  --gate-path "$GATES" --gate "actual verification command" \
  --review "Every line of $GATES/requirements.md holds, judged against $GATES/request.md" \
  --max-turns 20 --max-minutes 60
```

5. Before your first edit, read the baseline `observation` that `start` printed;
   do not run `check` for the baseline. Checks of new requirement lines must fail
   there where the request says behaviour is broken or missing. A check that
   passes at baseline must be explained (for example, it guards existing
   behaviour). Carried checks must pass; report a carried check that fails here
   as a pre-existing regression.

Use `--coverage` with the same criterion instead of `--gate` when no command can
check any line. When you write the gate yourself (`--gate-path`), always pass
`--review`: such a gate is never "strong", and without `--review` no reviewer
would check it. Without `--gate-path`, omit `--review` only when every line is
tagged `[gate]`. `start`
refuses a `--gate-path` without request.md and at least one requirement line, and
watches that directory for you. Keep the gate, its fixtures, request.md and
requirements.md in `$GATES`; keep logs and generated output elsewhere, outside the
watched tree. If a deliverable the request names lies outside `PROJECT`, pass
`--watch` for `PROJECT` and for that path. If a tool appends to a file on every
run (a log or audit trail), watch the deliverables and inputs instead of the
directory that holds it. Choose proportional budgets; user limits take precedence
over the example. Save the returned slug. State belongs to this runtime's project
`.codex/harness`.

## Optional state mode

State mode is for long unattended runs or parallel work; use conversation mode
otherwise. Use it when the user requests it. Add `--execution state` at goal creation;
existing goals keep their original mode. Read `status` before dispatching work.
For state goals, follow `step-begin`, fresh worker dispatch, `step-attach`, and
`step-finish` in the [shared CLI reference](references/commands.md#state-mode-worker-protocol).
The reservation counts against the budget.

Give each worker the saved packet and its referenced artifacts. Its role is to
perform one increment and return the update JSON; the coordinator owns lifecycle
commands. Also give it the resolved [Packet and update schema](references/commands.md#packet-and-update)
from the shared reference; the packet's short procedure is not the complete schema.
Do not copy this conversation into the worker. Bind the actual returned handle
immediately. Resume by inspecting that handle, not by launching a duplicate.

In Codex, dispatch a new `collaboration.spawn_agent` with `fork_turns: "none"` for
each reserved increment. Give the packet in its task message and retain the returned
agent handle. Follow the user's model preference and use host status/wait tools to
establish that the worker stopped before finishing. The Python CLI cannot dispatch
or inspect native workers for you.

After the worker stops, inspect its evidence and proposed changes, save its terminal
status evidence, and finish the attempt. Allow at most one formatting-only repair
request to that worker. Further implementation requires another reserved attempt.
Use existing independent review and completion checks after the attempt closes.

If `status` shows an attempt you didn't start, its session may have died.
Without hooks, run `step-reclaim` when `status` shows an attempt that no live
worker owns. It refuses while the lease is live (state-mode leases go stale
after 600 s without renewal).

```bash
python3 "$HARNESS" --runtime codex --project "$PROJECT" step-reclaim "$SLUG" \
  --attempt "$ATTEMPT_ID" --expected-revision "$CURRENT_REVISION"
```

If interrupted, establish worker and gate liveness before `step-recover`. Preserve
workspace edits and inspect them on the next increment. Never kill a process using
only a PID saved by a previous session. Missing fresh-worker tools or uncertain
liveness must be reported; neither is permission to start duplicate work.

Keep the fresh independent reviewer separate from the editing worker. A packet's
instruction to return an update must not cause recursive Horizon goal creation in
the worker. State mode itself creates no native goal, task, schedule, or API client.

## Conversation-mode increments

1. Read `status SLUG` before work. Continue only when status is active and
   `budget_exhausted` is false. Inspect the recorded next action. Start an
   increment or a review only if it, `check`, the review and the final report can
   all finish before the `deadline` that `status` prints. Near the deadline, leave
   the work buildable, run `check`, and give the final report.
2. Do one useful increment. Never weaken the gate, a reviewer's test, or the
   requirement list to fit your output: do not delete a failing case, widen a
   tolerance, add an exception, or change an expected value to match the code.
   If a check is wrong, fix it, say what changed and why in that check's `--did`,
   and list every gate change in the final report.
3. Run this command **once** to verify and save the increment. Read its JSON even
   on a nonzero exit. Work that crosses the wall-clock deadline is still recorded.

```bash
python3 "$HARNESS" --runtime codex --project "$PROJECT" check "$SLUG" \
  --did "Concrete change and evidence" --next "Next useful action"
```

A command pass does not complete the goal. Exit codes: 0 command pass, 1
failed/skipped/awaiting review, 2 error. Continue from the saved next action
while budget remains.

When you believe the work is done, run `close SLUG`. It completes the goal if a
passing gate result covers the current files and nothing else is missing;
otherwise it lists what is missing.

```bash
python3 "$HARNESS" --runtime codex --project "$PROJECT" close "$SLUG"
```

- If it says a review is required, dispatch the fresh reviewer with the
  `review_brief` it printed, record the verdict with `review`, then run `close`
  again.
- If it says a baseline test file changed, stop and ask the user (or a fresh
  reviewer) whether the change is legitimate. Only then run
  `approve-test-change`, naming who approved it and why. Never approve a
  changed test on your own judgment.

```bash
python3 "$HARNESS" --runtime codex --project "$PROJECT" approve-test-change "$SLUG" \
  --path "tests/test_parser.py" --reason "Approved by the user: the old assertion encoded the bug"
```

A skipped check never blocks completion. A pass recorded in an earlier session
still counts if the files are unchanged. When the budget is spent and `close`
cannot complete, it records the goal as finalized (`budget_exhausted`); give the
final report. `close --if-ready` never runs the gate.

## Independent review

When `close` (or `check`) reports that a review is required, it prints
`review_brief` and `review_file`. Dispatch a **fresh independent reviewer** with the
available Codex subagent tool; in this desktop runtime it is `collaboration.spawn_agent`
with `fork_turns: "none"`. Wait for it to finish with the host's wait tools
before anything else. Send it the `review_brief` unchanged, except that you replace
`<<ARTIFACTS>>` with the artifact paths and `<<HOW_TO_RUN>>` with the commands a
user would run (a goal started without `--gate-path` also needs `<<REQUEST>>` and
`<<REQUIREMENTS>>` filled in). Add nothing else: no diagnosis, design choices,
expected values, explanations of earlier findings, or questions that point to an
answer. If no reviewer is available, report the unmet requirement. Never
dispatch a reviewer for a last `check` you run before stopping, whatever
`review_required` says; a coverage goal sets it on every check.

The reviewer writes its reply to `review_file`. Pass that file, unedited, as
`--evidence-file`. Record every verdict, FAIL included, with
`review --result fail|pass` before changing anything. Record `pass` only when
every requirement line is PASS, or NOT REVIEWED on a line tagged
`[unverifiable]`. The brief ends with a `VERDICTS` block that the reviewer fills
in. The CLI refuses a pass when that block is missing, incomplete or holds a
FAIL or UNVERIFIED, when the reply has a `Rk: FAIL` line or a FAIL headline
elsewhere, or when it does not echo the checkpoint hash; record `fail` instead. A FAIL or UNVERIFIED line is not a pass:
spend the next increment on those lines while budget remains, or stop and report
them as unverified. A reviewer's expectation ("should handle", "by design",
"harmless") is not evidence. Use the real reviewer handle and the hash the
reviewer was given and echoed. Never record a review against a later checkpoint
or another goal. If anything changed after the reviewer saw it, run `check` and
a new review.

```bash
python3 "$HARNESS" --runtime codex --project "$PROJECT" review "$SLUG" \
  --result pass --judge "$JUDGE_HANDLE" --hash "$CHECKPOINT_HASH" \
  --evidence-file "$REVIEW_FILE"
```

After a recorded pass, run `close` again. It refuses on a missing or failed
review, changed artifacts or gate, or an unreadable snapshot. After any change
to reviewed files, run `close` for a new brief and review again, within the
round limit. The ledger records reviewer provenance; it cannot prove
independence by itself.

Never end a turn, or schedule a wakeup (an automation or thread heartbeat),
while your own reviewer or gate is running. When the reviewer passes an unchanged
checkpoint, run `review` and then `close` at once.

A finding blocks only if it bears on whether a requirement line holds, that is,
it makes the line FAIL or UNVERIFIED. Record other findings as non-blocking gaps
for the final report, and do not start an increment or a review for them. Run
at most two review rounds per goal in a session unless the user allows more.
Because any edit after a review needs a new review, after a second failed review
report the open lines instead of fixing and re-briefing. Never narrow a brief or
pre-explain a finding to get a pass.

## Limits and resumption

Stop new increments at the budget and give the final report. `abandon` closes
incomplete work; `reopen` may extend its budget only with user authorization.
Wall-clock budgets keep running between sessions. Do not resume an exhausted
goal: continue through a new goal as described under Earlier goals, or stop and
report. A skipped command needs changed inputs or `check --force` for external
state. Missing tools, unreadable inputs, and timeouts retain a failed checkpoint:
diagnose the cause, never fabricate a pass. Use `--timeout SECONDS` for a slow
gate. To record the gate's current result without spending an increment, run
`observe`; it exits 1 unless the gate passed.

```bash
python3 "$HARNESS" --runtime codex --project "$PROJECT" observe "$SLUG"
```

Read [the CLI reference](references/commands.md) for watch paths, lifecycle
commands, the review brief, and upgrade rules.

Disk checkpoints survive interruption; they do not wake the application.
Only when the user requests scheduled continuation, use the available Codex
automation tool with a thread heartbeat. Read status on every firing, stop on
completion or exhaustion, and stay quiet unless something actionable changes.
Do not create a native Codex goal merely because this skill loaded.

## Final report

End in the same turn with one complete report:
1. Each requirement line and its status: verified by gate, verified by reviewer,
   judgment call (name the reading you chose), unverified, or not met, each with
   evidence. Put unverified and not-met lines first.
2. Reviewer findings you did not resolve, quoted; every gate change; superseded
   requirements.
3. The goal's slug and status. `complete` means your gate and reviewer passed.
   Never present it, or a reviewer's PASS, as proof that the task is done.
Never promise to report something later. If you stop without completing, make the
ledger say why: with budget left, run a last `check` whose `--next` lists the open
lines; with the budget spent, or when the open lines cannot be verified here,
`abandon` with a reason that lists them.
