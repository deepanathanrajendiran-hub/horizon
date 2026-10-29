"""A gate of the public names a spec gives for a Python package: one test per name.

A spec build can import every module and still miss the names its users call (NL2Repo structlog: the
top-level `PrintLoggerFactory` was missing, and every official test errored in setup). This reads the
names from the spec, not from the code, so it can be written before the code exists."""
import keyword
import re
from pathlib import Path

_URL = re.compile(r"(?:https?://|www\.)\S+")
_NOT_REFS = {"org", "com", "net", "edu", "html", "md", "py", "txt", "toml", "cfg", "rst"}


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
        segments = _keep(chain.strip(".").split("."))
        if segments and segments[0] not in _NOT_REFS:
            found.add(".".join([package, *segments]))
    for module in re.findall(rf"^\s*import\s+({pkg}(?:\.\w+)+)", line):
        segments = _keep(module.split(".")[1:])
        if segments:
            found.add(".".join([package, *segments]))
    return found


def _from_imports(text: str, package: str, pkg: str) -> set[str]:
    found = set()
    lines = text.splitlines()
    for i, line in enumerate(lines):
        m = re.match(rf"\s*from\s+({pkg}(?:\.\w+)*)\s+import\s+(.*)$", line)
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
        base = _keep(module.split(".")[1:])
        if len(base) != len(module.split(".")) - 1:
            continue
        for item in names.split(","):
            words = item.strip().split()
            if not words or not words[0].isidentifier() or keyword.iskeyword(words[0]):
                continue
            if _is_test(words[0]):
                continue
            found.add(".".join([package, *base, words[0]]))
    return found


def refs(text: str, package: str) -> list[str]:
    """Every `package.x[.Y]` the spec names, sorted. A name on a line at most two lines above a mention
    of AttributeError is an example of a name that must not exist, so it is left out."""
    pkg = re.escape(package)
    text = _URL.sub(" ", text)
    lines = text.splitlines()
    kept = set()
    for i, line in enumerate(lines):
        # `try: x = pkg.missing` / `except AttributeError:` shows a name that must not exist.
        if any("AttributeError" in near for near in lines[i:i + 3]):
            continue
        kept |= _line_refs(line, package, pkg)
    kept |= _from_imports(text, package, pkg)
    return sorted(kept)


_TEMPLATE = '''"""Public names {spec} gives for `{package}` (written by Horizon `surface`; one test per name).

Delete a name only when the spec says it must not exist. A failure means the library lacks the name:
add it to the library."""
import importlib
import sys
from pathlib import Path

import pytest

PACKAGE = {package!r}
NAMES = {names}

for _base in (Path.cwd(), Path.cwd() / "src"):
    if (_base / PACKAGE).is_dir():
        sys.path.insert(0, str(_base))
        break


def _deepest_module(parts):
    for i in range(len(parts), 0, -1):
        name = ".".join(parts[:i])
        try:
            return importlib.import_module(name), i
        except ModuleNotFoundError as exc:
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
    body = "[\n" + "".join(f"    {n!r},\n" for n in names) + "]"
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_TEMPLATE.format(spec=Path(spec).name, package=package, names=body))
    return len(names)
