# Horizon state mode: design for coding and refactoring

Date: 2026-09-19. Status: proposed design, ready for written review.

## Decision and intended benefit

Add an optional `state` execution mode to Horizon. Keep one shared Python implementation and separate Codex and Claude Code skills that explain how to use their host's worker tools. Start with coding and refactoring tasks whose results can be checked with commands and, where needed, independent review.

Each worker receives a compact task packet, performs one useful increment, and returns a proposed state update. The coordinator validates that update and runs the existing acceptance checks. The next worker starts with a fresh conversation and the updated packet.

The intended benefit is more reliable handoffs: preserve decisions, failed approaches, evidence, and the next action without requiring every worker to reread the conversation. Reduced context cost is a hypothesis to measure, not a promised result.

The user agreed to develop this direction and to start with coding/refactoring. This document specifies the design for review; implementation follows a separate implementation plan.

## Evidence and alternatives

Current Horizon already persists objectives, budgets, checkpoints, fingerprints, and reviews. Its checkpoint descriptions are free text; it does not reconstruct a structured worker input or require a fresh worker for each increment. The [Sol/Terra evaluation](../../evaluations/2026-09-06-sol-terra/README.md) demonstrated a successful fresh-context checkpoint handoff and budget enforcement. It did not establish a productivity or token-cost advantage, and did not test interruption during a write.

