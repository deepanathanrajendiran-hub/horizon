"""E2E-SWE full run (2026-11-08; docs/evaluations/e2eswe-full on the bench branch): plain, Horizon 46d56b6 and the
candidate 5f3bb6a, 31 tasks x 2 builds each, the same day. Haiku 5.5, 15 model calls a session counting subagents.

Reviewers ran inside the session that sent them, so a reviewer sent late was cut off with it. Of 133 reviewer
dispatches, those sent with 0-8 of the session's calls used came back 64 times in 72; those sent after 9 or more came
back 4 times in 61. A reviewer that came back had used 3 to 10 calls (median 6). The dead rounds then held goals:
`close` said a reviewer "may still be writing" while its cut-off reply was under 15 minutes old (20 candidate
sessions), and once both hand-outs had died it said `review_unavailable` and would not complete (66 sessions). The
candidate's builds with such a stall scored 0.8 points above plain; its other builds 5.8.

So, when the runtime gives the session's call budget (HORIZON_SESSION_CALLS), no brief goes out with fewer calls left
than a reviewer needs; the next session starts with `close`. And a reply from a reviewer whose session has ended is
finished as it stands."""
import json
import sys
from pathlib import Path

from test_e2eswe_fixes import project, _start, _brief  # noqa: F401  (the fixture is used by name)

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))


def _calls(cli, session, n, agent_id=None):
    for _ in range(n):
        payload = {"session_id": session, "hook_event_name": "PostToolUse", "tool_name": "Read",
                   "tool_input": {"file_path": "x"}, "tool_response": {}}
        if agent_id:
            payload["agent_id"] = agent_id
        cli.hook("PostToolUse", payload)


def _state(p, slug):
    return json.loads((p / ".claude" / "harness" / "goals" / f"{slug}.json").read_text())


def _ready(p, cli, session="s1"):
    cli.new_session(session)
    slug, gates = _start(p, cli)
    (p / "src" / "extra.py").write_text("X = 1\n")
    return slug


# --- no brief the session cannot finish ------------------------------------------------------------------------

def test_close_holds_the_brief_back_when_the_session_has_too_few_calls_left(project):
    p, cli = project
    cli.env["HORIZON_SESSION_CALLS"] = "15"
    slug = _ready(p, cli)
    _calls(cli, "s1", 9)
    code, out = cli("close", slug)
    assert code == 1 and not out["complete"], out
    assert not out.get("review_brief") and out.get("review_deferred"), out
    assert "next session" in out["review_deferred"] and "`close`" in out["review_deferred"], out
    assert not _state(p, slug).get("review_issued"), "a brief held back is not a hand-out"


def test_check_holds_the_brief_back_too(project):
    p, cli = project
    cli.env["HORIZON_SESSION_CALLS"] = "15"
    slug = _ready(p, cli)
    _calls(cli, "s1", 10)
    code, out = cli("check", slug, "--did", "Built it", "--next", "Review")
    assert out.get("review_required") and not out.get("review_brief") and out.get("review_deferred"), out


def test_the_next_session_starts_with_close_and_close_then_hands_the_brief_out(project):
    p, cli = project
    cli.env["HORIZON_SESSION_CALLS"] = "15"
    slug = _ready(p, cli)
    _calls(cli, "s1", 12)
    assert cli("close", slug)[1].get("review_deferred")
    context = cli.new_session("s2")["hookSpecificOutput"]["additionalContext"]
    assert f"Start this session with `close {slug}`" in context, context
    _calls(cli, "s2", 1)
    code, out = cli("close", slug)
    assert out.get("review_brief") and not out.get("review_deferred"), out
    assert len(_state(p, slug)["review_issued"]) == 1
    assert "Start this session with" not in cli.new_session("s3")["hookSpecificOutput"]["additionalContext"]


def test_with_calls_to_spare_close_hands_the_brief_out(project):
    p, cli = project
    cli.env["HORIZON_SESSION_CALLS"] = "15"
    slug = _ready(p, cli)
    _calls(cli, "s1", 4)
    code, out = cli("close", slug)
    assert out.get("review_brief") and not out.get("review_deferred"), out


def test_without_a_session_budget_a_late_close_still_hands_the_brief_out(project):
    p, cli = project
    cli.env.pop("HORIZON_SESSION_CALLS", None)
    slug = _ready(p, cli)
    _calls(cli, "s1", 14)
    code, out = cli("close", slug)
    assert out.get("review_brief") and not out.get("review_deferred"), out


