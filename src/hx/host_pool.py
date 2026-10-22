"""A per-user, per-boot host ledger every hx root on the box shares.

`dotnet_slots` holds build slots this way; this ledger holds the plan executor's box
budgets. Each root reports its census (active runs, sampled native descendants) and
asks for launches in one immediate transaction, so two roots ticking at once never
grant past the box limit together. In-flight launches belong to the process that
admitted them by kernel process identity, never by bare PID or age; a row whose
owner no longer exists is released.

Budgets (all from the root's config, see plan_dispatch.settings):
- `sessions`: active runs across every root on the box, plus launches granted since
  each root's last census.
- `restore`: a token bucket of `per_second` launches with burst and in-flight bound
  `concurrency`, so a box restart or a wide ready set never starts every session at once.
- `growth`: the process-growth breaker; no launch while the sampled native descendants
  of all roots reach `max_descendants` or grew faster than `per_second` over the last
  sample window of at least one second.
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
CREATE TABLE IF NOT EXISTS roots (root TEXT PRIMARY KEY, sessions INTEGER NOT NULL,
 descendants INTEGER NOT NULL, stamp REAL NOT NULL);
CREATE TABLE IF NOT EXISTS launching (launch_key TEXT PRIMARY KEY, root TEXT NOT NULL,
 owner TEXT NOT NULL, started REAL NOT NULL);
CREATE TABLE IF NOT EXISTS samples (name TEXT PRIMARY KEY, level REAL NOT NULL, stamp REAL NOT NULL)
"""


def directory():
    return Path(tempfile.gettempdir()) / f"hx-host-{os.getuid()}"


def connect():
    # Boot identity isolates stale host state; HOME is never read or replaced.
    boot = hashlib.sha256(native_processes.boot().encode()).hexdigest()[:16]
    folder = directory()
    folder.mkdir(mode=0o700, parents=True, exist_ok=True)
    if folder.is_symlink() or folder.stat().st_uid != os.getuid():
        raise Conflict("host pool directory has unsafe ownership")
    db = sqlite3.connect(folder / f"{boot}.sqlite", timeout=10, isolation_level=None)
    db.row_factory = sqlite3.Row
    for statement in SCHEMA.split(";"):
        if statement.strip():
            db.execute(statement)
    return db


def _sample(db, name):
    row = db.execute("SELECT level,stamp FROM samples WHERE name=?", (name,)).fetchone()
    return (row["level"], row["stamp"]) if row else None


def _store(db, name, level, now):
    db.execute("INSERT INTO samples VALUES(?,?,?) ON CONFLICT(name) DO UPDATE SET level=excluded.level,stamp=excluded.stamp",
               (name, level, now))


def admit(root, keys, *, census, descendants, limits, now=None):
    """Grant the longest prefix of `keys` the box budgets allow: `(granted, reason)`.

    `census` is this root's active runs and `descendants` its sampled native process
    count. `reason` names the budget that withheld the rest, or is None.
    """
    root, now = str(root), time.time() if now is None else now
    owner = native_processes.probe(os.getpid())
    if owner is None:
        raise Conflict("host pool owner identity is unavailable")
    with closing(connect()) as db:
        db.execute("BEGIN IMMEDIATE")
        for row in db.execute("SELECT launch_key,owner FROM launching").fetchall():
            if native_processes.current(json.loads(row["owner"])) is None:
                db.execute("DELETE FROM launching WHERE launch_key=?", (row["launch_key"],))
        db.execute("INSERT INTO roots VALUES(?,?,?,?) ON CONFLICT(root) DO UPDATE SET sessions=excluded.sessions,"
                   "descendants=excluded.descendants,stamp=excluded.stamp", (root, census, descendants, now))
        sessions, processes = db.execute("SELECT sum(sessions),sum(descendants) FROM roots").fetchone()
        growth = limits["growth"]
        previous = _sample(db, "descendants")
        rate = _sample(db, "growth")
        if previous is None or now - previous[1] >= 1:
            if previous is not None:
                rate = ((processes - previous[0]) / (now - previous[1]), now)
                _store(db, "growth", rate[0], now)
            _store(db, "descendants", processes, now)
        reason = None
        if processes >= growth["max_descendants"] or (rate is not None and rate[0] > growth["per_second"]):
            granted, reason = 0, "process growth breaker is open"
        else:
            restore = limits["restore"]
            bucket = _sample(db, "restore")
            tokens = restore["concurrency"] if bucket is None else min(
                restore["concurrency"], bucket[0] + max(0.0, now - bucket[1]) * restore["per_second"])
            inflight = db.execute("SELECT count(*) FROM launching").fetchone()[0]
            room = {"box sessions": limits["sessions"] - sessions,
                    "restore concurrency": restore["concurrency"] - inflight,
                    "restore rate": int(tokens)}
            granted = max(0, min(len(keys), *room.values()))
            if granted < len(keys):
                reason = min(room, key=room.get) + " budget is spent"
            _store(db, "restore", tokens - granted, now)
        for key in keys[:granted]:
            db.execute("INSERT OR REPLACE INTO launching VALUES(?,?,?,?)", (key, root, canonical(owner["identity"]), now))
        # Granted launches count against the box until this root's next census sees their runs.
        db.execute("UPDATE roots SET sessions=sessions+? WHERE root=?", (granted, root))
        db.execute("COMMIT")
    return list(keys[:granted]), reason


def release(key):
    with closing(connect()) as db:
        db.execute("DELETE FROM launching WHERE launch_key=?", (key,))


def status():
    with closing(connect()) as db:
        return {"roots": [dict(row) for row in db.execute("SELECT * FROM roots ORDER BY root")],
                "launching": db.execute("SELECT count(*) FROM launching").fetchone()[0]}
