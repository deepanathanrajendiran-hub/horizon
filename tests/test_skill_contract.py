"""Validate shipped skill entrypoints, references and CLI examples for both agents."""
import re
import shlex
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
import cli
from string import Template
import json
import task_state
import reviewing


@pytest.mark.parametrize("runtime,folder", [("codex", ".agents"), ("claude", ".claude")])
@pytest.mark.parametrize("name", ["horizon"])
def test_skill_has_resolvable_entrypoint_and_references(runtime, folder, name):
    skill = ROOT / folder / "skills" / name / "SKILL.md"
    text = skill.read_text()
    assert text.startswith("---\n")
    frontmatter = text.split("---", 2)[1]
    assert f"name: {name}" in frontmatter
    assert "description: " in frontmatter
    assert (skill.parent / "scripts/harness.py").resolve() == ROOT / "harness/cli.py"
    for link in re.findall(r"\]\(([^)]+)\)", text):
        if "://" not in link:
            assert (skill.parent / link.split("#")[0]).is_file(), link


@pytest.mark.parametrize("runtime,folder", [("codex", ".agents"), ("claude", ".claude")])
@pytest.mark.parametrize("name", ["horizon"])
def test_documented_commands_parse_with_the_real_cli(runtime, folder, name):
    skill = ROOT / folder / "skills" / name / "SKILL.md"
    sources = [skill]
    if name == "horizon":
        sources.append(skill.parent / "references/commands.md")

    commands = 0
    documented_state_commands = set()
    documented_by_source = {"SKILL.md": set(), "commands.md": set()}
    fence = chr(96) * 3
    for source in sources:
        text = source.read_text()
        for block in re.findall(fence + r"bash\n(.*?)" + fence, text, re.S):
            for line in block.replace("\\\n", "").splitlines():
                if line.startswith('python3 "$HARNESS"') and not line.endswith(" --help"):
                    expanded = Template(line).safe_substitute(
                        RUNTIME=runtime,
                        CURRENT_REVISION="2",
                        PACKET_REVISION="1",
                    )
                    args = cli.parser().parse_args(shlex.split(expanded)[2:])
                    assert args.runtime == runtime
                    commands += 1
                    documented_by_source[source.name].add(args.action)
                    if source.name == "commands.md":
                        documented_state_commands.add(args.action)

    assert commands > 0
    if name == "horizon":
        assert {
            "step-begin",
            "step-attach",
            "step-finish",
            "step-recover",
        } <= documented_state_commands
        evidence_flow = {"close", "observe", "step-reclaim", "approve-test-change"}
        assert evidence_flow <= documented_by_source["SKILL.md"]
        assert evidence_flow <= documented_by_source["commands.md"]

@pytest.mark.parametrize("runtime", ["codex", "claude"])
def test_state_mode_command_forms_parse(runtime):
    parsed = cli.parser().parse_args([
        "--runtime", runtime, "start", "Objective", "--execution", "state", "--gate", "true",
    ])
    assert parsed.execution == "state"
    assert cli.parser().parse_args([
        "--runtime", runtime, "step-begin", "goal", "--request-id", "r1",
    ]).request_id == "r1"
    assert cli.parser().parse_args([
        "--runtime", runtime, "step-attach", "goal", "--attempt", "r1", "--worker", "host-1",
        "--expected-revision", "2",
    ]).expected_revision == 2
    finished = cli.parser().parse_args([
        "--runtime", runtime, "step-finish", "goal", "--attempt", "r1",
        "--expected-revision", "2", "--update-file", "update.json",
        "--worker-evidence-file", "terminal.txt", "--timeout", "3", "--force",
    ])
    assert finished.timeout == 3 and finished.force is True
    recovered = cli.parser().parse_args([
        "--runtime", runtime, "step-recover", "goal", "--attempt", "r1",
        "--expected-revision", "2", "--reason", "Stopped.", "--worker-state", "stopped",
        "--evidence-file", "terminal.txt",
    ])
    assert recovered.worker_state == "stopped"

def test_documented_state_update_examples_merge_with_real_contract(tmp_path):
    text = (ROOT / "docs/harness-cli.md").read_text()
    examples = [
        json.loads(block)
        for block in re.findall(r"\x60\x60\x60json\n(.*?)\x60\x60\x60", text, re.S)
    ]
    full_update = next(
        example for example in examples
        if example.get("schema_version") == 1 and example.get("attempt_id") == "request-001"
    )
    operations = {
        example["op"]: example
        for example in examples
        if example.get("op") in {"upsert", "archive"}
    }
    assert set(operations) == {"upsert", "archive"}

    task = {
        "summary": "",
        "entries": [{
            "id": "obsolete-finding",
            "kind": "question",
            "statement": "Does the previous finding still apply?",
            "evidence": [],
            "provenance": {
                "attempt_id": "prior-001",
                "tree_hash": "seed-snapshot",
                "at": "2026-09-24T00:00:00+00:00",
            },
        }],
        "next_action": "Inspect the documented update examples.",
    }
    provenance = {
        "attempt_id": "request-001",
        "tree_hash": "current-snapshot",
        "at": "2026-09-24T00:01:00+00:00",
    }

    candidate, _ = task_state.merge_update(
        task,
        full_update,
        attempt_id="request-001",
        base_revision=1,
        project=tmp_path,
        provenance=provenance,
    )
    assert candidate["next_action"]

    for operation in operations.values():
        update = {
            "schema_version": 1,
            "attempt_id": "request-001",
            "base_revision": 1,
            "did": "Validate the documented operation envelope.",
            "ops": [operation],
        }
        candidate, _ = task_state.merge_update(
            task,
            update,
            attempt_id="request-001",
            base_revision=1,
            project=tmp_path,
            provenance=provenance,
        )
        if operation["op"] == "upsert":
            assert any(entry["id"] == "parser-open-question" for entry in candidate["entries"])
        else:
            assert all(entry["id"] != "obsolete-finding" for entry in candidate["entries"])

