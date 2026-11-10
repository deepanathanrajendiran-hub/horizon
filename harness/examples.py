"""A gate of a spec's own runnable examples and signatures, generated before the code exists.

NL2Repo held-out review (fun docs/evaluations/nl2repo-heldout/transcripts/review.md): the surface gate checks
names only, and the agent's own behaviour tests checked its own guesses. The behaviours that lost tests were
visible in the spec's examples: a printed `# Output:`, an `assert`, an `except SomeError:`, a listed signature.
In four builds a check the agent had written from the spec failed and the agent edited the check to match its
code. This module reads those examples from the spec, so they are fixed before any code is written: a wrong
example is waived with a reason that cites the spec (`waive`), never rewritten.

The generated test file is self-contained. An example that uses a name it never defines (an illustrative
fragment), needs a module outside the package that is not installed, or runs longer than its time limit is
skipped, not failed: only the package's own behaviour fails it."""
import ast
import io
import json
import re
import textwrap
import tokenize
from pathlib import Path
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import hookguard

_FENCE = re.compile(r"^[ \t]*(```|~~~)[ \t]*([\w+.-]*)")
_LANGS = {"", "python", "py", "python3", "pycon", "pytest", "ipython"}
_MARKER = re.compile(r"^\s*(?:(?:output|out|result|returns?|expected|prints?)\s*:|->|=>|→)\s*(.*)$", re.I)
_PROSE_HEAD = re.compile(r"^[A-Za-z_]")
_WAIVED = "examples_waived.json"

FILE = "spec_examples.py"
_SPEC_LINE = re.compile(r"\bline\s+\d+", re.I)


def _blocks(text: str):
    """(first spec line of the body, language, body) for each fenced block."""
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        match = _FENCE.match(lines[i])
        if not match:
            i += 1
            continue
        fence, lang = match.group(1), match.group(2).lower()
        body, j = [], i + 1
        while j < len(lines) and not lines[j].lstrip().startswith(fence):
            body.append(lines[j])
            j += 1
        yield i + 2, lang, textwrap.dedent("\n".join(body))
        i = j + 1


def _parse(source: str):
    """The block as a module, or None. A listing of bare signatures (`def f(...) -> T:` with no body) gets `...`
    bodies so it parses."""
    try:
        return ast.parse(source)
    except SyntaxError:
        pass
    lines = source.splitlines()
    patched = []
    for k, line in enumerate(lines):
        patched.append(line)
        if line.rstrip().endswith(":") and not line.lstrip().startswith("#"):
            indent = len(line) - len(line.lstrip())
            following = next((l for l in lines[k + 1:] if l.strip()), "")
            if len(following) - len(following.lstrip()) <= indent:
                patched.append(" " * (indent + 4) + "...")
    try:
        return ast.parse("\n".join(patched))
    except SyntaxError:
        return None


def _in_package(module: str | None, package: str) -> bool:
    return bool(module) and (module == package or module.startswith(package + "."))


def _imports(tree, package: str) -> dict[str, str]:
    """Names this block imports from the package, mapped to their module."""
    found = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and _in_package(node.module, package):
            for alias in node.names:
                found[alias.asname or alias.name] = node.module
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _in_package(alias.name, package):
                    found[(alias.asname or alias.name).split(".")[0]] = alias.name
    return found


def _comments(source: str) -> list[tuple[int, str, bool]]:
    """(line, text after '#', whether the line holds only the comment)."""
    out = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                alone = not tok.line[:tok.start[1]].strip()
                out.append((tok.start[0], tok.string[1:].strip(), alone))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return out


def _literal(text: str) -> bool:
    try:
        ast.literal_eval(text)
        return True
    except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
        return False


