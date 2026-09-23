"""Validate shipped skill entrypoints, references and CLI examples for both agents."""
import re
import shlex
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "harness"))
import cli


@pytest.mark.parametrize("runtime,folder", [("codex", ".agents"), ("claude", ".claude")])
@pytest.mark.parametrize("name", ["horizon", "refine"])
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
@pytest.mark.parametrize("name", ["horizon", "refine"])
def test_documented_commands_parse_with_the_real_cli(runtime, folder, name):
    text = (ROOT / folder / "skills" / name / "SKILL.md").read_text()
    commands = 0
    fence = chr(96) * 3
    for block in re.findall(fence + r"bash\n(.*?)" + fence, text, re.S):
        for line in block.replace("\\\n", "").splitlines():
            if line.startswith('python3 "$HARNESS"'):
                args = cli.parser().parse_args(shlex.split(line)[2:])
                assert args.runtime == runtime
                commands += 1
    assert commands > 0

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
