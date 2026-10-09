"""Requirement lines and reviewer verdicts (Horizon v2).

In the TB4 pilot every recorded review was "pass" while four reviewer
replies said FAIL, and every stored verdict was the author's paraphrase.
These helpers read the reviewer's own reply one line at a time, so the
ledger can refuse a pass the reply does not support.
"""
import os
import re
import tempfile
from pathlib import Path

HASH_ECHO = 12

_RANK = {"FAIL": 4, "UNVERIFIED": 3, "NOT REVIEWED": 2, "PASS": 1}

# R1, R12, R1a, R1.2: every id a requirements file can use, read the same way
# on both sides. Folding R1a into R1, or skipping it, let a FAIL on R1b
# through while R1 passed.
_ID = r"R(\d+(?:\.\d+)*[a-z]?)(?!\w)"
# Heading, bullet, checkbox and numbering prefixes, then emphasis.
_PREFIX = (r"^[ \t ]*(?:#{1,6}[ \t]+)?(?:[-*+•][ \t]+)?(?:\[[ xX]\][ \t]+)?"
           r"(?:\d+[.)][ \t]+)?[*_`]*")
_REQUIREMENT = re.compile(_PREFIX + _ID)
_TAG = re.compile(r"\[(gate|review|unverifiable|judgment)\]", re.I)
# The first id, then any `, R2`, `and R3` or `-R4` (a range) that follows it.
_HEAD = re.compile(
    _PREFIX + _ID + r"((?:[ \t]*(?:,|&|\band\b|[-–])[ \t]*R\d+(?:\.\d+)*[a-z]?(?!\w))*)")
_MORE = re.compile(r"(,|&|\band\b|[-–])[ \t]*R(\d+(?:\.\d+)*[a-z]?)")
# Quoted requirement text and code spans are the reviewer citing something,
# not giving a verdict: `grep FAIL`, "the CLI fails with exit 3".
_SPANS = re.compile(r'"[^"\n]*"|“[^”\n]*”|`[^`\n]*`')
_PASS_MARKS, _FAIL_MARKS = "✅✔", "❌✖"
# What may sit between an id and its verdict word: separators and arrows,
# emphasis, any number of [tag] and (from ...) groups, and check marks.
_LEAD = re.compile(
    r"(?:[\s*_:.)|\-–—>=→,~]+|\[[^\]\n]*\]|\([^)\n]*\)"
    r"|[✅✔❌✖⚠️])*")
_WORD = r"(PASS(?:ED|ES)?|FAIL(?:ED|S|ING|URE)?|UNVERIFIED|UNVERIFIABLE|NOT\s+REVIEWED)(?!\w)"
_FIRST = re.compile(_WORD, re.I)
# Later on the line only an all-capitals verdict counts, so prose such as
# "no longer fails" after a PASS is not read as a FAIL.
_UPPER = re.compile(r"(?<!\w)" + _WORD)
# "0 FAILED", "was FAIL in the first review": a count or a history, not a verdict.
_SETTLED = re.compile(r"\b(?:0|no|zero|none|previously|was|were|earlier)\s+$", re.I)
# "Overall: FAIL", "Result: FAIL", "**Verdict:** FAIL" -- the pilot's lost FAILs
# were headline-only verdicts with no requirement lines.
_LABEL = re.compile(
    r"^[\W_]*(?:(?:overall|final)\s+)?(?:verdict|overall|result|status|conclusion|outcome)\b",
    re.I)
_LABEL_FAIL = re.compile(r"^[\W_]*fail(?:ed|s)?\b", re.I)
# "**FAIL.**" or "Fail." alone; not "FAIL count: 0", "FAILED tests/x.py" or
# "--- FAIL: TestX" from a test runner, and not "Fail-safe paths ...".
_FAIL_ALONE = re.compile(
    r"^[ \t]*(?:#{1,6}[ \t]+)?[*_]*(?:FAIL(?:ED)?|Fail(?:ed)?)[*_]*[ \t]*"
    r"(?:[.!:;,–—]|-(?!\w)|$)")


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


def _ids(head) -> list[str]:
    ids = [_rid(head.group(1))]
    for separator, raw in _MORE.findall(head.group(2)):
        previous, current = ids[-1], _rid(raw)
        if separator in "-–" and previous[1:].isdigit() and current[1:].isdigit():
            ids += [f"R{n}" for n in range(int(previous[1:]) + 1,
                                           min(int(current[1:]), int(previous[1:]) + 200) + 1)]
        else:
            ids.append(current)
    return ids


