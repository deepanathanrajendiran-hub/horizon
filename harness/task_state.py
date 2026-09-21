import copy
import json
import hashlib
from pathlib import Path
import stat
import re
import math


class StateError(ValueError):
    pass


def merge_update(task: dict, update: dict, *, attempt_id: str,
                 base_revision: int, project: Path, provenance: dict) -> tuple[dict, list[dict]]:
    validate_task(task)
    _validate_update(update, attempt_id, base_revision)
    _validate_provenance(provenance)
    if provenance["attempt_id"] != attempt_id:
        raise StateError("provenance attempt_id does not match reserved attempt")
    candidate = copy.deepcopy(task)
    audit = []
    indexes = {entry["id"]: index for index, entry in enumerate(candidate["entries"])}
    touched_fields = set()
    touched_entries = set()
    for operation in update["ops"]:
        _validate_operation(operation)
        kind = operation["op"]
        if kind == "set":
            field = operation["field"]
            if field in touched_fields:
                raise StateError(f"contradictory operations for {field}")
            touched_fields.add(field)
            before = candidate[field]
            candidate[field] = operation["value"]
            audit.append({"id": field, "before": before, "after": candidate[field], "reason": None})
            continue
        entry_id = operation.get("id", operation.get("entry", {}).get("id"))
        if entry_id in touched_entries:
            raise StateError(f"contradictory operations for entry ID: {entry_id}")
        touched_entries.add(entry_id)
        if kind == "archive":
            if entry_id not in indexes:
                raise StateError(f"unknown entry ID: {entry_id}")
            index = indexes.pop(entry_id)
            before = candidate["entries"][index]
            candidate["entries"][index] = None
            audit.append({"id": entry_id, "before": before, "after": None,
                          "reason": operation["reason"]})
        else:
            worker_entry = operation["entry"]
            _verify_entry_evidence(worker_entry, Path(project))
            accepted = copy.deepcopy(worker_entry)
            accepted["provenance"] = copy.deepcopy(provenance)
            if entry_id in indexes:
                index = indexes[entry_id]
                before = candidate["entries"][index]
                candidate["entries"][index] = accepted
            else:
                before = None
                indexes[entry_id] = len(candidate["entries"])
                candidate["entries"].append(accepted)
            audit.append({"id": entry_id, "before": before, "after": accepted, "reason": None})
    candidate["entries"] = [entry for entry in candidate["entries"] if entry is not None]
    validate_task(candidate)
    return candidate, audit

PACKET_LIMIT = 24 * 1024
UPDATE_LIMIT = 64 * 1024
SUMMARY_LIMIT = 2 * 1024
TEXT_LIMIT = 1024
EXCERPT_LIMIT = 2 * 1024
ENTRY_LIMIT = 64
EVIDENCE_LIMIT = 4

_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_HASH_RE = re.compile(r"[0-9a-f]{64}\Z")
_KINDS = {"observation", "question", "hypothesis", "failed_approach"}

re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
re.compile(r"[0-9a-f]{64}\Z")
_KINDS = {"observation", "question", "hypothesis", "failed_approach"}


def canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_text(value, field: str, limit: int, *, nonempty: bool = False) -> None:
    if not isinstance(value, str):
        raise StateError(f"{field} must be text fitting {limit} UTF-8 bytes")
    size = len(value.encode("utf-8"))
    if size > limit:
        raise StateError(f"{field} must fit {limit} UTF-8 bytes (got {size})")
    if nonempty and not value.strip():
        raise StateError(f"{field} must be nonempty")


def _require_id(value, field: str) -> None:
    if not isinstance(value, str) or not _ID_RE.fullmatch(value):
        raise StateError(f"{field} must match [A-Za-z0-9][A-Za-z0-9_-]{{0,63}}")


def _require_hash(value, field: str) -> None:
    if not isinstance(value, str) or not _HASH_RE.fullmatch(value):
        raise StateError(f"{field} must be a lowercase SHA-256 digest")


