"""Incremental repository indexing and syntax-lane persistence."""

from __future__ import annotations
import fnmatch
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from . import code_extract, code_projects, code_store as store

MAX_FILE_BYTES = 512 * 1024
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", "build"}


def discover(repo):
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "ls-files",
            "-z",
            "--cached",
            "--others",
            "--exclude-standard",
        ],
        capture_output=True,
        timeout=60,
    )
    if result.returncode == 0:
        names = sorted(set(os.fsdecode(p) for p in result.stdout.split(b"\0") if p))
    else:
        names = []
        patterns = []
        try:
            patterns = [
                p.strip()
                for p in (repo / ".gitignore").read_text().splitlines()
                if p.strip() and not p.startswith(("#", "!"))
            ]
        except OSError:
            pass
        for directory, dirs, files in os.walk(repo):
            dirs[:] = sorted(
                d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")
            )
            for name in sorted(files):
                rel = (Path(directory) / name).relative_to(repo).as_posix()
                if not any(
                    fnmatch.fnmatch(rel, p.strip("/"))
                    or any(
                        fnmatch.fnmatch(part, p.strip("/")) for part in Path(rel).parts
                    )
                    for p in patterns
                ):
                    names.append(rel)
    return [n for n in names if not any(p in SKIP_DIRS for p in Path(n).parts)]


def insert_symbol(conn, repo, row):
    value = {
        **row,
        "repo": repo,
        "name_folded": row["name"].casefold(),
        "qualified_folded": row["qualified"].casefold(),
        "attributes": json.dumps(row.get("attributes", [])),
        "stale": int(row.get("stale", False)),
        "unanchored": int(row.get("unanchored", False)),
        "column": row.get("column", 0),
    }
    columns = [
        "repo",
        "name",
        "qualified",
        "kind",
        "path",
        "line",
        "end_line",
        "signature",
        "lane",
        "extractor",
        "anchor_sha",
        "name_folded",
        "qualified_folded",
        "attributes",
        "stale",
        "unanchored",
        "column",
    ]
    conn.execute(
        "INSERT OR REPLACE INTO symbols("
        + ",".join(columns)
        + ") VALUES("
        + ",".join("?" for _ in columns)
        + ")",
        [value[k] for k in columns],
    )


def insert_ref(conn, repo, row, *, lane="syntax", extractor="stdlib", anchor=None):
    conn.execute(
        "INSERT INTO refs(repo,name,qualified,path,line,kind,lane,extractor,anchor_sha) VALUES(?,?,?,?,?,?,?,?,?)",
        (
            repo,
            row["name"],
            row["qualified"],
            row["path"],
            row["line"],
            row["kind"],
            lane,
            extractor,
            anchor,
        ),
    )


def insert_edge(
    conn,
    repo,
    src,
    dst,
    kind,
    *,
    lane="syntax",
    extractor="stdlib",
    anchor=None,
    evidence=None,
):
    conn.execute(
        "INSERT OR REPLACE INTO edges(repo,src_path,dst_text,kind,lane,extractor,anchor_sha,evidence) VALUES(?,?,?,?,?,?,?,?)",
        (repo, src, dst, kind, lane, extractor, anchor, json.dumps(evidence or {})),
    )


