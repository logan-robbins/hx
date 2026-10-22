"""A per-user, per-boot host ledger of .NET build and test slots every hx root shares.

Each root's config/hx.json `pools.build` and `pools.test` (1–64, defaults 2 and 4) bound
that kind of slot across the box when that root acquires one: the root is refused while the
box already holds as many slots of the kind as its own limit allows, so roots configured
differently never together exceed the largest limit. A slot belongs to the process that
acquired it by kernel process identity, never by bare PID or age; a slot whose owner and
every recorded child are gone is reclaimed at the next acquisition.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

from . import loop_jobs, native_processes
from .continuity_store import Conflict, canonical

POOLS = {'build': 2, 'test': 4}
SCHEMA = 'CREATE TABLE IF NOT EXISTS slots (job TEXT PRIMARY KEY, pool TEXT NOT NULL, owner TEXT NOT NULL, children TEXT NOT NULL)'


def directory():
    return Path(tempfile.gettempdir()) / f'hx-slots-{os.getuid()}'


def connect():
    # Boot identity isolates stale host state; HOME is never read or replaced.
    boot = hashlib.sha256(native_processes.boot().encode()).hexdigest()[:16]
    folder = directory()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.is_symlink() or folder.stat().st_uid != os.getuid():
        raise Conflict('host slot directory has unsafe ownership')
    db = sqlite3.connect(folder / f'{boot}.sqlite', timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute(SCHEMA)
    return db


def limit(root, pool):
    """The root's configured slot count for `pool`."""
    return loop_jobs.setting(root, 'pools', pool, POOLS[pool], low=1, high=64)


def _gone(child):
    from .check_artifacts import group_present
    try:
        return ((child['identity'] is None or native_processes.current(child['identity']) is None)
                and not group_present(child['group']))
    except Conflict:
        return False


def _reclaim(db):
    for row in db.execute('SELECT job,owner,children FROM slots').fetchall():
        if native_processes.current(json.loads(row['owner'])) is None and all(_gone(child) for child in json.loads(row['children'])):
            db.execute('DELETE FROM slots WHERE job=?', (row['job'],))


def acquire(job, pool, root):
    owner = native_processes.probe(os.getpid())
    if owner is None:
        raise Conflict('host slot owner identity is unavailable')
    allowed = limit(root, pool)
    with closing(connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute('SELECT 1 FROM slots WHERE job=?', (job,)).fetchone():
            raise Conflict(f'{pool} job already owns an unresolved host slot')
        _reclaim(db)
        if db.execute('SELECT count(*) FROM slots WHERE pool=?', (pool,)).fetchone()[0] >= allowed:
            raise Conflict(f'host {pool} concurrency limit reached; retry the queued request later')
        db.execute('INSERT INTO slots VALUES(?,?,?,?)', (job, pool, canonical(owner['identity']), '[]'))
        db.commit()


def child(job, value):
    with closing(connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT children FROM slots WHERE job=?', (job,)).fetchone()
        if row is None:
            raise Conflict('host slot disappeared during spawn')
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
        return {'pool': row['pool'], 'owner': json.loads(row['owner']), 'children': json.loads(row['children'])} if row else None


def held(pool):
    with closing(connect()) as db:
        return db.execute('SELECT count(*) FROM slots WHERE pool=?', (pool,)).fetchone()[0]
