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
import ast
import textwrap

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

# "Authenticator Class", "JWT Strategy Function": the word before a kind word names code; "The Class ..." does not
_KIND = re.compile(r"\b([A-Za-z_][\w.]*)[ \t]+(?:class|function|method|protocol|decorator|exception|enum|interface"
                   r"|struct|trait)\b", re.I)
_NOT_KIND_NAMES = {"the", "a", "an", "base", "abstract", "main", "core", "helper", "helpers", "utility", "custom",
                   "new", "this", "each", "every", "data", "python", "public", "private", "generic", "parent", "child",
                   "test", "top", "key", "other", "user-defined", "built-in"}
_GIVEN_LANGUAGES = ("python", "py", "python3", "")  # a fenced block the request gives as Python code


def leaf_sections(text: str) -> list[tuple[int, str, str]]:
    """(line number, heading, body) for each Markdown heading outside code fences that has no deeper heading
    under it; the body is the text up to the next heading. A fence closes only on the same character, at least as
    long, as CommonMark has it."""
    lines, found, fence = text.splitlines(), [], None
    for number, line in enumerate(lines, 1):
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
    ends = [number - 1 for _, number, _ in found[1:]] + [len(lines)]
    return [(number, heading, "\n".join(lines[number:end]))
            for i, ((level, number, heading), end) in enumerate(zip(found, ends))
            if i + 1 == len(found) or found[i + 1][0] <= level]


def leaf_headings(text: str) -> list[tuple[int, str]]:
    """(line number, heading) for each leaf section (see leaf_sections)."""
    return [(number, heading) for number, heading, _ in leaf_sections(text)]


def _kind_names(heading: str) -> list[str]:
    """The word before a kind word ("Authenticator Class"), unless it is a plain word such as "The"."""
    return [n for n in _KIND.findall(heading) if n.lower() not in _NOT_KIND_NAMES]


def _words(text: str) -> str:
    text = re.sub(r"[*_`\"'“”‘’]", "", text.lower())
    return " ".join(text.split())


def _code_names(heading: str) -> list[str]:
    """The code names a heading carries, for citing: backticked text, `name()` calls, snake_case and CamelCase
    words. The word before a kind word ("Strategy Class") is not one: it is often a common word."""
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
    text, and outside backticks each path, `name()` call, snake_case or CamelCase word, and the word before a
    kind word ("Strategy Class"). A path stays whole: its
    folder names are not names of their own."""
    names = [m.group(1) for text in re.findall(r"`([^`]+)`", heading) if (m := re.match(r"\s*([\w$./-]+)", text))]
    for token in re.findall(r"[\w$./-]+(?:\(\))?", re.sub(r"`[^`]*`", " ", heading)):
        word = token.removesuffix("()")
        if ("/" in word or token.endswith("()") or "_" in word.strip("_")
                or re.search(r"[a-z][A-Z]|[A-Z]{2,}[a-z]", word)):
            names.append(word)
    names += _kind_names(re.sub(r"`[^`]*`", " ", heading))
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

def _base_name(node) -> str:
    """`Generic[UP, ID]` -> Generic, `abc.ABC` -> ABC."""
    while isinstance(node, (ast.Subscript, ast.Call)):
        node = node.value if isinstance(node, ast.Subscript) else node.func
    return node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", "")


def _python_definitions(source: str) -> dict[str, list[tuple]]:
    """Top-level classes and functions, and the methods of top-level classes (`Class.method`), of Python source:
    {name: [(kind, info, line)]}, where info is a class's base names, or a function's parameter names, whether
    it takes *args or **kwargs, and whether its first parameter is self or cls; empty for text that is not Python."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return {}
    lines, out = source.splitlines(), {}

    def add(key, node):
        line = lines[node.lineno - 1].strip() if node.lineno <= len(lines) else ""
        if isinstance(node, ast.ClassDef):
            info = tuple(n for n in (_base_name(b) for b in node.bases) if n and n != "object")
            out.setdefault(key, []).append(("class", info, line))
        else:
            a = node.args
            every = [p.arg for p in a.posonlyargs + a.args + a.kwonlyargs]
            names = [n for n in every if n not in ("self", "cls")]
            method = bool(every) and every[0] in ("self", "cls")
            out.setdefault(key, []).append(("def", (tuple(names), bool(a.vararg or a.kwarg), method), line))

    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            add(node.name, node)
        if isinstance(node, ast.ClassDef):
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add(f"{node.name}.{item.name}", item)
    return out


def given_definitions(body: str) -> dict[str, list[tuple]]:
    """The Python definitions in a section's fenced code blocks (untagged or tagged python)."""
    out, block, fence, language = {}, [], None, ""
    for line in body.splitlines():
        opened = _FENCE.match(line)
        if fence:
            if opened and opened.group(1)[0] == fence[0] and len(opened.group(1)) >= len(fence) \
                    and not line[opened.end():].strip():
                if language in _GIVEN_LANGUAGES:
                    for key, found in _python_definitions(textwrap.dedent("\n".join(block))).items():
                        out.setdefault(key, []).extend(found)
                fence, block = None, []
            else:
                block.append(line)
        elif opened:
            fence, language = opened.group(1), line[opened.end():].strip().lower().split(" ")[0]
    return out


