"""Deterministic context and inspection limits at planned native boundaries."""
from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from .continuity_store import digest
from .native_tools import AdmissionDenied

CONTEXT_BYTES = 8192
MEMORY_BYTES = 4096
READ_LINES = 160
SEARCH_RESULTS = 80
INPUT_BYTES = 16384


def _stamp(path):
    stat = path.stat()
    return digest([stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])


def check_files(row, *, replacing=None):
    """Stat known installed files only; never scan a worker's home or repository."""
    payload = row['payload']
    capsule = Path(payload['capsule'])
    worker = payload['worker_id']
    paths = [(Path(payload['packet_path']), CONTEXT_BYTES),
             (capsule / 'config' / worker / 'AGENTS.md', CONTEXT_BYTES),
             (capsule / 'run' / worker / 'persona.md', CONTEXT_BYTES)]
    home = capsule / 'run' / worker / 'home'
    paths += [(home / name, MEMORY_BYTES) for name in ('MEMORY.md', 'memory.md', 'context.md')]
    if payload.get('workdir'):
        repository = Path(payload['workdir'])
        paths += [(repository / name, CONTEXT_BYTES) for name in ('AGENTS.md', 'CLAUDE.md', '.claude/CLAUDE.md')]
    for path, bound in paths:
        if path.resolve() != replacing and path.exists() and path.stat().st_size > bound:
            raise AdmissionDenied(f'{path.name} exceeds {bound} bytes; compress current task state before continuing')


def _source(payload, task):
    value = payload.get('file_path', payload.get('path', payload.get('filePath', payload.get('target_file'))))
    if not isinstance(value, str) or not value:
        raise AdmissionDenied('inspection requires a named file or directory')
    path = Path(value).expanduser()
    return (Path(task['workdir']) / path).resolve() if not path.is_absolute() else path.resolve()


