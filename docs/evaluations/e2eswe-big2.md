# E2E-SWE big2 run: results (Horizon exp/thin16-fixes 5b34414 against plain, 31 tasks × 2, 2026-11-14)

This is the run's record, copied from the benchmark runner's repository. "thin17" is Horizon at commit 5b34414, the
code this repository holds, and "plain" is Claude Code without it. "The big run" is the earlier same-day run of
2026-11-10 (Horizon thin3 against plain, on the same 31 tasks). The files named below are kept with the runner.

Plan: `plan.md`, frozen at tag `e2eswe-big2-5b34414` (commit e1846de) before any build ran.

The run had 124 builds in 10 waves of whole pairs, from 04:30 to 11:53 (7 hours 23 minutes):
- plain and thin17 (Horizon 5b34414);
- two builds each of the 31 headroom tasks.

All 124 finals and 1,029 session snapshots were graded with 0 grader errors.

## Primary (as fixed in the plan)

| | thin17 | plain | Result |
|---|---|---|---|
| Mean share, 31 tasks | **86.9** | 76.7 | |
| **1. Mean d, thin17 − plain** | | | **+10.2**, 95% CI +2.3 to +21.5, one-sided p 0.013: **helped** |
| Stop-session d | | | +13.2, p 0.006: helped (agrees) |
| **2. Collapsed builds** (0 at the last session) | **2 of 62** | 10 of 62 | one-sided Fisher p 0.015: **fewer collapses** |

**Both co-primary verdicts are positive. By the plan's definition, thin17 beats plain on this run.** thin17 was ahead by
more than 2 points on 14 tasks and behind on 5. Wilcoxon on task means: p 0.021.

**One grade was changed before the analysis.** It is reported here with what it does to the verdict.
- inline-snapshot's grading is not stable:
  - thin17-32b's final snapshot graded 0 of 26 in the 4-worker final pass.
  - It graded 26 of 26 alone (the `diag_exceptions.py` regrade), 26 in a second full pass made one build at a time,
    and then 26, 25 and 26.
  - plain-32b graded 26, 25, 26, 26 and 26.
- The second full pass matched the first on the other 122 builds.
- For the two builds whose passes disagreed, the median of their grades is used: thin17-32b 26, and plain-32b 26
  (unchanged).
  - The first pass is kept in `regrade-finals-parallel.json`, the second in `regrade-finals-seq.json`, and the repeats
    in `regrade-finals-repeat.json`.
- **With the original grade (thin17-32b at 0) both verdicts still hold:** d +8.6 (95% CI −0.5 to +19.9, p 0.044,
  helped) and collapses 3 against 10 (p 0.038).

## Checks before any score

- **Smoke:** `smoke-plain` 17 of 17, `smoke-thin17` 19 of 19.
- **Trace probe:** `close` reached, no background reviewer.
- **Memory guard:** 2 kills, both of a plain mpmath build's own runaway command. plain-46a session 6 was at 23.5 GB
  and plain-46b session 2 at 5.3 GB.
- **Writes outside the sandbox:**
  - `host_writes.py`: 0 after every wave.
  - `write_scan.py`: 1 Write. thin17-6b session 8 wrote a 1-byte file to `/tmp/placeholder` through
    `builds/thin17-6b/../../../../tmp`, the Write-tool escape the big run's plain-83a showed. It was moved to
    `~/e2eswe-runs/big2/host-writes/`.
- **Leak triage** (`leak-triage.out`, written before any score): no read of the hidden tests, of another build or of
  an installed copy of a build's target. No task dropped.
  - plain-46b tried Homebrew Python's mpmath as a reference; the deny rule blocked it and the import failed.
  - plain-77b searched `~/e2eswe-prep` and the denied `~/deepswe` for SAPP sources and found none.
  - plain-32a searched the builds folder for other builds' `setup.sh`; the sandbox returned none.
  - The other flags are builds packaging their own work, or own-venv paths written through another folder.

## Also reported

| Comparison (last session) | Tasks | Mean d | 95% CI | p (greater) | Wilcoxon p (greater) |
|---|---|---|---|---|---|
| thin17 − plain, 21 tried since the big run | 21 | +13.3 | +2.1 to +28.9 | 0.018 | 0.020 |
| thin17 − plain, the other 10 | 10 | +3.5 | −3.0 to +66.3 | 0.34 | 0.42 |
| thin17 − plain, the big run's dev 15 | 15 | +22.2 | +9.3 to +43.2 | 0.003 | 0.007 |
| thin17 − plain, the big run's other 16 | 16 | −1.2 | −11.9 to +4.6 | 0.59 | 0.41 |
| **thin17 − plain, normal tasks** (no build at 0 in either arm) | 22 | **+1.0** | −1.5 to +5.7 | 0.30 | |

- **The lead comes from collapses.** On the 22 tasks where neither arm had a build at 0, thin17 and plain are level.
  The big run's thin3 was −1.7 there.
- **The 10 tasks not tried since the big run:** thin17 96.8 and plain 93.3 (plain 99.1 in the big run). They are near
  the ceiling. In the big run thin3 was behind on them (−2.6); here it is +3.5, n.s.

