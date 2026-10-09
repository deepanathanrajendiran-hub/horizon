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
    "> **Verdict: FAIL, narrowly.**",
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
