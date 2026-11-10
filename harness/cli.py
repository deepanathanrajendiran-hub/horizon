"""Shared command interface; each runtime skill supplies its own --runtime."""
import argparse
import json
import os
import sys
from pathlib import Path

# Works through a skill-local symlink and from an unrelated project directory.
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import gate
import goal
import paths
import tree_hash
import filelock
import task_state
import steps
import reviewing
import evidence
import hooks
from datetime import datetime
import surface
import examples
import install_gate


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--runtime", choices=("codex", "claude"), required=True)
    p.add_argument("--project", type=Path, help="Working project; defaults to the current directory")
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
    start.add_argument("--gate-path", help="Directory holding request.md, requirements.md and the gate; watched automatically")
    start.add_argument("--timeout", type=int, default=gate.DEFAULT_TIMEOUT, help="Seconds for the baseline gate run")
    start.add_argument("--surface-spec", type=Path, help="Also write $GATE_PATH/test_surface.py from this spec first (see `surface`)")
    start.add_argument("--package", help="With --surface-spec: the package's import name, such as jwt")
    surface_cmd = commands.add_parser("surface", help="Write a pytest gate of the public names a spec gives for a Python package")
    surface_cmd.add_argument("--spec", type=Path, required=True)
    surface_cmd.add_argument("--package", required=True, help="Top-level import name, such as jwt")
    surface_cmd.add_argument("--out", type=Path, required=True, help="Test file to write, usually $GATES/test_surface.py")
    examples_cmd = commands.add_parser("examples", help="Run the spec's own examples (advisory) and list where the "
                                       "library disagrees")
    examples_cmd.add_argument("slug")
    examples_cmd.add_argument("--timeout", type=int, default=300)
    waive = commands.add_parser("waive-example", help="Skip one spec example that is wrong in the spec itself")
    waive.add_argument("slug")
    waive.add_argument("item")
    waive.add_argument("--reason", required=True, help="Cite the spec line that makes the example wrong")
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
    observe = commands.add_parser("observe", help="Run the gate now and record it; charges no turn")
    observe.add_argument("slug")
    observe.add_argument("--role", choices=("baseline", "resume", "final"), default="resume")
    observe.add_argument("--timeout", type=int, default=gate.DEFAULT_TIMEOUT)
    observe.add_argument("--next", dest="next_action", help="Also record the next action")
    note = commands.add_parser("note", help="Record the next action, a finding or a fact now; runs nothing and "
                               "charges no turn")
    note.add_argument("slug")
    note.add_argument("--next", dest="next_action")
    note.add_argument("--finding", help="A bug seen but not fixed yet; shown at every session start until cleared")
    note.add_argument("--fact", help="An environment fact that is not a code bug; shown at every session start")
    note.add_argument("--clear-findings", action="store_true", help="Drop the open findings once they are fixed")
    close = commands.add_parser("close", help="Complete when evidence allows, else report what is missing")
    close.add_argument("slug")
    close.add_argument("--if-ready", action="store_true", help="Never run the gate; complete or finalize only")
    close.add_argument("--timeout", type=int, default=gate.DEFAULT_TIMEOUT)
    approve = commands.add_parser("approve-test-change", help="Record a reviewer's or the user's approval of a changed baseline test")
    approve.add_argument("slug")
    approve.add_argument("--path", required=True)
    approve.add_argument("--reason", required=True)
    reclaim = commands.add_parser("step-reclaim", help="Close an attempt whose lease went stale")
    reclaim.add_argument("slug")
    reclaim.add_argument("--attempt", required=True)
    reclaim.add_argument("--expected-revision", required=True, type=int)
    reclaim.add_argument("--session")
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
    hook = commands.add_parser("hook", help="Run a Claude Code hook; reads the event JSON on stdin and always exits 0")
    hook.add_argument("event", choices=("SessionStart", "PostToolUse", "PostToolUseFailure", "Stop"))
    commands.add_parser("install-hooks", help="Add Horizon's hooks to .claude/settings.json")
    commands.add_parser("uninstall-hooks", help="Remove only Horizon's hooks from .claude/settings.json")
    return p


