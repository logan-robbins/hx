"""Repository-scoped derived graph storage; never an ownership authority."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from pathlib import Path


class GraphConnection(sqlite3.Connection):
    source_cache = None
    symbol_cache = None
    snapshot = None
    historical = False
    snapshot_maps = None
    snapshot_scopes = None
    membership_cache = None
    graph_cache = None
    body_cache = None
    path_tree = None


SCHEMA = """
CREATE TABLE IF NOT EXISTS repos(repo TEXT PRIMARY KEY, root TEXT NOT NULL, head TEXT, dirty INTEGER NOT NULL, indexed_at REAL);
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, repo TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
 size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, symbols TEXT NOT NULL, body TEXT NOT NULL,generation TEXT NOT NULL DEFAULT '', UNIQUE(repo,path));
CREATE TABLE IF NOT EXISTS symbols(repo TEXT NOT NULL,name TEXT NOT NULL,qualified TEXT NOT NULL,kind TEXT NOT NULL,
 path TEXT NOT NULL,line INTEGER NOT NULL,end_line INTEGER NOT NULL,signature TEXT NOT NULL,lane TEXT NOT NULL,
 extractor TEXT NOT NULL,anchor_sha TEXT,name_folded TEXT NOT NULL,qualified_folded TEXT NOT NULL DEFAULT '',attributes TEXT NOT NULL,stale INTEGER NOT NULL DEFAULT 0,
 unanchored INTEGER NOT NULL DEFAULT 0, column INTEGER NOT NULL DEFAULT 0,
 UNIQUE(repo,qualified,path,line,column,signature,lane,extractor));
