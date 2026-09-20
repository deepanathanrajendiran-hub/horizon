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

`start` defaults to 20 increments and no wall-clock ceiling. Set `--max-turns`
and `--max-minutes` to the requested limits. An increment is a `check`, not a
model message. Read `status` before work; an increment that crosses its time
deadline may still checkpoint. The CLI is not a scheduler or an execution sandbox.

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
