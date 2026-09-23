"""Shared command interface; each runtime skill supplies its own --runtime."""
import argparse
import json
import os
import re
import sys
from pathlib import Path

# Works through a skill-local symlink and from an unrelated project directory.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate
import goal
import paths
import refine_log
import tree_hash
import filelock
import task_state
import steps


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runtime", choices=("codex", "claude"), required=True)
    p.add_argument("--project", type=Path, help="Working project; defaults to the current directory")
    p.add_argument("--scope", choices=("project", "global"), default="project",
                   help="Refinement ledger scope; goal state is always project-local")
    commands = p.add_subparsers(dest="action", required=True)
    commands.add_parser("doctor", help="Check installation and report runtime paths without writing")
    status = commands.add_parser("status", help="List goals or read one goal before doing work")
    status.add_argument("slug", nargs="?")
    start = commands.add_parser("start", help="Create an explicitly requested bounded goal")
    start.add_argument("objective")
    criterion = start.add_mutually_exclusive_group(required=True)
    criterion.add_argument("--gate", help="Command whose exit status decides the command check")
    criterion.add_argument("--coverage", help="Criterion for an independent reviewer")
    start.add_argument("--review", help="Additional independent review required after the command passes")
    start.add_argument("--max-turns", type=int, default=20)
    start.add_argument("--max-minutes", type=int)
    start.add_argument("--watch", action="append", help="Artifact path relative to the project; repeatable")
    start.add_argument("--execution", choices=("conversation", "state"), default="conversation")
    check = commands.add_parser("check", help="Run verification and checkpoint one completed increment")
    check.add_argument("slug")
    check.add_argument("--did", required=True)
    check.add_argument("--next", required=True, dest="next_action")
    check.add_argument("--timeout", type=int, default=gate.DEFAULT_TIMEOUT)
    check.add_argument("--force", action="store_true", help="Rerun a command whose inputs are external")
    review = commands.add_parser("review", help="Record a fresh reviewer's verdict for the printed snapshot")
    review.add_argument("slug")
    review.add_argument("--result", choices=("pass", "fail"), required=True)
    review.add_argument("--judge", required=True)
    review.add_argument("--hash", required=True, dest="digest")
    review.add_argument("--evidence-file", type=Path, required=True)
    complete = commands.add_parser("complete", help="Complete only after all checks pass on current artifacts")
    complete.add_argument("slug")
    abandon = commands.add_parser("abandon")
    abandon.add_argument("slug")
    abandon.add_argument("--reason", required=True)
    reopen = commands.add_parser("reopen")
    reopen.add_argument("slug")
    reopen.add_argument("--reason", required=True)
    reopen.add_argument("--extra-turns", type=int)
    reopen.add_argument("--extra-minutes", type=int)
    begin = commands.add_parser("step-begin", help="Reserve one state-mode worker attempt")
    begin.add_argument("slug")
    begin.add_argument("--request-id", required=True)
    attach = commands.add_parser("step-attach", help="Bind a real host worker handle")
    attach.add_argument("slug")
    attach.add_argument("--attempt", required=True)
    attach.add_argument("--worker", required=True)
    attach.add_argument("--expected-revision", required=True, type=int)
    finish = commands.add_parser("step-finish", help="Finish a bound state-mode worker attempt")
    finish.add_argument("slug")
    finish.add_argument("--attempt", required=True)
    finish.add_argument("--expected-revision", required=True, type=int)
    finish.add_argument("--update-file", type=Path, required=True)
    finish.add_argument("--worker-evidence-file", type=Path, required=True)
    finish.add_argument("--timeout", type=int, default=gate.DEFAULT_TIMEOUT)
    finish.add_argument("--force", action="store_true")
    recover = commands.add_parser("step-recover", help="Recover an interrupted state-mode worker attempt")
    recover.add_argument("slug")
    recover.add_argument("--attempt", required=True)
    recover.add_argument("--expected-revision", required=True, type=int)
    recover.add_argument("--reason", required=True)
    recover.add_argument("--worker-state", choices=("stopped", "not-started"), required=True)
    recover.add_argument("--evidence-file", type=Path, required=True)
    commands.add_parser("refine-count", help="Read the rolling edit count before proposing")
    commands.add_parser("refine-history", help="Show refinements and later outcome observations")
    apply = commands.add_parser("refine-apply", help="Apply exactly the approved JSON proposal")
    apply.add_argument("proposal", type=Path)
    apply.add_argument("--approved", action="store_true", required=True)
    apply.add_argument("--override-cap", action="store_true")
    undo = commands.add_parser("refine-undo", help="Undo the latest or specified refinement")
    undo.add_argument("--entry")
    undo.add_argument("--force", action="store_true")
    undo.add_argument("--approved", action="store_true")
    outcome = commands.add_parser("refine-outcome", help="Record an observed later result; does not edit a lesson")
    outcome.add_argument("entry")
    outcome.add_argument("--outcome", choices=("helpful", "ineffective", "harmful"), required=True)
    outcome.add_argument("--task", required=True)
    outcome.add_argument("--evidence-file", type=Path, required=True)
    recall = commands.add_parser("recall", help="Read the memory index or retrieve relevant project notes")
    recall.add_argument("--query", default="")
    recall.add_argument("--limit", type=int, default=5)
    return p


