# Shared harness CLI

Both runtimes call the same Python implementation. Set `HARNESS` to an absolute
skill entrypoint (or `harness/cli.py`), `PROJECT` to the working project, and
`RUNTIME` to `codex` or `claude`. Python 3.10+ and a POSIX environment are required;
there are no third-party runtime dependencies. Quote paths containing spaces.

```bash
python3 "$HARNESS" --runtime "$RUNTIME" --project "$PROJECT" doctor
python3 "$HARNESS" --runtime "$RUNTIME" --project "$PROJECT" --help
```

Global options precede the subcommand. `--scope project|global` selects the
refinement ledger, not the goal location. Goals always belong to the project.
`doctor` prints resolved paths without creating files. Automatic project discovery
uses the nearest ancestor with a runtime marker (`.agents`/`.codex` or `.claude`),
then Git, then the current directory. Give `--project` the intended working root
when operating from an otherwise unmarked nested directory.

## Goal lifecycle

| Command | Purpose |
| --- | --- |
| `status [SLUG]` | List goals or inspect one, including budget and latest checkpoint |
| `start OBJECTIVE --gate COMMAND [--review CRITERION]` | Command verification with optional independent review |
| `start OBJECTIVE --coverage CRITERION` | Independent artifact review without a command |
| `check SLUG --did TEXT --next TEXT` | Run one check and checkpoint completed work |
| `review SLUG --result pass\|fail --judge HANDLE --hash HASH --evidence-file PATH` | Record the actual review of the checkpoint's snapshot |
| `complete SLUG` | Require all configured checks on current artifacts |
| `abandon SLUG --reason TEXT` | Close incomplete work honestly |
| `reopen SLUG --reason TEXT [--extra-turns N] [--extra-minutes N]` | Resume with an authorized budget extension |

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
checkpoint. The CLI is not a scheduler or an execution sandbox.

Repeat `--watch PATH` on `start` to select the complete set of relevant inputs.
Paths are relative to the discovered project; the default is the whole project.
The fingerprint includes file content, file mode, and Git state. It excludes
common dependency/cache directories and mutable `.claude/harness` and
`.codex/harness` goals, undo records, and changelogs. Harness source remains an
input. Directory symlinks are not traversed; watch their resolved targets
explicitly when they are relevant. Keep gate logs and review evidence outside
the watched tree. Include
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
and criterion. Evidence must contain at least 40 characters; that length is only
a basic empty-verdict guard. The calling agent must dispatch a real independent
reviewer. A caller-supplied handle or editable local ledger cannot prove identity
or prevent intentional falsification. `complete` preserves checkpoint evidence
and rejects changed artifacts, gate definitions, and missing/failed reviews.

After upgrading, rerun checks for active goals created by the old implementation.
Old checkpoints lack the current fingerprint/gate-definition contract and cannot
be used directly to complete. Completed history is left intact. Runtime state is
never silently migrated between Codex and Claude.

## Refinement proposals

`doctor` and `refine-count` return the correct `rho`, `G`, `K`, and `M` target paths
for the chosen runtime and scope. Read current files before constructing a
proposal. The CLI accepts a JSON file shaped like this (substitute actual absolute
paths from `doctor`):

```json
{
  "layer": "M",
  "rationale": "The same path-quoting failure occurred in two tasks.",
  "evidence": "Actual excerpts and task references showing both failures.",
  "edits": [
    {
      "path": "/absolute/project/.codex/memory/path-quoting.md",
      "kind": "text",
      "content": "Quote shell paths that contain spaces.\n"
    },
    {
      "path": "/absolute/project/.codex/memory/MEMORY.md",
      "kind": "append_line",
      "content": "- [Path quoting](path-quoting.md): shell paths containing spaces."
    }
  ]
}
```

`text` replaces the whole target. `append_line` atomically appends one line;
`replace_line` requires a `match` substring selecting exactly one line and a
single-line `content` replacement. Prefer line operations for a shared memory
index. All paths must be absolute. An empty or ambiguous match is an error.

```bash
python3 "$HARNESS" --runtime "$RUNTIME" --project "$PROJECT" refine-count
python3 "$HARNESS" --runtime "$RUNTIME" --project "$PROJECT" refine-apply "$PROPOSAL_FILE" --approved
python3 "$HARNESS" --runtime "$RUNTIME" --project "$PROJECT" refine-history
python3 "$HARNESS" --runtime "$RUNTIME" --project "$PROJECT" refine-undo --entry "$ENTRY_ID"
```

Three applies are allowed per rolling 24 hours, per runtime and scope. All global
edits for one runtime share a global ledger; project edits use that project's
ledger. `--override-cap` requires specific authorization. Approval flags record
the agent's assertion of authorization; they do not implement an approval UI.

Undo checks for intervening target changes. `refine-undo --force --approved`
discards those changes and requires explicit authorization. Protected targets
include this harness and its skills, configuration, hooks, and bookkeeping.
This protects routine refinement from rewriting its own controls, not against a
host process with arbitrary filesystem access.

Content tripwires apply at every layer to literal attempts to weaken evidence,
approval, or edit limits, or to claim harness write authority. Skill and subagent
edits also check for direct memory-write instructions. They can produce false
positives; inspect a refusal instead of silently routing around it. Count/history
inspection may initialize empty ledger directories but does not apply an edit.

## Memory and outcomes

Codex uses project `.codex/memory`, explicitly retrieved by `recall`. Claude uses
its existing project-memory directory under `~/.claude/projects`. Both routes
remain project-specific even under global refinement scope. `CODEX_HOME` controls
Codex's global configuration and ledger; personal skills live in `~/.agents/skills`.

`recall` reads the memory index. `recall --query TEXT --limit N` also returns
matching Markdown notes (simple keyword ranking, up to 20 notes). It is not
semantic search or automatic native Codex memory integration.

```bash
python3 "$HARNESS" --runtime "$RUNTIME" --project "$PROJECT" refine-outcome "$ENTRY_ID" \
  --outcome helpful --task "$LATER_TASK_REFERENCE" --evidence-file "$OBSERVATION_FILE"
```

Outcomes are `helpful`, `ineffective`, or `harmful`. Record only observed later
behavior; otherwise leave the entry unassessed. Observations appear in history,
do not alter the lesson, and do not consume an applied-edit slot. The repository's
`docs/evaluation.md` describes how to measure effects across tasks.
