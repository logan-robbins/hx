"""Durable, replayable graph changesets with exact source identity and modes.

`base` is the checkout's own binding when prepared (the optimistic check);
`snapshot_base` is the parent map, which may be a sibling checkout's snapshot.
"""

from __future__ import annotations

import time

from . import code_snapshot as snapshot

SCHEMA = """
CREATE TABLE IF NOT EXISTS code_changesets(identity TEXT PRIMARY KEY,repo TEXT NOT NULL,root TEXT NOT NULL,base TEXT,head TEXT,generation TEXT NOT NULL,complete INTEGER NOT NULL,state TEXT NOT NULL,created_at REAL NOT NULL,snapshot TEXT,dirty INTEGER NOT NULL DEFAULT 0,snapshot_base TEXT,resolved BLOB,dirty_paths BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS code_changes(identity TEXT NOT NULL,path TEXT NOT NULL,operation TEXT NOT NULL,sha TEXT,mode TEXT,extraction TEXT,reason TEXT,size INTEGER,mtime_ns INTEGER,PRIMARY KEY(identity,path));
"""


def prepare(
    conn,
    repo,
    root,
    base,
    head,
    generation,
    changes,
    *,
    complete,
    snapshot_base=None,
    dirty=False,
    dirty_paths=(),
    resolved=None,
):
    dirty_paths = sorted(dirty_paths)
    identity = snapshot.digest(
        [
            repo,
            str(root),
            base,
            snapshot_base,
            head,
            generation,
            complete,
            changes,
            dirty,
            dirty_paths,
            resolved,
        ]
    )
    conn.execute(
        "INSERT OR IGNORE INTO code_changesets(identity,repo,root,base,head,generation,complete,state,created_at,snapshot,dirty,snapshot_base,resolved,dirty_paths) VALUES(?,?,?,?,?,?,?,'prepared',?,NULL,?,?,?,?)",
        (
            identity,
            repo,
            str(root),
            base,
            head,
            generation,
            int(complete),
            time.time(),
            int(dirty),
            snapshot_base,
            snapshot.packed(resolved) if resolved else None,
            snapshot.packed(dirty_paths),
        ),
    )
    for row in changes:
        conn.execute(
            "INSERT OR IGNORE INTO code_changes VALUES(?,?,?,?,?,?,?,?,?)",
            (
                identity,
                row["path"],
                row["operation"],
                row.get("sha"),
                row.get("mode"),
                row.get("extraction"),
                row.get("reason"),
                row.get("size"),
                row.get("mtime_ns"),
            ),
        )
    conn.commit()
    return identity


def replay(root, identity, *, repo=None):
    """Replay exactly the durable objects; never re-read a changing checkout."""
    from . import code_store
    from .code_build import apply_changeset

    conn = code_store.connect(root, existing=True)
    if not conn:
        raise ValueError("code journal is missing")
    try:
        if (
            repo is not None
            and not conn.execute(
                "SELECT 1 FROM code_changesets WHERE identity=? AND repo=?",
                (identity, repo),
            ).fetchone()
        ):
            raise ValueError("changeset does not belong to the namespace")
        return apply_changeset(conn, identity)
    finally:
        conn.close()


def pending(root):
    from .code_store import connect

    conn = connect(root, existing=True)
    if not conn:
        return []
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT * FROM code_changesets WHERE state='prepared' ORDER BY created_at,identity"
            )
        ]
    finally:
        conn.close()


def list_changesets(root, *, repo=None, limit=100):
    from .code_store import connect

    conn = connect(root, existing=True)
    if not conn:
        return []
    try:
        return [
            dict(r)
            for r in conn.execute(
                "SELECT identity,repo,root,base,head,generation,complete,state,created_at,snapshot,dirty,snapshot_base,(SELECT count(*) FROM code_changes c WHERE c.identity=j.identity) AS changes FROM code_changesets j WHERE (? IS NULL OR repo=?) ORDER BY created_at DESC,identity LIMIT ?",
                (repo, repo, min(100, limit)),
            )
        ]
    finally:
        conn.close()
