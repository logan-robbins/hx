import json

import pytest

from hx import application_loop as loop, companion_map, companion_protocol, native_companion, passes
from .test_native_companion import planned
from .test_passes import answer, create
from .test_appmap import mapped
from .test_map_updates import active


@pytest.fixture(autouse=True)
def no_automatic_learning_model(monkeypatch):
    from hx import learning_companion
    # Existing context scheduling tests exercise their own paid lane only.
    monkeypatch.setattr(learning_companion, 'tick', lambda *a, **kw: {'status': 'idle'})
    monkeypatch.setattr(learning_companion, 'reconcile', lambda *a, **kw: None)


def test_two_companions_alternate_without_sharing_cursors(planned, monkeypatch):
    from hx import learning_companion
    store, run, _ = planned
    calls = []
    monkeypatch.setattr(loop, '_context_tick', lambda *a, **kw: calls.append('context') or {'status': 'committed'})
    monkeypatch.setattr(learning_companion, 'tick', lambda *a, **kw: calls.append('learning') or {'status': 'committed'})
    for expected in ('context', 'learning', 'context', 'learning'):
        assert loop.tick(store)['companion_kind'] == expected
    assert calls == ['context', 'learning', 'context', 'learning']
    assert store.db.execute('SELECT classified_seq FROM cursors WHERE run_id=?', (run,)).fetchone()[0] == 0
    assert store.db.execute('SELECT assessed_seq FROM learning_cursors').fetchone()[0] == 0


def test_context_failure_does_not_stop_independent_learner(planned, monkeypatch):
    from hx import learning_companion
    store, _, _ = planned
    def failed(*a, **kw):
        raise loop.Conflict('Context evidence is unresolved.')
    monkeypatch.setattr(loop, '_context_tick', failed)
    seen = []
    monkeypatch.setattr(learning_companion, 'tick', lambda *a, **kw: seen.append(1) or {'status': 'committed'})
    assert loop.tick(store)['status'] == 'unresolved'
    result = loop.tick(store)
    assert result['companion_kind'] == 'learning' and result['status'] == 'committed'
    assert seen == [1]


def test_learning_failure_does_not_stop_context_or_repeat_paid_loop(planned, monkeypatch):
    from hx import learning_companion
    store, _, _ = planned
    loop._save(store, 'companion-turn', {'next': 'learning'})
    calls = []
    def unavailable(*a, **kw):
        calls.append('learning')
        raise loop.Conflict('Learning assessment is unresolved.')
    monkeypatch.setattr(learning_companion, 'tick', unavailable)
    monkeypatch.setattr(loop, '_context_tick', lambda *a, **kw: calls.append('context') or {'status': 'committed'})
    assert loop.tick(store)['status'] == 'unresolved'
    assert loop.tick(store)['status'] == 'committed'
    assert calls == ['learning', 'context']


def test_pending_learning_does_not_delay_completion(planned, monkeypatch):
    from hx import learning_companion, native_completion
    store, _, _ = planned
    monkeypatch.setattr(native_completion, 'advance_one', lambda *a, **kw: {'status': 'completed'})
    monkeypatch.setattr(learning_companion, 'tick', lambda *a, **kw: pytest.fail('Completion has priority.'))
    assert loop.tick(store)['status'] == 'completed'


def test_automatic_delta_pass_commits_once_and_leaves_late_events(planned, monkeypatch):
    store, run, event = planned
    calls = []
    def execute(ledger, job_id, **kwargs):
        calls.append(job_id)
        job = companion_protocol.row(ledger, job_id)
        body = companion_protocol.frozen(ledger, job)
        with ledger.transaction() as tx:
            tx.append_event(run, 'main', 'late', 'tool_result', {'text': 'New result after snapshot.'})
        passes.commit(ledger, body['pass_id'], answer(body, [create(event)] if len(calls) == 1 else []))
        ledger.db.execute("UPDATE companion_jobs SET status='committed' WHERE job_id=?", (job_id,))
        return {'status': 'committed', 'job_id': job_id}
    monkeypatch.setattr(native_companion, 'execute', execute)
    assert loop.tick(store)['status'] == 'committed'
    assert len(calls) == 1
    assert store.db.execute("SELECT disposition FROM events ORDER BY seq DESC LIMIT 1").fetchone()[0] is None
    # Fair scheduling returns to this stream on the next round.
    loop.tick(store)
    assert loop.tick(store)['status'] == 'committed'
    assert len(calls) == 2


