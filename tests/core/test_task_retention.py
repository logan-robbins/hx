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
    assert retire_closed(store)['retired_events'] > 0
    with pytest.raises(ValidationError, match='retired'):
        read(store, event['event_id'])
    assert not store.db.execute("SELECT 1 FROM artifact_refs WHERE owner_type='pass' AND slot='input'").fetchone()
    # Small result identity remains so committed pass retries are still idempotent.
    assert passes.commit(store, frozen['pass_id'], answer(frozen, []))['pass_id'] == frozen['pass_id']
