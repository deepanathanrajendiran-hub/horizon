"""Which sections of a long request no requirement line cites.

NL2Repo generic check: the agent's requirement lines covered part of a long request (20 lines for a 776-line
spec), so a layout one section listed, and a reference section that described about 50 classes one by one, were
never built or compared. A section is a Markdown heading with no sub-heading under it; a requirement line cites
it by the heading's words, ignoring case, numbering and Markdown marks.

genericfix: a section is also covered when the work defines the function, class or path its heading names,
so a spec of one section per function needs no requirement line per function; `close` checks, not `start`."""
import hashlib
import re
from pathlib import Path

import reviewing
import tree_hash

LONG_INPUT_LINES = 200  # shorter inputs are read whole and need no section check
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.+?)(?:[ \t]+#+)?[ \t]*$")
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
INPUT_SUFFIXES = (".md", ".markdown")  # a named .py or .yaml file's `#` comments are not sections
MAX_INPUT_BYTES = 5_000_000
_NUMBER = re.compile(r"^\d+(\.\d+)*[.):]*\s+")  # "2. ", "10.1. ", "8.: ", "10: "
_NAME = re.compile(r"[\w./-]+\.\w+")

# A name the work defines: def/class/fun/fn/struct/... NAME in common languages, after any modifiers
# (public static, pub(crate) const, export default, ...), or a top-level NAME = ... outside config files
_MODIFIERS = (r"(?:(?:export|default|public|private|protected|internal|static|final|abstract|sealed|open|override"
              r"|async|unsafe|extern|const|pub(?:\([^)\n]*\))?)[ \t]+)*")
_KEYWORDS = (r"def|class|function\*?|func(?:[ \t]*\([^)\n]*\))?|fun|fn|struct|enum|interface|trait|type|object"
             r"|record|protocol|const|let|var|val|module")
_DEFINITION = re.compile(r"^[ \t]*" + _MODIFIERS + r"(?:" + _KEYWORDS + r")[ \t]+([A-Za-z_$][\w$]*)", re.M)
_ASSIGNED = re.compile(r"^([A-Za-z_$][\w$]*)[ \t]*(?::[^=\n]*)?=(?!=)", re.M)
_NOT_NAMES = {"def", "class", "function", "func", "fun", "fn", "struct", "enum", "interface", "trait", "type",
              "object", "record", "protocol", "const", "let", "var", "val", "module"}
CONFIG_SUFFIXES = (".toml", ".cfg", ".ini", ".yaml", ".yml", ".json", ".lock", ".txt", ".env")  # keys are not code


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

def _raw_names(heading: str) -> list[str]:
    """The code names a heading carries as written, for looking up in the work: the leading name of backticked
    text, and outside backticks each path, `name()` call, snake_case or CamelCase word. A path stays whole: its
    folder names are not names of their own."""
    names = [m.group(1) for text in re.findall(r"`([^`]+)`", heading) if (m := re.match(r"\s*([\w$./-]+)", text))]
    for token in re.findall(r"[\w$./-]+(?:\(\))?", re.sub(r"`[^`]*`", " ", heading)):
        word = token.removesuffix("()")
        if ("/" in word or token.endswith("()") or "_" in word.strip("_")
                or re.search(r"[a-z][A-Z]|[A-Z]{2,}[a-z]", word)):
            names.append(word)
    return [n for n in dict.fromkeys(n.removeprefix("./").rstrip("/.") for n in names) if len(n) >= 3]


def _covered(name: str, defined) -> bool:
    """A name is covered when the work has it whole; a path only so, and a dotted name also when the work has
    each of its parts (`more_itertools.chunked`), so `close` alone does not cover `Server.close`."""
    if name in defined:
        return True
    parts = name.split(".")
    return "/" not in name and len(parts) > 1 and all(parts) and all(part in defined for part in parts)


def _defined_by_work(heading: str, defined) -> bool:
    """Whether the work defines a code name the heading carries."""
    return any(_covered(n, defined) for n in _raw_names(heading))


def defined_names(root, skip=()) -> set[str]:
    """What the work defines: the names its source files define (not tests, docs or config), and the paths it
    has: every file and folder path and each trailing part of one (`com/x/Store.java`, `Store.java`), file stems,
    and dotted module names and their trailing parts (`more_itertools.more` for src/more_itertools/more.py).
    `skip` names folders to leave out."""
    out = set()
    for name in tree_hash.source_files(root, docs=True, tests=True, skip=skip) or []:
        parts = name.split("/")
        for end in range(1, len(parts) + 1):
            out.update("/".join(parts[i:end]) for i in range(end))
        module = parts[:-1] + [parts[-1].rsplit(".", 1)[0]]
        if module[-1] == "__init__" and len(module) > 1:
            module = module[:-1]
        out.update(".".join(module[i:]) for i in range(len(module)))
        if tree_hash.is_test(name) or tree_hash.is_doc(name) or name.lower().endswith(CONFIG_SUFFIXES):
            continue
        text = _read(Path(root) / name)
        if text:
            out.update(_DEFINITION.findall(text))
            out.update(_ASSIGNED.findall(text))
    return out - _NOT_NAMES - {""}

def uncited(text: str, requirements: str, defined=frozenset()) -> list[tuple[int, str]]:
    """The leaf headings of a long input that no requirement line (R1, R2, ...) cites, by the heading's words or
    by a code name it carries (`chunked()`, BaseUserManager), and whose code name is not in `defined` (what the
    work defines); none for a short input."""
    if len(text.splitlines()) < LONG_INPUT_LINES:
        return []
    lines = [_words(line) for line in requirements.splitlines() if reviewing._REQUIREMENT.match(line)]
    return [(number, heading) for number, heading in leaf_headings(text)
            if not _is_cited(heading, lines) and not _defined_by_work(heading, defined)]


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


def _goal_inputs(gate_path: str | None, root: str, at_start: dict | None = None) -> list[tuple[Path, str]]:
    """(path, text) of the goal's request.md and of each Markdown file it names, once each; a named file that
    changed since `at_start` recorded it is an output of the work and is left out."""
    if not gate_path:
        return []
    directory = Path(gate_path)
    request = _read(directory / "request.md")
    if request is None:
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
        out.append((path, text))
    return out


def defined_sections(gate_path: str | None, root: str, skip=()) -> list[str]:
    """The headings of the goal's long inputs whose code name the project defines already when the goal starts:
    a definition that was there before the work does not cover them."""
    long = [text for _, text in _goal_inputs(gate_path, root) if len(text.splitlines()) >= LONG_INPUT_LINES]
    if not long:
        return []
    defined = defined_names(root, skip=skip)
    return sorted({heading for text in long for _, heading in leaf_headings(text)
                   if _defined_by_work(heading, defined)})


def uncited_in_goal(gate_path: str | None, root: str, at_start: dict | None = None, skip=(),
                    predefined=()) -> list[dict]:
    """Sections of the goal's request.md and of each Markdown file it names, as {file, line, heading}, that no
    requirement line cites and whose code name the work does not define; `skip` names folders that are not the
    work, and a heading in `predefined` (defined before the goal started) is not covered by a definition. A named
    file that changed since `at_start` recorded it is an output of the work and is left out."""
    requirements = _read(Path(gate_path) / "requirements.md") if gate_path else None
    if requirements is None:
        return []
    out = [{"file": str(path), "line": number, "heading": heading}
           for path, text in _goal_inputs(gate_path, root, at_start)
           for number, heading in uncited(text, requirements)]
    if out:
        defined, before = defined_names(root, skip=skip), set(predefined)
        out = [m for m in out if m["heading"] in before or not _defined_by_work(m["heading"], defined)]
    return out


def message(missing: list[dict], limit: int = 25) -> str:
    """The `close` refusal that lists the sections neither cited nor built."""
    shown = "; ".join(f'"{m["heading"]}" ({Path(m["file"]).name} line {m["line"]})' for m in missing[:limit])
    more = f"; and {len(missing) - limit} more" if len(missing) > limit else ""
    return ("No requirement line quotes these sections of the request, and the work does not define the "
            "function, class or path they name: " + shown + more
            + ". Build each, or add a requirement line that quotes its heading (saying why, if it is out of "
            "scope); then run close again.")
