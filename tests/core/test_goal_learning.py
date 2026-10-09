import copy
import json

import pytest

from hx import appmap, companion_map, goal_templates, map_learning, map_updates, passes, planning, retrieval
from hx import checks, unit_execution
from hx.continuity_store import Conflict, ContinuityStore
from hx.errors import ValidationError
from .test_appmap import git, mapped, record
from .test_map_updates import active, patch
from .test_companion_map import frozen, response
from .test_checks import definition


def seed_check(active):
    store, repo, _, component, _, _, _, _, _ = active
    recipe = definition("print('compiler contract checked')")
    recipe.update(id='compiler-check', argv=['python3', '-c', "print('compiler contract checked')"], inputs=['compiler.py'])
    node = record('compiler-check', 'check', data={'recipe': recipe})
    source = copy.deepcopy(component)
    source['version'] = 2
    source['edges'].append(dict(kind='checked_by', to=node['id'], status='validated',
        evidence=dict(kind='check_declaration', check_id=node['id'], declaration='The compiler check asserts the assigned compiler behavior.')))
    map_updates.propose(store, repo, patch(active, nodes=[source, node], reads={'compiler': 1, 'bounded-context': 1, node['id']: 0}))


def complete_goal(active):
    store, repo, _, _, _, _, snapshot, runs, _ = active
    seed_check(active)
    with store.transaction() as tx:
        for run in runs:
            tx.finish_run(run, 'stopped')
    draft = goal_templates.draft(store, repo, snapshot,
        dict(plan_id='improve', goal='Preserve Zephyr obligations.', require=['compiler'], recipe='repair'))
    assert draft['ready'], draft['unknowns']
    planning.apply(store, draft['plan'])
    run = unit_execution.assign(store, 'improve.work', 1, 'eng-001')['run_id']
    receipt = checks.run_check(store, run, 'compiler-check')
    done = unit_execution.complete(store, run, {'compiler-check': receipt['receipt_id']})
    return draft, done


def test_observed_new_record_grows_map_in_same_pass(active):
    store, _, _, component, *_ = active
    body = frozen(active)
    discovered = record('compiler-source', 'file', claim='observed', data={'path': 'compiler.py'}, anchors=component['anchors'])
    result = response(active, body, nodes=[discovered], reads={**body['map_scope']['read_versions'], discovered['id']: 0})
    committed = passes.commit(store, body['pass_id'], result)
    assert committed['map_patch']['records']['compiler-source']['version'] == 1
    assert store.db.execute('SELECT max(classified_seq) FROM cursors').fetchone()[0] == body['to_seq']


def test_discovery_requires_observed_source_and_bounds_new_identities(active):
    body = frozen(active)
    node = record('invented', 'file', claim='observed', data={'path': 'elsewhere.py'},
                  anchors=[{'path': 'elsewhere.py', 'symbol': None, 'sha256': 'a' * 64}])
    result = response(active, body, nodes=[node], reads={**body['map_scope']['read_versions'], node['id']: 0})
    with pytest.raises(ValidationError, match='frozen evidence'):
        passes.commit(active[0], body['pass_id'], result)


def test_completed_goal_teaches_next_goal_without_research(active, monkeypatch):
    store, repo, _, _, _, _, snapshot, *_ = active
    draft, done = complete_goal(active)
    assert draft['plan']['tasks'][0]['write_paths'] == ['compiler.py']
    assert draft['commands'][0]['argv'][0] == 'python3'
    assert done['application_findings']
    monkeypatch.setattr(appmap, 'source_anchor', lambda *a, **kw: pytest.fail('current knowledge must not reread source'))
    learned = retrieval.plan_context(store, repo, snapshot, 'Zephyr')
    assert 'compiler' in learned['coverage']['seed_ids']
    assert any(node['kind'] == 'check' for node in learned['nodes'])
    followup = goal_templates.draft(store, repo, snapshot, dict(plan_id='next', goal='Verify Zephyr.', recipe='qa'))
    assert followup['ready'], followup['unknowns']
    assert followup['plan']['tasks'][0]['write_paths'] == []
    assert followup['plan']['tasks'][0]['checks'] == draft['plan']['tasks'][0]['checks']


