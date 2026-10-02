from __future__ import annotations

import copy
import json

import pytest

from hx import appmap, companion_map, map_updates, passes
from hx.continuity_store import Conflict, canonical
from hx.errors import ValidationError
from hx.facts import RequiredContextOverflow
from .test_appmap import mapped, record, git
from .test_map_updates import active, patch, selected
from .test_passes import answer, create


def frozen(active, *, index=0, ids=('compiler', 'bounded-context'), **kwargs):
    store, _, _, _, _, _, overlay, runs, _ = active
    return passes.prepare(store, runs[index], 'main', prompt_version='test', map_snapshot=overlay,
                          map_record_ids=ids, **kwargs)


def response(active, body, *, index=0, operations=None, **kwargs):
    result = answer(body, operations or [create({'event_id': active[-1][index]}, identifier='F'+str(index))])
    result['map_patch'] = patch(active, index=index, patch_id='pass-'+body['pass_id'], **kwargs)
    return result


def test_map_facts_and_cursor_commit_together_and_replay_without_source_reads(active, monkeypatch):
    store, repo, _, _, _, _, _, runs, _ = active
    body = frozen(active)
    result = response(active, body)
    with store.transaction() as tx:
        late = tx.append_event(runs[0], 'main', 'late', 'tool_result', {'text': 'Arrived during the pass.'})
    committed = passes.commit(store, body['pass_id'], result)
    assert committed['map_patch']['records']['compiler']['version'] == 2
    assert selected(active)['record']['version'] == 2
    assert store.db.execute("SELECT version FROM record_heads WHERE record_id='F0'").fetchone()[0] == 1
    assert store.db.execute('SELECT disposition FROM events WHERE event_id=?', (late['event_id'],)).fetchone()[0] is None
    assert store.db.execute('SELECT classified_seq FROM cursors WHERE run_id=?', (runs[0],)).fetchone()[0] == 1
    with store.transaction() as tx:
        tx.finish_run(runs[0], 'done')
    (repo / 'compiler.py').unlink()
    monkeypatch.setattr(map_updates, '_preflight', lambda *a: pytest.fail('replay must not hash source'))
    assert passes.commit(store, body['pass_id'], result) == committed
    assert store.db.execute('SELECT count(*) FROM map_patches').fetchone()[0] == 1


def test_invalid_fact_rolls_back_map_records_invalidation_outbox_and_cursor(active):
    store, _, _, _, _, _, _, _, _ = active
    body = frozen(active)
    result = response(active, body)
    result['operations'][0]['record']['evidence'] = ['invented']
    before = store.db.execute('SELECT revision FROM ledger_meta').fetchone()[0]
    outbox = store.db.execute('SELECT count(*) FROM outbox').fetchone()[0]
    with pytest.raises(ValidationError, match='outside'):
        passes.commit(store, body['pass_id'], result)
    assert selected(active)['record']['version'] == 1
    assert store.db.execute('SELECT count(*) FROM records').fetchone()[0] == 0
    assert store.db.execute('SELECT count(*) FROM map_patches').fetchone()[0] == 0
    assert store.db.execute('SELECT count(*) FROM task_replan_queue').fetchone()[0] == 0
    assert store.db.execute('SELECT count(*) FROM outbox').fetchone()[0] == outbox
    assert store.db.execute('SELECT revision FROM ledger_meta').fetchone()[0] == before
    assert store.db.execute('SELECT max(classified_seq) FROM cursors').fetchone()[0] == 0


def test_conflicting_worker_edit_preserves_all_pass_state(active):
    store, repo, *_ = active
    body = frozen(active)
    result = response(active, body)
    other = patch(active, index=1, patch_id='worker-edit')
    other['operations'][0]['record']['summary'] = 'A different responsibility.'
    map_updates.propose(store, repo, other)
    with pytest.raises(map_updates.MapConflict):
        passes.commit(store, body['pass_id'], result)
    assert selected(active)['record']['summary'] == 'A different responsibility.'
    assert store.db.execute('SELECT count(*) FROM records').fetchone()[0] == 0
    assert store.db.execute('SELECT max(classified_seq) FROM cursors').fetchone()[0] == 0


def test_identical_worker_edit_coalesces_with_companion_finding(active):
    store, repo, *_ = active
    body = frozen(active)
    map_updates.propose(store, repo, patch(active, index=1, patch_id='worker-edit'))
    result = passes.commit(store, body['pass_id'], response(active, body))
    assert result['map_patch']['records']['compiler'] == {'version': 2, 'coalesced': True}
    assert store.db.execute('SELECT count(*) FROM records').fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM map_evidence WHERE record_id='compiler' AND version=2").fetchone()[0] == 2


