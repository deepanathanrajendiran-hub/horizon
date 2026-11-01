---
name: horizon
description: Use for explicitly requested bounded work that must resume across turns or sessions, such as "keep working until the tests pass". Saves progress and verifies completion against a command or review criterion.
---

# Horizon for Claude Code

Horizon keeps a goal, its gate (a check command), notes and the next action on disk, so work survives the end of a
session. For anything it does not cover (earlier goals, large inputs, coverage gates, state mode, the multi-round
review, scheduling), read [the full guide](references/guide.md), and see [the CLI
reference](references/commands.md) for every command. Never read Horizon's own source or state.

`HARNESS` is the absolute `scripts/harness.py` beside this SKILL.md. `PROJECT` is the directory this session
started in. If the session-start message gives `HARNESS`, `PROJECT` and `GATES_ROOT`, use them, and paste the
literal paths it gives into every command: shell variables do not carry over between commands. If it names an
active goal for this request, go straight to Resuming: do not run `doctor` or `status`. Otherwise run `doctor`
once and use the `gates` path it prints as `GATES_ROOT`. If an earlier goal may cover this request, read
"Earlier goals" in the guide before starting a new one.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" doctor
```

## Resuming

The session-start message shows the next action, the gate's latest result with the failing checks by name, the
notes, the files the last commit changed and every file with its line count. Do not run `status`, `ls` or `wc` to
get these again, and do not re-read the request or the requirements unless the next action needs a part of it. Make
your first edit within your first 5 tool calls; never spend a session only reading. The message also lists any
section whose Python code the work defines differently; fix those first. When the message says `Plateau`, do what
it says in that session.

Keep the next action current with `note` after each write: one concrete edit, naming the file and what it needs,
never a reading plan. Record a bug you will not fix in the same call with `--finding`, and with `--fact`, once, an
environment problem that is not a code bug or the exact command that worked (interpreter path included), to copy
instead of retyping.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" note "$SLUG" --next "Make parse_date in src/dates.py accept the ISO forms R3 lists"
```

Shell: the shell may be zsh on macOS. There is no `timeout` command (use `--timeout` on `start`, `check`,
`observe` and `close`), `sed -i` needs `''`, and a test run's exit status never comes through a pipe.

## Starting a goal

Before changing anything, read the request, the inputs it names, the code it concerns, and that code's existing
tests and callers. Read a large input with the Read tool, up to about 800 lines per call; never `cat` it. Then
set the goal up in one Bash call, so that a session cut off early still leaves a goal. `NAME` is a short label for
this request that no directory in `GATES_ROOT` uses yet.

```bash
GATES="$GATES_ROOT/NAME"; mkdir -p "$GATES"
cat > "$GATES/request.md" <<'EOF'
<the request, verbatim>
EOF
cat > "$GATES/requirements.md" <<'EOF'
R1 [gate] "<a requirement, symptom, deliverable or constraint, in the request's words>"
R2 [review] "<the next one>"
EOF
cat > "$GATES/test_request.py" <<'EOF'
# Still passes: <one wrong result this gate would accept, or none>
<checks of the [gate] lines, each naming its line>
EOF
python3 "$HARNESS" --runtime claude --project "$PROJECT" start "Requested outcome" \
  --gate-path "$GATES" --gate "<command that runs the checks in $GATES>" \
  --review "Every line of $GATES/requirements.md holds, judged against $GATES/request.md"
```

- **Requirements:** one line for each requirement, reported symptom, deliverable and constraint, in the
  request's words, keeping qualifiers such as "exact", "all" and "unchanged". Tag `[gate]` a line a command can
  check, `[review]` a line a reviewer can judge by running the work, and `[unverifiable]` a line that states no
  checkable behaviour. Add `[judgment]` to a line with more than one reasonable reading, and record the reading
  you chose.
- **The gate:** checks of the `[gate]` lines, called the way existing callers and tests call the code. Take
  expected values from the request and its inputs, never from your own code. When the request says existing
  behaviour must stay the same, capture the current outputs now and compare against them.
- **The baseline:** `start` runs the gate once and prints the baseline `observation`; read it, and do not run
  `check` for it. Checks of behaviour the request calls broken or missing must fail there.

