"""Requirement lines, reviewer verdicts and the reviewer brief (Horizon v2).

In the TB4 pilot every recorded review was "pass" while four reviewer
replies said FAIL, and every stored verdict was the author's paraphrase.
The brief therefore ends with a VERDICTS block the reviewer fills in, one
line per requirement, and the ledger reads that block strictly. Two probes
showed that reading verdicts out of free prose fails both ways ("PARTIAL
PASS" passed; "the previous FAIL is fixed" was refused), so prose is only
checked for the two unambiguous signals: a `Rk: FAIL` line and an overall
FAIL headline.
"""
import json
import os
import re
import tempfile
from pathlib import Path

HASH_ECHO = 12
BRIEF_FILE_LIMIT = 32000

_RANK = {"FAIL": 4, "UNVERIFIED": 3, "NOT REVIEWED": 2, "PASS": 1}

# R1, R12, R1a, R1.2: every id a requirements file can use, read the same way
# in requirements.md and in the VERDICTS block.
_ID = r"R(\d+(?:\.\d+)*[a-z]?)(?!\w)"
_REQUIREMENT = re.compile(
    r"^[ \t ]*(?:#{1,6}[ \t]+)?(?:[-*+•][ \t]+)?(?:\[[ xX]\][ \t]+)?"
    r"(?:\d+[.)][ \t]+)?[*_`]*" + _ID)
_TAG = re.compile(r"\[(gate|review|unverifiable|judgment)\]", re.I)
_CROSS = "❌✖✗✘"
# `Rk: VERDICT`, allowing a bullet, emphasis or backticks and one check mark
# or cross -- nothing that could carry a second verdict. A cross makes it FAIL.
_BLOCK_LINE = re.compile(
    r"^[ \t]*(?:[-*+•][ \t]+)?[*_`]*" + _ID + r"[*_`]*[ \t]*:[ \t]*[*_`]*[ \t]*[*_`]*"
    r"(PASS|FAIL|UNVERIFIED|NOT[ \t]+REVIEWED)[*_`]*[ \t]*"
    r"([✅✔" + _CROSS + r"]️?)?[ \t]*$", re.I)
_FENCE = re.compile(r"^[ \t]*(?:```|~~~)[\w+.-]*[ \t]*$")
# A prose line in the `Rk: VERDICT - evidence` shape whose verdict is FAIL or
# UNVERIFIED; tags or a sub-case may sit in brackets after the id. The verdict
# must come right after the separator, so "R2: PASS - the old FAIL is fixed"
# is not one.
_OPEN = re.compile(
    r"^[ \t]*(?:[-*+•][ \t]+)?[*_]*" + _ID + r"[*_]*"
    r"(?:[ \t]*[\[(][^\])\n]*[\])])*[ \t]*[*_]*[ \t]*[:\-–—][ \t]*[*_]*[ \t]*"
    r"(FAIL(?:ED|S)?|UNVERIFIED)(?!\w)", re.I)
# "Verdict: FAIL", "1. **Overall:** FAIL", "Review verdict: FAIL", "Summary: ❌":
# a label, then FAIL at once -- not "Status: PASS (FAIL: 0)".
_LABEL_FAIL = re.compile(
    r"^[\W_]*(?:\d+[.)][\W_]*)?(?:[A-Za-z]+[ \t]+)?"
    r"(?:verdict|overall|result|status|conclusion|outcome|assessment|summary)\b"
    r"[\W_]*?(?:fail(?:ed|s)?\b|[" + _CROSS + r"])", re.I)
# "**FAIL.** The merged session ..." -- but not "**Failed:** 0".
_BOLD_FAIL = re.compile(r"^[ \t#]*(?:\*\*|__)[ \t]*fail(?:ed)?[.!]?[ \t]*(?:\*\*|__)", re.I)
# "FAIL", "Fail.", "## ❌ FAIL" alone -- not "FAIL: none" or "--- FAIL: TestX".
_ALONE_FAIL = re.compile(r"^[ \t#*_" + _CROSS + r"]*fail(?:ed)?[ \t*_.!]*$", re.I)


def _rid(raw: str) -> str:
    number, rest = re.match(r"(\d+)(.*)", raw).groups()
    return f"R{int(number)}{rest}"


def _order(rid: str) -> list:
    return [(0, int(part)) if part.isdigit() else (1, part)
            for part in re.findall(r"\d+|[a-z]", rid[1:])]


def _kind(word: str) -> str:
    word = word.upper()
    if word.startswith("PASS"):
        return "PASS"
    if word.startswith("FAIL"):
        return "FAIL"
    if word.startswith("NOT"):
        return "NOT REVIEWED"
    return "UNVERIFIED"