def _expectations(tree, source: str) -> dict[int, str]:
    """Statement end line -> expected output, for top-level expression statements only (a print in a loop
    prints more than once)."""
    exprs = {node.end_lineno: node for node in tree.body if isinstance(node, ast.Expr)}
    code_lines = set()
    for node in ast.walk(tree):
        if hasattr(node, "lineno"):
            code_lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    comments = _comments(source)
    expect = {}
    for k, (line, text, alone) in enumerate(comments):
        marker = _MARKER.match(text)
        if not alone:
            if line not in exprs:
                continue
            if marker and marker.group(1).strip():
                expect[line] = marker.group(1).strip()
            elif not marker and not _PROSE_HEAD.match(text) and _literal(text):
                expect[line] = text
            continue
        if not marker:
            continue
        value = marker.group(1).strip()
        if not value:
            # `# Output:` then exactly one comment line holding the value
            nxt = comments[k + 1] if k + 1 < len(comments) else None
            after = comments[k + 2] if k + 2 < len(comments) else None
            if not (nxt and nxt[2] and nxt[0] == line + 1) or (after and after[2] and after[0] == line + 2):
                continue
            value = nxt[1]
        above = max((end for end in exprs if end < line), default=None)
        if above is None or any(n in code_lines for n in range(above + 1, line)):
            continue
        expect.setdefault(above, value)
    return expect


def _params(args: ast.arguments, method: bool) -> list[list[str]]:
    positional = [a.arg for a in (*args.posonlyargs, *args.args)]
    if method and positional and positional[0] in ("self", "cls"):
        positional = positional[1:]
    return [[name, "pos"] for name in positional] + [[a.arg, "kw"] for a in args.kwonlyargs]


def _decorated(node, name: str) -> bool:
    return any((getattr(d, "attr", None) or getattr(d, "id", None)) == name for d in node.decorator_list)


def _signatures(tree, imported: dict[str, str], line: int) -> list[dict]:
    """Classes and functions the block imports from the package and then lists, or bare signatures."""
    sigs = []
    for node in tree.body:
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) or node.name.startswith("_"):
            continue
        module = imported.get(node.name)
        bare = all(isinstance(s, ast.Pass) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant))
                   for s in node.body)
        if module is None and not (bare and not isinstance(node, ast.ClassDef)):
            continue
        where = line + node.lineno - 1
        if isinstance(node, ast.ClassDef):
            methods = {}
            for item in node.body:
                if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                if item.name != "__init__" and item.name.startswith("_"):
                    continue
                if _decorated(item, "property"):
                    methods[item.name] = None
                else:
                    methods[item.name] = _params(item.args, method=not _decorated(item, "staticmethod"))
            bases = [ast.unparse(b) for b in node.bases
                     if isinstance(b, (ast.Name, ast.Attribute)) and ast.unparse(b) not in ("object",)]
            sigs.append({"id": f"sig_{node.name}_L{where}", "line": where, "module": module, "name": node.name,
                         "kind": "class", "bases": bases, "methods": methods, "params": None,
                         "imports": imported})
        else:
            sigs.append({"id": f"sig_{node.name}_L{where}", "line": where, "module": module, "name": node.name,
                         "kind": "def", "bases": [], "methods": {}, "params": _params(node.args, method=True),
                         "imports": imported})
    return sigs


def _doctest_source(body: str) -> str:
    return "\n".join(line[4:] for line in body.splitlines() if line.startswith((">>> ", "... ")))


def extract(text: str, package: str) -> tuple[list[dict], list[dict]]:
    """The spec's checkable examples and listed signatures for `package`."""
    found, sigs, n = [], [], 0
    for line, lang, body in _blocks(text):
        if lang not in _LANGS or not body.strip():
            continue
        doctest = ">>> " in body
        source = _doctest_source(body) if doctest else body
        tree = _parse(source)
        if tree is None:
            continue
        imported = _imports(tree, package)
        if not doctest:
            sigs += _signatures(tree, imported, line)
        if not imported:
            continue
        expect = {} if doctest else _expectations(tree, source)
        calls = []
        for node in tree.body:
            if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test")
                    and all(a.arg in ("tmp_path",) for a in (*node.args.posonlyargs, *node.args.args,
                                                             *node.args.kwonlyargs))
                    and not node.args.vararg and not node.args.kwarg):
                calls.append(node.name)
        checks = any(isinstance(node, ast.Assert) for node in ast.walk(tree))
        raises = any(isinstance(node, ast.ExceptHandler) and node.type is not None
                     and any(isinstance(x, ast.Name) and x.id in imported for x in ast.walk(node.type))
                     for node in ast.walk(tree))
        raises = raises or "pytest.raises" in source
        if not (expect or calls or checks or raises or doctest):
            continue
        n += 1
        found.append({"id": f"ex{n}_L{line}", "line": line, "source": body if doctest else source,
                      "expect": expect, "calls": calls, "doctest": doctest})
    seen, unique = set(), []
    for sig in sigs:
        if sig["id"] not in seen:
            seen.add(sig["id"])
            unique.append(sig)
    return found, unique


