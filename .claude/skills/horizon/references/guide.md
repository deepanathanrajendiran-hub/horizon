# Horizon for Claude Code: the full guide

The skill's SKILL.md holds the rules every session needs. This guide is the full manual: earlier
goals, coverage gates, state mode, the multi-round review, scheduling and the details behind each rule.

Keep the objective, evidence, budget, and next action on disk using the shared CLI.
Resolve `HARNESS` to the absolute `scripts/harness.py` beside the **SKILL.md
you actually loaded**, even for a global installation. Set `PROJECT` to the
absolute path of the directory this session started in (Claude Code's primary
working directory, not the shell's directory after a `cd`), unless the user
names another project. Never use a subdirectory you create or enter during the
work. Quote both paths.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" doctor
python3 "$HARNESS" --runtime claude --project "$PROJECT" status
```

If the session-start message gives `HARNESS`, `PROJECT` and `GATES_ROOT`, use
those values for the whole session; `GATES_ROOT` is the `gates` path. If it names
an active goal for this request, go straight to Resuming: do not run `doctor` or
`status`. If it says there is no active Horizon goal, skip "Earlier goals" and do
not run `status`. Otherwise run `doctor` first and use the `state` and `gates`
paths it prints for the whole session.

## Resuming

When a goal for this same request is already active, resume from what the session-start message shows: the next action, the gate
command and its latest result with the failing checks by name, the notes file,
the files the last commit changed and every file with its line count. Do not run `status` to get these again, nor `ls` or `wc` for the files it
lists.
Skip "Earlier goals" and "Before `start`". Do not re-read request.md,
requirements.md, the gate or a large input unless the next action needs part of
it, and then read only that part. Make your first edit within your first few
tool calls; never spend a session only reading.

A session can end without warning, before `check`. Keep the next action current
with `note`: one concrete edit, naming the file and the input lines it needs,
never a reading plan, and no session numbers. Update it after each write.
`observe` takes `--next` too. When you find a bug you will not fix in the
same call, record it at once with `note SLUG --finding "..."`: findings are
shown at every session start until `note SLUG --clear-findings`. Record an
environment problem that is not a code bug (a broken system library, a
sandbox limit) once with `--fact`, and do not diagnose it again. Record the exact
command that worked, interpreter path included, the same way, and copy it from
the session-start message instead of retyping it. When the
session-start message says the next action may already be done, check that
file once and go on to the next unbuilt part; it also lists the files changed
since the next action was recorded and the placeholders still to build.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" note "$SLUG" --next "Make parse_date in src/dates.py accept the ISO forms R3 lists"
```

## Large inputs

Read a large input with the Read tool, up to about 800 lines per call. Never `cat`
it (the output is cut to a short preview), and never read it in small slices:
a small read costs a call, as a large one does. Read a large input whole,
once, before you write code that depends on it. The goal's notes file (`notes_file` in
`start` and `status SLUG`) keeps only what saves a re-read: for each part of the
input, a `## ` heading with its line range and the file it maps to, and the
signatures and rules you will need again. Never record in notes what is or is
not implemented; the files and the gate show that. Write notes in the same Bash
call as an edit or a `check`, never in a call of their own.

## Shell

The shell may be zsh on macOS. There is no `timeout` command: use the CLI's
`--timeout` on `start`, `check`, `observe` and `close`. `sed -i` needs `''`.
Quote a line of `=` signs, and do not keep a command in a variable. Shell
variables set in one command are gone in the next: paste literal paths (the
session-start message gives the full Horizon command), never `$HARNESS`. Never take a
test run's exit status through a pipe. Run Python from the project root, and add
`__pycache__/` to `.gitignore`.

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
Create a goal when the user requests bounded continuation or invokes `/horizon`.

## Before `start`

Do these steps before changing any deliverable; scratch experiments elsewhere are
fine. Note the time when you first read the request: a time limit the user states
counts from their message, so give the goal the minutes that remain when you
create it. Let `GATES` be the `gates` path `doctor` printed (`GATES_ROOT` in the
session-start message), plus `/NAME`, where NAME
is a short label for this request that no directory there already uses.

1. Save the request verbatim to `$GATES/request.md`. Read the inputs it names, the
   code it concerns, and that code's existing tests and callers. For a large input,
   read its outline now and the rest right after `start`.
