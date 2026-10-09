"""Durable, bounded source refresh for indexed map anchors."""

from __future__ import annotations

import json

from . import appmap
from .continuity_store import canonical
from .errors import ValidationError
from .map_dependencies import invalidate_consumers
from .map_updates import require_worktree


def queue_sources(store, repository, snapshot, paths):
    if not isinstance(paths, list) or not 1 <= len(paths) <= 128:
        raise ValidationError("source refresh accepts 1–128 exact relative paths")
    for path in paths:
        appmap.relative_path(path)
    key = (appmap.manifest(repository)["repo_id"], snapshot)
    with store.transaction() as tx:
        require_worktree(tx.db, repository, *key)
        tx._change()
        generation = tx.db.execute("SELECT revision+1 FROM ledger_meta").fetchone()[0]
        for path in dict.fromkeys(paths):
            tx.db.execute("""INSERT INTO map_refresh_queue
                SELECT repository,snapshot,record_id,? FROM map_sources
                WHERE repository=? AND snapshot=? AND path=? GROUP BY record_id
                ON CONFLICT(repository,snapshot,record_id) DO UPDATE SET generation=excluded.generation""",
                (generation, *key, path))
        tx.enqueue("map_refresh", f"refresh:{key[0]}:{generation}", {"repository": key[0], "snapshot": snapshot})
    return {"snapshot": snapshot, "generation": generation}


def _stamp(repository, path):
    try:
        return appmap._source_stamp(repository, path)
    except ValidationError as exc:
        return "unavailable:" + str(exc)


def _inspect(store, repository, key, row):
    record = json.loads(row["payload"])
    observations, captures, failures = {}, {}, []
    for anchor in record["anchors"]:
        path, symbol = anchor["path"], anchor["symbol"] or ""
        observations.setdefault(path, _stamp(repository, path))
        try:
            cache = store.db.execute("SELECT source_stamp,payload FROM map_anchor_cache WHERE repository=? AND worktree=? AND path=? AND symbol=?",
                (key[0], str(repository.resolve()), path, symbol)).fetchone()
            if cache and cache["source_stamp"] == observations[path]:
                captured = {"anchor": json.loads(cache["payload"]), "source_stamp": cache["source_stamp"]}
            else:
                captured = appmap.source_anchor(repository, path, anchor["symbol"], with_stamp=True)
            captures[(path, symbol)] = captured
            if captured["anchor"]["sha256"] != anchor["sha256"]:
                failures.append({"path": path, "expected": anchor["sha256"], "actual": captured["anchor"]["sha256"]})
        except ValidationError as exc:
            failures.append({"path": path, "error": str(exc)})
    return record, observations, captures, failures


def drain(store, repository, snapshot, *, limit=16):
    if type(limit) is not int or not 1 <= limit <= 32:
        raise ValidationError("source refresh batch limit must be 1–32 records")
    key = (appmap.manifest(repository)["repo_id"], snapshot)
    require_worktree(store.db, repository, *key)
    # Headers only. Each selected record body is released before the next read.
    pending = store.db.execute("SELECT record_id,generation FROM map_refresh_queue WHERE repository=? AND snapshot=? ORDER BY record_id LIMIT ?", (*key, limit)).fetchall()
    refreshed, invalidated = [], []
    for item in pending:
        row = store.db.execute("SELECT r.* FROM map_records r JOIN map_heads h USING(repository,snapshot,record_id,version) WHERE repository=? AND snapshot=? AND record_id=?", (*key, item["record_id"])).fetchone()
        if row is None:
            raise ValidationError("source refresh queue has an absent map record")
        record, observations, captures, failures = _inspect(store, repository, key, row)
        with store.transaction() as tx:
            require_worktree(tx.db, repository, *key)
            current = tx.db.execute("SELECT h.version,q.generation FROM map_heads h JOIN map_refresh_queue q USING(repository,snapshot,record_id) WHERE repository=? AND snapshot=? AND record_id=?", (*key, item["record_id"])).fetchone()
            if current is None or current["version"] != row["version"] or current["generation"] != item["generation"]:
                continue
            if any(_stamp(repository, path) != stamp for path, stamp in observations.items()):
                continue
            tx._change()
            if failures and row["applicability"] == "current":
                record["version"] += 1
                tx.db.execute("INSERT INTO map_records VALUES(?,?,?,?,?,?,?,?,?)",
                    (*key, record["id"], record["version"], record["kind"], canonical(record), row["evidence"],
                     canonical({"anchors": record["anchors"], "source_invalidation": failures}), "stale"))
                tx.db.execute("UPDATE map_heads SET version=? WHERE repository=? AND snapshot=? AND record_id=?", (record["version"], *key, record["id"]))
                tx.db.execute("UPDATE map_relations SET status='stale' WHERE repository=? AND snapshot=? AND status='validated' AND (source=? OR target=?)", (*key, record["id"], record["id"]))
                counts = invalidate_consumers(tx, key, record["id"], record["version"], "Source or configuration evidence changed.")
                tx.enqueue("map_changed", f"source:{key[0]}:{snapshot}:{record['id']}:{record['version']}",
                    {"repository": key[0], "snapshot": snapshot, "changed": [record["id"]], "version": record["version"], "invalidated": counts})
                invalidated.append(record["id"])
            for (path, symbol), captured in captures.items():
                tx.db.execute("INSERT INTO map_anchor_cache VALUES(?,?,?,?,?,?) ON CONFLICT(repository,worktree,path,symbol) DO UPDATE SET source_stamp=excluded.source_stamp,payload=excluded.payload",
                    (key[0], str(repository.resolve()), path, symbol, captured["source_stamp"], canonical(captured["anchor"])))
            tx.db.execute("DELETE FROM map_refresh_queue WHERE repository=? AND snapshot=? AND record_id=? AND generation=?", (*key, record["id"], item["generation"]))
            refreshed.append(record["id"])
    more = store.db.execute("SELECT 1 FROM map_refresh_queue WHERE repository=? AND snapshot=? LIMIT 1", key).fetchone() is not None
    return {"snapshot": snapshot, "refreshed": refreshed, "invalidated": invalidated, "more": more}
