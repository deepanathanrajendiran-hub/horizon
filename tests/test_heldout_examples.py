"""The spec-example gate (NL2Repo held-out review, fun docs/evaluations/nl2repo-heldout/transcripts/review.md).

Horizon's gates checked names and the agent's own guesses, never the spec's literal examples. The losing
behaviours were visible in those examples: databases' force_rollback, python-jose's `except JWTError`,
pytest-cov's `fnmatch_lines` heading, mechanicalsoup's `choose_submit('preview')`. In 4 pairs a failing
check the agent wrote from the spec was edited to match the code. This gate is generated from the spec,
written before the baseline, and protected: a wrong example is waived with a reason, never rewritten."""
import json
import re
import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))

import examples  # noqa: E402


def _spec(*blocks: str, lang: str = "python") -> str:
    return "# Spec\n\nSome prose.\n\n" + "\n".join(f"```{lang}\n{textwrap.dedent(b).strip()}\n```\n\nMore prose.\n"
                                                 for b in blocks)


def test_a_print_with_a_same_line_output_comment_is_an_expectation():
    found, _ = examples.extract(_spec("""
        from pkg import double
        print(double(2))  # Output: 4
    """), "pkg")
    assert len(found) == 1
    assert list(found[0]["expect"].values()) == ["4"]


def test_an_output_comment_on_the_next_line_belongs_to_the_statement_above():
    found, _ = examples.extract(_spec("""
        from pkg import double
        value = double(2)
        print(value)
        # Output: 4
    """), "pkg")
    assert found[0]["expect"] == {3: "4"}


def test_a_bare_literal_comment_counts_but_prose_does_not():
    found, _ = examples.extract(_spec("""
        from pkg import double
        print(double(2))      # 4
        print(double(3))      # current value
        print(double(4))      # ('a', 1)
    """), "pkg")
    assert found[0]["expect"] == {2: "4", 4: "('a', 1)"}


def test_blocks_without_the_package_or_without_a_check_are_left_out():
    found, _ = examples.extract(_spec("""
        import os
        print(os.sep)  # Output: /
    """, """
        from pkg import double
        double(2)
    """), "pkg")
    assert found == []


def test_asserts_doctests_and_argument_free_test_functions_are_checks():
    found, _ = examples.extract(_spec("""
        from pkg import double
        assert double(2) == 4
    """, """
        >>> from pkg import double
        >>> double(5)
        10
    """, """
        from pkg import double

        def test_double():
            assert double(1) == 2

        def test_with_fixture(httpbin):
            assert double(1) == 2
    """), "pkg")
    assert len(found) == 3
    assert found[1]["doctest"] is True
    assert found[2]["calls"] == ["test_double"]


def test_a_class_listing_imported_from_the_package_is_a_signature_check():
    _, sigs = examples.extract(_spec("""
        from pkg.core import Thing

        class Thing(Base):
            def __init__(self, url, *, force_rollback=False, **options):
                ...

            def run(self, query, values=None):
                \"\"\"Run it.\"\"\"

            def _private(self, x):
                ...
    """), "pkg")
    assert len(sigs) == 1
    sig = sigs[0]
    assert (sig["module"], sig["name"], sig["kind"], sig["bases"]) == ("pkg.core", "Thing", "class", ["Base"])
    assert sig["methods"] == {"__init__": [["url", "pos"], ["force_rollback", "kw"]],
                              "run": [["query", "pos"], ["values", "pos"]]}


def test_a_bare_signature_line_without_a_body_is_still_a_signature():
    _, sigs = examples.extract(_spec("""
        def fix_text(
            text: str,
            config: Config | None = None,
            **kwargs,
        ) -> str:
    """), "pkg")
    assert [(s["module"], s["name"], s["params"]) for s in sigs] == [
        (None, "fix_text", [["text", "pos"], ["config", "pos"]])]


# --- running the generated gate against a small package ---------------------------------------------

