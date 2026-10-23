"""Deterministic context and inspection limits at planned native boundaries."""
from __future__ import annotations

import os
import hashlib
import json
import re
import shlex
from pathlib import Path

from .continuity_store import digest
from .native_tools import AdmissionDenied
from .errors import HxError

CONTEXT_BYTES = 8192
MEMORY_BYTES = 4096
SEARCH_RESULTS = 80
INPUT_BYTES = 16384
#: Build, test and diff programs whose output belongs in `hx tool-exec` or an assigned check.
NOISY = {'pytest', 'npm', 'pnpm', 'yarn', 'cargo', 'make', 'mvn', 'gradle', 'dotnet'}
NOISY_SUBCOMMANDS = {('git', 'diff'), ('git', 'show'), ('git', 'log'), ('go', 'test')}
READERS = {'cat', 'less', 'more', 'find', 'head', 'tail', 'awk'}
#: Global options that take the next argument before a git or go subcommand.
VALUE_OPTIONS = {'-C', '-c', '--git-dir', '--work-tree', '--namespace'}


def _stamp(path):
    stat = path.stat()
    return digest([stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns])


def check_files(row, *, replacing=None):
    """Stat known installed files only; never scan a worker's home or repository."""
    payload = row['payload']
    capsule = Path(payload['capsule'])
    worker = payload['worker_id']
    # System instructions are verified by the prompt manifest and launch context
    # budget. A facade/memory budget must never turn them into excerpts.
    paths = []  # Compiled checkpoint manifests own their complete context budget.
    home = capsule / 'run' / worker / 'home'
    paths += [(home / name, MEMORY_BYTES) for name in ('MEMORY.md', 'memory.md', 'context.md')]
    for path, bound in paths:
        if path.resolve() != replacing and path.exists() and path.stat().st_size > bound:
            raise AdmissionDenied(f'{path.name} exceeds {bound} bytes; compress current task state before continuing')


def _source(payload, task):
    value = payload.get('file_path', payload.get('path', payload.get('filePath', payload.get('target_file'))))
    if not isinstance(value, str) or not value:
        raise AdmissionDenied('inspection requires a named file or directory')
    path = Path(value).expanduser()
    return (Path(task['workdir']) / path).resolve() if not path.is_absolute() else path.resolve()


def _material_reference(row, path):
    return next((material for material in row['payload']['required_files']
                 if Path(material['path']).resolve() == path), None)


def _segments(command):
    """Each command of a shell line, split on `&&`, `;`, `|`, `||`, `&` and parentheses, with
    whether its input is piped. A `cd …` is a command of its own."""
    lexer = shlex.shlex(command, posix=True, punctuation_chars=';&|()<>')
    lexer.whitespace_split = True
    current, piped = [], False
    for token in lexer:
        if token and all(char in ';&|()' for char in token):
            if current:
                yield current, piped
            current, piped = [], '|' in token and '||' not in token
        else:
            current.append(token)
    if current:
        yield current, piped


def _module(args):
    """`python -m MODULE ARGS` as [MODULE, *ARGS]; None for a script, `-c` code or a REPL."""
    for index, part in enumerate(args):
        if part == '-m':
            return args[index + 1:] or None
        if part.startswith('-m'):
            return [part[2:], *args[index + 1:]]
        if part == '-c' or not part.startswith('-'):
            return None
    return None


def _program(parts):
    """A command's executable and arguments past `NAME=value`, `env`, `npx`, `uv run` and `python -m`."""
    while parts:
        name = Path(parts[0]).name
        if '=' in parts[0] and parts[0].partition('=')[0].isidentifier():
            parts = parts[1:]
        elif name in {'env', 'npx'} or name == 'uv' and parts[1:2] == ['run']:
            parts = parts[2 if name == 'uv' else 1:]
            while parts and parts[0].startswith('-'):
                parts = parts[1:]
        elif name.startswith('python') and (module := _module(parts[1:])):
            parts = module
        else:
            return name, parts[1:]
    return None, []


