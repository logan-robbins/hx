"""Coverage, shared published contracts, and read-through lease ownership."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from . import code_store as store
from .code_extract import LANGUAGES


def rows_of(value):
    if isinstance(value, list):
        return [row for item in value for row in rows_of(item)]
    if not isinstance(value, dict):
        return []
    if (
        "path" in value
        or "from_path" in value
        or "signature" in value
        or ("root" in value and "repo" in value)
    ):
        return [value]
    if "count" in value:
        return [value] * min(101, value["count"])
    if "fresh" in value:
        return [dict(stale=not value["fresh"])]
    return [
        row
        for child in value.values()
        if isinstance(child, (dict, list))
        for row in rows_of(child)
    ]


def owners(root, result):
    path = Path(root) / "state/continuity.sqlite"
    leases = []
    if path.exists():
        conn = sqlite3.connect("file:" + str(path) + "?mode=ro", uri=True)
        try:
            leases = conn.execute("""SELECT l.repository,l.canonical_path,r.worker_id,r.task_id,r.run_id
                FROM leases l JOIN runs r USING(run_id) WHERE r.ended_at IS NULL ORDER BY r.worker_id""").fetchall()
        finally:
            conn.close()
    graph = store.connect(root, existing=True)
    namespaces = (
        {r["repo"]: r["root"] for r in graph.execute("SELECT repo,root FROM repos")}
        if graph
        else {}
    )
    if graph:
        graph.close()
    for row in rows_of(result):
        path = row.get("path", row.get("from_path", "")).casefold()
        repo_root = namespaces.get(row.get("repo"))
        if repo_root:
            target = (
                Path(repo_root) / row.get("path", row.get("from_path", ""))
            ).resolve()
            if target.is_relative_to(Path(repo_root).resolve()):
                path = (
                    target.relative_to(Path(repo_root).resolve()).as_posix().casefold()
                )
        row["owners"] = [
            dict(worker=worker, task=task, run=run)
            for repo, scope, worker, task, run in leases
            if repo == row.get("repo")
            and (
                path == scope
                or path.startswith(scope + "/")
                or scope.startswith(path + "/")
            )
        ]


def register_contracts(conn, file, caller=None):
    """Record contracts and publish them into each namespace's caller checkout."""
    records = json.loads(Path(file).read_text())
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        raise ValueError("contract file must contain an object or array")
    for record in records:
        required = {"name", "path", "version", "producer", "repo", "sha"}
        if not isinstance(record, dict) or record.keys() != required:
            raise ValueError("contract requires name,path,version,producer,repo,sha")
        if any(
            not isinstance(record[k], (str, int)) or str(record[k]) == ""
            for k in required
        ):
            raise ValueError("contract fields must be nonempty scalars")
        if not isinstance(record["sha"], str) or not re.fullmatch(
            r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", record["sha"]
        ):
            raise ValueError("contract sha must be a Git commit hash")
        path = Path(record["path"])
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("contract path must be repository relative")
        if not conn.execute(
            "SELECT 1 FROM checkout_heads WHERE repo=?", (record["repo"],)
        ).fetchone():
            raise ValueError("contract repository is not indexed")
        conn.execute(
            "INSERT OR REPLACE INTO contracts VALUES(?,?,?,?,?,?)",
            tuple(
                str(record[k])
                for k in ("repo", "name", "path", "version", "producer", "sha")
            ),
        )
    from .code_snapshot import capture_external, checkout_root

    for identity in sorted({str(record["repo"]) for record in records}):
        capture_external(
            conn,
            identity,
            checkout_root(conn, identity, caller),
            {str(r["path"]) for r in records if str(r["repo"]) == identity},
            (),
        )
    conn.commit()
    return len(records)


def contracts(conn, name=None, *, repo=None, limit=100):
    from .code_snapshot import metadata_facts

    result = []
    for row in metadata_facts(conn, "contracts", name, repo=repo, limit=limit):
        value = store.decorate(conn, row)
        difference = store.git(
            conn.snapshot_roots[row["repo"]],
            "diff",
            "--name-only",
            row["sha"],
            conn.snapshot["head"] if conn.historical else "HEAD",
            "--",
            row["path"],
        )
        value.update(
            lane="imported",
            extractor="contract-registry",
            anchor_sha=None,
            unanchored=difference is None,
        )
        value["stale"] |= bool(difference)
        result.append(value)
    if name is not None and len(result) < limit:
        from .code_stubs import publications

        result += publications(conn, name, repo=repo, limit=limit - len(result))
    return result


