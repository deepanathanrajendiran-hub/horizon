# Horizon redesign: reliable resumption, evidence-based completion, escalating verification (design)

Date: 2026-10-27. Status: design approved in chat (option "A now, then C"); this written spec awaits review.
Evidence: `docs/research/2026-10-27-horizon-redesign-evidence.md`. It holds 55 findings from four research tracks, each audited against its source: 47 supported, 8 overstated (only the weaker reading is used).

## Why

Horizon failed in four ways in our 2026-10 studies (Haiku 5.5 mini GPT cap study; Sonnet on EvoCode-Bench):

1. **State mode stalls after a killed session.** An attempt stays `attached` to a worker that died with its session. Nothing expires, and `recover` needs liveness evidence nobody can produce. The result: one build ended at 0/23 as a false done, and another lost 4 sessions.
2. **It never finishes under short sessions.**
   - Completion needs `check`, then a reviewer, then `review`, then `complete`, all for the same turn. That never fit in a 15-call session, so all 10 sessions were spent after the tests already passed, at about 3× plain's cost.
   - A `skipped` check on an unchanged tree also blocks completion, because `_apply_completion` reads `checkpoints[-1]`.
3. **Same-model review shares the agent's blind spots.** On EvoCode-Bench, "complete" predicted a hidden-test pass only at the base rate. Horizon gave no gain at about 3× cost.
4. **State mode cannot record a baseline.** `check` is refused for state goals.

## Goals and success criteria

Horizon must handle cut-off sessions, long unattended runs and verification equally well.

- **Easy tasks.** No stalls and no false done, and it finishes once the work is verified. Cost is at most **1.5× plain's tokens** (median paired ratio) and is also reported as cost per pass.
- **Hard tasks.** More tokens are acceptable only if Horizon **beats plain** on pass rate.
- **Setup.** It works in Claude Code alone with no new API keys. If the Codex CLI is installed, it is used automatically as a second-family reviewer. The implementation stays stdlib-only Python.

## Decisions (from the user, 2026-10-27)

| Question | Decision |
|---|---|
| Architecture | Stage A (fix the protocol) first, then Stage C (escalation ladder). Option B (event-log rewrite, worktree per attempt) is deferred. |
| Verifier | Claude-only by default; Codex used automatically if installed; no API keys required. |
| Hooks | Horizon installs its hooks into the project's `.claude/settings.json` when the first goal starts, and says so. |
| Review | A goal may complete on a passing gate with no LLM review when the gate is *strong* (defined below). |
| Reclaim | A new session may take over a dead session's attempt immediately. Late writes from the old worker are rejected by an epoch check. |
| Test files | Changing a test file that existed at the baseline blocks completion until a reviewer or the user approves the change. |

Defaults chosen by the designer:

- **State mode becomes opt-in.** It is for long unattended runs and parallel work; conversation mode stays the default.
- **Codex findings are advisory.** A finding blocks only when it comes with a failing reproduction that Horizon runs itself.
- **Fallback reviewer.** When Codex is absent, the second reviewer is a Claude model no weaker than the author.
- **Recovered attempts.** A dead attempt's edits are kept and re-verified, not discarded. Since "done" comes from evidence about the current tree, they get no special trust.
- **The "blocked / spec conflict" terminal status is deferred.** Its evidence is weak for Claude models.

## Principles (each traced to the evidence file)

1. The CLI decides when work is done, from recorded runtime evidence, never from the model's own report (Unreliable Progress Bar; Overclaiming; Exactly-Once).
2. Executable checks come first. LLM review is used only where checks can't reach, or when a trigger fires (Huang et al.; Goel et al.; R2E-Gym; ECLoop; VeriHarness).
3. A claim on an attempt expires on a clock or when its session changes. Expiry, not proof of death, frees it, and fencing rejects stale writers (Gray & Cheriton; Kleppmann; Temporal).
4. On resume, re-observe ground truth before trusting saved state (Anthropic long-running harness post; Exactly-Once read-back).
5. Gate results, baselines and reviews are recorded observations keyed by tree hash. "Done" is computed over them, not from one same-session sequence (Temporal event history, applied within the existing checkpoint list).
6. Anytime semantics: the deliverable is always the current tree, and the harness finalizes it when the budget runs out (Scrouting; BATS, weaker reading).
7. Spend extra effort on observed signals, not on predicted difficulty (Bai et al.; SWE-Router, weaker reading; Kapoor).
8. Protect gates structurally, not with instructions (ImpossibleBench; METR reward hacking).

## Stage A: fix the protocol

### A1. Attempt leases with epoch fencing (`harness/steps.py`)

Each state-mode attempt gains:
- `owner_session`: the Claude Code or Codex session id;
- `lease_expires_at`;
- `epoch`: a counter bumped on every reclaim.

The lease term defaults to the per-attempt deadline (gate timeout plus a margin), capped by the goal's time budget.

