"""Functions of the work that are still placeholders, found by reading the source: nothing is imported or run.

A placeholder is a function with no body of its own: a docstring at most, then `pass`, `...` or
`raise NotImplementedError`, and either that raise or a docstring that calls it a placeholder. These are not:

- a function that does work and raises NotImplementedError on one path (E2E-SWE big run, invoke: a mock's lookup
  that raises when no result was registered was listed for five sessions);
- a method another class of the project overrides, or one of a class named Abstract*/Base* or based on
  ABC/Protocol: that is an interface (big run: 32 of 62 final builds were told to build methods such as
  `Operator.match`, `Filter.apply` and `Runner.run`, which every subclass defines; pgqueuer spent a whole session
  on them and authlib calls in ten);
- a special method other than `__init__` (wrapt: `__copy__` raises by the request's own design), or a raise whose
  message says who must supply the method ("must implement match()");
- a function a recorded fact names, with its file, as "by design".
"""

import ast
import re
import subprocess
from pathlib import Path

NOT_SOURCE = {"venv", "env", "site-packages", "node_modules", "build", "dist", "__pycache__"}
_ABSTRACT_NAME = re.compile(r"(?:Abstract|Base)(?:[A-Z_]|$)")
_MARKED = re.compile(r"\bplaceholder\b", re.I)
_SPECIAL = re.compile(r"__\w+__$")
MODULE = "module marked placeholder"


# `raise NotImplementedError('%s must implement match()' % ...)`: the library's user supplies this method (big run,
# grappa: `Operator.match`, which the request's `@operator` classes define).
_DELIBERATE = re.compile(r"must (?:implement|define|override|provide|be (?:implemented|overridden|defined|provided))"
                         r"|subclass", re.I)

def _ref(node) -> str | None:
    return getattr(node, "attr", None) or getattr(node, "id", None)


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
        if not name.endswith(".py"):
            continue
        if (any(p.startswith(".") or p in NOT_SOURCE or p in ("tests", "test") for p in parts[:-1])
                or parts[-1].startswith("test_") or parts[-1] == "conftest.py"):
            continue
        kept.append(name)
    return kept


def _bare(node) -> tuple[bool, bool]:
    """(the function has no body of its own, it raises NotImplementedError as a placeholder would). A raise whose
    message says who must supply the method (`_DELIBERATE`) is an interface, not a placeholder."""
    body = list(node.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    raises = False
    for statement in body:
        if isinstance(statement, ast.Pass):
            continue
        if isinstance(statement, ast.Expr) and (
                (isinstance(statement.value, ast.Constant) and statement.value.value is Ellipsis)
                or (isinstance(statement.value, ast.Yield) and statement.value.value is None)):
            continue
        if isinstance(statement, ast.Return) and (
                statement.value is None or (isinstance(statement.value, ast.Constant)
                                            and statement.value.value is None)):
            continue
        if isinstance(statement, ast.Raise) and "NotImplementedError" in ast.unparse(statement.exc or statement):
            raises = raises or not _DELIBERATE.search(ast.unparse(statement.exc or statement))
            continue
        return False, False
    return True, raises


def _is_placeholder(node) -> bool:
    bare, raises = _bare(node)
    return bare and (raises or bool(_MARKED.search(ast.get_docstring(node) or "")))


def _abstract(node: ast.ClassDef) -> bool:
    bases = {_ref(b) for b in node.bases}
    return bool(_ABSTRACT_NAME.match(node.name) or bases & {"ABC", "Protocol"}
                or any(_ref(k.value) == "ABCMeta" for k in node.keywords))


def _by_design(name: str, facts) -> list[str]:
    """The recorded facts that call something in this file "by design"."""
    return [fact for fact in facts if "by design" in fact.lower()
            and re.search(r"(?<![\w/.-])" + re.escape(name) + r"(?![\w/.-])", fact)]


def _waived(hit: str, noted: list[str]) -> bool:
    """A "by design" fact covers the functions it names, as `Class.method` or by the last name alone; one that
    names only the file covers nothing (thin4 run, mapillary_tools: the note is the way past `close`, so it has to
    say which function is meant)."""
    leaf = re.escape(hit.rsplit(".", 1)[-1])
    return any(re.search(r"(?<!\w)" + leaf + r"(?!\w)", _strip_paths(fact)) for fact in noted)


def _strip_paths(fact: str) -> str:
    return re.sub(r"\S+\.py\b", " ", fact)


def keys(found: list[tuple[str, list[str]]]) -> list[str]:
    """`file::name` for each placeholder found, the form `find(skip=...)` takes."""
    return [f"{name}::{hit}" for name, hits in found for hit in hits]


def find(root, facts=(), skip=()) -> list[tuple[str, list[str]]]:
    """[(file, [placeholder names])] for the project's source files, tests left out. `skip` holds the `file::name`
    of placeholders that were in the project before the goal: a codebase's own unfinished corners are not the
    work of a goal that changes something else in it."""
    root = Path(root)
    skip = set(skip)
    trees = {}
    for name in _source_files(root):
        try:
            trees[name] = ast.parse((root / name).read_text(errors="replace"))
        except (OSError, SyntaxError, ValueError):
            continue
    # Every class by name: its bases and the methods it defines, so an overridden method can be told apart.
    bases, methods = {}, {}
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                bases.setdefault(node.name, set()).update(b for b in map(_ref, node.bases) if b)
                methods.setdefault(node.name, set()).update(
                    child.name for child in node.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and not _is_placeholder(child))

    def overridden(owner: str, method: str) -> bool:
        seen, queue = set(), [owner]
        while queue:
            current = queue.pop()
            for name, parents in bases.items():
                if current in parents and name not in seen:
                    seen.add(name)
                    if method in methods.get(name, ()):
                        return True
                    queue.append(name)
        return False

    found = []
    for name, tree in trees.items():
        hits, real = [], False
        noted = _by_design(name, facts)
        stack = [(node, "", None) for node in tree.body]
        while stack:
            node, prefix, owner = stack.pop(0)
            if isinstance(node, ast.ClassDef):
                if _abstract(node):
                    real = True
                    continue
                stack += [(child, f"{prefix}{node.name}.", node.name) for child in node.body]
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not _is_placeholder(node):
                    real = True
                elif owner and ((_SPECIAL.match(node.name) and node.name != "__init__")
                                or overridden(owner, node.name)
                                or any(_ref(d) == "abstractmethod" for d in node.decorator_list)):
                    continue
                elif _waived(prefix + node.name, noted) or f"{name}::{prefix}{node.name}" in skip:
                    continue
                else:
                    hits.append(prefix + node.name)
        # A module that calls itself a placeholder and defines nothing that does work.
        if not real and not noted and f"{name}::{MODULE}" not in skip \
                and _MARKED.search((ast.get_docstring(tree) or "").split("\n\n")[0]):
            hits.append(MODULE)
        if hits:
            found.append((name, hits))
    return found


def listing(found: list[tuple[str, list[str]]], limit: int = 10) -> str:
    shown = []
    for name, hits in found[:limit]:
        names = ", ".join(hits[:4]) + (f" and {len(hits) - 4} more" if len(hits) > 4 else "")
        shown.append(f"{name} ({names})")
    more = f"; and {len(found) - limit} more files" if len(found) > limit else ""
    return "; ".join(shown) + more
