"""Bounded reuse of exact searches against unchanged, explicitly scoped sources."""
from __future__ import annotations

import json
import os
from pathlib import Path

from .continuity_store import canonical, digest
from .native_tools import AdmissionDenied

TOOLS = {'Grep', 'grep', 'search', 'Glob', 'glob', 'find', 'ls'}


def fingerprint(path):
    """Metadata only, at most 256 entries; larger scopes remain uncached."""
    pending, stamps = [Path(path)], []
    try:
        while pending:
            current = pending.pop()
            info = current.lstat()
            if current.is_symlink():
                return None  # Do not mistake link metadata for its target's contents.
            stamps.append((str(current), info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns))
            if len(stamps) > 256:
                return None
            if current.is_dir():
                with os.scandir(current) as entries:
                    for entry in entries:
                        # Include hidden files: the native query may search them.
                        if len(pending) + len(stamps) >= 256:
                            return None
                        pending.append(Path(entry.path))
    except OSError:
        return None
    return digest(sorted(stamps))


def prepare(store, row, task, payload):
    name = payload.get('tool_name', payload.get('toolName', ''))
    name = name.rsplit('.', 1)[-1] if isinstance(name, str) else ''
    args = payload.get('tool_input', payload.get('toolInput'))
    if name not in TOOLS or not isinstance(args, dict):
        return None
    from .runtime_policy import _source
    path = _source(args, task)
    repository = Path(task['workdir']).resolve()
    if not path.is_relative_to(repository):
        return None
    stamp = fingerprint(path)
    if stamp is None:
        return None
    key = digest([name, {**args, 'path': str(path)}])
    return dict(repository=str(repository), query_hash=key, source_hash=stamp, path=str(path), run_id=row['run_id'])


def remember(tx, row, session, actor, call, request):
    if request:
        known = tx.db.execute('SELECT * FROM search_coverage WHERE repository=? AND query_hash=? AND source_hash=?',
                                 (request['repository'], request['query_hash'], request['source_hash'])).fetchone()
        if known:
            status = 'Complete result' if known['complete'] else 'Partial result; absence is not established'
            raise AdmissionDenied(f'Cached search for unchanged scoped sources. {status}: {known["result"]}\nReuse this result; narrow or change the query for additional coverage.')
        tx.db.execute('INSERT INTO search_calls VALUES(?,?,?,?,?)',
                      (row['launch_id'], session, actor, call, canonical(request)))


def settle(tx, launch, session, actor, call, payload):
    key = (launch, session, actor, call)
    row = tx.db.execute('SELECT payload FROM search_calls WHERE launch_id=? AND session_id=? AND actor_id=? AND call_id=?', key).fetchone()
    if not row:
        return
    tx.db.execute('DELETE FROM search_calls WHERE launch_id=? AND session_id=? AND actor_id=? AND call_id=?', key)
    request = json.loads(row[0])
    response = payload.get('tool_response')
    if payload.get('is_error') or response is None:
        return
    if isinstance(response, dict) and (response.get('error') or response.get('is_error') or response.get('isError')):
        return
    text = response if isinstance(response, str) else canonical(response)
    # Unmarked native output is never treated as exhaustive negative evidence.
    complete = isinstance(response, dict) and response.get('complete') is True and not response.get('truncated')
    if not text.strip() or len(text.encode()) > 2048:
        return
    if fingerprint(request['path']) != request['source_hash']:
        return
    tx.db.execute('INSERT OR REPLACE INTO search_coverage VALUES(?,?,?,?,?,?)',
        (request['repository'], request['query_hash'], request['source_hash'], request['run_id'], text, int(complete)))
    # This is a small current-source cache, not an unbounded cross-task archive.
    tx.db.execute('''DELETE FROM search_coverage WHERE repository=? AND rowid NOT IN
        (SELECT rowid FROM search_coverage WHERE repository=? ORDER BY rowid DESC LIMIT 128)''',
        (request['repository'], request['repository']))
    tx.db.execute('DELETE FROM search_coverage WHERE rowid NOT IN (SELECT rowid FROM search_coverage ORDER BY rowid DESC LIMIT 256)')