[SKILL.state](https://arxiv.org/html/2608.26263v2#S3) supplies each model step with a procedure, structured state, and the latest observation instead of accumulated conversation. Its runtime validates proposed state changes. Our design borrows that separation, but uses native workers for meaningful increments. The coordinator retains its host conversation, and workers may accumulate context while using tools. We cannot claim the paper's whole-workflow context behavior or benchmark savings. The paper also identifies schema limits and loss of observations whose relevance becomes apparent later. [Limitations](https://arxiv.org/html/2608.26263v2#S7).

| Approach | Benefit | Cost or limitation |
| --- | --- | --- |
| **Optional state mode with fresh native workers — recommended** | Uses the existing CLI, skills, gates, and host tools; tests whether explicit handoffs help | Controls the task packet, not all context supplied by the host |
| Richer snapshots in the same conversation | Smallest change; easier recovery | Does not isolate workers from accumulated conversation |
| Standalone runtime that constructs every model request | Closest control to the paper's execution model | Adds model API configuration, billing, orchestration, and maintenance |

V1 needs no new model API keys, service, scheduler, or automatic memory system. It supports one active worker per goal. Parallel editing, automatic schema generation, research-specific schemas, and automatic promotion of task state into Refine lessons are outside this change.

## Components and ownership

| Component | Responsibility |
| --- | --- |
| Coordinator skill | Confirm the authorized goal and limits, inspect state, launch and track one worker, assess its proposed update, invoke verification, and report the result |
| Runtime-specific instructions | Explain fresh worker creation and handle tracking using tools actually available in Codex or Claude Code |
| Shared task-state module | Validate the state schema and update operations; construct and size-check the worker packet; label stale evidence |
| Shared step module | Reserve attempts, bind worker handles, accept results, checkpoint, and recover interrupted attempts |
| Existing goal/gate modules | Remain authoritative for budget, acceptance criteria, watched artifacts, review, and completion |

The CLI does not invoke model tools. In Codex, the coordinator uses a new `spawn_agent` with `fork_turns: "none"` for each increment. The Claude skill uses a fresh supported `Agent` invocation, with native behavior verified during rollout. A worker receives the packet and artifact references, not a copied coordinator transcript. Model selection follows user preferences and host availability; the design does not hard-code a model.

Workers can inspect and edit authorized project artifacts and run task tools. Only the coordinator drives the goal lifecycle. A worker returns evidence and a proposed update; it cannot establish completion or change the objective, gate, review requirement, budget, or watch paths through that update. These are workflow rules and validated interfaces, not a security boundary against a process that can directly edit local files.

If fresh workers are unavailable, report that state mode cannot run on that host. Do not silently claim a same-conversation continuation is fresh. The user can choose a separate ordinary Horizon goal using the existing mode.

## Persistent state and worker packet

`start --execution state` opts in. Omission means `conversation`, the existing behavior. Goals without an execution field remain conversation goals. Mode is fixed at creation; v1 does not convert an active legacy goal.

Keep the current goal JSON as the authoritative record. Add a versioned execution block containing the current revision, compact task state, current attempt, and attempt audit records. Store supporting files beneath `goals/<slug>.d/` in the selected runtime's harness directory. That location is already within the bookkeeping area excluded from artifact fingerprints; verify both the tree walk and Git status exclusions.

The active task state contains:

| Field | Content |
| --- | --- |
| `summary` | Brief description of the current situation; at most 2 KiB of UTF-8 text |
| `entries` | At most 64 entries, each with a stable ID, kind, statement, and evidence references |
| `next_action` | One concrete increment to perform next; at most 1 KiB |

Entry kinds are `observation`, `hypothesis`, `failed_approach`, `question`, and `artifact`. Statements are at most 1 KiB each, with at most four evidence references per entry. File references identify project-relative paths and expected SHA-256 digests; resolved paths must remain inside the project, including after following symlinks. Copy diagnostic evidence into the attempt's evidence directory before referencing it. The harness checks references, records provenance, and stamps accepted entries with the attempt and artifact snapshot. Invalid paths, missing required evidence, duplicate IDs, and unknown fields are errors. An observation or failed approach requires evidence; an untested explanation belongs under hypothesis.

The worker packet contains the objective, unchanged acceptance criteria, remaining limits, a short worker procedure, current state, artifact references, the latest checkpoint result, and any applicable review outcome. It identifies the skill/procedure version, attempt ID, revision, and current artifact fingerprint. The initial packet explicitly asks the first worker to inspect the project and establish task knowledge.

The **entire serialized packet is limited to 24 KiB of UTF-8**, including its fixed fields. This is a starting limit for evaluation, not a token estimate. Full acceptance criteria must fit: reject an oversized initial packet with the measured size rather than truncating the objective. Checkpoint and review excerpts each have a 2 KiB serialized allowance; validate updates with that space reserved so a later verdict cannot overflow the packet. Large evidence stays in referenced files, with visibly labeled excerpts in the packet. Missing history or missing evidence is reported explicitly.

Never silently drop active entries to fit the limit. Return a field-specific size error so the coordinator can shorten a summary or explicitly archive entries with a reason. The complete prior values and reasons remain in the audit record. Archive retrieval is explicit and selective; it does not automatically inject all old attempts into every prompt.

### Evidence freshness and truth

Runtime validation proves structure and reference consistency, not that a statement follows from its evidence. The coordinator checks the plausibility of additions and the reasons for removals before submission. Acceptance tests and independent review remain necessary.

At packet construction, compare references and the recorded snapshot with the current project. Changed or missing evidence is labeled stale. Code-derived observations and failed approaches from a different watched snapshot are historical evidence requiring rechecking, even if one referenced file is unchanged. Preserve them with that label rather than treating them as current facts or deleting them automatically. This conservative rule can create extra rechecking; evaluate that cost.

Only the active packet is bounded. Disk history may grow, and on-demand evidence reads, tool outputs, system instructions, and the coordinator's own conversation add context beyond that packet. Native transcripts remain in their host when available; the harness does not promise to export every internal tool interaction.

## Update contract

The worker returns an update document with the attempt ID, base revision, a concrete work summary, and explicit operations: set the summary or next action, upsert an entry by ID, or archive an entry by ID with a reason. Omission preserves an existing field; `null` does not mean deletion. Reject duplicate or contradictory operations against one entry.

The document may not modify lifecycle fields, acceptance criteria, budget, watch paths, or runtime-generated verification records. Validate the complete candidate state and packet before acceptance. Reject malformed JSON, files over 64 KiB, unknown schema versions, wrong attempt or revision, invalid evidence, and oversized results without partially applying operations.

On acceptance, save the state change, checkpoint, attempt outcome, and audit of replaced/archived values in one atomic goal-file replacement. Evidence files are prepared first; an unreferenced file left by a crash is not committed state. The audit retains the supplied packet, proposed update, accepted changes, worker handle, and verification evidence supplied to the harness. It is append-only through the CLI, not tamper-proof storage.

## One increment, from reservation to checkpoint

Expose four additive step commands; their full argument syntax will be fixed in the implementation plan.

1. **`step-begin`** takes a coordinator-generated request ID. Prepare and validate the packet outside the goal lock, then recheck the execution revision, active status, open attempt, and remaining budget under the lock. Reserve one attempt, increment `used.turns`, and persist the packet and its digest before returning it. Repeating the same request ID returns its saved reservation and current outcome without another charge or permission to launch another worker, including when that attempt is already terminal.
2. The coordinator launches one fresh worker with that packet, then uses **`step-attach`** to record the actual host handle. One handle can be bound to that attempt; a conflicting rebind is rejected and repeating the same binding is idempotent. Resumption with a saved handle inspects or waits for that worker instead of starting a duplicate.
3. Once the worker has stopped, the coordinator reviews its result and calls **`step-finish`** with the attempt ID, expected current execution revision, and update file. Check the worker's base revision against the saved packet, validate the candidate, persist a finishing marker, run the gate outside the state lock, then atomically accept the update and checkpoint. Failed tests can still accompany a useful accepted state update; only the gate controls the checkpoint verdict. The checkpoint uses the reserved turn number, so it does not charge a second turn.
4. The attempt is now closed. If required, dispatch a fresh independent reviewer using the existing review contract. Call `complete` only when all configured checks pass for the current artifacts; otherwise begin the next increment if budget remains.

`step-finish` follows existing check exit semantics: 0 for a command pass, 1 for failed/skipped/awaiting review, 2 for an error. Its JSON states whether it accepted an update and closed the attempt. A gate execution error closes the attempt with failed verification, while a rejected update leaves the attempt open for correction or recovery. A replay of an already committed finish returns its saved result without rerunning the command; a different update for that finished attempt is rejected.

The existing `check` command refuses state-mode goals and directs the caller to the step protocol. `review` and `complete` refuse an open attempt. Coverage-only goals retain their existing independent-review requirement; coding goals may use command-only or command plus review as today. Work requiring verification of external inputs retains the explicit force option; do not retry skipped checks silently.

### Budget semantics

In state mode, `max_turns` counts **reserved worker attempts**, including crashes and launch failures. In conversation mode it retains its current checkpoint semantics. Status and documentation display the distinction. Do not refund a reservation automatically: avoiding a free retry loop is more important than recovering one attempt lost before dispatch.

An already reserved attempt may save its outcome after crossing the time or turn limit. No new worker may start then. Recording/recovering that attempt and obtaining a required final review remain permissible, but cannot be used to perform another editing increment. A fresh replacement worker requires another reservation. Allow at most one request to the same worker to repair update formatting, with no additional implementation work; otherwise close it as incomplete. Explicit user authorization remains required to extend a goal's limits.

## Interruption, revision checks, and locking

`step-recover` closes an interrupted attempt after the coordinator establishes that its worker and any gate process have stopped. It records the reason, observed artifacts, and a failed recovery checkpoint; keeps the reserved turn charged; preserves prior entries and summary; and sets the next action to inspect the current workspace before continuing. It cannot turn an uncertain result into a pass. Calling it again for that recovered attempt returns the saved outcome.

| Failure | Required behavior |
| --- | --- |
| Crash after reservation but before dispatch | Recover the unused reservation after confirming no worker launched; the turn remains charged |
| Crash between dispatch and saving a worker or gate handle | Consult host/process records; if liveness cannot be established, report the unresolved attempt and do not launch duplicate work |
| Worker stops after modifying files but before returning an update | Preserve files, recover the attempt, then have the next worker inspect the actual diff and stale state |
| Invalid, oversized, or stale update | Preserve committed state and files; return a precise error; use the bounded repair or recovery path |
| Crash while the gate is running or after it returns but before commit | Treat the result as uncertain, stop/wait for the recorded gate process, and recover; do not claim exactly-once command execution or replay it automatically |
| Crash after the atomic commit but before the CLI response | Replayed finish returns the committed result |
| Missing/corrupt goal file or unknown schema version | Stop with a diagnostic; do not recreate a blank goal or guess missing state |
| Missing evidence or changed artifacts | Mark historical state stale; existing completion fingerprint checks continue to reject obsolete verification |
| Lock unavailable | Fail without mutating state; do not continue unlocked |

Use a monotonically increasing execution revision for state-mode lifecycle changes. Distinguish the immutable packet's `base_revision` from the record's current execution revision: binding a handle advances the latter without rewriting the packet supplied to the worker. Finish checks both values against their respective saved records. No other operation may edit active task state while an attempt is open. Persist the finishing revision and update digest; the final commit must match that revision. A second finish while the first is running reports in progress and cannot launch another gate. Accepted/recovered attempts are terminal, and their IDs cannot be reused for new work. A changed procedure version requires explicit recovery and a new packet, rather than silently changing an active worker's contract.

The existing lock helper can fall back to unlocked writes on permission errors and can reclaim locks by age. Those behaviors are unsuitable for attempt reservation. Add a strict process-owned lock for all goal writes on supported local macOS/Linux filesystems, using one consistent locking protocol for creation and every mutation of the same goal. It must fail closed, release on process death, and never reclaim a live holder merely because time elapsed. Keep model calls, hashing, and command execution outside critical sections; recheck revision before committing. Refine's unrelated locking behavior is outside this change. Other platforms cannot opt into state mode until an equivalent locking implementation is tested.

No filesystem transaction covers both a worker's source edits and its JSON checkpoint. Recovery therefore inspects artifacts; it never blindly reruns edits or rolls back user changes. Similarly, the goal lock does not lock the working tree against unrelated editors. Existing snapshot verification detects staleness, while logical merge decisions remain with the coordinator.

`abandon` must not close a goal with a live worker or gate. Stop the work and recover its open attempt first. Reopening remains an explicit lifecycle operation and never changes execution mode or erases attempt history.

## Compatibility and scope of edits

Keep both runtime skill entry points and their shared script/reference links. Add concise mode selection and handoff instructions, with detailed protocol documentation in the shared CLI reference. Do not install or overwrite global skills as part of this project change.

Likely implementation locations are `harness/task_state.py` for validation/packet construction and `harness/steps.py` for attempt lifecycle, with focused integration in `harness/cli.py`, `harness/goal.py`, and the goal-lock path in `harness/filelock.py`. Existing gate and fingerprint rules remain the completion authority. All state-mode goal writers must participate in the same revision/lock protocol; old public mutation functions must not provide a bypass.

Legacy goal files, command names, runtime paths, budget meanings, and existing regression cases remain supported. Tightening goal-write locking on supported platforms is intentional. No automatic migration, cross-runtime state copying, or shared global goal store is introduced.

Two earlier findings remain separate: Refine's decoded-content preview and storing exploratory reviews before the command gate passes. State mode may retain an exploratory review as labeled task evidence, but it does not change whether that review satisfies completion. The independent review contract remains unchanged.

## Validation and rollout

Implement in three reviewable stages:

1. **Durable state and attempts:** schema, packet limits, update validation, strict locking, budgets, replay behavior, and recovery, exercised through the actual CLI in temporary projects.
2. **Worker integration:** update the two skills and shared documentation; run a real Codex handoff and a native Claude Code handoff. If native Claude is unavailable, report that limitation and keep its state-mode support experimental.
3. **Comparative evaluation:** run the same coding fixtures with ordinary Horizon and state mode, then decide whether the feature earns broader use. Keep it opt-in regardless of the first pilot's outcome.

Required automated cases include two processes competing for one reservation, reused request IDs, interrupted writes, stale revisions, gate-process interruption, budget exhaustion before dispatch, a finish after the deadline, duplicate finish/recovery, invalid and oversized patches, explicit archives, stale/missing evidence, unknown schema, and the inability of updates to change acceptance criteria. Exercise legacy and state-mode completion with changed artifacts and required review. Rerun the existing regression suite at integration; the previously recorded 306 passes are historical evidence, not a current validation claim.

For the behavioral pilot, use Sol and Terra as previously requested, comparing each model with itself. Use six coding fixtures, three repetitions per model/mode: 72 goal runs in total, in isolated workspaces. Cover a bug spanning modules, API refactoring, test repair, checkpoint handoff, interruption during work, and a misleading/stale fact. Freeze three fixtures as held-out evaluation cases before tuning on the other three. Use identical starting artifacts, acceptance tests, review criteria, model settings, and declared turn/time limits across each pair; alternate run order. Report the modes' different turn semantics and failed launches instead of concealing them. This compares the combined mode change; it does not isolate the contribution of fresh workers from the contribution of structured state. A short adapter smoke test precedes this pilot.

Measure acceptance-gate success, false completion, regressions, repeated work, recovered handoffs, state omissions, stale-fact reliance, worker launches, repair attempts, elapsed time, and packet bytes. Record total model tokens/cost only when the host exposes trustworthy usage covering coordinator, workers, and reviewers; otherwise mark unavailable. Do not substitute packet bytes or character counts for billed token usage. Preserve raw outcomes, fixture versions, skill/core fingerprints, and deviations.

Release criteria are: all required protocol/regression checks pass; zero observed false completions or automatic budget extensions; all deliberately invalid updates are rejected without partial state mutation; fresh workers demonstrably resume from the saved packet; and runtime claims match the hosts actually tested. The pilot must also report whether task success or efficiency improved, stayed similar, or worsened. A small positive pilot justifies continued opt-in use, not a general performance claim or automatic default change.

## Design review checklist

- The packet limit applies to supplied task context, not all model context.
- Tests and independent review still decide completion.
- Failed attempts consume budget, and crash uncertainty cannot produce a pass.
- State changes preserve evidence and history while making omissions explicit.
- Codex and Claude use a shared protocol with host-specific worker instructions.
- Implementation and a larger evaluation follow the written design review.