def _horizon_skill(folder):
    return (ROOT / folder / "skills/horizon/SKILL.md").read_text()


def test_horizon_skills_share_their_sections():
    def headings(folder):
        return re.findall(r"^#{2,3} .+$", _horizon_skill(folder), re.M)
    assert headings(".claude") == headings(".agents")


@pytest.mark.parametrize("folder", [".claude", ".agents"])
def test_horizon_skills_carry_the_v2_contract(folder):
    text = _horizon_skill(folder)
    for phrase in ("## Earlier goals", "## Before `start`", "request.md", "requirements.md",
                   "[unverifiable]", "[judgment]", "# Still passes:", "baseline `observation`",
                   "--gate-path", "review_brief", "NOT REVIEWED", "deadline",
                   "at most two review rounds", "## Final report"):
        assert phrase in text, phrase


def test_documented_review_template_matches_the_harness():
    text = (ROOT / "docs/harness-cli.md").read_text()
    section = text.split("## Requirements and review", 1)[1]
    block = re.search(chr(96) * 3 + r"text\n(.*?)" + chr(96) * 3, section, re.S).group(1)
    assert block == reviewing.TEMPLATE

@pytest.mark.parametrize("folder", [".claude", ".agents"])
def test_horizon_skills_carry_the_close_protocol(folder):
    text = " ".join(_horizon_skill(folder).split())
    for phrase in ("run `close", "always pass `--review`", "Never approve a changed test on your own judgment",
                   "A skipped check never blocks completion", "`step-reclaim`",
                   "baseline test file changed", "`review_carried`", "`review_carried_from`",
                   "`review_scope`", "do not count toward the two-round limit"):
        assert phrase in text, phrase
    assert "Another `check` on unchanged files records `skipped`" not in text
    assert "`complete` at once" not in text and "run `review` and then `complete`" not in text
    if folder == ".claude":
        assert "next session" in text and '"hooks": "installed"' in text


@pytest.mark.parametrize("folder", [".claude", ".agents"])
def test_horizon_skills_build_first_and_hand_off_early(folder):
    # NL2Repo bench1 and bench2: sessions cut off before `check` handed on nothing, review rounds replaced
    # building, re-orienting and re-reading the spec took most of each session, and a package that did not
    # import scored 0 (fun repo: docs/evaluations/nl2repo-bench2/transcript-findings.md).
    # NL2Repo bench1 and bench2: sessions cut off before `check` handed on nothing, review rounds replaced
    # building, re-orienting and re-reading the spec took most of each session, and a package that did not
    # import scored 0 (fun repo: docs/evaluations/nl2repo-bench2/transcript-findings.md).
    text = " ".join(_horizon_skill(folder).split())
    for phrase in ("## Resuming", "never spend a session only reading", "Keep the next action current with `note`",
                   "never a reading plan, and no session numbers", "`observe` takes `--next`",
                   "## Large inputs", "## Shell", "There is no `timeout` command", "`--timeout` on `start`",
                   "### Building from a spec",
                   "a directory without one still imports, as a namespace package",
                   "define a name in the same edit that exports it",
                   "A passing gate means your checks pass, not that the request is met",
                   "fix the library, not the caller", "Dispatch a reviewer at the start of a session",
                   "Build first, review last", "`review_failed_lines`", "`review_paused`",
                   "`review_failed_files` with `review --result fail`",
                   "listing the lines no reviewer passed as unverified",
                   "If it prints a `review_brief` and every part of the request is built"):
        assert phrase in text, phrase
    if folder == ".claude":
        assert "do not run `doctor` or `status`" in text
        assert "or the session-start message when it shows the goal's budget" in text
    else:
        assert "run `observe` for a current result" in text

@pytest.mark.parametrize("folder", [".claude", ".agents"])
def test_horizon_skills_build_a_spec_as_fast_as_plain(folder):
    # NL2Repo bench3: reading the spec in small slices with a notes pass cost pyjwt its first two sessions
    # (plain read it in four calls and scored 196 after session 1); structlog imported every module but missed
    # spec names and scored 0; notes said "nothing implemented" after the code existed; one session wrote
    # docs files because the skill said "each file in its tree" (fun repo: nl2repo-bench3/report.md).
    text = " ".join(_horizon_skill(folder).split())
    for phrase in ("up to about 800 lines per call", "never read it in small slices",
                   "Read a spec whole, once, in the first session",
                   "Never record in notes what is or is not implemented",
                   "never in a call of their own", "read the whole spec now, in large parts",
                   "surface --spec", "one test per public name the spec gives",
                   "Delete a name from it only when the spec says it must not exist",
                   "never rewrite the probe around it",
                   "do not write checks for code that already passes them",
                   "docs, CI and config files only when the request asks for them"):
        assert phrase in text, phrase
    for gone in ("Never `cat` a file over 50 KB", "Read the notes before the input", "each file in its tree",
                 "read only its outline and module list now", "each as an importable stub"):
        assert gone not in text, gone
    if folder == ".claude":
        assert "Do not run `status`, `ls` or `wc` to get these again" in text