def run(conn, command, name=None, *, repo=None, root=None, limit=100):
    if command in {"contracts", "contract"}:
        return contracts(conn, name, repo=repo, limit=limit)
    if command == "projects":
        if conn.snapshot is not None:
            from .code_snapshot import (
                facts_in_path,
                metadata_facts,
                path_info,
                project_paths,
            )

            rows = metadata_facts(conn, "projects", name, repo=repo, limit=limit)
        else:
            rows = conn.execute(
                "SELECT * FROM projects WHERE (? IS NULL OR name LIKE ? OR path LIKE ?) AND (? IS NULL OR repo=?) ORDER BY repo,path LIMIT ?",
                (
                    name,
                    "%" + name + "%" if name else None,
                    "%" + name + "%" if name else None,
                    repo,
                    repo,
                    limit,
                ),
            )
        result = []
        for row in rows:
            data = json.loads(row["data"])
            if conn.snapshot is not None:
                deps = sorted(
                    facts_in_path(conn, "project_edges", row["path"], repo=row["repo"]),
                    key=lambda r: r["dst_text"],
                )[:100]
            else:
                deps = [
                    dict(r)
                    for r in conn.execute(
                        "SELECT * FROM project_edges WHERE repo=? AND src_path=? ORDER BY dst_text LIMIT 100",
                        (row["repo"], row["path"]),
                    )
                ]
            workspaces = data.get("workspaces", [])
            for pattern in workspaces:
                import fnmatch

                prefix = (
                    Path(row["path"]).parent / pattern / "package.json"
                ).as_posix()
                if conn.snapshot is not None:
                    targets = project_paths(conn, row["repo"])
                else:
                    targets = (
                        r[0]
                        for r in conn.execute(
                            "SELECT path FROM projects WHERE repo=? ORDER BY path",
                            (row["repo"],),
                        )
                    )
                for target in targets:
                    if fnmatch.fnmatch(target, prefix):
                        deps.append(
                            dict(
                                repo=row["repo"],
                                src_path=row["path"],
                                dst_text=target,
                                kind="project_depends_on",
                            )
                        )
            result.append(
                store.decorate(conn, row)
                | dict(
                    data=data,
                    dependencies=deps[:100],
                    lane="syntax",
                    extractor="manifest",
                    anchor_sha=path_info(conn, row["repo"], row["path"])["sha"]
                    if conn.snapshot is not None
                    else conn.execute(
                        "SELECT sha256 FROM files WHERE repo=? AND path=?",
                        (row["repo"], row["path"]),
                    ).fetchone()[0],
                )
            )
        return result
    # One entry per selected namespace: the checkout this read answers for.
    namespaces = []
    for identity, checkout in sorted(conn.snapshot_roots.items())[:limit]:
        if repo is not None and identity != repo:
            continue
        record = conn.execute(
            "SELECT * FROM main.repos WHERE repo=? AND root=?", (identity, checkout or "")
        ).fetchone()
        selected = conn.snapshot_maps[identity]
        entry = (
            dict(record)
            if record
            else dict(repo=identity, root=checkout or "", head=selected["head"], dirty=0, indexed_at=None)
        )
        entry["snapshot"] = selected["identity"]
        namespaces.append(entry)
    from .code_freshness import scope

    for entry in namespaces:
        entry["unanchored"] = not bool(entry["root"])
        if conn.historical:
            entry.update(
                head=conn.snapshot["head"],
                snapshot=conn.snapshot["identity"],
                stale=not bool(conn.snapshot["complete"]),
                source_proof="immutable-snapshot",
            )
        else:
            entry["stale"] = (
                entry["unanchored"]
                or not scope(root, entry["root"], ["."], local=True)["fresh"]
            )
    coverage = {}
    for row in conn.execute(
        "SELECT repo,path FROM files WHERE (? IS NULL OR repo=?)", (repo, repo)
    ):
        language = LANGUAGES.get(
            Path(row["path"]).suffix.lower(), Path(row["path"]).suffix.lower() or "text"
        )
        coverage.setdefault(
            language,
            {
                "files": 0,
                "symbols": 0,
                "signatures": 0,
                "references": 0,
                "resolved_references": 0,
            },
        )["files"] += 1
    for row in conn.execute(
        "SELECT path,signature FROM symbols WHERE (? IS NULL OR repo=?)", (repo, repo)
    ):
        key = LANGUAGES.get(
            Path(row["path"]).suffix.lower(), Path(row["path"]).suffix.lower() or "text"
        )
        data = coverage.setdefault(
            key,
            {
                "files": 0,
                "symbols": 0,
                "signatures": 0,
                "references": 0,
                "resolved_references": 0,
            },
        )
        data["symbols"] += 1
        data["signatures"] += bool(row["signature"])
    for row in conn.execute(
        "SELECT path,lane FROM refs WHERE (? IS NULL OR repo=?)", (repo, repo)
    ):
        key = LANGUAGES.get(
            Path(row["path"]).suffix.lower(), Path(row["path"]).suffix.lower() or "text"
        )
        data = coverage.setdefault(
            key,
            {
                "files": 0,
                "symbols": 0,
                "signatures": 0,
                "references": 0,
                "resolved_references": 0,
            },
        )
        data["references"] += 1
        data["resolved_references"] += row["lane"] == "resolved"
    from .code_adapters import detect
    from .code_snapshot import source_search_tables

    return dict(
        repos=namespaces,
        coverage=coverage,
        extractors=detect(),
        index_bytes=store.db_path(root).stat().st_size,
        tokenizers=source_search_tables(conn),
        excluded_files=[
            dict(r)
            for r in conn.execute(
                "SELECT repo,reason,count(*) AS files,sum(size) AS bytes FROM exclusions WHERE (? IS NULL OR repo=?) GROUP BY repo,reason ORDER BY repo,reason",
                (repo, repo),
            )
        ],
        ownership="continuity leases (read-through)",
    )


