# Horizon CLI

Codex and Claude Code call the same Python implementation. Set `HARNESS` to an absolute
skill entrypoint (or `harness/cli.py`), `PROJECT` to the working project, and
`RUNTIME` to `codex` or `claude`. Python 3.10+ and a POSIX environment are required;
there are no third-party runtime dependencies. Quote paths containing spaces.

```bash
python3 "$HARNESS" --runtime "$RUNTIME" --project "$PROJECT" doctor
python3 "$HARNESS" --runtime "$RUNTIME" --project "$PROJECT" --help
```

Global options precede the subcommand. Goals always belong to the project.
`doctor` prints resolved paths without creating files, including `gates`, the
directory under which each goal keeps its request, requirements and gate. Automatic project discovery
uses the nearest ancestor with a runtime marker (`.agents`/`.codex` or `.claude`),
then Git, then the current directory. Give `--project` the intended working root
when operating from an otherwise unmarked nested directory.

## Goal lifecycle

| Command | Purpose |
| --- | --- |
| `status [SLUG]` | List short goal rows, or inspect one goal (budget, deadline, latest checkpoint, notes file) |
| `start OBJECTIVE --gate COMMAND [--review CRITERION] [--gate-path DIR] [--timeout N]` | Command verification with optional independent review |
| `start OBJECTIVE --coverage CRITERION [--gate-path DIR]` | Independent artifact review without a command |
| `check SLUG --did TEXT --next TEXT [--parts 1-4,7]` | Run one check and checkpoint completed work; `--parts` saves parts of an open request check as run; prints `review_brief` and `review_file` when a review is due |
| `review SLUG --result pass\|fail --judge HANDLE --hash HASH --evidence-file PATH` | Record the actual review of the checkpoint's snapshot |
| `complete SLUG` | Require all configured checks on current artifacts |
| `observe SLUG [--role baseline\|resume\|final] [--timeout N] [--next TEXT]` | Run the gate now and record the result; charges no turn |
| `note SLUG [--next TEXT] [--finding TEXT] [--fact TEXT] [--clear-findings]` | Record the next action, an open finding or an environment fact now; runs nothing and charges no turn |
| `start ... --gate-path DIR --surface-spec FILE --package NAME` | Write `DIR/test_surface.py` (as `surface`) before the baseline gate run |
| `surface --spec FILE --package NAME --out FILE` | Write a pytest gate with one test per public name a spec gives for a Python package; needs no goal |
| `close SLUG [--if-ready] [--timeout N]` | Complete when recorded evidence covers the current files, else say what is missing |
| `approve-test-change SLUG --path P --reason R` | Record who approved a changed baseline test, and why |
| `step-reclaim SLUG --attempt A --expected-revision N [--session S]` | Close a state-mode attempt whose lease went stale |
| `hook SessionStart\|PostToolUse\|Stop`, `install-hooks`, `uninstall-hooks` | Claude Code hooks and their installation |
| `abandon SLUG --reason TEXT` | Close incomplete work honestly |
| `reopen SLUG --reason TEXT [--extra-turns N] [--extra-minutes N]` | Make an abandoned goal active again with an authorized budget extension, or a complete goal whose request is carried on (it keeps its gate, facts and checks; its request check is asked again; one whose time limit is spent needs `--extra-minutes`) |

`status` without a slug returns `{"goals": [...]}` with one short row per goal:
slug, status, goal, gate, review criterion, gate path, next action, abandon
reason, start and completion times, turns used, and the last gate and review
results, and `last_observed` (the newest gate run's result, role and time). Long text is clipped and ends in `…`. A goal file that cannot be read
appears as `{"slug": ..., "error": ...}` instead of stopping the listing. `start` returns the same rows for
the project's other goals as `other_goals`.

## Evidence, close and recovery

`start` runs the gate once as a baseline observation and charges no turn. Before
that run it saves a manifest of the project's test files and a separate manifest
of the gate's own files (project files named in the gate command, and any
`conftest.py`, `pytest.ini` or `tox.ini` in the watched paths); completion
compares against both. The `start` report includes
the baseline result as `observation` (the same shape `observe` and `close` return;
`null` for a coverage goal), and `start` still exits 0 when the baseline gate fails. In
the claude runtime, `start` installs Horizon's hooks before the baseline run when
they are missing (see Hooks below) and reports `"hooks": "installed"`, or
`"hooks_error"` if it could not; the goal is created either way. After
`uninstall-hooks`, `start` leaves the hooks alone until `install-hooks`.

`observe SLUG [--role baseline|resume|final] [--timeout N] [--next TEXT]` runs the gate now and
appends the result to the goal's `observations` list. `--role` defaults to
`resume`. It never skips an unchanged tree and charges no turn. The report has
`observation`. With `--next`, the observation also records the next action.
Only command gates can be observed. Exit 0 if the gate passed, 1 if it did not,
2 on error.

`note SLUG --next TEXT` records the next action at once, with no turn and no gate
run, so a session that ends before `check` still hands it on. `--finding TEXT`
keeps a bug seen but not yet fixed, and `--fact TEXT` an environment problem that
is not a code bug; the session-start message shows both until `--clear-findings`
(facts stay). Each next action records the project's Git HEAD, so the session-start
message can list the files changed since it was recorded and say when a file it
names changed afterwards ("may already be done"). A goal's
`next_action` is the newest of the last checkpoint's, note's and observation's.
A session that finds an active goal with no next action and no checkpoint is
told so at its start ("No `check` has saved this goal yet"), and to run `check`
as soon as something it built runs: until then nothing but the files carries
over and none of Horizon's checks has run (thin15 protobuf-to-pydantic: five
sessions without one, each reading the request and the source again).
`status SLUG` also reports `last_observation` (the newest gate run, with the last
600 characters of its output), and the Claude Code session-start message adds
`Last gate run:` when that run is newer than the last checkpoint. `status SLUG`
stays small: the latest review is listed without its evidence (a failed review
keeps its FAIL and UNVERIFIED lines as `fail_lines`), and gate outputs are
clipped to their last 600 characters. `check` and the other commands keep the
full output.

`start` creates the goal's notes file, `<state>/notes/SLUG.md`, for what the agent
learns from large inputs, part by part. `start` and `status SLUG` report it as
`notes_file`, and `status SLUG` lists its `## ` headings as `notes_sections`. The
notes file is bookkeeping: writing it never changes the checkpoint. `start
--timeout N` sets the baseline gate run's timeout.