- **Renewal.** A PostToolUse hook renews the lease for its worker (A6). Renewal costs no model calls.
- **New command: `step-reclaim SLUG ATTEMPT`.** It is allowed when either holds:
  - the lease has expired;
  - `owner_session` differs from the current session and that session has sent no renewal for 300 s (the user chose immediate reclaim for a new session). The 300 s window exceeds a slow model call, so a second live session doesn't steal a working attempt.

  It requires no liveness evidence. It:
  - bumps `epoch`;
  - records the current tree hash;
  - saves the dead attempt's working-tree diff as an observation;
  - marks the attempt `reclaimed`.
- **Fencing.** `step-finish` and `step-attach` must present the current epoch. A stale epoch is refused under the goal lock (the existing `expected_revision` stays).
- **Orphaned `finishing`.** An attempt stuck in `finishing` (its gate outlived the CLI) becomes reclaimable once `started_at + gate timeout + margin` has passed.
- **Workspace writes are not fenced.** A surviving zombie worker could still edit files. Stage A accepts this risk. Completion re-checks the tree hash, so a late edit invalidates an earlier pass instead of slipping through. A kill-9 probe (Testing, step 0) decides whether Option B's worktrees are ever needed.

### A2. Baseline observation (`harness/goal.py`, `harness/cli.py`)

A new command, `observe SLUG`, runs the gate under the goal lock, outside any attempt, for both modes. It appends a checkpoint with:
- `kind: "observation"`;
- `role: "baseline" | "resume" | "final"`;
- `tree_hash`, `gate_spec`, `result`, `output_digest`.

It charges no turn and changes no task state. `start` records a baseline observation automatically, which fixes failure 4.

At baseline it also records the **test-file manifest**: hashes of files that match the gate's test paths, or the watch set's test patterns (`test_*.py`, `*_test.*`, `tests/**`, plus any `--test-path`).

### A3. Evidence-based completion (`harness/goal.py`, `harness/gate.py`)

`_apply_completion` and `_apply_review` stop reading `checkpoints[-1]`. Completion is allowed when all of these hold:
- **Gate.** Some non-skipped checkpoint or observation has `result == "pass"`, and its `tree_hash` and `gate_spec` equal the current values.
- **Tests.** The test-file manifest is unchanged since the baseline, or every changed file has an approval record.
- **Review.** If review is required (A5), a passing review is recorded for that same `tree_hash`.

Reviews are keyed by `tree_hash`, not by turn. A `skipped` checkpoint never blocks completion.

### A4. One-call finishing and cheaper setup (`harness/cli.py`)

- **`close SLUG`** finishes in one call. It:
  1. runs `observe` if no pass exists for the current tree;
  2. completes if A3 allows it;
  3. otherwise returns exactly what is missing (gate, review at this hash, or a test-change approval).
- **`start` reads its inputs from files or stdin.** The request, the requirements and the gate path can be passed with `--request-file` and `--requirements-file`. Setup then needs one call instead of several writes plus `start`.
- **Budget exhaustion is anytime.** The Stop hook (A6) runs `close --if-ready`. When the goal's budget is spent, the CLI records `finalized: budget_exhausted` with the last gate status for the current tree. The tree is the deliverable.

### A5. When review is required

A **strong gate** is a command gate whose test files all existed at the baseline, are unchanged (or approved), and were not written by the agent during this goal.

Review is required when any of these hold:
- the gate is not strong;
- the gate is a review criterion;
- any requirement line is tagged `[review]` or `[unverifiable]`;
- a Stage C trigger fires (C1).

