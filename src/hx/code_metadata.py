"""Coverage, shared published contracts, and read-through lease ownership."""

from __future__ import annotations
import json
import sqlite3
import re
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


def register_contracts(conn, file):
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
            "SELECT 1 FROM repos WHERE repo=?", (record["repo"],)
        ).fetchone():
            raise ValueError("contract repository is not indexed")
        conn.execute(
            "INSERT OR REPLACE INTO contracts VALUES(?,?,?,?,?,?)",
            tuple(
                str(record[k])
                for k in ("repo", "name", "path", "version", "producer", "sha")
            ),
        )
    conn.commit()
    return len(records)


def contracts(conn, name=None, *, repo=None, limit=100):
    rows = conn.execute(
        """SELECT * FROM contracts WHERE (? IS NULL OR lower(name)=?) AND (? IS NULL OR repo=?)
        ORDER BY repo,name,path,version LIMIT ?""",
        (name, name.casefold() if name else None, repo, repo, limit),
    )
    result = []
    for row in rows:
        value = store.decorate(conn, row)
        root = conn.execute(
            "SELECT root FROM repos WHERE repo=?", (row["repo"],)
        ).fetchone()[0]
        difference = store.git(
            root, "diff", "--name-only", row["sha"], "HEAD", "--", row["path"]
        )
        value.update(
            lane="imported",
            extractor="contract-registry",
            anchor_sha=None,
            unanchored=difference is None,
        )
        value["stale"] |= bool(difference)
        result.append(value)
    return result


def run(conn, command, name=None, *, repo=None, register=None, root=None, limit=100):
    if command in {"contracts", "contract"}:
        if register:
            register_contracts(conn, register)
        return contracts(conn, name, repo=repo, limit=limit)
    if command == "projects":
        rows = conn.execute(
            """SELECT * FROM projects WHERE (? IS NULL OR name LIKE ? OR path LIKE ?) AND (? IS NULL OR repo=?)
            ORDER BY repo,path LIMIT ?""",
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
                for target in conn.execute(
                    "SELECT path FROM projects WHERE repo=? ORDER BY path",
                    (row["repo"],),
                ):
                    if fnmatch.fnmatch(target[0], prefix):
                        deps.append(
                            dict(
                                repo=row["repo"],
                                src_path=row["path"],
                                dst_text=target[0],
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
                    anchor_sha=conn.execute(
                        "SELECT sha256 FROM files WHERE repo=? AND path=?",
                        (row["repo"], row["path"]),
                    ).fetchone()[0],
                )
            )
        return result
    namespaces = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM repos WHERE (? IS NULL OR repo=?) ORDER BY repo LIMIT ?",
            (repo, repo, limit),
        )
    ]
    from .code_freshness import scope

    for entry in namespaces:
        entry["unanchored"] = not bool(entry["root"])
        entry["stale"] = (
            entry["unanchored"] or not scope(root, entry["root"], ["."])["fresh"]
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

    return dict(
        repos=namespaces,
        coverage=coverage,
        extractors=detect(),
        index_bytes=store.db_path(root).stat().st_size,
        tokenizers=store.fts_tables(conn),
        excluded_files=[
            dict(r)
            for r in conn.execute(
                "SELECT repo,reason,count(*) AS files,sum(size) AS bytes FROM exclusions WHERE (? IS NULL OR repo=?) GROUP BY repo,reason ORDER BY repo,reason",
                (repo, repo),
            )
        ],
        ownership="continuity leases (read-through)",
    )
