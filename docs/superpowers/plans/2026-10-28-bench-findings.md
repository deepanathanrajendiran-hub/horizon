# Fixes from the NL2Repo benchmark

**Goal:** make Horizon beat plain Claude Code when sessions end without warning. The NL2Repo benchmark (fun repo, `docs/evaluations/nl2repo-bench/report.md`) found four ways Horizon lost time; this branch fixes the first two in the CLI and all four in SKILL.md.

**Branch:** `fix/bench-findings`, from `fix/review-batches` (5c7f781). Stdlib only. Every code change gets a test that fails first.

## Findings this plan answers

1. **No hand-off from a cut-off session.** `next_action` comes only from `check --next`, which a session cut off mid-work never reaches. `observe` results are saved but `status`, `row` and the SessionStart hook ignore them. Every session started "Next: not set" and spent 4–9 of its 15 calls re-orienting.
2. **Review blocks building.**
   - After the gate passed, every `close` handed out another review brief. Each reviewer used up the session, and no verdict was recorded.
   - The carry rule needs every round to have said PASS, so a line with mixed verdicts stays open forever.
   - A FAIL written only in a round file never reached the agent.
   - Nine rounds ran on one unchanged checkpoint (pyjwt, DBUtils).
3. **Expensive start.** Request, requirements and a complete gate came before any code, and whole sessions passed with no code.
4. **All-or-nothing gate.** Top-level imports turn one missing module into a collection error for the whole gate.

## Tasks

### 1. `note` and `observe --next`
- `note SLUG --next TEXT` appends `{"at", "next"}` to `state["notes"]`. It charges nothing and runs nothing.
- `observe --next TEXT` stores `next` on its observation.
- `_next_action` returns the newest of: the last checkpoint's `next`, the last note's, and the last observation's.
- Execution (state-mode) goals are unchanged.
- **Tests:** a note after a checkpoint wins; a later checkpoint wins over an older note; `observe --next` sets it; `note` on a closed goal raises.

### 2. The hand-off shows the last gate run
- `summary` gains `last_observation`: role, at, gate, and the last 600 characters of the output.
- `row` gains `last_observed` (gate and at).
- The SessionStart line adds `Last gate run: <gate> (<role>, <at>): <last 300 chars, one line>` whenever there is an observation newer than the last checkpoint.
- **Tests:** in `test_goal.py` and `test_hooks.py`.

### 3. The newest verdict decides a line
- `reviewing.carried`: rounds are read in order, and within a file the last decided line wins. A line is carried when its newest verdict is PASS.
- **Tests:** PASS, then FAIL, then PASS is carried; PASS, then FAIL is not.

### 4. A FAIL in a round file stops the brief
- `reviewing.failed_in_rounds(slug, digest, requirements, review_file)` returns the newest-FAIL lines with their text, from this checkpoint's round files.
- When it is non-empty, `_review_handoff` sets `review_failed_lines` and `next_action_hint` ("Fix these lines, then check"), and issues no `review_brief`.
- **Tests:** a FAIL in an old round blocks the brief; the same line passed later does not.

### 5. At most three rounds per checkpoint
- `REVIEW_ROUNDS_PER_CHECKPOINT = 3`. When this checkpoint already has that many round files and the goal is not complete, `_review_handoff` sets `review_paused` and issues no brief. The message tells the agent to build: extend the gate toward what the request still lacks, fix what it finds, then `check`.
- Changed files give a new checkpoint, which starts with fresh rounds.
- **Tests:** in `test_horizon_cli.py`, the 4th close on the same files has no brief and has `review_paused`; a changed file gets a brief again.

### 6. SKILL.md (both copies), commands.md, docs/harness-cli.md
- **Resuming:** a fast path at the top. When the SessionStart message names an active goal with a next action, run `status SLUG` once, skip re-reading request, requirements and spec unless the next action needs them, and edit within the next few calls.
- **Set the next action early:** run `note --next` as soon as a target is picked, and again before any long read. Don't leave it for `check`.
- **Build first, review last:** dispatch a reviewer only when the build covers every part of the request. Otherwise extend the gate and build. Handle `review_failed_lines` (fix those lines) and `review_paused` (build).
- **Cheap start:** in session one, a first gate of a few checks is enough: the package imports and the named entry points exist. Grow it while building. Import inside each test, so that a missing module fails one test and not the whole run.
- **Timeouts:** use the CLI's `--timeout`; `timeout` is not on macOS.
- **Contract tests:** in `tests/test_skill_contract.py`, for each new rule phrase.

## Out of scope (noted)
- Settling `[unverifiable]` lines that reviewers answer UNVERIFIED. Task 5 bounds the cost.
- Moving rarely used SKILL.md sections into references/.

## Then
Bundle the branch head and pre-register a rerun of the five benchmark tasks (fun repo). The re-grader becomes the primary grader.
