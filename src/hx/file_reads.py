"""Exact complete-file delivery at the native tool boundary.

A read has one content identity, regardless of native display limits. A required
material chain is read one complete part file at a time; each part's receipt names its
chain, and `incomplete_material` holds later work until every part has its own receipt.
Receipts hash the on-disk bytes, never a host's display of them, and certify emitted
bytes, never attention.
"""
from __future__ import annotations

import hashlib
import json
import re
import shlex
import stat
from pathlib import Path

from .continuity_store import canonical, digest
from .errors import ValidationError

READ_TOOLS = {'Read', 'read', 'read_file'}
SHELL_TOOLS = {'Bash', 'bash', 'exec_command', 'shell', 'shell_command', 'run_shell_command', 'run_terminal_cmd', 'run_terminal_command'}


def read_input(observation):
    """Normalize actual native file tools and the exact shell broker command."""
    args = observation.get('tool_input', observation.get('toolInput'))
    if not isinstance(args, dict):
        return None
    name = tool_name(observation)
    if name in READ_TOOLS:
        return args
    if name not in SHELL_TOOLS:
        return None
    command = args.get('command', args.get('cmd'))
    if not isinstance(command, str):
        return None
    try:
        parts = shlex.split(command)
    except ValueError:
        return None
    if len(parts) >= 3 and Path(parts[0]).name == 'hx' and parts[1] == 'file-read':
        rest = parts[2:]
        if rest[:1] == ['--']:
            rest = rest[1:]
        if len(rest) != 1 or not rest[0] or rest[0].startswith('-'):
            raise ValidationError('hx file-read accepts exactly one complete file path; shell pipelines and slices are prohibited')
        return {'file_path': rest[0], 'read_cwd': args.get('workdir', args.get('cwd'))}
    if len(parts) >= 3 and Path(parts[0]).name == 'hx' and parts[1:3] == ['repo', 'file']:
        return {'repo_args': parts[2:]}
    if any(Path(part).name == 'hx' and parts[index + 1:index + 2] == ['file-read']
           for index, part in enumerate(parts)):
        raise ValidationError('hx file-read must be one standalone native shell command; compositions and pipelines cannot deliver complete-file results')
    if any(Path(part).name == 'hx' and parts[index + 1:index + 3] == ['repo', 'file']
           for index, part in enumerate(parts)):
        raise ValidationError('hx repo file must be one standalone native shell command for complete-file delivery')
    return None


def is_read(observation):
    return read_input(observation) is not None


def tool_name(observation):
    name = observation.get('tool_name', observation.get('toolName', ''))
    return name.rsplit('.', 1)[-1] if isinstance(name, str) else ''


def source_path(args, task):
    value = args.get('file_path', args.get('path', args.get('filePath', args.get('target_file'))))
    if not isinstance(value, str) or not value:
        raise ValidationError('complete-file read requires a named file')
    path = Path(value).expanduser()
    base = Path(task['workdir'])
    if isinstance(args.get('read_cwd'), str):
        base = (base / args['read_cwd']).resolve()
    return (base / path).resolve() if not path.is_absolute() else path.resolve()


def stat_stamp(path):
    stat = path.stat()
    return digest([stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])


def _chain(row, path):
    """The required-material chain holding `path`, in part order, or []."""
    refs = row['payload']['required_files']
    required = next((ref for ref in refs if Path(ref['path']).resolve() == path), None)
    if required is None:
        return []
    chain = [ref for ref in refs if ref['bundle_sha256'] == required['bundle_sha256']
             and ref['bundle_bytes'] == required['bundle_bytes'] and ref.get('kind') == required.get('kind')]
    return sorted(chain, key=lambda ref: ref['part'])


def unread_parts(readings):
    """`(path, part, parts)` of the first unread part of each material chain a reader started.

    `readings` are `(reader, receipt)` pairs of completed reads; a chain is complete only
    when every one of its parts has a receipt from the same reader.
    """
    chains = {}
    for reader, receipt in readings:
        material = receipt.get('material')
        if material:
            chains.setdefault((reader, material['bundle_sha256']), (material['paths'], set()))[1].add(material['part'])
    missing = []
    for paths, read in chains.values():
        unread = [part for part in range(1, len(paths) + 1) if part not in read]
        if unread:
            missing.append((paths[unread[0] - 1], unread[0], len(paths)))
    return missing


