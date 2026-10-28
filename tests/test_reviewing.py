"""Requirement lines and reviewer verdicts, read from real TB4 pilot reply shapes
and from the replies two adversarial probes wrote against the first parsers."""
import pytest

import reviewing
from pathlib import Path

REQUIREMENTS = """# Requirements
R1 [gate] "the CLI prints the turnaround time"
- R2 [review] "write each attack you hear as its own <note>"
3. **R3** [unverifiable] [judgment] "identify the exact residue positions" (reading: 1-based)
R4 (from old-goal R2) [gate] "exit status 3 on bad input"
Notes about R9 are not a requirement line.
R12: [review] "unchanged output for valid files"
"""

REQS = reviewing.requirement_lines('R1 [gate] "a"\nR2 [gate] "b"\nR3 [unverifiable] "c"\n')


def reply(*body, block=("R1: PASS", "R2: PASS", "R3: NOT REVIEWED")):
    return "\n".join(["Reviewed checkpoint: 2a43c09b1234", *body, "",
                      "VERDICTS", *block, "END VERDICTS"])


def test_requirement_lines_read_ids_and_tags():
    assert reviewing.requirement_lines(REQUIREMENTS) == {
        "R1": {"gate"}, "R2": {"review"}, "R3": {"unverifiable", "judgment"},
        "R4": {"gate"}, "R12": {"review"},
    }


def test_requirement_lines_keep_sub_ids_apart():
    text = ('R1 [gate] "a"\nR1a [review] "b"\nR1.2 [review] "c"\n'
            '## R2 [gate] "d"\n- [ ] R3 [review] "e"\n')
    assert reviewing.requirement_lines(text) == {
        "R1": {"gate"}, "R1a": {"review"}, "R1.2": {"review"}, "R2": {"gate"}, "R3": {"review"}}


def test_a_complete_block_supports_a_pass():
    assert reviewing.pass_refusals(reply("R1: ran `cli plan.json`, printed 45 min"), REQS) == []


def test_a_fenced_or_emphasised_block_is_read():
    text = ("Reviewed checkpoint: x\n**VERDICTS**\n```\nR1: PASS\nR2: pass\n"
            "R3: NOT REVIEWED\n```\n**END VERDICTS**")
    assert reviewing.pass_refusals(text, REQS) == []


@pytest.mark.parametrize("block, problem", [
    (("R1: PASS", "R2: FAIL", "R3: NOT REVIEWED"), "R2: FAIL"),
    (("R1: PASS", "R2: UNVERIFIED", "R3: NOT REVIEWED"), "R2: UNVERIFIED"),
    (("R1: PASS", "R3: NOT REVIEWED"), "R2: no verdict in the VERDICTS block"),
    (("R1: NOT REVIEWED", "R2: PASS", "R3: NOT REVIEWED"),
     "R1: NOT REVIEWED on a line not tagged [unverifiable]"),
    (("R1: PASS", "R2: PASS", "R3: NOT REVIEWED", "R9: PASS"), "R9: not a requirement line"),
    (("R1: PASS", "R2: PASS", "R2: FAIL", "R3: NOT REVIEWED"), "R2: more than one verdict"),
    (("R1: PASS", "R2: PASS for valid files, FAIL for the empty file", "R3: NOT REVIEWED"),
     "unreadable VERDICTS line"),
    (("R1: PASS", "R2: PARTIAL PASS", "R3: NOT REVIEWED"), "unreadable VERDICTS line"),
    (("R1: PASS", "R2: NOT PASSED", "R3: NOT REVIEWED"), "unreadable VERDICTS line"),
    (("R1: PASS", "R2: ?", "R3: NOT REVIEWED"), "unreadable VERDICTS line"),
])
def test_the_block_must_be_complete_readable_and_all_pass(block, problem):
    refusals = reviewing.pass_refusals(reply(block=block), REQS)
    assert any(problem in refusal for refusal in refusals), refusals


def test_a_gate_path_goal_needs_the_block():
    text = "Reviewed checkpoint: x\nR1: PASS\nR2: PASS\nR3: NOT REVIEWED"
    assert reviewing.pass_refusals(text, REQS) == ["no VERDICTS block"]


def test_two_blocks_or_an_unclosed_block_are_refused():
    two = reply() + "\nVERDICTS\nR1: PASS\nR2: PASS\nR3: NOT REVIEWED\nEND VERDICTS"
    assert "more than one VERDICTS block" in reviewing.pass_refusals(two, REQS)
    unclosed = "Reviewed checkpoint: x\nVERDICTS\nR1: PASS\nR2: PASS\nR3: NOT REVIEWED"
    assert "VERDICTS block has no END VERDICTS line" in reviewing.pass_refusals(unclosed, REQS)


