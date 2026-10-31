---
name: horizon
description: Use for explicitly requested bounded work that must resume across turns or sessions, such as "keep working until the tests pass". Saves progress and verifies completion against a command or review criterion.
---

# Horizon for Codex

Horizon keeps a goal, its gate (a check command), notes and the next action on disk, so work survives the end of
a session. This file holds what every session needs. For anything it does not cover (earlier goals, coverage
gates, state mode, the multi-round review, scheduling), read [the full guide](references/guide.md), and see
[the CLI reference](references/commands.md) for every command.

`HARNESS` is the absolute `scripts/harness.py` beside this SKILL.md. `PROJECT` is the directory this session
started in. Codex has no hooks: run `doctor` once and use the `gates` path it prints as `GATES_ROOT`, then
`status` to find an active goal for this request. Paste literal paths into every command: shell variables do not
carry over between commands.

```bash
python3 "$HARNESS" --runtime codex --project "$PROJECT" doctor
```

## Resuming

Run `status SLUG` once and read its `next_action`, `last_observation` and `notes_file`; if files changed since
`last_observation`, run `observe` for a current result. Do not re-read the request, the requirements or the spec
unless the next action needs a part of it. Make your first edit within your first few tool calls; never spend a
session only reading. If two sessions in a row changed no library source, build this session: do not run
`close` or start a review, and work through step 3 of Building.

Keep the next action current with `note` after each write: one concrete edit, naming the file and the input lines
it needs, never a reading plan. Record a bug you will not fix in the same call with `--finding`, and an
environment problem that is not a code bug with `--fact`, once.

```bash
python3 "$HARNESS" --runtime codex --project "$PROJECT" note "$SLUG" --next "Write pkg/testing.py from SPEC.md lines 1599-1741"
```

Shell: the shell may be zsh on macOS. There is no `timeout` command (use `--timeout` on `start`, `check`,
`observe` and `close`), `sed -i` needs `''`, and a test run's exit status never comes through a pipe.

## Starting a build from a spec

Read the spec's first part, which holds its outline and module list, in parts of up to about 250 lines per
command (longer output is cut; never `cat` it, never in small slices). Then set the goal up in one Bash call, so that a session cut off
early still leaves a goal. Copy this block and fill in the placeholders; nothing else is needed for setup. `pkg`
is the package's import name and `PY` the Python that has pytest.

```bash
GATES="$GATES_ROOT/pkg-build"; mkdir -p "$GATES"
cat > "$GATES/request.md" <<'EOF'
<the request, verbatim>
EOF
cat > "$GATES/requirements.md" <<'EOF'
R1 [gate] "<the first module or section the spec lists, in its words>"
R2 [review] "<the next one>"
EOF
python3 "$HARNESS" --runtime codex --project "$PROJECT" start "Build pkg as SPEC.md describes" \
  --gate-path "$GATES" --surface-spec "$PROJECT/SPEC.md" --package pkg \
  --gate "$PY -m pytest -q -p no:cacheprovider $GATES" \
  --review "Every line of $GATES/requirements.md holds, judged against $GATES/request.md"
```

One requirement line per module or section, quoted in the spec's words. Tag `[review]` only a line a reviewer can
judge by running the code; packaging, docs, CI, examples and lines that state no checkable behaviour are
`[unverifiable]`. `--surface-spec` writes the surface gate, one test per public name the spec gives; it is the
whole first gate. It also writes the advisory `spec_examples.py` (see Building). Then read the rest of the spec in
large parts. The notes file keeps only what saves a re-read: a `## ` heading per part with its line range and the
file it maps to, and the signatures and rules you will need again. Write notes in the same Bash call as an edit.

## Building

1. **Make the package exist first.** The package `__init__.py` and every module the spec lists, each with its
   classes and functions, then the main entry point, then packaging files. Write straight into the real files and
   keep the package importable after every write: define a name in the same edit that exports it.
2. **Run what you wrote, in the same session.** Before the session can end, import the package and run the spec's
   first usage example. A module never run is a guess.
