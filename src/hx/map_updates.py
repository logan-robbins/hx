"""Bounded, evidence-bound map proposals against isolated worktree overlays."""

from __future__ import annotations

import json
from pathlib import Path

from . import appmap
from .continuity_store import Conflict, ContinuityStore, _id, canonical, digest
from .errors import ValidationError

PATCH_BYTES = 262144
READ_LIMIT = 64
OP_LIMIT = 16


class MapConflict(Conflict):
    def __init__(self, details):
        self.details = details
        super().__init__("map proposal conflict: " + canonical(details))


def _worktree(repository):
    return {"path": str(repository.resolve()), "head": appmap._git(repository, "rev-parse", "HEAD"),
            "branch": appmap._git(repository, "rev-parse", "--symbolic-full-name", "HEAD"),
            "git_dir": appmap._git(repository, "rev-parse", "--absolute-git-dir")}


def require_worktree(db, repository, repo_id, snapshot):
    overlay = db.execute("SELECT o.*,s.import_path FROM map_overlays o JOIN map_snapshots s USING(repository,snapshot) WHERE repository=? AND snapshot=?",
                         (repo_id, snapshot)).fetchone()
    if overlay is None:
        raise Conflict("map writes require a worktree overlay")
    expected = {"path": overlay["import_path"], "head": overlay["head_commit"],
                "branch": overlay["branch"], "git_dir": overlay["git_dir"]}
    if _worktree(repository) != expected:
        raise Conflict("map overlay belongs to a different worktree, branch, or HEAD; create its overlay")
    return expected


def create_overlay(store: ContinuityStore, repository: Path, baseline: str) -> dict:
    """Explicit disk-backed batch: clone an immutable baseline into a worktree scope."""
    info, source = appmap._snapshot(store, repository, baseline)
    if source["kind"] != "baseline":
        raise ValidationError("an overlay must start from an imported committed baseline")
    identity = _worktree(repository)
    snapshot = "worktree:" + digest({"repository": info["repo_id"], "baseline": baseline, **identity})
    key = (info["repo_id"], snapshot)
    with store.transaction() as tx:
        if not tx.db.execute("SELECT 1 FROM map_overlays WHERE repository=? AND snapshot=?", key).fetchone():
            tx._change()
            # This hash identifies the imported baseline. Overlay truth is its
            # per-record heads, never a claim that the baseline hash stays current.
            tx.db.execute("INSERT INTO map_snapshots VALUES(?,?,?,?,?,?)",
                (*key, source["base_commit"], identity["path"], "overlay", source["map_hash"]))
            tx.db.execute("INSERT INTO map_overlays VALUES(?,?,?,?,?,?)",
                (*key, baseline, identity["head"], identity["branch"], identity["git_dir"]))
            # SQLite streams these copies on disk. No graph is loaded into Python.
            tx.db.execute("INSERT INTO map_records SELECT r.repository,?,r.record_id,r.version,r.kind,r.payload,r.evidence,r.inputs,r.applicability FROM map_records r JOIN map_heads h USING(repository,snapshot,record_id,version) WHERE r.repository=? AND r.snapshot=?",
                          (snapshot, info["repo_id"], baseline))
            tx.db.execute("INSERT INTO map_heads SELECT repository,?,record_id,version FROM map_heads WHERE repository=? AND snapshot=?",
                          (snapshot, info["repo_id"], baseline))
            tx.db.execute("INSERT INTO map_relations SELECT repository,?,source,kind,target,status FROM map_relations WHERE repository=? AND snapshot=?",
                          (snapshot, info["repo_id"], baseline))
            tx.db.execute("INSERT INTO map_sources SELECT repository,?,record_id,path,symbol,sha256 FROM map_sources WHERE repository=? AND snapshot=?",
                          (snapshot, info["repo_id"], baseline))
            tx.db.execute("INSERT INTO map_search_keys(repository,snapshot,record_id) SELECT repository,?,record_id FROM map_heads WHERE repository=? AND snapshot=?", (snapshot, info["repo_id"], baseline))
            tx.db.execute("""INSERT INTO map_search(rowid,summary,semantic)
                SELECT dest.rowid,s.summary,s.semantic FROM map_search_keys src
                JOIN map_search s ON s.rowid=src.rowid
                JOIN map_search_keys dest ON dest.repository=src.repository AND dest.record_id=src.record_id AND dest.snapshot=?
                WHERE src.repository=? AND src.snapshot=?""", (snapshot, info["repo_id"], baseline))
        require_worktree(tx.db, repository, *key)
    return {"repo_id": info["repo_id"], "snapshot": snapshot, "baseline": baseline}


