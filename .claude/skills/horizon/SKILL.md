---
name: horizon
description: Use for explicitly requested bounded work that must resume across turns or sessions, such as "keep working until the tests pass". Saves progress and the next action, and checks that the work installs, imports and runs before it is called done.
---

# Horizon for Claude Code

Horizon keeps a goal, its gate (the command that runs the work's tests), notes and the next action on disk, so work
survives the end of a session. It adds three checks of its own, taken from the request and not from your code: every
module and name the request gives imports, the package installs the way the request says, and the request's own
code examples give the results it states. Everything else is ordinary work: spend your calls on the request and the
code, not on Horizon. This file is all a session needs; [the full guide](references/guide.md) and [the CLI
reference](references/commands.md) cover the rest (requirement lines, reviewers, state mode). Never read Horizon's
own source or state.

`HARNESS` is the absolute `scripts/harness.py` beside this SKILL.md. `PROJECT` is the directory this session
started in. If the session-start message gives `HARNESS`, `PROJECT` and `GATES_ROOT`, use them, and paste the
literal paths it gives into every command: shell variables do not carry over between commands. If it names an
active goal for this request, go straight to Resuming. Otherwise run `doctor` once and use the `gates` path it
prints as `GATES_ROOT`.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" doctor
```

Shell: the shell may be zsh on macOS. There is no `timeout` command (use `--timeout` on `start`, `check` and
`close`), `sed -i` needs `''`, and a test run's exit status never comes through a pipe.

## Starting a goal

Read the request, the inputs it names, the code it concerns, and that code's existing tests and callers. Read a
large input with the Read tool, up to about 800 lines per call; never `cat` it. Then set the goal up in one Bash
call. `NAME` is a short label for this request that no directory in `GATES_ROOT` uses yet.

```bash
GATES="$GATES_ROOT/NAME"; mkdir -p "$GATES"
cat > "$GATES/request.md" <<'EOF'
<the request, verbatim>
EOF
python3 "$HARNESS" --runtime claude --project "$PROJECT" start "Requested outcome" \
  --gate-path "$GATES" --gate "<the command that runs the work's tests, naming its Python>"
```

- **The gate** is one command, with no pipe or `;`, that runs the work's tests: the project's own and the ones you
  add beside them as you build. Name the Python that has the project's dependencies, as in
  `/path/to/venv/bin/python -m pytest tests -q`: Horizon runs its own three checks with that same Python. If there
  are no tests yet, write one that imports the entry point the request names, then start. Once the entry point
  works, the next test you add runs the deliverable's main use end to end on a real input, with the result the
  request states: a gate of import tests passes while the product does nothing.
- **What `start` writes:** from the request and the Markdown files it names, a test of every module and public name
  they give and a file of their code examples. Never edit either. `start` also runs the gate once and prints the
  baseline `observation`; read it, and do not run `check` for it.
- When the request gives a time limit, pass it as `--max-minutes N`.

## Working

1. **Make the deliverable usable first.** For new work, create in the first session the entry point a user calls
   (package, module or command) and every module it imports, so it imports or runs before its parts are filled
   in. For existing code, run its tests before your first edit. When the request says how the work is installed
   or run (an install script, a package install, a command), make that work in a clean copy, not via `PYTHONPATH`.
2. **Write straight into the real files,** never into scratch parts to join later, and keep the work runnable after
   every write: a session can end at any call.
3. **Run what you wrote, in the same session,** the way a user would. Code never run is a guess.
4. **Build before you verify.** While a part of the request is unbuilt, the next action is building it. Take the
   largest unbuilt part next, not the next one in reading order.
5. **Take every expected value from the request** and its examples, never from your own code. A test that passes
   the first time it runs proves little, and a passing gate means your tests pass, not that the request is met:
   before you call a part done, read that part of the request again and run what it shows.
6. **Never weaken a test to fit your output.** When an example or a caller fails, fix the code, not the caller.

`check` is your test run and your checkpoint in one call: it runs the gate and Horizon's checks and saves what you
did and the next action. Run it when a part works, not after every edit. Use `note` only for what `check` does not
carry, in the same Bash call as another command, never a call of its own: `--fact`, once, for an environment
problem or the exact command that worked; `--finding` for a bug, or a part of the request, you leave for later.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" check "$SLUG" \
  --did "Concrete change and evidence" --next "One concrete edit: the file and what it needs"
python3 "$HARNESS" --runtime claude --project "$PROJECT" note "$SLUG" --fact "Tests run with /path/to/venv/bin/python -m pytest tests -q"
```

What `check`, `close` and the session-start message report, and what to do:

- `surface_missing`: modules and names the request gives that do not import. Give each a working first version
  before you deepen what exists. If the request does not mean one (a file name, an example's variable), delete it
  from the surface file and say why with `note --finding`.
- `install_problem`: the package does not install in a clean copy, offline. Fix the project's own files (its
  `pyproject.toml`, the setup script the request names); never build an environment of your own for it.
- `spec_examples`: how many of the request's own examples agree with the work, and which disagree. For each, read
  the request's prose: fix the code when the prose agrees with the example. If the example itself is wrong, skip
  it with `waive-example`, citing the line.
- `placeholders`: functions with no body yet; `close` waits for them. Fill each in. Only if the request says the
  caller supplies one, record it with `note --fact "<file> <name> raises NotImplementedError by design"`.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" waive-example "$SLUG" "$EXAMPLE_ID" --reason "Line 40 of the request says the opposite"
```

## Resuming

The session-start message shows the next action, the gate's latest result with the failing tests by name, the
notes, the files the last commit changed and every file with its line count. Do not run `status`, `ls` or `wc` to
get these again, and do not re-read the request unless the next action needs a part of it. Make your first edit
within your first 5 tool calls; never spend a session only reading. When the message says `Plateau`, do what it
says in that session. `Where the last session stopped` is the last thing that session said and ran before it was
cut off, which no `check` saved: if it names a problem, that problem comes before the next action.

## Closing

When every part of the request is built and run, run `close`. Its output is long: `complete` and `missing` come
first, and they are all you need. It completes the goal when the gate passes on the current files and Horizon's
checks hold; otherwise `missing` says what to fix. If the gate ran too few tests (`small_gate` in `check`), add
tests of the main use and of each part of the request, then fix what they find. If a baseline test file changed,
restore it and fix the code, unless a person approves with `approve-test-change`.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" close "$SLUG"
```

A session's length is not the goal's deadline: never end a session to hand off while the goal is active. In an
unattended run no one will answer a question, so never stop to ask: record your decision, or a test the
environment blocks (a write outside the project, the network, a port), with `note --fact`, take that test out of
the gate, and keep building.

## Final report

Only when the goal is complete, its budget is spent or a person ends the work, end with one report: what the
request asked for and what is built, the parts not met or not checked first; any `surface_missing`,
`install_problem` or disagreeing example left open; every test you removed or waived; the slug and status.
