"""E2E-SWE fix test (2026-11-08; docs/evaluations/e2eswe-fixtest on the bench branch): Horizon a64af28 alone, one
build each of 10 headroom tasks. Haiku 5.5, 15 model calls a session counting subagents.

The review brief was held back when a session had too few calls left (6 times), but 12 of 76 sessions still stalled
on the review, in 3 builds. In each, a brief went out with calls to spare and the agent did not act on it in time:

- fiddle: `close` handed the brief out in sessions 2 and 4; no reviewer was ever dispatched. Session 2's next `check`
  said a reviewer "may still be writing". The two unanswered briefs used both of the goal's hand-outs, so sessions
  5-10 got `review_unavailable` (5 of them stalled) and no brief again.
- grappa and pdfminer: reviewers dispatched late or too long were cut off with their session; pdfminer then got
  `review_unavailable` for its last 3 sessions.

`review_unavailable` made 8 of the 12 stall sessions. So:

1. A brief counts as a hand-out only once a reviewer took it up: its reply file is on disk (the brief's first step
   is to write the reply's header). A brief no reviewer took up is not pending: `close` and `check` give it again.
2. When the runtime gives the session's call budget (HORIZON_SESSION_CALLS), a new session's `close` hands out a
   fresh brief even after the goal's hand-outs died, while the session has the calls a reviewer needs; the session
   start says to begin with `close`. Without a budget, the hand-outs stay bounded as before."""
import json
import sys
from pathlib import Path

from test_e2eswe_fixes import project, _start, _brief  # noqa: F401  (the fixture is used by name)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))


def _state(p, slug):
    return json.loads((p / ".claude" / "harness" / "goals" / f"{slug}.json").read_text())


def _calls(cli, session, n):
    for _ in range(n):
        cli.hook("PostToolUse", {"session_id": session, "hook_event_name": "PostToolUse", "tool_name": "Read",
                                 "tool_input": {"file_path": "x"}, "tool_response": {}})


def _started(review_file, digest):
    # a reviewer took the brief up and was cut off before deciding anything
    Path(review_file).write_text(f"Reviewed checkpoint: {digest}\n")


# --- 1. a brief no reviewer took up is not a hand-out --------------------------------------------------------------

def test_a_brief_no_reviewer_took_up_is_not_pending_and_close_gives_it_again(project):
    p, cli = project
    slug, digest, review_file = _brief(p, cli)
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"], out
    assert not out.get("review_pending") and out.get("review_brief"), out
    assert out["review_file"] == str(review_file), out
    assert len(_state(p, slug)["review_issued"]) == 1, "the same brief in the same session is one hand-out"


def test_check_after_a_brief_no_reviewer_took_up_gives_the_brief_not_pending(project):
    p, cli = project
    slug, digest, review_file = _brief(p, cli)
    code, out = cli("check", slug, "--did", "Kept building", "--next", "Run close")
    assert not out.get("review_pending") and out.get("review_brief"), out


def test_once_the_reviewer_writes_its_reply_the_round_is_pending(project):
    p, cli = project
    slug, digest, review_file = _brief(p, cli)
    _started(review_file, digest)
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"] and out.get("review_pending"), out
    assert not out.get("review_brief"), out


def test_with_a_budget_briefs_no_reviewer_took_up_do_not_use_the_goals_hand_outs(project):
    # Without a budget a brief that went out in an earlier session still counts, taken up or not: nothing else
    # bounds the re-sends there, and a reviewer gone before its first write cannot be told from no reviewer
    # (test_heldout2_fixes.py, test_with_more_rounds_a_reviewer_that_never_writes_does_not_loop).
    p, cli = project
    cli.env["HORIZON_SESSION_CALLS"] = "15"
    slug, digest, review_file = _brief(p, cli)
    for session in ("s2", "s3", "s4"):
        cli.new_session(session)
        code, out = cli("close", slug)
        assert code == 1 and not out["complete"], (session, out)
        assert out.get("review_brief") and not out.get("review_unavailable"), (session, out)


def test_without_a_budget_reviewers_that_started_and_never_answered_still_use_the_hand_outs(project):
    p, cli = project
    cli.env.pop("HORIZON_SESSION_CALLS", None)
    slug, digest, review_file = _brief(p, cli)
    _started(review_file, digest)
    cli.new_session("s2")
    code, out = cli("close", slug)
    assert out.get("review_brief"), out
    _started(out["review_file"], digest)
    cli.new_session("s3")
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"], out
    assert out.get("review_unavailable") and not out.get("review_brief"), out


# --- 2. with a session budget, a new session hands out a fresh brief ----------------------------------------------

def _both_died(p, cli):
    cli.env["HORIZON_SESSION_CALLS"] = "15"
    slug, digest, review_file = _brief(p, cli)
    _started(review_file, digest)
    cli.new_session("s2")
    code, out = cli("close", slug)
    assert out.get("review_brief"), out
    _started(out["review_file"], digest)
    return slug, digest


def test_with_a_budget_a_new_session_hands_out_a_fresh_brief_after_both_reviewers_died(project):
    p, cli = project
    slug, digest = _both_died(p, cli)
    cli.new_session("s3")
    _calls(cli, "s3", 1)
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"], out
    assert out.get("review_brief") and not out.get("review_unavailable"), out