def validate(body):
    fields = {"schema_version", "patch_id", "task_id", "run_id", "snapshot", "read_versions", "operations", "evidence_ids"}
    if not isinstance(body, dict) or body.keys() != fields or type(body["schema_version"]) is not int or body["schema_version"] != 1:
        raise ValidationError("map patch has missing or unknown structure/schema")
    if len(canonical(body).encode()) > PATCH_BYTES:
        raise ValidationError("map patch exceeds 256 KiB")
    appmap.identifier(body["patch_id"])
    for key in ("task_id", "run_id", "snapshot"):
        _id(body[key])
    reads = body["read_versions"]
    if not isinstance(reads, dict) or not 1 <= len(reads) <= READ_LIMIT:
        raise ValidationError("map patch requires 1–64 expected record versions")
    for record_id, version in reads.items():
        appmap.identifier(record_id)
        if type(version) is not int or version < 0:
            raise ValidationError("expected record versions must be nonnegative integers")
    if not isinstance(body["operations"], list) or not 1 <= len(body["operations"]) <= OP_LIMIT:
        raise ValidationError("map patch requires 1–16 operations")
    operations = {}
    for op in body["operations"]:
        if not isinstance(op, dict):
            raise ValidationError("map operation must be an object")
        if op.get("op") == "put" and op.keys() == {"op", "record"}:
            appmap.validate_record(op["record"])
            record_id = op["record"]["id"]
            if op["record"]["version"] != reads.get(record_id, -2) + 1:
                raise ValidationError("proposed record version must advance its expected version by one")
            for edge in op["record"]["edges"]:
                if edge["to"] not in reads:
                    raise ValidationError("every referenced endpoint needs an expected read version")
        elif op.get("op") == "invalidate" and op.keys() == {"op", "id", "reason"}:
            record_id = op["id"]
            appmap.identifier(record_id)
            appmap._text(op["reason"], "invalidation reason")
        else:
            raise ValidationError("map operation must put a complete record or invalidate a record with a reason")
        if record_id not in reads or record_id in operations:
            raise ValidationError("each changed record needs one operation and an expected version")
        operations[record_id] = op
    evidence = body["evidence_ids"]
    if not isinstance(evidence, list) or not 1 <= len(evidence) <= 64:
        raise ValidationError("map patch requires 1–64 event evidence IDs")
    for event_id in evidence:
        _id(event_id)
    return operations


def _record(db, key, record_id, version=None):
    if version is None:
        return db.execute("SELECT r.* FROM map_records r JOIN map_heads h USING(repository,snapshot,record_id,version) WHERE repository=? AND snapshot=? AND record_id=?",
                          (*key, record_id)).fetchone()
    return db.execute("SELECT * FROM map_records WHERE repository=? AND snapshot=? AND record_id=? AND version=?",
                      (*key, record_id, version)).fetchone()


def _caller(tx, repository, body, worker_id):
    run = tx.db.execute("SELECT * FROM runs WHERE run_id=?", (body["run_id"],)).fetchone()
    if run is None or run["task_id"] != body["task_id"] or (worker_id is not None and run["worker_id"] != worker_id):
        raise Conflict("map caller/run does not belong to this task")
    task = tx.task(body["task_id"])
    workdir = task["payload"].get("workdir")
    if not isinstance(workdir, str) or not Path(workdir).is_absolute() or Path(workdir).resolve() != repository.resolve():
        raise Conflict("map repository must be the assigned task workdir")
    return run, task