def _require_keys(value, fields: set[str], field: str) -> None:
    if not isinstance(value, dict) or set(value) != fields:
        raise StateError(f"{field} fields must be {', '.join(sorted(fields))}")


def _validate_provenance(provenance: dict) -> None:
    _require_keys(provenance, {"attempt_id", "tree_hash", "at"}, "entry.provenance")
    _require_id(provenance["attempt_id"], "entry.provenance.attempt_id")
    if not isinstance(provenance["tree_hash"], str) or not provenance["tree_hash"]:
        raise StateError("entry.provenance.tree_hash must be nonempty text")
    if not isinstance(provenance["at"], str) or not provenance["at"]:
        raise StateError("entry.provenance.at must be nonempty text")


def validate_entry(entry: dict, *, accepted: bool) -> None:
    fields = {"id", "kind", "statement", "evidence"}
    if accepted:
        fields.add("provenance")
    _require_keys(entry, fields, "entry")
    _require_id(entry["id"], "entry.id")
    if entry["kind"] not in _KINDS:
        raise StateError("entry.kind must be observation, question, hypothesis, or failed_approach")
    _require_text(entry["statement"], "entry.statement", TEXT_LIMIT, nonempty=True)
    evidence = entry["evidence"]
    if not isinstance(evidence, list) or len(evidence) > EVIDENCE_LIMIT:
        raise StateError(f"entry.evidence must contain at most {EVIDENCE_LIMIT} references")
    if entry["kind"] in {"observation", "failed_approach"} and not evidence:
        raise StateError("entry.evidence is required for observations and failed approaches")
    seen = set()
    for index, reference in enumerate(evidence):
        _require_keys(reference, {"path", "sha256"}, f"entry.evidence[{index}]")
        if not isinstance(reference["path"], str) or not reference["path"]:
            raise StateError(f"entry.evidence[{index}].path must be nonempty text")
        _require_hash(reference["sha256"], f"entry.evidence[{index}].sha256")
        if reference["path"] in seen:
            raise StateError(f"duplicate evidence path: {reference['path']}")
        seen.add(reference["path"])
    if accepted:
        _validate_provenance(entry["provenance"])


def validate_task(task: dict) -> None:
    _require_keys(task, {"summary", "entries", "next_action"}, "task")
    _require_text(task["summary"], "task.summary", SUMMARY_LIMIT)
    _require_text(task["next_action"], "task.next_action", TEXT_LIMIT, nonempty=True)
    if not isinstance(task["entries"], list) or len(task["entries"]) > ENTRY_LIMIT:
        raise StateError(f"task.entries must contain at most {ENTRY_LIMIT} entries")
    seen = set()
    for entry in task["entries"]:
        validate_entry(entry, accepted=True)
        if entry["id"] in seen:
            raise StateError(f"duplicate entry ID: {entry['id']}")
        seen.add(entry["id"])


def new_execution() -> dict:
    return {"mode": "state", "schema_version": 1, "revision": 0,
            "task": {"summary": "", "entries": [],
                     "next_action": "Inspect the project and establish the next useful increment."},
            "active_attempt": None, "attempts": []}


def validate_execution(execution: dict) -> None:
    _require_keys(execution, {"mode", "schema_version", "revision", "task", "active_attempt", "attempts"}, "execution")
    if execution["mode"] != "state" or not _is_int(execution["schema_version"]) or execution["schema_version"] != 1:
        raise StateError("execution.schema_version must be integer 1 for state mode")
    if not _is_int(execution["revision"]) or execution["revision"] < 0:
        raise StateError("execution.revision must be a nonnegative integer")
    validate_task(execution["task"])
    if execution["active_attempt"] is not None:
        _require_id(execution["active_attempt"], "execution.active_attempt")
    if not isinstance(execution["attempts"], list):
        raise StateError("execution.attempts must be a list")
    seen = set()
    nonterminal = []
    for attempt in execution["attempts"]:
        _validate_attempt(attempt)
        if attempt["id"] in seen:
            raise StateError(f"duplicate attempt ID: {attempt['id']}")
        seen.add(attempt["id"])
        if attempt["status"] in {"reserved", "attached", "finishing"}:
            nonterminal.append(attempt["id"])
    if len(nonterminal) > 1:
        raise StateError("execution has more than one nonterminal attempt")
    if nonterminal:
        if execution["active_attempt"] != nonterminal[0]:
            raise StateError("execution.active_attempt must identify the nonterminal attempt")
    elif execution["active_attempt"] is not None:
        raise StateError("execution.active_attempt requires a nonterminal attempt")