2. Write `$GATES/requirements.md` with one numbered line (R1, R2, ...) for each
   requirement, reported symptom, deliverable and constraint in the request, quoted
   in the request's words. Keep qualifiers such as "exact", "all" and "unchanged",
   and keep stated limits. When the request names a class ("all X", "every Y"),
   list each member. You need not cite every
   section of a long input: `close` lists the ones neither quoted nor built (see Independent review). Tag each line `[gate]`, `[review]` or `[unverifiable]`;
   tag `[review]` only a line a reviewer can judge by running the work, and tag lines
   that state no checkable behaviour
   ("the functions cooperate") `[unverifiable]`. If a
   line admits more than one reasonable reading, also tag it `[judgment]` and
   record the reading you chose. This file states what was asked: leave out your
   diagnosis, method and planned fix. The reviewer tests `[unverifiable]` lines it
   can and answers the rest `NOT REVIEWED`; the final report lists those lines
   first.
3. Write the gate in `$GATES` as checks of the `[gate]` lines, each check naming its
   line (a large build may start with a
   small gate and grow it with the work, as "Building" below says). Exercise the entry points the request names, called the way existing
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
   baseline observation and charges no turn. In Claude Code, `start` also installs
   Horizon's hooks when they are missing and reports `"hooks": "installed"`; after
   `uninstall-hooks` it leaves them out.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" start "Requested outcome" \
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

Claude Code loads hooks when a session starts, so hooks installed by `start` take
effect from the next session. Until then, run `close` yourself at the end, and
`step-reclaim` if `status` shows an attempt you didn't start.

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
`.claude/harness`.

### Building

Make the deliverable usable first. In the first session, create the entry point a user calls (the package,
module or command the request names) and every module it imports, so that importing or running it works before
its parts are filled in, and make that the gate's first check. A part that cannot be imported scores as nothing
built, however complete it is. When the request says how the work is installed or run (an install script, a
package install, a command), that first check does it that way, in a clean copy, never through `PYTHONPATH` or the
source tree: a gate that imports from the source tree passes a project nobody can install. If no command can run
at all, write the code anyway and record that with `note --fact`.

Write straight into the real files, never into scratch parts to join later: a session can end at any call, and
parts not yet joined are lost. A long file goes in as several appends to the file itself. Keep the work runnable
after every write, and run what you wrote in the same session, the way a user would: code never run is a guess.

Build before you verify. While any part of the request is unbuilt or a placeholder, the next action is building
it, not new checks or a review. Take the largest part still unbuilt next, not the next one in the order you read
it, and give no part more than about a session while others are placeholders. Grow the gate with the work: add
checks for the part you just built, from the request and its own examples, each naming the requirement line it
checks (`test_r3_...`). `close` refuses while a `[gate]` line has no check naming it; a line no command can check
is a `[review]` line, so retag it and say why. A check that passes the first time it
runs is not progress. A check from an example in the request asserts the example's literal result: when your
code disagrees with it, change the code, not the check. An example can be wrong: drop such a check only when the
rest of the request contradicts it, and record why with `note --fact`. When the session-start message says
`Plateau`, do what it says in that session.

Where the request is silent or only illustrative, follow the conventions of the project and its language: its
existing names, patterns, error types and layout. Never assert your own guess in the gate: a check of behaviour
the request does not state carries a `# judgment` comment, and it is not evidence that the work is right.

### A spec that lists a Python API

