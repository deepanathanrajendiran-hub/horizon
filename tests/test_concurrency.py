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
import time

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


def test_concurrent_checkpoints_do_not_lose_turns(tmp_path, monkeypatch):
    state_dir = tmp_path / "goals"
    state_dir.mkdir()
    sys.path.insert(0, str(BIN))
    import goal
    monkeypatch.setattr(goal.paths, "goals_dir", lambda: state_dir)
    st = goal.create(goal="race", cwd=str(tmp_path), gate_kind="command",
                     gate_value="true", max_turns=100)
    _run_workers(f"""
        import goal
        goal.checkpoint({st['slug']!r}, f"worker {{WORKER}}", "next", "h", "fail", "")
    """, state_dir)
    final = json.loads((state_dir / f"{st['slug']}.json").read_text())
    assert final["used"]["turns"] == N, (
        f"lost turns: used.turns={final['used']['turns']} after {N} checkpoints")
    assert len(final["checkpoints"]) == N, (
        f"lost checkpoints: {len(final['checkpoints'])} recorded of {N}")


def test_concurrent_apply_edits_all_get_a_snapshot(tmp_path):
    """A colliding entry id overwrites another proposal's snapshot, which
    silently destroys the ability to undo it."""
    state_dir = tmp_path / "undo"
    state_dir.mkdir()
    targets = tmp_path / "targets"
    targets.mkdir()

    # override_cap: G2 would otherwise refuse all but the first three, which
    # is the correct behaviour and the subject of its own test. What is under
    # test here is that every edit that DOES apply keeps its own snapshot.
    _run_workers(f"""
        import refine_log
        refine_log.apply_edit("M", [({str(targets)!r} + f"/w{{WORKER}}.md", f"content {{WORKER}}")],
                              f"rationale {{WORKER}}", "evidence", override_cap=True)
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


def test_concurrent_creates_do_not_collide_on_slugs(tmp_path):
    """_unique_slug is check-then-write like every other mutator, and was
    the one left unlocked: eight concurrent creates of one title produced
    five state files, three goals silently losing theirs."""
    state_dir = tmp_path / "goals"
    state_dir.mkdir()

    _run_workers(f"""
        import goal
        goal.create(goal="Fix the login bug", cwd={str(tmp_path)!r},
                    gate_kind="command", gate_value="pytest", max_turns=5)
    """, state_dir)

    files = list(state_dir.glob("*.json"))
    assert len(files) == N, f"{len(files)} state files for {N} goals — slugs collided"
    slugs = {json.loads(f.read_text())["slug"] for f in files}
    assert len(slugs) == N

def test_goal_create_waits_for_process_owned_create_lock(tmp_path):
    sys.path.insert(0, str(BIN))
    import filelock

    if not filelock.state_supported():
        return
    state_dir = tmp_path / "goals"
    state_dir.mkdir()
    script = "\n".join([
        "import sys",
        f"sys.path.insert(0, {str(BIN)!r})",
        "from pathlib import Path",
        "import paths",
        f"paths.goals_dir = lambda: Path({str(state_dir)!r})",
        "import goal",
        "print('ready', flush=True)",
        f"goal.create('shared', {str(tmp_path)!r}, 'command', 'true')",
    ])
    child = None
    try:
        with filelock.goal_locked(state_dir / "CREATE"):
            child = subprocess.Popen(
                [sys.executable, "-u", "-c", script],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            assert child.stdout.readline().strip() == "ready", child.stderr.read()
            time.sleep(0.2)
            assert list(state_dir.glob("*.json")) == []
        stdout, stderr = child.communicate(timeout=5)
        assert child.returncode == 0, (stdout, stderr)
        assert len(list(state_dir.glob("*.json"))) == 1
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate(timeout=5)