def _validate_reference(reference, field: str) -> None:
    _require_keys(reference, {"path", "sha256"}, field)
    if not isinstance(reference["path"], str) or not reference["path"]:
        raise StateError(f"{field}.path must be nonempty text")
    _require_hash(reference["sha256"], f"{field}.sha256")


def _validate_worker(worker) -> None:
    if worker is None:
        return
    _require_keys(worker, {"runtime", "handle"}, "attempt.worker")
    if not isinstance(worker["runtime"], str) or worker["runtime"] not in {"codex", "claude"}:
        raise StateError("attempt.worker.runtime must be codex or claude")
    if not isinstance(worker["handle"], str) or not worker["handle"]:
        raise StateError("attempt.worker.handle must be nonempty text")


def _validate_gate_process(process) -> None:
    if process is None:
        return
    _require_keys(process, {"pid", "pgid", "started_at", "finish_nonce"}, "attempt.gate_process")
    for field in ("pid", "pgid"):
        if not _is_int(process[field]) or process[field] <= 0:
            raise StateError(f"attempt.gate_process.{field} must be a positive integer")
    for field in ("started_at", "finish_nonce"):
        if not isinstance(process[field], str) or not process[field]:
            raise StateError(f"attempt.gate_process.{field} must be nonempty text")


def _validate_pending(pending) -> None:
    if pending is None:
        return
    _require_keys(pending, {"update", "candidate", "audit", "worker_evidence"}, "attempt.pending")
    for field in ("update", "candidate", "audit", "worker_evidence"):
        _validate_reference(pending[field], f"attempt.pending.{field}")


def _validate_attempt(attempt: dict) -> None:
    _require_keys(attempt, {"id", "turn", "base_revision", "status", "packet",
                            "procedure_sha256", "worker", "gate_process", "finish_nonce",
                            "update_sha256", "pending", "events", "result"}, "attempt")
    _require_id(attempt["id"], "attempt.id")
    for field in ("turn", "base_revision"):
        if not _is_int(attempt[field]) or attempt[field] < 0:
            raise StateError(f"attempt.{field} must be a nonnegative integer")
    if not isinstance(attempt["status"], str) or attempt["status"] not in {"reserved", "attached", "finishing", "accepted", "recovered"}:
        raise StateError("attempt.status is invalid")
    _validate_reference(attempt["packet"], "attempt.packet")
    _require_hash(attempt["procedure_sha256"], "attempt.procedure_sha256")
    _validate_worker(attempt["worker"])
    _validate_gate_process(attempt["gate_process"])
    if attempt["finish_nonce"] is not None and (not isinstance(attempt["finish_nonce"], str) or not attempt["finish_nonce"]):
        raise StateError("attempt.finish_nonce must be null or nonempty text")
    if attempt["update_sha256"] is not None:
        _require_hash(attempt["update_sha256"], "attempt.update_sha256")
    _validate_pending(attempt["pending"])
    if not isinstance(attempt["events"], list):
        raise StateError("attempt.events must be a list")
    if attempt["result"] is not None and not isinstance(attempt["result"], dict):
        raise StateError("attempt.result must be null or an object")
    status = attempt["status"]
    if status == "reserved" and any((attempt["worker"] is not None, attempt["pending"] is not None,
                                       attempt["update_sha256"] is not None, attempt["result"] is not None)):
        raise StateError("reserved attempt has lifecycle fields set")
    if status == "attached" and (attempt["worker"] is None or attempt["pending"] is not None or
                                   attempt["update_sha256"] is not None or attempt["result"] is not None):
        raise StateError("attached attempt has inconsistent lifecycle fields")
    if status == "finishing" and (attempt["worker"] is None or attempt["pending"] is None or attempt["result"] is not None):
        raise StateError("finishing attempt has inconsistent lifecycle fields")
    if status == "accepted" and (attempt["worker"] is None or attempt["pending"] is None or
                                   attempt["update_sha256"] is None or attempt["result"] is None):
        raise StateError("accepted attempt has inconsistent lifecycle fields")
    if status == "recovered" and attempt["result"] is None:
        raise StateError("recovered attempt requires a result")


