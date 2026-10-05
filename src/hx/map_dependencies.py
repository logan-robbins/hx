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


def owned_input(tx, task, ref, run_id):
    """An executor may finish its own assigned edit, never adopt external changes."""
    run = tx.db.execute('SELECT task_id,task_revision,ended_at FROM runs WHERE run_id=?', (run_id,)).fetchone() if run_id else None
    if not run or run['ended_at'] is not None or (run['task_id'], run['task_revision']) != (task['task_id'], task['revision']):
        return False
    from .map_updates import _record, require_worktree
    key = (ref['repository'], ref['snapshot'])
    current = _record(tx.db, key, ref['id'])
    previous = _record(tx.db, key, ref['id'], ref['version'])
    if not current or not previous:
        return False
    records = [json.loads(row['payload']) for row in (previous, current)]
    if any(record['kind'] == 'check' or not record['anchors'] for record in records):
        return False  # Check/acceptance changes always require a revised assignment.
    metadata = json.loads(current['inputs'])
    if current['version'] != ref['version'] and metadata.get('patch_id'):
        writer = tx.db.execute('SELECT run_id FROM map_patches WHERE repository=? AND patch_id=?', (key[0], metadata['patch_id'])).fetchone()
        if not writer or writer[0] != run_id:
            return False
    root = Path(task['payload']['workdir']).resolve()
    require_worktree(tx.db, root, *key)
    from .planning import write_scope
    leases = [row[0] for row in tx.db.execute('SELECT canonical_path FROM leases WHERE repository=? AND run_id=?', (key[0], run_id))]
    for record in records:
        for anchor in record['anchors']:
            path = write_scope(root, anchor['path'])
            if not any(path == owner or path.startswith(owner + '/') for owner in leases):
                return False
    return True


def validate_task(tx, task, *, active_run=None):
    completed_bindings = set()
    for ref in task['payload'].get('map_inputs', []):
        try:
            validate_refs(tx, [ref], task['payload'])
        except Conflict:
            if owned_input(tx, task, ref, active_run):
                continue
            published = tx.db.execute('''SELECT p.version FROM unit_completions c
                JOIN map_publications p USING(run_id) WHERE c.task_id=? AND c.task_revision=?
                AND p.repository=? AND p.snapshot=? AND p.record_id=?''',
                (task['task_id'], task['revision'], ref['repository'], ref['snapshot'], ref['id'])).fetchone()
            if not published:
                raise
            # A completed edit's current output map can supersede its original
            # input map. Acceptance and the assignment itself remain immutable.
            validate_refs(tx, [{**ref, 'version': published[0]}], task['payload'])
            completed_bindings.add((ref['repository'], ref['snapshot'], ref['id']))
    for row in tx.db.execute('SELECT * FROM task_replan_queue WHERE task_id=? AND task_revision=?', (task['task_id'], task['revision'])):
        if (row['repository'], row['snapshot'], row['entity_id']) in completed_bindings:
            continue
        ref = dict(repository=row['repository'], snapshot=row['snapshot'], id=row['entity_id'], version=max(1, row['entity_version'] - 1))
        if not owned_input(tx, task, ref, active_run):
            raise Conflict('task map inputs require replanning and explicit revision rebinding')


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
    # An active owner is implementing this change under its existing acceptance
    # contract. Do not ask the Partner to reassign each intermediate source edit.
    for row in tx.db.execute('''SELECT q.task_id,r.run_id FROM task_replan_queue q JOIN runs r
        ON r.task_id=q.task_id AND r.task_revision=q.task_revision WHERE r.ended_at IS NULL
        AND q.repository=? AND q.snapshot=? AND q.entity_id=? AND q.entity_version=?''', (*key, entity_id, version)).fetchall():
        task = tx.task(row['task_id'])
        ref = dict(repository=key[0], snapshot=key[1], id=entity_id, version=max(1, version - 1))
        if owned_input(tx, task, ref, row['run_id']):
            tx.db.execute('DELETE FROM task_replan_queue WHERE task_id=? AND task_revision=? AND repository=? AND snapshot=? AND entity_id=?', (task['task_id'], task['revision'], *key, entity_id))
    counts["tasks"] = tx.db.execute("SELECT count(*) FROM task_replan_queue q JOIN task_heads h ON h.task_id=q.task_id AND h.revision=q.task_revision WHERE repository=? AND snapshot=? AND entity_id=? AND entity_version=?", (*key, entity_id, version)).fetchone()[0]
    return counts
