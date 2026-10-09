# EvoCode-Bench first look: Horizon v2 against plain Claude Code

Status: sections 1-3 approved by the user in conversation on 2026-10-09; section 4
(write-up and hygiene) presented and written here for review.

## Purpose and user decisions

The goal is a **shareable write-up**. It answers one question with an honest
"first look": on EvoCode-Bench's multi-round coding tasks, does Claude Code with
Horizon v2 pass more rounds than plain Claude Code?

The user's choices:
- **Budget:** about one week of the Claude subscription for the main run, roughly
  60 agent-hours, plus a short pilot.
- **Arms:** Horizon v2 against plain Claude Code. Both start a fresh session for
  each round.
- **Intel-only images:** patch our task copies to ARM and validate them. Tasks are
  chosen by a published-score rule, not by whether they build.

TB4 earlier settled the model, the native-Mac setup, staging, and the
no-API-key constraint.

Out of scope for this study:
- the ledger-wiped ablation arm;
- the continuous-session control;
- the forced mid-round interruption (P11);
- other models;
- repeat attempts.

These are named in the report's limits.

## Background facts (context sweep, 2026-10-09)

- **Source.** EvoCode-Bench is at github.com/UniPat-AI/EvoCodeBench (main
  f8fcfaa, MIT). Tasks come from the Hugging Face dataset UnipatAI/EvoCodeBench at
  revision 9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6 (CC-BY-NC-4.0, no model
  training).
- **Size.** 26 tasks and 227 rounds, in Harbor's official multi-step format.
- **Time limits.** The agent gets 1,800 s per round, or 3,600 s on 4 tasks.
  Every task uses `multi_step_reward_strategy = "mean"`.
- **Leaderboard.** Scored as the mean over tasks of passed_rounds / total_rounds.
  The best result is Opus 4.8 at 59.1. No Sonnet or Claude Code result is
  published.
- **Images.** All 26 images are `FROM ubuntu:22.04`. 17 hard-code a linux-amd64
  Go toolchain (16 download it, 1 copies a bundled tarball), and 7 of those run
  it while building. d5_w5 also downloads an amd64 `yq`. The local Docker daemon
  is linux/arm64.
- **Harbor 0.24.0** (pinned; b53b813) runs each step as a fresh `claude --print`
  conversation in one persistent container:
  - It clears `CLAUDE_CONFIG_DIR` between steps.
  - Since #1961, it also clears `/tests` and `/logs/verifier`.
  - It does not clear `/tmp`, `~/.claude`, or project files such as
    `.claude/harness`.
  - A step's verifier timeout defaults to 600 s, even when the task sets more.
  - On an agent timeout, Harbor stops only the host-side exec client, so the
    in-container `claude` keeps running.
