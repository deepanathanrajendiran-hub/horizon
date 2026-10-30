"""NL2Repo bench6 surface-gate gaps (fun repo: docs/evaluations/nl2repo-bench6/report.md).

- ydata-profiling (both arms 0/2182) and plain Rich-Click (0/145): the official test setup imports a submodule
  the spec's directory tree and file paths name, and the build never wrote it, so every test aborted.
- Horizon boto (0/1014) and both jinja builds (0/911): the task's own packaging reads a README the spec's
  directory tree lists, the build had none, and `pip install` failed before any test ran."""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

import surface  # noqa: E402

TREE = """\
## Project Directory Structure

```Plain
workspace/
├── .github/
│   └── workflows/
│       └── ci.yml
├── README.rst                # project description
├── LICENSE
├── setup.py
├── docs/
│   └── index.rst
├── src/
│   └── pkg/
│       ├── __init__.py
│       ├── __main__.py
│       ├── core.py
│       ├── _compat.py
│       └── utils/
│           ├── __init__.py
│           └── cache.py
└── tests/
    ├── conftest.py
    └── test_core.py
```

The loader lives in `src/pkg/io/loader.py`, and pkg.core.run is the entry point.
"""


def test_refs_take_every_module_the_directory_tree_and_file_paths_name():
    refs = surface.refs(TREE, "pkg")
    for want in ("pkg.core", "pkg._compat", "pkg.utils", "pkg.utils.cache", "pkg.io.loader", "pkg.core.run"):
        assert want in refs, want
    assert "pkg.__main__" not in refs  # importing it would run the command line
    assert not any("test" in r or "conftest" in r for r in refs)


def test_spec_files_are_the_top_level_files_packaging_reads():
    assert surface.spec_files(TREE) == ["LICENSE", "README.rst"]
    assert surface.spec_files("No tree here; see README.md.\n") == []


def _gate(tmp_path, spec_text):
    spec = tmp_path / "SPEC.md"
    spec.write_text(spec_text)
    gate = tmp_path / ".claude" / "gates" / "test_surface.py"
    gate.parent.mkdir(parents=True)
    surface.write_gate(spec, "pkg", gate)
    return gate


def _run(root, gate):
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(gate)],
                          cwd=root, capture_output=True, text=True, timeout=120)


def _write(root, files):
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def test_a_generated_gate_fails_on_a_missing_tree_module_and_a_missing_readme(tmp_path):
    gate = _gate(tmp_path, TREE)
    _write(tmp_path, {"src/pkg/__init__.py": "", "src/pkg/__main__.py": "raise SystemExit(2)\n",
                      "src/pkg/core.py": "def run():\n    pass\n", "src/pkg/_compat.py": "",
                      "src/pkg/utils/__init__.py": "", "src/pkg/io/__init__.py": "",
                      "src/pkg/io/loader.py": "", "LICENSE": "MIT\n"})
    failed = _run(tmp_path, gate)
    assert failed.returncode == 1
    assert "cache" in failed.stdout and "README.rst" in failed.stdout
    _write(tmp_path, {"src/pkg/utils/cache.py": "", "README.rst": "pkg\n"})
    passed = _run(tmp_path, gate)
    assert passed.returncode == 0, passed.stdout


def test_a_spec_without_a_tree_writes_no_file_check(tmp_path):
    gate = _gate(tmp_path, "Use pkg.dev.ConsoleRenderer.\n")
    assert "test_spec_file_exists" not in gate.read_text()
    _write(tmp_path, {"pkg/__init__.py": "", "pkg/dev.py": "class ConsoleRenderer:\n    pass\n"})
    assert _run(tmp_path, gate).returncode == 0

def test_a_single_module_package_takes_no_modules_from_paths_under_the_repo_folder():
    # autopep8's spec: the package is `autopep8.py`, and prose names the repo's `autopep8/update_readme.py`.
    spec = ("```Plain\nworkspace/\n├── README.rst\n├── autopep8.py\n└── update_readme.py\n```\n\n"
            "#### `split_readme` Function (`autopep8/update_readme.py`)\nUse autopep8.fix_code.\n")
    refs = surface.refs(spec, "autopep8")
    assert "autopep8.update_readme" not in refs and "autopep8.fix_code" in refs