_RUNTIME = r'''
import ast
import asyncio
import builtins
import contextlib
import doctest as _doctest
import importlib
import inspect
import io
import json
import pkgutil
import re
import signal
import sys
import traceback
from pathlib import Path

import pytest

TIME_LIMIT_S = 10
_WAIVED_FILE = Path(__file__).with_name("examples_waived.json")


def _find_package():
    here = Path(__file__).resolve()
    for base in (Path.cwd(), *here.parents):
        for root in (base, base / "src"):
            if (root / PACKAGE).is_dir() or (root / (PACKAGE + ".py")).is_file():
                return root
    return None


_ROOT = _find_package()
if _ROOT is not None and str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _waived():
    try:
        return json.loads(_WAIVED_FILE.read_text())
    except (OSError, ValueError):
        return {}


def _skip_if_waived(item_id):
    reason = _waived().get(item_id)
    if reason:
        pytest.skip(f"waived: {reason}")


class _Mismatch(AssertionError):
    pass


class _Timeout(Exception):
    pass


@contextlib.contextmanager
def _time_limit():
    if not hasattr(signal, "setitimer"):
        yield
        return

    def expire(signum, frame):
        raise _Timeout()

    old = signal.signal(signal.SIGALRM, expire)
    signal.setitimer(signal.ITIMER_REAL, TIME_LIMIT_S)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


_NO = object()


def _value(text):
    text = text.strip()
    for parse in (ast.literal_eval, json.loads):
        try:
            return parse(text)
        except Exception:
            continue
    return _NO


def _norm(text):
    return " ".join(str(text).replace('"', "'").split())


def _squash(text):
    return re.sub(r"\s+", "", str(text).replace('"', "'"))


def _matches(text, values, expected):
    wanted = [expected.strip()]
    if len(wanted[0]) >= 2 and wanted[0][0] == wanted[0][-1] and wanted[0][0] in "'\"":
        wanted.append(wanted[0][1:-1])
    seen = [text]
    if len(values) == 1:
        seen += [repr(values[0]), str(values[0])]
        if isinstance(values[0], (bytes, bytearray)):
            seen.append(bytes(values[0]).decode("utf-8", "replace"))
    for want in wanted:
        for got in seen:
            if _norm(got) == _norm(want) or _squash(got) == _squash(want):
                return True
            if "..." in want or "…" in want:
                parts = re.split(r"\.\.\.|…", _squash(want))
                if re.fullmatch(".*".join(re.escape(p) for p in parts), _squash(got), re.S):
                    return True
        squashed = _squash(want)
        for got in seen:
            g = _squash(got)
            # `print(f"Result: {x}")  # 3`: the value after a label
            if squashed and len(g) > len(squashed) and g.endswith(squashed) and g[-len(squashed) - 1] in ":=":
                return True
        literal = _value(want)
        if literal is not _NO:
            if len(values) == 1 and (_equal(values[0], literal) or _same_items(values[0], literal)):
                return True
            if any(_equal(_value(got), literal) or _same_items(_value(got), literal) for got in seen):
                return True
    return False


def _same_items(a, b):
    """A set printed where the spec shows a list, or the reverse."""
    kinds = (list, tuple, set, frozenset)
    if not (isinstance(a, kinds) and isinstance(b, kinds)) or not (isinstance(a, (set, frozenset))
                                                                 or isinstance(b, (set, frozenset))):
        return False
    try:
        return sorted(map(repr, a)) == sorted(map(repr, b))
    except Exception:
        return False


def _equal(a, b):
    try:
        return a is not _NO and bool(a == b)
    except Exception:
        return False


def _check(example, line, text, values):
    expected = example["expect"][line]
    if not _matches(text, values, expected):
        statement = example["source"].splitlines()[line - 1].strip()
        raise _Mismatch(f"SPEC line {example['line'] + line - 1}: `{statement}` printed {text!r}; "
                        f"the spec says {expected!r}. Fix the library, not this check.")


def _instrument(example):
    tree = ast.parse(example["source"])
    for node in tree.body:
        if not isinstance(node, ast.Expr) or node.end_lineno not in example["expect"]:
            continue
        call = node.value
        line = ast.Constant(node.end_lineno)
        if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "print":
            node.value = ast.Call(ast.Name("__hz_print", ast.Load()), [line, *call.args], call.keywords)
        elif any(isinstance(n, ast.Await) for n in ast.walk(call)):
            # `await f()  # Output: 3`: a lambda cannot await, so the value is checked as it is
            node.value = ast.Call(ast.Name("__hz_awaited", ast.Load()), [line, call], [])
        else:
            thunk = ast.Lambda(ast.arguments([], [], None, [], [], None, []), call)
            node.value = ast.Call(ast.Name("__hz_value", ast.Load()), [line, thunk], [])
    return ast.fix_missing_locations(tree)


def _where(exc, filename):
    frames = traceback.extract_tb(exc.__traceback__)
    return frames[-1] if frames else None


def _outcome(example, exc, filename):
    """Skip what the example itself lacks; fail what the package does wrong."""
    last = _where(exc, filename)
    if isinstance(exc, _Timeout):
        pytest.skip(f"example ran over {TIME_LIMIT_S} s")
    if isinstance(exc, NameError) and last is not None and last.filename == filename:
        pytest.skip(f"illustrative fragment: {exc}")
    if isinstance(exc, ModuleNotFoundError) and exc.name and exc.name.split(".")[0] != PACKAGE:
        pytest.skip(f"needs {exc.name}, which is not installed")
    if isinstance(exc, FileNotFoundError) and exc.filename and Path(str(exc.filename)).name in example["source"]:
        pytest.skip(f"assumes a file the example does not create: {exc.filename}")
    if _network(exc):
        pytest.skip(f"needs the network or a service: {exc}")
    if isinstance(exc, _Mismatch):
        pytest.fail(str(exc), pytrace=False)
    at = ""
    if last is not None:
        at = (f" at SPEC line {example['line'] + last.lineno - 1}" if last.filename == filename
              else f" in {Path(last.filename).name}:{last.lineno}")
    pytest.fail(f"spec example starting at SPEC line {example['line']} raised {type(exc).__name__}: {exc}{at}. "
                "Fix the library, not this check.", pytrace=False)


def _network(exc):
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        name = type(exc).__name__
        if isinstance(exc, (ConnectionError, TimeoutError)) or name in ("URLError", "HTTPError", "gaierror",
                                                                          "ConnectError", "ConnectTimeout"):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


def _run_doctest(example):
    parser = _doctest.DocTestParser()
    test = parser.get_doctest(example["source"], {}, example["id"], None, 0)
    runner = _doctest.DocTestRunner(optionflags=_doctest.ELLIPSIS | _doctest.NORMALIZE_WHITESPACE
                                    | _doctest.IGNORE_EXCEPTION_DETAIL, verbose=False)
    out = io.StringIO()
    with _time_limit():
        result = runner.run(test, out=out.write)
    if result.failed:
        pytest.fail(f"spec example at SPEC line {example['line']}:\n" + out.getvalue()[-1500:], pytrace=False)


def _run(example, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    if example["doctest"]:
        return _run_doctest(example)
    filename = f"<spec example {example['id']}>"
    try:
        code = compile(_instrument(example), filename, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    except SyntaxError as exc:
        pytest.skip(f"the example does not compile here: {exc}")

    def hz_print(line, *args, sep=" ", end="\n", file=None, flush=False):
        text = sep.join(str(a) for a in args)
        print(text, end=end)
        _check(example, line, text, args)

    def hz_value(line, thunk):
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            value = thunk()
        printed = buffer.getvalue().rstrip("\n")
        if value is None and printed:
            _check(example, line, printed, ())
        else:
            _check(example, line, repr(value), (value,))

    def hz_awaited(line, value):
        _check(example, line, repr(value), (value,))

    space = {"__name__": "__spec_example__", "__hz_print": hz_print, "__hz_value": hz_value,
             "__hz_awaited": hz_awaited}
    try:
        with _time_limit():
            result = eval(code, space)
            if inspect.iscoroutine(result):
                asyncio.run(result)
            for name in example["calls"]:
                fn = space[name]
                wants = inspect.signature(fn).parameters
                out = fn(tmp_path) if "tmp_path" in wants else fn()
                if inspect.iscoroutine(out):
                    asyncio.run(out)
    except BaseException as exc:
        if isinstance(exc, (KeyboardInterrupt, SystemExit, pytest.skip.Exception, pytest.fail.Exception)):
            raise
        _outcome(example, exc, filename)


@pytest.mark.parametrize("example", EXAMPLES, ids=[e["id"] for e in EXAMPLES])
def test_spec_example(example, tmp_path, monkeypatch):
    _skip_if_waived(example["id"])
    _run(example, tmp_path, monkeypatch)


def _import(name):
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.split(".")[0] != PACKAGE:
            pytest.skip(f"needs {exc.name}, which is not installed")
        raise


def _locate(sig):
    if sig["module"]:
        module = _import(sig["module"])
        obj = getattr(module, sig["name"], None)
        if obj is None:
            pytest.fail(f"{sig['module']}.{sig['name']} is missing (SPEC line {sig['line']})", pytrace=False)
        return module, obj
    package = _import(PACKAGE)
    candidates = [package]
    for info in pkgutil.walk_packages(getattr(package, "__path__", []), PACKAGE + "."):
        if info.name.rsplit(".", 1)[-1].startswith(("__main__", "test")):
            continue
        try:
            candidates.append(importlib.import_module(info.name))
        except Exception:
            continue
    for module in candidates:
        obj = getattr(module, sig["name"], None)
        if obj is not None and getattr(obj, "__module__", "").startswith(PACKAGE):
            return module, obj
    pytest.skip(f"{sig['name']} not found in {PACKAGE}; the surface gate checks names")


def _check_params(fn, spec, label, line):
    """What is wrong with fn's parameters against the spec's, or None."""
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return None
    params = list(signature.parameters.values())
    names = [p.name for p in params]
    var_kw = any(p.kind is p.VAR_KEYWORD for p in params)
    wanted = ", ".join(n if k == "pos" else f"*, {n}" for n, k in spec)
    missing = [n for n, k in spec if n not in names and not var_kw]
    if missing:
        return (f"{label} lacks parameter(s) {', '.join(missing)}; SPEC line {line} gives {label}({wanted}), "
                f"the library has {label}{signature}")
    positional = [p.name for p in params if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
    order = [n for n, k in spec if k == "pos" and n in positional]
    if [n for n in positional if n in order] != order:
        return (f"{label} takes its parameters in another order; SPEC line {line} gives {label}({wanted}), "
                f"the library has {label}{signature}")
    return None


def _base(sig, module, name):
    source = sig["imports"].get(name)
    try:
        if source:
            imported = importlib.import_module(source)
            return getattr(imported, name, None) if imported.__name__ != name else imported
    except Exception:
        return None
    if "." not in name and hasattr(builtins, name):
        return getattr(builtins, name)
    return getattr(module, name, None)


@pytest.mark.parametrize("sig", SIGNATURES, ids=[s["id"] for s in SIGNATURES])
def test_spec_signature(sig):
    _skip_if_waived(sig["id"])
    module, obj = _locate(sig)
    label = f"{sig['module'] or PACKAGE}.{sig['name']}"
    if sig["kind"] == "def":
        problems = [_check_params(obj, sig["params"], label, sig["line"])]
    else:
        problems = []
        for base_name in sig["bases"]:
            base = _base(sig, module, base_name)
            if isinstance(base, type) and isinstance(obj, type) and not issubclass(obj, base):
                problems.append(f"{label} is not a subclass of {base_name} (SPEC line {sig['line']})")
        for method, spec in sig["methods"].items():
            if method != "__init__" and not hasattr(obj, method):
                problems.append(f"{label}.{method} is missing (SPEC line {sig['line']})")
            elif spec is not None:
                target = obj if method == "__init__" else getattr(obj, method)
                name = label if method == "__init__" else f"{label}.{method}"
                problems.append(_check_params(target, spec, name, sig["line"]))
    problems = [p for p in problems if p]
    if problems:
        pytest.fail("; ".join(problems) + ". Fix the library, not this check.", pytrace=False)
'''
_HEADER = '''"""Generated by Horizon from {spec}: the spec's own examples and signatures. Do not edit this file.

Advisory, not part of the gate: a spec's examples are sometimes wrong. `examples SLUG` runs it. For each
disagreement, read the spec's prose: fix the library when the prose agrees with the example; otherwise waive the
example with `waive-example SLUG ID --reason "SPEC line N ..."`."""
'''