def _conflict(db, key, record_id, expected, current, proposed):
    base = _record(db, key, record_id, expected)
    raise MapConflict({"record_id": record_id, "expected_version": expected,
                       "current_version": current["version"] if current else 0,
                       "base": json.loads(base["payload"]) if base else None,
                       "current": json.loads(current["payload"]) if current else None,
                       "proposed": proposed})


def _preflight(store, repository, key, body, operations):
    """Hash selected inputs outside the writer transaction, then freeze their stamps."""
    stamps = {}
    versions = {}
    captures = {}
    for record_id, expected in body["read_versions"].items():
        row = _record(store.db, key, record_id)
        versions[record_id] = row["version"] if row else 0
        op = operations.get(record_id)
        if op and op["op"] == "invalidate":
            continue  # Contradictory evidence may invalidate an already stale source.
        record = op["record"] if op else (json.loads(row["payload"]) if row else None)
        if record is None:
            continue
        if not op and row["applicability"] != "current":
            raise Conflict(f"map read dependency is not current: {record_id}")
        if not op and store.db.execute("SELECT 1 FROM map_refresh_queue WHERE repository=? AND snapshot=? AND record_id=?", (*key, record_id)).fetchone():
            raise Conflict(f"map read dependency awaits source refresh: {record_id}")
        for anchor in record["anchors"]:
            anchor_key = (anchor["path"], anchor["symbol"] or "")
            stamp = appmap._source_stamp(repository, anchor["path"])
            capture = captures.get(anchor_key)
            if capture is None:
                cached = store.db.execute("SELECT source_stamp,payload FROM map_anchor_cache WHERE repository=? AND worktree=? AND path=? AND symbol=?",
                    (key[0], str(repository.resolve()), *anchor_key)).fetchone()
                if cached and cached["source_stamp"] == stamp:
                    capture = {"anchor": json.loads(cached["payload"]), "source_stamp": stamp}
                else:
                    capture = appmap.source_anchor(repository, anchor["path"], anchor["symbol"], with_stamp=True)
                captures[anchor_key] = capture
            if capture["source_stamp"] != stamp:
                raise Conflict("map source changed during proposal validation")
            if capture["anchor"]["sha256"] != anchor["sha256"]:
                raise Conflict(f"map proposal source anchor is stale: {record_id} -> {anchor['path']}")
            previous = stamps.get(anchor["path"])
            if previous is not None and previous != capture["source_stamp"]:
                raise Conflict("map source changed during proposal validation")
            stamps[anchor["path"]] = capture["source_stamp"]
    return versions, stamps, captures


