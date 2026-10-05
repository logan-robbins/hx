"""Checkpointed launch-only tool expansion without losing assignment ownership."""
import json
import os
from pathlib import Path

from . import context_packets, native_controller, native_launch, native_processes, native_producer, native_shutdown, native_tools
from .continuity_store import Conflict, canonical, digest


def workspace(task):
    from . import appmap, unit_execution
    root = Path(task['workdir'])
    state = unit_execution.workspace(task)
    paths = set(os.fsdecode(path) for path in
        (unit_execution._git(root, 'diff', '--name-only', '-z', 'HEAD') +
         unit_execution._git(root, 'ls-files', '--others', '--exclude-standard', '-z')).split(b'\0') if path)
    if len(paths) > 64:
        raise Conflict('restart has more than 64 changed paths; checkpoint a smaller unit')
    state['edits'] = {path: appmap.source_anchor(root, path)['sha256'] if (root / path).is_file() else None for path in sorted(paths)}
    return state


def advance_one(store, *, env=None):
    from .application_loop import _state, _save
    row = store.db.execute("SELECT scope,payload FROM runtime_cycles WHERE scope LIKE 'tools:%' AND json_extract(payload,'$.status')='pending_restart' ORDER BY scope LIMIT 1").fetchone()
    if not row:
        return None
    run_id = row['scope'][6:]
    current = native_launch._row(store, run_id)
    key = 'restart:' + run_id
    intent = _state(store, key)
    if not intent or intent.get('selection') != digest(json.loads(row['payload'])):
        if not current:
            return None
        if current['status'] != 'submitted':
            return None
        native_controller._owned_pane(current)
        child = {**(os.environ if env is None else env), 'HX_TMUX': ' '.join(current['payload']['tmux'])}
        if not native_controller.goal.pane_is_idle(native_controller.goal.capture_pane(current['payload']['session'], child) or ''):
            return None
        try:
            with store.transaction() as tx:
                drained(tx, current)
        except Conflict:
            return None
        packet = context_packets.issue(store, run_id, request_id='tools-' + digest(json.loads(row['payload'])), mode='planned')
        intent = {'old_launch': current['launch_id'], 'selection': digest(json.loads(row['payload'])),
                  'request': 'restart-' + packet['checkpoint_id'], 'phase': 'stopping'}
        _save(store, key, intent)
    if intent['phase'] == 'stopping':
        native_shutdown.advance(store, current['payload']['worker_id'], run_id, current['request_id'], env=env)
        current = native_launch._row(store, run_id)
        if current['status'] != 'stopped_unreconciled':
            return {'status': 'restart_stopping', 'run_id': run_id}
        if any(native_processes.current(p) for p in current['payload']['shutdown']['processes']):
            return None
        try:
            with store.transaction() as tx:
                drained(tx, current)
        except Conflict:
            return None
        with store.transaction() as tx:
            drained(tx, current)
            tx._change()
            tx.db.execute("UPDATE native_launches SET status='archived' WHERE launch_id=?", (current['launch_id'],))
            tx.db.execute("UPDATE capture_sources SET payload=json_set(payload,'$.retired',1) WHERE run_id=? AND json_extract(payload,'$.native_scope.launch_id')=?", (run_id, current['launch_id']))
            for stream in tx.db.execute('SELECT stream_id FROM native_bindings WHERE run_id=? AND session_id=?', (run_id, current['payload'].get('native_session'))).fetchall():
                tx.db.execute('INSERT OR REPLACE INTO runtime_cycles VALUES(?,?)', ('retired-stream:' + run_id + ':' + stream[0], '{}'))
            tx.db.execute('DELETE FROM runtime_cycles WHERE scope=?', ('tokens:' + current['launch_id'],))
            intent['phase'] = 'preparing'
            tx.db.execute('INSERT OR REPLACE INTO runtime_cycles VALUES(?,?)', (key, canonical(intent)))
    if intent['phase'] == 'preparing':
        old = store.db.execute('SELECT payload FROM native_launches WHERE launch_id=?', (intent['old_launch'],)).fetchone()
        worker = json.loads(old[0])['worker_id']
        native_launch.prepare(store, worker, run_id, intent['request'], env=env, resume_from=intent['old_launch'])
        intent['phase'] = 'launching'
        _save(store, key, intent)
    current = native_launch._row(store, run_id)
    result = native_controller.advance(store, current['payload']['worker_id'], run_id, intent['request'], env=env)
    if result['status'] == 'submitted':
        value = json.loads(row['payload'])
        value.update(status='loaded', confirmed=value['selected'])
        _save(store, row['scope'], value)
        # Match the installed selection after acknowledgement too.
        from .store import atomic_write_json
        from .tool_catalog import path
        atomic_write_json(path(native_launch._row(store, run_id)), value)
        intent['phase'] = 'complete'
        _save(store, key, intent)
    return {'status': 'restart_' + result['status'], 'run_id': run_id}


def drained(tx, row):
    native_producer.require_drained(tx, row['run_id'])
    if native_tools.pending(tx, row['launch_id']) or tx.db.execute("SELECT 1 FROM native_children WHERE launch_id=? AND status='active'", (row['launch_id'],)).fetchone():
        raise Conflict('restart waits for active calls and children')
    cursors, pending = context_packets._pending(tx, row['run_id'])
    if pending or any(c['head_seq'] != c['classified_seq'] for c in cursors):
        raise Conflict('restart waits for companion classification')
