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
import subprocess

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


def renew(slug: str, attempt_id: str, session: str | None, now: float | None = None,
          host: dict | None = None) -> None:
    """Renew the lease for `session`. `host` is the session's host process; without it a
    renewal by the same session keeps the one already recorded."""
    target = _path(slug)
    with filelock.goal_locked(target):
        data = read(slug)
        old = data.get(attempt_id) if isinstance(data.get(attempt_id), dict) else {}
        if host is None and session and old.get("session") == session:
            host = old.get("host")
        entry = {"session": session, "renewed_at": time.time() if now is None else now}
        if host:
            entry["host"] = host
        data[attempt_id] = entry
        tmp = target.parent / f".{target.name}.tmp-{os.getpid()}"
        tmp.write_text(json.dumps(data, indent=2) + "\n")
        os.replace(tmp, target)


def _ps(pid: int) -> tuple[int, str, str] | None:
    """(parent pid, start time, executable) of a process, or None when ps cannot say."""
    try:
        out = subprocess.run(["ps", "-o", "ppid=,lstart=,comm=", "-p", str(pid)],
                             capture_output=True, text=True, timeout=5,
                             env={**os.environ, "LC_ALL": "C"})
    except (OSError, subprocess.SubprocessError):
        return None
    parts = out.stdout.strip().split(None, 6)
    if out.returncode or len(parts) < 7 or not parts[0].isdigit():
        return None
    return int(parts[0]), " ".join(parts[1:6]), parts[6]


def host_process(names=("claude",)) -> dict | None:
    """The nearest ancestor running the host CLI, as {"pid", "started"}, or None if unknown.

    Hooks and CLI calls both run under the session's own host process, which dies with the
    session; the commands it started may not (docs/research/2026-10-27-kill9-probe.md)."""
    pid = os.getppid()
    for _ in range(8):
        row = _ps(pid)
        if row is None:
            return None
        ppid, started, comm = row
        if os.path.basename(comm) in names:
            return {"pid": pid, "started": started}
        if ppid <= 1:
            return None
        pid = ppid
    return None


def gone(host) -> bool:
    """True only when the recorded host process has provably ended (or its pid was reused).
    Anything unclear -- a malformed record, a denied signal -- is False, leaving it to the timers."""
    if not isinstance(host, dict):
        return False
    pid, started = host.get("pid"), host.get("started")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1 or not isinstance(started, str):
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except OSError:
        return False
    row = _ps(pid)
    return row is not None and row[1] != started


def reclaimable(lease: dict | None, session: str | None, now: float | None = None) -> str | None:
    """Why the attempt may be taken over, or None while its lease holds."""
    if not isinstance(lease, dict) or not isinstance(lease.get("renewed_at"), (int, float)):
        return "no lease recorded: the attempt predates leases or never renewed"
    age = (time.time() if now is None else now) - lease["renewed_at"]
    if age >= LEASE_TERM_S:
        return f"lease expired {int(age)} s after its last renewal"
    owner = lease.get("session")
    if session and owner and session != owner and gone(lease.get("host")):
        return f"owning session {owner}'s process {lease['host']['pid']} has exited"
    if session and owner and session != owner and age >= OTHER_SESSION_STALE_S:
        return f"owning session {owner} has been silent for {int(age)} s"
    return None
