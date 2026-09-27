"""Exact map input bindings and targeted invalidation of their consumers."""

from __future__ import annotations

import json
from pathlib import Path

from . import appmap
from .continuity_store import Conflict, canonical
from .errors import ValidationError


def validate_refs(tx, refs, task, *, current=True):
    if not isinstance(refs, list) or len(refs) > 64:
        raise ValidationError("map inputs must be an array of at most 64 exact references")
    seen, scopes = set(), set()
    for ref in refs:
        if not isinstance(ref, dict) or ref.keys() != {"repository", "snapshot", "id", "version"}:
            raise ValidationError("map input requires repository, snapshot, id, version")
        for field in ("repository", "snapshot", "id"):
            if not isinstance(ref[field], str) or not ref[field] or len(ref[field]) > 512:
                raise ValidationError("invalid map input identity")
        if type(ref["version"]) is not int or ref["version"] < 1:
            raise ValidationError("map input version must be positive")
        key = (ref["repository"], ref["snapshot"], ref["id"])
        if key in seen:
            raise ValidationError("map inputs cannot repeat a record")
        seen.add(key)
        row = tx.db.execute("SELECT * FROM map_records WHERE repository=? AND snapshot=? AND record_id=? AND version=?", (*key, ref["version"])).fetchone()
        if row is None:
            raise Conflict("map input refers to an unknown version")
        if not current:
            continue
        head = tx.db.execute("SELECT version FROM map_heads WHERE repository=? AND snapshot=? AND record_id=?", key).fetchone()
        if head is None or head[0] != ref["version"] or row["applicability"] != "current":
            raise Conflict(f"map input changed or is stale: {ref['id']}")
        if tx.db.execute("SELECT 1 FROM map_refresh_queue WHERE repository=? AND snapshot=? AND record_id=?", key).fetchone():
            raise Conflict(f"map input awaits source refresh: {ref['id']}")
        workdir = task.get("workdir")
        if not isinstance(workdir, str) or not Path(workdir).is_absolute():
            raise ValidationError("map inputs require an absolute task workdir")
        repository = Path(workdir).resolve()
        if key[:2] not in scopes:
            from .map_updates import require_worktree
            if appmap.manifest(repository)["repo_id"] != key[0]:
                raise Conflict("map input repository identity changed")
            require_worktree(tx.db, repository, *key[:2])
            scopes.add(key[:2])
        # A delayed watcher must never make source applicability optimistic.
        for anchor in json.loads(row["payload"])["anchors"]:
            try:
                stamp = appmap._source_stamp(repository, anchor["path"])
                cached = tx.db.execute("SELECT source_stamp,payload FROM map_anchor_cache WHERE repository=? AND worktree=? AND path=? AND symbol=?",
                    (key[0], str(repository), anchor["path"], anchor["symbol"] or "")).fetchone()
                if cached and cached["source_stamp"] == stamp:
                    actual = json.loads(cached["payload"])
                else:
                    captured = appmap.source_anchor(repository, anchor["path"], anchor["symbol"], with_stamp=True)
                    actual, stamp = captured["anchor"], captured["source_stamp"]
                if actual["sha256"] != anchor["sha256"] or appmap._source_stamp(repository, anchor["path"]) != stamp:
                    raise Conflict(f"map input source changed: {ref['id']}")
                if not cached or cached["source_stamp"] != stamp:
                    tx._change()
                    tx.db.execute("INSERT INTO map_anchor_cache VALUES(?,?,?,?,?,?) ON CONFLICT(repository,worktree,path,symbol) DO UPDATE SET source_stamp=excluded.source_stamp,payload=excluded.payload",
                        (key[0], str(repository), anchor["path"], anchor["symbol"] or "", stamp, canonical(actual)))
            except ValidationError as exc:
                raise Conflict(f"map input source unavailable: {ref['id']}") from exc


def bind_task(tx, task_id, revision, payload):
    refs = payload["map_inputs"]
    tx.db.executemany("INSERT INTO task_map_inputs VALUES(?,?,?,?,?,?)",
        ((task_id, revision, ref["repository"], ref["snapshot"], ref["id"], ref["version"]) for ref in refs))