When the request comes with a spec that names a Python package's public modules, classes and functions, `start
--surface-spec SPEC --package NAME` (with `--gate-path`) writes `$GATES/test_surface.py` before the baseline:
one test per public name the spec gives. It fails when a package directory lacks its `__init__.py`, since such a
directory still imports. Delete a name from it only when the spec says it must not exist, or when it is a file
name, a URL or a local variable in an example. It checks names only, so grow the gate with checks of behaviour.

Without the flag, `start` writes the same file whenever the request names a spec for a Python package (`start`
reports it as `surface_gate`). `close` runs it with the Python your gate command or gate script names, whatever
the gate runs, and completes only once every name imports: one missing module fails every test that imports it.
`check` and the session-start message list what is still missing. When your gate names no Python, the result is
reported but never stops `close`.

When that spec also says the package gets installed, `check` and `close` also install it, offline, from a clean
copy, and import it from another folder. `close` completes only once that works and any setup script the spec names
exists. An import from the project folder always works, so it proves nothing: users install first. Give the package
a `pyproject.toml` whose build backend is already installed, then run `check`, which reports a failure as
`install_problem`. Do not build an install environment of your own: there is no network, and pip without
`--no-index` retries it for minutes, past the session's end.

`--surface-spec` also writes `$GATES/spec_examples.py`: the spec's own runnable examples and the signatures it
lists. It is advisory, not part of the gate, because specs are often wrong. `examples SLUG` runs it. For each
disagreement, fix the code when the spec's prose agrees with the example; otherwise run `waive-example SLUG ID
--reason "SPEC line N ..."`.

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

In Claude Code, use a fresh supported `Agent` invocation with a general-purpose
worker for each reserved increment. Retain its actual host handle and terminal
result. Follow the user's model preference. If the current Agent interface cannot
provide the required fresh context or observable lifecycle, report that state mode
is unavailable in that session.

After the worker stops, inspect its evidence and proposed changes, save its terminal
status evidence, and finish the attempt. Allow at most one formatting-only repair
request to that worker. Further implementation requires another reserved attempt.
Use existing independent review and completion checks after the attempt closes.

If `status` shows an attempt you didn't start, its session may have died. An
attempt holds a lease that the PostToolUse hook renews. The session-start hook
reclaims the attempt automatically once the lease goes 600 s without renewal,
or 300 s when a different session owns it. Until the hooks are loaded, or if a
reclaim is still needed, run `step-reclaim` yourself. It refuses while the lease
is live.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" step-reclaim "$SLUG" \
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

1. Read `status SLUG` before work, or the session-start message when it shows
   the goal's budget. Continue only when status is active and
   `budget_exhausted` is false. Inspect the recorded next action. Start an
   increment or a review only if it, `check`, the review and the final report can
   all finish before the `deadline` that `status` prints. Only when that deadline
   is minutes away, leave the work buildable, run `check`, and give the final
   report. A session's length is not the goal's deadline: when a session is
   limited, keep building until the session is cut off, because your commits and
   `note` carry over. Never end a session to hand off while the goal is active
   and budget remains; if you stop, the Stop hook sends you back once with the
   next action. When no one is there to answer, do not stop to ask a scope
   question: record your ruling and why in the notes file and in `note --next`,
   and build on it.
2. Do one useful increment. Never weaken the gate, a reviewer's test, or the
   requirement list to fit your output: do not delete a failing case, widen a
   tolerance, add an exception, or change an expected value to match the code.
   If a check is wrong, fix it, say what changed and why in that check's `--did`,
   and list every gate change in the final report.
3. Run this command **once** to verify and save the increment. Read its JSON even
   on a nonzero exit. Work that crosses the wall-clock deadline is still recorded.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" check "$SLUG" \
  --did "Concrete change and evidence" --next "Next useful action"
```

`check` itself does not complete the goal, but once Horizon's hooks are loaded
the Stop hook runs `close --if-ready` at the end of each turn, which completes
the goal as soon as the recorded evidence allows. Exit codes: 0 command pass, 1
failed/skipped/awaiting review, 2 error. Continue from the saved next action
while budget remains.

When you believe the work is done, run `close SLUG`. It completes the goal if a
passing gate result covers the current files and nothing else is missing;
otherwise it lists what is missing. A `--coverage` goal has no command for
`close` to run: run `check` to get the review brief, record the reviewer's
verdict with `review`, then run `close`.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" close "$SLUG"
```

- If it prints a `review_brief` and every part of the request is built, dispatch
  the fresh reviewer with it, record the verdict with `review`, then run `close`
  again. If it prints `review_failed_lines` or `review_paused` instead, follow
  Independent review.
- If it says a baseline test file changed, restore the file and fix the code
  instead, unless a person (or a fresh reviewer) is there to approve the change.
  Only with that approval run `approve-test-change`, naming who approved it and
  why. Never approve a
  changed test on your own judgment. Baseline tests are the project's own tests
  from before the goal; the checks in the gate directory are yours, so fix a
  wrong one yourself, with no approval.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" approve-test-change "$SLUG" \
  --path "tests/test_parser.py" --reason "Approved by the user: the old assertion encoded the bug"
```

A skipped check never blocks completion. A pass recorded in an earlier session
still counts if the files are unchanged. When the budget is spent and `close`
cannot complete, it records the goal as finalized (`budget_exhausted`); give the
final report. `close --if-ready` never runs the gate, and neither it nor the Stop
hook completes a goal whose files are unchanged since the baseline. An explicit
`close` can; it then reports `"unchanged_since_baseline": true`, which the final
report must state.

## Independent review

