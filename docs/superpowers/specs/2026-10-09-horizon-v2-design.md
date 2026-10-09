# Horizon v2: request-anchored gates and reviews

Status: approved by the user 2026-10-09 ("ok do the stuff you from must , should"),
after they read the plain-language summary of
[skill-changes.md](../../evaluations/tb4-horizon-pilot/skill-changes.md) and its
three recommendations (base on the pilot skill; `[unverifiable]` lines end as
NOT REVIEWED and are listed first; add `--gate-path` and the C5 refusal in code).

## Purpose

In the Terminal-Bench 4.0 pilot, Horizon v1 passed 2 of 10 tasks, the same as the
control. It cost about 6 times the tokens and claimed "done" on 7 of its 8
failures. The goal ledger worked. The failures came from three places:

- Gates and reviewers checked the agent's paraphrase of the task, not the request.
- FAIL verdicts were never recorded.
- "Goal complete" was reported as "task done".

v2 makes the quoted request the contract, records every verdict line by line,
and reports by requirement. v2 is the skill EvoCode-Bench will test. It is
declared as a revised skill, so no result from it is pooled with the TB4 pilot.

## Scope

In scope are every **Must** and **Should** item of skill-changes.md section 2:
C1-C8, C11-C14, C9 (folded into C1), C10 and N1. The design source is the
item text in that file. This spec records the decisions it left open and the
places where this implementation deliberately differs from it.

Out of scope:
- C15 ("Could").
- C16 (dropped, see D3).
- Section 5's experiment-protocol items P1-P12. They belong to the EvoCode runner
  spec, which comes next. D6 and D7 go with them.

## Decisions

- **D0, base branch.** Branch `feat/horizon-v2` from `eval/tb4-horizon`@`1ead55b`.
  Its SKILL.md is byte-identical to the pilot bundle commit `2f65f2a` and keeps
  state mode. `main` is not used: its uncommitted runtime-separation work is not
  on this lineage.
- **D1, `[unverifiable]` lines.** Option (b) plus the middle ground:
  - The reviewer tests an `[unverifiable]` line if it can and answers it PASS or
    FAIL as usual. Otherwise it answers `Rk: NOT REVIEWED`.
  - A pass may carry NOT REVIEWED only on lines tagged `[unverifiable]`. The final
    report lists those lines first.
- **D2, harness support for C3.** The middle option:
  - `start --gate-path DIR` (one directory) is stored on the goal, added to the
    watch set when not already covered, and read by N1 and C5.
  - There is no gate digest, no `GateChanged` refusal and no `--gate-change` flag.
    Gate changes are reported in that check's `--did` and in the final report.
- **D3, C16.** Dropped: its supporting evidence was wrong.
- **D4, general advice in C9.** None added. The control never receives
  problem-solving tips, so adding them would confound the comparison. C9 is only
  the `[judgment]` tag (C1) and naming the chosen reading in the final report (C8).
- **D5, N1.** Built now.

## Harness contract

All changes live in `harness/goal.py` and `harness/cli.py`. Goals created without
`--gate-path` behave as before, except for the hash echo (C6) and the verdict
refusal (C5), which apply to every review.

### `doctor`

Adds `"gates": "<state>/gates"`, for example `PROJECT/.claude/harness/gates`. The
skills derive `GATES` from it, so if the P6 write canary fails the gate directory
can move by changing one line of harness code.

### `start --gate-path DIR`

- DIR is resolved against the project root and stored as `state["gate_path"]`
  (absolute). It is a top-level key, never inside `state["gate"]`, so the
  `gate_spec` equality checks are unchanged.
- `start` refuses with `GateRequired` unless:
  - DIR is a directory;
  - `DIR/request.md` exists and is not blank;
  - `DIR/requirements.md` exists and has at least one requirement line.
- If no watch path already covers DIR, DIR is appended to `watch_paths`.
- The flag is allowed with `--execution state`. It is stored, watched and used by
  `review`. Only `check` builds a review brief.

### Requirement lines

A requirement line in `requirements.md` is any line whose first token, after
optional list markers and emphasis, is `R<digits>`, followed by a non-word
character or the end of the line. Every `[gate]`, `[review]`, `[unverifiable]`
or `[judgment]` tag on the line belongs to that requirement. Later `R<digits>`
mentions on the same line, such as `(from OLD Rj)`, are ignored.

### `review` (C5, C6)

Every review, pass or fail:
- The evidence must contain the first 12 hex characters of `--hash`, compared
  case-insensitively. If it does not, `review` raises `EvidenceInsufficient`:
  "The evidence must be the reviewer's reply naming the reviewed checkpoint".
