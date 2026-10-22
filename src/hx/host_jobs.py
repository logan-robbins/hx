"""A per-user, per-boot host ledger of running executions by portable input identity.

Every hx root on the box claims here before it starts an equal-input .NET build, test
shard or generic check, so one execution serves every root and worktree whose inputs
match. A request whose identity is held by a live execution joins it: it waits on that
execution and receives its outcome, then records its own receipt. The holder publishes
the outcome when it finishes; joiners never start a process.

A claim belongs to the process that made it by kernel process identity, never by bare
PID or age. A holder that gives up without an outcome while alive (an exception) is
interrupted and refuses equal requests until reconciliation proves it quiescent. A claim
whose owner is gone, with no spawn in flight and every recorded child gone, is reclaimed
by the next equal request. Finished outcomes stay an hour for joiners that wake late.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from . import native_processes
from .continuity_store import Conflict, canonical

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (job TEXT PRIMARY KEY, identity TEXT NOT NULL, root TEXT NOT NULL,
 owner TEXT NOT NULL, children TEXT NOT NULL, spawning INTEGER NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','interrupted','finished')), result TEXT, stamp REAL NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS jobs_unresolved ON jobs(identity) WHERE status<>'finished';
CREATE INDEX IF NOT EXISTS jobs_identity ON jobs(identity,status,stamp)
"""
RETAIN_S = 3600


def directory():
    return Path(tempfile.gettempdir()) / f'hx-jobs-{os.getuid()}'


def connect():
    # Boot identity isolates stale host state; HOME is never read or replaced.
    boot = hashlib.sha256(native_processes.boot().encode()).hexdigest()[:16]
    folder = directory()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.is_symlink() or folder.stat().st_uid != os.getuid():
        raise Conflict('host job directory has unsafe ownership')
    db = sqlite3.connect(folder / f'{boot}.sqlite', timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    for statement in SCHEMA.split(';'):
        if statement.strip():
            db.execute(statement)
    return db


def _row(row):
    value = dict(row)
    value['owner'] = json.loads(value['owner'])
    value['children'] = json.loads(value['children'])
    value['result'] = json.loads(value['result']) if value['result'] else None
    return value


def _quiescent(row):
    from .check_artifacts import group_present
    if row['spawning']:
        return False
    try:
        return all((child['identity'] is None or native_processes.current(child['identity']) is None)
                   and not group_present(child['group']) for child in json.loads(row['children']))
    except Conflict:
        return False


def claim(identity, root, job, *, since, skip=()):
    """Claim `identity` for `job`: None once `job` holds it, else the execution to join.

    The execution is a live running holder, or one that finished at or after `since` (it
    ran while this request was preparing); finished jobs named in `skip` are passed over.
    Raises Conflict while an interrupted or dead, not provably quiescent holder remains.
    """
    owner = native_processes.probe(os.getpid())
    if owner is None:
        raise Conflict('host job owner identity is unavailable')
    now = time.time()
    with closing(connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        db.execute("DELETE FROM jobs WHERE status='finished' AND stamp<?", (now - RETAIN_S,))
        row = db.execute("SELECT * FROM jobs WHERE identity=? AND status<>'finished'", (identity,)).fetchone()
        if row is not None:
            alive = native_processes.current(json.loads(row['owner'])) is not None
            if alive and row['status'] == 'running':
                db.execute('COMMIT')
                return _row(row)
            if alive or not _quiescent(row):
                raise Conflict('equal inputs have an unresolved execution; reconcile its process ownership before retrying')
            db.execute('DELETE FROM jobs WHERE job=?', (row['job'],))
        finished = [item for item in db.execute(
            "SELECT * FROM jobs WHERE identity=? AND status='finished' AND stamp>=? ORDER BY stamp DESC",
            (identity, since)).fetchall() if item['job'] not in skip]
        if finished:
            db.execute('COMMIT')
            return _row(finished[0])
        db.execute("INSERT INTO jobs VALUES(?,?,?,?,'[]',0,'running',NULL,?)",
                   (job, identity, str(root), canonical(owner['identity']), now))
        db.execute('COMMIT')
    return None


def _update(job, statement, values):
    with closing(connect()) as db:
        db.execute(statement, (*values, job))


def spawning(job, flag=True):
    """Mark a spawn in flight (until its child is recorded, the claim cannot be reclaimed),
    or clear the mark once the spawn returned without a child."""
    _update(job, "UPDATE jobs SET spawning=? WHERE job=? AND status='running'", (int(flag),))


def child(job, value):
    with closing(connect()) as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute('SELECT children FROM jobs WHERE job=?', (job,)).fetchone()
        if row is not None:
            db.execute('UPDATE jobs SET children=?,spawning=0 WHERE job=?',
                       (canonical([*json.loads(row[0]), value]), job))
        db.execute('COMMIT')


def finish(job, outcome):
    _update(job, "UPDATE jobs SET status='finished',result=?,stamp=? WHERE job=? AND status='running'",
            (canonical(outcome), time.time()))


def interrupt(job):
    _update(job, "UPDATE jobs SET status='interrupted' WHERE job=? AND status='running'", ())


def release(job):
    _update(job, 'DELETE FROM jobs WHERE job=?', ())


def wait(job):
    """Wait while `job` runs under a live owner: its outcome once finished, else None."""
    delay = 0.02
    while True:
        with closing(connect()) as db:
            row = db.execute('SELECT * FROM jobs WHERE job=?', (job,)).fetchone()
        if row is None or row['status'] == 'interrupted':
            return None
        if row['status'] == 'finished':
            return json.loads(row['result'])
        if native_processes.current(json.loads(row['owner'])) is None:
            return None
        time.sleep(delay)
        delay = min(delay * 1.5, 0.25)


def get(job):
    with closing(connect()) as db:
        row = db.execute('SELECT * FROM jobs WHERE job=?', (job,)).fetchone()
        return _row(row) if row else None
