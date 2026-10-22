"""Bounded reuse of exact searches against unchanged, explicitly scoped sources, box-wide.

A search result is keyed by repository namespace, commit, the query with its scope relative
to the checkout, and the scope's content: names for listings, names and file contents for
content searches. One lane's result therefore serves every lane, worktree and root on the box
at that commit whose scope holds the same content, from a per-user, per-boot host ledger.
Paths under the checkout are stored as `{repository}` and served under the reader's own
checkout, so a reused result never names another worktree's files.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import time

from . import fingerprints, host_jobs
from .continuity_store import canonical, digest
from .native_tools import AdmissionDenied

SEARCHES = {'Grep', 'grep', 'search'}
TOOLS = SEARCHES | {'Glob', 'glob', 'find', 'ls'}
TOKEN = '{repository}'
SCHEMA = """
CREATE TABLE IF NOT EXISTS coverage (namespace TEXT NOT NULL, commit_id TEXT NOT NULL, query_hash TEXT NOT NULL,
 source_hash TEXT NOT NULL, result TEXT NOT NULL, complete INTEGER NOT NULL, stamp REAL NOT NULL,
 PRIMARY KEY(namespace,commit_id,query_hash,source_hash))
"""


def directory():
    return Path(tempfile.gettempdir()) / f'hx-search-{os.getuid()}'


def connect():
    return host_jobs.ledger(directory(), SCHEMA)


def fingerprint(path, top, contents):
    """Names relative to `top` and kinds, with sizes for listings or contents for a content
    search: at most 256 entries; larger scopes remain uncached."""
    pending, entries = [Path(path)], []
    try:
        while pending:
            current = pending.pop()
            info = current.lstat()
            name = current.relative_to(top).as_posix()
            if stat.S_ISDIR(info.st_mode):
                entries.append((name, 'directory'))
            elif not stat.S_ISREG(info.st_mode):
                return None  # Links, pipes and devices: never mistake them for contents or block on them.
            elif contents:
                with current.open('rb') as handle:
                    entries.append((name, 'file', hashlib.file_digest(handle, 'sha256').hexdigest()))
            else:
                entries.append((name, 'file', info.st_size))
            if len(entries) > 256:
                return None
            if stat.S_ISDIR(info.st_mode):
                with os.scandir(current) as found:
                    for entry in found:
                        # Include hidden files: the native query may search them.
                        if len(pending) + len(entries) >= 256:
                            return None
                        pending.append(Path(entry.path))
    except OSError:
        return None
    return digest(sorted(entries))


def prepare(task, payload):
    name = payload.get('tool_name', payload.get('toolName', ''))
    name = name.rsplit('.', 1)[-1] if isinstance(name, str) else ''
    args = payload.get('tool_input', payload.get('toolInput'))
    if name not in TOOLS or not isinstance(args, dict):
        return None
    from .runtime_policy import _source
    path = _source(args, task)
    workdir = Path(task['workdir'])
    if not path.is_relative_to(workdir.resolve()):
        return None
    found = fingerprints.checkout(workdir)
    if found is None or found['commit'] is None:
        return None
    top = found['top']
    stamp = fingerprint(path, top, name in SEARCHES)
    if stamp is None:
        return None
    # Results name the checkout as the worker spelled it; serve them back that way.
    spelled = workdir if workdir.resolve() == top else top
    anchors = fingerprints.combine({str(spelled): TOKEN}, fingerprints.anchors_for(workdir))
    request = dict(namespace=found['namespace'], commit=found['commit'], source_hash=stamp,
                   query_hash=digest([name, fingerprints.relocate({**args, 'path': str(path)}, anchors)]),
                   path=str(path), top=str(top), contents=name in SEARCHES, anchors=anchors)
    with closing(connect()) as db:
        known = db.execute('SELECT result,complete FROM coverage WHERE namespace=? AND commit_id=? AND query_hash=? AND source_hash=?',
                           (request['namespace'], request['commit'], request['query_hash'], stamp)).fetchone()
    if known:
        request['cached'] = {'result': known[0].replace(TOKEN + '/', str(spelled) + '/'), 'complete': bool(known[1])}
    return request


def remember(tx, row, session, actor, call, request):
    if request:
        known = request.pop('cached', None)
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
    if not text.strip() or len(text.encode()) > 2048 or TOKEN in text:
        return
    if fingerprint(request['path'], Path(request['top']), request['contents']) != request['source_hash']:
        return
    portable = fingerprints.relocate(text, request['anchors'])
    if any(prefix in portable for prefix in request['anchors']):
        return  # A checkout path that is not a path prefix would name this worktree to others.
    with closing(connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute('INSERT OR REPLACE INTO coverage VALUES(?,?,?,?,?,?,?)',
                   (request['namespace'], request['commit'], request['query_hash'], request['source_hash'],
                    portable, int(complete), time.time()))
        # This is a small current-source cache, not an unbounded cross-task archive.
        db.execute('''DELETE FROM coverage WHERE namespace=? AND rowid NOT IN
            (SELECT rowid FROM coverage WHERE namespace=? ORDER BY rowid DESC LIMIT 128)''',
                   (request['namespace'], request['namespace']))
        db.execute('DELETE FROM coverage WHERE rowid NOT IN (SELECT rowid FROM coverage ORDER BY rowid DESC LIMIT 256)')
        db.execute('COMMIT')
