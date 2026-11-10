"""A gate of the public names a spec gives for a Python package: one test per name.

A spec build can import every module and still miss the names its users call (NL2Repo structlog: the
top-level `PrintLoggerFactory` was missing, and every official test errored in setup). This reads the
names from the spec, not from the code, so it can be written before the code exists.

exp/surface-check (2026-11-06): `start` writes the gate itself whenever the request names a spec for a Python package,
and `close` runs it with the project's own interpreter, whatever the gate command runs. E2E-SWE spectester: the
hidden tests of dnachisel and mapillary_tools stopped at collection in all 10 sessions on a module or name the build
lacked (mapillary_tools: three modules its spec names), and no build had passed `start --surface-spec`."""
import collections
import json
import keyword
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
import hookguard

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
    """The names in an `__all__` the spec gives: a submodule's when the nearest heading above names one in code
    (`## Admin API (`pkg.admin`)`), else the package's own when `__init__` (not a constructor `__init__(`) is
    mentioned within 30 lines before it. E2E-SWE keepworking, kafka-python: an `__all__` under a `kafka.admin`
    heading, after a constructor's `__init__`, was read as the top-level package's."""
    found = set()
    owner_of_section, fence = None, None
    pkg = re.escape(package)
    for i, line in enumerate(lines):
        opened = re.match(r"^\s{0,3}(`{3,}|~{3,})", line)
        if opened:
            fence = None if fence and opened.group(1)[0] == fence[0] else (fence or opened.group(1))
            continue
        heading = None if fence else re.match(r"^\s{0,3}#{1,6}\s+(.*)$", line)
        if heading:
            named = re.findall(rf"`({pkg}(?:\.[A-Za-z_]\w*)*)`", heading.group(1))
            owner_of_section = named[-1].removesuffix(".__init__") if named else None
            continue
        if not re.match(r"\s*__all__\s*(?::[^=]*)?=\s*[\[(]", line):
            continue
        if owner_of_section and owner_of_section != package:
            owner = owner_of_section
        elif owner_of_section == package or any(re.search(r"__init__(?!\s*\()", near)
                                                 for near in lines[max(0, i - 30):i]):
            owner = package
        else:
            continue
        block = line
        j = i + 1
        while not re.search(r"[\])]", block) and j < len(lines):
            block += lines[j]
            j += 1
        for name in re.findall(r"['\"]([A-Za-z_]\w*)['\"]", block):
            if not keyword.iskeyword(name) and not _is_test(name):
                found.add(f"{owner}.{name}")
    return found

# A directory-tree line: `│   ├── core.py   # comment`; the branch's column gives the depth.
_QUANTIFIER = re.compile(r"\b(?:all|every|everything)\b", re.I)
_EXPORTED = re.compile(r"\b(?:importable|imported|re-?exported|exported|exposed)\b", re.I)
_TOP_LEVEL = re.compile(r"\btop[- ]level\b", re.I)
_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$")
_FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
# A list entry that opens with code: `name(args)` or `Name`, bold or not.
_ENTRY = re.compile(r"^\s{0,3}[-*+]\s+(?:\*\*|__)?`([A-Za-z_]\w*)\s*(\(|`)")
_CLASS_HEADING = re.compile(r"^(?:class\s+)?`?([A-Z]\w*)`?(?:\s*\(.*\))?$")
_BUILTINS = set(dir(__import__("builtins")))
_MEMBERS = re.compile(r"\b(?:methods?|attributes?|propert(?:y|ies)|parameters?|arguments?|options?|fields?)\b", re.I)


def _documented(name: str) -> bool:
    return not (name.startswith("_") or keyword.iskeyword(name) or _is_test(name) or name in _BUILTINS)


