"""Whole immutable Git-file reads through the native complete-file transport."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from . import repo_service
from .continuity_store import canonical
from .errors import ValidationError


def repository_read_request(root, argv, env=None):
    """Resolve exact hx repo file arguments into text and an immutable receipt."""
    argv = list(argv)
    if argv[:1] == ['file']:
        argv = argv[1:]
    parser = argparse.ArgumentParser(prog='hx repo file', add_help=False, exit_on_error=False)
    parser.add_argument('--root')
    parser.add_argument('--socket')
    parser.add_argument('--namespace', required=True)
    parser.add_argument('--at', default='HEAD')
    parser.add_argument('--path', required=True)
    parser.add_argument('--limit', type=int, default=20)
    try:
        args = parser.parse_args(argv)
    except (argparse.ArgumentError, SystemExit) as exc:
        raise ValidationError('complete repository read requires exact repo file arguments without slices or shell composition') from exc
    root = Path(args.root) if args.root else Path(root)
    body = {'method': 'file', 'namespace': args.namespace, 'at': args.at, 'path': args.path, 'limit': args.limit}
    socket = Path(args.socket) if args.socket else repo_service.configured_socket(root, env)
    result = repo_service.request(socket, body) if socket else repo_service.dispatch(root, body)
    return from_result(result)


def from_result(result):
    text = result.get('text')
    if not isinstance(text, str) or result.get('complete') is not True:
        raise ValidationError('repository file service did not return complete exact source')
    data = text.encode('utf-8')
    sha = hashlib.sha256(data).hexdigest()
    if result.get('bytes') != len(data) or result.get('sha256') != sha:
        raise ValidationError('repository file service returned a mismatched byte count or content hash')
    identity = {'namespace': result['namespace'], 'at': result['at'], 'path': result['path'],
                'object': result['object'], 'mode': result['mode']}
    return text, {'path': 'git:' + canonical(identity), 'bytes': len(data), 'sha256': sha, 'files': [],
                  'repository_file': identity}


def require_admitted(root, receipt, env):
    """A marker is legal only after the native hook reserved this exact read."""
    from .continuity_store import ContinuityStore
    if env.get('HX_CONTINUITY_RUN'):
        with ContinuityStore(Path(env.get('HX_CONTINUITY_AUTHORITY', root))) as ledger:
            reads = ledger.db.execute('''SELECT r.source_stamp FROM native_reads r
                JOIN native_tool_calls c USING(launch_id,session_id,actor_id,call_id)
                JOIN native_launches l USING(launch_id)
                WHERE l.run_id=? AND c.status='admitted' ''', (env['HX_CONTINUITY_RUN'],)).fetchall()
        if not any(json.loads(row[0]) == receipt for row in reads):
            raise ValidationError('repository file read has no matching admitted whole-file operation')
    elif env.get('HARNESS_ID') == 'partner':
        from .partner_file_reads import require_admitted as partner_admission
        partner_admission(root, receipt['path'])
    else:
        raise ValidationError('repository file read requires its managed native run')