A goal gets one review round. After it, `close` completes on a passing gate and
lists what went unreviewed (`unreviewed`), once you have fixed any FAIL lines
the round found (`close` names them) in the work itself: while no file outside
the gate directory and Horizon's own state has changed since the FAIL, `close`
refuses. If a FAIL line is wrong, record why with
`note --finding "Disputed Rk: <reason>"`. So dispatch that round once, as the first
action of a session, when every part of the request is built, never in a
session's last calls; `review_spent` means it has run, and only a reply that came
back (a finished round file or a recorded `review`) spends it. Send each brief
once: if the reviewer is cut off, or its reply holds only its header, do not send
it again yourself. `close` prints `review_pending` until a later session starts
(or 15 quiet minutes pass); after that it hands the round out once more (two
hand-outs per goal), and once no reviewer has answered either brief it prints
`review_unavailable` and sends no more. A round that never came back never lets
`close` complete: the goal then completes only on a passing review, or is
finalized when its budget is spent. With `review_pending` or `review_unavailable`,
keep building and editing the work; never leave it unchanged for a reviewer. A
reviewer still appending verdicts holds the goal until it ends or has been quiet
for 15 minutes. If a reviewer runs in the background anyway, keep building while it
runs and never wait or search for its reply. A reply that would pass but for its
UNVERIFIED lines (checks the environment cannot run) does not block `close`: those
lines go to `unreviewed` and the final report.

For a long request, or a Markdown file it names (200 lines or more), `close` also
lists each section heading that no requirement line quotes and whose function, class
or path the work does not define, as advice (`sections_advisory`) that never
stops `close`. Build the parts the request needs, but never replace working code,
add files the request's own layout does not list, or write requirement lines
only to quote headings (one line saying why, if it is out of scope, is enough). A section that gives Python code
counts as built only when the work's definition matches the code the request gives:
a class needs every base class it gives, a function every parameter it names.
Those do stop `close`, and no reviewer is sent while any remain; `close` lists
them with both versions.

Build first, review last. Dispatch a reviewer only when you believe every part
of the request is built and your gate checks it. While parts of the request are
unbuilt or unchecked, spend the session extending the gate and building, even if
`close` prints a brief. If `close` prints `review_failed_lines`, an earlier
reviewer of these same files found those lines FAIL and nobody recorded it:
record each file in `review_failed_files` with `review --result fail` (it is the
evidence file), then fix the lines, or add a gate check that shows a line holds,
and run `check`; do not dispatch a reviewer. If it prints `review_paused`, these
files have had four review rounds: go back to building, as it says. If every
part of the request is already built and checked, stop and give the final
report, listing the lines no reviewer passed as unverified. Dispatch a reviewer
at the start of a session, with most of the session left, not near its end.
A reviewer's calls count against a session that is limited in length: when a
round ends without verdicts, go back to building rather than dispatching
another. 

A passing gate means your checks pass, not that the request is met. While budget remains, extend the gate to the
next part of the request it does not check yet, preferring parts that are not built yet: every item the request
enumerates, not only the examples it shows. When an example or a caller fails against your work, fix the code,
not the caller.

When `close` (or `check`) reports that a review is required, it prints
`review_brief` and `review_file`, unless it prints `review_failed_lines` or
`review_paused` instead (above). Dispatch a **fresh independent reviewer** with
Claude Code's `Agent` tool: a fresh `subagent_type: "general-purpose"` invocation in the
foreground (`run_in_background: false`), and wait for its result in the same
turn. Send it the `review_brief` unchanged, except that you replace
`<<ARTIFACTS>>` with the artifact paths and `<<HOW_TO_RUN>>` with the commands a
user would run (a goal started without `--gate-path` also needs `<<REQUEST>>` and
`<<REQUIREMENTS>>` filled in). Add nothing else: no diagnosis, design choices,
expected values, explanations of earlier findings, or questions that point to an
answer. If no reviewer is available, report the unmet requirement. Never
dispatch a reviewer for a last `check` you run before stopping, whatever
`review_required` says; a coverage goal sets it on every check.

A session can end before its reviewer returns. The reviewer records each
verdict in `review_file` as it goes, and the next `close` on the same files
prints a new brief whose `review_carried` lines are already PASS: dispatch a
fresh reviewer with it as usual. If `review_carried` lists every requirement
line, an earlier reviewer finished but its reply was never recorded: record the
newest file in `review_carried_from` with `review`, and dispatch a reviewer only
if the CLI refuses it.