def status(state: dict) -> dict:
    return goal.summary(state)


def entry_summary(entry: dict) -> dict:
    result = {key: entry.get(key) for key in
              ("id", "at", "layer", "rationale", "reverted", "outcomes")}
    result["targets"] = [target["path"] if isinstance(target, dict) else target
                         for target in entry["targets"]]
    return result


def recall(query: str, limit: int) -> dict:
    if limit < 1 or limit > 20:
        raise ValueError("Recall limit must be between 1 and 20")
    directory = paths.memory_dir()
    index = directory / "MEMORY.md"
    report = {"directory": str(directory),
              "index": index.read_text()[:6000] if index.exists() else "",
              "notes": []}
    terms = re.findall(r"\w+", query.lower())
    if terms:
        ranked = []
        for note in sorted(directory.glob("*.md")):
            if note == index:
                continue
            content = note.read_text()
            score = sum(term in (note.stem + " " + content).lower() for term in terms)
            if score:
                ranked.append((score, note, content))
        ranked.sort(key=lambda item: (-item[0], str(item[1])))
        report["notes"] = [{"path": str(note), "text": content[:2000]}
                           for _, note, content in ranked[:limit]]
    return report


def apply_proposal(args) -> dict:
    proposal = json.loads(args.proposal.read_text())
    edits = []
    for item in proposal["edits"]:
        kind = item.get("kind", "text")
        if kind == "text":
            content = item["content"]
        elif kind == "append_line":
            content = refine_log.AppendLine(item["content"])
        elif kind == "replace_line":
            content = refine_log.ReplaceLine(item["match"], item["content"])
        else:
            raise ValueError(f"Unknown edit kind: {kind}")
        target = Path(item["path"]).expanduser()
        if not target.is_absolute():
            raise ValueError("Proposal target paths must be absolute")
        edits.append((target, content))
    entry = refine_log.apply_edit(
        proposal["layer"], edits, proposal["rationale"], proposal["evidence"],
        override_cap=args.override_cap, scope=args.scope)
    return entry_summary(entry)


def check(args) -> tuple[dict, int]:
    state = goal.load(args.slug)
    if goal.execution_mode(state) == "state":
        raise goal.GoalStateError(
            "state-mode goals use step commands; legacy check is unavailable"
        )
    if state["status"] != "active":
        raise goal.GoalClosed(f"Goal is already {state['status']}")
    turns = state["budget"].get("max_turns")
    if turns is not None and state["used"]["turns"] >= turns:
        raise goal.BudgetExhausted("Turn budget exhausted; no new increment may start.")
    if args.timeout <= 0:
        raise ValueError("Timeout must be positive")
    evaluated = dict(state, checkpoints=[]) if args.force else state
    error = None
    try:
        result, output, digest = gate.run_gate_at(evaluated, timeout=args.timeout)
    except gate.CoverageGate as exc:
        result, output, digest = (
            "fail", "Awaiting independent coverage review", exc.tree_hash
        )
    except gate.GateUnrunnable as exc:
        result, output, digest = "fail", str(exc), gate.UNKNOWN_HASH
        error = str(exc)
    if digest == gate.UNKNOWN_HASH and error is None:
        error = "Watched inputs could not be fingerprinted; verification is incomplete."
        result, output = "fail", error + "\n" + output
    state = goal.checkpoint(
        args.slug, args.did, args.next_action, digest, result, output
    )
    report = status(state)
    report["review_required"] = not error and (
        state["gate"]["kind"] == "coverage"
        or (result == "pass" and bool(state["gate"].get("review_criterion")))
    )
    if error:
        report["error"] = error
    return report, (2 if error else 0 if result == "pass" else 1)