PKG = '''
class PkgError(Exception):
    pass


def double(x):
    return 2 * x


def token():
    return "eyJhbGciOiJIUzI1NiJ9.payload.sig"


def record():
    return {"data": "value", "n": 1}


def as_bytes():
    return b'{"data":"value"}'


def fails():
    raise PkgError("boom")


class Thing:
    def __init__(self, url, *, force_rollback=False):
        self.url = url

    def run(self, query, values=None):
        return query
'''

SPEC = _spec(
    """
    from pkg import double
    print(double(2))  # Output: 4
    """,
    """
    from pkg import double
    print(double(2))  # Output: 5
    """,
    """
    from pkg import token
    print(token())
    # Output: eyJhbGciOiJIUzI1NiJ9...
    """,
    """
    from pkg import record
    print(record())  # Output: {"data": "value", "n": 1}
    """,
    """
    from pkg import as_bytes
    print(as_bytes())  # Output: {"data": "value"}
    """,
    """
    from pkg import double
    print(double(undefined_input))  # Output: 4
    """,
    """
    from pkg import double
    import some_module_nobody_installed
    assert double(1) == 2
    """,
    """
    from pkg import fails, PkgError
    try:
        fails()
    except PkgError:
        print("caught")
    """,
    """
    from pkg import double
    assert double(3) == 7
    """,
    """
    from pkg.core import Thing

    class Thing:
        def __init__(self, url, *, force_rollback=False):
            ...

        def run(self, query, values=None):
            ...
    """,
    """
    from pkg.core import Thing

    class Thing:
        def run(self, values, query):
            ...

        def stop(self):
            ...
    """,
)


def _outcomes(tmp_path, spec_text, waive=None):
    project = tmp_path / "proj"
    (project / "pkg").mkdir(parents=True)
    (project / "pkg" / "__init__.py").write_text("from pkg.core import *\nfrom pkg.core import PkgError\n")
    (project / "pkg" / "core.py").write_text(PKG)
    gates = project / ".claude" / "gates"
    spec = tmp_path / "SPEC.md"
    spec.write_text(spec_text)
    counts = examples.write_gate(spec, "pkg", gates / "spec_examples.py")
    for example_id, reason in (waive or {}).items():
        examples.waive(gates, example_id, reason)
    run = subprocess.run([sys.executable, "-m", "pytest", "-v", "-rA", "-p", "no:cacheprovider",
                          str(gates / "spec_examples.py")], cwd=project, capture_output=True, text=True)
    results = {}
    for test_id, status in re.findall(r"::\w+\[([\w.-]+)\] (PASSED|FAILED|SKIPPED|ERROR)", run.stdout):
        results[test_id] = status
    return counts, results, run.stdout


def test_the_generated_gate_passes_right_code_and_fails_wrong_examples(tmp_path):
    counts, results, out = _outcomes(tmp_path, SPEC)
    assert counts == {"examples": 9, "signatures": 2}, out
    ids = sorted(results)
    by_line = {i: results[i] for i in ids}
    passed = [i for i, s in by_line.items() if s == "PASSED"]
    failed = [i for i, s in by_line.items() if s == "FAILED"]
    skipped = [i for i, s in by_line.items() if s == "SKIPPED"]
    # right output, "..." prefix, quotes and spacing, bytes JSON, an expected package exception, a right signature
    assert len(passed) == 6, out
    # 4 != 5, assert 6 == 7, and a signature whose order and method are wrong
    assert len(failed) == 3, out
    # an undefined name in the example itself, and a module that is not the package's
    assert len(skipped) == 2, out
    assert "SPEC line" in out
    assert "values" in out and "stop" in out