3. **Recall the real library.** If the spec describes an existing library, its own tests are what will judge
   the build. Once the package imports, write a `## Real library` part in the notes, from memory: how that
   project behaves where its tests look. Cover the exact messages and output lines, status codes, token and file
   formats, defaults, the state an object moves through (open, share, close, reuse), the exception classes, and
   the public names the spec never lists. Run the code against each item and fix what differs. Where your recall
   and the spec's prose disagree on an observable result, follow the real library and record the choice with
   `note --fact`. The spec's silence about a name or a behaviour is not a reason to leave it out.
4. **Where the spec is silent or only illustrative,** do what the real library does (for a new library, what
   standard Python does): `datetime` for dates, built-in exception classes, the real names, parameters and module
   layout. Never assert your own guess in the gate: a check of behaviour the spec does not state carries a
   `# judgment` comment.
5. **Spec examples.** `examples SLUG` runs the spec's own runnable examples and listed signatures. It is
   advisory, not part of the gate, because specs are often wrong. For each disagreement, fix the library when the
   prose and the real library agree with the example; otherwise run `waive-example SLUG ID --reason "SPEC line N ..."`.
6. **Deepen the least-built part next.** The next action is the largest part still unbuilt or a placeholder, not
   the next section in spec order. Grow the gate with the code, from the spec's examples and your real-library
   notes. When an example or caller fails, fix the library, not the caller; never weaken the gate, a reviewer's
   test or the requirements to fit your output.
7. **Docs, translations, styles and CI files come last,** after the library runs and the recall pass is done.

```bash
python3 "$HARNESS" --runtime codex --project "$PROJECT" check "$SLUG" \
  --did "Concrete change and evidence" --next "Next useful action"
python3 "$HARNESS" --runtime codex --project "$PROJECT" observe "$SLUG"
```

## Review and close

When every part is built, run `close`. It completes the goal if a passing gate covers the current files and
nothing else is missing; otherwise it lists what is.

A goal gets one review round. Dispatch it once, as the first action of a session, never in a session's last
calls: when `close` prints a `review_brief`, send it unchanged to a fresh reviewer with the available Codex
subagent tool (in this desktop runtime `collaboration.spawn_agent` with `fork_turns: "none"`, then wait for it),
replacing only `<<ARTIFACTS>>` and `<<HOW_TO_RUN>>`. Record its reply file with `review` (`--result fail` for any FAIL or UNVERIFIED line), fix the
FAIL lines, and run `close` again: after the round it completes on a passing gate and lists what went unreviewed.

```bash
python3 "$HARNESS" --runtime codex --project "$PROJECT" close "$SLUG"
python3 "$HARNESS" --runtime codex --project "$PROJECT" review "$SLUG" \
  --result pass --judge "$JUDGE_HANDLE" --hash "$CHECKPOINT_HASH" \
  --evidence-file "$REVIEW_FILE"
```

If `close` says a baseline test file changed, ask the user whether the change is legitimate, and only then record
it. Never approve a changed test on your own judgment.

```bash
python3 "$HARNESS" --runtime codex --project "$PROJECT" approve-test-change "$SLUG" \
  --path "tests/test_parser.py" --reason "Approved by the user: the old assertion encoded the bug"
```

A session's length is not the goal's deadline: never end a session to hand off while the goal is active and
budget remains. Your work is committed whenever the session is cut off. In an unattended run no one will answer a
question: make the decision, record it with `note --fact`, and keep building. If `status` shows an attempt you
did not start, run `step-reclaim` as the guide describes.

```bash
python3 "$HARNESS" --runtime codex --project "$PROJECT" step-reclaim "$SLUG" --attempt "$ATTEMPT" --expected-revision 2
```

## Final report

Only when the goal is complete, its budget is spent or a person ends the work, end with one report: each
requirement line and its status (verified by gate, by reviewer, a judgment call, unverified or not met), the
unverified and not-met lines first; reviewer findings left open; every gate change; the slug and status.