def requirement_lines(text: str) -> dict[str, set[str]]:
    """Map each requirement id (R1, R2, R1a, R1.2, ...) to the tags on its line,
    in the order the ids first appear."""
    found: dict[str, set[str]] = {}
    for line in text.splitlines():
        match = _REQUIREMENT.match(line)
        if match:
            tags = found.setdefault(_rid(match.group(1)), set())
            tags.update(tag.lower() for tag in _TAG.findall(line))
    return found


def _read(evidence: str):
    """Split a reply into VERDICTS blocks and the prose around them.

    Returns (blocks, errors, body): each block is a list of (index, line);
    body is the prose lines that are neither quoted with `>` (an earlier
    reviewer) nor inside a code fence (test output).
    """
    blocks, errors, body = [], [], []
    current = None
    fenced = False
    for index, line in enumerate(evidence.splitlines()):
        quoted = line.lstrip().startswith(">")
        mark = re.sub(r"[\s*_#`]", "", line)
        if not quoted and mark == "VERDICTS":
            if current is not None:
                errors.append("a VERDICTS block starts inside another")
                blocks.append(current)
            current = []
            continue
        if not quoted and mark == "ENDVERDICTS":
            if current is None:
                errors.append("END VERDICTS without a VERDICTS line")
            else:
                blocks.append(current)
                current = None
            continue
        if current is not None:
            if line.strip() and not _FENCE.match(line):
                current.append((index, line))
            continue
        if _FENCE.match(line):
            fenced = not fenced
            continue
        if not quoted and not fenced:
            body.append((index, line))
    if current is not None:
        errors.append("VERDICTS block has no END VERDICTS line")
        blocks.append(current)
    return blocks, errors, body


def _block_kind(match) -> str:
    if match.group(3) and match.group(3)[0] in _CROSS:
        return "FAIL"
    return _kind(match.group(2))


def _block_verdicts(entries, errors: list) -> dict[str, str]:
    verdicts: dict[str, str] = {}
    for _, line in entries:
        match = _BLOCK_LINE.match(line)
        if not match:
            errors.append(f"unreadable VERDICTS line: {line.strip()[:100]}")
            continue
        rid, kind = _rid(match.group(1)), _block_kind(match)
        if rid in verdicts:
            errors.append(f"{rid}: more than one verdict in the VERDICTS block")
            kind = max(kind, verdicts[rid], key=_RANK.get)
        verdicts[rid] = kind
    return verdicts


def _open_lines(body):
    for index, line in body:
        match = _OPEN.match(line)
        if match:
            yield index, _rid(match.group(1)), _kind(match.group(2)), line.strip()


def _headlines(body):
    for index, line in body:
        if not _OPEN.match(line) and (
                _LABEL_FAIL.match(line) or _BOLD_FAIL.match(line) or _ALONE_FAIL.match(line)):
            yield index, line.strip()


def pass_refusals(evidence: str, requirements: dict[str, set[str]] | None) -> list[str]:
    """Why this reply cannot be recorded as a pass; empty when it can.

    `requirements` is None for a goal without a gate directory: its reply
    needs no VERDICTS block, but a block it does carry must be all PASS.
    """
    blocks, errors, body = _read(evidence)
    problems = list(errors)
    if len(blocks) > 1:
        problems.append("more than one VERDICTS block")
    block = _block_verdicts(blocks[0], problems) if blocks else None
    if block is None and requirements is not None:
        problems.append("no VERDICTS block")
    for rid, kind in sorted((block or {}).items(), key=lambda item: _order(item[0])):
        if kind in ("FAIL", "UNVERIFIED"):
            problems.append(f"{rid}: {kind}")
        elif (kind == "NOT REVIEWED" and requirements is not None and rid in requirements
              and "unverifiable" not in requirements[rid]):
            problems.append(f"{rid}: NOT REVIEWED on a line not tagged [unverifiable]")
    if block is not None and requirements is not None:
        problems += [f"{rid}: no verdict in the VERDICTS block"
                     for rid in sorted(requirements, key=_order) if rid not in block]
        problems += [f"{rid}: not a requirement line"
                     for rid in sorted(block, key=_order) if rid not in requirements]
    problems += [f'{rid}: {kind} (line: "{line[:100]}")' for _, rid, kind, line in _open_lines(body)]
    problems += [f"FAIL headline: {line[:160]}" for _, line in _headlines(body)]
    return problems


def previous_lines(evidence: str) -> list[str]:
    """A failed review's open lines, in order: FAIL/UNVERIFIED prose lines,
    FAIL headlines, and the block's FAIL/UNVERIFIED verdicts."""
    blocks, _, body = _read(evidence)
    kept = [(index, line) for index, _, _, line in _open_lines(body)]
    kept += list(_headlines(body))
    for index, line in (blocks[0] if blocks else []):
        match = _BLOCK_LINE.match(line)
        if match and _block_kind(match) in ("FAIL", "UNVERIFIED"):
            kept.append((index, line.strip()))
    return [line for _, line in sorted(set(kept))]


