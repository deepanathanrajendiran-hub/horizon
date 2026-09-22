"""Durable state-mode attempt reservation and worker binding."""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

import filelock
import goal
import paths
import task_state
import tree_hash




class StepError(goal.GoalStateError):
    """A state-mode step cannot proceed."""


class StepBusy(StepError):
    """Another attempt already owns this goal."""


class StepConflict(StepError):
    """A saved attempt or binding conflicts with this request."""


def _require_id(value: str, field: str) -> None:
    if not isinstance(value, str) or not task_state._ID_RE.fullmatch(value):
        raise StepError(f"{field} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}")

def _require_slug(slug: str) -> None:
    if not isinstance(slug, str) or not re.fullmatch(r"[\w-]+", slug, re.UNICODE):
        raise StepError("slug must be a single goal name component")


def _state(slug: str) -> dict:
    _require_slug(slug)
    state = goal.load(slug)
    if goal.execution_mode(state) != "state":
        raise StepError("step commands require a state-mode goal")
    return state


def _attempt(state: dict, attempt_id: str) -> dict | None:
    return next((item for item in state["execution"]["attempts"]
                 if item["id"] == attempt_id), None)


def _admit(state: dict) -> None:
    if state["status"] != "active":
        raise goal.GoalClosed(f"Goal is {state['status']}")
    if state["execution"]["active_attempt"] is not None:
        raise StepBusy("another attempt is still open")
    if goal.budget_exhausted(state):
        raise goal.BudgetExhausted("state-mode worker-attempt budget is exhausted")


def store_blob(slug: str, kind: str, payload: bytes) -> dict:
    """Save immutable bytes by SHA-256 under the project-local goal objects."""
    _require_slug(slug)
    if not isinstance(kind, str) or not kind:
        raise StepError("blob kind must be nonempty text")
    if not isinstance(payload, bytes):
        raise StepError("blob payload must be bytes")
    digest = hashlib.sha256(payload).hexdigest()
    directory = paths.goals_dir() / f"{slug}.d" / "objects"
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / f"{digest}.json"
    if target.exists():
        read_blob({"path": str(target.relative_to(paths.project_root())),
                   "sha256": digest}, paths.project_root())
    else:
        fd, temporary = tempfile.mkstemp(prefix=f".{digest}.", suffix=".tmp", dir=directory)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
    return {"path": str(target.relative_to(paths.project_root())), "sha256": digest}


def read_blob(ref: dict, project: Path) -> bytes:
    """Read a project-relative object and reject path or digest substitution."""
    if (not isinstance(ref, dict) or set(ref) != {"path", "sha256"}
            or not isinstance(ref["path"], str) or not isinstance(ref["sha256"], str)
            or not re.fullmatch(r"[0-9a-f]{64}", ref["sha256"])):
        raise StepConflict("invalid saved object reference")
    project = Path(project).resolve()
    path = Path(ref["path"])
    if path.is_absolute() or ".." in path.parts:
        raise StepConflict("saved object path escapes the project")
    target = (project / path).resolve()
    if not target.is_relative_to(project):
        raise StepConflict("saved object path escapes the project")
    if target.name != f"{ref['sha256']}.json" or "objects" not in target.parts:
        raise StepConflict("saved object path does not match its digest")
    try:
        payload = target.read_bytes()
    except OSError as error:
        raise StepConflict(f"saved object is unavailable: {error}") from error
    if hashlib.sha256(payload).hexdigest() != ref["sha256"]:
        raise StepConflict("saved object SHA-256 mismatch")
    return payload


def _saved_packet(state: dict, attempt: dict) -> dict:
    raw = read_blob(attempt["packet"], Path(state["cwd"]))
    try:
        packet = json.loads(
            raw,
            object_pairs_hook=task_state._no_duplicates,
            parse_constant=task_state._nonfinite,
            parse_float=task_state._finite_float,
        )
    except (UnicodeError, json.JSONDecodeError, task_state.StateError) as error:
        raise StepConflict(f"saved packet is unreadable: {error}") from error
    if (not isinstance(packet, dict)
            or task_state.canonical_bytes(packet) != raw
            or packet.get("attempt_id") != attempt["id"]
            or packet.get("base_revision") != attempt["base_revision"]
            or not isinstance(packet.get("procedure"), dict)
            or packet["procedure"].get("sha256") != attempt["procedure_sha256"]
            or len(raw) > task_state.PACKET_LIMIT):
        raise StepConflict("saved packet identity or size is invalid")
    return packet


def report(state: dict, attempt: dict, *, replayed: bool = False) -> dict:
    """Return current goal status plus this attempt's durable outcome."""
    status = attempt["status"]
    result = {
        **goal.summary(state),
        "attempt_id": attempt["id"],
        "execution_revision": state["execution"]["revision"],
        "attempt_status": status,
        "replayed": replayed,
        "update_accepted": status == "accepted",
        "attempt_closed": status in {"accepted", "recovered"},
        "exit_code": 0,
    }
    if attempt["result"] is not None:
        result.update(attempt["result"])
        result["attempt_id"] = attempt["id"]
        result["attempt_status"] = status
        result["replayed"] = replayed
        result["execution_revision"] = state["execution"]["revision"]
    return result


