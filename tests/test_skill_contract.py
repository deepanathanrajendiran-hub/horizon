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