def test_a_waived_example_is_skipped_with_its_reason(tmp_path):
    _, first, _ = _outcomes(tmp_path / "a", SPEC)
    wrong = [i for i, s in first.items() if s == "FAILED" and i.startswith("ex")]
    counts, results, out = _outcomes(tmp_path / "b", SPEC, waive={wrong[0]: "SPEC line 12 contradicts line 40"})
    assert results[wrong[0]] == "SKIPPED"
    assert "contradicts line 40" in out


def test_waiving_an_unknown_example_is_refused(tmp_path):
    gates = tmp_path / "gates"
    spec = tmp_path / "SPEC.md"
    spec.write_text(SPEC)
    examples.write_gate(spec, "pkg", gates / "spec_examples.py")
    try:
        examples.waive(gates, "ex999", "no such example")
    except ValueError as exc:
        assert "ex999" in str(exc)
    else:
        raise AssertionError("an unknown id was waived")
    assert json.loads((gates / "examples_waived.json").read_text()) == {}


def test_a_waiver_needs_a_reason_that_cites_the_spec(tmp_path):
    gates = tmp_path / "gates"
    spec = tmp_path / "SPEC.md"
    spec.write_text(SPEC)
    examples.write_gate(spec, "pkg", gates / "spec_examples.py")
    example_id = examples.ids(gates)[0]
    try:
        examples.waive(gates, example_id, "it is wrong")
    except ValueError as exc:
        assert "spec line" in str(exc).lower()
    else:
        raise AssertionError("a waiver without a spec line was accepted")


def _project(tmp_path):
    project = tmp_path / "proj"
    (project / "pkg").mkdir(parents=True)
    (project / "pkg" / "__init__.py").write_text("from pkg.core import *\nfrom pkg.core import PkgError\n")
    (project / "pkg" / "core.py").write_text(PKG)
    gates = project / ".claude" / "gates"
    gates.mkdir(parents=True)
    spec = tmp_path / "SPEC.md"
    spec.write_text(SPEC)
    examples.write_gate(spec, "pkg", gates / "spec_examples.py")
    return project, gates


def test_the_examples_file_is_advisory_so_the_gate_never_collects_it(tmp_path):
    project, gates = _project(tmp_path)
    (gates / "test_ok.py").write_text("def test_ok():\n    assert True\n")
    run = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(gates)],
                         cwd=project, capture_output=True, text=True)
    assert run.returncode == 0, run.stdout
    assert "1 passed" in run.stdout


def test_run_reports_agreements_and_each_disagreement_with_its_spec_line(tmp_path):
    project, gates = _project(tmp_path)
    record = tmp_path / "notes" / "goal.examples.json"
    result = examples.run(gates, project, python=sys.executable, record=record)
    assert (result["passed"], len(result["failed"]), result["skipped"]) == (6, 3, 2)
    messages = " ".join(f["message"] for f in result["failed"])
    assert "SPEC line" in messages and "printed '4'" in messages
    assert examples.last(record)["passed"] == 6
    # the record lives outside the gate directory: a run must not change the watched files
    assert sorted(p.name for p in gates.iterdir() if p.is_file()) == ["examples_waived.json", "spec_examples.py"]
    line = examples.summary_line(result)
    assert line.startswith("Spec examples: 6 agree, 3 disagree")
    assert "waive-example" in line


def test_summary_line_is_empty_without_examples():
    assert examples.summary_line(None) == ""
    assert examples.summary_line({"passed": 0, "failed": [], "skipped": 0}) == ""


def test_the_interpreter_comes_from_the_gate_command(tmp_path, monkeypatch):
    venv = tmp_path / "venv" / "bin"
    venv.mkdir(parents=True)
    for name in ("python", "pytest"):
        (venv / name).write_text("#!/bin/sh\n")
        (venv / name).chmod(0o755)
    assert examples.python_for(f"{venv}/python -m pytest gates -q") == str(venv / "python")
    monkeypatch.setenv("PATH", str(venv))
    assert examples.python_for("pytest gates -q") == str(venv / "python")
    assert examples.python_for("cd x && make test") == str(venv / "python")
