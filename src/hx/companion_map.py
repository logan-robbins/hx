"""Selected map context and atomic map updates within a frozen companion pass."""

from __future__ import annotations

import json
from pathlib import Path

from . import appmap, map_updates
from .continuity_store import Conflict, canonical
from .errors import ValidationError
from .facts import RequiredContextOverflow

MAX_RECORDS = 8


def assignment_selection(store, run_id):
    """Reuse declared map inputs and their direct edges; no repository search."""
    from .passes import _active_run
    with store.transaction() as tx:
        run = _active_run(tx, run_id)
        task = tx.task(run['task_id'])
        refs = task['payload'].get('map_inputs', [])
        if not refs:
            return None, ()
        scopes = {(ref['repository'], ref['snapshot']) for ref in refs}
        if len(scopes) != 1:
            raise ValidationError('one companion pass requires one assignment map snapshot')
        repository, snapshot = next(iter(scopes))
        ids = list(dict.fromkeys(ref['id'] for ref in refs))
        for source in tuple(ids):
            for edge in tx.db.execute('SELECT target FROM map_relations WHERE repository=? AND snapshot=? AND source=? ORDER BY target',
                                      (repository, snapshot, source)):
                if edge[0] not in ids:
                    ids.append(edge[0])
                if len(ids) > MAX_RECORDS:
                    raise RequiredContextOverflow('assignment map and direct interfaces exceed eight records; split the unit')
        return snapshot, tuple(ids)


def selection(snapshot, record_ids):
    if snapshot is None and not record_ids:
        return None
    if not isinstance(snapshot, str) or not snapshot or not 1 <= len(record_ids) <= MAX_RECORDS:
        raise ValidationError("companion map selection requires a snapshot and 1–8 record IDs")
    if len(set(record_ids)) != len(record_ids):
        raise ValidationError("companion map selection cannot repeat an ID")
    for record_id in record_ids:
        appmap.identifier(record_id)
    return {"snapshot": snapshot, "record_ids": list(record_ids)}


def freeze(tx, task, selected, *, max_bytes):
    repository = Path(task["workdir"])
    repo_id = appmap.manifest(repository)["repo_id"]
    key = (repo_id, selected["snapshot"])
    worktree = map_updates.require_worktree(tx.db, repository, *key)
    scope = {"repository": repo_id, "snapshot": key[1], "worktree": worktree,
             "read_versions": {}, "records": []}
    for record_id in selected["record_ids"]:
        header = tx.db.execute("""SELECT r.version,r.applicability,length(CAST(r.payload AS BLOB)) AS bytes
            FROM map_records r JOIN map_heads h USING(repository,snapshot,record_id,version)
            WHERE repository=? AND snapshot=? AND record_id=?""", (*key, record_id)).fetchone()
        scope["read_versions"][record_id] = header["version"] if header else 0
        if header:
            # Inspect stored size before loading a body, not after decoding it.
            if header["bytes"] + len(canonical(scope).encode()) > max_bytes:
                raise RequiredContextOverflow("selected map records exceed the pass budget; narrow selection")
            record = json.loads(map_updates._record(tx.db, key, record_id)["payload"])
            statuses = {(row["kind"], row["target"]): row["status"] for row in tx.db.execute(
                "SELECT kind,target,status FROM map_relations WHERE repository=? AND snapshot=? AND source=?",
                (*key, record_id))}
            for edge in record["edges"]:
                edge["status"] = statuses.get((edge["kind"], edge["to"]), "stale")
            refreshing = tx.db.execute("SELECT 1 FROM map_refresh_queue WHERE repository=? AND snapshot=? AND record_id=?",
                                      (*key, record_id)).fetchone() is not None
            anchors = []
            for anchor in record['anchors']:
                cached = tx.db.execute('SELECT source_stamp,payload FROM map_anchor_cache WHERE repository=? AND worktree=? AND path=? AND symbol=?',
                    (repo_id, str(repository.resolve()), anchor['path'], anchor['symbol'] or '')).fetchone()
                if cached:
                    from .map_refresh import _stamp
                    if cached['source_stamp'] == _stamp(repository, anchor['path']):
                        anchors.append(json.loads(cached['payload']))
            scope["records"].append({"record": record, "applicability": header["applicability"],
                                     "awaiting_refresh": refreshing, 'current_anchors': anchors})
    if len(canonical(scope).encode()) > max_bytes:
        raise RequiredContextOverflow("selected map records exceed the pass budget; narrow selection")
    return scope