- Evidence is stored whole up to 16,000 characters. Above that, it keeps the first
  8,000 and the last 8,000 with a truncation marker between them, so the hash
  echo and the first R-lines are kept. Gate output keeps the existing 4,000-character tail.

A **pass** is also refused with `EvidenceInsufficient`, and the message names
every offending line, when any of these holds:
1. The last verdict for some `Rk` is FAIL or UNVERIFIED.
2. A line is a FAIL headline: either it contains `verdict`, then non-word
   characters, then `fail` (case-insensitive), or its first word is an all-caps
   `FAIL`.
3. The goal has a `gate_path`, and one of these holds:
   - `requirements.md` is missing;
   - a requirement line has no verdict;
   - its last verdict is NOT REVIEWED but the line is not tagged `[unverifiable]`.

Verdict lines are matched one line at a time. Lines that start with `>`
(quoted earlier verdicts) are skipped. A verdict line:
- may start with a list or table prefix and may wrap the id in emphasis;
- may carry one bracketed or parenthesised tag after the id;
- may use `:`, `.`, `)`, `|` or dash separators;
- ends in PASS/PASSED, FAIL/FAILED/FAILS, UNVERIFIED or NOT REVIEWED (any case).

A **fail** is always accepted. It still needs the hash echo and the
40-character floor.

### `status` and `start` listing (C14)