def echoes(evidence: str, digest: str) -> bool:
    """True when the reply names the reviewed checkpoint's hash prefix."""
    prefix = str(digest)[:HASH_ECHO].lower()
    return bool(prefix) and prefix in evidence.lower()


# C4's neutral brief. The harness fills it so the author has no room to add
# framing; the pilot's briefs carried paraphrase, diagnosis and leading
# questions in most trials although the skill already forbade them.
TEMPLATE = """\
Review checkpoint {digest}. Begin your reply with `Reviewed checkpoint: {digest}`.

Request (verbatim):
{request}

Requirements:
{requirements}

Artifacts: <<ARTIFACTS>>
How to run: <<HOW_TO_RUN>>

This session may end before you finish. Start {review_file} with the line
`Reviewed checkpoint: {digest}`; then, as soon as you decide a requirement,
append its line to that file: `Rk: VERDICT - <command> - <what you observed>`.
A later reviewer of this checkpoint continues from the lines you recorded.

Build your own checks from the request. Do not create, change or delete files
inside the project: put any scripts and outputs in a new directory next to
{review_file}, and leave the project's git state as you found it. Do not read
or run anything under {harness_dir}, an earlier reviewer's files, or the
author's helpers. Call the code the way its existing callers and tests do,
including the repeated, boundary and multi-step cases the request implies.
Where the request is ambiguous, choose your own reading and say where the
artifact's reading differs.

For each requirement, give the command you ran and what you observed. A
requirement you could not test is UNVERIFIED, and so is one where a caveat
bears on whether it holds. Test a line tagged [unverifiable] if you can; if you
cannot, its verdict is NOT REVIEWED. If a symptom the request reports still
reproduces, that requirement FAILS even when a comment, docstring or design
note justifies the behaviour. Do not repeat earlier reviewers' lines. List
anything else under `Non-blocking:`. Keep the review under ten minutes and
within about six tool calls: test the riskiest lines first, and append each
verdict to the file as soon as you decide it.

End your reply with this block. Change each `?` to exactly one of PASS, FAIL,
UNVERIFIED or NOT REVIEWED, and write nothing else on those lines:

VERDICTS
{verdicts}
END VERDICTS

Before returning, replace the contents of {review_file} with your complete reply.
"""


def _within(path: str, root: str) -> bool:
    path, root = os.path.realpath(path), os.path.realpath(root)
    return os.path.commonpath([path, root]) == root


def review_file(slug: str, digest: str, n: int, watch_paths=()) -> str:
    """Where the reviewer writes its reply: one file per round, outside every
    watched path (a reply written there would change the checkpoint's hash),
    and never an exception -- `check` has already saved its checkpoint."""
    name = f"horizon-review-{slug}-{digest[:HASH_ECHO]}-{n}.md"
    candidates = []
    try:
        candidates.append(tempfile.gettempdir())
    except Exception:
        pass
    candidates += ["/tmp", "/var/tmp"]
    for base in candidates:
        if os.path.isdir(base) and not any(_within(base, watch) for watch in watch_paths):
            return os.path.join(base, name)
    return os.path.join(candidates[0], name)

def next_review_file(slug: str, digest: str, n: int, watch_paths=()) -> str:
    """Round `n`'s file, or the first later one not on disk: a reviewer cut off
    before its reply was recorded leaves its round's file behind."""
    path = review_file(slug, digest, n, watch_paths)
    while os.path.exists(path):
        n += 1
        path = review_file(slug, digest, n, watch_paths)
    return path


# `Rk: VERDICT`, optionally followed by evidence: the line a reviewer appends as
# soon as it decides a requirement, so a cut-off review still leaves its verdicts.
_DECIDED = re.compile(
    r"^[ \t]*(?:[-*+•][ \t]+)?[*_`]*" + _ID + r"[*_`]*[ \t]*:[ \t]*[*_`]*[ \t]*"
    r"(PASS|FAIL(?:ED|S)?|UNVERIFIED|NOT[ \t]+REVIEWED)(?!\w)", re.I)


def _round_files(slug: str, digest: str, review_file: str, since: float = 0.0) -> list[str]:
    """This checkpoint's earlier round files, oldest first, `review_file` itself left out.
    Files last written before `since` (the goal's start) belong to an earlier goal with the same slug."""
    directory = os.path.dirname(review_file)
    name = re.compile(re.escape(f"horizon-review-{slug}-{str(digest)[:HASH_ECHO]}-") + r"(\d+)\.md")
    try:
        entries = os.listdir(directory)
    except OSError:
        return []
    rounds = sorted((int(m.group(1)), entry) for entry in entries
                    if (m := name.fullmatch(entry)) and os.path.join(directory, entry) != review_file)
    paths = [os.path.join(directory, entry) for _, entry in rounds]
    if since:
        paths = [p for p in paths if _mtime(p) >= since]
    return paths


