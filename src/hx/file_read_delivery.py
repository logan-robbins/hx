"""Automatic transport segments for hosts which cap each hook context string.

All handlers belong to one native read operation. They resolve the complete file
without a model pagination decision. The original tool result settles only when
every required segment has been emitted; lost handlers leave admission pending.
"""
from __future__ import annotations

import json
import math

from .continuity_store import canonical, digest
from .errors import ValidationError
from .file_reads import broker_result

SEGMENT_CHARS = 9000
SEGMENTED_ADAPTERS = {'claude', 'grok', 'meta'}


def split_delivery(text):
    """UTF-8 byte bounds also bound UTF-16 host string lengths safely."""
    data = text.encode('utf-8')
    parts, start = [], 0
    while start < len(data):
        end = min(len(data), start + SEGMENT_CHARS)
        while end < len(data) and data[end] & 0xC0 == 0x80:
            end -= 1
        parts.append(data[start:end].decode('utf-8'))
        start = end
    return parts or ['']


def segment_slots(root, worker):
    from .token_controls import limits
    from pathlib import Path
    # UTF-8 byte accounting at admission is more conservative than characters.
    count = max(1, math.ceil(limits(root, worker)['threshold'] / SEGMENT_CHARS))
    profile = Path(root) / 'run' / worker / 'transport-profile.json'
    if profile.is_file():
        value = json.loads(profile.read_text())
        if value != {'segment_bytes': SEGMENT_CHARS, 'slots': count}:
            raise ValidationError('native context transport configuration changed; reinstall the adapter before launch or reset')
    return count


def install_profile(root, worker):
    from pathlib import Path
    from .token_controls import limits
    from .store import atomic_write_json
    count = max(1, math.ceil(limits(root, worker)['threshold'] / SEGMENT_CHARS))
    atomic_write_json(Path(root) / 'run' / worker / 'transport-profile.json',
                      {'segment_bytes': SEGMENT_CHARS, 'slots': count})
    return count


def emit(store, row, task, observation, index, *, write, event='PostToolUse'):
    """Write one exact segment, then durably acknowledge that emitted segment.

    Return the complete broker result once the segment set is complete, or None.
    A retry of an acknowledged handler is idempotent. The host waits for all
    configured handlers before making its next model request.
    """
    if type(index) is not int or not 0 <= index < segment_slots(row['payload']['capsule'], row['payload']['worker_id']):
        raise ValidationError('invalid complete-file transport segment')
    result = broker_result(store, row, task, observation)
    if result is None:
        return None
    text, receipt = result['text'], result['receipt']
    parts = split_delivery(text)
    count = len(parts)
    if index >= count:
        return None
    from .native_tools import _identity, _actor
    session, actor, call, _ = _identity(observation)
    actor = _actor(store, row, session, actor)
    scope = 'file-delivery:' + digest([row['launch_id'], session, actor, call])
    # Header ordering is explicit because native hosts may run hook commands in
    # parallel. Every payload character is included exactly once in the set.
    content = (f'Complete file transport segment {index + 1}/{count}; '
               f'sha256={receipt["sha256"]}; bytes={receipt["bytes"]}\n'
               + parts[index])
    if len(content.encode('utf-8')) > 10000:
        raise ValidationError('complete-file transport segment exceeds its native envelope')
    write(json.dumps({'hookSpecificOutput': {'hookEventName': event, 'additionalContext': content}}))
    with store.transaction() as tx:
        previous = tx.db.execute('SELECT payload FROM runtime_cycles WHERE scope=?', (scope,)).fetchone()
        state = json.loads(previous[0]) if previous else {'receipt': receipt, 'count': count, 'emitted': []}
        if state['receipt'] != receipt or state['count'] != count:
            raise ValidationError('complete-file delivery identity changed between transport segments')
        state['emitted'] = sorted(set(state['emitted']) | {index})
        tx._change()
        tx.db.execute('INSERT OR REPLACE INTO runtime_cycles VALUES(?,?)', (scope, canonical(state)))
        complete = len(state['emitted']) == count
    return result if complete else None