@pytest.mark.parametrize("body", [
    "R2 (empty file): FAIL - `cli empty.txt; echo $?` printed 0, expected 3",
    "- **R2:** FAIL - empty file exits 0",
    "R2 [gate]: UNVERIFIED - could not build torch",
    "R2: fail - the empty file exits 0",
    "**Verdict: FAIL, narrowly.** The burst path still drops tool calls.",
    "**FAIL.** The merged session is force-collected.",
    "Verdict: FAIL",
    "**Verdict:** FAIL",
    "Overall verdict - fail: the merged session is force-collected",
    "Review verdict: FAIL - `cli --json` regressed",
    "1. **Overall:** FAIL",
    "Summary: FAIL",
    "Result: FAIL (second example crashes)",
    "| **Overall** | **FAIL** |",
    "## ❌ FAIL",
    "Verdict: ❌",
    "Fail.",
    "**Fail**",
])
def test_a_fail_in_the_reply_body_blocks_a_pass(body):
    assert reviewing.pass_refusals(reply(body), REQS) != []
    assert reviewing.pass_refusals(reply(body), None) != []


@pytest.mark.parametrize("body", [
    "R2: PASS - the previously reported FAIL (300K rejected) is fixed",
    "R2: PASS (round 1: FAIL) - `python temps.py 300K` -> 26.85C",
    "R1: PASS - python validate.py samples/missing_id.csv prints row 3: FAIL (missing id)",
    "Non-blocking:\n- R3: the test for the FAIL branch takes 4s",
    "R4 (from fix-parser R2, which FAILED there): PASS - exit 3",
    "R1: PASS - pytest tests/ -> PASSED: 9, FAILED: 0",
    "R1: PASS - the parser no longer fails on empty input",
    "**Overall: PASS** - the previous FAIL on R2 is resolved.",
    "**Verdict: PASS, with caveats.**",
    "Result: PASS 6 · FAIL 0 · UNVERIFIED 0",
    "Status: PASS (FAIL: 0, UNVERIFIED: 0)",
    "## Summary\nPassed: 6\nFailed: 0",
    "FAIL: none",
    "**Failed:** 0",
    "FAIL count: 0",
    "Pre-fix run:\nFAIL: test_fahrenheit (tests.test_temps.TempsTest)",
    "| Req | Previous | Now |\n|---|---|---|\n| R2 | FAIL | PASS |",
    "| Req | Verdict | Fail reason |",
    "R7 [unverifiable]: NOT REVIEWED - no Windows machine, so this stays UNVERIFIED",
    '### R1 [gate] "Fix the conversion"\n**Verdict: PASS**',
    "R1: `PASS` - ran it",
    "> R2: FAIL - the earlier reviewer's finding",
    "> **Verdict: FAIL, narrowly.**",
    "```\nFAILED tests/test_ready.py::test_ready - AssertionError\n--- FAIL: TestReady (0.00s)\n```",
    "--- FAIL: TestReady (0.00s)",
    "- FAIL messages now include the path",
    "Verdict: failure modes from the earlier review no longer reproduce.",
    "Fail-safe paths were exercised.",
    "FAIL lines from the previous review are now resolved.",
    "No FAIL lines remain.",
    "```cli --json plan.json``` now prints JSON.",
])
def test_ordinary_review_prose_does_not_block_a_pass(body):
    assert reviewing.pass_refusals(reply(body), REQS) == []


def test_an_inline_triple_backtick_does_not_hide_a_later_headline():
    text = reply("```cli --json plan.json``` now prints nothing.", "**Verdict: FAIL** - `--json` regressed.")
    assert reviewing.pass_refusals(text, REQS) == ["FAIL headline: **Verdict: FAIL** - `--json` regressed."]


def test_without_a_gate_path_the_block_is_optional():
    assert reviewing.pass_refusals("Reviewed checkpoint: x\nLooks right overall.", None) == []
    assert reviewing.pass_refusals("R1: PASS\nR2: NOT REVIEWED", None) == []
    assert reviewing.pass_refusals("R1: PASS\nR2: UNVERIFIED - no data", None) == [
        'R2: UNVERIFIED (line: "R2: UNVERIFIED - no data")']
    assert reviewing.pass_refusals(reply(block=("R1: PASS", "R2: FAIL")), None) == ["R2: FAIL"]