def test_with_a_budget_the_fresh_brief_goes_out_once_a_session(project):
    p, cli = project
    slug, digest = _both_died(p, cli)
    cli.new_session("s3")
    code, out = cli("close", slug)
    _started(out["review_file"], digest)
    code, out = cli("close", slug)
    assert out.get("review_pending") and not out.get("review_brief"), out


def test_with_a_budget_the_fresh_brief_still_waits_for_a_session_with_the_calls(project):
    p, cli = project
    slug, digest = _both_died(p, cli)
    cli.new_session("s3")
    _calls(cli, "s3", 10)
    code, out = cli("close", slug)
    assert out.get("review_deferred") and not out.get("review_brief"), out


def test_with_a_budget_a_new_session_after_dead_reviewers_is_told_to_start_with_close(project):
    p, cli = project
    slug, digest = _both_died(p, cli)
    context = cli.new_session("s3")["hookSpecificOutput"]["additionalContext"]
    assert f"Start this session with `close {slug}`" in context, context


def test_after_an_answered_round_no_session_is_told_to_start_with_close(project):
    p, cli = project
    cli.env["HORIZON_SESSION_CALLS"] = "15"
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(f"Reviewed checkpoint: {digest}\nR1: PASS - ran parse - ok\nR2: PASS - ok\n"
                           "VERDICTS\nR1: PASS\nR2: PASS\nEND VERDICTS\n")
    context = cli.new_session("s2")["hookSpecificOutput"]["additionalContext"]
    assert "Start this session with" not in context, context


# --- review of b1c3778 (2026-11-08) ------------------------------------------------------------------------------

def _age(p, slug, review_file, seconds):
    # the reviewer went quiet `seconds` ago, and so did its hand-out
    import os
    import time
    from datetime import datetime, timedelta, timezone
    then = time.time() - seconds
    os.utime(review_file, (then, then))
    path = p / ".claude" / "harness" / "goals" / f"{slug}.json"
    state = json.loads(path.read_text())
    state["review_issued"][-1]["at"] = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    # the goal and the session started before that
    state["used"]["started"] = (datetime.now(timezone.utc) - timedelta(seconds=seconds + 120)).isoformat()
    path.write_text(json.dumps(state))
    calls = p / ".claude" / "harness" / "notes" / "session-calls.json"
    record = json.loads(calls.read_text())
    record["started"] = then - 60
    calls.write_text(json.dumps(record))


def test_a_resumed_session_after_dead_reviewers_hands_out_a_fresh_brief(project):
    # a resume keeps its session id: the reviewer of the earlier run ended with it
    p, cli = project
    slug, digest = _both_died(p, cli)
    cli.hook("SessionStart", {"session_id": "s2", "source": "resume"})
    code, out = cli("close", slug)
    assert not out.get("review_pending") and not out.get("review_unavailable"), out
    assert out.get("review_brief"), out


def test_with_a_budget_a_session_whose_reviewer_went_quiet_gets_no_second_brief_and_hears_of_the_next(project):
    p, cli = project
    slug, digest = _both_died(p, cli)
    cli.new_session("s3")
    code, out = cli("close", slug)
    _started(out["review_file"], digest)
    _age(p, slug, Path(out["review_file"]), 1000)
    code, out = cli("close", slug)
    assert not out.get("review_brief") and out.get("review_unavailable"), out
    assert "next session" in out["review_unavailable"], out
    cli.new_session("s4")
    assert cli("close", slug)[1].get("review_brief")


def test_a_brief_from_check_that_a_reviewer_took_up_is_pending(project):
    p, cli = project
    slug, digest = _both_died(p, cli)
    cli.new_session("s3")
    code, out = cli("check", slug, "--did", "Kept building", "--next", "Run close")
    assert out.get("review_brief"), out
    _started(out["review_file"], digest)
    code, out = cli("close", slug)
    assert out.get("review_pending") and not out.get("review_brief"), out


def test_a_budget_too_small_for_any_reviewer_keeps_the_hand_outs_bounded(project):
    p, cli = project
    cli.env["HORIZON_SESSION_CALLS"] = "6"
    slug, digest, review_file = _brief(p, cli)
    _started(review_file, digest)
    cli.new_session("s2")
    code, out = cli("close", slug)
    _started(out["review_file"], digest)
    context = cli.new_session("s3")["hookSpecificOutput"]["additionalContext"]
    assert "no reviewer has answered" not in context, context
    code, out = cli("close", slug)
    assert out.get("review_unavailable") and not out.get("review_brief"), out


def test_after_a_round_came_back_the_session_start_does_not_say_no_reviewer_answered(project):
    p, cli = project
    cli.env["HORIZON_SESSION_CALLS"] = "15"
    cli.env["HORIZON_REVIEW_ROUNDS_PER_GOAL"] = "2"
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(f"Reviewed checkpoint: {digest}\nR1: PASS - ran parse - ok\nR2: FAIL - unclear\n"
                           "VERDICTS\nR1: PASS\nR2: FAIL\nEND VERDICTS\n")
    context = cli.new_session("s2")["hookSpecificOutput"]["additionalContext"]
    assert "no reviewer has answered" not in context, context
