"""Which sections of a long request no requirement line cites.

NL2Repo generic check: the agent's requirement lines covered part of a long request (20 lines for a 776-line
spec), so a layout one section listed, and a reference section that described about 50 classes one by one, were
never built or compared. A section is a Markdown heading with no sub-heading under it; a requirement line cites
it by the heading's words, ignoring case, numbering and Markdown marks."""
import hashlib
import re
from pathlib import Path

import reviewing

LONG_INPUT_LINES = 200  # shorter inputs are read whole and need no section check
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
INPUT_SUFFIXES = (".md", ".markdown")  # a named .py or .yaml file's `#` comments are not sections
MAX_INPUT_BYTES = 5_000_000
_NUMBER = re.compile(r"^\d+(\.\d+)*[.):]*\s+")  # "2. ", "10.1. ", "8.: ", "10: "
_NAME = re.compile(r"[\w./-]+\.\w+")


def leaf_headings(text: str) -> list[tuple[int, str]]:
    """(line number, heading) for each Markdown heading outside code fences that has no deeper heading under it.
    A fence closes only on the same character, at least as long, as CommonMark has it."""
    found, fence = [], None
    for number, line in enumerate(text.splitlines(), 1):
        opened = _FENCE.match(line)
        if fence:
            if opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence) \
                    and not line[opened.end():].strip():
                fence = None
            continue
        if opened:
            fence = opened.group(1)
            continue
        match = _HEADING.match(line)
        if match:
            found.append((len(match.group(1)), number, match.group(2).strip()))
    return [(number, heading) for i, (level, number, heading) in enumerate(found)
            if i + 1 == len(found) or found[i + 1][0] <= level]


def _words(text: str) -> str:
    text = re.sub(r"[*_`\"'“”‘’]", "", text.lower())
    return " ".join(text.split())


def _code_names(heading: str) -> list[str]:
    """The code names a heading carries: backticked text, `name()` calls, snake_case and CamelCase words."""
    names = re.findall(r"`([^`]+)`", heading)
    names += re.findall(r"\b[A-Za-z_][\w.]*\(\)", heading)
    names += [w for w in re.findall(r"\b[A-Za-z_][\w.]*\b", heading)
              if "_" in w.strip("_") or re.search(r"[a-z][A-Z]|[A-Z]{2,}[a-z]", w)]
    return [n for n in dict.fromkeys(_words(n).removesuffix("()") for n in names) if len(n) >= 3]


def _is_cited(heading: str, lines: list[str]) -> bool:
    """Whether one requirement line holds the heading's words, or a code name it carries, as whole words."""
    names = [_words(_NUMBER.sub("", _words(heading)))] + _code_names(heading)
    patterns = [re.compile(r"(?<![\w.])" + re.escape(name) + r"(?![\w])") for name in names if name]
    return any(p.search(line) for p in patterns for line in lines)

def uncited(text: str, requirements: str) -> list[tuple[int, str]]:
    """The leaf headings of a long input that no requirement line (R1, R2, ...) cites, by the heading's words or
    by a code name it carries (`chunked()`, BaseUserManager); none for a short input."""
    if len(text.splitlines()) < LONG_INPUT_LINES:
        return []
    lines = [_words(line) for line in requirements.splitlines() if reviewing._REQUIREMENT.match(line)]
    return [(number, heading) for number, heading in leaf_headings(text) if not _is_cited(heading, lines)]


def inputs(request: str, root: Path) -> list[Path]:
    """Markdown files in the project the request names, by a path relative to the project root."""
    out = []
    for name in dict.fromkeys(_NAME.findall(request)):
        path = root / name.removeprefix("./")
        if not path.name.lower().endswith(INPUT_SUFFIXES):
            continue
        try:
            if path.is_file() and path.resolve().is_relative_to(root.resolve()) \
                    and path.resolve() not in [o.resolve() for o in out]:
                out.append(path)
        except OSError:
            continue
    return out


def _read(path: Path) -> str | None:
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            return None
        return path.read_text()
    except (OSError, UnicodeError):
        return None


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def input_hashes(gate_path: str | None, root: str) -> dict[str, str]:
    """The named inputs as they are when the goal starts, so a file the work later writes (a CHANGELOG the
    request says to extend) is known as an output, not a request to cover."""
    if not gate_path:
        return {}
    request = _read(Path(gate_path) / "request.md") or ""
    return {str(path): _digest(text) for path in inputs(request, Path(root))
            if (text := _read(path)) is not None}


def uncited_in_goal(gate_path: str | None, root: str, at_start: dict | None = None) -> list[dict]:
    """Uncited sections of the goal's request.md and of each Markdown file it names, as {file, line, heading};
    a named file that changed since `at_start` recorded it is an output of the work and is left out."""
    if not gate_path:
        return []
    directory = Path(gate_path)
    request, requirements = _read(directory / "request.md"), _read(directory / "requirements.md")
    if request is None or requirements is None:
        return []
    out, seen = [], set()
    for path in [directory / "request.md", *inputs(request, Path(root))]:
        text = _read(path)
        try:
            key = path.resolve()
        except OSError:
            continue
        if text is None or key in seen:
            continue
        seen.add(key)
        if at_start and str(path) in at_start and at_start[str(path)] != _digest(text):
            continue
        out += [{"file": str(path), "line": number, "heading": heading}
                for number, heading in uncited(text, requirements)]
    return out


def message(missing: list[dict], limit: int = 25) -> str:
    """The `close` refusal that lists the uncited sections."""
    shown = "; ".join(f'"{m["heading"]}" ({Path(m["file"]).name} line {m["line"]})' for m in missing[:limit])
    more = f"; and {len(missing) - limit} more" if len(missing) > limit else ""
    return ("The requirement lines cite no part of these sections of the request: " + shown + more
            + ". Add a requirement line that quotes each heading, or a line that quotes it and says why it is "
            "out of scope; then run check.")
