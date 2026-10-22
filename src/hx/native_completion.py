"""Worker completion intent, runtime checks, shutdown, and atomic unit publication."""
from __future__ import annotations

import json
import uuid

from . import checks, native_launch, native_processes, native_producer, native_shutdown, native_tools, unit_execution
from .continuity_store import Conflict, canonical
from .errors import ValidationError


def request(store, run_id, worker):
    from .application_loop import _state, _save
    scope = 'finish:' + run_id
    with store.transaction() as tx:
        run, task, _ = unit_execution._run(tx, run_id)
        from .dependency_packets import require_bound
        require_bound(task)
        if run['worker_id'] != worker:
            raise Conflict('completion belongs to another worker')
        launch = native_launch._row(tx, run_id)
        if not launch or launch['status'] != 'submitted':
            raise Conflict('completion requires the active submitted native assignment')
        previous = tx.db.execute('SELECT payload FROM runtime_cycles WHERE scope=?', (scope,)).fetchone()
        if previous:
            value = json.loads(previous[0])
            if value['status'] not in {'failed', 'completed'}:
                return value
        # Source outputs must already be committed before a worker relinquishes execution.
        unit_execution.workspace(task['payload'], clean=True)
        # Granularity gate: the unit's whole committed change must be one
        # behaviour unit by the repo's own norms, or sign-off is refused and
        # the Partner splits it. Prose instructions never achieved this.
        from . import discipline
        from pathlib import Path as _Path
        gate = discipline.granularity_gate(_Path(task['payload']['workdir']))
        if not gate['allowed']:
            raise Conflict(
                f"unit too large to sign off as one behaviour unit: "
                f"{gate['source_modules']} source modules, {gate['changed_lines']} lines; "
                f"{gate['instruction']}")
        request_id = str(uuid.uuid4())
        event = tx.append_event(run_id, 'completion', request_id, 'progress', {'completion_requested': True})
        value = {'status': 'requested', 'run_id': run_id, 'request_id': request_id,
                 'event_id': event['event_id'], 'receipts': {}}
        tx.db.execute('INSERT INTO runtime_cycles VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET payload=excluded.payload', (scope, canonical(value)))
    return value


def admission_closed(tx, run_id):
    row = tx.db.execute('SELECT payload FROM runtime_cycles WHERE scope=?', ('finish:' + run_id,)).fetchone()
    return row is not None and json.loads(row[0])['status'] in {'requested', 'checking', 'stopping', 'publishing', 'completed'}


def active(store):
    return store.db.execute("""SELECT count(*) FROM runtime_cycles WHERE scope LIKE 'finish:%'
        AND json_extract(payload,'$.status') IN ('requested','checking','stopping','publishing')""").fetchone()[0]


