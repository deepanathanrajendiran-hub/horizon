"""A gate of the public names a spec gives for a Python package: one test per name.

A spec build can import every module and still miss the names its users call (NL2Repo structlog: the
top-level `PrintLoggerFactory` was missing, and every official test errored in setup). This reads the
names from the spec, not from the code, so it can be written before the code exists."""
import keyword
import re
from pathlib import Path

_URL = re.compile(r"(?:https?://|www\.)\S+")

# File extensions and domain parts: `pkg.dev.py`, `pkg.readthedocs.io` name files and sites, not the library.
_NOT_REFS = {"org", "com", "net", "edu", "io", "readthedocs", "html", "md", "py", "txt", "toml", "cfg", "rst",
             "ini", "yml", "yaml"}


def _is_test(name: str) -> bool:
    return name in ("test", "tests") or name.startswith("test_")


def _keep(segments: list[str]) -> list[str]:
    """Cut a dotted chain at a keyword or a test name, and after its first class-like name."""
    kept = []
    for seg in segments:
        if keyword.iskeyword(seg) or _is_test(seg):
            break
        kept.append(seg)
        if seg[:1].isupper():
            break
    return kept


def _line_refs(line: str, package: str, pkg: str) -> set[str]:
    found = set()
    for chain in re.findall(rf"(?<![\w./-]){pkg}((?:\.[A-Za-z_]\w*)+)(?![\w/-])", line):
        whole = chain.strip(".").split(".")
        if whole[0] in _NOT_REFS or whole[-1] in _NOT_REFS:
            continue  # a file name or a domain: pkg.dev.py, pkg.readthedocs.io
        segments = _keep(whole)
        if segments:
            found.add(".".join([package, *segments]))
    for module in re.findall(rf"^\s*import\s+({pkg}(?:\.\w+)+)", line):
        segments = _keep(module.split(".")[1:])
        if segments:
            found.add(".".join([package, *segments]))
    return found


def _from_imports(lines: list[str], package: str, pkg: str) -> set[str]:
    """`from pkg.mod import a, b` anywhere in a line: code, prose in backticks, doctests or comments."""
    found = set()
    for i, line in enumerate(lines):
        m = re.search(rf"\bfrom\s+({pkg}(?:\.\w+)*)\s+import\s+(.*)$", line)
        if not m:
            continue
        module, names = m.group(1), m.group(2)
        if names.lstrip().startswith("("):
            names = names.lstrip()[1:]
            j = i + 1
            while ")" not in names and j < len(lines) and not re.match(r"\s*(from|import)\s", lines[j]):
                names += "," + lines[j]
                j += 1
            names = names.split(")", 1)[0]
        # The import ends where its own characters end; in prose a sentence follows it.
        names = re.match(r"[\w\s,\\]*", names).group(0)
        base = _keep(module.split(".")[1:])
        if len(base) != len(module.split(".")) - 1 or (base and base[-1] in _NOT_REFS):
            continue
        items = names.split(",")
        for k, item in enumerate(items):
            words = item.strip().strip("\\").split()
            # `Name` or `Name as alias`; the last item may run on into prose ("JSONRenderer at the top level")
            if not (len(words) == 1 or (len(words) == 3 and words[1] == "as") or (words and k == len(items) - 1)):
                continue
            if not words[0].isidentifier() or keyword.iskeyword(words[0]) or _is_test(words[0]):
                continue
            found.add(".".join([package, *base, words[0]]))
    return found


def _init_all(lines: list[str], package: str) -> set[str]:
    """The names in an `__all__` the spec gives for the package's `__init__.py` (within 30 lines after a
    mention of `__init__`)."""
    found = set()
    for i, line in enumerate(lines):
        if not re.match(r"\s*__all__\s*(?::[^=]*)?=\s*[\[(]", line):
            continue
        if not any("__init__" in near for near in lines[max(0, i - 30):i]):
            continue
        block = line
        j = i + 1
        while not re.search(r"[\])]", block) and j < len(lines):
            block += lines[j]
            j += 1
        for name in re.findall(r"['\"]([A-Za-z_]\w*)['\"]", block):
            if not keyword.iskeyword(name) and not _is_test(name):
                found.add(f"{package}.{name}")
    return found

# A directory-tree line: `│   ├── core.py   # comment`; the branch's column gives the depth.
_TREE_LINE = re.compile(r"^(?P<lead>[│|\s]*)(?:[├└]──|\|--|`--)\s*(?P<name>[^#\s][^#]*?)\s*(?:#.*)?$")
_NOT_PACKAGE_DIRS = {"tests", "test", "docs", "doc", "examples", "example", "benchmarks", "scripts"}
# Top-level files a project's own packaging commonly reads (setup.py's long_description, pyproject's readme and
# license), and the packaging files; any other file they name is checked by test_packaging_references.
_PACKAGING_FILE = re.compile(r"^(?:(?:README|LICEN[CS]E|CHANGELOG|CHANGES|HISTORY)(?:\.\w+)?"
                             r"|pyproject\.toml|setup\.py|setup\.cfg)$", re.I)
