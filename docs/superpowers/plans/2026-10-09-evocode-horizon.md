# EvoCode-Bench First Look Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and run the pre-registered EvoCode-Bench "first look" that compares Horizon v2 with plain Claude Code on N (6, 8 or 10) multi-round tasks, and produce a shareable report.

**Architecture:** A new package `bench/evo` reuses TB4's generic pieces by import:
- the Keychain token and agent environment;
- the usage probe and pacing;
- the run lock;
- the stream parser and the judge.

It adds the multi-step parts:
- dataset pinning;
- the seeded task order and balanced arm order;
- the ARM Dockerfile patch, including the bundled Go tarball swap;
- task copies with between-round hooks;
- image build and per-round oracle/nop/planted validation;
- a Harbor behaviour check;
- one Harbor multi-step job per (task, arm);
- per-round scoring;
- the permutation analysis;
- a pre-registration freeze with reserves.

TB4's behaviour, constants, prompts and tests stay unchanged.

**Tech Stack:**
- Python 3.10+ standard library;
- pytest, run with `sh bench/tb4/test.sh` (it wraps `uv run --with pytest`; bare `pytest` is not installed);
- Harbor 0.24.0 (`harbor` CLI, installed under uv tools);
- Docker Desktop (linux/arm64);
- Claude Code 2.1.293 inside task containers;
- `zstd` for the dataset archive.

**Spec:** `docs/superpowers/specs/2026-10-09-evocode-horizon-design.md`

**How the tasks were checked:** every task was drafted against a fixed interface skeleton and run test-first in a scratch copy of this worktree. All tasks were then combined in one clone and cross-checked; the suite there ends at `1123 passed, 7 skipped`. Every code block that creates a whole file is byte-identical to the file in that clone, and every count in an `Expected:` line comes from running the tasks in order (Task 1 alone, then Tasks 1-2, and so on). Replaying the 12 commits from a clean checkout of `3297c86` reproduces that tree exactly, and each commit step's `git show --stat` output is the replay's. Only the operator steps (Docker, Harbor jobs, subscription usage) have not been run.

## Global Constraints

- **Worktree.** Every command runs from the worktree root, `/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode`, on branch `eval/evocode-horizon`. Commands that start `cd "$WT"` first need `WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"` (quoted: the path has a trailing space in `fun `).
- **Dataset:** Hugging Face `UnipatAI/EvoCodeBench`, revision `9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6`, archive `archives/evocodebench_wotraj.tar.zst`. Verify its SHA-256 against the LFS oid that the HF API reports for that revision (`a13d0cb47574282372c59366217ac68d895ff49d1387c81098b058f739906184`).
- **EvoCode code reference:** `https://github.com/UniPat-AI/EvoCodeBench` at `f8fcfaa1c9ad1c5b0bbc433323b587e4ddea2f32`.
- **Versions and model:** Harbor `0.24.0`, Claude Code `2.1.293`, model `anthropic/claude-sonnet-5-5`, effort `high`, `CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000`, `--ak disable_web_search=true`.
- **Timeouts:** `--agent-timeout-multiplier 1.0` and `--agent-setup-timeout-multiplier 2.0`. Never pass `--resume-trajectory`.
- **Bundle:** Horizon bundle commit `f73975ad6db67b8d9ac0d822ed569a807e9eae59`. The bundle includes `harness/reviewing.py`.
- **Draw:**
  - Seed `20261009`.
  - The pool is 13 tasks; the walkthrough pair `theme_d11_w2_*` is excluded.
  - Domain round-robin order: `d1, d5, d8, d9, d10, d11, d12`.
  - Arm order is balanced in pairs: positions 2k and 2k+1 of the seeded order run the arms in opposite orders, with pair k's order drawn from `random.Random(f"{seed}:arms:pair{k}")`.
- **N rule:** `N` is chosen from `{10, 8, 6}`. The budget is 60 agent-hours, and the weekly utilization ceiling is 0.9.
- **Reserves:** the validated tasks after the Nth are pre-registered as reserves. After the freeze, a main task dropped for an infrastructure failure is replaced by the next unused reserve; with none left, a drop stays a drop.
- **Run data** lives in `~/Documents/evocode-horizon-runs`, never in the repo. The repo holds digests, the pre-registration and the report.
- **Contamination rule:**
  - Code and executors never print, log, commit or summarize EvoCode `tests/` or `solution/` contents, round instructions (beyond the first 200 characters for debugging the walkthrough task), or per-case results.
  - The scorer reads only `reward.txt`/`reward.json` and the pass/total counts on the CASE_SUMMARY line.
- **Fixed texts:**
  - Shared prompt: "You have {M} minutes for this request. Keep working until it is fully complete; do not stop early. Before finishing, verify your work against the request with your own tests or checks. If you cannot finish within {M} minutes, stop and state exactly what is complete and what is incomplete."
  - Horizon sentence: "Use the horizon skill for this request with `--max-turns 200`, and give the goal the minutes that remain of your {M} when you create it."
  - M is 30, or 60 when the task's agent timeout is 3,600 s.
- **TB4 stays unchanged.** The package `bench/tb4` must not change behaviour. The one permitted edit is an optional `modules` parameter on `bundle.build` (plus the `layout()` helper it uses) whose default keeps the TB4 tuple.
- **Never modify the extracted dataset.** All edits go to copies under `RUNS/copies/`.

## Review Focus

These five inputs are implied by the spec but are not exercised by any spec-level test. Each one now has a test in the task that owns the code:

1. **A task image with no python3 >= 3.10.** ubuntu:22.04 ships none, so the Horizon CLI could not run and the horizon arm would be zeroed silently. Validation records `python.ok` false and names the task in `python_missing`, and Task 12 Step 9 stops. Test: Task 6, `test_a_selected_task_whose_image_lacks_python_310_is_named_in_the_summary`.
2. **A transient network failure during `docker build`, or a validation record made by older copy code.** The first is retried once instead of swapping the task out for good. The second is validated again (its `code` digest differs) and refused at the freeze. Tests: Task 6, `test_a_transient_build_failure_is_retried_once` and `test_a_saved_record_made_by_other_copy_code_is_validated_again`.
3. **A `task.toml` `[environment].workdir` that overrides the image WORKDIR.** Harbor runs the agent, hooks and oracle there, so the planted ledger must go there too, or the planted-ledger check passes without testing anything. Test: Task 6, `test_planted_ledger_goes_to_the_task_toml_workdir_when_it_overrides_the_image`.
4. **A server or watcher the agent backgrounds, which reparents to PID 1.** It is not a claude descendant, so the collect hook does not kill it. It is listed in `survivors.txt`, counted per round, and reported. Tests: Task 5, `test_collect_lists_the_processes_that_survive_into_the_next_round`; Task 9, `test_a_round_row_carries_the_leftover_counts_of_its_own_round`; Task 10, `test_the_leftover_audit_counts_rounds_by_what_the_hook_found`.
5. **A task whose rounds have different agent timeouts.** One M per task would be wrong for some rounds, so the freeze refuses the task and the operator asks the user. Test: Task 11, `test_collect_refuses_a_task_whose_rounds_have_different_agent_timeouts`.

## File Structure

| File | Responsibility | Task |
| --- | --- | --- |
| `bench/evo/__init__.py` | package marker | 1 |
| `bench/evo/config.py` | frozen constants, paths, prompt texts, pool, tags, job names | 1 |
| `bench/tb4/bundle.py` | add `layout(modules)` and a `modules=MODULES` parameter to `build` (default unchanged) | 1 |
| `bench/evo/dataset.py` | fetch, verify and extract the pinned archive; list tasks from `task.toml` only | 2 |
| `bench/evo/order.py` | seeded domain round-robin order of the pool; balanced arm order; the N rule | 3 |
| `bench/evo/patch.py` | rewrite amd64 Go/yq downloads in a Dockerfile to arm64; swap a bundled Go tarball for go.dev's verified arm64 archive; refuse unknown amd64 patterns | 4 |
| `bench/evo/copies.py` | build agent and validation copies: patched Dockerfile and `arm-patch.json`, per-step setup and collect hooks, verifier timeouts, pinned image, no solutions in agent copies; prompt files | 5 |
| `bench/evo/steps.py` | read a multi-step trial: step names, per-step reward, exception, timing, agent and verifier dirs, case counts | 6 |
| `bench/evo/images.py` | build, inspect, tag and remove the arm64 image for a copy | 6 |
| `bench/evo/validate.py` | per-task validation: build, then oracle, nop and planted-ledger oracle on every round; python, user and case-count checks | 6 |
| `bench/evo/dummy/` | the dummy 2-round Harbor task used by `harborcheck` (no EvoCode content) | 7 |
| `bench/evo/harborcheck.py` | dummy-task behaviour checks (oracle, verifier timeout, Claude Code, timeout probe) and the `.claude` write canary | 7 |
| `bench/evo/run.py` | one Harbor multi-step job per (task, arm, attempt, phase) with pacing, lock, invalid detection, records, reserves and the image pin | 8 |
| `bench/evo/score.py` | per-round rows from a finished job, including audit flags and leftover counts | 9 |
| `bench/evo/analysis.py` | task scores, permutation test, secondary outcomes, usage, labels, report rendering | 10 |
| `bench/evo/prereg.py` | freeze: bundle, pre-registration file with reserves, tag; `require_freeze` | 11 |
| `tests/test_evo_*.py` | one test file per module | 1-11 |
| `docs/evaluations/evocode-horizon/results-schema.md` | the authoritative description of every run file | 11 |
| `docs/evaluations/evocode-horizon/prereg.{json,md}`, `report.md`, `deviations.md` | written by running Task 12 | 12 |

## Who does what

- **Tasks 1-11** are code, written test-first. They need no Docker, no Harbor job and no subscription usage, except two marked operator checks: Task 2 Step 6 reads the archive's metadata from the HF API, and Task 5 Step 6 runs both hooks in a throwaway ubuntu container.
- **Task 7 Steps 8-14** are operator reference only. Task 12 Steps 6-8 run them.
- **Task 12** is the runbook. An agent can run it end to end. The user is needed only:
  - to refresh the Claude token in their own terminal (`claude setup-token`, then `security add-generic-password ...`) when a step reports an authentication failure;
  - to answer when a step says **STOP**;
  - to decide whether to push the branch and tag, and whether to publish the report.

## Shared data formats

`docs/evaluations/evocode-horizon/results-schema.md` (Task 11 Step 6) is the authoritative description of the run record, round row, validation record and `prereg.json`. Each task's Interfaces block names the keys it produces or reads.

**Run directory layout (`config.RUNS` = `~/Documents/evocode-horizon-runs`):**
- `dataset/<revision>/archive.tar.zst`, `dataset/<revision>/PIN.json` (`{"revision","sha256","lfs_oid","url","fetched_at"}`) and `dataset/<revision>/tasks/<task>/` (the extracted original; read-only by convention)
- `order.json`: the seeded order and arm orders (Task 3)
- `copies/agent/<task>/`, `copies/validation/<task>/`, `copies/planted/<task>/` (Tasks 5-6); each copy holds `arm-patch.json`
- `prompts/<task>__<arm>.md` (Task 5)
- `validation/<task>.json` and `validation/summary.json` (Task 6)
- `checks/<job>/findings.json` (Task 7) and the validation Harbor jobs `checks/<kind>__<task>/` (Task 6)
- `bundle/horizon/` and `bundle-manifest.json` (Task 11; built in Task 12 Step 5)
- `jobs/<job_name>/`: Harbor job directories
- `records/<job_name>.json`: one per run attempt (Task 8)
- `ledgers/<job_name>/<i>/`: the final Horizon ledger copied from the stopped container (Task 8)
- `logs/<job_name>.log`, `logs/build__<task>.log`, `logs/<kind>__<task>.log`
- `rows.jsonl`: one row per (job, round) (Task 9)
- `timing.json` (Task 12 Step 12), `findings.md` (before the freeze), `deviations.md` (after it)
- `runner.lock`: held by `validate`, `harborcheck` and `run`

**Job name:** `{phase}__{task}__{arm}__a{attempt}`. Phase is one of `check`, `canary`, `dry`, `main`. Task names can contain `__`, so parse a job name by splitting the phase off the left and the arm and attempt off the right.

---

### Task 1: Config and bundle modules

**Deviation (additive only; no skeleton name or signature changes):**
- `config` also defines two extra names:
  - `ARCHIVE_LFS_OID`, the sha256 that the HF paths-info API reported for the archive at the pinned revision on 2026-10-09;
  - `PHASES = ("check", "canary", "dry", "main")`, which `job_name` validates.
- `bench/tb4/bundle.py` gains a public `layout(modules=MODULES) -> dict`, and `LAYOUT = layout(MODULES)`. That is how "`LAYOUT` computed from `modules`" is implemented. `build(repo, commit, out_root, modules=MODULES)` writes `layout(modules)`.
- `job_name` checks three things:
  - the phase is in `PHASES`;
  - the attempt is an `int` of at least 1;
  - the task and arm are non-empty and contain no `/`, and the arm contains no `__`.

  It does not restrict the arm to `ARMS`, so Task 7 may use its own label. Task names can contain `__` (`theme_d10_w4_ml_ai_mlops_migration_upgrade__not_1234`). To parse a job name, split the phase off the left and the arm and attempt off the right.

Verified facts this task relies on:
- `f73975a`'s `harness/cli.py` has `import reviewing`.
- The TB4 module set built at `f73975a` fails `doctor` with `ModuleNotFoundError: No module named 'reviewing'`.
- TB4's default build at `2f65f2a` must equal `docs/evaluations/tb4-horizon-pilot/evidence/bundle-manifest.json` byte for byte.

**Files:**
- Create: `bench/evo/__init__.py` (empty)
- Create: `bench/evo/config.py`
- Create: `tests/test_evo_config.py`
- Create: `tests/test_evo_bundle.py`
- Modify: `bench/tb4/bundle.py` (add `layout()` and the optional `modules` parameter of `build`; the defaults are unchanged)
- Test: `tests/test_evo_config.py`, `tests/test_evo_bundle.py`, and the unchanged `tests/test_tb4_bundle.py` and `tests/test_tb4_config.py`

**Interfaces:**
- Consumes:
  - `bench.tb4.config`: `HARBOR_VERSION`, `CLAUDE_CODE_VERSION`, `MODEL`, `EFFORT`, `MAX_OUTPUT_TOKENS`, `KEYCHAIN_SERVICE`, `JUDGE_MODEL`, `PROBE_MODEL`, `BUNDLE_COMMIT`, `FREEZE_TAG`.
  - `bench.tb4.bundle`: `MODULES`, and `build(repo, commit, out_root)`.
- Produces (`bench.evo.config`):
  - Paths: `REPO_ROOT: Path`, `RUNS: Path` (`~/Documents/evocode-horizon-runs`), `DOCS: Path`.
  - Dataset: `DATASET_REPO`, `DATASET_REVISION`, `ARCHIVE_PATH`, `ARCHIVE_LFS_OID`.
  - EvoCode code: `EVO_CODE_URL`, `EVO_CODE_COMMIT`.
  - Versions and model: `HARBOR_VERSION`, `CLAUDE_CODE_VERSION`, `MODEL`, `EFFORT`, `MAX_OUTPUT_TOKENS`.
  - Timeouts: `AGENT_TIMEOUT_MULTIPLIER = 1.0`, `AGENT_SETUP_TIMEOUT_MULTIPLIER = 2.0`.
  - Bundle: `BUNDLE_COMMIT`, `BUNDLE_MODULES` (TB4's 8 modules + `"reviewing"`).
  - Draw: `SEED`, `DOMAINS`, `POOL` (13 names, sorted), `EXCLUDED_PREFIX`, `WALKTHROUGH`.
  - Arms and phases: `ARMS`, `PHASES`.
  - N rule: `N_CHOICES`, `BUDGET_AGENT_HOURS`, `ROUNDS_PER_TASK_PLAN`, `WEEKLY_CEILING`, `FIVE_HOUR_CEILING`.
  - Reused TB4 names: `KEYCHAIN_SERVICE`, `JUDGE_MODEL`, `PROBE_MODEL`.
  - Freeze: `FREEZE_TAG = "evo-prereg"`.
  - Prompts: `SHARED_PROMPT`, `HORIZON_SENTENCE` (both contain `{M}`).
  - `prompt_text(arm: str, minutes: int) -> str`. It has no trailing newline. It raises `ValueError` on an unknown arm or a non-positive or non-`int` `minutes`.
  - `image_tag(task: str) -> str`, which returns `"evo-local/<task>:9fcae3e5539d"`.
  - `job_name(phase: str, task: str, arm: str, attempt: int) -> str`, which returns `"{phase}__{task}__{arm}__a{attempt}"` and raises `ValueError` as described above.
- Produces (`bench.tb4.bundle`):
  - `layout(modules=MODULES) -> dict` mapping bundle path to repository path;
  - `LAYOUT = layout(MODULES)`, unchanged in value;
  - `build(repo: Path, commit: str, out_root: Path, modules=MODULES) -> dict` mapping bundle path to sha256.

- [ ] **Step 1: Write the failing config test**

`tests/test_evo_config.py`:

```python
import re
from pathlib import Path

import pytest

from bench.evo import config
from bench.tb4 import bundle
from bench.tb4 import config as tb4

SPEC = config.REPO_ROOT / "docs/superpowers/specs/2026-10-09-evocode-horizon-design.md"
CONTROL_30 = (
    "You have 30 minutes for this request. Keep working until it is fully complete; do not stop early. "
    "Before finishing, verify your work against the request with your own tests or checks. "
    "If you cannot finish within 30 minutes, stop and state exactly what is complete and what is incomplete."
)
HORIZON_60 = (
    "You have 60 minutes for this request. Keep working until it is fully complete; do not stop early. "
    "Before finishing, verify your work against the request with your own tests or checks. "
    "If you cannot finish within 60 minutes, stop and state exactly what is complete and what is incomplete."
    "\n\n"
    "Use the horizon skill for this request with `--max-turns 200`, and give the goal "
    "the minutes that remain of your 60 when you create it."
)
# Docker's reference grammar: lowercase path components, then a tag.
DOCKER_REF = re.compile(r"^[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*(?:/[a-z0-9]+(?:(?:[._]|__|-+)[a-z0-9]+)*)*"
                        r":[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


def _flat(text: str) -> str:
    """Join the spec's wrapped lines and blockquotes into single spaces."""
    return re.sub(r"\s+", " ", re.sub(r"\n>\s?", "\n", text))


def test_paths():
    assert config.REPO_ROOT == Path(config.__file__).resolve().parents[2]
    assert (config.REPO_ROOT / "bench" / "tb4" / "test.sh").is_file()
    assert config.RUNS == Path.home() / "Documents" / "evocode-horizon-runs"
    assert config.DOCS == config.REPO_ROOT / "docs" / "evaluations" / "evocode-horizon"
    assert config.REPO_ROOT not in config.RUNS.parents


def test_dataset_and_code_pins():
    assert config.DATASET_REPO == "UnipatAI/EvoCodeBench"
    assert config.DATASET_REVISION == "9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6"
    assert config.ARCHIVE_PATH == "archives/evocodebench_wotraj.tar.zst"
    assert config.ARCHIVE_LFS_OID == "a13d0cb47574282372c59366217ac68d895ff49d1387c81098b058f739906184"
    assert config.EVO_CODE_URL == "https://github.com/UniPat-AI/EvoCodeBench"
    assert config.EVO_CODE_COMMIT == "f8fcfaa1c9ad1c5b0bbc433323b587e4ddea2f32"


def test_versions_model_and_timeouts_match_the_spec_and_tb4():
    assert config.HARBOR_VERSION == tb4.HARBOR_VERSION == "0.24.0"
    assert config.CLAUDE_CODE_VERSION == tb4.CLAUDE_CODE_VERSION == "2.1.293"
    assert config.MODEL == tb4.MODEL == "anthropic/claude-sonnet-5-5"
    assert config.EFFORT == tb4.EFFORT == "high"
    assert config.MAX_OUTPUT_TOKENS == tb4.MAX_OUTPUT_TOKENS == "128000"
    assert config.AGENT_TIMEOUT_MULTIPLIER == 1.0
    assert config.AGENT_SETUP_TIMEOUT_MULTIPLIER == 2.0


def test_bundle_pin_and_modules():
    assert config.BUNDLE_COMMIT == "f73975ad6db67b8d9ac0d822ed569a807e9eae59"
    assert config.BUNDLE_MODULES == bundle.MODULES + ("reviewing",)
    assert bundle.MODULES == ("filelock", "gate", "goal", "paths", "refine_log", "steps", "task_state",
                              "tree_hash")


def test_draw_constants():
    assert config.SEED == 20261009
    assert config.DOMAINS == ("d1", "d5", "d8", "d9", "d10", "d11", "d12")
    assert len(config.POOL) == 13 == len(set(config.POOL))
    assert config.POOL == tuple(sorted(config.POOL))
    assert config.EXCLUDED_PREFIX == "theme_d11_w2_"
    assert config.WALKTHROUGH == "theme_d11_w2_scientific_numerical_brownfield_modification__not_1234"
    assert config.WALKTHROUGH.startswith(config.EXCLUDED_PREFIX)
    assert not any(t.startswith(config.EXCLUDED_PREFIX) for t in config.POOL)
    domains = [re.match(r"theme_(d\d+)_w\d+_", t).group(1) for t in config.POOL]
    assert set(domains) == set(config.DOMAINS)


def test_pool_matches_the_spec_list():
    shorts = []
    for task in config.POOL:
        m = re.fullmatch(r"theme_(d\d+_w\d+)_[a-z0-9_]+?(__not_1234)?", task)
        assert m, task
        shorts.append(m.group(1) + (m.group(2) or ""))
    assert ", ".join(shorts) + "." in _flat(SPEC.read_text())


def test_arms_n_rule_reused_names_and_freeze():
    assert config.ARMS == ("control", "horizon")
    assert config.PHASES == ("check", "canary", "dry", "main")
    assert config.N_CHOICES == (10, 8, 6)
    assert config.BUDGET_AGENT_HOURS == 60.0
    assert config.ROUNDS_PER_TASK_PLAN == 10
    assert config.WEEKLY_CEILING == 0.9 and config.FIVE_HOUR_CEILING == 0.9
    assert config.KEYCHAIN_SERVICE == tb4.KEYCHAIN_SERVICE == "tb4-horizon-claude-token"
    assert config.JUDGE_MODEL == tb4.JUDGE_MODEL
    assert config.PROBE_MODEL == tb4.PROBE_MODEL
    assert config.FREEZE_TAG == "evo-prereg" != tb4.FREEZE_TAG


def test_prompt_text_for_both_arms_at_30_and_60():
    assert config.prompt_text("control", 30) == CONTROL_30
    assert config.prompt_text("horizon", 60) == HORIZON_60
    assert config.prompt_text("control", 60) == HORIZON_60.split("\n\n")[0]
    assert config.prompt_text("horizon", 30) == (
        CONTROL_30 + "\n\n" + config.HORIZON_SENTENCE.replace("{M}", "30"))
    for minutes in (30, 60):
        control = config.prompt_text("control", minutes)
        horizon = config.prompt_text("horizon", minutes)
        assert horizon.startswith(control + "\n\n")
        assert "{M}" not in horizon and "{" not in horizon


def test_prompt_text_rejects_unknown_arms_and_bad_minutes():
    with pytest.raises(ValueError):
        config.prompt_text("baseline", 30)
    for bad in (0, -5, 30.0, "30", True):
        with pytest.raises(ValueError):
            config.prompt_text("control", bad)


def test_spec_quotes_the_same_prompt_texts():
    spec = _flat(SPEC.read_text())
    assert config.SHARED_PROMPT.replace("{M}", "M") in spec
    assert config.HORIZON_SENTENCE.replace("{M}", "M") in spec


def test_prompts_never_mention_rounds_earlier_requests_or_the_benchmark():
    text = (config.SHARED_PROMPT + " " + config.HORIZON_SENTENCE).lower()
    for word in ("round", "earlier", "previous", "benchmark", "evocode", "step"):
        assert word not in text


def test_image_tag():
    assert config.image_tag("theme_d1_w2_code_build_brownfield_modification") == (
        "evo-local/theme_d1_w2_code_build_brownfield_modification:9fcae3e5539d")
    for task in (*config.POOL, config.WALKTHROUGH):
        assert DOCKER_REF.match(config.image_tag(task)), task


def test_job_name():
    assert config.job_name("main", config.POOL[1], "horizon", 1) == (
        "main__theme_d10_w4_ml_ai_mlops_migration_upgrade__not_1234__horizon__a1")
    assert config.job_name("dry", config.WALKTHROUGH, "control", 2) == (
        "dry__theme_d11_w2_scientific_numerical_brownfield_modification__not_1234__control__a2")
    for phase in config.PHASES:
        assert config.job_name(phase, "dummy", "control", 1).startswith(phase + "__")


def test_job_name_rejects_bad_parts():
    with pytest.raises(ValueError):
        config.job_name("pilot", "t", "control", 1)
    with pytest.raises(ValueError):
        config.job_name("main", "t", "control", 0)
    with pytest.raises(ValueError):
        config.job_name("main", "a/b", "control", 1)
    with pytest.raises(ValueError):
        config.job_name("main", "t", "con__trol", 1)
    with pytest.raises(ValueError):
        config.job_name("main", "t", "", 1)
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `sh bench/tb4/test.sh -q tests/test_evo_config.py`

Expected: FAIL with `ModuleNotFoundError: No module named 'bench.evo'` (`1 error during collection`).

- [ ] **Step 3: Implement the config module**

Create the package and its empty marker:

Run: `mkdir -p bench/evo && : > bench/evo/__init__.py && test ! -s bench/evo/__init__.py && echo empty marker`

Expected: `empty marker`.

`bench/evo/config.py`:

```python
"""Fixed settings for the EvoCode-Bench first look at Horizon v2. Change nothing here after the freeze.

Spec: docs/superpowers/specs/2026-10-09-evocode-horizon-design.md
"""
from pathlib import Path

from bench.tb4 import bundle as _tb4_bundle
from bench.tb4 import config as _tb4

REPO_ROOT = Path(__file__).resolve().parents[2]
RUNS = Path.home() / "Documents" / "evocode-horizon-runs"
DOCS = REPO_ROOT / "docs" / "evaluations" / "evocode-horizon"

DATASET_REPO = "UnipatAI/EvoCodeBench"
DATASET_REVISION = "9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6"
ARCHIVE_PATH = "archives/evocodebench_wotraj.tar.zst"
# The LFS oid (sha256) the HF paths-info API reported for ARCHIVE_PATH at DATASET_REVISION on 2026-10-09.
ARCHIVE_LFS_OID = "a13d0cb47574282372c59366217ac68d895ff49d1387c81098b058f739906184"
EVO_CODE_URL = "https://github.com/UniPat-AI/EvoCodeBench"
EVO_CODE_COMMIT = "f8fcfaa1c9ad1c5b0bbc433323b587e4ddea2f32"

HARBOR_VERSION = "0.24.0"
CLAUDE_CODE_VERSION = "2.1.293"
MODEL = "anthropic/claude-sonnet-5-5"
EFFORT = "high"
MAX_OUTPUT_TOKENS = "128000"
AGENT_TIMEOUT_MULTIPLIER = 1.0
AGENT_SETUP_TIMEOUT_MULTIPLIER = 2.0

BUNDLE_COMMIT = "f73975ad6db67b8d9ac0d822ed569a807e9eae59"
BUNDLE_MODULES = _tb4_bundle.MODULES + ("reviewing",)  # v2's cli.py imports reviewing

SEED = 20261009
DOMAINS = ("d1", "d5", "d8", "d9", "d10", "d11", "d12")
POOL = (
    "theme_d10_w11_ml_ai_mlops_automation_scripting",
    "theme_d10_w4_ml_ai_mlops_migration_upgrade__not_1234",
    "theme_d10_w9_ml_ai_mlops_reproducibility_verification",
    "theme_d11_w9_scientific_numerical_reproducibility_verification",
    "theme_d12_w1_automation_productivity_greenfield_implementation",
    "theme_d1_w11_code_build_automation_scripting",
    "theme_d1_w2_code_build_brownfield_modification",
    "theme_d1_w5_code_build_integration_e2e_wiring",
    "theme_d5_w1_data_engineering_greenfield_implementation",
    "theme_d5_w5_data_engineering_integration_e2e_wiring",
    "theme_d5_w9_data_engineering_reproducibility_verification",
    "theme_d8_w5_cloud_devops_integration_e2e_wiring",
    "theme_d9_w11_security_automation_scripting",
)
EXCLUDED_PREFIX = "theme_d11_w2_"
WALKTHROUGH = "theme_d11_w2_scientific_numerical_brownfield_modification__not_1234"

ARMS = ("control", "horizon")
PHASES = ("check", "canary", "dry", "main")

N_CHOICES = (10, 8, 6)
BUDGET_AGENT_HOURS = 60.0
ROUNDS_PER_TASK_PLAN = 10
WEEKLY_CEILING = 0.9
FIVE_HOUR_CEILING = 0.9

KEYCHAIN_SERVICE = _tb4.KEYCHAIN_SERVICE
JUDGE_MODEL = _tb4.JUDGE_MODEL
PROBE_MODEL = _tb4.PROBE_MODEL
FREEZE_TAG = "evo-prereg"

SHARED_PROMPT = (
    "You have {M} minutes for this request. Keep working until it is fully complete; do not stop early. "
    "Before finishing, verify your work against the request with your own tests or checks. "
    "If you cannot finish within {M} minutes, stop and state exactly what is complete and what is incomplete."
)
HORIZON_SENTENCE = (
    "Use the horizon skill for this request with `--max-turns 200`, and give the goal "
    "the minutes that remain of your {M} when you create it."
)


def prompt_text(arm: str, minutes: int) -> str:
    """The extra instruction for one arm: the shared paragraph, plus the Horizon sentence for horizon."""
    if arm not in ARMS:
        raise ValueError(f"unknown arm {arm!r}")
    if isinstance(minutes, bool) or not isinstance(minutes, int) or minutes <= 0:
        raise ValueError(f"minutes must be a positive int, not {minutes!r}")
    text = SHARED_PROMPT.replace("{M}", str(minutes))
    if arm == "horizon":
        text += "\n\n" + HORIZON_SENTENCE.replace("{M}", str(minutes))
    return text


def image_tag(task: str) -> str:
    return f"evo-local/{task}:{DATASET_REVISION[:12]}"


def job_name(phase: str, task: str, arm: str, attempt: int) -> str:
    """`{phase}__{task}__{arm}__a{attempt}`; task names may contain `__`, so arm and phase may not."""
    if phase not in PHASES:
        raise ValueError(f"unknown phase {phase!r}")
    if isinstance(attempt, bool) or not isinstance(attempt, int) or attempt < 1:
        raise ValueError(f"attempt must be an int >= 1, not {attempt!r}")
    if not task or not arm or "__" in arm or any("/" in part for part in (task, arm)):
        raise ValueError(f"bad task or arm: {task!r}, {arm!r}")
    return f"{phase}__{task}__{arm}__a{attempt}"
```

- [ ] **Step 4: Run the config tests to make sure they pass**

Run: `sh bench/tb4/test.sh -q tests/test_evo_config.py`

Expected: `14 passed in 0.02s`. The counts in this task's Expected lines are exact; the times vary.

- [ ] **Step 5: Write the failing bundle test**

`tests/test_evo_bundle.py`:

```python
import hashlib
import json
import os
import subprocess
import sys

import pytest

from bench.evo import config
from bench.tb4 import bundle
from bench.tb4 import config as tb4

TB4_FILES = [
    "horizon/SKILL.md",
    "horizon/references/commands.md",
    "horizon/scripts/filelock.py",
    "horizon/scripts/gate.py",
    "horizon/scripts/goal.py",
    "horizon/scripts/harness.py",
    "horizon/scripts/paths.py",
    "horizon/scripts/refine_log.py",
    "horizon/scripts/steps.py",
    "horizon/scripts/task_state.py",
    "horizon/scripts/tree_hash.py",
]
TB4_MANIFEST = config.REPO_ROOT / "docs/evaluations/tb4-horizon-pilot/evidence/bundle-manifest.json"


def _files(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def _cli(skill, project, home, *args):
    return subprocess.run(
        [sys.executable, str(skill / "scripts/harness.py"), "--runtime", "claude",
         "--project", str(project), *args],
        capture_output=True, text=True, timeout=60, env={**os.environ, "HOME": str(home)})


@pytest.fixture(scope="module")
def v2(tmp_path_factory):
    out = tmp_path_factory.mktemp("v2 bundle") / "root"
    return out, bundle.build(config.REPO_ROOT, config.BUNDLE_COMMIT, out, modules=config.BUNDLE_MODULES)


def test_bundle_commit_is_in_the_object_store():
    done = subprocess.run(["git", "-C", str(config.REPO_ROOT), "cat-file", "-t", config.BUNDLE_COMMIT],
                          capture_output=True, text=True)
    assert done.stdout.strip() == "commit", done.stderr


def test_layout_is_computed_from_modules():
    assert bundle.LAYOUT == bundle.layout(bundle.MODULES) == bundle.layout()
    extra = bundle.layout(("x",))
    assert extra["horizon/scripts/x.py"] == "harness/x.py"
    assert extra["horizon/scripts/harness.py"] == "harness/cli.py"
    assert len(extra) == 4


def test_v2_bundle_includes_reviewing(v2):
    out, manifest = v2
    expected = sorted(bundle.layout(config.BUNDLE_MODULES))
    assert _files(out) == expected == sorted(manifest)
    assert "horizon/scripts/reviewing.py" in expected
    assert len(expected) == len(TB4_FILES) + 1


def test_v2_bytes_come_from_the_bundle_commit(v2):
    out, manifest = v2
    for rel, src in bundle.layout(config.BUNDLE_MODULES).items():
        expected = subprocess.run(
            ["git", "-C", str(config.REPO_ROOT), "show", f"{config.BUNDLE_COMMIT}:{src}"],
            capture_output=True, check=True).stdout
        assert (out / rel).read_bytes() == expected
        assert manifest[rel] == hashlib.sha256(expected).hexdigest()


def test_v2_bundle_runs_outside_the_repository(v2, tmp_path):
    out, _ = v2
    skill = out / "horizon"
    project, home = tmp_path / "scratch project", tmp_path / "home"
    project.mkdir()
    home.mkdir()
    doctor = _cli(skill, project, home, "doctor")
    assert doctor.returncode == 0, doctor.stderr
    assert json.loads(doctor.stdout)["implementation"].startswith(str(out.resolve()))
    start = _cli(skill, project, home, "start", "smoke", "--gate", "true",
                 "--max-turns", "200", "--max-minutes", "30")
    assert start.returncode == 0, start.stderr
    slug = json.loads(start.stdout)["slug"]
    assert (project / ".claude/harness/goals" / f"{slug}.json").is_file()


def test_tb4_module_set_cannot_run_the_v2_cli(tmp_path):
    out = tmp_path / "root"
    bundle.build(config.REPO_ROOT, config.BUNDLE_COMMIT, out)
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir()
    home.mkdir()
    doctor = _cli(out / "horizon", project, home, "doctor")
    assert doctor.returncode != 0
    assert "reviewing" in doctor.stderr


def test_tb4_default_build_is_unchanged(tmp_path):
    out = tmp_path / "root"
    manifest = bundle.build(config.REPO_ROOT, tb4.BUNDLE_COMMIT, out)
    assert tb4.BUNDLE_COMMIT == "2f65f2a24d753b6875bd126fc989335baff82ed8"
    assert _files(out) == TB4_FILES == sorted(manifest)
    assert manifest == json.loads(TB4_MANIFEST.read_text())["files"]
```

- [ ] **Step 6: Run it to make sure it fails**

Run: `sh bench/tb4/test.sh -q tests/test_evo_bundle.py`

Expected: `1 failed, 3 passed, 3 errors`:
- `FAILED tests/test_evo_bundle.py::test_layout_is_computed_from_modules` with `AttributeError: module 'bench.tb4.bundle' has no attribute 'layout'`.
- `ERROR` for `test_v2_bundle_includes_reviewing`, `test_v2_bytes_come_from_the_bundle_commit` and `test_v2_bundle_runs_outside_the_repository`, each with `TypeError: build() got an unexpected keyword argument 'modules'`.

Three tests already pass on the old code, and that is expected:
- the commit is in the object store;
- the TB4 module set cannot run the v2 CLI;
- TB4's default build is unchanged.

- [ ] **Step 7: Implement the `modules` parameter in `bench/tb4/bundle.py`**

Keep `MODULES` exactly as it is. Replace the six-line module-level `LAYOUT = {...}` assignment, which sits directly below `MODULES`, with the block below. Put two blank lines between the `MODULES` line and `def layout`:

```python
def layout(modules=MODULES) -> dict:
    """{bundle path: repository path} for the skill, its CLI, its reference and `modules`."""
    return {
        "horizon/SKILL.md": ".claude/skills/horizon/SKILL.md",
        "horizon/scripts/harness.py": "harness/cli.py",
        "horizon/references/commands.md": "docs/harness-cli.md",
        **{f"horizon/scripts/{m}.py": f"harness/{m}.py" for m in modules},
    }


LAYOUT = layout(MODULES)
```

Replace the whole `build` function with:

```python
def build(repo: Path, commit: str, out_root: Path, modules=MODULES) -> dict:
    """Write layout(modules) from the Git object store; return {bundle path: sha256}."""
    if out_root.exists() and any(out_root.iterdir()):
        raise FileExistsError(f"{out_root} is not empty")
    manifest = {}
    for rel, src in sorted(layout(modules).items()):
        data = _git_show(repo, commit, src)
        dest = out_root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        manifest[rel] = hashlib.sha256(data).hexdigest()
    links = [p for p in out_root.rglob("*") if p.is_symlink()]
    if links:
        raise RuntimeError(f"bundle contains symlinks: {links}")
    return manifest
```

Nothing else in `bundle.py` changes. `main` still calls `build(config.REPO_ROOT, config.BUNDLE_COMMIT, args.out)` with the TB4 defaults.

- [ ] **Step 8: Run the bundle tests to make sure they pass**

Run: `sh bench/tb4/test.sh -q tests/test_evo_bundle.py`

Expected: `7 passed in 1.35s`.

- [ ] **Step 9: Confirm TB4's behaviour is unchanged**

Run: `sh bench/tb4/test.sh -q tests/test_tb4_bundle.py tests/test_tb4_config.py && git diff --stat -- bench/tb4`

Expected: `12 passed in 0.49s`, then exactly this diff stat:

```text
 bench/tb4/bundle.py | 25 ++++++++++++++++---------
 1 file changed, 16 insertions(+), 9 deletions(-)
```

If any other `bench/tb4` file appears, revert that file. The skeleton permits only this one edit.

- [ ] **Step 10: Run the whole suite**

Run: `sh bench/tb4/test.sh -q`

Expected: `819 passed in 45.88s` (798 at `3297c86` + 21; the time varies). If other work has landed first, expect the previous count plus this task's tests, with 0 failures.

- [ ] **Step 11: Commit**

```bash
git add bench/evo/__init__.py bench/evo/config.py bench/tb4/bundle.py tests/test_evo_config.py tests/test_evo_bundle.py
git commit -m "feat(evo): frozen first-look settings and a module list for the Horizon bundle"
```

Run: `git show --stat --format=%s HEAD | head -20`

Expected:

```text
feat(evo): frozen first-look settings and a module list for the Horizon bundle

 bench/evo/__init__.py    |   0
 bench/evo/config.py      | 102 +++++++++++++++++++++++++++++
 bench/tb4/bundle.py      |  25 +++++---
 tests/test_evo_bundle.py | 110 ++++++++++++++++++++++++++++++++
 tests/test_evo_config.py | 162 +++++++++++++++++++++++++++++++++++++++++++++++
 5 files changed, 390 insertions(+), 9 deletions(-)
```

---

### Task 2: Dataset

**Deviation (clarifications that other tasks rely on):**
- **`fetch` signature.** It is `fetch(revision, dest_root, *, download=download_file, oid=lfs_oid, now=time.time) -> Path`.
  - `oid` is a callable, `oid(revision) -> str`, not a string.
  - `download` is `download(url, dest_path) -> None`.
  - `now` makes `fetched_at` deterministic.
  - It returns `dest_root/<revision>/tasks`.
- **What lands in `tasks/`.** `fetch` puts only task directories (those that hold a `task.toml`) in `tasks/`. It lifts them out of any wrapper directory up to two levels deep, so the skeleton layout `dataset/<revision>/tasks/<task>/` holds whatever the archive's top level is (the archive's layout was not inspected, under the no-download rule). Other top-level files in the archive are dropped. The archive itself is never modified.
- **Pinned oid.** For `config.DATASET_REVISION`, `fetch` also requires the API's oid to equal `config.ARCHIVE_LFS_OID`.
- **`list_tasks` dicts.**
  - `dir` is a `str` (an absolute path, ready for JSON).
  - One extra key, `uniform_agent_timeout: bool`.
  - `agent_timeout_sec` is the largest effective per-step agent timeout. Harbor's `MultiStepTrial._step_agent_timeout_sec` rule gives each step's value: `[steps.agent] timeout_sec`, else `[agent] timeout_sec`.
  - `verifier_timeout_sec` is the task-level `[verifier] timeout_sec`, or `600.0` (Harbor's `VerifierConfig` default) when absent.
  - `reward_strategy` defaults to `"mean"`, Harbor's default for multi-step tasks.
- **`list_tasks` errors.** It raises `ValueError` for a task with no `[[steps]]`, and for a step with no agent timeout anywhere.
- **`minutes(task_info)`** reads only `task_info["agent_timeout_sec"]`.

**Files:**
- Create: `bench/evo/dataset.py`
- Create: `tests/test_evo_dataset.py`
- Test: `tests/test_evo_dataset.py`

**Interfaces:**
- Consumes (Task 1, `bench.evo.config`): `DATASET_REPO`, `DATASET_REVISION`, `ARCHIVE_PATH`, `ARCHIVE_LFS_OID`, `RUNS`, `POOL`, `WALKTHROUGH`.
- Produces (`bench.evo.dataset`):
  - `class DatasetMismatch(Exception)`.
  - `HARBOR_VERIFIER_DEFAULT_SEC = 600.0`.
  - `archive_url(revision: str) -> str`, which returns `https://huggingface.co/datasets/UnipatAI/EvoCodeBench/resolve/<revision>/archives/evocodebench_wotraj.tar.zst`.
  - `urlopen_json(url: str, data: bytes | None = None)`, which returns parsed JSON (a POST when `data` is given).
  - `download_file(url: str, dest: Path) -> None`.
  - `lfs_oid(revision: str, *, fetch=urlopen_json) -> str`. It POSTs `paths=archives/evocodebench_wotraj.tar.zst` (form-encoded) to `https://huggingface.co/api/datasets/UnipatAI/EvoCodeBench/paths-info/<revision>` and returns `[i]["lfs"]["oid"]`.
  - `sha256_file(path: Path) -> str`.
  - `extract(archive: Path, dest: Path, run=subprocess.run) -> None`, which runs `zstd -d -c | tar -x` with pipefail.
  - `tasks_parent(root: Path, depth: int = 3) -> Path`.
  - `fetch(revision, dest_root, *, download=download_file, oid=lfs_oid, now=time.time) -> Path`.
  - `read_task(task_dir: Path) -> dict`.
  - `list_tasks(tasks_root: Path) -> list[dict]`. Each dict has the keys `{"task","dir","rounds","step_names","agent_timeout_sec","verifier_timeout_sec","reward_strategy","uniform_agent_timeout"}`.
  - `minutes(task_info: dict) -> int`.
  - `summary(infos: list, revision: str) -> dict`.
  - `main(argv=None) -> int`, the CLI `python3 -m bench.evo.dataset fetch|list [--root DIR] [--revision REV] [--out FILE]`. The default root is `config.RUNS/"dataset"`.

- [ ] **Step 1: Write the failing test**

The test:
- builds a real `.tar.zst` in `tmp_path`, using `tarfile` plus the `zstd` CLI;
- injects the downloader and the oid;
- locks every synthetic `steps/` directory with `chmod 000` while `list_tasks` runs, so any read under `steps/` fails the test.

`tests/test_evo_dataset.py`:

```python
import hashlib
import json
import os
import subprocess
import tarfile
import tomllib
from pathlib import Path

import pytest

from bench.evo import config, dataset

REV = "rev-test"
# The shape the HF paths-info API returned for the pinned archive on 2026-10-09.
PATHS_INFO = [{
    "type": "file", "oid": "0c64f0cc960105014af45c94409188afd04ec75a", "size": 70121019,
    "lfs": {"oid": "a13d0cb47574282372c59366217ac68d895ff49d1387c81098b058f739906184",
            "size": 70121019, "pointerSize": 133},
    "xetHash": "bb9b08eb3070f43107a0717b9d3ce088c15a8ad456623230ff4f768fe3661381",
    "path": "archives/evocodebench_wotraj.tar.zst",
}]


def task_toml(rounds=3, agent=1800.0, verifier=1800.0, strategy="mean", step_agent=None):
    """A synthetic task.toml in EvoCode's documented format (README, schema_version 1.2)."""
    lines = ['schema_version = "1.2"']
    if strategy is not None:
        lines.append(f'multi_step_reward_strategy = "{strategy}"')
    lines += ["", "[metadata]", 'name = "synthetic"', "", "[metadata.requirement_chain]",
              f"num_steps = {rounds}"]
    for i in range(1, rounds + 1):
        lines += ["", "[[metadata.requirement_chain.steps]]", f'step = "round-{i}"',
                  'change_types = ["extension"]']
    if agent is not None:
        lines += ["", "[agent]", f"timeout_sec = {agent}"]
    if verifier is not None:
        lines += ["", "[verifier]", f"timeout_sec = {verifier}"]
    lines += ["", "[environment]", "build_timeout_sec = 600.0", "cpus = 1", "memory_mb = 4096"]
    for i in range(1, rounds + 1):
        lines += ["", "[[steps]]", f'name = "round-{i}"']
        if step_agent and i in step_agent:
            lines += ["", "[steps.agent]", f"timeout_sec = {step_agent[i]}"]
    return "\n".join(lines) + "\n"


def write_task(root: Path, name: str, toml: str) -> Path:
    task = root / name
    (task / "environment").mkdir(parents=True)
    (task / "environment" / "Dockerfile").write_text("FROM ubuntu:22.04\n")
    (task / "task.toml").write_text(toml)
    for step in tomllib.loads(toml).get("steps", []):
        step_dir = task / "steps" / step["name"]
        for sub in ("tests", "solution"):
            (step_dir / sub).mkdir(parents=True)
        (step_dir / "instruction.md").write_text("synthetic request\n")
        (step_dir / "tests" / "test.sh").write_text("#!/bin/bash\n")
        (step_dir / "solution" / "solve.sh").write_text("#!/bin/bash\n")
    return task


def make_archive(tmp_path: Path, tasks: dict, wrapper=None) -> bytes:
    src = tmp_path / "archive-src"
    base = src / wrapper if wrapper else src
    base.mkdir(parents=True)
    for name, toml in tasks.items():
        write_task(base, name, toml)
    (src / "README.md").write_text("synthetic\n")
    tar = tmp_path / "archive.tar"
    with tarfile.open(tar, "w") as out:
        for child in sorted(src.iterdir()):
            out.add(child, arcname=child.name)
    zst = tmp_path / "archive.tar.zst"
    subprocess.run(["zstd", "-q", "-f", str(tar), "-o", str(zst)], check=True)
    return zst.read_bytes()


class Downloader:
    def __init__(self, blob: bytes):
        self.blob, self.calls = blob, []

    def __call__(self, url, dest):
        self.calls.append(url)
        Path(dest).write_bytes(self.blob)


def boom(*args, **kwargs):
    raise AssertionError("must not be called")


@pytest.fixture
def blob(tmp_path):
    return make_archive(tmp_path, {"theme_d1_w2_x": task_toml(3), "theme_d5_w1_y": task_toml(2, agent=3600.0)})


def test_archive_url():
    assert dataset.archive_url(config.DATASET_REVISION) == (
        "https://huggingface.co/datasets/UnipatAI/EvoCodeBench/resolve/"
        "9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6/archives/evocodebench_wotraj.tar.zst")


def test_lfs_oid_posts_the_archive_path_to_paths_info():
    calls = []

    def fetch(url, data=None):
        calls.append((url, data))
        return PATHS_INFO

    assert dataset.lfs_oid(config.DATASET_REVISION, fetch=fetch) == config.ARCHIVE_LFS_OID
    assert calls == [("https://huggingface.co/api/datasets/UnipatAI/EvoCodeBench/paths-info/"
                      "9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6",
                      b"paths=archives%2Fevocodebench_wotraj.tar.zst")]


def test_lfs_oid_refuses_a_missing_or_non_lfs_entry():
    with pytest.raises(dataset.DatasetMismatch):
        dataset.lfs_oid(REV, fetch=lambda url, data=None: [])
    no_lfs = [{**PATHS_INFO[0], "lfs": None}]
    with pytest.raises(dataset.DatasetMismatch):
        dataset.lfs_oid(REV, fetch=lambda url, data=None: no_lfs)


def test_fetch_verifies_extracts_and_pins(tmp_path, blob):
    root = tmp_path / "dataset"
    digest = hashlib.sha256(blob).hexdigest()
    download = Downloader(blob)
    tasks = dataset.fetch(REV, root, download=download, oid=lambda rev: digest, now=lambda: 0)
    assert tasks == root / REV / "tasks"
    assert download.calls == [dataset.archive_url(REV)]
    assert sorted(p.name for p in tasks.iterdir()) == ["theme_d1_w2_x", "theme_d5_w1_y"]
    assert (tasks / "theme_d1_w2_x" / "task.toml").is_file()
    assert (root / REV / "archive.tar.zst").read_bytes() == blob
    pin = json.loads((root / REV / "PIN.json").read_text())
    assert pin == {"revision": REV, "sha256": digest, "lfs_oid": digest, "url": dataset.archive_url(REV),
                   "fetched_at": "1970-01-01T00:00:00Z"}
    assert sorted(p.name for p in (root / REV).iterdir()) == ["PIN.json", "archive.tar.zst", "tasks"]


def test_fetch_raises_on_a_sha256_mismatch_and_leaves_nothing_pinned(tmp_path, blob):
    root = tmp_path / "dataset"
    with pytest.raises(dataset.DatasetMismatch):
        dataset.fetch(REV, root, download=Downloader(blob), oid=lambda rev: "0" * 64)
    assert not (root / REV / "PIN.json").exists()
    assert not (root / REV / "tasks").exists()
    assert not (root / REV / "archive.tar.zst").exists()
    assert not (root / REV / "archive.tar.zst.part").exists()


def test_fetch_refuses_an_api_oid_that_differs_from_the_pinned_one(tmp_path):
    with pytest.raises(dataset.DatasetMismatch):
        dataset.fetch(config.DATASET_REVISION, tmp_path, download=boom, oid=lambda rev: "0" * 64)
    assert not (tmp_path / config.DATASET_REVISION / "archive.tar.zst").exists()


def test_fetch_is_idempotent_when_the_pin_matches(tmp_path, blob):
    root = tmp_path / "dataset"
    digest = hashlib.sha256(blob).hexdigest()
    first = dataset.fetch(REV, root, download=Downloader(blob), oid=lambda rev: digest, now=lambda: 0)
    pin_before = (root / REV / "PIN.json").read_text()
    again = dataset.fetch(REV, root, download=boom, oid=boom, now=lambda: 99)
    assert again == first
    assert (root / REV / "PIN.json").read_text() == pin_before


def test_fetch_refuses_a_tampered_archive_after_pinning(tmp_path, blob):
    root = tmp_path / "dataset"
    digest = hashlib.sha256(blob).hexdigest()
    dataset.fetch(REV, root, download=Downloader(blob), oid=lambda rev: digest)
    (root / REV / "archive.tar.zst").write_bytes(blob + b"x")
    with pytest.raises(dataset.DatasetMismatch):
        dataset.fetch(REV, root, download=boom, oid=boom)


def test_fetch_flattens_a_wrapper_directory(tmp_path):
    blob = make_archive(tmp_path, {"theme_d8_w5_z": task_toml(2)}, wrapper="EvoCodeBench")
    root = tmp_path / "dataset"
    tasks = dataset.fetch(REV, root, download=Downloader(blob), oid=lambda rev: hashlib.sha256(blob).hexdigest())
    assert [p.name for p in tasks.iterdir()] == ["theme_d8_w5_z"]
    assert not (root / REV / "tasks.partial").exists()


def test_fetch_reuses_a_verified_archive_and_replaces_an_unpinned_extraction(tmp_path, blob):
    base = tmp_path / "dataset" / REV
    (base / "tasks" / "half-extracted").mkdir(parents=True)
    (base / "tasks.partial" / "junk").mkdir(parents=True)
    (base / "archive.tar.zst").write_bytes(blob)
    tasks = dataset.fetch(REV, tmp_path / "dataset", download=boom, oid=lambda rev: hashlib.sha256(blob).hexdigest())
    assert sorted(p.name for p in tasks.iterdir()) == ["theme_d1_w2_x", "theme_d5_w1_y"]
    assert not (base / "tasks.partial").exists()
    assert (base / "PIN.json").is_file()


def test_fetch_refuses_an_archive_without_tasks(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "README.md").write_text("no tasks\n")
    tar, zst = tmp_path / "a.tar", tmp_path / "a.tar.zst"
    with tarfile.open(tar, "w") as out:
        out.add(src / "README.md", arcname="README.md")
    subprocess.run(["zstd", "-q", "-f", str(tar), "-o", str(zst)], check=True)
    blob = zst.read_bytes()
    with pytest.raises(dataset.DatasetMismatch):
        dataset.fetch(REV, tmp_path / "dataset", download=Downloader(blob),
                      oid=lambda rev: hashlib.sha256(blob).hexdigest())
    assert not (tmp_path / "dataset" / REV / "PIN.json").exists()


@pytest.fixture
def locked_steps():
    """chmod 000 every steps/ directory so any read under it raises PermissionError."""
    locked = []
    yield locked
    for path in locked:
        os.chmod(path, 0o755)


def test_list_tasks_parses_multi_step_tasks_without_touching_steps(tmp_path, locked_steps):
    root = tmp_path / "tasks"
    write_task(root, "theme_d1_w2_x", task_toml(3))
    write_task(root, "theme_d5_w1_y", task_toml(4, agent=1800.0, step_agent={2: 3600.0}, strategy=None,
                                                 verifier=None))
    (root / "notes.txt").write_text("not a task\n")
    (root / "empty-dir").mkdir()
    for steps in root.glob("*/steps"):
        os.chmod(steps, 0)
        locked_steps.append(steps)
    infos = dataset.list_tasks(root)
    assert infos == [
        {"task": "theme_d1_w2_x", "dir": str((root / "theme_d1_w2_x").resolve()), "rounds": 3,
         "step_names": ["round-1", "round-2", "round-3"], "agent_timeout_sec": 1800.0,
         "verifier_timeout_sec": 1800.0, "reward_strategy": "mean", "uniform_agent_timeout": True},
        {"task": "theme_d5_w1_y", "dir": str((root / "theme_d5_w1_y").resolve()), "rounds": 4,
         "step_names": ["round-1", "round-2", "round-3", "round-4"], "agent_timeout_sec": 3600.0,
         "verifier_timeout_sec": 600.0, "reward_strategy": "mean", "uniform_agent_timeout": False},
    ]
    json.dumps(infos)


def test_list_tasks_refuses_single_step_tasks_and_missing_agent_timeouts(tmp_path):
    single = tmp_path / "single"
    write_task(single, "t", task_toml(0))
    with pytest.raises(ValueError, match="multi-step"):
        dataset.list_tasks(single)
    untimed = tmp_path / "untimed"
    write_task(untimed, "t", task_toml(2, agent=None))
    with pytest.raises(ValueError, match="agent timeout"):
        dataset.list_tasks(untimed)


def test_minutes():
    assert dataset.minutes({"agent_timeout_sec": 1800.0}) == 30
    assert dataset.minutes({"agent_timeout_sec": 3599.0}) == 30
    assert dataset.minutes({"agent_timeout_sec": 3600.0}) == 60
    assert dataset.minutes({"agent_timeout_sec": 7200.0}) == 60


def test_cli_fetch_then_list(tmp_path, monkeypatch, capsys):
    names = {config.POOL[0]: task_toml(10), config.POOL[1]: task_toml(10, agent=3600.0),
             config.WALKTHROUGH: task_toml(5), "theme_d2_w1_other": task_toml(7, step_agent={1: 900.0})}
    blob = make_archive(tmp_path, names)
    monkeypatch.setattr(dataset, "download_file", Downloader(blob))
    monkeypatch.setattr(dataset, "lfs_oid", lambda rev: hashlib.sha256(blob).hexdigest())
    root = tmp_path / "dataset"
    assert dataset.main(["fetch", "--root", str(root), "--revision", REV]) == 0
    fetched = json.loads(capsys.readouterr().out)
    assert fetched["revision"] == REV and fetched["tasks"] == 4
    out = tmp_path / "tasks.json"
    assert dataset.main(["list", "--root", str(root), "--revision", REV, "--out", str(out)]) == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary == {
        "revision": REV, "tasks": 4, "rounds": 32, "reward_strategies": ["mean"],
        "pool_missing": list(config.POOL[2:]), "pool_rounds": 20,
        "pool": {config.POOL[0]: {"rounds": 10, "minutes": 30}, config.POOL[1]: {"rounds": 10, "minutes": 60}},
        "walkthrough": {"rounds": 5, "minutes": 30},
        "nonuniform_agent_timeouts": ["theme_d2_w1_other"],
    }
    assert [i["task"] for i in json.loads(out.read_text())] == sorted(names)


def test_cli_list_requires_a_finished_fetch(tmp_path, capsys):
    assert dataset.main(["list", "--root", str(tmp_path), "--revision", REV]) == 2
    assert "fetch" in capsys.readouterr().err
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `sh bench/tb4/test.sh -q tests/test_evo_dataset.py`

Expected: FAIL with `ImportError: cannot import name 'dataset' from 'bench.evo'` (`1 error during collection`).

- [ ] **Step 3: Implement**

`bench/evo/dataset.py`:

```python
"""Fetch, verify and extract the pinned EvoCode-Bench archive; list tasks from task.toml only.

Contamination rule: nothing in this module opens, lists or prints anything under a task's steps/.
"""
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import time
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

from bench.evo import config

HF = "https://huggingface.co"
USER_AGENT = "evocode-horizon-first-look"
HARBOR_VERIFIER_DEFAULT_SEC = 600.0  # harbor 0.24.0 models/task/config.py VerifierConfig.timeout_sec


class DatasetMismatch(Exception):
    """The archive, the API answer or an existing extraction is not the pinned dataset."""


def archive_url(revision: str) -> str:
    return f"{HF}/datasets/{config.DATASET_REPO}/resolve/{revision}/{config.ARCHIVE_PATH}"


def urlopen_json(url: str, data=None):
    request = urllib.request.Request(url, data=data, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def download_file(url: str, dest: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response, open(dest, "wb") as out:
        shutil.copyfileobj(response, out, 1 << 20)


def lfs_oid(revision: str, *, fetch=urlopen_json) -> str:
    """The archive's LFS oid (its sha256) as the HF paths-info API reports it at `revision`."""
    url = f"{HF}/api/datasets/{config.DATASET_REPO}/paths-info/{revision}"
    entries = fetch(url, urllib.parse.urlencode({"paths": config.ARCHIVE_PATH}).encode())
    for entry in entries:
        if entry.get("path") == config.ARCHIVE_PATH:
            oid = (entry.get("lfs") or {}).get("oid")
            if not oid:
                raise DatasetMismatch(f"{config.ARCHIVE_PATH} at {revision} is not an LFS file")
            return oid
    raise DatasetMismatch(f"{config.ARCHIVE_PATH} not found at {revision}")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def extract(archive: Path, dest: Path, run=subprocess.run) -> None:
    dest.mkdir(parents=True)
    run(["bash", "-o", "pipefail", "-c", 'zstd -d -q -c -- "$0" | tar -x -f - -C "$1"',
         str(archive), str(dest)], check=True)


def _task_children(directory: Path) -> list:
    return sorted(c for c in directory.iterdir() if c.is_dir() and (c / "task.toml").is_file())


def tasks_parent(root: Path, depth: int = 3) -> Path:
    """The one shallowest directory whose children hold task.toml; never enters a task directory."""
    level = [root]
    for _ in range(depth):
        hits = [d for d in level if _task_children(d)]
        if len(hits) > 1:
            raise DatasetMismatch(f"tasks found in several directories: {hits}")
        if hits:
            return hits[0]
        level = [c for d in level for c in sorted(d.iterdir())
                 if c.is_dir() and not (c / "task.toml").exists()]
    raise DatasetMismatch(f"no task directories under {root}")


def _check_expected(revision: str, expected: str) -> None:
    if revision == config.DATASET_REVISION and expected != config.ARCHIVE_LFS_OID:
        raise DatasetMismatch(f"LFS oid {expected} differs from the pinned {config.ARCHIVE_LFS_OID}")


def _check_pin(revision: str, base: Path) -> Path:
    pin = json.loads((base / "PIN.json").read_text())
    archive, tasks = base / "archive.tar.zst", base / "tasks"
    if (pin.get("revision") != revision or pin.get("sha256") != pin.get("lfs_oid")
            or not tasks.is_dir() or not archive.is_file() or sha256_file(archive) != pin["sha256"]):
        raise DatasetMismatch(f"{base} does not match its PIN.json; move it aside and fetch again")
    _check_expected(revision, pin["lfs_oid"])
    return tasks


def fetch(revision: str, dest_root: Path, *, download=download_file, oid=lfs_oid, now=time.time) -> Path:
    """Download, verify and extract the archive into dest_root/<revision>/tasks; return that directory.

    PIN.json is written last and marks a finished fetch. With a matching PIN.json nothing is fetched
    again; anything else left in the directory is from an interrupted fetch and is replaced.
    """
    base = Path(dest_root) / revision
    archive, tasks, staging = base / "archive.tar.zst", base / "tasks", base / "tasks.partial"
    if (base / "PIN.json").exists():
        return _check_pin(revision, base)
    expected = oid(revision)
    _check_expected(revision, expected)
    base.mkdir(parents=True, exist_ok=True)
    if not (archive.is_file() and sha256_file(archive) == expected):
        part = base / "archive.tar.zst.part"
        part.unlink(missing_ok=True)
        download(archive_url(revision), part)
        got = sha256_file(part)
        if got != expected:
            part.unlink()
            raise DatasetMismatch(f"downloaded sha256 {got} != LFS oid {expected}")
        part.replace(archive)
    for leftover in (staging, tasks):
        if leftover.exists():
            shutil.rmtree(leftover)
    extract(archive, staging)
    try:
        found = _task_children(tasks_parent(staging))
        tasks.mkdir()
        for task_dir in found:
            task_dir.rename(tasks / task_dir.name)
    finally:
        shutil.rmtree(staging)
    pin = {"revision": revision, "sha256": sha256_file(archive), "lfs_oid": expected,
           "url": archive_url(revision), "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now()))}
    (base / "PIN.json").write_text(json.dumps(pin, indent=2) + "\n")
    return tasks


def read_task(task_dir: Path) -> dict:
    """Facts about one task, read from its task.toml alone."""
    data = tomllib.loads((task_dir / "task.toml").read_text())
    steps = data.get("steps") or []
    if not steps:
        raise ValueError(f"{task_dir.name}: no [[steps]]; not a multi-step task")
    default_agent = data.get("agent", {}).get("timeout_sec")
    per_step = [step.get("agent", {}).get("timeout_sec", default_agent) for step in steps]
    if any(t is None for t in per_step):
        raise ValueError(f"{task_dir.name}: a step has no agent timeout")
    return {
        "task": task_dir.name,
        "dir": str(task_dir.resolve()),
        "rounds": len(steps),
        "step_names": [step["name"] for step in steps],
        "agent_timeout_sec": float(max(per_step)),
        "verifier_timeout_sec": float(data.get("verifier", {}).get("timeout_sec", HARBOR_VERIFIER_DEFAULT_SEC)),
        "reward_strategy": data.get("multi_step_reward_strategy", "mean"),
        "uniform_agent_timeout": len(set(per_step)) == 1,
    }


def list_tasks(tasks_root: Path) -> list:
    return [read_task(d) for d in _task_children(Path(tasks_root))]


def minutes(task_info: dict) -> int:
    """M for the prompts: 60 for a 3,600-s task, else 30."""
    return 60 if task_info["agent_timeout_sec"] >= 3600 else 30


def summary(infos: list, revision: str) -> dict:
    by_name = {i["task"]: i for i in infos}
    pool = {t: {"rounds": by_name[t]["rounds"], "minutes": minutes(by_name[t])}
            for t in config.POOL if t in by_name}
    walk = by_name.get(config.WALKTHROUGH)
    return {
        "revision": revision,
        "tasks": len(infos),
        "rounds": sum(i["rounds"] for i in infos),
        "reward_strategies": sorted({i["reward_strategy"] for i in infos}),
        "pool_missing": [t for t in config.POOL if t not in by_name],
        "pool_rounds": sum(p["rounds"] for p in pool.values()),
        "pool": pool,
        "walkthrough": {"rounds": walk["rounds"], "minutes": minutes(walk)} if walk else None,
        "nonuniform_agent_timeouts": [i["task"] for i in infos if not i["uniform_agent_timeout"]],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("fetch", "list"))
    parser.add_argument("--root", type=Path, default=config.RUNS / "dataset")
    parser.add_argument("--revision", default=config.DATASET_REVISION)
    parser.add_argument("--out", type=Path, help="list: also write every task's facts here as JSON")
    args = parser.parse_args(argv)
    base = args.root / args.revision
    if args.command == "fetch":
        tasks = fetch(args.revision, args.root, download=download_file, oid=lfs_oid)
        pin = json.loads((base / "PIN.json").read_text())
        print(json.dumps({**pin, "tasks_dir": str(tasks), "tasks": len(_task_children(tasks))}))
        return 0
    if not (base / "PIN.json").is_file():
        print(f"no PIN.json under {base}; run fetch first", file=sys.stderr)
        return 2
    infos = list_tasks(base / "tasks")
    if args.out:
        args.out.write_text(json.dumps(infos, indent=2) + "\n")
    print(json.dumps(summary(infos, args.revision), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `sh bench/tb4/test.sh -q tests/test_evo_dataset.py`

Expected: `16 passed in 0.21s`. The counts in this task's Expected lines are exact; the times vary. The tests need `zstd` on `PATH` (`/opt/homebrew/bin/zstd`, v1.5.7 on this Mac). If a test fails with `FileNotFoundError` naming `zstd`, run `brew install zstd` and rerun.

- [ ] **Step 5: Prove that the contamination test discriminates**

Temporarily make `read_task` list `steps/`, then restore the file:

Run: `sed -i.bak 's/"rounds": len(steps),/"rounds": len(list((task_dir \/ "steps").iterdir())),/' bench/evo/dataset.py && sh bench/tb4/test.sh -q tests/test_evo_dataset.py; mv bench/evo/dataset.py.bak bench/evo/dataset.py && sh bench/tb4/test.sh -q tests/test_evo_dataset.py`

Expected:
- First run: `1 failed, 15 passed`. The failure is `FAILED tests/test_evo_dataset.py::test_list_tasks_parses_multi_step_tasks_without_touching_steps`, with `PermissionError: [Errno 13] Permission denied` on a `steps` directory.
- Second run: `16 passed`.

If the first run passes, the `chmod 000` guard is not working (for example, because the tests run as root). Stop and fix the test before going on.

- [ ] **Step 6: Check the API pin live (operator step; network; reads metadata only and downloads nothing)**

Run: `python3 -c 'from bench.evo import config, dataset; print(dataset.lfs_oid(config.DATASET_REVISION))'`

Expected: `a13d0cb47574282372c59366217ac68d895ff49d1387c81098b058f739906184`.

If it differs:
- stop;
- do not fetch or change any pin;
- tell the user that the archive at the pinned revision no longer matches.

If it raises `urllib.error.URLError` or an HTTP 5xx, the network or HF is down: retry later. The full fetch and list belong to the Task 12 runbook.

- [ ] **Step 7: Run the whole suite**

Run: `sh bench/tb4/test.sh -q`

Expected: `835 passed in 45.88s` (819 after Task 1 + 16; the time varies). If other work has landed first, expect the previous count plus this task's tests, with 0 failures.

- [ ] **Step 8: Commit**

```bash
git add bench/evo/dataset.py tests/test_evo_dataset.py
git commit -m "feat(evo): fetch, verify and list the pinned EvoCode-Bench dataset"
```

Run: `git show --stat --format=%s HEAD | head -20`

Expected:

```text
feat(evo): fetch, verify and list the pinned EvoCode-Bench dataset

 bench/evo/dataset.py      | 216 +++++++++++++++++++++++++++++++++++
 tests/test_evo_dataset.py | 281 ++++++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 497 insertions(+)
```

---

### Task 3: Order and N rule

**Deviation (arm order):** The skeleton gives each task its own coin, `random.Random(f"{seed}:arms:{task}")`. With seed 20261009 that coin runs control first in 11 of the 13 pool tasks, including all of the first 6. Arm would then be tied to run position: the five-hour window state, which arm absorbs the risk of an invalid run, and the time of day. TB4 balanced the first arm, and the spec only asks for "a seeded random order per task". So `arm_order` pairs consecutive positions of the seeded order and gives the two tasks of a pair opposite orders (see Interfaces). Every even prefix of the order then runs each arm first equally often. The walkthrough task is outside the pool and keeps its own coin. `ALGORITHM` states both rules, so `order.json` records them.

The other additions to the skeleton are `ALGORITHM`, the `main` CLI, `write_order` refusing to replace a different order, and `n_rule` comparing `n * rounds * minutes` with `budget * 60` so that no division rounds.

**Files:**
- Create: `bench/evo/order.py`
- Create: `tests/test_evo_order.py`
- Test: `tests/test_evo_order.py`

**Interfaces:**
- Consumes (Task 1, `bench.evo.config`): `POOL`, `SEED`, `DOMAINS`, `ARMS`, `N_CHOICES`, `ROUNDS_PER_TASK_PLAN`, `BUDGET_AGENT_HOURS`, `RUNS`. The test also reads `WALKTHROUGH`.
- Produces (`bench.evo.order`):
  - `ALGORITHM: str`, the plain-text draw procedure stored in `order.json`: the domain round-robin, the paired arm order, and the walkthrough's own coin.
  - `domain(task: str) -> str`: `"d<k>"` from `theme_d<k>_...`. Raises `ValueError` for any other name.
  - `seeded_order(pool, seed: int, domains) -> list[str]`. Raises `ValueError` on a duplicate task or a task whose domain is not in `domains`.
  - `arm_order(task: str, seed: int) -> tuple[str, str]`: `("control", "horizon")` or `("horizon", "control")`.
    - A pool task at position p (from 0) of `seeded_order(config.POOL, seed, config.DOMAINS)` belongs to pair k = p // 2. `random.Random(f"{seed}:arms:pair{k}")` shuffles `["control", "horizon"]`; position 2k runs the arms in that order and position 2k+1 in the reverse order.
    - A task outside the pool (the walkthrough) uses `random.Random(f"{seed}:arms:{task}")`.
  - `n_rule(m_control: float, m_horizon: float, *, choices=config.N_CHOICES, rounds=config.ROUNDS_PER_TASK_PLAN, budget=config.BUDGET_AGENT_HOURS) -> int | None`. It returns the largest `n` with `n * rounds * (m_control + m_horizon) <= budget * 60`, which is the skeleton's `/60 <= budget` without the division rounding. Negative or NaN minutes raise `ValueError`.
  - `write_order(path, *, pool=config.POOL, seed=config.SEED, domains=config.DOMAINS) -> dict`. It writes `{"seed","algorithm","order","arm_orders"}` (with `arm_orders[task]` a two-item list) and returns the same dict. Rewriting identical content is a no-op. A different existing file raises `FileExistsError`, because the order is fixed once written.
  - `main(argv=None) -> int`: `python -m bench.evo.order [--out PATH]`, where `--out` defaults to `config.RUNS / "order.json"`. It prints the JSON.
- The draw for the registered pool and seed is pinned by the test:
  - order `d1_w5, d5_w1, d8_w5, d9_w11, d10_w4__not_1234, d11_w9, d12_w1, d1_w11, d5_w9, d10_w9, d1_w2, d5_w5, d10_w11`;
  - `arm_order` gives `horizon` first for `d5_w1`, `d8_w5`, `d10_w4__not_1234`, `d1_w11`, `d10_w9` and `d5_w5` (positions 2, 3, 5, 8, 10 and 12 from 1): 3 of the first 6, 4 of the first 8, 5 of the first 10 and 6 of the first 12;
  - for the walkthrough task it is `("horizon", "control")`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_evo_order.py`:

```python
import json
import math

import pytest

from bench.evo import config, order

EXPECTED_ORDER = [
    "theme_d1_w5_code_build_integration_e2e_wiring",
    "theme_d5_w1_data_engineering_greenfield_implementation",
    "theme_d8_w5_cloud_devops_integration_e2e_wiring",
    "theme_d9_w11_security_automation_scripting",
    "theme_d10_w4_ml_ai_mlops_migration_upgrade__not_1234",
    "theme_d11_w9_scientific_numerical_reproducibility_verification",
    "theme_d12_w1_automation_productivity_greenfield_implementation",
    "theme_d1_w11_code_build_automation_scripting",
    "theme_d5_w9_data_engineering_reproducibility_verification",
    "theme_d10_w9_ml_ai_mlops_reproducibility_verification",
    "theme_d1_w2_code_build_brownfield_modification",
    "theme_d5_w5_data_engineering_integration_e2e_wiring",
    "theme_d10_w11_ml_ai_mlops_automation_scripting",
]
# Pair k of the seeded order (positions 2k, 2k+1) runs opposite arm orders: horizon first in 3 of the first 6.
HORIZON_FIRST = {"theme_d5_w1_data_engineering_greenfield_implementation",
                 "theme_d8_w5_cloud_devops_integration_e2e_wiring",
                 "theme_d10_w4_ml_ai_mlops_migration_upgrade__not_1234",
                 "theme_d1_w11_code_build_automation_scripting",
                 "theme_d10_w9_ml_ai_mlops_reproducibility_verification",
                 "theme_d5_w5_data_engineering_integration_e2e_wiring"}


def test_domain_parses_the_theme_prefix():
    assert order.domain("theme_d1_w2_code_build_brownfield_modification") == "d1"
    assert order.domain("theme_d10_w4_ml_ai_mlops_migration_upgrade__not_1234") == "d10"
    assert order.domain(config.WALKTHROUGH) == "d11"
    for bad in ("d1_w2_code", "theme_dx_w2", "theme_d1"):
        with pytest.raises(ValueError):
            order.domain(bad)


def test_order_is_deterministic_and_ignores_input_order():
    first = order.seeded_order(config.POOL, config.SEED, config.DOMAINS)
    assert first == order.seeded_order(list(reversed(config.POOL)), config.SEED, config.DOMAINS)
    assert sorted(first) == sorted(config.POOL) and len(first) == 13
    assert order.seeded_order(config.POOL, config.SEED + 1, config.DOMAINS) != first


def test_order_for_the_registered_pool_and_seed_is_pinned():
    assert order.seeded_order(config.POOL, config.SEED, config.DOMAINS) == EXPECTED_ORDER


def test_order_alternates_domains_and_skips_exhausted_ones():
    got = [order.domain(t) for t in order.seeded_order(config.POOL, config.SEED, config.DOMAINS)]
    assert got == ["d1", "d5", "d8", "d9", "d10", "d11", "d12",
                   "d1", "d5", "d10",
                   "d1", "d5", "d10"]


def test_each_domains_internal_order_is_stable():
    full = order.seeded_order(config.POOL, config.SEED, config.DOMAINS)
    for d in config.DOMAINS:
        members = [t for t in config.POOL if order.domain(t) == d]
        alone = order.seeded_order(members, config.SEED, config.DOMAINS)
        assert [t for t in full if order.domain(t) == d] == alone
    fewer = [t for t in config.POOL if order.domain(t) != "d5"]
    shrunk = order.seeded_order(fewer, config.SEED, config.DOMAINS)
    assert [t for t in shrunk if order.domain(t) == "d1"] == [t for t in full if order.domain(t) == "d1"]


def test_order_refuses_unknown_domains_and_duplicates():
    with pytest.raises(ValueError, match="outside"):
        order.seeded_order(list(config.POOL) + ["theme_d2_w1_other"], config.SEED, config.DOMAINS)
    with pytest.raises(ValueError, match="duplicate"):
        order.seeded_order(list(config.POOL) + [config.POOL[0]], config.SEED, config.DOMAINS)


def test_arm_order_is_seeded_per_task():
    for task in config.POOL:
        arms = order.arm_order(task, config.SEED)
        assert sorted(arms) == ["control", "horizon"] and isinstance(arms, tuple)
        assert arms == order.arm_order(task, config.SEED)
        assert arms[0] == ("horizon" if task in HORIZON_FIRST else "control")


def test_arm_order_pairs_consecutive_positions_with_opposite_orders():
    ranked = order.seeded_order(config.POOL, config.SEED, config.DOMAINS)
    for k in range(len(ranked) // 2):
        first, second = ranked[2 * k], ranked[2 * k + 1]
        assert order.arm_order(second, config.SEED) == tuple(reversed(order.arm_order(first, config.SEED)))
    firsts = [order.arm_order(t, config.SEED)[0] for t in ranked]
    for prefix in (6, 8, 10, 12):
        assert firsts[:prefix].count("horizon") == prefix // 2


def test_arm_order_of_a_task_outside_the_pool_is_its_own_seeded_coin():
    assert order.arm_order(config.WALKTHROUGH, config.SEED) == ("horizon", "control")
    assert "{seed}:arms:{task}" in order.ALGORITHM and "{seed}:arms:pair{k}" in order.ALGORITHM


@pytest.mark.parametrize("m_control,m_horizon,expected", [
    (18.0, 18.0, 10),     # 10 * 10 * 36 / 60 == 60.0, an exact fit
    (18.0, 18.01, 8),     # just over for 10
    (22.5, 22.5, 8),      # 8 * 10 * 45 / 60 == 60.0, an exact fit
    (22.5, 22.51, 6),
    (30.0, 30.0, 6),      # 6 * 10 * 60 / 60 == 60.0, an exact fit
    (30.0, 30.01, None),  # even 6 does not fit: stop and report back
    (0.0, 0.0, 10),
])
def test_n_rule_boundaries(m_control, m_horizon, expected):
    assert order.n_rule(m_control, m_horizon) == expected


def test_n_rule_takes_its_parameters_as_keywords():
    assert order.n_rule(10.0, 20.0, choices=(4, 2), rounds=5, budget=10.0) == 4
    assert order.n_rule(10.0, 20.0, choices=(4, 2), rounds=5, budget=4.9) is None
    assert order.n_rule(10.0, 20.0, choices=(2, 4), rounds=5, budget=5.0) == 2


@pytest.mark.parametrize("bad", [-1.0, math.nan])
def test_n_rule_refuses_impossible_minutes(bad):
    with pytest.raises(ValueError):
        order.n_rule(bad, 20.0)
    with pytest.raises(ValueError):
        order.n_rule(20.0, bad)


def test_write_order_writes_the_registered_draw_once(tmp_path):
    path = tmp_path / "runs" / "order.json"
    payload = order.write_order(path)
    data = json.loads(path.read_text())
    assert data == payload
    assert set(data) == {"seed", "algorithm", "order", "arm_orders"}
    assert data["seed"] == config.SEED and data["algorithm"] == order.ALGORITHM
    assert data["order"] == EXPECTED_ORDER
    assert data["arm_orders"] == {t: list(order.arm_order(t, config.SEED)) for t in EXPECTED_ORDER}
    assert order.write_order(path) == payload
    with pytest.raises(FileExistsError):
        order.write_order(path, seed=config.SEED + 1)
    assert json.loads(path.read_text()) == payload


def test_main_writes_and_prints_the_order(tmp_path, capsys):
    path = tmp_path / "order.json"
    assert order.main(["--out", str(path)]) == 0
    assert json.loads(capsys.readouterr().out)["order"] == EXPECTED_ORDER
    assert json.loads(path.read_text())["order"] == EXPECTED_ORDER
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `sh bench/tb4/test.sh -q tests/test_evo_order.py`

Expected: FAIL with `ImportError: cannot import name 'order' from 'bench.evo'` (`1 error during collection`).

- [ ] **Step 3: Implement**

Create `bench/evo/order.py`:

```python
"""Seeded domain round-robin order of the task pool, per-task arm order, and the N rule."""
import argparse
import json
import random
import re
import sys
from pathlib import Path

from bench.evo import config

ALGORITHM = (
    "for each domain d in DOMAINS: members = sorted pool tasks whose name starts theme_<d>_; "
    "random.Random(f'{seed}:{d}').shuffle(members); then take round-robin over DOMAINS in that "
    "order, skipping exhausted domains, until every task is taken. Arm order: positions 2k and 2k+1 of that "
    "order (from 0) form pair k; arms = ['control', 'horizon']; random.Random(f'{seed}:arms:pair{k}').shuffle(arms); "
    "position 2k runs arms in that order and position 2k+1 in the reverse order. A task outside the pool "
    "(the walkthrough): random.Random(f'{seed}:arms:{task}').shuffle(arms)."
)
_DOMAIN = re.compile(r"^theme_(d\d+)_")


def domain(task: str) -> str:
    match = _DOMAIN.match(task)
    if not match:
        raise ValueError(f"not a theme_d<k>_ task name: {task!r}")
    return match.group(1)


def seeded_order(pool, seed: int, domains) -> list:
    names = list(pool)
    if len(set(names)) != len(names):
        raise ValueError("pool has duplicate tasks")
    outside = sorted(t for t in names if domain(t) not in domains)
    if outside:
        raise ValueError(f"tasks outside the listed domains: {outside}")
    queues = []
    for d in domains:
        members = sorted(t for t in names if domain(t) == d)
        random.Random(f"{seed}:{d}").shuffle(members)
        queues.append(members)
    order = []
    while len(order) < len(names):
        for queue in queues:
            if queue:
                order.append(queue.pop(0))
    return order


def arm_order(task: str, seed: int) -> tuple:
    """Consecutive pool tasks in the seeded order get opposite arm orders, so every even prefix of the order
    runs each arm first equally often; a task outside the pool gets its own seeded coin."""
    ranked = seeded_order(config.POOL, seed, config.DOMAINS)
    arms = list(config.ARMS)
    if task in ranked:
        position = ranked.index(task)
        random.Random(f"{seed}:arms:pair{position // 2}").shuffle(arms)
        if position % 2:
            arms.reverse()
    else:
        random.Random(f"{seed}:arms:{task}").shuffle(arms)
    return tuple(arms)


def n_rule(m_control: float, m_horizon: float, *, choices=config.N_CHOICES,
           rounds=config.ROUNDS_PER_TASK_PLAN, budget=config.BUDGET_AGENT_HOURS):
    if not (m_control >= 0 and m_horizon >= 0):
        raise ValueError(f"minutes per round must be non-negative numbers: {m_control!r}, {m_horizon!r}")
    for n in sorted(choices, reverse=True):
        if n * rounds * (m_control + m_horizon) <= budget * 60:
            return n
    return None


def write_order(path: Path, *, pool=config.POOL, seed=config.SEED, domains=config.DOMAINS) -> dict:
    tasks = seeded_order(pool, seed, domains)
    payload = {"seed": seed, "algorithm": ALGORITHM, "order": tasks,
               "arm_orders": {t: list(arm_order(t, seed)) for t in tasks}}
    text = json.dumps(payload, indent=2) + "\n"
    path = Path(path)
    if path.exists() and path.read_text() != text:
        raise FileExistsError(f"{path} holds a different order; it is fixed once written")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return payload


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=config.RUNS / "order.json")
    args = parser.parse_args(argv)
    print(json.dumps(write_order(args.out), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `sh bench/tb4/test.sh -q tests/test_evo_order.py`

Expected: `21 passed in 0.09s`. The counts in this task's Expected lines are exact; the times vary.
- If `test_order_for_the_registered_pool_and_seed_is_pinned`, `test_arm_order_is_seeded_per_task` or `test_arm_order_pairs_consecutive_positions_with_opposite_orders` fails while the other tests pass, Task 1's `POOL`, `SEED`, `DOMAINS` or `ARMS` differs from the skeleton's spelling.
- Fix `config.py` to match the skeleton. Do not edit the pinned lists.

- [ ] **Step 5: Smoke-test the CLI against a temporary path (never the real RUNS)**

Run: `tmp="$(mktemp -d)" && uv run --no-project python -m bench.evo.order --out "$tmp/order.json" | head -6 && uv run --no-project python -c 'import json, sys; d = json.load(open(sys.argv[1])); [print(i + 1, t, *d["arm_orders"][t]) for i, t in enumerate(d["order"])]' "$tmp/order.json"; rm -rf "$tmp"`

Expected: the first six lines of the JSON, then each position with its task and arm order:

```text
{
  "seed": 20261009,
  "algorithm": "for each domain d in DOMAINS: members = sorted pool tasks whose name starts theme_<d>_; random.Random(f'{seed}:{d}').shuffle(members); then take round-robin over DOMAINS in that order, skipping exhausted domains, until every task is taken. Arm order: positions 2k and 2k+1 of that order (from 0) form pair k; arms = ['control', 'horizon']; random.Random(f'{seed}:arms:pair{k}').shuffle(arms); position 2k runs arms in that order and position 2k+1 in the reverse order. A task outside the pool (the walkthrough): random.Random(f'{seed}:arms:{task}').shuffle(arms).",
  "order": [
    "theme_d1_w5_code_build_integration_e2e_wiring",
    "theme_d5_w1_data_engineering_greenfield_implementation",
1 theme_d1_w5_code_build_integration_e2e_wiring control horizon
2 theme_d5_w1_data_engineering_greenfield_implementation horizon control
3 theme_d8_w5_cloud_devops_integration_e2e_wiring horizon control
4 theme_d9_w11_security_automation_scripting control horizon
5 theme_d10_w4_ml_ai_mlops_migration_upgrade__not_1234 horizon control
6 theme_d11_w9_scientific_numerical_reproducibility_verification control horizon
7 theme_d12_w1_automation_productivity_greenfield_implementation control horizon
8 theme_d1_w11_code_build_automation_scripting horizon control
9 theme_d5_w9_data_engineering_reproducibility_verification control horizon
10 theme_d10_w9_ml_ai_mlops_reproducibility_verification horizon control
11 theme_d1_w2_code_build_brownfield_modification control horizon
12 theme_d5_w5_data_engineering_integration_e2e_wiring horizon control
13 theme_d10_w11_ml_ai_mlops_automation_scripting control horizon
```

The real `order.json` is written only by the runbook (Task 12, Step 4), and it must show these same 13 lines.

- [ ] **Step 6: Run the whole suite**

Run: `sh bench/tb4/test.sh -q`

Expected: `856 passed in 45.85s` (835 after Task 2 + 21; the time varies). If other work has landed first, expect the previous count plus this task's tests, with 0 failures.

- [ ] **Step 7: Commit**

```bash
git add bench/evo/order.py tests/test_evo_order.py
git commit -m "feat(evo): seeded domain round-robin order, balanced arm orders and the N rule"
```

Run: `git show --stat --format=%s HEAD | head -20`

Expected:

```text
feat(evo): seeded domain round-robin order, balanced arm orders and the N rule

 bench/evo/order.py      |  96 +++++++++++++++++++++++++++++++
 tests/test_evo_order.py | 146 ++++++++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 242 insertions(+)
```

---

### Task 4: ARM patch

**Deviation (a bundled Go tarball is replaced, not refused):** The skeleton says a `COPY` of a `*.linux-amd64.tar.gz` raises, because no pool task has one. The spec says otherwise. d10_w4 copies a bundled linux-amd64 Go tarball, and the copy must replace it with the same version's linux-arm64 artifact and record the checksum of each artifact it downloads. d10_w4 is position 5 in the seeded order, inside even N = 6, and tasks are not chosen by whether they build. So `patch.py` adds `replace_bundled_go`, which Task 5 runs on each copy's `environment/` before `arm_patch`:
- It swaps each `go<ver>.linux-amd64.tar.gz` that a `COPY` or `ADD` takes from the build context for go.dev's `go<ver>.linux-arm64.tar.gz`, checked against the sha256 that go.dev publishes.
- It rewrites that instruction's file names, and returns one artifact record per swap.
- It contacts go.dev only when such a file exists in the build context.

`arm_patch` is unchanged: it still raises "bundled" for an amd64 file that is not in the build context. Task 5 writes `arm_patch`'s changed lines and the artifact records to `arm-patch.json` at the top of each copy; that file is the spec's diff and checksum record. `main` shows only `arm_patch`'s changes and never contacts go.dev. Task 6's `validate.CODE_DIGEST` hashes `patch.py`, so any later edit to this file makes the saved validation records stale and they are validated again.

**Files:**
- Create: `bench/evo/patch.py`
- Create: `tests/test_evo_patch.py`
- Test: `tests/test_evo_patch.py`

**Interfaces:**
- Consumes: nothing from earlier tasks (standard library only).
- Produces (`bench.evo.patch`):
  - `class UnknownArchPattern(ValueError)`. Its message always starts with `"line <n>: "`, where `<n>` is the 1-based physical line, and ends with that line's stripped text.
  - `arm_patch(dockerfile: str) -> tuple[str, list[str]]`. It returns `(patched_text, changed)`, with one `changed` entry per rewritten physical line in the form `"<n>: <old stripped> -> <new stripped>"`. Line endings are preserved and the function is idempotent.
    - **Which lines it reads.** It works on logical instructions: `\` continuations, and full-line `#` comments skipped even inside a continuation. Rewrites happen only in `RUN`/`ADD`/`ENV`/`ARG` instructions.
    - **What it rewrites:**
      - `go<ver>.linux-amd64.tar.gz` to `go<ver>.linux-arm64.tar.gz`, where `<ver>` is a literal version (including rc/beta), `${VAR}` or `$VAR`;
      - `yq_linux_amd64` to `yq_linux_arm64`, for both the binary and the `.tar.gz` with the binary inside;
      - `GOARCH=amd64`, or the legacy `ENV GOARCH amd64`, to arm64.
    - **What it raises `UnknownArchPattern` for:**
      - a `COPY`, or an `ADD` of a non-URL source, that names an amd64 file (the message contains "bundled");
      - a 64-hex sha256 in an instruction where an artifact was rewritten (the message contains "sha256"), because an amd64 checksum would fail the arm64 download;
      - any `amd64`/`x86_64` token left outside comments after the rewrite, in any instruction, `FROM --platform` included.
  - `needs_patch(dockerfile: str) -> bool`: True when any non-comment line has an `amd64`/`x86_64` token. It never raises.
  - `GO_RELEASES_URL = "https://go.dev/dl/?mode=json&include=all"`.
  - `_fetch(url: str, dest=None)`: a GET that returns parsed JSON when `dest` is None, and otherwise writes the body to `dest`. It is private, but Task 5's test replaces `patch._fetch` with a fake so that no test touches the network.
  - `replace_bundled_go(env_dir: Path, dockerfile: str, *, releases=None, download=None) -> tuple[str, list[dict]]`. It returns `(dockerfile, artifacts)`.
    - `releases()` returns go.dev's release list; the default is `_fetch(GO_RELEASES_URL)`. `download(url, dest)` writes one file; the default is `_fetch`.
    - Only `COPY` and `ADD` tokens whose file name is `go<ver>.linux-amd64.tar.gz` (a literal version), that are not URLs, and for which `env_dir / token` is a file inside `env_dir` are swapped. An absolute path or a `..` path that leaves the build context (for example a COPY destination) is never touched, so a host file can never be deleted. Each swap downloads `https://go.dev/dl/go<ver>.linux-arm64.tar.gz` next to the amd64 file, deletes the amd64 file, and rewrites that physical line.
    - Each artifact record is `{"line", "file", "replaces", "url", "sha256"}`, with `file` relative to `env_dir`.
    - It raises `UnknownArchPattern` when go.dev publishes no such arm64 archive, or when the download's sha256 differs from the published one (the message contains "sha256"; the download is deleted and the amd64 file kept).
  - `main(argv=None) -> int`: `python -m bench.evo.patch DOCKERFILE`. It prints `arm_patch`'s changed lines, or `no change`, and returns 0. On `UnknownArchPattern` it prints the message to stderr and returns 2. It never writes the file.

- [ ] **Step 1: Write the failing test**

The last three tests cover `replace_bundled_go` with an injected release list and downloader, so they need no network.

Create `tests/test_evo_patch.py`:

```python
import pytest

from bench.evo import patch

CURL_GO = """FROM ubuntu:22.04
RUN apt-get update && apt-get install -y curl ca-certificates
RUN curl -fsSL https://go.dev/dl/go1.22.5.linux-amd64.tar.gz -o /tmp/go.tgz \\
    && tar -C /usr/local -xzf /tmp/go.tgz \\
    && rm /tmp/go.tgz
ENV PATH=/usr/local/go/bin:$PATH
"""

WGET_ARG_GO = """FROM ubuntu:22.04
ARG GO_VERSION=1.21.13
RUN apt-get update && apt-get install -y wget \\
 && wget -q https://dl.google.com/go/go${GO_VERSION}.linux-amd64.tar.gz \\
 && tar -C /usr/local -xzf go${GO_VERSION}.linux-amd64.tar.gz \\
 && rm go$GO_VERSION.linux-amd64.tar.gz
"""

YQ = """FROM ubuntu:22.04
RUN wget -qO /usr/local/bin/yq https://github.com/mikefarah/yq/releases/download/v4.44.3/yq_linux_amd64 \\
    && chmod +x /usr/local/bin/yq
RUN wget -qO- https://github.com/mikefarah/yq/releases/download/v4.44.3/yq_linux_amd64.tar.gz | tar xz \\
    && mv yq_linux_amd64 /usr/bin/yq
"""

DPKG_AWARE = """FROM ubuntu:22.04
RUN ARCH="$(dpkg --print-architecture)" \\
 && curl -fsSL "https://go.dev/dl/go1.22.5.linux-${ARCH}.tar.gz" | tar -C /usr/local -xz
"""

APT_GO = """FROM ubuntu:22.04
RUN apt-get update && apt-get install -y --no-install-recommends golang-go \\
    && rm -rf /var/lib/apt/lists/*
"""

GOARCH = """FROM ubuntu:22.04
ENV GOARCH=amd64
ENV GOARCH amd64
RUN CGO_ENABLED=0 GOARCH=amd64 go build -o /usr/local/bin/tool ./cmd/tool
"""

COMMENTED = """# syntax=docker/dockerfile:1
# Built for amd64 (x86_64) upstream.
FROM ubuntu:22.04
RUN apt-get update \\
# was: install the amd64 toolchain here
 && apt-get install -y make
"""


def test_curl_go_download_is_rewritten_to_arm64():
    text, changed = patch.arm_patch(CURL_GO)
    assert text == CURL_GO.replace("go1.22.5.linux-amd64.tar.gz", "go1.22.5.linux-arm64.tar.gz")
    assert changed == [
        "3: RUN curl -fsSL https://go.dev/dl/go1.22.5.linux-amd64.tar.gz -o /tmp/go.tgz \\"
        " -> RUN curl -fsSL https://go.dev/dl/go1.22.5.linux-arm64.tar.gz -o /tmp/go.tgz \\"
    ]


def test_wget_with_a_version_arg_is_rewritten_on_every_continuation_line():
    text, changed = patch.arm_patch(WGET_ARG_GO)
    assert text == WGET_ARG_GO.replace(".linux-amd64.tar.gz", ".linux-arm64.tar.gz")
    assert [c.split(":", 1)[0] for c in changed] == ["4", "5", "6"]
    assert "amd64" not in text


def test_yq_binary_and_tarball_are_rewritten():
    text, changed = patch.arm_patch(YQ)
    assert text == YQ.replace("yq_linux_amd64", "yq_linux_arm64")
    assert [c.split(":", 1)[0] for c in changed] == ["2", "4", "5"]


def test_goarch_settings_are_rewritten():
    text, changed = patch.arm_patch(GOARCH)
    assert text == ("FROM ubuntu:22.04\nENV GOARCH=arm64\nENV GOARCH arm64\n"
                    "RUN CGO_ENABLED=0 GOARCH=arm64 go build -o /usr/local/bin/tool ./cmd/tool\n")
    assert len(changed) == 3


@pytest.mark.parametrize("dockerfile", [DPKG_AWARE, APT_GO, COMMENTED])
def test_arch_aware_apt_and_commented_files_are_unchanged(dockerfile):
    assert patch.arm_patch(dockerfile) == (dockerfile, [])
    assert patch.needs_patch(dockerfile) is False


@pytest.mark.parametrize("dockerfile", [CURL_GO, WGET_ARG_GO, YQ, GOARCH])
def test_patching_is_idempotent(dockerfile):
    assert patch.needs_patch(dockerfile) is True
    once, changed = patch.arm_patch(dockerfile)
    assert changed
    assert patch.arm_patch(once) == (once, [])
    assert patch.needs_patch(once) is False


def test_line_endings_are_preserved():
    crlf = CURL_GO.replace("\n", "\r\n")
    text, _ = patch.arm_patch(crlf)
    assert text == crlf.replace("linux-amd64", "linux-arm64")


@pytest.mark.parametrize("line,lineno", [
    ("RUN curl -fsSLO https://github.com/protocolbuffers/protobuf/releases/download/v25.1/protoc-25.1-linux-x86_64.zip", 2),
    ("RUN wget https://releases.hashicorp.com/terraform/1.9.0/terraform_1.9.0_linux_amd64.zip", 2),
    ("ARG ARCH=amd64", 2),
    ("RUN dpkg --add-architecture amd64", 2),
    ("RUN echo ok \\\n && curl -fsSL https://example.com/tool-linux-amd64 -o /usr/local/bin/tool", 3),
])
def test_an_unknown_amd64_pattern_raises_with_its_line(line, lineno):
    with pytest.raises(patch.UnknownArchPattern) as err:
        patch.arm_patch(f"FROM ubuntu:22.04\n{line}\n")
    assert str(err.value).startswith(f"line {lineno}: ")
    assert "amd64" in str(err.value) or "x86_64" in str(err.value)


def test_an_unknown_pattern_in_the_same_run_as_a_go_download_still_raises():
    dockerfile = CURL_GO.replace("    && rm /tmp/go.tgz", "    && curl -fsSLO https://example.com/helper_amd64.deb")
    with pytest.raises(patch.UnknownArchPattern, match="^line 5: "):
        patch.arm_patch(dockerfile)


def test_from_an_amd64_platform_raises():
    with pytest.raises(patch.UnknownArchPattern, match="^line 1: "):
        patch.arm_patch("FROM --platform=linux/amd64 ubuntu:22.04\n")


@pytest.mark.parametrize("instruction", [
    "COPY go1.21.0.linux-amd64.tar.gz /tmp/",
    "COPY vendor/go1.21.0.linux-amd64.tar.gz /usr/local/",
    "ADD go1.21.0.linux-amd64.tar.gz /usr/local/",
])
def test_a_bundled_amd64_tarball_raises(instruction):
    with pytest.raises(patch.UnknownArchPattern, match="^line 2: .*bundled"):
        patch.arm_patch(f"FROM ubuntu:22.04\n{instruction}\n")


def test_add_of_a_go_url_is_rewritten():
    dockerfile = "FROM ubuntu:22.04\nADD https://go.dev/dl/go1.22.5.linux-amd64.tar.gz /tmp/go.tgz\n"
    text, changed = patch.arm_patch(dockerfile)
    assert text == dockerfile.replace("amd64", "arm64") and len(changed) == 1


def test_a_pinned_checksum_next_to_a_rewritten_artifact_raises():
    digest = "a" * 64
    dockerfile = ("FROM ubuntu:22.04\n"
                  "RUN curl -fsSLO https://go.dev/dl/go1.22.5.linux-amd64.tar.gz \\\n"
                  f' && echo "{digest}  go1.22.5.linux-amd64.tar.gz" | sha256sum -c - \\\n'
                  " && tar -C /usr/local -xzf go1.22.5.linux-amd64.tar.gz\n")
    with pytest.raises(patch.UnknownArchPattern, match="^line 3: .*sha256"):
        patch.arm_patch(dockerfile)


def test_a_checksum_in_an_untouched_instruction_is_fine():
    digest = "b" * 64
    dockerfile = CURL_GO + f'RUN echo "{digest}  other.tar.gz" > /tmp/sums\n'
    text, changed = patch.arm_patch(dockerfile)
    assert len(changed) == 1 and digest in text


def test_main_prints_the_changes_and_never_writes(tmp_path, capsys):
    path = tmp_path / "Dockerfile"
    path.write_text(CURL_GO)
    assert patch.main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "linux-arm64" in out and out.startswith("3: ")
    assert path.read_text() == CURL_GO
    path.write_text(APT_GO)
    assert patch.main([str(path)]) == 0
    assert capsys.readouterr().out == "no change\n"
    path.write_text("FROM ubuntu:22.04\nARG ARCH=amd64\n")
    assert patch.main([str(path)]) == 2
    assert capsys.readouterr().err.startswith("line 2: ")


def test_a_bundled_go_tarball_is_swapped_for_the_verified_arm64_archive(tmp_path):
    import hashlib

    env = tmp_path / "environment"
    env.mkdir()
    (env / "go1.21.0.linux-amd64.tar.gz").write_bytes(b"amd64 toolchain")
    arm64 = b"arm64 toolchain"
    digest = hashlib.sha256(arm64).hexdigest()
    releases = [{"version": "go1.21.0", "files": [
        {"filename": "go1.21.0.linux-arm64.tar.gz", "os": "linux", "arch": "arm64", "sha256": digest}]}]
    got = []

    def download(url, dest):
        got.append(url)
        dest.write_bytes(arm64)

    dockerfile = ("FROM ubuntu:22.04\nCOPY go1.21.0.linux-amd64.tar.gz /tmp/\n"
                  "RUN tar -C /usr/local -xzf /tmp/go1.21.0.linux-amd64.tar.gz\n")
    text, artifacts = patch.replace_bundled_go(env, dockerfile, releases=lambda: releases, download=download)
    assert got == ["https://go.dev/dl/go1.21.0.linux-arm64.tar.gz"]
    assert sorted(p.name for p in env.iterdir()) == ["go1.21.0.linux-arm64.tar.gz"]
    assert artifacts == [{"line": 2, "file": "go1.21.0.linux-arm64.tar.gz", "replaces": "go1.21.0.linux-amd64.tar.gz",
                          "url": got[0], "sha256": digest}]
    patched, changed = patch.arm_patch(text)
    assert "amd64" not in patched and [c.split(":", 1)[0] for c in changed] == ["3"]
    (env / "go1.21.0.linux-amd64.tar.gz").write_bytes(b"amd64 toolchain")
    wrong = [{"files": [{"filename": "go1.21.0.linux-arm64.tar.gz", "sha256": "0" * 64}]}]
    with pytest.raises(patch.UnknownArchPattern, match="^line 2: .*sha256"):
        patch.replace_bundled_go(env, dockerfile, releases=lambda: wrong, download=download)
    assert (env / "go1.21.0.linux-amd64.tar.gz").is_file()


def test_no_bundled_tarball_means_no_network(tmp_path):
    def boom(*args, **kwargs):
        raise AssertionError("must not fetch")

    (tmp_path / "environment").mkdir()
    for dockerfile in ("FROM ubuntu:22.04\nCOPY go1.21.0.linux-amd64.tar.gz /tmp/\n",
                       "FROM ubuntu:22.04\nRUN curl -fsSLO https://go.dev/dl/go1.22.5.linux-amd64.tar.gz\n"):
        assert patch.replace_bundled_go(tmp_path / "environment", dockerfile, releases=boom, download=boom) == (
            dockerfile, [])

def test_a_tarball_path_outside_the_build_context_is_never_touched(tmp_path):
    def boom(*args, **kwargs):
        raise AssertionError("must not fetch")

    env = tmp_path / "ctx" / "environment"
    env.mkdir(parents=True)
    host = tmp_path / "go1.21.0.linux-amd64.tar.gz"
    host.write_bytes(b"a host file")
    (tmp_path / "ctx" / "go1.21.0.linux-amd64.tar.gz").write_bytes(b"next to the context")
    for token in (str(host), "../go1.21.0.linux-amd64.tar.gz"):
        dockerfile = f"FROM ubuntu:22.04\nCOPY {token} /tmp/\n"
        assert patch.replace_bundled_go(env, dockerfile, releases=boom, download=boom) == (dockerfile, [])
    assert host.read_bytes() == b"a host file"
    assert (tmp_path / "ctx" / "go1.21.0.linux-amd64.tar.gz").is_file()
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `sh bench/tb4/test.sh -q tests/test_evo_patch.py`

Expected: FAIL with `ImportError: cannot import name 'patch' from 'bench.evo'` (`1 error during collection`).

- [ ] **Step 3: Implement**

Create `bench/evo/patch.py`:

```python
"""Rewrite a Dockerfile's linux-amd64 Go and yq downloads to linux-arm64; refuse any other amd64 pattern."""
import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path

PATCHED = ("RUN", "ADD", "ENV", "ARG")
BUNDLING = ("COPY", "ADD")
RULES = (
    (re.compile(r"(?<![\w.-])(go(?:\d+(?:\.\d+)*(?:(?:rc|beta)\d+)?|\$\{[^}\s]+\}|\$\w+))"
                r"\.linux-amd64\.tar\.gz"), r"\1.linux-arm64.tar.gz"),
    (re.compile(r"(?<![A-Za-z0-9])yq_linux_amd64(?![A-Za-z0-9])"), "yq_linux_arm64"),
    (re.compile(r"(\bGOARCH(?:=|[ \t]+)[\"']?)amd64(?![A-Za-z0-9])"), r"\1arm64"),
)
ARCH_TOKEN = re.compile(r"(?<![A-Za-z0-9])(?:amd64|x86_64)(?![A-Za-z0-9])")
SHA256 = re.compile(r"(?<![0-9A-Fa-f])[0-9A-Fa-f]{64}(?![0-9A-Fa-f])")


class UnknownArchPattern(ValueError):
    pass


def _instructions(lines: list):
    """Yield (keyword, code line indexes) per logical instruction; comment and blank lines belong to none."""
    keyword, members, continuing = None, [], False
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if not continuing:
            if members:
                yield keyword, members
            keyword, members = stripped.split(None, 1)[0].upper(), []
        members.append(index)
        continuing = stripped.endswith("\\")
    if members:
        yield keyword, members


def _refuse_bundled(keyword: str, lines: list, members: list) -> None:
    for index in members:
        for token in lines[index].split():
            if ARCH_TOKEN.search(token) and "://" not in token:
                raise UnknownArchPattern(
                    f"line {index + 1}: {keyword} of a bundled amd64 file needs an arm64 file in the "
                    f"build context: {lines[index].strip()}")


def _rewrite(line: str) -> str:
    for pattern, replacement in RULES:
        line = pattern.sub(replacement, line)
    return line


def arm_patch(dockerfile: str) -> tuple:
    lines = dockerfile.splitlines(keepends=True)
    changed = []
    for keyword, members in _instructions(lines):
        if keyword in BUNDLING:
            _refuse_bundled(keyword, lines, members)
        rewritten = False
        if keyword in PATCHED:
            for index in members:
                old, new = lines[index], _rewrite(lines[index])
                if new != old:
                    lines[index], rewritten = new, True
                    changed.append(f"{index + 1}: {old.strip()} -> {new.strip()}")
        for index in members:
            if rewritten and SHA256.search(lines[index]):
                raise UnknownArchPattern(
                    f"line {index + 1}: a pinned sha256 sits in an instruction whose amd64 artifact was "
                    f"rewritten; the arm64 artifact needs its own checksum: {lines[index].strip()}")
            if ARCH_TOKEN.search(lines[index]):
                raise UnknownArchPattern(
                    f"line {index + 1}: amd64 pattern this patcher does not know: {lines[index].strip()}")
    return "".join(lines), changed


GO_RELEASES_URL = "https://go.dev/dl/?mode=json&include=all"
_GO_AMD64 = re.compile(r"go(?P<ver>\d+(?:\.\d+)*(?:(?:rc|beta)\d+)?)\.linux-amd64\.tar\.gz")


def _fetch(url: str, dest=None):
    """GET url: parsed JSON when dest is None, else the body written to dest."""
    request = urllib.request.Request(url, headers={"User-Agent": "evocode-horizon-first-look"})
    with urllib.request.urlopen(request, timeout=600) as response:
        if dest is None:
            return json.load(response)
        with open(dest, "wb") as out:
            shutil.copyfileobj(response, out, 1 << 20)


def replace_bundled_go(env_dir: Path, dockerfile: str, *, releases=None, download=None) -> tuple:
    """Swap each go<ver>.linux-amd64.tar.gz that a COPY or ADD takes from the build context for go.dev's
    linux-arm64 archive of the same version, checked against go.dev's published sha256, and rewrite that
    instruction. Returns (dockerfile, artifacts); go.dev is contacted only when such a file exists."""
    releases = releases or (lambda: _fetch(GO_RELEASES_URL))
    download = download or _fetch
    env_dir = Path(env_dir)
    lines = dockerfile.splitlines(keepends=True)
    artifacts, published = [], None
    for keyword, members in _instructions(lines):
        if keyword not in BUNDLING:
            continue
        for index in members:
            swapped = False
            for token in lines[index].split():
                match = _GO_AMD64.fullmatch(Path(token).name)
                source = env_dir / token
                # a COPY destination or a path outside the build context is never touched
                inside = source.resolve().is_relative_to(env_dir.resolve())
                if not match or "://" in token or not inside or not source.is_file():
                    continue
                name = f"go{match['ver']}.linux-arm64.tar.gz"
                if published is None:
                    published = {f.get("filename"): f.get("sha256") for r in releases() for f in r.get("files") or []}
                if not published.get(name):
                    raise UnknownArchPattern(f"line {index + 1}: go.dev publishes no {name}: {lines[index].strip()}")
                target = source.with_name(name)
                download(f"https://go.dev/dl/{name}", target)
                digest = hashlib.sha256(target.read_bytes()).hexdigest()
                if digest != published[name]:
                    target.unlink()
                    raise UnknownArchPattern(f"line {index + 1}: {name} has sha256 {digest}, go.dev publishes "
                                             f"{published[name]}: {lines[index].strip()}")
                source.unlink()
                swapped = True
                artifacts.append({"line": index + 1, "file": target.relative_to(env_dir).as_posix(),
                                  "replaces": token, "url": f"https://go.dev/dl/{name}", "sha256": digest})
            if swapped:
                lines[index] = _GO_AMD64.sub(lambda m: f"go{m['ver']}.linux-arm64.tar.gz", lines[index])
    return "".join(lines), artifacts


def needs_patch(dockerfile: str) -> bool:
    lines = dockerfile.splitlines()
    return any(ARCH_TOKEN.search(lines[i]) for _, members in _instructions(lines) for i in members)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Show the arm64 patch for a Dockerfile; never writes it.")
    parser.add_argument("dockerfile", type=Path)
    args = parser.parse_args(argv)
    try:
        _, changed = arm_patch(args.dockerfile.read_text())
    except UnknownArchPattern as err:
        print(err, file=sys.stderr)
        return 2
    print("\n".join(changed) if changed else "no change")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `sh bench/tb4/test.sh -q tests/test_evo_patch.py`

Expected: `29 passed`. The counts in this task's Expected lines are exact; the times vary.

- [ ] **Step 5: Smoke-test the CLI on a throwaway Dockerfile**

Run: `tmp="$(mktemp -d)" && printf 'FROM ubuntu:22.04\nRUN curl -fsSL https://go.dev/dl/go1.22.5.linux-amd64.tar.gz | tar -C /usr/local -xz\n' > "$tmp/Dockerfile" && uv run --no-project python -m bench.evo.patch "$tmp/Dockerfile"; echo "exit $?"; rm -rf "$tmp"`

Expected:

```text
2: RUN curl -fsSL https://go.dev/dl/go1.22.5.linux-amd64.tar.gz | tar -C /usr/local -xz -> RUN curl -fsSL https://go.dev/dl/go1.22.5.linux-arm64.tar.gz | tar -C /usr/local -xz
exit 0
```

- [ ] **Step 6: Run both new test files together**

Run: `sh bench/tb4/test.sh -q tests/test_evo_order.py tests/test_evo_patch.py`

Expected: `50 passed`.

- [ ] **Step 7: Run the whole suite**

Run: `sh bench/tb4/test.sh -q`

Expected: `885 passed` (856 after Task 3 + 29; the time varies). If other work has landed first, expect the previous count plus this task's tests, with 0 failures.

- [ ] **Step 8: Commit**

```bash
git add bench/evo/patch.py tests/test_evo_patch.py
git commit -m "feat(evo): patch amd64 Go and yq artifacts to arm64; refuse unknown amd64 patterns"
```

Run: `git show --stat --format=%s HEAD | head -20`

Expected:

```text
feat(evo): patch amd64 Go and yq artifacts to arm64; refuse unknown amd64 patterns

 bench/evo/patch.py      | 158 +++++++++++++++++++++++++++++++++
 tests/test_evo_patch.py | 231 ++++++++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 389 insertions(+)
```

### Task 5: Task copies, hooks and prompts

**Deviation:** All skeleton names are kept, but some signatures and behaviours differ from the skeleton:

- **`COLLECT_COMMAND` reads `/proc` instead of calling `pkill -KILL -f`.** It stops, then kills:
  - every process whose argv[0] basename is `claude`, or whose `/proc/<pid>/exe` ends in `/claude` or lies under `claude/versions/`;
  - all of their descendants (the spec requires "and its children").

  There are three reasons for this:
  - ubuntu:22.04 has no procps, so `pkill` is missing in oracle and nop runs;
  - `pkill -f` on the whole command line also matches Harbor's `bash -c '… | claude …'` wrapper and the hook's own shell;
  - `pkill` cannot reach descendants.
- **`COLLECT_COMMAND` also lists what survives the round.** After its snapshot it writes every other process still running to `survivors.txt`, one `<pid> <args>` line each, and does not kill them. The spec's leftover-file audit asks what survives a round. A server or watcher the agent started with `nohup` or `setsid` is reparented to PID 1, so it is not a `claude` descendant and only this list shows it. The list leaves out:
  - PID 1;
  - processes with an empty command line (kernel threads, zombies);
  - the processes the hook killed;
  - the hook's own shells. The hook's first line is `# evo-collect-hook (bench/evo/copies.py)`, and any command line containing `evo-collect-hook` is skipped.

  Task 9 counts these lines for the leftover audit.
- **The hook entry is `{command, timeout_sec = 60.0, user = "root"}`, not just `{command, timeout_sec = 60}`.** It is put first in each step's hook list.
- **`step_toml_overrides` has a keyword parameter, `docker_image: str | None = None`.**
  - It keeps a step's own explicit `[steps.verifier].timeout_sec`.
  - It refuses a task that already sets `[environment].docker_image`.
  - It refuses any separate verifier environment, because neither the ARM patch nor the pin would reach it.
- **The TOML writer is public:** `dump_toml(data) -> str`. `step_toml_overrides` uses it, and the tests call it directly.
- **`make_copy` swaps a bundled Go tarball and records the ARM patch.** The spec requires d10_w4's bundled tarball to be replaced by the same version's linux-arm64 artifact, and d10_w4 is position 5 in the pinned order. So before `patch.arm_patch`, `make_copy` calls Task 4's `patch.replace_bundled_go` on the copy's `environment/`:
  - It replaces each `go<ver>.linux-amd64.tar.gz` that a `COPY` or `ADD` takes from the build context with go.dev's linux-arm64 archive of the same version.
  - It checks the download against go.dev's published sha256.
  - It contacts go.dev only when such a file exists.

  `make_copy` then writes `arm-patch.json` at the top of the copy: `{"changed": [...], "artifacts": [...]}`. That holds the changed Dockerfile lines and each downloaded artifact with its sha256. Harbor 0.24.0 still accepts a task directory that contains it, and Step 5 checks this. Task 6 copies the record into each validation record as `arm_patch`.
- **`make_copy` accepts `image_tag=None`, which means no pin.** Every caller (Tasks 6, 7 and 8) passes a tag; only the tests pass `None`.
- **`make_copy` refuses a task whose step already ships `workdir/setup.sh`.** EvoCode's README layout has no step `workdir/`.
- **Every file the hook writes goes under `/logs/artifacts/<round>/`:**
  - `harness/`
  - `killed.txt`
  - `tmp-ls.txt`
  - `project-files.txt`
  - `survivors.txt`

  On the host they land at `<trial_dir>/steps/<round>/artifacts/logs/artifacts/<round>/`. Harbor bind-mounts /logs/artifacts at `<trial>/artifacts/logs/artifacts` and moves it into `steps/<round>/artifacts/` after each step. Task 7's `harborcheck.collected_root(trial_dir, round)` returns that base.

**Harbor 0.24.0 behaviour these hooks rely on.** I read each item in the installed source; it is quoted in the module docstring.
- `MultiStepTrial._prepare_step` runs under the step's agent user. It uploads `steps/<name>/workdir/.` into the container working directory (`pwd`), then runs `bash <workdir>/setup.sh`.
  - A non-zero exit sets the step's `exception_info`.
  - With no verifier result, `_should_stop_after_step` then aborts every later round. So `SETUP_SH` always exits 0.
- `_run_step` always runs `_collect_step_artifacts` after the agent phase and before `_run_step_verifier`, because `_run_step_agent` catches the timeout.
  - Collect hooks are the task-level `[[verifier.collect]]` plus the step's `[[steps.verifier.collect]]`.
  - They run in `main` through `bash -c`, with cwd equal to the container WORKDIR, or `[environment].workdir` if set.
  - They are best-effort: a failure is only logged.
- `_step_verifier_timeout_sec` uses `step.verifier.timeout_sec`.
  - That field defaults to `600.0` and is never `None`, so the task-level `[verifier].timeout_sec` is never consulted for a step.
  - This is why every step gets an explicit value.
- `VerifierCollectConfig` fields are:
  - `command`;
  - `service` (default `main`);
  - `timeout_sec` (default 60.0);
  - `user` (default `None`, which means the container default).
- `Trial._main_artifacts_mount_dir` is `paths.host_artifact_path("main", "/logs/artifacts")`, which is `<trial>/artifacts/logs/artifacts`. `MultiStepTrial._archive_step_outputs` moves `<trial>/artifacts/` into `steps/<name>/artifacts/` after each step.

**Files:**
- Create: `bench/evo/copies.py`
- Test: `tests/test_evo_copies.py`

**Interfaces:**

Consumes:
- From Task 1: `bench.evo.config.ARMS` and `bench.evo.config.prompt_text(arm: str, minutes: int) -> str`.
- From Task 4:
  - `bench.evo.patch.arm_patch(dockerfile: str) -> tuple[str, list[str]]`;
  - `bench.evo.patch.replace_bundled_go(env_dir: Path, dockerfile: str, *, releases=None, download=None) -> tuple`, which returns `(dockerfile, artifacts)`;
  - `bench.evo.patch.UnknownArchPattern`;
  - `bench.evo.patch._fetch(url, dest=None)`, which the test replaces with a fake so that no test touches the network.
- From TB4, unchanged: `bench.tb4.copies.tree_digest(root: Path) -> str`.

Produces (`bench/evo/copies.py`):
- `SETUP_SH: str`
  - Run as `bash <workdir>/setup.sh` from the workdir.
  - Empties the top-level entries of `${EVO_TMP_ROOT:-/tmp}`, except an entry that contains the workdir.
  - Removes `${HOME:-/root}/.claude/projects`.
  - Then runs `rm -f -- "$0"` and `exit 0`.
- `COLLECT_COMMAND(round_name: str) -> str`
  - Raises `ValueError` for a name that is not a single directory name.
  - Its first line is `# evo-collect-hook (bench/evo/copies.py)`.
  - Reads `${EVO_PROC_ROOT:-/proc}`, writes `${EVO_ARTIFACTS_ROOT:-/logs/artifacts}/<round>/`, and lists `${EVO_TMP_ROOT:-/tmp}`.
  - Writes `survivors.txt` with one `<pid> <args>` line (args cut to 200 characters) for each process it left running. Task 9's `score.leftovers` counts the lines.
  - Always exits 0.
- `dump_toml(data: dict) -> str`
  - Uses a fixed order.
  - Raises `ValueError` unless `tomllib.loads(result) == data`.
- `step_toml_overrides(task_toml_text: str, verifier_timeout_sec: float, *, docker_image: str | None = None) -> str`
- `make_copy(src_task_dir: Path, dest_dir: Path, *, kind: str, image_tag: str | None) -> str`
  - `kind` is `"agent"` or `"validation"`.
  - Calls `patch.replace_bundled_go`, then `patch.arm_patch`, and writes `ARM_PATCH_FILE` at the top of the copy: `{"changed": [...], "artifacts": [...]}` as JSON with indent 2. Task 6 reads it.
  - Returns `tree_digest(dest_dir)`.
  - Raises `FileExistsError`, `ValueError`, `FileNotFoundError` or `patch.UnknownArchPattern`. For a bundled tarball it can also raise an `OSError`, such as `urllib.error.URLError`, when go.dev cannot be reached.
  - On any error it removes the partial copy.
- `write_prompt(dest: Path, arm: str, minutes: int) -> Path`
  - Writes `config.prompt_text(arm, minutes) + "\n"` and creates parent directories.
- Constants:
  - `KINDS`
  - `HARBOR_VERIFIER_TIMEOUT_DEFAULT = 600.0`
  - `COLLECT_TIMEOUT_SEC = 60.0`
  - `COLLECT_USER = "root"`
  - `HARNESS_COPY = "harness"`
  - `KILLED_FILE = "killed.txt"`
  - `TMP_LISTING_FILE = "tmp-ls.txt"`
  - `PROJECT_FILES_FILE = "project-files.txt"`
  - `SURVIVORS_FILE = "survivors.txt"`
  - `ARM_PATCH_FILE = "arm-patch.json"`

Task 6's `validate.CODE_DIGEST` hashes this file together with `images.py`, `patch.py`, `steps.py` and `validate.py`. Any later edit here, even to a docstring, makes Task 6 validate every task again.

Contamination: `make_copy` copies `tests/` and `solution/` trees without opening them. It reads only `task.toml`, `environment/Dockerfile` and any go.dev archive it downloads itself, and it prints nothing.

- [ ] **Step 1: Write the failing test**

Create `tests/test_evo_copies.py`. The hook tests run the real `SETUP_SH` and `COLLECT_COMMAND` with `bash` in a `tmp_path` sandbox:
- `/tmp`, `/proc`, `/logs/artifacts` and `HOME` are redirected through env variables.
- The kill tests point a fake `/proc` at real `sleep` processes that the test owns, so nothing outside the test is ever signalled.
- The survivors test checks that a server reparented to PID 1 is listed in `survivors.txt`, and that the killed `claude` and the hook itself are not.
- The bundled-Go test replaces `patch._fetch` with a fake, so it needs no network.

```python
"""Task copies, per-round hooks and prompt files (bench/evo/copies.py). Every task here is synthetic."""
import datetime
import os
import signal
import subprocess
import tomllib

import pytest

from bench.evo import config, copies, patch
from bench.tb4.copies import tree_digest

ROUNDS = ("round-1", "round-2", "round-3")
TAG = "evo-local/theme_demo:9fcae3e5539d"

TASK_TOML = """schema_version = "1.2"
multi_step_reward_strategy = "mean"

[metadata]
name = "demo"
difficulty = "medium"
category = "code_build"
created = 2026-01-02

[metadata.requirement_chain]
num_steps = 3

[[metadata.requirement_chain.steps]]
step = "round-1"
change_types = ["extension"]

[[metadata.requirement_chain.steps]]
step = "round-2"
change_types = ["correction", "conflict"]

[[steps]]
name = "round-1"

[[steps]]
name = "round-2"

[steps.agent]
timeout_sec = 3600.0

[[steps]]
name = "round-3"

[steps.verifier]
timeout_sec = 300.0

[agent]
timeout_sec = 1800.0

[verifier]
timeout_sec = 1200.0

[verifier.env]
"MODE.x" = "strict"

[environment]
build_timeout_sec = 900.0
cpus = 2
memory_mb = 4096
storage_mb = 10240
"""

GO_DOCKERFILE = (
    "FROM ubuntu:22.04\n"
    "RUN curl -fsSL https://go.dev/dl/go1.22.5.linux-amd64.tar.gz | tar -C /usr/local -xz\n"
    "WORKDIR /app\n"
)


def make_task(root, toml=TASK_TOML, dockerfile=GO_DOCKERFILE):
    task = root / "theme_demo"
    (task / "environment").mkdir(parents=True)
    (task / "environment" / "Dockerfile").write_text(dockerfile)
    (task / "task.toml").write_text(toml)
    for name in ROUNDS:
        step = task / "steps" / name
        (step / "tests").mkdir(parents=True)
        (step / "solution").mkdir()
        (step / "instruction.md").write_text(f"synthetic request {name}\n")
        (step / "tests" / "test.sh").write_text("#!/bin/bash\necho 1 > /logs/verifier/reward.txt\n")
        (step / "solution" / "solve.sh").write_text("#!/bin/bash\ntrue\n")
    (task / "steps" / "round-2" / "workdir").mkdir()
    (task / "steps" / "round-2" / "workdir" / "data.csv").write_text("a,b\n")
    (task / "solution").mkdir()
    (task / "solution" / "README.md").write_text("top-level solution\n")
    return task


# ---- dump_toml -------------------------------------------------------------------------------

def test_dump_toml_round_trips_a_rich_document():
    data = {
        "schema_version": "1.2",
        "flag": True,
        "count": -3,
        "ratio": 0.5,
        "limit": float("inf"),
        "day": datetime.date(2026, 1, 2),
        "at": datetime.datetime(2026, 10, 9, 7, 30, tzinfo=datetime.timezone.utc),
        "text": 'quote " backslash \\ tab \t newline \n bell \x07 del \x7f unicode é ✓',
        "a.b c": "quoted key",
        "empty_list": [],
        "mixed": [1, "x", {"k": 1, "nested": {"deep": [1, 2]}}],
        "table": {"x": 1, "empty": {}, "sub": {"y": [1.5, 2.5]}},
        "steps": [
            {"name": "round-1", "verifier": {"timeout_sec": 600.0, "collect": [{"command": "a\nb", "timeout_sec": 60.0}]}},
            {"name": "round-2", "agent": {"timeout_sec": 3600.0}},
        ],
        "metadata": {"requirement_chain": {"num_steps": 2, "steps": [{"step": "round-1"}, {"step": "round-2"}]}},
    }
    text = copies.dump_toml(data)
    assert tomllib.loads(text) == data
    assert copies.dump_toml(data) == text


def test_dump_toml_refuses_values_toml_cannot_hold():
    with pytest.raises(ValueError):
        copies.dump_toml({"x": None})
    with pytest.raises(ValueError):
        copies.dump_toml({"x": {1, 2}})


# ---- step_toml_overrides ---------------------------------------------------------------------

def test_step_overrides_add_timeouts_and_collect_hooks_and_keep_every_other_key():
    text = copies.step_toml_overrides(TASK_TOML, 1200.0, docker_image=TAG)
    out = tomllib.loads(text)
    steps = out["steps"]
    assert [s["name"] for s in steps] == list(ROUNDS)
    assert [s["verifier"]["timeout_sec"] for s in steps] == [1200.0, 1200.0, 300.0]
    for step in steps:
        assert step["verifier"]["collect"] == [
            {"command": copies.COLLECT_COMMAND(step["name"]), "timeout_sec": 60.0, "user": "root"}
        ]
    assert steps[1]["agent"] == {"timeout_sec": 3600.0}
    assert out["environment"]["docker_image"] == TAG
    for step in steps:
        step["verifier"].pop("collect")
        if step["name"] != "round-3":
            step["verifier"].pop("timeout_sec")
        if not step["verifier"]:
            step.pop("verifier")
    out["environment"].pop("docker_image")
    assert out == tomllib.loads(TASK_TOML)
    assert copies.step_toml_overrides(TASK_TOML, 1200.0, docker_image=TAG) == text


def test_step_overrides_without_an_image_leave_the_environment_alone():
    out = tomllib.loads(copies.step_toml_overrides(TASK_TOML, 1200.0))
    assert out["environment"] == tomllib.loads(TASK_TOML)["environment"]


def test_step_overrides_put_the_kill_hook_before_a_task_defined_hook():
    own = TASK_TOML.replace(
        '[[steps]]\nname = "round-1"\n',
        '[[steps]]\nname = "round-1"\n\n[[steps.verifier.collect]]\ncommand = "echo theirs"\n',
    )
    collect = tomllib.loads(copies.step_toml_overrides(own, 1200.0))["steps"][0]["verifier"]["collect"]
    assert [hook["command"] for hook in collect] == [copies.COLLECT_COMMAND("round-1"), "echo theirs"]


def test_step_overrides_refuse_what_the_copy_cannot_pin():
    with pytest.raises(ValueError, match="steps"):
        copies.step_toml_overrides('schema_version = "1.2"\n', 600.0)
    with pytest.raises(ValueError, match="docker_image"):
        copies.step_toml_overrides(TASK_TOML.replace("cpus = 2", 'cpus = 2\ndocker_image = "x"'), 600.0,
                                   docker_image=TAG)
    separate = TASK_TOML.replace("[verifier]\ntimeout_sec = 1200.0",
                                 '[verifier]\ntimeout_sec = 1200.0\nenvironment_mode = "separate"')
    with pytest.raises(ValueError, match="separate"):
        copies.step_toml_overrides(separate, 600.0)
    step_env = TASK_TOML.replace("[steps.verifier]\ntimeout_sec = 300.0",
                                 "[steps.verifier]\ntimeout_sec = 300.0\n\n[steps.verifier.environment]\ncpus = 1")
    with pytest.raises(ValueError, match="separate"):
        copies.step_toml_overrides(step_env, 600.0)


def test_collect_command_refuses_names_that_are_not_one_directory():
    for bad in ("", ".", "..", "a/b", "a\\b", "a\0b"):
        with pytest.raises(ValueError):
            copies.COLLECT_COMMAND(bad)
    assert "'round 1'" in copies.COLLECT_COMMAND("round 1")


# ---- make_copy -------------------------------------------------------------------------------

def test_agent_copy_has_hooks_timeouts_pin_and_arm_image_but_no_solutions(tmp_path):
    src = make_task(tmp_path / "src")
    before = tree_digest(src)
    dest = tmp_path / "copies" / "agent" / "theme_demo"
    digest = copies.make_copy(src, dest, kind="agent", image_tag=TAG)
    assert tree_digest(src) == before
    assert digest == tree_digest(dest)
    for name in ROUNDS:
        setup = dest / "steps" / name / "workdir" / "setup.sh"
        assert setup.read_text() == copies.SETUP_SH
        assert os.access(setup, os.X_OK)
        assert (dest / "steps" / name / "instruction.md").is_file()
        assert (dest / "steps" / name / "tests" / "test.sh").is_file()
    assert list(dest.rglob("solution")) == []
    assert (dest / "steps" / "round-2" / "workdir" / "data.csv").read_text() == "a,b\n"
    dockerfile = (dest / "environment" / "Dockerfile").read_text()
    assert "go1.22.5.linux-arm64.tar.gz" in dockerfile and "amd64" not in dockerfile
    data = tomllib.loads((dest / "task.toml").read_text())
    assert data["environment"]["docker_image"] == TAG
    assert [s["verifier"]["timeout_sec"] for s in data["steps"]] == [1200.0, 1200.0, 300.0]
    assert [s["verifier"]["collect"][0]["command"] for s in data["steps"]] == [
        copies.COLLECT_COMMAND(name) for name in ROUNDS]


def test_validation_copy_keeps_every_solution(tmp_path):
    src = make_task(tmp_path / "src")
    dest = tmp_path / "copies" / "validation" / "theme_demo"
    copies.make_copy(src, dest, kind="validation", image_tag=TAG)
    for name in ROUNDS:
        assert (dest / "steps" / name / "solution" / "solve.sh").is_file()
        assert (dest / "steps" / name / "workdir" / "setup.sh").is_file()
    assert (dest / "solution" / "README.md").is_file()


def test_copies_of_one_task_have_one_digest_and_no_image_pin_when_none_is_given(tmp_path):
    src = make_task(tmp_path / "src")
    first = copies.make_copy(src, tmp_path / "a" / "theme_demo", kind="agent", image_tag=None)
    second = copies.make_copy(src, tmp_path / "b" / "theme_demo", kind="agent", image_tag=None)
    assert first == second
    data = tomllib.loads((tmp_path / "a" / "theme_demo" / "task.toml").read_text())
    assert "docker_image" not in data["environment"]


def test_a_task_without_a_verifier_timeout_gets_harbors_600_second_default(tmp_path):
    src = make_task(tmp_path / "src", toml=TASK_TOML.replace("[verifier]\ntimeout_sec = 1200.0\n", "[verifier]\n"))
    dest = tmp_path / "copy" / "theme_demo"
    copies.make_copy(src, dest, kind="agent", image_tag=TAG)
    data = tomllib.loads((dest / "task.toml").read_text())
    assert [s["verifier"]["timeout_sec"] for s in data["steps"]] == [600.0, 600.0, 300.0]


def test_make_copy_refuses_and_leaves_nothing_behind(tmp_path):
    src = make_task(tmp_path / "src")
    with pytest.raises(ValueError):
        copies.make_copy(src, tmp_path / "x" / "theme_demo", kind="oracle", image_tag=TAG)
    taken = tmp_path / "taken" / "theme_demo"
    taken.mkdir(parents=True)
    with pytest.raises(FileExistsError):
        copies.make_copy(src, taken, kind="agent", image_tag=TAG)

    odd = make_task(tmp_path / "odd", dockerfile="FROM ubuntu:22.04\nRUN curl -fsSLO https://example.com/helm-v3.15.0-linux-amd64.tar.gz\n")
    with pytest.raises(patch.UnknownArchPattern):
        copies.make_copy(odd, tmp_path / "y" / "theme_demo", kind="agent", image_tag=TAG)
    assert not (tmp_path / "y" / "theme_demo").exists()

    own_setup = make_task(tmp_path / "own")
    (own_setup / "steps" / "round-2" / "workdir" / "setup.sh").write_text("#!/bin/bash\ntrue\n")
    with pytest.raises(ValueError, match="setup.sh"):
        copies.make_copy(own_setup, tmp_path / "z" / "theme_demo", kind="agent", image_tag=TAG)
    assert not (tmp_path / "z" / "theme_demo").exists()


# ---- SETUP_SH, run for real in a sandbox -----------------------------------------------------

def run_setup(work, home, tmp_root):
    (work / "setup.sh").write_text(copies.SETUP_SH)
    env = dict(os.environ, HOME=str(home), EVO_TMP_ROOT=str(tmp_root))
    return subprocess.run(["bash", str(work / "setup.sh")], cwd=work, env=env,
                          capture_output=True, text=True, timeout=30)


def test_setup_empties_tmp_and_claude_projects_but_never_the_project(tmp_path):
    tmp_root, home, work = tmp_path / "tmp", tmp_path / "home", tmp_path / "app"
    (tmp_root / "sentinel-dir" / "deep").mkdir(parents=True)
    (tmp_root / "sentinel-dir" / "deep" / "f").write_text("x")
    (tmp_root / ".hidden").write_text("x")
    (tmp_root / "file.txt").write_text("x")
    (work / ".claude" / "harness" / "goals").mkdir(parents=True)
    (work / ".claude" / "harness" / "goals" / "g.json").write_text("{}")
    (work / "src.py").write_text("print(1)\n")
    os.symlink(work, tmp_root / "link-to-project")
    (home / ".claude" / "projects" / "-app" / "memory").mkdir(parents=True)
    (home / ".claude" / "projects" / "-app" / "memory" / "MEMORY.md").write_text("m")
    (home / ".claude" / "settings.json").write_text("{}")
    before = tree_digest(work)
    result = run_setup(work, home, tmp_root)
    assert result.returncode == 0, result.stderr
    assert tmp_root.is_dir() and list(tmp_root.iterdir()) == []
    assert not (home / ".claude" / "projects").exists()
    assert (home / ".claude" / "settings.json").is_file()
    assert not (work / "setup.sh").exists()
    assert tree_digest(work) == before


def test_setup_exits_zero_when_nothing_exists(tmp_path):
    work = tmp_path / "app"
    work.mkdir()
    result = run_setup(work, tmp_path / "no-home", tmp_path / "no-tmp")
    assert result.returncode == 0, result.stderr
    assert list(work.iterdir()) == []


def test_setup_keeps_a_project_that_lives_under_tmp(tmp_path):
    tmp_root = tmp_path / "tmp"
    work = tmp_root / "proj" / "app"
    work.mkdir(parents=True)
    (work / "main.py").write_text("x = 1\n")
    (tmp_root / "proj" / "sibling.txt").write_text("y")
    (tmp_root / "junk").write_text("z")
    result = run_setup(work, tmp_path / "home", tmp_root)
    assert result.returncode == 0, result.stderr
    assert sorted(p.name for p in tmp_root.iterdir()) == ["proj"]
    assert (work / "main.py").read_text() == "x = 1\n"
    assert not (work / "setup.sh").exists()


@pytest.mark.skipif(os.geteuid() == 0, reason="root can delete anything")
def test_setup_exits_zero_when_it_cannot_delete_something(tmp_path):
    tmp_root, work = tmp_path / "tmp", tmp_path / "app"
    work.mkdir()
    locked = tmp_root / "locked"
    locked.mkdir(parents=True)
    (locked / "f").write_text("x")
    locked.chmod(0o555)
    try:
        result = run_setup(work, tmp_path / "home", tmp_root)
    finally:
        locked.chmod(0o755)
    assert result.returncode == 0, result.stderr
    assert not (work / "setup.sh").exists()


# ---- COLLECT_COMMAND, run for real against a fake /proc of real processes -----------------------

def fake_proc(root, pid, ppid, argv, exe=None, status=True):
    entry = root / str(pid)
    entry.mkdir(parents=True)
    if status:
        (entry / "status").write_text(f"Name:\tx\nState:\tS (sleeping)\nPPid:\t{ppid}\n")
    (entry / "cmdline").write_bytes(b"".join(arg.encode() + b"\0" for arg in argv))
    if exe:
        os.symlink(exe, entry / "exe")


def run_collect(work, proc, arts, tmp_root, name="round-2"):
    env = dict(os.environ, EVO_PROC_ROOT=str(proc), EVO_ARTIFACTS_ROOT=str(arts), EVO_TMP_ROOT=str(tmp_root))
    return subprocess.run(["bash", "-c", copies.COLLECT_COMMAND(name)], cwd=work, env=env,
                          capture_output=True, text=True, timeout=60)


def test_collect_kills_claude_and_its_descendants_only_and_snapshots_the_round(tmp_path):
    procs = {key: subprocess.Popen(["sleep", "300"]) for key in
             ("wrapper", "claude", "child", "grandchild", "by_exe", "server", "hook", "kthread", "no_status")}
    pid = {key: p.pid for key, p in procs.items()}
    proc = tmp_path / "proc"
    versions = "/root/.local/share/claude/versions/2.1.293"
    fake_proc(proc, pid["wrapper"], 1, ["bash", "-c", "printf '%s' \"$x\" | claude --verbose --print | tee /logs/agent/claude-code.txt"], "/usr/bin/bash")
    fake_proc(proc, pid["claude"], pid["wrapper"], ["claude", "--verbose", "--output-format=stream-json", "--print"], versions)
    fake_proc(proc, pid["child"], pid["claude"], ["/bin/bash", "-c", "pytest -q"], "/usr/bin/bash")
    fake_proc(proc, pid["grandchild"], pid["child"], ["python3", "-m", "pytest", "-q"], "/usr/bin/python3.10")
    fake_proc(proc, pid["by_exe"], 1, [versions, "--print"], versions)
    fake_proc(proc, pid["server"], 1, ["python3", "-m", "http.server"], "/usr/bin/python3.10")
    fake_proc(proc, pid["hook"], 0, ["bash", "-c", copies.COLLECT_COMMAND("round-2")], "/usr/bin/bash")
    fake_proc(proc, pid["kthread"], 2, [])
    fake_proc(proc, pid["no_status"], pid["claude"], ["claude", "--print"], versions, status=False)
    work, arts, tmp_root = tmp_path / "app", tmp_path / "artifacts", tmp_path / "tmp"
    (work / ".claude" / "harness" / "goals").mkdir(parents=True)
    (work / ".claude" / "harness" / "goals" / "g.json").write_text("{}")
    (work / ".git").mkdir()
    (work / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (work / "pkg").mkdir()
    (work / "pkg" / "mod.py").write_text("")
    (work / "src.py").write_text("")
    tmp_root.mkdir()
    (tmp_root / "sentinel").write_text("s")
    try:
        result = run_collect(work, proc, arts, tmp_root)
        assert result.returncode == 0, result.stderr
        for key in ("claude", "child", "grandchild", "by_exe"):
            assert procs[key].wait(timeout=10) == -signal.SIGKILL, key
        for key in ("wrapper", "server", "hook", "kthread", "no_status"):
            assert procs[key].poll() is None, key
    finally:
        for p in procs.values():
            if p.poll() is None:
                p.kill()
                p.wait()
    out = arts / "round-2"
    killed = (out / "killed.txt").read_text().splitlines()
    assert sorted(int(line.split()[0]) for line in killed) == sorted(
        pid[key] for key in ("claude", "child", "grandchild", "by_exe"))
    assert any(line.startswith(f"{pid['claude']} claude --verbose") for line in killed)
    assert (out / "harness" / "goals" / "g.json").read_text() == "{}"
    assert "sentinel" in (out / "tmp-ls.txt").read_text()
    assert (out / "project-files.txt").read_text().splitlines() == [
        "./.claude/harness/goals/g.json", "./pkg/mod.py", "./src.py"]
    assert (work / ".claude" / "harness" / "goals" / "g.json").read_text() == "{}"


def test_collect_exits_zero_with_nothing_to_kill_or_copy(tmp_path):
    work, arts = tmp_path / "app", tmp_path / "artifacts"
    work.mkdir()
    result = run_collect(work, tmp_path / "no-proc", arts, tmp_path / "no-tmp", name="round-1")
    assert result.returncode == 0, result.stderr
    out = arts / "round-1"
    assert (out / "killed.txt").read_text() == ""
    assert not (out / "harness").exists()
    assert (out / "project-files.txt").read_text() == ""
    assert (out / "tmp-ls.txt").is_file()


# ---- prompts ---------------------------------------------------------------------------------

def test_write_prompt_writes_the_arm_text_for_its_minutes(tmp_path):
    control = copies.write_prompt(tmp_path / "prompts" / "theme_demo__control.md", "control", 30)
    horizon = copies.write_prompt(tmp_path / "prompts" / "theme_demo__horizon.md", "horizon", 60)
    assert control == tmp_path / "prompts" / "theme_demo__control.md"
    assert control.read_text() == config.prompt_text("control", 30) + "\n"
    assert horizon.read_text() == config.prompt_text("horizon", 60) + "\n"
    assert "30 minutes" in control.read_text() and "horizon" not in control.read_text()
    assert "60 minutes" in horizon.read_text() and "horizon skill" in horizon.read_text()
    with pytest.raises(ValueError):
        copies.write_prompt(tmp_path / "prompts" / "x__ablation.md", "ablation", 30)


def test_collect_lists_the_processes_that_survive_into_the_next_round(tmp_path):
    procs = {key: subprocess.Popen(["sleep", "300"]) for key in ("claude", "server", "hook")}
    pid = {key: p.pid for key, p in procs.items()}
    proc = tmp_path / "proc"
    fake_proc(proc, pid["claude"], 1, ["claude", "--print"], "/root/.local/share/claude/versions/2.1.293")
    fake_proc(proc, pid["server"], 1, ["python3", "-m", "http.server", "8000"], "/usr/bin/python3.10")
    fake_proc(proc, pid["hook"], 0, ["bash", "-c", copies.COLLECT_COMMAND("round-2")], "/usr/bin/bash")
    work = tmp_path / "app"
    work.mkdir()
    try:
        result = run_collect(work, proc, tmp_path / "artifacts", tmp_path / "tmp")
        assert result.returncode == 0, result.stderr
    finally:
        for p in procs.values():
            if p.poll() is None:
                p.kill()
            p.wait()
    survivors = (tmp_path / "artifacts" / "round-2" / copies.SURVIVORS_FILE).read_text().splitlines()
    assert survivors == [f"{pid['server']} python3 -m http.server 8000"]


def test_a_bundled_go_tarball_is_replaced_in_the_copy_and_recorded(tmp_path, monkeypatch):
    import hashlib
    import json

    arm64 = b"arm64 toolchain"
    fetched = []

    def fetch(url, dest=None):
        fetched.append(url)
        if dest is None:
            return [{"files": [{"filename": "go1.21.0.linux-arm64.tar.gz", "sha256": hashlib.sha256(arm64).hexdigest()}]}]
        dest.write_bytes(arm64)

    monkeypatch.setattr(patch, "_fetch", fetch)
    src = make_task(tmp_path / "src", dockerfile="FROM ubuntu:22.04\nCOPY go1.21.0.linux-amd64.tar.gz /tmp/\n"
                                                 "RUN tar -C /usr/local -xzf /tmp/go1.21.0.linux-amd64.tar.gz\n")
    (src / "environment" / "go1.21.0.linux-amd64.tar.gz").write_bytes(b"amd64 toolchain")
    before = tree_digest(src)
    dest = tmp_path / "copy" / "theme_demo"
    copies.make_copy(src, dest, kind="agent", image_tag=TAG)
    assert tree_digest(src) == before
    assert sorted(p.name for p in (dest / "environment").iterdir()) == ["Dockerfile", "go1.21.0.linux-arm64.tar.gz"]
    assert "amd64" not in (dest / "environment" / "Dockerfile").read_text()
    record = json.loads((dest / copies.ARM_PATCH_FILE).read_text())
    assert record["artifacts"][0]["sha256"] == hashlib.sha256(arm64).hexdigest()
    assert [c.split(":", 1)[0] for c in record["changed"]] == ["3"]
    assert fetched == ["https://go.dev/dl/?mode=json&include=all", "https://go.dev/dl/go1.21.0.linux-arm64.tar.gz"]
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `sh bench/tb4/test.sh -q tests/test_evo_copies.py`

Expected: FAIL during collection with `E   ImportError: cannot import name 'copies' from 'bench.evo' (/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode/bench/evo/__init__.py)`, then `Interrupted: 1 error during collection`.

- [ ] **Step 3: Implement**

Create `bench/evo/copies.py`:

```python
"""Agent and validation copies of an EvoCode task: arm64 Dockerfile, per-round hooks, step verifier
timeouts, an optional pinned image, and no reference solutions in agent copies. Also the prompt files.

Harbor 0.24.0 facts these hooks rely on (harbor/trial/multi_step.py, harbor/trial/trial.py):
- Before each agent phase, Harbor uploads steps/<name>/workdir/. into the container's working directory
  and runs `bash <workdir>/setup.sh` there as the step's agent user. A non-zero exit fails the step and
  ends the trial, so SETUP_SH always exits 0.
- After each agent phase (also after a timeout or an agent error) and before the verifier, Harbor runs the
  task's [[verifier.collect]] hooks and then the step's [[steps.verifier.collect]] hooks in the main
  service with `bash -c`, in the same working directory. Failures are logged and never stop the trial.
- A step's verifier timeout is [steps.verifier].timeout_sec, which defaults to 600 s; the task-level
  [verifier].timeout_sec is never consulted for a step.
- Files written under /logs/artifacts during a step end up in the trial's steps/<name>/artifacts/logs/artifacts/
  (bind mount at <trial>/artifacts/logs/artifacts, moved after each step).

The env variables EVO_TMP_ROOT, EVO_PROC_ROOT and EVO_ARTIFACTS_ROOT exist only so tests can run the
hooks on a Mac sandbox; Harbor never sets them, so the defaults /tmp, /proc and /logs/artifacts apply.
"""
import datetime
import json
import re
import shlex
import shutil
import tomllib
from pathlib import Path

from bench.evo import config, patch
from bench.tb4.copies import tree_digest

KINDS = ("agent", "validation")
HARBOR_VERIFIER_TIMEOUT_DEFAULT = 600.0
COLLECT_TIMEOUT_SEC = 60.0
COLLECT_USER = "root"
HARNESS_COPY = "harness"
KILLED_FILE = "killed.txt"
TMP_LISTING_FILE = "tmp-ls.txt"
PROJECT_FILES_FILE = "project-files.txt"
SURVIVORS_FILE = "survivors.txt"
ARM_PATCH_FILE = "arm-patch.json"  # the patch record, at the top of each copy

SETUP_SH = """#!/bin/bash
# EvoCode first look: per-round reset (bench/evo/copies.py). Harbor runs this as the step's agent user
# from the agent working directory before every agent phase. It empties /tmp and Claude's per-project
# session memory, never touches the project, deletes itself last, and always exits 0.
shopt -s dotglob nullglob
tmp_root="${EVO_TMP_ROOT:-/tmp}"
here="$(pwd -P)"
tmp_real="$(cd "$tmp_root" 2>/dev/null && pwd -P)"
if [ -n "$tmp_real" ] && [ "$here" != "$tmp_real" ]; then
  for entry in "$tmp_real"/*; do
    case "$here/" in
      "$entry"/*) continue ;;
    esac
    rm -rf -- "$entry" 2>/dev/null
  done
fi
rm -rf -- "${HOME:-/root}/.claude/projects" 2>/dev/null
rm -f -- "$0"
exit 0
"""

_COLLECT_TEMPLATE = r"""# evo-collect-hook (bench/evo/copies.py)
set +e
out="${EVO_ARTIFACTS_ROOT:-/logs/artifacts}"/__ROUND__
proc="${EVO_PROC_ROOT:-/proc}"
mkdir -p "$out"
evo_tree() {
  roots=" " pairs=""
  for d in "$proc"/[0-9]*; do
    pid=${d##*/}
    [ "$pid" = "$$" ] && continue
    ppid=""
    while read -r key val rest; do
      if [ "$key" = "PPid:" ]; then ppid=$val; break; fi
    done 2>/dev/null < "$d/status"
    [ -n "$ppid" ] || continue
    pairs="$pairs $pid:$ppid"
    arg0=""
    IFS= read -r -d '' arg0 2>/dev/null < "$d/cmdline"
    exe=$(readlink "$d/exe" 2>/dev/null)
    case "${arg0##*/}|$exe" in
      "claude|"*|*/claude|*/claude/versions/*) roots="$roots$pid " ;;
    esac
  done
  tree=$roots grown=1
  while [ "$grown" = 1 ]; do
    grown=0
    for pair in $pairs; do
      case "$tree" in
        *" ${pair#*:} "*)
          case "$tree" in
            *" ${pair%%:*} "*) ;;
            *) tree="$tree${pair%%:*} " grown=1 ;;
          esac ;;
      esac
    done
  done
  echo $tree
}
all=" " pass=0
while [ "$pass" -lt 5 ]; do
  pass=$((pass + 1))
  new=""
  for pid in $(evo_tree); do
    case "$all" in *" $pid "*) ;; *) new="$new $pid" ;; esac
  done
  [ -n "$new" ] || break
  kill -STOP $new 2>/dev/null
  all="$all${new# } "
done
: > "$out/killed.txt"
for pid in $all; do
  printf '%s %s\n' "$pid" "$(tr '\0' ' ' 2>/dev/null < "$proc/$pid/cmdline" | cut -c1-200)" >> "$out/killed.txt"
done
[ "$all" = " " ] || kill -KILL $all 2>/dev/null
if [ -d "$PWD/.claude/harness" ]; then
  cp -R "$PWD/.claude/harness" "$out/harness"
fi
ls -la "${EVO_TMP_ROOT:-/tmp}" > "$out/tmp-ls.txt" 2>&1
find . \( -name .git -prune \) -o \( -type f -o -type l \) -print 2>/dev/null | LC_ALL=C sort > "$out/project-files.txt"
: > "$out/survivors.txt"
for d in "$proc"/[0-9]*; do
  pid=${d##*/}
  case "$pid" in "$$"|1) continue ;; esac
  case "$all" in *" $pid "*) continue ;; esac
  args=$(tr '\0\n' '  ' 2>/dev/null < "$d/cmdline" | cut -c1-200)
  args=${args% }
  case "$args" in ""|*evo-collect-hook*) continue ;; esac
  printf '%s %s\n' "$pid" "$args" >> "$out/survivors.txt"
done
chmod -R a+rwX "$out" 2>/dev/null
exit 0
"""

_BARE_KEY = re.compile(r"[A-Za-z0-9_-]+")
_ESCAPES = {'"': '\\"', "\\": "\\\\", "\b": "\\b", "\t": "\\t", "\n": "\\n", "\f": "\\f", "\r": "\\r"}


def _check_step_name(name: str) -> str:
    if not name or name in (".", "..") or any(ch in name for ch in "/\\\0"):
        raise ValueError(f"step name {name!r} is not a single directory name")
    return name


def COLLECT_COMMAND(round_name: str) -> str:
    """The collect hook for one round: stop every claude process and its descendants, then snapshot the
    Horizon ledger, a /tmp listing and the project file list into /logs/artifacts/<round>/. Exits 0."""
    return _COLLECT_TEMPLATE.replace("__ROUND__", shlex.quote(_check_step_name(round_name)))


def _toml_string(text: str) -> str:
    out = []
    for ch in text:
        if ch in _ESCAPES:
            out.append(_ESCAPES[ch])
        elif ord(ch) < 0x20 or ord(ch) == 0x7F:
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return '"' + "".join(out) + '"'


def _toml_key(key: str) -> str:
    return key if _BARE_KEY.fullmatch(key) else _toml_string(key)


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return _toml_string(value)
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{_toml_key(k)} = {_toml_value(v)}" for k, v in value.items()) + "}"
    raise ValueError(f"cannot write TOML value {value!r}")


def _is_table_array(value) -> bool:
    return isinstance(value, list) and bool(value) and all(isinstance(item, dict) for item in value)


def _emit_table(lines: list, path: tuple, table: dict) -> None:
    for key, value in table.items():
        if not isinstance(value, dict) and not _is_table_array(value):
            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
    for key, value in table.items():
        sub = path + (key,)
        name = ".".join(_toml_key(part) for part in sub)
        if isinstance(value, dict):
            lines += ["", f"[{name}]"]
            _emit_table(lines, sub, value)
        elif _is_table_array(value):
            for item in value:
                lines += ["", f"[[{name}]]"]
                _emit_table(lines, sub, item)


def dump_toml(data: dict) -> str:
    """Write a parsed TOML document back out in a fixed order; raise unless it parses back to `data`."""
    lines = []
    _emit_table(lines, (), data)
    text = "\n".join(lines).lstrip("\n") + "\n"
    if tomllib.loads(text) != data:
        raise ValueError("TOML rewrite did not round-trip")
    return text


def _refuse_separate_verifier(data: dict) -> None:
    verifiers = [("[verifier]", data.get("verifier", {}))]
    verifiers += [(f"step {s.get('name')!r}", s.get("verifier", {})) for s in data["steps"]]
    for where, verifier in verifiers:
        if "environment" in verifier or verifier.get("environment_mode") == "separate":
            raise ValueError(f"{where}: a separate verifier environment is not supported by the copies")


def step_toml_overrides(task_toml_text: str, verifier_timeout_sec: float, *, docker_image: str | None = None) -> str:
    """Give every [[steps]] entry a verifier timeout (unless it sets its own) and the collect hook, first in
    its hook list; pin [environment].docker_image when given. Every other key is kept."""
    data = tomllib.loads(task_toml_text)
    steps = data.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("task.toml has no [[steps]]")
    _refuse_separate_verifier(data)
    for step in steps:
        verifier = step.setdefault("verifier", {})
        verifier.setdefault("timeout_sec", float(verifier_timeout_sec))
        hook = {"command": COLLECT_COMMAND(step["name"]), "timeout_sec": COLLECT_TIMEOUT_SEC, "user": COLLECT_USER}
        verifier["collect"] = [hook, *verifier.get("collect", [])]
    if docker_image is not None:
        environment = data.setdefault("environment", {})
        if "docker_image" in environment:
            raise ValueError("task already sets [environment].docker_image")
        environment["docker_image"] = docker_image
    return dump_toml(data)


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def _fill_copy(dest: Path, kind: str, image_tag: str | None) -> None:
    dockerfile = dest / "environment" / "Dockerfile"
    text, artifacts = patch.replace_bundled_go(dest / "environment", dockerfile.read_text())
    patched, changed = patch.arm_patch(text)
    dockerfile.write_text(patched)
    (dest / ARM_PATCH_FILE).write_text(json.dumps({"changed": changed, "artifacts": artifacts}, indent=2) + "\n")
    toml_path = dest / "task.toml"
    text = toml_path.read_text()
    data = tomllib.loads(text)
    for step in data.get("steps") or []:
        step_dir = dest / "steps" / _check_step_name(step["name"])
        if not step_dir.is_dir():
            raise FileNotFoundError(step_dir)
        setup = step_dir / "workdir" / "setup.sh"
        if setup.exists() or setup.is_symlink():
            raise ValueError(f"{setup}: the task already ships a setup.sh")
        setup.parent.mkdir(exist_ok=True)
        setup.write_text(SETUP_SH)
        setup.chmod(0o755)
    timeout = data.get("verifier", {}).get("timeout_sec", HARBOR_VERIFIER_TIMEOUT_DEFAULT)
    toml_path.write_text(step_toml_overrides(text, timeout, docker_image=image_tag))
    if kind == "agent":
        _remove(dest / "solution")
        for step_dir in sorted((dest / "steps").iterdir()):
            _remove(step_dir / "solution")


def make_copy(src_task_dir: Path, dest_dir: Path, *, kind: str, image_tag: str | None) -> str:
    """Copy a task, then patch, hook, time and pin the copy; agent copies lose every solution. The source
    is only read. Returns the copy's tree digest; on any error the partial copy is removed."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}, not {kind!r}")
    src_task_dir, dest_dir = Path(src_task_dir), Path(dest_dir)
    if dest_dir.exists():
        raise FileExistsError(dest_dir)
    shutil.copytree(src_task_dir, dest_dir, symlinks=True)
    try:
        _fill_copy(dest_dir, kind, image_tag)
    except BaseException:
        shutil.rmtree(dest_dir, ignore_errors=True)
        raise
    return tree_digest(dest_dir)


def write_prompt(dest: Path, arm: str, minutes: int) -> Path:
    """Write the extra-instruction file Harbor appends to every round's instruction for this arm."""
    if arm not in config.ARMS:
        raise ValueError(f"unknown arm {arm!r}")
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(config.prompt_text(arm, minutes) + "\n")
    return dest
```

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `sh bench/tb4/test.sh -q tests/test_evo_copies.py`

Expected: `21 passed in 1.17s` (the time varies). The kill tests take well under a second.

Run: `sh bench/tb4/test.sh -q tests/test_evo_copies.py tests/test_tb4_copies.py`

Expected: `29 passed in 0.33s` (the time varies). TB4's `tree_digest` and copies are unchanged.

- [ ] **Step 5: Check a copy against Harbor's own task model (no Docker, no network)**

Run, from the worktree root:

```sh
"$(uv tool dir)/harbor/bin/python" - <<'PY'
import importlib.metadata, json, pathlib, shutil, sys, tempfile
sys.path.insert(0, ".")
from harbor.models.task.task import Task
from harbor.models.task.verifier_mode import resolve_step_verifier_mode
from bench.evo import copies

root = pathlib.Path(tempfile.mkdtemp())
try:
    src = root / "src" / "demo"
    (src / "environment").mkdir(parents=True)
    (src / "environment" / "Dockerfile").write_text("FROM ubuntu:22.04\nWORKDIR /app\n")
    (src / "task.toml").write_text(
        'schema_version = "1.2"\nmulti_step_reward_strategy = "mean"\n\n'
        '[[steps]]\nname = "round-1"\n\n[[steps]]\nname = "round-2"\n\n[steps.verifier]\ntimeout_sec = 300.0\n\n'
        '[agent]\ntimeout_sec = 1800.0\n\n[verifier]\ntimeout_sec = 1200.0\n\n[environment]\ncpus = 1\n')
    for name in ("round-1", "round-2"):
        (src / "steps" / name / "tests").mkdir(parents=True)
        (src / "steps" / name / "solution").mkdir()
        (src / "steps" / name / "instruction.md").write_text("synthetic\n")
        (src / "steps" / name / "tests" / "test.sh").write_text("#!/bin/bash\n")
        (src / "steps" / name / "solution" / "solve.sh").write_text("#!/bin/bash\n")
    dest = root / "copy" / "demo"
    copies.make_copy(src, dest, kind="agent", image_tag="evo-local/demo:9fcae3e5539d")
    task = Task(dest)
    print("harbor", importlib.metadata.version("harbor"))
    for step in task.config.steps:
        hooks = [(h.service, h.timeout_sec, h.user, h.command == copies.COLLECT_COMMAND(step.name))
                 for h in step.verifier.collect]
        print(step.name, step.verifier.timeout_sec, resolve_step_verifier_mode(task.config, step).value,
              hooks, task.paths.step_setup_path(step.name).is_file())
    print(task.config.environment.docker_image)
    print(Task.is_valid_dir(dest), sorted(p.name for p in dest.iterdir()),
          json.loads((dest / copies.ARM_PATCH_FILE).read_text()))
finally:
    shutil.rmtree(root)
PY
```

Expected, exactly:

```
harbor 0.24.0
round-1 1200.0 shared [('main', 60.0, 'root', True)] True
round-2 300.0 shared [('main', 60.0, 'root', True)] True
evo-local/demo:9fcae3e5539d
True ['arm-patch.json', 'environment', 'steps', 'task.toml'] {'changed': [], 'artifacts': []}
```

If the output differs:
- **A pydantic `ValidationError`** means a field name or shape no longer matches Harbor. Fix `step_toml_overrides` against `harbor/models/task/config.py`, add a unit test, and rerun.
- **`600.0` on `round-1`** means the step timeout did not take effect.
- **`False` on the last line** means Harbor no longer accepts a task directory that holds `arm-patch.json`. Stop and ask the user where the patch record should go.
- **Any version other than `0.24.0`** means you must stop: the pinned Harbor is not the one installed.

- [ ] **Step 6: Smoke-run both hooks on real Linux (operator; Docker, no agent, no subscription)**

The unit tests run the hooks under macOS bash 3.2 against a fake `/proc`. This step runs them in an ubuntu:22.04 container with:
- a real `/proc`;
- bash 5;
- GNU tools;
- no procps.

ubuntu:22.04 is the base image of every EvoCode task.

Run, from the worktree root:

```sh
chk="$(mktemp -d)"
uv run --no-project python -c "import sys; from bench.evo import copies; d = sys.argv[1]; open(d + '/setup.sh', 'w').write(copies.SETUP_SH); open(d + '/collect.sh', 'w').write(copies.COLLECT_COMMAND('round-2'))" "$chk"
cat > "$chk/drive.sh" <<'EOF_DRIVE'
#!/bin/bash
# Smoke-run the Task 5 hooks on real Linux inside a throwaway container.
set -u
mkdir -p /app/.claude/harness/goals /app/.git /root/.claude/projects/-app/memory
echo '{}' > /app/.claude/harness/goals/g.json
echo ref > /app/.git/HEAD
echo x > /app/main.py
echo s > /tmp/old-sentinel
echo m > /root/.claude/projects/-app/memory/MEMORY.md
cp /check/setup.sh /app/setup.sh
cd /app
bash /app/setup.sh; echo "setup_rc=$?"
echo "tmp_empty=$([ -z "$(ls -A /tmp)" ] && echo yes || echo no)"
echo "projects_gone=$([ -e /root/.claude/projects ] && echo no || echo yes)"
echo "setup_gone=$([ -e /app/setup.sh ] && echo no || echo yes)"
echo "ledger_kept=$([ -f /app/.claude/harness/goals/g.json ] && echo yes || echo no)"
bash -c '(exec -a claude bash -c "sleep 301 & (sleep 302 & wait) & wait"); sleep 400' 2>/dev/null &
wrapper=$!
sleep 303 &
bystander=$!
echo t > /tmp/round-sentinel
sleep 1
bash -c "$(cat /check/collect.sh)"; echo "collect_rc=$?"
sleep 1
alive() { kill -0 "$1" 2>/dev/null && echo yes || echo no; }
echo "wrapper_alive=$(alive "$wrapper") bystander_alive=$(alive "$bystander")"
left=$(for p in /proc/[0-9]*; do tr '\0' ' ' < "$p/cmdline" 2>/dev/null; echo; done | grep -cE '^(claude |sleep 30[12] )')
echo "claude_tree_left=$left killed_lines=$(wc -l < /logs/artifacts/round-2/killed.txt)"
echo "harness_copied=$([ -f /logs/artifacts/round-2/harness/goals/g.json ] && echo yes || echo no)"
echo "project_files=$(tr '\n' ' ' < /logs/artifacts/round-2/project-files.txt)"
echo "tmp_listed=$(grep -c round-sentinel /logs/artifacts/round-2/tmp-ls.txt)"
survivors=/logs/artifacts/round-2/survivors.txt
echo "survivor_lines=$(wc -l < "$survivors") wrapper_listed=$(grep -c "^$wrapper " "$survivors") bystander_listed=$(grep -cx "$bystander sleep 303" "$survivors")"
kill "$wrapper" "$bystander" 2>/dev/null
exit 0
EOF_DRIVE
docker pull -q --platform linux/arm64 ubuntu:22.04
docker run --rm --network none --platform linux/arm64 -v "$chk:/check:ro" ubuntu:22.04 bash /check/drive.sh
rm -rf "$chk"
```

Expected, after the `docker.io/library/ubuntu:22.04` line from the pull (the `project_files` line ends in a space):

```
setup_rc=0
tmp_empty=yes
projects_gone=yes
setup_gone=yes
ledger_kept=yes
collect_rc=0
wrapper_alive=yes bystander_alive=yes
claude_tree_left=0 killed_lines=4
harness_copied=yes
project_files=./.claude/harness/goals/g.json ./main.py 
tmp_listed=1
survivor_lines=2 wrapper_listed=1 bystander_listed=1
```

What each line covers:
- `killed_lines=4`: the `claude`-named shell, its subshell, `sleep 301` and `sleep 302`.
- `wrapper_alive=yes`: the hook spared the `bash -c '… claude …'` parent.
- `bystander_alive=yes`: the hook spared an unrelated process.
- `survivor_lines=2`: `survivors.txt` lists the wrapper and the bystander, and nothing else. PID 1 (`drive.sh`), the hook's own shells and the four killed processes are left out.

If the output differs:
- **`claude_tree_left` above 0, or `killed_lines` not 4:** the tree walk missed a process.
- **`wrapper_alive=no` or `bystander_alive=no`:** the match is too wide.
- **`tmp_empty=no` or `setup_gone=no`:** `SETUP_SH` is wrong.
- **`survivor_lines` not 2, or a `_listed` count of 0:** the survivors list includes PID 1, the hook or a killed process, or it misses a live one.

In every case, do not continue to Task 6. Reproduce the failure as a unit test in `tests/test_evo_copies.py`, fix `bench/evo/copies.py`, and rerun Steps 4 to 6.

If Docker is not running, start Docker Desktop and rerun. This step needs no network beyond the image pull.

- [ ] **Step 7: Run the whole suite**

Run: `sh bench/tb4/test.sh -q`

Expected: `906 passed` (885 after Task 4 + 21; the time varies). If other work has landed first, expect the previous count plus this task's tests, with 0 failures.

- [ ] **Step 8: Commit**

```sh
git add bench/evo/copies.py tests/test_evo_copies.py
git commit -m "feat(evo): task copies with per-round hooks, step verifier timeouts and prompt files"
```

Run: `git show --stat --format=%s HEAD | head -20`

Expected:

```text
feat(evo): task copies with per-round hooks, step verifier timeouts and prompt files

 bench/evo/copies.py      | 300 ++++++++++++++++++++++++++++++
 tests/test_evo_copies.py | 473 +++++++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 773 insertions(+)
```

```
feat(evo): task copies with per-round hooks, step verifier timeouts and prompt files

 bench/evo/copies.py      | 300 ++++++++++++++++++++++++++++++
 tests/test_evo_copies.py | 473 +++++++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 773 insertions(+)
```

### Task 6: Steps reader, images and validation

**Deviation (additive only; every skeleton name and signature is kept):**
- `steps.read_trial` step dicts carry one extra key, `agent_sec` (float or `None`).
  - `agent_dir`, `verifier_dir` and `artifacts_dir` are `pathlib.Path` objects.
  - `agent_started` and `agent_finished` are Harbor's ISO strings, for example `"2026-10-09T10:00:00.123456Z"`.
  - `reward` is Harbor's recorded step reward (`step_results[i].verifier_result.rewards.reward`). When that is missing, it falls back to the step's reward files.
- `steps.case_counts` returns `(passed, total)`, in that order.
- `images.inspect` returns TB4's superset `{"id","arch","workdir","user"}`. `images.build_command` and `images.remove` are added.
- `validate_task` also creates `copies/agent/<task>/`, the copy that Task 8 runs.
  - The skeleton names no owner for that copy, so Task 8 must not create it again.
  - `copy_digest` is the digest of the agent copy.
- `validate_task` tries `docker build` twice before it fails a task, because a network hiccup during apt or a download is not a property of the task. The reason then starts `build failed twice:`.
- The Validation JSON gains these keys:
  - `validation_copy_digest`;
  - `image_tag`;
  - `workdir`, where Harbor runs the agent, the hooks and the oracle: `[environment].workdir` from the copy's `task.toml` when it is set, else the image's WORKDIR. The planted ledger goes there, and Task 8 needs it to find the ledger.
  - `user`, the sorted distinct users that the rounds' `setup.sh` and agent run as: `[steps.agent].user`, else `[agent].user`, else the image's USER. `""`, `"0"`, `0` and `"root"`, and a `user:group` whose user part is one of those (for example `0:0`), are all written `"root"`. It is `null` when validation stops before the image is inspected. It does not gate `ok`.
  - `python`, which records the TB4 python3 >= 3.10 probe and does not gate `ok`;
  - `oracle_cases`, the `[passed, total]` pairs per oracle round;
  - `code`, the `validate.CODE_DIGEST` of the code that made the record;
  - `arm_patch`, the validation copy's `arm-patch.json` from Task 5: `{"changed": [...], "artifacts": [...]}`. It is absent when making the copies failed.
- `validate.CODE_DIGEST` is the sha256 of `copies.py`, `images.py`, `patch.py`, `steps.py` and `validate.py` (`validate.CODE_FILES`), read together. `validate_in_order` reuses a saved record only when its `code` matches. Any edit to those five files therefore makes the next walk validate every task again, and Task 11 refuses a record whose `code` differs.
- `validate.agent_users(task_toml, image_user)` computes `user`.
- The summary of `validate_in_order` gains `python_missing`, the selected tasks whose image failed the python3 probe. The `--n` CLI prints it too.
- New run paths:
  - `copies/planted/<task>/`;
  - `checks/<kind>__<task>/`, where Harbor puts the validation jobs;
  - `logs/build__<task>.log` and `logs/<kind>__<task>.log`;
  - `validation/summary.json`.
- `validate_in_order(order, n, *, runs, run=subprocess.run, log=print) -> dict`.

**Files:**
- Create: `bench/evo/steps.py`, `bench/evo/images.py`, `bench/evo/validate.py`
- Test: `tests/test_evo_steps.py`, `tests/test_evo_images.py`, `tests/test_evo_validate.py`

**Interfaces:**
- Consumes:
  - Task 1: `config.RUNS`, `config.DATASET_REVISION`, `config.AGENT_TIMEOUT_MULTIPLIER`, `config.image_tag(task: str) -> str`.
  - Task 3: the file `RUNS/order.json`. Only its `"order"` list is read, and `order.py` is not imported.
  - Task 4: `patch.UnknownArchPattern`, and the bytes of `bench/evo/patch.py` for `CODE_DIGEST`.
  - Task 5:
    - `copies.make_copy(src_task_dir, dest_dir, *, kind, image_tag) -> str`;
    - `copies.ARM_PATCH_FILE` (`"arm-patch.json"`), the record that `make_copy` writes at the top of each copy: `{"changed": [...], "artifacts": [...]}`;
    - the bytes of `bench/evo/copies.py` for `CODE_DIGEST`.
  - TB4, unchanged:
    - `bench.tb4.images.PLATFORM`, `bench.tb4.images.inspect(tag, run)` and `bench.tb4.images.python_ok(tag, run)`;
    - `bench.tb4.copies.tree_digest(root)`;
    - `bench.tb4.run.job_result(job_dir)` and `bench.tb4.run.hold_lock(path)`.
- Produces:
  - `steps.read_trial(trial_dir) -> {"steps": [{"name","index","reward","exception","agent_started","agent_finished","agent_sec","agent_dir","verifier_dir","artifacts_dir"}], "trial_exception"}`:
    - `index` starts at 1.
    - The list holds only the steps Harbor ran, so a chain that stopped early is shorter.
    - `exception` and `trial_exception` are `exception_type` strings or `None`.
    - A step name that would leave the trial directory raises `ValueError`.
  - `steps.step_reward(step_dir) -> float | None` reads `verifier/reward.json` `"reward"` first, else `verifier/reward.txt`. This is Harbor's precedence.
  - `steps.case_counts(step_dir) -> tuple[int, int] | None` returns `(passed, total)` from the last `CASE_SUMMARY` line of `verifier/test-stdout.txt`.
  - `images.build_command(copy_dir, tag) -> list` and `images.build(copy_dir, tag, log, run=subprocess.run) -> None`.
  - `images.inspect(tag, run=subprocess.run) -> {"id","arch","workdir","user"}` and `images.remove(tag, run=subprocess.run) -> None`.
  - `validate.check_command(copy, agent, job_name, jobs_dir) -> list`.
  - `validate.planted_copy(validation_copy, dest, workdir, first_step) -> str` and `validate.PLANTED_LEDGER: dict[str, str]`.
  - `validate.round_results(job_dir, names) -> (rewards, cases)` and `validate.failures(out) -> list[str]`. Task 11 applies `failures` again to the saved records.
  - `validate.CODE_DIGEST: str`. Task 11 compares each record's `code` with it.
  - `validate.agent_users(task_toml: dict, image_user) -> list[str]`, with the private helper `_user(value) -> str`.
  - `validate.validate_task(task, *, runs, run=subprocess.run) -> dict`, which also writes `RUNS/validation/<task>.json` with the keys `task`, `copy_digest`, `validation_copy_digest`, `image_tag`, `image_id`, `arch`, `workdir`, `user`, `python`, `rounds`, `oracle`, `nop`, `planted`, `oracle_cases`, `ok`, `reason`, `code` and `arm_patch`, in that order.
  - `validate.validate_in_order(order, n, *, runs, run=subprocess.run, log=print) -> {"n","order","selected","walked","swaps","complete","python_missing"}`, which also writes `RUNS/validation/summary.json`.
  - CLI: `python3 -m bench.evo.validate (--n N | --task NAME) [--runs DIR]`.
    - `--n` prints `{"selected","swaps","complete","python_missing"}` and exits 0 when `complete` is true, else 1.
    - `--task` prints `{"task","ok","reason"}` and exits 0 when `ok` is true, else 1.

**Where the facts come from:**
- **Harbor 0.24.0.**
  - `harbor/models/trial/result.py`: `TrialResult.step_results: list[StepResult] | None`. `StepResult` has these fields:
    - `step_name`;
    - `agent_result`;
    - `verifier_result.rewards`;
    - `exception_info.exception_type`;
    - `agent_execution.started_at` and `agent_execution.finished_at`;
    - `verifier`.
  - Datetimes dump as `...Z`. This was checked by dumping a real `StepResult`.
  - `harbor/models/trial/paths.py`: the trial file is `result.json`, even though the class docstring says `results.json`. Each step's outputs go to `steps/<step_name>/{agent,verifier,artifacts}`.
  - `harbor/trial/multi_step.py`: `_archive_step_outputs` moves the outputs there after every step. The chain stops after a step that has `exception_info` and no `verifier_result`, or that fails `min_reward`. An `AgentTimeoutError` step is still verified.
  - `harbor/trial/multi_step.py`: `_step_agent_user` returns `[steps.agent].user`, else `[agent].user`. The step's `setup.sh` and agent both run as that user. `None` falls through to `docker exec`'s default, the image's USER.
  - `harbor/models/task/config.py`: `EnvironmentConfig.workdir` "Overrides the container's WORKDIR when set." `harbor/environments/docker/docker.py` runs commands in `cwd or self.task_env_config.workdir`.
  - `harbor/verifier/verifier.py`: `reward.json` wins over `reward.txt`. The test script's stdout and stderr both go to `verifier/test-stdout.txt`.
  - `harbor/agents/oracle.py`: the oracle uploads `steps/<name>/solution/` to `/solution` and runs `solve.sh` directly, which honours its shebang. Without a step solution, it falls back to the task-wide `solution/`.
- **EvoCode README** (f8fcfaa, "Metrics"). The verifier "reports per-test-case results (`CASE_SUMMARY total_cases=… success_count=…`)". Steps are named `round-N`. No other EvoCode file was opened.
- **Contamination.** `case_counts` keeps only lines that carry the marker. The validator writes Harbor logs to `RUNS/logs` and never prints them. It prints only rewards and reasons.

- [ ] **Step 1: Write the failing test for the steps reader**

Create `tests/test_evo_steps.py`:

```python
import json
from pathlib import Path

import pytest

from bench.evo import steps

# The CASE_SUMMARY line exactly as EvoCode's README (f8fcfaa, "Metrics") documents it:
#   CASE_SUMMARY total_cases=… success_count=…
README_LINE = "CASE_SUMMARY total_cases=45 success_count=44"


def timing(start, end):
    return {"started_at": start, "finished_at": end}


def step_result(name, reward=None, exception=None, agent=None):
    """One StepResult as Harbor 0.24.0 dumps it (harbor/models/trial/result.py)."""
    return {
        "step_name": name,
        "agent_result": {"n_input_tokens": 1000, "n_cache_tokens": 800, "n_output_tokens": 50, "cost_usd": 0.01,
                         "model_usage": None, "rollout_details": None, "metadata": None},
        "verifier_result": None if reward is None else {"rewards": {"reward": reward}},
        "exception_info": None if exception is None else {
            "exception_type": exception, "exception_message": "synthetic", "exception_traceback": "synthetic",
            "occurred_at": "2026-10-09T10:30:00Z"},
        "agent_execution": agent,
        "verifier": timing("2026-10-09T10:30:01Z", "2026-10-09T10:31:00Z"),
    }


def make_trial(root: Path, results, trial_exception=None, files=None) -> Path:
    """A multi-step trial dir: result.json plus steps/<name>/{agent,verifier,artifacts} (harbor/trial/multi_step.py)."""
    trial = root / "demo__AbCdEfG"
    trial.mkdir(parents=True)
    for result in results or []:
        for sub in ("agent", "verifier", "artifacts"):
            (trial / "steps" / result["step_name"] / sub).mkdir(parents=True, exist_ok=True)
    for rel, text in (files or {}).items():
        path = trial / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    (trial / "result.json").write_text(json.dumps({
        "id": "00000000-0000-0000-0000-000000000000", "task_name": "demo", "trial_name": trial.name,
        "trial_uri": trial.as_uri(), "task_id": {"path": "/copies/agent/demo"}, "source": None,
        "task_checksum": "x", "config": {}, "agent_info": {"name": "claude-code", "version": "2.1.293"},
        "agent_result": None, "verifier_result": {"rewards": {"reward": 0.5}}, "verifier_environment_mode": None,
        "exception_info": None if trial_exception is None else {
            "exception_type": trial_exception, "exception_message": "synthetic", "exception_traceback": "synthetic",
            "occurred_at": "2026-10-09T11:00:00Z"},
        "started_at": "2026-10-09T09:59:00Z", "finished_at": "2026-10-09T11:00:00Z",
        "environment_setup": None, "agent_setup": None, "agent_execution": None, "verifier": None,
        "step_results": results,
    }, indent=4))
    return trial


def write(step_dir: Path, name: str, text: str) -> None:
    (step_dir / "verifier").mkdir(parents=True, exist_ok=True)
    (step_dir / "verifier" / name).write_text(text)


def test_read_trial_returns_each_step_in_order(tmp_path):
    trial = make_trial(tmp_path, [
        step_result("round-1", 1.0, agent=timing("2026-10-09T10:00:00.250000Z", "2026-10-09T10:20:00.250000Z")),
        step_result("round-2", 0.0, "AgentTimeoutError", agent=timing("2026-10-09T10:21:00Z", "2026-10-09T10:51:00Z")),
    ])
    out = steps.read_trial(trial)
    assert out["trial_exception"] is None
    first, second = out["steps"]
    assert first == {
        "name": "round-1", "index": 1, "reward": 1.0, "exception": None,
        "agent_started": "2026-10-09T10:00:00.250000Z", "agent_finished": "2026-10-09T10:20:00.250000Z",
        "agent_sec": 1200.0,
        "agent_dir": trial / "steps" / "round-1" / "agent",
        "verifier_dir": trial / "steps" / "round-1" / "verifier",
        "artifacts_dir": trial / "steps" / "round-1" / "artifacts",
    }
    assert (second["name"], second["index"], second["reward"], second["exception"], second["agent_sec"]) == (
        "round-2", 2, 0.0, "AgentTimeoutError", 1800.0)


def test_a_step_without_a_verifier_result_falls_back_to_its_reward_file(tmp_path):
    trial = make_trial(tmp_path, [step_result("round-1", None, agent=timing("2026-10-09T10:00:00Z", None))],
                       files={"steps/round-1/verifier/reward.txt": "1\n"})
    (only,) = steps.read_trial(trial)["steps"]
    assert only["reward"] == 1.0 and only["agent_finished"] is None and only["agent_sec"] is None


def test_an_ungraded_step_has_no_reward(tmp_path):
    trial = make_trial(tmp_path, [step_result("round-1", None, "RuntimeError", agent=None)])
    (only,) = steps.read_trial(trial)["steps"]
    assert (only["reward"], only["exception"], only["agent_started"], only["agent_sec"]) == (
        None, "RuntimeError", None, None)


def test_a_chain_that_stopped_early_lists_only_the_steps_harbor_ran(tmp_path):
    trial = make_trial(tmp_path, [step_result("round-1", 0.0, "NonZeroAgentExitCodeError")],
                       trial_exception="CancelledError")
    out = steps.read_trial(trial)
    assert [s["name"] for s in out["steps"]] == ["round-1"]
    assert out["trial_exception"] == "CancelledError"


def test_a_trial_without_step_results_has_no_steps(tmp_path):
    out = steps.read_trial(make_trial(tmp_path, None))
    assert out == {"steps": [], "trial_exception": None}


def test_a_step_name_that_leaves_the_trial_dir_is_refused(tmp_path):
    trial = make_trial(tmp_path, [])
    data = json.loads((trial / "result.json").read_text())
    data["step_results"] = [step_result("../escape", 1.0)]
    (trial / "result.json").write_text(json.dumps(data))
    with pytest.raises(ValueError):
        steps.read_trial(trial)


def test_read_trial_reward_matches_the_reward_files(tmp_path):
    trial = make_trial(tmp_path, [step_result("round-1", 1.0), step_result("round-2", 0.0)],
                       files={"steps/round-1/verifier/reward.txt": "1\n",
                              "steps/round-2/verifier/reward.txt": "0\n"})
    for step in steps.read_trial(trial)["steps"]:
        assert step["reward"] == steps.step_reward(trial / "steps" / step["name"])


def test_step_reward_reads_reward_txt(tmp_path):
    write(tmp_path, "reward.txt", "1\n")
    assert steps.step_reward(tmp_path) == 1.0


def test_step_reward_prefers_reward_json_like_harbor(tmp_path):
    write(tmp_path, "reward.txt", "1\n")
    write(tmp_path, "reward.json", json.dumps({"reward": 0}))
    assert steps.step_reward(tmp_path) == 0.0


@pytest.mark.parametrize("name,text", [("reward.txt", ""), ("reward.txt", "pass\n"), ("reward.txt", "nan\n"),
                                       ("reward.json", "{"), ("reward.json", json.dumps({"score": 1})),
                                       ("reward.json", json.dumps([1]))])
def test_step_reward_is_none_for_an_unusable_file(tmp_path, name, text):
    write(tmp_path, name, text)
    assert steps.step_reward(tmp_path) is None


def test_step_reward_is_none_without_files(tmp_path):
    assert steps.step_reward(tmp_path) is None


def test_case_counts_reads_the_readme_line(tmp_path):
    write(tmp_path, "test-stdout.txt", f"collected 45 items\n{README_LINE}\n")
    assert steps.case_counts(tmp_path) == (44, 45)


def test_case_counts_takes_the_last_summary_line(tmp_path):
    write(tmp_path, "test-stdout.txt",
          "CASE_SUMMARY total_cases=10 success_count=3\nrerun\nCASE_SUMMARY total_cases=12 success_count=12\n")
    assert steps.case_counts(tmp_path) == (12, 12)


def test_case_counts_tolerates_prefixes_colour_and_key_order(tmp_path):
    write(tmp_path, "test-stdout.txt", "[verifier] \x1b[32mCASE_SUMMARY success_count=7, total_cases=9\x1b[0m\n")
    assert steps.case_counts(tmp_path) == (7, 9)


@pytest.mark.parametrize("text", ["no summary here\n", "NO_CASE_SUMMARY total_cases=4 success_count=4\n",
                                  "CASE_SUMMARY total_cases=4\n", "CASE_SUMMARY total_cases=4 success_count=5\n",
                                  "CASE_SUMMARY total_cases=4 success_count=4\nCASE_SUMMARY broken\n"])
def test_case_counts_is_none_without_a_well_formed_last_line(tmp_path, text):
    write(tmp_path, "test-stdout.txt", text)
    assert steps.case_counts(tmp_path) is None


def test_case_counts_is_none_without_test_stdout(tmp_path):
    assert steps.case_counts(tmp_path) is None
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `sh bench/tb4/test.sh -q tests/test_evo_steps.py`
Expected: FAIL. Collection stops with `Interrupted: 1 error during collection` and `1 error`. The error is `ImportError: cannot import name 'steps' from 'bench.evo'`, followed by the path of `bench/evo/__init__.py` in parentheses.

- [ ] **Step 3: Implement the steps reader**

Create `bench/evo/steps.py`:

```python
"""Read a finished Harbor 0.24.0 multi-step trial: per-step reward, exception, timing and output directories.

Harbor writes result.json (TrialResult) with step_results[], one StepResult per step it ran:
step_name, agent_result, verifier_result.rewards, exception_info.exception_type,
agent_execution.{started_at,finished_at} and verifier.{started_at,finished_at}. After each step it moves
the step's outputs to steps/<step_name>/{agent,verifier,artifacts}. A chain that stops early has fewer entries.
Contamination rule: nothing here reads verifier output beyond the reward files and the CASE_SUMMARY counts.
"""
import json
import math
import re
from datetime import datetime
from pathlib import Path

CASE_SUMMARY = re.compile(r"\bCASE_SUMMARY\b")
_CASE_FIELD = re.compile(r"\b(total_cases|success_count)=(\d+)\b")
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if math.isfinite(value) else None


def step_reward(step_dir):
    """The step's reward from steps/<name>/verifier, with Harbor's precedence: reward.json "reward", else reward.txt."""
    verifier = Path(step_dir) / "verifier"
    try:
        if (verifier / "reward.json").is_file():
            data = json.loads((verifier / "reward.json").read_text())
            return _finite(data.get("reward")) if isinstance(data, dict) else None
        if (verifier / "reward.txt").is_file():
            return _finite(float((verifier / "reward.txt").read_text().strip()))
    except (ValueError, OSError):
        return None
    return None


def case_counts(step_dir):
    """(passed, total) from the last CASE_SUMMARY line of verifier/test-stdout.txt, else None.

    EvoCode's README documents the line as `CASE_SUMMARY total_cases=… success_count=…`. Only lines that carry
    the marker are kept; every other line is skipped unread past the marker test.
    """
    path = Path(step_dir) / "verifier" / "test-stdout.txt"
    if not path.is_file():
        return None
    last = None
    with open(path, errors="replace") as handle:
        for line in handle:
            line = _ANSI.sub("", line)
            if CASE_SUMMARY.search(line):
                last = line
    if last is None:
        return None
    fields = dict(_CASE_FIELD.findall(last))
    if set(fields) != {"total_cases", "success_count"}:
        return None
    passed, total = int(fields["success_count"]), int(fields["total_cases"])
    return (passed, total) if passed <= total else None


def _stamp(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _seconds(started, finished):
    if not started or not finished:
        return None
    try:
        return (_stamp(finished) - _stamp(started)).total_seconds()
    except ValueError:
        return None


def _exception_type(record):
    return (record.get("exception_info") or {}).get("exception_type")


def read_trial(trial_dir) -> dict:
    """Every step Harbor ran, in order, from <trial_dir>/result.json and <trial_dir>/steps/<name>/."""
    trial_dir = Path(trial_dir)
    result = json.loads((trial_dir / "result.json").read_text())
    out = []
    for index, step in enumerate(result.get("step_results") or [], start=1):
        name = step["step_name"]
        if not name or "/" in name or "\\" in name or name in (".", ".."):
            raise ValueError(f"unsafe step name in result.json: {name!r}")
        step_dir = trial_dir / "steps" / name
        reward = _finite(((step.get("verifier_result") or {}).get("rewards") or {}).get("reward"))
        if reward is None:
            reward = step_reward(step_dir)
        execution = step.get("agent_execution") or {}
        started, finished = execution.get("started_at"), execution.get("finished_at")
        out.append({"name": name, "index": index, "reward": reward, "exception": _exception_type(step),
                    "agent_started": started, "agent_finished": finished, "agent_sec": _seconds(started, finished),
                    "agent_dir": step_dir / "agent", "verifier_dir": step_dir / "verifier",
                    "artifacts_dir": step_dir / "artifacts"})
    return {"steps": out, "trial_exception": _exception_type(result)}
```

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `sh bench/tb4/test.sh -q tests/test_evo_steps.py`
Expected: `25 passed` (the reference run printed `25 passed in 0.04s`).

- [ ] **Step 5: Run the full suite**

Run: `sh bench/tb4/test.sh -q`
Expected: `931 passed`, 0 failed (906 after Task 5 + 25). If other work has landed first, expect the previous count plus this task's tests, with 0 failures.

- [ ] **Step 6: Commit the steps reader**

```bash
git add bench/evo/steps.py tests/test_evo_steps.py
git commit -m "feat(evo): read per-step reward, timing and CASE_SUMMARY counts from a Harbor multi-step trial"
```

Run: `git show --stat --format=%s HEAD | head -20`
Expected:

```text
feat(evo): read per-step reward, timing and CASE_SUMMARY counts from a Harbor multi-step trial

 bench/evo/steps.py      | 100 +++++++++++++++++++++++++++
 tests/test_evo_steps.py | 175 ++++++++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 275 insertions(+)
```

```text
feat(evo): read per-step reward, timing and CASE_SUMMARY counts from a Harbor multi-step trial

 bench/evo/steps.py      | 100 +++++++++++++++++++++++++++
 tests/test_evo_steps.py | 175 ++++++++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 275 insertions(+)
```

- [ ] **Step 7: Write the failing tests for images and validation**

Create `tests/test_evo_images.py`:

```python
import json
import subprocess

from bench.evo import images


class FakeRun:
    def __init__(self, stdout=""):
        self.calls, self.stdout = [], stdout

    def __call__(self, cmd, **kwargs):
        self.calls.append((list(cmd), kwargs))
        return subprocess.CompletedProcess(cmd, 0, self.stdout, "")


def test_build_uses_the_copy_environment_and_arm64(tmp_path):
    fake, log = FakeRun(), object()
    images.build(tmp_path / "copy", "evo-local/demo:9fcae3e5539d", log, run=fake)
    (cmd, kwargs), = fake.calls
    assert cmd == ["docker", "build", "--platform", "linux/arm64", "-t", "evo-local/demo:9fcae3e5539d",
                   str(tmp_path / "copy" / "environment")]
    assert kwargs == {"check": True, "stdout": log, "stderr": subprocess.STDOUT, "timeout": 3600}


def test_inspect_returns_id_arch_workdir_and_user():
    payload = [{"Id": "sha256:abc", "Architecture": "arm64", "Config": {"WorkingDir": "/app", "User": ""}}]
    fake = FakeRun(json.dumps(payload))
    assert images.inspect("evo-local/demo:9fcae3e5539d", run=fake) == {
        "id": "sha256:abc", "arch": "arm64", "workdir": "/app", "user": "root"}
    assert fake.calls[0][0] == ["docker", "image", "inspect", "evo-local/demo:9fcae3e5539d"]


def test_remove_forces_the_tag_out():
    fake = FakeRun()
    images.remove("evo-local/demo:9fcae3e5539d", run=fake)
    assert fake.calls[0][0] == ["docker", "image", "rm", "-f", "evo-local/demo:9fcae3e5539d"]
```

Create `tests/test_evo_validate.py`. The synthetic task below holds no EvoCode content. Besides the per-round `ok` rule and the walk over the order, these tests pin:
- the `arm_patch` record and the `code` digest in every record;
- one retry of a failed build;
- a new validation when a saved record's `code` differs from `validate.CODE_DIGEST`;
- the planted ledger in `[environment].workdir` when `task.toml` sets it;
- the `user` that each round runs as;
- `python_missing` in the summary and in the CLI output.

```python
import json
import subprocess
from pathlib import Path

from bench.evo import config, patch, validate

TOML = """version = "1.0"
multi_step_reward_strategy = "mean"

[agent]
timeout_sec = 1800.0

[verifier]
timeout_sec = 900.0

[environment]
cpus = 2
memory_mb = 4096

[[steps]]
name = "round-1"

[[steps]]
name = "round-2"
"""
TAG = "evo-local/demo:9fcae3e5539d"


def cp(cmd, code=0, out=""):
    return subprocess.CompletedProcess(cmd, code, out, "")


def write_trial(trial: Path, rewards) -> None:
    """A Harbor multi-step trial; a None reward means the chain never reached that round."""
    trial.mkdir(parents=True)
    results = []
    for i, reward in enumerate(rewards, start=1):
        if reward is None:
            break
        name = f"round-{i}"
        verifier = trial / "steps" / name / "verifier"
        verifier.mkdir(parents=True)
        (verifier / "reward.txt").write_text(f"{reward}\n")
        (verifier / "test-stdout.txt").write_text(
            f"collected 4 items\nCASE_SUMMARY total_cases=4 success_count={4 if reward == 1 else 1}\n")
        results.append({"step_name": name, "verifier_result": {"rewards": {"reward": reward}}, "exception_info": None,
                        "agent_execution": {"started_at": "2026-10-09T10:00:00Z", "finished_at": "2026-10-09T10:01:00Z"}})
    (trial / "result.json").write_text(json.dumps({"trial_name": trial.name, "exception_info": None,
                                                   "step_results": results}))


class FakeHost:
    def __init__(self, rewards=None, arch="arm64", build_ok=True, ids=("sha256:img",)):
        self.calls = []
        self.rewards = rewards or {"oracle": [1, 1], "nop": [0, 0], "planted": [1, 1]}
        self.arch, self.build_ok, self.ids = arch, build_ok, list(ids)

    def __call__(self, cmd, **kwargs):
        cmd = list(cmd)
        self.calls.append(cmd)
        if cmd[:2] == ["docker", "build"]:
            if not self.build_ok:
                raise subprocess.CalledProcessError(1, cmd)
            return cp(cmd)
        if cmd[:3] == ["docker", "image", "inspect"]:
            image_id = self.ids.pop(0) if len(self.ids) > 1 else self.ids[0]
            return cp(cmd, 0, json.dumps([{"Id": image_id, "Architecture": self.arch,
                                           "Config": {"WorkingDir": "/app", "User": ""}}]))
        if cmd[:2] == ["docker", "run"]:
            return cp(cmd, 0, "3.10.12\n")
        if cmd[:2] == ["harbor", "run"]:
            job = cmd[cmd.index("--job-name") + 1]
            write_trial(Path(cmd[cmd.index("-o") + 1]) / job / "demo__AbCdEfG", self.rewards[job.split("__")[0]])
            return cp(cmd)
        return cp(cmd)

    def harbor_runs(self):
        return [(c[c.index("-a") + 1], Path(c[c.index("-p") + 1])) for c in self.calls if c[:2] == ["harbor", "run"]]


def make_task(runs: Path, name="demo") -> Path:
    task = runs / "dataset" / config.DATASET_REVISION / "tasks" / name
    (task / "environment").mkdir(parents=True)
    (task / "environment" / "Dockerfile").write_text("FROM ubuntu:22.04\nRUN apt-get update\n")
    (task / "task.toml").write_text(TOML)
    for step in ("round-1", "round-2"):
        for sub in ("tests", "solution"):
            (task / "steps" / step / sub).mkdir(parents=True)
        (task / "steps" / step / "instruction.md").write_text(f"Synthetic {step} request.\n")
        (task / "steps" / step / "tests" / "test.sh").write_text("#!/bin/bash\necho 1 > /logs/verifier/reward.txt\n")
        (task / "steps" / step / "solution" / "solve.sh").write_text(f"#!/bin/bash\necho {step}\n")
    return task


def test_check_command_runs_one_trial_with_the_evo_multiplier(tmp_path):
    assert validate.check_command(tmp_path / "copy", "oracle", "oracle__demo", tmp_path / "checks") == [
        "harbor", "run", "-p", str(tmp_path / "copy"), "-a", "oracle",
        "--agent-timeout-multiplier", "1.0", "--no-force-build", "-n", "1", "-k", "1", "-r", "0",
        "-o", str(tmp_path / "checks"), "--job-name", "oracle__demo", "-y"]


def test_happy_path_validates_every_round_and_writes_the_record(tmp_path):
    make_task(tmp_path)
    host = FakeHost()
    out = validate.validate_task("demo", runs=tmp_path, run=host)
    assert out["ok"], out["reason"]
    assert (out["rounds"], out["oracle"], out["nop"], out["planted"]) == (2, [1.0, 1.0], [0.0, 0.0], [1.0, 1.0])
    assert (out["image_tag"], out["image_id"], out["arch"], out["workdir"]) == (TAG, "sha256:img", "arm64", "/app")
    assert out["oracle_cases"] == [[4, 4], [4, 4]] and out["python"]["ok"]
    assert out["arm_patch"] == {"changed": [], "artifacts": []}
    assert len(out["copy_digest"]) == 64 and len(out["validation_copy_digest"]) == 64
    assert json.loads((tmp_path / "validation" / "demo.json").read_text()) == out
    copies = tmp_path / "copies"
    assert not list((copies / "agent" / "demo").rglob("solution"))
    assert (copies / "validation" / "demo" / "steps" / "round-2" / "solution" / "solve.sh").is_file()
    assert host.harbor_runs() == [("oracle", copies / "validation" / "demo"), ("nop", copies / "validation" / "demo"),
                                  ("oracle", copies / "planted" / "demo")]
    assert ["docker", "build", "--platform", "linux/arm64", "-t", TAG,
            str(copies / "validation" / "demo" / "environment")] in host.calls


def test_planted_copy_wraps_round_one_only(tmp_path):
    src = make_task(tmp_path)
    dest = tmp_path / "planted"
    digest = validate.planted_copy(src, dest, "/work space/", "round-1")
    solution = dest / "steps" / "round-1" / "solution"
    wrapper = (solution / "solve.sh").read_text()
    assert len(digest) == 64
    assert "mkdir -p '/work space/.claude/harness'" in wrapper
    assert "cp -R /solution/horizon-ledger/. '/work space/.claude/harness'/" in wrapper
    assert "/solution/solve.orig.sh" in wrapper and (solution / "solve.sh").stat().st_mode & 0o111
    assert (solution / "solve.orig.sh").read_text() == "#!/bin/bash\necho round-1\n"
    planted = sorted(p.relative_to(solution / "horizon-ledger").as_posix()
                     for p in (solution / "horizon-ledger").rglob("*") if p.is_file())
    assert planted == sorted(validate.PLANTED_LEDGER)
    assert any(p.startswith("goals/") for p in planted) and any("test" in p for p in planted)
    assert (dest / "steps" / "round-2" / "solution" / "solve.sh").read_text() == "#!/bin/bash\necho round-2\n"
    assert (src / "steps" / "round-1" / "solution" / "solve.sh").read_text() == "#!/bin/bash\necho round-1\n"


def test_a_passing_nop_round_fails_the_task(tmp_path):
    make_task(tmp_path)
    out = validate.validate_task("demo", runs=tmp_path,
                                 run=FakeHost(rewards={"oracle": [1, 1], "nop": [0, 1], "planted": [1, 1]}))
    assert not out["ok"] and "nop rewards [0.0, 1.0]" in out["reason"]


def test_an_oracle_chain_that_stops_early_fails_the_task(tmp_path):
    make_task(tmp_path)
    out = validate.validate_task("demo", runs=tmp_path,
                                 run=FakeHost(rewards={"oracle": [1, None], "nop": [0, 0], "planted": [1, 1]}))
    assert not out["ok"] and out["oracle"] == [1.0, None] and "oracle rewards" in out["reason"]


def test_a_failing_planted_round_fails_the_task(tmp_path):
    make_task(tmp_path)
    out = validate.validate_task("demo", runs=tmp_path,
                                 run=FakeHost(rewards={"oracle": [1, 1], "nop": [0, 0], "planted": [1, 0]}))
    assert not out["ok"] and "planted rewards [1.0, 0.0]" in out["reason"]


def test_a_nop_run_without_any_graded_round_fails_the_task(tmp_path):
    make_task(tmp_path)
    out = validate.validate_task("demo", runs=tmp_path,
                                 run=FakeHost(rewards={"oracle": [1, 1], "nop": [None, None], "planted": [1, 1]}))
    assert not out["ok"] and "nop rewards [None, None]" in out["reason"]


def test_build_failure_stops_before_any_harbor_run(tmp_path):
    make_task(tmp_path)
    host = FakeHost(build_ok=False)
    out = validate.validate_task("demo", runs=tmp_path, run=host)
    assert not out["ok"] and out["reason"].startswith("build failed")
    assert host.harbor_runs() == []


def test_an_amd64_image_stops_before_any_harbor_run(tmp_path):
    make_task(tmp_path)
    host = FakeHost(arch="amd64")
    out = validate.validate_task("demo", runs=tmp_path, run=host)
    assert not out["ok"] and out["reason"] == "architecture amd64, expected arm64"
    assert host.harbor_runs() == []


def test_an_unknown_arch_pattern_is_recorded(tmp_path, monkeypatch):
    make_task(tmp_path)

    def refuse(*args, **kwargs):
        raise patch.UnknownArchPattern("RUN curl -LO https://example.invalid/tool_amd64.deb")

    monkeypatch.setattr(validate.copies, "make_copy", refuse)
    host = FakeHost()
    out = validate.validate_task("demo", runs=tmp_path, run=host)
    assert not out["ok"] and out["reason"].startswith("copy failed: RUN curl")
    assert host.calls == []


def test_an_image_that_changes_during_checks_fails_the_task(tmp_path):
    make_task(tmp_path)
    out = validate.validate_task("demo", runs=tmp_path, run=FakeHost(ids=("sha256:one", "sha256:two")))
    assert not out["ok"] and "image ID changed during checks" in out["reason"]


def test_revalidation_starts_from_clean_copies_and_jobs(tmp_path):
    make_task(tmp_path)
    validate.validate_task("demo", runs=tmp_path, run=FakeHost())
    out = validate.validate_task("demo", runs=tmp_path, run=FakeHost())
    assert out["ok"], out["reason"]


def ok_record(task, ok=True, reason=None):
    return {"task": task, "ok": ok, "reason": reason, "code": validate.CODE_DIGEST, "python": {"ok": True}}


def test_validate_in_order_replaces_a_failed_task_with_the_next_one(tmp_path, monkeypatch):
    (tmp_path / "validation").mkdir()
    (tmp_path / "validation" / "a.json").write_text(json.dumps(ok_record("a")))
    seen = []

    def fake_validate(task, *, runs, run):
        seen.append(task)
        out = ok_record(task, ok=task != "b", reason="nop rewards [1.0]" if task == "b" else None)
        (runs / "validation" / f"{task}.json").write_text(json.dumps(out))
        return out

    monkeypatch.setattr(validate, "validate_task", fake_validate)
    host, lines = FakeHost(), []
    summary = validate.validate_in_order(["a", "b", "c", "d"], 2, runs=tmp_path, run=host, log=lines.append)
    assert seen == ["b", "c"]
    assert summary["selected"] == ["a", "c"] and summary["complete"]
    assert summary["swaps"] == [{"dropped": "b", "reason": "nop rewards [1.0]", "replaced_by": "c"}]
    assert [w["task"] for w in summary["walked"]] == ["a", "b", "c"]
    assert ["docker", "image", "rm", "-f", config.image_tag("b")] in host.calls
    assert any("b" in line and "c" in line for line in lines)
    assert json.loads((tmp_path / "validation" / "summary.json").read_text()) == summary


def test_validate_in_order_reports_an_incomplete_walk(tmp_path, monkeypatch):
    monkeypatch.setattr(validate, "validate_task",
                        lambda task, *, runs, run: ok_record(task, ok=False, reason="build failed"))
    summary = validate.validate_in_order(["a", "b", "c"], 2, runs=tmp_path, run=FakeHost(), log=lambda m: None)
    assert summary["selected"] == [] and not summary["complete"]
    assert summary["swaps"] == [{"dropped": "a", "reason": "build failed", "replaced_by": None},
                                {"dropped": "b", "reason": "build failed", "replaced_by": None}]


def test_main_walks_the_saved_order_under_the_runner_lock(tmp_path, monkeypatch, capsys):
    (tmp_path / "order.json").write_text(json.dumps({"seed": 20261009, "order": ["a", "b"], "arm_orders": {}}))
    got = {}

    def fake_walk(order, n, *, runs, run=None, log=None):
        got.update(order=order, n=n, runs=runs, locked=(runs / "runner.lock").exists())
        return {"selected": order[:n], "swaps": [], "complete": True}

    monkeypatch.setattr(validate, "validate_in_order", fake_walk)
    assert validate.main(["--runs", str(tmp_path), "--n", "2"]) == 0
    assert got == {"order": ["a", "b"], "n": 2, "runs": tmp_path, "locked": True}
    assert json.loads(capsys.readouterr().out) == {"selected": ["a", "b"], "swaps": [], "complete": True,
                                                "python_missing": []}


def test_main_validates_one_named_task(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(validate, "validate_task", lambda task, *, runs, run=None: ok_record(task, ok=False,
                                                                                            reason="x"))
    assert validate.main(["--runs", str(tmp_path), "--task", "walk"]) == 1
    assert json.loads(capsys.readouterr().out) == {"task": "walk", "ok": False, "reason": "x"}


def test_a_selected_task_whose_image_lacks_python_310_is_named_in_the_summary(tmp_path):
    make_task(tmp_path)

    class NoPython(FakeHost):
        def __call__(self, cmd, **kwargs):
            if list(cmd)[:2] == ["docker", "run"]:
                self.calls.append(list(cmd))
                return cp(cmd, 127, "sh: 1: python3: not found\n")
            return super().__call__(cmd, **kwargs)

    summary = validate.validate_in_order(["demo"], 1, runs=tmp_path, run=NoPython(), log=lambda m: None)
    record = json.loads((tmp_path / "validation" / "demo.json").read_text())
    assert record["ok"] is True and record["python"]["ok"] is False
    assert summary["selected"] == ["demo"] and summary["python_missing"] == ["demo"]


def test_a_transient_build_failure_is_retried_once(tmp_path):
    make_task(tmp_path)

    class Flaky(FakeHost):
        builds = 0

        def __call__(self, cmd, **kwargs):
            if list(cmd)[:2] == ["docker", "build"]:
                self.builds += 1
                if self.builds == 1:
                    self.calls.append(list(cmd))
                    raise subprocess.CalledProcessError(1, cmd)
            return super().__call__(cmd, **kwargs)

    host = Flaky()
    out = validate.validate_task("demo", runs=tmp_path, run=host)
    assert out["ok"], out["reason"]
    assert host.builds == 2


def test_a_saved_record_made_by_other_copy_code_is_validated_again(tmp_path, monkeypatch):
    (tmp_path / "validation").mkdir()
    (tmp_path / "validation" / "a.json").write_text(json.dumps(
        {"task": "a", "ok": False, "reason": "copy failed: line 3: bundled amd64 file", "code": "older"}))
    seen = []

    def fake_validate(task, *, runs, run):
        seen.append(task)
        return {"task": task, "ok": True, "reason": None, "code": validate.CODE_DIGEST, "python": {"ok": True}}

    monkeypatch.setattr(validate, "validate_task", fake_validate)
    summary = validate.validate_in_order(["a"], 1, runs=tmp_path, run=FakeHost(), log=lambda m: None)
    assert seen == ["a"] and summary["selected"] == ["a"]


def test_planted_ledger_goes_to_the_task_toml_workdir_when_it_overrides_the_image(tmp_path):
    task = make_task(tmp_path)
    (task / "task.toml").write_text(TOML.replace("memory_mb = 4096", 'memory_mb = 4096\nworkdir = "/srv/app"'))
    out = validate.validate_task("demo", runs=tmp_path, run=FakeHost())
    assert out["ok"], out["reason"]
    assert out["workdir"] == "/srv/app"
    wrapper = (tmp_path / "copies" / "planted" / "demo" / "steps" / "round-1" / "solution" / "solve.sh").read_text()
    assert "mkdir -p /srv/app/.claude/harness" in wrapper


class ImageUser(FakeHost):
    """An image whose configured USER is `user`."""

    def __init__(self, user, **kwargs):
        super().__init__(**kwargs)
        self.user = user

    def __call__(self, cmd, **kwargs):
        if list(cmd)[:3] == ["docker", "image", "inspect"]:
            self.calls.append(list(cmd))
            return cp(cmd, 0, json.dumps([{"Id": "sha256:img", "Architecture": "arm64",
                                           "Config": {"WorkingDir": "/app", "User": self.user}}]))
        return super().__call__(cmd, **kwargs)


def test_the_user_each_step_runs_as_is_recorded(tmp_path):
    # Harbor 0.24.0 runs a step's setup.sh and agent as [steps.agent].user, else [agent].user, else the image USER
    task = make_task(tmp_path)
    step_user = TOML.replace('name = "round-2"\n', 'name = "round-2"\n\n[steps.agent]\nuser = "app"\n')
    (task / "task.toml").write_text(step_user)
    out = validate.validate_task("demo", runs=tmp_path, run=FakeHost())
    assert out["ok"], out["reason"]
    assert out["user"] == ["app", "root"]
    (task / "task.toml").write_text(step_user.replace("timeout_sec = 1800.0\n", 'timeout_sec = 1800.0\nuser = "app"\n'))
    assert validate.validate_task("demo", runs=tmp_path, run=FakeHost())["user"] == ["app"]
    (task / "task.toml").write_text(TOML.replace("timeout_sec = 1800.0\n", "timeout_sec = 1800.0\nuser = 0\n"))
    assert validate.validate_task("demo", runs=tmp_path, run=ImageUser("app"))["user"] == ["root"]


def test_a_task_that_sets_no_user_records_the_image_user(tmp_path):
    make_task(tmp_path)
    for image_user, expected in (("", ["root"]), ("0", ["root"]), ("root", ["root"]), ("1000:1000", ["1000:1000"])):
        assert validate.validate_task("demo", runs=tmp_path, run=ImageUser(image_user))["user"] == expected

def test_a_user_with_a_root_group_suffix_is_root():
    for user in ("0:0", "root:root", "root:0"):
        assert validate.agent_users({"agent": {"user": user}}, "app") == ["root"]
    assert validate.agent_users({"agent": {"user": "1000:0"}}, "") == ["1000:0"]


def test_the_code_digest_covers_every_module_that_makes_or_judges_a_copy():
    import hashlib

    here = Path(validate.__file__).resolve().parent
    assert validate.CODE_FILES == ("copies.py", "images.py", "patch.py", "steps.py", "validate.py")
    assert validate.CODE_DIGEST == hashlib.sha256(b"".join((here / n).read_bytes() for n in validate.CODE_FILES)).hexdigest()
```

- [ ] **Step 8: Run them to make sure they fail**

Run: `sh bench/tb4/test.sh -q tests/test_evo_images.py tests/test_evo_validate.py`
Expected: FAIL. Collection stops with `Interrupted: 2 errors during collection` and `2 errors`. The errors are `ImportError: cannot import name 'images' from 'bench.evo'` and `ImportError: cannot import name 'validate' from 'bench.evo'`, each followed by the path of `bench/evo/__init__.py` in parentheses.

- [ ] **Step 9: Implement images**

Create `bench/evo/images.py`:

```python
"""Build, inspect and remove the arm64 image of an EvoCode task copy (one image per task, shared by its copies)."""
import subprocess
from pathlib import Path

from bench.tb4 import images as tb4_images

PLATFORM = tb4_images.PLATFORM  # linux/arm64


def build_command(copy_dir: Path, tag: str) -> list:
    return ["docker", "build", "--platform", PLATFORM, "-t", tag, str(Path(copy_dir) / "environment")]


def build(copy_dir: Path, tag: str, log, run=subprocess.run) -> None:
    """Build <copy_dir>/environment (the ARM-patched Dockerfile) as `tag`; raises CalledProcessError on failure."""
    run(build_command(copy_dir, tag), check=True, stdout=log, stderr=subprocess.STDOUT, timeout=3600)


def inspect(tag: str, run=subprocess.run) -> dict:
    """{"id", "arch", "workdir", "user"} of a local image (TB4's reader)."""
    return tb4_images.inspect(tag, run)


def remove(tag: str, run=subprocess.run) -> None:
    run(["docker", "image", "rm", "-f", tag], capture_output=True, text=True)
```

- [ ] **Step 10: Implement validation**

Create `bench/evo/validate.py`.

How the planted ledger works:
- It plants a fake ledger with test-like file names in each common language, so no task needs its language detected and no round content is read.
- Every planted test file fails if a verifier collects it. A verifier that picks up `.claude/harness` therefore turns the planted run red.
- Round 1's `solve.sh` plants the ledger in the recorded `workdir`. That is `[environment].workdir` when `task.toml` sets it, else the image's WORKDIR, because Harbor runs the oracle there.
- The setup hook from Task 5 never touches the project, so the ledger stays in place for every later round.

Other rules in this file:
- `docker build` gets a second try before the task fails.
- `CODE_DIGEST` hashes `copies.py`, `images.py`, `patch.py`, `steps.py` and `validate.py` (`validate.CODE_FILES`). A saved record is reused only when its `code` matches.
- `agent_users` gives the users the rounds run as. `python_missing` names the selected tasks without python3 >= 3.10. Neither gates `ok`; the operator stops on them (see the notes after Step 13).

```python
"""Validate EvoCode tasks in the seeded order (spec: Pilot, validation and freeze, step 2).

Per task: make the agent and validation copies, build the arm64 image from the patched Dockerfile, then run
Harbor's oracle, nop and planted-ledger oracle over every round. A task is ok only when every oracle and planted
round scores 1, every graded nop round scores below 1 (round 1 graded), and the image is arm64.
Writes RUNS/validation/<task>.json per task and RUNS/validation/summary.json for the walk over the order.
"""
import argparse
import hashlib
import json
import shlex
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from bench.evo import config, copies, images, patch, steps
from bench.tb4 import copies as tb4_copies
from bench.tb4 import images as tb4_images
from bench.tb4 import run as tb4_run

# The code that makes copies and judges them; a saved record made by other code is validated again.
CODE_FILES = ("copies.py", "images.py", "patch.py", "steps.py", "validate.py")
CODE_DIGEST = hashlib.sha256(b"".join((Path(__file__).resolve().parent / name).read_bytes()
                                      for name in CODE_FILES)).hexdigest()
CHECKS = (("oracle", "oracle", "validation"), ("nop", "nop", "validation"), ("planted", "oracle", "planted"))
PLANTED_SLUG = "evo-planted-check"
_COLLECTED = "a planted Horizon ledger file was collected by the verifier"
# A fake ledger with test-like names in each common language; each file fails loudly if a verifier collects it.
PLANTED_LEDGER = {
    f"goals/{PLANTED_SLUG}.json": json.dumps({"slug": PLANTED_SLUG, "goal": "Planted ledger for task validation",
                                              "status": "active", "turns_used": 3, "max_turns": 200}, indent=2) + "\n",
    f"gates/{PLANTED_SLUG}/request.md": "# Request\n\nPlanted request text for task validation.\n",
    f"gates/{PLANTED_SLUG}/test_planted_gate.py": f'def test_planted_gate():\n    raise AssertionError("{_COLLECTED}")\n',
    f"gates/{PLANTED_SLUG}/planted_gate_test.go": ('package planted\n\nimport "testing"\n\n'
                                                   f'func TestPlantedGate(t *testing.T) {{\n\tt.Fatal("{_COLLECTED}")\n}}\n'),
    f"gates/{PLANTED_SLUG}/planted_gate.test.js": f'test("planted gate", () => {{\n  throw new Error("{_COLLECTED}");\n}});\n',
    f"gates/{PLANTED_SLUG}/planted_gate.test.ts": f'test("planted gate", () => {{\n  throw new Error("{_COLLECTED}");\n}});\n',
    f"gates/{PLANTED_SLUG}/test_planted_gate.sh": f'#!/bin/sh\necho "{_COLLECTED}" >&2\nexit 1\n',
}
WRAPPER = """#!/bin/bash
# Planted-ledger check: put a fake Horizon ledger in the project as the agent user, then run the real solution.
set -euo pipefail
mkdir -p {target}
cp -R /solution/horizon-ledger/. {target}/
if [ -x /solution/solve.orig.sh ]; then exec /solution/solve.orig.sh "$@"; fi
exec bash /solution/solve.orig.sh "$@"
"""


def tasks_root(runs: Path) -> Path:
    return Path(runs) / "dataset" / config.DATASET_REVISION / "tasks"


def copy_dir(runs: Path, kind: str, task: str) -> Path:
    return Path(runs) / "copies" / kind / task


def step_names(task_dir: Path) -> list:
    return [step["name"] for step in tomllib.loads((Path(task_dir) / "task.toml").read_text()).get("steps") or []]


def check_command(copy: Path, agent: str, job_name: str, jobs_dir: Path) -> list:
    return ["harbor", "run", "-p", str(copy), "-a", agent,
            "--agent-timeout-multiplier", str(config.AGENT_TIMEOUT_MULTIPLIER),
            "--no-force-build", "-n", "1", "-k", "1", "-r", "0",
            "-o", str(jobs_dir), "--job-name", job_name, "-y"]


def planted_copy(validation_copy: Path, dest: Path, workdir: str, first_step: str) -> str:
    """Copy the validation copy and wrap round 1's solve.sh so it plants PLANTED_LEDGER first."""
    if dest.exists():
        raise FileExistsError(dest)
    shutil.copytree(validation_copy, dest, symlinks=True)
    solution = dest / "steps" / first_step / "solution"
    if not (solution / "solve.sh").is_file():
        solution = dest / "solution"  # a task-wide solution runs for every step
    solve = solution / "solve.sh"
    if not solve.is_file():
        shutil.rmtree(dest)
        raise FileNotFoundError(f"no solve.sh for {first_step}")
    solve.rename(solution / "solve.orig.sh")
    for rel, text in PLANTED_LEDGER.items():
        path = solution / "horizon-ledger" / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
    solve.write_text(WRAPPER.format(target=shlex.quote(workdir.rstrip("/") + "/.claude/harness")))
    solve.chmod(0o755)
    return tb4_copies.tree_digest(dest)


def round_results(job_dir: Path, names: list) -> tuple:
    """Per-round rewards and [passed, total] case counts of a check job; None where a round has none."""
    trial_dir, _ = tb4_run.job_result(job_dir)
    if trial_dir is None:
        return [None] * len(names), [None] * len(names)
    found = {step["name"]: step for step in steps.read_trial(trial_dir)["steps"]}
    rewards = [found[name]["reward"] if name in found else None for name in names]
    cases = [steps.case_counts(trial_dir / "steps" / name) if name in found else None for name in names]
    return rewards, [list(c) if c else None for c in cases]


def failures(out: dict) -> list:
    rounds, problems = out["rounds"], []
    for kind in ("oracle", "planted"):
        rewards = out[kind]
        if len(rewards) != rounds or any(r is None or r < 1.0 for r in rewards):
            problems.append(f"{kind} rewards {rewards}, expected 1.0 on all {rounds} rounds")
    nop = out["nop"]
    if not nop or nop[0] is None or any(r is not None and r >= 1.0 for r in nop):
        problems.append(f"nop rewards {nop}, expected below 1.0 with round 1 graded")
    return problems


def _reset(task: str, runs: Path) -> None:
    for path in (*(copy_dir(runs, kind, task) for kind in ("agent", "validation", "planted")),
                 *(Path(runs) / "checks" / f"{name}__{task}" for name, _, _ in CHECKS)):
        if path.exists():
            shutil.rmtree(path)


def _finish(out: dict, problems: list, runs: Path) -> dict:
    out["ok"], out["reason"] = not problems, "; ".join(problems) or None
    saved = Path(runs) / "validation" / f"{out['task']}.json"
    saved.parent.mkdir(parents=True, exist_ok=True)
    saved.write_text(json.dumps(out, indent=2) + "\n")
    return out


def _user(value) -> str:
    text = "" if value is None else str(value)
    # a user:group form names root by its user part, e.g. "0:0" or "root:root"
    return "root" if text.split(":", 1)[0] in ("", "0", "root") else text


def agent_users(task_toml: dict, image_user) -> list:
    """The sorted distinct users Harbor 0.24.0 runs each step's setup.sh and agent as: [steps.agent].user, else
    [agent].user, else the image's USER. "", "0" and "root" are all root."""
    default = (task_toml.get("agent") or {}).get("user")
    users = set()
    for step in task_toml.get("steps") or [{}]:
        user = (step.get("agent") or {}).get("user")
        users.add(_user(user if user is not None else default if default is not None else image_user))
    return sorted(users)


def validate_task(task: str, *, runs: Path, run=subprocess.run) -> dict:
    runs = Path(runs)
    tag = config.image_tag(task)
    validation, agent, planted = (copy_dir(runs, kind, task) for kind in ("validation", "agent", "planted"))
    checks, logs = runs / "checks", runs / "logs"
    out = {"task": task, "copy_digest": None, "validation_copy_digest": None, "image_tag": tag, "image_id": None,
           "arch": None, "workdir": None, "user": None, "python": None, "rounds": 0, "oracle": [], "nop": [],
           "planted": [], "oracle_cases": [], "ok": False, "reason": None, "code": CODE_DIGEST}
    _reset(task, runs)
    for path in (validation.parent, agent.parent, planted.parent, checks, logs):
        path.mkdir(parents=True, exist_ok=True)
    src = tasks_root(runs) / task
    try:
        out["validation_copy_digest"] = copies.make_copy(src, validation, kind="validation", image_tag=tag)
        out["copy_digest"] = copies.make_copy(src, agent, kind="agent", image_tag=tag)
        out["arm_patch"] = json.loads((validation / copies.ARM_PATCH_FILE).read_text())
    except (patch.UnknownArchPattern, ValueError, OSError) as error:
        return _finish(out, [f"copy failed: {error}"], runs)
    names = step_names(validation)
    out["rounds"] = len(names)
    if not names:
        return _finish(out, ["task.toml has no [[steps]]"], runs)
    with open(logs / f"build__{task}.log", "w") as handle:
        for attempt in (1, 2):  # a network hiccup during apt or a download is not a property of the task
            try:
                images.build(validation, tag, handle, run)
                break
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
                if attempt == 2:
                    return _finish(out, [f"build failed twice: {error}"], runs)
    image = images.inspect(tag, run)
    task_toml = tomllib.loads((validation / "task.toml").read_text())
    environment = task_toml.get("environment") or {}
    # Harbor runs the agent, the hooks and the oracle in [environment].workdir when set, else the image WORKDIR
    out.update(image_id=image["id"], arch=image["arch"], workdir=environment.get("workdir") or image["workdir"],
               user=agent_users(task_toml, image["user"]))
    if image["arch"] != "arm64":
        return _finish(out, [f"architecture {image['arch']}, expected arm64"], runs)
    ok, text = tb4_images.python_ok(tag, run)
    out["python"] = {"ok": ok, "version": text.strip()[-200:]}
    try:
        planted_copy(validation, planted, out["workdir"], names[0])
    except OSError as error:
        return _finish(out, [f"planted copy failed: {error}"], runs)
    copy_for = {"validation": validation, "planted": planted}
    for name, agent_name, kind in CHECKS:
        job = f"{name}__{task}"
        with open(logs / f"{job}.log", "w") as handle:
            run(check_command(copy_for[kind], agent_name, job, checks), stdout=handle, stderr=subprocess.STDOUT)
        rewards, cases = round_results(checks / job, names)
        out[name] = rewards
        if name == "oracle":
            out["oracle_cases"] = cases
    problems = failures(out)
    if images.inspect(tag, run)["id"] != out["image_id"]:
        problems.append("image ID changed during checks")
    return _finish(out, problems, runs)


def validate_in_order(order, n: int, *, runs: Path, run=subprocess.run, log=print) -> dict:
    """Walk the seeded order until n tasks are ok; a failed planned task is replaced by the next ok one."""
    runs = Path(runs)
    (runs / "validation").mkdir(parents=True, exist_ok=True)
    selected, walked, records = [], [], {}
    for task in order:
        if len(selected) == n:
            break
        saved = runs / "validation" / f"{task}.json"
        result = json.loads(saved.read_text()) if saved.exists() else None
        if result is None or result.get("code") != CODE_DIGEST:
            log(f"validating {task}")
            result = validate_task(task, runs=runs, run=run)
            if not result["ok"]:
                images.remove(config.image_tag(task), run)
        records[task] = result
        walked.append({"task": task, "ok": result["ok"], "reason": result["reason"]})
        if result["ok"]:
            selected.append(task)
    planned = list(order)[:n]
    replacements = [task for task in selected if task not in planned]
    dropped = [w for w in walked if not w["ok"] and w["task"] in planned]
    swaps = [{"dropped": w["task"], "reason": w["reason"],
              "replaced_by": replacements[i] if i < len(replacements) else None} for i, w in enumerate(dropped)]
    for swap in swaps:
        log(f"swap: {swap['dropped']} failed validation ({swap['reason']}); replaced by {swap['replaced_by']}")
    summary = {"n": n, "order": list(order), "selected": selected, "walked": walked, "swaps": swaps,
               "complete": len(selected) == n,
               "python_missing": [t for t in selected if (records[t].get("python") or {}).get("ok") is not True]}
    (runs / "validation" / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, default=config.RUNS)
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--n", type=int, help="walk RUNS/order.json until N tasks are ok")
    target.add_argument("--task", help="validate one task now (the walkthrough task for the dry run)")
    args = parser.parse_args(argv)
    with tb4_run.hold_lock(args.runs / "runner.lock"):
        if args.task:
            result = validate_task(args.task, runs=args.runs)
            print(json.dumps({"task": result["task"], "ok": result["ok"], "reason": result["reason"]}))
            return 0 if result["ok"] else 1
        order = json.loads((args.runs / "order.json").read_text())["order"]
        summary = validate_in_order(order, args.n, runs=args.runs)
    print(json.dumps({"selected": summary["selected"], "swaps": summary["swaps"], "complete": summary["complete"],
                      "python_missing": summary.get("python_missing", [])}))
    return 0 if summary["complete"] else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 11: Run the tests to make sure they pass**

Run: `sh bench/tb4/test.sh -q tests/test_evo_images.py tests/test_evo_validate.py`
Expected: `27 passed` (3 + 24).

Then run the Task 6 files with their TB4 neighbours, to confirm TB4 is unchanged:

Run: `sh bench/tb4/test.sh -q tests/test_evo_steps.py tests/test_evo_images.py tests/test_evo_validate.py tests/test_tb4_images.py tests/test_tb4_validate.py tests/test_tb4_run.py tests/test_tb4_copies.py`
Expected: `107 passed`. The count assumes TB4's test files are as at 3297c86. If the count differs, nothing may fail.

Run: `python3 -m bench.evo.validate --help`
Expected: exit 0. The usage line starts with `usage:` and shows `[-h] [--runs RUNS] (--n N | --task TASK)`; on Python 3.14 it wraps after `(--n N |`. The module docstring and the options `--runs RUNS`, `--n N` and `--task TASK` follow. No Docker or Harbor call is made.

- [ ] **Step 12: Run the full suite**

Run: `sh bench/tb4/test.sh -q`
Expected: `958 passed`, 0 failed (931 after Step 5 + 27).

- [ ] **Step 13: Commit**

```bash
git add bench/evo/images.py bench/evo/validate.py tests/test_evo_images.py tests/test_evo_validate.py
git commit -m "feat(evo): build arm64 task images and validate oracle, nop and planted-ledger runs per round"
```

Run: `git show --stat --format=%s HEAD | head -20`
Expected:

```text
feat(evo): build arm64 task images and validate oracle, nop and planted-ledger runs per round

 bench/evo/images.py        |  25 +++
 bench/evo/validate.py      | 260 +++++++++++++++++++++++++++++++
 tests/test_evo_images.py   |  36 +++++
 tests/test_evo_validate.py | 375 +++++++++++++++++++++++++++++++++++++++++++++
 4 files changed, 696 insertions(+)
```

```text
feat(evo): build arm64 task images and validate oracle, nop and planted-ledger runs per round

 bench/evo/images.py        |  25 ++++
 bench/evo/validate.py      | 258 ++++++++++++++++++++++++++++++++
 tests/test_evo_images.py   |  36 +++++
 tests/test_evo_validate.py | 363 +++++++++++++++++++++++++++++++++++++++++++++
 4 files changed, 682 insertions(+)
```

**Operator use (in Task 12, Steps 9-10; listed here so the outputs are defined):**

1. Run `python3 -m bench.evo.validate --n 10` from the worktree.
   - It needs Docker, Harbor and `RUNS/order.json`. It does not use the subscription.
   - It prints one JSON line with the keys `selected` (10 task names in seeded order), `swaps`, `complete` (`true`) and `python_missing`, and exits 0.
   - If `complete` is false, the order ran out of ok tasks. Stop and report to the user before the freeze.
   - For every swap, `RUNS/validation/<task>.json` `reason` holds the cause. A reason that starts `copy failed: line` means Task 4's patcher refused a Dockerfile line. Stop and ask the user (Task 12, Step 9).
   - A relaunch resumes the walk. It reuses each saved record whose `code` equals `validate.CODE_DIGEST` and validates the others again.
   - If `python_missing` is not empty, Horizon's CLI cannot run in those containers. Stop and ask the user, because the spec's four validation criteria do not cover this.
2. For every selected task, check `RUNS/validation/<task>.json`:
   - **`oracle_cases`** should have no `null` entry. A `null` means the CASE_SUMMARY line was not found in `verifier/test-stdout.txt`.
     - Check only with a marker count: `grep -c CASE_SUMMARY "$RUNS"/checks/oracle__<task>/*/steps/<round>/verifier/test-stdout.txt`. Never print the file.
     - Report the null to the user, because the case score depends on it.
   - **`python.ok`** should be true. It is the fact behind `python_missing`.
   - **`user`** should be `["root"]`. The spec's setup hook assumes root; another user leaves root-owned `/tmp` entries and has a different HOME. Stop and ask the user if it is not (Task 12, Step 9).
   - **`arm_patch`** lists the Dockerfile lines the ARM patch changed and every artifact it downloaded, with its URL and sha256.
3. For the dry-run task, run `python3 -m bench.evo.validate --task theme_d11_w2_scientific_numerical_brownfield_modification__not_1234`.
   - It prints `{"task": "theme_d11_w2_scientific_numerical_brownfield_modification__not_1234", "ok": true, "reason": null}` and exits 0.
   - It exits 1 with a reason when validation fails.
   - Check its record as in item 2: `python.ok` true and `user` `["root"]`.

### Task 7: Harbor behaviour check and write canary

**Deviation:** five changes from the skeleton, all checked against Harbor 0.24.0's source.

1. **How the verifier timeout is checked.** Harbor does not record the resolved per-step verifier timeout anywhere:
   - `result.json` and `config.json` hold the `TrialConfig`, which keeps only `verifier.override_timeout_sec` and `max_timeout_sec`.
   - `lock.json` holds only the multipliers.
   - The value is resolved at run time in `Trial._step_verifier_timeout_sec`.
   - Each `[[steps]]` entry's `verifier.timeout_sec` defaults to `600.0`, so a step never inherits the task-level value.

   The check is therefore behavioural. `harborcheck vtimeout` runs a one-round variant with the oracle agent, `[verifier] timeout_sec = 20` and a test that sleeps 45 s first. It passes only when round 1 ends with `VerifierTimeoutError` "Verifier execution timed out after 20.0 seconds". With Harbor's 600-s step default, the same round would pass after 45 s.
2. **The timeout probe uses 60 s, not 20 s.** Round 1's agent limit is 60 s, so the model's first turn at effort `high` can start `sleep 600` before the limit. The probe then shows what happens to a `claude` process, and its tool child, that is still running.
3. **Where collected artifacts land (binding for Tasks 6 and 9).** Whatever the main container writes to `/logs/artifacts/...` during step `S` ends up at `<trial>/steps/S/artifacts/logs/artifacts/...`. Two pieces of Harbor code cause this:
   - The bind-mount source is `<trial>/artifacts/logs/artifacts` (`Trial._main_artifacts_mount_dir`, `Trial._agent_env_mounts`).
   - `MultiStepTrial._archive_step_outputs` moves it into `steps/S/artifacts/` after each step.

   So the collect hook's ledger copy is at `steps/<round>/artifacts/logs/artifacts/<round>/harness/`. `harborcheck.collected_root(trial_dir, step)` encodes this path.
4. **Arm names in job names.** Oracle runs use the arm `oracle` in their job names, so `config.job_name` must accept any arm without `__` or `/`, not only the two `ARMS`.
5. **One job per check, plus an extra probe hook.**
   - The check runs as its own Harbor job, first with `--agent oracle` (no usage, deterministic, to prove the dummy and the assertions) and then with `claude-code`.
   - `vtimeout`, `probe` and `canary` are separate jobs.
   - The dummy declares a task-level `[[verifier.collect]]` probe. Harbor runs task-level hooks before step-level ones (`Trial._collect_hooks_for`), so this probe records the process list just before `copies.COLLECT_COMMAND` stops every `claude` process and its descendants.

**Files:**
- Create: `bench/evo/harborcheck.py`
- Create: `bench/evo/dummy/task.toml`, `bench/evo/dummy/environment/Dockerfile`, `bench/evo/dummy/environment/probe.sh`
- Create: `bench/evo/dummy/steps/round-1/instruction.md`, `bench/evo/dummy/steps/round-1/tests/test.sh`, `bench/evo/dummy/steps/round-1/tests/round1-marker.txt`, `bench/evo/dummy/steps/round-1/solution/solve.sh`
- Create: `bench/evo/dummy/steps/round-2/instruction.md`, `bench/evo/dummy/steps/round-2/tests/test.sh`, `bench/evo/dummy/steps/round-2/solution/solve.sh`
- Test: `tests/test_evo_harborcheck.py`

**Interfaces:**
- Consumes:
  - From Task 1, `bench.evo.config`:
    - `RUNS`, `MODEL`, `EFFORT`, `CLAUDE_CODE_VERSION`, `MAX_OUTPUT_TOKENS`, `AGENT_TIMEOUT_MULTIPLIER`, `AGENT_SETUP_TIMEOUT_MULTIPLIER` and `HARBOR_VERSION`;
    - `image_tag(task: str) -> str`, called with `"dummy"`;
    - `job_name(phase, task, arm, attempt) -> str`, which must accept the arm `oracle`;
    - `prompt_text(arm: str, minutes: int) -> str`.
  - From Task 5, `bench.evo.copies`:
    - `SETUP_SH`;
    - `COLLECT_COMMAND(round_name: str) -> str`, which stops every `claude` process and its descendants and copies `$PWD/.claude/harness` to `/logs/artifacts/<round>/harness/`, the path Task 9 reads;
    - `make_copy(src_task_dir: Path, dest_dir: Path, *, kind: str, image_tag: str | None) -> str`, always called here with the dummy's tag. `step_toml_overrides` must keep two things in the dummy's `task.toml`: the task-level `[[verifier.collect]]` and a step's inline `agent = { timeout_sec = 60.0 }`.
  - From Task 6, `bench.evo.images`: `build(copy_dir, tag, log, run)` and `inspect(tag, run) -> {"id", "arch", "workdir", "user"}`; this task reads `id` and `arch`.
  - In the operator steps only (run by Task 12): the Horizon bundle at `RUNS/bundle/horizon`, which Task 12 Step 5 builds. Step 13's canary passes it as `--skill`.
  - From TB4, unchanged:
    - `bench.tb4.run`: `read_token`, `agent_env`, `running_trials`, `RunnerBusy`, `job_result`, `cleanup_job` and `hold_lock`;
    - `bench.tb4.stream`: `read_events`, `tool_uses`, `tool_results` and `terminal_api_error`;
    - `bench.tb4.copies.tree_digest`, used in the test only.
- Produces (`bench/evo/harborcheck.py`):
  - Constants:
    - `DUMMY`, `STEPS = ("round-1", "round-2")`, `VARIANTS = ("check", "vtimeout", "probe", "canary")` and `AGENTS = ("oracle", "claude-code")`;
    - the probe names: `BEFORE`, `PLANTED`, `PROBE_FILES`, `SENTINEL`, `IMAGE_FILE`, `LEDGER_FILES`, `MEMORY_FILE`, `TESTS_MARKER`, `VERIFIER_MARKER`, `DECOY` and `SLEEPER`;
    - `CANARY_REL`, `SLEEP_INSTRUCTION` and `CANARY_INSTRUCTION`.
  - Building a task:
    - `dummy_toml(*, steps=STEPS, verifier_timeout_sec=120.0, round1_agent_timeout_sec=None) -> str`. The committed `bench/evo/dummy/task.toml` is exactly its default output.
    - `build_variant(dest: Path, variant: str) -> Path`.
    - `prepare(work: Path, variant: str, kind: str, tag: str) -> Path`. It writes `work/source` (the variant) and `work/task` (the `make_copy` output).
  - Running a job:
    - `check_command(task_dir: Path, agent: str, job_name: str, jobs_dir: Path, *, prompt=None, skill=None) -> list`.
    - `run_job(variant, agent, job_name, runs, *, token=None, skill=None, run=subprocess.run, log=print) -> tuple[dict, Path | None, dict | None]`. It returns `(meta, trial_dir, result)`.
  - Reading a finished trial:
    - `collected_root(trial_dir, step) -> Path` and `probe_dir(trial_dir, step, label) -> Path`.
    - `check_findings(trial_dir, result) -> {"ok", "failures", "facts"}`.
    - `verifier_timeout_findings(trial_dir, result, expected_sec=20.0) -> {"ok", "failures", "facts"}`.
    - `timeout_probe_findings(trial_dir, result) -> {"ok": None, "failures": [], "recorded": {...}}`.
    - `canary_findings(trial_dir, result) -> {"ok", "failures", "facts"}`.
  - The live runs, each returning the saved findings dict:
    - `run_check(runs, *, agent="claude-code", attempt=1, token=None, run=subprocess.run, log=print) -> dict`;
    - `verifier_timeout_check(runs, *, attempt=1, run=subprocess.run, log=print) -> dict`;
    - `timeout_probe(runs, token, *, attempt=1, run=subprocess.run, log=print) -> dict`;
    - `canary(runs, token, *, attempt=1, skill=None, run=subprocess.run, log=print) -> dict`.
  - The CLI, `main(argv=None) -> int`: `python3 -m bench.evo.harborcheck check|vtimeout|probe|canary [--agent oracle|claude-code] [--attempt N] [--skill DIR] [--runs DIR]`. It holds `RUNS/runner.lock`. It exits 1 only when `ok` is false.
  - On disk:
    - `RUNS/checks/<job>/` holds `source/`, `task/`, `build.log`, `prompt.md` (claude-code runs only), `harbor.log` and `findings.json`, which is `meta` merged with the findings.
    - The Harbor job is at `RUNS/jobs/<job>/`.
    - Job names are `check__dummy__oracle__aN`, `check__dummy__control__aN`, `check__dummy-vtimeout__oracle__aN`, `check__dummy-probe__control__aN` and `canary__dummy__{control|horizon}__aN`.
- Produces (`bench/evo/dummy/`): the two-round dummy task. Its image is `FROM ubuntu:22.04` plus `probe.sh` and installs nothing (no `apt-get`), so a build needs no network.

- [ ] **Step 1: Write the failing test** — create `tests/test_evo_harborcheck.py`:

```python
import json
import subprocess
import tomllib
from pathlib import Path

import pytest

from bench.evo import config, copies
from bench.evo import harborcheck as hc
from bench.tb4 import run as tb4run
from bench.tb4.copies import tree_digest

FIRST, SECOND = hc.STEPS
QUIET = {"log": lambda message: None}


def cp(cmd, code=0, out=""):
    return subprocess.CompletedProcess(cmd, code, out, "")


# ---------------------------------------------------------------- the dummy task


def test_dummy_task_toml_is_the_generated_default():
    text = (hc.DUMMY / "task.toml").read_text()
    assert text == hc.dummy_toml()
    data = tomllib.loads(text)
    assert [s["name"] for s in data["steps"]] == list(hc.STEPS)
    assert data["multi_step_reward_strategy"] == "mean"
    assert data["agent"]["timeout_sec"] == 1800.0
    assert data["verifier"]["timeout_sec"] == hc.VERIFIER_TIMEOUT_SEC
    assert data["verifier"]["collect"] == [{"command": hc.BEFORE_COLLECT_COMMAND, "timeout_sec": 60.0}]


def test_dummy_has_every_step_file_and_an_arch_neutral_image():
    for name in hc.STEPS:
        for rel in ("instruction.md", "tests/test.sh", "solution/solve.sh"):
            assert (hc.DUMMY / "steps" / name / rel).is_file(), (name, rel)
    assert (hc.DUMMY / "steps" / FIRST / "tests" / hc.TESTS_MARKER).is_file()
    dockerfile = (hc.DUMMY / "environment" / "Dockerfile").read_text()
    assert dockerfile.startswith("FROM ubuntu:22.04\n")
    assert "amd64" not in dockerfile and "x86_64" not in dockerfile
    assert "COPY probe.sh /opt/evo-check/probe.sh" in dockerfile and hc.IMAGE_FILE in dockerfile
    assert "apt-get" not in dockerfile  # no network step: probe.sh needs only what ubuntu:22.04 ships


def test_instructions_and_solutions_run_the_same_probe_commands():
    for name, command in ((FIRST, "bash /opt/evo-check/probe.sh plant"),
                          (SECOND, f"bash /opt/evo-check/probe.sh record {SECOND}")):
        step = hc.DUMMY / "steps" / name
        assert command in (step / "instruction.md").read_text()
        assert command in (step / "solution" / "solve.sh").read_text()
    assert "bash /opt/evo-check/probe.sh plant" in hc.SLEEP_INSTRUCTION and hc.SLEEPER in hc.SLEEP_INSTRUCTION
    assert hc.CANARY_REL in hc.CANARY_INSTRUCTION and "Write tool" in hc.CANARY_INSTRUCTION


def test_every_dummy_script_parses():
    scripts = sorted(hc.DUMMY.rglob("*.sh"))
    assert len(scripts) == 5
    for script in scripts:
        assert subprocess.run(["bash", "-n", str(script)]).returncode == 0, script


def test_probe_plants_and_records_the_files_the_findings_read(tmp_path):
    root, home, project = tmp_path / "root", tmp_path / "home", tmp_path / "project"
    for d in (root / "tmp", root / "tests", root / "logs" / "verifier", home, project):
        d.mkdir(parents=True)
    (root / "tests" / hc.TESTS_MARKER).write_text("x\n")
    env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": str(home), "EVO_CHECK_ROOT": str(root),
           "EVO_CHECK_DECOY": "0"}
    probe = hc.DUMMY / "environment" / "probe.sh"
    assert subprocess.run(["bash", str(probe), "plant"], cwd=project, env=env).returncode == 0
    assert subprocess.run(["bash", str(probe), "record", "later"], cwd=project, env=env).returncode == 0
    out = root / "logs" / "artifacts" / "probe"
    assert sorted(p.name for p in out.iterdir()) == sorted([FIRST, hc.PLANTED, "later"])
    for label in (FIRST, hc.PLANTED, "later"):
        assert sorted(p.name for p in (out / label).iterdir()) == sorted(hc.PROBE_FILES)
    planted = out / hc.PLANTED
    assert hc.SENTINEL in (planted / "tmp.txt").read_text().splitlines()
    assert hc.SENTINEL not in (out / FIRST / "tmp.txt").read_text().splitlines()
    assert (planted / "ledger.txt").read_text().splitlines() == sorted(hc.LEDGER_FILES)
    assert hc.MEMORY_FILE in (planted / "dot-claude.txt").read_text().splitlines()
    assert hc.TESTS_MARKER in (planted / "tests.txt").read_text().splitlines()
    assert (home / ".claude" / "projects" / "-app" / "memory" / "check.md").is_file()
    assert subprocess.run(["bash", str(probe), "bogus"], cwd=project, env=env,
                          capture_output=True).returncode == 2


# ---------------------------------------------------------------- variants and copies


@pytest.mark.parametrize("variant, steps, verifier_timeout, agent_timeout", [
    ("check", hc.STEPS, 120.0, None),
    ("vtimeout", hc.STEPS[:1], 20.0, None),
    ("probe", hc.STEPS, 120.0, 60.0),
    ("canary", hc.STEPS[:1], 120.0, None),
])
def test_variants(tmp_path, variant, steps, verifier_timeout, agent_timeout):
    dest = hc.build_variant(tmp_path / "v", variant)
    data = tomllib.loads((dest / "task.toml").read_text())
    assert [s["name"] for s in data["steps"]] == list(steps)
    assert sorted(p.name for p in (dest / "steps").iterdir()) == sorted(steps)
    assert data["verifier"]["timeout_sec"] == verifier_timeout
    assert data["steps"][0].get("agent", {}).get("timeout_sec") == agent_timeout
    instruction = (dest / "steps" / FIRST / "instruction.md").read_text()
    assert (instruction == hc.SLEEP_INSTRUCTION) == (variant == "probe")
    assert (instruction == hc.CANARY_INSTRUCTION) == (variant == "canary")
    assert (dest / "steps" / FIRST / "tests" / "sleep_sec").is_file() == (variant == "vtimeout")


def test_variant_refuses_unknown_names_and_existing_destinations(tmp_path):
    with pytest.raises(ValueError):
        hc.build_variant(tmp_path / "x", "nope")
    hc.build_variant(tmp_path / "v", "check")
    with pytest.raises(FileExistsError):
        hc.build_variant(tmp_path / "v", "check")


@pytest.mark.parametrize("variant", hc.VARIANTS)
@pytest.mark.parametrize("kind", ("agent", "validation"))
def test_prepared_copies_carry_the_main_run_hooks(tmp_path, variant, kind):
    before = tree_digest(hc.DUMMY)
    task = hc.prepare(tmp_path / "work", variant, kind, "evo-local/dummy:test")
    assert tree_digest(hc.DUMMY) == before
    data = tomllib.loads((task / "task.toml").read_text())
    assert data["environment"]["docker_image"] == "evo-local/dummy:test"
    assert data["verifier"]["collect"][0]["command"] == hc.BEFORE_COLLECT_COMMAND
    for step in data["steps"]:
        assert step["verifier"]["timeout_sec"] == data["verifier"]["timeout_sec"]
        assert any(c["command"] == copies.COLLECT_COMMAND(step["name"]) for c in step["verifier"]["collect"])
        assert (task / "steps" / step["name"] / "workdir" / "setup.sh").read_text() == copies.SETUP_SH
        assert (task / "steps" / step["name"] / "solution" / "solve.sh").is_file() == (kind == "validation")
    if variant == "probe":
        assert data["steps"][0]["agent"]["timeout_sec"] == hc.PROBE_AGENT_TIMEOUT_SEC


# ---------------------------------------------------------------- harbor commands


def test_claude_code_check_uses_the_main_run_flags(tmp_path):
    cmd = hc.check_command(tmp_path / "task", "claude-code", "check__dummy__control__a1", tmp_path / "jobs",
                           prompt=tmp_path / "p.md")
    pairs = {(cmd[i], cmd[i + 1]) for i in range(len(cmd) - 1)}
    for pair in (("-p", str(tmp_path / "task")), ("-a", "claude-code"), ("-m", config.MODEL),
                 ("--effort", "high"), ("--ak", "version=2.1.293"), ("--ak", "disable_web_search=true"),
                 ("--ae", "CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN}"),
                 ("--ae", "CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000"), ("--agent-timeout-multiplier", "1.0"),
                 ("--agent-setup-timeout-multiplier", "2.0"), ("--extra-instruction-path", str(tmp_path / "p.md")),
                 ("--ek", "keep_containers=true"), ("-n", "1"), ("-k", "1"), ("-r", "0"),
                 ("-o", str(tmp_path / "jobs")), ("--job-name", "check__dummy__control__a1")):
        assert pair in pairs, pair
    assert cmd[:2] == ["harbor", "run"] and cmd[-1] == "-y"
    assert "--no-force-build" in cmd and "--resume-trajectory" not in cmd and "--skill" not in cmd


def test_oracle_check_has_no_model_flags_and_a_skill_is_appended(tmp_path):
    oracle = hc.check_command(tmp_path / "t", "oracle", "j", tmp_path / "jobs")
    assert oracle[oracle.index("-a") + 1] == "oracle"
    assert not {"-m", "--ak", "--ae", "--effort", "--extra-instruction-path"} & set(oracle)
    with_skill = hc.check_command(tmp_path / "t", "claude-code", "j", tmp_path / "jobs", skill=tmp_path / "horizon")
    assert with_skill[-2:] == ["--skill", str(tmp_path / "horizon")]


# ---------------------------------------------------------------- fixture trial directories


def good_files() -> dict:
    """What a passing two-round check leaves in its trial directory, keyed by relative path."""
    def probe(step, label, **overrides):
        files = {"pwd.txt": "/app", "user.txt": "root", "home.txt": "/root", "tmp.txt": "claude-0",
                 "project.txt": "a.txt", "tests.txt": "", "verifier.txt": "", "dot-claude.txt": "",
                 "ledger.txt": "", "procs.txt": "1 0 sleep infinity"}
        files.update(overrides)
        return {f"steps/{step}/artifacts/logs/artifacts/probe/{label}/{k}": v for k, v in files.items()}

    ledger = "\n".join(sorted(hc.LEDGER_FILES))
    decoy = f"1 0 sleep infinity\n42 1 {hc.DECOY}"
    files = {}
    files.update(probe(FIRST, FIRST))
    files.update(probe(FIRST, hc.PLANTED, **{"tmp.txt": hc.SENTINEL, "ledger.txt": ledger,
                                             "dot-claude.txt": f".claude\n{hc.MEMORY_FILE}", "procs.txt": decoy}))
    files.update(probe(FIRST, hc.BEFORE, **{"ledger.txt": ledger, "procs.txt": decoy}))
    files.update(probe(SECOND, SECOND, **{"project.txt": ".claude\na.txt", "ledger.txt": ledger}))
    files.update(probe(SECOND, hc.BEFORE, **{"ledger.txt": ledger}))
    files[f"steps/{FIRST}/verifier/{hc.VERIFIER_MARKER}"] = f"{hc.TESTS_MARKER}\ntest.sh"
    for step in hc.STEPS:
        files[f"steps/{step}/artifacts/logs/artifacts/{step}/harness/goals/check-goal.json"] = "{}"
        files[f"steps/{step}/verifier/reward.txt"] = "1"
    return files


def step_result(name, reward=1.0, exception=None):
    return {"step_name": name,
            "verifier_result": {"rewards": {"reward": reward}} if reward is not None else None,
            "exception_info": ({"exception_type": exception[0], "exception_message": exception[1]}
                               if exception else None),
            "agent_execution": {"started_at": "2026-10-10T10:00:00Z", "finished_at": "2026-10-10T10:01:00Z"},
            "verifier": {"started_at": "2026-10-10T10:01:00Z", "finished_at": "2026-10-10T10:01:05Z"}}


def make_trial(root: Path, files=None, steps=None):
    trial = root / "dummy__AbC1234"
    trial.mkdir(parents=True)
    for rel, text in (good_files() if files is None else files).items():
        path = trial / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text + "\n")
    result = {"trial_name": trial.name,
              "step_results": steps if steps is not None else [step_result(n) for n in hc.STEPS]}
    (trial / "result.json").write_text(json.dumps(result))
    return trial, result


def _set(files, step, label, name, text):
    files[f"steps/{step}/artifacts/logs/artifacts/probe/{label}/{name}"] = text
    return files


def _drop(files, rel):
    del files[rel]
    return files


def test_a_clean_check_passes(tmp_path):
    trial, result = make_trial(tmp_path)
    findings = hc.check_findings(trial, result)
    assert findings["failures"] == [] and findings["ok"] is True
    assert findings["facts"]["user"] == "root" and findings["facts"]["pwd"] == "/app"
    assert findings["facts"]["rewards"] == {FIRST: 1.0, SECOND: 1.0}


BREAKS = {
    "round 1 setup did not run": (lambda f: _set(f, FIRST, FIRST, "tmp.txt", hc.IMAGE_FILE),
                                  f"{FIRST}: setup.sh did not empty /tmp"),
    "round 2 tmp kept": (lambda f: _set(f, SECOND, SECOND, "tmp.txt", hc.SENTINEL),
                         f"{SECOND}: setup.sh did not empty /tmp"),
    "ledger wiped": (lambda f: _set(f, SECOND, SECOND, "ledger.txt", ""),
                     f"{SECOND}: setup.sh removed the ledger files"),
    "memory kept": (lambda f: _set(f, SECOND, SECOND, "dot-claude.txt", hc.MEMORY_FILE),
                    f"{SECOND}: setup.sh left the ~/.claude/projects memory"),
    "memory never planted": (lambda f: _set(f, FIRST, hc.PLANTED, "dot-claude.txt", ".claude"),
                             f"{FIRST}: the ~/.claude/projects memory was not planted"),
    "setup left": (lambda f: _set(f, SECOND, SECOND, "project.txt", "a.txt\nsetup.sh"),
                   f"{SECOND}: setup.sh is still in the project"),
    "decoy survived": (lambda f: _set(f, SECOND, SECOND, "procs.txt", f"7 1 {hc.DECOY}"),
                       f"{SECOND}: the collect hook left the stray claude decoy running"),
    "decoy never ran": (lambda f: _set(f, FIRST, hc.BEFORE, "procs.txt", "1 0 sleep infinity"),
                        f"{FIRST}: the stray claude decoy was not running before the collect hook"),
    "tests readable": (lambda f: _set(f, SECOND, SECOND, "tests.txt", hc.TESTS_MARKER),
                       f"{SECOND}: round 1's /tests is readable"),
    "verifier readable": (lambda f: _set(f, SECOND, SECOND, "verifier.txt", hc.VERIFIER_MARKER),
                          f"{SECOND}: round 1's /logs/verifier is readable"),
    "no collect copy": (lambda f: _drop(f, f"steps/{SECOND}/artifacts/logs/artifacts/{SECOND}/harness/goals/"
                                           "check-goal.json"),
                        f"{SECOND}: the collect hook did not copy the ledger"),
    "probe missing": (lambda f: _drop(f, f"steps/{SECOND}/artifacts/logs/artifacts/probe/{SECOND}/tmp.txt"),
                      f"{SECOND}: probe file {SECOND}/tmp.txt missing"),
    "tests never seen": (lambda f: _drop(f, f"steps/{FIRST}/verifier/{hc.VERIFIER_MARKER}"),
                         f"{FIRST}: the verifier did not list {hc.TESTS_MARKER} in /tests"),
    "sentinel never planted": (lambda f: _set(f, FIRST, hc.PLANTED, "tmp.txt", ""),
                               f"{FIRST}: the /tmp sentinel was not planted"),
}


@pytest.mark.parametrize("name", sorted(BREAKS))
def test_each_broken_behaviour_is_named(tmp_path, name):
    mutate, expected = BREAKS[name]
    trial, result = make_trial(tmp_path, files=mutate(good_files()))
    findings = hc.check_findings(trial, result)
    assert findings["ok"] is False
    assert any(f.startswith(expected) for f in findings["failures"]), findings["failures"]


def test_rewards_exceptions_and_missing_rounds_fail(tmp_path):
    trial, result = make_trial(tmp_path / "a", steps=[step_result(FIRST, reward=0.0)])
    failures = hc.check_findings(trial, result)["failures"]
    assert f"{FIRST}: reward 0.0, expected 1.0" in failures and f"{SECOND}: did not run" in failures
    timed_out = step_result(FIRST, exception=("AgentTimeoutError", "Agent execution timed out after 1800.0 seconds"))
    trial, result = make_trial(tmp_path / "b", steps=[timed_out, step_result(SECOND)])
    assert any(f.startswith(f"{FIRST}: AgentTimeoutError") for f in hc.check_findings(trial, result)["failures"])
    assert hc.check_findings(None, None) == {"ok": False, "failures": ["no trial directory with result.json"],
                                             "facts": {}}


def test_verifier_timeout_passes_only_on_the_short_override(tmp_path):
    short = ("VerifierTimeoutError", "Verifier execution timed out after 20.0 seconds")
    trial, result = make_trial(tmp_path / "a", files={}, steps=[step_result(FIRST, reward=None, exception=short)])
    assert hc.verifier_timeout_findings(trial, result)["ok"] is True
    trial, result = make_trial(tmp_path / "b", files={}, steps=[step_result(FIRST, reward=1.0)])
    findings = hc.verifier_timeout_findings(trial, result)
    assert findings["ok"] is False and "no exception" in findings["failures"][0]
    default = ("VerifierTimeoutError", "Verifier execution timed out after 600.0 seconds")
    trial, result = make_trial(tmp_path / "c", files={}, steps=[step_result(FIRST, reward=None, exception=default)])
    assert hc.verifier_timeout_findings(trial, result)["ok"] is False


def test_timeout_probe_records_without_asserting(tmp_path):
    claude = "claude --verbose --output-format=stream-json --print"
    files = {
        f"steps/{FIRST}/artifacts/logs/artifacts/probe/{hc.BEFORE}/procs.txt":
            f"1 0 sleep infinity\n50 1 {claude}\n51 50 /bin/bash -c sleep 600\n52 51 sleep 600\n42 1 {hc.DECOY}",
        f"steps/{SECOND}/artifacts/logs/artifacts/probe/{SECOND}/procs.txt":
            f"1 0 sleep infinity\n52 1 sleep 600\n90 1 {claude}",
        f"steps/{FIRST}/artifacts/logs/artifacts/{FIRST}/harness/goals/check-goal.json": "{}",
    }
    timed_out = step_result(FIRST, reward=0.0, exception=("AgentTimeoutError",
                                                          "Agent execution timed out after 60.0 seconds"))
    trial, result = make_trial(tmp_path, files=files, steps=[timed_out, step_result(SECOND)])
    out = hc.timeout_probe_findings(trial, result)
    assert out["ok"] is None and out["failures"] == []
    rec = out["recorded"]
    assert rec["round_1_exception"]["type"] == "AgentTimeoutError"
    assert rec["round_1_verifier_ran"] is True and rec["round_1_reward"] == 0.0
    assert rec["round_1_ledger_collected"] is True
    assert rec["round_2_started"] is True and rec["round_2_reward"] == 1.0
    assert rec["running_before_collect"] == [claude, "/bin/bash -c sleep 600", "sleep 600", hc.DECOY]
    assert rec["still_running_in_round_2"] == ["sleep 600"]
    assert rec["claude_like_in_round_2"] == [claude]
    assert hc.timeout_probe_findings(None, None)["ok"] is False


def canary_stream(is_error, target="/app/.claude/harness/gates/x/request.md"):
    events = [
        {"type": "system", "subtype": "init", "claude_code_version": "2.1.293"},
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "id": "t1", "name": "Write", "input": {"file_path": target, "content": "canary\n"}}]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "tool_use_id": "t1", "is_error": is_error,
             "content": "denied" if is_error else "File created successfully"}]}},
        {"type": "result", "subtype": "success", "is_error": False, "result": "Done."},
    ]
    return "\n".join(json.dumps(e) for e in events)


@pytest.mark.parametrize("is_error, collected, ok", [
    (False, True, True), (True, False, False), (True, True, False), (False, False, False)])
def test_canary_needs_a_successful_write_and_the_collected_file(tmp_path, is_error, collected, ok):
    files = {f"steps/{FIRST}/agent/claude-code.txt": canary_stream(is_error)}
    if collected:
        files[f"steps/{FIRST}/artifacts/logs/artifacts/{FIRST}/harness/gates/x/request.md"] = "canary"
    trial, result = make_trial(tmp_path, files=files, steps=[step_result(FIRST)])
    findings = hc.canary_findings(trial, result)
    assert findings["ok"] is ok
    assert findings["facts"]["writes"] == [{"file_path": "/app/.claude/harness/gates/x/request.md",
                                            "is_error": is_error,
                                            "result": "denied" if is_error else "File created successfully"}]


# ---------------------------------------------------------------- whole runs against a fake host


class FakeHost:
    """Answers docker and harbor calls; `harbor run` writes the given trial into its job directory."""

    def __init__(self, files=None, steps=None, arch="arm64"):
        self.calls, self.envs = [], []
        self.files, self.steps, self.arch = files, steps, arch

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if cmd[:2] == ["harbor", "run"]:
            self.envs.append(kwargs.get("env"))
            make_trial(Path(cmd[cmd.index("-o") + 1]) / cmd[cmd.index("--job-name") + 1],
                       files=self.files, steps=self.steps)
            return cp(cmd)
        if cmd[0] == "docker" and "inspect" in cmd:
            return cp(cmd, 0, json.dumps([{"Id": "sha256:dummy", "Architecture": self.arch,
                                           "Config": {"WorkingDir": "/app", "User": ""}}]))
        return cp(cmd)


def test_run_check_end_to_end_with_a_fake_host(tmp_path):
    host = FakeHost()
    out = hc.run_check(tmp_path, agent="claude-code", token="tok", run=host, **QUIET)
    assert out["ok"] is True and out["job_name"] == "check__dummy__control__a1"
    assert out["image_id"] == "sha256:dummy" and out["arch"] == "arm64"
    saved = json.loads((tmp_path / "checks" / "check__dummy__control__a1" / "findings.json").read_text())
    assert saved == out
    harbor = next(c for c in host.calls if c[:2] == ["harbor", "run"])
    assert harbor[harbor.index("-a") + 1] == "claude-code"
    task = Path(harbor[harbor.index("-p") + 1])
    assert not list(task.rglob("solution"))
    prompt = Path(harbor[harbor.index("--extra-instruction-path") + 1])
    assert prompt.read_text() == config.prompt_text("control", 30)
    assert host.envs[0]["CLAUDE_CODE_OAUTH_TOKEN"] == "tok" and host.envs[0]["CLAUDE_FORCE_OAUTH"] == "1"
    assert any(c[:2] == ["docker", "build"] and config.image_tag("dummy") in c for c in host.calls)
    with pytest.raises(FileExistsError):
        hc.run_check(tmp_path, agent="claude-code", token="tok", run=host, **QUIET)


def test_oracle_check_keeps_solutions_needs_no_token_and_flags_a_wrong_arch(tmp_path):
    host = FakeHost(arch="amd64")
    out = hc.run_check(tmp_path, agent="oracle", run=host, **QUIET)
    assert out["job_name"] == "check__dummy__oracle__a1" and out["ok"] is False
    assert out["failures"][0] == "image architecture amd64, expected arm64"
    harbor = next(c for c in host.calls if c[:2] == ["harbor", "run"])
    assert host.envs == [None] and "--extra-instruction-path" not in harbor
    task = Path(harbor[harbor.index("-p") + 1])
    assert (task / "steps" / FIRST / "solution" / "solve.sh").is_file()


def test_claude_code_runs_need_a_token_and_agents_are_checked(tmp_path):
    with pytest.raises(ValueError):
        hc.run_check(tmp_path, agent="claude-code", token=None, run=FakeHost(), **QUIET)
    with pytest.raises(ValueError):
        hc.run_job("check", "codex", "j", tmp_path, run=FakeHost(), **QUIET)


def test_vtimeout_probe_and_canary_jobs(tmp_path):
    short = ("VerifierTimeoutError", "Verifier execution timed out after 20.0 seconds")
    vt = hc.verifier_timeout_check(tmp_path, run=FakeHost(files={}, steps=[step_result(FIRST, None, short)]),
                                   **QUIET)
    assert vt["job_name"] == "check__dummy-vtimeout__oracle__a1" and vt["ok"] is True
    assert vt["variant"] == "vtimeout"
    pr = hc.timeout_probe(tmp_path, "tok", run=FakeHost(files={}, steps=[step_result(FIRST), step_result(SECOND)]),
                          **QUIET)
    assert pr["job_name"] == "check__dummy-probe__control__a1" and pr["ok"] is None
    host = FakeHost(files={f"steps/{FIRST}/agent/claude-code.txt": canary_stream(False),
                           f"steps/{FIRST}/artifacts/logs/artifacts/{FIRST}/harness/gates/x/request.md": "canary"},
                    steps=[step_result(FIRST)])
    ca = hc.canary(tmp_path, "tok", skill=tmp_path / "bundle", run=host, **QUIET)
    assert ca["job_name"] == "canary__dummy__horizon__a1" and ca["ok"] is True
    harbor = next(c for c in host.calls if c[:2] == ["harbor", "run"])
    assert harbor[-2:] == ["--skill", str(tmp_path / "bundle")]
    task = Path(harbor[harbor.index("-p") + 1])
    assert (task / "steps" / FIRST / "instruction.md").read_text() == hc.CANARY_INSTRUCTION


def test_a_running_trial_blocks_a_check(tmp_path):
    def busy(cmd, **kwargs):
        if cmd[:3] == ["docker", "ps", "-q"]:
            return cp(cmd, 0, "r1\n")
        if cmd[:2] == ["docker", "inspect"]:
            return cp(cmd, 0, json.dumps([{"Id": "r1", "Config": {"Labels": {
                "com.docker.compose.project": "x__abc1234__env"}}}]))
        return cp(cmd)

    with pytest.raises(tb4run.RunnerBusy):
        hc.run_check(tmp_path, agent="oracle", run=busy, **QUIET)
    assert not (tmp_path / "checks").exists()


def test_main_dispatches_and_sets_the_exit_code(tmp_path, monkeypatch, capsys):
    seen = []
    monkeypatch.setattr(hc, "run_check", lambda runs, **kw: seen.append(("check", kw["agent"], kw["token"]))
                        or {"job_name": "j", "ok": True, "failures": []})
    monkeypatch.setattr(hc, "canary", lambda runs, token, **kw: seen.append(("canary", token, kw["skill"]))
                        or {"job_name": "c", "ok": False, "failures": ["x"]})
    monkeypatch.setattr(hc, "timeout_probe", lambda runs, token, **kw: seen.append(("probe", token))
                        or {"job_name": "p", "ok": None, "failures": [], "recorded": {"a": 1}})
    monkeypatch.setattr(hc.tb4run, "read_token", lambda: "tok")
    assert hc.main(["check", "--agent", "oracle", "--runs", str(tmp_path)]) == 0
    assert hc.main(["canary", "--skill", str(tmp_path / "s"), "--runs", str(tmp_path)]) == 1
    assert hc.main(["probe", "--runs", str(tmp_path)]) == 0
    assert seen == [("check", "oracle", None), ("canary", "tok", tmp_path / "s"), ("probe", "tok")]
    assert '"recorded"' in capsys.readouterr().out
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `sh bench/tb4/test.sh -q tests/test_evo_harborcheck.py`
Expected: FAIL at collection with `E   ImportError: cannot import name 'harborcheck' from 'bench.evo' (/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode/bench/evo/__init__.py)`, and the last line is `1 error in 0.09s` (the time varies).

- [ ] **Step 3: Create the dummy task.** It is a two-round Harbor task with no EvoCode content.
  - Round 1 runs the probe's `plant`, then writes `a.txt`.
  - Round 2 runs the probe's `record round-2`, then writes `b.txt`.
  - The probe is baked into the image at `/opt/evo-check/probe.sh`, outside the project. It reads `/proc` directly, so it needs no `ps`.
  - The image installs nothing. `probe.sh` uses only bash and tools that `ubuntu:22.04` ships (awk, setsid, nohup, id, find, tr), so the build needs no network.
  - Each solution runs the same command its instruction names, so the oracle run is deterministic.

  Create these files with exactly this content:

`bench/evo/dummy/task.toml`:

```toml
# Harbor behaviour-check task for the EvoCode first look. It holds no EvoCode content.
# Generated by bench.evo.harborcheck.dummy_toml(); a test keeps the two identical.
schema_version = "1.4"
multi_step_reward_strategy = "mean"

[agent]
timeout_sec = 1800.0

[verifier]
timeout_sec = 120.0

[[verifier.collect]]
command = "bash /opt/evo-check/probe.sh record before-collect"
timeout_sec = 60.0

[environment]
build_timeout_sec = 900.0
cpus = 1
memory_mb = 2048

[[steps]]
name = "round-1"

[[steps]]
name = "round-2"
```

`bench/evo/dummy/environment/Dockerfile`:

```dockerfile
FROM ubuntu:22.04
COPY probe.sh /opt/evo-check/probe.sh
# setup.sh must remove this file before round 1's agent phase.
RUN chmod 0755 /opt/evo-check/probe.sh && touch /tmp/evo-image-file
WORKDIR /app
```

`bench/evo/dummy/environment/probe.sh`:

```bash
#!/bin/bash
# Behaviour-check probe for the EvoCode first look (bench/evo/harborcheck.py). No EvoCode content.
#   probe.sh plant         record round-1, plant what round 2 must or must not see, record round-1-planted
#   probe.sh record LABEL  record what this process can see into /logs/artifacts/probe/LABEL/
# EVO_CHECK_ROOT and EVO_CHECK_DECOY exist only so the unit test can run this outside a container.
set -u
R="${EVO_CHECK_ROOT:-}"
H="${HOME:-/root}"

record() {
  out="$R/logs/artifacts/probe/$1"
  mkdir -p "$out"
  pwd > "$out/pwd.txt"
  id -un > "$out/user.txt" 2>&1
  printf '%s\n' "$H" > "$out/home.txt"
  ls -A "$R/tmp" > "$out/tmp.txt" 2>&1
  ls -A . > "$out/project.txt" 2>&1
  ls -A "$R/tests" > "$out/tests.txt" 2>&1
  ls -A "$R/logs/verifier" > "$out/verifier.txt" 2>&1
  (cd "$H" 2>/dev/null && find .claude 2>/dev/null | LC_ALL=C sort) > "$out/dot-claude.txt"
  find .claude/harness -type f 2>/dev/null | LC_ALL=C sort > "$out/ledger.txt"
  for d in "$R"/proc/[0-9]*; do
    [ -r "$d/cmdline" ] || continue
    args="$(tr '\0' ' ' < "$d/cmdline" 2>/dev/null)"
    [ -n "$args" ] || continue
    ppid="$(awk '/^PPid:/ {print $2}' "$d/status" 2>/dev/null)"
    printf '%s %s %s\n' "${d##*/}" "${ppid:-?}" "${args% }"
  done > "$out/procs.txt"
}

plant() {
  printf 'sentinel\n' > "$R/tmp/evo-check-sentinel"
  mkdir -p .claude/harness/goals .claude/harness/gates/check-gate "$H/.claude/projects/-app/memory"
  printf '{"slug": "check-goal", "status": "active"}\n' > .claude/harness/goals/check-goal.json
  printf 'check gate request\n' > .claude/harness/gates/check-gate/request.md
  printf 'memory\n' > "$H/.claude/projects/-app/memory/check.md"
  if [ "${EVO_CHECK_DECOY:-1}" = 1 ]; then
    # A stray "claude": its command line is "claude 86399"; the collect hook must kill it.
    setsid nohup bash -c 'exec -a claude sleep 86399' > /dev/null 2>&1 < /dev/null &
    sleep 1
  fi
}

case "${1:-}" in
  plant) record round-1; plant; record round-1-planted ;;
  record) record "${2:?usage: probe.sh record LABEL}" ;;
  *) echo "usage: probe.sh plant | probe.sh record LABEL" >&2; exit 2 ;;
esac
exit 0
```

`bench/evo/dummy/steps/round-1/instruction.md`:

```markdown
This is a short check of the test environment.

1. Run this exact shell command in the current directory and wait for it to finish:

       bash /opt/evo-check/probe.sh plant

2. Create a file named `a.txt` in the current directory containing exactly the letter `A` followed by a newline.

Do not change any other file.
```

`bench/evo/dummy/steps/round-1/tests/test.sh`:

```bash
#!/bin/bash
# Round 1 passes when /app/a.txt holds exactly "A".
# harborcheck's verifier-timeout variant adds /tests/sleep_sec so this outlives a short verifier timeout.
if [ -f /tests/sleep_sec ]; then sleep "$(cat /tests/sleep_sec)"; fi
mkdir -p /logs/verifier
ls -A /tests > /logs/verifier/round1-verifier-marker.txt 2>&1
if [ "$(cat /app/a.txt 2>/dev/null)" = "A" ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
```

`bench/evo/dummy/steps/round-1/tests/round1-marker.txt`:

```text
This file is in /tests only while round 1 is graded.
```

`bench/evo/dummy/steps/round-1/solution/solve.sh`:

```bash
#!/bin/bash
set -euo pipefail
cd /app
bash /opt/evo-check/probe.sh plant
printf 'A\n' > a.txt
```

`bench/evo/dummy/steps/round-2/instruction.md`:

```markdown
This is a short check of the test environment.

1. Run this exact shell command in the current directory and wait for it to finish:

       bash /opt/evo-check/probe.sh record round-2

2. Create a file named `b.txt` in the current directory containing exactly the letter `B` followed by a newline.

Do not change any other file.
```

`bench/evo/dummy/steps/round-2/tests/test.sh`:

```bash
#!/bin/bash
# Round 2 passes when /app/b.txt holds exactly "B" and round 1's /app/a.txt is still there.
mkdir -p /logs/verifier
if [ "$(cat /app/b.txt 2>/dev/null)" = "B" ] && [ "$(cat /app/a.txt 2>/dev/null)" = "A" ]; then
  echo 1 > /logs/verifier/reward.txt
else
  echo 0 > /logs/verifier/reward.txt
fi
```

`bench/evo/dummy/steps/round-2/solution/solve.sh`:

```bash
#!/bin/bash
set -euo pipefail
cd /app
bash /opt/evo-check/probe.sh record round-2
printf 'B\n' > b.txt
```

Then make the scripts executable (git records the mode):

```bash
cd "$WT" && chmod 0755 bench/evo/dummy/environment/probe.sh bench/evo/dummy/steps/*/tests/test.sh bench/evo/dummy/steps/*/solution/solve.sh
```

Run: `git ls-files -s --others --exclude-standard bench/evo/dummy | wc -l`
Expected: `10`. These are the ten dummy files: none is staged yet, and `.gitignore` hides none of them.

Run: `stat -f '%Lp %N' bench/evo/dummy/environment/probe.sh bench/evo/dummy/steps/*/tests/test.sh bench/evo/dummy/steps/*/solution/solve.sh`
Expected, exactly:

```
755 bench/evo/dummy/environment/probe.sh
755 bench/evo/dummy/steps/round-1/tests/test.sh
755 bench/evo/dummy/steps/round-2/tests/test.sh
755 bench/evo/dummy/steps/round-1/solution/solve.sh
755 bench/evo/dummy/steps/round-2/solution/solve.sh
```

- [ ] **Step 4: Implement** — create `bench/evo/harborcheck.py`:

```python
"""Harbor behaviour check, agent-timeout probe and .claude write canary, run on a dummy two-round task.

The dummy (bench/evo/dummy) holds no EvoCode content. Every run goes through copies.make_copy and a pinned
image, exactly like the main run, so the hooks under test are the ones the study uses. What each container
wrote to /logs/artifacts during step S is read from <trial>/steps/S/artifacts/logs/artifacts/ (Harbor 0.24.0
bind-mounts /logs/artifacts at artifacts/logs/artifacts and moves it into steps/S/artifacts after each step).
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from bench.evo import config, copies, images
from bench.tb4 import run as tb4run
from bench.tb4 import stream

DUMMY = Path(__file__).resolve().parent / "dummy"
STEPS = ("round-1", "round-2")
VARIANTS = ("check", "vtimeout", "probe", "canary")
AGENTS = ("oracle", "claude-code")
IMAGE_TASK = "dummy"
PROMPT_MINUTES = 30

AGENT_TIMEOUT_SEC = 1800.0          # as an EvoCode round; the dummy rounds take a minute or two
VERIFIER_TIMEOUT_SEC = 120.0        # the dummy's [verifier] timeout; make_copy copies it into every step
SHORT_VERIFIER_TIMEOUT_SEC = 20.0   # vtimeout variant: Harbor's step default would be 600 s
VERIFIER_SLEEP_SEC = 45             # ... and round 1's test sleeps this long first
PROBE_AGENT_TIMEOUT_SEC = 60.0      # probe variant: round 1's [steps.agent] timeout

BEFORE = "before-collect"           # probe label written by the task-level collect hook (runs first)
PLANTED = "round-1-planted"
BEFORE_COLLECT_COMMAND = f"bash /opt/evo-check/probe.sh record {BEFORE}"
PROBE_FILES = ("pwd.txt", "user.txt", "home.txt", "tmp.txt", "project.txt", "tests.txt", "verifier.txt",
               "dot-claude.txt", "ledger.txt", "procs.txt")
SENTINEL = "evo-check-sentinel"
IMAGE_FILE = "evo-image-file"
LEDGER_FILES = (".claude/harness/goals/check-goal.json", ".claude/harness/gates/check-gate/request.md")
MEMORY_FILE = ".claude/projects/-app/memory/check.md"  # under $HOME; setup.sh removes ~/.claude/projects
TESTS_MARKER = "round1-marker.txt"
VERIFIER_MARKER = "round1-verifier-marker.txt"
DECOY = "claude 86399"
SLEEPER = "sleep 600"
CANARY_REL = ".claude/harness/gates/x/request.md"

SLEEP_INSTRUCTION = """This is a short check of the test environment.

1. Run this exact shell command in the current directory and wait for it to finish:

       bash /opt/evo-check/probe.sh plant

2. Run `sleep 600` as a foreground shell command (not in the background) and wait for it to finish.

3. Only after it has finished, create a file named `a.txt` in the current directory containing exactly the letter `A` followed by a newline.
"""

CANARY_INSTRUCTION = """This is a short check of the test environment.

Use the Write tool (not a shell command) to create the file `.claude/harness/gates/x/request.md` in the current directory, containing exactly the word `canary` followed by a newline.

If the Write tool refuses or fails, do not create the file any other way; reply with the exact error message instead.
"""


def dummy_toml(*, steps=STEPS, verifier_timeout_sec=VERIFIER_TIMEOUT_SEC, round1_agent_timeout_sec=None) -> str:
    """The dummy's task.toml; bench/evo/dummy/task.toml is this function's default output."""
    lines = [
        "# Harbor behaviour-check task for the EvoCode first look. It holds no EvoCode content.",
        "# Generated by bench.evo.harborcheck.dummy_toml(); a test keeps the two identical.",
        'schema_version = "1.4"',
        'multi_step_reward_strategy = "mean"',
        "",
        "[agent]",
        f"timeout_sec = {AGENT_TIMEOUT_SEC!r}",
        "",
        "[verifier]",
        f"timeout_sec = {float(verifier_timeout_sec)!r}",
        "",
        "[[verifier.collect]]",
        f'command = "{BEFORE_COLLECT_COMMAND}"',
        "timeout_sec = 60.0",
        "",
        "[environment]",
        "build_timeout_sec = 900.0",
        "cpus = 1",
        "memory_mb = 2048",
        "",
    ]
    for name in steps:
        lines += ["[[steps]]", f'name = "{name}"']
        if name == STEPS[0] and round1_agent_timeout_sec is not None:
            lines.append(f"agent = {{ timeout_sec = {float(round1_agent_timeout_sec)!r} }}")
        lines.append("")
    return "\n".join(lines)


def build_variant(dest: Path, variant: str) -> Path:
    """Copy the dummy to dest and turn it into one of VARIANTS; the dummy itself is never edited."""
    if variant not in VARIANTS:
        raise ValueError(f"unknown variant {variant!r}")
    if dest.exists():
        raise FileExistsError(dest)
    shutil.copytree(DUMMY, dest)
    steps = STEPS if variant in ("check", "probe") else STEPS[:1]
    for extra in STEPS[len(steps):]:
        shutil.rmtree(dest / "steps" / extra)
    (dest / "task.toml").write_text(dummy_toml(
        steps=steps,
        verifier_timeout_sec=SHORT_VERIFIER_TIMEOUT_SEC if variant == "vtimeout" else VERIFIER_TIMEOUT_SEC,
        round1_agent_timeout_sec=PROBE_AGENT_TIMEOUT_SEC if variant == "probe" else None))
    first = dest / "steps" / STEPS[0]
    if variant == "vtimeout":
        (first / "tests" / "sleep_sec").write_text(f"{VERIFIER_SLEEP_SEC}\n")
    elif variant == "probe":
        (first / "instruction.md").write_text(SLEEP_INSTRUCTION)
    elif variant == "canary":
        (first / "instruction.md").write_text(CANARY_INSTRUCTION)
    return dest


def prepare(work: Path, variant: str, kind: str, tag: str) -> Path:
    """work/source is the variant; work/task is its copy with the main run's hooks and pinned image."""
    source = build_variant(work / "source", variant)
    task = work / "task"
    copies.make_copy(source, task, kind=kind, image_tag=tag)
    return task


def check_command(task_dir: Path, agent: str, job_name: str, jobs_dir: Path, *, prompt=None, skill=None) -> list:
    cmd = ["harbor", "run", "-p", str(task_dir), "-a", agent]
    if agent == "claude-code":
        cmd += ["-m", config.MODEL, "--effort", config.EFFORT,
                "--ak", f"version={config.CLAUDE_CODE_VERSION}", "--ak", "disable_web_search=true",
                "--ae", "CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN}",
                "--ae", f"CLAUDE_CODE_MAX_OUTPUT_TOKENS={config.MAX_OUTPUT_TOKENS}"]
    cmd += ["--agent-timeout-multiplier", str(config.AGENT_TIMEOUT_MULTIPLIER),
            "--agent-setup-timeout-multiplier", str(config.AGENT_SETUP_TIMEOUT_MULTIPLIER)]
    if prompt is not None:
        cmd += ["--extra-instruction-path", str(prompt)]
    cmd += ["--ek", "keep_containers=true", "--no-force-build", "-n", "1", "-k", "1", "-r", "0",
            "-o", str(jobs_dir), "--job-name", job_name, "-y"]
    if skill is not None:
        cmd += ["--skill", str(skill)]
    return cmd


def run_job(variant: str, agent: str, job_name: str, runs: Path, *, token=None, skill=None,
            run=subprocess.run, log=print):
    """Prepare, build, run one Harbor job and remove its containers; returns (meta, trial_dir, result)."""
    if agent not in AGENTS:
        raise ValueError(f"unknown agent {agent!r}")
    if agent == "claude-code" and not token:
        raise ValueError("the claude-code agent needs the subscription token")
    work, job_dir = runs / "checks" / job_name, runs / "jobs" / job_name
    if work.exists() or job_dir.exists():
        raise FileExistsError(f"{job_name} already exists; rerun with a higher --attempt")
    busy = tb4run.running_trials(run)
    if busy:
        raise tb4run.RunnerBusy(f"trial containers still running: {busy}")
    work.mkdir(parents=True)
    job_dir.parent.mkdir(parents=True, exist_ok=True)
    tag = config.image_tag(IMAGE_TASK)
    task_dir = prepare(work, variant, "validation" if agent == "oracle" else "agent", tag)
    with open(work / "build.log", "w") as handle:
        images.build(task_dir, tag, handle, run)
    image = images.inspect(tag, run)
    prompt = None
    if agent == "claude-code":
        prompt = work / "prompt.md"
        prompt.write_text(config.prompt_text("control", PROMPT_MINUTES))
    cmd = check_command(task_dir, agent, job_name, job_dir.parent, prompt=prompt, skill=skill)
    log(f"{job_name}: harbor run, agent {agent}, variant {variant}")
    with open(work / "harbor.log", "w") as handle:
        proc = run(cmd, env=tb4run.agent_env(token) if token else None, stdout=handle, stderr=subprocess.STDOUT)
    trial_dir, result = tb4run.job_result(job_dir)
    tb4run.cleanup_job(job_dir, run)
    meta = {"job_name": job_name, "variant": variant, "agent": agent, "image_tag": tag, "image_id": image["id"],
            "arch": image["arch"], "harbor_exit": proc.returncode, "harbor_version": config.HARBOR_VERSION,
            "trial_dir": str(trial_dir) if trial_dir else None, "command": cmd}
    return meta, trial_dir, result


# ---------------------------------------------------------------- reading a finished trial


def collected_root(trial_dir: Path, step: str) -> Path:
    """Where Harbor 0.24.0 leaves what the main container wrote to /logs/artifacts during one step."""
    return Path(trial_dir) / "steps" / step / "artifacts" / "logs" / "artifacts"


def probe_dir(trial_dir: Path, step: str, label: str) -> Path:
    return collected_root(trial_dir, step) / "probe" / label


def _read(path: Path):
    return path.read_text(errors="replace").splitlines() if path.is_file() else None


def _args(lines) -> list:
    """Command lines from a probe procs.txt ("pid ppid args...")."""
    return [parts[2] for parts in (line.split(" ", 2) for line in lines or []) if len(parts) == 3]


def _claude_like(args: str) -> bool:
    return args.startswith("claude") or "/claude " in args


def _steps(result) -> dict:
    return {s.get("step_name"): s for s in (result or {}).get("step_results") or []}


def _reward(step):
    value = (((step or {}).get("verifier_result") or {}).get("rewards") or {}).get("reward")
    return float(value) if value is not None else None


def _exception(step):
    info = (step or {}).get("exception_info")
    if not info:
        return None
    return {"type": info.get("exception_type"), "message": str(info.get("exception_message") or "")[:300]}


def _no_trial(key: str) -> dict:
    return {"ok": False, "failures": ["no trial directory with result.json"], key: {}}


def check_findings(trial_dir, result) -> dict:
    """Assert the setup and collect hooks and Harbor's per-round reset on a finished two-round check."""
    if trial_dir is None or result is None:
        return _no_trial("facts")
    trial_dir = Path(trial_dir)
    first, second = STEPS
    failures = []

    def lines(step, label, name):
        found = _read(probe_dir(trial_dir, step, label) / name)
        if found is None:
            failures.append(f"{step}: probe file {label}/{name} missing")
        return found or []

    steps = _steps(result)
    for name in STEPS:
        exc, reward = _exception(steps.get(name)), _reward(steps.get(name))
        if name not in steps:
            failures.append(f"{name}: did not run")
        elif exc:
            failures.append(f"{name}: {exc['type']}: {exc['message']}")
        elif reward != 1.0:
            failures.append(f"{name}: reward {reward}, expected 1.0")
    # Preconditions: what round 2 must not see really existed in round 1.
    if TESTS_MARKER not in (_read(trial_dir / "steps" / first / "verifier" / VERIFIER_MARKER) or []):
        failures.append(f"{first}: the verifier did not list {TESTS_MARKER} in /tests")
    if SENTINEL not in lines(first, PLANTED, "tmp.txt"):
        failures.append(f"{first}: the /tmp sentinel was not planted")
    if MEMORY_FILE not in lines(first, PLANTED, "dot-claude.txt"):
        failures.append(f"{first}: the ~/.claude/projects memory was not planted")
    if [f for f in LEDGER_FILES if f not in lines(first, PLANTED, "ledger.txt")]:
        failures.append(f"{first}: the fake ledger was not planted")
    if DECOY not in _args(lines(first, BEFORE, "procs.txt")):
        failures.append(f"{first}: the stray claude decoy was not running before the collect hook")
    # The behaviours under test.
    if IMAGE_FILE in lines(first, first, "tmp.txt"):
        failures.append(f"{first}: setup.sh did not empty /tmp")
    if SENTINEL in lines(second, second, "tmp.txt"):
        failures.append(f"{second}: setup.sh did not empty /tmp")
    kept = lines(second, second, "ledger.txt")
    missing = [f for f in LEDGER_FILES if f not in kept]
    if missing:
        failures.append(f"{second}: setup.sh removed the ledger files {missing}")
    if MEMORY_FILE in lines(second, second, "dot-claude.txt"):
        failures.append(f"{second}: setup.sh left the ~/.claude/projects memory")
    for name in STEPS:
        if "setup.sh" in lines(name, name, "project.txt"):
            failures.append(f"{name}: setup.sh is still in the project")
        if not (collected_root(trial_dir, name) / name / "harness" / "goals" / "check-goal.json").is_file():
            failures.append(f"{name}: the collect hook did not copy the ledger")
    if DECOY in _args(lines(second, second, "procs.txt")):
        failures.append(f"{second}: the collect hook left the stray claude decoy running")
    if TESTS_MARKER in lines(second, second, "tests.txt"):
        failures.append(f"{second}: round 1's /tests is readable")
    if VERIFIER_MARKER in lines(second, second, "verifier.txt"):
        failures.append(f"{second}: round 1's /logs/verifier is readable")

    def fact(step, label, name):
        return _read(probe_dir(trial_dir, step, label) / name) or []

    facts = {"user": next(iter(fact(first, first, "user.txt")), None),
             "home": next(iter(fact(first, first, "home.txt")), None),
             "pwd": next(iter(fact(first, first, "pwd.txt")), None),
             "rewards": {name: _reward(steps.get(name)) for name in STEPS},
             "round_2_tmp": fact(second, second, "tmp.txt"),
             "round_2_claude_like": [a for a in _args(fact(second, second, "procs.txt")) if _claude_like(a)]}
    return {"ok": not failures, "failures": failures, "facts": facts}


def verifier_timeout_findings(trial_dir, result, expected_sec=SHORT_VERIFIER_TIMEOUT_SEC) -> dict:
    """The step verifier timeout took effect: round 1 hit it at expected_sec, not at Harbor's 600-s default."""
    if trial_dir is None or result is None:
        return _no_trial("facts")
    step = _steps(result).get(STEPS[0])
    exc = _exception(step)
    expected = f"after {float(expected_sec)} seconds"
    facts = {"reward": _reward(step), "exception": exc}
    if exc and exc["type"] == "VerifierTimeoutError" and expected in exc["message"]:
        return {"ok": True, "failures": [], "facts": facts}
    got = f"{exc['type']}: {exc['message']}" if exc else "no exception"
    return {"ok": False, "failures": [f"{STEPS[0]}: expected VerifierTimeoutError {expected}, got {got} "
                                      f"with reward {facts['reward']}"], "facts": facts}


def timeout_probe_findings(trial_dir, result) -> dict:
    """Record (not assert) what a round-1 agent timeout does to grading, to stray processes and to round 2."""
    if trial_dir is None or result is None:
        return _no_trial("recorded")
    trial_dir = Path(trial_dir)
    steps = _steps(result)
    first, second = steps.get(STEPS[0]), steps.get(STEPS[1])
    before = _args(_read(probe_dir(trial_dir, STEPS[0], BEFORE) / "procs.txt"))
    later = _args(_read(probe_dir(trial_dir, STEPS[1], STEPS[1]) / "procs.txt"))
    recorded = {
        "round_1_exception": _exception(first),
        "round_1_verifier_ran": bool(((first or {}).get("verifier") or {}).get("started_at")),
        "round_1_reward": _reward(first),
        "round_1_ledger_collected": (collected_root(trial_dir, STEPS[0]) / STEPS[0] / "harness").is_dir(),
        "round_2_started": bool(((second or {}).get("agent_execution") or {}).get("started_at")),
        "round_2_reward": _reward(second),
        "running_before_collect": [a for a in before if _claude_like(a) or SLEEPER in a],
        "still_running_in_round_2": [a for a in later if a == DECOY or SLEEPER in a],
        "claude_like_in_round_2": [a for a in later if _claude_like(a)],
    }
    return {"ok": None, "failures": [], "recorded": recorded}


def canary_findings(trial_dir, result) -> dict:
    """Pass when a Write tool call created .claude/harness/gates/x/request.md and the collect hook copied it."""
    if trial_dir is None or result is None:
        return _no_trial("facts")
    step = STEPS[0]
    trial_dir = Path(trial_dir)
    copied = collected_root(trial_dir, step) / step / "harness" / "gates" / "x" / "request.md"
    events = stream.read_events(trial_dir / "steps" / step / "agent" / "claude-code.txt")
    results = stream.tool_results(events)
    writes = []
    for _, block in stream.tool_uses(events):
        target = str((block.get("input") or {}).get("file_path") or "")
        if block.get("name") == "Write" and target.endswith(CANARY_REL):
            text, is_error = results.get(block.get("id"), ("", None))
            writes.append({"file_path": target, "is_error": is_error, "result": text[:300]})
    failures = []
    if not copied.is_file():
        failures.append("the collected ledger has no gates/x/request.md")
    if not any(w["is_error"] is False for w in writes):
        failures.append("no Write tool call created the file without an error")
    api_error = stream.terminal_api_error(events)
    if api_error:
        failures.append(f"API error: {api_error}")
    return {"ok": not failures, "failures": failures,
            "facts": {"writes": writes, "file_collected": copied.is_file(), "exception": _exception(
                _steps(result).get(step))}}


# ---------------------------------------------------------------- the four live runs


def _write_json(path: Path, obj) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _finish(runs: Path, meta: dict, findings: dict) -> dict:
    if meta["arch"] != "arm64":
        findings["failures"].insert(0, f"image architecture {meta['arch']}, expected arm64")
        findings["ok"] = False
    out = {**meta, **findings}
    _write_json(runs / "checks" / meta["job_name"] / "findings.json", out)
    return out


def run_check(runs: Path, *, agent="claude-code", attempt=1, token=None, run=subprocess.run, log=print) -> dict:
    name = config.job_name("check", "dummy", "oracle" if agent == "oracle" else "control", attempt)
    meta, trial_dir, result = run_job("check", agent, name, runs, token=token, run=run, log=log)
    return _finish(runs, meta, check_findings(trial_dir, result))


def verifier_timeout_check(runs: Path, *, attempt=1, run=subprocess.run, log=print) -> dict:
    name = config.job_name("check", "dummy-vtimeout", "oracle", attempt)
    meta, trial_dir, result = run_job("vtimeout", "oracle", name, runs, run=run, log=log)
    return _finish(runs, meta, verifier_timeout_findings(trial_dir, result))


def timeout_probe(runs: Path, token: str, *, attempt=1, run=subprocess.run, log=print) -> dict:
    name = config.job_name("check", "dummy-probe", "control", attempt)
    meta, trial_dir, result = run_job("probe", "claude-code", name, runs, token=token, run=run, log=log)
    return _finish(runs, meta, timeout_probe_findings(trial_dir, result))


def canary(runs: Path, token: str, *, attempt=1, skill=None, run=subprocess.run, log=print) -> dict:
    name = config.job_name("canary", "dummy", "horizon" if skill else "control", attempt)
    meta, trial_dir, result = run_job("canary", "claude-code", name, runs, token=token, skill=skill, run=run,
                                      log=log)
    return _finish(runs, meta, canary_findings(trial_dir, result))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("what", choices=("check", "vtimeout", "probe", "canary"))
    parser.add_argument("--agent", choices=AGENTS, default="claude-code", help="check only")
    parser.add_argument("--attempt", type=int, default=1)
    parser.add_argument("--skill", type=Path, help="canary only: the Horizon bundle, to run the horizon arm")
    parser.add_argument("--runs", type=Path, default=config.RUNS)
    args = parser.parse_args(argv)
    with tb4run.hold_lock(args.runs / "runner.lock"):
        if args.what == "vtimeout":
            out = verifier_timeout_check(args.runs, attempt=args.attempt)
        elif args.what == "check":
            token = tb4run.read_token() if args.agent == "claude-code" else None
            out = run_check(args.runs, agent=args.agent, attempt=args.attempt, token=token)
        elif args.what == "probe":
            out = timeout_probe(args.runs, tb4run.read_token(), attempt=args.attempt)
        else:
            out = canary(args.runs, tb4run.read_token(), attempt=args.attempt, skill=args.skill)
    summary = {k: out.get(k) for k in ("job_name", "ok", "failures")}
    if "recorded" in out:
        summary["recorded"] = out["recorded"]
    print(json.dumps(summary, indent=2))
    return 0 if out.get("ok") is not False else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Run the tests, then check every prepared copy against Harbor's own validator**

Run: `sh bench/tb4/test.sh -q tests/test_evo_harborcheck.py`
Expected: `48 passed in 0.39s` (the time varies).

Then run: `sh bench/tb4/test.sh -q tests/test_evo_harborcheck.py tests/test_tb4_run.py tests/test_tb4_copies.py tests/test_tb4_stream.py`
Expected: `112 passed in 0.58s` (the time varies). These are the TB4 modules `harborcheck` imports; this task changes nothing in TB4.

Then load every prepared copy with Harbor's own `Task` model and validator (no Docker, no usage):

```bash
cd "$WT" && W="$(mktemp -d)" && python3 -c '
import sys
from pathlib import Path
from bench.evo import harborcheck as hc
for v in hc.VARIANTS:
    for k in ("agent", "validation"):
        hc.prepare(Path(sys.argv[1]) / f"{v}-{k}", v, k, "evo-local/dummy:test")
' "$W" && "$(uv tool dir)/harbor/bin/python" -c '
import sys
from pathlib import Path
from harbor.models.task.task import Task
for d in [Path(sys.argv[2])] + sorted(p / "task" for p in Path(sys.argv[1]).iterdir()):
    steps = [(s.name, s.agent.timeout_sec, s.verifier.timeout_sec, len(s.verifier.collect)) for s in Task(d).config.steps]
    print(d.name if d.name != "task" else d.parent.name, Task.is_valid_dir(d), steps)
' "$W" bench/evo/dummy && rm -rf "$W"
```

Expected, exactly:

```
dummy True [('round-1', None, 600.0, 0), ('round-2', None, 600.0, 0)]
canary-agent True [('round-1', None, 120.0, 1)]
canary-validation True [('round-1', None, 120.0, 1)]
check-agent True [('round-1', None, 120.0, 1), ('round-2', None, 120.0, 1)]
check-validation True [('round-1', None, 120.0, 1), ('round-2', None, 120.0, 1)]
probe-agent True [('round-1', 60.0, 120.0, 1), ('round-2', None, 120.0, 1)]
probe-validation True [('round-1', 60.0, 120.0, 1), ('round-2', None, 120.0, 1)]
vtimeout-agent True [('round-1', None, 20.0, 1)]
vtimeout-validation True [('round-1', None, 20.0, 1)]
```

The first line shows Harbor's 600-s step default that the override works around. Every copy must be `True`, and its step verifier timeout must equal the task's `[verifier]` value. If a copy prints `False`, or a step shows `600.0` or `0` collect hooks, the fault is in Task 5's `make_copy` or `step_toml_overrides`: fix it there, then rerun this step.

- [ ] **Step 6: Run the full suite**

Run: `sh bench/tb4/test.sh -q`
Expected: `1006 passed` (the time varies). Task 6 ended at `958 passed`, and this task adds 48 tests. If other work has landed first, expect the previous count plus this task's tests, with 0 failures.

- [ ] **Step 7: Commit**

```bash
cd "$WT" && git add bench/evo/harborcheck.py bench/evo/dummy tests/test_evo_harborcheck.py && git commit -m "feat(evo): Harbor behaviour check, timeout probe and write canary on a dummy task"
```

Run: `git show --stat --format=%s HEAD | head -20`
Expected, exactly:

```
feat(evo): Harbor behaviour check, timeout probe and write canary on a dummy task

 bench/evo/dummy/environment/Dockerfile             |   5 +
 bench/evo/dummy/environment/probe.sh               |  49 +++
 bench/evo/dummy/steps/round-1/instruction.md       |   9 +
 bench/evo/dummy/steps/round-1/solution/solve.sh    |   5 +
 .../dummy/steps/round-1/tests/round1-marker.txt    |   1 +
 bench/evo/dummy/steps/round-1/tests/test.sh        |  11 +
 bench/evo/dummy/steps/round-2/instruction.md       |   9 +
 bench/evo/dummy/steps/round-2/solution/solve.sh    |   5 +
 bench/evo/dummy/steps/round-2/tests/test.sh        |   8 +
 bench/evo/dummy/task.toml                          |  25 ++
 bench/evo/harborcheck.py                           | 434 ++++++++++++++++++++
 tests/test_evo_harborcheck.py                      | 456 +++++++++++++++++++++
 12 files changed, 1017 insertions(+)
```

Git shortens the marker file's path to fit 80 columns; it is `bench/evo/dummy/steps/round-1/tests/round1-marker.txt`.

The remaining steps are **operator reference only; do not run them while implementing this task**. Task 12 Steps 6-8 run them, in this order, after the bundle exists (Task 12 Step 5). Run now, they would create the `__a1` jobs that Task 12 expects to create, and `run_job` would refuse those with `FileExistsError`. They need Docker Desktop and Harbor; Steps 11-13 also use a few minutes of subscription usage. In them:
- `$WT` is the worktree root set in Global Constraints;
- `RUNS="$HOME/Documents/evocode-horizon-runs"`, which equals `config.RUNS`.

Every attempt writes new directories. Never delete an old attempt; rerun with `--attempt N+1`.

**How to read a failed check (Steps 9 and 11).** Each `failures` entry starts with one of these:

| Failure begins with | Meaning | Action |
| --- | --- | --- |
| `round-1: setup.sh did not empty /tmp` or `round-2: setup.sh did not empty /tmp` | `copies.SETUP_SH` did not run or left `/tmp` | Look for `setup.sh failed` in `$RUNS/jobs/<job>/*/trial.log`, fix Task 5, rerun |
| `round-2: setup.sh removed the ledger files` | `SETUP_SH` touched the project | Fix Task 5, rerun |
| `round-2: setup.sh left the ~/.claude/projects memory` | `SETUP_SH` did not remove `~/.claude/projects` | Fix Task 5, rerun |
| `round-N: setup.sh is still in the project` | `SETUP_SH` did not delete itself | Fix Task 5, rerun |
| `round-N: the collect hook did not copy the ledger` | `COLLECT_COMMAND` failed or wrote elsewhere | Look for `Collect hook` warnings in `trial.log`, fix Task 5, rerun |
| `round-2: the collect hook left the stray claude decoy running` | `COLLECT_COMMAND` did not match the decoy, whose `argv[0]` is `claude` (`claude 86399`) | Fix Task 5, rerun |
| `round-2: round 1's /tests is readable` or `round-2: round 1's /logs/verifier is readable` | Harbor did not reset the shared verifier directories | **STOP**: this contradicts Harbor 0.24.0 (#1961) and risks test leakage. Report to the user |
| `probe file ... missing`, `... was not planted`, `the stray claude decoy was not running before the collect hook`, `the verifier did not list round1-marker.txt in /tests` | The probe or the round-1 test did not run. In Step 11, this means the agent skipped or changed the command | Read the trial's `steps/round-1/agent/claude-code.txt` and rerun once. If it repeats, **STOP** and report |
| `round-N: <ExceptionType>: ...`, `round-N: reward ...`, `no trial directory with result.json` | The job itself failed | Read `$RUNS/checks/<job>/harbor.log` and the trial's `trial.log`, fix the environment, rerun |
| `image architecture ...` | The dummy image is not arm64 | Check that Docker Desktop builds natively (`docker info --format '{{.Architecture}}'` prints `aarch64`), then rerun |

- [ ] **Step 8 (operator): Preconditions**

```bash
export RUNS="$HOME/Documents/evocode-horizon-runs"
cd "$WT" && docker info --format '{{.Architecture}}' && harbor --version && python3 -c 'from bench.tb4 import run; run.read_token(); print("token ok")'
```

Expected:

```text
feat(evo): Harbor behaviour check, timeout probe and write canary on a dummy task

 bench/evo/dummy/environment/Dockerfile             |   5 +
 bench/evo/dummy/environment/probe.sh               |  49 +++
 bench/evo/dummy/steps/round-1/instruction.md       |   9 +
 bench/evo/dummy/steps/round-1/solution/solve.sh    |   5 +
 .../dummy/steps/round-1/tests/round1-marker.txt    |   1 +
 bench/evo/dummy/steps/round-1/tests/test.sh        |  11 +
 bench/evo/dummy/steps/round-2/instruction.md       |   9 +
 bench/evo/dummy/steps/round-2/solution/solve.sh    |   5 +
 bench/evo/dummy/steps/round-2/tests/test.sh        |   8 +
 bench/evo/dummy/task.toml                          |  25 ++
 bench/evo/harborcheck.py                           | 434 ++++++++++++++++++++
 tests/test_evo_harborcheck.py                      | 456 +++++++++++++++++++++
 12 files changed, 1017 insertions(+)
```
- If Docker is not running, start Docker Desktop and repeat.
- If `read_token` raises `RuntimeError: no token in Keychain service tb4-horizon-claude-token`, this is the user's one manual step. Ask the user to run `claude setup-token`, then `security add-generic-password -U -s tb4-horizon-claude-token -a "$USER" -w`, which prompts for the token so it never enters the shell history. Then repeat this step.

- [ ] **Step 9 (operator, no usage): Oracle behaviour check**

Run: `cd "$WT" && python3 -m bench.evo.harborcheck check --agent oracle`
Expected: about 3 minutes, including the first build of `evo-local/dummy:9fcae3e5539d`. Exit 0, and it prints:

```json
{
  "job_name": "check__dummy__oracle__a1",
  "ok": true,
  "failures": []
}
```

This proves the dummy, the probe and the assertions before any usage is spent. If `ok` is false, use the table above.

- [ ] **Step 10 (operator, no usage): Verifier-timeout override**

Run: `cd "$WT" && python3 -m bench.evo.harborcheck vtimeout`
Expected: about 2 minutes. Exit 0, with `"job_name": "check__dummy-vtimeout__oracle__a1"`, `"ok": true` and `"failures": []`.

If the failure reads `round-1: expected VerifierTimeoutError after 20.0 seconds, got no exception with reward 1.0`, the per-step override did not take effect. Run:

```bash
python3 -c 'import sys, tomllib; d = tomllib.load(open(sys.argv[1], "rb")); print([s["verifier"]["timeout_sec"] for s in d["steps"]])' "$RUNS/checks/check__dummy-vtimeout__oracle__a1/task/task.toml"
```

- If it prints something other than `[20.0]`, fix `copies.step_toml_overrides` (Task 5) and rerun with `--attempt 2`.
- If it prints `[20.0]`, **STOP** and report to the user: Harbor ignores the override, so every EvoCode verifier would be cut off at 600 s.

- [ ] **Step 11 (operator, a few minutes of usage): Claude Code behaviour check**

Run: `cd "$WT" && python3 -m bench.evo.harborcheck check`
Expected: about 5 minutes, because Harbor first installs Claude Code 2.1.293 and its system packages in the container. Exit 0, with `"job_name": "check__dummy__control__a1"`, `"ok": true` and `"failures": []`.

Then run: `python3 -c 'import json, os; print(json.load(open(os.path.expandvars("$RUNS/checks/check__dummy__control__a1/findings.json")))["facts"])'`
Expected:
- `user` is `root` and `pwd` is `/app`;
- `rewards` is `{'round-1': 1.0, 'round-2': 1.0}`;
- `round_2_claude_like` holds exactly one line, round 2's own `claude --verbose --output-format=stream-json` process.

If `ok` is false, use the table above.

- [ ] **Step 12 (operator, about 3 minutes of usage): Agent-timeout probe (recorded, not asserted)**

Run: `cd "$WT" && python3 -m bench.evo.harborcheck probe`
Expected: exit 0 and `"ok": null`. The `recorded` block shows:
- `round_1_exception.type`: `AgentTimeoutError`, with a message containing `after 60.0 seconds`.
- `round_1_verifier_ran`: true. `round_1_reward`: 0.0, because the agent never reached `a.txt`. `round_1_ledger_collected`: true.
- `round_2_started`: true. `round_2_reward`: 1.0.
- `running_before_collect`: a line that starts with `claude --verbose --output-format=stream-json` and ends with `--print`, and `sleep 600` lines. This shows the in-container `claude` outlives Harbor's agent timeout, as the spec says.
- `still_running_in_round_2`: `[]`. `claude_like_in_round_2`: exactly one line.

If it differs:
- **`still_running_in_round_2` contains `sleep 600`.** The collect hook stopped `claude` but missed its tool child, and the spec requires both. The trial's `steps/round-1/artifacts/logs/artifacts/round-1/survivors.txt` lists it. Fix the descendant walk in `copies.COLLECT_COMMAND` (Task 5), rerun Task 5's tests, then rerun this step with `--attempt 2`.
- **`claude_like_in_round_2` has two `claude` lines.** Round 1's `claude` survived the hook. Fix the `claude` match in `copies.COLLECT_COMMAND` (Task 5) and rerun.
- **`round_2_started` is false.** Harbor ended the chain after an agent timeout. **STOP** and report to the user: Task 9's "not reached" rows would then follow every timeout.
- **`round_1_reward` is 1.0.** The stray `claude` finished the round between the timeout and grading. **STOP** and report.

The record is `$RUNS/checks/check__dummy-probe__control__a1/findings.json`. The report's validity section cites it.

- [ ] **Step 13 (operator, about 2 minutes of usage): Write canary**

Run: `cd "$WT" && python3 -m bench.evo.harborcheck canary --skill "$RUNS/bundle/horizon"`
Expected: exit 0, with `"job_name": "canary__dummy__horizon__a1"`, `"ok": true` and `"failures": []`.

The canary runs the horizon arm's exact flags and its skill: the bundle Task 12 Step 5 built at `$RUNS/bundle/horizon`. Harbor copies the skill into the container, so the bundle on the host is not changed. The canary's extra instruction is the control text, so the agent does not start a Horizon goal.

If the failures include `no Write tool call created the file without an error`, read `facts.writes[0].result` in `$RUNS/checks/canary__dummy__horizon__a1/findings.json`. That is Claude Code refusing a write under `.claude/`. **STOP** and report to the user. The spec's fallback, relocating the gates directory through `doctor`'s `gates` path, changes the Horizon bundle and must be decided before the freeze. If the only failure is `API error: ...`, rerun with `--attempt 2` once usage allows.

- [ ] **Step 14 (operator): Summarise the evidence**

```bash
cd "$WT" && python3 - <<'EOF'
import hashlib, json
from bench.evo import config
for path in sorted((config.RUNS / "checks").glob("*/findings.json")):
    data = json.loads(path.read_text())
    print(data["job_name"], data["ok"], hashlib.sha256(path.read_bytes()).hexdigest()[:12], data["failures"])
EOF
```

Expected: one line per attempt. The latest attempt of each job reads as follows, with your own 12-character digests:

```
canary__dummy__horizon__a1 True <sha256[:12]> []
check__dummy-probe__control__a1 None <sha256[:12]> []
check__dummy-vtimeout__oracle__a1 True <sha256[:12]> []
check__dummy__control__a1 True <sha256[:12]> []
check__dummy__oracle__a1 True <sha256[:12]> []
```

There is nothing to commit, because run data stays in `$RUNS`. Task 11's pre-registration records these digests. If any line except the probe's is not `True ... []`, the freeze does not go ahead.

### Task 8: Runner

**Deviation (additive only; every skeleton name and positional signature is kept):**
- The record JSON keeps every skeleton key and adds four:
  - `container_image`: the image of the kept main container;
  - `usage_before`: the probe info the job started under;
  - `usage_after`: the last `rate_limit_event` in the last started round's stream;
  - `ledger`: `copy_ledger`'s `{"candidates","copied"}` for the final project ledger, or `null`.

  `started` and `finished` are epoch seconds. Task 10 can compute "usage consumed" from `usage_before` and `usage_after`.
- The run directory gains `logs/<job_name>.log` (Harbor's output), `ledgers/<job_name>/<i>/` (the final ledger, copied from the stopped main container that `keep_containers` leaves behind) and `runner.lock`.
- New keyword arguments:
  - `run_trial` also takes the keyword-only `info=None`, `sleep=time.sleep` and `log=print`.
  - `run_phase` takes the keyword-only `paths`, `token`, `freeze`, `probe`, `infos=None`, `run`, `now`, `sleep` and `log`, and returns `{"records": [...], "dropped": [...]}`.
  - `phase_tasks` takes the keyword-only `paths=None`.
- **Drops stop the phase.** After the second invalid run of an arm, `run_phase` appends the drop line to `deviations.md` and raises `TaskDropped`; the CLI then exits 3. A systemic failure, such as the network being down during agent setup, therefore cannot drop task after task. A relaunch skips tasks that are already dropped and runs the next unused reserve in their place (see the task list below).
- **No attempt is spent when Harbor never starts.** If Harbor exits without creating the job directory, `run_trial` raises `InfrastructureFailure` and writes no record.
- **Five-hour pacing.** Besides the seven-day rule, a job starts only while five-hour utilization is below `FIVE_HOUR_CEILING − min(worst-case hours, 5) × 0.098` (TB4's five-hour rate). One worst-case window therefore cannot push the account over the ceiling halfway through a job.
- **Inputs for the dry run.** `run_phase` makes the agent copy and both prompt files on first use (`prepare`, using Task 5's functions) and never rewrites them. The Horizon bundle `RUNS/bundle/horizon` must exist before the dry run, because the dry run's horizon arm passes `--skill`.
- **Main-phase task list and reserves.** The main phase reads `docs/evaluations/evocode-horizon/prereg.json`. `main_tasks` returns its `"tasks"`, then one entry of its `"reserve"` list for each distinct main task that `deviations.md` records as dropped, in reserve order. This is spec rule 7: a dropped task is replaced by the next unused reserve, and with no reserve left a drop stays a drop. A `prereg.json` without `"reserve"` has no reserves. `--only` accepts a reserve once it is in play. `main` imports `bench.evo.prereg.require_freeze` inside the function only, so this task stands alone before Task 11 exists.
- **Image pinned at the start of the main run.** After `require_freeze` and before `read_token`, `main` compares `validated_image(task, paths)` with `prereg.json["task_details"][task]["image_id"]` for every task `phase_tasks` returns, reserves in play included. A difference raises `ImageChanged("<task>: validated image differs from the pre-registered one")`. The reason: a validation rerun after the freeze rebuilds the image and rewrites `validation/<task>.json`, and `run_trial` compares the image only with that record.

**Files:**
- Create: `bench/evo/run.py`
- Test: `tests/test_evo_run.py`

**Interfaces:**

Consumes (exact, from earlier tasks and TB4):
- **`bench.evo.config` (Task 1):**
  - `RUNS`, `DOCS`, `DATASET_REVISION`;
  - `MODEL`, `EFFORT`, `CLAUDE_CODE_VERSION`, `MAX_OUTPUT_TOKENS`;
  - `AGENT_TIMEOUT_MULTIPLIER`, `AGENT_SETUP_TIMEOUT_MULTIPLIER`;
  - `SEED`, `WALKTHROUGH`, `ARMS`, `WEEKLY_CEILING`, `FIVE_HOUR_CEILING`;
  - `prompt_text(arm, minutes) -> str`, `image_tag(task) -> str`, `job_name(phase, task, arm, attempt) -> str`.
- **`bench.evo.dataset` (Task 2):**
  - `list_tasks(tasks_root) -> list[dict]`. The runner uses the keys `task`, `dir` (the original's path), `rounds` (an int) and `agent_timeout_sec`.
  - `minutes(task_info) -> int`.
- **`bench.evo.order` (Task 3):** `arm_order(task, seed) -> tuple[str, str]`.
  - Pool tasks at positions 2k and 2k+1 of the seeded order get opposite arm orders.
  - A task outside the pool, such as the walkthrough, gets its own seeded coin; for `WALKTHROUGH` that is `("horizon", "control")`.
  - The tests call `arm_order` at test time, so they pin no particular order.
- **`bench.evo.copies` (Task 5):**
  - `make_copy(src_task_dir, dest_dir, *, kind, image_tag) -> str`;
  - `write_prompt(dest, arm, minutes) -> Path`, where `dest` is the prompt file's own path.
- **`bench.evo.steps` (Task 6):** `read_trial(trial_dir) -> dict`.
  - The runner reads `steps[*].name`, `.exception`, `.agent_started` and `.agent_dir` (an absolute path), plus `trial_exception`.
  - An exception may be an exception-type string or an ExceptionInfo dict; both are accepted.
  - An unreached step is either absent or has `agent_started` and `exception` both null.
- **`bench.evo.images` (Task 6):** `inspect(tag, run) -> {"id","arch"}`.
- **`validation/<task>.json` (Task 6):** `ok`, `reason` and `image_id` (the `docker image inspect` `Id`, `sha256:` followed by 64 hex digits).
- **Task 11:**
  - `bench.evo.prereg.require_freeze(run) -> str`, called as `require_freeze(subprocess.run)`;
  - `prereg.json` keys `"tasks"`, `"reserve"` (a list in order; optional) and `"task_details"`, whose `[task]["image_id"]` Task 11 writes for every measured task and every reserve.
- **TB4, by import and unchanged:**
  - `bench.tb4.run.{read_token, agent_env, probe_usage, wait_for_headroom, hold_lock, sleep_marker, running_trials, compose_containers, copy_ledger, remove_containers, cleanup_job, job_result, USAGE_EXCEPTIONS, ImageChanged, RunnerBusy, InfrastructureFailure}`;
  - `bench.tb4.stream.{read_events, usage_limit_evidence, terminal_api_error, rate_limit_info, ledger_candidates}`.

Produces (`bench/evo/run.py`):
- **`Paths(root)`**, a frozen dataclass.
  - Properties: `tasks` (`dataset/<DATASET_REVISION>/tasks`), `agent_copies`, `prompts`, `skill` (`bundle/horizon`), `validation`, `jobs`, `records`, `ledgers`, `logs`, `deviations`, `lock`.
  - Methods: `copy(task)`, `prompt(task, arm)` (`prompts/<task>__<arm>.md`) and `ensure()`.
- **`EvoTrial(phase, task, arm, attempt)`**, a frozen dataclass. `.job_name` is `config.job_name(...)`.
- **The Harbor command.** `harbor_command(trial, paths) -> list` builds the skeleton's flags; `--skill RUNS/bundle/horizon` is passed for horizon only, and `--resume-trajectory` is never passed.
- **Pacing:**
  - `worst_case_hours(task_info) -> float` = rounds × `agent_timeout_sec` × multiplier / 3600;
  - `needed_weekly(hours) -> float` = hours × 0.0127;
  - `headroom_limits(task_info) -> (five_limit, weekly_limit)`.
- **Inputs:**
  - `task_infos(paths) -> {task: info}` and `prepare(info, paths) -> Path`;
  - `validated_image(task, paths) -> str`, the `image_id` of a passing `validation/<task>.json`. It raises `NotValidated` when the record is missing or did not pass.
- **Validity:**
  - `started_steps(read) -> [(step, events)]`;
  - `classify(read, slept) -> str | None`. It checks, in order: `interrupted` (a trial-level `CancelledError`), `usage_limit`, `api_error`, `infrastructure`, then `host_slept`.
  - A timed-out round's cut-off last line is not counted as evidence.
- **`run_trial(trial, paths, token, freeze, *, run=subprocess.run, now=time.time, probe, info=None, sleep=time.sleep, log=print) -> dict`.** It writes and returns `records/<job_name>.json`.
- **`run_phase(phase, tasks, *, attempt=1, paths, token, freeze, probe, infos=None, run=subprocess.run, now=time.time, sleep=time.sleep, log=print) -> dict`.**
- **Drops:**
  - `dropped_tasks(paths, phase) -> list[str]`, read by `phase_tasks` and by Task 10's report CLI.
  - `note_drop(paths, phase, task, arm, attempts) -> str` writes the line `- <phase> <task>: dropped after two invalid runs of the <arm> arm (a1: <reason>; a2: <reason>)`.
- **Task list:**
  - `main_tasks(prereg: dict, dropped) -> list` = `list(prereg["tasks"]) + list(prereg.get("reserve") or [])[:len(set(dropped))]`. Task 10's report CLI builds its planned list with it.
  - `phase_tasks(phase, prereg_path, only=None, *, paths=None) -> list`. It returns `[WALKTHROUGH]` for `dry`. For `main` it returns `main_tasks(prereg, dropped_tasks(paths, "main"))`, or just the pre-registered tasks when `paths` is None. `only` must name a task in that list, or it raises `SystemExit`.
- **CLI:** `main(argv=None, *, require_freeze=None) -> int`, run as `python3 -m bench.evo.run --phase dry|main [--only TASK] [--attempt N] [--runs DIR] [--prereg FILE] [--print-commands]`.
  - It calls `phase_tasks(..., paths=Paths(--runs))`.
  - For `--phase main` it calls `require_freeze`, then checks every task's validated image against `prereg.json["task_details"]` and raises `ImageChanged` on a difference, all before `read_token`.
  - It exits 3, printing `stopped: <drop line>` to stderr, when a task is dropped.
- **Exceptions:** `NotValidated`, `MissingInput`, `TaskDropped`, with `ImageChanged`, `RunnerBusy` and `InfrastructureFailure` re-exported from TB4.

- [ ] **Step 1: Write the failing test**

Create `tests/test_evo_run.py`:

```python
import dataclasses
import json
import subprocess
from pathlib import Path

import pytest

from bench.evo import config, copies, images, order, run

TASK = "theme_d1_w2_code_build_brownfield_modification"
TRIAL_NAME = "theme_d1_w2_code_build_brownfiel__AbC1234"  # Harbor: task_name[:32] + "__" + 7 random chars
PROJECT = "theme_d1_w2_code_build_brownfiel__abc1234__env"
IMAGE = "sha256:" + "a" * 64
INFO = {"task": TASK, "dir": "/nonexistent/original", "rounds": 10,
        "step_names": [f"round-{i}" for i in range(1, 11)], "agent_timeout_sec": 1800.0,
        "verifier_timeout_sec": 1800.0, "reward_strategy": "mean"}
TRIAL = run.EvoTrial(phase="main", task=TASK, arm="horizon", attempt=1)
RECORD_KEYS = {"job_name", "phase", "task", "arm", "attempt", "started", "finished", "exit_code", "job_dir",
               "trial_dir", "valid", "invalid_reason", "image_id", "freeze", "harbor_version",
               "claude_code_version", "command"}

INIT = {"type": "system", "subtype": "init", "cwd": "/app", "model": "claude-sonnet-5-5",
        "claude_code_version": "2.1.293"}
ALLOWED = {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed", "unifiedWindows": {
    "five_hour": {"utilization": 0.2, "resetsAt": 5000}, "seven_day": {"utilization": 0.3, "resetsAt": 9000}}}}
REJECTED = {"type": "rate_limit_event", "rate_limit_info": {"status": "rejected", "rateLimitType": "five_hour",
                                                            "resetsAt": 5000}}
SPOKE = {"type": "assistant", "parent_tool_use_id": None, "message": {"model": "claude-sonnet-5-5",
                                                                     "content": [{"type": "text", "text": "Working."}]}}
DONE = {"type": "result", "subtype": "success", "is_error": False, "result": "Done."}
API_500 = {"type": "result", "subtype": "success", "is_error": True, "api_error_status": 500,
           "result": "API Error: 500 Internal server error"}
OVERLOADED = {"type": "assistant", "parent_tool_use_id": None, "error": "overloaded",
              "message": {"model": "<synthetic>", "content": [{"type": "text", "text": "API Error: overloaded"}]}}
CLEAN = (ALLOWED, INIT, SPOKE, DONE)
HEADROOM = {"status": "allowed", "five_hour": 0.1, "seven_day": 0.2}


def cp(cmd, code=0, out=""):
    return subprocess.CompletedProcess(cmd, code, out, "")


def lines(*events):
    return "".join(json.dumps(e) + "\n" for e in events)


def step(name, events=CLEAN, exception=None, started=True):
    return {"name": name, "events": events, "exception": exception, "started": started}


def ready(tmp_path, image=IMAGE, ok=True, skill=True):
    paths = run.Paths(tmp_path / "runs").ensure()
    paths.copy(TASK).mkdir(parents=True)
    for arm in config.ARMS:
        paths.prompt(TASK, arm).write_text(config.prompt_text(arm, 30) + "\n")
    if skill:
        paths.skill.mkdir(parents=True)
        (paths.skill / "SKILL.md").write_text("---\nname: horizon\n---\n")
    (paths.validation / f"{TASK}.json").write_text(json.dumps(
        {"task": TASK, "image_id": image, "arch": "arm64", "ok": ok, "reason": None if ok else "nop passed"}))
    return paths


@pytest.fixture(autouse=True)
def pinned_image(monkeypatch):
    """images.inspect (Task 6) asks Docker; here every tag resolves to IMAGE, and the asked tags are kept."""
    asked = []
    monkeypatch.setattr(images, "inspect", lambda tag, run=None: asked.append(tag) or {"id": IMAGE, "arch": "arm64"})
    return asked


def box(cid, image, project, service="main"):
    return {"Id": cid, "Image": image, "Config": {"Labels": {
        "com.docker.compose.project": project, "com.docker.compose.service": service}}}


class FakeHost:
    """Answers docker, sysctl and harbor calls; `harbor run` writes a multi-step trial as Harbor 0.24.0 does."""

    def __init__(self, steps=None, trial_exception=None, sleeps=("s0",), running="", container_image=IMAGE,
                 write_result=True, exit_code=0, create_job=True):
        self.calls, self.harbor_kwargs = [], None
        self.steps = steps if steps is not None else [step("round-1"), step("round-2")]
        self.trial_exception, self.sleeps, self.running = trial_exception, list(sleeps), running
        self.container_image = container_image
        self.write_result, self.exit_code, self.create_job = write_result, exit_code, create_job

    def harbor(self, cmd, kwargs):
        self.harbor_kwargs = kwargs
        if not self.create_job:
            return
        job_dir = Path(cmd[cmd.index("-o") + 1]) / cmd[cmd.index("--job-name") + 1]
        trial_dir = job_dir / TRIAL_NAME
        trial_dir.mkdir(parents=True)
        (job_dir / "lock.json").write_text(json.dumps({"schema_version": 3, "harbor": {"version": "0.24.0"}}))
        if not self.write_result:
            return
        results = []
        for item in self.steps:
            step_dir = trial_dir / "steps" / item["name"]
            for sub in ("agent", "verifier", "artifacts"):
                (step_dir / sub).mkdir(parents=True)
            if item["started"]:
                (step_dir / "agent" / "claude-code.txt").write_text(lines(*item["events"]))
                (step_dir / "verifier" / "reward.txt").write_text("1\n")
            exception = None
            if item["exception"]:
                exception = {"exception_type": item["exception"], "exception_message": "",
                             "exception_traceback": "", "occurred_at": "2026-10-09T12:30:00Z"}
            results.append({
                "step_name": item["name"], "agent_result": None, "exception_info": exception,
                "verifier_result": {"rewards": {"reward": 1.0}} if item["started"] else None,
                "agent_execution": ({"started_at": "2026-10-09T12:00:00Z", "finished_at": "2026-10-09T12:20:00Z"}
                                    if item["started"] else None),
                "verifier": None})
        trial_exception = None
        if self.trial_exception:
            trial_exception = {"exception_type": self.trial_exception, "exception_message": "",
                               "exception_traceback": "", "occurred_at": "2026-10-09T12:00:00Z"}
        (trial_dir / "result.json").write_text(json.dumps({
            "task_name": TASK, "trial_name": TRIAL_NAME, "agent_info": {"name": "claude-code", "version": "2.1.293"},
            "exception_info": trial_exception, "step_results": results}))

    def __call__(self, cmd, **kwargs):
        self.calls.append(list(cmd))
        if cmd[:1] == ["sysctl"]:
            return cp(cmd, 0, self.sleeps.pop(0) if len(self.sleeps) > 1 else self.sleeps[0])
        if cmd[:2] == ["harbor", "run"]:
            self.harbor(cmd, kwargs)
            return cp(cmd, self.exit_code)
        if cmd[:3] == ["docker", "ps", "-q"]:
            return cp(cmd, 0, self.running)
        if cmd[:3] == ["docker", "ps", "-aq"]:
            return cp(cmd, 0, "c1\nc9\n")
        if cmd[:2] == ["docker", "inspect"]:
            if cmd[2:] == ["r1"]:
                return cp(cmd, 0, json.dumps([box("r1", IMAGE, "other_task__xyz9876__env")]))
            return cp(cmd, 0, json.dumps([box("c1", self.container_image, PROJECT),
                                          box("c9", IMAGE, "other_task__xyz9876__env")]))
        if cmd[:2] == ["docker", "cp"]:
            (Path(cmd[3]) / "goals").mkdir(parents=True)
            return cp(cmd)
        return cp(cmd)


def ran_harbor(host):
    return any(c[:2] == ["harbor", "run"] for c in host.calls)


def go(paths, host, trial=TRIAL, probe=lambda: HEADROOM, now=None, slept=None):
    return run.run_trial(trial, paths, "secret-token", "abc123", run=host, now=now or iter([5.0, 10.0, 20.0]).__next__,
                         probe=probe, info=INFO, sleep=(slept if slept is not None else []).append,
                         log=lambda message: None)


def _without(cmd, *flags):
    cmd = list(cmd)
    for flag in flags:
        if flag in cmd:
            i = cmd.index(flag)
            del cmd[i:i + 2]
    return cmd


def test_both_arms_share_every_flag_but_prompt_job_name_and_skill(tmp_path):
    paths = run.Paths(tmp_path)
    control = run.harbor_command(dataclasses.replace(TRIAL, arm="control"), paths)
    horizon = run.harbor_command(TRIAL, paths)
    assert horizon[-2:] == ["--skill", str(tmp_path / "bundle" / "horizon")] and "--skill" not in control
    assert control[control.index("--extra-instruction-path") + 1] == str(tmp_path / "prompts" / f"{TASK}__control.md")
    assert horizon[horizon.index("--extra-instruction-path") + 1] == str(tmp_path / "prompts" / f"{TASK}__horizon.md")
    assert horizon[horizon.index("--job-name") + 1] == f"main__{TASK}__horizon__a1" == TRIAL.job_name
    flags = ("--extra-instruction-path", "--job-name", "--skill")
    assert _without(control, *flags) == _without(horizon, *flags)


def test_fixed_flags_secret_by_reference_and_no_resume(tmp_path):
    cmd = run.harbor_command(TRIAL, run.Paths(tmp_path))
    assert cmd[:2] == ["harbor", "run"] and "-y" in cmd
    assert [cmd[i + 1] for i, x in enumerate(cmd) if x == "--ae"] == [
        "CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN}", "CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000"]
    assert [cmd[i + 1] for i, x in enumerate(cmd) if x == "--ak"] == ["version=2.1.293", "disable_web_search=true"]
    for flag, value in (("-p", str(tmp_path / "copies" / "agent" / TASK)), ("-a", "claude-code"),
                        ("-m", "anthropic/claude-sonnet-5-5"), ("--effort", "high"),
                        ("--agent-timeout-multiplier", "1.0"), ("--agent-setup-timeout-multiplier", "2.0"),
                        ("--ek", "keep_containers=true"), ("-n", "1"), ("-k", "1"), ("-r", "0"),
                        ("-o", str(tmp_path / "jobs"))):
        assert cmd[cmd.index(flag) + 1] == value
    assert "--no-force-build" in cmd and "--force-build" not in cmd
    assert not any("resume" in x for x in cmd) and not any("FORCE_OAUTH" in x for x in cmd)


def test_worst_case_hours_and_weekly_need():
    assert run.worst_case_hours(INFO) == 5.0
    assert run.worst_case_hours(dict(INFO, rounds=5, agent_timeout_sec=3600.0)) == 5.0
    assert run.needed_weekly(5.0) == pytest.approx(0.0635)
    assert run.needed_weekly(0) == 0


def test_headroom_limits_leave_room_for_the_worst_case():
    assert run.headroom_limits(INFO) == (0.41, 0.8365)
    assert run.headroom_limits(dict(INFO, agent_timeout_sec=3600.0)) == (0.41, 0.773)
    assert run.headroom_limits(dict(INFO, rounds=2)) == (0.802, 0.8873)


@pytest.mark.parametrize("busy,seconds", [
    ({"status": "allowed", "five_hour": 0.1, "seven_day": 0.85, "seven_day_resets_at": 5000}, 4960),
    ({"status": "allowed", "five_hour": 0.45, "five_hour_resets_at": 3000, "seven_day": 0.2}, 2960),
])
def test_run_trial_waits_until_both_windows_have_room(tmp_path, busy, seconds):
    paths, host, slept = ready(tmp_path), FakeHost(), []
    probes = iter([busy, {"status": "allowed", "five_hour": 0.1, "seven_day": 0.8}])
    record = go(paths, host, probe=lambda: next(probes), now=lambda: 100.0, slept=slept)
    assert slept == [seconds] and ran_harbor(host)
    assert record["usage_before"]["seven_day"] == 0.8 and record["valid"] is True


def test_a_valid_job_is_recorded_with_versions_ledger_and_cleanup(tmp_path, pinned_image):
    paths, host = ready(tmp_path), FakeHost()
    record = go(paths, host)
    assert pinned_image == [config.image_tag(TASK)]
    assert RECORD_KEYS <= set(record)
    assert record["valid"] is True and record["invalid_reason"] is None
    assert (record["job_name"], record["phase"], record["task"], record["arm"], record["attempt"]) == (
        f"main__{TASK}__horizon__a1", "main", TASK, "horizon", 1)
    assert (record["started"], record["finished"], record["exit_code"]) == (10.0, 20.0, 0)
    assert record["image_id"] == record["container_image"] == IMAGE and record["freeze"] == "abc123"
    assert (record["harbor_version"], record["claude_code_version"]) == ("0.24.0", "2.1.293")
    assert record["job_dir"] == str(paths.jobs / TRIAL.job_name)
    assert record["trial_dir"] == str(paths.jobs / TRIAL.job_name / TRIAL_NAME)
    assert record["command"] == run.harbor_command(TRIAL, paths)
    assert record["ledger"]["copied"] == {"/app/.claude/harness": str(paths.ledgers / TRIAL.job_name / "0")}
    assert record["usage_after"]["seven_day"] == 0.3
    assert ["docker", "rm", "-f", "c1"] in host.calls
    assert host.harbor_kwargs["env"]["CLAUDE_CODE_OAUTH_TOKEN"] == "secret-token"
    saved = (paths.records / f"{TRIAL.job_name}.json").read_text()
    assert json.loads(saved) == record and "secret-token" not in saved
    assert (paths.logs / f"{TRIAL.job_name}.log").exists()


def started(tmp_path, name, *events, exception=None, tail=""):
    agent = tmp_path / name / "agent"
    agent.mkdir(parents=True)
    (agent / "claude-code.txt").write_text(lines(*events) + tail)
    return {"name": name, "exception": exception, "agent_started": "2026-10-09T12:00:00Z", "agent_dir": str(agent)}


def not_started(name, exception=None):
    return {"name": name, "exception": exception, "agent_started": None, "agent_dir": None}


def test_classify_fixture_streams(tmp_path):
    ok = started(tmp_path, "round-1", *CLEAN)

    def read(*steps, trial_exception=None):
        return {"steps": list(steps), "trial_exception": trial_exception}

    assert run.classify(None, False) == "infrastructure"
    assert run.classify(read(ok), False) is None
    assert run.classify(read(ok), True) == "host_slept"
    assert run.classify(read(ok, started(tmp_path, "round-2", ALLOWED, INIT, REJECTED, DONE)), False) == "usage_limit"
    assert run.classify(read(ok, started(tmp_path, "round-3", *CLEAN, exception="ApiUsageLimitError")),
                        False) == "usage_limit"
    assert run.classify(read(ok, started(tmp_path, "round-4", ALLOWED, INIT, SPOKE, API_500)), False) == "api_error"
    assert run.classify(read(ok, started(tmp_path, "round-5", ALLOWED, INIT, OVERLOADED, SPOKE, DONE)), False) is None
    assert run.classify(read(not_started("round-1", "RuntimeError")), False) == "infrastructure"
    assert run.classify(read(ok, not_started("round-2", "RuntimeError")), False) == "infrastructure"
    assert run.classify(read(trial_exception="EnvironmentStartTimeoutError"), False) == "infrastructure"
    assert run.classify(read(ok, trial_exception={"exception_type": "CancelledError"}), False) == "interrupted"
    timed_out = started(tmp_path, "round-6", ALLOWED, INIT, SPOKE, exception="AgentTimeoutError")
    assert run.classify(read(ok, timed_out, not_started("round-7")), False) is None
    cut = started(tmp_path, "round-8", ALLOWED, INIT, SPOKE, exception="AgentTimeoutError",
                  tail='{"type": "rate_limit_event", "rate_limit_info": {"status": "allo')
    assert run.classify(read(ok, cut), False) is None
    said = started(tmp_path, "round-9", ALLOWED, INIT, SPOKE, tail="API Error: Repeated 529 Overloaded errors\n")
    assert run.classify(read(ok, said), False) == "api_error"


def test_usage_limit_in_a_later_round_invalidates_the_job(tmp_path):
    host = FakeHost(steps=[step("round-1"), step("round-2", (ALLOWED, INIT, REJECTED, DONE),
                                                     "NonZeroAgentExitCodeError")])
    record = go(ready(tmp_path), host)
    assert (record["valid"], record["invalid_reason"]) == (False, "usage_limit")


def test_a_job_without_a_trial_result_is_infrastructure_and_cleaned(tmp_path):
    host = FakeHost(write_result=False, exit_code=1)
    record = go(ready(tmp_path), host)
    assert (record["valid"], record["invalid_reason"], record["exit_code"]) == (False, "infrastructure", 1)
    assert ["docker", "rm", "-f", "c1"] in host.calls and record["ledger"] is None


def test_harbor_failing_before_the_job_exists_stops_without_spending_the_attempt(tmp_path):
    paths = ready(tmp_path)
    with pytest.raises(run.InfrastructureFailure):
        go(paths, FakeHost(create_job=False, exit_code=2))
    assert not (paths.records / f"{TRIAL.job_name}.json").exists()


def test_setup_failure_before_any_agent_is_infrastructure(tmp_path):
    record = go(ready(tmp_path), FakeHost(steps=[step("round-1", exception="RuntimeError", started=False)]))
    assert (record["invalid_reason"], record["ledger"]) == ("infrastructure", None)


def test_a_host_sleep_during_the_job_invalidates_it(tmp_path):
    record = go(ready(tmp_path), FakeHost(sleeps=("before", "after")))
    assert record["invalid_reason"] == "host_slept"


def test_a_container_on_another_image_is_infrastructure(tmp_path):
    record = go(ready(tmp_path), FakeHost(container_image="sha256:" + "b" * 64))
    assert record["invalid_reason"] == "infrastructure"


def test_an_existing_record_is_returned_without_running(tmp_path):
    paths, host = ready(tmp_path), FakeHost()
    (paths.records / f"{TRIAL.job_name}.json").write_text(json.dumps({"job_name": TRIAL.job_name, "valid": True}))
    assert go(paths, host)["valid"] is True and host.calls == []


def test_a_job_directory_without_a_record_is_recorded_as_interrupted(tmp_path):
    paths, host = ready(tmp_path), FakeHost()
    (paths.jobs / TRIAL.job_name / TRIAL_NAME).mkdir(parents=True)
    record = go(paths, host, probe=lambda: pytest.fail("an interruption is recorded without waiting"))
    assert (record["valid"], record["invalid_reason"]) == (False, "interrupted")
    assert ["docker", "rm", "-f", "c1"] in host.calls and not ran_harbor(host)
    assert json.loads((paths.records / f"{TRIAL.job_name}.json").read_text()) == record


def test_running_trial_containers_block_a_new_job(tmp_path):
    host = FakeHost(running="r1\n")
    with pytest.raises(run.RunnerBusy):
        go(ready(tmp_path), host)
    assert not ran_harbor(host)


@pytest.mark.parametrize("kwargs,error", [
    (dict(image="sha256:" + "c" * 64), run.ImageChanged),
    (dict(ok=False), run.NotValidated),
    (dict(skill=False), run.MissingInput),
])
def test_bad_inputs_stop_before_harbor(tmp_path, kwargs, error):
    host = FakeHost()
    with pytest.raises(error):
        go(ready(tmp_path, **kwargs), host)
    assert not ran_harbor(host)
    assert not (tmp_path / "runs" / "records" / f"{TRIAL.job_name}.json").exists()


def test_control_needs_no_skill_and_a_missing_prompt_stops(tmp_path):
    paths = ready(tmp_path, skill=False)
    assert go(paths, FakeHost(), trial=dataclasses.replace(TRIAL, arm="control"))["valid"] is True
    paths.prompt(TASK, "horizon").unlink()
    paths.skill.mkdir(parents=True)
    (paths.skill / "SKILL.md").write_text("---\nname: horizon\n---\n")
    with pytest.raises(run.MissingInput):
        go(paths, FakeHost())


def fake_runs(monkeypatch, outcomes):
    """Patch run_trial: outcomes maps (arm, attempt) to an invalid_reason; anything missing is valid."""
    calls = []

    def fake(trial, paths, token, freeze, **kwargs):
        calls.append((trial.task, trial.arm, trial.attempt))
        reason = outcomes.get((trial.arm, trial.attempt))
        return {"job_name": trial.job_name, "valid": reason is None, "invalid_reason": reason}

    monkeypatch.setattr(run, "run_trial", fake)
    monkeypatch.setattr(run, "prepare", lambda info, paths: paths.copy(info["task"]))
    return calls


def phase(tmp_path, tasks=(TASK,)):
    infos = {task: dict(INFO, task=task) for task in tasks}
    return run.run_phase("main", list(tasks), paths=run.Paths(tmp_path).ensure(), token="t", freeze="f",
                         probe=lambda: HEADROOM, infos=infos, log=lambda message: None)


def test_run_phase_runs_each_task_in_its_seeded_arm_order(tmp_path, monkeypatch):
    calls = fake_runs(monkeypatch, {})
    other = "theme_d5_w1_data_engineering_greenfield_implementation"
    result = phase(tmp_path, tasks=(TASK, other))
    expected = [(t, arm, 1) for t in (TASK, other) for arm in order.arm_order(t, config.SEED)]
    assert calls == expected and result["dropped"] == [] and len(result["records"]) == 4


def test_run_phase_refuses_a_task_missing_from_the_dataset(tmp_path, monkeypatch):
    calls = fake_runs(monkeypatch, {})
    with pytest.raises(run.MissingInput):
        run.run_phase("main", [TASK, "theme_d0_w0_unknown"], paths=run.Paths(tmp_path).ensure(), token="t",
                      freeze="f", probe=lambda: HEADROOM, infos={TASK: INFO}, log=lambda message: None)
    assert calls == []


def test_an_invalid_run_is_retried_once_with_the_next_attempt(tmp_path, monkeypatch):
    first, second = order.arm_order(TASK, config.SEED)
    calls = fake_runs(monkeypatch, {(first, 1): "api_error"})
    result = phase(tmp_path)
    assert calls == [(TASK, first, 1), (TASK, first, 2), (TASK, second, 1)]
    assert result["dropped"] == [] and not (tmp_path / "deviations.md").exists()


def test_two_invalid_runs_drop_the_task_once_and_stop_the_phase(tmp_path, monkeypatch):
    first, second = order.arm_order(TASK, config.SEED)
    calls = fake_runs(monkeypatch, {(first, 1): "usage_limit", (first, 2): "infrastructure"})
    with pytest.raises(run.TaskDropped):
        phase(tmp_path)
    assert calls == [(TASK, first, 1), (TASK, first, 2)]
    text = (tmp_path / "deviations.md").read_text()
    assert text == (f"- main {TASK}: dropped after two invalid runs of the {first} arm "
                    f"(a1: usage_limit; a2: infrastructure)\n")
    assert run.dropped_tasks(run.Paths(tmp_path), "main") == [TASK]
    assert run.dropped_tasks(run.Paths(tmp_path), "dry") == []
    calls.clear()
    assert phase(tmp_path)["dropped"] == [TASK]
    assert calls == [] and (tmp_path / "deviations.md").read_text() == text


def test_a_second_arm_that_fails_twice_drops_the_task(tmp_path, monkeypatch):
    first, second = order.arm_order(TASK, config.SEED)
    calls = fake_runs(monkeypatch, {(second, 1): "interrupted", (second, 2): "api_error"})
    with pytest.raises(run.TaskDropped):
        phase(tmp_path)
    assert calls == [(TASK, first, 1), (TASK, second, 1), (TASK, second, 2)]
    assert run.dropped_tasks(run.Paths(tmp_path), "main") == [TASK]


def test_prepare_makes_the_agent_copy_and_prompts_once(tmp_path, monkeypatch):
    paths = run.Paths(tmp_path).ensure()
    made = []

    def make_copy(src, dest, *, kind, image_tag):
        made.append((Path(src), Path(dest).name, kind, image_tag))
        Path(dest).mkdir(parents=True)
        (Path(dest) / "task.toml").write_text("")
        return "digest"

    monkeypatch.setattr(copies, "make_copy", make_copy)
    stale = paths.copy(TASK).with_name(TASK + ".tmp")
    stale.mkdir(parents=True)
    info = dict(INFO, agent_timeout_sec=3600.0)
    assert run.prepare(info, paths) == paths.copy(TASK)
    assert made == [(Path(INFO["dir"]), TASK + ".tmp", "agent", config.image_tag(TASK))]
    assert (paths.copy(TASK) / "task.toml").exists() and not stale.exists()
    for arm in config.ARMS:
        assert paths.prompt(TASK, arm).read_text().strip() == config.prompt_text(arm, 60)
    paths.prompt(TASK, "control").write_text("kept")
    run.prepare(info, paths)
    assert len(made) == 1 and paths.prompt(TASK, "control").read_text() == "kept"


def test_phase_tasks(tmp_path):
    prereg = tmp_path / "prereg.json"
    prereg.write_text(json.dumps({"tasks": [TASK, "theme_d5_w1_data_engineering_greenfield_implementation"]}))
    assert run.phase_tasks("dry", prereg) == [config.WALKTHROUGH]
    assert run.phase_tasks("main", prereg) == [TASK, "theme_d5_w1_data_engineering_greenfield_implementation"]
    assert run.phase_tasks("main", prereg, only=TASK) == [TASK]
    with pytest.raises(SystemExit):
        run.phase_tasks("main", prereg, only=config.WALKTHROUGH)


OTHER = "theme_d5_w1_data_engineering_greenfield_implementation"
RESERVE = ["theme_d8_w5_cloud_devops_integration_e2e_wiring", "theme_d9_w11_security_automation_scripting"]


def drop(paths, task):
    run.note_drop(paths, "main", task, "control", [(1, "infrastructure"), (2, "infrastructure")])


def test_one_dropped_main_task_brings_in_the_first_reserve(tmp_path):
    paths = run.Paths(tmp_path)
    prereg = tmp_path / "prereg.json"
    prereg.write_text(json.dumps({"tasks": [TASK, OTHER], "reserve": RESERVE}))
    assert run.phase_tasks("main", prereg, paths=paths) == [TASK, OTHER]
    drop(paths, TASK)
    assert run.phase_tasks("main", prereg, paths=paths) == [TASK, OTHER, RESERVE[0]]
    assert run.phase_tasks("main", prereg, only=RESERVE[0], paths=paths) == [RESERVE[0]]
    with pytest.raises(SystemExit):
        run.phase_tasks("main", prereg, only=RESERVE[1], paths=paths)
    assert run.phase_tasks("dry", prereg, paths=paths) == [config.WALKTHROUGH]
    assert run.main_tasks({"tasks": [TASK], "reserve": RESERVE}, [TASK]) == [TASK, RESERVE[0]]


def test_two_drops_with_one_reserve_bring_in_only_that_reserve(tmp_path):
    paths = run.Paths(tmp_path)
    prereg = tmp_path / "prereg.json"
    prereg.write_text(json.dumps({"tasks": [TASK, OTHER], "reserve": RESERVE[:1]}))
    drop(paths, TASK)
    drop(paths, OTHER)
    assert run.phase_tasks("main", prereg, paths=paths) == [TASK, OTHER, RESERVE[0]]
    no_reserve = tmp_path / "old.json"
    no_reserve.write_text(json.dumps({"tasks": [TASK, OTHER]}))
    assert run.phase_tasks("main", no_reserve, paths=paths) == [TASK, OTHER]


def test_main_prints_both_arms_in_seeded_order(tmp_path, capsys):
    assert run.main(["--phase", "dry", "--runs", str(tmp_path), "--print-commands"]) == 0
    printed = capsys.readouterr().out.splitlines()
    arms = order.arm_order(config.WALKTHROUGH, config.SEED)
    assert len(printed) == 2
    for line, arm in zip(printed, arms):
        assert f"--job-name dry__{config.WALKTHROUGH}__{arm}__a1 " in line
        assert ("--skill" in line) == (arm == "horizon")
    assert not (tmp_path / "records").exists()


def test_main_checks_the_freeze_before_reading_the_token(tmp_path, monkeypatch):
    prereg = tmp_path / "prereg.json"
    prereg.write_text(json.dumps({"tasks": [TASK]}))
    monkeypatch.setattr(run, "read_token", lambda *a, **k: pytest.fail("token read before the freeze check"))

    class Unfrozen(RuntimeError):
        pass

    def require_freeze(git):
        raise Unfrozen("tag evo-prereg is missing")

    with pytest.raises(Unfrozen):
        run.main(["--phase", "main", "--runs", str(tmp_path / "runs"), "--prereg", str(prereg)],
                 require_freeze=require_freeze)


class TokenRead(Exception):
    """Raised by the patched read_token: main got past every check that comes before the token."""


def frozen_main(paths, prereg, monkeypatch):
    def token(*args, **kwargs):
        raise TokenRead

    monkeypatch.setattr(run, "read_token", token)
    return run.main(["--phase", "main", "--runs", str(paths.root), "--prereg", str(prereg)],
                    require_freeze=lambda git: "abc")


def test_main_refuses_a_validated_image_that_differs_from_the_preregistered_one(tmp_path, monkeypatch):
    paths = ready(tmp_path)
    prereg = tmp_path / "prereg.json"
    prereg.write_text(json.dumps({"tasks": [TASK], "task_details": {TASK: {"image_id": "sha256:" + "d" * 64}}}))
    with pytest.raises(run.ImageChanged, match=TASK):
        frozen_main(paths, prereg, monkeypatch)
    prereg.write_text(json.dumps({"tasks": [TASK], "task_details": {TASK: {"image_id": IMAGE}}}))
    with pytest.raises(TokenRead):
        frozen_main(paths, prereg, monkeypatch)


def test_main_checks_the_preregistered_image_of_a_reserve_once_it_is_in_play(tmp_path, monkeypatch):
    paths = ready(tmp_path)
    (paths.validation / f"{RESERVE[0]}.json").write_text(json.dumps(
        {"task": RESERVE[0], "image_id": IMAGE, "arch": "arm64", "ok": True, "reason": None}))
    prereg = tmp_path / "prereg.json"
    details = {TASK: {"image_id": IMAGE}, RESERVE[0]: {"image_id": "sha256:" + "e" * 64}}
    prereg.write_text(json.dumps({"tasks": [TASK], "reserve": RESERVE[:1], "task_details": details}))
    with pytest.raises(TokenRead):  # the reserve is not in play yet
        frozen_main(paths, prereg, monkeypatch)
    drop(paths, TASK)
    with pytest.raises(run.ImageChanged, match=RESERVE[0]):
        frozen_main(paths, prereg, monkeypatch)
```

The last four tests cover the reserves that replace dropped tasks and the check of the pre-registered image at the start of the main run.

- [ ] **Step 2: Run it to make sure it fails**

Run: `sh bench/tb4/test.sh -q tests/test_evo_run.py`
Expected: FAIL during collection with `E   ImportError: cannot import name 'run' from 'bench.evo' (/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode/bench/evo/__init__.py)`, then `ERROR tests/test_evo_run.py`, `Interrupted: 1 error during collection` and `1 error in 0.09s` (the time varies).

- [ ] **Step 3: Implement**

Create `bench/evo/run.py`:

```python
"""Run one Harbor multi-step job per (task, arm, attempt, phase), paced by subscription usage, and keep the evidence."""
import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from bench.evo import config, copies, dataset, images, order, steps
from bench.tb4 import stream
from bench.tb4.run import (USAGE_EXCEPTIONS, ImageChanged, InfrastructureFailure, RunnerBusy, agent_env, cleanup_job,
                           compose_containers, copy_ledger, hold_lock, job_result, probe_usage, read_token,
                           remove_containers, running_trials, sleep_marker, wait_for_headroom)

PHASES = ("dry", "main")
WEEKLY_PER_AGENT_HOUR = 0.0127    # TB4: seven-day utilization one agent-hour used
FIVE_HOUR_PER_AGENT_HOUR = 0.098  # TB4: five-hour utilization one agent-hour used
FIVE_HOUR_WINDOW_HOURS = 5.0


class NotValidated(RuntimeError):
    """The task has no passing validation record, so it may not run."""


class MissingInput(FileNotFoundError):
    """The agent copy, the arm's prompt file or the Horizon bundle is missing."""


class TaskDropped(RuntimeError):
    """An arm was invalid twice; the task is dropped and the phase stops for the operator to look."""


@dataclass(frozen=True)
class Paths:
    root: Path

    @property
    def tasks(self): return self.root / "dataset" / config.DATASET_REVISION / "tasks"

    @property
    def agent_copies(self): return self.root / "copies" / "agent"

    @property
    def prompts(self): return self.root / "prompts"

    @property
    def skill(self): return self.root / "bundle" / "horizon"

    @property
    def validation(self): return self.root / "validation"

    @property
    def jobs(self): return self.root / "jobs"

    @property
    def records(self): return self.root / "records"

    @property
    def ledgers(self): return self.root / "ledgers"

    @property
    def logs(self): return self.root / "logs"

    @property
    def deviations(self): return self.root / "deviations.md"

    @property
    def lock(self): return self.root / "runner.lock"

    def copy(self, task: str) -> Path:
        return self.agent_copies / task

    def prompt(self, task: str, arm: str) -> Path:
        return self.prompts / f"{task}__{arm}.md"

    def ensure(self):
        for d in (self.agent_copies, self.prompts, self.validation, self.jobs, self.records, self.ledgers, self.logs):
            d.mkdir(parents=True, exist_ok=True)
        return self


@dataclass(frozen=True)
class EvoTrial:
    phase: str
    task: str
    arm: str
    attempt: int

    @property
    def job_name(self) -> str:
        return config.job_name(self.phase, self.task, self.arm, self.attempt)


def harbor_command(trial: EvoTrial, paths: Paths) -> list:
    cmd = ["harbor", "run", "-p", str(paths.copy(trial.task)), "-a", "claude-code", "-m", config.MODEL,
           "--effort", config.EFFORT,
           "--ak", f"version={config.CLAUDE_CODE_VERSION}",
           "--ak", "disable_web_search=true",
           "--ae", "CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN}",
           "--ae", f"CLAUDE_CODE_MAX_OUTPUT_TOKENS={config.MAX_OUTPUT_TOKENS}",
           "--agent-timeout-multiplier", str(config.AGENT_TIMEOUT_MULTIPLIER),
           "--agent-setup-timeout-multiplier", str(config.AGENT_SETUP_TIMEOUT_MULTIPLIER),
           "--extra-instruction-path", str(paths.prompt(trial.task, trial.arm)),
           "--ek", "keep_containers=true", "--no-force-build",
           "-n", "1", "-k", "1", "-r", "0",
           "-o", str(paths.jobs), "--job-name", trial.job_name, "-y"]
    if trial.arm == "horizon":
        cmd += ["--skill", str(paths.skill)]
    return cmd


def worst_case_hours(task_info: dict) -> float:
    """Agent-hours if every round runs to its timeout."""
    return task_info["rounds"] * task_info["agent_timeout_sec"] * config.AGENT_TIMEOUT_MULTIPLIER / 3600


def needed_weekly(hours: float) -> float:
    return hours * WEEKLY_PER_AGENT_HOUR


def headroom_limits(task_info: dict) -> tuple:
    """(five-hour, seven-day) utilization above which a job waits, so its worst case stays under the ceilings."""
    hours = worst_case_hours(task_info)
    five = config.FIVE_HOUR_CEILING - min(hours, FIVE_HOUR_WINDOW_HOURS) * FIVE_HOUR_PER_AGENT_HOUR
    return round(five, 4), round(config.WEEKLY_CEILING - needed_weekly(hours), 4)


def task_infos(paths: Paths) -> dict:
    return {info["task"]: info for info in dataset.list_tasks(paths.tasks)}


def prepare(info: dict, paths: Paths) -> Path:
    """Make the agent copy and both prompt files once; an existing copy or prompt is never rewritten."""
    task = info["task"]
    dest = paths.copy(task)
    if not dest.exists():
        tmp = dest.with_name(dest.name + ".tmp")
        if tmp.exists():
            shutil.rmtree(tmp)
        copies.make_copy(Path(info["dir"]), tmp, kind="agent", image_tag=config.image_tag(task))
        os.replace(tmp, dest)
    for arm in config.ARMS:
        if not paths.prompt(task, arm).exists():
            copies.write_prompt(paths.prompt(task, arm), arm, dataset.minutes(info))
    return dest


def _write_json(path: Path, obj) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def _exception_type(value):
    if isinstance(value, dict):
        return value.get("exception_type")
    return value or None


def started_steps(read: dict) -> list:
    """(step, stream-json events) for every step whose agent phase started, in step order."""
    return [(s, stream.read_events(Path(s["agent_dir"]) / "claude-code.txt") if s.get("agent_dir") else [])
            for s in read["steps"] if s.get("agent_started")]


def _evidence(step: dict, events: list) -> list:
    """A timed-out round's claude is killed mid-write, so a cut-off last line is not evidence."""
    if _exception_type(step.get("exception")) == "AgentTimeoutError" and events and events[-1].get("type") == "_raw":
        return events[:-1]
    return events


def classify(read, slept: bool):
    """Why a finished job is invalid ("interrupted", "usage_limit", "api_error", "infrastructure",
    "host_slept"), or None when it is valid. `read` is steps.read_trial output, or None without a result."""
    if read is None:
        return "infrastructure"
    trial_exception = _exception_type(read.get("trial_exception"))
    if trial_exception == "CancelledError":
        return "interrupted"
    started = started_steps(read)
    events = [_evidence(s, e) for s, e in started]
    if (any(_exception_type(s.get("exception")) in USAGE_EXCEPTIONS for s, _ in started)
            or any(stream.usage_limit_evidence(e) for e in events)):
        return "usage_limit"
    if any(stream.terminal_api_error(e) for e in events):
        return "api_error"
    if (trial_exception or not started
            or any(s.get("exception") and not s.get("agent_started") for s in read["steps"])):
        return "infrastructure"
    if slept:
        return "host_slept"
    return None


def _cwd(events):
    return next((e.get("cwd") for e in events if e.get("type") == "system" and e.get("subtype") == "init"), None)


def final_ledger(streams, containers, dest: Path, run=subprocess.run):
    """Copy the project ledger from the kept main container: the state after the last round."""
    candidates = []
    for events in streams:
        cwd = _cwd(events)
        for path in (stream.ledger_candidates(events, cwd) if cwd else []):
            if path not in candidates:
                candidates.append(path)
    return copy_ledger(containers, candidates, dest, run) if candidates else None


def _harbor_version(job_dir: Path):
    try:
        return json.loads((job_dir / "lock.json").read_text())["harbor"]["version"]
    except (OSError, ValueError, KeyError, TypeError):
        return None


def validated_image(task: str, paths: Paths) -> str:
    path = paths.validation / f"{task}.json"
    if not path.exists():
        raise NotValidated(f"{task}: no validation record; run python3 -m bench.evo.validate first")
    data = json.loads(path.read_text())
    if not data.get("ok"):
        raise NotValidated(f"{task}: validation did not pass: {data.get('reason')}")
    return data["image_id"]


def check_inputs(trial: EvoTrial, paths: Paths) -> None:
    needed = [paths.copy(trial.task), paths.prompt(trial.task, trial.arm)]
    if trial.arm == "horizon":
        needed.append(paths.skill / "SKILL.md")
    missing = [str(p) for p in needed if not p.exists()]
    if missing:
        raise MissingInput(f"{trial.job_name}: missing {missing}")


def new_record(trial: EvoTrial, paths: Paths, freeze: str) -> dict:
    return {"job_name": trial.job_name, "phase": trial.phase, "task": trial.task, "arm": trial.arm,
            "attempt": trial.attempt, "started": None, "finished": None, "exit_code": None,
            "job_dir": str(paths.jobs / trial.job_name), "trial_dir": None, "valid": False, "invalid_reason": None,
            "image_id": None, "container_image": None, "freeze": freeze, "harbor_version": None,
            "claude_code_version": None, "command": harbor_command(trial, paths), "usage_before": None,
            "usage_after": None, "ledger": None}


def _finish(record: dict, trial: EvoTrial, paths: Paths, trial_dir: Path, result: dict, slept: bool, run) -> None:
    record["trial_dir"] = str(trial_dir)
    record["harbor_version"] = _harbor_version(paths.jobs / trial.job_name)
    record["claude_code_version"] = (result.get("agent_info") or {}).get("version")
    read = steps.read_trial(trial_dir)
    streams = [events for _, events in started_steps(read)]
    containers = compose_containers(result.get("trial_name") or trial_dir.name, run)
    main = next((c for c in containers if c["role"] == "agent" and c["service"] == "main"), None)
    record["container_image"] = main["image"] if main else None
    reason = classify(read, slept)
    if reason is None and main is not None and main["image"] != record["image_id"]:
        reason = "infrastructure"  # Harbor ran another image than the validated one
    record["invalid_reason"] = reason
    record["usage_after"] = next((info for info in map(stream.rate_limit_info, reversed(streams)) if info), None)
    if streams:
        record["ledger"] = final_ledger(streams, containers, paths.ledgers / trial.job_name, run)
    remove_containers(containers, run)


def run_trial(trial: EvoTrial, paths: Paths, token: str, freeze: str, *, run=subprocess.run, now=time.time,
              probe, info=None, sleep=time.sleep, log=print) -> dict:
    record_path = paths.records / f"{trial.job_name}.json"
    if record_path.exists():
        return json.loads(record_path.read_text())
    busy = running_trials(run)
    if busy:
        raise RunnerBusy(f"trial containers still running: {busy}")
    record = new_record(trial, paths, freeze)
    job_dir = paths.jobs / trial.job_name
    if job_dir.exists():  # an earlier runner stopped mid-job: record it without waiting or running
        cleanup_job(job_dir, run)
        record["invalid_reason"] = "interrupted"
        _write_json(record_path, record)
        return record
    check_inputs(trial, paths)
    expected = validated_image(trial.task, paths)
    found = images.inspect(config.image_tag(trial.task), run)["id"]
    if found != expected:
        raise ImageChanged(f"{trial.task}: {config.image_tag(trial.task)} is {found}, validated {expected}")
    record["image_id"] = expected
    five_limit, weekly_limit = headroom_limits(info or task_infos(paths)[trial.task])
    record["usage_before"] = wait_for_headroom(probe, five_limit, weekly_limit, sleep=sleep, now=now, log=log)
    slept_before = sleep_marker(run)
    record["started"] = now()
    with open(paths.logs / f"{trial.job_name}.log", "w") as out:
        proc = run(record["command"], env=agent_env(token), stdout=out, stderr=subprocess.STDOUT)
    record["finished"], record["exit_code"] = now(), proc.returncode
    slept = sleep_marker(run) != slept_before
    if not job_dir.exists():  # Harbor never started the job: fix the command or host; no attempt is spent
        raise InfrastructureFailure(f"{trial.job_name}: harbor exited {proc.returncode} without a job directory; "
                                    f"see {paths.logs / (trial.job_name + '.log')}")
    trial_dir, result = job_result(job_dir)
    if trial_dir is None:
        cleanup_job(job_dir, run)
        record["invalid_reason"] = "infrastructure"
    else:
        _finish(record, trial, paths, trial_dir, result, slept, run)
    record["valid"] = record["invalid_reason"] is None
    _write_json(record_path, record)
    return record


def _drop_prefix(phase: str, task: str) -> str:
    return f"- {phase} {task}: dropped"


def dropped_tasks(paths: Paths, phase: str) -> list:
    """Tasks of this phase that deviations.md records as dropped, in the order they were dropped."""
    if not paths.deviations.exists():
        return []
    pattern = re.compile(rf"^- {re.escape(phase)} (\S+): dropped\b", re.M)
    return pattern.findall(paths.deviations.read_text())


def note_drop(paths: Paths, phase: str, task: str, arm: str, attempts: list) -> str:
    """Append the drop line; `attempts` is [(attempt number, invalid_reason), ...]."""
    detail = "; ".join(f"a{number}: {reason}" for number, reason in attempts)
    line = f"{_drop_prefix(phase, task)} after two invalid runs of the {arm} arm ({detail})\n"
    with open(paths.deviations, "a") as handle:
        handle.write(line)
    return line


def run_phase(phase: str, tasks, *, attempt: int = 1, paths: Paths, token: str, freeze: str, probe, infos=None,
              run=subprocess.run, now=time.time, sleep=time.sleep, log=print) -> dict:
    """Run each task's arms in its seeded order; retry an invalid run once; two invalid runs drop the task."""
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {PHASES}")
    infos = task_infos(paths) if infos is None else infos
    missing = [task for task in tasks if task not in infos]
    if missing:
        raise MissingInput(f"{missing} not in {paths.tasks}; run python3 -m bench.evo.dataset fetch first")
    records, dropped = [], []
    for task in tasks:
        if task in dropped_tasks(paths, phase):
            log(f"{task}: dropped earlier in phase {phase}; skipping")
            dropped.append(task)
            continue
        prepare(infos[task], paths)
        for arm in order.arm_order(task, config.SEED):
            attempts = []
            for number in (attempt, attempt + 1):
                record = run_trial(EvoTrial(phase, task, arm, number), paths, token, freeze, run=run, now=now,
                                   probe=probe, info=infos[task], sleep=sleep, log=log)
                records.append(record)
                log(f"{record['job_name']}: {'valid' if record['valid'] else record['invalid_reason']}")
                if record["valid"]:
                    break
                attempts.append((number, record["invalid_reason"]))
            else:
                raise TaskDropped(note_drop(paths, phase, task, arm, attempts).strip())
    return {"records": records, "dropped": dropped}


def main_tasks(prereg: dict, dropped) -> list:
    """The pre-registered tasks, then one reserve per main task dropped so far, in reserve order: a drop is
    replaced by the next unused reserve, and with none left it stays a drop."""
    reserve = list(prereg.get("reserve") or [])
    return list(prereg["tasks"]) + reserve[:len(set(dropped))]


def phase_tasks(phase: str, prereg_path: Path, only=None, *, paths=None) -> list:
    """The walkthrough task for the dry run; for the main run the pre-registered list plus the reserves that
    replace the main tasks deviations.md records as dropped under `paths` (none without `paths`)."""
    if phase == "dry":
        tasks = [config.WALKTHROUGH]
    else:
        dropped = dropped_tasks(paths, "main") if paths is not None else []
        tasks = main_tasks(json.loads(Path(prereg_path).read_text()), dropped)
    if only is None:
        return tasks
    if only not in tasks:
        raise SystemExit(f"--only {only}: not a {phase} task ({', '.join(tasks)})")
    return [only]


def main(argv=None, *, require_freeze=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=PHASES, required=True)
    parser.add_argument("--only", help="run just this task of the phase")
    parser.add_argument("--attempt", type=int, default=1, help="first attempt number (a retry uses the next)")
    parser.add_argument("--runs", type=Path, default=config.RUNS)
    parser.add_argument("--prereg", type=Path, default=config.DOCS / "prereg.json")
    parser.add_argument("--print-commands", action="store_true", help="print the Harbor commands and exit")
    args = parser.parse_args(argv)
    paths = Paths(args.runs)
    tasks = phase_tasks(args.phase, args.prereg, args.only, paths=paths)
    if args.print_commands:
        for task in tasks:
            for arm in order.arm_order(task, config.SEED):
                trial = EvoTrial(args.phase, task, arm, args.attempt)
                print(" ".join(shlex.quote(x) for x in harbor_command(trial, paths)))
        return 0
    paths.ensure()
    with hold_lock(paths.lock):
        freeze = "pre-freeze"
        if args.phase == "main":
            if require_freeze is None:
                from bench.evo.prereg import require_freeze  # Task 11; imported late so this module stands alone
            freeze = require_freeze(subprocess.run)
            # a validation rerun after the freeze rebuilds the image and rewrites its record: refuse that here
            pinned = json.loads(Path(args.prereg).read_text())["task_details"]
            for task in tasks:
                if validated_image(task, paths) != (pinned.get(task) or {}).get("image_id"):
                    raise ImageChanged(f"{task}: validated image differs from the pre-registered one")
        token = read_token()
        try:
            result = run_phase(args.phase, tasks, attempt=args.attempt, paths=paths, token=token, freeze=freeze,
                               probe=lambda: probe_usage(token))
        except TaskDropped as error:
            print(f"stopped: {error}", file=sys.stderr)
            return 3
    print(json.dumps({"phase": args.phase, "dropped": result["dropped"],
                      "records": [[r["job_name"], r["valid"], r["invalid_reason"]] for r in result["records"]]}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `sh bench/tb4/test.sh -q tests/test_evo_run.py`
Expected: `34 passed in 0.11s` (the time varies).

The `run_trial` tests read the FakeHost trial through the real `steps.read_trial` (Task 6). The fixture uses Harbor 0.24.0's field names:
- `result.json` `step_results[].step_name`;
- `.agent_execution.started_at`;
- `.exception_info.exception_type`;
- `agent_info.version`;
- the job's `lock.json` `harbor.version`;
- the `steps/<name>/{agent,verifier,artifacts}` layout.

If only those tests fail, compare `read_trial`'s keys with the skeleton before changing either side.

- [ ] **Step 5: Confirm TB4's runner is untouched**

Run: `sh bench/tb4/test.sh -q tests/test_evo_run.py tests/test_tb4_run.py && git diff --stat -- bench/tb4/run.py bench/tb4/stream.py`
Expected: `69 passed in 0.31s` (34 here and TB4's 35; the time varies), and no diff output.

- [ ] **Step 6: Render the dry-phase commands (no Docker, no subscription, no files written)**

Run: `cd "$WT" && python3 -m bench.evo.run --phase dry --print-commands`
Expected: exactly these two lines, and nothing created under `~/Documents/evocode-horizon-runs`:

```text
harbor run -p /Users/deeps/Documents/evocode-horizon-runs/copies/agent/theme_d11_w2_scientific_numerical_brownfield_modification__not_1234 -a claude-code -m anthropic/claude-sonnet-5-5 --effort high --ak version=2.1.293 --ak disable_web_search=true --ae 'CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN}' --ae CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000 --agent-timeout-multiplier 1.0 --agent-setup-timeout-multiplier 2.0 --extra-instruction-path /Users/deeps/Documents/evocode-horizon-runs/prompts/theme_d11_w2_scientific_numerical_brownfield_modification__not_1234__horizon.md --ek keep_containers=true --no-force-build -n 1 -k 1 -r 0 -o /Users/deeps/Documents/evocode-horizon-runs/jobs --job-name dry__theme_d11_w2_scientific_numerical_brownfield_modification__not_1234__horizon__a1 -y --skill /Users/deeps/Documents/evocode-horizon-runs/bundle/horizon
harbor run -p /Users/deeps/Documents/evocode-horizon-runs/copies/agent/theme_d11_w2_scientific_numerical_brownfield_modification__not_1234 -a claude-code -m anthropic/claude-sonnet-5-5 --effort high --ak version=2.1.293 --ak disable_web_search=true --ae 'CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN}' --ae CLAUDE_CODE_MAX_OUTPUT_TOKENS=128000 --agent-timeout-multiplier 1.0 --agent-setup-timeout-multiplier 2.0 --extra-instruction-path /Users/deeps/Documents/evocode-horizon-runs/prompts/theme_d11_w2_scientific_numerical_brownfield_modification__not_1234__control.md --ek keep_containers=true --no-force-build -n 1 -k 1 -r 0 -o /Users/deeps/Documents/evocode-horizon-runs/jobs --job-name dry__theme_d11_w2_scientific_numerical_brownfield_modification__not_1234__control__a1 -y
```

- Horizon runs first: the walkthrough is outside the pool, so `order.arm_order(WALKTHROUGH, 20261009)` is its own seeded coin, `("horizon", "control")`.
- Only the horizon line ends with `--skill /Users/deeps/Documents/evocode-horizon-runs/bundle/horizon`.
- No token value appears; `--ae 'CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN}'` passes it by reference.

If a flag differs, fix `harbor_command` (and its test) before committing. If the module fails to import, an earlier task's module is missing a name listed under Consumes.

- [ ] **Step 7: Run the whole suite**

Run: `sh bench/tb4/test.sh -q`
Expected: `1040 passed` (1006 after Task 7 + 34; the time varies). If other work has landed first, expect the previous count plus this task's tests, with 0 failures.

- [ ] **Step 8: Commit**

```bash
cd "$WT"
git add bench/evo/run.py tests/test_evo_run.py
git commit -m "feat(evo): run one Harbor multi-step job per task and arm, paced, validity-classified and retried once"
```

Run: `git show --stat --format=%s HEAD | head -20`
Expected:

```text
feat(evo): run one Harbor multi-step job per task and arm, paced, validity-classified and retried once

 bench/evo/run.py      | 429 ++++++++++++++++++++++++++++++++++++++
 tests/test_evo_run.py | 559 ++++++++++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 988 insertions(+)
```

```text
feat(evo): run one Harbor multi-step job per task and arm, paced, validity-classified and retried once

 bench/evo/run.py      | 429 ++++++++++++++++++++++++++++++++++++++
 tests/test_evo_run.py | 562 ++++++++++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 991 insertions(+)
```

### Task 9: Scoring

**Deviation:**
1. The Row has 25 keys: the skeleton's 23, then two more after `"label"`. `score.ROW_FIELDS` is the authoritative key order.
   - `"audit_flags"` is a sorted list drawn from `"verifier-files"` (a tool input names `/tests`, `/solution` or `/logs/verifier`) and `"benchmark-source"` (`EvoCodeBench` or `UniPat-AI`). The spec's validity check "No agent read `/tests` or `/logs/verifier`" has no other producer.
   - `"leftovers"` holds counts from the collect hook's audit files for the round: `{"killed", "survivors", "tmp_entries"}`, or `null` when no audit was collected. The spec's validity check "the leftover-file audit: what survives a round" has no other producer.
2. `round_rows(record, *, runs=config.RUNS)` and `score_phase(phase, *, runs=config.RUNS)` gain a keyword-only `runs`. Calls in the skeleton's form still work. The reason: a round that was never reached is missing from Harbor's `result.json` `step_results`, and `config.json` keeps `-p` unresolved, so it may be relative. The full round list is therefore read from the agent copy's `runs/copies/agent/<task>/task.toml` (`[[steps]] name`, in order).
3. The module also has a `main` CLI: `python3 -m bench.evo.score --phase dry|main [--runs DIR]`. It prints only `{"phase","rows","out"}`.

**Files:**
- Create: `bench/evo/score.py`
- Test: `tests/test_evo_score.py`

**Interfaces:**
- Consumes:
  - `bench.evo.config.RUNS` (Task 1).
  - `bench.evo.steps.read_trial(trial_dir) -> {"steps": [{"name","index","reward","exception","agent_started","agent_finished","agent_dir","verifier_dir","artifacts_dir"}], "trial_exception"}` and `bench.evo.steps.case_counts(step_dir) -> tuple[int, int] | None` (Task 6). The tuple is read as `(passed, total)`, that is `(success_count, total_cases)`, in Row order. `step_dir` is `<trial_dir>/steps/<name>`.
  - The scorer tolerates these Task 6 representations:
    - `exception` as either the `exception_type` string or the whole `exception_info` dict;
    - `agent_started` and `agent_finished` as ISO strings or `datetime`;
    - `artifacts_dir` as either `<step>/artifacts` (the round's folder is then `logs/artifacts/<round>`, Docker's bind mount of `/logs/artifacts`) or the convention dir itself (the round's folder is then `<round>`).
  - Record JSON keys `job_name`, `phase`, `task`, `arm`, `attempt`, `trial_dir` and `valid` (Task 8).
  - From Task 5: the agent copy's rewritten `task.toml`, and `COLLECT_COMMAND(round_name)`, whose `round_name` is the Harbor step name. Into `/logs/artifacts/<round>/` it copies `$PWD/.claude/harness` to `harness/`, and writes `killed.txt` (`KILLED_FILE`: the claude processes and their descendants that were still running, which it kills), `tmp-ls.txt` (`TMP_LISTING_FILE`: `ls -la /tmp`) and `survivors.txt` (`SURVIVORS_FILE`: other processes still running, PID 1 and the hook itself left out). Each line of `killed.txt` and `survivors.txt` is `<pid> <command line>`. The scorer only counts lines and never copies a name or command into a Row.
  - From TB4, unchanged:
    - `bench.tb4.stream.read_events`, `final_result`, `horizon_calls` and `tool_uses`;
    - `bench.tb4.score.parse_time`.
- Produces:
  - `ROW_FIELDS: tuple[str, ...]` (25 keys).
  - `round_rows(record: dict, *, runs: Path = config.RUNS) -> list[dict]`: one Row per task round, in `task.toml` order.
  - `score_phase(phase: str, *, runs: Path = config.RUNS) -> int`: rewrites `runs/rows.jsonl` and returns the phase's row count.
  - `ledger_goals(artifacts_dir: Path, round_name: str) -> dict[str, dict]`
  - `goal_fields(previous: dict, current: dict, calls: list) -> dict`
  - `leftovers(artifacts_dir: Path, round_name: str) -> dict | None`
  - `audit_flags(events: list) -> list[str]`
  - `round_names(task_dir: Path) -> list[str]`
  - `load_records(runs: Path, phase: str) -> list[dict]`: valid records only.
  - `main(argv=None) -> int`
- Row semantics that Task 10 relies on:
  - `reward` is a float, and 0.0 when the step has no reward.
  - `passed` is `reward >= 1.0`.
  - A round the trial never reached has `reward` 0.0, `passed` false and `end_reason` `"not reached"`. Its other values are `null`, `0`, `[]` or `{}`.
  - On a timed-out step (`AgentTimeoutError`), `final_message`, `tokens_in`, `tokens_out` and `cost_usd` are `null`. This is TB4's rule: a result line written after the deadline is not the answer. `result_records` still counts that line.
  - `tokens_in` sums `inputTokens`, `cacheReadInputTokens` and `cacheCreationInputTokens`. `tokens_out` sums `outputTokens`. Both are taken over every model in the last result record's `modelUsage`.
  - `goal_slugs` is a sorted list. `goal_status` is a `{slug: status}` dict holding the status at the end of the round.
  - A goal belongs to a round when its ledger file is new in that round's copy, or differs from the previous round's copy. A read-only `status` call does not count.
  - `horizon_started` is true only when the round created a goal: a new slug in the ledger, or a `start` call that returned a summary. Continuing an earlier goal does not count.
  - `end_reason` is one of `"complete"`, `"abandoned"`, `"active"`, `"no goal"`, `"unknown"` or `"not reached"`. It is the status of the touched goal that started last. When no ledger copy was collected, it is the status from the last summary that a ledger-changing call (`start`, `check`, `review`, `complete`, `abandon` or `reopen`) printed.
  - `audit_flags` is a sorted list, `[]` when nothing matched. It covers the tool inputs in the step's `claude-code.txt` and in its session files under `agent/sessions/projects`.
  - `leftovers` is `{"killed": int, "survivors": int | None, "tmp_entries": int | None}` when the round's folder has a `killed.txt`, and `null` otherwise (no audit collected, or the round was not reached). `survivors` is `null` when `survivors.txt` is missing. `tmp_entries` counts the `tmp-ls.txt` lines other than the `total` line, `.` and `..`.
  - `label` is `null` until Task 10 sets it. `score_phase` keeps an existing label only while the row's `final_message` is unchanged.

- [ ] **Step 1: Write the failing test**

Create `tests/test_evo_score.py`. The fixtures write a Harbor 0.24.0 multi-step trial with the real field names: `result.json` `step_results[]` with `step_name`, `verifier_result.rewards.reward`, `exception_info.exception_type` and `agent_execution.started_at`/`finished_at`, plus `steps/<name>/{agent,verifier,artifacts}`. Every stream, reward, CASE_SUMMARY line, ledger file and audit file is synthetic. The CASE_SUMMARY line uses the only documented form (EvoCode README at f8fcfaa): `CASE_SUMMARY total_cases=<n> success_count=<k>`. The last test writes the collect hook's `killed.txt`, `survivors.txt` and `tmp-ls.txt`.

```python
import json

import pytest

from bench.evo import score

TASK = "theme_d0_w0_demo_task"
INIT = {"type": "system", "subtype": "init", "claude_code_version": "2.1.293", "model": "claude-sonnet-5-5"}
USAGE = {"claude-sonnet-5-5": {"inputTokens": 10, "outputTokens": 20, "cacheReadInputTokens": 30,
                               "cacheCreationInputTokens": 40, "costUSD": 1.5}}
HARNESS = 'python3 "$HARNESS" --runtime claude --project /app '


def done(text="All requirements are met.", cost=1.5, usage=USAGE):
    return {"type": "result", "subtype": "success", "is_error": False, "result": text,
            "total_cost_usd": cost, "modelUsage": usage}


def say(text):
    return {"type": "assistant", "parent_tool_use_id": None,
            "message": {"content": [{"type": "text", "text": text}]}}


def use(id_, command, name="Bash", key="command"):
    return {"type": "assistant", "parent_tool_use_id": None, "message": {"content": [
        {"type": "tool_use", "id": id_, "name": name, "input": {key: command}}]}}


def res(id_, content, is_error=False):
    return {"type": "user", "parent_tool_use_id": None, "message": {"content": [
        {"type": "tool_result", "tool_use_id": id_, "content": content, "is_error": is_error}]}}


def goal(slug, status, started="2026-10-09T12:01:00+00:00", turns=1):
    return {"slug": slug, "goal": slug, "cwd": "/app", "status": status,
            "gate": {"kind": "command", "value": "make test"}, "watch_paths": ["/app"],
            "budget": {"max_turns": 200, "max_minutes": 25}, "used": {"turns": turns, "started": started},
            "checkpoints": [], "subagents": []}


def start_call(id_, slug):
    return [use(id_, HARNESS + f'start "{slug}" --gate "make test" --max-turns 200 --max-minutes 25'),
            res(id_, json.dumps(goal(slug, "active", turns=0), indent=2))]


def write_task(runs, names):
    task_dir = runs / "copies" / "agent" / TASK
    task_dir.mkdir(parents=True, exist_ok=True)
    steps = "".join(f'[[steps]]\nname = "{n}"\n\n[steps.agent]\ntimeout_sec = 1800\n\n' for n in names)
    (task_dir / "task.toml").write_text('version = "1.0"\n\n[agent]\ntimeout_sec = 1800\n\n' + steps)


def lines(events):
    return "\n".join(e["text"] if e.get("type") == "_raw" else json.dumps(e) for e in events) + "\n"


def make_job(runs, names, rounds, *, arm="horizon", phase="main", attempt=1, valid=True):
    """A finished Harbor 0.24.0 multi-step job on disk; `rounds` holds one spec per step that ran."""
    write_task(runs, names)
    job = f"{phase}__{TASK}__{arm}__a{attempt}"
    trial_dir = runs / "jobs" / job / "theme_d0_w0_demo_tas__AbC1234"
    step_results = []
    for index, (name, spec) in enumerate(zip(names, rounds)):
        step_dir = trial_dir / "steps" / name
        (step_dir / "agent").mkdir(parents=True)
        (step_dir / "agent" / "claude-code.txt").write_text(lines(spec.get("events", [])))
        if spec.get("sessions"):
            session = step_dir / "agent" / "sessions" / "projects" / "-app" / "s1.jsonl"
            session.parent.mkdir(parents=True)
            session.write_text(lines(spec["sessions"]))
        (step_dir / "verifier").mkdir()
        reward = spec.get("reward", 1)
        if reward is not None:
            (step_dir / "verifier" / "reward.txt").write_text(f"{reward}\n")
        if spec.get("cases"):
            passed, total = spec["cases"]
            (step_dir / "verifier" / "test-stdout.txt").write_text(
                f"running checks\nCASE_SUMMARY total_cases={total} success_count={passed}\n")
        if spec.get("goals") is not None:
            goals = step_dir / "artifacts" / "logs" / "artifacts" / name / "harness" / "goals"
            goals.mkdir(parents=True)
            for item in spec["goals"]:
                (goals / f"{item['slug']}.json").write_text(json.dumps(item))
        started, finished = f"2026-10-09T{12 + index:02d}:00:00Z", f"2026-10-09T{12 + index:02d}:10:00Z"
        exception = spec.get("exception")
        step_results.append({
            "step_name": name, "agent_result": None,
            "verifier_result": {"rewards": {"reward": reward}} if reward is not None else None,
            "exception_info": {"exception_type": exception, "exception_message": "x",
                               "exception_traceback": "", "occurred_at": finished} if exception else None,
            "agent_execution": {"started_at": started, "finished_at": finished},
            "verifier": {"started_at": finished, "finished_at": finished}})
    (trial_dir / "result.json").write_text(json.dumps({
        "task_name": TASK, "trial_name": trial_dir.name, "step_results": step_results,
        "verifier_result": None, "exception_info": None}))
    return {"job_name": job, "phase": phase, "task": TASK, "arm": arm, "attempt": attempt,
            "job_dir": str(runs / "jobs" / job), "trial_dir": str(trial_dir),
            "valid": valid, "invalid_reason": None if valid else "usage_limit"}


def write_records(runs, *records):
    (runs / "records").mkdir(exist_ok=True)
    for record in records:
        (runs / "records" / f"{record['job_name']}.json").write_text(json.dumps(record))


def test_one_row_per_round_with_reward_cases_time_and_tokens(tmp_path):
    names = ["r1", "r2", "r3"]
    record = make_job(tmp_path, names, [
        {"events": [INIT, say("work"), done()], "reward": 1, "cases": (4, 4)},
        {"events": [INIT, done("Partly done.")], "reward": 0, "cases": (3, 4)},
        {"events": [INIT, done()], "reward": 1.0},
    ])
    rows = score.round_rows(record, runs=tmp_path)
    assert [list(r) for r in rows] == [list(score.ROW_FIELDS)] * 3
    assert [(r["round"], r["round_index"]) for r in rows] == [("r1", 1), ("r2", 2), ("r3", 3)]
    assert [(r["reward"], r["passed"]) for r in rows] == [(1.0, True), (0.0, False), (1.0, True)]
    assert [(r["cases_passed"], r["cases_total"]) for r in rows] == [(4, 4), (3, 4), (None, None)]
    first = rows[0]
    assert {k: first[k] for k in ("job_name", "phase", "task", "arm", "attempt")} == {
        "job_name": f"main__{TASK}__horizon__a1", "phase": "main", "task": TASK, "arm": "horizon", "attempt": 1}
    assert (first["agent_sec"], first["tokens_in"], first["tokens_out"], first["cost_usd"]) == (600.0, 80, 20, 1.5)
    assert (first["final_message"], rows[1]["final_message"]) == ("All requirements are met.", "Partly done.")
    assert (first["result_records"], first["label"], first["audit_flags"]) == (1, None, [])


def test_rounds_after_the_chain_stopped_are_failed_not_reached_rows(tmp_path):
    names = ["r1", "r2", "r3", "r4"]
    record = make_job(tmp_path, names, [
        {"events": [INIT, done()], "reward": 1},
        {"events": [INIT, say("work")], "reward": None, "exception": "NonZeroAgentExitCodeError"},
    ])
    rows = score.round_rows(record, runs=tmp_path)
    assert [r["round"] for r in rows] == names
    aborted = rows[1]
    assert (aborted["reward"], aborted["passed"], aborted["final_message"], aborted["end_reason"]) == (
        0.0, False, None, "no goal")
    for row in rows[2:]:
        assert (row["reward"], row["passed"], row["end_reason"]) == (0.0, False, "not reached")
        assert (row["final_message"], row["result_records"], row["goal_slugs"], row["agent_sec"]) == (None, 0, [], None)


def test_a_record_without_a_trial_dir_is_all_not_reached(tmp_path):
    write_task(tmp_path, ["r1", "r2"])
    record = {"job_name": f"main__{TASK}__control__a1", "phase": "main", "task": TASK, "arm": "control",
              "attempt": 1, "trial_dir": None, "valid": True}
    rows = score.round_rows(record, runs=tmp_path)
    assert [(r["round_index"], r["end_reason"], r["reward"]) for r in rows] == [
        (1, "not reached", 0.0), (2, "not reached", 0.0)]


def test_a_timed_out_round_fails_and_ignores_a_late_result_line(tmp_path):
    record = make_job(tmp_path, ["r1"], [
        {"events": [INIT, say("work"), done("late claim")], "reward": 0, "exception": "AgentTimeoutError"}])
    row = score.round_rows(record, runs=tmp_path)[0]
    assert (row["reward"], row["passed"], row["result_records"]) == (0.0, False, 1)
    assert (row["final_message"], row["tokens_in"], row["tokens_out"], row["cost_usd"]) == (None, None, None, None)


def test_every_result_record_is_counted_and_the_last_one_is_the_answer(tmp_path):
    later = {"claude-sonnet-5-5": {"inputTokens": 20, "outputTokens": 40, "cacheReadInputTokens": 60,
                                   "cacheCreationInputTokens": 80},
             "claude-haiku-5": {"inputTokens": 1, "outputTokens": 2}}
    events = [INIT, done("Interim: the review is still running."), say("more"), done("All done.", 2.5, later)]
    row = score.round_rows(make_job(tmp_path, ["r1"], [{"events": events}]), runs=tmp_path)[0]
    assert (row["result_records"], row["final_message"]) == (2, "All done.")
    assert (row["tokens_in"], row["tokens_out"], row["cost_usd"]) == (161, 42, 2.5)


def test_horizon_calls_and_a_start_are_read_per_round(tmp_path):
    first = [INIT, *start_call("t1", "fix-parser"),
             use("t2", HARNESS + "check fix-parser --did a --next b"), res("t2", "{}"), done()]
    second = [INIT, use("t3", "python3 tools/harness.py status"), res("t3", "ok"), done()]
    rows = score.round_rows(make_job(tmp_path, ["r1", "r2"], [{"events": first}, {"events": second}]),
                            runs=tmp_path)
    assert (rows[0]["horizon_calls"], rows[0]["horizon_started"]) == (2, True)
    # no ledger copy was collected, so the start call's own summary is the evidence
    assert (rows[0]["goal_slugs"], rows[0]["goal_status"], rows[0]["end_reason"]) == (
        ["fix-parser"], {"fix-parser": "active"}, "active")
    # a task's own harness.py is not the Horizon CLI
    assert (rows[1]["horizon_calls"], rows[1]["horizon_started"], rows[1]["end_reason"]) == (0, False, "no goal")


def test_a_refused_start_is_no_goal(tmp_path):
    events = [INIT, use("t1", HARNESS + 'start "Fix" --gate "make test | tail -3"'),
              res("t1", '{"error": "GateUnsound", "message": "discards the exit status"}', True), done()]
    row = score.round_rows(make_job(tmp_path, ["r1"], [{"events": events}]), runs=tmp_path)[0]
    assert (row["horizon_calls"], row["horizon_started"], row["goal_slugs"], row["end_reason"]) == (
        1, False, [], "no goal")


def test_goals_are_attributed_to_the_round_that_created_or_changed_them(tmp_path):
    first_done = goal("first-request", "complete", started="2026-10-09T12:01:00+00:00", turns=3)
    second_active = goal("second-request", "active", started="2026-10-09T13:02:00+00:00")
    record = make_job(tmp_path, ["r1", "r2", "r3"], [
        {"events": [INIT, *start_call("t1", "first-request"), done()], "goals": [first_done]},
        {"events": [INIT, *start_call("t2", "second-request"), done()], "goals": [first_done, second_active]},
        {"events": [INIT, use("t3", HARNESS + "status"), res("t3", json.dumps([first_done, second_active])),
                    done()], "goals": [first_done, second_active]},
    ])
    rows = score.round_rows(record, runs=tmp_path)
    # the ledger copy at the end of the round wins over the start call's "active"
    assert (rows[0]["goal_slugs"], rows[0]["goal_status"], rows[0]["end_reason"]) == (
        ["first-request"], {"first-request": "complete"}, "complete")
    # round 1's finished goal is still in round 2's ledger but is not round 2's
    assert (rows[1]["goal_slugs"], rows[1]["goal_status"], rows[1]["end_reason"]) == (
        ["second-request"], {"second-request": "active"}, "active")
    assert rows[0]["horizon_started"] and rows[1]["horizon_started"]
    # reading the ledger with `status` touches no goal
    assert (rows[2]["horizon_calls"], rows[2]["goal_slugs"], rows[2]["horizon_started"], rows[2]["end_reason"]) == (
        1, [], False, "no goal")


def test_a_goal_continued_in_a_later_round_belongs_there_but_is_not_a_new_start(tmp_path):
    active = goal("first-request", "active", turns=1)
    finished = goal("first-request", "complete", turns=4)
    record = make_job(tmp_path, ["r1", "r2"], [
        {"events": [INIT, *start_call("t1", "first-request"), done()], "goals": [active]},
        {"events": [INIT, use("t2", HARNESS + "complete first-request"), res("t2", json.dumps(finished)), done()],
         "goals": [finished]},
    ])
    second = score.round_rows(record, runs=tmp_path)[1]
    assert (second["goal_slugs"], second["end_reason"], second["horizon_calls"]) == (["first-request"], "complete", 1)
    assert second["horizon_started"] is False


def test_the_latest_started_goal_decides_the_end_reason(tmp_path):
    older = goal("first", "abandoned", started="2026-10-09T12:01:00+00:00")
    newer = goal("second", "complete", started="2026-10-09T12:05:00+00:00")
    row = score.round_rows(make_job(tmp_path, ["r1"], [{"events": [INIT, done()], "goals": [newer, older]}]),
                           runs=tmp_path)[0]
    assert row["goal_status"] == {"first": "abandoned", "second": "complete"}
    assert (row["goal_slugs"], row["end_reason"], row["horizon_started"]) == (["first", "second"], "complete", True)


def test_an_unrecognised_goal_status_is_unknown(tmp_path):
    odd = goal("first", "paused")
    row = score.round_rows(make_job(tmp_path, ["r1"], [{"events": [INIT, done()], "goals": [odd]}]),
                           runs=tmp_path)[0]
    assert (row["goal_status"], row["end_reason"]) == ({"first": "paused"}, "unknown")


def test_ledger_copy_is_read_from_either_artifact_layout(tmp_path):
    for base in (tmp_path / "a" / "logs" / "artifacts" / "r1", tmp_path / "b" / "r1"):
        goals = base / "harness" / "goals"
        goals.mkdir(parents=True)
        (goals / "fix.json").write_text(json.dumps(goal("fix", "active")))
        (goals / "notes.json").write_text("[1, 2]")
        (goals / "broken.json").write_text("{")
    assert list(score.ledger_goals(tmp_path / "a", "r1")) == ["fix"]
    assert list(score.ledger_goals(tmp_path / "b", "r1")) == ["fix"]
    assert score.ledger_goals(tmp_path / "c", "r1") == {}


def test_audit_flags_cover_verifier_files_and_the_benchmark_source(tmp_path):
    events = [INIT, use("t1", "cat /tests/test_outputs.py"),
              use("r1", "/logs/verifier/reward.txt", name="Read", key="file_path"), done()]
    sessions = [use("s1", "git clone https://github.com/UniPat-AI/EvoCodeBench")]
    clean = [INIT, use("t1", "pytest -q /app/tests && ls /app/tests/"), done()]
    record = make_job(tmp_path, ["r1", "r2"], [{"events": events, "sessions": sessions}, {"events": clean}])
    rows = score.round_rows(record, runs=tmp_path)
    assert rows[0]["audit_flags"] == ["benchmark-source", "verifier-files"]
    assert rows[1]["audit_flags"] == []


def test_a_trial_step_missing_from_task_toml_is_an_error(tmp_path):
    record = make_job(tmp_path, ["r1", "r2"], [{"events": [INIT, done()]}, {"events": [INIT, done()]}])
    (tmp_path / "copies" / "agent" / TASK / "task.toml").write_text('[[steps]]\nname = "r1"\n')
    with pytest.raises(ValueError, match="r2"):
        score.round_rows(record, runs=tmp_path)


def test_score_phase_writes_valid_records_of_the_phase_and_keeps_labels(tmp_path):
    names = ["r1", "r2"]
    good = make_job(tmp_path, names, [{"events": [INIT, done("Finished.")], "reward": 0},
                                      {"events": [INIT, done("Also finished.")]}], arm="control")
    bad = make_job(tmp_path, names, [{"events": [INIT, done()]}], arm="horizon", valid=False)
    dry = make_job(tmp_path, names, [{"events": [INIT, done()]}], phase="dry")
    write_records(tmp_path, good, bad, dry)
    other = {field: None for field in score.ROW_FIELDS}
    other.update(job_name=f"check__{TASK}__control__a1", phase="check", task=TASK, arm="control", attempt=1,
                 round="r1", round_index=1)
    rows_path = tmp_path / "rows.jsonl"
    rows_path.write_text("".join(json.dumps(r) + "\n" for r in (
        other,
        {"job_name": good["job_name"], "phase": "main", "round": "r1", "final_message": "Finished.",
         "label": "claims_complete"},
        {"job_name": good["job_name"], "phase": "main", "round": "r2", "final_message": "Old text.",
         "label": "claims_complete"})))
    assert score.score_phase("main", runs=tmp_path) == 2
    rows = [json.loads(line) for line in rows_path.read_text().splitlines()]
    assert [(r["phase"], r["job_name"], r["round"]) for r in rows] == [
        ("check", other["job_name"], "r1"), ("main", good["job_name"], "r1"), ("main", good["job_name"], "r2")]
    # a label survives only while the message it judged is unchanged
    assert [r["label"] for r in rows[1:]] == ["claims_complete", None]
    assert score.score_phase("main", runs=tmp_path) == 2
    assert [json.loads(line)["label"] for line in rows_path.read_text().splitlines()][1:] == ["claims_complete", None]
    assert score.score_phase("dry", runs=tmp_path) == 2
    assert [json.loads(line)["phase"] for line in rows_path.read_text().splitlines()] == [
        "check", "dry", "dry", "main", "main"]


def test_cli_prints_counts_and_never_a_message(tmp_path, capsys):
    record = make_job(tmp_path, ["r1"], [{"events": [INIT, done("SECRET-FINAL-MESSAGE")]}])
    write_records(tmp_path, record)
    assert score.main(["--runs", str(tmp_path), "--phase", "main"]) == 0
    out = capsys.readouterr().out
    assert json.loads(out) == {"phase": "main", "rows": 1, "out": str(tmp_path / "rows.jsonl")}
    assert "SECRET" not in out


def test_leftovers_count_the_collect_hooks_audit_files(tmp_path):
    out = tmp_path / "logs" / "artifacts" / "r1"
    out.mkdir(parents=True)
    (out / "killed.txt").write_text("41 claude --print\n42 sleep 600\n")
    (out / "survivors.txt").write_text("1 sleep infinity\n77 python3 -m http.server 8000\n")
    (out / "tmp-ls.txt").write_text("total 8\ndrwxrwxrwt 3 root root 96 .\ndrwxr-xr-x 1 root root 4096 ..\n"
                                    "-rw-r--r-- 1 root root 2 cache.db\n")
    assert score.leftovers(tmp_path, "r1") == {"killed": 2, "survivors": 2, "tmp_entries": 1}
    assert score.leftovers(tmp_path, "r2") is None

def test_a_round_row_carries_the_leftover_counts_of_its_own_round(tmp_path):
    from pathlib import Path

    record = make_job(tmp_path, ["r1", "r2"], [{"events": [INIT, done()]}, {"events": [INIT, done()]}])
    audit = Path(record["trial_dir"]) / "steps" / "r1" / "artifacts" / "logs" / "artifacts" / "r1"
    audit.mkdir(parents=True)
    (audit / "killed.txt").write_text("41 claude --print\n")
    rows = score.round_rows(record, runs=tmp_path)
    assert rows[0]["leftovers"] == {"killed": 1, "survivors": None, "tmp_entries": None}
    assert rows[1]["leftovers"] is None
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `sh bench/tb4/test.sh -q tests/test_evo_score.py`
Expected: FAIL at collection with `ImportError: cannot import name 'score' from 'bench.evo'`, followed by the path of the worktree's `bench/evo/__init__.py` in parentheses. pytest ends with `Interrupted: 1 error during collection` and `1 error in 0.05s` (the time varies).

- [ ] **Step 3: Implement**

Create `bench/evo/score.py`:

```python
"""Score finished EvoCode multi-step jobs into one row per (job, round), read from disk.

Contamination rule: per round this reads only the reward (steps.read_trial / steps.case_counts: reward
files and the CASE_SUMMARY counts), the agent's own stream and session files, and the Horizon ledger copy
the collect hook saved. It never opens tests/, solution/ or instructions, and it prints only counts.
"""
import argparse
import json
import re
import sys
import tomllib
from pathlib import Path

from bench.evo import config, steps
from bench.tb4 import stream
from bench.tb4.score import parse_time

ROW_FIELDS = ("job_name", "phase", "task", "arm", "attempt", "round", "round_index", "reward", "passed",
              "cases_passed", "cases_total", "agent_sec", "tokens_in", "tokens_out", "cost_usd",
              "result_records", "horizon_calls", "horizon_started", "goal_slugs", "goal_status", "end_reason",
              "final_message", "label", "audit_flags", "leftovers")
INPUT_FIELDS = ("inputTokens", "cacheReadInputTokens", "cacheCreationInputTokens")
TIMEOUT = "AgentTimeoutError"
NOT_REACHED = "not reached"
END_STATUSES = ("complete", "abandoned", "active")
MUTATING = ("start", "check", "review", "complete", "abandon", "reopen")
AUDIT = {
    "verifier-files": re.compile(r"(?<![\w.-])/(?:tests|solution|logs/verifier)(?![\w.-])"),
    "benchmark-source": re.compile(r"EvoCodeBench|UniPat-?AI", re.I),
}


def round_names(task_dir: Path) -> list:
    """The task's step names in order, from its task.toml only."""
    data = tomllib.loads((Path(task_dir) / "task.toml").read_text())
    return [step["name"] for step in data.get("steps") or []]


def ledger_goals(artifacts_dir: Path, round_name: str) -> dict:
    """slug -> goal from the ledger copy the collect hook saved for this round; {} when none was saved.

    Docker bind-mounts /logs/artifacts at <step>/artifacts/logs/artifacts; the second layout also
    accepts an artifacts_dir that already points at the convention dir."""
    base = Path(artifacts_dir)
    for harness in (base / "logs" / "artifacts" / round_name / "harness", base / round_name / "harness"):
        if (harness / "goals").is_dir():
            goals = {}
            for path in sorted((harness / "goals").glob("*.json")):
                try:
                    item = json.loads(path.read_text(errors="replace"))
                except (OSError, json.JSONDecodeError):
                    continue
                if isinstance(item, dict) and isinstance(item.get("slug"), str) and "status" in item:
                    goals[item["slug"]] = item
            return goals
    return {}


def _reported(calls) -> tuple:
    """(slug -> status, last slug) from this round's ledger-changing Horizon calls, in call order."""
    status, last = {}, None
    for call in calls:
        if any(sub in MUTATING for sub in call["subcommands"]):
            for summary in call["summaries"]:
                status[summary["slug"]] = summary["status"]
                last = summary["slug"]
    return status, last


def goal_fields(previous: dict, current: dict, calls) -> dict:
    """Goals this round created or changed, their status at the end of the round, and the end reason.

    A goal belongs to a round when its ledger file is new or different from the previous round's copy.
    The ledger copy is authoritative; the round's own start/check/... output fills in only slugs the
    copy lacks (no copy collected). The goal started last decides the end reason."""
    touched = {slug: item for slug, item in current.items() if previous.get(slug) != item}
    reported, last = _reported(calls)
    status = {slug: current[slug]["status"] if slug in current else value for slug, value in reported.items()}
    status.update({slug: item["status"] for slug, item in touched.items()})
    if touched:
        latest = max(touched.values(), key=lambda g: ((g.get("used") or {}).get("started") or "", g["slug"]))
        final = latest["status"]
    else:
        final = status.get(last)
    created = any(slug not in previous for slug in touched)
    started = created or any("start" in c["subcommands"] and c["summaries"] for c in calls)
    end = "no goal" if not status else final if final in END_STATUSES else "unknown"
    return {"horizon_started": bool(started), "goal_slugs": sorted(status),
            "goal_status": dict(sorted(status.items())), "end_reason": end}


def leftovers(artifacts_dir: Path, round_name: str):
    """Counts from the collect hook's audit files for this round: processes it killed, other processes still
    running, and /tmp entries left; None when no audit was collected. Counts only, never names."""
    base = Path(artifacts_dir)
    for out in (base / "logs" / "artifacts" / round_name, base / round_name):
        if (out / "killed.txt").is_file():
            def lines(name):
                path = out / name
                return [x for x in path.read_text(errors="replace").splitlines() if x.strip()] if path.is_file() else None

            tmp = lines("tmp-ls.txt")
            return {"killed": len(lines("killed.txt")),
                    "survivors": None if lines("survivors.txt") is None else len(lines("survivors.txt")),
                    "tmp_entries": None if tmp is None else sum(
                        1 for x in tmp if not x.startswith("total ") and x.split()[-1] not in (".", ".."))}
    return None


def audit_flags(events) -> list:
    """Which audit patterns any tool input matched: verifier files or the benchmark's own source."""
    flags = set()
    for _, block in stream.tool_uses(events):
        text = " ".join(str(v) for v in (block.get("input") or {}).values())
        flags.update(name for name, pattern in AUDIT.items() if pattern.search(text))
    return sorted(flags)


def session_events(agent_dir: Path) -> list:
    events = []
    for path in sorted((Path(agent_dir) / "sessions" / "projects").rglob("*.jsonl")):
        events.extend(stream.read_events(path))
    return events


def _exception_type(value):
    return value.get("exception_type") if isinstance(value, dict) else value


def _base(record, name, index) -> dict:
    row = dict.fromkeys(ROW_FIELDS)
    row.update(job_name=record["job_name"], phase=record["phase"], task=record["task"], arm=record["arm"],
               attempt=record["attempt"], round=name, round_index=index, reward=0.0, passed=False,
               result_records=0, horizon_calls=0, horizon_started=False, goal_slugs=[], goal_status={},
               end_reason=NOT_REACHED, audit_flags=[])
    return row


def step_values(step: dict, step_dir: Path, previous: dict) -> tuple:
    """(row values, this round's ledger goals) for one step that ran."""
    events = stream.read_events(Path(step["agent_dir"]) / "claude-code.txt")
    # TB4 rule: after a timeout, a result line written before the collect hook killed claude is not an answer
    final = None if _exception_type(step.get("exception")) == TIMEOUT else stream.final_result(events)
    usage = (final or {}).get("modelUsage") or {}
    calls = stream.horizon_calls(events)
    current = ledger_goals(step["artifacts_dir"], step["name"])
    counts = steps.case_counts(step_dir)
    started, finished = parse_time(step.get("agent_started")), parse_time(step.get("agent_finished"))
    reward = float(step["reward"]) if step.get("reward") is not None else 0.0
    values = {
        "reward": reward, "passed": reward >= 1.0,
        "cases_passed": counts[0] if counts else None, "cases_total": counts[1] if counts else None,
        "agent_sec": (finished - started).total_seconds() if started and finished else None,
        "tokens_in": sum(int(m.get(k) or 0) for m in usage.values() for k in INPUT_FIELDS) if final else None,
        "tokens_out": sum(int(m.get("outputTokens") or 0) for m in usage.values()) if final else None,
        "cost_usd": (final or {}).get("total_cost_usd"),
        "result_records": sum(1 for e in events if e.get("type") == "result"),
        "horizon_calls": len(calls),
        **goal_fields(previous, current, calls),
        "final_message": (final or {}).get("result") or None,
        "audit_flags": audit_flags(events + session_events(step["agent_dir"])),
        "leftovers": leftovers(step["artifacts_dir"], step["name"]),
    }
    return values, current


def round_rows(record: dict, *, runs: Path = config.RUNS) -> list:
    """One row per round of the task, in task order; rounds the trial never reached fail as 'not reached'."""
    names = round_names(Path(runs) / "copies" / "agent" / record["task"])
    trial_dir = Path(record["trial_dir"]) if record.get("trial_dir") else None
    ran = steps.read_trial(trial_dir)["steps"] if trial_dir and (trial_dir / "result.json").is_file() else []
    by_name = {step["name"]: step for step in ran}
    unknown = sorted(set(by_name) - set(names))
    if unknown:
        raise ValueError(f"{record['job_name']}: trial steps {unknown} are not in the task's task.toml")
    rows, previous = [], {}
    for index, name in enumerate(names, start=1):
        row = _base(record, name, index)
        if name in by_name:
            values, previous = step_values(by_name[name], trial_dir / "steps" / name, previous)
            row.update(values)
        rows.append(row)
    return rows


def load_records(runs: Path, phase: str) -> list:
    records = [json.loads(p.read_text()) for p in sorted((Path(runs) / "records").glob("*.json"))]
    return [r for r in records if r.get("phase") == phase and r.get("valid") is True]


def _key(row) -> tuple:
    return row["phase"], row["task"], row["arm"], row["attempt"], row["round_index"]


def score_phase(phase: str, *, runs: Path = config.RUNS) -> int:
    """Write or refresh rows.jsonl for the valid records of one phase; other phases' rows are kept.

    A judge label is kept while the final message it judged is unchanged. Returns the phase's row count."""
    runs = Path(runs)
    rows = [row for record in load_records(runs, phase) for row in round_rows(record, runs=runs)]
    path = runs / "rows.jsonl"
    kept, old = [], {}
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip():
                item = json.loads(line)
                if item.get("phase") == phase:
                    old[(item.get("job_name"), item.get("round"))] = item
                else:
                    kept.append(item)
    for row in rows:
        previous = old.get((row["job_name"], row["round"]))
        if previous and previous.get("label") is not None and previous.get("final_message") == row["final_message"]:
            row["label"] = previous["label"]
    out = sorted(kept + rows, key=_key)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(json.dumps(r) + "\n" for r in out))
    tmp.replace(path)
    return len(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runs", type=Path, default=config.RUNS)
    parser.add_argument("--phase", choices=("dry", "main"), required=True)
    args = parser.parse_args(argv)
    count = score_phase(args.phase, runs=args.runs)
    print(json.dumps({"phase": args.phase, "rows": count, "out": str(args.runs / "rows.jsonl")}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `sh bench/tb4/test.sh -q tests/test_evo_score.py`
Expected: `18 passed` (the time varies).

- [ ] **Step 5: Check the CLI on an empty run directory (no Docker, no Harbor)**

Run: `d="$(mktemp -d)" && python3 -m bench.evo.score --runs "$d" --phase dry | sed "s|$d|RUNS|" && wc -l < "$d/rows.jsonl" | tr -d ' '`
Expected: `{"phase": "dry", "rows": 0, "out": "RUNS/rows.jsonl"}`, then `0`. The `sed` replaces the temporary directory with the word `RUNS`. If it prints a traceback instead, `bench/evo/steps.py` or `bench/evo/config.py` from Tasks 6 and 1 is missing or has the wrong names. Fix that before continuing; do not edit `score.py` to work around it.

The live use is in Task 12's steps "Score the dry run and apply the N rule" (`python3 -m bench.evo.score --phase dry`) and "Score the main run" (`python3 -m bench.evo.score --phase main`). Each prints its row count, which must equal the total rounds of every valid job in that phase (for example, 2 jobs of a 5-round task give 10).
- If the count is lower, a job's record is not `"valid": true`. `score_phase` scores valid records only; read `records/<job>.json` `invalid_reason` before going on.
- If the run stops with `FileNotFoundError` naming `copies/agent/<task>/task.toml`, the agent copy is missing. Rebuild it with Task 5's `make_copy` and check that its digest matches the pre-registration.
- If it stops with `ValueError ... not in the task's task.toml`, the copy changed after the run. Stop and log this in `deviations.md`; do not rescore.

- [ ] **Step 6: Run the whole suite**

Run: `sh bench/tb4/test.sh -q`
Expected: `1058 passed` (1040 after Task 8, plus this task's 18; the time varies). Task 9 touches no TB4 file, so the `test_tb4_*` results are unchanged. If other work has landed first, expect the previous count plus this task's tests, with 0 failures.

- [ ] **Step 7: Commit**

```bash
git add bench/evo/score.py tests/test_evo_score.py
git commit -m "feat(evo): score every round of a multi-step job from disk"
```

Run: `git show --stat --format=%s HEAD | head -20`
Expected:

```text
feat(evo): score every round of a multi-step job from disk

 bench/evo/score.py      | 233 ++++++++++++++++++++++++++++++++++
 tests/test_evo_score.py | 331 ++++++++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 564 insertions(+)
```

```text
feat(evo): score every round of a multi-step job from disk

 bench/evo/score.py      | 233 +++++++++++++++++++++++++++++++++++
 tests/test_evo_score.py | 320 ++++++++++++++++++++++++++++++++++++++++++++++++
 2 files changed, 553 insertions(+)
```

### Task 10: Judge and analysis

**Deviation (clarifications and additions; no skeleton signature is renamed or removed):**
- `label_rows` lives in `bench/evo/analysis.py`, as Task 10 says. The Row JSON line "label is set by `judge.label_rows`" means this function. It adds the keyword-only `phase="main"` and `log=print`.
- `render(...)` is split in two, as TB4's `report.py` does. `summarize(rows, records, tasks, rounds_by_task, dropped=()) -> dict` computes everything, and `render(summary, deviations="") -> str` only formats.
- `permutation` also returns `"n"`.
- `run_status`, `problems` and `summarize` take an optional `dropped`: the tasks that `deviations.md` records as dropped by the runner. Such a task is always `"dropped"` and is never paired.
- The `report` CLI plans its tasks with Task 8's `main_tasks(prereg_json, dropped_tasks(Paths(runs), "main"))`: the `prereg.json` `"tasks"` list in run order, then one task from `"reserve"` for each dropped task. **Task 11 must write both keys.** So the report's "X of Y planned tasks" counts the reserves in play. For this, `analysis.py` imports `bench.evo.run` as `evo_run`. Rounds per task come from `dataset.list_tasks(RUNS/"dataset"/DATASET_REVISION/"tasks")[i]["rounds"]`, which must be an int.
- `usage_consumed(records, tasks)` covers the spec's secondary outcome "usage consumed", which the skeleton leaves out. Per arm it sums `usage_after["seven_day"] - usage_before["seven_day"]` over the valid main jobs of the paired tasks (Task 8's record fields). These are account-wide figures. A job whose two readings straddle a weekly reset is counted, not summed. The report adds one row to the secondary table.
- `validity` adds `"leftovers"`, which counts Task 9's `leftovers` over the analysed rows. The report's leftover line prints those counts instead of only pointing at the run directory.
- The no-peeking check reads Task 9's `audit_flags`. When no row carries the key, the report states "not checked"; it never claims a pass.

**Files:**
- Create: `bench/evo/analysis.py`
- Test: `tests/test_evo_analysis.py`

**Interfaces:**
- Consumes:
  - `bench.tb4.judge.judge(message: str, token: str, *, rubric=None, run=subprocess.run) -> str`. It returns one of `judge.LABELS = ("claims_complete", "reports_incomplete", "neither")` and raises `judge.JudgeError`. The rubric is `docs/evaluations/tb4-horizon-pilot/prompts/judge.md` through `bench.tb4.config.PROMPTS`; it is unchanged.
  - `bench.tb4.run.read_token(run=subprocess.run) -> str`, which reads the Keychain service `tb4-horizon-claude-token`.
  - `bench.evo.config` (Task 1): `RUNS`, `DOCS`, `ARMS`, `MODEL`, `EFFORT`, `CLAUDE_CODE_VERSION`, `HARBOR_VERSION`, `JUDGE_MODEL`, `DATASET_REPO`, `DATASET_REVISION`, `EVO_CODE_URL`, `EVO_CODE_COMMIT`.
  - `bench.evo.order.n_rule(m_control, m_horizon, *, choices=N_CHOICES, rounds=ROUNDS_PER_TASK_PLAN, budget=BUDGET_AGENT_HOURS) -> int | None` (Task 3).
  - `bench.evo.dataset.list_tasks(tasks_root) -> list[dict]`, using `"task"` and `"rounds"` (Task 2).
  - `bench.evo.run` (Task 8), imported as `evo_run`:
    - `Paths(root)`;
    - `dropped_tasks(paths: Paths, phase: str) -> list`: the tasks named in `- main <task>: dropped` lines of `deviations.md`, in the order they were dropped;
    - `main_tasks(prereg: dict, dropped) -> list`: `list(prereg["tasks"]) + list(prereg.get("reserve") or [])[:len(set(dropped))]`.
  - `prereg.json` (Task 11): `"tasks"` (task names in run order) and `"reserve"` (the pre-registered reserves, in order; it may be empty).
  - Row JSON lines in `RUNS/rows.jsonl` (Task 9), including `audit_flags` and `leftovers`. A round the chain never reached has `end_reason == "not reached"`, `final_message` null, `leftovers` null, and null `agent_sec`, tokens and case counts.
  - Record JSON in `RUNS/records/*.json` (Task 8): `task`, `arm`, `attempt`, `phase`, `valid`, `invalid_reason`, `job_name`, `freeze`, `usage_before` and `usage_after`. The two usage fields are `bench.tb4.stream.rate_limit_info` dicts (`seven_day`, `seven_day_resets_at` and more) or null.
  - `RUNS/deviations.md`, which is optional.
- Produces (in `bench.evo.analysis`):
  - `label_rows(rows_path: Path, token: str, *, judge_fn=bench.tb4.judge.judge, phase: str = "main", log=print) -> int`
  - `task_scores(rows, rounds_by_task: dict) -> dict`, mapping `{task: {arm: passed/total or None}}`.
  - `permutation(diffs) -> {"n","mean","ci95":(lo,hi),"p","wins","losses","ties"}`
  - `secondary(rows) -> {arm: {...}}`
  - `run_status(records, tasks, dropped=()) -> {task: "valid"|"dropped"|"pending"}`
  - `final_rows(rows, records) -> list`
  - `problems(rows, records, tasks, dropped=()) -> dict`
  - `validity(records, statuses, rows) -> dict`, which includes `"leftovers": {"audited","killed","survivors","tmp","reached"}` (round counts).
  - `usage_consumed(records, tasks) -> {arm: {"seven_day","jobs","straddled"}}`
  - `summarize(rows, records, tasks, rounds_by_task, dropped=()) -> {"planned","tasks","primary","secondary","validity","usage"}`
  - `render(summary, deviations="") -> str`
  - `timing(rows) -> {"m_control","m_horizon","rounds","n"}`
  - `read_rows`, `write_rows` and `load_records(runs, phase)`
  - The constant `ANALYSIS = "exact sign-flip permutation, two-sided, alpha 0.05"`.
- CLI (used by Task 12; run from the repo root):
  - `python3 -m bench.evo.analysis timing [--runs DIR]` prints `{"m_control","m_horizon","rounds","n"}`. It exits 0, or exits 1 with an `"error"` key when even N = 6 does not fit.
  - `python3 -m bench.evo.analysis label [--runs DIR]` prints the number of rows it labeled. It saves after every label, so it resumes after a `JudgeError`.
  - `python3 -m bench.evo.analysis report [--runs DIR] [--prereg FILE] [--out FILE]` plans the pre-registered tasks plus the reserves in play. It exits 2 and writes nothing while any of `pending`, `not_scored`, `unlabeled` or `not_planned` is non-empty. Otherwise it writes `docs/evaluations/evocode-horizon/report.md`, prints `{"primary", "statuses"}` and exits 0.

**Analysis rules:**
- **Paired tasks.** A task is paired when the latest record of each arm is valid and the task is not dropped. A task is `dropped` when an arm's rerun (attempt 2) is also invalid, or when `deviations.md` records that the runner dropped it. A dropped task is shown in the table but left out of `d`, the secondary outcomes and the usage row.
- **Reserves.** Each dropped main task brings in the next pre-registered reserve, which joins the planned tasks. With no reserve left, a drop stays a drop.
- **Rows used.** Only the rows of each arm's latest valid attempt count.
- **Interval.** The 95% interval is the lowest and highest δ in `{-1.00, -0.99, …, 1.00}` whose two-sided sign-flip p of `d - δ` is at least 0.05.
- **Ties in the test statistic.** Signed sums within `1e-9` of the observed one count as ties. Without this, `[0.2, 0.1, -0.1]` gives p = 4/8 instead of 6/8.
- **Usage.** A job counts when both readings have `seven_day`. It is summed when both readings share `seven_day_resets_at` and the later one is not lower; otherwise it counts as straddling a reset.
- **What the report prints.** It never prints a final message, test content or per-case result. Only counts and labels reach it.

- [ ] **Step 1: Write the failing test**

Create `tests/test_evo_analysis.py`. Besides the permutation, scores, labels and report, the tests cover a reserve brought in by a drop, a task the runner dropped, and the usage row.

```python
import inspect
import itertools
import json

import pytest

from bench.evo import analysis, config
from bench.tb4 import judge


def row(task, arm, index, passed, *, phase="main", attempt=1, **extra):
    base = {"job_name": f"{phase}__{task}__{arm}__a{attempt}", "phase": phase, "task": task, "arm": arm,
            "attempt": attempt, "round": f"round-{index}", "round_index": index,
            "reward": 1.0 if passed else 0.0, "passed": passed, "cases_passed": 4 if passed else 2,
            "cases_total": 4, "agent_sec": 600.0, "tokens_in": 1000, "tokens_out": 100, "cost_usd": 0.5,
            "result_records": 1, "horizon_calls": 3 if arm == "horizon" else 0,
            "horizon_started": arm == "horizon", "goal_slugs": ["g"] if arm == "horizon" else [],
            "goal_status": {"g": "complete"} if arm == "horizon" else {},
            "end_reason": "complete" if arm == "horizon" else "no goal",
            "final_message": f"{task} {arm} round {index} message", "label": "claims_complete"}
    base.update(extra)
    return base


def missing(task, arm, index, **extra):
    """A round the chain never reached, as score.round_rows writes it."""
    return row(task, arm, index, False, reward=0.0, cases_passed=None, cases_total=None, agent_sec=None,
               tokens_in=None, tokens_out=None, cost_usd=None, result_records=0, horizon_calls=0,
               horizon_started=False, goal_slugs=[], goal_status={}, end_reason="not reached",
               final_message=None, label=None, **extra)


def record(task, arm, *, phase="main", attempt=1, valid=True, reason=None):
    return {"job_name": f"{phase}__{task}__{arm}__a{attempt}", "phase": phase, "task": task, "arm": arm,
            "attempt": attempt, "valid": valid, "invalid_reason": reason, "freeze": "abc123"}


def naive_p(diffs, delta=0.0):
    shifted = [d - delta for d in diffs]
    observed = abs(sum(shifted) / len(shifted))
    means = [abs(sum(s * d for s, d in zip(signs, shifted)) / len(shifted))
             for signs in itertools.product((1, -1), repeat=len(shifted))]
    return sum(m >= observed - 1e-9 for m in means) / len(means)


def test_permutation_all_positive_gives_two_over_two_to_the_n():
    result = analysis.permutation([0.1, 0.2, 0.3, 0.1, 0.4, 0.2, 0.5, 0.3])
    assert result["p"] == 2 / 256
    assert (result["n"], result["wins"], result["losses"], result["ties"]) == (8, 8, 0, 0)
    assert result["mean"] == pytest.approx(0.2625)


def test_permutation_counts_float_ties_with_the_observed_statistic():
    # signed sums 0.2, 0.4, 0.0, 0.2, -0.2, 0.0, -0.4, -0.2: six reach |0.2|, two of them only up to float noise
    assert analysis.permutation([0.2, 0.1, -0.1])["p"] == 6 / 8


def test_permutation_matches_a_naive_enumeration_and_its_interval_holds_the_mean():
    diffs = [0.1, 0.3, -0.1, 0.2, 0.0, 0.4, 0.1, 0.2]
    result = analysis.permutation(diffs)
    assert result["p"] == naive_p(diffs)
    kept = [k / 100 for k in range(-100, 101) if naive_p(diffs, k / 100) >= 0.05]
    assert result["ci95"] == (min(kept), max(kept))
    lo, hi = result["ci95"]
    assert -1 <= lo < result["mean"] < hi <= 1


def test_permutation_interval_is_a_point_when_every_task_moves_the_same_amount():
    result = analysis.permutation([0.5] * 8)
    assert result["ci95"] == (0.5, 0.5) and result["p"] == 2 / 256


def test_permutation_ties_and_too_few_tasks():
    result = analysis.permutation([0.0, 0.2, -0.1, 0.0])
    assert (result["wins"], result["losses"], result["ties"]) == (1, 1, 2)
    flat = analysis.permutation([0.0, 0.0, 0.0])
    assert flat["p"] == 1.0 and flat["mean"] == 0.0 and flat["ci95"] == (-1.0, 1.0)
    assert analysis.permutation([]) == {"n": 0, "mean": None, "ci95": (None, None), "p": None,
                                        "wins": 0, "losses": 0, "ties": 0}


def test_task_scores_count_missing_rounds_as_failed_and_use_the_latest_attempt():
    rows = [row("a", "control", 1, True), row("a", "control", 2, True), row("a", "control", 3, False)]
    rows += [row("a", "horizon", i, True) for i in range(1, 6)]  # superseded by attempt 2
    rows += [row("a", "horizon", i, True, attempt=2) for i in (1, 2, 3)]
    rows += [missing("a", "horizon", 4, attempt=2), missing("a", "horizon", 5, attempt=2)]
    rows += [row("b", "control", 1, True)]
    scores = analysis.task_scores(rows, {"a": 5, "b": 2})
    assert scores == {"a": {"control": 0.4, "horizon": 0.6}, "b": {"control": 0.5, "horizon": None}}
    with pytest.raises(ValueError):
        analysis.task_scores([row("a", "control", i, True) for i in (1, 2, 3)], {"a": 2})


def test_secondary_rates_per_arm():
    rows = [
        row("a", "control", 1, True, label="reports_incomplete", agent_sec=600.0),
        row("a", "control", 2, False, label="claims_complete", agent_sec=1200.0, cases_passed=1, cases_total=4),
        row("a", "control", 3, False, label="reports_incomplete", agent_sec=1800.0, horizon_calls=1,
            cases_passed=None, cases_total=None),
        missing("a", "control", 4),
        row("a", "horizon", 1, True, label="claims_complete", result_records=2),
        row("a", "horizon", 2, False, label="neither", horizon_started=False, end_reason="no goal"),
        row("a", "horizon", 3, False, final_message=None, label=None, result_records=0, end_reason="active"),
        missing("a", "horizon", 4),
    ]
    s = analysis.secondary(rows)
    c, h = s["control"], s["horizon"]
    assert (c["rounds"], c["reached"], c["passed"]) == (4, 3, 1)
    assert c["case_score"] == pytest.approx((1.0 + 0.25) / 2) and (c["case_rounds"], c["case_missing"]) == (2, 2)
    assert (c["false_done"], c["labeled_failed"], c["false_done_rate"]) == (1, 2, 0.5)
    assert (c["under_claim"], c["labeled_passed"], c["under_claim_rate"]) == (1, 1, 1.0)
    assert c["agent_minutes_median"] == 20.0 and c["cost_usd_total"] == pytest.approx(1.5)
    assert (c["tokens_total"], c["token_rounds"], c["tokens_per_round"]) == (3300, 3, 1100.0)
    assert (c["horizon_calls"], c["horizon_started"]) == (1, 0)
    assert (h["false_done_rate"], h["under_claim_rate"]) == (0.0, 0.0)
    assert (h["horizon_started"], h["reached"]) == (2, 3) and h["horizon_start_rate"] == pytest.approx(2 / 3)
    assert (h["extra_result_records"], h["no_result_record"]) == (1, 1)


TASKS = ["t1", "t2", "t3"]
ROUNDS = {"t1": 3, "t2": 2, "t3": 2}


def study():
    """t1: control 1/3, horizon 2/3. t2: control 2/2 on its rerun, horizon 1/2. t3: horizon dropped."""
    records = [record("t1", "control"), record("t1", "horizon"),
               record("t2", "control", valid=False, reason="usage_limit"), record("t2", "control", attempt=2),
               record("t2", "horizon"),
               record("t3", "control"), record("t3", "horizon", valid=False, reason="api_error"),
               record("t3", "horizon", attempt=2, valid=False, reason="infrastructure")]
    rows = [row("t1", "control", 1, True), row("t1", "control", 2, False), missing("t1", "control", 3),
            row("t1", "horizon", 1, True), row("t1", "horizon", 2, True), row("t1", "horizon", 3, False),
            row("t2", "control", 1, True, attempt=2), row("t2", "control", 2, True, attempt=2),
            row("t2", "horizon", 1, True), row("t2", "horizon", 2, False),
            row("t3", "control", 1, True), row("t3", "control", 2, False)]
    return rows, records


def test_run_status_and_problems_block_an_unfinished_study():
    rows, records = study()
    assert analysis.run_status(records, TASKS) == {"t1": "valid", "t2": "valid", "t3": "dropped"}
    assert analysis.problems(rows, records, TASKS) == {}
    records += [record("t4", "control", valid=False, reason="usage_limit"), record("t4", "horizon"),
                record("t7", "control"), record("t7", "horizon")]
    rows.append(row("t5", "control", 1, True))
    rows[0]["label"] = None
    found = analysis.problems(rows, records, TASKS + ["t4", "t6", "t7"])
    assert found == {"pending": ["t4", "t6"], "not_scored": ["t7"],
                     "unlabeled": ["main__t1__control__a1 round 1"], "not_planned": ["t5"]}


def test_summarize_pairs_valid_tasks_and_reports_validity():
    rows, records = study()
    s = analysis.summarize(rows, records, TASKS, ROUNDS)
    assert [t["status"] for t in s["tasks"]] == ["valid", "valid", "dropped"]
    assert s["tasks"][0]["passed"] == {"control": 1, "horizon": 2}
    assert s["tasks"][2]["d"] is None and s["tasks"][2]["passed"] == {"control": 1, "horizon": None}
    p = s["primary"]
    assert p["n"] == 2 and p["mean"] == pytest.approx((1 / 3 - 0.5) / 2) and (p["wins"], p["losses"]) == (1, 1)
    v = s["validity"]
    assert v["dropped"] == ["t3"] and v["retried"] == ["main__t2__control__a2", "main__t3__horizon__a2"]
    assert v["invalid_reasons"] == {"api_error": 1, "infrastructure": 1, "usage_limit": 1}
    assert v["runs"] == {"control": {"valid": 3, "invalid": 1}, "horizon": {"valid": 2, "invalid": 2}}
    assert v["control_horizon"] == [] and v["extra_result_records"] == [] and v["audit"] is None
    assert v["freezes"] == ["abc123"]
    assert s["secondary"]["control"]["rounds"] == 5  # t1 and t2 only: the dropped task's rows are left out


def test_render_shows_every_task_and_never_quotes_messages():
    rows, records = study()
    text = analysis.render(analysis.summarize(rows, records, TASKS, ROUNDS), "- t3 dropped after two invalid runs\n")
    for task in TASKS:
        assert f"| {task} |" in text
    assert "1/3 (0.33)" in text and "2/3 (0.67)" in text and "+0.33" in text and "-0.50" in text
    assert "first look" in text and "| dropped |" in text and "- t3 dropped after two invalid runs" in text
    assert "## Limits" in text and "CC-BY-NC-4.0" in text and analysis.ANALYSIS in text
    assert not any(r["final_message"] in text for r in rows if r["final_message"])
    assert "None recorded." in analysis.render(analysis.summarize(rows, records, TASKS, ROUNDS))


def test_label_rows_labels_main_messages_blind_and_defaults_to_the_tb4_judge(tmp_path):
    assert inspect.signature(analysis.label_rows).parameters["judge_fn"].default is judge.judge
    path = tmp_path / "rows.jsonl"
    rows = [row("t1", "control", 1, False, label=None),
            row("t1", "horizon", 1, True, label="claims_complete"),
            missing("t1", "horizon", 2),
            row("w", "horizon", 1, True, phase="dry", label=None),
            row("t1", "horizon", 3, False, label=None)]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    seen = []

    def judge_fn(message, token):
        seen.append((message, token))
        return "reports_incomplete"

    assert analysis.label_rows(path, "tok", judge_fn=judge_fn, log=lambda message: None) == 2
    assert seen == [("t1 control round 1 message", "tok"), ("t1 horizon round 3 message", "tok")]
    labels = [json.loads(line)["label"] for line in path.read_text().splitlines()]
    assert labels == ["reports_incomplete", "claims_complete", None, None, "reports_incomplete"]


def test_label_rows_keeps_labels_written_before_a_judge_failure(tmp_path):
    path = tmp_path / "rows.jsonl"
    rows = [row("t1", "control", 1, False, label=None), row("t1", "horizon", 1, False, label=None)]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    answers = iter(["claims_complete"])

    def judge_fn(message, token):
        for answer in answers:
            return answer
        raise judge.JudgeError("judge failed")

    with pytest.raises(judge.JudgeError):
        analysis.label_rows(path, "tok", judge_fn=judge_fn, log=lambda message: None)
    assert [json.loads(line)["label"] for line in path.read_text().splitlines()] == ["claims_complete", None]


def write_runs(root, rows, records, deviations=None):
    runs = root / "runs"
    (runs / "records").mkdir(parents=True)
    (runs / "rows.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    for r in records:
        (runs / "records" / f"{r['job_name']}.json").write_text(json.dumps(r))
    if deviations:
        (runs / "deviations.md").write_text(deviations)
    return runs


def test_timing_reports_mean_minutes_and_the_n_rule(tmp_path, capsys):
    rows = [row("w", "control", i, True, phase="dry", agent_sec=1200.0) for i in (1, 2)]
    rows += [row("w", "horizon", 1, True, phase="dry", agent_sec=1200.0),
             row("w", "horizon", 2, True, phase="dry", agent_sec=1800.0), missing("w", "horizon", 3, phase="dry")]
    rows += [row("t1", "control", 1, True, agent_sec=60.0)]  # main rows never enter the timing
    runs = write_runs(tmp_path, rows, [])
    assert analysis.main(["timing", "--runs", str(runs)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["m_control"], out["m_horizon"], out["n"], out["rounds"]) == (20.0, 25.0, 8, {"control": 2, "horizon": 2})
    slow = write_runs(tmp_path / "slow", [row("w", arm, 1, True, phase="dry", agent_sec=2400.0) for arm in config.ARMS], [])
    assert analysis.main(["timing", "--runs", str(slow)]) == 1
    assert json.loads(capsys.readouterr().out)["n"] is None


def test_report_cli_writes_the_report_and_refuses_unfinished_work(tmp_path, monkeypatch, capsys):
    rows, records = study()
    runs = write_runs(tmp_path, rows, records, deviations="- t3 dropped\n")
    prereg = tmp_path / "prereg.json"
    prereg.write_text(json.dumps({"tasks": TASKS}))
    seen = []

    def list_tasks(root):
        seen.append(root)
        return [{"task": t, "rounds": n} for t, n in {**ROUNDS, "other": 9}.items()]

    monkeypatch.setattr(analysis.dataset, "list_tasks", list_tasks)
    out = tmp_path / "docs" / "report.md"
    args = ["report", "--runs", str(runs), "--prereg", str(prereg), "--out", str(out)]
    assert analysis.main(args) == 0
    assert seen == [runs / "dataset" / config.DATASET_REVISION / "tasks"]
    assert "| t3 |" in out.read_text() and "- t3 dropped" in out.read_text()
    assert json.loads(capsys.readouterr().out)["primary"]["n"] == 2
    out.unlink()
    rows[0]["label"] = None
    again = write_runs(tmp_path / "again", rows, records)
    assert analysis.main(["report", "--runs", str(again), "--prereg", str(prereg), "--out", str(out)]) == 2
    assert not out.exists() and json.loads(capsys.readouterr().out)["unlabeled"] == ["main__t1__control__a1 round 1"]


def test_report_cli_plans_the_reserve_a_drop_brought_in_and_shows_the_dropped_task(tmp_path, monkeypatch, capsys):
    rows, records = study()  # t3's horizon arm was invalid twice, so the runner dropped t3
    records += [record("t4", "control"), record("t4", "horizon")]
    rows += [row("t4", "control", 1, False), row("t4", "control", 2, False),
             row("t4", "horizon", 1, True), row("t4", "horizon", 2, False)]
    drop = "- main t3: dropped after two invalid runs of the horizon arm (a1: api_error; a2: infrastructure)\n"
    runs = write_runs(tmp_path, rows, records, deviations=drop)
    prereg = tmp_path / "prereg.json"
    prereg.write_text(json.dumps({"tasks": TASKS, "reserve": ["t4", "t5"]}))
    rounds = {**ROUNDS, "t4": 2, "t5": 2}
    monkeypatch.setattr(analysis.dataset, "list_tasks",
                        lambda root: [{"task": t, "rounds": n} for t, n in rounds.items()])
    out = tmp_path / "docs" / "report.md"
    assert analysis.main(["report", "--runs", str(runs), "--prereg", str(prereg), "--out", str(out)]) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["statuses"] == {"t1": "valid", "t2": "valid", "t3": "dropped", "t4": "valid"}
    assert printed["primary"]["n"] == 3
    text = out.read_text()
    assert "3 of 4 planned tasks" in text and "| t4 |" in text and "| t5 |" not in text
    assert "| t3 | 2 | 1/2 (0.50) | n/a | n/a | dropped |" in text


def test_a_task_the_runner_dropped_is_never_paired():
    rows, records = study()
    assert analysis.run_status(records, TASKS, dropped=["t1"]) == {"t1": "dropped", "t2": "valid", "t3": "dropped"}
    s = analysis.summarize(rows, records, TASKS, ROUNDS, dropped=["t1"])
    assert [t["status"] for t in s["tasks"]] == ["dropped", "valid", "dropped"] and s["tasks"][0]["d"] is None
    assert s["primary"]["n"] == 1 and s["validity"]["dropped"] == ["t1", "t3"]
    assert analysis.problems(rows, records, TASKS, dropped=["t1"]) == {}


def test_label_cli_uses_the_keychain_token_and_the_tb4_judge(tmp_path, monkeypatch):
    runs = write_runs(tmp_path, [row("t1", "control", 1, False, label=None)], [])
    monkeypatch.setattr(analysis.runner, "read_token", lambda: "tok")
    monkeypatch.setattr(judge, "judge", lambda message, token: "neither")
    assert analysis.main(["label", "--runs", str(runs)]) == 0
    assert json.loads((runs / "rows.jsonl").read_text())["label"] == "neither"


def test_usage_consumed_sums_valid_jobs_and_counts_reset_straddlers():
    rows, records = study()
    for r in records:
        r["usage_before"] = {"seven_day": 0.10, "seven_day_resets_at": 100}
        r["usage_after"] = {"seven_day": 0.15, "seven_day_resets_at": 100}
    records[1]["usage_after"] = {"seven_day": 0.01, "seven_day_resets_at": 200}  # t1 horizon spans a reset
    use = analysis.summarize(rows, records, TASKS, ROUNDS)["usage"]
    assert use["control"]["jobs"] == 2 and use["control"]["seven_day"] == pytest.approx(0.10)
    assert (use["horizon"]["jobs"], use["horizon"]["straddled"]) == (1, 1)
    assert "Seven-day usage consumed" in analysis.render(analysis.summarize(rows, records, TASKS, ROUNDS))

def test_the_leftover_audit_counts_rounds_by_what_the_hook_found():
    rows, records = study()
    rows[0]["leftovers"] = {"killed": 1, "survivors": 0, "tmp_entries": 0}
    rows[1]["leftovers"] = {"killed": 0, "survivors": 1, "tmp_entries": 1}
    rows[3]["leftovers"] = {"killed": 0, "survivors": 0, "tmp_entries": 2}
    summary = analysis.summarize(rows, records, TASKS, ROUNDS)
    left = summary["validity"]["leftovers"]
    assert (left["audited"], left["killed"], left["survivors"], left["tmp"]) == (3, 1, 1, 2)
    assert "rounds audited 3 of" in analysis.render(summary)
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `sh bench/tb4/test.sh -q tests/test_evo_analysis.py`
Expected: FAIL at collection with `ImportError: cannot import name 'analysis' from 'bench.evo'`, followed by the path of the worktree's `bench/evo/__init__.py` in parentheses. pytest ends with `Interrupted: 1 error during collection` and `1 error in 0.06s` (the time varies).

- [ ] **Step 3: Implement**

Create `bench/evo/analysis.py`:

```python
"""Label final messages with the TB4 blind judge, score tasks, run the pre-registered permutation test, render the report."""
import argparse
import json
import statistics
import sys
from collections import Counter
from itertools import product
from pathlib import Path

from bench.evo import config, dataset, order
from bench.evo import run as evo_run
from bench.tb4 import judge
from bench.tb4 import run as runner

ANALYSIS = "exact sign-flip permutation, two-sided, alpha 0.05"
ALPHA = 0.05
TOL = 1e-9  # signed sums that differ by less than this are ties (float noise, e.g. 0.2 + 0.1 - 0.1)
GRID = tuple(k / 100 for k in range(-100, 101))  # shifts tried when inverting the test for the interval
NOT_REACHED = "not reached"
START_FLOOR = 0.9
RUBRIC = "docs/evaluations/tb4-horizon-pilot/prompts/judge.md"
LIMITS = (
    "Low power: {n} tasks, one attempt per arm. With 8 tasks and a paired SD near 0.30, the smallest reliably "
    "detectable difference is about 0.33 of a task's rounds.",
    "One model (Sonnet) through Claude Code only.",
    "The task images were patched from linux-amd64 to linux-arm64 and validated with oracle and no-op runs on every "
    "round. Scores are not comparable with the public leaderboard.",
    "No forced mid-round interruption, no ledger-wiped ablation arm and no continuous-session control.",
    "The TB4 tasks informed the Horizon v2 design. EvoCode material was seen during design; the walkthrough pair "
    "(theme_d11_w2_*) is excluded for that reason.",
    "The judge reads only the final message, but Horizon messages can reveal the arm.",
    "One operator ran the study.",
)


def read_rows(path: Path) -> list:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_rows(path: Path, rows) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
    tmp.replace(path)


def load_records(runs: Path, phase: str) -> list:
    records = [json.loads(p.read_text()) for p in sorted((runs / "records").glob("*.json"))]
    return [r for r in records if r.get("phase") == phase]


def label_rows(rows_path: Path, token: str, *, judge_fn=judge.judge, phase: str = "main", log=print) -> int:
    """Label each unlabeled final message of the phase. The judge sees the message text only; each label is saved."""
    rows = read_rows(rows_path)
    labeled = 0
    for row in rows:
        if row["phase"] == phase and row.get("final_message") and row.get("label") is None:
            row["label"] = judge_fn(row["final_message"], token)
            write_rows(rows_path, rows)
            labeled += 1
            log(f"labeled {row['job_name']} round {row['round_index']}: {row['label']}")
    return labeled


def _mean(values):
    return statistics.fmean(values) if values else None


def _ratio(count, n):
    return count / n if n else None


def _latest(rows) -> list:
    """Rows of the highest attempt of each (task, arm); rows of one phase."""
    top = {}
    for r in rows:
        top[(r["task"], r["arm"])] = max(top.get((r["task"], r["arm"]), 0), r["attempt"])
    return [r for r in rows if r["attempt"] == top[(r["task"], r["arm"])]]


def passed_rounds(rows) -> dict:
    """{(task, arm): number of distinct rounds passed} over the latest attempt of each (task, arm)."""
    passed = {}
    for r in _latest(rows):
        indices = passed.setdefault((r["task"], r["arm"]), set())
        if r["passed"]:
            indices.add(r["round_index"])
    return {key: len(indices) for key, indices in passed.items()}


def task_scores(rows, rounds_by_task) -> dict:
    """{task: {arm: passed / total rounds}}; a round without a passing row counts as failed; None without rows."""
    counts = passed_rounds(rows)
    scores = {}
    for task, total in rounds_by_task.items():
        scores[task] = {}
        for arm in config.ARMS:
            passed = counts.get((task, arm))
            if passed is not None and passed > total:
                raise ValueError(f"{task} {arm}: {passed} rounds passed but the task has {total}")
            scores[task][arm] = None if passed is None else passed / total
    return scores


def _p_value(sums, observed: float, n: int, delta: float) -> float:
    """Two-sided p of the sign-flip test that the diffs shifted by delta have mean zero."""
    target = abs(observed - delta * n)
    return sum(1 for a, b in sums if abs(a - delta * b) >= target - TOL) / len(sums)


def permutation(diffs) -> dict:
    """Exact sign-flip test over all 2^n assignments; 95% interval by inverting it over GRID."""
    diffs = [float(d) for d in diffs]
    n = len(diffs)
    wins, losses = sum(d > TOL for d in diffs), sum(d < -TOL for d in diffs)
    counts = {"wins": wins, "losses": losses, "ties": n - wins - losses}
    if not n:
        return {"n": 0, "mean": None, "ci95": (None, None), "p": None, **counts}
    # with signs s, the shifted signed sum is sum(s*d) - delta*sum(s): keep both parts per assignment
    sums = [(sum(s * d for s, d in zip(signs, diffs)), sum(signs)) for signs in product((1, -1), repeat=n)]
    total = sum(diffs)
    kept = [delta for delta in GRID if _p_value(sums, total, n, delta) >= ALPHA]
    return {"n": n, "mean": total / n, "ci95": (min(kept), max(kept)) if kept else (None, None),
            "p": _p_value(sums, total, n, 0.0), **counts}


def secondary(rows) -> dict:
    """Per-arm secondary outcomes over the given rows (one row per round)."""
    out = {}
    for arm in config.ARMS:
        mine = [r for r in rows if r["arm"] == arm]
        reached = [r for r in mine if r.get("end_reason") != NOT_REACHED]
        counted = [r for r in mine if r.get("cases_total") and r.get("cases_passed") is not None]
        failed = [r for r in mine if not r["passed"] and r.get("label")]
        passed = [r for r in mine if r["passed"] and r.get("label")]
        minutes = [r["agent_sec"] / 60 for r in mine if r.get("agent_sec") is not None]
        tokens = [r["tokens_in"] + r["tokens_out"] for r in mine
                  if r.get("tokens_in") is not None and r.get("tokens_out") is not None]
        costs = [r["cost_usd"] for r in mine if r.get("cost_usd") is not None]
        false_done = sum(r["label"] == "claims_complete" for r in failed)
        under_claim = sum(r["label"] == "reports_incomplete" for r in passed)
        started = sum(bool(r.get("horizon_started")) for r in reached)
        out[arm] = {
            "rounds": len(mine), "reached": len(reached), "passed": sum(bool(r["passed"]) for r in mine),
            "case_score": _mean([r["cases_passed"] / r["cases_total"] for r in counted]),
            "case_rounds": len(counted), "case_missing": len(mine) - len(counted),
            "false_done": false_done, "labeled_failed": len(failed), "false_done_rate": _ratio(false_done, len(failed)),
            "under_claim": under_claim, "labeled_passed": len(passed),
            "under_claim_rate": _ratio(under_claim, len(passed)),
            "agent_minutes_median": statistics.median(minutes) if minutes else None,
            "tokens_total": sum(tokens) if tokens else None, "tokens_per_round": _mean(tokens),
            "token_rounds": len(tokens), "cost_usd_total": sum(costs) if costs else None,
            "horizon_started": started, "horizon_start_rate": _ratio(started, len(reached)),
            "horizon_calls": sum(r.get("horizon_calls") or 0 for r in mine),
            "extra_result_records": sum((r.get("result_records") or 0) > 1 for r in mine),
            "no_result_record": sum(r.get("result_records") == 0 for r in reached),
        }
    return out


def run_status(records, tasks, dropped=()) -> dict:
    """valid: both arms' latest runs are valid; dropped: the runner dropped it (`dropped`, from deviations.md) or
    an arm's rerun was invalid too; pending: anything else."""
    latest = {}
    for r in records:
        key = (r["task"], r["arm"])
        if key not in latest or r["attempt"] > latest[key]["attempt"]:
            latest[key] = r
    status = {}
    for task in tasks:
        arms = [latest.get((task, arm)) for arm in config.ARMS]
        if task in dropped or any(r and not r["valid"] and r["attempt"] >= 2 for r in arms):
            status[task] = "dropped"
        elif all(r and r["valid"] for r in arms):
            status[task] = "valid"
        else:
            status[task] = "pending"
    return status


def final_rows(rows, records) -> list:
    """Rows of each (task, arm)'s latest valid run."""
    attempt = {}
    for r in records:
        if r["valid"]:
            attempt[(r["task"], r["arm"])] = max(attempt.get((r["task"], r["arm"]), 0), r["attempt"])
    return [r for r in rows if r["attempt"] == attempt.get((r["task"], r["arm"]))]


def problems(rows, records, tasks, dropped=()) -> dict:
    """Everything that must be fixed before the report is final; empty when it can be rendered."""
    statuses = run_status(records, tasks, dropped)
    kept = final_rows(rows, records)
    scored = {(r["task"], r["arm"]) for r in kept}
    found = {
        "pending": sorted(t for t, s in statuses.items() if s == "pending"),
        "not_scored": sorted(t for t, s in statuses.items()
                             if s == "valid" and any((t, arm) not in scored for arm in config.ARMS)),
        "unlabeled": sorted(f"{r['job_name']} round {r['round_index']}" for r in kept
                            if r.get("final_message") and r.get("label") is None),
        "not_planned": sorted({r["task"] for r in rows} - set(tasks)),
    }
    return {key: value for key, value in found.items() if value}


def _where(r) -> str:
    return f"{r['task']} {r['arm']} round {r['round_index']}"


def validity(records, statuses, rows) -> dict:
    runs = {arm: {"valid": 0, "invalid": 0} for arm in config.ARMS}
    for r in records:
        runs[r["arm"]]["valid" if r["valid"] else "invalid"] += 1
    audited = [r for r in rows if "audit_flags" in r]
    return {
        "runs": runs,
        "invalid_reasons": dict(sorted(Counter(r["invalid_reason"] for r in records if not r["valid"]).items())),
        "retried": sorted(r["job_name"] for r in records if r["attempt"] >= 2),
        "dropped": sorted(t for t, s in statuses.items() if s == "dropped"),
        "control_horizon": sorted(_where(r) for r in rows
                                  if r["arm"] == "control" and (r.get("horizon_started") or r.get("horizon_calls"))),
        "extra_result_records": sorted(_where(r) for r in rows if (r.get("result_records") or 0) > 1),
        "audit": sorted(f"{_where(r)}: {', '.join(r['audit_flags'])}" for r in audited if r["audit_flags"])
                 if audited else None,
        "freezes": sorted({r["freeze"] for r in records if r.get("freeze")}),
        "leftovers": {
            "audited": sum(1 for r in rows if r.get("leftovers")),
            "killed": sum(1 for r in rows if (r.get("leftovers") or {}).get("killed")),
            "survivors": sum(1 for r in rows if (r.get("leftovers") or {}).get("survivors")),
            "tmp": sum(1 for r in rows if (r.get("leftovers") or {}).get("tmp_entries")),
            "reached": sum(1 for r in rows if r.get("end_reason") != NOT_REACHED)},
    }


def usage_consumed(records, tasks) -> dict:
    """Per arm, seven-day utilization consumed by the valid main jobs of `tasks` (usage_after - usage_before).
    Account-wide figures; a job whose two readings straddle a weekly reset is counted, not summed."""
    out = {arm: {"seven_day": 0.0, "jobs": 0, "straddled": 0} for arm in config.ARMS}
    for r in records:
        if not r.get("valid") or r["task"] not in tasks:
            continue
        before, after = r.get("usage_before") or {}, r.get("usage_after") or {}
        if before.get("seven_day") is None or after.get("seven_day") is None:
            continue
        if before.get("seven_day_resets_at") != after.get("seven_day_resets_at") or after["seven_day"] < before["seven_day"]:
            out[r["arm"]]["straddled"] += 1
            continue
        out[r["arm"]]["seven_day"] += after["seven_day"] - before["seven_day"]
        out[r["arm"]]["jobs"] += 1
    return out


def summarize(rows, records, tasks, rounds_by_task, dropped=()) -> dict:
    statuses = run_status(records, tasks, dropped)
    kept = final_rows(rows, records)
    counts = passed_rounds(kept)
    scores = task_scores(kept, {t: rounds_by_task[t] for t in tasks})
    paired = [t for t in tasks if statuses[t] == "valid" and None not in scores[t].values()]
    per_task = [{"task": t, "status": statuses[t], "rounds": rounds_by_task[t],
                 "passed": {arm: counts.get((t, arm)) for arm in config.ARMS}, "score": scores[t],
                 "d": scores[t]["horizon"] - scores[t]["control"] if t in paired else None} for t in tasks]
    paired_rows = [r for r in kept if r["task"] in paired]
    return {"planned": len(tasks), "tasks": per_task,
            "primary": permutation([t["d"] for t in per_task if t["d"] is not None]),
            "secondary": secondary(paired_rows), "validity": validity(records, statuses, paired_rows),
            "usage": usage_consumed(records, paired)}


def _num(value, spec=".2f") -> str:
    return "n/a" if value is None else format(value, spec)


def _share(passed, total) -> str:
    return "n/a" if passed is None else f"{passed}/{total} ({passed / total:.2f})"


def _rate(count, n, rate) -> str:
    return "n/a" if rate is None else f"{rate:.2f} ({count}/{n})"


def _met(ok: bool, detail: str = "") -> str:
    return "met" if ok else f"not met{': ' + detail if detail else ''}"


SECONDARY = (
    ("Rounds (reached)", lambda a: f"{a['rounds']} ({a['reached']})"),
    ("Rounds passed", lambda a: str(a["passed"])),
    ("Case score (mean per-round share of cases passed)", lambda a: _num(a["case_score"])),
    ("Rounds with case counts / without", lambda a: f"{a['case_rounds']} / {a['case_missing']}"),
    ('False "done" rate (failed rounds labeled claims_complete)',
     lambda a: _rate(a["false_done"], a["labeled_failed"], a["false_done_rate"])),
    ("Under-claim rate (passed rounds labeled reports_incomplete)",
     lambda a: _rate(a["under_claim"], a["labeled_passed"], a["under_claim_rate"])),
    ("Median agent minutes per round", lambda a: _num(a["agent_minutes_median"], ".1f")),
    ("Tokens per round (mean, input + output)", lambda a: _num(a["tokens_per_round"], ",.0f")),
    ("Total tokens", lambda a: _num(a["tokens_total"], ",")),
    ("Cost reported by Claude Code (USD)", lambda a: _num(a["cost_usd_total"])),
    ("Horizon start rate (rounds reached)",
     lambda a: _rate(a["horizon_started"], a["reached"], a["horizon_start_rate"])),
    ("Horizon CLI calls", lambda a: str(a["horizon_calls"])),
    ("Rounds with more than one result record", lambda a: str(a["extra_result_records"])),
    ("Reached rounds with no result record", lambda a: str(a["no_result_record"])),
)


def render(summary: dict, deviations: str = "") -> str:
    p, sec, val = summary["primary"], summary["secondary"], summary["validity"]
    lo, hi = p["ci95"]
    start = sec["horizon"]["horizon_start_rate"]
    audit = ("not checked: the rows carry no audit flags" if val["audit"] is None
             else _met(not val["audit"], "; ".join(val["audit"])))
    lines = [
        "# EvoCode-Bench first look: Horizon v2 against plain Claude Code", "",
        f"This is a first look, not a definitive comparison: {p['n']} of {summary['planned']} planned tasks were "
        "analysed, with one attempt per arm. Every task's result is shown.", "",
        f"Setup: {config.MODEL} at effort {config.EFFORT}, Claude Code {config.CLAUDE_CODE_VERSION}, Harbor "
        f"{config.HARBOR_VERSION}, a fresh session every round. Both arms get the same time-budget paragraph; the "
        "Horizon arm also gets the Horizon v2 skill and one sentence asking it to use the skill.", "",
        "## Primary result", "",
        "Outcome: the share of a task's rounds passed (reward 1). A missing, aborted or timed-out round counts as "
        f"failed. Analysis: {ANALYSIS}, on d = Horizon - control per task.", "",
        f"- Tasks analysed: {p['n']}",
        f"- Mean d: {_num(p['mean'], '+.2f')}",
        f"- 95% interval (test inversion over shifts in steps of 0.01): [{_num(lo, '+.2f')}, {_num(hi, '+.2f')}]",
        f"- Two-sided permutation p: {_num(p['p'], '.4f')}",
        f"- Horizon won / lost / tied: {p['wins']} / {p['losses']} / {p['ties']}",
        "", "## Per-task results", "",
        "| Task | Rounds | Control | Horizon | d | Status |", "| --- | --- | --- | --- | --- | --- |",
    ]
    lines += [f"| {t['task']} | {t['rounds']} | {_share(t['passed']['control'], t['rounds'])} | "
              f"{_share(t['passed']['horizon'], t['rounds'])} | {_num(t['d'], '+.2f')} | {t['status']} |"
              for t in summary["tasks"]]
    lines += ["", "## Secondary outcomes (analysed tasks)", "", "| Outcome | Control | Horizon |", "| --- | --- | --- |"]
    lines += [f"| {name} | {cell(sec['control'])} | {cell(sec['horizon'])} |" for name, cell in SECONDARY]
    use = summary["usage"]
    lines.append("| Seven-day usage consumed (jobs summed; jobs across a reset) | " + " | ".join(
        f"{use[arm]['seven_day']:.3f} ({use[arm]['jobs']}; {use[arm]['straddled']})" for arm in config.ARMS) + " |")
    runs = "; ".join(f"{arm} {val['runs'][arm]['valid']} valid, {val['runs'][arm]['invalid']} invalid"
                     for arm in config.ARMS)
    lines += [
        "", "## Validity", "",
        f"- Main runs: {runs}",
        f"- Invalid reasons: {', '.join(f'{k} {v}' for k, v in val['invalid_reasons'].items()) or 'none'}",
        f"- Reruns: {', '.join(val['retried']) or 'none'}",
        f"- Dropped tasks: {', '.join(val['dropped']) or 'none'}",
        f"- Horizon started in at least 90% of reached Horizon rounds: "
        f"{_rate(sec['horizon']['horizon_started'], sec['horizon']['reached'], start)}, "
        f"{_met(start is not None and start >= START_FLOOR)}",
        f"- Control never invoked Horizon: {_met(not val['control_horizon'], ', '.join(val['control_horizon']))}",
        f"- No round has more than one result record: "
        f"{_met(not val['extra_result_records'], ', '.join(val['extra_result_records']))}",
        f"- No agent read /tests or /logs/verifier: {audit}",
        f"- Leftover audit (rounds audited {val['leftovers']['audited']} of {val['leftovers']['reached']} reached): "
        f"a claude process killed after {val['leftovers']['killed']}, other processes still running after "
        f"{val['leftovers']['survivors']}, files left in /tmp after {val['leftovers']['tmp']}. The listings stay in "
        "the run directory.",
        f"- Freeze: {', '.join(val['freezes']) or 'not recorded'}",
        f"- Final-message labels: the TB4 blind judge ({config.JUDGE_MODEL}) sees only the message text; "
        f"rubric {RUBRIC}.",
        "", "## Deviations", "", deviations.strip() or "None recorded.",
        "", "## Limits", "",
    ]
    lines += [f"- {limit.format(n=p['n'])}" for limit in LIMITS]
    lines += [
        "", "## Credit and license", "",
        f"EvoCode-Bench by UniPat-AI ({config.EVO_CODE_URL} at {config.EVO_CODE_COMMIT[:7]}); tasks from the Hugging "
        f"Face dataset {config.DATASET_REPO} at revision {config.DATASET_REVISION[:7]}, licensed CC-BY-NC-4.0. This "
        "report is non-commercial. It reproduces no hidden tests, per-case results, reference solutions, round "
        "instructions or transcripts.", "",
    ]
    return "\n".join(lines)


def timing(rows) -> dict:
    """Mean agent minutes per round in each arm from the dry run, and the N they allow."""
    dry = _latest([r for r in rows if r["phase"] == "dry"])
    minutes = {arm: [r["agent_sec"] / 60 for r in dry if r["arm"] == arm and r.get("agent_sec") is not None]
               for arm in config.ARMS}
    means = {arm: _mean(values) for arm, values in minutes.items()}
    n = None if None in means.values() else order.n_rule(means["control"], means["horizon"])
    return {"m_control": means["control"], "m_horizon": means["horizon"],
            "rounds": {arm: len(values) for arm, values in minutes.items()}, "n": n}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--runs", type=Path, default=config.RUNS)
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("label", parents=[common], help="label main-phase final messages with the TB4 blind judge")
    report = sub.add_parser("report", parents=[common], help="render the report from main-phase rows")
    report.add_argument("--prereg", type=Path, default=config.DOCS / "prereg.json")
    report.add_argument("--out", type=Path, default=config.DOCS / "report.md")
    sub.add_parser("timing", parents=[common], help="m_control, m_horizon and the N rule from the dry run")
    args = parser.parse_args(argv)
    rows_path = args.runs / "rows.jsonl"
    if args.action == "label":
        print(label_rows(rows_path, runner.read_token(), judge_fn=judge.judge))
        return 0
    rows = read_rows(rows_path)
    if args.action == "timing":
        result = timing(rows)
        if result["n"] is None:
            result["error"] = "even N = 6 does not fit the budget: stop and report to the user before the freeze"
        print(json.dumps(result))
        return 0 if result["n"] is not None else 1
    # the planned tasks are the pre-registered ones plus the reserves the runner brought in for its drops
    dropped = evo_run.dropped_tasks(evo_run.Paths(args.runs), "main")
    tasks = evo_run.main_tasks(json.loads(args.prereg.read_text()), dropped)
    info = {t["task"]: t["rounds"] for t in dataset.list_tasks(args.runs / "dataset" / config.DATASET_REVISION / "tasks")}
    rounds_by_task = {t: info[t] for t in tasks}
    rows = [r for r in rows if r["phase"] == "main"]
    records = load_records(args.runs, "main")
    found = problems(rows, records, tasks, dropped)
    if found:
        print(json.dumps({"error": "the main phase is not final: finish runs, scoring and labels first", **found}))
        return 2
    deviations = args.runs / "deviations.md"
    summary = summarize(rows, records, tasks, rounds_by_task, dropped)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(render(summary, deviations.read_text() if deviations.exists() else ""))
    print(json.dumps({"primary": summary["primary"], "statuses": {t["task"]: t["status"] for t in summary["tasks"]}}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `sh bench/tb4/test.sh -q tests/test_evo_analysis.py`
Expected: `19 passed` (the time varies).

- [ ] **Step 5: Confirm the reused TB4 judge and report are untouched**

Run: `sh bench/tb4/test.sh -q tests/test_tb4_judge.py tests/test_tb4_report.py tests/test_evo_analysis.py && git diff --exit-code -- bench/tb4 docs/evaluations/tb4-horizon-pilot`
Expected: `36 passed` (the time varies), and `git diff` prints nothing and exits 0.
If `git diff` shows changes, revert them: Task 10 must not edit TB4.

- [ ] **Step 6: Run the whole suite**

Run: `sh bench/tb4/test.sh -q`
Expected: `1077 passed` (1058 after Task 9, plus this task's 19; the time varies). If other work has landed first, expect the previous count plus this task's tests, with 0 failures.

- [ ] **Step 7: Commit**

```bash
git add bench/evo/analysis.py tests/test_evo_analysis.py
git commit -m "feat(evo): blind judge labels, sign-flip permutation analysis and the report"
```

Run: `git show --stat --format=%s HEAD | head -20`
Expected:

```text
feat(evo): blind judge labels, sign-flip permutation analysis and the report

 bench/evo/analysis.py      | 426 +++++++++++++++++++++++++++++++++++++++++++++
 tests/test_evo_analysis.py | 326 ++++++++++++++++++++++++++++++++++
 2 files changed, 752 insertions(+)
```

```text
feat(evo): blind judge labels, sign-flip permutation analysis and the report

 bench/evo/analysis.py      | 426 +++++++++++++++++++++++++++++++++++++++++++++
 tests/test_evo_analysis.py | 317 +++++++++++++++++++++++++++++++++
 2 files changed, 743 insertions(+)
```

### Task 11: Pre-registration freeze

**Deviation:** nine changes from the skeleton. Names and positional signatures are kept.

1. **`collect` takes its other inputs as required keyword-only arguments.** The signature is `collect(n, order, validations, dry_timing, *, task_infos, bundle, pin, observed, code, pilot)`.
   - `order` is the whole `order.json` payload, not just the list.
   - `collect_from_runs(runs)` reads every input from `RUNS`, so `collect` itself stays free of I/O except for repository files.
   - `build_bundle(runs)` returns `{"commit", "modules", "files": {path: sha256}}`.
2. **The freeze pins are written now, not at freeze time.** They are in `tests/test_evo_prereg.py` and skip while neither `prereg.json` nor the `evo-prereg` tag exists. They run as soon as `prereg write` creates the file. After the tag, they read every pinned file from the tag, so later edits elsewhere do not break them. `require_freeze` is what locks the run.
3. **`require_freeze` is `require_freeze(run=subprocess.run, *, repo=config.REPO_ROOT) -> str`.** Task 8's call `require_freeze(subprocess.run)` still works. Beyond the skeleton's checks, it also refuses in three cases:
   - the tag has no `prereg.json`;
   - the frozen paths have uncommitted or untracked changes;
   - `report.md` is committed. This closes the main run, as TB4's committed `results.jsonl` did.
4. **The pre-registration also records the pilot and the code.**
   - For the pilot, it records the latest attempt of each Task 7 check from `RUNS/checks/<job>/findings.json`: the verdict, the failures, the probe's `recorded` block and the file's digest. The freeze is refused when a non-probe check's latest attempt did not pass.
   - For the code, it records the sha256 of every tracked file under `bench/evo` and `bench/tb4`. `write` refuses uncommitted code.
5. **Two new run-directory files:**
   - `RUNS/timing.json`, the operator's saved output of `python3 -m bench.evo.analysis timing`;
   - `RUNS/bundle-manifest.json`.
6. **`build_bundle` runs before the dry run (Task 12 Step 5),** because the horizon arm needs `RUNS/bundle/horizon`. Called again at the freeze, it verifies the bundle already there against a fresh build instead of rebuilding it.
7. **This task also creates `docs/evaluations/evocode-horizon/results-schema.md`.** The file structure lists it, but no other task owns it.
8. **The pre-registration names reserves.** The spec allows a task to be replaced after the freeze for an infrastructure failure, but `prereg.json` and `bench/evo` are frozen, so the replacements are fixed now.
   - `reserve_tasks` lists, in order, the tasks after the Nth measured task whose validation record passes the checks a measured task passes. A task without a record, or failing a check, is skipped, never an error. Task 12 Step 9 validates until 10 tasks pass, so the reserve holds at most 10 - N tasks: four for N = 6, two for N = 8, none for N = 10.
   - `task_details` and `prompts` cover the reserves too, so Task 8's runner can check a reserve's pinned image once a drop brings it in. `arm_orders` still covers only the measured tasks.
   - `OUTCOMES["running"]` and `prereg.md` (a `### Reserve` table) say how the reserves are used. Task 8's `run.main_tasks` and Task 10's report plan `tasks + reserve[:number of dropped main tasks]`.
9. **`collect` holds each selected task to Task 6's rule, to Task 6's current code and to one M per task.**
   - `validation_holds` calls Task 6's `validate.failures` instead of restating the rule. A nop chain that a step's `min_reward` stopped after a graded round 1 therefore passes both.
   - `select_tasks` refuses a record whose `code` is not `validate.CODE_DIGEST`: other copy code made it.
   - `collect` refuses a measured task whose rounds have different agent timeouts (`uniform_agent_timeout` false), because the prompt's M would be wrong for some rounds.

**Files:**
- Create: `bench/evo/prereg.py`
- Create: `tests/test_evo_prereg.py`, with the unit tests and the 7 freeze pins, which skip until the freeze.
- Create: `docs/evaluations/evocode-horizon/results-schema.md`
- Generated later, in Task 12 Step 13, not here: `docs/evaluations/evocode-horizon/prereg.json` and `docs/evaluations/evocode-horizon/prereg.md`.

**Interfaces:**
- Consumes:
  - Task 1, `bench.evo.config`:
    - `REPO_ROOT`, `RUNS` and `DOCS` (which must lie under `REPO_ROOT`);
    - `DATASET_REPO`, `DATASET_REVISION`, `ARCHIVE_PATH`, `EVO_CODE_URL` and `EVO_CODE_COMMIT`;
    - `HARBOR_VERSION`, `CLAUDE_CODE_VERSION`, `MODEL`, `EFFORT`, `MAX_OUTPUT_TOKENS`, `AGENT_TIMEOUT_MULTIPLIER` and `AGENT_SETUP_TIMEOUT_MULTIPLIER`;
    - `BUNDLE_COMMIT` and `BUNDLE_MODULES`;
    - `SEED`, `DOMAINS`, `POOL`, `EXCLUDED_PREFIX`, `WALKTHROUGH` and `ARMS`;
    - `N_CHOICES`, `BUDGET_AGENT_HOURS`, `ROUNDS_PER_TASK_PLAN`, `WEEKLY_CEILING` and `FIVE_HOUR_CEILING`;
    - `JUDGE_MODEL`, `PROBE_MODEL` and `FREEZE_TAG`;
    - `prompt_text(arm: str, minutes: int) -> str` and `image_tag(task: str) -> str`.
  - Task 1, `bench.tb4.bundle.build(repo, commit, out_root, modules=MODULES) -> dict`.
  - Task 2, `bench.evo.dataset.list_tasks(tasks_root) -> list[dict]` (the keys `task`, `rounds`, `agent_timeout_sec`, `verifier_timeout_sec` and `uniform_agent_timeout`) and `dataset.minutes(task_info) -> int`.
  - Task 3, `bench.evo.order`:
    - `seeded_order(pool, seed, domains) -> list[str]`;
    - `arm_order(task, seed) -> tuple[str, str]`. A pool task's order comes from its pair: positions 2k and 2k+1 of `seeded_order(config.POOL, seed, config.DOMAINS)` shuffle with `random.Random(f"{seed}:arms:pair{k}")`, and position 2k+1 gets the reverse. A task outside the pool keeps `random.Random(f"{seed}:arms:{task}")`. `collect` checks `order.json` against it for every pool task;
    - `n_rule(m_control, m_horizon) -> int | None`;
    - `write_order(path)`, used in the tests only. It writes `order.json` with `seed`, `algorithm` (`order.ALGORITHM`, which `collect` copies into `draw.algorithm`), `order` and `arm_orders`.
  - Task 5, `bench.evo.copies.write_prompt(dest, arm, minutes) -> Path`, which writes `prompt_text(arm, minutes) + "\n"`.
  - Task 6, `bench.evo.validate`:
    - `failures(out) -> list[str]`, the validation rule, which reads `rounds`, `oracle`, `nop` and `planted`;
    - `CODE_DIGEST`, the sha256 of `copies.py`, `images.py`, `patch.py`, `steps.py` and `validate.py` (`validate.CODE_FILES`). Its value changes whenever those files change, and `select_tasks` then refuses the older records.
  - Task 6, `RUNS/validation/<task>.json`:
    - It reads `task`, `copy_digest`, `image_id`, `arch`, `rounds`, `oracle`, `nop`, `planted`, `ok`, `reason` and `code`, and ignores other keys (`user`, `python`, `workdir` and the rest).
    - It ignores `validation/summary.json`.
  - Task 7, `RUNS/checks/<job_name>/findings.json`, with `job_name`, `ok`, `failures` and, for the probe, `recorded`. The job names are `check__dummy__oracle__aN`, `check__dummy-vtimeout__oracle__aN`, `check__dummy__control__aN`, `check__dummy-probe__control__aN` and `canary__dummy__horizon__aN`.
  - Task 10, the JSON that `python3 -m bench.evo.analysis timing` prints: `{"m_control", "m_horizon", "rounds", "n"}`.
- Produces (`bench/evo/prereg.py`):
  - The exceptions `FreezeError`, `BundleMismatch(FreezeError)` and `NotFrozen`.
  - The constants:
    - `SPEC`, `JUDGE_RUBRIC`, `PREREG_JSON`, `REPORT`, `CODE_PATHS` and `FROZEN_PATHS` (`bench/evo`, `bench/tb4` and `docs/evaluations/evocode-horizon/prereg.json`);
    - `PILOT_JOBS` and `RECORDED_ONLY`;
    - `ANALYSIS` (`"exact sign-flip permutation, two-sided, alpha 0.05"`) and `OUTCOMES`. `OUTCOMES["running"]` ends with `: a main task dropped for an infrastructure failure is replaced by the next unused reserve in order; with no reserve left a drop stays a drop.` The freeze pin `test_pinned_settings_and_dataset` compares the frozen record against `OUTCOMES`.
  - The bundle, timing and selection functions:
    - `build_bundle(runs=config.RUNS, *, build=bundle.build) -> dict`;
    - `n_from_timing(dry_timing) -> int`;
    - `validation_holds(record, rounds) -> bool`: arch `arm64`, the round count, and `validate.failures(record)` empty;
    - `select_tasks(n, order_list, validations, task_infos) -> (tasks, replaced)`;
    - `reserve_tasks(order_list, tasks, validations: dict, task_infos: dict) -> list`.
  - The record functions:
    - `prompt_file_digest(arm, minutes, *, write=copies.write_prompt) -> str`;
    - `versions() -> dict`;
    - `collect(...) -> dict`;
    - `render(prereg) -> str`. The measured-task table lists only `tasks`. A `### Reserve` subsection follows the replaced list, with a reserve table or `No reserve: validation stopped at the Nth passing task, or no later task passed it.` Both tables come from the private helper `_task_table(p, tasks)`;
    - `write(prereg, docs_dir=config.DOCS) -> (json_path, md_path)`.
  - The input readers:
    - `observed_versions(run=subprocess.run) -> dict`;
    - `code_digests(repo=config.REPO_ROOT, run=subprocess.run) -> dict`;
    - `read_pilot(checks_dir) -> dict`;
    - `collect_from_runs(runs=config.RUNS) -> dict`.
  - The freeze checks:
    - `frozen_commit(run=subprocess.run, repo=config.REPO_ROOT) -> str | None`;
    - `require_freeze(run=subprocess.run, *, repo=config.REPO_ROOT) -> str`.
  - The CLI, `python3 -m bench.evo.prereg bundle|write|check [--runs DIR] [--docs DIR]`. It exits 1 with `FreezeError: ...` or `NotFrozen: ...` on stderr. `bundle` prints `{"commit", "files", "skill"}`, `write` prints `{"n", "tasks", "replaced", "reserve", "json", "md"}` and `check` prints `{"frozen": commit}`.
  - The top-level keys of `prereg.json`:
    - `study`, `spec`, `freeze_tag`, `n` and `n_rule`;
    - `tasks`, `replaced`, `reserve` and `task_details`. `tasks` lists the measured tasks in order and `reserve` the pre-registered replacements in order. `task_details` holds the measured tasks and the reserves, each with `order`, `rounds`, `minutes`, `agent_timeout_sec`, `verifier_timeout_sec`, `arm_order`, `image_tag`, `image_id` and `copy_digest`.
      - Task 8's `run.main_tasks(prereg, dropped)` returns `tasks + reserve[:len(set(dropped))]`. Task 8's `phase_tasks(..., paths=)` and Task 10's `report` both use it, with the drops that `dropped_tasks(paths, "main")` reads from `deviations.md`.
      - Task 8's `run.main` raises `ImageChanged` for any of those tasks whose validated image differs from `task_details[task]["image_id"]`.
    - `arms`, `arm_orders` (the measured tasks only), `draw` and `prompts` (the minutes of the measured tasks and the reserves);
    - `bundle`, `versions`, `observed`, `dataset`, `evocode_code` and `judge`;
    - `analysis`, `outcomes`, `pilot` and `code`.

- [ ] **Step 1: Write the failing test.** Create `tests/test_evo_prereg.py`. The unit tests come first, then the 7 freeze pins, then the tests for the nop chain, the agent-timeout guard and the reserves:

```python
import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from bench.evo import config, order, prereg, validate
from bench.tb4 import bundle


def cp(cmd, code=0, out=""):
    return subprocess.CompletedProcess(cmd, code, out, "")


def sha(data) -> str:
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


ORDER = order.seeded_order(config.POOL, config.SEED, config.DOMAINS)
TIMING_8 = {"m_control": 20.0, "m_horizon": 25.0, "rounds": {"control": 5, "horizon": 5}, "n": 8}
PIN = {"revision": config.DATASET_REVISION, "sha256": "a" * 64, "lfs_oid": "a" * 64,
       "url": "https://huggingface.co/x", "fetched_at": "2026-10-09T12:00:00Z"}
BUNDLE = {"commit": config.BUNDLE_COMMIT, "modules": list(config.BUNDLE_MODULES),
          "files": {"horizon/SKILL.md": "1" * 64, "horizon/scripts/reviewing.py": "2" * 64}}
OBSERVED = {"harbor": config.HARBOR_VERSION, "claude (host)": "2.1.293 (Claude Code)",
            "docker server": "29.0.0", "docker arch": "aarch64", "python (host)": "3.14.4"}
CODE = {"bench/evo/config.py": "3" * 64}


def check(job, attempt=1, ok=True, failures=(), **extra):
    return {"job_name": f"{job}__a{attempt}", "attempt": attempt, "ok": ok, "failures": list(failures),
            "sha256": sha(job), **extra}


PILOT = {job: check(job) for job in prereg.PILOT_JOBS}
PILOT["check__dummy-probe__control"] = check("check__dummy-probe__control", ok=None,
                                             recorded={"round_2_started": True, "round_1_reward": 0.0})


def infos(sixty=()):
    return {t: {"task": t, "dir": f"/data/{t}", "rounds": 10, "step_names": [f"round-{i}" for i in range(1, 11)],
                "agent_timeout_sec": 3600.0 if t in sixty else 1800.0, "verifier_timeout_sec": 900.0,
                "reward_strategy": "mean"} for t in ORDER}


def valid(task, rounds=10, **changes):
    record = {"task": task, "copy_digest": sha(task), "image_id": "sha256:" + sha("image " + task),
              "arch": "arm64", "rounds": rounds, "oracle": [1.0] * rounds, "nop": [0.0] * rounds,
              "planted": [1.0] * rounds, "ok": True, "reason": None, "code": validate.CODE_DIGEST}
    record.update(changes)
    return record


def payload():
    return {"seed": config.SEED, "algorithm": "domain round-robin", "order": list(ORDER),
            "arm_orders": {t: list(order.arm_order(t, config.SEED)) for t in ORDER}}


def make(n=8, validations=None, timing=TIMING_8, task_infos=None, order_payload=None, **changes):
    kwargs = {"task_infos": task_infos or infos(), "bundle": BUNDLE, "pin": PIN, "observed": OBSERVED,
              "code": CODE, "pilot": PILOT}
    kwargs.update(changes)
    validations = {t: valid(t) for t in ORDER} if validations is None else validations
    return prereg.collect(n, order_payload or payload(), validations, timing, **kwargs)


def test_collect_takes_the_first_n_ok_tasks_in_the_seeded_order():
    validations = {t: valid(t) for t in ORDER}
    validations[ORDER[1]] = valid(ORDER[1], ok=False, reason="nop reward 1.0 in round-3")
    out = make(validations=validations, task_infos=infos(sixty={ORDER[2]}))
    assert out["n"] == 8 and out["tasks"] == [ORDER[0]] + ORDER[2:9]
    assert out["replaced"] == [{"task": ORDER[1], "reason": "nop reward 1.0 in round-3"}]
    assert out["arm_orders"] == {t: list(order.arm_order(t, config.SEED)) for t in out["tasks"]}
    third = out["task_details"][ORDER[2]]
    assert (third["order"], third["rounds"], third["minutes"]) == (3, 10, 60)
    assert third["image_id"] == validations[ORDER[2]]["image_id"]
    assert third["image_tag"] == config.image_tag(ORDER[2])
    assert third["copy_digest"] == validations[ORDER[2]]["copy_digest"]
    assert out["task_details"][ORDER[0]]["minutes"] == 30
    assert sorted(out["prompts"]["texts"]) == ["control@30", "control@60", "horizon@30", "horizon@60"]
    assert out["prompts"]["texts"]["horizon@60"] == config.prompt_text("horizon", 60)
    assert out["prompts"]["text_sha256"]["control@30"] == sha(config.prompt_text("control", 30))
    assert out["n_rule"]["m_control"] == 20.0 and out["n_rule"]["n"] == 8
    assert out["analysis"] == "exact sign-flip permutation, two-sided, alpha 0.05"
    assert out["dataset"]["pin"] == PIN and out["bundle"] == BUNDLE and out["pilot"] == PILOT
    assert out["spec"]["path"] == prereg.SPEC
    assert out["spec"]["sha256"] == sha((config.REPO_ROOT / prereg.SPEC).read_bytes())


def test_collect_needs_no_validation_beyond_the_nth_ok_task():
    validations = {t: valid(t) for t in ORDER[:6]}
    assert make(n=6, validations=validations, timing={"m_control": 30.0, "m_horizon": 30.0})["tasks"] == ORDER[:6]


def test_collect_is_deterministic_and_survives_json():
    first, second = make(), make()
    assert first == second == json.loads(json.dumps(first))


def broken(case):
    validations = {t: valid(t) for t in ORDER}
    if case == "unvalidated task inside the window":
        del validations[ORDER[3]]
        return {"validations": validations}
    if case == "too few ok tasks":
        return {"validations": {t: valid(t, ok=t in ORDER[:7]) for t in ORDER}}
    if case == "ok claimed but a nop round passes":
        validations[ORDER[0]] = valid(ORDER[0], nop=[0.0] * 9 + [1.0])
        return {"validations": validations}
    if case == "ok claimed on amd64":
        validations[ORDER[0]] = valid(ORDER[0], arch="amd64")
        return {"validations": validations}
    if case == "validated by older copy code":
        validations[ORDER[0]] = valid(ORDER[0], code="older")
        return {"validations": validations}
    if case == "ok claimed with a missing round":
        validations[ORDER[0]] = valid(ORDER[0], oracle=[1.0] * 9 + [None])
        return {"validations": validations}
    return {
        "n differs from the rule": {"n": 10},
        "no fit at all": {"n": 6, "timing": {"m_control": 40.0, "m_horizon": 40.0}},
        "a dry arm without minutes": {"timing": {"m_control": 20.0, "m_horizon": None}},
        "order not seeded": {"order_payload": dict(payload(), order=list(reversed(ORDER)))},
        "arm order changed": {"order_payload": dict(payload(), arm_orders={t: ["horizon", "control"] for t in ORDER})},
        "pin mismatch": {"pin": dict(PIN, lfs_oid="b" * 64)},
        "pin of another revision": {"pin": dict(PIN, revision="main")},
        "task missing from the dataset": {"task_infos": {t: i for t, i in infos().items() if t != ORDER[5]}},
        "another harbor": {"observed": dict(OBSERVED, harbor="0.25.0")},
        "bundle from another commit": {"bundle": dict(BUNDLE, commit="2f65f2a24d753b6875bd126fc989335baff82ed8")},
        "bundle without reviewing": {"bundle": dict(BUNDLE, files={"horizon/SKILL.md": "1" * 64})},
        "pilot check missing": {"pilot": {k: v for k, v in PILOT.items() if k != "canary__dummy__horizon"}},
        "pilot check failed": {"pilot": dict(PILOT, canary__dummy__horizon=check(
            "canary__dummy__horizon", ok=False, failures=["no Write tool call created the file without an error"]))},
    }[case]


@pytest.mark.parametrize("case", [
    "unvalidated task inside the window", "too few ok tasks", "ok claimed but a nop round passes",
    "ok claimed on amd64", "ok claimed with a missing round", "validated by older copy code", "n differs from the rule", "no fit at all",
    "a dry arm without minutes", "order not seeded", "arm order changed", "pin mismatch",
    "pin of another revision", "task missing from the dataset", "another harbor", "bundle from another commit",
    "bundle without reviewing", "pilot check missing", "pilot check failed"])
def test_collect_refuses_inputs_that_break_the_protocol(case):
    with pytest.raises(prereg.FreezeError):
        make(**broken(case))


def test_n_from_timing():
    assert prereg.n_from_timing(TIMING_8) == 8
    assert prereg.n_from_timing({"m_control": 15.0, "m_horizon": 20.0}) == 10
    for timing in ({"m_control": 40.0, "m_horizon": 40.0}, {"m_control": None, "m_horizon": 1.0}, {}):
        with pytest.raises(prereg.FreezeError):
            prereg.n_from_timing(timing)


def test_prompt_digest_is_of_the_file_write_prompt_makes():
    data = (config.prompt_text("horizon", 30) + "\n").encode()
    assert prereg.prompt_file_digest("horizon", 30) == sha(data)

    def wrong(dest, arm, minutes):
        Path(dest).write_text("Do something else.\n")
        return Path(dest)

    with pytest.raises(prereg.FreezeError):
        prereg.prompt_file_digest("control", 30, write=wrong)


def test_write_renders_both_files(tmp_path):
    out = make(task_infos=infos(sixty={ORDER[0]}))
    json_path, md_path = prereg.write(out, tmp_path / "docs")
    assert json.loads(json_path.read_text()) == out
    text = md_path.read_text()
    for task in out["tasks"]:
        assert f"`{task}`" in text
    for key, prompt in out["prompts"]["texts"].items():
        assert all(paragraph in text for paragraph in prompt.split("\n\n"))
        assert out["prompts"]["text_sha256"][key] in text and out["prompts"]["file_sha256"][key] in text
    for needle in ("N = 8", "evo-prereg", "exact sign-flip permutation, two-sided, alpha 0.05", config.BUNDLE_COMMIT,
                   "../../superpowers/specs/2026-10-09-evocode-horizon-design.md", PIN["sha256"],
                   "`canary__dummy__horizon__a1`", "round_2_started", out["task_details"][ORDER[0]]["image_id"]):
        assert needle in text
    assert "None" not in text.replace("None of", "")


def test_build_bundle_builds_then_verifies(tmp_path):
    runs = tmp_path / "runs"
    manifest = prereg.build_bundle(runs)
    assert manifest["commit"] == config.BUNDLE_COMMIT and manifest["modules"] == list(config.BUNDLE_MODULES)
    assert len(manifest["files"]) == 12 and "horizon/scripts/reviewing.py" in manifest["files"]
    assert json.loads((runs / "bundle-manifest.json").read_text()) == manifest
    assert prereg.build_bundle(runs) == manifest
    (runs / "bundle" / "horizon" / "scripts" / "reviewing.py").write_text("# edited\n")
    with pytest.raises(prereg.BundleMismatch, match="reviewing.py"):
        prereg.build_bundle(runs)


def test_build_bundle_refuses_extra_files(tmp_path):
    prereg.build_bundle(tmp_path)
    (tmp_path / "bundle" / "horizon" / "scripts" / "__pycache__").mkdir()
    (tmp_path / "bundle" / "horizon" / "scripts" / "__pycache__" / "goal.cpython-312.pyc").write_bytes(b"x")
    with pytest.raises(prereg.BundleMismatch, match="pyc"):
        prereg.build_bundle(tmp_path)


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid",
                           *args], capture_output=True, text=True, check=True).stdout


def test_code_digests_hash_tracked_code_and_refuse_uncommitted_changes(tmp_path):
    repo = tmp_path / "repo with space "
    (repo / "bench" / "evo").mkdir(parents=True)
    (repo / "bench" / "tb4").mkdir()
    (repo / "bench" / "evo" / "a.py").write_text("A = 1\n")
    (repo / "bench" / "tb4" / "b.py").write_text("B = 2\n")
    (repo / "README.md").write_text("not code\n")
    git(tmp_path, "init", "-q", str(repo))
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "code")
    assert prereg.code_digests(repo) == {"bench/evo/a.py": sha("A = 1\n"), "bench/tb4/b.py": sha("B = 2\n")}
    (repo / "README.md").write_text("changed, but not code\n")
    assert len(prereg.code_digests(repo)) == 2
    (repo / "bench" / "evo" / "new.py").write_text("")
    with pytest.raises(prereg.FreezeError, match="new.py"):
        prereg.code_digests(repo)
    (repo / "bench" / "evo" / "new.py").unlink()
    (repo / "bench" / "tb4" / "b.py").write_text("B = 3\n")
    with pytest.raises(prereg.FreezeError, match="b.py"):
        prereg.code_digests(repo)


def fake_git(tag="abc", prereg_at_tag=True, ancestor=True, diff_clean=True, dirty="", report_tracked=False):
    def fake(cmd, **kwargs):
        sub = cmd[3]
        if sub == "rev-parse":
            return cp(cmd, 0 if tag else 1, tag + "\n" if tag else "")
        if sub == "cat-file":
            return cp(cmd, 0 if prereg_at_tag else 128)
        if sub == "merge-base":
            return cp(cmd, 0 if ancestor else 1)
        if sub == "diff":
            return cp(cmd, 0 if diff_clean else 1)
        if sub == "status":
            return cp(cmd, 0, dirty)
        if sub == "ls-files":
            return cp(cmd, 0 if report_tracked else 1)
        raise AssertionError(cmd)
    return fake


def test_require_freeze_accepts_a_descendant_with_the_frozen_paths_unchanged():
    calls = []

    def recording(cmd, **kwargs):
        calls.append(cmd)
        return fake_git()(cmd, **kwargs)

    assert prereg.require_freeze(recording) == "abc"
    diff = next(c for c in calls if c[3] == "diff")
    assert diff[4:] == ["--quiet", "abc", "HEAD", "--", "bench/evo", "bench/tb4",
                        "docs/evaluations/evocode-horizon/prereg.json"]
    assert ["refs/tags/evo-prereg^{commit}"] == [c[-1] for c in calls if c[3] == "rev-parse"]


@pytest.mark.parametrize("kwargs", [
    {"tag": ""}, {"prereg_at_tag": False}, {"ancestor": False}, {"diff_clean": False},
    {"dirty": " M bench/evo/run.py\n"}, {"dirty": "?? bench/evo/extra.py\n"}, {"report_tracked": True}])
def test_require_freeze_refuses(kwargs):
    with pytest.raises(prereg.NotFrozen):
        prereg.require_freeze(fake_git(**kwargs))


def runs_dir(tmp_path, timing=TIMING_8):
    runs = tmp_path / "runs"
    order.write_order(runs / "order.json")
    (runs / "timing.json").write_text(json.dumps(timing))
    base = runs / "dataset" / config.DATASET_REVISION
    base.mkdir(parents=True)
    (base / "PIN.json").write_text(json.dumps(PIN))
    (runs / "validation").mkdir()
    for task in ORDER[:9]:
        (runs / "validation" / f"{task}.json").write_text(json.dumps(valid(task, ok=task != ORDER[4],
                                                                           reason="build failed")))
    (runs / "validation" / "summary.json").write_text(json.dumps({"selected": []}))
    findings = [(job, 1, None if job in prereg.RECORDED_ONLY else True)
                for job in prereg.PILOT_JOBS if job != "canary__dummy__horizon"]
    findings += [("canary__dummy__horizon", 1, False), ("canary__dummy__horizon", 2, False),
                 ("canary__dummy__horizon", 10, True)]
    for job, attempt, ok in findings:
        path = runs / "checks" / f"{job}__a{attempt}" / "findings.json"
        path.parent.mkdir(parents=True)
        data = {"job_name": f"{job}__a{attempt}", "ok": ok, "failures": [] if ok is not False else ["x"],
                "facts": {"pwd": "/app"}}
        if ok is None:
            data["recorded"] = {"round_2_started": True}
        path.write_text(json.dumps(data))
    (runs / "checks" / "check__dummy__oracle__a1" / "harbor.log").write_text("log\n")
    return runs


@pytest.fixture
def offline(monkeypatch):
    monkeypatch.setattr(prereg.dataset, "list_tasks", lambda root: list(infos().values()))
    monkeypatch.setattr(prereg, "build_bundle", lambda runs: BUNDLE)
    monkeypatch.setattr(prereg, "observed_versions", lambda run=None: OBSERVED)
    monkeypatch.setattr(prereg, "code_digests", lambda repo=None, run=None: CODE)
    monkeypatch.setattr(prereg, "frozen_commit", lambda run=None, repo=None: None)


def test_main_write_collects_from_the_run_directory(tmp_path, offline, capsys):
    runs, docs = runs_dir(tmp_path), tmp_path / "docs"
    (runs / "validation" / f"{ORDER[9]}.json").write_text(json.dumps(valid(ORDER[9])))
    assert prereg.main(["write", "--runs", str(runs), "--docs", str(docs)]) == 0
    printed = json.loads(capsys.readouterr().out)
    expected = ORDER[:4] + ORDER[5:9]
    assert printed["n"] == 8 and printed["tasks"] == expected and printed["reserve"] == [ORDER[9]]
    assert printed["replaced"] == [{"task": ORDER[4], "reason": "build failed"}]
    saved = json.loads((docs / "prereg.json").read_text())
    assert saved["tasks"] == expected and saved["pilot"]["check__dummy-probe__control"]["recorded"]
    assert saved["pilot"]["canary__dummy__horizon"]["attempt"] == 10
    assert (docs / "prereg.md").is_file()


def test_main_write_refuses_a_failed_latest_check(tmp_path, offline, capsys):
    runs = runs_dir(tmp_path)
    assert prereg.main(["write", "--runs", str(runs), "--docs", str(tmp_path / "docs")]) == 0
    capsys.readouterr()
    path = runs / "checks" / "canary__dummy__horizon__a11" / "findings.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"job_name": "canary__dummy__horizon__a11", "ok": False, "failures": ["refused"]}))
    assert prereg.main(["write", "--runs", str(runs), "--docs", str(tmp_path / "docs")]) == 1
    assert "canary__dummy__horizon__a11" in capsys.readouterr().err


def test_main_write_stops_when_the_rule_does_not_fit(tmp_path, offline, capsys):
    runs = runs_dir(tmp_path, timing={"m_control": 45.0, "m_horizon": 50.0})
    assert prereg.main(["write", "--runs", str(runs), "--docs", str(tmp_path / "docs")]) == 1
    assert "even N = 6 does not fit" in capsys.readouterr().err
    assert not (tmp_path / "docs").exists()


def test_main_write_names_a_missing_input(tmp_path, offline, capsys):
    runs = runs_dir(tmp_path)
    (runs / "timing.json").unlink()
    assert prereg.main(["write", "--runs", str(runs), "--docs", str(tmp_path / "docs")]) == 1
    assert "timing.json" in capsys.readouterr().err


def test_main_write_refuses_after_the_tag(tmp_path, offline, monkeypatch, capsys):
    monkeypatch.setattr(prereg, "frozen_commit", lambda run=None, repo=None: "abc")
    assert prereg.main(["write", "--runs", str(runs_dir(tmp_path)), "--docs", str(tmp_path / "docs")]) == 1
    assert "already frozen at abc" in capsys.readouterr().err


def test_main_check_and_bundle(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(prereg, "require_freeze", lambda run=None: "abc")
    assert prereg.main(["check"]) == 0
    assert json.loads(capsys.readouterr().out) == {"frozen": "abc"}

    def refuse(run=None):
        raise prereg.NotFrozen("tag evo-prereg is missing")

    monkeypatch.setattr(prereg, "require_freeze", refuse)
    assert prereg.main(["check"]) == 1
    assert "tag evo-prereg is missing" in capsys.readouterr().err
    monkeypatch.setattr(prereg, "build_bundle", lambda runs: BUNDLE)
    assert prereg.main(["bundle", "--runs", str(tmp_path)]) == 0
    assert json.loads(capsys.readouterr().out) == {"commit": config.BUNDLE_COMMIT, "files": 2,
                                                   "skill": str(tmp_path / "bundle" / "horizon")}


# Freeze pins: they run once prereg.json exists or the evo-prereg tag does, and pin the record to the repository.
PREREG = config.DOCS / "prereg.json"
frozen = pytest.mark.skipif(not PREREG.exists() and prereg.frozen_commit() is None,
                            reason="before the freeze: no prereg.json and no evo-prereg tag")


def _repo_git(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(config.REPO_ROOT), *args], capture_output=True)


def _blob(path: str) -> bytes:
    """A repository file as frozen: at the tag once it exists, else in the working tree."""
    tag = prereg.frozen_commit()
    if tag is None:
        return (config.REPO_ROOT / path).read_bytes()
    proc = _repo_git("show", f"{tag}:{path}")
    assert proc.returncode == 0, f"{path} is not in {config.FREEZE_TAG}"
    return proc.stdout


@pytest.fixture(scope="module")
def pinned():
    return json.loads(_blob(prereg.PREREG_JSON))


@frozen
def test_pinned_record_is_the_committed_one(pinned):
    assert json.loads(PREREG.read_text()) == pinned


@frozen
def test_pinned_task_count_and_n_rule(pinned):
    assert pinned["n"] in (6, 8, 10) and len(pinned["tasks"]) == pinned["n"] == len(set(pinned["tasks"]))
    assert pinned["n"] == order.n_rule(pinned["n_rule"]["m_control"], pinned["n_rule"]["m_horizon"])


@frozen
def test_pinned_tasks_are_the_first_ok_tasks_of_the_seeded_order(pinned):
    seeded = order.seeded_order(config.POOL, config.SEED, config.DOMAINS)
    assert pinned["draw"]["order"] == seeded
    walked = pinned["tasks"] + [r["task"] for r in pinned["replaced"]]
    assert sorted(walked, key=seeded.index) == seeded[:len(walked)]
    assert [t for t in seeded if t in pinned["tasks"]] == pinned["tasks"]
    assert not any(t.startswith(config.EXCLUDED_PREFIX) for t in pinned["tasks"])
    assert pinned["arm_orders"] == {t: list(order.arm_order(t, config.SEED)) for t in pinned["tasks"]}


@frozen
def test_pinned_prompts_match_the_configured_texts(pinned):
    prompts = pinned["prompts"]
    for key, text in prompts["texts"].items():
        arm, minutes = key.split("@")
        assert text == config.prompt_text(arm, int(minutes))
        assert prompts["text_sha256"][key] == sha(text)
        assert prompts["file_sha256"][key] == prereg.prompt_file_digest(arm, int(minutes))
    for task in pinned["tasks"]:
        minutes = pinned["task_details"][task]["minutes"]
        assert {f"{arm}@{minutes}" for arm in config.ARMS} <= set(prompts["texts"])


@frozen
def test_pinned_bundle_rebuilds_from_its_commit(pinned, tmp_path):
    files = bundle.build(config.REPO_ROOT, config.BUNDLE_COMMIT, tmp_path / "bundle", modules=config.BUNDLE_MODULES)
    assert pinned["bundle"] == {"commit": config.BUNDLE_COMMIT, "modules": list(config.BUNDLE_MODULES),
                                "files": files}


@frozen
def test_pinned_digests_match_the_repository(pinned):
    assert pinned["spec"] == {"path": prereg.SPEC, "sha256": sha(_blob(prereg.SPEC))}
    assert pinned["judge"]["sha256"] == sha(_blob(prereg.JUDGE_RUBRIC))
    for path, digest in pinned["code"].items():
        assert sha(_blob(path)) == digest, path
    assert any(p.startswith("bench/evo/") for p in pinned["code"])
    assert any(p.startswith("bench/tb4/") for p in pinned["code"])


@frozen
def test_pinned_settings_and_dataset(pinned):
    assert pinned["versions"] == prereg.versions() and pinned["observed"]["harbor"] == config.HARBOR_VERSION
    assert pinned["analysis"] == prereg.ANALYSIS and pinned["outcomes"] == prereg.OUTCOMES
    pin = pinned["dataset"]["pin"]
    assert pinned["dataset"]["revision"] == pin["revision"] == config.DATASET_REVISION
    assert pin["sha256"] == pin["lfs_oid"] and len(pin["sha256"]) == 64
    assert set(prereg.PILOT_JOBS) <= set(pinned["pilot"])
    assert all(pinned["pilot"][job]["ok"] is True for job in prereg.PILOT_JOBS if job not in prereg.RECORDED_ONLY)


def test_collect_accepts_a_nop_chain_that_stopped_after_a_graded_first_round():
    # A step with min_reward aborts the chain after the nop's failed round 1; validate.failures accepts this.
    validations = {t: valid(t) for t in ORDER}
    validations[ORDER[0]] = valid(ORDER[0], nop=[0.0] + [None] * 9)
    assert make(validations=validations)["tasks"][0] == ORDER[0]


def test_collect_refuses_a_task_whose_rounds_have_different_agent_timeouts():
    task_infos = infos()
    task_infos[ORDER[0]] = dict(task_infos[ORDER[0]], uniform_agent_timeout=False)
    with pytest.raises(prereg.FreezeError, match="agent timeout"):
        make(task_infos=task_infos)


def test_collect_preregisters_the_ok_tasks_after_the_nth_as_reserves():
    validations = {t: valid(t) for t in ORDER}
    validations[ORDER[9]] = valid(ORDER[9], ok=False, reason="nop reward 1.0 in round-2")
    validations[ORDER[10]] = valid(ORDER[10], code="older")  # judged by other copy code
    validations[ORDER[11]] = valid(ORDER[11], nop=[0.0] * 9 + [1.0])  # says ok, but a nop round passes
    task_infos = infos(sixty={ORDER[12]})
    del validations[ORDER[8]]  # never validated: the walk stopped at the Nth ok task
    out = make(validations=validations, task_infos=task_infos)
    assert out["tasks"] == ORDER[:8] and out["reserve"] == [ORDER[12]]
    validations[ORDER[8]] = valid(ORDER[8])
    task_infos[ORDER[12]] = dict(task_infos[ORDER[12]], uniform_agent_timeout=False)
    out = make(validations=validations, task_infos=task_infos)
    assert out["tasks"] == ORDER[:8] and out["reserve"] == [ORDER[8]]
    assert set(out["task_details"]) == set(ORDER[:9])
    reserve = out["task_details"][ORDER[8]]
    assert (reserve["order"], reserve["arm_order"]) == (9, list(order.arm_order(ORDER[8], config.SEED)))
    assert reserve["image_id"] == validations[ORDER[8]]["image_id"]
    assert reserve["copy_digest"] == validations[ORDER[8]]["copy_digest"]
    with_60 = make(validations={t: valid(t) for t in ORDER}, task_infos=infos(sixty={ORDER[12]}))
    assert with_60["reserve"] == ORDER[8:] and "horizon@60" in with_60["prompts"]["texts"]
    text = prereg.render(with_60)
    assert "replaced by the next unused reserve in order" in text and "a drop stays a drop" in text
    assert all(f"`{task}`" in text for task in with_60["reserve"])
    assert "None" not in text.replace("None of", "")

def test_prereg_md_states_the_nop_rule_and_why_a_reserve_can_be_empty():
    validations = {t: valid(t) for t in ORDER[:8]}
    text = prereg.render(make(validations=validations))
    assert "nop reward below 1 on round 1 and on every later round it reached" in text
    assert "No reserve: validation stopped at the Nth passing task, or no later task passed it." in text
```

- [ ] **Step 2: Run it to make sure it fails**

Run: `sh bench/tb4/test.sh -q tests/test_evo_prereg.py`
Expected: FAIL at collection with `E   ImportError: cannot import name 'prereg' from 'bench.evo'`, followed by the worktree's `bench/evo/__init__.py` path, then `1 error during collection`.

- [ ] **Step 3: Implement.** Create `bench/evo/prereg.py`:

```python
"""Freeze the EvoCode first look: build the Horizon bundle, write the pre-registration, check the freeze tag."""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from bench.evo import config, copies, dataset, validate
from bench.evo import order as evo_order
from bench.tb4 import bundle as tb4_bundle

SPEC = "docs/superpowers/specs/2026-10-09-evocode-horizon-design.md"
JUDGE_RUBRIC = "docs/evaluations/tb4-horizon-pilot/prompts/judge.md"
DOCS_REL = config.DOCS.relative_to(config.REPO_ROOT).as_posix()
PREREG_JSON = f"{DOCS_REL}/prereg.json"
REPORT = f"{DOCS_REL}/report.md"
CODE_PATHS = ("bench/evo", "bench/tb4")
FROZEN_PATHS = (*CODE_PATHS, PREREG_JSON)
# Task 7's Harbor check jobs, by job name without the attempt; the probe is recorded, not asserted.
PILOT_JOBS = ("check__dummy__oracle", "check__dummy-vtimeout__oracle", "check__dummy__control",
              "check__dummy-probe__control", "canary__dummy__horizon")
RECORDED_ONLY = ("check__dummy-probe__control",)
_ATTEMPT = re.compile(r"^(?P<job>.+)__a(?P<attempt>\d+)$")
ANALYSIS = "exact sign-flip permutation, two-sided, alpha 0.05"
OUTCOMES = {
    "primary": (
        "Per-task fraction of rounds passed: a round passes when its verifier reward is at least 1.0, and a missing, "
        "aborted or timed-out round counts as failed. Each task's difference is d = horizon - control. Reported: "
        "the mean d with a 95% interval from inverting the exact sign-flip test over shifts in steps of 0.01 "
        "within [-1, 1], the two-sided permutation p over all 2^N sign assignments, and the tasks won, lost and tied."
    ),
    "secondary": [
        "Case score: the mean per-round share of hidden test cases passed, from the pass/total counts on the "
        "verifier's CASE_SUMMARY line; rounds without counts are excluded and counted.",
        "False done rate: the share of failed rounds whose final message the TB4 blind judge labels claims_complete.",
        "Under-claim rate: the share of passed rounds whose final message the judge labels reports_incomplete.",
        "Cost: median agent minutes per round, total tokens, and subscription usage consumed.",
        "Horizon start rate in Horizon rounds, Horizon calls in control rounds, and extra result records.",
    ],
    "validity": [
        "Horizon was started in at least 90% of Horizon rounds.",
        "Control never invoked Horizon.",
        "Each round has exactly one result record; any extra record is flagged.",
        "No agent read /tests or /logs/verifier.",
        "The leftover-file audit shows what survives a round.",
    ],
    "running": (
        "One attempt per arm per task; a task's two arms run back to back in its seeded arm order. An invalid run "
        "(usage limit, API error, infrastructure failure, host sleep or interruption) is rerun once; a second "
        "invalid run drops the task, logged in deviations.md. After the freeze a task is replaced only for an "
        "infrastructure failure, never because of its results: a main task dropped for an infrastructure failure "
        "is replaced by the next unused reserve in order; with no reserve left a drop stays a drop."
    ),
}


class FreezeError(RuntimeError):
    """The inputs do not support a freeze: fix them before the freeze, or stop and ask the user."""


class BundleMismatch(FreezeError):
    """RUNS/bundle differs from the bundle built at BUNDLE_COMMIT."""


class NotFrozen(RuntimeError):
    """Main-phase runs need the evo-prereg tag in HEAD and the frozen paths unchanged since it."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def tree_files(root: Path) -> dict:
    """{relative path: sha256} for every file under root; a symlink is listed as 'symlink'."""
    found = {}
    for path in sorted(Path(root).rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_symlink():
            found[rel] = "symlink"
        elif path.is_file():
            found[rel] = sha256_file(path)
    return found


def _write_text(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _read_json(path: Path, what: str):
    if not path.is_file():
        raise FreezeError(f"missing {path}: {what}")
    return json.loads(path.read_text())


def build_bundle(runs=config.RUNS, *, build=tb4_bundle.build) -> dict:
    """Build RUNS/bundle at BUNDLE_COMMIT, or verify the one already there; write RUNS/bundle-manifest.json."""
    runs = Path(runs)
    runs.mkdir(parents=True, exist_ok=True)
    out = runs / "bundle"
    if out.exists() and any(out.iterdir()):
        with tempfile.TemporaryDirectory() as tmp:
            files = build(config.REPO_ROOT, config.BUNDLE_COMMIT, Path(tmp) / "bundle", modules=config.BUNDLE_MODULES)
        found = tree_files(out)
        differ = sorted(k for k in set(found) | set(files) if found.get(k) != files.get(k))
        if differ:
            raise BundleMismatch(f"{out} differs from commit {config.BUNDLE_COMMIT[:7]} in {differ}")
    else:
        files = build(config.REPO_ROOT, config.BUNDLE_COMMIT, out, modules=config.BUNDLE_MODULES)
    manifest = {"commit": config.BUNDLE_COMMIT, "modules": list(config.BUNDLE_MODULES), "files": files}
    _write_text(runs / "bundle-manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    return manifest


def n_from_timing(dry_timing: dict) -> int:
    """N from the dry run's minutes per round, by the pre-registered N rule."""
    m_control, m_horizon = dry_timing.get("m_control"), dry_timing.get("m_horizon")
    if m_control is None or m_horizon is None:
        raise FreezeError(f"the dry run gave no minutes per round for an arm: {dry_timing}")
    n = evo_order.n_rule(m_control, m_horizon)
    if n is None:
        raise FreezeError(f"even N = 6 does not fit the budget (m_control {m_control:.1f}, m_horizon "
                          f"{m_horizon:.1f}): stop and report to the user before the freeze")
    return n


def validation_holds(record: dict, rounds: int) -> bool:
    """Task 6's rule (validate.failures), re-applied to the saved per-round rewards, plus arch and round count."""
    lists = [record.get(kind) or [] for kind in ("oracle", "nop", "planted")]
    if record.get("arch") != "arm64" or record.get("rounds") != rounds or any(len(x) != rounds for x in lists):
        return False
    return not validate.failures(record)


def select_tasks(n: int, order_list, validations: dict, task_infos: dict) -> tuple:
    """The first n tasks of the order whose validation is ok, and the tasks passed over, with their reasons."""
    selected, replaced = [], []
    for task in order_list:
        if len(selected) == n:
            break
        record = validations.get(task)
        if record is None:
            raise FreezeError(f"{task} has no validation record; validate in order before the freeze")
        if record.get("ok") is not True:
            replaced.append({"task": task, "reason": record.get("reason")})
            continue
        if not validation_holds(record, task_infos[task]["rounds"]):
            raise FreezeError(f"{task}: its validation record says ok, but its rewards or arch break the rule")
        if record.get("code") != validate.CODE_DIGEST:
            raise FreezeError(f"{task}: validated by other copy code; rerun python3 -m bench.evo.validate --n 10")
        selected.append(task)
    if len(selected) < n:
        raise FreezeError(f"N = {n} needs {n} validated tasks; the order has {len(selected)}")
    return selected, replaced


def reserve_tasks(order_list, tasks, validations: dict, task_infos: dict) -> list:
    """The pre-registered replacements: every task after the Nth selected one whose validation record passes the
    checks the selected tasks pass (ok, the saved rewards, the copy code digest, one agent timeout for all rounds).
    A task without a record, or failing a check, is skipped, never an error."""
    order_list = list(order_list)
    reserve = []
    for task in order_list[order_list.index(tasks[-1]) + 1:]:
        record = validations.get(task)
        if (record is not None and record.get("ok") is True
                and validation_holds(record, task_infos[task]["rounds"])
                and record.get("code") == validate.CODE_DIGEST
                and task_infos[task].get("uniform_agent_timeout") is not False):
            reserve.append(task)
    return reserve


def check_order(order: dict) -> None:
    seeded = evo_order.seeded_order(config.POOL, config.SEED, config.DOMAINS)
    if order.get("seed") != config.SEED or order.get("order") != seeded:
        raise FreezeError("order.json is not the seeded order of the pool; write it with python3 -m bench.evo.order")
    arm_orders = order.get("arm_orders") or {}
    for task in seeded:
        if list(arm_orders.get(task) or []) != list(evo_order.arm_order(task, config.SEED)):
            raise FreezeError(f"order.json has the wrong arm order for {task}")


def check_inputs(pin: dict, bundle: dict, observed: dict, pilot: dict) -> None:
    if pin.get("revision") != config.DATASET_REVISION or len(pin.get("sha256") or "") != 64 \
            or pin.get("sha256") != pin.get("lfs_oid"):
        raise FreezeError(f"PIN.json does not pin revision {config.DATASET_REVISION} to its LFS oid: {pin}")
    if bundle.get("commit") != config.BUNDLE_COMMIT or bundle.get("modules") != list(config.BUNDLE_MODULES) \
            or "horizon/scripts/reviewing.py" not in (bundle.get("files") or {}):
        raise FreezeError(f"the bundle is not the v2 bundle at {config.BUNDLE_COMMIT[:7]}")
    if observed.get("harbor") != config.HARBOR_VERSION:
        raise FreezeError(f"harbor --version printed {observed.get('harbor')!r}, not {config.HARBOR_VERSION}")
    missing = [job for job in PILOT_JOBS if job not in pilot]
    if missing:
        raise FreezeError(f"Harbor check results missing: {missing}; run Task 7's checks first")
    failed = [f"{pilot[job]['job_name']}: {pilot[job]['failures']}" for job in PILOT_JOBS
              if job not in RECORDED_ONLY and pilot[job].get("ok") is not True]
    if failed:
        raise FreezeError(f"the latest attempt of a Harbor check failed: {failed}")


def prompt_file_digest(arm: str, minutes: int, *, write=copies.write_prompt) -> str:
    """sha256 of the prompt file copies.write_prompt makes, written in a scratch directory."""
    with tempfile.TemporaryDirectory() as tmp:
        data = Path(write(Path(tmp) / "prompt.md", arm, minutes)).read_bytes()
    if data.decode().strip() != config.prompt_text(arm, minutes):
        raise FreezeError(f"write_prompt({arm!r}, {minutes}) does not write the configured text")
    return sha256_bytes(data)


def prompt_facts(minutes_used) -> dict:
    keys = [(arm, m) for m in sorted(set(minutes_used)) for arm in config.ARMS]
    texts = {f"{arm}@{m}": config.prompt_text(arm, m) for arm, m in keys}
    return {"texts": texts,
            "text_sha256": {key: sha256_bytes(text.encode()) for key, text in texts.items()},
            "file_sha256": {f"{arm}@{m}": prompt_file_digest(arm, m) for arm, m in keys}}


def versions() -> dict:
    return {"harbor": config.HARBOR_VERSION, "claude_code": config.CLAUDE_CODE_VERSION, "model": config.MODEL,
            "effort": config.EFFORT, "max_output_tokens": config.MAX_OUTPUT_TOKENS,
            "agent_timeout_multiplier": config.AGENT_TIMEOUT_MULTIPLIER,
            "agent_setup_timeout_multiplier": config.AGENT_SETUP_TIMEOUT_MULTIPLIER,
            "disable_web_search": True, "resume_trajectory": False,
            "weekly_ceiling": config.WEEKLY_CEILING, "five_hour_ceiling": config.FIVE_HOUR_CEILING,
            "probe_model": config.PROBE_MODEL, "judge_model": config.JUDGE_MODEL}


def collect(n, order, validations, dry_timing, *, task_infos, bundle, pin, observed, code, pilot) -> dict:
    """The pre-registration: everything the main run and the analysis are held to."""
    check_order(order)
    missing = [t for t in order["order"] if t not in task_infos]
    if missing:
        raise FreezeError(f"the dataset has no task.toml for {missing}")
    if n != n_from_timing(dry_timing):
        raise FreezeError(f"N = {n}, but the N rule gives {n_from_timing(dry_timing)}")
    check_inputs(pin, bundle, observed, pilot)
    tasks, replaced = select_tasks(n, order["order"], validations, task_infos)
    mixed = [t for t in tasks if task_infos[t].get("uniform_agent_timeout") is False]
    if mixed:
        raise FreezeError(f"rounds of {mixed} have different agent timeouts, so one M per task is wrong: "
                          "stop and ask the user")
    reserve = reserve_tasks(order["order"], tasks, validations, task_infos)
    details = {}
    for task in tasks + reserve:
        info, record = task_infos[task], validations[task]
        details[task] = {"order": order["order"].index(task) + 1, "rounds": info["rounds"],
                         "minutes": dataset.minutes(info), "agent_timeout_sec": info["agent_timeout_sec"],
                         "verifier_timeout_sec": info["verifier_timeout_sec"],
                         "arm_order": list(order["arm_orders"][task]), "image_tag": config.image_tag(task),
                         "image_id": record["image_id"], "copy_digest": record["copy_digest"]}
    return {
        "study": "EvoCode-Bench first look: Horizon v2 against plain Claude Code",
        "spec": {"path": SPEC, "sha256": sha256_file(config.REPO_ROOT / SPEC)},
        "freeze_tag": config.FREEZE_TAG,
        "n": n,
        "n_rule": {"choices": list(config.N_CHOICES), "rounds_per_task_plan": config.ROUNDS_PER_TASK_PLAN,
                   "budget_agent_hours": config.BUDGET_AGENT_HOURS, "m_control": dry_timing["m_control"],
                   "m_horizon": dry_timing["m_horizon"], "dry_rounds": dry_timing.get("rounds"),
                   "dry_task": config.WALKTHROUGH, "n": n},
        "tasks": tasks,
        "replaced": replaced,
        "reserve": reserve,
        "task_details": details,
        "arms": list(config.ARMS),
        "arm_orders": {t: list(order["arm_orders"][t]) for t in tasks},
        "draw": {"seed": config.SEED, "domains": list(config.DOMAINS), "pool": list(config.POOL),
                 "excluded_prefix": config.EXCLUDED_PREFIX, "walkthrough": config.WALKTHROUGH,
                 "algorithm": order.get("algorithm"), "order": list(order["order"])},
        "prompts": prompt_facts(d["minutes"] for d in details.values()),
        "bundle": bundle,
        "versions": versions(),
        "observed": observed,
        "dataset": {"repo": config.DATASET_REPO, "revision": config.DATASET_REVISION,
                    "archive": config.ARCHIVE_PATH, "pin": pin},
        "evocode_code": {"url": config.EVO_CODE_URL, "commit": config.EVO_CODE_COMMIT},
        "judge": {"model": config.JUDGE_MODEL, "rubric": JUDGE_RUBRIC,
                  "sha256": sha256_file(config.REPO_ROOT / JUDGE_RUBRIC)},
        "analysis": ANALYSIS,
        "outcomes": OUTCOMES,
        "pilot": pilot,
        "code": code,
    }


def _table(header, rows) -> list:
    return ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |",
            *("| " + " | ".join(str(c) for c in row) + " |" for row in rows)]


def _task_table(p: dict, tasks) -> list:
    details = p["task_details"]
    return _table(["#", "Task", "Rounds", "Minutes", "Arm order", "Image ID", "Agent copy digest"],
                  [(details[t]["order"], f"`{t}`", details[t]["rounds"], details[t]["minutes"],
                    " then ".join(details[t]["arm_order"]), f"`{details[t]['image_id']}`",
                    f"`{details[t]['copy_digest']}`") for t in tasks])


def render(p: dict) -> str:
    """prereg.md: the human-readable form of prereg.json."""
    rule = p["n_rule"]
    lines = [
        "# EvoCode-Bench first look: pre-registration", "",
        f"Frozen at Git tag `{p['freeze_tag']}`. Design: [spec](../../superpowers/specs/{Path(p['spec']['path']).name}) "
        f"(sha256 `{p['spec']['sha256']}`). Every value here is also in [prereg.json](prereg.json). After the tag, "
        "every change goes to `deviations.md` with its date, its reason and the runs it affects.", "",
        "## Question", "",
        "On EvoCode-Bench's multi-round coding tasks, does Claude Code with Horizon v2 pass more rounds than plain "
        "Claude Code?", "",
        f"## Tasks (N = {p['n']})", "",
        f"The pool is {len(p['draw']['pool'])} tasks; the walkthrough pair `{p['draw']['excluded_prefix']}*` is "
        f"excluded. Seed {p['draw']['seed']} orders the pool by a round-robin over the domains "
        f"{', '.join(p['draw']['domains'])}. The measured tasks are the first N in that order that passed "
        "validation (arm64 build; oracle and planted-ledger oracle reward 1 on every round; nop reward below 1 on "
        "round 1 and on every later round it reached, since a step's min_reward may stop the nop chain early).", "",
        *_task_table(p, p["tasks"]),
        "",
    ]
    if p["replaced"]:
        lines += ["Passed over because they failed validation:", ""]
        lines += [f"- `{r['task']}`: {r['reason']}" for r in p["replaced"]]
    else:
        lines += ["None of the tasks before them failed validation."]
    reserve = p.get("reserve") or []
    lines += ["", "### Reserve", "",
              "Pre-registered replacements, used as the Running section says: the tasks after the Nth measured "
              "task in the order whose validation passes the same checks.", ""]
    lines += (_task_table(p, reserve) if reserve
              else ["No reserve: validation stopped at the Nth passing task, or no later task passed it."])
    lines += [
        "", "## N rule", "",
        f"The timing dry run on `{rule['dry_task']}` gave m_control = {rule['m_control']:.2f} and m_horizon = "
        f"{rule['m_horizon']:.2f} agent minutes per round. N is the largest of {rule['choices']} with "
        f"N x {rule['rounds_per_task_plan']} x (m_control + m_horizon) / 60 <= {rule['budget_agent_hours']} "
        f"agent-hours: N = {p['n']}.", "",
        "## Arms and prompts", "",
        f"Both arms: {p['versions']['model']} at effort {p['versions']['effort']}, Claude Code "
        f"{p['versions']['claude_code']}, Harbor {p['versions']['harbor']}, a fresh session every round, the same "
        "patched images and task copies. Control appends the shared paragraph to every round; Horizon also installs "
        f"the Horizon v2 bundle and appends the Horizon sentence. M is 30, or 60 for 3,600-s tasks.", "",
    ]
    for key, text in p["prompts"]["texts"].items():
        lines += [f"**{key}** (text sha256 `{p['prompts']['text_sha256'][key]}`, file sha256 "
                  f"`{p['prompts']['file_sha256'][key]}`)", ""]
        lines += [f"> {line}" if line else ">" for line in text.split("\n")] + [""]
    lines += ["## Analysis", "", OUTCOMES["primary"], "", f"Test: {p['analysis']}.", "",
              "## Running", "", OUTCOMES["running"], "",
              "## Secondary outcomes", ""] + [f"- {x}" for x in p["outcomes"]["secondary"]]
    lines += ["", "## Validity checks", ""] + [f"- {x}" for x in p["outcomes"]["validity"]]
    lines += ["", "## Settings and versions", "",
              *_table(["Setting", "Value"], [(k, f"`{v}`") for k, v in p["versions"].items()]), "",
              *_table(["Observed on the host", "Value"], [(k, f"`{v}`") for k, v in p["observed"].items()]), "",
              "## Horizon bundle", "",
              f"Commit `{p['bundle']['commit']}`, modules {', '.join(p['bundle']['modules'])}.", "",
              *_table(["File", "SHA-256"], [(f"`{f}`", f"`{d}`") for f, d in sorted(p["bundle"]["files"].items())]),
              "", "## Dataset", "",
              f"`{p['dataset']['repo']}` at revision `{p['dataset']['revision']}`, archive "
              f"`{p['dataset']['archive']}`, sha256 `{p['dataset']['pin']['sha256']}` (equal to its LFS oid). "
              f"EvoCode code reference: {p['evocode_code']['url']} at `{p['evocode_code']['commit']}`.", "",
              "## Judge", "",
              f"`{p['judge']['model']}` with the TB4 rubric `{p['judge']['rubric']}` (sha256 `{p['judge']['sha256']}`), "
              "blind to task and arm.", "",
              "## Pilot checks", ""]
    verdict = {True: "passed", False: "failed", None: "recorded only"}
    lines += ["The latest attempt of each Harbor behaviour check (Task 7), with the digest of its findings.json.", "",
              *_table(["Job", "Result", "Failures", "findings.json sha256"],
                      [(f"`{c['job_name']}`", verdict[c["ok"]], len(c["failures"]), f"`{c['sha256']}`")
                       for _, c in sorted(p["pilot"].items())]), ""]
    for _, check in sorted(p["pilot"].items()):
        if "recorded" in check:
            lines += [f"Recorded by `{check['job_name']}`:", "", "```json",
                      json.dumps(check["recorded"], indent=2, sort_keys=True), "```", ""]
    lines += ["## Code", "",
              f"{len(p['code'])} tracked files under `bench/evo` and `bench/tb4`; their sha256 digests are in "
              "prereg.json. `python3 -m bench.evo.prereg check` refuses main-phase runs once they change."]
    return "\n".join(lines) + "\n"


def write(prereg: dict, docs_dir=config.DOCS) -> tuple:
    """Write prereg.json and prereg.md; return their paths."""
    docs_dir = Path(docs_dir)
    docs_dir.mkdir(parents=True, exist_ok=True)
    json_path, md_path = docs_dir / "prereg.json", docs_dir / "prereg.md"
    _write_text(json_path, json.dumps(prereg, indent=2, sort_keys=True) + "\n")
    _write_text(md_path, render(prereg))
    return json_path, md_path


def observed_versions(run=subprocess.run) -> dict:
    def out(*cmd) -> str:
        try:
            return run(list(cmd), capture_output=True, text=True).stdout.strip()
        except OSError as error:
            return f"unavailable: {error}"

    return {"harbor": out("harbor", "--version"), "claude (host)": out("claude", "--version"),
            "docker server": out("docker", "version", "--format", "{{.Server.Version}}"),
            "docker arch": out("docker", "info", "--format", "{{.Architecture}}"),
            "python (host)": sys.version.split()[0]}


def code_digests(repo=config.REPO_ROOT, run=subprocess.run) -> dict:
    """sha256 of every tracked file under bench/evo and bench/tb4; refuses uncommitted or untracked changes."""
    def git(*args) -> str:
        return run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True).stdout

    dirty = git("status", "--porcelain", "--untracked-files=all", "--", *CODE_PATHS).strip()
    if dirty:
        raise FreezeError(f"commit the code before the freeze:\n{dirty}")
    names = [name for name in git("ls-files", "-z", "--", *CODE_PATHS).split("\0") if name]
    return {name: sha256_file(Path(repo) / name) for name in sorted(names)}


def read_pilot(checks_dir: Path) -> dict:
    """The latest attempt of every Harbor check job: verdict, failures, probe record and findings digest."""
    latest = {}
    for path in sorted(Path(checks_dir).glob("*/findings.json")):
        data = json.loads(path.read_text())
        match = _ATTEMPT.match(str(data.get("job_name") or ""))
        if not match:
            continue
        job, attempt = match["job"], int(match["attempt"])
        if job in latest and latest[job]["attempt"] > attempt:
            continue
        latest[job] = {"job_name": data["job_name"], "attempt": attempt, "ok": data.get("ok"),
                       "failures": data.get("failures") or [], "sha256": sha256_file(path)}
        if "recorded" in data:
            latest[job]["recorded"] = data["recorded"]
    return latest


def collect_from_runs(runs=config.RUNS) -> dict:
    """Read every input from the run directory, then collect."""
    runs = Path(runs)
    base = runs / "dataset" / config.DATASET_REVISION
    order = _read_json(runs / "order.json", "write the order first")
    timing = _read_json(runs / "timing.json", "save the dry run's analysis timing output there first")
    pin = _read_json(base / "PIN.json", "fetch the dataset first")
    task_infos = {info["task"]: info for info in dataset.list_tasks(base / "tasks")}
    validations = {}
    for task in order.get("order") or []:
        path = runs / "validation" / f"{task}.json"
        if path.is_file():
            validations[task] = json.loads(path.read_text())
    return collect(n_from_timing(timing), order, validations, timing, task_infos=task_infos,
                   bundle=build_bundle(runs), pin=pin, observed=observed_versions(), code=code_digests(),
                   pilot=read_pilot(runs / "checks"))


def frozen_commit(run=subprocess.run, repo=config.REPO_ROOT):
    """The commit the evo-prereg tag points at, or None."""
    proc = run(["git", "-C", str(repo), "rev-parse", "-q", "--verify", f"refs/tags/{config.FREEZE_TAG}^{{commit}}"],
               capture_output=True, text=True)
    commit = (proc.stdout or "").strip()
    return commit if proc.returncode == 0 and commit else None


def require_freeze(run=subprocess.run, *, repo=config.REPO_ROOT) -> str:
    """Return the freeze commit; HEAD may move past it only with the frozen paths unchanged."""
    def git(*args):
        return run(["git", "-C", str(repo), *args], capture_output=True, text=True)

    tag = frozen_commit(run, repo)
    if tag is None:
        raise NotFrozen(f"tag {config.FREEZE_TAG} is missing")
    if git("cat-file", "-e", f"{tag}:{PREREG_JSON}").returncode != 0:
        raise NotFrozen(f"{config.FREEZE_TAG} has no {PREREG_JSON}")
    if git("merge-base", "--is-ancestor", tag, "HEAD").returncode != 0:
        raise NotFrozen(f"HEAD does not contain {config.FREEZE_TAG}")
    if git("diff", "--quiet", tag, "HEAD", "--", *FROZEN_PATHS).returncode != 0:
        raise NotFrozen(f"frozen paths changed since {config.FREEZE_TAG}: {', '.join(FROZEN_PATHS)}")
    dirty = (git("status", "--porcelain", "--untracked-files=all", "--", *FROZEN_PATHS).stdout or "").strip()
    if dirty:
        raise NotFrozen(f"uncommitted changes in frozen paths:\n{dirty}")
    if git("ls-files", "--error-unmatch", "--", REPORT).returncode == 0:
        raise NotFrozen(f"{REPORT} is committed: the main run is closed")
    return tag


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("bundle", "write", "check"))
    parser.add_argument("--runs", type=Path, default=config.RUNS)
    parser.add_argument("--docs", type=Path, default=config.DOCS)
    args = parser.parse_args(argv)
    try:
        if args.action == "check":
            print(json.dumps({"frozen": require_freeze()}))
        elif args.action == "bundle":
            manifest = build_bundle(args.runs)
            print(json.dumps({"commit": manifest["commit"], "files": len(manifest["files"]),
                              "skill": str(args.runs / "bundle" / "horizon")}))
        else:
            tag = frozen_commit()
            if tag:
                raise FreezeError(f"already frozen at {tag}; record any change in deviations.md")
            prereg = collect_from_runs(args.runs)
            json_path, md_path = write(prereg, args.docs)
            print(json.dumps({"n": prereg["n"], "tasks": prereg["tasks"], "replaced": prereg["replaced"],
                              "reserve": prereg["reserve"], "json": str(json_path), "md": str(md_path)}))
    except (FreezeError, NotFrozen) as error:
        print(f"{type(error).__name__}: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Run the tests to make sure they pass**

Run: `sh bench/tb4/test.sh -q tests/test_evo_prereg.py`
Expected: `46 passed, 7 skipped` (the time varies). The 7 skips are the freeze pins. Check their reason:

Run: `sh bench/tb4/test.sh -q -rs tests/test_evo_prereg.py | grep -c 'before the freeze: no prereg.json and no evo-prereg tag'`
Expected: `7`.

- [ ] **Step 5: Prove that the freeze pins and the freeze check discriminate.** This runs in a scratch clone of the committed Tasks 1-10 plus this task's two new files. It uses `$WT` from Global Constraints. Nothing touches the worktree, `$RUNS`, or the real repository's tags. `observed_versions` runs `harbor --version`, so Harbor 0.24.0 must be installed.

```bash
T="$(mktemp -d)"
git clone -q "$WT" "$T/repo"
cp "$WT/bench/evo/prereg.py" "$T/repo/bench/evo/prereg.py"
cp "$WT/tests/test_evo_prereg.py" "$T/repo/tests/test_evo_prereg.py"
cd "$T/repo" && git add -A bench tests && git -c user.name=t -c user.email=t@example.invalid commit -qm "prereg under test"
python3 - "$T/runs" <<'EOF'
import sys
from pathlib import Path
from bench.evo import config, order, prereg, validate
runs = Path(sys.argv[1])
seeded = order.seeded_order(config.POOL, config.SEED, config.DOMAINS)
payload = {"seed": config.SEED, "algorithm": "scratch", "order": seeded,
           "arm_orders": {t: list(order.arm_order(t, config.SEED)) for t in seeded}}
infos = {t: {"task": t, "dir": "/scratch", "rounds": 10, "step_names": [], "agent_timeout_sec": 1800.0,
             "verifier_timeout_sec": 900.0, "reward_strategy": "mean", "uniform_agent_timeout": True} for t in seeded}
validations = {t: {"task": t, "copy_digest": "c" * 64, "image_id": "sha256:" + "d" * 64, "arch": "arm64",
                   "rounds": 10, "oracle": [1.0] * 10, "nop": [0.0] * 10, "planted": [1.0] * 10, "ok": True,
                   "reason": None, "code": validate.CODE_DIGEST} for t in seeded}
pin = {"revision": config.DATASET_REVISION, "sha256": "a" * 64, "lfs_oid": "a" * 64, "url": "u", "fetched_at": "t"}
pilot = {job: {"job_name": f"{job}__a1", "attempt": 1, "ok": None if job in prereg.RECORDED_ONLY else True,
               "failures": [], "sha256": "e" * 64} for job in prereg.PILOT_JOBS}
record = prereg.collect(8, payload, validations, {"m_control": 20.0, "m_horizon": 25.0}, task_infos=infos,
                        bundle=prereg.build_bundle(runs), pin=pin, observed=prereg.observed_versions(),
                        code=prereg.code_digests(), pilot=pilot)
print([p.name for p in prereg.write(record, config.DOCS)], record["reserve"] == seeded[8:])
EOF
sh bench/tb4/test.sh -q tests/test_evo_prereg.py | tail -1
git add docs && git -c user.name=t -c user.email=t@example.invalid commit -qm "scratch freeze"
git -c user.name=t -c user.email=t@example.invalid tag -a evo-prereg -m "scratch freeze"
sh bench/tb4/test.sh -q tests/test_evo_prereg.py | tail -1
python3 -m bench.evo.prereg check
python3 -c 'import json, pathlib; p = pathlib.Path("docs/evaluations/evocode-horizon/prereg.json"); d = json.loads(p.read_text()); d["n"] = 10; p.write_text(json.dumps(d))'
sh bench/tb4/test.sh -q tests/test_evo_prereg.py | tail -1
python3 -m bench.evo.prereg check; echo "exit $?"
cd / && rm -rf "$T"
```

Expected, in order:
1. `['prereg.json', 'prereg.md'] True`: both files are written, and the reserve is the 5 pool tasks after the 8th.
2. `53 passed`, then the time: the pins run against the untagged file.
3. `53 passed`, then the time: the pins read the tag.
4. `{"frozen": "`, the 40-character SHA of the scratch freeze commit, then `"}`.
5. `1 failed, 52 passed`, then the time: `test_pinned_record_is_the_committed_one` catches the edited record.
6. `NotFrozen: uncommitted changes in frozen paths:`, then `M docs/evaluations/evocode-horizon/prereg.json` and `exit 1`.

If line 1 is instead a `FreezeError` traceback naming `harbor --version`, install Harbor 0.24.0 and repeat. If any other line differs, the pins do not discriminate: fix `prereg.py` or the test before committing.

- [ ] **Step 6: Write the results schema.** Create `docs/evaluations/evocode-horizon/results-schema.md`. Each table follows the code that writes the file: `run.new_record` and `run._finish`, `score.ROW_FIELDS` and `score.step_values`, `validate.validate_task`, and `prereg.collect`.

```markdown
# EvoCode-Bench first look: results schema

Run data stays in `~/Documents/evocode-horizon-runs` (`RUNS`). The repository holds only `prereg.json`, `prereg.md`, `report.md`, `deviations.md` and this file. `null` means a value does not exist for that run or round; it is never estimated. Final messages, transcripts, verifier output and per-case results are never committed.

## Run record: `RUNS/records/<job_name>.json`

One per Harbor job attempt of the `dry` and `main` phases, written by `python3 -m bench.evo.run`. The job name is `{phase}__{task}__{arm}__a{attempt}`. Task 7's checks write `RUNS/checks/<job_name>/findings.json` instead.

| Field | Type | Meaning |
| --- | --- | --- |
| `job_name` | str | The job name above |
| `phase` | str | `dry` or `main` |
| `task`, `arm`, `attempt` | str, str, int | Identity; `arm` is `control` or `horizon` |
| `started`, `finished` | number or null | Epoch seconds just before and just after `harbor run`; `null` for a job an earlier runner left unfinished |
| `exit_code` | int or null | Exit status of `harbor run` |
| `job_dir` | str | The Harbor job directory, `RUNS/jobs/<job_name>` |
| `trial_dir` | str or null | The job's trial directory |
| `valid` | bool | The job counts toward the analysis |
| `invalid_reason` | str or null | `null`, `usage_limit`, `api_error`, `infrastructure`, `host_slept` or `interrupted` |
| `image_id` | str or null | ID of the validated task image the job was checked against |
| `container_image` | str or null | Image of the kept main container; one other than `image_id` makes the job `infrastructure` |
| `freeze` | str | The `evo-prereg` commit for `main`; `pre-freeze` for `dry` |
| `harbor_version`, `claude_code_version` | str or null | Versions the job ran with, from Harbor's lock file and trial result |
| `command` | list | The `harbor run` argv; the token appears only as `${CLAUDE_CODE_OAUTH_TOKEN}` |
| `usage_before`, `usage_after` | object or null | Rate-limit info (`status`, `five_hour`, `five_hour_resets_at`, `seven_day`, `seven_day_resets_at`, `limit_type`, `limit_utilization`, `limit_resets_at`): from the usage probe before the job, and from the latest round stream that has one |
| `ledger` | object or null | `{candidates, copied}`: the ledger paths the streams named, and where each one copied from the stopped main container landed under `RUNS/ledgers/<job_name>` |

## Round row: `RUNS/rows.jsonl`

One JSON object per round of every valid job, in task order, written by `python3 -m bench.evo.score --phase dry` or `--phase main`. A round the trial never reached keeps the defaults: `reward` 0.0, `passed` false, `end_reason` `not reached`. `label` is filled by `python3 -m bench.evo.analysis label`.

| Field | Type | Meaning |
| --- | --- | --- |
| `job_name`, `phase`, `task`, `arm`, `attempt` | str, str, str, str, int | Identity, as in the run record |
| `round`, `round_index` | str, int | Step name and its position, from 1 |
| `reward` | number | The step's verifier reward (`reward.txt` or `reward.json`); 0.0 when it has none or was not reached |
| `passed` | bool | `reward >= 1.0`; a missing, aborted or timed-out round is `false` |
| `cases_passed`, `cases_total` | int or null | The two counts on the verifier's CASE_SUMMARY line, nothing else |
| `agent_sec` | number or null | Agent phase duration of the round |
| `tokens_in`, `tokens_out` | int or null | Input tokens (cache reads and writes included) and output tokens from the round's last result record; `null` without one or after a timeout |
| `cost_usd` | number or null | `total_cost_usd` from that record |
| `result_records` | int | Result records in the round's stream; more than 1 is flagged |
| `horizon_calls` | int | Horizon CLI invocations in the round |
| `horizon_started` | bool | The round created a Horizon goal, or a Horizon `start` in it returned one |
| `goal_slugs`, `goal_status` | list, object | Goals the round created or changed, and each one's status at the round's end |
| `end_reason` | str | `complete`, `abandoned`, `active`, `no goal`, `unknown` or `not reached` |
| `final_message` | str or null | The round's last result message, `null` after a timeout; read by the judge, never committed |
| `label` | str or null | Judge label: `claims_complete`, `reports_incomplete` or `neither` |
| `audit_flags` | list | `verifier-files` when a tool input named `/tests`, `/solution` or `/logs/verifier`; `benchmark-source` when one named EvoCodeBench or UniPat-AI |
| `leftovers` | object or null | Counts from the collect hook's audit: `killed` processes, `survivors` still running and `tmp_entries` left in `/tmp` (the last two `null` when their file is missing); `null` when no audit was collected |

## Validation record: `RUNS/validation/<task>.json`

One per validated task, written by `python3 -m bench.evo.validate`. A saved record whose `code` differs from the current code is validated again.

| Field | Type | Meaning |
| --- | --- | --- |
| `task` | str | Task directory name |
| `copy_digest`, `validation_copy_digest` | str or null | Tree digests of the agent copy and the validation copy |
| `arm_patch` | object | `{changed, artifacts}` from the copy's `arm-patch.json`: the Dockerfile lines the arm64 patch rewrote, and the bundled Go archives it swapped for go.dev's arm64 ones; absent when the copy failed |
| `image_tag` | str | The local image tag, `evo-local/<task>:<first 12 characters of the dataset revision>` |
| `image_id`, `arch` | str or null | The built image's ID and architecture (`arm64` to pass) |
| `workdir` | str or null | Where Harbor runs the agent, the hooks and the oracle: `[environment].workdir`, else the image WORKDIR |
| `user` | list or null | The sorted distinct users each step's `setup.sh` and agent run as: `[steps.agent].user`, else `[agent].user`, else the image USER; `""`, `"0"`, `root` and a user:group whose user part is one of those (for example `0:0`) are shown as `root`; `null` until the image is inspected |
| `python` | object or null | `{ok, version}`: whether the image has python3 >= 3.10, which the Horizon CLI needs |
| `rounds` | int | Number of steps in `task.toml` |
| `oracle`, `nop`, `planted` | list | Per-round rewards (number or null) of the oracle, nop and planted-ledger oracle runs |
| `oracle_cases` | list | `[passed, total]` from the CASE_SUMMARY line, or null, per oracle round |
| `ok`, `reason` | bool, str or null | `ok` only when every oracle and planted reward is 1, round 1's nop reward is graded and below 1, every other graded nop reward is below 1 (a step's min_reward may stop the nop chain early), `arch` is `arm64` and the image ID did not change during the checks; `reason` names what failed |
| `code` | str | `validate.CODE_DIGEST`: sha256 of `copies.py`, `images.py`, `patch.py`, `steps.py` and `validate.py`, the code that made the copies and judged them |

## Pre-registration: `prereg.json`

Written by `python3 -m bench.evo.prereg write`, committed and tagged `evo-prereg`. Top-level keys:

| Key | Meaning |
| --- | --- |
| `study`, `spec`, `freeze_tag` | Title, the design spec's path and sha256, and the tag name |
| `n`, `n_rule` | N, and the rule's inputs: the choices, the planned rounds per task, the budget, the dry run's minutes per round per arm, its rounds and its task |
| `tasks`, `replaced` | The measured tasks in order; the tasks before them passed over for failing validation, with the reason |
| `reserve` | The pre-registered replacements: the tasks after the Nth measured one whose validation passes the same checks, in order. A main task dropped for an infrastructure failure is replaced by the next unused one; with none left a drop stays a drop |
| `task_details` | For each measured and reserve task: its position in the order, rounds, minutes, agent and verifier timeouts, arm order, image tag, image ID and agent copy digest |
| `arms`, `arm_orders`, `draw` | Arm names, each measured task's seeded arm order, and the seeded draw (seed, domains, pool, excluded prefix, walkthrough, algorithm, order) |
| `prompts` | Each prompt text a measured or reserve task uses, keyed by arm and minutes (for example `horizon@30`), with the sha256 of the text and of the file `copies.write_prompt` writes |
| `bundle` | The Horizon bundle commit, modules and per-file sha256 |
| `versions`, `observed` | Configured settings, and the versions found on the host at the freeze |
| `dataset`, `evocode_code` | The pinned dataset revision, archive and `PIN.json`; the EvoCode code reference |
| `judge` | Judge model and the TB4 rubric's path and sha256 |
| `analysis`, `outcomes` | The pre-registered test and the primary, secondary, validity and running rules |
| `pilot` | The latest attempt of each Harbor behaviour check: job name, attempt, verdict, failures, findings digest, and the probe's recorded facts |
| `code` | sha256 of every tracked file under `bench/evo` and `bench/tb4` at the freeze |
```

- [ ] **Step 7: Run the full suite**

Run: `sh bench/tb4/test.sh -q`
Expected: `1123 passed, 7 skipped` (1077 after Task 10 + 46; the time varies). The 7 skips are the freeze pins, and no TB4 test changes. If other work has landed first, expect the previous count plus this task's tests, with 0 failures.

- [ ] **Step 8: Commit**

```bash
cd "$WT" && git add bench/evo/prereg.py tests/test_evo_prereg.py docs/evaluations/evocode-horizon/results-schema.md
git commit -m "feat(evo): pre-registration freeze, reserves, bundle verification and freeze pins"
```

Run: `git show --stat --format=%s HEAD | head -20`
Expected:

```text
feat(evo): pre-registration freeze, reserves, bundle verification and freeze pins

 bench/evo/prereg.py                                | 516 +++++++++++++++++++++
 docs/evaluations/evocode-horizon/results-schema.md |  91 ++++
 tests/test_evo_prereg.py                           | 504 ++++++++++++++++++++
 3 files changed, 1111 insertions(+)
```

### Task 12: Runbook (operator; no new code)

**Deviation:** seven changes to the skeleton's runbook.

1. **The bundle is built in Step 5, right after the order, instead of at the freeze.** The dry run's horizon arm needs `RUNS/bundle/horizon` (`--skill`), and the canary uses the same bundle. At the freeze, `prereg write` verifies the bundle byte for byte against a fresh build at `BUNDLE_COMMIT`.
2. **Two steps are added at the start:** preconditions (Step 1) and a token and usage probe (Step 2).
3. **Step 9 reads more than `ok`.** For every validated task it prints whether python3 3.10 or later runs in the image, how many rounds lack case counts, how many bundled Go tarballs were replaced, whether the copy ends with the python3 block (Task 12a) and which user each round runs as. It STOPs on any of them, and on an unknown amd64 pattern, instead of letting the walk swap the task.
4. **Step 12 also measures the goal start offset and the usage per round**, as the spec's dry run requires. It prints offsets and utilizations only, never goal text.
5. **A dropped main task is replaced by a reserve (Step 14).** `prereg.json` pre-registers the reserves (spec rule 7), and on relaunch the runner runs the next unused one. The skeleton could only drop.
6. **A post-run integrity check is added (Step 16).** It checks every planned task against `prereg.json`, including any reserve in play: prompt-file digests, agent-copy digests, image IDs, the freeze commit of every main record, and the versions of every main record that ran a trial.
7. **A contamination check runs before both commits (Steps 13 and 19).** It prints counts and line numbers only, never task content. A host-path check runs with it.

**Files:**
- Created by running the steps, and committed:
  - `docs/evaluations/evocode-horizon/prereg.json` and `prereg.md` (Step 13);
  - `docs/evaluations/evocode-horizon/report.md` and `deviations.md` (Steps 18-19).
- Run data, never committed:
  - everything under `$RUNS`, including `$RUNS/bin/contamination-check.py` (Step 1) and `$RUNS/timing.json` (Step 12);
  - `$RUNS/findings.md`, the operator's STOP notes before the freeze, and the runner's dry-phase drop lines;
  - `$RUNS/deviations.md`, after the freeze, with the runner's main-phase drop lines.

**Interfaces:**
- Consumes these commands and names, all run from `$WT`:
  - Task 2: `python3 -m bench.evo.dataset fetch`, and `python3 -m bench.evo.dataset list`, which prints `tasks`, `rounds`, `reward_strategies`, `pool_missing`, `pool_rounds`, `walkthrough` and `nonuniform_agent_timeouts`.
  - Task 3: `python3 -m bench.evo.order`, which prints the `order.json` payload. Its `arm_orders` come from `order.arm_order(task, seed)`:
    - positions 2k and 2k+1 of the seeded order form pair k, shuffled with `random.Random(f"{seed}:arms:pair{k}")`;
    - position 2k+1 runs the reverse order;
    - the walkthrough keeps its own coin, `('horizon', 'control')`;
    - `ALGORITHM` states both rules.
  - Task 6:
    - `python3 -m bench.evo.validate --n N`, which prints `{"selected", "swaps", "complete", "python_missing"}`, and `python3 -m bench.evo.validate --task TASK`;
    - each `validation/<task>.json` carries `python`, `oracle_cases`, `arm_patch` (`{changed, artifacts, added}`; `added` holds `copies.PYTHON_LINE`, see Step 9), `user` (from `validate.agent_users(task_toml, image_user)`) and `code` (`validate.CODE_DIGEST`). A saved record whose `code` differs is validated again;
    - a task whose image holds `setup.sh` in its working directory fails validation with the reason `project file setup.sh in the working directory would be overwritten by the round hook`, before any Harbor job;
    - `validate_task` and `--task` raise `ValueError` for a task name that is empty or absolute, or that holds `/`, `\` or `..`, before anything is deleted;
    - `steps.read_trial` (Step 12).
  - Task 7: `python3 -m bench.evo.harborcheck check [--agent oracle]`, `vtimeout`, `probe` and `canary --skill DIR`. The findings land in `$RUNS/checks/<job>/findings.json`. The dummy's own Dockerfile is `FROM ubuntu:22.04` with no apt step. Its copies, like every copy since Task 12a, end with the python3 block, so a check run again after Task 12a installs python3 with apt while it builds the dummy image.
  - Task 8: `python3 -m bench.evo.run --phase dry|main [--print-commands]`. It exits 3 with `stopped: ...` when a task is dropped. In both phases:
    - a drop line goes to `$RUNS/deviations.md` for `main` and to `$RUNS/findings.md` for `dry`. Only a main drop is skipped on a relaunch, so a dry relaunch with the next `--attempt` runs the walkthrough again;
    - when the usage probe that gates a job returns no `five_hour` or `seven_day` utilization, it raises `InfrastructureFailure: usage probe returned no rate-limit data`. No job starts, no record is written and no attempt is spent;
    - a Harbor model-outcome type (`run.MODEL_OUTCOMES`, for example `AgentSafetyRefusalError` or `ContextWindowExceededError`) keeps a run valid even when the stream ends on the API error line Harbor read it from. Any Harbor type it does not know is `infrastructure`.

    For `main`:
    - it runs `run.main_tasks(prereg, dropped)`, which is `prereg["tasks"]` plus `prereg["reserve"][:len(set(dropped))]`, with `dropped = run.dropped_tasks(run.Paths(RUNS), "main")`;
    - before reading the token, it raises `ImageChanged("<task>: validated image differs from the pre-registered one")` when any of those tasks has a validated image that differs from `prereg.json`'s `task_details`;
    - it then calls `prereg.verify_runs` (Task 11).
  - Task 9: `python3 -m bench.evo.score --phase dry|main`, and `score.ledger_goals` (Step 12), which returns `None` for a round whose ledger copy was not collected. Each row has the 26 keys of `score.ROW_FIELDS`; the last, `exception`, is the round's Harbor exception type or `null`. A valid record whose `<trial_dir>/result.json` cannot be read raises `ValueError` naming the job.
  - Task 10: `python3 -m bench.evo.analysis timing|label|report`. `report` plans the same `main_tasks` list and shows a dropped task as `dropped`. It calls `analysis.summarize(rows, records, tasks, rounds_by_task, dropped, preregistered=len(prereg["tasks"]))`, so the report states the analysed count, the pre-registered N and the reserves used. It exits 2 when a main record's `freeze` differs from the commit `require_freeze` returned. It renders `deviations.md` with the `--runs` path, the worktree and the home directory written as `$RUNS`, `$WT` and `~`.
  - Task 11: `python3 -m bench.evo.prereg bundle|write|check`:
    - `write` prints `{"n", "tasks", "replaced", "reserve", "json", "md"}`;
    - `write` refuses (`FreezeError`) a Harbor check whose latest attempt is not `ok`, and a timeout probe whose latest attempt has `ok` false or any `failures`;
    - `prereg.json` holds `reserve` (from `prereg.reserve_tasks`) and `task_details` for every measured task and every reserve;
    - `OUTCOMES["running"]` states the reserve rule;
    - `prereg.md` has a `### Reserve` subsection;
    - `prereg.json` holds `decisions`, the decisions made before the freeze (`prereg.DECISIONS`, Task 12a), and `prereg.md` lists them under `## Decisions made before the freeze`, between `### Reserve` and `## N rule`;
    - `prereg.verify_runs(prereg, runs, tasks)` raises `NotFrozen` when the bundle, an existing prompt file or an agent copy differs from `prereg.json`, or an agent copy is missing;
    - `prereg.require_frozen_record(path, run)` raises `NotFrozen` unless the file holds exactly the tagged `prereg.json`. `run --phase main` and `analysis report` call it, and `score --phase main`, `analysis label` and `analysis report` call `require_freeze`.
  - TB4, unchanged:
    - `bench.tb4.run.read_token` and `probe_usage`;
    - `bench.tb4.copies.tree_digest`;
    - `bench.tb4.stream.read_events` and `rate_limit_info`;
    - `bench.tb4.score.parse_time`;
    - `python3 -m bench.tb4.leakcheck`;
    - `sh bench/tb4/test.sh`.
- Produces: the freeze commit, the annotated tag `evo-prereg` and the report commit. Nothing is pushed.

**Rules for every step:**
- Every command runs from `$WT`, the worktree path from Global Constraints, with the real `HOME`. Each shell starts fresh, so every command block below begins with this line:

  ```bash
  WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
  ```

  A one-line `Run:` command that starts with `cd "$WT"` needs only `WT`, set as in Global Constraints. The path is quoted because `fun ` ends in a space.
- Long steps run detached under `nohup caffeinate -dimsu`, so they survive the session ending and the Mac idling.
- Never use the horizon or refine skills to run or monitor these steps, and never use `/loop` wrapped around them. Otherwise Horizon goals land in the repository's own `.claude/harness`. Monitor only with the shell commands given.
- Never start a second runner. `validate`, `harborcheck` and `run` all hold `$RUNS/runner.lock`.
- Task 7's operator steps are reference only. Steps 6-8 here run them, after the bundle exists (Step 5).
- **Contamination.** Never open, print, copy, quote or summarize any of these:
  - anything under `$RUNS/dataset/$REV/tasks/*/` except what the CLIs print;
  - any `tests/` or `solution/` file, or any `instruction.md`;
  - verifier output (`verifier/test-stdout.txt`), or per-case results under `$RUNS/jobs` or `$RUNS/checks`.

  There are four exceptions:
  - While debugging the dry run, you may read up to the first 200 characters of one of the walkthrough task's round instructions.
  - An agent transcript (`agent/claude-code.txt`) may be read to diagnose an invalid run, but never quoted.
  - Step 9 may count `CASE_SUMMARY` lines with `grep -c`, which prints one count per file and nothing else.
  - Step 9 may scan each selected task's `environment/Dockerfile` for language toolchains, printing and recording only the toolchain names, never file contents.
- **Before every commit**, two checks must pass:
  - `python3 -m bench.tb4.leakcheck` prints `no token in staged files`;
  - the contamination check reports zero hits.
- **STOP** means three things:
  1. Append a dated line to `$RUNS/findings.md` before the freeze, or to `$RUNS/deviations.md` after it. `analysis report` copies `deviations.md` into `report.md`, and Step 19 commits it, so every line must be fit to publish:
     - give the step, the exception type and a paraphrase of the message, never the command line or the raw message;
     - write `$RUNS`, `$WT` and `~` in place of the run directory, the worktree and the home directory, never an absolute path;
     - never name a file under `tests/` or `solution/`, never quote an instruction, and never give per-case results.

     Start each line with `- YYYY-MM-DD:`. The runner writes its own drop lines, `- dry <task>: dropped ...` to `$RUNS/findings.md` and `- main <task>: dropped ...` to `$RUNS/deviations.md`. It reads lines that start `- main <task>: dropped` as drops, so a hand-written line must never look like one.
  2. Ask the user.
  3. Do not continue until they answer.
- **The user's only manual step** is refreshing the token. It is needed when a step prints any of these:
  - `no token in Keychain service tb4-horizon-claude-token`;
  - a `ProbeError`, judge error or run failure mentioning authentication, `401`, `OAuth token has expired` or `invalid x-api-key`.

  Then ask the user to run, in their own terminal:
  1. `claude setup-token`. It prints a long-lived token.
  2. `security add-generic-password -U -a "$USER" -s tb4-horizon-claude-token -w`. It prompts for the token twice, so the token never enters the shell history.

  Then repeat the step that stopped. Nothing else needs the user unless a step says **STOP**.

- [ ] **Step 1: Preconditions** (no usage)

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
mkdir -p "$RUNS/logs" "$RUNS/bin"
cd "$WT" && git branch --show-current && git status --short && git log --oneline -1
docker info --format '{{.NCPU}} {{.MemTotal}} {{.Architecture}}'
harbor --version
COLUMNS=200 harbor run --help | grep -oE -- '--(extra-instruction-path|skill|agent-timeout-multiplier|agent-setup-timeout-multiplier|no-force-build|job-name)\b' | sort -u | wc -l
harbor agent schema claude-code --json | python3 -c 'import json, sys; d = json.load(sys.stdin); p = d.get("properties", d); print(sorted(k for k in p if k in ("disable_web_search", "reasoning_effort", "version")))'
claude --version
zstd --version
python3 -c 'import sys, tomllib; print(sys.version.split()[0])'
security find-generic-password -s tb4-horizon-claude-token -w | wc -c
docker system df
sh bench/tb4/test.sh -q | tail -1
cat > "$RUNS/bin/contamination-check.py" <<'EOF'
"""Count lines of EvoCode task material (instructions, tests, solutions) that appear in our docs; print counts only."""
import json
import sys
from pathlib import Path

tasks, docs = Path(sys.argv[1]), Path(sys.argv[2])
material = set()
for path in tasks.rglob("*"):
    parts = set(path.relative_to(tasks).parts)
    if path.is_file() and path.stat().st_size < 2_000_000 and (parts & {"tests", "solution"} or path.name == "instruction.md"):
        for line in path.read_text(errors="ignore").splitlines():
            if 40 <= len(line.strip()) <= 400:
                material.add(line.strip())
hits = {}
for doc in sorted(p for p in docs.iterdir() if p.is_file()):
    lines = doc.read_text(errors="ignore").splitlines()
    hits[doc.name] = [i for i, line in enumerate(lines, 1) if len(line) >= 40 and any(m in line for m in material)]
print(json.dumps({"material_lines": len(material), "hits": {name: len(found) for name, found in hits.items()},
                  "hit_lines": {name: found for name, found in hits.items() if found}}))
sys.exit(1 if any(hits.values()) else 0)
EOF
```

Expected:
- The branch is `eval/evocode-horizon` and `git status --short` prints nothing, so the code is the branch tip as committed. The last commit's hash is not checked; the suite count below is.
- Docker prints `8`, a memory value of at least `12000000000`, and `aarch64`.
- `harbor --version` prints `0.24.0`, the flag count is `6`, and the schema check prints `['disable_web_search', 'reasoning_effort', 'version']`.
- `claude --version` prints the host version. Note it: the judge runs on the host, and Step 17 compares against it.
- `zstd --version` prints a version, and Python prints `3.11` or later.
- The token byte count is above `20`.
- `docker system df` shows images using well under half of Docker Desktop's disk limit.
- The suite ends with `1242 passed, 8 skipped`, the count after the pre-freeze decisions (Task 12a); the time varies. The 8 skips are the freeze pins in `tests/test_evo_prereg.py`.

If anything differs:
- **Docker is not running:** start Docker Desktop and repeat.
- **The token is missing or the count is 0:** do the user's manual step.
- **The branch is not `eval/evocode-horizon`, the worktree is dirty, or a test fails:** fix it through the owning task's tests first.
- **The suite passes with other counts:** STOP. Other work has landed, and the counts in Steps 13 and 19 no longer hold.
- **Harbor is not 0.24.0:** STOP.
- **The architecture is not `aarch64`:** STOP.
- **Docker's disk is more than half full:** STOP, and ask the user to raise the disk limit. Validation builds about 11 images, each with a Go toolchain.

- [ ] **Step 2: Token and usage probe** (one one-word reply from the probe model)

Run: `cd "$WT" && python3 -c 'from bench.tb4 import run; info = run.probe_usage(run.read_token()) or {}; print({k: info.get(k) for k in ("status", "five_hour", "seven_day")})'`
Expected: a dict whose `status` starts with `allowed` and whose `five_hour` and `seven_day` are numbers. Note `seven_day`. The main run needs roughly 0.76 of a week (60 agent-hours at about 0.0127 per agent-hour). The runner waits on its own whenever usage leaves too little headroom.
If `five_hour` or `seven_day` is `None`, the probe returned no rate-limit data: STOP. The runner refuses to start a job without it (`InfrastructureFailure: usage probe returned no rate-limit data`), because its pacing would run blind.
If it raises `RuntimeError: no token in Keychain service tb4-horizon-claude-token; see plan step A2`, or a `ProbeError` that mentions authentication, do the user's manual step, then repeat. For any other `ProbeError`, wait 10 minutes and repeat once; if it fails again, STOP.

- [ ] **Step 3: Fetch and list the dataset**

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && python3 -m bench.evo.dataset fetch
python3 -m bench.evo.dataset list | python3 -c 'import json, sys; d = json.load(sys.stdin); print(d["tasks"], d["rounds"], d["reward_strategies"], d["pool_missing"], d["pool_rounds"], d["walkthrough"], d["nonuniform_agent_timeouts"])'
```

Expected:
- `fetch` prints one JSON line with `"revision": "9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6"`, a `"sha256"` equal to `"lfs_oid"`, and `"tasks": 26`. A rerun is idempotent and prints the same.
- `list` prints `26 227 ['mean'] [] 130 {'rounds': 5, 'minutes': <30 or 60>} []`. The last list holds the tasks whose rounds have different agent timeouts.

If anything differs:
- **`DatasetMismatch`:** STOP. Never retry with another revision, and never skip the check.
- **Any count differs, or `pool_missing` is not empty:** STOP. The download is not the dataset the spec describes.
- **The last list names a pool task or the walkthrough:** STOP. One M per task would be wrong for some of its rounds, and the freeze refuses such a task. A task outside the pool and the walkthrough does not matter.

- [ ] **Step 4: Write the seeded order**

Run: `cd "$WT" && python3 -m bench.evo.order | python3 -c 'import json, sys; d = json.load(sys.stdin); [print(i + 1, t, *d["arm_orders"][t]) for i, t in enumerate(d["order"])]'`
Expected, exactly. This is Task 3's seeded order and balanced arm order with seed 20261009, where consecutive positions get opposite arm orders:

```
1 theme_d1_w5_code_build_integration_e2e_wiring control horizon
2 theme_d5_w1_data_engineering_greenfield_implementation horizon control
3 theme_d8_w5_cloud_devops_integration_e2e_wiring horizon control
4 theme_d9_w11_security_automation_scripting control horizon
5 theme_d10_w4_ml_ai_mlops_migration_upgrade__not_1234 horizon control
6 theme_d11_w9_scientific_numerical_reproducibility_verification control horizon
7 theme_d12_w1_automation_productivity_greenfield_implementation control horizon
8 theme_d1_w11_code_build_automation_scripting horizon control
9 theme_d5_w9_data_engineering_reproducibility_verification control horizon
10 theme_d10_w9_ml_ai_mlops_reproducibility_verification horizon control
11 theme_d1_w2_code_build_brownfield_modification control horizon
12 theme_d5_w5_data_engineering_integration_e2e_wiring horizon control
13 theme_d10_w11_ml_ai_mlops_automation_scripting control horizon
```

Horizon runs first in 3 of the first 6 tasks, 4 of the first 8 and 5 of the first 10. The order is written to `$RUNS/order.json` once, and a rerun prints the same order.

If anything differs:
- **The domain sequence is not `d1 d5 d8 d9 d10 d11 d12 d1 d5 d10 d1 d5 d10`, or any line differs:** STOP. Task 3 does not implement the pre-registered draw.
- **It raises `FileExistsError: ... holds a different order; it is fixed once written`:** an earlier build of Task 3 wrote `$RUNS/order.json`. STOP.

- [ ] **Step 5: Build and smoke-test the Horizon bundle**

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && python3 -m bench.evo.prereg bundle
find "$RUNS/bundle" -type l | wc -l
for tag in python:3.10-slim python:3.12-slim; do
  docker run --rm --platform linux/arm64 --network none -e PYTHONDONTWRITEBYTECODE=1 \
    -v "$RUNS/bundle/horizon:/opt/horizon:ro" --entrypoint sh "$tag" -c '
set -eu
P="$(mktemp -d)"; H=/opt/horizon/scripts/harness.py
python3 "$H" --runtime claude --project "$P" doctor > "$P.doctor.json"
python3 "$H" --runtime claude --project "$P" start smoke --gate true --max-turns 3 --max-minutes 5 > "$P.start.json"
S="$(python3 -c "import json, sys; print(json.load(open(sys.argv[1]))[\"slug\"])" "$P.start.json")"
python3 "$H" --runtime claude --project "$P" status "$S" > /dev/null
test -f "$P/.claude/harness/goals/$S.json"
echo "ok $(python3 --version)"'
done
```

Expected:
- `{"commit": "f73975ad6db67b8d9ac0d822ed569a807e9eae59", "files": 12, "skill": "/Users/deeps/Documents/evocode-horizon-runs/bundle/horizon"}`. A rerun verifies the bundle instead of rebuilding it and prints the same.
- `0`: no symlinks.
- `ok Python 3.10.<x>`, then `ok Python 3.12.<x>`. The bundle is mounted read-only, so the smoke test writes nothing into it.

If anything differs:
- **`BundleMismatch`:** something changed `$RUNS/bundle`. Move it to `$RUNS/bundle-mismatch-1` and rerun this step. This is allowed only before the dry run.
- **A smoke test fails:** STOP. The v2 bundle does not run on its own at Python 3.10, so every Horizon round would fail.

- [ ] **Step 6: Harbor checks that use no subscription usage** (Task 7)

These steps run Task 7's checks for the first time, so every job is attempt 1. If Task 7's operator steps were run anyway, the `__a1` jobs already exist and `run_job` raises `FileExistsError`. Then rerun each command with the next `--attempt`; never delete an old attempt.

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && python3 -m bench.evo.harborcheck check --agent oracle
python3 -m bench.evo.harborcheck vtimeout
```

Expected:
- The first command takes about 3 minutes, including the first build of `evo-local/dummy:9fcae3e5539d`. It exits 0 with `"job_name": "check__dummy__oracle__a1"`, `"ok": true` and `"failures": []`.
- The second takes about 2 minutes. It exits 0 with `"job_name": "check__dummy-vtimeout__oracle__a1"`, `"ok": true` and `"failures": []`.

If `ok` is false, diagnose with Task 7's table "How to read a failed check" and its Step 10 instructions. Then rerun the failing command with `--attempt 2`. A row of that table that says STOP means STOP here too.

- [ ] **Step 7: Harbor checks that use a few minutes of usage** (Task 7)

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && python3 -m bench.evo.harborcheck check
python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["facts"])' "$RUNS/checks/check__dummy__control__a1/findings.json"
python3 -m bench.evo.harborcheck probe
python3 -m bench.evo.harborcheck canary --skill "$RUNS/bundle/horizon"
```

Expected:
- **`check`:** about 5 minutes. It exits 0 with `"job_name": "check__dummy__control__a1"`, `"ok": true` and `"failures": []`. In its facts:
  - `user` is `root` and `pwd` is `/app`;
  - `rewards` is `{'round-1': 1.0, 'round-2': 1.0}`;
  - `round_2_claude_like` holds exactly one `claude ...` line.
- **`probe`:** exits 0 with `"ok": null` and `"failures": []`. The exit code is 0 even when `failures` lists something, so read the array. Its `recorded` block shows:
  - `round_1_exception.type` is `AgentTimeoutError`, after 60.0 seconds;
  - `round_1_verifier_ran` is true and `round_1_reward` is 0.0;
  - `round_2_started` is true and `round_2_reward` is 1.0;
  - `still_running_in_round_2` is `[]`.
- **`canary`:** exits 0 with `"job_name": "canary__dummy__horizon__a1"`, `"ok": true` and `"failures": []`. It uses the Step 5 bundle, the one the horizon arm runs with. Harbor copies the skill directory into the container (`cp -r <skills_dir>/* $CLAUDE_CONFIG_DIR/skills/`), so the host bundle stays unchanged. Step 13 verifies that.

If anything differs:
- **`check` fails:** use Task 7's table, then rerun with `--attempt 2`.
- **`check` fails with `round-2: probe file round-2/procs.txt has no PID-1 line, so a killed decoy cannot be told from an unread process list`:** the agent's round-2 listing does not show the container's processes, so the decoy check read nothing. Rerun with the next `--attempt`. If it fails the same way again, STOP. Task 7's table has no row for this failure.
- **The probe's `failures` is not `[]`, or its `ok` is `false`:** its `recorded` facts rest on a process list nobody read, or on no trial at all. The failures read `round-1: probe file before-collect/procs.txt missing`, `round-2: probe file round-2/procs.txt missing`, `... procs.txt has no PID-1 line, so it did not list the container's processes` or `no trial directory with result.json`. Rerun the probe with the next `--attempt`. If it fails again, STOP. `prereg write` refuses to freeze on a probe whose latest attempt has `ok` false or any failure.
- **The probe shows `round_2_started` false or `round_1_reward` 1.0:** STOP.
- **The probe shows `sleep 600` still running, or two `claude` lines, in round 2:** fix Task 5's `COLLECT_COMMAND` as Task 7 Step 12 says. Rerun Task 5's tests, then rerun the probe with `--attempt 2`.
- **The canary reports `no Write tool call created the file without an error`:** STOP. The fallback, relocating the gates directory, changes the bundle and must be decided before the freeze.
- **The only failure is `API error: ...`:** rerun with `--attempt 2` once usage allows.

- [ ] **Step 8: Summarise the checks**

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && python3 - <<'EOF'
import hashlib, json
from bench.evo import config
for path in sorted((config.RUNS / "checks").glob("*/findings.json")):
    data = json.loads(path.read_text())
    print(data["job_name"], data["ok"], hashlib.sha256(path.read_bytes()).hexdigest()[:12], data["failures"])
EOF
```

Expected: one line per attempt. The latest attempt of each job reads as follows, with its own attempt number N and 12-character digest:
- `canary__dummy__horizon__aN True <digest> []`;
- `check__dummy-probe__control__aN None <digest> []`;
- `check__dummy-vtimeout__oracle__aN True <digest> []`;
- `check__dummy__control__aN True <digest> []`;
- `check__dummy__oracle__aN True <digest> []`.

`prereg write` records the latest attempts, and it refuses to freeze otherwise: every check's latest line must be `True <digest> []`, and the probe's latest line must be `None <digest> []`. A probe line with `False` or with any failure is refused, like a failed check. If any latest line differs, go back to Step 6 or 7.

- [ ] **Step 9: Validate tasks in the seeded order until 10 pass** (Mac time only; many hours)

Run: `docker system df`
Expected: images use well under half of Docker Desktop's disk limit. If the disk is already more than half full, STOP and ask the user to raise the limit.

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && nohup caffeinate -dimsu python3 -m bench.evo.validate --n 10 >> "$RUNS/logs/validate.log" 2>&1 &
echo $! > "$RUNS/validate.pid"
```

Monitor about every 30 minutes:

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
ls "$RUNS/validation" | grep -c '^theme_'; tail -3 "$RUNS/logs/validate.log"; kill -0 "$(cat "$RUNS/validate.pid")" 2>/dev/null && echo running || echo exited
```

How the walk works:
- Each task's agent and validation copies get the ARM patch. A Go tarball that a `COPY` or `ADD` takes from the build context is swapped for go.dev's linux-arm64 archive of the same version, checked against go.dev's published sha256. No pool task is known to bundle one: d10_w4 downloads its Go, and the patch rewrites that download, so nothing is swapped (the spec's guess that d10_w4 bundles its Go was wrong). In a `RUN` instruction's shell `case` statement that also has an arm whose pattern names `aarch64` or `arm64`, only the arms whose pattern names `x86_64` or `amd64` (and not `aarch64` or `arm64`) are left as they are, from the pattern to the `;;` (user decision, 2026-10-09; d12_w1). Every other part of the statement is patched and checked as usual: the `aarch64` and `arm64` arms, `*)` arms, combined arms such as `x86_64|aarch64)`, and the `case ... in` header.
- Then every copy's Dockerfile ends with the python3 block, `copies.PYTHON_BLOCK`, for both arms (user decision, 2026-10-09; d10_w4 and d1_w11 had no python3):

  ```dockerfile

  # evo: python3 for the Horizon CLI, added to both arms when the image lacks it (bench/evo/copies.py)
  RUN command -v python3 >/dev/null 2>&1 || (apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends python3 && rm -rf /var/lib/apt/lists/*)
  ```

  It does nothing when the image already has python3. `arm-patch.json`, and so each record's `arm_patch`, holds that `RUN` line under `added`.
- Each task builds its arm64 image. A failed build is retried once; a second failure is a validation failure.
- Before any Harbor job, the image's working directory is checked for a file named `setup.sh`. Harbor would overwrite it with the round hook every round, and the hook deletes itself, so such a task fails validation with the reason `project file setup.sh in the working directory would be overwritten by the round hook`. The walk swaps it like any other failure.
- Then every round runs three times: oracle, nop, and oracle with a planted ledger.
- When the process has exited, the log's last line is `{"selected": [...], "swaps": [...], "complete": true, "python_missing": []}`. `selected` holds 10 task names in seeded order. Each swap names a task that failed validation, its reason, and its replacement.
- The walk resumes. After a crash or reboot, launch the same command again, and finished tasks are read from `$RUNS/validation/`.
- A record made by other copy code (its `code` differs from `validate.CODE_DIGEST`) is validated again. So after any fix to Task 4, 5 or 6, the relaunched walk validates every task again. Task 12a changed `patch.py` and `copies.py`, so the walk after it validates every task again.

Read the results without opening any task material:

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && python3 - <<'EOF'
import json
from bench.evo import config, copies
for path in sorted((config.RUNS / "validation").glob("theme_*.json")):
    d = json.loads(path.read_text())
    print(path.stem, d["ok"], d["arch"], d["rounds"], (d.get("python") or {}).get("ok"),
          sum(c is None for c in d.get("oracle_cases") or []), len((d.get("arm_patch") or {}).get("artifacts") or []),
          (d.get("arm_patch") or {}).get("added") == [copies.PYTHON_LINE], d.get("user"), d["reason"])
EOF
```

Expected: one line per walked task, giving:
- the task name, `ok`, the architecture and the round count;
- whether python3 3.10 or later runs in the image;
- the number of rounds whose oracle run had no case counts;
- the number of bundled Go tarballs replaced;
- whether the copy ends with the python3 block (`arm_patch.added` is `[copies.PYTHON_LINE]`);
- the users the rounds run as;
- the reason it failed.

Every selected task prints `True arm64 <rounds> True 0 0 True ['root'] None`. The bundled-tarball count is 0 for every task, d10_w4 included: its Go is downloaded and the patch rewrites the download, so nothing is bundled. (An earlier version of this step said 1 for d10_w4; that was wrong.)

theme_d12_w1 must be among them: the user decided to include it (Task 12a), so the walk must not swap it. Its x86_64 `case` arm is left as written, so check that its image's Go toolchain is the arm64 one. The check prints only Go's version line, never task content:

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && docker run --rm --platform linux/arm64 --entrypoint go "$(python3 -c 'from bench.evo import config; print(config.image_tag("theme_d12_w1_automation_productivity_greenfield_implementation"))')" version
```

Expected: one line, `go version go<version> linux/arm64`.

`go` may not be on the image's PATH. In that case Docker fails with `executable file not found in $PATH`, and nothing runs. Then repeat the command with `--entrypoint /usr/local/go/bin/go`, where a Go tarball is usually unpacked. It must print the same `linux/arm64` line. If that also fails, STOP and ask the user. (This check runs `go version` on the binary itself instead of `file`, because `file` is often missing from slim images and `go version` states the same architecture.)

Watch item, recorded and not a STOP: the planted ledger plants test-like files in Python, Go, JavaScript, TypeScript and shell only (`validate.PLANTED_LEDGER`). Validation's planted-ledger oracle run is the real protection: it passes every round only when no verifier collected a planted file. To record which other languages a selected task could test in, list the language toolchains its `environment/Dockerfile` installs. That file is not under `tests/`, and the ARM patcher already reads it. The snippet prints only toolchain names, never file contents, and appends one dated line to `$RUNS/findings.md`:

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && python3 - <<'EOF'
import json
import re
import time
from bench.evo import config
# toolchain name -> what installs it in a Dockerfile; only the names are printed or recorded
TOOLCHAINS = {"python3": r"\bpython3?\b|\bpip3?\b", "golang": r"\bgolang\b|\bgo\d+\.\d+|/usr/local/go\b",
              "nodejs": r"\bnodejs\b|\bnpm\b|\bnode:\d|nodesource|\byarn\b", "typescript": r"\btypescript\b|\btsc\b",
              "rustc": r"\brustc\b|\bcargo\b|\brustup\b", "openjdk": r"\bopenjdk\b|\bjdk\b|\bmaven\b|\bgradle\b",
              "ruby": r"\bruby\b", "php": r"\bphp\b", "dotnet": r"\bdotnet\b", "perl": r"\bperl\b",
              "gcc": r"\bgcc\b|\bg\+\+|\bclang\b|build-essential", "r-base": r"\br-base\b"}
PLANTED = {"python3", "golang", "nodejs", "typescript"}  # shell is in every image
summary = json.loads((config.RUNS / "validation" / "summary.json").read_text())
tasks = config.RUNS / "dataset" / config.DATASET_REVISION / "tasks"
lines = []
for task in summary["selected"]:
    text = (tasks / task / "environment" / "Dockerfile").read_text(errors="replace")
    found = sorted(name for name, pattern in TOOLCHAINS.items() if re.search(pattern, text, re.I))
    other = [name for name in found if name not in PLANTED]
    planted = json.loads((config.RUNS / "validation" / f"{task}.json").read_text()).get("planted") or []
    planted_ok = bool(planted) and all(r is not None and r >= 1.0 for r in planted)
    print(task, found, "other:", other, "planted_ok:", planted_ok, "STOP" if other and not planted_ok else "record")
    lines.append(f"- {time.strftime('%Y-%m-%d')}: Step 9 toolchains: {task} {found}\n")
with open(config.RUNS / "findings.md", "a") as handle:
    handle.writelines(lines)
EOF
```

Expected: one line per selected task, giving its toolchain names, the ones outside Python, Go, JavaScript, TypeScript and shell, `planted_ok: True` and `record`. Every selected task passed validation, so its planted-ledger oracle run passed every round, and the last word is `record`. A toolchain outside that set is a finding for the report's reader, not a defect.

If anything differs:
- **`"complete": false`:** all 13 tasks were walked and fewer than 10 passed. If fewer than 6 passed, STOP: no N fits. Otherwise note the count in `$RUNS/findings.md` and continue. The freeze refuses an N larger than this count, and that becomes a STOP at Step 13.
- **A task is not `ok` because of `UnknownArchPattern` (its reason starts `copy failed: line`):** STOP. The spec requires patching every pool task's amd64 Go and yq artifacts, and tasks are not chosen by whether they build. Write the line number (not the line) to `$RUNS/findings.md`. Ask the user whether to extend Task 4's patcher (with a test) or to log a swap.
- **A task's reason starts `copy failed:` and mentions a symlink:** STOP. The copies refuse a symlink that leaves the task directory (a runner limit, not a property of the task), so the walk swapped the task for the runner's sake. Write only `copy failed: symlink` and the task name to `$RUNS/findings.md`, never the path. Ask the user whether to log the swap.
- **A toolchain line ends with `STOP`:** the task installs a toolchain outside Python, Go, JavaScript, TypeScript and shell, and its planted-ledger oracle run did not pass every round. STOP and ask the user. With an `ok` validation record this cannot happen, so it means the record and the walk disagree.
- **`python_missing` is not empty, or a selected task prints python `False`:** STOP and ask the user. Every copy now ends with the python3 block, but the block installs python3 only when the image has none. So python `False` means one of two things: the image already had a python3 older than 3.10, which makes the block a no-op; or the block installed the distribution's python3, and that is older than 3.10. Both are STOPs. The spec's validation criteria do not cover a container without python3 3.10 or later, and the Horizon CLI cannot run in one.
- **A selected task prints `False` for the python3 block:** STOP. Its record says ok, but its copy does not end with the block, so the record and the copy code disagree. (A task whose copy failed also prints `False`; its reason says why.)
- **A task fails with `build failed twice` (the walk swaps it) although the earlier walk built it, for example d10_w4 or d1_w11:** STOP and ask the user. The python3 block is the only change to its image, so the block probably broke the build, for example with no `apt-get` in the image or a non-root `USER` at the end of the Dockerfile.
- **theme_d12_w1 is not `ok`, for any reason, or is not in `selected`:** STOP and ask the user. The user decided to include it (Task 12a), so the walk's swap of it is not accepted on its own. Write only its reason's first words (for example `copy failed: line`, with the line number but not the line) to `$RUNS/findings.md`.
- **The d12_w1 Go check prints anything other than `linux/arm64`, or fails with both entrypoints:** STOP and ask the user. Its image would build or test with an amd64 toolchain.
- **A selected task has a null case count above 0:** set `T` to that task's name and run only `grep -c CASE_SUMMARY "$RUNS"/checks/oracle__"$T"/*/steps/*/verifier/test-stdout.txt`. It prints one count per round file. Never print or open the files. STOP and report the counts.
- **A selected task's users are not `['root']`:** STOP and ask the user. The spec says the per-round setup hook runs as root, but Harbor runs it as the step's agent user. A non-root user leaves root-owned `/tmp` entries and has a different `HOME`.

- [ ] **Step 10: Validate the walkthrough task; reclaim the build cache**

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && python3 -m bench.evo.validate --task theme_d11_w2_scientific_numerical_brownfield_modification__not_1234
python3 -c 'import json; from bench.evo import config; d = json.loads((config.RUNS / "validation" / f"{config.WALKTHROUGH}.json").read_text()); print((d.get("python") or {}).get("ok"), sum(c is None for c in d.get("oracle_cases") or []), d.get("user"))'
docker builder prune -f && docker system df
```

Expected:
- `{"task": "theme_d11_w2_scientific_numerical_brownfield_modification__not_1234", "ok": true, "reason": null}` and exit 0.
- `True 0 ['root']`: python3 runs in the image, every oracle round has case counts, and the rounds run as root.
- The prune does not touch the tagged images, which every run pins by ID.

If `ok` is false, STOP. The dry run needs this task, and the spec allows its own ARM validation but no substitute. If the second line differs, STOP, as in Step 9.

- [ ] **Step 11: Timing dry run** (walkthrough task, both arms; about 3-10 agent-hours)

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && python3 -m bench.evo.run --phase dry --print-commands
python3 -m bench.evo.run --phase dry --print-commands | grep -c -- '--resume-trajectory'
python3 -m bench.evo.run --phase dry --print-commands | grep -c -- '--skill'
```

Expected:
- Two `harbor run` lines, in the walkthrough's seeded arm order:
  - first `--job-name dry__theme_d11_w2_scientific_numerical_brownfield_modification__not_1234__horizon__a1`;
  - then `--job-name dry__theme_d11_w2_scientific_numerical_brownfield_modification__not_1234__control__a1`.
- Both lines carry:
  - `--agent-timeout-multiplier 1.0 --agent-setup-timeout-multiplier 2.0`;
  - `--ak version=2.1.293 --ak disable_web_search=true`;
  - the literal `CLAUDE_CODE_OAUTH_TOKEN=${CLAUDE_CODE_OAUTH_TOKEN}`, never the token itself;
  - their own `--extra-instruction-path /Users/deeps/Documents/evocode-horizon-runs/prompts/theme_d11_w2_scientific_numerical_brownfield_modification__not_1234__horizon.md` or `...__control.md`.
- `0`: no line has `--resume-trajectory`.
- `1`: only the horizon line has `--skill /Users/deeps/Documents/evocode-horizon-runs/bundle/horizon`.

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && nohup caffeinate -dimsu python3 -m bench.evo.run --phase dry >> "$RUNS/logs/dry.log" 2>&1 &
echo $! > "$RUNS/dry.pid"
```

Monitor about every 30 minutes:

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
ls "$RUNS/records" | grep '^dry__'; tail -3 "$RUNS/logs/dry.log"; kill -0 "$(cat "$RUNS/dry.pid")" 2>/dev/null && echo running || echo exited
```

Expected: when the process exits, the log's last line is:

```
{"phase": "dry", "dropped": [], "records": [["dry__theme_d11_w2_scientific_numerical_brownfield_modification__not_1234__horizon__a1", true, null], ["dry__theme_d11_w2_scientific_numerical_brownfield_modification__not_1234__control__a1", true, null]]}
```

An invalid first attempt is retried once on its own. It shows as an extra entry, with its `invalid_reason`, before the `__a2` entry.
If the log ends with `stopped: - dry theme_d11_w2_scientific_numerical_brownfield_modification__not_1234: dropped after two invalid runs ...` (exit 3), STOP. Without a valid dry run there is no N. The runner wrote that drop line to `$RUNS/findings.md`, the pre-freeze log, never to `$RUNS/deviations.md`. A dry drop is never skipped on a relaunch, so if the user says to retry, relaunch the command above with `--attempt 3`, the next unused number; an arm that already has a valid dry record is not run again.
Handle `RunnerBusy`, `InfrastructureFailure` (including `usage probe returned no rate-limit data`), `usage headroom did not return` and authentication errors as in Step 14.

When the process has exited, confirm that Harbor left the walkthrough's agent copy unchanged. The dry run is the first Harbor job that runs from an agent copy (`-p`), and on every main start `verify_runs` holds each agent copy to the digest validation recorded:

Run: `cd "$WT" && python3 -c 'import json; from bench.evo import config; from bench.tb4.copies import tree_digest; w = config.WALKTHROUGH; d = json.loads((config.RUNS / "validation" / f"{w}.json").read_text()); print(tree_digest(config.RUNS / "copies" / "agent" / w) == d["copy_digest"])'`
Expected: `True`. If it prints `False`, STOP before the freeze: a Harbor job changed its task directory, so every main relaunch would refuse with `NotFrozen`.

- [ ] **Step 12: Score the dry run and apply the N rule**

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && python3 -m bench.evo.score --phase dry
python3 - <<'EOF'
import json
from bench.evo import config
rows = [json.loads(line) for line in open(config.RUNS / "rows.jsonl") if line.strip()]
dry = sorted((r for r in rows if r["phase"] == "dry"), key=lambda r: (r["arm"], r["round_index"]))
for r in dry:
    minutes = None if r["agent_sec"] is None else round(r["agent_sec"] / 60, 1)
    print(r["arm"], r["round_index"], r["reward"], r["passed"], minutes, r["horizon_started"], r["horizon_calls"],
          r["result_records"], r["end_reason"])
horizon = [r for r in dry if r["arm"] == "horizon"]
print({"rows": len(dry), "horizon_started": sum(r["horizon_started"] for r in horizon), "horizon_rounds": len(horizon),
       "control_horizon_calls": sum(r["horizon_calls"] for r in dry if r["arm"] == "control")})
EOF
python3 - <<'EOF'
import json
from pathlib import Path
from bench.evo import config, score, steps
from bench.tb4 import stream
from bench.tb4.score import parse_time
for path in sorted((config.RUNS / "records").glob("dry__*.json")):
    record = json.loads(path.read_text())
    if not record.get("valid") or not record.get("trial_dir"):
        continue
    for step in steps.read_trial(record["trial_dir"])["steps"]:
        t0 = parse_time(step["agent_started"])
        starts = [parse_time((g.get("used") or {}).get("started"))
                  for g in (score.ledger_goals(step["artifacts_dir"], step["name"]) or {}).values()]
        new = sorted((t - t0).total_seconds() / 60 for t in starts if t and t0 and t >= t0)
        usage = stream.rate_limit_info(stream.read_events(Path(step["agent_dir"]) / "claude-code.txt")) or {}
        print(record["arm"], step["index"], "goal_start_min", round(new[0], 1) if new else None,
              "five_hour", usage.get("five_hour"), "seven_day", usage.get("seven_day"))
EOF
python3 - <<'EOF'
import json
from pathlib import Path
from bench.evo import config, steps
from bench.evo import run as evo_run
from bench.tb4 import stream
# the runner paces jobs with TB4's rates per agent-hour; the dry run measures this study's own
limits = {"seven_day": evo_run.WEEKLY_PER_AGENT_HOUR, "five_hour": evo_run.FIVE_HOUR_PER_AGENT_HOUR}
for path in sorted((config.RUNS / "records").glob("dry__*.json")):
    record = json.loads(path.read_text())
    if not record.get("valid") or not record.get("trial_dir"):
        continue
    readings, hours = [record.get("usage_before") or {}], []
    for step in steps.read_trial(record["trial_dir"])["steps"]:
        readings.append(stream.rate_limit_info(stream.read_events(Path(step["agent_dir"]) / "claude-code.txt")) or {})
        hours.append((step["agent_sec"] or 0) / 3600)
    rates = {}
    for window in limits:  # sum the rises between consecutive readings of one window, skipping a reset
        rise = used = 0.0
        for before, after, h in zip(readings, readings[1:], hours):
            if (before.get(window) is not None and after.get(window) is not None
                    and before.get(f"{window}_resets_at") == after.get(f"{window}_resets_at")):
                rise, used = rise + after[window] - before[window], used + h
        rates[window] = round(rise / used, 4) if used else None
    print(record["arm"], rates, {w: rates[w] is None or rates[w] > limits[w] for w in limits})
EOF
python3 -m bench.evo.analysis timing | tee "$RUNS/timing.json"
```

Expected:
- `score` prints `{"phase": "dry", "rows": 10, "out": "/Users/deeps/Documents/evocode-horizon-runs/rows.jsonl"}`: 2 arms times 5 rounds.
- The first inspection prints 10 lines, then the summary dict. Each line gives the arm, round, reward, passed, minutes, Horizon started, Horizon calls, result records and end reason.
- The second inspection prints one line per round each valid dry job reached. Each line gives the arm, the round index, `goal_start_min`, `five_hour` and `seven_day`:
  - `goal_start_min` is the minutes from the round's agent start to the first goal it created. It is a number in horizon rounds where Horizon started, and `None` in control rounds;
  - `five_hour` and `seven_day` are the utilizations at the round's last rate-limit event.

  It prints offsets and utilizations only, never goal text.
- The third inspection prints one line per valid dry job: the arm, its measured `seven_day` and `five_hour` rise per agent-hour, and whether each one exceeds the rate the runner paces with (`run.WEEKLY_PER_AGENT_HOUR` 0.0127, `run.FIVE_HOUR_PER_AGENT_HOUR` 0.098). A rate is `None` when no two consecutive readings of that window fall in one window period. It prints numbers only. Expected: both flags `False` for both arms.
- `timing` prints `{"m_control": <x>, "m_horizon": <y>, "rounds": {"control": 5, "horizon": 5}, "n": <10, 8 or 6>}`, and `$RUNS/timing.json` holds it.

STOP if any of these holds:
- **`"n": null`** (and `timing` exits 1): even N = 6 does not fit 60 agent-hours. By the spec's rule, report this to the user before the freeze.
- **`n` is larger than the number of tasks Step 9 validated.**
- **`control_horizon_calls` is above 0, or any control row has `horizon_started` true:** the control arm saw Horizon.
- **`horizon_started` is 0:** Horizon never started, so the skill or the prompt is broken.
- **Either arm's measured rise per agent-hour exceeds the runner's rate, or is `None` (a flag is `True`):** the runner's pacing, frozen at the tag, would admit jobs that can pass the 0.9 ceilings. STOP before the freeze and report the numbers.

Otherwise, append a dated line to `$RUNS/findings.md` with these numbers:
- rounds passed per arm;
- the Horizon start rate;
- m_control, m_horizon and N;
- the median goal start offset;
- each arm's rise in `five_hour` and `seven_day` across its rounds, and its rise per agent-hour.

A low start rate, or rounds that run out of time, is a finding, not a defect to fix.

- [ ] **Step 13: Freeze**

Write the pre-registration and read it:

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && git status --short
python3 -m bench.evo.prereg write
sed -n '/^## Tasks/,/^## N rule/p' docs/evaluations/evocode-horizon/prereg.md
sh bench/tb4/test.sh -q -rs tests/test_evo_prereg.py | grep -c 'before the freeze: no prereg.json'
```

Expected:
- `git status --short` prints nothing at all; otherwise STOP. `write` hashes the spec and the judge rubric from the working tree, and the tag freezes everything in `prereg.FROZEN_PATHS`: `bench/evo`, `bench/tb4`, `prereg.json`, the judge rubric, the spec and `prereg.md`. Every one of them must be committed, with `prereg.json` and `prereg.md` added by the commit below, before the tag. A dirty spec would leave a digest in the tagged record that no commit holds.
- `write` prints `{"n": N, "tasks": [...], "replaced": [...], "reserve": [...], "json": "/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode/docs/evaluations/evocode-horizon/prereg.json", "md": "/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode/docs/evaluations/evocode-horizon/prereg.md"}`, where:
  - N equals Step 12's `n`;
  - `tasks` are the first N names of Step 9's `selected` list;
  - `replaced` names the same tasks as the swaps that come before them in Step 9. Compare task names only: `replaced` gives each a shortened public reason (`copy failed`, `build failed twice`, `planted copy failed`, with `$RUNS` and `~` in place of host paths), and the full text stays in `$RUNS/validation/<task>.json`;
  - `reserve` is the rest of Step 9's `selected` list, in order. For N = 8 that is the 9th and 10th names, and for N = 10 it is `[]`. These are the pre-registered replacements for main tasks dropped by an infrastructure failure.
- `prereg.json` also records `decisions`: the three decisions made before the freeze, `prereg.DECISIONS` (python3 added to the copies for both arms, the ARM patcher leaving an arch `case` statement that has an arm64 arm, and the d5_w9 swap). `write` does not print them.
- The section shows a table of the N measured tasks with their image IDs, then the list of swapped tasks, then `### Reserve`. That subsection holds a table of the reserve tasks with their image IDs, or `No reserve: validation stopped at the Nth passing task, or no later task passed it.` Then `## Decisions made before the freeze` lists the three decisions, one bullet each, word for word as in `prereg.DECISIONS`.
- The grep count is `0`: the freeze pins now run against `prereg.json`.

If `write` fails, act on its message:

| `write` message contains | Action |
| --- | --- |
| `even N = 6 does not fit` | STOP: report to the user |
| `validated tasks; the order has` | STOP: fewer tasks passed validation than the N rule needs |
| `has no validation record` | Step 9 did not finish its walk: relaunch Step 9 (it resumes), then repeat this step |
| `validated by other copy code` | Relaunch Step 9. It validates again every task whose record other code made. Then repeat this step |
| `its validation record says ok, but its rewards or arch break the rule` | STOP |
| `have different agent timeouts` | STOP: one M per task would be wrong for some rounds; ask the user |
| `Harbor check results missing` or `the latest attempt of a Harbor check failed` | Go back to Steps 6-8 |
| `the latest attempt of the timeout probe failed` | Go back to Step 7 and rerun the probe with the next `--attempt`. If it fails again, STOP |
| `BundleMismatch` | STOP: `$RUNS/bundle` changed after the dry run used it |
| `harbor --version printed` | STOP: Harbor is no longer 0.24.0 |
| `commit the code before the freeze` | Commit the pending code change with its tests passing, then repeat |
| `PIN.json does not pin`, `order.json is not the seeded order` or `order.json has the wrong arm order` | STOP |
| `already frozen at` | The tag already exists. Do not write again; continue from the post-freeze checks below |
| Anything else | STOP |

Run the full suite before the commit:

Run: `cd "$WT" && sh bench/tb4/test.sh -q`
Expected: the last line is `1250 passed` followed by the run time, with no `skipped`. That is Step 1's `1242 passed, 8 skipped` with the 8 freeze pins now running. Any other count, or any failure, is a STOP.

Commit and tag. Each command runs only if the one before it succeeded:

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && git add docs/evaluations/evocode-horizon/prereg.json docs/evaluations/evocode-horizon/prereg.md \
  && python3 -I "$RUNS/bin/contamination-check.py" "$RUNS/dataset/$REV/tasks" docs/evaluations/evocode-horizon \
  && ! grep -rn -e "$HOME" -e '/Users/' docs/evaluations/evocode-horizon/ \
  && python3 -m bench.tb4.leakcheck \
  && git commit -m "docs(evo): pre-register the EvoCode-Bench first look" \
  && git tag -a evo-prereg -m "EvoCode-Bench first look pre-registration"
```

Expected:
- The contamination check prints `"hits"` of 0 for every file and exits 0.
- The host-path `grep` prints nothing: no file names the home directory or any `/Users/` path.
- `leakcheck` prints `no token in staged files`.
- The commit and the tag succeed.

If the contamination check reports hits, or the host-path `grep` prints any line, nothing is committed. Run `git restore --staged docs/evaluations/evocode-horizon`, do not open the lines the contamination check lists, and STOP.

Run: `cd "$WT" && git show --stat --format=%s HEAD | head -20`
Expected: the subject `docs(evo): pre-register the EvoCode-Bench first look`, then exactly two files, `docs/evaluations/evocode-horizon/prereg.json` and `docs/evaluations/evocode-horizon/prereg.md`, and `2 files changed`.

Post-freeze checks:

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && python3 -m bench.evo.prereg check
git rev-parse 'evo-prereg^{commit}'
sh bench/tb4/test.sh -q tests/test_evo_prereg.py | tail -1
python3 -m bench.evo.run --phase main --print-commands | wc -l
```

Expected:
- `check` prints `{"frozen": "<commit>"}`, and the `git rev-parse` line prints the same commit.
- The pins print `72 passed`: the file's `64 passed, 8 skipped` before the freeze, with the 8 freeze pins running.
- The command count is `2N` lines, for example `16` for N = 8. No task has dropped yet, so no reserve is in play.

From here on, every change goes to `$RUNS/deviations.md` with its date and reason.

- [ ] **Step 14: Main run** (about 60 agent-hours plus usage waits; expect about a week)

Probe the token and usage first:

Run: `cd "$WT" && python3 -c 'from bench.tb4 import run; info = run.probe_usage(run.read_token()) or {}; print({k: info.get(k) for k in ("status", "five_hour", "seven_day")})'`
Expected: a dict whose `status` starts with `allowed` and whose `five_hour` and `seven_day` are numbers. If they are `None`, the probe returned no rate-limit data: STOP, because the runner would refuse every job (`InfrastructureFailure: usage probe returned no rate-limit data`). If the token fails, do the user's manual step.

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && nohup caffeinate -dimsu python3 -m bench.evo.run --phase main >> "$RUNS/logs/main.log" 2>&1 &
echo $! > "$RUNS/main.pid"
```

Monitor about every hour:

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
ls "$RUNS/records" | grep -c '^main__'; tail -5 "$RUNS/logs/main.log"; kill -0 "$(cat "$RUNS/main.pid")" 2>/dev/null && echo running || echo exited
```

Expected:
- Before any job, the runner checks that every planned task's validated image is the one in `prereg.json`.
- The record count rises to at least `2N`. Add 2 for each reserve in play, plus any retries and a dropped task's records.
- Before each job, the runner probes usage. It logs a wait whenever the weekly utilization leaves too little room for a worst-case job.
- When the process exits, the log's last line is `{"phase": "main", "dropped": [...], "records": [...]}`.

When the runner stops early, or a live runner keeps logging the same failure, act on the end of `main.log`:

| Log ends with | Action |
| --- | --- |
| `stopped: - main <task>: dropped after two invalid runs ...` (exit 3) | The runner has written the drop to `$RUNS/deviations.md`. A drop follows two invalid runs of one arm: usage limit, API error, infrastructure failure, host sleep or interruption. An interrupted second attempt drops the task too (see the row for a reboot). A Harbor exception type the runner does not know makes the run `infrastructure`, and that can be the model's own repeatable crash. So if the line names `api_error` or `infrastructure`, **STOP** before relaunching: read each invalid job's transcript (`agent/claude-code.txt` under `$RUNS/jobs/<job>/`, never quoted) and its log (`$RUNS/logs/<job>.log`). Relaunch only once they show a passing API or Docker failure, not model behaviour, because the relaunch brings in a reserve. For any cause, fix it (Docker, network, token or usage), then relaunch this step. The runner skips the dropped task and every finished job. It runs the next unused reserve from `prereg.json`'s `reserve`, as pre-registered; with no reserve left, the drop stays a drop. Check which reserve came in with the command below. If the same cause drops a second task, **STOP** |
| `RunnerBusy` | Follow the RunnerBusy procedure below, then relaunch |
| `InfrastructureFailure: usage probe returned no rate-limit data` | **STOP**. The probe before the job sent no `five_hour` or `seven_day` utilization, so the runner started no job and spent no attempt; its pacing would have run blind |
| Any other `InfrastructureFailure` | Check `docker info` and the network, then relaunch |
| `usage headroom did not return` | Relaunch after the reset time shown in the log |
| `usage probe failed (exit ...): ...; retrying in 600 s` (or `usage probe did not finish: ...; retrying in 600 s`), while the runner is still running | The runner retries the probe every 10 minutes, up to 48 times, and no job runs meanwhile. Read the message. If it mentions authentication (`401`, `OAuth token has expired` or `invalid x-api-key`), confirm that `pgrep -fl 'harbor run'` prints nothing, end the runner with `kill "$(cat "$RUNS/main.pid")"`, do the user's manual step and relaunch: the runner read the token once, at launch. Otherwise, after 3 such lines in a row, **STOP** |
| A `ProbeError`, or a job invalid with `api_error`, mentioning authentication | Do the user's manual step, then relaunch |
| `ImageChanged`, `NotFrozen` or `NotValidated` | **STOP**, whatever the message. See "Freeze refusals" below |
| `CalledProcessError` from `docker image inspect` | **STOP**: the pinned image is gone, and rebuilding gives a new image ID, which is a deviation |
| Nothing (the Mac rebooted, or the process vanished) | Run the interrupted-job check below before relaunching. For a job it calls a `first attempt`, relaunch: the runner records the job as `interrupted` and retries it. For a job it calls a `retry`, **STOP**: the relaunch would record it as `interrupted` and drop the task, and a reserve would come in |
| Anything else | **STOP** |

**Freeze refusals.** Never move, delete or recreate the `evo-prereg` tag, never pass `--prereg`, and never edit `$RUNS/bundle`, a prompt file or an agent copy to make a check pass. Ask the user. The messages:
- `ImageChanged: <task>: validated image differs from the pre-registered one` at launch: a validation rerun after the freeze replaced a pinned image. This check also covers a reserve once it comes into play.
- `NotFrozen` from `prereg.verify_runs`, at launch: `.../bundle differs from commit f73975a in [...]`, `.../bundle is not the bundle prereg.json pins; ...`, `.../prompts/<task>__<arm>.md differs from the <arm>@<M> prompt file prereg.json pins`, `.../copies/agent/<task>: the agent copy prereg.json pins for <task> is missing`, `.../copies/agent/<task> differs from the agent copy prereg.json pins for <task>`, or `<task> is not pre-registered in prereg.json`. Something changed the run directory after the freeze.
- `NotFrozen` from `prereg.require_frozen_record`: `<path> differs from docs/evaluations/evocode-horizon/prereg.json at evo-prereg`, or `cannot read <path>`. The prereg file is not the tagged one.
- `NotFrozen` from `prereg.require_freeze`: `tag evo-prereg is missing`, `HEAD does not contain evo-prereg`, `frozen paths changed since evo-prereg: ...`, `uncommitted changes in frozen paths` or `docs/evaluations/evocode-horizon/report.md is committed: the main run is closed`. A frozen path changed, or the report is committed.

Interrupted-job check: it lists each main job directory that has no record, and whether the relaunch would retry it or drop its task.

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && python3 - <<'EOF'
import json
from bench.evo import config
records = [json.loads(p.read_text()) for p in (config.RUNS / "records").glob("main__*.json")]
for job in sorted((config.RUNS / "jobs").glob("main__*")):
    if not (config.RUNS / "records" / f"{job.name}.json").exists():
        head, arm, _ = job.name.rsplit("__", 2)
        task = head[len("main__"):]
        retry = any((r["task"], r["arm"]) == (task, arm) and not r["valid"] for r in records)
        print(job.name, "retry" if retry else "first attempt")
EOF
```

Expected: nothing, or one line per unfinished job. Usually there is one: the job that was running.

To see the drops and the reserves now in play:

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && python3 -c 'import json; from bench.evo import config, run; p = json.loads((config.DOCS / "prereg.json").read_text()); d = run.dropped_tasks(run.Paths(config.RUNS), "main"); print(d, run.main_tasks(p, d)[len(p["tasks"]):])'
```

Expected: the dropped main tasks in drop order, then the reserves in play. There is one reserve per drop while reserves last, in `prereg.json`'s `reserve` order. With no drop, it prints `[] []`.

**RunnerBusy procedure.** Either another runner holds the lock, or a job's containers are still running.
1. `cat "$RUNS/runner.lock"` shows the runner's PID. If `ps -p <pid>` shows a live `bench.evo` process, wait for it, or end it with `kill <pid>`.
2. If `pgrep -fl 'harbor run'` shows a Harbor process, wait for it to finish.
3. If neither is alive, remove each project named in the error with this command, then relaunch:

   ```bash
   docker ps -aq --filter label=com.docker.compose.project=<project> | xargs docker rm -f
   ```

A relaunch never runs again a job that has a record. It can still drop a task: after an interrupted retry, as above.

- [ ] **Step 15: Score the main run**

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && python3 -m bench.evo.score --phase main
python3 -c 'import json; from bench.evo import config, run; p = json.loads((config.DOCS / "prereg.json").read_text()); d = run.dropped_tasks(run.Paths(config.RUNS), "main"); print(d, 2 * sum(p["task_details"][t]["rounds"] for t in run.main_tasks(p, d)))'
```

Expected:
- `score` prints `{"phase": "main", "rows": R, "out": "/Users/deeps/Documents/evocode-horizon-runs/rows.jsonl"}`.
- The second line prints the dropped tasks and the planned rounds of both arms, counting every reserve in play.
- With no drop, R equals that number. Otherwise R equals it minus the rounds of each dropped task's arms that have no valid record.

Every valid job yields one row per round, and a round the chain never reached is a `not reached` row. Each row also carries `exception`, the round's Harbor exception type or `null`.
If R differs otherwise, STOP. Rescoring is safe until Step 19 commits the report: labels are kept while the final message is unchanged.
If `score` raises `ValueError: <job>: the record is valid but its trial result.json is missing; ...`, STOP: a job directory was moved or deleted, and scoring it as failed rounds would be wrong.
If `score` raises `NotFrozen` with any message, STOP: a frozen path changed, or the report is committed. Never move, delete or recreate the `evo-prereg` tag, and never pass `--prereg`; ask the user. See "Freeze refusals" in Step 14.

- [ ] **Step 16: Integrity check against the pre-registration**

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && python3 - <<'EOF'
import hashlib
import json
import subprocess
from bench.evo import config
from bench.evo import run as evo_run
from bench.tb4.copies import tree_digest
p = json.loads((config.DOCS / "prereg.json").read_text())
tag = subprocess.run(["git", "rev-parse", "evo-prereg^{commit}"], capture_output=True, text=True, check=True).stdout.strip()
dropped = evo_run.dropped_tasks(evo_run.Paths(config.RUNS), "main")
tasks = evo_run.main_tasks(p, dropped)  # the measured tasks plus the reserves in play
problems = []
for task in tasks:
    details = p["task_details"][task]
    for arm in config.ARMS:
        path = config.RUNS / "prompts" / f"{task}__{arm}.md"
        found = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if found != p["prompts"]["file_sha256"][f"{arm}@{details['minutes']}"]:
            problems.append(f"prompt file {path.name}: {found}")
    found = tree_digest(config.RUNS / "copies" / "agent" / task)
    if found != details["copy_digest"]:
        problems.append(f"agent copy {task}: {found}")
records = [json.loads(x.read_text()) for x in sorted((config.RUNS / "records").glob("main__*.json"))]
for r in records:
    if r["task"] not in tasks:
        problems.append(f"{r['job_name']}: not a planned task")
        continue
    if r.get("valid") and r.get("image_id") != p["task_details"][r["task"]]["image_id"]:
        problems.append(f"{r['job_name']}: image {r.get('image_id')}")
    if r.get("freeze") != tag:
        problems.append(f"{r['job_name']}: freeze {r.get('freeze')}")
    # an interrupted record, or an infrastructure record without a trial, never ran a trial and has no versions
    if r.get("trial_dir") and (not str(r.get("harbor_version") or "").startswith(config.HARBOR_VERSION)
                               or not str(r.get("claude_code_version") or "").startswith(config.CLAUDE_CODE_VERSION)):
        problems.append(f"{r['job_name']}: versions {r.get('harbor_version')}, {r.get('claude_code_version')}")
valid = {(r["task"], r["arm"]) for r in records if r.get("valid")}
missing = [f"{t}__{a}" for t in tasks for a in config.ARMS if (t, a) not in valid]
print(json.dumps({"planned": len(tasks), "dropped": dropped, "records": len(records), "valid_arms": len(valid),
                  "missing": missing, "problems": problems}, indent=2))
EOF
```

Expected:
- `"problems": []`.
- With no drop: `"planned": N`, `"dropped": []`, `"valid_arms": 2N` and `"missing": []`.
- After drops, `planned` also counts the reserves in play, and `missing` lists only arms of tasks in `dropped`.
- The freeze commit is checked on every main record. The versions are checked only on records that ran a trial: an `interrupted` record, or an `infrastructure` record whose job left no trial, has `null` versions by design, and its relaunch is the rerun the pre-registration allows.

If anything else appears, append each problem to `$RUNS/deviations.md` as a dated line and STOP.

- [ ] **Step 17: Label the final messages** (the TB4 blind judge, on the host)

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
claude --version
cd "$WT" && python3 -c 'import json; from bench.evo import config; print(json.loads((config.DOCS / "prereg.json").read_text())["observed"]["claude (host)"])'
nohup caffeinate -dimsu python3 -m bench.evo.analysis label >> "$RUNS/logs/label.log" 2>&1 &
echo $! > "$RUNS/label.pid"
```

Expected:
- The two version lines match. If they differ, append a dated line to `$RUNS/deviations.md` before labelling, because the judge's host version changed after the freeze.
- `label.log` gains one `labeled <job> round <k>: <label>` line per message, and its last line is the count labelled.

Monitor until the process exits:

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
tail -2 "$RUNS/logs/label.log"; kill -0 "$(cat "$RUNS/label.pid")" 2>/dev/null && echo running || echo exited
```

On a judge error or an authentication error, fix it (the user's manual step for the token) and relaunch. Only unlabelled rows are sent again.
If `label.log` ends with `NotFrozen`, whatever the message, STOP: it is not a judge error. A frozen path (the code, the spec, the judge rubric, `prereg.json` or `prereg.md`) changed after the tag, or the report is committed. Never move, delete or recreate the `evo-prereg` tag, and never pass `--prereg`; ask the user. See "Freeze refusals" in Step 14.

- [ ] **Step 18: Render the report**

Run: `cd "$WT" && python3 -m bench.evo.analysis report`
Expected: exit 0, and `docs/evaluations/evocode-horizon/report.md` is written. It prints one JSON line with two keys:
- `primary`, with the keys `mean`, `ci95`, `p`, `wins`, `losses` and `ties`;
- `statuses`, with one entry per planned task, including any reserve a drop brought in. Each entry is `valid` or `dropped`.

The report states the analysed count, the pre-registered N and how many dropped tasks a reserve replaced. Its Deviations section is `$RUNS/deviations.md` with the run directory, the worktree and the home directory written as `$RUNS`, `$WT` and `~`.

If it exits 2, nothing is written:
- With `"error": "the main phase is not final: finish runs, scoring and labels first"`, the JSON lists `pending`, `not_scored`, `unlabeled` or `not_planned`. Finish Steps 14-17 for those items and rerun.
- With `"error": "main records were made under another freeze commit than the one the report checks against: stop and ask the user"`, the JSON gives `freeze`, the commit `require_freeze` returned, and `records`, each such job with its commit. STOP: the tag moved after those runs, so the report would render them as if they had been frozen with today's code.

If it raises `NotFrozen` with any message, STOP. Never move, delete or recreate the `evo-prereg` tag, and never pass `--prereg`; ask the user. See "Freeze refusals" in Step 14.

- [ ] **Step 19: Commit the report**

Copy the deviations:

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && if [ -f "$RUNS/deviations.md" ]; then cp "$RUNS/deviations.md" docs/evaluations/evocode-horizon/deviations.md; else printf '# Deviations\n\nNone. The main run followed the pre-registration.\n' > docs/evaluations/evocode-horizon/deviations.md; fi
```

Run the full suite before the commit:

Run: `cd "$WT" && sh bench/tb4/test.sh -q`
Expected: the last line is `1250 passed` followed by the run time, with no `skipped`, as at the freeze.

Commit. Each command runs only if the one before it succeeded:

```bash
WT="/Users/deeps/Documents/NEW STUFF/fun /.superpowers/worktrees/evocode"; RUNS="$HOME/Documents/evocode-horizon-runs"; REV=9fcae3e5539d1c0e85e2481fe06bd6af42cc4bc6
cd "$WT" && git add docs/evaluations/evocode-horizon/report.md docs/evaluations/evocode-horizon/deviations.md \
  && python3 -I "$RUNS/bin/contamination-check.py" "$RUNS/dataset/$REV/tasks" docs/evaluations/evocode-horizon \
  && ! grep -rn -e "$HOME" -e '/Users/' docs/evaluations/evocode-horizon/ \
  && python3 -m bench.tb4.leakcheck \
  && git commit -m "docs(evo): EvoCode-Bench first look report"
```

Expected:
- The contamination check prints `"hits"` of 0 for every file and exits 0.
- The host-path `grep` prints nothing. `report.md` writes host paths as `$RUNS`, `$WT` and `~`, but `deviations.md` is copied as written, so a hand-written line that broke the STOP rule shows here.
- `leakcheck` prints `no token in staged files`.
- The commit succeeds.

If the contamination check reports hits, or the host-path `grep` prints any line, nothing is committed. Run `git restore --staged docs/evaluations/evocode-horizon`, do not open the lines the contamination check lists, and STOP.

Run: `cd "$WT" && git show --stat --format=%s HEAD | head -20`
Expected: the subject `docs(evo): EvoCode-Bench first look report`, then exactly two files, `docs/evaluations/evocode-horizon/deviations.md` and `docs/evaluations/evocode-horizon/report.md`, and `2 files changed`.

Run: `cd "$WT" && python3 -m bench.evo.prereg check; echo "exit $?"`
Expected: `NotFrozen: docs/evaluations/evocode-horizon/report.md is committed: the main run is closed`, then `exit 1`. That lock is intended: no further main-phase job can run.

Then hand back to the user with:
- the primary result: mean d, its 95% interval, p, and wins, losses and ties;
- the per-task table's headline;
- the validity checks;
- any deviations, including drops and the reserves that replaced them;
- the path of `report.md`.

Offer to publish the report as a page. Do not push the branch or the tag; the user decides that.