def _step_error(args, error: Exception) -> dict:
    """Translate a failed state-mode command without inventing an outcome."""
    report = {
        "error": type(error).__name__,
        "message": str(error),
        "update_accepted": False,
    }
    slug = getattr(args, "slug", None)
    attempt_id = getattr(args, "request_id", None) or getattr(args, "attempt", None)
    if slug is not None:
        report["slug"] = slug
    if attempt_id is not None:
        report["attempt_id"] = attempt_id
    if hasattr(args, "expected_revision"):
        report["expected_revision"] = args.expected_revision
    if slug is None:
        return report
    try:
        state = goal.load(slug)
    except (goal.GoalStateError, OSError, ValueError):
        return report
    if goal.execution_mode(state) != "state":
        return report
    report["execution_revision"] = state["execution"]["revision"]
    attempt = next(
        (item for item in state["execution"]["attempts"] if item["id"] == attempt_id),
        None,
    )
    if attempt is not None:
        report["attempt_status"] = attempt["status"]
    return report


def _dispatch_step(args) -> tuple[dict, int]:
    try:
        if args.action == "step-begin":
            report = steps.begin(args.slug, args.request_id)
        elif args.action == "step-attach":
            report = steps.attach(
                args.slug, args.attempt, args.worker,
                expected_revision=args.expected_revision,
            )
        elif args.action == "step-finish":
            report = steps.finish(
                args.slug, args.attempt, expected_revision=args.expected_revision,
                update_file=args.update_file, worker_evidence_file=args.worker_evidence_file,
                timeout=args.timeout, force=args.force,
            )
        else:
            report = steps.recover(
                args.slug, args.attempt, expected_revision=args.expected_revision,
                reason=args.reason, worker_state=args.worker_state,
                evidence_file=args.evidence_file,
            )
    except (goal.GoalStateError, task_state.StateError, OSError, ValueError) as error:
        return _step_error(args, error), 2
    return report, report["exit_code"]


def dispatch(args) -> tuple[dict, int]:
    if args.action == "doctor":
        return {
            "runtime": paths.runtime(),
            "project": str(paths.project_root()),
            "implementation": str(Path(__file__).resolve().parent),
            "state": str(paths.harness_root()),
            "targets": paths.targets(args.scope),
            "memory_mode": "explicit recall" if paths.runtime() == "codex" else "Claude project memory",
            "state_supported": filelock.state_supported(),
            "worker_availability": "Determined by the host; the Python CLI does not dispatch or inspect workers.",
        }, 0
    if args.action == "status":
        if args.slug:
            return status(goal.load(args.slug)), 0
        directory = paths.harness_root() / "goals"
        return {"goals": [status(goal.load(p.stem)) for p in sorted(directory.glob("*.json"))]}, 0
    if args.action == "start":
        if args.coverage and args.review:
            raise ValueError("Use --coverage alone, or --gate together with --review")
        state = goal.create(
            args.objective, str(paths.project_root()),
            "coverage" if args.coverage else "command", args.coverage or args.gate,
            max_turns=args.max_turns, max_minutes=args.max_minutes,
            watch_paths=args.watch, review_criterion=args.review,
            execution=args.execution,
        )
        return status(state), 0
    if args.action in {"step-begin", "step-attach", "step-finish", "step-recover"}:
        return _dispatch_step(args)
    if args.action == "check":
        return check(args)
    if args.action == "review":
        state = goal.record_review(args.slug, args.result, args.evidence_file.read_text(),
                                   args.judge, args.digest)
        return status(state), 0 if args.result == "pass" else 1
    if args.action == "complete":
        return status(goal.complete(args.slug, "")), 0
    if args.action == "abandon":
        return status(goal.abandon(args.slug, args.reason)), 0
    if args.action == "reopen":
        return status(goal.reopen(args.slug, args.reason, extra_turns=args.extra_turns,
                                  extra_minutes=args.extra_minutes)), 0
    if args.action == "refine-count":
        return {"used": refine_log.count_recent_edits(scope=args.scope), "cap": refine_log.CAP,
                "window_hours": refine_log.CAP_WINDOW_HOURS, "scope": args.scope,
                "targets": paths.targets(args.scope)}, 0
    if args.action == "refine-history":
        return {"entries": [entry_summary(e) for e in refine_log.list_entries(scope=args.scope)]}, 0
    if args.action == "refine-apply":
        return apply_proposal(args), 0
    if args.action == "refine-undo":
        if args.force and not args.approved:
            raise ValueError("Forced undo requires explicit approval to discard later changes")
        return entry_summary(refine_log.undo(force=args.force, entry_id=args.entry, scope=args.scope)), 0
    if args.action == "refine-outcome":
        entry = refine_log.record_outcome(args.entry, args.outcome, args.task,
                                          args.evidence_file.read_text(), scope=args.scope)
        return entry_summary(entry), 0
    if args.action == "recall":
        return recall(args.query, args.limit), 0
    raise ValueError(f"Unknown action: {args.action}")


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    os.environ["CONTINUAL_HARNESS_RUNTIME"] = args.runtime
    try:
        if args.project:
            os.chdir(args.project.expanduser().resolve())
        result, code = dispatch(args)
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