def test_failed_semantic_call_is_not_retried_every_tick(planned, monkeypatch):
    from hx import jev
    store, run, _ = planned
    calls = []
    def unavailable(*args, **kwargs):
        calls.append(1)
        raise jev.Unavailable('timeout')
    monkeypatch.setattr(jev, 'evaluate', unavailable)
    assert loop.tick(store)['status'] == 'unresolved'
    for _ in range(4):
        loop.tick(store)
    assert len(calls) == 1
    assert store.db.execute('SELECT classified_seq FROM cursors').fetchone()[0] == 0
    with store.transaction() as tx:
        tx.append_event(run, 'main', 'new', 'tool_result', {'text': 'Another observation.'})
    for _ in range(2):
        loop.tick(store)
    assert len(calls) == 2


def test_indexed_map_watch_invalidates_after_change_without_tree_search(active, monkeypatch):
    from pathlib import Path
    store, repo, info, _, _, _, overlay, *_ = active
    with store.transaction() as tx:
        task = tx.task('T0')
        tx.put_task('T0', {**task['payload'], 'map_inputs': [{'repository': info['repo_id'], 'snapshot': overlay, 'id': 'compiler', 'version': 1}]}, expected_revision=1)
        tx.db.execute("UPDATE runs SET task_revision=2 WHERE task_id='T0'")
    monkeypatch.setattr(Path, 'rglob', lambda *a, **kw: pytest.fail('no repository scan'))
    loop.watch_map(store)
    assert store.db.execute("SELECT applicability FROM map_records JOIN map_heads USING(repository,snapshot,record_id,version) WHERE snapshot=? AND record_id='compiler'", (overlay,)).fetchone()[0] == 'current'
    (repo / 'compiler.py').write_text('def changed():\n    return False\n')
    loop.watch_map(store)
    loop.watch_map(store)
    assert store.db.execute("SELECT applicability FROM map_records JOIN map_heads USING(repository,snapshot,record_id,version) WHERE snapshot=? AND record_id='compiler'", (overlay,)).fetchone()[0] == 'stale'


def test_assignment_map_scope_is_selected_without_manual_ids(active):
    store, _, info, _, _, _, overlay, runs, _ = active
    with store.transaction() as tx:
        task = tx.task('T0')
        body = {**task['payload'], 'map_inputs': [{'repository': info['repo_id'], 'snapshot': overlay, 'id': 'compiler', 'version': 1}]}
        tx.put_task('T0', body, expected_revision=1)
        tx.db.execute('UPDATE runs SET task_revision=2 WHERE run_id=?', (runs[0],))
    snapshot, ids = companion_map.assignment_selection(store, runs[0])
    assert snapshot == overlay and 'compiler' in ids


def test_live_companion_process_keeps_slot(planned, monkeypatch):
    store, run, _ = planned
    job = native_companion.prepare(store, 'eng-001', run, 'main', 'test')
    with store.transaction() as tx:
        tx.db.execute("UPDATE companion_jobs SET status='running' WHERE job_id=?", (job['job_id'],))
        companion_protocol.update(tx, companion_protocol.row(tx, job['job_id']), process={'identity': {'pid': 123}})
    monkeypatch.setattr(loop.native_processes, 'current', lambda identity: {'identity': identity})
    assert loop.reconcile_companion(store)['status'] == 'running'
    monkeypatch.setattr(loop.native_processes, 'current', lambda identity: None)
    monkeypatch.setattr(loop.native_processes, 'probe', lambda pid: None)
    assert loop.reconcile_companion(store)['status'] == 'unresolved'
    assert store.db.execute('SELECT classified_seq FROM cursors').fetchone()[0] == 0


