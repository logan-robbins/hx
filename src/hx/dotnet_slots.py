"""A per-user, per-boot host-wide build admission ledger across processes."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from . import native_processes
from .continuity_store import Conflict, canonical


def connect():
    # This never touches or replaces HOME. Boot identity isolates stale host
    # state; old records still cannot authorize process-instance signalling.
    import hashlib
    boot = hashlib.sha256(native_processes.boot().encode()).hexdigest()[:16]
    folder = Path(tempfile.gettempdir()) / f'hx-dotnet-host-{os.getuid()}'
    folder.mkdir(mode=0o700, exist_ok=True)
    if folder.is_symlink() or folder.stat().st_uid != os.getuid():
        raise Conflict('host build admission directory has unsafe ownership')
    db = sqlite3.connect(folder / f'{boot}.sqlite', timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute('CREATE TABLE IF NOT EXISTS slots (job TEXT PRIMARY KEY, owner TEXT NOT NULL, children TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS settings (name TEXT PRIMARY KEY,value INTEGER NOT NULL)')
    return db


def acquire(job, limit=None):
    with closing(connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        existing = db.execute('SELECT * FROM slots WHERE job=?', (job,)).fetchone()
        if existing:
            raise Conflict('build already owns an unresolved host slot')
        # Active slots are never inferred dead from time or bare PIDs. Only the
        # explicit durable job reconciliation releases them.
        if limit is not None:
            db.execute("INSERT INTO settings VALUES('limit',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value", (limit,))
        configured = db.execute("SELECT value FROM settings WHERE name='limit'").fetchone()
        limit = configured[0] if configured else 2
        if db.execute('SELECT count(*) FROM slots').fetchone()[0] >= limit:
            raise Conflict('host build concurrency limit reached; retry the queued request later')
        owner = native_processes.probe(os.getpid())
        if owner is None:
            raise Conflict('build host slot owner identity is unavailable')
        db.execute('INSERT INTO slots VALUES(?,?,?)', (job, canonical(owner['identity']), '[]'))
        db.commit()


def child(job, value):
    with closing(connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT children FROM slots WHERE job=?', (job,)).fetchone()
        if row is None:
            raise Conflict('host build slot disappeared during spawn')
        children = json.loads(row[0])
        children.append(value)
        db.execute('UPDATE slots SET children=? WHERE job=?', (canonical(children), job))
        db.commit()


def release(job):
    with closing(connect()) as db:
        db.execute('DELETE FROM slots WHERE job=?', (job,))


def metadata(job):
    with closing(connect()) as db:
        row = db.execute('SELECT * FROM slots WHERE job=?', (job,)).fetchone()
        return {'owner': json.loads(row['owner']), 'children': json.loads(row['children'])} if row else None