`close SLUG [--if-ready] [--timeout N]` completes the goal when a passing gate
result (a checkpoint or an observation, from this session or an earlier one) covers
the current files and nothing else is missing. It runs the gate itself when no
pass covers the files, unless `--if-ready` is given (then it only completes or
finalizes). Otherwise it returns `missing`, with `observation`. Rules:
- A skipped check never blocks completion.
- `close --if-ready` (and the Stop hook) never completes a goal whose files are
  unchanged since a baseline observation: that pass predates any work. An
  explicit `close` may complete them, and then reports
  `"unchanged_since_baseline": true`.
- A `--coverage` goal has no command to run: `check` records the checkpoint and
  prints the review brief, `review` records the verdict, then `close` completes.
- A review is required only for goals created with `--review` (or coverage gates),
  and only when the gate is not "strong" (tests that existed at baseline and are
  unchanged, gate files unchanged, no gate directory) or a requirement line is tagged `[review]`,
  `[unverifiable]` or `[judgment]`. When one is required and a pass covers the
  tree, the report includes `review_file` and `review_brief`; record the verdict
  with `review`, then run `close` again.
- A changed baseline test file blocks completion until `approve-test-change`. A baseline test is one the project had
  when the session that started the goal began, committed or not: the SessionStart hook records the test files of a
  session that begins with no active goal, and `start` reads that record. A test file the session wrote before
  `start`, or one an earlier goal added (recorded when that goal completes or is abandoned), is the goal's own work
  and is not held. With no such record (no hook ran), every test file there at `start` is baseline. The message
  gives the command that puts a committed file back from the commit the goal began on.
- A goal with a gate directory and a command gate completes only when every
  requirement line tagged `[gate]` is named by a check: its id as written (`R3`,
  `R03`, `R1.2`, `# R3`) or inside an identifier after an underscore
  (`test_r3_...`, `test_r1_2_...`) appears in a file of the gate directory other
  than `.md`, `.txt` or `.rst` files, in a project file the gate command names (a
  pytest node id counts for its file), or in a test file under a project directory
  it names. A bare `r3` (a variable) does not count, and `R1` is not named by
  `R10`, `R1a`, `R1.2` or `test_r1_2`. Only regular text files are read (no FIFO,
  device or binary file; 2 MB each, 20 MB in all), and an unreadable path is
  skipped. Otherwise `missing` says so and `unchecked_gate_lines` lists the ids,
  and neither `close` nor `check` hands out a review brief while any remain. A
  line no command can check is a `[review]` line.
- When the budget is spent and the goal cannot complete, `close` records
  `finalized: {reason: "budget_exhausted", ...}` for the current tree.

Exit 0 when the goal is complete, 1 otherwise, 2 on error.

`approve-test-change SLUG --path P --reason R` records that a changed baseline
test is legitimate. `--reason` must say who approved it and why. Ask the user (or
a fresh reviewer) first. It is refused while a state-mode attempt is open. Exit 0
on success, 2 on error.

`step-reclaim SLUG --attempt A --expected-revision N [--session S]` closes a
state-mode attempt whose lease is stale. A lease is renewed by the PostToolUse
hook and expires 600 s after its last renewal (300 s when a different session now
asks). It returns `StepBusy` (exit 2) while the lease is live. Codex has no
hooks, so nothing renews a lease after `step-begin` or `step-attach`: it lapses
after 600 s while the worker may still be running. In Codex, reclaim only an
attempt whose worker the host confirms has stopped, or that no worker was ever
attached to. Leases live beside
the goal file, in `SLUG.leases.json` in the goals directory, so renewals do not
bump the goal's execution revision.

```bash
python3 "$HARNESS" --runtime "$RUNTIME" --project "$PROJECT" observe "$SLUG" --role resume
python3 "$HARNESS" --runtime "$RUNTIME" --project "$PROJECT" close "$SLUG"
python3 "$HARNESS" --runtime "$RUNTIME" --project "$PROJECT" approve-test-change "$SLUG" \
  --path tests/test_parser.py --reason "Approved by the user: the old assertion encoded the bug"
python3 "$HARNESS" --runtime "$RUNTIME" --project "$PROJECT" step-reclaim "$SLUG" \
  --attempt "$ATTEMPT_ID" --expected-revision "$CURRENT_REVISION"
```

### Hooks