def _subcommand(args):
    """The subcommand past global options, as `diff` in `git -C path --no-pager diff`."""
    index = 0
    while index < len(args) and args[index].startswith('-'):
        index += 2 if args[index] in VALUE_OPTIONS else 1
    return args[index] if index < len(args) else None


def inspect_call(row, task, observation, *, root=None):
    """Return a complete read identity, or None for non-file operations."""
    name = observation.get('tool_name', observation.get('toolName', ''))
    name = name.rsplit('.', 1)[-1] if isinstance(name, str) else ''
    args = observation.get('tool_input', observation.get('toolInput'))
    if task.get('dependency_packet') and task.get('binding_status') != 'approved' and name in {'Write', 'write', 'write_file', 'Edit', 'edit', 'edit_file', 'search_replace', 'apply_patch'}:
        raise AdmissionDenied('search the packet areas, bind packet checks citing the search receipts, and obtain independent policy approval before source edits')
    from .continuity_store import canonical
    replacing = None
    if name in {'Write', 'write', 'write_file', 'Edit', 'edit', 'edit_file', 'search_replace'} and isinstance(args, dict):
        target = _source(args, task)
        if target.name.lower() in {'memory.md', 'context.md', 'agents.md', 'claude.md'}:
            replacing = target
    check_files(row, replacing=replacing)
    if len(canonical(args).encode()) > INPUT_BYTES:
        raise AdmissionDenied('tool arguments exceed 16384 bytes; split the operation')
    if name == 'apply_patch':
        patch = args if isinstance(args, str) else args.get('patch', args.get('input', '')) if isinstance(args, dict) else ''
        if isinstance(patch, str):
            for target in re.findall(r'^\*\*\* (?:(?:Add|Update|Delete) File|Move to): (.+)$', patch, re.M):
                path = _source({'path': target.strip()}, task)
                if path == Path(row['payload']['packet_path']).resolve() or _material_reference(row, path) is not None:
                    raise AdmissionDenied('structured patches cannot mutate runtime-owned checkpoints or required material')
    if not isinstance(args, dict):
        return None  # Native patch tools can carry a string.
    if name in {'Write', 'write', 'write_file', 'Edit', 'edit', 'edit_file', 'search_replace'}:
        path = _source(args, task)
        if path == Path(row['payload']['packet_path']).resolve():
            raise AdmissionDenied('the task checkpoint is runtime-owned; use typed progress updates')
        if _material_reference(row, path) is not None:
            raise AdmissionDenied('required instruction material is immutable and runtime-owned')
        if path.name.lower() in {'memory.md', 'context.md', 'agents.md', 'claude.md'}:
            content = args.get('content', args.get('new_string', args.get('newText', '')))
            old = args.get('old_string', args.get('oldText', ''))
            size = path.stat().st_size if path.exists() and name in {'Edit', 'edit', 'edit_file', 'search_replace'} else 0
            repeats = max(1, size) if args.get('replace_all') else 1
            growth = len(str(content).encode()) - len(str(old).encode())
            projected = size + growth * (repeats if growth > 0 else 1)
            if projected > MEMORY_BYTES:
                raise AdmissionDenied('context/memory write exceeds 4096 bytes; replace obsolete facts instead of appending history')
    from .file_reads import read_input
    try:
        file_args = read_input(observation)
    except HxError as exc:
        raise AdmissionDenied(str(exc)) from exc
    if file_args is not None:
        if 'repo_args' in file_args:
            from .file_reads import prepare, identity
            try:
                _, receipt = prepare(row, task, observation, root=root)
            except (HxError, OSError) as exc:
                raise AdmissionDenied(str(exc)) from exc
            return identity(receipt)
        from .file_reads import source_path
        path = source_path(file_args, task)
        required = _material_reference(row, path)
        if required is not None:
            if root is None:
                raise AdmissionDenied('required material reads need the native authority root')
            try:
                if required.get('kind') == 'assignment':
                    from .context_packets import verify_material
                    from .continuity_store import ContinuityStore
                    with ContinuityStore.reader(root) as store:
                        verified = verify_material(store, row['run_id'], path,
                                                   checkpoint_id=row['payload']['checkpoint_id'])
                else:
                    from .prompt_compiler import verify_material
                    verified = verify_material(root, row['payload']['worker_id'], Path(row['payload']['manifest']),
                                               path, workdir=task['workdir'])
            except (HxError, OSError) as exc:
                raise AdmissionDenied('required instruction material failed bundle or content verification') from exc
            if verified != required:
                raise AdmissionDenied('required instruction material differs from the admitted launch')
        from .file_reads import prepare, identity
        try:
            _, receipt = prepare(row, task, observation, root=root)
        except (HxError, OSError) as exc:
            raise AdmissionDenied(str(exc)) from exc
        return identity(receipt)
    if name in {'Grep', 'grep', 'search', 'Glob', 'glob', 'find', 'ls'}:
        _source(args, task)
        limit = args.get('head_limit', args.get('limit', args.get('max_results')))
        if type(limit) is not int or not 1 <= limit <= SEARCH_RESULTS:
            raise AdmissionDenied('search requires a named scope and at most 80 results; if the tool has no limit, use hx tool-exec')
    if name == 'list_dir':
        raise AdmissionDenied('native list_dir cannot accept a result bound; use hx tool-exec --request ID -- ls PATH')
    if name in {'Bash', 'bash', 'exec_command', 'shell', 'shell_command', 'run_shell_command', 'run_terminal_cmd'}:
        command = args.get('command', args.get('cmd', ''))
        # These common unbounded inspections have native bounded equivalents.
        # This is an efficiency gate, not an attempt to sandbox arbitrary code.
        if isinstance(command, str):
            try:
                commands = list(_segments(command))
            except ValueError:
                return None
            from .file_inspection import require_non_file_output
            try:
                require_non_file_output(['sh', '-c', command])
            except HxError as exc:
                raise AdmissionDenied(str(exc)) from exc
            for parts, piped in commands:
                program, args = _program(parts)
                if program in NOISY or (program, _subcommand(args)) in NOISY_SUBCOMMANDS:
                    raise AdmissionDenied('run build/test/diff output through hx tool-exec --request ID -- COMMAND, or use an assigned hx check')
                if program in READERS or program == 'sed' and any(part.startswith('-n') for part in args):
                    raise AdmissionDenied('use the native complete-file read tool for files or bounded search for trees')
                # Piped input is command output being filtered, not a tree search.
                if program in {'rg', 'grep'} and not piped:
                    raise AdmissionDenied('use scoped Grep with head_limit, or hx tool-exec --request ID -- rg PATTERN PATH for bounded output')
    return None