def _no_duplicates(pairs):
    output = {}
    for key, value in pairs:
        if key in output:
            raise StateError(f"duplicate key in JSON input: {key}")
        output[key] = value
    return output


def _nonfinite(value):
    raise StateError(f"non-finite JSON value: {value}")

def _finite_float(value: str) -> float:
    parsed = float(value)
    if not math.isfinite(parsed):
        raise StateError(f"non-finite JSON value: {value}")
    return parsed


def read_update(path: Path) -> dict:
    try:
        with Path(path).open("rb") as stream:
            raw = stream.read(UPDATE_LIMIT + 1)
    except OSError as error:
        raise StateError(f"cannot read update: {error}") from error
    if len(raw) > UPDATE_LIMIT:
        raise StateError(f"update must fit {UPDATE_LIMIT} UTF-8 bytes (got {len(raw)})")
    try:
        update = json.loads(raw.decode("utf-8"), object_pairs_hook=_no_duplicates,
                            parse_constant=_nonfinite, parse_float=_finite_float)
    except (UnicodeDecodeError, json.JSONDecodeError, StateError) as error:
        raise StateError(f"invalid update JSON: {error}") from error
    if not isinstance(update, dict):
        raise StateError("update JSON must be an object")
    return update


def _resolve_reference(reference: dict, project: Path) -> Path:
    root = Path(project).resolve(strict=True)
    requested = Path(reference["path"])
    if requested.is_absolute():
        raise StateError("evidence path must be project-relative")
    try:
        target = (root / requested).resolve(strict=True)
    except OSError as error:
        raise StateError(f"evidence is missing: {reference['path']}") from error
    try:
        target.relative_to(root)
    except ValueError as error:
        raise StateError("evidence path escapes project") from error
    try:
        mode = target.stat().st_mode
    except OSError as error:
        raise StateError(f"cannot stat evidence: {reference['path']}") from error
    if not stat.S_ISREG(mode):
        raise StateError(f"evidence must be a regular file: {reference['path']}")
    return target


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise StateError(f"cannot read evidence: {path}") from error
    return digest.hexdigest()


def _verify_entry_evidence(entry: dict, project: Path) -> None:
    for reference in entry["evidence"]:
        target = _resolve_reference(reference, project)
        actual = _hash_file(target)
        if actual != reference["sha256"]:
            raise StateError(f"evidence.sha256 does not match {reference['path']}")


def inspect_evidence(entry: dict, project: Path, current_hash: str) -> dict:
    result = {"id": entry.get("id"), "freshness": "current", "reasons": []}
    provenance = entry.get("provenance")
    if current_hash == "unknown":
        result["freshness"] = "unverified"
        result["reasons"].append("current snapshot hash is unknown")
    elif not isinstance(provenance, dict):
        result["freshness"] = "unverified"
        result["reasons"].append("entry has no provenance")
    elif provenance.get("tree_hash") != current_hash:
        result["freshness"] = "stale"
        result["reasons"].append("provenance tree hash does not match the current snapshot")
    for reference in entry.get("evidence", []):
        try:
            target = _resolve_reference(reference, project)
            if _hash_file(target) != reference.get("sha256"):
                result["reasons"].append(f"evidence hash changed: {reference.get('path')}")
        except StateError as error:
            result["reasons"].append(str(error))
    if result["reasons"] and result["freshness"] == "current":
        result["freshness"] = "unverified"
    return result