def _blanket_owner(line: str, package: str) -> str | None:
    """The module a sentence says every name comes from: "All public classes and functions below are importable
    from the top-level `pkg` package", "All importable from `pkg.exceptions`."; None for any other line."""
    if not (_QUANTIFIER.search(line) and _EXPORTED.search(line)) or re.search(r"\bsubmodules?\b", line, re.I):
        return None
    pkg = re.escape(package)
    named = re.search(rf"\b(?:from|off|of|in|at)\s+(?:the\s+)?(?:top[- ]level\s+)?`({pkg}(?:\.[A-Za-z_]\w*)*)`", line)
    if named:
        return named.group(1).removesuffix(".__init__")
    if _TOP_LEVEL.search(line) and not re.search(r"`[A-Za-z_][\w.]*`\s+package", line.replace(f"`{package}`", "")):
        return package
    return None


def _entries(lines: list[str], package: str) -> list[tuple[int, str, str | None]]:
    """(line, name, module it says it is importable from) for each name the lines document: a list entry that opens
    with `name(...)` or `Name`, a heading that is one class-like name, and code that opens a heading or is
    class-like in it. An entry under a class's heading is that class's method, not a name of the package."""
    out, fence, in_class, of_members = [], None, False, False
    pkg = re.escape(package)
    # "Exceptions" or "Helpers" alone in a heading is a title; "Location" is a class when the spec also calls it.
    called = set(re.findall(r"\b([A-Z]\w*)\(", "\n".join(lines)))
    for i, line in enumerate(lines):
        opened = _FENCE.match(line)
        if opened:
            fence = None if fence and opened.group(1)[0] == fence[0] else (fence or opened.group(1))
            continue
        if fence:
            continue
        heading = _HEADING.match(line)
        if heading:
            title = heading.group(2).strip()
            bare = _CLASS_HEADING.match(re.split(r"\s+[—–-]\s+", title)[0].strip())
            if bare and "`" not in bare.group(0) and not (re.search(r"[a-z][A-Z]", bare.group(1))
                                                          or bare.group(1) in called):
                bare = None
            in_class = bool(bare)
            names = [bare.group(1)] if bare else []
            first = re.match(r"`([A-Za-z_]\w*)`", title)
            if first:
                names.append(first.group(1))
                in_class = in_class or first.group(1)[:1].isupper()
            names += [n for n in re.findall(r"`([A-Z]\w*)`", title)]
            out += [(i, n, None) for n in dict.fromkeys(names)]
            of_members = False
            continue
        entry = _ENTRY.match(line)
        if not entry:
            if line.strip() and not re.match(r"^\s*(?:[-*+]|\d+[.)])\s", line) and not line.startswith((" ", "\t")):
                # the sentence that introduces a list says what it lists ("Base class methods available on all:")
                of_members = bool(_MEMBERS.search(line)) and line.rstrip().endswith(":")
            continue
        if of_members or (entry.group(2) == "`" and not entry.group(1)[:1].isupper()):
            continue  # a bare lower-case name in a list is a parameter or an attribute
        if in_class and not entry.group(1)[:1].isupper():
            continue
        elsewhere = re.search(rf"\bimportable\s+from\s+`({pkg}(?:\.[A-Za-z_]\w*)+)`", line, re.I)
        out.append((i, entry.group(1), elsewhere.group(1) if elsewhere else None))
    return out


