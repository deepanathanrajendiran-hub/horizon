# Horizon on Terminal-Bench 4.0: design for a paired benchmark pilot

Date: 2026-10-07. Status: design approved in conversation on 2026-10-07 (question, metrics, task selection, runner, arms, bundle, scoring, file layout). Revised the same day after a three-lens review (validity, feasibility, consistency); the review's corrections are listed at the end. This written spec awaits the user's review before an implementation plan is written.

## Decision and question

Measure whether Horizon helps on externally authored long-horizon tasks. Run the same Terminal-Bench 4.0 tasks with headless Claude Code on Sonnet 5.5 in two arms that differ only in Horizon:

- **Control:** the unchanged task instruction plus a shared time, persistence, and verification paragraph.
- **Horizon:** the same text plus one sentence that invokes Horizon, with the Horizon skill bundle installed.

The question is whether Horizon's machinery (goal, self-authored gate, independent review, checkpoints, budget) changes hidden-verifier success or false completion claims relative to an equally instructed agent. Earlier in-repo measurements could not answer this: the [A/B trial](../results.md) (n=5 per arm) and [Bench 2](../bench2-interruption.md) (n=3 per arm) used small home-made fixtures on which both arms succeeded. The [evaluation protocol](../../evaluation.md) calls for external tasks, frozen rubrics, and preserved failures. This design applies that protocol to one condition pair.

This is a staged pilot. Stage 1 runs 10 tasks × 2 arms = 20 measured trials to validate the setup and measure baseline difficulty and variance. It makes no significance claim. Stage 2 happens only if the decision rule below passes, under a separately frozen protocol amendment.

## Evidence for the benchmark choice

A survey of 56 candidate benchmarks ranked three as strong fits. The ranking and verifier output are retained verbatim in [benchmark-survey-2026-10-07.json](../../evaluations/tb4-horizon-pilot/evidence/benchmark-survey-2026-10-07.json). Independent verifiers then checked each of the three against primary sources.

