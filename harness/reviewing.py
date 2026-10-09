"""Requirement lines and reviewer verdicts (Horizon v2).

In the TB4 pilot every recorded review was "pass" while four reviewer
replies said FAIL, and every stored verdict was the author's paraphrase.
These helpers read the reviewer's own reply one line at a time, so the
ledger can refuse a pass the reply does not support.
"""
import re

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
