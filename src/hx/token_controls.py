"""Token pressure at native hooks, safe reset boundaries, and model output caps."""
from __future__ import annotations
import json
from pathlib import Path

from .continuity_store import Conflict, canonical
from .config_models import load_models
from .config_harness import load_harness


def limits(root, worker):
    config = load_harness(Path(root) / 'config' / worker / 'harness.json', check_cross_file=False)
    model = load_models(Path(root) / 'config/models.json')[config.model]
    output = model.max_output_tokens or min(8192, max(1, (model.window - model.threshold) // 2))
    return {'threshold': max(1, min(model.threshold, model.window - output - min(4096, model.window // 10))),
            'autocompact_window': model.autocompact_window or model.window - output,
            'max_output_tokens': output, 'window': model.window}


def observe(store, row, event, payload):
    from .application_loop import _save, _state
    if any(payload.get(key) for key in ('agent_id', 'agentId', 'subagent_id')):
        return
    key = 'tokens:' + row['launch_id']
    if event == 'context' and payload.get('source') in {'clear', 'compact'}:
        _save(store, key, {'tokens': 0, 'reset': 'observed'})
        return
    count = payload.get('context_tokens')
    if type(count) is not int or count < 0:
        return  # Never reinterpret cumulative spend as current context size.
    bound = limits(row['payload']['capsule'], row['payload']['worker_id'])
    old = _state(store, key)
    value = {'tokens': count, 'threshold': bound['threshold'], 'reset': 'needed' if count >= bound['threshold'] else 'none'}
    if old.get('reset') == 'submitted':
        value['reset'] = 'submitted'
    if old != value:
        _save(store, key, value)


def guard(store, row):
    from .application_loop import _state
    from .native_tools import AdmissionDenied
    state = _state(store, 'tokens:' + row['launch_id'])
    if state.get('reset') in {'needed', 'submitted'}:
        raise AdmissionDenied('context threshold reached; end this turn so the companion checkpoint can be restored in a fresh context')


def reset_one(store, *, env=None):
    from . import application_loop as loop, context_packets, goal, native_controller, native_launch, native_tools
    row = store.db.execute("SELECT scope,payload FROM runtime_cycles WHERE scope LIKE 'tokens:%' AND json_extract(payload,'$.reset')='needed' ORDER BY scope LIMIT 1").fetchone()
    if not row:
        return None
    launch = store.db.execute('SELECT run_id FROM native_launches WHERE launch_id=?', (row['scope'][7:],)).fetchone()
    if not launch:
        return None
    current = native_launch._row(store, launch[0])
    if current['launch_id'] != row['scope'][7:]:
        return None
    if current['status'] != 'submitted' or native_tools.pending(store, current['launch_id']):
        return None
    with store.transaction() as tx:
        if tx.db.execute("SELECT 1 FROM native_children WHERE launch_id=? AND status='active'", (current['launch_id'],)).fetchone():
            return None
        from .native_producer import require_drained
        require_drained(tx, current['run_id'])
        cursors, pending = context_packets._pending(tx, current['run_id'])
        if pending or any(c['head_seq'] != c['classified_seq'] for c in cursors):
            return None
    pane = native_controller._owned_pane(current)
    child = {**(env or {}), 'HX_TMUX': ' '.join(current['payload']['tmux'])}
    visible = goal.capture_pane(current['payload']['session'], child)
    if not visible or not goal.pane_is_idle(visible):
        return None
    context_packets.issue(store, current['run_id'], request_id='threshold-' + current['payload']['checkpoint_id'], mode='planned')
    # Claim before sending; ambiguous delivery must not repeatedly clear state.
    with store.transaction() as tx:
        actual = native_launch._row(tx, current['run_id'])
        state = loop._state(tx, row['scope'])
        if actual['status'] != 'submitted' or actual['payload'].get('pane') != pane or state.get('reset') != 'needed':
            return None
        tx._change()
        tx.db.execute('UPDATE runtime_cycles SET payload=? WHERE scope=?',
                      (canonical({**state, 'reset': 'submitted'}), row['scope']))
    adapter = current['payload']['tool_selection']['adapter']
    slash = (Path(current['payload']['capsule']) / 'adapters' / adapter / 'seam-command').read_text().strip()
    if not slash.startswith('/') or any(char.isspace() for char in slash):
        raise Conflict('invalid native reset command')
    goal.paste(current['payload']['session'], slash, child)
    return {'status': 'reset_submitted', 'run_id': current['run_id']}


def captured_usage(tx, run_id, stream_id, event):
    """Use incremental capture; never rescan the transcript in a hook."""
    if stream_id.startswith('child:'):
        return
    from .native_launch import _row
    row = _row(tx, run_id)
    if not row:
        return
    usage = event.data.get('last_usage') if event.data.get('usage_scope') == 'session_total' else event.usage
    if not usage:
        return
    adapter = row['payload'].get('tool_selection', {}).get('adapter')
    if adapter == 'codex':
        count = usage.get('input_tokens')
    else:
        fields = ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens') if 'input_tokens' in usage else ('input', 'cacheRead', 'cacheWrite')
        count = sum(usage.get(key, 0) for key in fields) if fields[0] in usage else None
    if type(count) is not int or count < 0:
        return
    bound = limits(row['payload']['capsule'], row['payload']['worker_id'])
    key = 'tokens:' + row['launch_id']
    old = tx.db.execute('SELECT payload FROM runtime_cycles WHERE scope=?', (key,)).fetchone()
    old = json.loads(old[0]) if old else {}
    value = {'tokens': count, 'threshold': bound['threshold'], 'reset': 'needed' if count >= bound['threshold'] else 'none'}
    if old.get('reset') == 'submitted':
        value['reset'] = 'submitted'
    tx._change()
    tx.db.execute('INSERT OR REPLACE INTO runtime_cycles VALUES(?,?)', (key, canonical(value)))
