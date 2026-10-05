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
            # A fresh assignment can discover its first map records from captured
            # execution, without having to guess their IDs before work begins.
            found = tx.db.execute('SELECT o.snapshot FROM map_overlays o JOIN map_snapshots s USING(repository,snapshot) WHERE s.import_path=? ORDER BY o.rowid DESC LIMIT 1',
                                  (str(Path(task['payload']['workdir']).resolve()),)).fetchone() if task['payload'].get('workdir') else None
            return (found[0], ()) if found else (None, ())
        scopes = {(ref['repository'], ref['snapshot']) for ref in refs}
        if len(scopes) != 1:
            raise ValidationError('one companion pass requires one assignment map snapshot')
        repository, snapshot = next(iter(scopes))
        ids = list(dict.fromkeys(ref['id'] for ref in refs))
        if len(ids) > MAX_RECORDS:
            raise RequiredContextOverflow('required assignment map exceeds eight records; split the unit')
        # Required bindings survive. Neighbors are hints, not additional obligations.
        # Interleave checks/contracts before containment and bound database work.
        for kind in ('checked_by', 'provides', 'consumes', 'depends_on', 'implements', 'contains'):
            for source in tuple(dict.fromkeys(ref['id'] for ref in refs)):
                for edge in tx.db.execute("SELECT CASE WHEN source=? THEN target ELSE source END FROM map_relations WHERE repository=? AND snapshot=? AND (source=? OR target=?) AND kind=? AND status='validated' ORDER BY source,target LIMIT 8",
                                          (source, repository, snapshot, source, source, kind)):
                    if edge[0] not in ids and len(ids) < MAX_RECORDS:
                        ids.append(edge[0])
        return snapshot, tuple(ids)


def selection(snapshot, record_ids):
    if snapshot is None and not record_ids:
        return None
    if not isinstance(snapshot, str) or not snapshot or not 0 <= len(record_ids) <= MAX_RECORDS:
        raise ValidationError("companion map selection requires a snapshot and at most eight record IDs")
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
    required = {ref['id'] for ref in task.get('map_inputs', [])} or set(selected['record_ids'])
    omitted = []
    for record_id in selected["record_ids"]:
        header = tx.db.execute("""SELECT r.version,r.applicability,length(CAST(r.payload AS BLOB)) AS bytes
            FROM map_records r JOIN map_heads h USING(repository,snapshot,record_id,version)
            WHERE repository=? AND snapshot=? AND record_id=?""", (*key, record_id)).fetchone()
        scope["read_versions"][record_id] = header["version"] if header else 0
        if header:
            # Inspect stored size before loading a body, not after decoding it.
            if header["bytes"] + len(canonical(scope).encode()) + (256 if record_id not in required else 0) > max_bytes:
                if record_id in required:
                    raise RequiredContextOverflow("selected map records exceed the pass budget; narrow selection")
                scope['read_versions'].pop(record_id)
                omitted.append(record_id)
                continue
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
    if omitted:
        scope["omitted_neighbors"] = omitted
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


