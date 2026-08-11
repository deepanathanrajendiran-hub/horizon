"""Multi-process races on shared harness state.

A harness whose premise is surviving across sessions will be run from two
terminals in one project, or by a /loop firing beside a live session.
Read-modify-write without a lock does not error there -- it silently
drops data, which is the worst failure mode for a durable record.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BIN = REPO_ROOT / ".claude" / "harness" / "bin"

N = 8


def _run_workers(body: str, state_dir: Path, n: int = N):
    """Run `body` in n real subprocesses, all racing on the same state."""
    script = textwrap.dedent(f"""
        import sys, os
        sys.path.insert(0, {str(BIN)!r})
        import paths
        paths.goals_dir = lambda: {str(state_dir)!r} and __import__("pathlib").Path({str(state_dir)!r})
        paths.undo_dir = lambda: __import__("pathlib").Path({str(state_dir)!r})
        paths.changelog = lambda: __import__("pathlib").Path({str(state_dir / "CHANGELOG.md")!r})
        WORKER = int(sys.argv[1])
        {textwrap.indent(textwrap.dedent(body), "        ").strip()}
    """)
    procs = [subprocess.Popen([sys.executable, "-c", script, str(i)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
             for i in range(n)]
    return [p.communicate() for p in procs]


def test_concurrent_checkpoints_do_not_lose_turns(tmp_path):
    state_dir = tmp_path / "goals"
    state_dir.mkdir()
    sys.path.insert(0, str(BIN))
    import goal
    goal.paths.goals_dir = lambda: state_dir
    st = goal.create(goal="race", cwd=str(tmp_path), gate_kind="command",
                     gate_value="true", max_turns=100)

    _run_workers(f"""
        import goal
        goal.checkpoint({st['slug']!r}, f"worker {{WORKER}}", "next", "h", "fail", "")
    """, state_dir)

    final = json.loads((state_dir / f"{st['slug']}.json").read_text())
    assert final["used"]["turns"] == N, (
        f"lost turns: used.turns={final['used']['turns']} after {N} checkpoints"
    )
    assert len(final["checkpoints"]) == N, (
        f"lost checkpoints: {len(final['checkpoints'])} recorded of {N}"
    )


def test_concurrent_apply_edits_all_get_a_snapshot(tmp_path):
    """A colliding entry id overwrites another proposal's snapshot, which
    silently destroys the ability to undo it."""
    state_dir = tmp_path / "undo"
    state_dir.mkdir()
    targets = tmp_path / "targets"
    targets.mkdir()

    _run_workers(f"""
        import refine_log
        refine_log.apply_edit("M", [({str(targets)!r} + f"/w{{WORKER}}.md", f"content {{WORKER}}")],
                              f"rationale {{WORKER}}", "evidence")
    """, state_dir)

    entries = [json.loads(p.read_text()) for p in state_dir.glob("[0-9]*.json")]
    assert len(entries) == N, f"{len(entries)} entries survived of {N} proposals"

    snapshotted = {Path(t["path"]).name for e in entries for t in e["targets"]}
    written = {p.name for p in targets.glob("*.md")}
    assert written == snapshotted, (
        f"targets written with no undo snapshot: {sorted(written - snapshotted)}"
    )

    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), f"colliding entry ids: {sorted(ids)}"