def write_gate(spec: Path, package: str, out: Path) -> dict:
    """Write the example gate to `out` and an empty waiver file beside it. Returns the counts."""
    text = Path(spec).read_text(errors="replace")
    found, sigs = extract(text, package)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    body = (_HEADER.format(spec=re.sub(r"[^\w. -]", "_", Path(spec).name))
            + f"PACKAGE = {package!r}\nEXAMPLE_COUNT = {len(found)}\n"
            + f"EXAMPLES = {found!r}\nSIGNATURES = {sigs!r}\n" + _RUNTIME)
    out.write_text(body)
    waived = out.with_name(_WAIVED)
    if not waived.exists():
        waived.write_text("{}\n")
    return {"examples": len(found), "signatures": len(sigs)}


def ids(gate_dir: Path) -> list[str]:
    """Every example and signature id in a gate directory's generated file."""
    text = (Path(gate_dir) / FILE).read_text()
    return re.findall(r"'id': '((?:ex|sig_)[\w.-]+)'", text)


def waive(gate_dir: Path, item_id: str, reason: str) -> dict:
    """Skip one generated check, with a reason that cites the spec line that makes it wrong."""
    known = ids(gate_dir)
    if item_id not in known:
        raise ValueError(f"{item_id} is not a generated example or signature; known ids include "
                         + ", ".join(known[:8]))
    if not reason or not _SPEC_LINE.search(reason):
        raise ValueError("A waiver needs a reason that cites the spec line that makes the example wrong, "
                         "such as 'SPEC line 120 contradicts line 300'.")
    path = Path(gate_dir) / _WAIVED
    try:
        current = json.loads(path.read_text())
    except (OSError, ValueError):
        current = {}
    current[item_id] = reason.strip()
    path.write_text(json.dumps(current, indent=1, sort_keys=True) + "\n")
    return current