def _validate_update(update: dict, attempt_id: str, base_revision: int) -> None:
    _require_keys(update, {"schema_version", "attempt_id", "base_revision", "did", "ops"}, "update")
    if not _is_int(update["schema_version"]) or update["schema_version"] != 1:
        raise StateError("update.schema_version must be integer 1")
    _require_id(update["attempt_id"], "update.attempt_id")
    if update["attempt_id"] != attempt_id:
        raise StateError("update.attempt_id does not match reserved attempt")
    if not _is_int(update["base_revision"]):
        raise StateError("update.base_revision must be an integer, not boolean")
    if update["base_revision"] != base_revision:
        raise StateError("update.base_revision does not match reserved revision")
    _require_text(update["did"], "update.did", TEXT_LIMIT, nonempty=True)
    if not isinstance(update["ops"], list):
        raise StateError("update.ops must be a list")


def _validate_operation(operation: dict) -> None:
    if not isinstance(operation, dict) or not isinstance(operation.get("op"), str):
        raise StateError("update operation must be an object with op")
    if operation["op"] == "set":
        _require_keys(operation, {"op", "field", "value"}, "set operation")
        if operation["field"] not in {"summary", "next_action"}:
            raise StateError("set operation field must be summary or next_action")
        limit = SUMMARY_LIMIT if operation["field"] == "summary" else TEXT_LIMIT
        _require_text(operation["value"], f"task.{operation['field']}", limit,
                      nonempty=operation["field"] == "next_action")
    elif operation["op"] == "upsert":
        _require_keys(operation, {"op", "entry"}, "upsert operation")
        validate_entry(operation["entry"], accepted=False)
    elif operation["op"] == "archive":
        _require_keys(operation, {"op", "id", "reason"}, "archive operation")
        _require_id(operation["id"], "archive operation.id")
        _require_text(operation["reason"], "archive operation.reason", TEXT_LIMIT, nonempty=True)
    else:
        raise StateError(f"unknown update operation: {operation['op']}")


def _packet_excerpt(value, record: str, reserve: bool) -> dict:
    text = "" if value is None else str(value)
    if reserve:
        text = "\\x00" * EXCERPT_LIMIT
    low, high = 0, len(text)
    while low < high:
        middle = (low + high + 1) // 2
        candidate = {"text": text[:middle], "truncated": middle < len(text), "record": record}
        if len(canonical_bytes(candidate)) <= EXCERPT_LIMIT:
            low = middle
        else:
            high = middle - 1
    return {"text": text[:low], "truncated": low < len(text), "record": record}


def _latest_checkpoint(state: dict, reserve: bool):
    checkpoints = state.get("checkpoints", [])
    if not checkpoints:
        return None
    checkpoint = copy.deepcopy(checkpoints[-1])
    output = checkpoint.pop("gate_output", None)
    checkpoint["verdict"] = _packet_excerpt(output, f"checkpoint:{checkpoint.get('turn', 'latest')}", reserve)
    return checkpoint


def _latest_review(state: dict, reserve: bool):
    reviews = state.get("reviews", [])
    if not reviews:
        return None
    review = copy.deepcopy(reviews[-1])
    for key in ("evidence", "result", "output", "verdict"):
        if key in review and isinstance(review[key], str):
            review[key] = _packet_excerpt(review[key], f"review:{review.get('turn', 'latest')}", reserve)
            break
    return review

def _with_packet_reservations(packet: dict) -> dict:
    reserved = copy.deepcopy(packet)
    checkpoint = reserved["latest_checkpoint"]
    if checkpoint is None:
        reserved["latest_checkpoint"] = _packet_excerpt(None, "checkpoint:reserved", True)
    else:
        checkpoint["verdict"] = _packet_excerpt(
            None, f"checkpoint:{checkpoint.get('turn', 'latest')}", True)
    review = reserved["latest_review"]
    if review is None:
        reserved["latest_review"] = _packet_excerpt(None, "review:reserved", True)
    else:
        for field in ("evidence", "result", "output", "verdict"):
            if field in review and isinstance(review[field], dict):
                review[field] = _packet_excerpt(
                    None, f"review:{review.get('turn', 'latest')}", True)
                break
        else:
            review["verdict"] = _packet_excerpt(
                None, f"review:{review.get('turn', 'latest')}", True)
    for entry in reserved["task"]["entries"]:
        entry["freshness"] = "unverified"
    for entry in reserved["snapshot"]["evidence"]:
        entry["freshness"] = "unverified"
    return reserved


