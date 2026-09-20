# Separate runtime skills, shared harness

The user approved the review's improvements and requested separate Codex and
Claude Code experiences. Implementation stays in this checkout to include the
existing untracked Codex skills; the user's existing changelog edits are retained.

## Design

- `.agents/skills/{horizon,refine}`: Codex instructions and entrypoints.
- `.claude/skills/{horizon,refine}`: Claude Code instructions and entrypoints.
- `harness/`: one standard-library Python implementation, explicitly selected
  with `--runtime codex` or `--runtime claude`. The previous Claude module path
  remains a compatibility link.
- Runtime state stays separate: `.codex/harness` versus `.claude/harness`.
  Existing Claude state is not migrated or rewritten.
- Claude memory routing retains its existing backend. Codex uses explicit,
  skill-managed project memory with a documented recall command, without
  claiming that Codex automatically loads a Claude-style memory directory.
- Completion requires a current content fingerprint and all configured checks.
  Command-plus-review goals retain separate results for the same snapshot.
  A review record is provenance supplied by the caller, not proof of independence.
- Mutable harness records do not invalidate task fingerprints. Source changes,
  including harness source changes, still do.
- Refinement outcomes can be recorded against existing entries. Approval to
  improve these skills is already supplied; future learning edits retain their
  proposal/approval/undo contract.

## Execution and verification

- [x] Add regression tests for complete skill startup, runtime state separation,
  protected paths, stale completion, review-required completion, and self-induced
  gate reruns. Observe failures against the old implementation.
- [x] Extract the shared modules; implement explicit runtime paths and preserve
  old Claude imports. Verify project, nested-directory, and global paths.
- [x] Implement current-snapshot completion and optional independent review;
  verify failed, missing, stale, and successful verdicts.
- [x] Add a small CLI for setup, create/status/check/review/complete, refinement
  apply/undo/outcomes, and explicit recall. Run it in fresh processes.
- [x] Replace copied orchestration blocks with separate runtime instructions and
  entrypoints. Test both skill folders, including paths containing spaces.
- [x] Add outcome recording and a repeatable evaluation protocol; report measured
  results separately from proposed future experiments.
- [x] Run the full regression suite, realistic isolated skill exercises, review the
  diff, and update usage/installation documentation with observed results.

The initial review ran 277 existing tests successfully. It also reproduced both
Codex setup failures, stale completion with a failing current gate, unprotected
Codex paths, and checkpoint-only changes causing another gate execution.

Final result: 306 tests passed, four skill validations passed, and isolated Codex
horizon/refine behavior exercises completed. See the
[verification report](../../runtime-separation-results.md) for evidence and limits.