def _blanket_exports(lines: list[str], package: str) -> set[str]:
    """Names a spec documents without an import line, when it says in one sentence where all of them import from.

    The sentence covers its own section when that section documents names (a list of exceptions followed by "All
    importable from `pkg`."); otherwise everything after it (an "Imports" section at the top of the spec).

    E2E-SWE thin2: dnachisel and mpmath scored 0 on an ImportError at collection. Each spec says once that
    everything below is importable from the top-level package and then lists its functions in bullets; the surface
    gate, reading import lines only, held 4 and 10 names."""
    entries = _entries(lines, package)
    levels = [(i, len(m.group(1))) for i, m in ((i, _HEADING.match(line)) for i, line in enumerate(lines)) if m]
    found, fence = set(), None
    for i, line in enumerate(lines):
        opened = _FENCE.match(line)
        if opened:
            fence = None if fence and opened.group(1)[0] == fence[0] else (fence or opened.group(1))
            continue
        if fence or _HEADING.match(line):
            continue
        # a sentence may wrap: read it with the next line
        owner = _blanket_owner(line + " " + (lines[i + 1] if i + 1 < len(lines) else ""), package) \
            if _QUANTIFIER.search(line) else None
        if not owner:
            continue
        above = [(at, level) for at, level in levels if at < i]
        start, level = above[-1] if above else (0, 0)
        end = next((at for at, lv in levels if at > i and lv <= level), len(lines))
        inside = [e for e in entries if start < e[0] < end]
        covered = inside if inside else [e for e in entries if e[0] > i]
        for _, name, elsewhere in covered:
            if _documented(name) and name != package:
                found.add(f"{elsewhere or owner}.{name}")
    return found



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


# "Rewritings (`rewritings.py`)": a section titled after the module file it documents.
_FILE_IN_TITLE = re.compile(r"\(`((?:[A-Za-z_][\w-]*/)*[A-Za-z_]\w*\.py)`\)")
# "### `generic.py`": a heading that is only a file name.
_FILE_ONLY = re.compile(r"^`((?:[A-Za-z_][\w-]*/)*[A-Za-z_]\w*\.py)`$")
# "Walker Infrastructure (`walkers/`)": a heading that names a folder.
_DIR_IN_TITLE = re.compile(r"`((?:[A-Za-z_][\w-]*/)+)`")
# Folders that hold a project's scripts, tests and docs, not its package.
_SCRIPT_DIRS = {"tools", "bin", "script", "scripts", "tests", "test", "docs", "doc", "examples", "example",
                "benchmarks"}


def _heading_modules(lines: list[str], package: str) -> set[str]:
    """Modules a spec documents section by section, from its headings: "Rewritings (`rewritings.py`)" is the
    package's `rewritings` module, and a heading that is only `generic.py` under "Walkers (`walkers/`)" is
    `walkers.generic`. Not modules: a file in a folder of scripts, tests or docs (`tools/pdf2txt.py`), a packaging
    or test file, a single-module package's own file, and a heading that is only a file name with no folder heading
    above it (a spec may head a section with a file the program writes, such as a generated client's `types.py`).

    E2E-SWE thin2: pysmt's build had no `pysmt.parsing` or `pysmt.rewritings`, and 8 hidden tests failed on
    ModuleNotFoundError; its spec names each module only in its section's heading ("## 19. HR Parsing
    (`parsing.py`)"), so the surface gate did not ask for them."""
    # A single-module package (`tool.py` in the spec's tree) has no modules: `tool/x.py` is then the repo folder.
    if any(p.split("/")[-1] == f"{package}.py" for _, p in _tree_paths("\n".join(lines))):
        return set()
    found, fence, folders = set(), None, {}
    for line in lines:
        opened = _FENCE.match(line)
        if opened:
            fence = None if fence and opened.group(1)[0] == fence[0] else (fence or opened.group(1))
            continue
        if fence:
            continue
        heading = _HEADING.match(line)
        if not heading:
            continue
        level, title = len(heading.group(1)), heading.group(2).strip()
        folders = {lv: d for lv, d in folders.items() if lv < level}
        named = _FILE_IN_TITLE.search(title)
        path = named.group(1) if named else None
        if path is None:
            only = _FILE_ONLY.match(title)
            if only and folders:
                path = f"{folders[max(folders)]}/{only.group(1)}"
            folder = _DIR_IN_TITLE.search(title)
            if folder and not only:
                folders[level] = folder.group(1).rstrip("/")
        if not path:
            continue
        parts = path.split("/")
        if parts[0] != package:
            if (parts[0] in _SCRIPT_DIRS or _PACKAGING_FILE.match(parts[-1])
                    or (len(parts) == 1 and parts[0] == f"{package}.py")):
                continue
            path = f"{package}/{path}"
        module = _module_of(path, package)
        if module:
            found.add(module)
    return found