CREATE INDEX IF NOT EXISTS symbol_name ON symbols(repo,name_folded);
CREATE INDEX IF NOT EXISTS symbol_path ON symbols(repo,path);
CREATE INDEX IF NOT EXISTS symbol_qualified ON symbols(repo,lower(qualified));
CREATE TABLE IF NOT EXISTS refs(repo TEXT NOT NULL,name TEXT NOT NULL,qualified TEXT NOT NULL,path TEXT NOT NULL,
 line INTEGER NOT NULL,kind TEXT NOT NULL,lane TEXT NOT NULL,extractor TEXT NOT NULL,anchor_sha TEXT,stale INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ref_name ON refs(repo,name);
CREATE INDEX IF NOT EXISTS ref_path ON refs(repo,path);
CREATE INDEX IF NOT EXISTS ref_folded ON refs(repo,lower(name));
CREATE INDEX IF NOT EXISTS ref_qualified ON refs(repo,lower(qualified));
CREATE TABLE IF NOT EXISTS edges(repo TEXT NOT NULL,src_path TEXT NOT NULL,dst_text TEXT NOT NULL,kind TEXT NOT NULL,
 lane TEXT NOT NULL,extractor TEXT NOT NULL,anchor_sha TEXT,stale INTEGER NOT NULL DEFAULT 0,evidence TEXT NOT NULL DEFAULT '{}',
 UNIQUE(repo,src_path,dst_text,kind,lane,extractor));
CREATE INDEX IF NOT EXISTS edge_path ON edges(repo,src_path);
CREATE TABLE IF NOT EXISTS projects(repo TEXT NOT NULL,name TEXT NOT NULL,path TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(repo,path));
CREATE TABLE IF NOT EXISTS project_edges(repo TEXT NOT NULL,src_path TEXT NOT NULL,dst_text TEXT NOT NULL,kind TEXT NOT NULL,
 PRIMARY KEY(repo,src_path,dst_text));
CREATE TABLE IF NOT EXISTS contracts(repo TEXT NOT NULL,name TEXT NOT NULL,path TEXT NOT NULL,version TEXT NOT NULL,
 producer TEXT NOT NULL,sha TEXT NOT NULL,PRIMARY KEY(repo,name,path,version));
CREATE TABLE IF NOT EXISTS exclusions(repo TEXT NOT NULL,path TEXT NOT NULL,sha256 TEXT NOT NULL,size INTEGER NOT NULL,reason TEXT NOT NULL,PRIMARY KEY(repo,path));
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
"""


def db_path(root):
    return Path(root) / "state" / "code-index" / "index.db"


def _connect(root, *, existing=False, trigram=None):
    path = db_path(root)
    if existing and not path.is_file():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30, factory=GraphConnection)
    conn.row_factory = sqlite3.Row
    conn.source_cache = {}
    conn.symbol_cache = {}
    conn.execute("PRAGMA journal_mode=WAL")
    # The old index had no repository identity. It is derived, so rebuild it.
    old = conn.execute("SELECT 1 FROM sqlite_master WHERE name='files'").fetchone()
    if old and "repo" not in {r[1] for r in conn.execute("PRAGMA table_info(files)")}:
        for table in ("files", "symbols", "edges", "code_search", "code_trigram"):
            conn.execute("DROP TABLE IF EXISTS " + table)
    conn.executescript(SCHEMA)
    from . import code_journal, code_snapshot

    conn.executescript(code_snapshot.SCHEMA + code_journal.SCHEMA)
    if "kind" not in {
        r[1] for r in conn.execute("PRAGMA table_info(snapshot_ref_postings)")
    }:
        conn.execute(
            "ALTER TABLE snapshot_ref_postings ADD COLUMN kind TEXT NOT NULL DEFAULT ''"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS snapshot_ref_kind ON snapshot_ref_postings(kind,repo,path,artifact)"
    )
    version = conn.execute(
        "SELECT value FROM metadata WHERE key='snapshot_postings_version'"
    ).fetchone()
    if not version or version[0] != "4":
        for row in conn.execute("SELECT identity,data FROM graph_objects").fetchall():
            code_snapshot.put_postings(
                conn, row["identity"], code_snapshot.unpacked(row["data"])
            )
        conn.execute(
            "INSERT OR REPLACE INTO metadata VALUES('snapshot_postings_version','4')"
        )
    for column in ("files_count", "symbols_count"):
        if column not in {
            r[1] for r in conn.execute("PRAGMA table_info(code_snapshots)")
        }:
            conn.execute(
                f"ALTER TABLE code_snapshots ADD COLUMN {column} INTEGER NOT NULL DEFAULT 0"
            )
    if not conn.execute(
        "SELECT 1 FROM metadata WHERE key='snapshot_counts_version'"
    ).fetchone():
        code_snapshot.backfill_counts(conn)
        conn.execute("INSERT INTO metadata VALUES('snapshot_counts_version','1')")
    if "dirty" not in {
        r[1] for r in conn.execute("PRAGMA table_info(code_changesets)")
    }:
        conn.execute(
            "ALTER TABLE code_changesets ADD COLUMN dirty INTEGER NOT NULL DEFAULT 0"
        )
    symbol_schema = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='symbols'"
    ).fetchone()[0]
    if "line,column,signature" not in symbol_schema.replace(" ", "").replace("\n", ""):
        conn.execute("ALTER TABLE symbols RENAME TO symbols_previous")
        conn.executescript(SCHEMA)
        columns = [r[1] for r in conn.execute("PRAGMA table_info(symbols_previous)")]
        names = ",".join(columns)
        conn.execute(
            f"INSERT INTO symbols({names}) SELECT {names} FROM symbols_previous"
        )
        conn.execute("DROP TABLE symbols_previous")
        conn.executescript(SCHEMA)
    if "qualified_folded" not in {
        r[1] for r in conn.execute("PRAGMA table_info(symbols)")
    }:
        conn.execute(
            "ALTER TABLE symbols ADD COLUMN qualified_folded TEXT NOT NULL DEFAULT ''"
        )
    for row in conn.execute(
        "SELECT rowid,qualified FROM symbols WHERE qualified_folded=''"
    ).fetchall():
        conn.execute(
            "UPDATE symbols SET qualified_folded=? WHERE rowid=?",
            (row["qualified"].casefold(), row["rowid"]),
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS symbol_qualified_folded ON symbols(repo,qualified_folded)"
    )
    if "resolved" not in {
        r[1] for r in conn.execute("PRAGMA table_info(code_changesets)")
    }:
        conn.execute("ALTER TABLE code_changesets ADD COLUMN resolved BLOB")
    if "snapshot_base" not in {
        r[1] for r in conn.execute("PRAGMA table_info(code_changesets)")
    }:
        conn.execute("ALTER TABLE code_changesets ADD COLUMN snapshot_base TEXT")
    if "generation" not in {r[1] for r in conn.execute("PRAGMA table_info(files)")}:
        conn.execute("ALTER TABLE files ADD COLUMN generation TEXT NOT NULL DEFAULT ''")
    if trigram is None:
        option = conn.execute(
            "SELECT value FROM metadata WHERE key='config.trigram'"
        ).fetchone()
        trigram = option is None or option[0] == "true"
    else:
        conn.execute(
            "INSERT OR REPLACE INTO metadata VALUES('config.trigram',?)",
            ("true" if trigram else "false",),
        )
    for table, tokenizer in [
        ("code_search", "porter unicode61"),
        ("code_trigram", "trigram"),
    ]:
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name=?", (table,)
        ).fetchone()
        if present and "repo" not in {
            r[1] for r in conn.execute("PRAGMA table_info(" + table + ")")
        }:
            conn.execute("DROP TABLE " + table)
            present = None
        if table == "code_trigram" and not trigram:
            conn.execute("DROP TABLE IF EXISTS " + table)
            continue
        try:
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING fts5(repo UNINDEXED,path,symbols,body,content='files',content_rowid='id',tokenize='{tokenizer}')"
            )
            if not present:
                conn.execute(f"INSERT INTO {table}({table}) VALUES('rebuild')")
        except sqlite3.OperationalError:
            if table != "code_trigram":
                raise
    import zlib

    conn.create_function(
        "hx_source",
        1,
        lambda data: zlib.decompress(data).decode("utf-8", errors="replace"),
        deterministic=True,
    )
    if not conn.execute(
        "SELECT 1 FROM metadata WHERE key='source_search_version'"
    ).fetchone():
        conn.execute(
            "INSERT INTO source_search(rowid,body) SELECT rowid,hx_source(data) FROM source_objects"
        )
        conn.execute("INSERT INTO metadata VALUES('source_search_version','1')")
    if trigram:
        present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='source_trigram'"
        ).fetchone()
        try:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS source_trigram USING fts5(body,content='',tokenize='trigram')"
            )
            if not present:
                conn.execute(
                    "INSERT INTO source_trigram(rowid,body) SELECT rowid,hx_source(data) FROM source_objects"
                )
        except sqlite3.OperationalError:
            pass
    else:
        conn.execute("DROP TABLE IF EXISTS source_trigram")
    conn.commit()
    return conn


def connect(root, *, existing=False, trigram=None):
    """Serialize migrations only; independent graph readers remain concurrent."""
    path = db_path(root)
    if existing and not path.is_file():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    import fcntl

    with (path.parent / ".schema.lock").open("a") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            return _connect(root, existing=existing, trigram=trigram)
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def git(repo, *args):
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, timeout=30
    )
    return (
        result.stdout.decode("utf-8", errors="replace").strip()
        if result.returncode == 0
        else None
    )


def namespace(repo):
    repo = Path(repo).resolve()
    try:
        identity = json.loads((repo / ".hx/map/manifest.json").read_text())["repo_id"]
        if isinstance(identity, str) and identity:
            return identity
    except OSError, ValueError, KeyError:
        pass
    remotes = git(repo, "remote", "get-url", "origin")
    if not remotes:
        names = git(repo, "remote")
        remotes = (
            git(repo, "remote", "get-url", names.splitlines()[0]) if names else None
        )
    return remotes or str(repo)


def source_bytes(path):
    """Git stores a symlink target as its content, independently of its referent."""
    return os.fsencode(os.readlink(path)) if path.is_symlink() else path.read_bytes()


def span_sha(text, line, end_line):
    return hashlib.sha256(
        "".join(text.splitlines(keepends=True)[line - 1 : end_line]).encode()
    ).hexdigest()


def fts_tables(conn):
    if conn.snapshot is not None:
        return []
    return [
        r[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE name IN ('code_search','code_trigram')"
        )
    ]


def delete_file(conn, repo, path):
    row = conn.execute(
        "SELECT * FROM files WHERE repo=? AND path=?", (repo, path)
    ).fetchone()
    if row:
        for table in fts_tables(conn):
            conn.execute(
                f"INSERT INTO {table}({table},rowid,repo,path,symbols,body) VALUES('delete',?,?,?,?,?)",
                (row["id"], repo, path, row["symbols"], row["body"]),
            )
    conn.execute("DELETE FROM files WHERE repo=? AND path=?", (repo, path))
    conn.execute("DELETE FROM exclusions WHERE repo=? AND path=?", (repo, path))
    for table, key in [
        ("symbols", "path"),
        ("refs", "path"),
        ("edges", "src_path"),
        ("projects", "path"),
        ("project_edges", "src_path"),
    ]:
        conn.execute(
            f"DELETE FROM {table} WHERE repo=? AND {key}=? AND "
            + (
                "lane IN ('syntax','text','resolved')"
                if table in {"symbols", "refs", "edges"}
                else "1"
            ),
            (repo, path),
        )


def decorate(conn, row):
    result = dict(row)
    if "attributes" in result:
        result["attributes"] = json.loads(result["attributes"])
    if "evidence" in result:
        result["evidence"] = json.loads(result["evidence"])
    if (
        result.get("lane") == "imported"
        and not result.get("anchor_sha")
        and "sha" not in result
    ):
        result["unanchored"] = True
    identity, path = result["repo"], result.get("path", result.get("src_path"))
    graph = None
    if conn.snapshot is not None:
        from .code_snapshot import graph_for

        graph = graph_for(conn, identity, path)
    if conn.snapshot is not None and conn.historical:
        generation = (
            graph["files"][0]["generation"] if graph and graph["files"] else None
        )
        result["stale"] = bool(result.get("stale")) or bool(
            generation
            and generation != conn.snapshot["generation"]
            and result.get("lane") != "imported"
        )
        result["snapshot"] = conn.snapshot["identity"]
        result["source_proof"] = "immutable-snapshot"
        return result
    stale = bool(result.get("stale"))
    key = (identity, path)
    if key not in conn.source_cache:
        repo = conn.execute(
            "SELECT root FROM repos WHERE repo=?", (identity,)
        ).fetchone()
        if conn.snapshot is not None:
            file = (
                graph["files"][0]
                if graph and graph["files"]
                else dict(graph["exclusions"][0], generation=None)
                if graph and graph["exclusions"]
                else None
            )
        else:
            file = conn.execute(
                "SELECT sha256,generation FROM files WHERE repo=? AND path=? UNION ALL SELECT sha256,NULL AS generation FROM exclusions WHERE repo=? AND path=?",
                (*key, *key),
            ).fetchone()
        actual = None
        if repo and repo[0] and path:
            target = Path(repo[0]) / path
            if target.parent.resolve().is_relative_to(Path(repo[0]).resolve()):
                try:
                    actual = hashlib.sha256(source_bytes(target)).hexdigest()
                except OSError:
                    pass
        if conn.snapshot is not None:
            marked = graph and any(
                r.get("stale")
                for table in ("symbols", "refs", "edges")
                for r in graph[table]
            )
        else:
            marked = conn.execute(
                "SELECT 1 FROM symbols WHERE repo=? AND path=? AND stale=1 UNION SELECT 1 FROM refs WHERE repo=? AND path=? AND stale=1 UNION SELECT 1 FROM edges WHERE repo=? AND src_path=? AND stale=1 LIMIT 1",
                (*key, *key, *key),
            ).fetchone()
        generation = conn.execute(
            "SELECT value FROM metadata WHERE key=?", ("extractors:" + identity,)
        ).fetchone()
        old_generation = bool(
            file and generation and file["generation"] != generation[0]
        )
        mode_changed = False
        if conn.snapshot is not None and repo and path:
            mode = graph.get("mode") if graph else None
            try:
                stat = (Path(repo[0]) / path).lstat()
                actual_mode = (
                    "120000"
                    if (Path(repo[0]) / path).is_symlink()
                    else "100755"
                    if stat.st_mode & 0o111
                    else "100644"
                )
                mode_changed = bool(mode and mode != actual_mode)
            except OSError:
                mode_changed = bool(mode)
        conn.source_cache[key] = (
            actual,
            file["sha256"] if file else None,
            bool(marked) or old_generation or mode_changed,
        )
    actual, indexed, marked = conn.source_cache[key]
    if not result.get("unanchored"):
        stale |= actual is None or (indexed is not None and actual != indexed) or marked
        if (
            indexed is None
            and actual is not None
            and result.get("line")
            and result.get("anchor_sha")
        ):
            repo = conn.execute(
                "SELECT root FROM repos WHERE repo=?", (identity,)
            ).fetchone()
            text = (Path(repo[0]) / path).read_text()
            stale |= (
                span_sha(text, result["line"], result["end_line"])
                != result["anchor_sha"]
            )
    result["stale"] = stale
    return result