- **TB4 usage.** About 1.27% of the weekly limit per agent-hour (about 70
  agent-hours up to the runner's 0.9 ceiling), and about 9.8% of a five-hour
  window per agent-hour.

## Study design

### Arms

Both arms use the same settings:
- model `anthropic/claude-sonnet-5-5` at effort `high`;
- Claude Code 2.1.293 and Harbor 0.24.0;
- `CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000`, web search disabled;
- `--agent-timeout-multiplier 1.0`;
- the same patched images and task copies;
- a fresh session every round (Harbor's default; `--resume-trajectory` is never
  passed).

| Arm | Skill | Extra instruction appended to every round |
| --- | --- | --- |
| `control` | none | the shared paragraph |
| `horizon` | the Horizon v2 bundle (`--skill`) | the shared paragraph, plus the Horizon sentence |

**The shared paragraph.** M is the task's own per-round limit in minutes: 30, or
60 for 3,600-s tasks.

> You have M minutes for this request. Keep working until it is fully complete;
> do not stop early. Before finishing, verify your work against the request with
> your own tests or checks. If you cannot finish within M minutes, stop and state
> exactly what is complete and what is incomplete.

**The Horizon sentence.** This is decision D7; "the minutes that remain" relies
on C13.

> Use the horizon skill for this request with `--max-turns 200`, and give the goal
> the minutes that remain of your M when you create it.

The prompts never mention rounds, earlier requests, or the benchmark. The TB4
prompts stay frozen.

### Tasks

The rule is fixed here, before any run:

1. Start from the 26 tasks in the pinned dataset revision.
2. Drop the walkthrough pair `theme_d11_w2_*` (`sparse-iterative-pde-solver` and
   `sparse-iterative-solver-review`). The operator saw material from them while
   designing Horizon v2.
3. Keep the tasks where Claude Opus 4.8's published passed_rounds / total_rounds
   is strictly between 0 and 1, using the per-task aggregate table in the EvoCode
   repo at f8fcfaa. That gives 13 tasks and 130 rounds:
   d10_w11, d10_w4__not_1234, d10_w9, d11_w9, d12_w1, d1_w11, d1_w2, d1_w5,
   d5_w1, d5_w5, d5_w9, d8_w5, d9_w11.
4. Order the pool with seed `20261009`: a round-robin across the domain prefixes
   (d1, d5, d8, d9, d10, d11, d12), taking tasks within each domain in seeded
   random order.
5. Measure the first N tasks in that order. N is set by the dry-run rule below.
6. A task that fails validation is replaced by the next task in the order, and
   the swap is logged.
7. After the freeze, a task is replaced only for an infrastructure failure, never
   because of its results.

**The N rule.**
- The timing dry run gives the mean agent-minutes per round for each arm,
  m_control and m_horizon.
- N is the largest value in {6, 8, 10} for which
  N × 10 rounds × (m_control + m_horizon) / 60 ≤ 60 agent-hours.
- If even N = 6 does not fit, stop and report back to the user before the freeze.

### Running

- One attempt per arm per task.
- Each task's two arms run back to back, in a seeded random order per task.
- One Harbor multi-step job runs one (task, arm) through all of its rounds.
- **Before each job,** the runner probes subscription usage. It waits until the
  seven-day utilization leaves room for a worst-case job (rounds × M minutes)
  under the 0.9 ceiling.
- **If a job hits a usage limit, an API error, or a Docker failure,** the run is
  invalid and is rerun once. A second infrastructure failure drops the task
  (logged).

### Outcomes and pre-registered analysis

- **Primary.** The per-task fraction of rounds passed, using EvoCode's policy: a
  missing, aborted, or timed-out round counts as failed. Each task's difference
  is d = horizon − control. We report:
  - the mean d with a 95% interval from the exact sign-flip permutation
    distribution (2^N assignments);
  - the two-sided permutation p-value;
  - the count of tasks won, lost, and tied.
- **Secondary:**
  - Case score: the mean per-round share of hidden test cases passed, from the
    counts on the verifier's summary line.
  - False "done" rate: the share of failed rounds whose final message claims
    completion.
  - Under-claim rate: the share of passed rounds whose final message reports the
    work as incomplete.
  - Cost: agent minutes per round, tokens per round, and usage consumed.
  - The false-done and under-claim labels come from the TB4 blind judge, run per
    round, and do not depend on the arm.
- **Validity checks, reported:**
  - Horizon was started in at least 90% of Horizon rounds.
  - Control never invoked Horizon.
  - Each step has exactly one result record (flag any extra).
  - No agent read `/tests` or `/logs/verifier`.
  - The leftover-file audit: what survives a round.
- **Framing.** With N = 8 and an SD of the paired difference near 0.30, the
  smallest reliably detectable difference is about 0.33 of a task's rounds. The
  report calls itself a first look and shows every task's result.

## Runner architecture

New code lives in a new package, `bench/evo`.
- It imports the generic TB4 pieces: Keychain token, `agent_env`, usage probe and
  pacing, run lock, host-sleep check, `stream.py`, `judge.py`, `leakcheck.py`,
  and `test.sh`.
- It does not change their behaviour. TB4's frozen constants, prompts, and tests
  stay as they are.
- `bench/tb4/bundle.py` gains `reviewing` in `MODULES`, because v2's `cli.py`
  imports it.
- A test proves the TB4 bundle at `2f65f2a` still builds exactly as before.

**Task copies.**

Two derived copies are made per selected task under the run directory. The
downloaded originals are never edited.
- The **agent copy** has no `solution/` directories.
- The **validation copy** keeps the reference solutions for oracle runs.

Each copy has four things changed:

1. *ARM patch.* Every linux-amd64 Go or yq download, and d10_w4's bundled
   tarball, is replaced by the same version's linux-arm64 artifact. The patcher
   prints a diff and records the checksum of each artifact it downloads.
2. *Step hooks.*
   - A `steps/<round>/workdir/setup.sh` runs as root before each agent phase. It
     empties `/tmp`, `~/.claude` and `/root/.claude/projects/*/memory`, in both
     arms. It leaves the project, including `.claude/harness`, untouched.
   - A `[[steps.verifier.collect]]` runs after each agent phase and before
     grading, even after a timeout. It kills any remaining `claude` process and
     its children. It copies the Horizon ledger, a `/tmp` listing, and a
     project file list into `/logs/artifacts`.
3. *Timeouts.* Each step's verifier timeout is set to the task's
   `[verifier].timeout_sec`, which works around the 600-s default. The agent
   multiplier is 1.0.
4. *Extra instruction.* A per-task, per-arm prompt file holds the right M.

**Scoring** reads `result.json` `step_results[]` and `steps/<round>/{agent,verifier}`
and writes one row per (task, arm, round) with these fields:
- reward;
- case counts (pass/total from the summary line only);
- agent seconds;
- tokens;
- result-record count;
- Horizon calls;
- goal status per slug;
- end reason;
- judge label.

The task score and the analysis use these rows. Harbor's trial-level mean reward
is never used.

**Data** lives in `~/Documents/evocode-horizon-runs`:
- task copies, jobs, records, ledgers, and the bundle;
- `deviations.md`.

The repo holds only digests, the pre-registration, and the report.

## Pilot, validation and freeze (in order)

1. **Harbor behaviour check.** Use a two-round dummy task with no EvoCode content
   and a few minutes of usage. It must show:
   - the setup hook runs, empties `/tmp`, keeps the ledger, and is not readable
     by the agent afterwards (or, if it is, that this is the same in both arms);
   - the collect hook kills a stray `claude`;
   - round 2 cannot read round 1's `/tests` or `/logs/verifier`;
   - the verifier timeout override takes effect;
   - and records what an agent timeout does to grading and to the next round.
2. **Task validation** (Mac time only). For each task, in the seeded order, until
   N tasks pass:
   - the patched images build on arm64;
   - an oracle run passes every round, with reward 1 at each step;
   - a nop run fails each round;
   - a planted-ledger oracle run still passes every round. The planted ledger is
     a fake `.claude/harness/{goals,gates}` with test-like file names in the
     task's language.
3. **Write canary.** Under the exact agent configuration, an agent creates
   `.claude/harness/gates/x/request.md` with the Write tool. If that is refused,
   adopt the fallback (relocate the gates directory through `doctor`'s `gates`
   path) before the freeze.
4. **Timing dry run** on `sparse-iterative-solver-review` (5 rounds, excluded
   from results). Both arms run once, at about 3-4 agent-hours. It measures:
   - minutes and usage per round in each arm;
   - Horizon start rate;
   - goal start offset;
   - whether the procedure fits.

   It sets N by the rule above. It needs its own ARM validation if required.
5. **Freeze.**
   - Build the Horizon v2 bundle from eval/tb4-horizon@f73975a.
   - Commit a pre-registration file holding: this design, N, the drawn task
     list, prompts, configs, image IDs, and the bundle and prompt digests.
   - Tag the commit `evo-prereg`. A test pins it.
   - After the tag, every deviation goes to `deviations.md` with its reason.
6. **Main run,** then scoring, judging, and the report.

## Write-up and hygiene

- **The report** is `docs/evaluations/evocode-horizon/report.md`. It contains:
  - a per-task table for both arms;
  - the primary analysis, with its interval and p-value;
  - wins, losses, and ties;
  - the secondary outcomes;
  - cost;
  - validity checks;
  - deviations.
- **Its limits section covers:**
  - N tasks, one attempt each;
  - Sonnet only;
  - patched ARM images;
  - no mid-round interruption and no ablation;
  - the TB4 tasks informed the v2 design;
  - EvoCode material was seen during design (handled by excluding the
    walkthrough pair);
  - one operator ran the study.
- **Never published or committed:**
  - hidden test contents;
  - per-case results;
  - reference solutions;
  - round instructions beyond short quotes;
  - full transcripts.

  The scorer reads only reward files and the counts on the summary line. Reports
  quote final messages only as far as the judge needs.
- **Credit and license.** The report credits EvoCode-Bench and stays
  non-commercial (CC-BY-NC-4.0).

## Risks

- **Patched images may change behaviour.** Validation with oracle and nop on
  every round guards this.
- **Horizon's v2 procedure may not fit in 30 minutes.** The dry run measures
  this. It is a finding, not a defect to fix mid-study.
- **A usage limit could stop a multi-hour job.** The usage probe before each job,
  and rerunning invalid runs, guard this.
- **Persistent container state carries over.** The setup and collect hooks, and
  the leftover-file audit, cover it.
- **Eight tasks give low power.** Stated up front.
