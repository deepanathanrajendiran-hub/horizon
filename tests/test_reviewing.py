"""Requirement lines and reviewer verdicts, read from real TB4 pilot reply shapes."""
import pytest

import reviewing

REQUIREMENTS = """# Requirements
R1 [gate] "the CLI prints the turnaround time"
- R2 [review] "write each attack you hear as its own <note>"
3. **R3** [unverifiable] [judgment] "identify the exact residue positions" (reading: 1-based)
R4 (from old-goal R2) [gate] "exit status 3 on bad input"
Notes about R9 are not a requirement line.
R12: [review] "unchanged output for valid files"
"""


def test_requirement_lines_read_ids_and_tags():
    assert reviewing.requirement_lines(REQUIREMENTS) == {
        "R1": {"gate"}, "R2": {"review"}, "R3": {"unverifiable", "judgment"},
        "R4": {"gate"}, "R12": {"review"},
    }


def test_verdicts_read_bullets_tables_emphasis_and_dashes():
    reply = "\n".join([
        "Reviewed checkpoint: 2a43c09b1234",
        "- R1: PASS - ran `cli plan.json`, printed 45 min",
        "| R2 | FAIL | m18 has two attacks merged |",
        "1. **R3** [unverifiable]: NOT REVIEWED - no reference audio",
        "R4 — passed (exit 3 observed)",
        "**R5:** UNVERIFIED - could not build torch",
        "R6 (gate) - Fail: tuple path broken",
    ])
    assert reviewing.verdicts(reply) == ({
        "R1": "PASS", "R2": "FAIL", "R3": "NOT REVIEWED",
        "R4": "PASS", "R5": "UNVERIFIED", "R6": "FAIL",
    }, [])


@pytest.mark.parametrize("line", [
    "**Verdict: FAIL, narrowly.**",
    "**FAIL.**",
    "Verdict: FAIL",
    "Overall verdict - fail: the merged session is force-collected",
    "**Verdict:** FAIL",
])
def test_fail_headlines_are_found(line):
    reply = f"Reviewed checkpoint: abc\n{line}\nR1: PASS - ok"
    assert reviewing.verdicts(reply)[1] == [line]


@pytest.mark.parametrize("line", [
    "**Verdict: PASS, with caveats.**",
    "| Req | Verdict | Fail reason |",
    "Fail-safe paths were exercised.",
    "Reviewed checkpoint: abc\n> **Verdict: FAIL, narrowly.**",
    "No FAIL lines remain.",
])
def test_other_lines_are_not_fail_headlines(line):
    assert reviewing.verdicts(line)[1] == []


def test_quoted_lines_are_ignored_and_the_last_verdict_wins():
    assert reviewing.verdicts("> R2: FAIL - old finding\nR2: PASS - fixed") == ({"R2": "PASS"}, [])
    assert reviewing.verdicts("R1: PASS\nR1: FAIL - on reread")[0] == {"R1": "FAIL"}


def test_pass_refusals_without_requirements_flag_only_failures():
    assert reviewing.pass_refusals("Reviewed checkpoint: x\nLooks right overall.", None) == []
    assert reviewing.pass_refusals("R1: PASS\nR2: NOT REVIEWED", None) == []
    assert reviewing.pass_refusals("R1: PASS\nR2: UNVERIFIED - no data", None) == ["R2: UNVERIFIED"]
    assert reviewing.pass_refusals("**FAIL.**", None) == ["FAIL headline: **FAIL.**"]


def test_pass_refusals_with_requirements_need_every_line():
    required = {"R1": {"gate"}, "R2": {"review"}, "R3": {"unverifiable"}}
    assert reviewing.pass_refusals("R1: PASS\nR2: PASS\nR3: NOT REVIEWED", required) == []
    assert reviewing.pass_refusals("R1: PASS\nR3: PASS", required) == ["R2: no verdict"]
    assert reviewing.pass_refusals("R1: NOT REVIEWED\nR2: PASS\nR3: PASS", required) == [
        "R1: NOT REVIEWED on a line not tagged [unverifiable]"]


def test_echoes_needs_the_checkpoint_prefix():
    digest = "2a43c09b" + "0" * 56
    assert reviewing.echoes("Reviewed checkpoint: 2A43C09B0000", digest)
    assert not reviewing.echoes("Reviewed checkpoint: 6ec419f70000", digest)


