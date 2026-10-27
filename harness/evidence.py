"""Evidence rules for completion.

Which recorded gate result covers the files as they are now, which test files
changed since the baseline, and whether a reviewer is still required. Kept
apart from goal.py so the rules are tested once, without the lifecycle.
"""

import hashlib
import os
import re
from pathlib import Path

import tree_hash
import shlex

_TEST_PATTERNS = (
    re.compile(r"(^|/)tests?/"),
    re.compile(r"(^|/)test_[^/]*\.py$"),
    re.compile(r"_test\.[^/]+$"),
    re.compile(r"\.(test|spec)\.[^/]+$"),
)


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _is_harness_state(rel: str) -> bool:
    parts = rel.split("/")
    return any(parts[i].lower() in (".claude", ".codex") and parts[i + 1:i + 2] == ["harness"]
               for i in range(len(parts)))


def _is_test(rel: str) -> bool:
    return any(p.search(rel) for p in _TEST_PATTERNS)


def _watched_files(cwd: str, watch_paths: list[str] | None):
    """(project-relative path, path) for every file in the watched paths, harness state excluded."""
    root = Path(cwd).resolve()
    for watch in watch_paths or [cwd]:
        base = Path(watch).resolve()
        if base.is_file():
            files = [base]
        else:
            files = []
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = [d for d in dirnames if d not in tree_hash.EXCLUDED_DIRS]
                files += [Path(dirpath) / name for name in filenames]
        for path in files:
            try:
                rel = path.relative_to(root).as_posix()
            except ValueError:
                rel = path.as_posix()
            if not _is_harness_state(rel):
                yield rel, path


def _digests(files) -> dict[str, str]:
    out: dict[str, str] = {}
    for rel, path in files:
        try:
            out[rel] = _sha(path)
        except OSError:
            continue
    return out


def test_manifest(cwd: str, watch_paths: list[str] | None) -> dict[str, str]:
    """sha256 of every test file in the watched paths, keyed by project-relative path."""
    return _digests((rel, path) for rel, path in _watched_files(cwd, watch_paths) if _is_test(rel))

_GATE_CONFIG_NAMES = {"conftest.py", "pytest.ini", "tox.ini"}


def _command_files(cwd: str, command: str):
    """Project files the gate command names, such as the script in `sh run_tests.sh`."""
    root = Path(cwd).resolve()
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = command.split()
    for token in tokens:
        for word in token.split():
            for part in (word, word.partition("=")[2]):
                if not part:
                    continue
                path = (root / part).resolve()
                try:
                    rel = path.relative_to(root).as_posix()
                except ValueError:
                    continue
                if path.is_file() and not _is_harness_state(rel):
                    yield rel, path


def gate_manifest(cwd: str, command: str, watch_paths: list[str] | None) -> dict[str, str]:
    """sha256 of the files that decide what the gate runs: files its command names,
    and conftest.py, pytest.ini and tox.ini in the watched paths."""
    files = dict(_command_files(cwd, command))
    files.update((rel, path) for rel, path in _watched_files(cwd, watch_paths)
                 if path.name in _GATE_CONFIG_NAMES)
    return _digests(files.items())

_HASH = re.compile(r"[0-9a-f]{64}")
_REVIEW_TAGS = {"review", "unverifiable", "judgment"}


def passing_record(state: dict, observed_hash: str) -> dict | None:
    """The latest pass recorded for exactly these files and this gate, or None."""
    if not isinstance(observed_hash, str) or not _HASH.fullmatch(observed_hash):
        return None
    records = [*state.get("checkpoints", []), *state.get("observations", [])]
    matches = [r for r in records
               if r.get("gate") == "pass" and r.get("tree_hash") == observed_hash
               and r.get("gate_spec") == state["gate"]]
    return max(matches, key=lambda r: r.get("at", "")) if matches else None


def _current_tests(state: dict) -> dict[str, str]:
    return test_manifest(state["cwd"], state.get("watch_paths"))


def changed_baseline_tests(state: dict) -> list[str]:
    """Baseline test files now changed or deleted, minus approved changes."""
    base = state.get("test_manifest") or {}
    now = _current_tests(state)
    approved = {(a["path"], a["sha256"]) for a in state.get("approvals", [])}
    return sorted(rel for rel, digest in base.items()
                  if now.get(rel) != digest and (rel, now.get(rel, "deleted")) not in approved)


def added_tests(state: dict) -> list[str]:
    """Test files that did not exist at the baseline."""
    if "test_manifest" not in state:
        return sorted(_current_tests(state))
    return sorted(set(_current_tests(state)) - set(state["test_manifest"]))

def changed_gate_files(state: dict) -> list[str] | None:
    """Gate scripts and configuration changed, added or deleted since creation.

    None when the goal predates the record, so nothing can be compared.
    """
    if "gate_manifest" not in state:
        return None
    base = state["gate_manifest"]
    now = gate_manifest(state["cwd"], state["gate"]["value"], state.get("watch_paths"))
    return sorted(rel for rel in set(base) | set(now) if base.get(rel) != now.get(rel))


def gate_is_strong(state: dict) -> bool:
    """A command gate over tests that existed before the work and are unchanged,
    run by gate scripts and configuration that are unchanged too.

    A gate directory means the agent wrote the gate, so it is never strong.
    """
    return (state["gate"]["kind"] == "command" and not state.get("gate_path")
            and bool(state.get("test_manifest"))
            and not changed_baseline_tests(state) and not added_tests(state)
            and changed_gate_files(state) == [])


def review_reasons(state: dict, requirements: dict | None) -> list[str]:
    """Why a reviewer must still pass this snapshot; empty means no review is needed."""
    if state["gate"]["kind"] == "coverage":
        return ["coverage gates are judged by a reviewer"]
    if not state["gate"].get("review_criterion"):
        return []
    reasons = []
    if not gate_is_strong(state):
        gate_files = changed_gate_files(state)
        if gate_files:
            reasons.append("the gate is not strong: its own scripts or configuration changed "
                           "during this goal: " + ", ".join(gate_files))
        else:
            reasons.append("the gate is not strong: its tests were written or changed during this goal")
    tagged = sorted(rid for rid, tags in (requirements or {}).items() if set(tags) & _REVIEW_TAGS)
    if tagged:
        reasons.append("requirement lines need review: " + ", ".join(tagged))
    return reasons