- `status` without a slug returns `{"goals": [row, ...]}`. Each row holds:
  - `slug`, `status`, `goal` (≤200 characters);
  - `gate` as `{kind, value ≤200}` and `review_criterion` (≤300 or null);
  - `gate_path`, `next_action` (≤300) and `abandoned_reason` (≤300 or null);
  - `started`, `completed_at`, `turns_used`;
  - `last_gate` (the last checkpoint's gate result or null) and `last_review`
    (the latest review result or null).

  A clipped field ends in `…`.
- `status SLUG` is unchanged, apart from the summary fields below and `gate_path`.
- `start` adds `other_goals`: the rows of every other goal in the project.

### Summary (C13)

`goal.summary` adds two fields:
- `deadline`: ISO-8601 `used.started + budget.max_minutes`, or null.
- `minutes_left`: `max(0, deadline - now)` in minutes, rounded to 0.1, or null.

### `check` review brief (N1)

When `review_required` is true, `check` adds two fields:
- `review_file`: `<tempfile.gettempdir()>/horizon-review-<slug>-<hash12>-<n>.md`,
  where `n` is the number of reviews already recorded on the goal plus 1.
- `review_brief`: the C4 template with these values filled in:
  - the checkpoint hash;
  - `request.md` and `requirements.md` from `gate_path`, each capped at 32,000
    characters with a pointer to the full file when cut;
  - the runtime's harness directory;
  - `review_file`.

The agent replaces only `<<ARTIFACTS>>` and `<<HOW_TO_RUN>>`. Without a
`gate_path`, `<<REQUEST>>` and `<<REQUIREMENTS>>` stay as placeholders.

If the latest recorded review on the goal is a fail, the brief ends with
`Previous:` and that review's FAIL and UNVERIFIED R-lines and FAIL headlines,
each prefixed with `> `.

## Skill prose

The two SKILL.md files and `docs/harness-cli.md` take the item text of C1-C14,
C9, C10 and N1 with these adjustments:

- `GATES` is `<gates path doctor prints>/NAME` rather than a literal
  `.claude/harness/gates/NAME`.
- The start example passes `--gate-path "$GATES"`. The review criterion is
  "Every line of $GATES/requirements.md holds, judged against $GATES/request.md".
- C4 becomes a short instruction. Send the `review_brief` from `check` unchanged
  except for the two placeholders, and add nothing to it. The full template is
  documented in `docs/harness-cli.md`.
- C3 uses the `--did` wording (D2 has no `--gate-change`). C12's "also watch
  `$GATES`" is dropped because the harness adds it.
- C5's prose mentions the harness refusals so the agent can read a refusal correctly.
- D1(b): "Record `pass` only when every requirement line is PASS, or NOT REVIEWED
  on a line tagged `[unverifiable]`."
- Codex mirror:
  - `.codex/harness` and `$horizon`;
  - `collaboration.spawn_agent` with `fork_turns: "none"`, waiting with the host's
    wait tools;
  - no `ScheduleWakeup`; "automation or heartbeat" instead.
- Bash examples stay outside numbered lists, so the contract test parses them.

## Testing

- TDD for each harness change, with fixtures taken from real pilot reply shapes:
  - `**Verdict: FAIL, narrowly.**`, `**FAIL.**` and `**Verdict: PASS, with caveats.**`;
  - bullets, tables and `>` quotes.
- The existing review call sites gain the hash echo (C6 lists them).
- The `bench/tb4/stream.py` extraction still sees only top-level summaries when
  `start` carries `other_goals`.
- Both SKILL.md files keep the same `##` headings, and every documented
  `python3 "$HARNESS"` line parses with the real CLI.
- The full suite, `sh bench/tb4/test.sh -q`, passes.
- `tests/test_tb4_bundle.py` stays pinned to `2f65f2a`.

## Risks carried from the report

- Prose rules may still be ignored. Only C5, C6, C14, N1 and the `--gate-path`
  checks are enforced.
- `.claude` is a protected directory in Claude Code. Whether a headless Write into
  `$GATES` works is the P6 canary.
- More procedure per round. The P7 timing dry run must show that a round fits in
  25 minutes.
- Every change was designed from the same 10 TB4 trials, and some of the design
  drew on EvoCode material. P8 handles the contamination.

## Revision after the parser probes (2026-10-09, supersedes the verdict grammar below)

Two adversarial probes ran against the revised parser and found highly plausible
replies that still went wrong in both directions:
- **False passes:** a sub-case line `R2 (empty file): FAIL` followed by
  `R2 (malformed): PASS`, `PARTIAL PASS`, `NOT PASSED`, a lowercase "fails",
  "could not verify".
- **False refusals:** "the previous FAIL is fixed", a validator's own FAIL
  output, `FAILED: 0` tallies, a Previous/Now table.

Reading verdicts out of free prose cannot be made reliable. Because the harness
owns the brief (N1), the reply format changes instead:
- The brief ends with a pre-filled `VERDICTS` ... `END VERDICTS` block holding
  one `Rk: ?` line per requirement.
- The reviewer replaces each `?` with exactly one of PASS, FAIL, UNVERIFIED or
  NOT REVIEWED. The ledger reads that block strictly, and anything else on a
  block line is refused as unreadable.
- Prose is checked only for a `Rk: FAIL`/`Rk: UNVERIFIED` line in the
  documented shape and for an unambiguous FAIL headline.
- A gate-path goal's pass needs exactly one complete block. A goal without a
  gate path needs no block, but any block it does carry must be all PASS.

The reviewer's own words remain the evidence, which was C5's aim, and no verdict
is guessed from prose.

## Revisions after the final review (2026-10-09)

A four-lens review with adversarial verification (8 agents) found that the verdict
grammar above was too narrow in both directions. One line could hold a FAIL after
an earlier PASS, which was read as a pass. Common correct reply shapes were refused
as "no verdict". The harness contract now differs from the sections above in these
ways:

- **Verdict lines.**
  - A line's worst verdict counts: its first verdict word, plus any all-capitals
    verdict or cross mark later on the line.
  - Quoted text, code spans, and counts or history (`0 FAILED`, `was FAIL`) are
    ignored.
  - Accepted forms:
    - arrows and `=`;
    - any number of tag groups;
    - headings, checkboxes and bullets;
    - table rows with the id in any cell;
    - id lists and ranges (`R1, R2`, `R1-R3`);
    - FAILING and FAILURE;
    - sub-ids such as `R1a` and `R1.2`, which are distinct requirements on both
      sides.
- **FAIL headlines.**
  - A line counts when it starts with Verdict, Overall, Result, Status,
    Conclusion or Outcome and says FAIL, or when it is only `FAIL` or `Fail` plus
    punctuation.
  - Lines inside code fences, such as test-runner output, never count.
  - A reply that is quoted in full is still read.
- **The hash echo is required for a pass only.** A fail is always kept, because
  losing a reviewer's FAIL is the failure v2 exists to prevent.
- **An empty requirements file refuses a pass.** Once a goal has a gate
  directory, a pass is refused when its `requirements.md` has no requirement
  lines.
- **Unreadable sibling goals.** The listing shows them as `{slug, error}` rows,
  so `start` still returns its new slug.
- **`review_file`** avoids every watched path and never raises.
- **The brief** tells the reviewer to keep its own files outside the project.
- **Caches.** The tree hash ignores cache directories in `git status` as well as
  in the walk, so caches written by a gate or a reviewer no longer make a
  checkpoint stale.
- **Skill prose:**
  - the baseline check and a last check before stopping never get a reviewer;
  - the C13 timing sentence moves before `start`;
  - the start example now sits before the baseline step.