def test_previous_lines_keep_open_verdicts_in_order():
    text = ("Reviewed checkpoint: x\nR2 (empty file): FAIL - m18\n**Verdict: FAIL, narrowly.**\n"
            "R1 looked fine\n> R5: FAIL - an older finding\n\n"
            "VERDICTS\nR1: PASS\nR2: FAIL\nR3: UNVERIFIED\nR4: NOT REVIEWED\nEND VERDICTS")
    assert reviewing.previous_lines(text) == [
        "R2 (empty file): FAIL - m18", "**Verdict: FAIL, narrowly.**", "R2: FAIL", "R3: UNVERIFIED"]


def test_echoes_needs_the_checkpoint_prefix():
    digest = "2a43c09b" + "0" * 56
    assert reviewing.echoes("Reviewed checkpoint: 2A43C09B0000", digest)
    assert not reviewing.echoes("Reviewed checkpoint: 6ec419f70000", digest)


def test_brief_fills_the_template_and_keeps_two_placeholders():
    text = reviewing.brief(digest="a" * 64, request="Fix the parser.",
                           requirements='R1 [gate] "Fix the parser."', verdicts="R1: ?",
                           harness_dir="/p/.claude/harness", review_file="/tmp/r.md", previous=[])
    assert text.startswith(f"Review checkpoint {'a' * 64}. Begin your reply with "
                           f"`Reviewed checkpoint: {'a' * 64}`.")
    assert "Request (verbatim):\nFix the parser.\n" in text
    assert 'Requirements:\nR1 [gate] "Fix the parser."\n' in text
    assert "\nVERDICTS\nR1: ?\nEND VERDICTS\n" in text
    assert "<<ARTIFACTS>>" in text and "<<HOW_TO_RUN>>" in text
    assert "/p/.claude/harness" in text and "/tmp/r.md" in text
    assert "Previous:" not in text
    assert "Do not create, change or delete files inside the project" in " ".join(text.split())


def test_brief_quotes_previous_open_lines():
    text = reviewing.brief(digest="a" * 64, request="r", requirements="R1 x", verdicts="R1: ?",
                           harness_dir="h", review_file="f",
                           previous=["R2: FAIL - m18 merges two attacks"])
    assert text.endswith("\nPrevious:\n> R2: FAIL - m18 merges two attacks\n")


def test_brief_skeleton_lists_every_requirement(tmp_path):
    gates = tmp_path / "gates"
    gates.mkdir()
    (gates / "request.md").write_text("Fix it.")
    (gates / "requirements.md").write_text('R1 [gate] "a"\nR1a [review] "b"\nR2 [unverifiable] "c"\n')
    text = reviewing.brief_for({"slug": "s", "gate_path": str(gates), "reviews": []},
                               "b" * 64, "/tmp/r.md", "/p/.claude/harness")
    assert "\nVERDICTS\nR1: ?\nR1a: ?\nR2: ?\nEND VERDICTS\n" in text


def test_long_request_is_cut_with_a_pointer(tmp_path):
    gates = tmp_path / "gates"
    gates.mkdir()
    (gates / "request.md").write_text("q" * (reviewing.BRIEF_FILE_LIMIT + 10))
    (gates / "requirements.md").write_text("R1 [gate] x\n")
    state = {"slug": "s", "gate_path": str(gates), "reviews": []}
    text = reviewing.brief_for(state, "b" * 64, "/tmp/r.md", "/p/.claude/harness")
    assert f"[truncated; the full text is in {gates / 'request.md'}, which you may read]" in text
    assert "q" * reviewing.BRIEF_FILE_LIMIT in text
    assert "q" * (reviewing.BRIEF_FILE_LIMIT + 1) not in text


def test_unreadable_gate_files_become_placeholders(tmp_path):
    state = {"slug": "s", "gate_path": str(tmp_path / "gone"), "reviews": []}
    text = reviewing.brief_for(state, "b" * 64, "/tmp/r.md", "/p/.claude/harness")
    assert "<<REQUEST: could not read" in text and "<<REQUIREMENTS: could not read" in text
    assert "<<VERDICT_LINES" in text


def test_review_file_leaves_watched_paths_and_survives_a_missing_tempdir(tmp_path, monkeypatch):
    project = tmp_path / "project"
    monkeypatch.setattr(reviewing.tempfile, "gettempdir", lambda: str(project / "tmp"))
    chosen = reviewing.review_file("s", "a" * 64, 1, watch_paths=[str(project)])
    assert not chosen.startswith(str(project))

    def broken():
        raise FileNotFoundError("no usable temporary directory")
    monkeypatch.setattr(reviewing.tempfile, "gettempdir", broken)
    assert reviewing.review_file("s", "a" * 64, 1, watch_paths=[str(project)])