def observed_paths(body):
    paths = {a['path'] for item in body.get('map_scope', {}).get('records', []) for a in item['record']['anchors']}
    # Only paths already observed in this pass can extend its source scope.
    def observe(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {'path', 'file_path', 'filePath', 'target_file'} and isinstance(child, str):
                    candidate = Path(child)
                    if candidate.is_absolute():
                        try:
                            child = str(candidate.resolve().relative_to(Path(body['task']['workdir']).resolve()))
                        except ValueError:
                            continue
                    try:
                        appmap.relative_path(child)
                    except ValidationError:
                        continue
                    paths.add(child)
                elif isinstance(child, (dict, list)):
                    observe(child)
        elif isinstance(value, list):
            for child in value:
                observe(child)
    for event in body['events']:
        observe(event.get('payload', {}))
        for path in event.get('source_paths', event.get('payload', {}).get('source_paths', [])):
            observe({'path': path})
    return paths


def discovery_anchors(tx, body, max_bytes):
    if 'map_scope' not in body:
        return
    repository = Path(body['task']['workdir']).resolve()
    scope = body['map_scope']
    known = {a['path'] for item in scope['records'] for a in item['record']['anchors']}
    anchors = []
    for path in sorted(observed_paths(body) - known)[:4]:
        try:
            stamp = appmap._source_stamp(repository, path)
            cached = tx.db.execute('SELECT source_stamp,payload FROM map_anchor_cache WHERE repository=? AND worktree=? AND path=? AND symbol=?', (scope['repository'], str(repository), path, '')).fetchone()
            if cached and cached['source_stamp'] == stamp:
                anchor = json.loads(cached['payload'])
            else:
                captured = appmap.source_anchor(repository, path, with_stamp=True)
                anchor, stamp = captured['anchor'], captured['source_stamp']
                tx.db.execute('INSERT OR REPLACE INTO map_anchor_cache VALUES(?,?,?,?,?,?)', (scope['repository'], str(repository), path, '', stamp, json.dumps(anchor)))
        except (ValidationError, OSError):
            continue
        from .continuity_store import canonical
        candidate = {**body, 'discovery_anchors': [*anchors, anchor]}
        if len(canonical(candidate).encode()) > max_bytes:
            break
        anchors.append(anchor)
    if anchors:
        body['discovery_anchors'] = anchors


def prepare_patch(store, body, patch):
    scope = body.get("map_scope")
    if scope is None:
        raise ValidationError("map patch requires a frozen map selection")
    map_updates.validate(patch)
    if (patch["patch_id"] != "pass-" + body["pass_id"] or patch["task_id"] != body["task_id"]
        or patch["run_id"] != body["run_id"] or patch["snapshot"] != scope["snapshot"]
        or any(patch["read_versions"].get(key) != version for key, version in scope["read_versions"].items())):
        raise Conflict("map patch identity and read versions must match the frozen pass")
    additions = set(patch['read_versions']) - set(scope['read_versions'])
    if len(additions) > 4 or any(patch['read_versions'][key] != 0 for key in additions):
        raise ValidationError('map discovery allows at most four new identities, each at version zero')
    paths = observed_paths(body)
    new_records = {op['record']['id']: op['record'] for op in patch['operations'] if op['op'] == 'put'}
    for identity in additions:
        record = new_records.get(identity)
        if not record or record['claim'] != 'observed' or not record['anchors'] or any(a['path'] not in paths for a in record['anchors']):
            raise ValidationError('new map identities require observed anchors within frozen evidence')
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
        "read_versions (preserve map_scope.read_versions; add at most four new IDs at version 0), operations, evidence_ids. "
        "Operations: {op:put,record:<complete map record>} or {op:invalidate,id,reason}. "
        "Put version is expected read version+1; absent selected IDs have version 0. "
        "New IDs require observed claims anchored to paths in frozen evidence or selected records. New or changed edges need endpoint read versions; unchanged omitted edges retain runtime status. Record fields: schema_version=1,id,version,kind, "
        "claim=required/observed/hypothesis,summary,data,anchors,edges,attributes,replaces. "
        "current_anchors supplies verified current source locations and hashes for changed files. "
        "Use them to repair source anchors only when the observed behavior supports the semantic claim. "
        "Use discovery_anchors for newly observed files; leave unsupported discoveries pending. Never invent hashes. "
        "Observed claims require source anchors. Anchors: path,symbol,sha256,optional line/end_line. "
        "Edges: kind,to,status,evidence. Attributes use namespaced keys; replaces is an ID array. "
        "Map status describes the captured ledger state; source applicability is rechecked at commit. "
        "Data fields by kind: " + canonical({kind: sorted(fields) for kind, fields in appmap.DATA_FIELDS.items()}))