def test_pending_only_pass_is_not_recovered_as_accepted(planned, monkeypatch):
    store, run, _ = planned
    job = native_companion.prepare(store, 'eng-001', run, 'main', 'interrupted')
    body = companion_protocol.frozen(store, job)
    response = answer(body, [])
    response['dispositions'] = {event['event_id']: 'pending' for event in body['events']}
    passes.commit(store, body['pass_id'], response)
    with store.transaction() as tx:
        tx.db.execute("UPDATE companion_jobs SET status='running' WHERE job_id=?", (job['job_id'],))
        companion_protocol.update(tx, companion_protocol.row(tx, job['job_id']), process={'identity': {'pid': 123}})
    monkeypatch.setattr(loop.native_processes, 'current', lambda identity: None)
    monkeypatch.setattr(loop.native_processes, 'probe', lambda pid: None)
    assert loop.reconcile_companion(store)['status'] == 'unresolved'


def test_loop_cli_advances_empty_root(instance, capsys):
    from hx.cli import main
    assert main(['loop', '--once', '--root', str(instance)]) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'idle'


def test_service_starts_once_and_reuses_live_kernel_identity(instance):
    import os
    import signal
    from hx.continuity_store import ContinuityStore
    from hx import native_processes
    with ContinuityStore(instance) as store:
        with store.transaction() as tx:
            tx.put_task('service-task', {'goal': 'Wait for an assigned event.'}, expected_revision=0)
            tx.start_run('service-task', 1, 'eng-001')
    identity = loop.ensure(instance)
    try:
        assert native_processes.current(identity)
        assert loop.ensure(instance) == identity
        with ContinuityStore(instance) as store:
            assert loop._state(store, 'application-service')['ready'] is True
        with pytest.raises(Exception, match='already running'):
            with loop.service_lock(instance):
                pytest.fail('resident loop lock must exclude manual concurrent ticks')
    finally:
        native_processes.send(identity, signal.SIGKILL)
        os.waitpid(identity['pid'], 0)


def test_partner_status_and_explicit_retry_do_not_invoke_models(planned, monkeypatch, capsys):
    from hx import jev
    store, run, _ = planned
    monkeypatch.setattr(jev, 'evaluate', lambda *a, **kw: pytest.fail('status/retry must not infer'))
    snapshot = loop.status(store)
    assert snapshot['active'][0]['run_id'] == run and snapshot['active'][0]['unclassified'] == 1
    assert loop.main(['--retry', run], store.root, env={'HARNESS_ID': 'partner'}) == 0
    assert json.loads(capsys.readouterr().out)['retry_requested'] == 1
    assert loop._state(store, 'retry:' + run)['generation'] == 1


def test_completion_error_does_not_starve_independent_companion_work(planned, monkeypatch):
    from hx import native_completion
    from hx.continuity_store import Conflict
    store, _, _ = planned
    def broken(*a, **kw):
        raise Conflict('One unit has an unreconciled capture gap.')
    monkeypatch.setattr(native_completion, 'advance_one', broken)
    monkeypatch.setattr(native_companion, 'execute', lambda *a, **kw: {'status': 'committed'})
    assert loop.tick(store)['status'] == 'committed'
    assert 'capture gap' in loop.status(store)['completion_error']['error']


def test_root_service_is_resident_without_worker_reservations_and_recovers_after_exit(tmp_path):
    import os
    import signal
    from hx.continuity_store import ContinuityStore
    identities = []
    try:
        first = loop.ensure(tmp_path)
        identities.append(first)
        with ContinuityStore(tmp_path) as store:
            view = loop.status(store)
            assert view['service']['running'] and view['active'] == []
        assert loop.ensure(tmp_path) == first
        loop.native_processes.send(first, signal.SIGKILL)
        os.waitpid(first['pid'], 0)
        with ContinuityStore(tmp_path) as store:
            assert not loop.status(store)['service']['ready']
        second = loop.ensure(tmp_path)
        identities.append(second)
        assert second != first and loop.native_processes.current(second)
        assert loop.ensure(tmp_path) == second
    finally:
        for identity in identities:
            if loop.native_processes.current(identity):
                loop.native_processes.send(identity, signal.SIGKILL)
                os.waitpid(identity['pid'], 0)


def test_graceful_service_exit_clears_readiness(tmp_path):
    import threading
    from hx.continuity_store import ContinuityStore
    stop = threading.Event()
    stop.set()
    loop.serve(tmp_path, stop=stop)
    with ContinuityStore(tmp_path) as store:
        assert loop._state(store, 'application-service')['ready'] is False
        assert loop.status(store)['service']['running'] is False