def spec_files(text: str) -> list[str]:
    """Top-level files in the spec's directory tree that packaging reads (NL2Repo bench6: boto3's setup.py opens
    README.rst, and a build without it failed to install)."""
    return sorted({p for depth, p in _tree_paths(text) if depth == 0 and _PACKAGING_FILE.match(p)})

def refs(text: str, package: str) -> list[str]:
    text = _unalias(text, package)
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
    kept |= _init_all(text.splitlines(), package)  # with its backticks: headings name the module in code
    kept |= _blanket_exports(text.splitlines(), package)
    kept |= _heading_modules(text.splitlines(), package)
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

FILE = "test_surface.py"
RUN_TIMEOUT_S = 120
# A name whose dotted references all end in one of these is a file (setup.sh, pyproject.toml), not a package.
_FILE_SUFFIXES = _NOT_REFS | {"sh", "bat", "ps1", "lock", "json", "csv", "xml", "gz", "zip", "in", "log", "lst"}
_NOT_PACKAGES = set(getattr(sys, "stdlib_module_names", ())) | {"pytest", "__future__", "self", "cls"}
_IMPORT_LINE = re.compile(r"^\s*(?:>>>\s*)?(?:from\s+([A-Za-z_]\w*)[\w.]*\s+import\b|import\s+([A-Za-z_]\w*))", re.M)
_DOTTED = re.compile(r"(?<![\w.])([A-Za-z_]\w*)\.[A-Za-z_]\w*")


_ALIAS_LINE = re.compile(r"^\s*(?:>>>\s*)?import\s+([A-Za-z_][\w.]*)\s+as\s+([A-Za-z_]\w*)", re.M)
_FROM_NAMES = re.compile(r"^\s*(?:>>>\s*)?from\s+[\w.]+\s+import\s+\(?([^)\n#]+)", re.M)


def _aliases(text: str) -> tuple[dict, set]:
    """`import fiddle as fdl` makes `fdl` a second name for `fiddle` ({alias: module}); `from mpmath import mp` makes
    `mp` a name taken from a module, not a package (E2E-SWE fiddle and mpmath: the surface gate was written for `fdl`
    and `mp`, which no build has)."""
    aliases = {alias: module for module, alias in _ALIAS_LINE.findall(text) if alias != module.split(".")[0]}
    bound = set()
    for names in _FROM_NAMES.findall(text):
        for part in names.split(","):
            m = re.match(r"\s*([A-Za-z_]\w*)(?:\s+as\s+([A-Za-z_]\w*))?\s*$", part)
            if m:
                bound.add(m.group(2) or m.group(1))
    return aliases, bound


def _unalias(text: str, package: str) -> str:
    """The spec's text with each alias of the package written out (`fdl.Config` becomes `fiddle.Config`)."""
    for alias, module in _aliases(text)[0].items():
        if module.split(".")[0] == package:
            text = re.sub(rf"(?<![\w.]){re.escape(alias)}\.", module + ".", text)
    return text

def guess_package(text: str) -> str | None:
    """The import name a spec builds: the name it imports from most, and whose dotted names it gives most in code
    spans, the title counting extra; never a standard module, never a name whose dotted references are all files
    (`setup.sh`), and never an alias or a name taken from a module: an alias's spans count for its module. None when
    nothing qualifies (a prose request for no Python package)."""
    counts, imported = collections.Counter(), set()
    aliases, bound = _aliases(text)
    for a, b in _IMPORT_LINE.findall(text):
        counts[a or b] += 3
        imported.add(a or b)
    lines = text.splitlines()
    title = next((line for line in lines if line.strip()), "")
    for span in re.findall(r"`([^`\n]+)`", text):
        for first in _DOTTED.findall(span):
            if first in aliases:
                counts[aliases[first].split(".")[0]] += 1
            elif first not in bound or first in imported:
                counts[first] += 1
    for span in re.findall(r"`([A-Za-z_]\w*)`", title):
        if span in counts:
            counts[span] += 5
    for name, _ in counts.most_common():
        if name in _NOT_PACKAGES or (name[:1].isupper() and name not in imported):
            continue
        if name in aliases and name not in imported:
            continue
        if [r for r in refs(text, name) if r.split(".")[-1] not in _FILE_SUFFIXES]:
            return name
    return None