def index_repo(
    root,
    repo_path,
    *,
    paths=None,
    max_file_bytes=None,
    trigram=True,
    resolve=False,
    indexer_checkout=None,
):
    repo = Path(repo_path).resolve()
    if not repo.is_dir():
        raise ValueError("repository does not exist")
    if store.db_path(root).resolve().is_relative_to(repo):
        raise ValueError("code index state must be outside the product repository")
    maximum = (
        max_file_bytes
        if max_file_bytes is not None
        else int(os.environ.get("HX_CODE_MAX_FILE_BYTES", MAX_FILE_BYTES))
    )
    if maximum < 1:
        raise ValueError("max-file-bytes must be positive")
    identity = store.namespace(repo)
    conn = store.connect(root, trigram=trigram)
    indexed = changed = skipped = 0
    from . import code_adapters

    detected = code_adapters.detect()
    try:
        excluded_paths = {
            r[0]
            for r in conn.execute(
                "SELECT path FROM exclusions WHERE repo=?", (identity,)
            )
        }
        existing_rows = list(
            conn.execute(
                "SELECT path,sha256,generation FROM files WHERE repo=?", (identity,)
            )
        )
        existing = {r["path"]: r["sha256"] for r in existing_rows}
        file_generations = {r["path"]: r["generation"] for r in existing_rows}
        generation = hashlib.sha256(
            Path(code_extract.__file__).read_bytes()
            + Path(code_adapters.__file__).read_bytes()
            + Path(code_projects.__file__).read_bytes()
            + json.dumps(detected, sort_keys=True).encode()
        ).hexdigest()
        generation_key = "extractors:" + identity
        previous_generation = conn.execute(
            "SELECT value FROM metadata WHERE key=?", (generation_key,)
        ).fetchone()
        if previous_generation and previous_generation[0] != generation:
            for table in ("symbols", "refs", "edges"):
                conn.execute(
                    f"UPDATE {table} SET stale=1 WHERE repo=? AND lane<>'imported'",
                    (identity,),
                )
        from .code_invalidation import dependencies, invalidate

        reverse = dependencies(conn, identity)
        changed_paths = set()
        refreshed = set()
        stale_paths = {
            r[0]
            for r in conn.execute(
                "SELECT path FROM symbols WHERE repo=? AND stale=1 UNION SELECT path FROM refs WHERE repo=? AND stale=1 UNION SELECT src_path FROM edges WHERE repo=? AND stale=1",
                (identity, identity, identity),
            )
        }
        names = [
            n
            for n in discover(repo)
            if (repo / n).is_file() and (repo / n).resolve().is_relative_to(repo)
        ]
        if paths is not None:
            scopes = [str(p).rstrip("/") for p in paths]
            if any(Path(p).is_absolute() or ".." in Path(p).parts for p in scopes):
                raise ValueError("index paths must be repository relative")
            names = [
                n
                for n in names
                if any(n == p or n.startswith(p + "/") or p == "." for p in scopes)
            ]
            old = [
                n
                for n in set(existing) | excluded_paths
                if any(n == p or n.startswith(p + "/") or p == "." for p in scopes)
            ]
        else:
            old = list(set(existing) | excluded_paths)
        for gone in sorted(set(old) - set(names)):
            store.delete_file(conn, identity, gone)
            changed += 1
            changed_paths.add(gone)
        for rel in names:
            path = repo / rel
            if not path.resolve().is_relative_to(repo) or not path.is_file():
                continue
            try:
                stat = path.stat()
                if stat.st_size > maximum:
                    from .code_exclusions import record

                    skipped += 1
                    if rel in existing:
                        store.delete_file(conn, identity, rel)
                    updated = record(conn, identity, rel, path, "oversize")
                    changed += int(updated)
                    if updated:
                        changed_paths.add(rel)
                    refreshed.add(rel)
                    continue
                data = path.read_bytes()
            except OSError:
                continue
            if b"\0" in data:
                from .code_exclusions import record

                skipped += 1
                if rel in existing:
                    store.delete_file(conn, identity, rel)
                updated = record(conn, identity, rel, path, "binary")
                changed += int(updated)
                if updated:
                    changed_paths.add(rel)
                refreshed.add(rel)
                continue
            sha = hashlib.sha256(data).hexdigest()
            indexed += 1
            refreshed.add(rel)
            if (
                existing.get(rel) == sha
                and rel not in stale_paths
                and file_generations.get(rel) == generation
            ):
                continue
            changed += 1
            changed_paths.add(rel)
            text = data.decode("utf-8", errors="replace")
            symbols, refs, edges = code_extract.extract(text, rel)
            symbols, refs = code_adapters.syntax(
                repo, rel, text, symbols, refs, detected
            )
            store.delete_file(conn, identity, rel)
            conn.execute(
                "INSERT INTO files(repo,path,sha256,size,mtime_ns,symbols,body,generation) VALUES(?,?,?,?,?,?,?,?)",
                (
                    identity,
                    rel,
                    sha,
                    len(data),
                    stat.st_mtime_ns,
                    " ".join(s["qualified"] for s in symbols),
                    text,
                    generation,
                ),
            )
            row = conn.execute(
                "SELECT id,symbols FROM files WHERE repo=? AND path=?", (identity, rel)
            ).fetchone()
            for table in store.fts_tables(conn):
                conn.execute(
                    f"INSERT INTO {table}(rowid,repo,path,symbols,body) VALUES(?,?,?,?,?)",
                    (row["id"], identity, rel, row["symbols"], text),
                )
            for symbol in symbols:
                insert_symbol(conn, identity, symbol)
            for ref in refs:
                insert_ref(
                    conn,
                    identity,
                    ref,
                    extractor=ref.get(
                        "extractor",
                        "stdlib-" + code_extract.LANGUAGES.get(path.suffix, "text"),
                    ),
                    anchor=sha,
                )
            for src, dst, kind in edges:
                insert_edge(conn, identity, src, dst, kind, anchor=sha)
            project, dependencies = code_projects.extract(text, rel)
            if project:
                conn.execute(
                    "INSERT INTO projects VALUES(?,?,?,?)",
                    (identity, project["name"], rel, json.dumps(project["data"])),
                )
                for dst in dependencies:
                    conn.execute(
                        "INSERT OR IGNORE INTO project_edges VALUES(?,?,?,?)",
                        (identity, rel, dst, "project_depends_on"),
                    )
        invalidate(conn, identity, reverse, changed_paths, refreshed)
        head = store.git(repo, "rev-parse", "HEAD")
        dirty = bool(store.git(repo, "status", "--porcelain"))
        conn.execute(
            "INSERT INTO repos VALUES(?,?,?,?,?) ON CONFLICT(repo) DO UPDATE SET root=excluded.root,head=excluded.head,dirty=excluded.dirty,indexed_at=excluded.indexed_at",
            (identity, str(repo), head, int(dirty), time.time()),
        )
        conn.execute(
            "INSERT OR REPLACE INTO metadata VALUES(?,?)",
            ("extractors", json.dumps(detected)),
        )
        conn.execute(
            "INSERT OR REPLACE INTO metadata VALUES(?,?)", (generation_key, generation)
        )
        resolved = (
            code_adapters.resolved(conn, identity, repo, indexer_checkout)
            if resolve
            else {"status": "not-requested"}
        )
        conn.commit()
        return {
            "repo": identity,
            "files": conn.execute(
                "SELECT count(*) FROM files WHERE repo=?", (identity,)
            ).fetchone()[0],
            "symbols": conn.execute(
                "SELECT count(*) FROM symbols WHERE repo=?", (identity,)
            ).fetchone()[0],
            "changed": changed,
            "skipped": skipped,
            "head": head,
            "resolved": resolved,
        }
    finally:
        conn.close()