def test_transfer_and_scoped_export_make_findings_portable(active, tmp_path):
    store, repo, _, _, _, baseline, _, *_ = active
    _, done = complete_goal(active)
    target = tmp_path / 'integration'
    git(repo, 'worktree', 'add', '--detach', str(target), 'HEAD')
    snapshot = map_updates.create_overlay(store, target, baseline)['snapshot']
    imported = map_learning.transfer(store, target, snapshot, done['run_id'])
    assert imported['records']['compiler'] == 2
    assert imported['records']['compiler-check'] == 1
    assert map_learning.transfer(store, target, snapshot, done['run_id']) == {'records': {}}
    assert 'compiler' in retrieval.plan_context(store, target, snapshot, 'Zephyr')['coverage']['seed_ids']
    exported = appmap.export_records(store, target, snapshot, ['compiler', 'compiler-check'])
    assert exported['written'] == 2
    assert appmap.export_records(store, target, snapshot, ['compiler', 'compiler-check'])['written'] == 0
    appmap.check(target)
    git(target, 'add', '.hx/map')
    git(target, 'commit', '-qm', 'Publish current application findings')
    clone = tmp_path / 'clone'
    git(tmp_path, 'clone', '-q', str(target), str(clone))
    with ContinuityStore(tmp_path / 'fresh-ledger') as fresh:
        baseline = appmap.import_baseline(fresh, clone)['snapshot']
        assert 'compiler' in retrieval.plan_context(fresh, clone, baseline, 'Zephyr')['coverage']['seed_ids']


def test_transfer_rejects_uninstalled_sources_without_partial_map(active, tmp_path):
    store, repo, _, _, _, baseline, _, *_ = active
    _, done = complete_goal(active)
    target = tmp_path / 'integration'
    git(repo, 'worktree', 'add', '--detach', str(target), 'HEAD')
    snapshot = map_updates.create_overlay(store, target, baseline)['snapshot']
    (target / 'compiler.py').write_text('class Compiler:\n    def build(self):\n        return 42\n')
    with pytest.raises(Conflict, match='not installed'):
        map_learning.transfer(store, target, snapshot, done['run_id'])
    assert store.db.execute('SELECT version FROM map_heads WHERE snapshot=? AND record_id=?', (snapshot, 'compiler')).fetchone()[0] == 1


def test_generated_replan_preserves_acceptance_and_refreshes_only_changed_units(active):
    from .test_planning import plan, unit
    store, repo, info, component, _, _, snapshot, *_ = active
    task = unit(repo, 'change')
    task['map_inputs'] = [dict(repository=info['repo_id'], snapshot=snapshot, id='compiler', version=1)]
    original = plan(active, [task, unit(repo, 'independent')])
    planning.apply(store, original)
    map_updates.propose(store, repo, patch(active))
    result = goal_templates.replan(store, original['plan_id'])
    assert result['ready']
    assert result['affected_tasks'] == ['change']
    assert result['plan']['constraints'] == original['constraints']
    assert result['plan']['tasks'][0]['acceptance'] == task['acceptance']
    assert result['plan']['tasks'][0]['map_inputs'][0]['version'] == 2
    assert result['changes'][0]['old_version'] == 1
    applied = planning.apply(store, result['plan'])
    assert applied['task_versions'] == {'change': 2, 'independent': 1}
    assert goal_templates.changes(store) == []


def test_missing_knowledge_produces_specific_unknowns_not_invented_checks(active):
    result = goal_templates.draft(active[0], active[1], active[6], dict(plan_id='unknown', goal='Investigate totallymissing.'))
    assert not result['ready']
    assert result['plan']['tasks'][0]['checks'] == {}
    assert result['commands'] == []
    assert result['unknowns']