def test_a_subagents_tool_calls_count_toward_the_session(project):
    p, cli = project
    cli.env["HORIZON_SESSION_CALLS"] = "15"
    slug = _ready(p, cli)
    _calls(cli, "s1", 4)
    _calls(cli, "s1", 5, agent_id="explorer-1")
    code, out = cli("close", slug)
    assert out.get("review_deferred") and not out.get("review_brief"), out


def test_calls_counted_in_an_earlier_session_do_not_count_in_this_one(project):
    p, cli = project
    cli.env["HORIZON_SESSION_CALLS"] = "15"
    slug = _ready(p, cli)
    _calls(cli, "s1", 12)
    cli.new_session("s2")
    _calls(cli, "s2", 2)
    code, out = cli("close", slug)
    assert out.get("review_brief"), out


# --- a reviewer gone with its session is not still writing --------------------------------------------------------

def test_a_cut_off_reply_from_an_ended_session_is_its_round_at_once(project):
    p, cli = project
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(f"Reviewed checkpoint: {digest}\nR1: PASS - ran parse on three lines - ok\n")
    cli.new_session("s2")  # the reviewer was cut off with session 1, a minute ago
    code, out = cli("close", slug)
    assert not out.get("review_pending"), out
    assert code == 0 and out["complete"] and "unreviewed" in out, out


def test_a_cut_off_reply_in_this_session_may_still_be_written(project):
    p, cli = project
    slug, digest, review_file = _brief(p, cli)
    review_file.write_text(f"Reviewed checkpoint: {digest}\nR1: PASS - ran parse on three lines - ok\n")
    code, out = cli("close", slug)
    assert out.get("review_pending") and not out["complete"], out

# --- review of the fix (2026-11-08) -------------------------------------------------------------------------------

def _failed_calls(cli, session, n):
    # Claude Code fires PostToolUse only after a tool succeeds; a Bash command exiting non-zero (a `close` that is not
    # complete, a failing gate) fires PostToolUseFailure (E2E-SWE full run logs: 93 failed calls, no PostToolUse)
    for _ in range(n):
        cli.hook("PostToolUseFailure", {"session_id": session, "hook_event_name": "PostToolUseFailure",
                                        "tool_name": "Bash", "tool_input": {"command": "x"}, "error": "exit 1"})


def test_failed_tool_calls_count_toward_the_session(project):
    p, cli = project
    cli.env["HORIZON_SESSION_CALLS"] = "15"
    slug = _ready(p, cli)
    _calls(cli, "s1", 3)
    _failed_calls(cli, "s1", 6)
    code, out = cli("close", slug)
    assert out.get("review_deferred") and not out.get("review_brief"), out


def test_install_hooks_registers_the_failure_event(project):
    p, cli = project
    code, out = cli("install-hooks")
    settings = json.loads((p / ".claude" / "settings.json").read_text())
    assert "PostToolUseFailure" in settings["hooks"], settings["hooks"].keys()


def test_a_budget_too_small_for_any_reviewer_never_holds_the_brief_back(project):
    p, cli = project
    cli.env["HORIZON_SESSION_CALLS"] = "6"
    slug = _ready(p, cli)
    _calls(cli, "s1", 5)
    code, out = cli("close", slug)
    assert out.get("review_brief") and not out.get("review_deferred"), out


def test_a_resumed_session_keeps_its_id_but_starts_a_new_count(project):
    # `claude --resume` keeps the session id; a runtime that resumes capped sessions gives each a new budget
    p, cli = project
    cli.env["HORIZON_SESSION_CALLS"] = "15"
    slug = _ready(p, cli)
    _calls(cli, "s1", 12)
    assert cli("close", slug)[1].get("review_deferred")
    context = cli.hook("SessionStart", {"session_id": "s1", "source": "resume"})["hookSpecificOutput"][
        "additionalContext"]
    assert f"Start this session with `close {slug}`" in context, context
    _calls(cli, "s1", 1)
    code, out = cli("close", slug)
    assert out.get("review_brief") and not out.get("review_deferred"), out


def test_a_compaction_does_not_reset_the_count(project):
    p, cli = project
    cli.env["HORIZON_SESSION_CALLS"] = "15"
    slug = _ready(p, cli)
    _calls(cli, "s1", 10)
    cli.hook("SessionStart", {"session_id": "s1", "source": "compact"})
    code, out = cli("close", slug)
    assert out.get("review_deferred") and not out.get("review_brief"), out