_BUILD_FILES = ("pyproject.toml", "setup.py", "setup.cfg")


def _tree_paths(text: str) -> list[tuple[int, str]]:
    """(depth, path) for each entry of the spec's directory trees; depth 0 is directly under the root."""
    out, stack = [], []
    for line in text.splitlines():
        found = _TREE_LINE.match(line)
        if not found:
            continue
        depth = len(found.group("lead")) // 4
        name = found.group("name").strip().rstrip("/")
        stack = stack[:depth] + [name]
        out.append((depth, "/".join(stack)))
    return out


def _module_of(path: str, package: str) -> str | None:
    """`src/pkg/utils/cache.py` -> `pkg.utils.cache`; None for tests, docs, `__main__` and non-modules."""
    if not path.endswith(".py"):
        return None
    parts = path[:-3].split("/")
    if package not in parts[:-1] or any(p in _NOT_PACKAGE_DIRS for p in parts):
        return None
    mods = parts[parts.index(package):]
    if mods[-1] == "__init__":
        mods = mods[:-1]
    if (len(mods) < 2 or "__main__" in mods or any(_is_test(m) or m == "conftest" for m in mods)
            or not all(m.isidentifier() for m in mods)):
        return None
    return ".".join(mods)


def _module_refs(text: str, package: str) -> set[str]:
    paths = [p for _, p in _tree_paths(text)]
    # A single-module package (autopep8.py): `autopep8/...` in prose is the repo folder, not the package.
    if not any(p.split("/")[-1] == f"{package}.py" for p in paths):
        paths += re.findall(rf"(?<![\w/.-])(?:[\w.-]+/)*{re.escape(package)}/(?:\w+/)*\w+\.py\b", text)
    return {m for m in (_module_of(p, package) for p in paths) if m}


def spec_files(text: str) -> list[str]:
    """Top-level files in the spec's directory tree that packaging reads (NL2Repo bench6: boto3's setup.py opens
    README.rst, and a build without it failed to install)."""
    return sorted({p for depth, p in _tree_paths(text) if depth == 0 and _PACKAGING_FILE.match(p)})

def refs(text: str, package: str) -> list[str]:
    pkg = re.escape(package)
    modules = _module_refs(text, package)
    text = _URL.sub(" ", text)
    # Prose in backticks, doctest prompts and comments hold imports too.
    lines = [re.sub(r"^\s*(?:>>>|\.\.\.|#)\s?", "", line).replace("`", " ") for line in text.splitlines()]
    kept = set(modules)
    for i, line in enumerate(lines):
        # `try: x = pkg.missing` / `except AttributeError:` shows a name that must not exist.
        if any("AttributeError" in near for near in lines[i:i + 3]):
            continue
        kept |= _line_refs(line, package, pkg)
    kept |= _from_imports(lines, package, pkg)
    kept |= _init_all(lines, package)
    # A private module's public names are reached through the package (NL2Repo structlog: the spec gave
    # `from structlog._config import reset_defaults`, and callers used `structlog.reset_defaults`).
    for ref in list(kept):
        parts = ref.split(".")
        if (len(parts) == 3 and parts[1].startswith("_") and not parts[1].startswith("__")
                and not parts[2].startswith("_")):
            kept.add(f"{package}.{parts[2]}")
    return sorted(kept)