| Candidate | Outcome on this machine (Apple M4, arm64, 16 GB, Docker Desktop) |
| --- | --- |
| **Terminal-Bench 4.0 — chosen** | 66 tasks; expert estimates median 4 h, 52 in the 1–8 h band; every task scored by a separate verifier environment; Claude Code + Sonnet 5.5 at 61.8% ±2.9 ([Snorkel](https://snorkel.ai/leaderboard/terminal-bench-4-0/)), so neither floor nor ceiling. Harbor's `claude-code` agent supports subscription OAuth and skill injection. |
| DeepSWE v1.1 | Agent and verifier images are amd64-only; open grader defects let test-infrastructure edits and crashed workers pass; Sonnet 5.5 already scores 71.0%. Rejected. |
| Long-Horizon-Terminal-Bench | 24 of 47 tasks deny the agent container network, so Claude Code cannot install or reach the API there; all images are amd64-only. Rejected. |

Terminal-Bench constraints that this design handles:

- The official dataset's prebuilt images are amd64-only, so native arm64 means building from a `v4.0.0` checkout. Arm64 build failures score 0 and look like agent failures ([harbor#2758](https://github.com/harbor-framework/harbor/issues/2758)).
- Harbor 0.24.0 rebuilds a local task's image at the start of every trial when `task.toml` sets no `docker_image`, which is true of all 66 tasks. Base images are unpinned, so builds can drift.
- Solutions and tests are public on GitHub, and the agent environment has public network. Disabling WebSearch/WebFetch does not block `curl`.
- Leaderboard scores are not comparable with this run: different images (native arm64), a 120-minute cap instead of 8 hours, an explicit effort level, and a modified instruction. The comparison is between our two arms only.

## Fixed experimental settings

| Setting | Value (identical in both arms) |
| --- | --- |
| Benchmark | `harbor-framework/terminal-bench` at tag `v4.0.0`; record the resolved commit |
| Runner | Exactly `harbor==0.24.0`. If it is unavailable, stop and re-verify the Harbor behaviors this spec relies on before running |
| Agent | Harbor `claude-code` agent; Claude Code pinned to 2.1.293 and verified inside the container |
| Model and effort | `claude-sonnet-5-5` with `reasoning_effort=high` (the model's documented default), set explicitly and recorded in Harbor's job config |
| Output limit | `CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000` (the benchmark repository's own default) |
| Authentication | `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token` plus `CLAUDE_FORCE_OAUTH=1`; environment only, never written to a file in the repository |
| Web tools | WebSearch and WebFetch disabled |
| Agent hard limit | 7,200 s per trial; verifier timeouts unchanged |
| Images | One locally built, tagged image per task, pinned through `docker_image` in every task copy (below); build cache not pruned during stage 1 |
| Concurrency | Strictly one trial at a time |
| Docker Desktop VM | About 12 GB memory and 8 CPUs, recorded |
| Order | Both arms of a task run back to back; which arm runs first comes from the seeded assignment in `manifest.csv` |

No additional model API key is used. If the subscription path fails in the setup trials, stop and report. Do not substitute an API key.

## Task selection

Selection uses only task metadata, builds, and reference runs. It never uses measured agent outcomes.

1. **Static filter** on each `v4.0.0` task:
   - `[environment]` requests no GPU, at most 8 CPUs, and at most 8,192 MB of memory;
   - no `environment/docker-compose.yaml` (multi-service tasks);
   - the verifier keeps network access: exclude when `[verifier.environment]` sets `allow_internet = false` or a non-public `network_mode`;
   - not one of the five amd64-only tasks (terminal-bench issues #1802–#1806): `coq-block-bound`, `hof-topology-interpenetration`, `math-eval-grader`, `gsea-proteomics`, `wdm-design`.

   Record every exclusion and its reason. The reviewer's parse of these predicates leaves 43 tasks; the implementation records the actual count.
2. **Build and Python check:** build each survivor once on the M4 and tag it `tb4-local/<task>:v4.0.0`. Record the image ID. The image must provide `python3` at version 3.10 or later, and the bundle's `doctor` command must succeed inside it. Do not install Python for one arm only; exclude tasks that fail.
3. **Reference checks** on the tagged image:
   - the oracle (reference solution) must score 1;
   - a do-nothing agent must score 0;
   - the oracle must still score 1 with the Horizon bundle injected exactly as Harbor injects it and a representative Horizon ledger planted at `<workdir>/.claude/harness`.

   The third check catches verifiers that grade the whole workspace or a `git add -A` diff, where files that only the Horizon arm creates would otherwise be scored. A task that fails any check is excluded and reported.
4. **Seeded draw:**
   - Take the survivors with expert estimates of 2–8 h.
   - Using a fixed seed committed with the manifest, first draw one setup task.
   - Then draw 10 measured tasks, stratified by the task's category field: categories in seeded random order, tasks drawn round-robin.
   - Assign the first arm per task by a seeded, balanced 5/5 split.

   The seed, the algorithm, and the result are written to `manifest.csv`.

## The two arms

Both arms use the same delivery mechanism: per-arm derived copies of each task directory, pinned to the same `docker_image`, whose instruction files differ only by the appended text. The implementation plan confirms the mechanism against Harbor. Both copies are hashed. The appended sentence and the skill flag are the only differences between arms.

Shared paragraph, appended in both arms:

> You have 105 minutes. Keep working until the task is fully complete; do not stop early. Before finishing, verify your work against the task's requirements with your own tests or checks. If you cannot finish within 105 minutes, stop and state exactly what is complete and what is incomplete.

Horizon arm only, appended after the shared paragraph:

> Before other work, use the horizon skill to create a goal with a verification gate you write, a review criterion, and the budget `--max-minutes 100 --max-turns 200`; follow that goal until it completes or its budget runs out.

Both arms share the same 105-minute soft deadline and the same 120-minute hard limit, so they have equal working time and the same expected way of ending.

- **Turn budget:** `--max-turns 200` is pinned because the CLI's turn budget defaults to 20 and cannot be extended without user authorization, which a headless run never has. An agent-chosen turn count would add a hidden stopping rule to the Horizon arm only.
- **Minute budget:** Horizon's clock starts at goal creation, so `--max-minutes 100` plus creating the goal first targets the shared 105-minute deadline. The goal-creation offset is recorded for each trial.
- **Horizon's normal behavior:** Horizon runs as designed, including its fresh reviewer subagent, whose usage counts toward the Horizon arm.
- **Control:** gets no skill.

The prompt texts are stored verbatim and frozen.

## Horizon bundle

The checked-in skill cannot be copied as-is: `scripts/harness.py` and `references/commands.md` are symlinks into the repository and arrive dangling in a container, and `cli.py` imports sibling modules.

- **Source:** commit `2f65f2a`, the state-mode pilot's frozen source. `git diff 2f65f2a f273da9 -- harness .claude .agents tests` is empty, and the full suite passed at `f273da9` on 2026-10-07: 530 passed in 40.44 s with `HOME` isolated.
- **Build:** read files from the Git object store at that commit, not the working tree. Exact tree:

  ```
  <bundle-root>/horizon/SKILL.md                  # .claude/skills/horizon/SKILL.md, unchanged
  <bundle-root>/horizon/scripts/harness.py        # harness/cli.py
  <bundle-root>/horizon/scripts/{filelock,gate,goal,paths,refine_log,steps,task_state,tree_hash}.py
  <bundle-root>/horizon/references/commands.md    # docs/harness-cli.md
  ```

  No symlinks. Harbor receives `--skill <bundle-root>/horizon` and names the skill after that directory. The SHA-256 manifest of every file is written outside the skill directory and copied into `environment.md`.
- **Mode:** default (conversation) mode. State mode does not work in this layout: `task_state.procedure()` resolves the skill file relative to a repository layout that the bundle lacks. The protocol documents this, and the integrity scan flags any `--execution state` call as a Horizon defect.
- **Smoke tests:** in clean `python:3.10-slim` and `python:3.12-slim` containers with no access to this repository, install the bundle as a Claude skill. Then run `doctor`, a `start` with a trivial command gate, `check`, and `status` against a scratch project, and confirm the ledger lands in that project's `.claude/harness`. Step 2 of task selection additionally runs `doctor` in every candidate task image.

Horizon state stays inside each container, at `<workdir>/.claude/harness` (normally `/app`). The host's live ledger is never touched by any trial. If Harbor can collect that directory as a trial artifact, collect it in both arms. Otherwise, derive the Horizon fields from the CLI's JSON output in the transcript.

## Setup trials and the freeze

1. **Setup trials (not measured).** Run both arms on the setup task drawn in step 4. That task is excluded from the measured set. The setup trials confirm:
   - subscription authentication;
   - Claude Code version, model, and effort;
   - skill loading and CLI execution in the task container;
   - that Harbor uses the pinned image without rebuilding;
   - the shape of real Harbor output.

   Their job directories, with the token scrubbed, become the scorer's test fixtures.
2. **Freeze commit**, made before the first measured trial and recorded in every result row. It contains:
   - the protocol `README.md`;
   - `manifest.csv` with the seed and algorithm;
   - both arm texts;
   - the judge prompt and exact judge command;
   - the integrity-scan patterns;
   - `results-schema.md`;
   - `environment.md`: versions, commits, image IDs, the bundle SHA-256 manifest, and task-copy hashes;
   - the scorer, with its tests passing.

Nothing listed here changes after the first measured trial. A change requires a recorded protocol deviation.

## Scoring

Score from disk only, from Harbor's per-trial outputs. An agent's own report is evidence, not the result. Each record carries `task`, `arm`, `attempt`, `trial_path`, `image_id`, `freeze_commit`, and `protocol_deviations`. Pass means `reward == 1.0`.

| Field | Definition |
| --- | --- |
| `reward` | The task verifier's score from Harbor's result files (primary outcome) |
| `valid` | See validity below; computed before reward is read |
| `timed_out` | Harbor `exception_info` type is `AgentTimeoutError`. Valid; the verifier still runs |
| `final_message` | The `result` text of Claude Code's stream-json `result` record; null when that record is absent (always the case after a timeout). Never the last raw assistant line, never a subagent message |
| `label` | Judge output for a non-null `final_message`: `claims_complete`, `reports_incomplete`, or `neither`; null otherwise |
| `false_done` | `label = claims_complete` and `reward < 1` |
| `honest_stop` | `label = reports_incomplete` and `reward < 1` |
| `tokens`, `cost_usd` | Sum of the `result` record's `modelUsage` entries (input, output, cache read, cache creation; includes subagents) and its `total_cost_usd`. Null when the record is absent. Harbor's `agent_result` token counts and its LiteLLM cost fallback are never used |
| `wall_s` | From Harbor's `agent_execution` start and finish times (always present) |
| `skill_loaded`, `horizon_started` | Whether the skill was available, and whether a `start` returned a goal slug (both arms; the control should show neither) |
| `horizon_budget`, `horizon_used`, `horizon_end` | Horizon arm: `max_minutes`/`max_turns`, turns used, goal-creation offset, and the end reason (`complete`, `minutes exhausted`, `turns exhausted`, `abandoned`, `no goal`, `unknown`) |
| `horizon_gate` | Horizon arm: the gate command chosen |
| `audit_flags` | Results of the integrity scan |

- **Validity** is determined from what ended the run. A trial is invalid if any of these holds:
  - Harbor `exception_info` has any type other than `AgentTimeoutError`;
  - the verifier raised an error or produced no reward;
  - the run ended because of a usage-limit, authentication, or API error, meaning the `result` record has `is_error` with such a cause, or no main-session assistant turn follows such an error;
  - the trial's image ID differs from the task's validated image.

  Errors that Claude Code retried, followed by further main-session work, do not make a trial invalid.
- **Reruns:** if either arm of a task is invalid, rerun both arms in the original order, after any usage limit has reset. At most one rerun per task. A second invalid result excludes that task's pair. Discarded attempts stay on disk, and a first-attempt-only table is reported alongside the main one. Invalid counts are reported per arm.
- **Integrity scan:** scan Harbor's `claude-code.txt` plus every main and `subagents/*.jsonl` session file. Flag:
  - retrieval of the Terminal-Bench repository or its solutions;
  - reads or edits of verifier test files;
  - `curl`/`git` access to benchmark sources;
  - any `--execution state` call.

  Flagged trials stay on disk. The main table is shown with and without flagged pairs, dropping the whole pair when either trial is flagged.
- **Judge:** a pinned `claude -p --model claude-opus-5-5` call with tools disabled, run from an empty scratch directory with an isolated `CLAUDE_CONFIG_DIR` (no user settings, skills, memory, or MCP servers). It sees one `final_message` and a frozen, arm-neutral rubric, and returns the label as JSON:
  - `claims_complete`: asserts the task is done, with no requirement stated as unmet;
  - `reports_incomplete`: states that at least one requirement is unmet, unverified, or failing;
  - `neither`: anything else.

  It never sees the reward or the arm label, although Horizon's messages may reveal the arm; the report states this limitation. The judge's label is the recorded value. In stage 1, the controller also labels all non-null final messages before reading rewards and reports the agreement rate. The controller's labels never replace the judge's.

Raw evidence (transcripts, session files, result files, verifier output) is retained outside the repository. The repository stores the per-trial records plus SHA-256 digests of the raw files.

## Stage 1 analysis and decision rule

The analysis is intention-to-treat: every valid Horizon-arm trial counts as Horizon, whether or not the agent used the skill. Stage 1 is descriptive:

- a per-task 2×2 table of pass/fail by arm, and the discordant tasks;
- per arm: timeouts; false-done and honest-stop as counts and as rates over trials with `reward < 1` and a non-null final message;
- the Horizon end-reason counts;
- treatment fidelity: `horizon_started` per trial, reviewer dispatch, `review`, and `complete`;
- per arm: median tokens and cost with their n and null counts, and median wall time with timeouts counted as censored at 7,200 s;
- invalid, rerun, excluded, and flagged counts.

Proceed to stage 2 only if all three conditions hold:

1. At least 9 of the 10 task pairs are valid after at most one rerun each.
2. The control arm passes between 2 and 8 (inclusive) of the valid pairs.
3. Horizon `start` succeeded in at least 90% of valid Horizon trials, and no control trial invoked the Horizon CLI.

Otherwise, report the failure and fix the setup or task set before spending more usage.

The stage 2 amendment is frozen before any stage 2 trial. It fixes:

- the task set;
- the number of repetitions;
- the test: exact McNemar on discordant pairs, two-sided, α = 0.05. With more than one repetition, the task is the unit instead, using a sign test on per-task pass-rate differences.

Stage 1 tasks and trials are excluded from the confirmatory test.

## Files and locations

| Location | Content |
| --- | --- |
| Branch `eval/tb4-horizon`, worktree `.superpowers/worktrees/tb4-horizon` | All repository changes for this work |
| `bench/tb4/` | Standard-library Python scripts: build the bundle; filter, build, and validate tasks; draw the manifest; generate per-arm task copies; run trials through Harbor; scan, judge, and score from disk |
| `tests/test_tb4_*.py`, `tests/fixtures/tb4/` | Pytest coverage for the bundle builder (exact tree, no symlinks, digests), the static filter, the seeded draw, the validity rules, the field extraction (including a subagent and a timeout fixture), and the integrity scan. Fixtures come from the scrubbed setup trials |
| `docs/evaluations/tb4-horizon-pilot/` | Protocol `README.md`, `manifest.csv`, `prompts/` (arm texts, judge prompt and command), `results-schema.md` (following the state pilot's convention), `environment.md`, `results.jsonl`, `report.md`, `evidence/` |
| `~/Documents/tb4-horizon-runs/` | Terminal-Bench checkout, built bundle, Harbor job directories, raw transcripts (outside the repository, not a temporary directory) |

Run the test suite with `uv run --with pytest` and an isolated `HOME`. No script invokes Horizon against the host repository. Before each commit, check that no committed file contains the OAuth token.

## Scope

In scope: stage 1 setup, the setup trials, the 20 measured trials, scoring, the report, and the stage 2 go/no-go decision.

Out of scope:

- refine;
- Horizon state mode;
- forced interruption and resume (a candidate stage 2 extension);
- Codex runtimes;
- leaderboard-comparable scores;
- changes to Horizon itself.

If a Horizon defect blocks a trial, record it as a result. Fixing it would change the treatment, so it requires a new frozen source and the user's approval.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Arm64 builds fail, drift, or differ between arms | Build once, tag, pin through `docker_image`; oracle, nop, and bundle-plus-ledger checks on that image; per-trial image-ID check |
| Horizon's own files change what the verifier grades | Reference check 3 with the bundle injected and a ledger planted |
| Hidden Horizon stopping rules | Turn budget pinned at 200; shared 105-minute deadline; end reason recorded |
| Subscription usage limits interrupt trials | Structured validity rule; rerun both arms after the limit resets; report per arm; schedule around the separate state-mode pilot, which shares usage |
| Timeouts hide final messages and usage | `timed_out` recorded; labels and usage null with counts; wall time from Harbor timing, censored at the cap |
| Answer leakage through public network | WebSearch/WebFetch disabled; integrity scan includes subagent sessions; tables with and without flagged pairs |
| The instruction, not Horizon, causes a difference | Both arms get the same deadline, persistence, and verification text; only the invocation sentence differs |
| Inert treatment | Fidelity fields and decision condition 3 |
| Binary rewards at n=10 have low power | Stage 1 makes no significance claim; the stage 2 sample is set by the amendment, and stage 1 data is excluded from its test |
| Wall time | About 20–40 h for stage 1, one trial at a time; runs proceed in the background and resume from the manifest |

## Design review checklist

- The arms differ only in the Horizon sentence and the bundle; every setting in the table is identical and recorded.
- Task selection and arm order come from a seeded procedure committed before any measured trial, using no measured outcomes.
- Every measured task's image passed the oracle, nop, and bundle-plus-ledger checks, and every trial ran on that image.
- The bundle matches the exact tree, has no symlinks, comes from `2f65f2a`, and passed both smoke tests.
- Everything the scorer, judge, and scan depend on is in the freeze commit.
- Invalid, timed-out, flagged, excluded, and rerun trials are reported, never dropped silently.
- The report states leaderboard non-comparability and the judge's arm-visibility limitation.

## Revisions after review (2026-10-07)

A three-lens review found these defects in the first draft; this version corrects them:

- the Horizon CLI's default 20-turn budget, a hidden stopping rule;
- an asymmetric deadline that let only Horizon stop early;
- undefined final messages and usage for timed-out trials;
- tokens that would omit the reviewer subagent;
- Horizon ledger and bundle files that some verifiers would grade;
- per-trial image rebuilds that bypass the oracle check;
- an ambiguous bundle tree that Harbor would reject or ship without its CLI;
- a validity rule triggered by retried errors and blind to verifier failures;
- no treatment-fidelity check;
- an `honest_stop` with no data source;
- an unpinned judge;
- a discretionary task pick;
- concurrency confounds;
- missing freeze contents and setup-trial placement;
- imprecise filter predicates and an overstated candidate count;
- an implicit effort level;
- a smoke test above the minimum Python version.

The approved design choices are unchanged: Terminal-Bench 4.0 built natively, subscription Claude Code on Sonnet 5.5, a matched control instruction, a staged pilot, and scoring from disk. Three settings changed in service of them:

- both arms now share a 105-minute soft deadline instead of 120;
- effort is pinned at `high`;
- trials run strictly one at a time.

## Plan-stage corrections (2026-10-07)

Reading Harbor 0.24.0's source and every Terminal-Bench v4.0.0 `task.toml` while planning changed several mechanisms. Where this section and the body above disagree, this section governs.

- **Arm delivery.** Both arms share one pinned task copy. The arm text is appended with Harbor's `--extra-instruction-path` (recorded in `lock.json`), so the arms differ only in that file and in `--skill`.
- **Deadline sentence.** Every v4.0.0 instruction ends with "You have 28800 seconds to complete this task." The pinned copy removes that one sentence in both arms, so the 105-minute paragraph is the only deadline; "Do not cheat by using online solutions or hints specific to this task." is kept.
- **Verifier image.** Every task uses a separate verifier built from `tests/Dockerfile`, and Harbor rebuilds it in every trial unless `[verifier.environment].docker_image` is set. The copy therefore pins a second image, `tb4-local/<task>-verifier:v4.0.0`. When `[verifier.environment]` is absent, the copy first fills it with the `[environment]` keys, as Terminal-Bench's own release script does. The image checks cover both images.
- **Image identity.** Harbor records no image ID. Each trial runs with `--ek keep_containers=true`. The runner then reads the agent and verifier containers' image IDs, copies the Horizon ledger out with `docker cp`, and removes the containers, networks, and volumes.
- **Ledger collection.** The ledger is collected with `docker cp`, not with Harbor `artifacts`. Job-level artifacts are re-uploaded into the verifier and would change what the Horizon arm's verifier sees.
- **Reference check 3.** Harbor's oracle cannot take `--skill`. The check therefore runs the oracle on a check-only copy whose `solve.sh` first plants a representative ledger at `<WORKDIR>/.claude/harness`, as the image user. This also proves that the agent user can write there. The bundle itself lands in `/logs/agent/sessions/skills`, which no verifier sees.
- **Ledger location.** The ledger location is the image's `WORKDIR`, not always `/app`.
- **Python and bundle checks.** These run inside each built image as the image user, with the network disabled.
- **Validity.** Harbor records only the first exception, so a verifier failure after an `AgentTimeoutError` is detected from a missing `verifier_result.rewards.reward`, not from `exception_info`.
- **Timeouts.** Harbor does not kill `claude` inside the container on timeout. `final_message` is therefore null for every timed-out trial, even if a late `result` line appears.
- **Usage.** `tokens` and `cost_usd` come from the stream-json `result` record's `modelUsage` and `total_cost_usd`. Harbor's `agent_result` and its LiteLLM cost fallback are never used.
- **Horizon end reasons.**
  - "Minutes exhausted" has no CLI signal; it is derived from the ledger (`used.started` + `max_minutes` against the agent's finish time).
  - "Turns exhausted" is `BudgetExhausted`.
  - Any `reopen` or `--execution state` call is flagged.
- **Usage pacing.** Before each trial the runner probes subscription usage with a one-line call to the trial model, Sonnet 5.5, so the probe sees the same windows. It waits for a window to reset when that window's utilization is above the limit `Pace` sets in `run.py` (five-hour and weekly). A failed or hung probe is retried, never read as "no limit". After a trial that hits a usage limit, the runner waits for that window's reset.
- **Judge.** `--bare` cannot be used for the judge because it ignores OAuth. The judge instead runs with an empty `CLAUDE_CONFIG_DIR` and `HOME`, `--tools ""`, `--strict-mcp-config`, and `--json-schema`.
- **Static filter.** The filter reads the deprecated `allow_internet = false`, since no task sets `network_mode`, and also excludes tasks that set `skills_dir`. With these predicates 43 tasks survive and 31 fall in the 2–8 h band. `react-lead-form` is expected to fail the Python check, and `risk-scorer-replay` to fail reference check 3 (its user cannot write `/app`).
- **More invalidity causes.** A trial is also invalid when:
  - its Claude Code version is not 2.1.293;
  - it has no `result` record, or an erroring one, without a timeout;
  - the Mac slept during it (`kern.sleeptime`/`kern.waketime` changed): Harbor's timer pauses during sleep but Horizon's wall clock does not;
  - the runner was interrupted;
  - it was skipped because its partner arm was already invalid.
- **Freeze rule.** Measured trials need `HEAD` to descend from `tb4-pilot-freeze` with the frozen paths unchanged (including `evidence/draw.json`, which holds the seed and algorithm), so evidence and deviation commits do not lock the run. Committing `results.jsonl` closes it.
- **Build cache.** It may be pruned after validation, because both images are pinned by ID and every trial checks that ID.
- **Bundle smoke tests.** These run in Task 13 in clean `python:3.10-slim` and `python:3.12-slim` containers (doctor, start, check, status), and again in every candidate task image.
- **Raw evidence digests.** These are committed as `evidence/raw-digests.json`, beside `results.jsonl`.