**Collapsed builds:**

| Arm | Build | Task | What the grading image shows |
|---|---|---|---|
| plain | 19a, 19b | fiddle | no `setup.sh` |
| plain | 46a | mpmath | no `setup.sh` |
| plain | 46b | mpmath | `setup.sh` runs but installs nothing importable |
| plain | 51b | pdfminer | `setup.sh` fails (exit 1) |
| plain | 64a | pysmt | `setup.sh` fails (exit 1) |
| plain | 4a, 17b, 42b, 57b | barf, dnachisel, mapillary_tools, protobuf-to-pydantic | installs; an import the hidden tests make fails at collection |
| thin17 | 15a | django-modelcluster | installs; a TypeError stops the run before any test |
| thin17 | 46a | mpmath | installs; an import the hidden tests make fails at collection |

Six of plain's 10 are install failures, which Horizon's install gate exists to catch. thin17 had none.

**Builds at 0 by session** (sessions 1-10):
- thin17: 23, 14, 7, 4, 3, 2, 2, 2, 2, 2.
- plain: 40, 28, 21, 17, 14, 13, 11, 10, 10, 10.

**Mean share by session:**
- thin17: 52.0, 66.4, 76.5, 82.9, 84.5, 86.0, 86.6, 86.8, 86.7, 86.9.
- plain: 28.1, 45.3, 55.2, 62.5, 69.4, 71.6, 74.6, 76.2, 76.3, 76.7.

### Other days (described only)

| Comparison | Tasks | Mean d | p (greater) | p (less) |
|---|---|---|---|---|
| this plain − the big run's plain | 31 | −6.7 | 0.99 | **0.006** |
| thin17 − the big run's thin3 | 31 | −0.2 | 0.52 | 0.48 |
| the big run's thin3 − its plain (for reference) | 31 | +3.7 | 0.20 | 0.80 |
| thin17 − a461068 (thin4 and thin4rest, one build a task) | 31 | −3.9 | 0.96 | 0.035 |
| thin17 − 9519263 (thin16 run, one build) on its 5 tasks | 5 | +1.0 | 0.38 | 0.69 |

**Plain had a worse day than in the big run**: 76.7 against 83.4, with 10 collapses against 5. thin17 scored the same
as the big run's thin3 (86.9 against 87.1). The same-day design is what the verdict rests on. Across days, this
version is level with thin3 and below a461068's one-build run (90.8).

### Per task (mean of two builds; the builds in brackets)

| Task | thin17 | plain | d | big thin3 | big plain |
|---|---|---|---|---|---|
| authlib | 93.4 [93.4, 93.4] | 66.4 [61.8, 71.1] | +27.0 | 80.3 | 56.6 |
| barf | 60.9 [43.5, 78.3] | 34.8 [0, 69.6] | +26.1 | 58.7 | 54.3 |
| cattrs | 90.6 [100, 81.2] | 100 [100, 100] | −9.4 | 93.8 | 100 |
| cement | 99.5 [100, 99.0] | 99.5 [99.0, 100] | 0 | 99.0 | 100 |
| claripy | 85.0 [88.3, 81.7] | 82.5 [86.7, 78.3] | +2.5 | 80.8 | 82.5 |
| django-modelcluster | 44.0 [0, 87.9] | 93.4 [95.6, 91.2] | **−49.4** | 91.2 | 93.4 |
| dnachisel | 85.7 [88.6, 82.9] | 45.7 [91.4, 0] | +40.0 | 90.0 | 94.3 |
| fiddle | 100 [100, 100] | 0 [0, 0] | **+100** | 97.4 | 0 |
| grappa | 91.7 [91.7, 91.7] | 95.8 [95.8, 95.8] | −4.1 | 87.5 | 97.9 |
| hojichar | 100 [100, 100] | 99.4 [98.8, 100] | +0.6 | 100 | 99.4 |
| huey | 93.4 [92.5, 94.3] | 92.5 [92.5, 92.5] | +0.9 | 94.3 | 96.2 |
| icontract | 96.9 [96.9, 96.9] | 98.4 [100, 96.9] | −1.5 | 98.4 | 100 |
| inline-snapshot | 100 [100, 100] | 100 [100, 100] | 0 | 84.6 | 100 |
| invoke | 98.7 [98.7, 98.7] | 95.6 [100, 91.1] | +3.1 | 94.3 | 100 |
| kafka-python | 62.3 [59.0, 65.6] | 63.1 [63.9, 62.3] | −0.8 | 63.1 | 62.3 |
| lark | 100 [100, 100] | 93.8 [100, 87.5] | +6.2 | 100 | 90.6 |
| ldaptor | 99.6 [100, 99.1] | 100 [100, 100] | −0.4 | 100 | 99.6 |
| mapillary_tools | 75.9 [74.1, 77.8] | 38.9 [77.8, 0] | +37.0 | 75.9 | 76.9 |
| mpmath | 26.8 [0, 53.6] | 0 [0, 0] | +26.8 | 30.4 | 0 |
| pdfminer | 92.4 [84.8, 100] | 50.0 [100, 0] | +42.4 | 92.4 | 98.5 |
| pgqueuer | 95.2 [95.2, 95.2] | 100 [100, 100] | −4.8 | 100 | 97.6 |
| pingtop | 97.9 [97.9, 97.9] | 97.9 [97.9, 97.9] | 0 | 96.8 | 96.8 |
| protobuf-to-pydantic | 66.7 [73.8, 59.5] | 29.8 [59.5, 0] | +36.9 | 71.4 | 67.9 |
| pyrsistent | 98.0 [98.0, 98.0] | 98.0 [98.0, 98.0] | 0 | 96.9 | 99.0 |
| pysmt | 83.3 [88.9, 77.8] | 50.0 [0, 100] | +33.3 | 81.5 | 46.3 |
| pytest-bdd | 94.0 [92.0, 96.0] | 94.0 [88.0, 100] | 0 | 94.0 | 98.0 |
| sapp | 80.8 [80.8, 80.8] | 90.4 [80.8, 100] | −9.6 | 73.1 | 90.4 |
| transitions | 89.4 [83.0, 95.7] | 89.4 [91.5, 87.2] | 0 | 86.2 | 96.8 |
| tremolo | 98.5 [97.0, 100] | 100 [100, 100] | −1.5 | 98.5 | 100 |
| websockets | 97.1 [100, 94.3] | 85.7 [94.3, 77.1] | +11.4 | 97.1 | 97.1 |
| wrapt | 95.3 [95.3, 95.3] | 93.0 [95.3, 90.7] | +2.3 | 91.9 | 93.0 |