def inspect_call(row, task, observation):
    """Return a bounded read identity, or None when this is not a source read."""
    name = observation.get('tool_name', observation.get('toolName', ''))
    name = name.rsplit('.', 1)[-1] if isinstance(name, str) else ''
    args = observation.get('tool_input', observation.get('toolInput'))
    from .continuity_store import canonical
    replacing = None
    if name in {'Write', 'write', 'write_file', 'Edit', 'edit', 'edit_file', 'search_replace'} and isinstance(args, dict):
        target = _source(args, task)
        if target.name.lower() in {'memory.md', 'context.md', 'agents.md', 'claude.md'}:
            replacing = target
    check_files(row, replacing=replacing)
    if len(canonical(args).encode()) > INPUT_BYTES:
        raise AdmissionDenied('tool arguments exceed 16384 bytes; split the operation')
    if not isinstance(args, dict):
        return None  # Native patch tools can carry a string.
    if name in {'Write', 'write', 'write_file', 'Edit', 'edit', 'edit_file', 'search_replace'}:
        path = _source(args, task)
        if path == Path(row['payload']['packet_path']).resolve():
            raise AdmissionDenied('the task checkpoint is runtime-owned; use typed progress updates')
        if path.name.lower() in {'memory.md', 'context.md', 'agents.md', 'claude.md'}:
            content = args.get('content', args.get('new_string', args.get('newText', '')))
            old = args.get('old_string', args.get('oldText', ''))
            size = path.stat().st_size if path.exists() and name in {'Edit', 'edit', 'edit_file', 'search_replace'} else 0
            repeats = max(1, size) if args.get('replace_all') else 1
            growth = len(str(content).encode()) - len(str(old).encode())
            projected = size + growth * (repeats if growth > 0 else 1)
            if projected > MEMORY_BYTES:
                raise AdmissionDenied('context/memory write exceeds 4096 bytes; replace obsolete facts instead of appending history')
    if name in {'Read', 'read', 'read_file'}:
        path = _source(args, task)
        limit = args.get('limit')
        offset = args.get('offset', 1)
        if type(offset) is not int or offset < 1:
            raise AdmissionDenied('source read offset must be a positive line number')
        if limit is not None and (type(limit) is not int or not 1 <= limit <= READ_LINES):
            raise AdmissionDenied('source reads allow at most 160 lines; select the needed symbol range')
        # Small files are already bounded. Large files require explicit slices.
        if path.stat().st_size > CONTEXT_BYTES and limit is None:
            raise AdmissionDenied('full-file read exceeds 8192 bytes; select at most 160 lines')
        if name == 'read_file' and path.name in {'SKILL.md', 'AGENTS.md', 'CLAUDE.md'} and path.stat().st_size > CONTEXT_BYTES:
            raise AdmissionDenied('this native tool returns instruction files whole; use hx tool-exec for bounded extraction')
        return digest([str(path), offset, limit]), _stamp(path)
    if name in {'Grep', 'grep', 'search', 'Glob', 'glob', 'find', 'ls'}:
        _source(args, task)
        limit = args.get('head_limit', args.get('limit', args.get('max_results')))
        if type(limit) is not int or not 1 <= limit <= SEARCH_RESULTS:
            raise AdmissionDenied('search requires a named scope and at most 80 results; if the tool has no limit, use hx tool-exec')
    if name == 'list_dir':
        raise AdmissionDenied('native list_dir cannot accept a result bound; use hx tool-exec --request ID -- ls PATH')
    if name in {'Bash', 'bash', 'exec_command', 'shell', 'run_shell_command', 'run_terminal_cmd'}:
        command = args.get('command', args.get('cmd', ''))
        # These common unbounded inspections have native bounded equivalents.
        # This is an efficiency gate, not an attempt to sandbox arbitrary code.
        if isinstance(command, str):
            try:
                parts = shlex.split(command)
            except ValueError:
                return None
            noisy = {'pytest', 'npm', 'pnpm', 'yarn', 'cargo', 'make', 'mvn', 'gradle', 'dotnet'}
            if parts and (parts[0] in noisy or parts[:2] in [['git', 'diff'], ['git', 'show'], ['git', 'log'], ['go', 'test']]):
                raise AdmissionDenied('run build/test/diff output through hx tool-exec --request ID -- COMMAND, or use an assigned hx check')
            if parts and parts[0] in {'cat', 'less', 'more', 'find'}:
                raise AdmissionDenied('use a scoped file range or bounded search; whole-file/tree inspection requires an explicit batch operation')
            if parts and parts[0] in {'rg', 'grep'}:
                raise AdmissionDenied('use scoped Grep with head_limit, or hx tool-exec --request ID -- rg PATTERN PATH for bounded output')
    return None


def remember_read(tx, row, session, actor, call, reading):
    if reading is None:
        return
    key, stamp = reading
    checkpoint = row['payload']['checkpoint_id']
    prior = tx.db.execute('''SELECT r.call_id FROM native_reads r JOIN native_tool_calls c
        USING(launch_id,session_id,actor_id,call_id)
        WHERE r.launch_id=? AND r.session_id=? AND r.actor_id=? AND r.checkpoint_id=?
        AND r.read_key=? AND r.source_stamp=? AND r.success=1 AND c.status='settled' LIMIT 1''',
        (row['launch_id'], session, actor, checkpoint, key, stamp)).fetchone()
    if prior:
        raise AdmissionDenied(f'unchanged source range already read in call {prior[0]}; use that result or select a different range')
    tx.db.execute('INSERT INTO native_reads VALUES(?,?,?,?,?,?,?,0)',
        (row['launch_id'], session, actor, call, checkpoint, key, stamp))


def settled(tx, launch_id, session, actor, call, payload):
    result = payload.get('tool_response')
    failed = payload.get('is_error') or (isinstance(result, dict) and
        (result.get('is_error') or result.get('isError') or result.get('error')))
    tx.db.execute('UPDATE native_reads SET success=? WHERE launch_id=? AND session_id=? AND actor_id=? AND call_id=?',
        (0 if failed else 1, launch_id, session, actor, call))
