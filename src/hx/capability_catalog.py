"""Bounded MCP/skill discovery through an explicit local catalog.

The shell facade works on every adapter; it does not pretend to modify a host's
native tool schemas. Indexing lists capabilities, never invokes an MCP tool.
"""
import json
import os
import selectors
import select
import signal
import subprocess
import time
from pathlib import Path

from .continuity_store import Conflict, canonical, digest
from .errors import ValidationError
from .store import atomic_write_json

LIMIT = 262144


class MCP:
    def __init__(self, command):
        if not isinstance(command, list) or not command or len(command) > 32 or any(not isinstance(s, str) or len(s) > 4096 for s in command):
            raise ValidationError('MCP server requires a bounded argv array')
        self.process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, start_new_session=True)
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        self.pending = b''
        self.serial = 0

    def __enter__(self):
        self.call('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {}, 'clientInfo': {'name': 'hx', 'version': '1'}})
        self.send({'jsonrpc': '2.0', 'method': 'notifications/initialized'})
        return self

    def send(self, body):
        data = (canonical(body) + '\n').encode()
        if len(data) > 8192:
            raise ValidationError('MCP request exceeds 8 KiB')
        fd = self.process.stdin.fileno()
        os.set_blocking(fd, False)
        deadline = time.monotonic() + 10
        while data:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([], [fd], [], remaining)[1]:
                raise Conflict('MCP server stopped accepting input')
            try:
                data = data[os.write(fd, data):]
            except BlockingIOError:
                continue
            except BrokenPipeError:
                raise Conflict('MCP server closed its input') from None

    def call(self, method, params):
        self.serial += 1
        identity = self.serial
        self.send({'jsonrpc': '2.0', 'id': identity, 'method': method, 'params': params})
        deadline, consumed = time.monotonic() + 10, 0
        while time.monotonic() < deadline:
            if b'\n' not in self.pending:
                if not self.selector.select(max(0, deadline - time.monotonic())):
                    break
                data = os.read(self.process.stdout.fileno(), 65536)
                if not data:
                    raise Conflict('MCP server exited without a result')
                consumed += len(data)
                if consumed > LIMIT:
                    raise ValidationError('MCP response exceeds 256 KiB')
                self.pending += data
                continue
            line, self.pending = self.pending.split(b'\n', 1)
            message = json.loads(line)
            if 'method' in message:
                if 'id' in message:
                    self.send({'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32601, 'message': 'Unsupported client capability'}})
                continue
            if message.get('id') != identity:
                continue
            if 'error' in message:
                raise Conflict('MCP server rejected the request')
            return message['result']
        raise Conflict('MCP server response timed out')

    def __exit__(self, *_):
        self.selector.close()
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=.5)
            except subprocess.TimeoutExpired:
                os.killpg(self.process.pid, signal.SIGKILL)
                self.process.wait()
        self.process.stdin.close()
        self.process.stdout.close()


def config(root):
    path = Path(root) / 'config/capabilities.json'
    if not path.exists():
        return {'schema_version': 1, 'servers': {}, 'skills': {}}
    if path.stat().st_size > 65536:
        raise ValidationError('capability registration exceeds 64 KiB')
    body = json.loads(path.read_text())
    if body.keys() != {'schema_version', 'servers', 'skills'} or body['schema_version'] != 1:
        raise ValidationError('capabilities require schema_version=1, servers and skills')
    return body


def index(root):
    declared = config(root)
    entries = {}
    if len(declared['servers']) > 16 or len(declared['skills']) > 64:
        raise ValidationError('capability sources exceed 16 MCP servers or 64 skills')
    for name, server in declared['servers'].items():
        if not name.replace('-', '').replace('_', '').isalnum():
            raise ValidationError('MCP server IDs must be simple names')
        client = MCP(server['command'])
        try:
            with client:
                cursor = None
                seen = set()
                for _ in range(16):
                    result = client.call('tools/list', {'cursor': cursor} if cursor else {})
                    for tool in result.get('tools', []):
                        if not isinstance(tool.get('name'), str) or not 1 <= len(tool['name']) <= 128 or any(c.isspace() for c in tool['name']):
                            raise ValidationError('MCP tool names must be bounded exact IDs without whitespace')
                        identity = 'mcp:' + name + ':' + tool['name']
                        if identity in entries or len(entries) >= 256:
                            raise ValidationError('duplicate or oversized MCP catalog')
                        if not isinstance(tool.get('inputSchema'), dict):
                            raise ValidationError('MCP tool lacks its input schema')
                        entries[identity] = {'kind': 'mcp', 'server': name, 'name': tool['name'],
                            'purpose': (tool.get('description') or tool['name'])[:384],
                            'schema': tool['inputSchema'], 'source': digest(server)}
                    cursor = result.get('nextCursor')
                    if not cursor:
                        break
                    if cursor in seen:
                        raise Conflict('MCP list cursor repeated')
                    seen.add(cursor)
                else:
                    raise ValidationError('MCP catalog requires more than 16 pages')
        finally:
            # __enter__ can fail before context-manager cleanup executes.
            client.__exit__()
    for name, source in declared['skills'].items():
        path = Path(source).resolve()
        if path.name != 'SKILL.md' or path.stat().st_size > 32768:
            raise ValidationError('registered skills must be SKILL.md files of at most 32 KiB')
        body = path.read_text()
        entries['skill:' + name] = {'kind': 'skill', 'path': str(path), 'source': digest(body),
            'purpose': next((line[12:].strip() for line in body.splitlines()[:32] if line.startswith('description:')), name)[:384]}
    value = {'version': digest(entries), 'configuration': digest(declared), 'entries': entries}
    if len(canonical(value).encode()) > LIMIT:
        raise ValidationError('capability catalog exceeds 256 KiB; register a smaller set')
    atomic_write_json(Path(root) / 'state/capabilities.json', value)
    return {'version': value['version'], 'capabilities': len(entries)}


def read(root):
    path = Path(root) / 'state/capabilities.json'
    if not path.exists():
        return {'version': digest({}), 'entries': {}}
    if path.stat().st_size > LIMIT:
        raise ValidationError('capability catalog exceeds its bound')
    value = json.loads(path.read_text())
    if value['configuration'] != digest(config(root)):
        raise Conflict('capability configuration changed; refresh the catalog')
    return value


def load(store, run_id, identity):
    from .application_loop import _save, _state
    catalog = read(store.root)
    entry = catalog['entries'].get(identity)
    if not entry:
        raise ValidationError('unknown capability ID')
    if entry['kind'] == 'skill':
        path = Path(entry['path'])
        if path.stat().st_size > 32768:
            raise ValidationError('skill exceeds 32 KiB')
        with path.open() as source:
            body = source.read(32769)
        if digest(body) != entry['source']:
            raise Conflict('skill changed; refresh the catalog')
        if len(body.encode()) > 8192:
            raise ValidationError('skill body exceeds context bound; split the skill before loading')
        result = {'id': identity, 'body': body}
    else:
        if len(canonical(entry['schema']).encode()) > 8192:
            raise ValidationError('tool schema exceeds the per-tool context bound')
        result = {'id': identity, 'inputSchema': entry['schema'], 'invoke': 'hx tools call ' + identity + ' --request ID --input FILE'}
    current = _state(store, 'capabilities:' + run_id)
    selected = current.get('selected', []) if current.get('version') == catalog['version'] else []
    if len(selected) >= 16 and identity not in selected:
        raise ValidationError('task capability selection exceeds 16 entries')
    _save(store, 'capabilities:' + run_id, {'version': catalog['version'], 'selected': list(dict.fromkeys([*selected, identity]))})
    return {**result, 'version': catalog['version'], 'visibility': 'shell_facade'}


def invoke(store, run_id, identity, arguments, request_id):
    from .application_loop import _state, _save
    from .continuity_store import _id
    _id(request_id)
    catalog = read(store.root)
    chosen = _state(store, 'capabilities:' + run_id)
    if chosen.get('version') != catalog['version'] or identity not in chosen.get('selected', []):
        raise Conflict('load this capability in the current task before invoking it')
    entry = catalog['entries'][identity]
    if entry['kind'] != 'mcp' or not isinstance(arguments, dict):
        raise ValidationError('MCP invocation requires a tool ID and argument object')
    key = 'capability-call:' + run_id + ':' + request_id
    inputs = digest([catalog['version'], identity, arguments])
    with store.transaction() as tx:
        from .passes import _active_run
        _active_run(tx, run_id)
        previous = _state(tx, key)
        if previous:
            if previous['input_hash'] != inputs:
                raise Conflict('capability request ID reused with different inputs')
            if previous['status'] != 'complete':
                raise Conflict('capability execution outcome is uncertain; do not repeat the action')
            return previous['result']
        tx._change()
        tx.db.execute('INSERT INTO runtime_cycles VALUES(?,?)', (key, canonical({'input_hash': inputs, 'status': 'running', 'capability': identity})))
    client = MCP(config(store.root)['servers'][entry['server']]['command'])
    try:
        with client:
            result = client.call('tools/call', {'name': entry['name'], 'arguments': arguments})
    finally:
        client.__exit__()
    encoded = canonical(result).encode()
    with store.transaction() as tx:
        event = tx.append_event(run_id, 'capabilities', 'capability:' + request_id, 'tool_result', {'capability': identity, 'is_error': bool(result.get('isError'))})
        sha = tx.put_artifact(encoded, owner_type='event', owner_id=event['event_id'], slot='payload')
        body = {'artifact_hash': sha, 'bytes': len(encoded), 'capability': identity, 'is_error': bool(result.get('isError'))}
        tx.db.execute('UPDATE events SET payload=?,payload_hash=? WHERE event_id=?', (canonical(body), digest(body), event['event_id']))
        answer = {'event_id': event['event_id'], **result} if len(encoded) <= 4096 else {
            'event_id': event['event_id'], 'isError': bool(result.get('isError')), 'bytes': len(encoded),
            'read': 'hx evidence ' + event['event_id'] + ' --limit 4096'}
        tx.db.execute('UPDATE runtime_cycles SET payload=? WHERE scope=?', (canonical({'input_hash': inputs, 'status': 'complete', 'result': answer}), key))
    return answer