def status(state: dict) -> dict:
    return goal.summary(state)

def examples_record(state: dict) -> Path:
    """Where a goal's last spec-example result is kept: beside its notes, which the checkpoint never hashes."""
    return goal.notes_path(state["slug"]).with_suffix(".examples.json")

def _run_examples(state: dict, timeout: int = 60) -> dict | None:
    """Run the goal's advisory spec examples with the project's own interpreter, or None without them."""
    gate_path = state.get("gate_path")
    if not gate_path or not (Path(gate_path) / examples.FILE).is_file():
        return None
    python = examples.python_for(state["gate"]["value"] if state["gate"]["kind"] == "command" else None)
    try:
        key = tree_hash.compute(state["cwd"], state.get("watch_paths"))
    except Exception:
        key = None
    return examples.run(Path(gate_path), Path(state["cwd"]), python=python, timeout=timeout,
                        record=examples_record(state), key=key)


def _with_examples(report: dict, state: dict) -> dict:
    """Add the spec-example summary, and the names the surface gate finds missing, to a check or close report. Both
    are reported here; only `close` acts on the surface gate."""
    try:
        line = examples.summary_line(_run_examples(state))
    except Exception:
        line = ""
    if line:
        report["spec_examples"] = line
    try:
        key = tree_hash.compute(state["cwd"], state.get("watch_paths"))
        found = goal.surface_check(goal.load(state["slug"]), key)
    except Exception:
        key, found = None, None
    if found and found["failed"] and "surface_advisory" not in report:
        report["surface_missing"] = (surface.grouped(found["failed"]) + ". `close` waits for these: a test that "
                                     "imports a missing one fails, whatever else works.")
    try:
        installed = goal.install_check(goal.load(state["slug"]), key)
    except Exception:
        installed = None
    if installed and not installed.get("ok") and not installed.get("error") and "install_advisory" not in report:
        report["install_problem"] = (install_gate.problem(installed) + ". `close` waits for it: until the package "
                                     "installs, every test that imports it fails.")
    try:  # `check` says it before `close` holds on it
        fresh = goal.load(state["slug"])
        last = (fresh.get("checkpoints") or [None])[-1]
        small = (goal.small_gate(fresh, last.get("gate_output") or "")
                 if last and last.get("gate") == "pass" and fresh["status"] == "active" and "missing" not in report
                 else None)
    except Exception:
        small = None
    if small:
        report["small_gate"] = small
    try:  # as with the small gate: said at `check`, held at `close`
        unbuilt = goal.placeholders(goal.load(state["slug"])) if "missing" not in report else None
    except Exception:
        unbuilt = None
    if unbuilt:
        report["placeholders"] = unbuilt
    return report


def goal_rows(exclude: str | None = None) -> list[dict]:
    directory = paths.harness_root() / "goals"
    rows = []
    for path in sorted(directory.glob("*.json")):
        if path.name.endswith(".leases.json"):
            continue  # a side file of leases, not a goal
        if path.stem == exclude:
            continue
        # `start` lists siblings after saving the new goal; one broken file
        # must not hide the new slug and invite a duplicate retry.
        try:
            rows.append(goal.row(goal.load(path.stem)))
        except (goal.GoalStateError, KeyError, TypeError, ValueError) as exc:
            rows.append({"slug": path.stem, "error": f"unreadable goal: {exc}"})
    return rows