def _kinds_after(text: str) -> list[str]:
    """Every verdict the text after an id gives: the first word, then any
    all-capitals verdict or cross mark later on."""
    text = _SPANS.sub(" ", text)
    lead = _LEAD.match(text)
    kinds = []
    first = _FIRST.match(text, lead.end())
    if first:
        kinds.append(_kind(first.group(1)))
    kinds += [_kind(m.group(1)) for m in _UPPER.finditer(text)
              if not _SETTLED.search(text[:m.start()])]
    if any(mark in text for mark in _FAIL_MARKS):
        kinds.append("FAIL")
    if any(mark in lead.group(0) for mark in _PASS_MARKS):
        kinds.append("PASS")
    return kinds


def _verdict_line(line: str):
    """(ids, worst verdict) for a requirement's verdict line, else None."""
    if line.lstrip().startswith("|"):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        for index, cell in enumerate(cells):
            head = _HEAD.match(cell)
            if head:
                kinds = _kinds_after(cell[head.end():])
                for later in cells[index + 1:]:
                    kinds += _kinds_after(later)
                break
        else:
            return None
    else:
        head = _HEAD.match(line)
        if not head:
            return None
        kinds = _kinds_after(line[head.end():])
    if not kinds:
        return None
    return _ids(head), max(kinds, key=_RANK.get)


def _headline(line: str) -> bool:
    label = _LABEL.match(line)
    if label:
        after = line[label.end():]
        if _LABEL_FAIL.match(after):
            return True
        text = _SPANS.sub(" ", after)
        return any(_kind(m.group(1)) == "FAIL" and not _SETTLED.search(text[:m.start()])
                   for m in _UPPER.finditer(text))
    return bool(_FAIL_ALONE.match(line))


def _scan(evidence: str):
    """Yield (index, rid, kind, line) for each verdict and FAIL headline.

    Headlines have rid None and kind "FAIL". Lines quoted with `>` repeat an
    earlier reviewer and are skipped, unless the whole reply is quoted. Test
    output inside a code fence is never a headline.
    """
    lines = evidence.splitlines()
    quoted_only = not any(line.strip() and not line.lstrip().startswith(">") for line in lines)
    fenced = False
    for index, line in enumerate(lines):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if line.lstrip().startswith(">"):
            if not quoted_only:
                continue
            line = re.sub(r"^\s*(?:>\s?)+", "", line)
        found = _verdict_line(line)
        if found:
            ids, kind = found
            for rid in ids:
                yield index, rid, kind, line.strip()
        elif not fenced and _headline(line):
            yield index, None, "FAIL", line.strip()


def requirement_lines(text: str) -> dict[str, set[str]]:
    """Map each requirement id (R1, R2, R1a, R1.2, ...) to the tags on its line."""
    found: dict[str, set[str]] = {}
    for line in text.splitlines():
        match = _REQUIREMENT.match(line)
        if match:
            tags = found.setdefault(_rid(match.group(1)), set())
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


def _mention(evidence: str, rid: str) -> str | None:
    pattern = re.compile(r"(?<!\w)" + re.escape(rid) + r"(?!\w|\.\d)")
    for line in evidence.splitlines():
        if not line.lstrip().startswith(">") and pattern.search(line):
            return line.strip()
    return None


def pass_refusals(evidence: str, requirements: dict[str, set[str]] | None) -> list[str]:
    """Why this reply cannot be recorded as a pass; empty when it can."""
    last, headlines = verdicts(evidence)
    problems = [f"{rid}: {kind}" for rid, kind in sorted(last.items(), key=lambda i: _order(i[0]))
                if kind in ("FAIL", "UNVERIFIED")]
    problems += [f"FAIL headline: {line[:160]}" for line in headlines]
    for rid, tags in sorted((requirements or {}).items(), key=lambda i: _order(i[0])):
        kind = last.get(rid)
        if kind is None:
            mention = _mention(evidence, rid)
            problems.append(f"{rid}: no verdict"
                            + (f' (unread line: "{mention[:100]}")' if mention else ""))
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

Build your own checks from the request. Do not create, change or delete files
inside the project: put any scripts and outputs in a new directory next to
{review_file}, and leave the project's git state as you found it. Do not read
or run anything under {harness_dir}, an earlier reviewer's files, or the
author's helpers. Call the code the way its existing callers and tests do,
including the repeated, boundary and multi-step cases the request implies.
Where the request is ambiguous, choose your own reading and say where the
artifact's reading differs.

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
    return [line for _, line in sorted(set(kept))]


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
        return (text[:BRIEF_FILE_LIMIT]
                + f"\n[truncated; the full text is in {target}, which you may read]")
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
