---
name: horizon
description: Use for explicitly requested bounded work that must resume across turns or sessions, such as "keep working until the tests pass". Saves progress and verifies completion against a command or review criterion.
---

# Horizon for Claude Code

Keep the objective, evidence, budget, and next action on disk using the shared CLI.
Resolve `HARNESS` to the absolute `scripts/harness.py` beside the **SKILL.md
you actually loaded**, even for a global installation. Set `PROJECT` to the
working project's absolute path. Quote both paths.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" doctor
python3 "$HARNESS" --runtime claude --project "$PROJECT" recall --query "relevant task terms"
python3 "$HARNESS" --runtime claude --project "$PROJECT" status
```

Reuse a matching active goal. Read its last checkpoint and raw artifacts before
resuming. Create one when the user requests bounded continuation or invokes
`/horizon`. Derive a concrete finish condition from the request.
For code or mixed tasks, use a command plus a semantic review criterion:

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" start "Requested outcome" \
  --gate "actual verification command" \
  --review "Observable requirements the command cannot establish" \
  --max-turns 20 --max-minutes 60
```

Use `--coverage "criterion"` instead of `--gate` for research or other artifact
review. A purely mechanical task may use a command alone. Choose proportional
budgets; user limits take precedence over the example. Save the returned slug.
State belongs to this runtime's project `.claude/harness`.

## Each increment

1. Read `status SLUG` before work. Continue only when status is active and
   `budget_exhausted` is false. Inspect the recorded next action.
2. Do one useful increment. Preserve the acceptance criteria.
3. Run this command **once** to verify and save the increment. Read its JSON even
   on a nonzero exit. Work that crosses the wall-clock deadline is still recorded.

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" check "$SLUG" \
  --did "Concrete change and evidence" --next "Next useful action"
```

A command pass does not complete the goal. Coverage checks initially record an
awaiting-review result. Exit codes: 0 command pass, 1 failed/skipped/awaiting
review, 2 error. Continue from the saved next action while budget remains.

## Independent review

When `review_required` is true, dispatch a **fresh independent reviewer**.
Use Claude Code's available `Agent` tool with a fresh
`subagent_type: "general-purpose"` invocation.
Give it the objective, acceptance criterion, artifact paths, and raw check output.
Request pass/fail with evidence and omissions. Do not supply a desired verdict or
substitute your own explanation. If no reviewer is available, report the unmet
requirement.

Save the actual review (at least 40 characters) in a temporary file **outside the
watched project**. Use the real reviewer handle and the exact
`last_checkpoint.tree_hash` printed by `check`:

```bash
python3 "$HARNESS" --runtime claude --project "$PROJECT" review "$SLUG" \
  --result pass --judge "$JUDGE_HANDLE" --hash "$CHECKPOINT_HASH" \
  --evidence-file "$REVIEW_FILE"
python3 "$HARNESS" --runtime claude --project "$PROJECT" complete "$SLUG"
```

Replace `pass` with the actual verdict. Complete only after all configured checks
pass. Missing/failed review, changed artifacts, a changed gate, or an unreadable
snapshot blocks completion. Check and review again after any change. The ledger
records reviewer provenance; it cannot prove independence by itself.

## Limits and resumption

Stop new increments at the budget and report remaining gaps. `abandon` closes
incomplete work; `reopen` may extend its budget only with user authorization.
A skipped command needs changed inputs or `check --force` for external state.
Missing tools, unreadable inputs, and timeouts retain a failed checkpoint:
diagnose the cause, never fabricate a pass. Use `--timeout SECONDS` for a slow gate.
Read [the CLI reference](references/commands.md) for watch paths, lifecycle
commands, and upgrade rules.

Disk checkpoints survive interruption; they do not wake the application.
Only when the user requests scheduled continuation, use `/loop` for an open
Claude Code session or an available Desktop scheduled task for later local
sessions. Cloud routines use a separate environment. Read status on every firing
and stop on completion or exhaustion.
Report final status, evidence, remaining gaps, and the slug.