def find_spec(gate_path, root) -> tuple[Path, str] | None:
    """The goal's spec and its package: the longest of request.md and the Markdown files it names that gives a
    Python package's names."""
    import sections
    found = sections._goal_inputs(str(gate_path) if gate_path else None, str(root))
    for path, text in sorted(found, key=lambda pair: -len(pair[1].splitlines())):
        package = guess_package(text)
        if package:
            return Path(path), package
    return None


def _named_python(text: str) -> str | None:
    """The first absolute python (or the python beside an absolute pytest) a command or script names."""
    for line in text.splitlines():
        try:
            words = shlex.split(line, comments=True, posix=True)
        except ValueError:
            words = line.split()
        for word in words:
            word = word.rstrip(";&|)")
            if "=" in word and not word.startswith(os.sep):
                word = word.split("=", 1)[1]
            name = os.path.basename(word)
            if not word.startswith(os.sep):
                continue
            if re.fullmatch(r"python[\d.]*", name) and os.access(word, os.X_OK):
                return word
            if name in ("pytest", "py.test"):
                sibling = os.path.join(os.path.dirname(word), "python")
                if os.access(sibling, os.X_OK):
                    return sibling
    return None

def _relative_python(words: list[str], root: Path) -> str | None:
    """The first python (or the python beside a pytest) the command's words name without an absolute path: a bare
    name is looked up on PATH, a relative path from the project, then from each directory a `cd` in the command
    enters."""
    bases, previous = [root], ""
    for word in words:
        if previous == "cd":
            bases.insert(0, Path(word) if os.path.isabs(word) else root / word)
        previous = word
    for word in words:
        word = word.rstrip(";&|)")
        if "=" in word:
            word = word.split("=", 1)[1]
        name = os.path.basename(word)
        is_python = bool(re.fullmatch(r"python[\d.]*", name))
        if not word or os.path.isabs(word) or not (is_python or name in ("pytest", "py.test")):
            continue
        if os.sep in word:
            found = next((str(base / word) for base in bases if os.access(base / word, os.X_OK)), None)
        else:
            found = shutil.which(word)
        if not found:
            continue
        if is_python:
            return os.path.abspath(found)
        sibling = os.path.join(os.path.dirname(os.path.abspath(found)), "python")
        if os.access(sibling, os.X_OK):
            return sibling
    return None


def interpreter(state: dict) -> str | None:
    """The project's own Python: the one the gate command names, else one a script the command runs names (wherever
    the script is, Horizon's gates root included). An absolute path wins; without one, a python or pytest the
    command names by a bare name or a relative path is the one the gate itself runs, so it counts too (E2E-SWE full
    and retry runs: 9 of 161 goals named no absolute Python, and the surface gate and install check only advised).
    None when the command names no Python at all: another Python may lack the project's dependencies, so its result
    cannot block."""
    if (state.get("gate") or {}).get("kind") != "command":
        return None
    command = str(state["gate"]["value"])
    found = _named_python(command)
    if found:
        return found
    try:
        words = shlex.split(command, posix=True)
    except ValueError:
        words = command.split()
    root = Path(state["cwd"])
    for word in words:
        path = Path(word.rstrip(";&|)")) if os.path.isabs(word) else root / word.rstrip(";&|)")
        try:
            if path.is_file() and path.stat().st_size < 200_000:
                found = _named_python(path.read_text(errors="replace"))
                if found:
                    return found
        except (OSError, ValueError):
            continue
    return _relative_python(words, root)


