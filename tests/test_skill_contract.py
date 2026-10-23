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
                   "[unverifiable]", "[judgment]", "# Still passes:", "baseline before any change",
                   "--gate-path", "review_brief", "NOT REVIEWED", "deadline",
                   "at most two review rounds", "## Final report"):
        assert phrase in text, phrase


def test_documented_review_template_matches_the_harness():
    text = (ROOT / "docs/harness-cli.md").read_text()
    section = text.split("## Requirements and review", 1)[1]
    block = re.search(chr(96) * 3 + r"text\n(.*?)" + chr(96) * 3, section, re.S).group(1)
    assert block == reviewing.TEMPLATE
