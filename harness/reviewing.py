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
anything else under `Non-blocking:`. Keep the review under ten minutes.

End your reply with this block. Change each `?` to exactly one of PASS, FAIL,
UNVERIFIED or NOT REVIEWED, and write nothing else on those lines:

VERDICTS
{verdicts}
END VERDICTS

Before returning, write your complete reply to {review_file}.
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


def brief(*, digest: str, request: str, requirements: str, verdicts: str, harness_dir: str,
          review_file: str, previous: list[str]) -> str:
    text = TEMPLATE.format(digest=digest, request=request, requirements=requirements,
                           verdicts=verdicts, harness_dir=harness_dir, review_file=review_file)
    if previous:
        text += "\nPrevious:\n" + "".join(f"> {line}\n" for line in previous)
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


def _skeleton(gate_path: str | None) -> str:
    if gate_path:
        try:
            ids = list(requirement_lines((Path(gate_path) / "requirements.md").read_text()))
        except (OSError, UnicodeError):
            ids = []
        if ids:
            return "\n".join(f"{rid}: ?" for rid in ids)
    return "<<VERDICT_LINES: one `Rk: ?` line per requirement>>"


def brief_for(state: dict, digest: str, review_file: str, harness_dir: str) -> str:
    """The brief for this goal's checkpoint. Never raises: `check` has already
    written the checkpoint, so a crash here would lose its report."""
    reviews = state.get("reviews") or []
    previous = (previous_lines(reviews[-1].get("evidence", ""))
                if reviews and reviews[-1].get("result") == "fail" else [])
    gate_path = state.get("gate_path")
    return brief(digest=digest,
                 request=_gate_text(gate_path, "request.md", "REQUEST"),
                 requirements=_gate_text(gate_path, "requirements.md", "REQUIREMENTS"),
                 verdicts=_skeleton(gate_path),
                 harness_dir=harness_dir, review_file=review_file, previous=previous)