## Working

1. **Make the deliverable usable first.** In the first session, create the entry point a user calls (the package,
   module or command the request names) and every module it imports, so that importing or running it works before
   its parts are filled in; the gate's first check does exactly that.
2. **Write straight into the real files,** never into scratch parts to join later, and keep the work runnable
   after every write: a session can end at any call.
3. **Run what you wrote, in the same session.** Before the session can end, run the changed code the way a user
   would. Code never run is a guess.
4. **Build before you verify.** While a part of the request is unbuilt, the next action is building it, not new
   checks or a review. Take the largest unbuilt part next, not the next one in reading order.
5. **Grow the gate with the work.** Add checks for each part as you build it, from the request and its examples.
   A check that passes the first time it runs is not progress. A passing gate means your checks pass, not that
   the request is met.
6. **Where the request is silent,** follow the conventions of the project and its language. Never assert your own
   guess in the gate: a check of behaviour the request does not state carries a `# judgment` comment.
7. **Never weaken the gate,** a reviewer's test or the requirements to fit your output. When an example or a caller
   fails, fix the code, not the caller. A check you wrote that contradicts the request is yours to fix.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" check "$SLUG" \
  --did "Concrete change and evidence" --next "Next useful action"
python3 "$HARNESS" --runtime claude --project "$PROJECT" observe "$SLUG"
```

## Review and close

When every part is built, run `close`. It completes the goal if a passing gate covers the current files and nothing
else is missing; otherwise it lists what is. For a long request, or a Markdown file it names (200 lines or more),
`close` lists each section heading that no requirement line quotes and whose function, class or path the work does
not define, as advice: build what the request needs, but never replace working code, add files the request's layout
does not list, or write requirement lines only to quote headings; a line saying why, if it is out of scope, is
enough. A section that gives Python code counts as built only when the work's definition matches the code the
request gives (its base classes and parameter names); `close` waits for that.

A goal gets one review round. Dispatch it once, as the first action of a session, never in a session's last calls:
when `close` prints a `review_brief`, send it unchanged to a fresh reviewer with Claude Code's `Agent` tool
(`subagent_type: "general-purpose"`, in the foreground), replacing only `<<ARTIFACTS>>` and `<<HOW_TO_RUN>>`. Once
handed out, it counts as the round: never send it again, even if the reviewer is cut off; run `close`. If it runs
in the background, keep building while it runs; never wait for its reply. Record its reply file with `review`
(`--result fail` only when it has a FAIL line; UNVERIFIED lines go to the final report), fix the FAIL lines in the
work, and run `close` again: after the round it completes on a passing gate and lists what went unreviewed. It will
not complete while the work is unchanged since a FAIL. If a FAIL line is wrong, record why with `note --finding
"Disputed Rk: <reason>"`.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" close "$SLUG"
python3 "$HARNESS" --runtime claude --project "$PROJECT" review "$SLUG" \
  --result pass --judge "$JUDGE_HANDLE" --hash "$CHECKPOINT_HASH" \
  --evidence-file "$REVIEW_FILE"
```

If `close` says a baseline test file changed, restore it and fix the code instead, unless a person is there
to approve the change; record it only with their approval. Never approve a changed test on your own judgment.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" approve-test-change "$SLUG" \
  --path "tests/test_parser.py" --reason "Approved by the user: the old assertion encoded the bug"
```

A session's length is not the goal's deadline: never end a session to hand off while the goal is active and budget
remains. Your work is committed whenever the session is cut off. In an unattended run no one will answer a
question, so never stop to ask: record your decision, or a check the environment blocks, with `note --fact` and
keep building. If `status` shows an attempt you did not start, run `step-reclaim` as the guide describes.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" step-reclaim "$SLUG" --attempt "$ATTEMPT" --expected-revision 2
```

## Final report

Only when the goal is complete, its budget is spent or a person ends the work, end with one report: each
requirement line and its status (verified by gate, by reviewer, a judgment call, unverified or not met), the
unverified and not-met lines first; reviewer findings left open; every gate change; the slug and status.