def published_contracts(root, name=None, *, repo=None, limit=100, task=None):
    """Read current immutable publications using the authoritative ledger proof.

    The existing ledger is opened read-only; source graph storage receives no
    records or permissions. Exact consumer edges alone describe allowed use.
    """
    from types import SimpleNamespace

    from .continuity_store import Conflict, Transaction, digest
    from .dependency_packets import contract_proof
    from .errors import Refused, ValidationError

    root = Path(root).resolve()
    path = root / "state/continuity.sqlite"
    if not path.is_file():
        return []
    conn = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, isolation_level=None)
    conn.row_factory = sqlite3.Row
    tx = None
    try:
        if not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='unit_contracts'"
        ).fetchone():
            return []
        conn.execute("BEGIN")
        tx = Transaction(
            SimpleNamespace(
                root=root, path=path, db=conn, artifacts=root / "state/artifacts"
            )
        )
        consumer = tx.task(task) if task is not None else None
        if task is not None and consumer is None:
            raise ValidationError("unknown consumer task")
        rows = conn.execute(
            """SELECT c.*,json_extract(t.payload,'$.repository') AS repository
            FROM unit_contracts c JOIN task_heads h ON h.task_id=c.task_id AND h.revision=c.task_revision
            JOIN tasks t ON t.task_id=c.task_id AND t.revision=c.task_revision
            WHERE (? IS NULL OR lower(c.output_id)=? OR lower(c.task_id || '/' || c.output_id)=?)
            AND (? IS NULL OR json_extract(t.payload,'$.repository')=?)
            ORDER BY c.task_id,c.output_id,c.version DESC LIMIT ?""",
            (
                name,
                name.casefold() if name else None,
                name.casefold() if name else None,
                repo,
                repo,
                min(100, max(1, limit)),
            ),
        )
        result = []
        for row in rows:
            proof = json.loads(row["payload"])
            need = (
                next(
                    (
                        edge
                        for edge in consumer["payload"].get("prerequisites", [])
                        if edge["task_id"] == row["task_id"]
                        and edge["output_id"] == row["output_id"]
                        and edge["version"] == row["version"]
                        and edge.get("need", "hard") in {"contract", "substitutable"}
                    ),
                    None,
                )
                if consumer
                else None
            )
            dependency = need or dict(
                task_id=row["task_id"],
                output_id=row["output_id"],
                version=row["version"],
            )
            reason = None
            try:
                proof = contract_proof(tx, dependency)
            except (
                Conflict,
                Refused,
                ValidationError,
                OSError,
                ValueError,
                TypeError,
                KeyError,
            ) as exc:
                reason = str(exc)[:300]
            files = proof.get("files", {})
            selected = dict(list(sorted(files.items()))[:100])
            metadata = dict(
                repo=row["repository"],
                name=row["output_id"],
                producer=row["task_id"],
                producer_revision=row["task_revision"],
                version=row["version"],
                path=next(iter(selected), ""),
                paths=proof.get("paths", []),
                commit=proof.get("commit"),
                sha=proof.get("commit"),
                tree=proof.get("tree"),
                contract_hash=digest(proof),
                files=selected,
                file_count=len(files),
                file_refs_truncated=len(files) > 100,
                artifact_refs={p: proof.get("artifacts", {}).get(p) for p in selected},
                lane="published-contract",
                extractor="continuity-ledger",
                source_proof="immutable-ledger-publication",
                authority="continuity-ledger",
                ledger_scope="current",
                stale=reason is not None,
                unanchored=False,
                publication_status="unavailable" if reason else "verified",
            )
            if reason:
                metadata["reason"] = reason
            if consumer:
                metadata["consumer"] = consumer["task_id"]
                metadata["permission"] = dict(
                    declared=need is not None,
                    available=need is not None and reason is None,
                    need=need.get("need") if need else None,
                    stand_in=bool(need and need.get("stand_in") and reason is None),
                )
                if need and need.get("need") == "substitutable":
                    metadata["real_provider_ref"] = dict(
                        task_id=need["task_id"],
                        output_id=need["provider_output"],
                        version=need["provider_version"],
                    )
            result.append(metadata)
        return result
    finally:
        if tx is not None:
            tx.active = False
        if conn.in_transaction:
            conn.rollback()
        conn.close()


def join_publications(root, result, name=None, *, repo=None, limit=100, task=None):
    """Client-side ledger join for local or shared graph contract results."""
    publications = published_contracts(root, name, repo=repo, limit=limit, task=task)
    if not publications:
        return result
    candidates = result if isinstance(result, list) else []
    # Verified ledger identities precede derived registry candidates, while both
    # remain visibly identified by their own source and freshness evidence.
    return [*publications, *candidates][: min(100, limit)]
