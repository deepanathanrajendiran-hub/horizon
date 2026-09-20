# Runtime separation: verification results

Verified on 2026-09-05 in the existing checkout. The user's pre-existing
`.claude/harness/CHANGELOG.md` changes were preserved. Personal installations
were not modified.

## Automated verification

- Baseline before implementation: **277 tests passed**. Direct probes still
  reproduced broken Codex startup, stale completion, unprotected Codex targets,
  and checkpoint-only changes causing another gate execution.
- Final suite: **306 tests passed** in 21.45 seconds.
- Four skill files passed the skill-creator frontmatter validator.
- Both runtimes and both skill entrypoints start in fresh Python processes.
  Paths with spaces and a trailing space are covered.
- The old tests executing copied Python from Markdown were replaced by tests
  of the real CLI and parsing its documented command examples. Core state,
  concurrency, gate, tree-hash, and refinement tests remain.

The machine's default Python lacked pytest on its search path. The final run
used the already-cached pytest environment without installing runtime packages:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
PYTHONPATH=/Users/deeps/.cache/uv/archive-v0/044nI_HLHtvNp2AOxHKcK/lib/python3.14/site-packages \
python3 -m pytest -q -p no:cacheprovider --tb=short
```

For a normal development environment with pytest installed, use
`python3 -m pytest -q`.

Regression coverage includes current-content completion, missing/failed/stale
reviews, changing gate definitions, unchanged-input skips, forced checks,
missing executables, timeout/retry, unreadable snapshots, budget exhaustion,
checkpointing after a wall-clock deadline, runtime/global routing, memory recall,
refinement apply/history/outcomes/undo, and case aliases/custom Codex homes.

The full suite exposed an existing concurrency test that replaced a module's
state-path function without restoring it. The fixture now uses pytest's
monkeypatch restoration; concurrency assertions were retained.

## Independent review

A fresh reviewer found five additional defects: case aliases of shared source,
custom Codex-home protection, history's target representation, FIFO hashing, and
checkpoint loss across a wall-clock deadline. Each was reproduced before its
fix. The reviewer verified the fixes with isolated reproductions and reported no
new actionable findings. A final CLI check also makes an unknown fingerprint
an explicit verification error even when the shell command passes.

## Agent behavior exercises

**Horizon, Codex:** A fresh agent read the installed project skill in an isolated
temporary project. It produced a deployment handoff checklist, checkpointed a
coverage goal, dispatched a separate reviewer, recorded the actual verdict for
the exact checkpoint hash, and completed using **1 of 3 increments**. The saved
goal state was independently read back and confirmed complete. Review evidence
was kept outside the watched project.

**Refine, Codex:** A separate exercise used two supplied errors from this session
involving Python source embedded in JavaScript. The agent proposed a focused
memory note and index link with evidence and a later evaluation task. It did not
apply the proposal: count remained **0**, history stayed empty, and both target
files remained absent. Inspection created only empty ledger directories, now
documented in the CLI reference. No benefit was claimed without a later trial.

The smoke tests wrote only temporary artifacts. Claude's CLI flows, routing, and
skill examples were tested, but a live Claude Code Agent/scheduling session was
not exercised. No scheduling was requested or configured. These correctness
checks do not establish a productivity advantage; see the
[evaluation protocol](evaluation.md) for that experiment.
