"""Requirement lines and reviewer verdicts (Horizon v2).

In the TB4 pilot every recorded review was "pass" while four reviewer
replies said FAIL, and every stored verdict was the author's paraphrase.
These helpers read the reviewer's own reply one line at a time, so the
ledger can refuse a pass the reply does not support.
"""
import re
import tempfile
from pathlib import Path

HASH_ECHO = 12

_REQUIREMENT = re.compile(r"^[ \t]*(?:[-*+][ \t]+|\d+[.)][ \t]+)?[*_`]*R(\d+)(?!\w)")
_TAG = re.compile(r"\[(gate|review|unverifiable|judgment)\]", re.I)
# List, table and emphasis prefixes; one bracketed or parenthesised tag;
# colon, dot, paren, pipe or dash separators. A narrower pattern matched none
# of the pilot's four FAIL replies.
_VERDICT = re.compile(
    r"^[ \t]*(?:[-*+]|\d+[.)]|\|)?[ \t]*[*_`]*R(\d+)(?!\d)[*_`]*[ \t]*"
    r"(?:[\[(][^\])\n]{0,60}[\])][ \t]*)?"
    r"(?:[:.)|\-–—][ \t]*)*[*_`]*[ \t]*"
    r"(PASS(?:ED|ES)?|FAIL(?:ED|S)?|UNVERIFIED|NOT[ \t]+REVIEWED)(?!\w)",
    re.I)
# "Verdict: FAIL, narrowly." -- but not a table header "| Verdict | Fail reason |".
_VERDICT_FAIL = re.compile(
    r"\bverdict\b[*_`]*[ \t]*(?:[:=\-–—]|is\b)[ \t]*[*_`]*[ \t]*fail", re.I)
# "**FAIL.**" -- all capitals, so "Fail-safe paths ..." is prose.
_FAIL_FIRST = re.compile(r"^[^\w>]*FAIL(?:ED|S)?(?![\w-])")


def _kind(word: str) -> str:
    word = word.upper()
    if word.startswith("PASS"):
        return "PASS"
    if word.startswith("FAIL"):
        return "FAIL"
    if word.startswith("NOT"):
        return "NOT REVIEWED"
    return "UNVERIFIED"


def _order(rid: str) -> int:
    return int(rid[1:])


def _scan(evidence: str):
    """Yield (index, rid, kind, line) for each verdict line and FAIL headline.

    Headlines have rid None and kind "FAIL". Lines quoted with `>` repeat an
    earlier reviewer and are skipped.
    """
    for index, line in enumerate(evidence.splitlines()):
        if line.lstrip().startswith(">"):
            continue
        match = _VERDICT.match(line)
        if match:
            yield index, f"R{int(match.group(1))}", _kind(match.group(2)), line.strip()
        elif _VERDICT_FAIL.search(line) or _FAIL_FIRST.match(line):
            yield index, None, "FAIL", line.strip()


def requirement_lines(text: str) -> dict[str, set[str]]:
    """Map each requirement id (R1, R2, ...) to the tags on its line."""
    found: dict[str, set[str]] = {}
    for line in text.splitlines():
        match = _REQUIREMENT.match(line)
        if match:
            tags = found.setdefault(f"R{int(match.group(1))}", set())
            tags.update(tag.lower() for tag in _TAG.findall(line))
    return found


def verdicts(evidence: str) -> tuple[dict[str, str], list[str]]:
    """The last verdict for each requirement, and every FAIL headline line."""
    last: dict[str, str] = {}
    headlines: list[str] = []
    for _, rid, kind, line in _scan(evidence):
        if rid is None:
            headlines.append(line)
        else:
            last[rid] = kind
    return last, headlines


def pass_refusals(evidence: str, requirements: dict[str, set[str]] | None) -> list[str]:
    """Why this reply cannot be recorded as a pass; empty when it can."""
    last, headlines = verdicts(evidence)
    problems = [f"{rid}: {kind}" for rid, kind in sorted(last.items(), key=lambda i: _order(i[0]))
                if kind in ("FAIL", "UNVERIFIED")]
    problems += [f"FAIL headline: {line[:160]}" for line in headlines]
    for rid, tags in sorted((requirements or {}).items(), key=lambda i: _order(i[0])):
        kind = last.get(rid)
        if kind is None:
            problems.append(f"{rid}: no verdict")
        elif kind == "NOT REVIEWED" and "unverifiable" not in tags:
            problems.append(f"{rid}: NOT REVIEWED on a line not tagged [unverifiable]")
    return problems


def echoes(evidence: str, digest: str) -> bool:
    """True when the reply names the reviewed checkpoint's hash prefix."""
    prefix = str(digest)[:HASH_ECHO].lower()
    return bool(prefix) and prefix in evidence.lower()


BRIEF_FILE_LIMIT = 32000

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

Build your own checks from the request. Do not read or run anything under
{harness_dir}, an earlier reviewer's files, or the author's helpers. Call the
code the way its existing callers and tests do, including the repeated, boundary
and multi-step cases the request implies. Where the request is ambiguous, choose
your own reading and say where the artifact's reading differs.

Reply with exactly one line per requirement:
`Rk: PASS|FAIL|UNVERIFIED - command and observed output`. A requirement you could
not test is UNVERIFIED, and so is one where a caveat bears on whether it holds.
Test a line tagged [unverifiable] if you can and answer it as usual; if you
cannot, answer `Rk: NOT REVIEWED`. If a symptom the request reports still
reproduces, that line FAILS even when a comment, docstring or design note
justifies the behaviour. Do not repeat earlier reviewers' lines. List anything
else under `Non-blocking:`. Keep the review under ten minutes. Before returning,
write your complete reply to {review_file}.
"""


def previous_lines(evidence: str) -> list[str]:
    """A failed review's open lines, in order: FAIL/UNVERIFIED verdicts and FAIL headlines."""
    last: dict[str, tuple[int, str, str]] = {}
    kept: list[tuple[int, str]] = []
    for index, rid, kind, line in _scan(evidence):
        if rid is None:
            kept.append((index, line))
        else:
            last[rid] = (index, kind, line)
    kept += [(index, line) for index, kind, line in last.values()
             if kind in ("FAIL", "UNVERIFIED")]
    return [line for _, line in sorted(kept)]


def review_file(slug: str, digest: str, n: int) -> str:
    """Where the reviewer writes its reply: outside the project, one file per round."""
    return str(Path(tempfile.gettempdir()) / f"horizon-review-{slug}-{digest[:HASH_ECHO]}-{n}.md")


def brief(*, digest: str, request: str, requirements: str, harness_dir: str,
          review_file: str, previous: list[str]) -> str:
    text = TEMPLATE.format(digest=digest, request=request, requirements=requirements,
                           harness_dir=harness_dir, review_file=review_file)
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
        return text[:BRIEF_FILE_LIMIT] + f"\n[truncated; read the full text in {target}]"
    return text.rstrip("\n")


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
                 harness_dir=harness_dir, review_file=review_file, previous=previous)