def _begin_report(state: dict, attempt: dict, packet: dict, *, replayed: bool) -> dict:
    result = report(state, attempt, replayed=replayed)
    result["packet"] = packet
    result["packet_ref"] = attempt["packet"]
    result["packet_path"] = attempt["packet"]["path"]
    result["packet_sha256"] = attempt["packet"]["sha256"]
    return result


def begin(slug: str, request_id: str) -> dict:
    """Reserve and persist one worker attempt before native dispatch."""
    _require_id(request_id, "request_id")
    state = _state(slug)
    existing = _attempt(state, request_id)
    if existing is not None:
        return _begin_report(state, existing, _saved_packet(state, existing), replayed=True)
    _admit(state)
    expected_revision = state["execution"]["revision"]
    current_hash = tree_hash.compute(state["cwd"], state["watch_paths"])
    packet_state = {
        **state,
        "audit_path": str(goal._path(slug).relative_to(paths.project_root())),
    }
    try:
        packet = task_state.build_packet(
            packet_state, attempt_id=request_id, base_revision=expected_revision + 1,
            current_hash=current_hash, runtime=paths.runtime(), reserve_verdicts=True,
        )
    except task_state.StateError as error:
        raise StepError(f"cannot prepare worker packet: {error}") from error
    packet_bytes = task_state.canonical_bytes(packet)
    packet_ref = store_blob(slug, "packet", packet_bytes)
    with filelock.goal_locked(goal._path(slug)):
        state = _state(slug)
        existing = _attempt(state, request_id)
        if existing is not None:
            return _begin_report(state, existing, _saved_packet(state, existing), replayed=True)
        if state["execution"]["revision"] != expected_revision:
            raise goal.RevisionConflict("Execution revision changed during packet preparation")
        _admit(state)
        if tree_hash.compute(state["cwd"], state["watch_paths"]) != current_hash:
            raise goal.RevisionConflict("Project snapshot changed during packet preparation")
        try:
            current_procedure = task_state.procedure(paths.runtime())
        except task_state.StateError as error:
            raise StepConflict(f"cannot verify Horizon procedure: {error}") from error
        if current_procedure["sha256"] != packet["procedure"]["sha256"]:
            raise goal.RevisionConflict("Horizon procedure changed during packet preparation")
        if read_blob(packet_ref, Path(state["cwd"])) != packet_bytes:
            raise StepConflict("saved packet bytes changed during preparation")
        attempt = {
            "id": request_id,
            "turn": state["used"]["turns"] + 1,
            "base_revision": expected_revision + 1,
            "status": "reserved",
            "packet": packet_ref,
            "procedure_sha256": packet["procedure"]["sha256"],
            "worker": None,
            "gate_process": None,
            "finish_nonce": None,
            "update_sha256": None,
            "pending": None,
            "events": [],
            "result": None,
        }
        state["used"]["turns"] = attempt["turn"]
        state["execution"]["active_attempt"] = request_id
        state["execution"]["attempts"].append(attempt)
        state = goal.commit_revision(state, expected_revision=expected_revision)
    return _begin_report(state, attempt, packet, replayed=False)


def attach(slug: str, attempt_id: str, worker: str, *, expected_revision: int) -> dict:
    """Record the actual native worker handle for a reserved attempt."""
    _require_slug(slug)
    _require_id(attempt_id, "attempt_id")
    if not isinstance(worker, str) or not worker.strip():
        raise StepError("worker handle must be nonempty text")
    if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) or expected_revision < 0:
        raise StepError("expected_revision must be a nonnegative integer")
    with filelock.goal_locked(goal._path(slug)):
        state = _state(slug)
        attempt = _attempt(state, attempt_id)
        if attempt is None:
            raise StepConflict(f"unknown attempt: {attempt_id}")
        _saved_packet(state, attempt)
        binding = {"runtime": paths.runtime(), "handle": worker}
        if attempt["worker"] is not None:
            if attempt["worker"] != binding:
                raise StepConflict("attempt is already bound to a different worker")
            return report(state, attempt, replayed=True)
        if state["execution"]["revision"] != expected_revision:
            raise goal.RevisionConflict("Execution revision changed before worker binding")
        if attempt["status"] != "reserved" or state["execution"]["active_attempt"] != attempt_id:
            raise StepConflict("attempt is not available for worker binding")
        try:
            current_procedure = task_state.procedure(paths.runtime())
        except task_state.StateError as error:
            raise StepConflict(f"cannot verify Horizon procedure: {error}") from error
        if current_procedure["sha256"] != attempt["procedure_sha256"]:
            raise StepConflict("Horizon procedure changed since reservation")
        attempt["worker"] = binding
        attempt["status"] = "attached"
        state = goal.commit_revision(state, expected_revision=expected_revision)
    return report(state, attempt)