def work_definitions(root, skip=()) -> dict[str, list[tuple]]:
    """The Python definitions of the work's source files (not tests or docs), each with its file and line."""
    out = {}
    for name in tree_hash.source_files(root, docs=True, tests=True, skip=skip) or []:
        if not name.endswith(".py") or tree_hash.is_test(name) or tree_hash.is_doc(name):
            continue
        text = _read(Path(root) / name)
        for key, found in (_python_definitions(text) if text else {}).items():
            out.setdefault(key, []).extend((kind, info, f"{line}` ({name})") for kind, info, line in found)
    return out


def _ancestors(bases: tuple, work: dict, seen=None) -> set[str]:
    """The base names of a work class and, through the work's own classes, of their bases."""
    seen = set() if seen is None else seen
    out = set(bases)
    for base in bases:
        if base in seen:
            continue
        seen.add(base)
        for kind, info, _ in work.get(base, []):
            if kind == "class":
                out |= _ancestors(info, work, seen)
    return out


def _agrees(given: tuple, found: tuple, work: dict) -> bool:
    """A class agrees when it has every base the request gives, directly or through the work's own classes; a
    function when it takes every parameter the request names, or takes *args or **kwargs."""
    if given[0] != found[0]:
        return False
    if given[0] == "class":
        return set(given[1]) <= _ancestors(found[1], work)
    return set(given[1][0]) <= set(found[1][0]) or found[1][1]


def differs(heading: str, body: str, work: dict) -> str | None:
    """How the work defines the code a section gives differently, for the definitions the heading names (and
    their methods); None when every one the work defines agrees. A function the section shows alone with a
    `self` parameter is a method, and any method of that name may agree."""
    names = set(_raw_names(heading))
    names |= {n.split(".")[-1] for n in names}
    for key, given in given_definitions(body).items():
        if key.split(".")[0] not in names:
            continue
        for g in given:
            keys = [key] + ([k for k in work if k.endswith("." + key)] if g[0] == "def" and g[1][2] else [])
            found = [f for k in keys for f in work.get(k, [])]
            if found and not any(_agrees(g, f, work) for f in found):
                return f"the request has `{g[2]}`, the work `{found[0][2]}"
    return None

def _not_quoted(text: str, requirements: str) -> list[tuple[int, str, str]]:
    """(line, heading, body) of each leaf section of a long input that no requirement line cites; none for a
    short input."""
    if len(text.splitlines()) < LONG_INPUT_LINES:
        return []
    lines = [_words(line) for line in requirements.splitlines() if reviewing._REQUIREMENT.match(line)]
    return [(number, heading, body) for number, heading, body in leaf_sections(text)
            if not _is_cited(heading, lines)]


def uncited(text: str, requirements: str, defined=frozenset()) -> list[tuple[int, str]]:
    """The leaf headings of a long input that no requirement line (R1, R2, ...) cites, by the heading's words or
    by a code name it carries (`chunked()`, BaseUserManager), and whose code name is not in `defined` (what the
    work defines); none for a short input."""
    return [(number, heading) for number, heading, _ in _not_quoted(text, requirements)
            if not _defined_by_work(heading, defined)]


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
    """Sections of the goal's request.md and of each Markdown file it names, as {file, line, heading, kind}, that
    no requirement line cites and the work has not built: kind "differs" (with a `detail`) when the work defines
    the code the section names but not as the Python code the section gives; "code" when the section names code the
    work does not define; "prose" when it names none. `skip` names folders that are not the work; a heading in
    `predefined` (defined before the goal started) is not covered by a definition. A named file that changed since
    `at_start` recorded it is an output of the work and is left out."""
    requirements = _read(Path(gate_path) / "requirements.md") if gate_path else None
    if requirements is None:
        return []
    candidates = [(path, number, heading, body) for path, text in _goal_inputs(gate_path, root, at_start)
                  for number, heading, body in _not_quoted(text, requirements)]
    if not candidates:
        return []
    defined, before, work, out = defined_names(root, skip=skip), set(predefined), None, []
    for path, number, heading, body in candidates:
        item = {"file": str(path), "line": number, "heading": heading}
        if heading not in before and _defined_by_work(heading, defined):
            work = work_definitions(root, skip=skip) if work is None else work
            detail = differs(heading, body, work)
            if detail is None:
                continue
            item.update(kind="differs", detail=detail)
        else:
            item["kind"] = "code" if _raw_names(heading) else "prose"
        out.append(item)
    return out


def message(missing: list[dict], limit: int = 25) -> str:
    """The `close` refusal that lists the sections neither cited nor built: code the work defines differently
    first, then code it does not define, then prose sections."""
    groups = [("differs", "These sections give code the work defines differently: "),
              ("code", "These sections name code the work has not built in this goal: "),
              ("prose", "No requirement line quotes these sections: ")]
    parts, shown = [], 0
    for kind, lead in groups:
        items = [m for m in missing if m.get("kind", "code") == kind][:max(0, limit - shown)]
        if not items:
            continue
        shown += len(items)
        parts.append(lead + "; ".join(f'"{m["heading"]}" ({Path(m["file"]).name} line {m["line"]})'
                                      + (f': {m["detail"]}' if m.get("detail") else "") for m in items) + ".")
    more = f" And {len(missing) - shown} more." if len(missing) > shown else ""
    return (" ".join(parts) + more + " Build or fix each to match the request, or add a requirement line that "
            "quotes its heading (saying why, if it is out of scope or differs on purpose); then run close again.")
