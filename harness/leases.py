"""Leases on state-mode attempts.

A worker's claim on an attempt expires on a clock: nothing can prove a worker
died with its session, but a lease that stopped being renewed can be taken
over. Kept beside the goal file, not in it, because renewals happen on every
tool call and must not bump the goal's execution revision.
"""

import json
import os
import time

import filelock
import paths

LEASE_TERM_S = 600          # gate.DEFAULT_TIMEOUT (480) plus a margin
OTHER_SESSION_STALE_S = 300  # longer than a slow model call


def _path(slug: str):
    return paths.goals_dir() / f"{slug}.leases.json"


def read(slug: str) -> dict:
    try:
        data = json.loads(_path(slug).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def renew(slug: str, attempt_id: str, session: str | None, now: float | None = None) -> None:
    target = _path(slug)
    with filelock.goal_locked(target):
        data = read(slug)
        data[attempt_id] = {"session": session, "renewed_at": time.time() if now is None else now}
        tmp = target.parent / f".{target.name}.tmp-{os.getpid()}"
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, target)


def reclaimable(lease: dict | None, session: str | None, now: float | None = None) -> str | None:
    """Why the attempt may be taken over, or None while its lease holds."""
    if not isinstance(lease, dict) or not isinstance(lease.get("renewed_at"), (int, float)):
        return "no lease recorded: the attempt predates leases or never renewed"
    age = (time.time() if now is None else now) - lease["renewed_at"]
    if age >= LEASE_TERM_S:
        return f"lease expired {int(age)} s after its last renewal"
    owner = lease.get("session")
    if session and owner and session != owner and age >= OTHER_SESSION_STALE_S:
        return f"owning session {owner} has been silent for {int(age)} s"
    return None