@pytest.mark.parametrize("line, readable", [
    ("- **R1:** **PASS** ✅", True),
    ("`R1: PASS`", True),
    ("* R1: PASS", True),
    ("R1: PASS ❌", True),
    ("R1: PASS - but the empty file exits 0", False),
    ("R1: PASS (see above)", False),
])
def test_block_lines_may_carry_markdown_decoration_and_nothing_else(line, readable):
    text = reply(block=(line, "R2: PASS", "R3: NOT REVIEWED"))
    refusals = reviewing.pass_refusals(text, REQS)
    assert any("unreadable" in refusal for refusal in refusals) != readable, refusals
    if line.endswith("❌"):
        assert "R1: FAIL" in refusals

DIGEST = "c98b12a9e830" + "0" * 52


def _round(directory, n, text, slug="s", digest=DIGEST):
    path = directory / f"horizon-review-{slug}-{digest[:12]}-{n}.md"
    path.write_text(text)
    return path


def test_carried_keeps_lines_every_earlier_round_passed(tmp_path):
    # Reviewers cut off by a session cap leave their decided lines behind.
    _round(tmp_path, 1, f"Reviewed checkpoint: {DIGEST}\nR1: PASS - ran tok - ok\nR2: FAIL - broke\n")
    _round(tmp_path, 2, f"Reviewed checkpoint: {DIGEST[:12]}\n- **R2:** PASS\nR3: PASS - x\n"
                        "> R1: FAIL - an earlier reviewer, quoted\nR9: PASS - not a requirement\n")
    _round(tmp_path, 3, "no echo of the checkpoint\nR4: PASS\n")
    _round(tmp_path, 1, f"Reviewed checkpoint: {'d' * 12}\nR4: PASS\n", digest="d" * 64)
    _round(tmp_path, 1, f"Reviewed checkpoint: {DIGEST}\nR4: PASS\n", slug="other")
    ids, files = reviewing.carried("s", DIGEST, {"R1": set(), "R2": set(), "R3": set(), "R4": set()},
                                   str(tmp_path / "horizon-review-s-x-4.md"))
    assert ids == ["R1", "R3"]
    assert [Path(f).name for f in files] == [f"horizon-review-s-{DIGEST[:12]}-{n}.md" for n in (1, 2)]


def test_carried_is_empty_without_earlier_rounds(tmp_path):
    assert reviewing.carried("s", DIGEST, {"R1": set()}, str(tmp_path / "r.md")) == ([], [])
    assert reviewing.carried("s", DIGEST, {"R1": set()}, str(tmp_path / "missing" / "r.md")) == ([], [])


def test_next_review_file_skips_rounds_already_on_disk(tmp_path, monkeypatch):
    monkeypatch.setattr(reviewing.tempfile, "gettempdir", lambda: str(tmp_path))
    first = reviewing.next_review_file("s", DIGEST, 1)
    assert first.endswith("-1.md")
    Path(first).write_text("cut off")
    assert reviewing.next_review_file("s", DIGEST, 1).endswith("-2.md")
    assert reviewing.next_review_file("s", DIGEST, 3).endswith("-3.md")


def test_brief_prefills_carried_lines_and_says_so(tmp_path):
    gates = tmp_path / "gates"
    gates.mkdir()
    (gates / "request.md").write_text("Fix it.")
    (gates / "requirements.md").write_text('R1 [gate] "a"\nR1a [review] "b"\nR2 [review] "c"\n')
    text = reviewing.brief_for({"slug": "s", "gate_path": str(gates), "reviews": []},
                               DIGEST, "/tmp/r.md", "/p/.claude/harness", carried=["R1", "R2"])
    assert "\nVERDICTS\nR1: PASS\nR1a: ?\nR2: PASS\nEND VERDICTS\n" in text
    assert text.endswith("\nAlready passed by an earlier reviewer of this checkpoint, who was cut off: "
                         "R1, R2. Leave those lines as PASS and test only the lines marked `?`.\n")


def test_brief_asks_for_verdicts_as_they_are_decided():
    text = " ".join(reviewing.brief(digest=DIGEST, request="r", requirements="R1 x", verdicts="R1: ?",
                                    harness_dir="h", review_file="/tmp/r.md", previous=[]).split())
    assert "as soon as you decide a requirement, append" in text
    assert "replace the contents of /tmp/r.md with your complete reply" in text