def _mtime(path: str) -> float:
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _round_verdicts_of(path: str, digest: str):
    """One round file's decided lines as (rid, kind, line), and [path], or ([], []) when it does not
    name the checkpoint. Lines quoted with `>` are ignored."""
    try:
        text = Path(path).read_text()
    except (OSError, UnicodeError):
        return [], []
    if not echoes(text, digest):
        return [], []
    decided = []
    for line in text.splitlines():
        match = None if line.lstrip().startswith(">") else _DECIDED.match(line)
        if match:
            decided.append((_rid(match.group(1)), _kind(match.group(2)), line.strip()))
    return decided, [path]


def _round_verdicts(slug: str, digest: str, review_file: str, since: float = 0.0):
    """Each decided line of this checkpoint's earlier rounds as (rid, kind, line), with the files read.
    Only files that name the checkpoint count, and lines quoted with `>` are ignored."""
    decided, files = [], []
    for path in _round_files(slug, digest, review_file, since):
        lines, read = _round_verdicts_of(path, digest)
        decided += lines
        files += read
    return decided, files


def carried(slug: str, digest: str, requirements: dict, review_file: str) -> tuple[list, list]:
    """Requirement lines that an earlier round's file for this checkpoint
    passed and none failed, with those files. A later UNVERIFIED or NOT
    REVIEWED does not reopen a passed line. Only files that name the
    checkpoint count, and lines quoted with `>` are ignored."""
    decided, files = _round_verdicts(slug, digest, review_file)
    verdicts: dict[str, set[str]] = {}
    for rid, kind, _ in decided:
        verdicts.setdefault(rid, set()).add(kind)
    ids = sorted((rid for rid, kinds in verdicts.items()
                  if "PASS" in kinds and "FAIL" not in kinds and rid in requirements),
                 key=_order)
    return ids, files


def failed_in_rounds(slug: str, digest: str, requirements: dict, review_file: str,
                     since: float = 0.0) -> list[str]:
    """Every FAIL line an earlier round wrote for this checkpoint, as written.
    A reviewer cut off before its reply was recorded leaves these only in its file."""
    return [line for line, _ in failed_rounds(slug, digest, requirements, review_file, since)]


def failed_rounds(slug: str, digest: str, requirements: dict, review_file: str,
                  since: float = 0.0) -> list[tuple[str, str]]:
    """(FAIL line, the round file that holds it) for this checkpoint's earlier rounds, each line once."""
    seen, out = set(), []
    for path in _round_files(slug, digest, review_file, since):
        decided, files = _round_verdicts_of(path, digest)
        for rid, kind, line in decided:
            if kind == "FAIL" and rid in requirements and line not in seen:
                seen.add(line)
                out.append((line, path))
    return out


def round_count(slug: str, digest: str, review_file: str, since: float = 0.0) -> int:
    """How many review rounds this checkpoint has had, finished or cut off."""
    return len(_round_files(slug, digest, review_file, since))

REVIEW_BATCH = 8
# After this many rounds on unchanged files, Horizon stops briefing reviewers and sends the agent back
# to building (NL2Repo benchmark: nine rounds on one checkpoint, no code change for eight sessions).
REVIEW_ROUNDS_PER_CHECKPOINT = 4

# One review round per goal, on any checkpoint. After it, `close` completes on a passing gate without
# another round, listing what went unreviewed (NL2Repo held-out: in 7 of 12 losses, close/review rounds that a
# 15-call session never finished took 3-8 sessions in which the library did not change).
REVIEW_ROUNDS_PER_GOAL = 1


def rounds_per_goal() -> int:
    """REVIEW_ROUNDS_PER_GOAL, or HORIZON_REVIEW_ROUNDS_PER_GOAL when set (a large value restores the
    multi-round review: carried lines, smaller batches, the pause after REVIEW_ROUNDS_PER_CHECKPOINT)."""
    try:
        return max(1, int(os.environ["HORIZON_REVIEW_ROUNDS_PER_GOAL"]))
    except (KeyError, ValueError):
        return REVIEW_ROUNDS_PER_GOAL


def handouts_per_goal() -> int:
    """How many review briefs `close` may hand out per goal: one more than its rounds, so a round whose reviewer
    never answered goes out once more, and no further (E2E-SWE paper run: of 65 reviewers, 46 came back only as
    "Async agent launched" and 11 were cut off by the call cap)."""
    return rounds_per_goal() + 1