def check_scope(tx, body, *, versions=False):
    scope = body.get("map_scope")
    if scope is None:
        return
    repository = Path(body["task"]["workdir"])
    if appmap.manifest(repository)["repo_id"] != scope["repository"]:
        raise Conflict("companion map repository changed after preparation")
    key = (scope["repository"], scope["snapshot"])
    if map_updates.require_worktree(tx.db, repository, *key) != scope["worktree"]:
        raise Conflict("companion map worktree changed after preparation")
    if versions:
        for record_id, version in scope["read_versions"].items():
            row = tx.db.execute("SELECT version FROM map_heads WHERE repository=? AND snapshot=? AND record_id=?",
                                (*key, record_id)).fetchone()
            if (row[0] if row else 0) != version:
                raise Conflict("companion selected map record changed before execution")


def prepare_patch(store, body, patch):
    scope = body.get("map_scope")
    if scope is None:
        raise ValidationError("map patch requires a frozen map selection")
    map_updates.validate(patch)
    if (patch["patch_id"] != "pass-" + body["pass_id"] or patch["task_id"] != body["task_id"]
        or patch["run_id"] != body["run_id"] or patch["snapshot"] != scope["snapshot"]
        or patch["read_versions"] != scope["read_versions"]):
        raise Conflict("map patch identity and read versions must match the frozen pass")
    allowed = {event["event_id"] for event in body["events"]}
    allowed.update(event for record in body["records"] for event in record["evidence"])
    if not set(patch["evidence_ids"]) <= allowed:
        raise ValidationError("map patch cites evidence outside the frozen pass")
    with store.transaction() as tx:
        check_scope(tx, body)
    return map_updates.prepare_proposal(store, Path(body["task"]["workdir"]), patch)


def apply_patch(tx, body, prepared):
    check_scope(tx, body)
    if tx.db.execute("SELECT 1 FROM map_patches WHERE repository=? AND patch_id=?",
                     (prepared["repo_id"], prepared["body"]["patch_id"])).fetchone():
        raise Conflict("map patch was already committed outside this companion pass")
    # Facts may explicitly rebind to a map version from this same response.
    # Invalidate remaining older consumers after installing those facts.
    return map_updates.apply_proposal(tx, prepared, defer_invalidation=True)


def finalize(tx, body, prepared, result):
    map_updates.invalidate_dependants(tx, prepared, result)
    check_scope(tx, body)
    for path, stamp in prepared["stamps"].items():
        if appmap._source_stamp(prepared["repository"], path) != stamp:
            raise Conflict("map source changed before companion commit")


def instructions():
    return (" When map_scope is supplied, optionally include map_patch in the same response. "
        "It commits atomically with facts and dispositions. Reuse the supplied records; do not search for more. "
        "Map patch fields: schema_version=1, patch_id='pass-'+pass_id, task_id, run_id, snapshot, "
        "read_versions (copy map_scope.read_versions exactly), operations, evidence_ids. "
        "Operations: {op:put,record:<complete map record>} or {op:invalidate,id,reason}. "
        "Put version is expected read version+1; absent selected IDs have version 0. "
        "Every edge endpoint must be in read_versions. Record fields: schema_version=1,id,version,kind, "
        "claim=required/observed/hypothesis,summary,data,anchors,edges,attributes,replaces. "
        "current_anchors supplies verified current source locations and hashes for changed files. "
        "Use them to repair source anchors only when the observed behavior supports the semantic claim. "
        "Reuse valid supplied anchors or exact anchors present in captured evidence; never invent hashes. "
        "Observed claims require source anchors. Anchors: path,symbol,sha256,optional line/end_line. "
        "Edges: kind,to,status,evidence. Attributes use namespaced keys; replaces is an ID array. "
        "Map status describes the captured ledger state; source applicability is rechecked at commit. "
        "Data fields by kind: " + canonical({kind: sorted(fields) for kind, fields in appmap.DATA_FIELDS.items()}))
