import sys
from pathlib import Path

import pytest

BIN = Path(__file__).resolve().parents[1] / "harness"
sys.path.insert(0, str(BIN))

import evidence  # noqa: E402
import goal  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_goals(tmp_path, monkeypatch):
    d = tmp_path / "goals"
    d.mkdir()
    (tmp_path / "project").mkdir()
    monkeypatch.setattr(goal.paths, "goals_dir", lambda: d)
    return d


def _project():
    return goal.paths.goals_dir().parent / "project"


def _make(gate_value="true", **kw):
    args = dict(goal="Fix the parser", cwd=str(_project()), gate_kind="command",
                gate_value=gate_value, max_turns=3)
    args.update(kw)
    return goal.create(**args)


def test_manifest_hashes_test_files_only():
    p = _project()
    (p / "tests").mkdir()
    (p / "tests" / "test_a.py").write_text("def test_a(): pass\n")
    (p / "src.py").write_text("x = 1\n")
    (p / "lib_test.go").write_text("package lib\n")
    manifest = evidence.test_manifest(str(p), [str(p)])
    assert set(manifest) == {"tests/test_a.py", "lib_test.go"}
    assert all(len(v) == 64 for v in manifest.values())


def test_manifest_skips_harness_state_and_excluded_dirs():
    p = _project()
    (p / ".claude" / "harness" / "gates" / "g").mkdir(parents=True)
    (p / ".claude" / "harness" / "gates" / "g" / "test_gate.py").write_text("x\n")
    (p / "node_modules" / "m").mkdir(parents=True)
    (p / "node_modules" / "m" / "test_x.py").write_text("x\n")
    assert evidence.test_manifest(str(p), [str(p)]) == {}


def test_observe_records_baseline_without_charging_a_turn():
    state = _make()
    saved = goal.observe(state["slug"], "baseline")
    obs = saved["observations"]
    assert [o["role"] for o in obs] == ["baseline"]
    assert obs[0]["gate"] == "pass" and obs[0]["kind"] == "observation"
    assert obs[0]["gate_spec"] == saved["gate"]
    assert saved["used"]["turns"] == 0 and saved["checkpoints"] == []
    assert saved["test_manifest"] == {}


def test_observe_runs_even_when_tree_unchanged():
    state = _make()
    goal.observe(state["slug"], "baseline")
    saved = goal.observe(state["slug"], "resume")
    assert [o["gate"] for o in saved["observations"]] == ["pass", "pass"]


def test_first_manifest_is_kept():
    p = _project()
    (p / "test_a.py").write_text("one\n")
    state = _make()
    first = goal.observe(state["slug"], "baseline")["test_manifest"]["test_a.py"]
    (p / "test_a.py").write_text("two\n")
    saved = goal.observe(state["slug"], "resume")
    assert saved["test_manifest"]["test_a.py"] == first


def test_observe_in_state_mode_is_allowed_when_idle_and_bumps_revision(make_state_goal):
    state = make_state_goal()
    before = state["execution"]["revision"]
    saved = goal.observe(state["slug"], "baseline")
    assert saved["execution"]["revision"] == before + 1
    assert saved["checkpoints"] == []


def test_observe_refuses_coverage_gates():
    state = _make(gate_kind="coverage", gate_value="The parser rejects empty input")
    with pytest.raises(goal.GateNotPassed):
        goal.observe(state["slug"], "baseline")


def test_observe_rejects_unknown_role():
    state = _make()
    with pytest.raises(ValueError):
        goal.observe(state["slug"], "whenever")
