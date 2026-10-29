# Fixes from the bench2 transcripts

**Goal:** turn bench2's findings into Horizon changes. The findings log is in the fun repo, `docs/evaluations/nl2repo-bench2/transcript-findings.md`. The rerun is on structlog, pyjwt and unidecode.

**Branch:** `fix/bench2-findings`, from `fix/bench-findings` (f3efc4b). Stdlib only. Every code change gets a test that fails first.

## Tasks

### 1. The checkpoint hash ignores git HEAD (finding G)
- `tree_hash.compute` no longer mixes in `git rev-parse HEAD`. File contents already decide the snapshot, and a commit that changes no watched file must not throw away gate passes and carried review lines.
- **Test:** commit with no file change, and the hash is unchanged; change a file, and the hash changes.

### 2. `status SLUG` is compact (finding A)
- Each entry in `reviews` keeps result, at, judge and hash, and drops the evidence.
- `last_checkpoint.gate_output` and `gate_evidence` outputs are clipped to their last 600 characters.
- **Test:** a goal with a 16,000-character review and 4,000-character gate output gives a status under 6 KB.

### 3. `start --timeout` (finding H)
- It sets the baseline observation's timeout.
- **Test:** `start --timeout 30` is accepted.

### 4. A notes file per goal (finding E)
- The file is `<harness>/notes/<slug>.md`. It is left out of the checkpoint hash (bookkeeping).
- `status SLUG` reports `notes_file` and `notes_sections`, its `#`/`##` headings clipped to 600 characters.
- `start` creates the file with a short header saying what it is for.
- **Tests:** the file is created; it is excluded from the hash; its sections are listed.

### 5. The session-start hook hands over everything (findings A, B, C)
- For each active conversation-mode goal with a command gate, it runs the gate as a `resume` observation. It skips the run when an observation of the current tree already exists, and caps the run at 240 s.
- It then injects:
  - the `HARNESS` and `PROJECT` values to use;
  - the slug and next action;
  - the fresh gate result, with the last 600 characters of output;
  - the notes file and its sections;
  - the files the last commit changed.
- The SessionStart hook timeout becomes 300 s. `install` rewrites a hook whose settings differ, not only one whose command differs.
- **Tests:** a hook after a file change records a new observation and shows its tail; with the tree unchanged, it does not run the gate again.

### 6. Review scope starts with the lines that need a reviewer (finding G)
- In `review_scope`, open lines tagged `[review]`, `[judgment]` or `[unverifiable]` come before `[gate]` lines.
- **Test:** with R1–R4 `[gate]` and R5–R6 `[review]`, a batch of 2 is R5 and R6.

### 7. SKILL.md, both copies, plus docs (findings A–H)
- **Resuming:** when the session-start message names the goal and gives `HARNESS` and `PROJECT`, skip `doctor` and `status`. Start from the gate result and the next action it shows.
- **Breadth first for a build from a spec:**
  - First increment: every module the spec lists, as an importable stub, plus the package `__init__` re-exporting the listed names, plus packaging.
  - The first gate has one test per module and checks that module's public names by exact import path, importing inside the test. It checks that `__init__.py` exists, because a namespace package imports without one.
- **Keep the package importable:** define a name in the same call that exports it. Never import a module that doesn't exist yet.
- **Notes name one concrete edit:** a file and the spec lines it needs. Update the next action after each write. No session numbers.
- **Large inputs:** never `cat` a file over 50 KB. Read by section. Record, in the notes file, each section's line range, its target module and whether it is done. Read the notes before the input.
- **A passing gate is not done:** extend it to the next unchecked part of the request. That covers every item the spec enumerates (files in its tree, names, whole ranges, not only the listed examples). Work on library behaviour before docs, examples or images. When a caller fails against the library, fix the library.
- **Reviewer timing:** dispatch a reviewer only at the start of a session, with most of the session left.
- **Environment:**
  - macOS `sed -i ''`;
  - no `timeout`;
  - zsh: no unquoted `=====`, and no commands stored in variables;
  - don't take a test's exit status through a pipe;
  - add a `.gitignore` for `__pycache__`.
- **Contract tests** for each rule phrase.

## Then
A fresh reviewer checks the whole branch; its findings are fixed. Then: bundle, pre-register bench3 (structlog, pyjwt, unidecode, both arms, the same rule over three tasks), smoke check, and the user's go.

## Review findings deferred (minor)
- A goal created before this branch has a baseline hash in the old format, so `_unchanged_since_baseline` cannot match it. A goal with no work could then be auto-completed by the Stop hook after the hook's own `resume` pass. This affects only goals that cross the upgrade.
- `HARNESS` in the session-start message is the copy that installed the hooks, which may differ from the SKILL.md copy that is loaded.
- With narrow watch paths, git-visible files inside them are hashed twice.