def incomplete_material(tx, launch_id, *, session=None, actor=None, checkpoint=None):
    """`unread_parts` over a launch's completed native reads, optionally for one reader."""
    query = 'SELECT session_id,actor_id,checkpoint_id,source_stamp FROM native_reads WHERE launch_id=? AND success=1'
    params = [launch_id]
    for column, value in (('session_id', session), ('actor_id', actor), ('checkpoint_id', checkpoint)):
        if value is not None:
            query += f' AND {column}=?'
            params.append(value)
    rows = tx.db.execute(query, params).fetchall()
    return unread_parts(((session_id, actor_id, checkpoint_id), json.loads(stamp))
                        for session_id, actor_id, checkpoint_id, stamp in rows)


def _read_ceiling(row):
    from .observer import MAX_RECORD_BYTES
    ceiling = (MAX_RECORD_BYTES - 65536) // 6
    payload = row['payload']
    if payload.get('capsule') and payload.get('worker_id'):
        from .token_controls import limits
        budget = limits(payload['capsule'], payload['worker_id'])['threshold']
        ceiling = min(ceiling, max(0, budget - payload.get('charged_initial_tokens', 0) - 4096))
    return ceiling


def prepare(row, task, observation, *, root=None):
    """Read one immutable snapshot and return text plus its complete receipt."""
    args = read_input(observation)
    if not isinstance(args, dict):
        raise ValidationError('complete-file read requires structured input')
    if 'repo_args' in args:
        if root is None:
            raise ValidationError('repository file reads require the native authority root')
        from .repo_service import repository_read_request
        return repository_read_request(root, args['repo_args'])
    if any(key in args for key in ('offset', 'limit', 'start_line', 'end_line', 'startLine', 'endLine', 'line_range', 'range')):
        raise ValidationError('bounded file slices are prohibited; request the file once without offsets or limits')
    path = source_path(args, task)
    chain = _chain(row, path)
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise ValidationError('complete-file reads require regular files; streams, devices and directories are not file content')
    ceiling = _read_ceiling(row)
    if metadata.st_size > ceiling:
        raise ValidationError('complete-file size exceeds the current context/transport budget; '
                              'no content was loaded or delivered; refactor the file or restore a fresh managed context')
    stamp = stat_stamp(path)
    if chain:
        from .instruction_materials import read_part
        data, reference = read_part(chain, path)
    else:
        # A concurrent writer cannot turn a small admitted file into an
        # unbounded allocation. This bounded internal verification is never
        # exposed as a successful prefix to the model.
        with path.open('rb') as handle:
            data = handle.read(ceiling + 1)
        if len(data) > ceiling:
            raise ValidationError('file grew beyond the complete delivery budget while reading; no partial content was delivered')
    if stamp != stat_stamp(path):
        raise ValidationError('file changed while preparing its complete read')
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValidationError('complete-file text read requires UTF-8; use an explicit binary inspection tool') from exc
    receipt = {'path': str(path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(),
               'files': [{'path': str(path), 'stamp': stamp}]}
    if chain:
        receipt['material'] = {'bundle_sha256': reference['bundle_sha256'], 'part': reference['part'],
                               'paths': [ref['path'] for ref in chain]}
    return text, receipt


def identity(receipt):
    return digest(receipt['path']), canonical(receipt)


def ensure_capacity(store, row, receipt, *, admitted=False):
    """Refuse before execution when a complete result cannot fit this context.

    UTF-8 bytes are a deliberately conservative token upper bound. An absent
    host token count is not permission to use the complete model window.
    """
    from .application_loop import _state
    from .token_controls import limits
    from .observer import MAX_RECORD_BYTES
    bound = limits(row['payload']['capsule'], row['payload']['worker_id'])
    current = max(_state(store, 'tokens:' + row['launch_id']).get('tokens', 0),
                  row['payload'].get('charged_initial_tokens', 0))
    # Until a fresh checkpoint retires these receipts, count every admitted
    # complete result. This also reserves space for concurrent tool calls and
    # hosts which report usage only after the following model response.
    prior = store.db.execute('SELECT source_stamp FROM native_reads WHERE launch_id=? AND checkpoint_id=?',
        (row['launch_id'], row['payload']['checkpoint_id'])).fetchall()
    reserved = sum(json.loads(item[0])['bytes'] for item in prior)
    # Reserve envelope/instruction room, even before the first usage observation.
    available = max(0, bound['threshold'] - current - reserved - 4096)
    if (0 if admitted else receipt['bytes']) > available or receipt['bytes'] * 6 + 65536 > MAX_RECORD_BYTES:
        raise ValidationError('complete-file delivery does not fit the current context/transport budget; '
                              'restore a fresh managed context or split the source file before reading; '
                              'no partial content was delivered')


def text_candidates(value):
    """Decode known native public text envelopes without discarding any text."""
    if isinstance(value, str):
        yield value
        # Claude Read's display format adds line numbers, but does not change
        # file bytes. Only accept a contiguous complete numbering from line one.
        lines = value.splitlines(keepends=True)
        decoded = []
        for number, line in enumerate(lines, 1):
            match = re.match(r'^[ \t]*' + str(number) + r'(?:\t|→|\|)(.*)$', line, re.S)
            if not match:
                break
            decoded.append(match[1])
        else:
            if lines:
                yield ''.join(decoded)
    elif isinstance(value, dict):
        for key in ('text', 'content', 'output', 'file'):
            if key in value:
                yield from text_candidates(value[key])
    elif isinstance(value, list):
        blocks = [item.get('text') for item in value if isinstance(item, dict) and item.get('type') == 'text']
        if len(blocks) == len(value) and all(isinstance(item, str) for item in blocks):
            yield ''.join(blocks)
            yield '\n'.join(blocks)


def incomplete(value):
    if not isinstance(value, (dict, list)):
        return False
    if isinstance(value, list):
        return any(incomplete(item) for item in value)
    if any(value.get(key) for key in ('truncated', 'is_truncated', 'has_more', 'isError', 'is_error', 'error')):
        return True
    if value.get('complete') is False:
        return True
    return any(incomplete(item) for item in value.values() if isinstance(item, (dict, list)))


def delivered(receipt, payload):
    result = payload.get('tool_response', payload.get('toolResult', payload.get('tool_result')))
    if payload.get('is_error') or incomplete(result):
        return False
    return any(len(text.encode('utf-8')) == receipt['bytes']
               and hashlib.sha256(text.encode('utf-8')).hexdigest() == receipt['sha256']
               for text in text_candidates(result))


def broker_result(store, row, task, observation):
    """Resolve every continuation and recheck the admitted snapshot before emit."""
    session = observation.get('session_id') or observation.get('sessionId') or observation.get('transcript_path')
    actor = observation.get('agent_id') or observation.get('agentId') or ''
    call = observation.get('tool_use_id') or observation.get('toolUseId')
    from .native_tools import _actor
    actor = _actor(store, row, session, actor)
    found = store.db.execute('SELECT source_stamp FROM native_reads WHERE launch_id=? AND session_id=? AND actor_id=? AND call_id=?',
        (row['launch_id'], session, actor, call)).fetchone()
    if found is None:
        denied = store.db.execute("SELECT 1 FROM native_tool_calls WHERE launch_id=? AND session_id=? AND actor_id=? AND call_id=? AND status='denied'",
            (row['launch_id'], session, actor, call)).fetchone()
        if denied:
            return None
        raise ValidationError('complete-file broker has no admitted read')
    expected = json.loads(found[0])
    text, receipt = prepare(row, task, observation, root=store.root)
    if receipt != expected:
        raise ValidationError('file changed after read admission; retry the complete file')
    ensure_capacity(store, row, receipt, admitted=True)
    return {'text': text, 'receipt': receipt, 'complete': True}


def main(argv, root, *, env=None):
    """The shell tool returns a small marker; its synchronous hook emits bytes."""
    import argparse
    import os
    parser = argparse.ArgumentParser(prog='hx file-read',
                                     epilog='example: hx file-read src/app.py')
    parser.add_argument('path')
    args = parser.parse_args(argv)
    child = os.environ if env is None else env
    if not child.get('HARNESS_ID'):
        raise ValidationError('hx file-read requires a managed native agent so the complete result is delivered by its read broker')
    path = Path(args.path).expanduser().resolve()
    if child.get('HX_CONTINUITY_RUN'):
        from .continuity_store import ContinuityStore
        with ContinuityStore.reader(Path(child.get('HX_CONTINUITY_AUTHORITY', root))) as ledger:
            admitted = ledger.db.execute('''SELECT r.source_stamp FROM native_reads r
                JOIN native_tool_calls c USING(launch_id,session_id,actor_id,call_id)
                JOIN native_launches l USING(launch_id) WHERE l.run_id=? AND c.status='admitted' ''',
                (child['HX_CONTINUITY_RUN'],)).fetchall()
        if not any(json.loads(item[0])['path'] == str(path) for item in admitted):
            raise ValidationError('hx file-read has no matching admitted complete-file operation; invoke it as one standalone native shell command')
    elif child.get('HARNESS_ID') == 'partner':
        from .partner_file_reads import require_admitted
        require_admitted(root, path)
    else:
        raise ValidationError('hx file-read requires its admitted run identity')
    # Do not emit file content here: a native shell display cap cannot truncate
    # it when the only shell output is this explicit broker marker.
    print(canonical({'complete_file_read': 'synchronous_native_broker', 'path': str(path)}))
    return 0
