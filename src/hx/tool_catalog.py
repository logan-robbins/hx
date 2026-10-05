"""Small task-scoped tool discovery. Native visibility and advice stay distinct."""
from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path

from . import jev_decisions, native_launch, passes
from .config_harness import load_harness
from .continuity_store import ContinuityStore, Conflict, canonical, digest
from .errors import ValidationError
from .store import atomic_write_json

# Only documented built-ins belong here. Provider MCP catalogs are not inferred
# from permissions or copied into the context to decide what to load.
CATALOG = {
    'claude': {'required': ['Bash', 'Read', 'Edit', 'Write', 'Grep'], 'mode': 'launch',
               'optional': {'WebFetch': 'Read a named web page.', 'WebSearch': 'Find current public documentation.',
                            'NotebookEdit': 'Edit a Jupyter notebook.', 'Agent': 'Delegate an explicitly scoped child task.'}},
    'pi': {'required': ['bash', 'read', 'edit', 'write'], 'mode': 'dynamic',
           'optional': {'grep': 'Search within named source paths.', 'find': 'Locate files by a scoped pattern.',
                        'ls': 'List a named directory.', 'hx_subagent': 'Delegate an explicitly scoped child task.'}},
    'grok': {'required': ['run_terminal_cmd', 'read_file', 'search_replace', 'grep'], 'mode': 'advisory',
             'optional': {'web_fetch': 'Read a named web page.', 'web_search': 'Find current public documentation.',
                          'list_dir': 'Inspect a named directory; route large output through hx tool-exec.'}},
    'codex': {'required': ['exec_command', 'apply_patch'], 'mode': 'advisory', 'optional': {}},
    'meta': {'required': [], 'mode': 'advisory', 'optional': {}},
}


def initial(adapter):
    catalog = CATALOG[adapter]
    return {'catalog': digest([adapter, catalog]), 'adapter': adapter, 'mode': catalog['mode'],
            'selected': catalog['required'][:], 'confirmed': [], 'status': 'configured'}


def prepared_selection(store, run_id, adapter):
    from .application_loop import _state
    value = _state(store, 'tools:' + run_id) or initial(adapter)
    if value['catalog'] != initial(adapter)['catalog'] or value['adapter'] != adapter:
        raise Conflict('tool catalog changed before launch')
    return value


def path(row):
    return Path(row['payload']['capsule']) / 'run' / row['payload']['worker_id'] / 'toolset.json'


def scope(store, run_id, env=None):
    child = os.environ if env is None else env
    with store.transaction() as tx:
        run = passes._active_run(tx, run_id)
        if child.get('HARNESS_ID') not in {None, run['worker_id']}:
            raise Conflict('tool selection belongs to another worker')
        adapter = load_harness(store.root / 'config' / run['worker_id'] / 'harness.json', check_cross_file=False).flavor
        row = native_launch._row(tx, run_id)
    return adapter, row


def discover(store, run_id, query, *, env=None):
    if not isinstance(query, str) or not query.strip() or len(query.encode()) > 512:
        raise ValidationError('tool discovery requires a complete next action of at most 512 bytes')
    adapter, row = scope(store, run_id, env)
    catalog = CATALOG[adapter]
    entries = {name: 'Required execution/context capability.' for name in catalog['required']} | catalog['optional']
    if query in entries:
        selected = [query]  # Exact names need no semantic decision.
    elif catalog['optional']:
        questions = {name: {'type': 'noul', 'instructions': f'Would this tool help the stated next action: {purpose}'}
                     for name, purpose in catalog['optional'].items()}
        judged = jev_decisions.decide(store, run_id, 'tool-discovery-v1', {'next_action': query}, questions,
                                      binding=digest(catalog), env=env)
        selected = sorted((name for name in questions if judged['answers'][name]['noul'] >= .65),
                          key=lambda name: (-judged['answers'][name]['noul'], name))[:3]
    else:
        selected = []
    return {'adapter': adapter, 'visibility': catalog['mode'], 'required': catalog['required'],
            'suggested': [{'id': name, 'purpose': entries[name]} for name in selected],
            'note': 'Selection does not invoke tools or authorize actions. Advisory adapters retain their native tool schemas.'}