def test_brief_fills_the_template_and_keeps_two_placeholders():
    text = reviewing.brief(digest="a" * 64, request="Fix the parser.",
                           requirements='R1 [gate] "Fix the parser."',
                           harness_dir="/p/.claude/harness", review_file="/tmp/r.md", previous=[])
    assert text.startswith(f"Review checkpoint {'a' * 64}. Begin your reply with "
                           f"`Reviewed checkpoint: {'a' * 64}`.")
    assert "Request (verbatim):\nFix the parser.\n" in text
    assert 'Requirements:\nR1 [gate] "Fix the parser."\n' in text
    assert "<<ARTIFACTS>>" in text and "<<HOW_TO_RUN>>" in text
    assert "/p/.claude/harness" in text and "/tmp/r.md" in text
    assert "Previous:" not in text


def test_brief_quotes_previous_open_lines():
    text = reviewing.brief(digest="a" * 64, request="r", requirements="R1 x", harness_dir="h",
                           review_file="f", previous=["R2: FAIL - m18 merges two attacks"])
    assert text.endswith("\nPrevious:\n> R2: FAIL - m18 merges two attacks\n")


def test_previous_lines_keep_only_open_verdicts_in_order():
    reply = ("Reviewed checkpoint: x\nR1: PASS - ok\nR2: FAIL - m18\n"
             "**Verdict: FAIL, narrowly.**\nR3: UNVERIFIED - no audio\nR4: NOT REVIEWED\n"
             "R5: FAIL - first look\nR5: PASS - on rerun\nNon-blocking: style")
    assert reviewing.previous_lines(reply) == [
        "R2: FAIL - m18", "**Verdict: FAIL, narrowly.**", "R3: UNVERIFIED - no audio"]


def test_long_request_is_cut_with_a_pointer(tmp_path):
    gates = tmp_path / "gates"
    gates.mkdir()
    (gates / "request.md").write_text("q" * (reviewing.BRIEF_FILE_LIMIT + 10))
    (gates / "requirements.md").write_text("R1 [gate] x\n")
    state = {"slug": "s", "gate_path": str(gates), "reviews": []}
    text = reviewing.brief_for(state, "b" * 64, "/tmp/r.md", "/p/.claude/harness")
    assert f"[truncated; the full text is in {gates / 'request.md'}, which you may read]" in text
    assert "q" * reviewing.BRIEF_FILE_LIMIT in text and "q" * (reviewing.BRIEF_FILE_LIMIT + 1) not in text


def test_unreadable_gate_files_become_placeholders(tmp_path):
    state = {"slug": "s", "gate_path": str(tmp_path / "gone"), "reviews": []}
    text = reviewing.brief_for(state, "b" * 64, "/tmp/r.md", "/p/.claude/harness")
    assert "<<REQUEST: could not read" in text and "<<REQUIREMENTS: could not read" in text

# Final-review findings: the shapes reviewers actually write.

@pytest.mark.parametrize("line, expected", [
    ("R1 [gate] -> PASS", {"R1": "PASS"}),
    ("R1 → PASS", {"R1": "PASS"}),
    ("R1 = PASS", {"R1": "PASS"}),
    ("R1: ✅ PASS", {"R1": "PASS"}),
    ("R2: ❌ FAIL", {"R2": "FAIL"}),
    ("### R1: PASS", {"R1": "PASS"}),
    ("### R2 — FAIL", {"R2": "FAIL"}),
    ("- [x] R1: PASS", {"R1": "PASS"}),
    ("• R1: FAIL", {"R1": "FAIL"}),
    ("R1: FAIL", {"R1": "FAIL"}),
    ('R1 [gate] "ready.txt says ready": PASS', {"R1": "PASS"}),
    ("**R1 [gate]**: PASS", {"R1": "PASS"}),
    ("R2 [unverifiable] [judgment]: NOT REVIEWED", {"R2": "NOT REVIEWED"}),
    ("R4 (from fix-fahrenheit-conversion-in-temps-py-and-accept-kelvin R3) [gate]: PASS",
     {"R4": "PASS"}),
    ("| R1 | ready.txt says ready | PASS | cat ready.txt |", {"R1": "PASS"}),
    ("| 1 | R1 | PASS |", {"R1": "PASS"}),
    ("| R1 | [gate] | pass |", {"R1": "PASS"}),
    ("| R2 | keep exit 3 | FAIL | empty file exits 0 |", {"R2": "FAIL"}),
    ("R1-R3: PASS", {"R1": "PASS", "R2": "PASS", "R3": "PASS"}),
    ("R1, R2: PASS", {"R1": "PASS", "R2": "PASS"}),
    ("R1: FAILING", {"R1": "FAIL"}),
    ("R1: Failure - x", {"R1": "FAIL"}),
    ("R2: Fixed, now PASS", {"R2": "PASS"}),
    ("R1a: FAIL - b.txt says broken", {"R1a": "FAIL"}),
    ("R1.2: PASS", {"R1.2": "PASS"}),
])
def test_verdicts_read_the_shapes_reviewers_write(line, expected):
    assert reviewing.verdicts(line) == (expected, [])


