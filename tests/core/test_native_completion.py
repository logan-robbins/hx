import time

import pytest

from hx import native_completion, native_launch, unit_execution
from hx.continuity_store import Conflict
from .test_appmap import mapped
from .test_map_updates import active
from .test_native_tools import runtime, configured, assignment, fleet, submitted, payload, fire


def classified(store, run):
    # Deterministic reducer fixture; companion behavior has its own scoped tests.
    with store.transaction() as tx:
        for row in tx.db.execute('SELECT * FROM cursors WHERE run_id=?', (run,)).fetchall():
            dispositions = {event[0]: 'reduced' for event in tx.db.execute('SELECT seq FROM events WHERE run_id=? AND stream_id=? AND seq>?', (run, row['stream_id'], row['classified_seq']))}
            if dispositions:
                tx.classify(run, row['stream_id'], expected_revision=row['revision'], through=row['head_seq'], dispositions=dispositions)


def test_native_completion_waits_for_turn_checks_shutdown_then_publishes(runtime):
    store, _, run, env = runtime
    row = submitted(runtime)
    intent = native_completion.request(store, run, 'eng-001')
    assert native_completion.request(store, run, 'eng-001') == intent
    assert fire(store, run, row, 'tool-start', payload('after-completion')) == 2
    assert native_completion.advance_one(store, env=env)['reason'] == 'native turn end'
    assert fire(store, run, row, 'stop', {'session_id': row['payload']['native_session']}) == 0
    assert native_completion.advance_one(store, env=env)['reason'] == 'companion has pending observations'
    classified(store, run)
    result = None
    for _ in range(100):
        result = native_completion.advance_one(store, env=env)
        classified(store, run)
        if result and result['status'] == 'completed':
            break
        time.sleep(.01)
    assert result['status'] == 'completed'
    assert result['receipts'].keys() == {'unit'}
    assert native_launch._row(store, run)['status'] == 'quiesced'
    assert store.db.execute('SELECT outcome FROM runs WHERE run_id=?', (run,)).fetchone()[0] == 'completed'
    assert store.db.execute('SELECT count(*) FROM leases WHERE run_id=?', (run,)).fetchone()[0] == 0
    assert store.db.execute('SELECT count(*) FROM unit_outputs').fetchone()[0] > 0
    # Simulate losing the controller acknowledgement after atomic publication.
    from hx.application_loop import _save
    _save(store, 'finish:' + run, {**result, 'status': 'publishing'})
    assert native_completion.advance_one(store, env=env)['result'] == result['result']


def test_pending_tool_prevents_shutdown_and_lease_release(runtime):
    store, _, run, env = runtime
    row = submitted(runtime)
    assert fire(store, run, row, 'tool-start', payload()) == 0
    native_completion.request(store, run, 'eng-001')
    assert fire(store, run, row, 'stop', {'session_id': row['payload']['native_session']}) == 0
    classified(store, run)
    assert native_completion.advance_one(store, env=env)['reason'] == 'active native tools or children'
    assert native_launch._row(store, run)['status'] == 'submitted'
    assert store.db.execute('SELECT count(*) FROM leases WHERE run_id=?', (run,)).fetchone()[0] > 0


def test_failed_check_notifies_once_and_reopens_admission(runtime, monkeypatch):
    from hx import native_controller
    store, _, run, env = runtime
    row = submitted(runtime)
    native_completion.request(store, run, 'eng-001')
    fire(store, run, row, 'stop', {'session_id': row['payload']['native_session']})
    classified(store, run)
    calls = []
    monkeypatch.setattr(native_completion.checks, 'run_check', lambda *a, **kw: {'valid': False, 'event_id': 'failed-receipt'})
    monkeypatch.setattr(native_controller.goal, 'paste', lambda *a: calls.append(a))
    result = native_completion.advance_one(store, env=env)
    assert result['status'] == 'failed' and result['notification'] == 'sent'
    assert len(calls) == 1 and 'failed-receipt' in calls[0][1]
    assert not native_completion.admission_closed(store, run)
    assert native_completion.advance_one(store, env=env) is None
    assert len(calls) == 1


def test_publication_rechecks_late_evidence_after_shutdown(runtime, monkeypatch):
    store, _, run, env = runtime
    row = submitted(runtime)
    native_completion.request(store, run, 'eng-001')
    fire(store, run, row, 'stop', {'session_id': row['payload']['native_session']})
    classified(store, run)
    original = unit_execution.complete
    injected = []
    def late(ledger, run_id, receipts, **kw):
        if not injected:
            with ledger.transaction() as tx:
                injected.append(tx.append_event(run, 'main', 'late-diagnostic', 'tool_result', {'error': 'Late shutdown diagnostic.'}))
        return original(ledger, run_id, receipts, **kw)
    monkeypatch.setattr(unit_execution, 'complete', late)
    with pytest.raises(Conflict, match='late native evidence'):
        for _ in range(100):
            native_completion.advance_one(store, env=env)
            classified(store, run)
            time.sleep(.01)
    assert store.db.execute('SELECT ended_at FROM runs WHERE run_id=?', (run,)).fetchone()[0] is None
    assert store.db.execute('SELECT count(*) FROM leases WHERE run_id=?', (run,)).fetchone()[0] > 0
    assert store.db.execute('SELECT count(*) FROM unit_completions WHERE run_id=?', (run,)).fetchone()[0] == 0
    classified(store, run)
    assert native_completion.advance_one(store, env=env)['status'] == 'completed'


def test_tool_expansion_restarts_same_assignment_and_rejects_old_hooks(runtime):
    from hx import native_restart, tool_catalog, hook_contract
    store, _, run, env = runtime
    old = submitted(runtime)
    classified(store, run)
    tool_catalog.load(store, run, ['WebFetch'], env={})
    result = None
    for _ in range(150):
        result = native_restart.advance_one(store, env=env)
        classified(store, run)
        if result and result['status'] == 'restart_submitted':
            break
        time.sleep(.02)
    assert result and result['status'] == 'restart_submitted', result
    current = native_launch._row(store, run)
    assert current['launch_id'] != old['launch_id']
    assert current['payload']['resume_from'] == old['launch_id']
    assert tool_catalog.selection(store, current)['status'] == 'loaded'
    assert 'WebFetch' in tool_catalog.selection(store, current)['confirmed']
    assert store.db.execute('SELECT count(*) FROM runs WHERE ended_at IS NULL').fetchone()[0] == 1
    assert store.db.execute('SELECT count(*) FROM leases WHERE run_id=?', (run,)).fetchone()[0]
    with pytest.raises(Conflict, match='retired'):
        hook_contract.validate(store, run_id=run, launch_id=old['launch_id'], adapter='claude', worker_id='eng-001')