SESSION_CALLS_ENV = "HORIZON_SESSION_CALLS"

# The calls a review round needs left in its session: the `close` that hands it out, the dispatch, and the
# reviewer's own reads and reply. E2E-SWE full run (15 calls a session, subagents counted): reviewers that came back
# had used 3-10 calls, median 6; of those sent with 9 or more calls used, 4 in 61 came back.
REVIEW_CALLS = 8


def session_call_budget() -> int | None:
    """The calls a session may make, its subagents' counted, when the runtime gives it (HORIZON_SESSION_CALLS)."""
    try:
        value = int(os.environ[SESSION_CALLS_ENV])
    except (KeyError, ValueError):
        return None
    return value if value > 0 else None


def _calls_path() -> Path:
    import paths
    return paths.harness_root() / "notes" / "session-calls.json"  # bookkeeping: never hashed


def _write_calls(path: Path, session: str, calls: int) -> None:
    tmp = path.with_name(path.name + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps({"session": session, "calls": calls}))
    os.replace(tmp, path)  # a reader never sees a half-written count


def count_call(session: str | None) -> None:
    """One more tool call in this session, a subagent's and a failed one's included (PostToolUse and
    PostToolUseFailure)."""
    if not session:
        return
    import filelock
    path = _calls_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with filelock.locked(path):
        try:
            record = json.loads(path.read_text())
            calls = int(record["calls"]) if record.get("session") == session else 0
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            calls = 0
        _write_calls(path, session, calls + 1)


def reset_calls(session: str | None) -> None:
    """A session (re)starts with no calls: a resume keeps its session id but gets a new budget."""
    if not session:
        return
    import filelock
    path = _calls_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with filelock.locked(path):
        _write_calls(path, session, 0)


def calls_used() -> int:
    """The tool calls the current session has made so far; 0 when unknown."""
    import paths
    session = paths.current_session()
    try:
        record = json.loads(_calls_path().read_text())
        return int(record["calls"]) if session and record.get("session") == session else 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError):
        return 0


def calls_short() -> tuple[int, int] | None:
    """(calls left, budget) when the session has fewer calls left than a review round needs; None when it has
    enough, when the runtime gives no budget, or when the budget is too small for any reviewer (holding back then
    would never hand a brief out)."""
    budget = session_call_budget()
    if budget is None or budget <= REVIEW_CALLS:
        return None
    left = budget - calls_used()
    return (max(left, 0), budget) if left < REVIEW_CALLS else None


def review_dir(watch_paths=()) -> str:
    """The directory round files are written to."""
    return os.path.dirname(review_file("goal", "0" * 64, 1, watch_paths))


def goal_round_files(slug: str, directory: str, since: float = 0.0) -> list[str]:
    """Every round file of this goal, on any checkpoint, written since the goal started."""
    name = re.compile(re.escape(f"horizon-review-{slug}-") + r"[0-9a-f]{%d}-\d+\.md" % HASH_ECHO)
    try:
        entries = os.listdir(directory)
    except OSError:
        return []
    paths = [os.path.join(directory, e) for e in sorted(entries) if name.fullmatch(e)]
    return [p for p in paths if _mtime(p) >= since]