@pytest.mark.parametrize("line, expected", [
    ("R1: PASS for valid files, FAIL for the empty file (exit 0, expected 3)", "FAIL"),
    ("R1: passes exit 3 through only for valid files - FAIL: empty file exits 0", "FAIL"),
    ("R1: pass/fail could not be determined - UNVERIFIED", "UNVERIFIED"),
    ("R1: Passed? No: the symptom still reproduces. FAIL", "FAIL"),
    ("R1: PASS - the parser no longer fails on empty input", "PASS"),
    ('R1 [gate] "the CLI fails with exit 3 on bad input": PASS - exit 3', "PASS"),
    ("R1: PASS - ran `grep FAIL out.log`, no matches", "PASS"),
    ("R1: PASS - 12 PASSED, 0 FAILED", "PASS"),
    ("R1: PASS - was FAIL in the first review", "PASS"),
])
def test_the_worst_verdict_on_a_line_wins(line, expected):
    assert reviewing.verdicts(line)[0] == {"R1": expected}


@pytest.mark.parametrize("line", [
    "**Overall: FAIL** - the second example still crashes",
    "Result: FAIL (second example crashes)",
    "Status: FAIL",
    "Conclusion: fail",
    "Fail.",
    "**Fail**",
    "| **Overall** | **FAIL** |",
    "Overall verdict: R2 FAIL",
])
def test_overall_fail_shapes_are_headlines(line):
    assert reviewing.verdicts(f"Reviewed checkpoint: abc\n{line}")[1] == [line]


@pytest.mark.parametrize("reply", [
    "R1: PASS - before the fix\n```\nFAILED tests/test_ready.py::test_ready - AssertionError\n"
    "--- FAIL: TestReady (0.00s)\nFAIL: test_ready (tests.test_ready)\n```\nnow 3 passed",
    "Verdict: failure modes from the earlier review no longer reproduce.",
    "FAIL lines from the previous review are now resolved.",
    "- FAIL messages now include the path",
    "FAIL count: 0",
    "--- FAIL: TestReady (0.00s)",
    "Verdict: PASS (0 FAIL)",
])
def test_test_output_and_prose_are_not_headlines(reply):
    assert reviewing.verdicts(reply)[1] == []


def test_a_reply_quoted_in_full_is_still_read():
    reply = "> Reviewed checkpoint: abc\n> R1: PASS\n> R2: FAIL - empty file exits 0"
    assert reviewing.verdicts(reply) == ({"R1": "PASS", "R2": "FAIL"}, [])


def test_requirement_lines_keep_sub_ids_apart():
    text = ('R1 [gate] "a"\nR1a [review] "b"\nR1.2 [review] "c"\n'
            '## R2 [gate] "d"\n- [ ] R3 [review] "e"\n')
    assert reviewing.requirement_lines(text) == {
        "R1": {"gate"}, "R1a": {"review"}, "R1.2": {"review"}, "R2": {"gate"}, "R3": {"review"}}


def test_a_fail_on_a_sub_id_blocks_the_pass():
    required = reviewing.requirement_lines('R1 [gate] "a"\nR1a [review] "b"\nR1b [review] "c"\n')
    assert reviewing.pass_refusals("R1: PASS\nR1a: PASS\nR1b: FAIL - b.txt says broken",
                                   required) == ["R1b: FAIL"]


def test_a_missing_verdict_quotes_the_line_that_mentions_it():
    refusals = reviewing.pass_refusals("R1: PASS\nR2 looks fine to me", {"R1": set(), "R2": set()})
    assert refusals == ['R2: no verdict (unread line: "R2 looks fine to me")']


def test_brief_keeps_reviewer_files_out_of_the_project():
    text = reviewing.brief(digest="a" * 64, request="r", requirements="R1 x", harness_dir="h",
                           review_file="/tmp/r.md", previous=[])
    assert "Do not create, change or delete files inside the project" in " ".join(text.split())


def test_review_file_leaves_watched_paths_and_survives_a_missing_tempdir(tmp_path, monkeypatch):
    project = tmp_path / "project"
    monkeypatch.setattr(reviewing.tempfile, "gettempdir", lambda: str(project / "tmp"))
    chosen = reviewing.review_file("s", "a" * 64, 1, watch_paths=[str(project)])
    assert not chosen.startswith(str(project))

    def broken():
        raise FileNotFoundError("no usable temporary directory")
    monkeypatch.setattr(reviewing.tempfile, "gettempdir", broken)
    assert reviewing.review_file("s", "a" * 64, 1, watch_paths=[str(project)])
