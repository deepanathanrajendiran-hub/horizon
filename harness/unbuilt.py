"""Names the request gives in code that the work defines nowhere, found by reading: nothing is imported or run.

The surface gate checks the names it can place: a module, or a name with the module it imports from. A request gives
many more in passing ("Functions: `get_logic(**kwargs)`, `most_generic_logic(...)`", "`recover_cfg_all(entries)` —
recover a CFG per function"), and those were on no list. E2E-SWE thin9 run (250581a): the three builds that fell
furthest had each left such names out and closed: pysmt 11 of them (92.6 to 70.4), barf one (82.6 to 56.5), mpmath
four (75.0 to 0). Over 36 tasks the surface gate held about a fifth of the function and class names their requests
give in code.

So this lists, for the session to read against the request, every name the request writes as a call (`name(...)`)
or as a class-like word in a code span, that no source file of the project defines. It is advice and never stops
`close`: a name may belong to another library, or to an example. What is left out, to keep the list worth reading:

- code blocks (an example calls `print` and its own variables), and spans that are not names;
- builtins, typing names, keywords, names that start with `_`, one-letter names and ALL_CAPS words;
- a dotted name whose first part is neither the package, a class nor `self` (`np.array(...)`, `os.path.join(...)`).

"Defines" is read generously: a function, class, assignment, attribute, import or parameter of that name, or the
name as a string (a table of handlers, `setattr`); in a file that is not Python, the word anywhere."""
import ast
import builtins
import keyword
import re
import subprocess
import typing
from pathlib import Path

MAX_FILES = 3000
MAX_BYTES = 2_000_000
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
_SPAN = re.compile(r"`([^`\n]+)`")
# ASCII names only: `iθ(` and `Γ(` in a formula are mathematics, not code.
_CALL = re.compile(r"(?<![\w.])((?:[A-Za-z_]\w*\.)*)([A-Za-z_]\w*)\s*\((?![^)]*[^\x00-\x7f])", re.ASCII)
_CLASSLIKE = re.compile(r"((?:[A-Za-z_]\w*\.)*)([A-Z]\w*)", re.ASCII)
_NOT_NAMES = (set(dir(builtins)) | set(typing.__all__) | set(keyword.kwlist) | {"self", "cls"})
_NOT_SOURCE = {"venv", "env", "site-packages", "node_modules", "build", "dist", "__pycache__"}
_SOURCE = (".py", ".pyi", ".js", ".jsx", ".mjs", ".ts", ".tsx", ".go", ".rs", ".java", ".kt", ".rb", ".php", ".c",
           ".h", ".cpp", ".hpp", ".cs", ".swift", ".scala", ".lua")


def _wanted(name: str) -> bool:
    return (len(name) > 1 and not name.startswith("_") and name not in _NOT_NAMES
            and not (name.isupper() and len(name) > 1))


def _ours(head: str, package: str | None) -> bool:
    """Whether a dotted name's leading part can be the work's own: nothing, the package, a class or `self`."""
    if not head:
        return True
    first = head.split(".")[0]
    return first == package or first[:1].isupper() or first in ("self", "cls")


def request_names(text: str, package: str | None = None) -> list[str]:
    """The names the text writes as a call or as a class-like word in a code span, in the order it gives them."""
    found, fence = {}, None
    for line in (text or "").splitlines():
        opened = _FENCE.match(line)
        if opened:
            fence = None if fence and opened.group(1)[0] == fence[0] else (fence or opened.group(1))
            continue
        if fence:
            continue
        for span in _SPAN.findall(line):
            span = span.strip()
            for head, name in _CALL.findall(span):
                if _wanted(name) and _ours(head.rstrip("."), package):
                    found.setdefault(name, None)
            whole = _CLASSLIKE.fullmatch(span)
            if whole and _wanted(whole.group(2)) and _ours(whole.group(1).rstrip("."), package) \
                    and re.search(r"[a-z]", whole.group(2)):
                found.setdefault(whole.group(2), None)
    return list(found)


def _source_files(root: Path) -> list[str]:
    try:
        out = subprocess.run(["git", "-C", str(root), "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                             capture_output=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode:
        return []
    kept = []
    for name in dict.fromkeys(out.stdout.decode(errors="replace").split("\0")):
        parts = name.split("/")
        if not name.endswith(_SOURCE) or any(p.startswith(".") or p in _NOT_SOURCE for p in parts[:-1]):
            continue
        if any(p in ("tests", "test") for p in parts[:-1]) or parts[-1].startswith("test_") \
                or parts[-1] == "conftest.py":
            continue  # a test that calls the name does not build it
        kept.append(name)
    return kept


def _python_names(source: str) -> set[str] | None:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return None
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.add(node.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            out.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Store):
            out.add(node.attr)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            out |= {(alias.asname or alias.name).split(".")[0] for alias in node.names}
        elif isinstance(node, ast.arg):
            out.add(node.arg)
        elif isinstance(node, ast.keyword) and node.arg:
            out.add(node.arg)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.isidentifier():
            out.add(node.value)
    return out


def defined(root) -> set[str] | None:
    """Every name the project's source defines, or None when the project cannot be read (not a git work tree, or
    too large to read at a session's start)."""
    root = Path(root)
    files = _source_files(root)
    if len(files) > MAX_FILES:
        return None
    out = set()
    for name in files:
        path = root / name
        try:
            if path.stat().st_size > MAX_BYTES:
                continue
            source = path.read_text(errors="replace")
        except OSError:
            continue
        names = _python_names(source) if name.endswith((".py", ".pyi")) else None
        out |= names if names is not None else set(re.findall(r"[A-Za-z_]\w*", source))
    return out


def find(text: str, root, package: str | None = None) -> list[str]:
    """The names of `request_names` that the project defines nowhere. Empty when the project has no source yet (a
    list of everything is no help before the first file) or cannot be read."""
    wanted = request_names(text, package)
    if not wanted:
        return []
    have = defined(root)
    if not have:
        return []
    return [name for name in wanted if name not in have]


def listing(names: list[str], limit: int = 40) -> str:
    more = f", and {len(names) - limit} more" if len(names) > limit else ""
    return ", ".join(names[:limit]) + more