_TEMPLATE = '''"""Public names {spec} gives for `{package}` (written by Horizon `surface`; one test per name).

A failure means the library lacks the name: add it to the library. Delete a name only when the spec says it
must not exist, or when it is a file name, a URL or a local variable in an example, not a library name."""
import importlib
import re
import sys
import traceback
from pathlib import Path

import pytest

PACKAGE = {package!r}
NAMES = {names}


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
_PROJECT = _ROOT.parent if _ROOT is not None and _ROOT.name == "src" else (_ROOT or Path.cwd())


def _package_error():
    """The package's own import error with the project file and line that raised it, or None. Every name
    check reports it, so one broken import shows as its cause, not as N missing names."""
    try:
        importlib.import_module(PACKAGE)
    except ModuleNotFoundError as exc:
        if exc.name == PACKAGE:
            return None  # no package yet: each name says so
        error = exc
    except Exception as exc:
        error = exc
    else:
        return None
    where = ""
    for frame in reversed(traceback.extract_tb(error.__traceback__)):
        try:
            where = f" ({{Path(frame.filename).resolve().relative_to(_ROOT).as_posix()}}:{{frame.lineno}})"
            break
        except (TypeError, ValueError):
            continue
    return f"{{type(error).__name__}}: {{error}}{{where}}"


_PACKAGE_ERROR = _package_error()


def _deepest_module(parts):
    for i in range(len(parts), 0, -1):
        name = ".".join(parts[:i])
        try:
            return importlib.import_module(name), i
        except ModuleNotFoundError as exc:
            # Only a missing module on this name's own path falls back; a missing dependency is an error.
            if not exc.name or not (name == exc.name or name.startswith(exc.name + ".")):
                raise
    pytest.fail(f"package {{PACKAGE}} does not import")


@pytest.mark.parametrize("name", NAMES, ids=NAMES)
def test_spec_name_exists(name):
    if _PACKAGE_ERROR:
        pytest.fail(f"{{PACKAGE}} does not import: {{_PACKAGE_ERROR}}")
    parts = name.split(".")
    module, depth = _deepest_module(parts)
    for i in range(1, depth + 1):
        prefix = ".".join(parts[:i])
        if getattr(importlib.import_module(prefix), "__file__", None) is None:
            pytest.fail(f"{{prefix}} is a namespace package: add {{prefix.replace('.', '/')}}/__init__.py")
    if depth < len(parts):
        assert hasattr(module, parts[depth]), f"{{'.'.join(parts[:depth])}} has no attribute {{parts[depth]}}"
'''

_FILES_TEMPLATE = '''

FILES = {files}


@pytest.mark.parametrize("path", FILES, ids=FILES)
def test_spec_file_exists(path):
    # The project's own packaging can read these (setup.py opening README.rst); without them it does not install.
    assert (_PROJECT / path).is_file(), f"{{path}} is in the spec's directory tree: create it at {{_PROJECT}}"
'''

_PACKAGING_TEMPLATE = '''

def _packaging_problems():
    """Files and modules the project's own packaging names but that do not exist."""
    problems = []
    pyproject, setup_py, setup_cfg = (_PROJECT / n for n in ("pyproject.toml", "setup.py", "setup.cfg"))
    refs = []
    if pyproject.is_file():
        text = pyproject.read_text(errors="replace")
        refs += [("pyproject.toml", r) for r in re.findall(r'^\\s*(?:readme|license-files)\\s*=\\s*"([^"]+)"', text, re.M)]
        refs += [("pyproject.toml", r) for r in re.findall(r'\\bfile\\s*=\\s*"([^"]+)"', text)]
        scripts = re.search(r"^\\[project\\.(?:scripts|gui-scripts)\\]\\s*$(.*?)(?=^\\[|\\Z)", text, re.M | re.S)
        for name, target in re.findall(r'^\\s*"?([\\w.-]+)"?\\s*=\\s*"([^"]+)"', scripts.group(1) if scripts else "", re.M):
            module = target.split(":")[0].strip()
            base = (_ROOT or _PROJECT) / module.replace(".", "/")
            if not (base.with_suffix(".py").is_file() or (base / "__init__.py").is_file()):
                problems.append(f"script {name} points at {module}, which does not exist")
    if setup_py.is_file():
        text = setup_py.read_text(errors="replace")
        refs += [("setup.py", r) for r in re.findall(r"""(?:open|Path)\\(\\s*['"]([^'"]+)['"]""", text)]
    if setup_cfg.is_file():
        refs += [("setup.cfg", r) for r in re.findall(r"file:\\s*([^\\s,;]+)", setup_cfg.read_text(errors="replace"))]
    for where, ref in refs:
        if not any(_PROJECT.glob(ref)):
            problems.append(f"{where} names {ref}, which does not exist")
    return problems


def test_packaging_references():
    # The project's own packaging must install: a setup.py that opens a missing README.rst fails pip install.
    problems = _packaging_problems()
    assert not problems, "; ".join(problems)
'''

def write_gate(spec: Path, package: str, out: Path) -> int:
    text = Path(spec).read_text(errors="replace")
    names = refs(text, package)
    if not names:
        raise ValueError(f"no names for package {package!r} in {spec}: check --package (the import name, "
                         "such as jwt for PyJWT)")
    body = "[\n" + "".join(f"    {n!r},\n" for n in names) + "]"
    gate = _TEMPLATE.format(spec=Path(spec).name, package=package, names=body)
    files = spec_files(text)
    if files:
        gate += _FILES_TEMPLATE.format(files=repr(files))
    if any(f in _BUILD_FILES for f in files):
        gate += _PACKAGING_TEMPLATE
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(gate)
    return len(names)