def _started(state: dict) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(state["used"]["started"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return 0.0

# A round with decided lines but no END VERDICTS counts as finished once its file has been quiet this long: a
# reviewer still running keeps appending verdicts (the brief says so), a cut-off one stops.
ROUND_QUIET_S = 900


def round_finished(text: str, age_s: float) -> bool:
    """A complete reply, or a cut-off one that decided something and has been quiet for ROUND_QUIET_S."""
    if re.search(r"^[ \t]*END VERDICTS[ \t]*$", text, re.M):
        return True
    return bool(_fail_lines(text) or any(_DECIDED.match(l) for l in text.splitlines()
                                         if not l.lstrip().startswith(">"))) and age_s >= ROUND_QUIET_S


def _text(path: str) -> str:
    try:
        return Path(path).read_text()
    except (OSError, UnicodeError):
        return ""


def _goal_hashes(state: dict) -> set[str]:
    hashes = [c.get("tree_hash") for c in state.get("checkpoints") or []]
    hashes += [o.get("tree_hash") for o in state.get("observations") or []]
    hashes += [r.get("tree_hash") for r in state.get("reviews") or []]
    return {str(h)[:HASH_ECHO] for h in hashes if h}


def _goal_round_paths(state: dict) -> list[str]:
    """Round files named for one of this goal's own checkpoints and naming it, written since the goal began."""
    directory = review_dir(state.get("watch_paths") or [state["cwd"]])
    known = _goal_hashes(state)
    out = []
    for path in goal_round_files(state["slug"], directory, _started(state)):
        match = re.search(r"-([0-9a-f]{%d})-\d+\.md$" % HASH_ECHO, path)
        if match and match.group(1) in known and echoes(_text(path), match.group(1)):
            out.append(path)
    return out


def _decided(text: str) -> bool:
    """A reply that decided at least one line (a FAIL headline counts)."""
    return bool(_fail_lines(text) or any(_DECIDED.match(l) for l in text.splitlines()
                                         if not l.lstrip().startswith(">")))


def _ended_files(state: dict) -> set[str]:
    """Reply files of rounds handed out only in sessions that have ended: a reviewer runs inside the session that
    sends it, so it ended with that session, and its reply is finished as it stands (E2E-SWE full run: `close` said
    a cut-off reviewer "may still be writing" for 15 minutes, in 20 of the candidate's sessions)."""
    import paths
    session = paths.current_session()
    if not session:
        return set()
    issued = state.get("review_issued") or []
    live = {os.path.realpath(i["file"]) for i in issued if i.get("file") and i.get("session") == session}
    return {os.path.realpath(i["file"]) for i in issued
            if i.get("file") and i.get("session") and i["session"] != session} - live


def goal_rounds(state: dict) -> list[str]:
    """This goal's finished review rounds: complete replies, cut-off ones quiet for ROUND_QUIET_S, and cut-off ones
    whose reviewer's session has ended."""
    import time
    now = time.time()
    ended = _ended_files(state)
    return [p for p in _goal_round_paths(state)
            if round_finished(_text(p), now - _mtime(p)) or (os.path.realpath(p) in ended and _decided(_text(p)))]


def _writing(text: str, age_s: float) -> bool:
    """A reviewer still at work: its reply has decided lines, no END VERDICTS, and changed within ROUND_QUIET_S."""
    decided = bool(_fail_lines(text) or any(_DECIDED.match(l) for l in text.splitlines()
                                            if not l.lstrip().startswith(">")))
    return decided and not round_finished(text, age_s)


def issue_expired(entry: dict, now: float | None = None) -> bool:
    """Whether a round `close` handed out can no longer come back: a later session has started (the reviewer, run
    inside the session that dispatched it, ended with that session), or neither the hand-out nor its reply file
    has changed for ROUND_QUIET_S (a runtime without hooks)."""
    import time
    import paths
    session = paths.current_session()
    if entry.get("session") and session and entry["session"] != session:
        return True
    from datetime import datetime
    try:
        latest = datetime.fromisoformat(entry["at"]).timestamp()
    except (KeyError, TypeError, ValueError):
        return True
    if entry.get("file") and os.path.exists(entry["file"]):
        latest = max(latest, _mtime(entry["file"]))
    return (time.time() if now is None else now) - latest >= ROUND_QUIET_S


def _answered(state: dict) -> int:
    """How many of this goal's rounds came back: finished round files on its own checkpoints, or recorded reviews."""
    return max(len(goal_rounds(state)), len(state.get("reviews") or []))


def review_spent(state: dict) -> bool:
    """Whether this goal has had its review round(s): finished rounds on this goal's own checkpoints or recorded
    reviews. A round handed out and never answered (heldout2: a reviewer cut off by the call cap after its header,
    or a background one gone with its session) used to count once a later session started; on the E2E-SWE paper
    run that closed 79 goals on the agent's own gate, 57 of them below 100% on the hidden tests. It no longer
    counts: `close` hands the round out again, up to `handouts_per_goal()`."""
    return _answered(state) >= rounds_per_goal()


def review_pending(state: dict) -> bool:
    """Every round handed out, and the last one has not come back and still may: it went out in this session (or
    less than ROUND_QUIET_S ago, for a runtime without hooks), or a reviewer is still writing. Keep building, send
    no other reviewer."""
    issued = state.get("review_issued") or []
    if len(issued) < rounds_per_goal() or review_spent(state) or _answered(state) >= len(issued):
        return False
    if not issue_expired(issued[-1]):
        return True
    import time
    now = time.time()
    ended = _ended_files(state)
    return any(_writing(_text(p), now - _mtime(p)) for p in _goal_round_paths(state)
               if os.path.realpath(p) not in ended)


def review_unavailable(state: dict) -> bool:
    """Every brief this goal may hand out went out, none of its rounds is still to come back, and too few came back:
    no reviewer will answer, so `close` sends no more and completes only on a passing review."""
    issued = state.get("review_issued") or []
    return (len(issued) >= handouts_per_goal() and not review_spent(state)
            and not review_pending(state))


def _fail_lines(text: str) -> list[str]:
    """`Rk: FAIL` lines and FAIL headlines ("Verdict: FAIL") in a round file, as written; quoted lines ignored."""
    out = []
    for line in text.splitlines():
        if line.lstrip().startswith(">"):
            continue
        decided = _DECIDED.match(line)
        if decided and _kind(decided.group(2)) == "FAIL":
            out.append(line.strip())
        elif not decided and not _OPEN.match(line) and (
                _LABEL_FAIL.match(line) or _BOLD_FAIL.match(line) or _ALONE_FAIL.match(line)):
            out.append(line.strip())
    return list(dict.fromkeys(out))


def failed_on(state: dict, digest: str, requirements: dict | None = None) -> list[str]:
    """Why a reviewer failed exactly these files: a recorded `review --result fail`, or FAIL lines and headlines
    in a round file for this checkpoint, whatever the requirement ids. A recorded fail is not one when the only
    thing keeping its reply from a pass is UNVERIFIED lines: the reviewer could not check (heldout2: mypy,
    Windows or another Python missing from the sandbox), and nothing in the work is known to be wrong."""
    out = [f"a recorded review FAIL by {r.get('judge')}" for r in state.get("reviews") or []
           if r.get("tree_hash") == digest and r.get("result") == "fail"
           and not only_unverified(r.get("evidence") or "", digest, requirements)]
    prefix = str(digest)[:HASH_ECHO]
    for path in _goal_round_paths(state):
        if f"-{prefix}-" not in os.path.basename(path):
            continue
        text = _text(path)
        if echoes(text, digest):
            out += _fail_lines(text)
    return list(dict.fromkeys(out))


def only_unverified(evidence: str, digest: str, requirements: dict | None) -> bool:
    """A reply for these files that would be a pass but for its UNVERIFIED lines."""
    if not echoes(evidence, digest):
        return False
    refusals = pass_refusals(evidence, requirements)
    return bool(refusals) and all(re.match(r"R[\w.]*: UNVERIFIED(?: \(line:|$)", r) for r in refusals)


def _unverified_lines(text: str) -> list[str]:
    """`Rk: UNVERIFIED` lines in a reply, outside its VERDICTS block when the reply gives reasons there."""
    out = []
    for line in text.splitlines():
        decided = _DECIDED.match(line)
        if decided and not line.lstrip().startswith(">") and _kind(decided.group(2)) == "UNVERIFIED":
            out.append(line.strip())
    return list(dict.fromkeys(out))


def goal_unverified_lines(state: dict) -> list[str]:
    """UNVERIFIED lines from this goal's rounds and recorded reviews, for the `unreviewed` note."""
    out = []
    for path in goal_rounds(state):
        out += _unverified_lines(_text(path))
    for review in state.get("reviews") or []:
        out += _unverified_lines(review.get("evidence") or "")
    lines = list(dict.fromkeys(out))
    detailed = [l for l in lines if len(l.split()) > 2]
    return detailed or lines


def goal_fail_lines(state: dict) -> list[str]:
    """FAIL lines and headlines every round of this goal wrote, on any checkpoint."""
    out = []
    for path in goal_rounds(state):
        out += _fail_lines(_text(path))
    return list(dict.fromkeys(out))


def decided_counts(slug: str, review_file: str, since: float = 0.0) -> list[int]:
    """How many requirement lines each earlier round file of this goal decided,
    oldest first, across every checkpoint. Files older than `since` (the goal's
    start, as a timestamp) belong to another goal that had the same slug. A
    reviewer's complete reply repeats its brief's VERDICTS block, so NOT
    REVIEWED lines and lines an earlier round of the same checkpoint passed
    (carried into the brief) are not counted as decided."""
    directory = os.path.dirname(review_file)
    name = re.compile(re.escape(f"horizon-review-{slug}-") + r"([0-9a-f]{%d})-\d+\.md" % HASH_ECHO)
    try:
        entries = os.listdir(directory)
    except OSError:
        return []
    rounds = []
    for entry in entries:
        path = os.path.join(directory, entry)
        match = name.fullmatch(entry)
        if not match or path == review_file:
            continue
        try:
            at = os.path.getmtime(path)
            text = Path(path).read_text()
        except (OSError, UnicodeError):
            continue
        if at < since:
            continue
        verdicts: dict[str, set[str]] = {}
        for line in text.splitlines():
            decided = None if line.lstrip().startswith(">") else _DECIDED.match(line)
            if decided:
                verdicts.setdefault(_rid(decided.group(1)), set()).add(_kind(decided.group(2)))
        rounds.append((at, entry, match.group(1), verdicts))
    counts = []
    passed: dict[str, set[str]] = {}
    for _, _, prefix, verdicts in sorted(rounds):
        earlier = passed.setdefault(prefix, set())
        counts.append(sum(1 for rid, kinds in verdicts.items()
                          if kinds - {"NOT REVIEWED"} and rid not in earlier))
        earlier.update(rid for rid, kinds in verdicts.items() if kinds == {"PASS"})
    return counts


def batch_size(counts: list[int]) -> int | None:
    """How many open lines the next reviewer is given: all of them (None) while
    no earlier round exists; after one, twice what the last round decided, or
    REVIEW_BATCH halved for each round in a row that decided nothing.
    NL2Repo cerberus: nine reviewers handed all 33 lines decided none."""
    if not counts:
        return None
    empty = 0
    for count in reversed(counts):
        if count:
            break
        empty += 1
    if empty:
        return max(1, REVIEW_BATCH >> empty)
    return 2 * counts[-1]


def review_scope(requirements: dict, carried, batch: int | None) -> list[str] | None:
    """The open requirement lines this round reviews, in requirement order, or None for all.
    Lines only a reviewer can check are chosen before [gate] lines: a reviewer cut off by a short
    session should have spent its calls on them (NL2Repo bench2 unidecode). [unverifiable] lines come last. Lines left out are
    never only [unverifiable] ones: a pass would skip them untried."""
    open_ids = [rid for rid in requirements if rid not in carried]
    # [review]/[judgment] first, [gate] next, [unverifiable] last: an unverifiable line usually comes back
    # NOT REVIEWED, which never carries, so putting it first would review it every round.
    ranked = sorted(open_ids, key=lambda rid: (2 if "unverifiable" in (requirements[rid] or ())
                                                else 1 if set(requirements[rid] or ()) <= {"gate"} else 0))
    if batch is None or batch >= len(open_ids):
        return None
    chosen, rest = ranked[:batch], ranked[batch:]
    if all("unverifiable" in requirements[rid] for rid in rest):
        return None
    return [rid for rid in open_ids if rid in chosen]


def brief(*, digest: str, request: str, requirements: str, verdicts: str, harness_dir: str,
          review_file: str, previous: list[str], carried=(), scope=None) -> str:
    text = TEMPLATE.format(digest=digest, request=request, requirements=requirements,
                           verdicts=verdicts, harness_dir=harness_dir, review_file=review_file)
    if previous:
        text += "\nPrevious:\n" + "".join(f"> {line}\n" for line in previous)
    if carried:
        text += ("\nAlready passed by an earlier reviewer of this checkpoint, who was cut off: "
                 + ", ".join(carried) + ". Leave those lines as PASS and test only the lines marked `?`.\n")
    if scope:
        text += ("\nThis round reviews only " + ", ".join(scope) + ". Earlier reviewers were cut off "
                 "before deciding enough lines, so the lines marked NOT REVIEWED are left for a later "
                 "round: leave them as they are and do not test them. Read only the parts of the request "
                 "these lines need. Decide the lines in order, and append its verdict to "
                 f"{review_file} before you start the next.\n")
    return text


def _gate_text(gate_path: str | None, name: str, placeholder: str) -> str:
    if not gate_path:
        return f"<<{placeholder}>>"
    target = Path(gate_path) / name
    try:
        text = target.read_text()
    except (OSError, UnicodeError) as exc:
        return f"<<{placeholder}: could not read {target}: {exc}>>"
    if len(text) > BRIEF_FILE_LIMIT:
        return (text[:BRIEF_FILE_LIMIT]
                + f"\n[truncated; the full text is in {target}, which you may read]")
    return text.rstrip("\n")


def _skeleton(gate_path: str | None, carried=(), scope=None) -> str:
    if gate_path:
        try:
            ids = list(requirement_lines((Path(gate_path) / "requirements.md").read_text()))
        except (OSError, UnicodeError):
            ids = []
        if ids:
            def verdict(rid):
                if rid in carried:
                    return "PASS"
                return "?" if scope is None or rid in scope else "NOT REVIEWED"
            return "\n".join(f"{rid}: {verdict(rid)}" for rid in ids)
    return "<<VERDICT_LINES: one `Rk: ?` line per requirement>>"


def brief_for(state: dict, digest: str, review_file: str, harness_dir: str, carried=(),
              scope=None) -> str:
    """The brief for this goal's checkpoint. Never raises: `check` has already
    written the checkpoint, so a crash here would lose its report."""
    reviews = state.get("reviews") or []
    previous = (previous_lines(reviews[-1].get("evidence", ""))
                if reviews and reviews[-1].get("result") == "fail" else [])
    gate_path = state.get("gate_path")
    return brief(digest=digest,
                 request=_gate_text(gate_path, "request.md", "REQUEST"),
                 requirements=_gate_text(gate_path, "requirements.md", "REQUIREMENTS"),
                 verdicts=_skeleton(gate_path, carried, scope),
                 harness_dir=harness_dir, review_file=review_file, previous=previous,
                 carried=carried, scope=scope)