def test_high_fanout_context_is_bounded_and_preserves_unselected_edges(active):
    from .test_passes import answer
    store, repo, info, component, _, _, snapshot, *_ = active
    neighbors = [record('neighbor-' + str(i)) for i in range(15)]
    hub = record('hub', claim='observed', anchors=component['anchors'], edges=[
        dict(kind='depends_on', to=node['id'], status='validated', evidence={'kind': 'source', 'anchors': [0]})
        for node in neighbors])
    nodes = [hub, *neighbors]
    map_updates.propose(store, repo, patch(active, nodes=nodes, reads={node['id']: 0 for node in nodes}))
    with store.transaction() as tx:
        tx.put_task('hub-task', dict(goal='Improve the hub.', workdir=str(repo), map_inputs=[dict(repository=info['repo_id'], snapshot=snapshot, id='hub', version=1)]), expected_revision=0)
        run = tx.start_run('hub-task', 1, 'eng-hub')
        event = tx.append_event(run, 'main', 'hub-reviewed', 'tool_result', {'path': 'compiler.py'})
    selected_snapshot, ids = companion_map.assignment_selection(store, run)
    assert len(ids) == 8
    body = passes.prepare(store, run, 'main', prompt_version='test', map_snapshot=selected_snapshot, map_record_ids=ids)
    updated = copy.deepcopy(hub)
    updated.update(version=2, summary='The hub coordinates current dependencies.')
    result = answer(body, [])
    result['map_patch'] = dict(schema_version=1, patch_id='pass-' + body['pass_id'], task_id='hub-task', run_id=run,
        snapshot=snapshot, read_versions=body['map_scope']['read_versions'], operations=[{'op': 'put', 'record': updated}], evidence_ids=[event['event_id']])
    passes.commit(store, body['pass_id'], result)
    assert len(appmap.get_record(store, repo, snapshot, 'hub')['record']['edges']) == 15


def test_new_assignment_learns_first_map_entry_from_large_observation(active):
    from hx.events import Event
    from hx.observer import append_observation
    from .test_passes import answer
    store, repo, _, _, _, _, snapshot, runs, _ = active
    (repo / 'new.py').write_text('def discovered():\n    return 1\n')
    with store.transaction() as tx:
        append_observation(tx, run_id=runs[0], stream_id='main', session_id='large',
            event=Event('tool_result', {'tool_input': {'file_path': str(repo / 'new.py')}, 'tool_response': 'x' * 10000}, 'large-read'),
            fallback_identity=('test',))
    chosen, ids = companion_map.assignment_selection(store, runs[0])
    assert chosen == snapshot and ids == ()
    body = passes.prepare(store, runs[0], 'main', prompt_version='test', map_snapshot=chosen, map_record_ids=ids)
    anchor = next(item for item in body['discovery_anchors'] if item['path'] == 'new.py')
    node = record('new-function', 'file', claim='observed', data={'path': 'new.py'}, anchors=[anchor])
    result = answer(body, [])
    result['map_patch'] = dict(schema_version=1, patch_id='pass-' + body['pass_id'], task_id='T0', run_id=runs[0],
        snapshot=snapshot, read_versions={'new-function': 0}, operations=[{'op': 'put', 'record': node}], evidence_ids=[body['events'][-1]['event_id']])
    passes.commit(store, body['pass_id'], result)
    assert appmap.get_record(store, repo, snapshot, 'new-function')['record']['anchors'] == [anchor]


def test_draft_cli_writes_plan_and_returns_small_actionable_result(active, tmp_path, run_hx):
    seed_check(active)
    request, output = tmp_path / 'request.json', tmp_path / 'plan.json'
    request.write_text(json.dumps(dict(plan_id='drafted', goal='Improve compiler behavior.', require=['compiler'])))
    result = run_hx('plan', 'draft', '--repo', str(active[1]), '--snapshot', active[6], '--file', str(request), '--out', str(output), '--root', str(active[0].root))
    assert result.returncode == 0, result.stderr
    assert len(result.stdout) < 1500
    assert json.loads(result.stdout)['ready']
    applied = run_hx('plan', 'apply', '--file', str(output), '--root', str(active[0].root))
    assert applied.returncode == 0, applied.stderr