When earlier rounds were cut off, the brief covers only the lines in
`review_scope`, and the other open lines read NOT REVIEWED; a round that decided
nothing gets a smaller scope next time. Dispatch the brief unchanged. If the
reviewer passes every line in its scope, record nothing and run `close` for the
next brief, which carries those lines; record the reply with `review --result
fail` if it has a FAIL or UNVERIFIED line. Rounds whose scoped lines all pass
do not count toward the two-round limit.

The reviewer writes its reply to `review_file`. Pass that file, unedited, as
`--evidence-file`. Record every verdict, FAIL included, with
`review --result fail|pass` before changing anything. Record `pass` only when
every requirement line is PASS, or NOT REVIEWED on a line tagged
`[unverifiable]`. The brief ends with a `VERDICTS` block that the reviewer fills
in. The CLI refuses a pass when that block is missing, incomplete or holds a
FAIL or UNVERIFIED, when the reply has a `Rk: FAIL` line or a FAIL headline
elsewhere, or when it does not echo the checkpoint hash; record `fail` instead. A FAIL line is not a pass: fix
it in the work while budget remains. An UNVERIFIED line (a check the environment
cannot run) needs no fix: report it as unverified. A reviewer's expectation ("should handle", "by design",
"harmless") is not evidence. Use the real reviewer handle and the hash the
reviewer was given and echoed. Never record a review against a later checkpoint
or another goal. If anything changed after the reviewer saw it, run `close` for
a new brief (`check` for a `--coverage` goal) and a new review.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" review "$SLUG" \
  --result pass --judge "$JUDGE_HANDLE" --hash "$CHECKPOINT_HASH" \
  --evidence-file "$REVIEW_FILE"
```

After a recorded pass, run `close` again. It refuses on a missing or failed
review, changed artifacts or gate, or an unreadable snapshot. After any change
to reviewed files, run `close` for a new brief and review again, within the
round limit. The ledger records reviewer provenance; it cannot prove
independence by itself.

Never end a turn, or schedule a wakeup (`ScheduleWakeup`, `/loop`, cron), while
your own reviewer or gate is running. When the reviewer passes an unchanged
checkpoint, run `review` and then `close` at once.

A finding blocks only if it bears on whether a requirement line holds, that is,
it makes the line FAIL or UNVERIFIED. Record other findings as non-blocking gaps
for the final report, and do not start an increment or a review for them. Run
at most two review rounds per goal in a session unless the user allows more
(Horizon itself allows one per goal unless `HORIZON_REVIEW_ROUNDS_PER_GOAL` is set).
After the goal's review round, fix any FAIL lines it found and close; do not
re-brief a reviewer. Never narrow a brief or
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
python3 "$HARNESS" --runtime claude --project "$PROJECT" observe "$SLUG"
```

Read [the CLI reference](commands.md) for watch paths, lifecycle
commands, the review brief, and upgrade rules.

In an unattended run no one will answer or approve anything. When the runner
sets `HORIZON_UNATTENDED=1`, the Stop hook sends an active goal with budget left
back to work up to five times a session (as many times as `HORIZON_SESSION_CALLS`
when the runner gives its per-session call budget), so do not stop to wait for a
person or a reviewer. Once `close` completes the goal, the hook sends the session
back to make the work more correct against the request until its budget ends. Take a check the environment blocks (a write outside the project, the
network, a port) out of the gate, retag its line `[review]`, record it with
`note --fact`, and keep building.

Disk checkpoints survive interruption; they do not wake the application.
Only when the user requests scheduled continuation, use `/loop` for an open
Claude Code session or an available Desktop scheduled task for later local
sessions. Cloud routines use a separate environment. Read status on every firing
and stop on completion or exhaustion.

## Final report

Give the final report only when the goal is complete, its budget is spent, or a
person ends the work; a session that is merely ending is none of these. End that
turn with one complete report:
1. Each requirement line and its status: verified by gate, verified by reviewer,
   judgment call (name the reading you chose), unverified, or not met, each with
   evidence. Put unverified and not-met lines first.
2. Reviewer findings you did not resolve, quoted; every gate change; superseded
   requirements.
3. The goal's slug and status. `complete` means your gate and reviewer passed.
   Never present it, or a reviewer's PASS, as proof that the task is done.
Never promise to report something later. If a person stops the work before it is
complete, make the ledger say why: with budget left, run a last `check` whose
`--next` lists the open lines; with the budget spent, or when the open lines cannot be verified here,
`abandon` with a reason that lists them.
