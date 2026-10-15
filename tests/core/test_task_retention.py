import pytest

from hx import passes
from hx.facts import RequiredContextOverflow
from .test_passes import answer, create
from .test_native_companion import planned


def test_compression_erases_old_payload_and_drop_keeps_only_identity(planned):
    store, run, event = planned
    first = passes.prepare(store, run, 'main', prompt_version='test')
    passes.commit(store, first['pass_id'], answer(first, [create(event, text='Obsolete verbose parser state. ' * 20)]))
    with store.transaction() as tx:
        next_event = tx.append_event(run, 'main', 'next', 'tool_result', {'text': 'Parser state simplified.'})
    second = passes.prepare(store, run, 'main', prompt_version='test', record_ids=('F1',))
    operation = create(next_event, text='The parser rejects unknown fields.')
    operation.update(op='compress', expected_version=1)
    passes.commit(store, second['pass_id'], answer(second, [operation]))
    assert store.db.execute("SELECT count(*) FROM records WHERE record_id='F1'").fetchone()[0] == 1
    with store.transaction() as tx:
        tx.append_event(run, 'main', 'drop', 'tool_result', {'text': 'That finding no longer applies.'})
    third = passes.prepare(store, run, 'main', prompt_version='test', record_ids=('F1',))
    passes.commit(store, third['pass_id'], answer(third, [{'op': 'drop', 'record_id': 'F1', 'expected_version': 2, 'reason': 'Superseded application state.'}]))
    with store.transaction() as tx:
        record = tx.record('F1')
    assert record['version'] == 3 and record['validity'] == 'dropped'
    assert record['payload'] == {'schema_version': 1} and record['evidence'] == []


def test_quota_failure_rolls_back_patch_and_event_cursor(planned):
    store, run, event = planned
    body = passes.prepare(store, run, 'main', prompt_version='test')
    with pytest.raises(RequiredContextOverflow, match='compress or drop'):
        passes.commit(store, body['pass_id'], answer(body, [create(event, text='x' * 9000)]))
    assert store.db.execute('SELECT count(*) FROM records').fetchone()[0] == 0
    assert store.db.execute('SELECT classified_seq FROM cursors').fetchone()[0] == 0


def test_closed_task_erases_current_facts_and_semantic_cache(planned):
    from hx import native_companion
    store, run, event = planned
    job = native_companion.prepare(store, 'eng-001', run, 'main', 'closed')
    with store.transaction() as tx:
        tx.put_record('old', expected_version=0, task_id='T', **create(event, 'old')['record'])
        tx.finish_run(run, 'stopped')
        from hx.task_retention import close
        close(tx, 'T')
        record = tx.record('old')
    assert record['validity'] == 'dropped' and record['payload'] == {'schema_version': 1}
    assert store.db.execute('SELECT count(*) FROM retrieval_runs').fetchone()[0] == 0
    assert native_companion.prepare(store, 'eng-001', run, 'main', 'closed')['job_id'] == job['job_id']


def test_closed_raw_events_and_frozen_pass_inputs_are_retired(planned):
    from hx.task_retention import retire_closed
    from hx.evidence import read
    from hx.errors import ValidationError
    store, run, event = planned
    frozen = passes.prepare(store, run, 'main', prompt_version='test')
    passes.commit(store, frozen['pass_id'], answer(frozen, []))
    with store.transaction() as tx:
        tx.finish_run(run, 'completed')
    # Context completion cannot acknowledge the learner's independent cursor.
    assert retire_closed(store)['retired_events'] == 0
    with store.transaction() as tx:
        tx.db.execute('UPDATE learning_cursors SET assessed_seq=100')
    assert retire_closed(store)['retired_events'] > 0
    with pytest.raises(ValidationError, match='retired'):
        read(store, event['event_id'])
    assert not store.db.execute("SELECT 1 FROM artifact_refs WHERE owner_type='pass' AND slot='input'").fetchone()
    # Small result identity remains so committed pass retries are still idempotent.
    assert passes.commit(store, frozen['pass_id'], answer(frozen, []))['pass_id'] == frozen['pass_id']


def test_learning_cursor_retains_only_unassessed_closed_events_and_evidence_artifacts(planned):
    from hx import learning_companion
    from hx.task_retention import retire_closed
    store, run, event = planned
    with store.transaction() as tx:
        second = tx.append_event(run, 'main', 'learning-later', 'tool_result', {'text': 'Another useful learning observation.'})
        tx.finish_run(run, 'completed')
        evidence = tx.put_artifact(b'Frozen learning evidence and provenance.', owner_type='learning', owner_id='L', slot='evidence')
    learning_companion.register(store)
    key = learning_companion.ledger_stream(run, 'main')
    with store.transaction() as tx:
        tx.db.execute('UPDATE learning_cursors SET assessed_seq=? WHERE agent_id=? AND stream_id=?',
                      (event['seq'], 'eng-001', key))
    assert retire_closed(store)['retired_events'] == 1
    assert json_payload(store, event['event_id']) == {'retired': True}
    assert json_payload(store, second['event_id']).get('text') == 'Another useful learning observation.'
    assert (store.artifacts / evidence).read_bytes() == b'Frozen learning evidence and provenance.'


def json_payload(store, event_id):
    import json
    return json.loads(store.db.execute('SELECT payload FROM events WHERE event_id=?', (event_id,)).fetchone()[0])


@pytest.mark.parametrize('historical_status', ['prepared', 'reducing'])
def test_closed_companion_retry_returns_frozen_identity_without_refresh_or_execution(planned, monkeypatch, historical_status):
    from hx import companion_protocol, native_companion, prompt_compiler, sidecar_process
    from hx.continuity_store import Conflict
    store, run, _ = planned
    job = native_companion.prepare(store, 'eng-001', run, 'main', 'historical-request')
    with store.transaction() as tx:
        tx.db.execute('UPDATE companion_jobs SET status=? WHERE job_id=?', (historical_status, job['job_id']))
        tx.finish_run(run, 'completed')
        from hx.task_retention import close
        close(tx, 'T')
    expected = companion_protocol.row(store, job['job_id'])
    monkeypatch.setattr(prompt_compiler, 'build', lambda *a, **kw: pytest.fail('closed requests cannot refresh instructions'))
    monkeypatch.setattr(native_companion, '_reduce', lambda *a, **kw: pytest.fail('closed requests cannot commit a reduction'))
    monkeypatch.setattr(sidecar_process, 'preflight', lambda *a, **kw: pytest.fail('closed requests cannot prepare a model transport'))
    monkeypatch.setattr(sidecar_process, 'execute', lambda *a, **kw: pytest.fail('closed requests cannot spawn a model'))
    assert native_companion.prepare(store, 'eng-001', run, 'main', 'historical-request') == expected
    with pytest.raises(Conflict, match='different inputs'):
        native_companion.prepare(store, 'eng-001', run, 'main', 'historical-request', record_ids=('different-record',))
    with pytest.raises(Conflict, match='active run'):
        native_companion.execute(store, job['job_id'])
    assert companion_protocol.row(store, job['job_id']) == expected