def procedure(runtime: str) -> dict:
    skill_names = {"codex": ".agents/skills/horizon/SKILL.md",
                   "claude": ".claude/skills/horizon/SKILL.md"}
    if runtime not in skill_names:
        raise StateError("runtime must be codex or claude")
    skill_path = Path(__file__).resolve().parents[1] / skill_names[runtime]
    try:
        skill_bytes = skill_path.read_bytes()
    except OSError as error:
        raise StateError(f"cannot read {runtime} Horizon skill from checkout: {error}") from error
    instructions = (
        "Return one schema-version-1 JSON update. Include only set, upsert, and archive operations. "
        "Do not include lifecycle fields or entry provenance. Evidence references must name files inside the project."
    )
    digest = hashlib.sha256(instructions.encode("utf-8") + skill_bytes).hexdigest()
    return {"schema_version": 1, "instructions": instructions, "sha256": digest}


def build_packet(goal_state: dict, *, attempt_id: str, base_revision: int,
                 current_hash: str, runtime: str, reserve_verdicts: bool = False) -> dict:
    _require_id(attempt_id, "attempt_id")
    if not _is_int(base_revision) or base_revision < 0:
        raise StateError("base_revision must be a nonnegative integer")
    execution = goal_state.get("execution")
    if not isinstance(execution, dict):
        raise StateError("goal state has no execution object")
    validate_execution(execution)
    for field in ("goal", "gate", "watch_paths", "budget", "used"):
        if field not in goal_state:
            raise StateError(f"goal state missing {field}")
    if not isinstance(goal_state["goal"], str):
        raise StateError("goal must be text")
    if not isinstance(goal_state["gate"], dict):
        raise StateError("gate must be an object")
    if not isinstance(goal_state["watch_paths"], list) or not all(isinstance(path, str) for path in goal_state["watch_paths"]):
        raise StateError("watch_paths must be a list of text paths")
    try:
        project = Path(goal_state.get("cwd", ".")).resolve(strict=True)
    except OSError:
        project = Path.cwd().resolve()
    annotations = [inspect_evidence(entry, project, current_hash)
                   for entry in execution["task"]["entries"]]
    packet_task = copy.deepcopy(execution["task"])
    for entry, annotation in zip(packet_task["entries"], annotations):
        entry["freshness"] = annotation["freshness"]
        entry["freshness_reasons"] = annotation["reasons"]
    packet = {
        "schema_version": 1,
        "attempt_id": attempt_id,
        "base_revision": base_revision,
        "objective": goal_state["goal"],
        "gate": copy.deepcopy(goal_state["gate"]),
        "watch_paths": list(goal_state["watch_paths"]),
        "budget": copy.deepcopy(goal_state["budget"]),
        "used": copy.deepcopy(goal_state["used"]),
        "procedure": procedure(runtime),
        "task": packet_task,
        "snapshot": {"tree_hash": current_hash, "evidence": annotations},
        "latest_checkpoint": _latest_checkpoint(goal_state, reserve_verdicts),
        "latest_review": _latest_review(goal_state, reserve_verdicts),
        "audit_path": goal_state.get("audit_path"),
    }
    actual_size = len(canonical_bytes(packet))
    if actual_size > PACKET_LIMIT:
        raise StateError(f"packet must fit {PACKET_LIMIT} UTF-8 bytes (got {actual_size})")
    if reserve_verdicts:
        reserved_size = len(canonical_bytes(_with_packet_reservations(packet)))
        if reserved_size > PACKET_LIMIT:
            raise StateError(f"packet must fit {PACKET_LIMIT} UTF-8 bytes (got {reserved_size})")
    return packet
