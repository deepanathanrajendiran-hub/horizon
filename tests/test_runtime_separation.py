"""Integration regressions for the separate Codex and Claude skill runtimes."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.claude/harness/bin'))
import goal
import gate
import paths
import refine_log
import tree_hash


@pytest.fixture
def project(tmp_path, monkeypatch):
    root = tmp_path / 'project with a trailing space '
    root.mkdir()
    (root / '.claude').mkdir()
    monkeypatch.chdir(root)
    monkeypatch.delenv('CONTINUAL_HARNESS_RUNTIME', raising=False)
    return root


def test_checkpoint_alone_does_not_rerun_gate(project):
    state = goal.create('check', str(project), 'command', 'false')
    result, output, digest = gate.run_gate_at(state)
    state = goal.checkpoint(state['slug'], 'checked', 'fix', digest, result, output)
    assert gate.run_gate_at(state)[0] == 'skipped'


def test_completion_refuses_changed_artifact(project):
    artifact = project / 'ready.txt'
    artifact.write_text('ready')
    state = goal.create('check', str(project), 'command', 'test -f ready.txt')
    result, output, digest = gate.run_gate_at(state)
    goal.checkpoint(state['slug'], 'ready', 'finish', digest, result, output)
    artifact.unlink()
    with pytest.raises(goal.GateNotPassed):
        goal.complete(state['slug'], output)
    assert goal.load(state['slug'])['status'] == 'active'


@pytest.mark.parametrize('relative', [
    '.agents/skills/refine/SKILL.md', '.agents/skills/horizon/SKILL.md',
    '.codex/config.toml', '.codex/harness/undo/0001.json',
])
def test_codex_harness_paths_are_protected(project, relative):
    with pytest.raises(refine_log.ProtectedTarget):
        refine_log._assert_allowed(project / relative)


def test_runtime_state_is_separate(project, monkeypatch):
    claude = paths.harness_root()
    monkeypatch.setenv('CONTINUAL_HARNESS_RUNTIME', 'codex')
    codex = paths.harness_root()
    assert claude == project / '.claude/harness'
    assert codex == project / '.codex/harness'
    assert claude != codex


@pytest.mark.parametrize('runtime,folder', [('codex', '.agents'), ('claude', '.claude')])
@pytest.mark.parametrize('skill', ['horizon', 'refine'])
def test_skill_entrypoint_starts_in_fresh_process(project, runtime, folder, skill):
    entry = ROOT / folder / 'skills' / skill / 'scripts/harness.py'
    result = subprocess.run([sys.executable, str(entry), '--runtime', runtime, 'doctor'],
                            cwd=project, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report['runtime'] == runtime
    assert Path(report['project']) == project


def test_required_review_blocks_command_only_completion(project):
    state = goal.create('review task', str(project), 'command', 'true',
                        review_criterion='The solution satisfies the user requirements.')
    result, output, digest = gate.run_gate_at(state)
    goal.checkpoint(state['slug'], 'implemented', 'review', digest, result, output)
    with pytest.raises(goal.GateNotPassed):
        goal.complete(state['slug'], output)

def test_content_change_with_preserved_metadata_is_detected(project):
    artifact = project / 'result.txt'
    artifact.write_text('good')
    before = tree_hash.compute(project)
    stat = artifact.stat()
    artifact.write_text('evil')
    os.utime(artifact, ns=(stat.st_atime_ns, stat.st_mtime_ns))
    assert tree_hash.compute(project) != before


def test_bookkeeping_is_excluded_in_git_repository(project):
    subprocess.run(['git', 'init', '-q'], cwd=project, check=True)
    state = goal.create('check', str(project), 'command', 'false')
    result, output, digest = gate.run_gate_at(state)
    state = goal.checkpoint(state['slug'], 'checked', 'fix', digest, result, output)
    assert gate.run_gate_at(state)[0] == 'skipped'
    code = project / 'harness'
    code.mkdir()
    (code / 'helper.py').write_text('VALUE = 1')
    assert gate.run_gate_at(state)[0] == 'fail'

REVIEW = 'Checked the requested behavior against the primary artifacts and acceptance criteria.'


def _review_goal(project):
    state = goal.create('review task', str(project), 'command', 'true',
                        review_criterion='The requested behavior is implemented.')
    result, output, digest = gate.run_gate_at(state)
    state = goal.checkpoint(state['slug'], 'implemented', 'review', digest, result, output)
    return state, digest


def test_current_independent_review_allows_completion(project):
    state, digest = _review_goal(project)
    goal.record_review(state['slug'], 'pass', REVIEW, 'fresh-judge-1', digest)
    assert goal.complete(state['slug'], '')['status'] == 'complete'


def test_failed_review_blocks_completion(project):
    state, digest = _review_goal(project)
    goal.record_review(state['slug'], 'fail', REVIEW, 'fresh-judge-1', digest)
    with pytest.raises(goal.GateNotPassed):
        goal.complete(state['slug'], '')


def test_review_refuses_changed_snapshot(project):
    state, digest = _review_goal(project)
    (project / 'changed.txt').write_text('new work')
    with pytest.raises(goal.GateNotPassed):
        goal.record_review(state['slug'], 'pass', REVIEW, 'fresh-judge-1', digest)


def test_old_review_does_not_cover_another_turn(project):
    state, digest = _review_goal(project)
    goal.record_review(state['slug'], 'pass', REVIEW, 'fresh-judge-1', digest)
    goal.checkpoint(state['slug'], 'new attempt', 'review', digest, 'pass', '')
    with pytest.raises(goal.GateNotPassed):
        goal.complete(state['slug'], '')


def test_changed_gate_definition_invalidates_pass(project):
    state = goal.create('check', str(project), 'command', 'true')
    result, output, digest = gate.run_gate_at(state)
    state = goal.checkpoint(state['slug'], 'done', 'finish', digest, result, output)
    state['gate']['value'] = 'false'
    goal._save(state)
    with pytest.raises(goal.GateNotPassed):
        goal.complete(state['slug'], output)

def test_refinement_outcomes_are_recorded_without_changing_the_lesson(project):
    entry = refine_log.apply_edit('M', [(project / 'lesson.md', 'Quote paths with spaces.')],
                                  'Observed a quoting failure', 'The unquoted command failed.')
    before = (project / 'lesson.md').read_text()
    result = refine_log.record_outcome(entry['id'], 'helpful', 'task-2',
                                      'The follow-up task used the lesson and the command succeeded.')
    assert result['outcomes'][-1]['outcome'] == 'helpful'
    assert result['outcomes'][-1]['task'] == 'task-2'
    assert (project / 'lesson.md').read_text() == before
    assert refine_log.count_recent_edits() == 1
    refine_log.undo()
    assert not (project / 'lesson.md').exists()


def _cli(project, runtime, *args):
    return subprocess.run([sys.executable, str(ROOT / 'harness/cli.py'),
                           '--runtime', runtime, *args], cwd=project,
                          capture_output=True, text=True)


@pytest.mark.parametrize('runtime', ['codex', 'claude'])
def test_cli_command_review_completion_flow(project, runtime):
    started = _cli(project, runtime, 'start', 'Check artifact', '--gate', 'test -f ready.txt',
                   '--review', 'The ready file contains the requested ready marker.')
    assert started.returncode == 0, started.stderr
    slug = json.loads(started.stdout)['slug']
    (project / 'ready.txt').write_text('ready')
    checked = _cli(project, runtime, 'check', slug, '--did', 'Created artifact', '--next', 'Review')
    assert checked.returncode == 0, checked.stderr
    cp = json.loads(checked.stdout)['last_checkpoint']
    assert _cli(project, runtime, 'complete', slug).returncode != 0
    evidence = project.parent / 'review.txt'
    evidence.write_text(REVIEW)
    reviewed = _cli(project, runtime, 'review', slug, '--result', 'pass', '--judge', 'judge-1',
                    '--hash', cp['tree_hash'], '--evidence-file', str(evidence))
    assert reviewed.returncode == 0, reviewed.stderr
    done = _cli(project, runtime, 'complete', slug)
    assert done.returncode == 0, done.stderr
    assert json.loads(done.stdout)['status'] == 'complete'


@pytest.mark.parametrize('runtime', ['codex', 'claude'])
def test_cli_failures_are_recorded_and_budget_stops_work(project, runtime):
    started = _cli(project, runtime, 'start', 'Fail honestly', '--gate', 'false', '--max-turns', '1')
    assert started.returncode == 0, started.stderr
    slug = json.loads(started.stdout)['slug']
    checked = _cli(project, runtime, 'check', slug, '--did', 'Attempted fix', '--next', 'More work')
    assert checked.returncode == 1
    state = json.loads(checked.stdout)
    assert state['last_checkpoint']['gate'] == 'fail'
    assert state['budget_exhausted'] is True
    assert _cli(project, runtime, 'check', slug, '--did', 'Another', '--next', 'More').returncode == 2
    assert _cli(project, runtime, 'complete', slug).returncode == 2

def test_history_includes_recorded_outcomes(project):
    entry = refine_log.apply_edit('M', [(project / 'lesson.md', 'Quote paths.')], 'reason', 'evidence')
    refine_log.record_outcome(entry['id'], 'helpful', 'task-2', 'The command succeeded.')
    result = _cli(project, 'claude', 'refine-history')
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['entries'][0]['outcomes'][0]['outcome'] == 'helpful'


def test_custom_codex_home_is_protected(project, monkeypatch):
    home = project.parent / 'custom-codex-home'
    monkeypatch.setenv('CODEX_HOME', str(home))
    monkeypatch.setenv('CONTINUAL_HARNESS_RUNTIME', 'codex')
    for relative in ('config.toml', 'harness/undo/0001.json', 'harness/bin/goal.py'):
        with pytest.raises(refine_log.ProtectedTarget):
            refine_log._assert_allowed(home / relative)


def test_shared_source_case_alias_is_protected():
    alias = ROOT / 'HARNESS' / 'goal.py'
    if not alias.exists() or not alias.samefile(ROOT / 'harness/goal.py'):
        pytest.skip('Filesystem is case-sensitive')
    with pytest.raises(refine_log.ProtectedTarget):
        refine_log._assert_allowed(alias)


def test_fingerprint_refuses_a_fifo_without_blocking(project):
    os.mkfifo(project / 'pipe')
    code = ("import sys; sys.path.insert(0, " + repr(str(ROOT / 'harness')) + "); "
            "import tree_hash; tree_hash.compute('.')")
    result = subprocess.run([sys.executable, '-c', code], cwd=project,
                            capture_output=True, text=True, timeout=2)
    assert result.returncode != 0
    assert 'special file' in result.stderr.lower()


def test_inflight_checkpoint_survives_expired_wall_clock(project):
    state = goal.create('slow task', str(project), 'command', 'false', max_minutes=1)
    state['used']['started'] = '2000-01-01T00:00:00+00:00'
    goal._save(state)
    checked = _cli(project, 'claude', 'check', state['slug'], '--did', 'Finished increment',
                   '--next', 'Another increment would need more budget')
    assert checked.returncode == 1, checked.stderr
    recorded = json.loads(checked.stdout)
    assert recorded['used']['turns'] == 1
    assert recorded['budget_exhausted'] is True