def verify_material_read(tx, row, task, observation, reading):
    """Recheck a verified immutable read at the final admission transaction."""
    if reading is None:
        return
    name = observation.get('tool_name', observation.get('toolName', ''))
    name = name.rsplit('.', 1)[-1] if isinstance(name, str) else ''
    from .file_reads import read_input
    args = read_input(observation)
    if args is None:
        return
    if 'repo_args' in args:
        return  # A fixed Git object is revalidated by the emission broker.
    from .file_reads import source_path
    path = source_path(args, task)
    receipt = json.loads(reading[1])
    try:
        if any(_stamp(Path(part['path'])) != part['stamp'] for part in receipt['files']):
            raise AdmissionDenied('file changed before native read admission')
    except OSError as exc:
        raise AdmissionDenied('file disappeared before native read admission') from exc
    required = _material_reference(row, path)
    if required is None:
        return
    if required.get('kind') == 'assignment':
        from .context_packets import _read
        current = tx.db.execute('SELECT checkpoint_id FROM runs WHERE run_id=?', (row['run_id'],)).fetchone()
        try:
            packet = _read(tx, row['payload']['checkpoint_id'], row['run_id'])
        except (HxError, OSError) as exc:
            raise AdmissionDenied('required assignment changed before native tool admission') from exc
        if current is None or current[0] != row['payload']['checkpoint_id'] or required not in packet['required_files']:
            raise AdmissionDenied('required assignment checkpoint changed before native tool admission')
    try:
        before = _stamp(path)
        with path.open('rb') as handle:
            data = handle.read(required['bytes'] + 1)
        after = _stamp(path)
    except OSError as exc:
        raise AdmissionDenied('required material disappeared before native tool admission') from exc
    expected_stamp = next(part['stamp'] for part in receipt['files'] if Path(part['path']).resolve() == path)
    if before != expected_stamp or after != before or len(data) != required['bytes'] or hashlib.sha256(data).hexdigest() != required['sha256']:
        raise AdmissionDenied('required material changed before native tool admission')


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
        raise AdmissionDenied(f'complete unchanged file already delivered in call {prior[0]}; use that result')
    tx.db.execute('INSERT INTO native_reads VALUES(?,?,?,?,?,?,?,0)',
        (row['launch_id'], session, actor, call, checkpoint, key, stamp))


