"""A "by design" note covers the functions it names, and `close` completes on its first passing call.

E2E-SWE thin4 runs (2026-11-10, exp/thin a461068): mapillary_tools recorded its last two unbuilt functions as "by
design" and closed. A note that named only the file would have waved through every function in it, so the note now
has to say which function is meant.

The request check that 2446c64 added at `close` (the first passing `close` listed the request's parts and asked for
the work to be run against each) is gone. It fired in 26 of 30 builds over four runs (thin5 to thin8) and no build
shows a gap it found: in the thin8 run sapp closed at 18 of 26 hidden tests and claripy at 41 of 60 after passing
it ("every output matched the spec"). A session that reads its work against its own reading of the request misses
what that reading missed, and the check cost about a session a build."""
import subprocess

import pytest

from installable_project import installable
from test_e2eswe_fixes import project  # noqa: F401  (the fixture is used by name)
from test_thin import SLUG, _build, _context, _thin

import stubs


def test_the_first_passing_close_completes_the_goal(project):
    p, cli = project
    cli.new_session("s1")
    _thin(p, cli)
    _build(p)
    installable(p)
    code, out = cli("close", SLUG)
    assert code == 0 and out["complete"] and not out["missing"], out
    assert "against the request" not in _context(cli.new_session("s2"))


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "work"
    (root / "pkg").mkdir(parents=True)
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    (root / "pkg" / "upload.py").write_text(
        "class Service:\n    def fetch_offset(self):\n        raise NotImplementedError\n\n"
        "    def upload_chunks(self):\n        raise NotImplementedError\n\n"
        "def on_progress(done):\n    raise NotImplementedError\n")
    return root


def _names(root, facts=()):
    return {name: hits for name, hits in stubs.find(root, facts)}


def test_a_note_that_names_only_the_file_covers_nothing(repo):
    found = _names(repo, ["pkg/upload.py raises NotImplementedError by design"])
    assert found == {"pkg/upload.py": ["on_progress", "Service.fetch_offset", "Service.upload_chunks"]}, found


def test_a_note_covers_the_function_it_names_and_no_other(repo):
    found = _names(repo, ["pkg/upload.py on_progress raises NotImplementedError by design"])
    assert found == {"pkg/upload.py": ["Service.fetch_offset", "Service.upload_chunks"]}, found


def test_a_method_is_named_with_or_without_its_class(repo):
    facts = ["pkg/upload.py Service.fetch_offset raises NotImplementedError by design",
             "pkg/upload.py upload_chunks() and on_progress raise NotImplementedError by design"]
    assert _names(repo, facts) == {}


def test_a_note_about_another_file_covers_nothing_here(repo):
    found = _names(repo, ["pkg/other.py on_progress raises NotImplementedError by design"])
    assert "on_progress" in found["pkg/upload.py"], found


def test_the_file_name_is_not_read_as_a_function_name(repo):
    (repo / "pkg" / "upload.py").write_text("def upload():\n    raise NotImplementedError\n")
    assert _names(repo, ["pkg/upload.py raises NotImplementedError by design"]) == {"pkg/upload.py": ["upload"]}