def _review_handoff(report: dict, state: dict, digest: str, issue: bool | str = False) -> None:
    """Hand out the review brief. With `issue` (True from `close`, "check" from `check`), the round is recorded as
    handed out; a brief no reviewer took up (its reply file is not on disk) is given again, and which hand-outs count
    is `reviewing._handouts_used`. If a round
    never comes back, a later session's `close` hands it out again, up to `reviewing.handouts_per_goal()`; after
    that `close` sends no more and completes only on a passing review (E2E-SWE paper run: an unanswered round
    counted as spent closed 79 goals on the agent's own gate), unless the runtime gives the session's call budget:
    then a session no reviewer has worked in may hand out a fresh brief (`reviewing.fresh_brief_allowed`)."""
    review_file = reviewing.next_review_file(
        state["slug"], digest, len(state.get("reviews") or []) + 1,
        watch_paths=state.get("watch_paths") or [state["cwd"]])
    try:
        requirements = goal._requirements(state) or {}
    except Exception:
        requirements = {}
    try:
        since = datetime.fromisoformat(state["used"]["started"]).timestamp()
    except (KeyError, TypeError, ValueError):
        since = 0.0
    # A FAIL that a cut-off reviewer wrote only to its file: record it, then fix it, before anyone
    # reviews these files again.
    failed = reviewing.failed_rounds(state["slug"], digest, requirements, review_file, since)
    if failed:
        report["review_failed_lines"] = [line for line, _ in failed]
        report["review_failed_files"] = list(dict.fromkeys(path for _, path in failed))
        report.setdefault("missing", []).append(
            "A reviewer of these files found FAIL lines: " + "; ".join(report["review_failed_lines"])
            + ". Record each file in review_failed_files with `review --result fail` (as the evidence file), "
            "then fix those lines and run check.")
        return
    if reviewing.review_spent(state):
        report["review_spent"] = ("This goal has had its review round; close completes on a passing gate without "
                                  "another, listing what went unreviewed.")
        return
    if reviewing.review_pending(state):
        # E2E-SWE paper run, pytermgui: told a reviewer was still writing, the agent left the work unchanged for two
        # sessions so as not to move "the snapshot the reviewer is judging".
        report["review_pending"] = ("A reviewer may still be writing its reply for this goal. Keep building and "
                                    "edit the work as it needs: never leave it unchanged for the reviewer, and "
                                    "never wait for it. Record its reply with `review` if it arrives; its FAIL "
                                    "lines still apply to the files it judged.")
        return
    if reviewing.review_unavailable(state):
        report["review_unavailable"] = (
            ("No reviewer answered this goal's review briefs, and close hands out no more in this session: start "
             "the next session with `close`, which hands out a fresh brief while a reviewer has the calls to "
             "finish. " if reviewing.budget_tracked() else
             "No reviewer answered this goal's review briefs, and close hands out no more. ")
            + "It will not complete this goal without a passing review: keep building and extend the gate toward "
            "what the request still lacks. When the budget is spent, close records the current files as the "
            "deliverable.")
        report.setdefault("missing", []).append(report["review_unavailable"])
        return
    # Lines an earlier reviewer of this same checkpoint passed before it was cut off.
    carried, carried_from = reviewing.carried(state["slug"], digest, requirements, review_file)
    rounds = reviewing.round_count(state["slug"], digest, review_file, since)
    if rounds >= reviewing.REVIEW_ROUNDS_PER_CHECKPOINT and not set(requirements) <= set(carried):
        report["review_paused"] = (
            f"Review paused: these files have had {rounds} review rounds without completing. Go back to "
            "building: extend the gate toward what the request still lacks, fix what it finds, then check. "
            "Changed files start review again. If every part of the request is already built and checked, "
            "stop and give the final report, listing the lines no reviewer passed as unverified.")
        report.setdefault("missing", []).append(report["review_paused"])
        return
    short = reviewing.calls_short()
    if short:
        # E2E-SWE full run: reviewers sent with 9 or more of a session's 15 calls used came back 4 times in 61; the
        # dead rounds then held the goal ("may still be writing", then `review_unavailable`).
        left, budget = short
        report["review_deferred"] = (
            f"No reviewer now: this session has about {left} of its {budget} calls left and a reviewer needs about "
            f"{reviewing.REVIEW_CALLS}, so it would be cut off with the session, as nearly every reviewer sent this "
            "late has been. No hand-out is used. Keep building and run `check` before the session ends; start the "
            "next session with `close`: it hands the brief out first, while a reviewer has the calls to finish.")
        report.setdefault("missing", []).append(report["review_deferred"])
        try:
            goal.defer_review(state["slug"])
        except Exception:
            pass  # only the next session's reminder is lost
        return
    report["review_file"] = review_file
    report["review_carried"], report["review_carried_from"] = carried, carried_from
    # After a cut-off round, a smaller batch of the open lines; smaller again after a round that decided none.
    report["review_scope"] = reviewing.review_scope(
        requirements, report["review_carried"],
        reviewing.batch_size(reviewing.decided_counts(state["slug"], report["review_file"], since)))
    report["review_brief"] = reviewing.brief_for(
        state, digest, report["review_file"], str(paths.harness_root()),
        carried=report["review_carried"], scope=report["review_scope"])
    if issue:
        goal.issue_review(state["slug"], digest, review_file, via_check=issue == "check")


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
        or (result == "pass"
            and bool(evidence.review_reasons(state, goal._requirements(state))))
    )
    if report["review_required"]:
        # As in `close`: no reviewer while a [gate] line has no check naming it (a reply would spend the round on
        # a gate that is about to change).
        unchecked = goal.unchecked_gate_lines(state)
        if unchecked:
            report["unchecked_gate_lines"] = unchecked
        else:
            # recorded too, so a reviewer that took it up is pending; it counts only once taken up
            _review_handoff(report, state, digest, issue="check")
    if error:
        report["error"] = error
    _with_examples(report, state)
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
        elif args.action == "step-reclaim":
            report = steps.reclaim(
                args.slug, args.attempt, expected_revision=args.expected_revision,
                session=args.session,
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

def _auto_install_hooks(entrypoint: str) -> dict:
    """start installs the Claude Code hooks unless the user ran uninstall-hooks.

    A failure is reported, never raised: the goal already exists.
    """
    if hooks.opted_out():
        return {}
    try:
        if hooks.install(paths.project_root(), entrypoint):
            return {"hooks": "installed"}
    except (hooks.HookInstallError, OSError) as exc:
        return {"hooks_error": str(exc)}
    return {}


def dispatch(args) -> tuple[dict, int]:
    if args.action == "surface":
        names = surface.write_gate(args.spec, args.package, args.out)
        return {"names": names, "out": str(args.out),
                "next": "Run it with your gate; a failure means the library lacks that name"}, 0
    if args.action == "doctor":
        return {
            "runtime": paths.runtime(),
            "project": str(paths.project_root()),
            "implementation": str(Path(__file__).resolve().parent),
            "state": str(paths.harness_root()),
            "gates": str(paths.harness_root() / "gates"),
            "state_supported": filelock.state_supported(),
            "worker_availability": "Determined by the host; the Python CLI does not dispatch or inspect workers.",
        }, 0
    if args.action == "status":
        if args.slug:
            return goal.summary(goal.load(args.slug), compact=True), 0
        return {"goals": goal_rows()}, 0
    if args.action == "start":
        if args.coverage and args.review:
            raise ValueError("Use --coverage alone, or --gate together with --review")
        state = goal.create(
            args.objective, str(paths.project_root()),
            "coverage" if args.coverage else "command", args.coverage or args.gate,
            max_turns=args.max_turns, max_minutes=args.max_minutes,
            watch_paths=args.watch, review_criterion=args.review,
            execution=args.execution, gate_path=args.gate_path,
        )
        # Hooks first: writing .claude/settings.json after the baseline would
        # leave the baseline describing files that no longer exist.
        installed = _auto_install_hooks(args.entrypoint) if paths.runtime() == "claude" else {}
        goal.ensure_notes(state["slug"])
        if state["gate"]["kind"] == "command":
            state = goal.observe(state["slug"], "baseline", timeout=args.timeout)
        report = status(state)
        report["observation"] = goal.latest_observation(state)
        report["other_goals"] = goal_rows(exclude=state["slug"])
        report.update(installed)
        if getattr(args, "earlier_request", None):
            report["earlier_request"] = args.earlier_request
        if getattr(args, "spec_examples", None) is not None:
            report["spec_examples"] = args.spec_examples
        if getattr(args, "surface_gate", None):
            report["surface_gate"] = {**args.surface_gate, "note": (
                "Horizon wrote this file from the spec: one test per module and public name it gives. `close` runs "
                "it with your gate's Python and completes only once every name imports.")}
        return report, 0
    if args.action == "examples":
        result = _run_examples(goal.load(args.slug), timeout=args.timeout)
        if result is None:
            raise ValueError("This goal has no spec examples: start it with --surface-spec and --gate-path")
        return {"summary": examples.summary_line(result) or "Spec examples: none ran.", "result": result}, 0
    if args.action == "waive-example":
        state = goal.load(args.slug)
        if not state.get("gate_path"):
            raise ValueError("This goal has no gate directory, so no spec examples")
        return {"waived": examples.waive(Path(state["gate_path"]), args.item, args.reason)}, 0
    if args.action == "install-hooks":
        changed = hooks.install(paths.project_root(), args.entrypoint)
        hooks.set_opted_out(False)
        return {"changed": changed}, 0
    if args.action == "uninstall-hooks":
        changed = hooks.uninstall(paths.project_root())
        hooks.set_opted_out(True)
        return {"changed": changed}, 0
    if args.action in {"step-begin", "step-attach", "step-finish", "step-recover", "step-reclaim"}:
        return _dispatch_step(args)
    if args.action == "check":
        return check(args)
    if args.action == "review":
        state = goal.record_review(args.slug, args.result, args.evidence_file.read_text(),
                                   args.judge, args.digest)
        return status(state), 0 if args.result == "pass" else 1
    if args.action == "observe":
        state = goal.observe(args.slug, args.role, timeout=args.timeout, next_action=args.next_action)
        report = status(state)
        report["observation"] = goal.latest_observation(state)
        return report, 0 if report["observation"]["gate"] == "pass" else 1
    if args.action == "note":
        return status(goal.note(args.slug, args.next_action, finding=args.finding, fact=args.fact,
                                clear_findings=args.clear_findings)), 0
    if args.action == "close":
        result = goal.close(args.slug, run_gate=not args.if_ready, timeout=args.timeout)
        if not result["complete"]:
            state = goal.load(args.slug)
            try:
                digest = tree_hash.compute(state["cwd"], state.get("watch_paths"))
                needs_review = (state["status"] == "active" and bool(
                    evidence.review_reasons(state, goal._requirements(state))))
            except Exception:
                # Unfingerprintable files or unreadable requirements: `missing`
                # already says so, and there is no snapshot to hand a reviewer.
                digest, needs_review = None, False
            # A reviewer is sent only once the requirement lines cover the request, and the gate checks every
            # [gate] line (E2E-SWE paper run: a brief spent on a gate that lacks checks is a hand-out lost).
            if (needs_review and evidence.passing_record(state, digest)
                    and not goal.differing_sections(state) and not goal.unchecked_gate_lines(state)):
                _review_handoff(result, state, digest, issue=True)
            _with_examples(result, state)
        return result, 0 if result["complete"] else 1
    if args.action == "approve-test-change":
        return status(goal.approve_test_change(args.slug, args.path, args.reason)), 0
    if args.action == "complete":
        return status(goal.complete(args.slug, "")), 0
    if args.action == "abandon":
        return status(goal.abandon(args.slug, args.reason)), 0
    if args.action == "reopen":
        return status(goal.reopen(args.slug, args.reason, extra_turns=args.extra_turns,
                                  extra_minutes=args.extra_minutes)), 0
    raise ValueError(f"Unknown action: {args.action}")


EARLIER_REQUEST = "## Earlier request for this project (kept by Horizon)"


def _carry_request(args) -> None:
    """`start` of a thin goal in a project that already had one: keep the first goal's request in the new
    request.md, below what the agent wrote, unless the new text already holds it. The surface gate and the spec
    examples are then still written from the spec the first request names.

    E2E-SWE thin run, mpmath: after one goal completed, the next one's request.md read "Remaining placeholder:
    lambertw in mpmath/_elem.py", and `close` completed that goal while no hidden test could be collected."""
    if args.action != "start" or not args.gate_path:
        return
    try:
        new = Path(args.gate_path).resolve()
        target = new / "request.md"
        if not target.is_file() or (new / "requirements.md").exists():
            return
        first = None
        for path in paths.goals_dir().glob("*.json"):
            if path.name.endswith(".leases.json"):
                continue
            try:
                state = json.loads(path.read_text())
                earlier = Path(state["gate_path"]).resolve()
                started = str((state.get("used") or {}).get("started") or "")
                text = (earlier / "request.md").read_text(errors="replace").split(EARLIER_REQUEST)[0].strip()
            except (OSError, ValueError, KeyError, TypeError):
                continue
            if text and earlier != new and (first is None or started < first[0]):
                first = (started, state.get("slug") or path.stem, text)
        if first is None:
            return
        written = target.read_text(errors="replace")
        if " ".join(first[2].split()) in " ".join(written.split()):
            return
        target.write_text(written.rstrip("\n") + f"\n\n{EARLIER_REQUEST}\n\n{first[2]}\n")
        args.earlier_request = (f"Horizon added the request of this project's first goal ({first[1]}) to "
                                f"{target}: this goal is done only when that request is met too.")
    except Exception:
        pass

def _start_surface(args) -> None:
    """`start`: write the surface gate into the gate directory before the baseline runs. With `--surface-spec`, from
    that spec; otherwise from the spec the request names, when it gives a Python package's names (exp/surface-check:
    no build passed the flag, and two scored 0 on a module their spec named). A file already there is kept."""
    _carry_request(args)
    if args.action != "start":
        return
    if args.surface_spec is None:
        if not args.gate_path:
            return
        target = Path(args.gate_path) / surface.FILE
        try:  # a request that names no spec, or a spec for no Python package, gets no surface gate
            found = None if target.exists() else surface.find_spec(args.gate_path, paths.project_root())
            if found:
                spec, package = found
                count = surface.write_gate(spec, package, target)
                args.surface_gate = {"spec": str(spec), "package": package, "names": count, "file": str(target)}
        except Exception:
            pass
        # The spec's own examples too, advisory (E2E-SWE full and retry runs: no build of 77 passed --surface-spec,
        # so the one check that does not come from the agent's own reading of the spec never ran).
        try:
            examples_file = Path(args.gate_path) / examples.FILE
            found = None if examples_file.exists() else surface.find_spec(args.gate_path, paths.project_root())
            if found:
                args.spec_examples = examples.write_gate(found[0], found[1], examples_file)
        except Exception:
            pass
        return
    if not args.gate_path or not args.package:
        raise ValueError("--surface-spec needs --gate-path and --package")
    surface.write_gate(args.surface_spec, args.package, Path(args.gate_path) / "test_surface.py")
    try:  # advisory: a failure here never stops the goal
        args.spec_examples = examples.write_gate(args.surface_spec, args.package,
                                                 Path(args.gate_path) / examples.FILE)
    except Exception as exc:
        args.spec_examples = {"error": f"{type(exc).__name__}: {exc}"}

def main(argv=None) -> int:
    args = parser().parse_args(argv)
    os.environ["CONTINUAL_HARNESS_RUNTIME"] = args.runtime
    args.entrypoint = os.path.abspath(sys.argv[0])
    if args.action == "hook":
        # Hooks never block or fail the host session, whatever they are fed.
        try:
            if args.project:
                os.chdir(args.project.expanduser().resolve())
            raw = sys.stdin.read()
            payload = json.loads(raw) if raw.strip() else {}
            out = hooks.run(args.event, payload)
            if out:
                print(json.dumps(out))
        except BaseException:
            pass
        return 0
    try:
        if args.project:
            os.chdir(args.project.expanduser().resolve())
        _start_surface(args)
        result, code = dispatch(args)
    except Exception as exc:
        print(json.dumps({"error": type(exc).__name__, "message": str(exc)}, ensure_ascii=False),
              file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
