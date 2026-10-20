"""Complete-file delivery after a native read: Claude's own result, or transport segments.

Claude's Read returns the whole admitted file (start.sh lifts its token cap to the context
threshold, which bounds every admitted read), so its hook verifies that result against the
admitted bytes and adds only `native_marker`. Hosts without a complete native result
(SEGMENTED_ADAPTERS) and the Partner get automatic transport segments: one hook entry per
segment, each within the host's per-hook context cap, settling only when every segment has
been emitted; lost handlers leave admission pending.
"""
from __future__ import annotations

import json
import math

from .continuity_store import canonical, digest
from .errors import ValidationError
from .file_reads import READ_TOOLS, broker_result, read_input, tool_name

SEGMENT_CHARS = 9000
SEGMENTED_ADAPTERS = {'grok', 'meta'}
#: Hosts whose own Read result is the complete-file delivery.
NATIVE_READ_ADAPTERS = {'claude'}


def require_native_read(adapter, observation):
    """A shell broker returns only its marker, so a native-read host must use its Read tool."""
    if adapter in NATIVE_READ_ADAPTERS and tool_name(observation) not in READ_TOOLS and read_input(observation):
        raise ValidationError('the native Read tool is the complete-file delivery on this host; Read the file '
                              '(materialize another revision with hx repo materialize first)')


def native_marker(result, observation):
    """The completeness marker for a host's own Read result, which must be the admitted file.

    Claude shows CRLF line endings as LF; the receipt still hashes the on-disk bytes. Any other
    difference (a capped, paged or changed result) fails the delivery.
    """
    response = observation.get('tool_response')
    native = response.get('file') if isinstance(response, dict) else None
    content = native.get('content') if isinstance(native, dict) else None
    text, receipt = result['text'], result['receipt']
    marker = f'Complete file: sha256={receipt["sha256"]}; bytes={receipt["bytes"]}'
    if observation.get('is_error') or not isinstance(content, str):
        raise ValidationError('the native read returned no file content')
    if content == text:
        return marker
    if '\r\n' in text and content == text.replace('\r\n', '\n'):
        return marker + '; CRLF line endings shown as LF'
    raise ValidationError('the native read result is not the complete admitted file')


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
