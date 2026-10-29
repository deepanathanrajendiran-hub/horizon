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

def refs(text: str, package: str) -> list[str]:
    pkg = re.escape(package)
    text = _URL.sub(" ", text)
    # Prose in backticks, doctest prompts and comments hold imports too.
    lines = [re.sub(r"^\s*(?:>>>|\.\.\.|#)\s?", "", line).replace("`", " ") for line in text.splitlines()]
    kept = set()
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
import sys
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
    parts = name.split(".")
    module, depth = _deepest_module(parts)
    for i in range(1, depth + 1):
        prefix = ".".join(parts[:i])
        if getattr(importlib.import_module(prefix), "__file__", None) is None:
            pytest.fail(f"{{prefix}} is a namespace package: add {{prefix.replace('.', '/')}}/__init__.py")
    if depth < len(parts):
        assert hasattr(module, parts[depth]), f"{{'.'.join(parts[:depth])}} has no attribute {{parts[depth]}}"
'''

def write_gate(spec: Path, package: str, out: Path) -> int:
    names = refs(Path(spec).read_text(errors="replace"), package)
    if not names:
        raise ValueError(f"no names for package {package!r} in {spec}: check --package (the import name, "
                         "such as jwt for PyJWT)")
    body = "[\n" + "".join(f"    {n!r},\n" for n in names) + "]"
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_TEMPLATE.format(spec=Path(spec).name, package=package, names=body))
    return len(names)
