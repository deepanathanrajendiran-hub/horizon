"""An unattended run spends its whole budget (development line from 46d56b6, 2026-11-05).

E2E-SWE paper2 (Horizon 46d56b6, 31 tasks, 15 calls a session, 10 sessions): 15 builds ended before session 10
because a session ended on its own, and the runner reads that as done. Horizon used 249 sessions and 215M input
tokens against plain Claude Code's 267 and 246M.

- 8 of the 15 stopped after `close` completed the goal: the Stop hook lets any stop through once no goal is active,
  though the run had sessions left and no one was waiting for a report.
- 7 stopped with the goal still active: each had been sent back 5 times in its last session (the fixed bound), and
  the sixth stop went through. In django-modelcluster `close` could not complete (both review hand-outs unanswered),
  and the hook's only instruction was to stop once `close` completes, so the agent kept trying to stop.
"""
from test_e2eswe_fixes import _brief, _start, project  # noqa: F401  (the fixture is used by name)

import hooks


def _complete(p, cli):
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(f"Reviewed checkpoint: {digest}\nR1: PASS - ran parse - ok\nR2: PASS - reads well\n"
                           "VERDICTS\nR1: PASS\nR2: PASS\nEND VERDICTS\n")
    code, out = cli("review", slug, "--result", "pass", "--judge", "reviewer-1", "--hash", digest,
                    "--evidence-file", str(review_file))
    code, out = cli("close", slug)
    assert code == 0 and out["complete"], out
    return slug


def test_unattended_stop_after_the_goal_completes_sends_the_agent_back_to_improve_the_work(project):
    p, cli = project
    cli.env["HORIZON_UNATTENDED"] = "1"
    slug = _complete(p, cli)
    out = cli.hook("Stop", {"session_id": "s1", "stop_hook_active": False})
    assert out and out["decision"] == "block", out
    reason = out["reason"]
    assert slug in reason and "complete" in reason and "budget" in reason, reason
    assert "against the request" in reason and "gate" in reason, reason


def test_attended_stop_after_the_goal_completes_still_goes_through(project):
    p, cli = project
    _complete(p, cli)
    assert cli.hook("Stop", {"session_id": "s1", "stop_hook_active": False}) is None


def test_unattended_stop_with_no_goal_at_all_goes_through(project):
    p, cli = project
    cli.env["HORIZON_UNATTENDED"] = "1"
    cli.new_session("s1")
    assert cli.hook("Stop", {"session_id": "s1", "stop_hook_active": False}) is None


def test_unattended_stop_after_completion_is_bounded_per_session(project):
    p, cli = project
    cli.env["HORIZON_UNATTENDED"] = "1"
    _complete(p, cli)
    blocks = [cli.hook("Stop", {"session_id": "s9", "stop_hook_active": True})
              for _ in range(hooks.UNATTENDED_STOP_BLOCKS + 1)]
    assert all(b and b["decision"] == "block" for b in blocks[:-1]) and blocks[-1] is None, blocks


def test_a_known_session_call_budget_sets_how_often_a_session_is_sent_back(project):
    p, cli = project
    cli.env["HORIZON_UNATTENDED"] = "1"
    cli.env["HORIZON_SESSION_CALLS"] = "8"
    cli.new_session("s1")
    _start(p, cli)
    blocks = [cli.hook("Stop", {"session_id": "s1", "stop_hook_active": True}) for _ in range(9)]
    assert all(b and b["decision"] == "block" for b in blocks[:8]) and blocks[8] is None, blocks


def test_an_unusable_session_call_budget_keeps_the_fixed_bound(project):
    p, cli = project
    cli.env["HORIZON_UNATTENDED"] = "1"
    cli.new_session("s1")
    _start(p, cli)
    for i, value in enumerate(("0", "-4", "many", "")):
        cli.env["HORIZON_SESSION_CALLS"] = value
        session = f"bad{i}"
        blocks = [cli.hook("Stop", {"session_id": session, "stop_hook_active": True})
                  for _ in range(hooks.UNATTENDED_STOP_BLOCKS + 1)]
        assert all(b and b["decision"] == "block" for b in blocks[:-1]) and blocks[-1] is None, (value, blocks)


def test_unattended_stop_on_a_goal_close_cannot_complete_says_to_keep_improving_not_to_wait(project):
    p, cli = project
    cli.env["HORIZON_UNATTENDED"] = "1"
    slug, digest, review_file = _brief(p, cli)
    cli.new_session("s2")
    cli("close", slug)
    cli.new_session("s3")
    code, out = cli("close", slug)
    assert out.get("review_unavailable"), out
    reason = cli.hook("Stop", {"session_id": "s3", "stop_hook_active": True})["reason"]
    assert "cannot complete" in reason and "against the request" in reason, reason