thin17's largest loss is django-modelcluster: build a scored 0 (a TypeError before any test), and build b scored 87.9.

### thin17's mechanisms (`fixes.py`, the goal states, `mechanisms.py`, `checks.py`)

- **Request check:** asked in 58 of 62 builds, at 2 to 11 parts (median 5).
  - 42 completed with every part run; 16 were open at the cap; 4 builds were never asked.
  - No goal completed with parts left.
  - No goal listed a heading under two files (`parts_doubled` 0), and no session start showed a part as "from line N".
- **Not-saved line:** in 40 builds (54 session starts).
- **Missing setup script:** the write-it line showed in 8 builds; no build recorded an install fact.
- **Thin goals:** 62 of 62 builds had one, with the spec's examples and the surface test in the gate folder. 42
  completed.
- **First gate call:** sessions ran their own gate in 344 of 421 later sessions, at call 9 at the median.

### Cost

| | thin17 | plain | big run thin3 | big run plain |
|---|---|---|---|---|
| Sessions | 483 | 546 | 370 | 570 |
| Model calls | 7,517 | 8,706 | 5,688 | 9,080 |
| Input tokens (mostly cache reads) | 407M | 496M | 311M | 524M |
| Builds stopped early | 38 | 12 | 48 | 12 |

thin17 used 18% fewer tokens than plain, and 31% more than the big run's thin3. In all the run was 903M, against the
plan's estimate of 0.85-1.05B.

## What this says

1. **thin17 beats plain on this run, by the plan's rule:** mean d +10.2 (p 0.013), fewer collapses (2 against 10,
   p 0.015), and the stop-session d agrees. The verdict holds with or without the one regraded build.
2. **The win is collapses, not better work on normal tasks.**
   - On the 22 tasks with no build at 0, the arms are level (+1.0).
   - Six of plain's 10 zeros are failed or missing setup scripts, which Horizon's install gate catches; thin17 had
     none.
   - On its normal tasks the big run's thin3 was −1.7; thin17 is +1.0.
3. **Plain had a bad day.** It was 6.7 below the big run's plain (p 0.006), with twice the collapses. thin17 matched
   the big run's thin3.
   - With the big run's plain, thin17's d would be about +3.5. That is about what thin3 got (+3.7, n.s.).
   - Same-day pairing is the design, so the verdict stands. But how big the win is depends on how often plain
     collapses, and that moves from day to day.
4. **Limits:**
   - These are the development tasks: 21 of the 31 were read from since the big run.
   - On the 10 that were not, the arms are level (+3.5, n.s.).
   - A claim of a general win still needs held-out tasks.

## Files

`~/e2eswe-runs/big2/`:
- `summary.json`, `analyze.out`, `fixes.out`, `fixes.json`, `mechanisms.out`, `checks.out`
- `leak-audit.out`, `leak-audit.json`, `leak-triage.out`, `leak-dropped.json`, `outside-reads.out`, `write-scan.out`,
  `host-writes-*.out`, `host-writes/`
- `regrade-finals.json` (used), `regrade-finals-parallel.json`, `regrade-finals-seq.json`,
  `regrade-finals-repeat.json`, `regrade-finals-changes.json`, `regrade-sessions.json`, `grade-finals.out`,
  `grade-finals-seq.out`, `grade-sessions.out`
- `diag-exceptions.out`, `gate-in-image.jsonl`, `memory-guard.log`, `waves.log`, `wave*.out`, `smoke.out`, `trace.out`
