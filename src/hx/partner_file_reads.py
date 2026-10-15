"""Whole-file broker for the Partner and its native research children.

Admission freezes the exact source identity. Automatic native post-tool hooks
deliver every UTF-8 byte and settle the read only after every segment is emitted.
The broker reserves context space and rejects slices or duplicate unchanged reads.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import json
from pathlib import Path

from . import file_reads, partner_context, store, streams, subagents
from .continuity_store import ContinuityStore, canonical, digest
from .errors import ValidationError
from .file_read_delivery import split_delivery, segment_slots


def directory(root):
    return Path(root) / 'run/partner/file-reads'


@contextmanager
def locked(root):
    target = directory(root)
    target.mkdir(parents=True, exist_ok=True)
    with (target / 'reads.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        yield


def _identity(observation):
    session = observation.get('session_id') or observation.get('sessionId') or observation.get('transcript_path')
    actor = observation.get('agent_id') or observation.get('agentId') or observation.get('subagent_id') or ''
    call = observation.get('tool_use_id') or observation.get('toolUseId')
    if not isinstance(session, str) or not session or not isinstance(call, str) or not call:
        raise ValidationError('complete Partner reads require native session and tool-call identity')
    return session, str(actor), call


def _context(root, actor):
    path, _ = partner_context.paths(root, 'child:' + actor if actor else 'partner')
    if not path.is_file():
        path, _ = partner_context.paths(root)
    if not path.is_file():
        raise ValidationError('Partner context must be delivered before complete-file admission')
    state = json.loads(path.read_text())
    if not state.get('generation') or len(state.get('emitted', [])) != state.get('parts', -1):
        raise ValidationError('Partner context delivery is still pending')
    return state


def _path(root, identity):
    return directory(root) / (digest(identity) + '.json')


def _records(root):
    return [(path, json.loads(path.read_text())) for path in sorted(directory(root).glob('*.json'))]


def _prepare(root, observation):
    args = file_reads.read_input(observation)
    if args is None:
        return None
    cwd = observation.get('cwd') or root
    task = {'workdir': str(cwd)}
    if 'repo_args' in args:
        return file_reads.prepare({'payload': {'required_files': []}}, task, observation, root=root)
    path = file_reads.source_path(args, task)
    from .token_controls import limits
    if path.stat().st_size > limits(root, 'partner')['threshold']:
        raise ValidationError('complete file exceeds the Partner context budget; refactor the file before reading; no prefix was delivered')
    from .instruction_materials import discover_bundle
    refs = discover_bundle(path)
    row = {'payload': {'required_files': refs}}
    text, receipt = file_reads.prepare(row, task, observation, root=root)
    return text, receipt


def _capacity(root, actor, context, receipt, records):
    from .token_controls import limits
    bound = limits(root, 'partner')['threshold']
    main_path, _ = partner_context.paths(root)
    main = json.loads(main_path.read_text()) if main_path.is_file() else {}
    initial = context['bytes'] + (main.get('bytes', 0) if actor else 0)
    handle = subagents.load(root, 'partner').get(actor) if actor else None
    stream = subagents.stream_for('partner', handle) if handle else 'partner-main'
    usage = max((row.get('context_tokens', 0) for row in streams.iter_records(streams.stream_path(root, 'partner', stream))
                 if row.get('seq', 0) > context.get('event_floor', 0) and type(row.get('context_tokens')) is int), default=0)
    reserved = sum(item['receipt']['bytes'] for _, item in records
                   if item['actor'] == actor and item['generation'] == context['generation'])
    available = max(0, bound - max(initial, usage) - reserved - 4096)
    if receipt['bytes'] > available:
        raise ValidationError('complete file does not fit the remaining Partner context; take a fresh managed context before reading; no partial content was delivered')


def admit(root, observation):
    if file_reads.read_input(observation) is None:
        name = file_reads.tool_name(observation)
        args = observation.get('tool_input', observation.get('toolInput', {}))
        if name in {'Bash', 'bash', 'exec_command', 'shell', 'run_shell_command', 'run_terminal_cmd', 'run_terminal_command'} and isinstance(args, dict):
            from .file_inspection import require_non_file_output
            command = args.get('command', args.get('cmd', ''))
            if isinstance(command, str):
                require_non_file_output(['sh', '-c', command])
        return None
    identity = _identity(observation)
    with locked(root):
        context = _context(root, identity[1])
        text, receipt = _prepare(root, observation)
        count = len(split_delivery(text))
        if count > segment_slots(root, 'partner'):
            raise ValidationError('complete file exceeds the installed native delivery capacity; no prefix was delivered')
        records = _records(root)
        prior = next((item for _, item in records if item['identity'] == list(identity)), None)
        if prior:
            if prior['receipt'] != receipt or prior['generation'] != context['generation']:
                raise ValidationError('native file-call identity changed after admission')
            return prior
        for _, item in records:
            if item['actor'] == identity[1] and item['generation'] == context['generation']:
                if item['status'] == 'complete' and item['receipt'] == receipt:
                    raise ValidationError('this complete unchanged file was already delivered in the current context; use that result')
        _capacity(root, identity[1], context, receipt, records)
        for old_path, item in records:
            if (item['actor'] == identity[1] and item['generation'] == context['generation']
                    and item['status'] == 'admitted' and item['receipt']['path'] == receipt['path']):
                item.update(status='superseded', captured=True)
                store.atomic_write_json(old_path, item)
        state = {'identity': list(identity), 'actor': identity[1], 'generation': context['generation'],
                 'receipt': receipt, 'status': 'admitted', 'captured': False}
        store.atomic_write_json(_path(root, identity), state)
        return state


def emit(root, observation, index, *, write, event='PostToolUse'):
    if file_reads.read_input(observation) is None:
        return None
    try:
        identity = _identity(observation)
    except ValidationError:
        partner_context.invalidate(root)
        raise
    with locked(root):
        path = _path(root, identity)
        if not path.is_file():
            # A timed-out or skipped pre-tool hook must not make an unmatched
            # native preview look safe merely because no pending record exists.
            partner_context.invalidate(root)
            raise ValidationError('complete Partner file broker has no admitted read')
        state = json.loads(path.read_text())
        if state['captured']:
            return None
        text, receipt = _prepare(root, observation)
        if receipt != state['receipt']:
            raise ValidationError('file changed after read admission; retry the complete file')
        frozen = partner_context.emit(root, observation, index, build=lambda: text, write=write,
                                      scope='file:' + digest(identity), event=event)
        if len(frozen['emitted']) != frozen['parts']:
            return None
        # Bounded raw observation text is not the full source. Preserve the
        # exact delivered bytes and source identity for independent assessment.
        owner = digest(identity)
        with ContinuityStore(root) as ledger:
            with ledger.transaction() as tx:
                artifact = tx.put_artifact(text.encode('utf-8'), owner_type='partner-file-read', owner_id=owner, slot='complete')
                source = tx.put_artifact(canonical(receipt).encode('utf-8'), owner_type='partner-file-read', owner_id=owner, slot='receipt')
        handle = subagents.load(root, 'partner').get(identity[1]) if identity[1] else None
        stream = subagents.stream_for('partner', handle) if handle else 'partner-main'
        event_identity = {'session_id': identity[0], 'actor_id': identity[1], 'call_id': identity[2]}
        if not any(item.get('event') == 'complete_file_read' and item.get('read_identity') == event_identity
                   for item in streams.iter_records(streams.stream_path(root, 'partner', stream))):
            streams.append_record(root, 'partner', stream, {
                'event': 'complete_file_read', 'source': 'native-whole-file-broker',
                'artifact_hash': artifact, 'receipt_artifact_hash': source,
                'bytes': receipt['bytes'], 'sha256': receipt['sha256'],
                'read_identity': event_identity, 'context_generation': state['generation'],
            })
        state['evidence'] = {'artifact_hash': artifact, 'receipt_artifact_hash': source}
        state.update(status='complete', captured=True)
        store.atomic_write_json(path, state)
        return {'text': text, 'receipt': receipt, 'complete': True}


def require_delivered(root, observation=None):
    args = file_reads.read_input(observation) if observation else None
    if args is not None:
        return  # Independent whole reads share a capacity reservation, not a barrier.
    with locked(root):
        for _, state in _records(root):
            current = _context(root, state['actor'])
            if current['generation'] == state['generation'] and state['status'] == 'admitted':
                raise ValidationError('complete Partner file delivery is pending; no further operation is admitted')


def require_admitted(root, path):
    targets = {str(path), str(Path(path).resolve())}
    with locked(root):
        for _, state in _records(root):
            if state['receipt']['path'] in targets and state['status'] == 'admitted':
                current = _context(root, state['actor'])
                if current['generation'] == state['generation']:
                    return state
    raise ValidationError('hx file-read requires an admitted native complete-file operation')
