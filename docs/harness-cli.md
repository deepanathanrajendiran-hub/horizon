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
| `status [SLUG]` | List short goal rows, or inspect one goal in full (budget, deadline, latest checkpoint) |
| `start OBJECTIVE --gate COMMAND [--review CRITERION] [--gate-path DIR]` | Command verification with optional independent review |
| `start OBJECTIVE --coverage CRITERION [--gate-path DIR]` | Independent artifact review without a command |
| `check SLUG --did TEXT --next TEXT` | Run one check and checkpoint completed work; prints `review_brief` and `review_file` when a review is due |
| `review SLUG --result pass\|fail --judge HANDLE --hash HASH --evidence-file PATH` | Record the actual review of the checkpoint's snapshot |
| `complete SLUG` | Require all configured checks on current artifacts |
| `observe SLUG [--role baseline\|resume\|final] [--timeout N]` | Run the gate now and record the result; charges no turn |
| `close SLUG [--if-ready] [--timeout N]` | Complete when recorded evidence covers the current files, else say what is missing |
| `approve-test-change SLUG --path P --reason R` | Record who approved a changed baseline test, and why |
| `step-reclaim SLUG --attempt A --expected-revision N [--session S]` | Close a state-mode attempt whose lease went stale |
| `hook SessionStart\|PostToolUse\|Stop`, `install-hooks`, `uninstall-hooks` | Claude Code hooks and their installation |
| `abandon SLUG --reason TEXT` | Close incomplete work honestly |
| `reopen SLUG --reason TEXT [--extra-turns N] [--extra-minutes N]` | Resume with an authorized budget extension |

`status` without a slug returns `{"goals": [...]}` with one short row per goal:
slug, status, goal, gate, review criterion, gate path, next action, abandon
reason, start and completion times, turns used, and the last gate and review
results. Long text is clipped and ends in `…`. A goal file that cannot be read
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

`observe SLUG [--role baseline|resume|final] [--timeout N]` runs the gate now and
appends the result to the goal's `observations` list. `--role` defaults to
`resume`. It never skips an unchanged tree and charges no turn. The report has
`observation`. Only command gates can be observed. Exit 0 if the gate passed,
1 if it did not, 2 on error.

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
- A changed baseline test file blocks completion until `approve-test-change`.
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
- `SessionStart` records the session id, reports each active goal in one line and
  reclaims a stale attempt automatically.
- `PostToolUse` renews the lease of the session's open attempt.
- `Stop` completes or finalizes goals whose recorded evidence already allows it
  (the `close --if-ready` rules, so never on files unchanged since the
  baseline); it never runs the gate.

Claude Code loads hooks when a session starts, so hooks installed by `start` take
effect from the next session.

## Requirements and review

`start --gate-path DIR` records the goal's gate directory. DIR must already hold a
non-blank `request.md` (the request, verbatim) and a `requirements.md` with at
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
anything else under `Non-blocking:`. Keep the review under ten minutes.

End your reply with this block. Change each `?` to exactly one of PASS, FAIL,
UNVERIFIED or NOT REVIEWED, and write nothing else on those lines:

VERDICTS
{verdicts}
END VERDICTS

Before returning, write your complete reply to {review_file}.
```

The agent replaces only `<<ARTIFACTS>>` and `<<HOW_TO_RUN>>`. A goal without
`--gate-path` also keeps `<<REQUEST>>`, `<<REQUIREMENTS>>` and `<<VERDICT_LINES>>`
for the agent to fill. When the latest review was recorded as a fail, the brief ends with
`Previous:` and that review's FAIL and UNVERIFIED lines and FAIL headlines, each
prefixed with `> `. Do not dispatch a reviewer for a last
check before stopping; a coverage goal reports `review_required` on every check.

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
The fingerprint includes file content, file mode, and Git state. It excludes
common dependency/cache directories and mutable `.claude/harness` and
`.codex/harness` goals, undo records, and changelogs. Harness source remains an
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