def test_owner_finishes_mapped_edit_while_other_consumers_require_replan(active):
    from hx import map_refresh
    from .test_planning import plan, unit
    store, repo, info, component, _, _, snapshot, runs, _ = active
    with store.transaction() as tx:
        for run in runs:
            tx.finish_run(run, 'stopped')
    owner, consumer = unit(repo, 'owner', paths=['compiler.py']), unit(repo, 'consumer', paths=['other.py'])
    refs = [dict(repository=info['repo_id'], snapshot=snapshot, id='compiler', version=1)]
    owner['map_inputs'] = copy.deepcopy(refs)
    consumer['map_inputs'] = copy.deepcopy(refs)
    owner['outputs'].append(dict(id='source', version=1, kind='source', paths=['compiler.py']))
    consumer['prerequisites'] = [dict(task_id='owner', output_id='proof', version=1)]
    planning.apply(store, plan(active, [owner, consumer]))
    run = unit_execution.assign(store, 'owner', 1, 'eng-001')['run_id']
    (repo / 'compiler.py').write_text("class Compiler:\n    def build(self):\n        return 'improved context'\n")
    map_refresh.queue_sources(store, repo, snapshot, ['compiler.py'])
    map_refresh.drain(store, repo, snapshot)
    # The edit owner keeps its ability to run commands/checks and repair the map.
    with store.transaction() as tx:
        unit_execution._run(tx, run)
    assert {item['task_id'] for item in goal_templates.changes(store)} == {'consumer'}
    with pytest.raises(Conflict):
        planning.assignment(store, 'consumer')
    current = appmap.get_record(store, repo, snapshot, 'compiler')['record']
    current.update(version=current['version'] + 1, anchors=[appmap.source_anchor(repo, 'compiler.py', 'Compiler.build')])
    with store.transaction() as tx:
        event = tx.append_event(run, 'main', 'repair-map', 'tool_result', {'path': 'compiler.py'})
    map_updates.propose(store, repo, dict(schema_version=1, patch_id='owner-map-repair', task_id='owner', run_id=run,
        snapshot=snapshot, read_versions={'compiler': 2}, operations=[dict(op='put', record=current)], evidence_ids=[event['event_id']]))
    assert {item['task_id'] for item in goal_templates.changes(store)} == {'consumer'}
    git(repo, 'add', 'compiler.py')
    git(repo, 'commit', '-qm', 'Improve compiler')
    map_updates.advance_head(store, repo, info['repo_id'], snapshot)
    map_refresh.drain(store, repo, snapshot)
    checked = checks.run_check(store, run, 'unit')
    completed = unit_execution.complete(store, run, {'unit': checked['receipt_id']})
    assert completed['application_findings']['count'] == 1
    revision = goal_templates.replan(store, 'continuation')
    assert revision['ready'], revision['unknowns']
    assert planning.apply(store, revision['plan'])['task_versions'] == {'owner': 1, 'consumer': 2}
    assert unit_execution.assign(store, 'consumer', 2, 'eng-002')['run_id']


def test_template_keeps_neighboring_consumers_out_of_write_ownership(active):
    store, repo, _, component, _, _, snapshot, *_ = active
    seed_check(active)
    (repo / 'consumer.py').write_text('consume = True\n')
    contract = record('contract', 'interface', data={'contract': 'build() -> str', 'invariants': ['The result is a string.']})
    consumer = record('downstream', claim='observed', anchors=[appmap.source_anchor(repo, 'consumer.py')],
        edges=[dict(kind='consumes', to='contract', status='validated', evidence={'kind': 'source', 'anchors': [0]})])
    source = appmap.get_record(store, repo, snapshot, 'compiler')['record']
    source['version'] += 1
    source['edges'].append(dict(kind='provides', to='contract', status='validated', evidence={'kind': 'source', 'anchors': [0]}))
    map_updates.propose(store, repo, patch(active, patch_id='boundaries', nodes=[source, contract, consumer],
        reads={'compiler': 2, 'contract': 0, 'downstream': 0}))
    draft = goal_templates.draft(store, repo, snapshot, dict(plan_id='focused', goal='Improve compiler behavior.', require=['compiler']))
    assert draft['ready'], draft['unknowns']
    task = draft['plan']['tasks'][0]
    assert task['write_paths'] == ['compiler.py']
    assert 'downstream' not in {ref['id'] for ref in task['map_inputs']}
    assert any(item['id'] == 'downstream' for item in draft['known'])