Hooks exist for the claude runtime only; Codex has none. `install-hooks` adds
Horizon's `SessionStart`, `PostToolUse` and `Stop` hooks to the project's
`.claude/settings.json` and clears the opt-out; `uninstall-hooks` removes only
those and writes the opt-out marker `hooks-optout` in the state directory, so a
later `start` does not reinstall them. `hook EVENT` reads
the event JSON on stdin and always exits 0, so a hook never blocks a session.
No hook runs the project's code (its gate command, the spec examples, the surface
gate or the install check): Claude Code runs hooks outside the sandbox it gives the agent's
own commands, and a gate that runs a setup script would write where the agent
cannot. A hook reads saved results; `check`, `observe` and `close` run the code.
- `SessionStart` records the session id and reclaims a stale attempt
  automatically. It then
  reports the `HARNESS`, `PROJECT` and `GATES_ROOT` values to use (also when no goal is
  active, with `No active Horizon goal.` and the files line; in a project that
  has never had a goal the line adds that nothing is saved or checked until
  `start` runs), each goal's budget, deadline,
  gate directory, next action, gate command and newest gate result, whether
  from an `observe` or a `check` (`Last gate run:`), and, for a conversation-mode
  goal with a command gate when no observation, checkpoint or pass covers the
  current files, `The files changed after that run: `check` runs the gate on
  them.` When the output
  lists `FAILED`/`ERROR` lines it gives the failing checks by name, up to 12, and
  the summary line; otherwise the last 300 characters. A message shared by three
  or more failures leads as `Root cause:`, and surface-gate names are counted by
  module (`Missing names by module:`) instead of listed. Then the files changed
  since the next action was recorded (with `may already be done` when the next
  action names one of them), open findings and known facts, the literal command
  that runs Horizon (`Run Horizon as:`, with the hook's own interpreter), the
  notes file and
  its parts, the files the last commit changed (relative to the repository root),
  and the project's files with their line counts, up to 40, tracked files and
  code files first; files in dot-directories, environments, `build`, `dist` and
  `*.egg-info` are left out, and a file over 1 MB shows its size instead. A
  project with more than 200 files is summed up instead: the file count of each
  folder (two levels, the ten largest) and the files that differ from the last
  commit, with their line counts. Last, while a goal is active,
  `Placeholders still to build:` lists source files with functions that have
  no body yet: a docstring at most, then `pass`, `...` or
  `raise NotImplementedError`, and either that raise or a docstring that says
  placeholder. Not listed: a function that does work and raises on one path, a
  method another class of the project overrides, a method of an abstract base,
  a special method other than `__init__`, a raise whose message says who
  must supply the method ("must implement ..."), a function a recorded fact
  names, with its file, as "by design", and a placeholder the project already
  had when its first thin goal started. More lines appear when they apply:
  `The last close passed every check and asked for the work to be gone through
  against the request`, with the parts still to run, from the `close` that asked
  until `check --parts` has saved every part (see **Request check**). After the missing
  names (all of them, by module): `The last gate run ... never finished`, when
  a gate run was killed before it returned (its call's time limit, or the end
  of the session), with the `check --timeout 100` that saves the session's
  state; and `This next action has now opened N sessions in a row`, from the
  third session that starts on the same next action. `install-hooks` gives the hook a 300-second timeout (an older
  30-second one is raised) and keeps a longer timeout you set.
- `PostToolUse` renews the lease of the session's open attempt. While a goal is
  active, it and `PostToolUseFailure` also keep the session's latest call in
  `notes/last-call.json`: the last thing the agent said (read from the end of the
  session's transcript, up to 400 characters), the command or tool input (300)
  and the end of what came back (400). A subagent's calls are not kept, and a
  `check` or `close` call clears the file, since it saves the next action itself.
  The next session's `SessionStart` message ends with `Where the last session
  stopped`, so a session cut off before `check` still hands on what it had found.
- `Stop` completes or finalizes goals whose recorded evidence already allows it
  (the `close --if-ready` rules, so never on files unchanged since the
  baseline); it never runs the gate, and the surface gate and install check
  count only when a `check` or `close` saved their result for the current files
  (otherwise the goal stays active). If a conversation-mode goal is then still
  active with budget left, it blocks the stop once and names each such goal's
  next action: a session's length is not the goal's deadline. The next stop in a
  row (`stop_hook_active`) always goes through, so a person can still be answered.
  With `HORIZON_UNATTENDED=1` (a run with no person, set by the runner in the
  environment Claude Code starts with), the next stop in a row is blocked too,
  with a message that no one will answer or approve, up to
  `UNATTENDED_STOP_BLOCKS` (5) blocks per session; after that a stop goes through.

Claude Code loads hooks when a session starts, so hooks installed by `start` take
effect from the next session.

## Requirements and review

`start --gate-path DIR` records the goal's gate directory. DIR must already hold a
non-blank `request.md` (the request, verbatim). With no `requirements.md` there, the
goal is thin (exp/thin): it has no requirement lines and, without `--review`, no
reviewer; its gate is the command given plus the surface gate, the install check and
the advisory spec examples below. This is fixed at `start`: a goal that began with
a `requirements.md` keeps needing it. Two more things hold for a thin goal:

- **Placeholders.** `close` does not complete a thin goal while the work has
  placeholder functions (the rule above); `check` reports them as
  `placeholders`. A name that imports passes the surface gate even when its
  function only raises. Build each, or record a deliberate one with
  `note --fact "<file> <name> raises NotImplementedError by design"`: the fact
  covers the functions it names in that file, and one that names only the file
  covers none. A function that makes placeholders (it defines a function that
  only raises NotImplementedError and returns it) is listed as
  `name: makes placeholders, each one still to build`, since the functions it
  stands in for are in no source file.
- **Request check.** The first `close` that would complete a thin goal does not:
  `missing` lists the parts of the request (the headings of `request.md` and of
  each Markdown file it names, at the deepest level that gives at most 30 per
  file; sections shorter than 40 lines in a row are one part, which keeps its
  first heading and runs to the next part's line, and a request too short for
  two such parts is cut once near its middle; each part is listed with its
  first and last line, `4 Asyncio API (lines 158 to 199)`, the last part's last
  line being the file's: in the thin15 run the last part was given as "from
  line 158" and three builds read only the section it is named by; a named
  file that `request.md` also holds a whole copy of is listed once, under the
  file: in the thin16 run one session pasted its 357-line SPEC.md into
  `request.md`, and the pass ran 14 parts for 7) and asks
  for one pass through
  them: run the work as each part describes,
  print what comes back, and read it against the request; where they differ the
  code changes, never a test's expectation. The parts are numbered, and the goal
  keeps the list: `check --parts 1-4,7` saves the parts that were run (ranges and
  single numbers; a number past the end names nothing, anything else is an
  error). Until none is left, `check` reports the rest as `request_check`,
  `close` lists them in `missing`, and the session-start message shows them, so
  the pass can span sessions. One `check` saves no more parts than the tool
  calls the session made since the last save (or since `close` asked), and 8 at
  most; where no hook counts the session's calls, only the 8 applies. (thin13
  run: four of nine builds saved every part with `check --parts` calls made one
  after another, nothing run between them; sapp ran three scripts for 17 parts
  and closed in session 3. thin14 run, with that rule and a part for every
  heading: 17 to 22 parts a request, 12 lines at the median; the pass took all
  35 sessions left after the ask in 5 builds, 4 had not finished it, and one
  used 19 of its 20 turns, which is why short sections are joined.) While the
  pass is under way the session-start
  message names the next part with its lines and leaves out the Plateau line,
  the repeated-next-action line and "change a file of the work within your
  first 5 tool calls": the pass is the session's work, and it changes no file
  unless it finds a difference. (Before the thin12 run any `check` after the ask
  released it: protobuf-to-pydantic saved an ordinary checkpoint and closed at 22
  of 42 hidden tests without the pass; of 44 builds asked, 9 ran the work at
  most once before the releasing `check`.) Not asked when the request has fewer than 2 headed
  parts, when the files are unchanged since the baseline, or when the budget is
  spent. The Stop hook's `close --if-ready` never starts it and never completes
  a goal that has not been through it. When the work has unbuilt names (next
  item), the message ends with them.
- **Unbuilt names.** `check`, `close` and the session-start message list, as
  `unbuilt_names`, the names the request gives in code that no source file of the
  project defines: a call (`name(...)`) or a class-like word in a code span of
  `request.md` or a Markdown file it names, outside code blocks. Left out:
  builtins, typing names, names that start with `_`, one-letter and ALL_CAPS
  names, and a dotted name whose first part is not the package, a class or
  `self`. A function, class, assignment, attribute, import or parameter of that
  name defines it, and so does the name as a string; in a file that is not
  Python, the word anywhere. Tests do not count. This is advice and never stops
  `close`: a name may be another library's or an example's own. Nothing is
  listed before the project has source, or for a project of more than 3,000
  source files. The surface gate also reads every name of a list entry that
  opens with several ("- `besselj(n, z)`, `bessely(n, z)` — ...").
- **Other kinds of work.** The surface gate, the spec examples and the install
  check are for Python work: the request shows Python (a Python code block, a
  `>>>` prompt, a `.py` file, pip, pytest or the word), or the project has Python
  source and the request's code blocks are not in another language. The install
  check also needs the request itself to show Python, so "run `npm install`"
  starts none. Placeholders are read from Python source only. A request for Go,
  for a Node project or in one sentence gets the goal, its gate, the notes and
  (with headed parts) the request check, and nothing else.
- **Small gate.** When the surface gate lists N names and the gate's pytest run
  passed fewer than min(N, 10) tests, `close` does not complete and `missing` says
  how many tests ran; `check` reports the same as `small_gate`. A gate whose
  output has no pytest count, or a request that names no package, is not counted.
- **Earlier request.** When the project already has a goal with another gate
  directory, `start` appends the first goal's `request.md` to the new one under
  `## Earlier request for this project (kept by Horizon)`, unless the new text
  already holds it, and reports `earlier_request`.

 A `requirements.md` that is there must hold at
least one requirement line. A requirement line starts with an id (`R1`, `R2`, or
a sub-id such as `R1a` or `R1.2`) after any heading, list, checkbox or numbering
marker and emphasis; the tags `[gate]`, `[review]`, `[unverifiable]` and
`[judgment]` on that line belong to it. `start` adds DIR to the watch set when no
watched path covers it. Keep gate scripts, fixtures and requirement files in the
gate directory inside the watched tree; keep gate logs, generated output and
review evidence outside the watched tree.

When a review is due, `check` returns `review_file`, a path in the temporary
directory, outside every watched path, that is new for each review round, and `review_brief`. The brief is the template
below, filled in with:
- the checkpoint hash;
- the gate directory's request and requirements (each cut at 32,000 characters,
  with a pointer to the full file);
- the runtime's harness directory;
- `review_file`;
- a `VERDICTS` block with one `Rk: ?` line for each requirement line.

```text
Review checkpoint {digest}. Begin your reply with `Reviewed checkpoint: {digest}`.

Request (verbatim):
{request}

Requirements:
{requirements}

Artifacts: <<ARTIFACTS>>
How to run: <<HOW_TO_RUN>>

This session may end before you finish. Start {review_file} with the line
`Reviewed checkpoint: {digest}`; then, as soon as you decide a requirement,
append its line to that file: `Rk: VERDICT - <command> - <what you observed>`.
A later reviewer of this checkpoint continues from the lines you recorded.

Build your own checks from the request. Do not create, change or delete files
inside the project: put any scripts and outputs in a new directory next to
{review_file}, and leave the project's git state as you found it. Do not read
or run anything under {harness_dir}, an earlier reviewer's files, or the
author's helpers. Call the code the way its existing callers and tests do,
including the repeated, boundary and multi-step cases the request implies.
Where the request is ambiguous, choose your own reading and say where the
artifact's reading differs.

For each requirement, give the command you ran and what you observed. A
requirement you could not test is UNVERIFIED, and so is one where a caveat
bears on whether it holds. Test a line tagged [unverifiable] if you can; if you
cannot, its verdict is NOT REVIEWED. If a symptom the request reports still
reproduces, that requirement FAILS even when a comment, docstring or design
note justifies the behaviour. Do not repeat earlier reviewers' lines. List
anything else under `Non-blocking:`. Keep the review under ten minutes and
within about six tool calls: test the riskiest lines first, and append each
verdict to the file as soon as you decide it.

End your reply with this block. Change each `?` to exactly one of PASS, FAIL,
UNVERIFIED or NOT REVIEWED, and write nothing else on those lines:

VERDICTS
{verdicts}
END VERDICTS

Before returning, replace the contents of {review_file} with your complete reply.
```

The agent replaces only `<<ARTIFACTS>>` and `<<HOW_TO_RUN>>`. A goal without
`--gate-path` also keeps `<<REQUEST>>`, `<<REQUIREMENTS>>` and `<<VERDICT_LINES>>`
for the agent to fill. When the latest review was recorded as a fail, the brief ends with
`Previous:` and that review's FAIL and UNVERIFIED lines and FAIL headlines, each
prefixed with `> `. Do not dispatch a reviewer for a last
check before stopping; a coverage goal reports `review_required` on every check.

A reviewer cut off by a session limit leaves the verdict lines it appended in its
round's file. The next `check` or `close` on the same checkpoint reports them as
`review_carried` (with the files in `review_carried_from`) and pre-fills them as
PASS. Once any earlier round of the goal exists, the brief also covers only a
batch of the open lines, listed in `review_scope` (`null` means all of them):
twice as many as the last round decided, or 4, 2 and then 1 after one, two or
three rounds in a row that decided none. The other open lines read
`NOT REVIEWED`, and the brief ends with a note naming the batch. Lines left out
of a batch are never only `[unverifiable]` ones. A batch takes lines tagged
`[review]`, `[judgment]` or `[unverifiable]` before `[gate]` lines, and lists them
in requirement order. A line counts as carried once some round passed it and no
round failed it.

Two cases get no brief. If any earlier round of these same files, written since
the goal started, has a FAIL line, `check` and `close` report `review_failed_lines`
(the lines as written) and `review_failed_files`, and add them to `missing`: record
each file with `review --result fail`, so later briefs quote it under `Previous:`,
then fix the lines and check. And after four rounds on unchanged files, unless the
carried lines already cover every requirement line, they report `review_paused`
(also in `missing`), which sends the agent back to building. In both cases
`review_required` stays true and there is no `review_brief` or `review_file`.
Changed files are a new checkpoint and start with fresh rounds.

`review` stores the reviewer's reply whole up to 16,000 characters. Beyond that it
keeps the reply's head and tail. A pass must contain the first 12 characters of
`--hash`; a fail is recorded without that echo, so no reviewer's FAIL is lost.

The verdicts are read from the reply's block, which runs from a `VERDICTS` line
to an `END VERDICTS` line. Emphasis, heading marks and code fences around the
block are allowed. Each line inside the block must read `Rk: PASS`, `Rk: FAIL`,
`Rk: UNVERIFIED` or `Rk: NOT REVIEWED`, with nothing else on it.

The prose around the block is checked only for two unambiguous signals:
- a `Rk: FAIL` or `Rk: UNVERIFIED` line, where tags or a sub-case in brackets
  may follow the id;
- a FAIL headline, that is, a label such as Verdict, Overall, Result, Status,
  Conclusion, Outcome, Assessment or Summary followed at once by FAIL, or a
  line that is only a bold or bare `FAIL`.

Text such as "the previous FAIL is fixed", tallies like `FAILED: 0`, and test
output inside code fences does not count. Lines quoted with `>`, an earlier
reviewer's findings, are ignored.

A pass is refused when any of these holds:
- the block holds a FAIL or UNVERIFIED, an unreadable line, or two verdicts for
  one id, or the reply has more than one block or an unclosed one;
- the prose has a `Rk: FAIL`/`Rk: UNVERIFIED` line or a FAIL headline;
- the goal has a gate directory, and either its requirements.md has no
  requirement lines, or the reply has no block, or the block misses a
  requirement line, names an id that is not one, or answers NOT REVIEWED on a
  line without the `[unverifiable]` tag.

Without a gate directory, a block is optional. A fail needs only the
40-character floor and the current checkpoint.

## State-mode worker protocol

State mode is opt-in. Use it only when the user requests “use Horizon state
mode,” and add `--execution state` when creating the goal:

```bash
python3 "$HARNESS" --runtime "$RUNTIME" --project "$PROJECT" start "Requested outcome" \
  --execution state --gate "actual verification command" \
  --review "Observable requirements the command cannot establish" --max-turns 5
```

Omitting `--execution` keeps the existing conversation mode. Goals without an
`execution` field are conversation goals, and a goal's mode cannot be converted
after creation. State mode supports one active worker per goal and currently
requires the tested POSIX locking implementation. It adds no model API, service,
scheduler, automatic memory, native goal, or native task. The CLI reserves and
records attempts; the host must dispatch and inspect native workers.

For each increment, the coordinator follows this sequence:

1. Read `status SLUG`, then reserve one attempt with `step-begin`. Reservation
   consumes one `max_turns` unit, including a crash or launch failure.
2. Give a fresh worker the returned `packet`, every referenced artifact, and the
   resolved [Packet and update](#packet-and-update) section from this shared
   reference. The packet's short procedure is a role reminder, not the complete
   operation schema. Do not copy the coordinator conversation. The worker performs
   one increment and returns one update JSON document; it does not run lifecycle
   commands or recursively create a Horizon goal.
3. Immediately bind the actual host handle with `step-attach`. If coordination
   is interrupted, inspect that handle before deciding whether to recover; never
   launch a duplicate merely because the old context is unavailable.
4. After host status or wait tools show that the worker stopped, inspect its
   changes and evidence, save that observation, and call `step-finish`. At most
   one request to the same worker may repair update formatting only. Any further
   implementation needs another reserved attempt and fresh worker.
5. After the attempt closes, use the ordinary fresh independent review and
   completion checks. The editing worker must not act as its own reviewer.

### Packet and update

`step-begin` returns the complete packet plus `packet_path` and `packet_sha256`.
The packet contains the attempt ID, its `base_revision`, objective, gate, watch
paths, budget, compact task state, current evidence freshness, bounded latest
checkpoint/review projections, the worker procedure, and `audit_path`. Give the
worker the saved packet bytes, the artifacts they reference, and this resolved
section as an explicit shared protocol artifact.

The worker returns exactly one schema-version-1 update. `base_revision` is the
revision saved in its packet, not the newer execution revision printed after
attachment. `set` may change only `summary` or `next_action`; `upsert` supplies a
complete entry; `archive` names an existing entry and a reason. Workers cannot
write lifecycle fields, accepted-entry provenance, reviews, or completion state.
A minimal valid update is:

```json
{
  "schema_version": 1,
  "attempt_id": "request-001",
  "base_revision": 1,
  "did": "Investigated the parser and saved the supported finding.",
  "ops": [
    {
      "op": "set",
      "field": "summary",
      "value": "The empty-input branch substitutes zero before conversion."
    },
    {
      "op": "set",
      "field": "next_action",
      "value": "Remove the fallback while preserving valid integer parsing."
    }
  ]
}
```

An upserted entry has `id`, a `kind` of `observation`, `question`, `hypothesis`,
or `failed_approach`, a `statement`, and up to four project-relative evidence
references shaped as `{"path": "evidence/result.txt", "sha256": "..."}`.
Observations and failed approaches require evidence. The coordinator verifies
the files and adds provenance when accepting the update. IDs match
`[A-Za-z0-9][A-Za-z0-9_-]{0,63}`. Statements, `did`, archive reasons, and
`next_action` must be nonempty; a summary may be empty. An upsert uses the
`entry` key and no top-level `id`. Its exact envelope is:

```json
{
  "op": "upsert",
  "entry": {
    "id": "parser-open-question",
    "kind": "question",
    "statement": "Does the parser reject surrounding whitespace as intended?",
    "evidence": []
  }
}
```

An archive uses an existing entry's `id` and a nonempty reason; it does not use
an `entry` key. Its exact envelope is:

```json
{
  "op": "archive",
  "id": "obsolete-finding",
  "reason": "Superseded by the verified parser behavior."
}
```

### Command example

Suppose `step-begin` reports `packet.base_revision` 1. Preserve that value as
`PACKET_REVISION`. After attachment, preserve the response's newer
`execution_revision` as `CURRENT_REVISION` (2 here). These are intentionally
different:

```bash
python3 "$HARNESS" --runtime "$RUNTIME" --project "$PROJECT" step-begin "$SLUG" \
  --request-id request-001
python3 "$HARNESS" --runtime "$RUNTIME" --project "$PROJECT" step-attach "$SLUG" \
  --attempt request-001 --worker "$WORKER_HANDLE" --expected-revision "$PACKET_REVISION"
```

Save the valid update above to `$UPDATE_FILE`, retaining `base_revision: 1`.
Save actual host terminal evidence, for example
`Host wait observed worker HANDLE reach a terminal stopped result`, to
`$WORKER_EVIDENCE`. After independently inspecting both, finish with the current
execution revision:

```bash
python3 "$HARNESS" --runtime "$RUNTIME" --project "$PROJECT" step-finish "$SLUG" \
  --attempt request-001 --expected-revision "$CURRENT_REVISION" \
  --update-file "$UPDATE_FILE" --worker-evidence-file "$WORKER_EVIDENCE"
```

If the attempt was interrupted, first establish worker and gate liveness and
preserve at least 40 UTF-8 bytes of coordinator observation in
`$RECOVERY_EVIDENCE`. Then close the attempt without accepting an update:

```bash
python3 "$HARNESS" --runtime "$RUNTIME" --project "$PROJECT" step-recover "$SLUG" \
  --attempt request-001 --expected-revision "$CURRENT_REVISION" \
  --reason "Worker stopped before returning a usable update." \
  --worker-state stopped --evidence-file "$RECOVERY_EVIDENCE"
```

For recovery from `finishing`, the evidence must contain exactly one standalone,
case-insensitive `gate-state: stopped` or `gate-state: not-started` header. If a
saved `gate_process` exists, use `stopped` and exactly one `gate-pid: N` header
matching its diagnostic PID. Supporting observation prose, excluding these
headers, must be at least 40 UTF-8 bytes and must cover worker and gate
liveness. `not-started` is valid only without a saved gate record. `stopped`
asserts that the gate and its descendants/process group have stopped; PID
disappearance alone is insufficient. These are coordinator assertions, not
authentication or Python liveness checks. Never signal a PID from saved state.

### Attempts, replay, and recovery

Attempt statuses are exact:

| Status | Meaning |
| --- | --- |
| `reserved` | Budget charged and packet saved; no worker handle bound |
| `attached` | Actual native worker handle bound |
| `finishing` | Update, candidate, evidence, and gate diagnostics durably staged |
| `accepted` | Update and verification checkpoint committed; attempt closed |
| `recovered` | Interrupted attempt closed without accepting an update |

Every successful step response contains `slug`, `attempt_id`,
`execution_revision`, `attempt_status`, `replayed`, `update_accepted`,
`attempt_closed`, and `exit_code`. Begin also returns packet fields; finish and
recovery include compact goal/checkpoint state. Repeating begin with the same
request ID, attach with the same handle, an accepted finish with the same
canonical update, or recovery with the same reason/state/evidence returns the
saved result with `replayed: true` and does not spend another attempt or revision.
A conflicting replay is rejected. A concurrent duplicate finish reports
`StepBusy`; do not start another gate or worker.

Recovery preserves workspace edits for inspection on the next increment, saves
a failed checkpoint, accepts no task update, and does not refund the reservation.
Use `not-started` only for an unbound reserved attempt with no gate record. Use
`stopped` for attached or finishing attempts after host tools establish terminal
state. Uncertain liveness or missing fresh-worker tools must be reported rather
than treated as permission to duplicate or kill work.

### Bounds and durable detail

The entire canonical serialized packet, including fixed fields, is limited to
24 KiB UTF-8. A task summary is limited to 2 KiB, next action and each statement
to 1 KiB, active entries to 64, and evidence references to four per entry. Worker
update input is limited to 64 KiB. `did`, archive reasons, and recovery reasons
are limited to 1 KiB. Active entries are never silently dropped to fit.

Each complete latest-checkpoint and latest-review projection, including metadata
and its nested verdict excerpt, has a 2 KiB serialized allowance. Evidence
freshness reasons have a 256-byte serialized bound and explicit truncation flags.
Follow their record references under the packet's `audit_path` for durable goal
records and full evidence references. To inspect an archived entry, open the goal
at `audit_path`, locate the terminal attempt's retained `pending.audit` reference,
and verify/read that content-addressed audit object. Full checkpoint, review, and
entry history stays in goal/audit storage even when the packet carries an excerpt.

Packet freshness is advisory and snapshot-specific. Inspect referenced files
again when an entry is stale, missing, changed, outside the current snapshot, or
marked `unverified`; never present stale evidence as current proof. No filesystem
transaction spans a worker's source edits and its JSON update, so the coordinator
must inspect both before finishing.

State mode does not automatically export the coordinator conversation, worker
transcript, or native tool calls. Packets contain explicit compact state and file
references only. Preserve real host handles, terminal observations, update files,
and any required audit artifacts separately when those facts matter.

`start` defaults to 20 increments and no wall-clock ceiling. Set `--max-turns`
and `--max-minutes` to the requested limits. An increment is a `check`, not a
model message in conversation mode and a reserved worker attempt in state mode.
Read `status` before work; an increment that crosses its time deadline may still
checkpoint. Every goal summary prints `deadline` (the start time plus
`--max-minutes`) and `minutes_left`; both are null without `--max-minutes`. The
clock keeps running between sessions. The CLI is not a scheduler or an execution
sandbox.

Repeat `--watch PATH` on `start` to select the complete set of relevant inputs.
Paths are relative to the discovered project; the default is the whole project.
The fingerprint covers the content and mode of every watched file and of every
file Git can see in the repository (tracked or untracked, not ignored), even
outside the watch paths or the project directory; a nested repository or
submodule counts by its commit and its changes. It leaves out Git's own state, so a commit that changes no file keeps the
checkpoint. It excludes common dependency/cache directories and mutable
`.claude/harness` and `.codex/harness` goals, notes, undo records, and
changelogs. Harness source remains an
input. Directory symlinks are not traversed; watch their resolved targets
explicitly when they are relevant. Use the directory the session started in as the
project. If a deliverable lies outside the project, watch the project and that
path. If a tool appends to a file on every run (a log or audit trail), watch the
deliverables and inputs instead of the directory that holds it. Include
external inputs explicitly, or use `check --force` when external state changes.
Watch selection is a trust boundary: omitted inputs cannot invalidate a result.

Checks return JSON with the exact **pre-gate** fingerprint. If a gate modifies
watched files, completion refuses the stale snapshot; rerun on stable inputs.
Do not replace the stored hash with a post-gate hash. Unchanged inputs skip another
command once; two skips never occur consecutively. Use `--force` for a deliberate rerun.
`check --timeout SECONDS` overrides the 480-second default. Unreadable snapshots
and command errors retain a failed checkpoint. Exit 0 means pass, 1 means
failed/skipped/awaiting review, and 2 means an error; inspect JSON before deciding.

Command-plus-review and coverage goals require a review for the same checkpoint
and criterion. Evidence must contain at least 40 characters, and a pass must
also contain the checkpoint's hash prefix. Those checks are basic guards; the verdict rules are under
[Requirements and review](#requirements-and-review). The calling agent must dispatch a real independent
reviewer. A caller-supplied handle or editable local ledger cannot prove identity
or prevent intentional falsification. `complete` preserves checkpoint evidence
and rejects changed artifacts, gate definitions, and missing/failed reviews.

After upgrading, rerun checks for active goals created by the old implementation.
Old checkpoints lack the current fingerprint/gate-definition contract and cannot
be used directly to complete. Completed history is left intact. Runtime state is
never silently migrated between Codex and Claude.

## Public-surface gate

**Written and held by `start` and `close` (exp/surface-check):**

- **Where it comes from:** with no `--surface-spec`, `start` takes the longest of `request.md` and the Markdown files
  it names that gives a Python package's names. The package is, in order: the name the spec states ("the top-level
  `x` package", "importable as `x`"); the name its title gives, when the spec writes that name in code
  (`# protobuf-to-pydantic` names `protobuf_to_pydantic`); then the name it imports from most and gives most dotted
  names of in code spans. Never a standard module or a file (`setup.sh`). When the titled package has no dotted
  names (a command-line tool), only an imported name can still be the package, so no surface test is written for a
  variable of the examples. (The count alone was wrong for 12 of E2E-SWE's 96 specs: `google` for
  protobuf-to-pydantic, `request` for tremolo.)
- **What it writes:** `$GATES/test_surface.py`, reported as `surface_gate`. A file already there is kept.
- **How it is held:** `close` runs the file with the Python the gate command names, or one a script the command
  runs names, and does not complete while a name fails to import. An absolute path wins; without one, a `python` or
  `pytest` named by a bare name is looked up on `PATH`, and one named by a relative path is found from the project
  or a directory the command enters with `cd` (exp/thin). The install check uses the same Python. The result is
  cached per snapshot in `notes/surface-SLUG.json`.
- **Where misses show early:** `check` reports them as `surface_missing`, and the SessionStart message lists them.
- **No Python named:** the file runs with Horizon's own Python, and what fails is reported as `surface_advisory`
  without stopping `close`.
- **An `__all__` block** belongs to the module the nearest heading above names in code (`## Admin (`pkg.admin`)`),
  else to the package when an `__init__` (not a constructor `__init__(`) is mentioned within 30 lines before it.
- **A sentence that says where everything imports from** ("All public classes and functions below are importable
  from the top-level `pkg` package", "All importable from `pkg.exceptions`.") adds the names the spec documents
  without an import line (exp/thin, after dnachisel and mpmath scored 0 on an ImportError at collection):
  - a list entry that opens with code, `name(args)` or `Name`;
  - a heading that is one class name, and code that opens a heading or is class-like in it.

  The sentence covers its own section when that section documents names, otherwise everything after it. Left out:
  entries under a class's heading or after a line that introduces methods, attributes, parameters, options or
  fields; bare lower-case names; private names and builtins; a sentence about submodules or another package. An
  entry that says `importable from `pkg.mod`` belongs to that module.
- **Aliases:** `import fiddle as fdl` makes `fdl.Config` a reference to `fiddle.Config`, and a name taken from a
  module (`from mpmath import mp`) is never the package.

## Install check

**Held by `close` (exp/install-gate) when the spec that gives the package also says it gets installed** (an install
command, a packaging file, a setup script, or the word install):

- **What it does:** copies the project (without `.git`, `.claude` and caches) to a temporary folder and makes a
  throwaway environment with the project's Python (`python -m venv`). That environment reads the project
  interpreter's own packages through a path file. It runs `pip install -e . --no-build-isolation --use-pep517`
  there with no index, then `python -I -c "import PACKAGE"` from another folder.
- **Setup scripts:** when the spec names a setup script (`setup.sh`), the check runs that script in the copy, with
  the throwaway environment's `python` and `pip` first on `PATH`, in place of the pip command.
- **What it never does:** write to the project interpreter's environment.
- **It fails when:** a setup script the spec names is missing, the script fails or installs nothing, there is no
  `pyproject.toml` and no setup script that installs, pip fails (its error lines are reported), or the import fails.
- **How it is held:** `close` does not complete on a failure, and `check` reports it as `install_problem`. The
  SessionStart message repeats the last one. Results are cached per snapshot in `notes/install-SLUG.json`.
- **When it cannot run:** with no project Python named, the venv module missing, a project over 300 MB, or a
  timeout, the result is reported as `install_advisory` and never stops `close`.

`surface --spec SPEC.md --package NAME --out $GATES/test_surface.py` reads a spec
and writes one pytest test per public name it gives for the package:
`NAME.mod.Name` references, `from NAME.mod import a, b` anywhere in a line (code,
prose in backticks, doctests, comments), `import NAME.mod`, and the names of an
`__all__` given within 30 lines after a mention of `__init__`. Each test imports
the deepest module on the name's path and checks the next name on it, no deeper;
it fails when a package directory on the path has no `__init__.py` (a namespace
package), and a missing dependency is an error, not a missing name. A namespace
package with no directory in the project is another library's: the failure says
to delete the name from `NAMES` and to add no `__init__.py`, which would hide
that library. URLs, file
names and domains (`NAME.dev.py`, `NAME.readthedocs.io`), Python keywords, test
modules and names within two lines above a mention of `AttributeError` are left
out. A public name the spec gives in a private module (`NAME._mod.name`) is also
checked as `NAME.name`: a package re-exports its private modules' public names. The test finds the package (a directory or `NAME.py`) from the working
directory or the test file's own directories, and their `src/`. It reads only the
spec, so it can be written before the code. A spec that gives no names for the
package is an error (check `--package`: the import name, such as `jwt` for PyJWT).

Every module inside the package that the spec's directory tree (`├──`/`└──` lines)
or a file path in its text names (`src/NAME/utils/cache.py`) is checked too, except
`__main__` and tests; when the tree shows the package as a single `NAME.py`, paths
in the text are left out (`NAME/` there is the repository folder). When the tree
lists top-level files that packaging commonly reads (README, LICENSE and
change logs), `test_spec_file_exists` checks each exists at the project root: a
`setup.py` that opens a missing `README.rst` fails to install. The packaging
files themselves (`pyproject.toml`, `setup.py`, `setup.cfg`) count when the tree
lists them, and then `test_packaging_references` checks every file they name
(`readme`, `license` files, `open(...)` in setup.py, `file:` in setup.cfg) and
that each `[project.scripts]` target module exists. When the package itself
fails to import, every name check fails with that one error and the file and
line that raised it (`pkg does not import: ...`).

## Spec examples (advisory)

`start --gate-path $GATES` writes `$GATES/spec_examples.py` and an empty `$GATES/examples_waived.json` whenever it
finds the spec the way the surface gate does (exp/thin), and so does `start --surface-spec SPEC.md --package NAME
--gate-path $GATES` for a spec you name. A file already there is kept. The file holds:

- **Examples:** each fenced Python block that imports from the package and states an expected result. That is a
  `print(...)` or an expression with an `# Output:`/`# Result:`/`# ->` comment (on its line or the next), or a
  bare literal comment such as `# [1, 2]`. It is also an `assert`, an `except` naming a package exception,
  `pytest.raises`, a doctest, or an argument-free `def test_...()`.
- **Signatures:** classes and functions a block imports from the package and then lists, and bare
  `def name(...) -> T:` signature lines. Parameter names and their order are compared, and so are listed
  methods and base classes.

Its name does not start with `test_`, so a gate that runs pytest on `$GATES` never collects it: it is advisory.
On NL2Repo's held-out tasks the real upstream releases disagreed with about a quarter of the examples their
specs give, so a blocking check would push correct code toward a wrong spec.

How a result is decided:

- **Matching** ignores quote style and spacing. `...` matches anything, a set matches a list with the same
  items, and a value printed after a label (`print(f"Total: {x}")  # 3`) matches.
- **Skipped, not failed:** an example that uses a name it never defines, needs a module outside the package
  that is not installed, assumes a file it names but does not create, needs the network, or runs over 10
  seconds.

`examples SLUG` runs it with the Python the gate command runs. That is found anywhere in the command, past `cd X &&`,
`;` and environment settings, else it is the first `python` on `PATH`. A run that cannot start reports its real
error output.

It prints a `summary` and the `result`. The result has `passed`, `skipped`, `failed` (each id and message) and
`kinds`. The summary gives examples and signatures apart. It says "none runnable in this spec" when the spec gives
no runnable example, and "none ran here" when every example was skipped. `check` and `close`
add the same `spec_examples` line to their reports, with at most 60 seconds per run. Each run is skipped when the
watched files are unchanged since the last one. The session-start hook only shows the latest result: it never
runs spec code itself, so spec code runs only through commands the agent starts. The last result is kept beside
the goal's notes, outside the watched files, so a run never changes the checkpoint. A project pytest
configuration that collects every `*.py` file would collect `spec_examples.py` into the gate; keep the default
`test_*.py` pattern.

`waive-example SLUG ID --reason "SPEC line N ..."` skips one example or signature whose spec is wrong. The
reason must cite a spec line, and the waiver is recorded in `examples_waived.json`. That file is in the gate
directory, so a waiver changes the checkpoint: run the gate again before `close`.

## One review round per goal

A goal gets one review round, on any checkpoint (`HORIZON_REVIEW_ROUNDS_PER_GOAL` raises the limit).

**What counts as a round:** a round file named for one of the goal's own checkpoints that repeats its hash, and
is finished. A file is finished when its reply ends in `END VERDICTS`, or when it decided a line and has been quiet
for 15 minutes (a reviewer still running keeps writing). A recorded `review` also ends a round. A round that `close`
handed out (it records the session it went to) and that never came back does not count: a reviewer cut off after
its header, or one that never replied. Until the E2E-SWE paper run it counted once a later session had started,
and 79 goals closed on the agent's own gate that way, 57 of them below 100% on the hidden tests. Now `close` hands
such a round out again in a later session, up to one hand-out more than the rounds (two by default). While the last
hand-out can still come back (no later session has started and it or its reply file changed in the last 15
minutes, or a reviewer is still writing for the goal: a reply with decided lines, no `END VERDICTS`, changed in the
last 15 minutes), `check` and `close` print `review_pending` instead of another brief. Once every hand-out has gone
out and none of them still can come back, they print `review_unavailable` and send no more: `close` then completes
only on a passing review, or finalizes the goal when its budget is spent. The same file handed out again in a later
session is another hand-out. A brief that `check` prints does not count until its reply is finished.

**After a round that came back,** an explicit `close` completes on a passing gate without a passing review. It reports
`unreviewed`: why a review was required, plus the FAIL lines the round found that were fixed but not re-checked.
`check` and `close` then print `review_spent` instead of a brief.

**Two cases still block:**
- a recorded `review --result fail` on exactly the current files, unless the only thing keeping its reply from a pass is
  UNVERIFIED lines (those lines go to `unreviewed`);
- a FAIL line or FAIL headline that the round wrote for them.

`close` names these until the files change. `close --if-ready` and the Stop hook never complete a goal without a
passing review.

When the goal's `request.md`, or a Markdown file in the project it names, is 200 lines or more, `close` checks
each section of it: a heading outside code with no sub-heading under it, and a heading with sub-headings whose own
text gives Python code for a name it carries. A section is covered when a requirement line cites it, by its words
or by a code name it carries (`chunked()`, `BaseUserManager`), or when the work defines that code name: a name its
source files (not tests, docs or config) define or import (`from m import a`, `import m as n`), or a file, folder or dotted module path it has. A dotted name
needs each part, and code the project had when the goal started does not count. The word before a capitalised
Class, Function, Method, Protocol and similar ("Strategy Class") is a code name for looking up the work, not for
citing. When the section gives Python code in a fenced block, the work's definition must also match it: every base
class the request gives (directly, through the work's own classes, or as the standard library implies, so
`ValueError` is an `Exception` and `Protocol[T]` is generic), and every parameter it names (not positional-only
ones), unless the work takes *args or **kwargs. Otherwise `close` lists the section with both signatures and what
is missing. Only differing code stops `close` (and no reviewer is sent while any remains). Code the work has not built and
prose sections are listed in `sections_advisory`, and `close` completes regardless. `start` does not check. At session start, the hook lists the sections whose code differs, so a session sees them
before `close`.

In a session that resumes a goal some session has worked on (it has a next action or a checkpoint), the
session-start message asks for a change to the work within the first 5 tool calls. If the 6th or the 11th call
comes with no project file changed since the session started (tests, docs, hidden files and the gate directory
count; Horizon's bookkeeping does not), the PostToolUse hook adds a reminder that names the next action. A
subagent's calls are not counted. There is no reminder in a session that starts its goal, in a goal started
from the command line that no session has worked on yet, or outside a Git repository. A named file the work changes after
the baseline (a CHANGELOG the request says to extend) is an output and is not checked.
After a failed review round, `close` refuses while the work (every project file, hidden files and tests
included, outside the gate directory and Horizon's own state) is unchanged since the round, unless a
`Disputed Rk:` finding answers each FAIL line.

A gate in a `--gate-path` directory is reported as "the agent's own, written during this goal". The project's own
`tox.ini` or `conftest.py` changing is not a gate change.

The session-start message adds `Plateau: ...` when the project's source (every file outside tests, hidden
and build folders and the gate directory, in any language, documentation excluded: doc folders and `.md`,
`.rst`, `.adoc` files) is unchanged across three session starts in a row. Two whole sessions
changed nothing, so the message sends the agent back to running and building the work.

## Recorded facts that change a check

- `note SLUG --fact "Not ours: NAME, NAME"`: the names are left out of `unbuilt_names` from then on (another
  library's name, an example's own, a name made at run time).
- `note SLUG --fact "install check: <what was refused>"`: the install check's failure is a permission error of the
  sandbox it runs in. `close` then reports the install problem without waiting for it. The fact answers only for
  that: a package that does not build, a script that hangs or installs nothing, an import that fails, and a setup
  script the request names and the project lacks are held whatever was recorded. A missing script is written even
  where the request says something else runs it: a script that installs the project from its own files costs a
  few lines if another takes its place, and without one nothing installs (thin14 fiddle: 0 of 58).
- A later goal of the same project starts with the earlier goals' facts, except the ones that release a hold
  (`install check: ...` and `... by design`): those answer for one goal's request.
- `check` on files that have not changed since the last gate run does not run the gate again (`gate_skipped`): the
  last result and its exit code stand.
- One `check --parts` saves no more parts of the request check than the tool calls made since the last save, and
  at most 8.
- A name the request writes short after a full one (`` `pkg.protocol`, `.server`, `.client` ``) is on the surface
  list as the full name's sibling (`pkg.server`, `pkg.client`).