def selection(store, row):
    from .application_loop import _state
    value = _state(store, 'tools:' + row['run_id'])
    if not value:
        value = row['payload'].get('tool_selection')
    if not value:
        root = getattr(store, 'root', None) or store.store.root
        adapter = load_harness(root / 'config' / row['payload']['worker_id'] / 'harness.json', check_cross_file=False).flavor
        value = initial(adapter)
    return value


def verify(store, row):
    expected = selection(store, row)
    source = path(row)
    if source.stat().st_size > 8192 or json.loads(source.read_text()) != expected:
        raise Conflict('native tool selection changed outside its task loader')
    catalog = CATALOG[expected['adapter']]
    if expected['catalog'] != digest([expected['adapter'], catalog]):
        raise Conflict('native tool catalog changed')
    if not set(catalog['required']).issubset(expected['selected']) or not set(expected['selected']).issubset(set(catalog['required']) | set(catalog['optional'])):
        raise Conflict('native tool selection omitted required tools or invented a tool')


def load(store, run_id, names, *, env=None):
    from .application_loop import _save
    adapter, row = scope(store, run_id, env)
    catalog = CATALOG[adapter]
    known = set(catalog['required']) | set(catalog['optional'])
    if not names or len(names) > 8 or any(name not in known for name in names):
        raise ValidationError('load 1–8 exact IDs returned by this adapter catalog')
    lockpath = path(row).with_suffix('.lock') if row else store.root / 'run' / ('tool-selection-' + run_id + '.lock')
    lockpath.parent.mkdir(parents=True, exist_ok=True)
    with lockpath.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = selection(store, row) if row else prepared_selection(store, run_id, adapter)
        if value['catalog'] != digest([adapter, catalog]):
            raise Conflict('tool catalog changed; reprepare the native assignment')
        chosen = list(dict.fromkeys([*value['selected'], *names]))
        if chosen == value['selected']:
            return value
        value = {**value, 'selected': chosen,
                 'status': {'dynamic': 'pending_native', 'launch': 'pending_restart', 'advisory': 'advisory'}[catalog['mode']]}
        if (row is None or row['status'] == 'prepared') and catalog['mode'] == 'launch':
            value['status'] = 'configured'
        _save(store, 'tools:' + run_id, value)
        if row and (catalog['mode'] == 'dynamic' or row['status'] == 'prepared'):
            atomic_write_json(path(row), value)
    return value


def acknowledge(store, run_id, names, *, env=None):
    from .application_loop import _save
    adapter, row = scope(store, run_id, env)
    if row is None or adapter != 'pi':
        raise Conflict('dynamic tool acknowledgement requires the Pi extension')
    with path(row).with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        value = selection(store, row)
        if set(names) != set(value['selected']):
            raise Conflict('native tool visibility differs from the requested set')
        value = {**value, 'confirmed': names, 'status': 'loaded'}
        _save(store, 'tools:' + run_id, value)
    return value


def main(argv, root, *, env=None):
    parser = argparse.ArgumentParser(prog='hx tools')
    parser.add_argument('action', choices=['discover', 'load', 'status', 'acknowledge'])
    parser.add_argument('ids', nargs='*')
    parser.add_argument('--query')
    parser.add_argument('--run')
    parser.add_argument('--root')
    args = parser.parse_args(argv)
    child = os.environ if env is None else env
    run_id = args.run or child.get('HX_CONTINUITY_RUN')
    if not run_id:
        raise ValidationError('tool discovery/loading requires the current planned run')
    with ContinuityStore(root) as store:
        if args.action == 'discover':
            result = discover(store, run_id, args.query, env=child)
        elif args.action == 'load':
            result = load(store, run_id, args.ids, env=child)
        elif args.action == 'acknowledge':
            result = acknowledge(store, run_id, args.ids, env=child)
        else:
            adapter, row = scope(store, run_id, child)
            result = selection(store, row) if row else prepared_selection(store, run_id, adapter)
    print(canonical(result))
    return 0
