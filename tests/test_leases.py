import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "harness"))

import cli  # noqa: E402
import goal  # noqa: E402
import leases  # noqa: E402
import steps  # noqa: E402


def test_reclaimable_rules():
    assert leases.reclaimable(None, "s2", now=100.0).startswith("no lease")
    lease = {"session": "s1", "renewed_at": 1000.0}
    assert leases.reclaimable(lease, "s1", now=1000.0 + 599) is None
    assert "expired" in leases.reclaimable(lease, "s1", now=1000.0 + 600)
    assert leases.reclaimable(lease, "s2", now=1000.0 + 299) is None
    assert "silent" in leases.reclaimable(lease, "s2", now=1000.0 + 300)
    assert leases.reclaimable(lease, None, now=1000.0 + 300) is None


def test_corrupt_lease_file_reads_as_empty(make_state_goal):
    state = make_state_goal()
    leases._path(state["slug"]).write_text("{not json")
    assert leases.read(state["slug"]) == {}


def _attached(make_state_goal):
    state = make_state_goal()
    slug = state["slug"]
    begun = steps.begin(slug, "req-1")
    attached = steps.attach(slug, "req-1", "worker-1", expected_revision=begun["execution_revision"])
    leases.renew(slug, "req-1", "s1")  # what the owning session's first PostToolUse hook does
    return slug, attached


def test_attach_records_an_unowned_lease(make_state_goal, monkeypatch):
    # The shared session file is overwritten by every SessionStart, so it cannot name the owner;
    # the first PostToolUse of the owning session claims the lease.
    monkeypatch.setattr(steps.paths, "current_session", lambda: "s1")
    slug = make_state_goal()["slug"]
    begun = steps.begin(slug, "req-1")
    assert leases.read(slug)["req-1"]["session"] is None
    steps.attach(slug, "req-1", "worker-1", expected_revision=begun["execution_revision"])
    assert leases.read(slug)["req-1"]["session"] is None


def test_unowned_lease_is_reclaimable_only_after_the_term():
    lease = {"session": None, "renewed_at": 1000.0}
    assert leases.reclaimable(lease, "s2", now=1000.0 + 301) is None
    assert leases.reclaimable(lease, "s2", now=1000.0 + 599) is None
    assert "expired" in leases.reclaimable(lease, "s2", now=1000.0 + 600)


def test_reclaim_refuses_live_lease_then_closes_stale_one(make_state_goal, monkeypatch):
    monkeypatch.setattr(steps.paths, "current_session", lambda: "s1")
    slug, attached = _attached(make_state_goal)
    rev = attached["execution_revision"]
    renewed = leases.read(slug)["req-1"]["renewed_at"]
    with pytest.raises(steps.StepBusy):
        steps.reclaim(slug, "req-1", expected_revision=rev, session="s2", now=renewed + 10)
    out = steps.reclaim(slug, "req-1", expected_revision=rev, session="s2", now=renewed + 301)
    state = goal.load(slug)
    assert state["execution"]["active_attempt"] is None
    assert state["execution"]["attempts"][0]["status"] == "recovered"
    assert out["attempt_status"] == "recovered" and out["attempt_closed"] is True


def test_dead_worker_cannot_finish_after_reclaim(make_state_goal, monkeypatch, tmp_path):
    monkeypatch.setattr(steps.paths, "current_session", lambda: "s1")
    slug, attached = _attached(make_state_goal)
    renewed = leases.read(slug)["req-1"]["renewed_at"]
    steps.reclaim(slug, "req-1", expected_revision=attached["execution_revision"],
                  session="s2", now=renewed + 301)
    update = tmp_path / "u.json"
    update.write_text("{}")
    with pytest.raises(steps.StepConflict):
        steps.finish(slug, "req-1", expected_revision=goal.load(slug)["execution"]["revision"],
                     update_file=update, worker_evidence_file=update)


def test_reclaim_replay_is_harmless(make_state_goal, monkeypatch):
    monkeypatch.setattr(steps.paths, "current_session", lambda: "s1")
    slug, attached = _attached(make_state_goal)
    renewed = leases.read(slug)["req-1"]["renewed_at"]
    steps.reclaim(slug, "req-1", expected_revision=attached["execution_revision"],
                  session="s2", now=renewed + 301)
    again = steps.reclaim(slug, "req-1", expected_revision=0, session="s2", now=renewed + 400)
    assert again["replayed"] is True


def test_lease_file_is_not_listed_as_a_goal(make_state_goal):
    slug, _ = _attached(make_state_goal)
    assert leases._path(slug).exists()
    rows = cli.goal_rows()
    assert [row["slug"] for row in rows] == [slug]
    assert not any("error" in row for row in rows)


def test_renewing_a_lease_does_not_bump_the_execution_revision(make_state_goal):
    slug, attached = _attached(make_state_goal)
    leases.renew(slug, "req-1", "s1")
    assert goal.load(slug)["execution"]["revision"] == attached["execution_revision"]