def validate_record_refs(tx, task_id, kind, inputs, validity):
    refs = inputs.get("map", [])
    if refs and kind in {"goal", "constraint", "cursor"}:
        raise ValidationError("binding goals, constraints, and active cursors cannot expire with map facts")
    validate_refs(tx, refs, tx.task(task_id)["payload"], current=validity == "current")


def bind_record(tx, record_id, version, inputs):
    refs = inputs["map"]
    tx.db.executemany("INSERT INTO record_entities VALUES(?,?,?,?,?,?)",
        ((record_id, version, ref["repository"], ref["snapshot"], ref["id"], ref["version"]) for ref in refs))


def validate_task(tx, task):
    validate_refs(tx, task["payload"].get("map_inputs", []), task["payload"])
    if tx.db.execute("SELECT 1 FROM task_replan_queue WHERE task_id=? AND task_revision=? LIMIT 1", (task["task_id"], task["revision"])).fetchone():
        raise Conflict("task map inputs require replanning and explicit revision rebinding")


def invalidate_consumers(tx, key, entity_id, version, reason):
    """Called in the map mutation transaction; only exact consumers are affected."""
    tx._change()
    counts = {"facts": 0, "receipts": 0, "tasks": 0}
    # Keyset iteration survives new invalid versions without collecting consumer
    # bodies or a potentially large ID list in memory.
    after = ""
    while True:
        row = tx.db.execute("""SELECT r.record_id FROM record_entities e
            JOIN record_heads h ON h.record_id=e.record_id AND h.version=e.version
            JOIN records r ON r.record_id=h.record_id AND r.version=h.version
            WHERE e.repository=? AND e.snapshot=? AND e.entity_id=? AND e.entity_version<?
              AND r.validity='current' AND r.record_id>? ORDER BY r.record_id LIMIT 1""",
            (*key, entity_id, version, after)).fetchone()
        if row is None:
            break
        after = row[0]
        old = tx.record(after)
        if old["kind"] in {"goal", "constraint", "cursor"}:
            continue
        tx.put_record(after, expected_version=old["version"], task_id=old["task_id"], kind=old["kind"],
            payload=old["payload"], evidence=old["evidence"], inputs=old["inputs"], reason=reason,
            expires_when=old["expires_when"], retention=old["retention"], consuming_step=old["consuming_step"], validity="invalid")
        counts["facts"] += 1
        task = tx.task(old["task_id"])
        counts["receipts"] += tx.db.execute("UPDATE receipts SET valid=0 WHERE valid=1 AND run_id IN (SELECT run_id FROM runs WHERE task_id=? AND task_revision=?)", (old["task_id"], task["revision"])).rowcount
        tx.db.execute("""INSERT INTO task_replan_queue VALUES(?,?,?,?,?,?,?)
            ON CONFLICT(task_id,task_revision,repository,snapshot,entity_id)
            DO UPDATE SET entity_version=excluded.entity_version,reason=excluded.reason""",
            (old["task_id"], task["revision"], *key, entity_id, version, reason))
    counts["receipts"] += tx.db.execute("""UPDATE receipts SET valid=0 WHERE valid=1 AND run_id IN (
        SELECT r.run_id FROM runs r JOIN task_map_inputs i ON i.task_id=r.task_id AND i.task_revision=r.task_revision
        WHERE i.repository=? AND i.snapshot=? AND i.entity_id=? AND i.entity_version<?)""",
        (*key, entity_id, version)).rowcount
    tx.db.execute("""INSERT INTO task_replan_queue
        SELECT i.task_id,i.task_revision,i.repository,i.snapshot,i.entity_id,?,? FROM task_map_inputs i
        JOIN task_heads h ON h.task_id=i.task_id AND h.revision=i.task_revision
        WHERE i.repository=? AND i.snapshot=? AND i.entity_id=? AND i.entity_version<?
        ON CONFLICT(task_id,task_revision,repository,snapshot,entity_id)
        DO UPDATE SET entity_version=excluded.entity_version,reason=excluded.reason""",
        (version, reason, *key, entity_id, version))
    counts["tasks"] = tx.db.execute("SELECT count(*) FROM task_replan_queue q JOIN task_heads h ON h.task_id=q.task_id AND h.revision=q.task_revision WHERE repository=? AND snapshot=? AND entity_id=? AND entity_version=?", (*key, entity_id, version)).fetchone()[0]
    return counts
