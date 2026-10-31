"""Which sections of a long request no requirement line cites.

NL2Repo generic check: the agent's requirement lines covered part of a long request (20 lines for a 776-line
spec), so a layout one section listed, and a reference section that described about 50 classes one by one, were
never built or compared. A section is a Markdown heading with no sub-heading under it; a requirement line cites
it by the heading's words, ignoring case, numbering and Markdown marks."""
import re
from pathlib import Path

LONG_INPUT_LINES = 200  # shorter inputs are read whole and need no section check
_HEADING = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t#]*$")
_FENCE = re.compile(r"^[ \t]*(```|~~~)")
_NUMBER = re.compile(r"^\d+(\.\d+)*[.):]*\s+")  # "2. ", "10.1. ", "8.: ", "10: "
_NAME = re.compile(r"[\w./-]+\.\w+")


def leaf_headings(text: str) -> list[tuple[int, str]]:
    """(line number, heading) for each Markdown heading outside code fences that has no deeper heading under it."""
    found, fenced = [], False
    for number, line in enumerate(text.splitlines(), 1):
        if _FENCE.match(line):
            fenced = not fenced
            continue
        match = None if fenced else _HEADING.match(line)
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


def _is_cited(heading: str, cited: str) -> bool:
    if _words(_NUMBER.sub("", _words(heading))) in cited:
        return True
    return any(re.search(r"(?<![\w.])" + re.escape(name) + r"(?![\w])", cited) for name in _code_names(heading))

def uncited(text: str, requirements: str) -> list[tuple[int, str]]:
    """The leaf headings of a long input that the requirement lines do not cite, by the heading's words or by a
    code name it carries (`chunked()`, BaseUserManager); none for a short input."""
    if len(text.splitlines()) < LONG_INPUT_LINES:
        return []
    cited = _words(requirements)
    return [(number, heading) for number, heading in leaf_headings(text) if not _is_cited(heading, cited)]


def inputs(request: str, root: Path) -> list[Path]:
    """Files in the project the request names, by a path relative to the project root."""
    out = []
    for name in dict.fromkeys(_NAME.findall(request)):
        path = root / name.removeprefix("./")
        try:
            if path.is_file() and path.resolve().is_relative_to(root.resolve()) and path not in out:
                out.append(path)
        except OSError:
            continue
    return out


def uncited_in_goal(gate_path: str | None, root: str) -> list[dict]:
    """Uncited sections of the goal's request.md and of each project file it names, as {file, line, heading}."""
    if not gate_path:
        return []
    directory = Path(gate_path)
    try:
        request = (directory / "request.md").read_text()
        requirements = (directory / "requirements.md").read_text()
    except (OSError, UnicodeError):
        return []
    out = []
    for path in [directory / "request.md", *inputs(request, Path(root))]:
        try:
            text = path.read_text()
        except (OSError, UnicodeError):
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