def _last(record: Path | None) -> dict | None:
    try:
        return json.loads(Path(record).read_text()) if record else None
    except (OSError, ValueError):
        return None


def run(gate_dir: Path, project: Path, python: str, timeout: int = RUN_TIMEOUT_S, record: Path | None = None,
        key: str | None = None) -> dict | None:
    """Run the surface gate and return {"passed", "failed": [{"id", "message"}], "error"}; None without one. The
    result is saved to `record` (outside the watched files) and reused while `key`, the files' hash, is unchanged."""
    target = Path(gate_dir) / FILE
    if not target.is_file():
        return None
    previous = _last(record)
    if key is not None and previous and previous.get("key") == key and previous.get("python") == python:
        return previous
    hookguard.refuse("The surface gate")
    result = {"passed": 0, "failed": [], "error": None, "key": key, "python": python}
    root = None
    with tempfile.TemporaryDirectory() as tmp:
        report = Path(tmp) / "report.xml"
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        output = ""
        try:
            done = subprocess.run([python, "-m", "pytest", "-q", "-p", "no:cacheprovider", "-W", "ignore",
                                   f"--junitxml={report}", str(target)], cwd=str(project), env=env,
                                  capture_output=True, text=True, timeout=timeout)
            output = (done.stderr or "") + " " + (done.stdout or "")
            root = ET.parse(report).getroot()
        except subprocess.TimeoutExpired:
            result["error"] = f"did not finish in {timeout} s"
        except (OSError, ET.ParseError):
            result["error"] = f"did not run with {python}: {' '.join(output.split())[-300:] or 'no output'}"
    for case in (root.iter("testcase") if root is not None else ()):
        name = case.get("name") or ""
        item = name[name.find("[") + 1:-1] if "[" in name else name
        bad = next((c for c in case if c.tag in ("failure", "error")), None)
        if bad is not None:
            text = " ".join((bad.get("message") or bad.text or "").split())
            result["failed"].append({"id": item, "message": re.sub(r"^(?:Failed|AssertionError):\s*", "", text)[:300]})
        elif not any(c.tag == "skipped" for c in case):
            result["passed"] += 1
    if record is not None:
        try:
            Path(record).parent.mkdir(parents=True, exist_ok=True)
            Path(record).write_text(json.dumps(result, indent=1) + "\n")
        except OSError:
            pass
    return result


def record_path(slug: str) -> Path:
    import paths
    return paths.harness_root() / "notes" / f"surface-{slug}.json"  # bookkeeping: never hashed


def check(state: dict, key: str | None) -> dict | None:
    """Run the goal's surface gate, if its gate directory has one. `blocking` is whether the result may stop
    `close`: only with the project's own interpreter, and only when the file ran."""
    gate_path = state.get("gate_path")
    if not gate_path or not (Path(gate_path) / FILE).is_file():
        return None
    python = interpreter(state)
    result = run(Path(gate_path), Path(state["cwd"]), python or sys.executable, record=record_path(state["slug"]),
                 key=key)
    if result is None:
        return None
    return {**result, "blocking": python is not None and not result.get("error"),
            "file": str(Path(gate_path) / FILE)}


def listing(failed: list[dict], limit: int = 12) -> str:
    shown = "; ".join(f"{f['id']} ({f['message'][:120]})" if f.get("message") else f["id"] for f in failed[:limit])
    return shown + (f"; and {len(failed) - limit} more" if len(failed) > limit else "")


def message(found: dict) -> str:
    return ("Names and modules the request gives that do not import: " + listing(found["failed"]) + ". A test "
            "that imports a missing one fails, whatever else works. Build each one; if the request does not mean "
            f"one (a file name, an example's variable), delete it from {found['file']} and say why with "
            "`note --finding`.")