@pytest.mark.parametrize('change', ['new_id', 'changed_read', 'foreign_snapshot', 'late_evidence', 'other_run'])
def test_map_response_cannot_expand_frozen_scope(active, change):
    store, _, _, _, _, _, _, runs, events = active
    body = frozen(active)
    result = response(active, body)
    proposal = result['map_patch']
    if change == 'new_id':
        proposal['read_versions']['unknown'] = 0
        proposal['operations'].append({'op': 'put', 'record': record('unknown')})
    elif change == 'changed_read':
        proposal['read_versions']['bounded-context'] = 2
    elif change == 'foreign_snapshot':
        proposal['snapshot'] = 'different'
    elif change == 'late_evidence':
        with store.transaction() as tx:
            event = tx.append_event(runs[0], 'main', 'late', 'tool_result', {'text': 'not in frozen pass'})
        proposal['evidence_ids'] = [event['event_id']]
    else:
        proposal['run_id'], proposal['task_id'], proposal['evidence_ids'] = runs[1], 'T1', [events[1]]
    with pytest.raises((Conflict, ValidationError)):
        passes.commit(store, body['pass_id'], result)
    assert selected(active)['record']['version'] == 1
    assert store.db.execute('SELECT max(classified_seq) FROM cursors').fetchone()[0] == 0


def test_selected_absent_ids_allow_related_new_map_records(active):
    store, *_ = active
    body = frozen(active, ids=('packets', 'tools'))
    assert body['map_scope']['read_versions'] == {'packets': 0, 'tools': 0}
    nodes = [record('packets', edges=[{'kind': 'depends_on', 'to': 'tools', 'status': 'candidate', 'evidence': {}}]), record('tools')]
    result = passes.commit(store, body['pass_id'], response(active, body, nodes=nodes, reads={'packets': 0, 'tools': 0}))
    assert set(result['map_patch']['records']) == {'packets', 'tools'}


def test_same_pass_fact_rebind_survives_and_old_consumers_are_invalidated(active):
    store, _, info, _, _, _, overlay, _, events = active
    ref = {'repository': info['repo_id'], 'snapshot': overlay, 'id': 'compiler', 'version': 1}
    with store.transaction() as tx:
        for name in ('kept', 'obsolete'):
            fact = create({'event_id': events[0]}, name)['record']
            fact['inputs'] = {'map': [ref]}
            tx.put_record(name, expected_version=0, task_id='T0', **fact)
    body = frozen(active, record_ids=('kept',))
    operation = create({'event_id': events[0]}, 'kept')
    operation.update(op='supersede', expected_version=1)
    operation['record']['inputs'] = {'map': [{**ref, 'version': 2}]}
    passes.commit(store, body['pass_id'], response(active, body, operations=[operation]))
    with store.transaction() as tx:
        assert tx.record('kept')['validity'] == 'current' and tx.record('kept')['version'] == 2
        assert tx.record('obsolete')['validity'] == 'invalid'
    assert store.db.execute("SELECT count(*) FROM task_replan_queue WHERE task_id='T0'").fetchone()[0] == 1


def test_late_source_write_rolls_back_both_map_and_facts(active, monkeypatch):
    store, repo, *_ = active
    body = frozen(active)
    original = passes._write_operation
    def concurrent_write(*args):
        result = original(*args)
        (repo / 'compiler.py').write_text('Changed after map validation.\n')
        return result
    monkeypatch.setattr(passes, '_write_operation', concurrent_write)
    with pytest.raises(Conflict, match='source changed'):
        passes.commit(store, body['pass_id'], response(active, body))
    assert store.db.execute("SELECT max(version) FROM map_records WHERE record_id='compiler'").fetchone()[0] == 1
    assert store.db.execute('SELECT count(*) FROM records').fetchone()[0] == 0
    assert store.db.execute('SELECT max(classified_seq) FROM cursors').fetchone()[0] == 0


def test_map_selection_uses_headers_before_loading_oversized_records(active):
    store, _, info, _, _, _, overlay, *_ = active
    value = record(summary='A' * 20000)
    store.db.execute('UPDATE map_records SET payload=? WHERE repository=? AND snapshot=? AND record_id=?',
                     (canonical(value), info['repo_id'], overlay, 'compiler'))
    factory = store.db.row_factory
    def bounded(cursor, values):
        for column, value in zip(cursor.description, values):
            if column[0] == 'payload' and isinstance(value, str):
                assert len(value) < 10000, 'oversized map body was loaded'
        return factory(cursor, values)
    store.db.row_factory = bounded
    try:
        with pytest.raises(RequiredContextOverflow, match='map records'):
            frozen(active)
    finally:
        store.db.row_factory = factory


def test_no_map_selection_means_no_map_inspection(active, monkeypatch):
    store, _, _, _, _, _, _, runs, _ = active
    monkeypatch.setattr(companion_map, 'freeze', lambda *a, **kw: pytest.fail('unselected map must not load'))
    body = passes.prepare(store, runs[0], 'main', prompt_version='test')
    assert 'map_scope' not in body
    with pytest.raises(ValidationError, match='frozen map selection'):
        passes.commit(store, body['pass_id'], response(active, body))


def test_map_scope_rejects_branch_change_and_changed_version_before_model(active):
    store, repo, *_ = active
    body = frozen(active)
    map_updates.propose(store, repo, patch(active))
    with store.transaction() as tx, pytest.raises(Conflict, match='before execution'):
        companion_map.check_scope(tx, body, versions=True)
    git(repo, 'checkout', '-qb', 'other-branch')
    with store.transaction() as tx, pytest.raises(Conflict, match='worktree'):
        companion_map.check_scope(tx, body)