Otherwise a strong passing gate completes the goal with no LLM review (the user's decision).

### A6. Hooks Horizon installs (`harness/hooks.py`, new)

Hooks are installed into the project's `.claude/settings.json` on the first `start` in a project, and Horizon reports that it did. Install is idempotent, and `uninstall-hooks` removes them. In Codex, the same commands are run by the skill at session start, at a cost of one call.

- **SessionStart → `resume-scan`.**
  - reclaims attempts whose session is gone or whose lease expired;
  - runs `observe` with role `resume` for each active goal;
  - injects a short status line: goal, gate status at the current tree, what's missing for `close`, and the budget used.
- **PostToolUse → `renew`.** Renews the lease of the attempt owned by this session or `agent_id`, and counts calls for the budget readout.
- **Stop → `close --if-ready`.** Zero model calls.

Hooks never call a model and never edit project files other than Horizon's state.

### A7. SKILL.md and docs

- Rewrite the protocol for the new flow: `start` (one call), work, `close`.
- Only on what `close` reports: run the reviewer or ask for test approval.
- State mode is described as opt-in for long unattended or parallel work.
- Remove the documented "skipped blocks completion" behavior.

## Stage C: escalation ladder (built on A)

### C1. Triggers

Triggers are computed by the CLI from the recorded state using stdlib only, never from the model's self-estimate. Each trigger is recorded with its reason.

- the first gate observation after baseline fails;
- the gate is not strong (A5);
- `[review]` or `[unverifiable]` lines exist;
- a baseline test file changed;
- no gate progress (same failing count) across K = 3 increments;
- the same file is edited in N = 5 increments with no gate change;
- a requirement line has no linked passing evidence.

### C2. Tiers

- **Tier 0 (default).**
  - The targeted gate plus the project's full existing test suite as a regression gate.
  - Test-file protection (A2/A3).
  - For bug fixes: a reproduction that fails at baseline and passes at the end (fail-to-pass, SWT-Bench). This uses A2's baseline observation.
  - With a strong gate and no trigger, tier 0 completes on a pass.
- **Tier 1 (a trigger fired).**
  - An independent subagent writes tests from the request and requirements only, without the implementation transcript. Its tests are added to the gate under a separate path. Their failures are triaged by running them, not trusted outright (CodeT; agentic PBT, about 44% invalid raw reports).
  - Then one fresh reviewer with a protocol: link each requirement line to evidence and re-run that evidence (VeriHarness).
- **Tier 2 (tier 1 still disputed, or a `[review]` line on a hard goal).**
  - A second-family reviewer: `codex exec` in a read-only sandbox, with a timeout, an input-size check and a recorded exit code, when Codex is installed. Otherwise a Claude model no weaker than the author.
  - Its findings are advisory. A finding blocks only with a failing reproduction that Horizon runs.
  - A crash, timeout or empty reply is recorded as `not_reviewed`, never as a pass.
- **Review provenance.** `review` records the reviewer's `model`, `family` and `tier`. Completion rules can require a family different from the author when tier 2 applies.
- **Tier 3 is out of scope for this spec.** That covers diverse parallel attempts and a stall restart.

## Data model changes (backward compatible)

- **Goal JSON gains:**
  - `test_manifest` (path → sha256, at baseline);
  - `approvals` (path, tree_hash, by, reason);
  - `triggers` (name, reason, at);
  - `tier`;
  - `hooks_installed`.
- **Checkpoints gain** `kind` (`increment` or `observation`), `role` and `output_digest`.
- **Attempts gain** `owner_session`, `lease_expires_at`, `epoch` and `reclaimed_from`.
- **Reviews gain** `tree_hash`, `model`, `family` and `tier`.
- **Old goals** load with defaults. An old active goal must run `observe` before `close`, and its test manifest is taken at that first observation.

## Error handling

- Every refusal names what is missing and the exact command that supplies it.
- Hook failures are recorded and never block the session. Without hooks, the skill falls back to running `resume-scan` itself.
- A reviewer crash or timeout is `not_reviewed`.
- A gate timeout is `fail`, with the output saved.
- Fingerprinting failures fail closed, as today.

## Testing

0. **Kill-9 probe (before building A1's details).** Start a background worker subagent, `kill -9` the parent session, and check whether the worker keeps running or writing. If it survives, either shorten leases or bring Option B's worktree isolation forward.
1. **Model-free conformance tests** (pytest, stdlib only):
   - kill between every pair of steps, then resume;
   - two concurrent resumers, of which only one may reclaim;
   - replaying `step-finish` and `step-reclaim`;
   - a stale epoch refused;
   - a `skipped` checkpoint not blocking `close`;
   - a pass reused across sessions on an unchanged tree;
   - a tree change invalidating a pass;
   - a test-file change blocking completion until approved;
   - strong versus weak gate classification;
   - trigger computation;
   - Codex wrapper failure modes (timeout, empty output, non-zero exit → `not_reviewed`).

   Each bug gets a test proven to fail on the pre-change code.
2. **Stage A study: cap study re-run.** Haiku 5.5, 15 calls per session, up to 10 sessions, the same SPEC and hidden tests.
   - **Arms:** plain, current Horizon (`main` before this work), Horizon-A, and Horizon-A state mode with a session kill injected mid-attempt.
   - **Gate to ship A:** no false done; no stalled build; Horizon-A completes within one session of its first all-pass; median paired token ratio against plain ≤ 1.5×.
3. **Stage C study.** EvoCode-Bench, then SWE-bench Pro (Colima).
   - **Metric for failure 3:** does "complete" predict a hidden-test pass above the base rate?
   - **Also measured:** pass rate against plain, cost per pass, and the tier distribution.
   - **Design:** pre-registered, with repeated paired runs per task and one tier ablated at a time.
   - **Gate to ship C:** better calibration or pass rate than plain on the hard benchmark, with easy-task cost still ≤ 1.5×.
4. **Cost measurement.** Use total tokens from result records, with input including cache plus output. Killed sessions report input only, because the stream-json output counts are placeholders (cap-study deviation 6). Also report model calls.

## Out of scope

- Option B: the event-log rewrite and a git worktree per attempt.
- Tier 3: parallel diverse attempts and a stall restart.
- The "blocked / spec conflict" terminal status.
- Scheduling or waking an app.
- Changes to Refine.

## Risks

- **Lease and session heuristics.** A reclaim racing a surviving worker could leave overlapping edits. Mitigated by the tree-hash re-check at completion and by the kill-9 probe.
- **Strong-gate classification.** Whether a gate counts as strong depends on the test-path patterns. A misclassified agent-written test could skip review. Test-file provenance is recorded per file, and an ambiguous file is treated as not strong.
- **Untuned triggers.** The K and N thresholds are uncalibrated, so the Stage C study measures how often each trigger fires and what it costs.
- **Hook setup.** Writing to `.claude/settings.json` touches user config. Install is announced, idempotent and reversible.