def settled(tx, launch_id, session, actor, call, payload):
    from .file_reads import delivered
    found = tx.db.execute('SELECT source_stamp FROM native_reads WHERE launch_id=? AND session_id=? AND actor_id=? AND call_id=?',
                         (launch_id, session, actor, call)).fetchone()
    if found is None:
        return
    success = delivered(json.loads(found[0]), payload)
    tx.db.execute('UPDATE native_reads SET success=? WHERE launch_id=? AND session_id=? AND actor_id=? AND call_id=?',
        (1 if success else -1, launch_id, session, actor, call))


def require_complete_reads(tx, row, session, actor, reading):
    """Incomplete native output never authorizes a later action or completion."""
    pending = tx.db.execute('''SELECT r.call_id FROM native_reads r JOIN native_tool_calls c
        USING(launch_id,session_id,actor_id,call_id) WHERE r.launch_id=? AND r.session_id=?
        AND r.actor_id=? AND r.checkpoint_id=? AND c.status='admitted' AND r.success<>1 LIMIT 1''',
        (row['launch_id'], session, actor, row['payload']['checkpoint_id'])).fetchone()
    if pending and reading is None:
        raise AdmissionDenied('complete-file transport is still pending for call ' + pending[0] +
                              '; wait for every automatic transport segment before another operation')
    unresolved = tx.db.execute('''SELECT r.read_key,r.call_id FROM native_reads r
        WHERE r.launch_id=? AND r.session_id=? AND r.actor_id=? AND r.checkpoint_id=? AND r.success=-1
        AND NOT EXISTS (SELECT 1 FROM native_reads ok WHERE ok.launch_id=r.launch_id
            AND ok.session_id=r.session_id AND ok.actor_id=r.actor_id AND ok.checkpoint_id=r.checkpoint_id
            AND ok.read_key=r.read_key AND ok.success=1 AND ok.rowid>r.rowid)''',
        (row['launch_id'], session, actor, row['payload']['checkpoint_id'])).fetchall()
    if unresolved and (reading is None or reading[0] not in {item[0] for item in unresolved}):
        raise AdmissionDenied('complete-file delivery failed in call ' + unresolved[0][1] +
                              '; retry that whole file before another operation')
    from .file_reads import incomplete_material
    missing = incomplete_material(tx, row['launch_id'], session=session, actor=actor,
                                  checkpoint=row['payload']['checkpoint_id'])
    if missing and reading is None:
        path, part, parts = missing[0]
        raise AdmissionDenied(f'required material is incomplete; read {path} next (part {part} of {parts}) '
                              'before another operation')
