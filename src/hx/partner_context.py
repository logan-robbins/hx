"""Complete Partner context through automatic synchronous native hook segments.

Every installed handler participates in one boundary round, including empty
slots. A file lock freezes one source body even when the host starts handlers in
parallel. Output is acknowledged only after its complete JSON envelope is sent.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
from pathlib import Path
import uuid

from . import store
from .errors import ValidationError
from .file_read_delivery import segment_slots, split_delivery


def paths(root, scope='partner'):
    directory = Path(root) / 'run/partner/context-delivery'
    if scope != 'partner':
        directory = directory / hashlib.sha256(scope.encode()).hexdigest()
    return directory / 'state.json', directory / 'parts'


@contextmanager
def locked(root, scope='partner'):
    state, _ = paths(root, scope)
    state.parent.mkdir(parents=True, exist_ok=True)
    with (state.parent / 'delivery.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def validate(root, text):
    from .token_controls import limits
    slots = segment_slots(root, 'partner')
    data = text.encode('utf-8')
    parts = split_delivery(text) if text else []
    budget = limits(root, 'partner')['threshold']
    if len(data) > budget or len(parts) > slots:
        raise ValidationError(
            f'complete Partner context is {len(data)} UTF-8 bytes; native delivery budget '
            f'is {budget} bytes across {slots} automatic segments; reduce maintained '
            'context before launch/reset; prefixes and model-driven pagination are prohibited')
    return {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'slots': slots,
            'parts': len(parts)}


def invalidate(root):
    """A requested fresh session cannot reuse a prior completed boundary receipt."""
    with locked(root):
        state, _ = paths(root)
        store.atomic_write_json(state, {'pending': True})


def emit(root, payload, index, *, build, write, scope='partner', event='SessionStart'):
    """Emit this handler's whole segment of one complete frozen Partner boundary."""
    slots = segment_slots(root, 'partner')
    if type(index) is not int or not 0 <= index < slots:
        raise ValidationError('invalid Partner context transport segment')
    boundary = {key: payload.get(key) for key in ('session_id', 'sessionId', 'source', 'transcript_path')}
    with locked(root, scope):
        state_path, parts_dir = paths(root, scope)
        state = json.loads(state_path.read_text()) if state_path.is_file() else {}
        # A native boundary waits for its entire configured handler set before
        # the next model request. Either handler may arrive first.
        if state.get('boundary') != boundary or len(state.get('seen', [])) == slots or state.get('pending'):
            # Invalidate the prior receipt before composition or validation can
            # fail. A failed new boundary cannot inherit old tool permission.
            store.atomic_write_json(state_path, {'pending': True, 'scope': scope, 'boundary': boundary})
            text = build()
            record = validate(root, text)
            state = {**record, 'scope': scope, 'boundary': boundary, 'generation': str(uuid.uuid4()),
                     'seen': [], 'emitted': [], 'files': []}
            from . import streams, subagents
            handle = subagents.load(root, 'partner').get(str(payload.get('agent_id') or '')) if scope.startswith('child:') else None
            state['event_floor'] = streams.last_seq(streams.stream_path(root, 'partner', subagents.stream_for('partner', handle) if handle else 'partner-main'))
            for part, content in enumerate(split_delivery(text) if text else []):
                data = content.encode('utf-8')
                target = parts_dir / f'{state["generation"]}-{part}.txt'
                store.atomic_write_text(target, data.decode('utf-8'))
                state['files'].append({'path': str(target), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
            store.atomic_write_json(state_path, state)
            retained = {item['path'] for item in state['files']}
            if parts_dir.is_dir():
                for old in parts_dir.iterdir():
                    if str(old) not in retained:
                        old.unlink()
        if index < state['parts']:
            part = state['files'][index]
            data = Path(part['path']).read_bytes()
            if len(data) != part['bytes'] or hashlib.sha256(data).hexdigest() != part['sha256']:
                raise ValidationError('frozen Partner context identity changed before delivery')
            content = (f'Complete context segment {index + 1}/{state["parts"]}; '
                       f'boundary={state["generation"]}; sha256={state["sha256"]}; bytes={state["bytes"]}\n'
                       + data.decode('utf-8'))
            if len(content.encode('utf-8')) > 10_000:
                raise ValidationError('Partner context segment exceeds the native envelope')
            write(json.dumps({'hookSpecificOutput': {'hookEventName': event,
                                                     'additionalContext': content}}, ensure_ascii=False))
            state['emitted'] = sorted(set(state['emitted']) | {index})
        state['seen'] = sorted(set(state['seen']) | {index})
        store.atomic_write_json(state_path, state)
        return state


def require_delivered(root):
    """Fail tool admission after a missing, failed, or interrupted boundary hook."""
    segment_slots(root, 'partner')  # An installed profile cannot silently change.
    with locked(root):
        state_path, _ = paths(root)
        state = json.loads(state_path.read_text()) if state_path.is_file() else {}
        if state.get('pending') or not state.get('parts') or len(state.get('emitted', [])) != state['parts']:
            raise ValidationError('complete Partner context has not been delivered; finish or retry the native context boundary before tool use')
        for child_path in state_path.parent.glob('*/state.json'):
            child = json.loads(child_path.read_text())
            if child.get('scope', '').startswith('file:'):
                continue  # The file broker owns retry and supersession rules.
            if child.get('pending') or len(child.get('emitted', [])) != child.get('parts', 0):
                raise ValidationError('complete subagent context or result has not been delivered before tool use')
        return {key: state[key] for key in ('generation', 'bytes', 'sha256', 'parts')}