def python_for(gate_command: str | None) -> str:
    """The interpreter the project's gate uses: the first python or pytest the gate command runs, past any
    `cd X &&`, `;` and environment settings, else the first python on PATH (the hook's own interpreter lacks the
    project's dependencies; fix check: gates beginning `cd <build> && <venv>/python` fell back to it)."""
    try:
        words = shlex.split(gate_command or "", posix=True)
    except ValueError:
        words = (gate_command or "").split()
    for word in words:
        word = word.rstrip(";")
        name = os.path.basename(word)
        if re.fullmatch(r"python[\d.]*", name) and os.sep in word and os.access(word, os.X_OK):
            return word
        if name in ("pytest", "py.test"):
            found = word if os.sep in word else shutil.which(word)
            if found:
                sibling = os.path.join(os.path.dirname(found), "python")
                if os.access(sibling, os.X_OK):
                    return sibling
    for name in ("python", "python3"):
        found = shutil.which(name)
        if found:
            return found
    return sys.executable


def run(gate_dir: Path, project: Path, python: str | None = None, timeout: int = 300,
        record: Path | None = None, key: str | None = None) -> dict | None:
    """Run the advisory examples file and return what agreed, what disagreed and why. The result is saved to
    `record`, which must lie outside the watched files: a run never changes the checkpoint."""
    gate_dir = Path(gate_dir)
    target = gate_dir / FILE
    if key is not None:
        previous = last(record)
        if previous and previous.get("key") == key:
            return previous  # the same files: the same result
    if not target.is_file():
        return None
    hookguard.refuse("The spec examples")
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.xml"
        try:
            done = subprocess.run([python or sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
                                   "-W", "ignore", f"--junitxml={report}", str(target)],
                                  cwd=str(project), capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return {"passed": 0, "failed": [], "skipped": 0, "error": f"did not finish in {timeout} s"}
        except OSError as exc:
            return {"passed": 0, "failed": [], "skipped": 0, "error": str(exc)}
        try:
            root = ET.parse(report).getroot()
        except (OSError, ET.ParseError):
            why = " ".join(((done.stderr or "") + " " + (done.stdout or "")).split())[-300:]
            return {"passed": 0, "failed": [], "skipped": 0,
                    "error": f"the examples did not run with {python or sys.executable}: {why or 'no output'}"}
    result = {"passed": 0, "failed": [], "skipped": 0,
              "kinds": {"examples": {"passed": 0, "failed": 0, "skipped": 0},
                        "signatures": {"passed": 0, "failed": 0, "skipped": 0}}}
    if key is not None:
        result["key"] = key
    counted = re.search(r"^EXAMPLE_COUNT = (\d+)$", target.read_text(errors="replace"), re.M)
    result["example_count"] = int(counted.group(1)) if counted else None
    for case in root.iter("testcase"):
        name = case.get("name") or ""
        item = name[name.find("[") + 1:-1] if "[" in name else name
        bad = next((c for c in case if c.tag in ("failure", "error")), None)
        if not item.startswith(("ex", "sig_")):
            continue  # pytest's placeholder for an empty list
        tally = result["kinds"]["signatures" if item.startswith("sig_") else "examples"]
        if bad is not None:
            message = " ".join((bad.get("message") or bad.text or "").split())
            result["failed"].append({"id": item, "message": re.sub(r"^Failed:\s*", "", message)[:400]})
            tally["failed"] += 1
        elif any(c.tag == "skipped" for c in case):
            result["skipped"] += 1
            tally["skipped"] += 1
        else:
            result["passed"] += 1
            tally["passed"] += 1
    if record is not None:
        try:
            Path(record).parent.mkdir(parents=True, exist_ok=True)
            Path(record).write_text(json.dumps(result, indent=1) + "\n")
        except OSError:
            pass
    return result


def last(record: Path | None) -> dict | None:
    """The result `run` saved to `record`, if any."""
    if record is None:
        return None
    try:
        return json.loads(Path(record).read_text())
    except (OSError, ValueError):
        return None


def summary_line(result: dict | None, limit: int = 5) -> str:
    """One line for reports and the session-start message: runnable examples and listed signatures apart, so
    signatures that agree never read as agreeing examples (fix check: pytest-cov's spec has none runnable)."""
    if not result:
        return ""
    if result.get("error"):
        return f"Spec examples: {result['error']}."
    failed = result.get("failed") or []
    kinds = result.get("kinds") or {}
    ex, sig = kinds.get("examples") or {}, kinds.get("signatures") or {}
    if not kinds:  # a result recorded before the split
        ex = {"passed": result.get("passed", 0), "failed": len(failed), "skipped": result.get("skipped", 0)}
    if not any(ex.values()) and not any(sig.values()):
        return ""
    if result.get("example_count") == 0 or not any(ex.values()):
        line = "Spec examples: none runnable in this spec"
    elif not ex.get("passed") and not ex.get("failed"):
        line = f"Spec examples: none ran here (all {ex.get('skipped', 0)} skipped: a module or service is missing)"
    else:
        line = f"Spec examples: {ex.get('passed', 0)} agree, {ex.get('failed', 0)} disagree"
    if any(sig.values()):
        line += f"; signatures: {sig.get('passed', 0)} agree, {sig.get('failed', 0)} disagree"
    if not failed:
        return line + "."
    shown = "; ".join(f"{f['id']}: {f['message'][:160]}" for f in failed[:limit])
    more = f"; and {len(failed) - limit} more" if len(failed) > limit else ""
    return (line + f" ({shown}{more}). For each, read the spec's prose and recall the real library: fix the "
            "library when they agree with the example, else `waive-example SLUG ID --reason \"SPEC line N ...\"`.")