def propose(store: ContinuityStore, repository: Path, body: dict, *, worker_id=None) -> dict:
    operations = validate(body)
    repo_id = appmap.manifest(repository)["repo_id"]
    key = (repo_id, body["snapshot"])
    request_hash = digest(body)
    with store.transaction() as tx:
        run, task = _caller(tx, repository, body, worker_id)
        previous = tx.db.execute("SELECT * FROM map_patches WHERE repository=? AND patch_id=?", (repo_id, body["patch_id"])).fetchone()
        if previous:
            if previous["request_hash"] != request_hash or previous["run_id"] != body["run_id"]:
                raise Conflict("map patch ID was already used with different content")
            return json.loads(previous["result"])
        if run["ended_at"] is not None or task["revision"] != run["task_revision"]:
            raise Conflict("map proposal requires an active run bound to the current task revision")
        require_worktree(tx.db, repository, *key)
    frozen_versions, stamps, captures = _preflight(store, repository, key, body, operations)
    with store.transaction() as tx:
        run, task = _caller(tx, repository, body, worker_id)
        if run["ended_at"] is not None or task["revision"] != run["task_revision"]:
            raise Conflict("map proposal run/task changed during source validation")
        previous = tx.db.execute("SELECT * FROM map_patches WHERE repository=? AND patch_id=?", (repo_id, body["patch_id"])).fetchone()
        if previous:
            if previous["request_hash"] != request_hash or previous["run_id"] != body["run_id"]:
                raise Conflict("map patch ID was already used with different content")
            return json.loads(previous["result"])
        require_worktree(tx.db, repository, *key)
        for event_id in body["evidence_ids"]:
            event = tx.db.execute("SELECT r.task_id FROM events e JOIN runs r USING(run_id) WHERE event_id=?", (event_id,)).fetchone()
            if event is None or event[0] != body["task_id"]:
                raise ValidationError("map evidence is absent or belongs to another task")
        coalesced = set()
        for record_id, expected in body["read_versions"].items():
            current = _record(tx.db, key, record_id)
            actual = current["version"] if current else 0
            op = operations.get(record_id)
            if not op and tx.db.execute("SELECT 1 FROM map_refresh_queue WHERE repository=? AND snapshot=? AND record_id=?", (*key, record_id)).fetchone():
                raise Conflict(f"map read dependency awaits source refresh: {record_id}")
            proposed = op.get("record") if op else None
            identical = (proposed is not None and current is not None and current["applicability"] == "current"
                         and appmap.semantic_identity(json.loads(current["payload"])) == appmap.semantic_identity(proposed))
            if identical:
                statuses = {(edge["kind"], edge["target"]): edge["status"] for edge in tx.db.execute(
                    "SELECT kind,target,status FROM map_relations WHERE repository=? AND snapshot=? AND source=?", (*key, record_id))}
                identical = all(statuses.get((edge["kind"], edge["to"])) == edge["status"] for edge in proposed["edges"])
            elif op and op["op"] == "invalidate" and current is not None and current["applicability"] == "stale":
                base = _record(tx.db, key, record_id, expected)
                identical = base is not None and appmap.semantic_identity(json.loads(base["payload"])) == appmap.semantic_identity(json.loads(current["payload"]))
            # A concurrent identical edit is safe only when every other dependency
            # still matches. All read dependencies are checked before any writes.
            if (actual != expected or actual != frozen_versions[record_id]) and not identical:
                _conflict(tx.db, key, record_id, expected, current, proposed if proposed is not None else op)
            if identical:
                coalesced.add(record_id)
        results, changed = {}, []
        tx._change()
        for record_id, op in operations.items():
            old = _record(tx.db, key, record_id)
            if record_id in coalesced:
                version = old["version"]
            else:
                version = old["version"] + 1 if old else 1
                if op["op"] == "put":
                    record = {**op["record"], "version": version}
                    applicability = "current"
                else:
                    if old is None:
                        raise ValidationError("cannot invalidate an absent map record")
                    record = {**json.loads(old["payload"]), "version": version}
                    applicability = "stale"
                tx.db.execute("INSERT INTO map_records VALUES(?,?,?,?,?,?,?,?,?)",
                    (*key, record_id, version, record["kind"], canonical(record), canonical(body["evidence_ids"]),
                     canonical({"anchors": record["anchors"], "patch_id": body["patch_id"],
                                "reason": op.get("reason")}), applicability))
                tx.db.execute("INSERT INTO map_heads VALUES(?,?,?,?) ON CONFLICT(repository,snapshot,record_id) DO UPDATE SET version=excluded.version",
                              (*key, record_id, version))
                appmap.index_relations(tx.db, *key, record)
                if old and (applicability != old["applicability"] or appmap.semantic_identity(record) != appmap.semantic_identity(json.loads(old["payload"]))):
                    changed.append(record_id)
            tx.db.executemany("INSERT OR IGNORE INTO map_evidence VALUES(?,?,?,?,?)",
                             ((*key, record_id, version, event_id) for event_id in body["evidence_ids"]))
            results[record_id] = {"version": version, "coalesced": record_id in coalesced}
            tx.db.execute("DELETE FROM map_refresh_queue WHERE repository=? AND snapshot=? AND record_id=?", (*key, record_id))
        # Validate every affected incident edge under the same writer lock. The
        # unchanged remainder was validated on import or an earlier transaction.
        for record_id in operations:
            for edge in tx.db.execute("""
                WITH incident AS (
                    SELECT * FROM map_relations WHERE repository=? AND snapshot=? AND source=?
                    UNION ALL
                    SELECT * FROM map_relations WHERE repository=? AND snapshot=? AND target=? AND source!=?
                )
                SELECT e.*,s.kind AS source_kind,t.kind AS target_kind,
                       s.applicability AS source_applicability,t.applicability AS target_applicability
                FROM incident e
                LEFT JOIN map_heads sh ON sh.repository=e.repository AND sh.snapshot=e.snapshot AND sh.record_id=e.source
                LEFT JOIN map_records s ON s.repository=sh.repository AND s.snapshot=sh.snapshot AND s.record_id=sh.record_id AND s.version=sh.version
                LEFT JOIN map_heads th ON th.repository=e.repository AND th.snapshot=e.snapshot AND th.record_id=e.target
                LEFT JOIN map_records t ON t.repository=th.repository AND t.snapshot=th.snapshot AND t.record_id=th.record_id AND t.version=th.version
                """, (*key, record_id, *key, record_id, record_id)):
                if edge["source_kind"] is None or edge["target_kind"] is None:
                    raise ValidationError("resulting map graph has a missing endpoint")
                appmap.validate_relation(edge["source_kind"], edge["kind"], edge["target_kind"])
                if edge["status"] == "validated" and (edge["source_applicability"] != "current" or edge["target_applicability"] != "current"):
                    tx.db.execute("UPDATE map_relations SET status='stale' WHERE repository=? AND snapshot=? AND source=? AND kind=? AND target=?",
                                  (*key, edge["source"], edge["kind"], edge["target"]))
        for record_id in changed:
            from .map_dependencies import invalidate_consumers
            invalidate_consumers(tx, key, record_id, results[record_id]["version"], "A required map input changed.")
            # A source rewritten by this same patch explicitly revalidates its
            # outgoing declarations against the resulting graph and read set.
            for edge in tx.db.execute("SELECT source,kind FROM map_relations WHERE repository=? AND snapshot=? AND target=? AND status='validated'", (*key, record_id)):
                if edge["source"] not in operations:
                    tx.db.execute("UPDATE map_relations SET status='stale' WHERE repository=? AND snapshot=? AND source=? AND kind=? AND target=?",
                                  (*key, edge["source"], edge["kind"], record_id))
        # Metadata fences detect source writes during hashing/commit without a
        # second full-file read. Watcher generation/lease integration comes later.
        for path, stamp in stamps.items():
            if appmap._source_stamp(repository, path) != stamp:
                raise Conflict("map source changed before proposal commit")
        for (path, symbol), capture in captures.items():
            tx.db.execute("INSERT INTO map_anchor_cache VALUES(?,?,?,?,?,?) ON CONFLICT(repository,worktree,path,symbol) DO UPDATE SET source_stamp=excluded.source_stamp,payload=excluded.payload",
                (repo_id, str(repository.resolve()), path, symbol, capture["source_stamp"], canonical(capture["anchor"])))
        require_worktree(tx.db, repository, *key)
        result = {"patch_id": body["patch_id"], "snapshot": body["snapshot"], "records": results, "changed": changed}
        tx.db.execute("INSERT INTO map_patches VALUES(?,?,?,?,?)", (repo_id, body["patch_id"], body["run_id"], request_hash, canonical(result)))
        tx.enqueue("map_changed", f"map:{repo_id}:{body['patch_id']}", {**result, "repo_id": repo_id, "task_id": body["task_id"]})
        return result