def advance_one(store, *, env=None, jobs=None):
    """Advance the next completion intent after the last one visited.

    With `jobs`, an acceptance check runs in the loop's job runner: this call
    submits it and returns `checking`, and a later call takes its receipt.
    """
    from .application_loop import _save, _state
    after = _state(store, 'completion-round').get('after', '')
    found = store.db.execute("""SELECT scope,payload FROM runtime_cycles WHERE scope LIKE 'finish:%'
        AND json_extract(payload,'$.status') IN ('requested','checking','stopping','publishing')
        ORDER BY scope<=?,scope LIMIT 1""", (after,)).fetchone()
    if found is None:
        return None
    scope, intent = found['scope'], json.loads(found['payload'])
    _save(store, 'completion-round', {'after': scope})
    run_id = intent['run_id']
    # Publication and the controller receipt are separate transactions. Recover
    # the accepted completion before requiring a still-active run.
    published = store.db.execute('SELECT payload FROM unit_completions WHERE run_id=?', (run_id,)).fetchone()
    if published:
        result = unit_execution.complete(store, run_id, intent['receipts'], env=env)
        intent.update(status='completed', result=result)
        _save(store, scope, intent)
        return intent
    row = native_launch._row(store, run_id)
    if row is None:
        raise Conflict('completion lost its native launch')
    with store.transaction() as tx:
        run, task, _ = unit_execution._run(tx, run_id)
        if native_tools.pending(tx, row['launch_id']) or tx.db.execute("SELECT 1 FROM native_children WHERE launch_id=? AND status='active' LIMIT 1", (row['launch_id'],)).fetchone():
            return {'status': 'waiting', 'run_id': run_id, 'reason': 'active native tools or children'}
        if intent['status'] == 'requested':
            stop = tx.db.execute("""SELECT 1 FROM events e WHERE e.run_id=? AND e.kind='finish'
                AND e.rowid>(SELECT rowid FROM events WHERE event_id=?)
                AND json_extract(e.payload,'$.observation.data.agent_id') IS NULL
                AND json_extract(e.payload,'$.agent_id') IS NULL LIMIT 1""", (run_id, intent['event_id'])).fetchone()
            if not stop:
                return {'status': 'waiting', 'run_id': run_id, 'reason': 'native turn end'}
        if tx.db.execute("SELECT 1 FROM cursors WHERE run_id=? AND head_seq<>classified_seq LIMIT 1", (run_id,)).fetchone() or tx.db.execute("SELECT 1 FROM events WHERE run_id=? AND disposition='pending' LIMIT 1", (run_id,)).fetchone():
            return {'status': 'waiting', 'run_id': run_id, 'reason': 'companion has pending observations'}
        native_producer.require_drained(tx, run_id)
    if intent['status'] in {'requested', 'checking'}:
        intent['status'] = 'checking'
        _save(store, scope, intent)
        from .dependency_packets import completion_checks
        for check_id in sorted(completion_checks(task['payload'])):
            if check_id in intent['receipts']:
                continue
            request_id = 'finish-' + intent['request_id'] + '-' + check_id
            if jobs is None:
                checked = checks.run_check(store, run_id, check_id, request_id=request_id, env=env)
            else:
                key = ('check', run_id, request_id)
                outcome = jobs.take(key)
                if outcome is None:
                    jobs.submit(key, lambda ledger: checks.run_check(ledger, run_id, check_id, request_id=request_id, env=env), store)
                    outcome = jobs.take(key)
                if outcome is None:
                    return {'status': 'checking', 'run_id': run_id, 'check': check_id}
                checked = outcome.result()
            if not checked['valid']:
                intent.update(status='failed', failed_check=check_id, evidence='hx evidence ' + checked['event_id'])
                # The never-twice question, advisory only (TOOL_SKILL_PLAN step 4).
                note = _repeat_note(store, row, check_id, checked, env)
                if note:
                    intent['learnings'] = note
                _save(store, scope, intent)
                with store.transaction() as tx:
                    tx.enqueue('unit_check_failed', 'finish:' + intent['request_id'], {**intent, 'identity': checked['event_id']})
                # One bounded continuation, never a silent repeated check loop.
                # Persist intent before transport; an uncertain paste is not resent.
                from . import native_controller
                import os
                intent['notification'] = 'sending'
                _save(store, scope, intent)
                native_controller._owned_pane(row)
                child = {**(os.environ if env is None else env), 'HX_TMUX': ' '.join(row['payload']['tmux'])}
                native_controller.goal.paste(row['payload']['session'],
                    f"Acceptance check {check_id} failed. Read {intent['evidence']} --limit 4096, fix this unit, then request completion again."
                    + (f" {intent['learnings']}" if intent.get('learnings') else ''), child)
                intent['notification'] = 'sent'
                _save(store, scope, intent)
                return intent
            intent['receipts'][check_id] = checked['receipt_id']
            _save(store, scope, intent)
            return {'status': 'advancing', 'run_id': run_id, 'check': check_id}
        intent['status'] = 'stopping'
        _save(store, scope, intent)
    if intent['status'] == 'stopping':
        native_shutdown.advance(store, row['payload']['worker_id'], run_id, row['request_id'], env=env)
        row = native_launch._row(store, run_id)
        if row['status'] != 'stopped_unreconciled':
            return {'status': 'advancing', 'run_id': run_id, 'phase': 'stopping'}
        state = row['payload']['shutdown']
        if any(native_processes.current(identity) for identity in state['processes']):
            return {'status': 'waiting', 'run_id': run_id, 'reason': 'observed process remains alive'}
        with store.transaction() as tx:
            native_producer.require_drained(tx, run_id)
            if native_tools.pending(tx, row['launch_id']) or tx.db.execute("SELECT 1 FROM native_children WHERE launch_id=? AND status='active' LIMIT 1", (row['launch_id'],)).fetchone():
                raise Conflict('completion gained unsettled operations during shutdown')
            # This is the managed tool/process scope, not a claim about arbitrary
            # remote work outside the native admission contract.
            current = native_launch._row(tx, run_id)
            if current['status'] != 'stopped_unreconciled':
                raise Conflict('shutdown changed during completion')
            tx._change()
            tx.db.execute("UPDATE native_launches SET status='quiesced' WHERE run_id=? AND status<>'archived'", (run_id,))
        intent['status'] = 'publishing'
        _save(store, scope, intent)
    result = unit_execution.complete(store, run_id, intent['receipts'], env=env)
    intent.update(status='completed', result=result)
    _save(store, scope, intent)
    return intent



def _repeat_note(store, row, check_id, checked, env):
    """`learnings: repeat of <Rule> (score)` for a failed acceptance check, or None.

    The first 2,000 bytes of the check's retained output are the `learn.repeat` signal
    (TOOL_SKILL_PLAN step 4). Advisory: an unreadable receipt, a missing learnings directory
    or anything else simply yields no note, and the failure path is unchanged.
    """
    from . import evidence, learn

    try:
        sliced = evidence.read(store, checked['event_id'], limit=learn.REPEAT_SIGNAL_BYTES)
        if sliced.get('encoding') != 'utf-8':
            return None
        worker = row['payload']['worker_id']
    except Exception:  # noqa: BLE001 — advisory; never part of the verdict
        return None
    return learn.completion_repeat(store.root, worker, sliced['content'], check=check_id, env=env)
