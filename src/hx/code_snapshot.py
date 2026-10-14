"""Immutable graph snapshots backed by compressed objects and a persistent treap.

Only changed paths and their O(log paths) ancestors are written for a new map.
The mutable graph tables are a compatibility projection, never revision storage.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
import zlib
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS source_objects(sha TEXT PRIMARY KEY,size INTEGER NOT NULL,data BLOB NOT NULL);
CREATE VIRTUAL TABLE IF NOT EXISTS source_search USING fts5(body,content='',tokenize='porter unicode61');
CREATE TABLE IF NOT EXISTS extraction_objects(identity TEXT PRIMARY KEY,sha TEXT NOT NULL,generation TEXT NOT NULL,language TEXT NOT NULL,path TEXT NOT NULL,context TEXT NOT NULL,data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS graph_objects(identity TEXT PRIMARY KEY,sha TEXT,data BLOB NOT NULL);
CREATE TABLE IF NOT EXISTS snapshot_contract_postings(artifact TEXT NOT NULL,ordinal INTEGER NOT NULL,repo TEXT NOT NULL,path TEXT NOT NULL,name TEXT NOT NULL,name_folded TEXT NOT NULL,version TEXT NOT NULL,PRIMARY KEY(artifact,ordinal));
CREATE INDEX IF NOT EXISTS snapshot_contract_name ON snapshot_contract_postings(name_folded,repo,path,artifact);
CREATE TABLE IF NOT EXISTS snapshot_project_postings(artifact TEXT NOT NULL,ordinal INTEGER NOT NULL,repo TEXT NOT NULL,path TEXT NOT NULL,name TEXT NOT NULL,PRIMARY KEY(artifact,ordinal));
CREATE INDEX IF NOT EXISTS snapshot_project_name ON snapshot_project_postings(name,repo,path,artifact);
CREATE TABLE IF NOT EXISTS snapshot_object_counts(artifact TEXT PRIMARY KEY,files INTEGER NOT NULL,symbols INTEGER NOT NULL,exclusions INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS snapshot_symbol_postings(artifact TEXT NOT NULL,ordinal INTEGER NOT NULL,repo TEXT NOT NULL,path TEXT NOT NULL,name TEXT NOT NULL,name_folded TEXT NOT NULL,qualified_folded TEXT NOT NULL,lane TEXT NOT NULL,line INTEGER NOT NULL,PRIMARY KEY(artifact,ordinal));
CREATE INDEX IF NOT EXISTS snapshot_symbol_name ON snapshot_symbol_postings(name_folded,repo,path,artifact);
CREATE INDEX IF NOT EXISTS snapshot_symbol_qualified ON snapshot_symbol_postings(qualified_folded,repo,path,artifact);
CREATE TABLE IF NOT EXISTS snapshot_signature_terms(artifact TEXT NOT NULL,ordinal INTEGER NOT NULL,repo TEXT NOT NULL,path TEXT NOT NULL,term TEXT NOT NULL,PRIMARY KEY(artifact,ordinal,term));
CREATE INDEX IF NOT EXISTS snapshot_signature_term ON snapshot_signature_terms(term,repo,path,artifact,ordinal);
CREATE TABLE IF NOT EXISTS snapshot_test_postings(artifact TEXT NOT NULL,ordinal INTEGER NOT NULL,repo TEXT NOT NULL,path TEXT NOT NULL,name_folded TEXT NOT NULL,PRIMARY KEY(artifact,ordinal));
CREATE INDEX IF NOT EXISTS snapshot_test_name ON snapshot_test_postings(name_folded,repo,path,artifact);
CREATE INDEX IF NOT EXISTS snapshot_test_path ON snapshot_test_postings(repo,path,artifact);
CREATE TABLE IF NOT EXISTS snapshot_ref_postings(artifact TEXT NOT NULL,ordinal INTEGER NOT NULL,repo TEXT NOT NULL,path TEXT NOT NULL,name_folded TEXT NOT NULL,qualified_folded TEXT NOT NULL,lane TEXT NOT NULL,line INTEGER NOT NULL,kind TEXT NOT NULL DEFAULT '',PRIMARY KEY(artifact,ordinal));
CREATE INDEX IF NOT EXISTS snapshot_ref_name ON snapshot_ref_postings(name_folded,repo,path,artifact);
CREATE INDEX IF NOT EXISTS snapshot_ref_qualified ON snapshot_ref_postings(qualified_folded,repo,path,artifact);
CREATE TABLE IF NOT EXISTS snapshot_edge_postings(artifact TEXT NOT NULL,ordinal INTEGER NOT NULL,repo TEXT NOT NULL,path TEXT NOT NULL,dst_folded TEXT NOT NULL,lane TEXT NOT NULL,kind TEXT NOT NULL,PRIMARY KEY(artifact,ordinal));
CREATE INDEX IF NOT EXISTS snapshot_edge_target ON snapshot_edge_postings(dst_folded,lane,repo,path,artifact);
CREATE TABLE IF NOT EXISTS snapshot_import_postings(artifact TEXT NOT NULL,ordinal INTEGER NOT NULL,repo TEXT NOT NULL,path TEXT NOT NULL,leaf TEXT NOT NULL,PRIMARY KEY(artifact,ordinal,leaf));
CREATE INDEX IF NOT EXISTS snapshot_import_leaf ON snapshot_import_postings(leaf,repo,path,artifact,ordinal);
CREATE TABLE IF NOT EXISTS snapshot_file_postings(artifact TEXT PRIMARY KEY,repo TEXT NOT NULL,path TEXT NOT NULL,sha TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS snapshot_file_sha ON snapshot_file_postings(sha,repo,path,artifact);
CREATE INDEX IF NOT EXISTS snapshot_file_path ON snapshot_file_postings(repo,path,artifact);
CREATE TABLE IF NOT EXISTS snapshot_path_postings(artifact TEXT PRIMARY KEY,repo TEXT NOT NULL,path TEXT NOT NULL,stem TEXT NOT NULL,stem_folded TEXT NOT NULL,suffix TEXT NOT NULL,sha TEXT NOT NULL,size INTEGER NOT NULL,generation TEXT,is_text INTEGER NOT NULL,reason TEXT);
CREATE INDEX IF NOT EXISTS snapshot_path_stem ON snapshot_path_postings(repo,stem,path,artifact);
CREATE INDEX IF NOT EXISTS snapshot_path_stem_folded ON snapshot_path_postings(repo,stem_folded,path,artifact);
CREATE INDEX IF NOT EXISTS snapshot_path_scope ON snapshot_path_postings(repo,path,artifact);
CREATE TABLE IF NOT EXISTS snapshot_nodes(identity TEXT PRIMARY KEY,path TEXT NOT NULL,artifact TEXT NOT NULL,left_node TEXT,right_node TEXT,priority TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS code_snapshots(identity TEXT PRIMARY KEY,repo TEXT NOT NULL,head TEXT,overlay TEXT NOT NULL,generation TEXT NOT NULL,map_root TEXT,parent TEXT,complete INTEGER NOT NULL,created_at REAL NOT NULL,files_count INTEGER NOT NULL DEFAULT 0,symbols_count INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS snapshot_revision ON code_snapshots(repo,head,complete);
CREATE TABLE IF NOT EXISTS checkout_heads(root TEXT PRIMARY KEY,repo TEXT NOT NULL,snapshot TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS snapshot_refs(repo TEXT NOT NULL,ref TEXT NOT NULL,snapshot TEXT NOT NULL,PRIMARY KEY(repo,ref));
"""


def packed(value):
    return zlib.compress(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    )


def unpacked(value):
    return json.loads(zlib.decompress(value))


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def put_postings(conn, artifact, value):
    for ordinal, row in enumerate(value.get("contracts", [])):
        conn.execute(
            "INSERT OR IGNORE INTO snapshot_contract_postings VALUES(?,?,?,?,?,?,?)",
            (
                artifact,
                ordinal,
                row["repo"],
                row["path"],
                row["name"],
                row["name"].casefold(),
                row["version"],
            ),
        )
    for ordinal, row in enumerate(value.get("projects", [])):
        conn.execute(
            "INSERT OR IGNORE INTO snapshot_project_postings VALUES(?,?,?,?,?)",
            (artifact, ordinal, row["repo"], row["path"], row["name"]),
        )
    conn.execute(
        "INSERT OR IGNORE INTO snapshot_object_counts VALUES(?,?,?,?)",
        (
            artifact,
            len(value.get("files", [])),
            len(value.get("symbols", [])),
            len(value.get("exclusions", [])),
        ),
    )
    for ordinal, row in enumerate(value.get("symbols", [])):
        conn.execute(
            "INSERT OR IGNORE INTO snapshot_symbol_postings VALUES(?,?,?,?,?,?,?,?,?)",
            (
                artifact,
                ordinal,
                row["repo"],
                row["path"],
                row["name"],
                row["name"].casefold(),
                row["qualified"].casefold(),
                row["lane"],
                row["line"],
            ),
        )
        if row["kind"] in {"class", "struct", "impl"}:
            for term in set(re.findall(r"\w+", row["signature"].casefold())):
                conn.execute(
                    "INSERT OR IGNORE INTO snapshot_signature_terms VALUES(?,?,?,?,?)",
                    (artifact, ordinal, row["repo"], row["path"], term),
                )
        labels = row.get("attributes", "").casefold()
        if (
            "test" in row["path"].casefold()
            or "spec" in row["path"].casefold()
            or "test" in row["name"].casefold()
            or any(t in labels for t in ("test", "fact", "theory"))
        ):
            conn.execute(
                "INSERT OR IGNORE INTO snapshot_test_postings VALUES(?,?,?,?,?)",
                (artifact, ordinal, row["repo"], row["path"], row["name"].casefold()),
            )
    for ordinal, row in enumerate(value.get("refs", [])):
        conn.execute(
            "INSERT OR REPLACE INTO snapshot_ref_postings(artifact,ordinal,repo,path,name_folded,qualified_folded,lane,line,kind) VALUES(?,?,?,?,?,?,?,?,?)",
            (
                artifact,
                ordinal,
                row["repo"],
                row["path"],
                row["name"].casefold(),
                row["qualified"].casefold(),
                row["lane"],
                row["line"],
                row["kind"],
            ),
        )
    for ordinal, row in enumerate(value.get("edges", [])):
        conn.execute(
            "INSERT OR IGNORE INTO snapshot_edge_postings VALUES(?,?,?,?,?,?,?)",
            (
                artifact,
                ordinal,
                row["repo"],
                row["src_path"],
                row["dst_text"].casefold(),
                row["lane"],
                row["kind"],
            ),
        )
        if row["kind"] == "imports":
            text = row["dst_text"].replace("\\", "/")
            leaves = {
                Path(text).name,
                Path(text).stem,
                text.replace(".", "/").split("/")[-1],
            }
            for leaf in leaves:
                if leaf:
                    conn.execute(
                        "INSERT OR IGNORE INTO snapshot_import_postings VALUES(?,?,?,?,?)",
                        (
                            artifact,
                            ordinal,
                            row["repo"],
                            row["src_path"],
                            leaf.casefold(),
                        ),
                    )
    for is_text, table in ((1, "files"), (0, "exclusions")):
        for row in value.get(table, []):
            path = Path(row["path"])
            conn.execute(
                "INSERT OR IGNORE INTO snapshot_path_postings VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (
                    artifact,
                    row["repo"],
                    row["path"],
                    path.stem,
                    path.stem.casefold(),
                    path.suffix.lower(),
                    row["sha256"],
                    row["size"],
                    row.get("generation"),
                    is_text,
                    row.get("reason"),
                ),
            )
    for row in value.get("files", []):
        conn.execute(
            "INSERT OR IGNORE INTO snapshot_file_postings VALUES(?,?,?,?)",
            (artifact, row["repo"], row["path"], row["sha256"]),
        )


def put_graph(conn, value, sha):
    artifact = digest(value)
    inserted = conn.execute(
        "INSERT OR IGNORE INTO graph_objects VALUES(?,?,?)",
        (artifact, sha, packed(value)),
    )
    if inserted.rowcount:
        put_postings(conn, artifact, value)
    return artifact


def source(conn, data):
    sha = hashlib.sha256(data).hexdigest()
    inserted = conn.execute(
        "INSERT OR IGNORE INTO source_objects VALUES(?,?,?)",
        (sha, len(data), zlib.compress(data)),
    )
    if inserted.rowcount:
        rowid = conn.execute(
            "SELECT rowid FROM source_objects WHERE sha=?", (sha,)
        ).fetchone()[0]
        text = data.decode("utf-8", errors="replace")
        for table in source_search_tables(conn):
            conn.execute(f"INSERT INTO {table}(rowid,body) VALUES(?,?)", (rowid, text))
    return sha


def extraction_key(sha, generation, path, context):
    from .code_extract import LANGUAGES

    language = LANGUAGES.get(
        Path(path).suffix.lower(), Path(path).suffix.lower() or "text"
    )
    return digest([sha, generation, language, path, context]), language


def extraction(conn, identity):
    row = conn.execute(
        "SELECT data FROM extraction_objects WHERE identity=?", (identity,)
    ).fetchone()
    return unpacked(row[0]) if row else None


def put_extraction(conn, key, sha, generation, language, path, context, result):
    conn.execute(
        "INSERT OR IGNORE INTO extraction_objects VALUES(?,?,?,?,?,?,?)",
        (key, sha, generation, language, path, context, packed(result)),
    )


class PathMap:
    """Persistent deterministic treap: each update copies only a search path."""

    def __init__(self, conn):
        self.conn = conn
        self.cache = {}

    def node(self, identity):
        if not identity:
            return None
        if identity not in self.cache:
            self.cache[identity] = dict(
                self.conn.execute(
                    "SELECT * FROM snapshot_nodes WHERE identity=?", (identity,)
                ).fetchone()
            )
        return self.cache[identity]

    def make(self, path, artifact, left=None, right=None):
        identity = digest([path, artifact, left, right])
        priority = hashlib.sha256(path.encode()).hexdigest()
        self.conn.execute(
            "INSERT OR IGNORE INTO snapshot_nodes VALUES(?,?,?,?,?,?)",
            (identity, path, artifact, left, right, priority),
        )
        self.cache[identity] = dict(
            identity=identity,
            path=path,
            artifact=artifact,
            left_node=left,
            right_node=right,
            priority=priority,
        )
        return identity

    def merge(self, left, right):
        if not left:
            return right
        if not right:
            return left
        a, b = self.node(left), self.node(right)
        if a["priority"] < b["priority"]:
            return self.make(
                a["path"],
                a["artifact"],
                a["left_node"],
                self.merge(a["right_node"], right),
            )
        return self.make(
            b["path"], b["artifact"], self.merge(left, b["left_node"]), b["right_node"]
        )

    def set(self, root, path, artifact):
        if not root:
            return self.make(path, artifact) if artifact else None
        node = self.node(root)
        if path == node["path"]:
            return (
                self.make(path, artifact, node["left_node"], node["right_node"])
                if artifact
                else self.merge(node["left_node"], node["right_node"])
            )
        side = "left_node" if path < node["path"] else "right_node"
        child = self.set(node[side], path, artifact)
        left = child if side == "left_node" else node["left_node"]
        right = child if side == "right_node" else node["right_node"]
        if child and self.node(child)["priority"] < node["priority"]:
            c = self.node(child)
            if side == "left_node":
                return self.make(
                    c["path"],
                    c["artifact"],
                    c["left_node"],
                    self.make(node["path"], node["artifact"], c["right_node"], right),
                )
            return self.make(
                c["path"],
                c["artifact"],
                self.make(node["path"], node["artifact"], left, c["left_node"]),
                c["right_node"],
            )
        return self.make(node["path"], node["artifact"], left, right)

    def get(self, root, path):
        while root:
            n = self.node(root)
            if path == n["path"]:
                return n["artifact"]
            root = n["left_node"] if path < n["path"] else n["right_node"]
        return None

    def scoped_items(self, root, prefix):
        if not root:
            return
        n = self.node(root)
        lower = prefix.rstrip("/") + "/"
        upper = lower + chr(0x10FFFF)
        if n["path"] >= lower:
            yield from self.scoped_items(n["left_node"], prefix)
        if n["path"] == prefix or lower <= n["path"] < upper:
            yield n["path"], n["artifact"]
        if n["path"] < upper:
            yield from self.scoped_items(n["right_node"], prefix)

    def items(self, root):
        if root:
            n = self.node(root)
            yield from self.items(n["left_node"])
            yield n["path"], n["artifact"]
            yield from self.items(n["right_node"])


def capture_path(conn, repo, path, *, mode=None):
    file = conn.execute(
        "SELECT * FROM main.files WHERE repo=? AND path=?", (repo, path)
    ).fetchone()
    exclusion = conn.execute(
        "SELECT * FROM main.exclusions WHERE repo=? AND path=?", (repo, path)
    ).fetchone()
    value = {"mode": mode}
    for table, key in [
        ("symbols", "path"),
        ("refs", "path"),
        ("edges", "src_path"),
        ("projects", "path"),
        ("project_edges", "src_path"),
        ("contracts", "path"),
    ]:
        value[table] = [
            dict(r)
            for r in conn.execute(
                f"SELECT * FROM main.{table} WHERE repo=? AND {key}=? ORDER BY rowid",
                (repo, path),
            )
        ]
    sha = file["sha256"] if file else exclusion["sha256"] if exclusion else None
    if file:
        from .code_store import span_sha

        for row in value["symbols"]:
            if (
                row["lane"] == "imported"
                and row.get("anchor_sha")
                and span_sha(file["body"], row["line"], row["end_line"])
                != row["anchor_sha"]
            ):
                row["stale"] = 1
        for row in value["edges"]:
            if (
                row["lane"] == "imported"
                and row.get("anchor_sha")
                and row["anchor_sha"] != sha
            ):
                row["stale"] = 1
    if file:
        value["files"] = [
            {k: file[k] for k in file.keys() if k not in {"body", "id", "mtime_ns"}}
        ]
        if hashlib.sha256(file["body"].encode()).hexdigest() == sha:
            source(conn, file["body"].encode())
    else:
        value["files"] = []
    value["exclusions"] = [dict(exclusion)] if exclusion else []
    if (
        not file
        and not exclusion
        and not any(
            value[t] for t in ("symbols", "refs", "edges", "projects", "contracts")
        )
    ):
        return None
    return put_graph(conn, value, sha)


def publish(
    conn,
    repo,
    repository,
    head,
    generation,
    updates,
    *,
    complete=True,
    parent=None,
    activate_checkout=True,
):
    if parent is None and activate_checkout:
        row = conn.execute(
            "SELECT snapshot FROM checkout_heads WHERE root=?", (str(repository),)
        ).fetchone()
        parent = row[0] if row else None
    old = (
        conn.execute(
            "SELECT * FROM code_snapshots WHERE identity=?", (parent,)
        ).fetchone()
        if parent
        else None
    )
    tree = PathMap(conn)
    map_root = old["map_root"] if old else None
    files_count = old["files_count"] if old else 0
    symbols_count = old["symbols_count"] if old else 0
    for path, artifact in sorted(updates.items()):
        previous = tree.get(map_root, path)
        for object_id, direction in ((previous, -1), (artifact, 1)):
            if object_id:
                counts = conn.execute(
                    "SELECT files,symbols FROM snapshot_object_counts WHERE artifact=?",
                    (object_id,),
                ).fetchone()
                files_count += direction * counts["files"]
                symbols_count += direction * counts["symbols"]
        map_root = tree.set(map_root, path, artifact)
    # Overlay identity is a Merkle source+mode+lane map, so distinct dirty trees
    # never alias even when HEAD is identical or a scoped refresh is incomplete.
    overlay = map_root or digest([])
    identity = digest([repo, head, overlay, generation, bool(complete)])
    conn.execute(
        "INSERT OR IGNORE INTO code_snapshots(identity,repo,head,overlay,generation,map_root,parent,complete,created_at,files_count,symbols_count) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            identity,
            repo,
            head,
            overlay,
            generation,
            map_root,
            parent,
            int(complete),
            time.time(),
            files_count,
            symbols_count,
        ),
    )
    if activate_checkout:
        conn.execute(
            "INSERT OR REPLACE INTO checkout_heads VALUES(?,?,?)",
            (str(repository), repo, identity),
        )
        conn.execute(
            "INSERT OR REPLACE INTO snapshot_refs VALUES(?,?,?)",
            (repo, "latest", identity),
        )
    return identity


def resolve(conn, at, repo=None):
    if repo is None:
        repos = [r[0] for r in conn.execute("SELECT repo FROM main.repos")]
        if len(repos) != 1:
            raise ValueError("--at requires exactly one namespace; pass --namespace")
        repo = repos[0]
    row = conn.execute(
        "SELECT * FROM code_snapshots WHERE repo=? AND identity=?", (repo, at)
    ).fetchone()
    if row:
        return dict(row)
    registered = conn.execute(
        "SELECT root FROM main.repos WHERE repo=?", (repo,)
    ).fetchone()
    from .code_store import git

    head = (
        git(registered[0], "rev-parse", "--verify", at + "^{commit}")
        if registered and registered[0]
        else None
    )
    lookup = "commit:" + head if head else at
    if at == "latest":
        lookup = at
    ref = conn.execute(
        "SELECT snapshot FROM snapshot_refs WHERE repo=? AND ref=?", (repo, lookup)
    ).fetchone()
    if ref:
        return dict(
            conn.execute(
                "SELECT * FROM code_snapshots WHERE identity=?", (ref[0],)
            ).fetchone()
        )
    raise ValueError(
        "revision is not indexed; capture the explicit Git revision or pass its snapshot ID"
    )


def activate_query(conn, at, repo=None):
    """An explicit revision may populate the shared immutable cache on demand."""
    try:
        selected = resolve(conn, at, repo)
    except ValueError as exc:
        if not str(exc).startswith("revision is not indexed"):
            raise
        if repo is None:
            repos = [r[0] for r in conn.execute("SELECT repo FROM main.repos")]
            if len(repos) != 1:
                raise ValueError(
                    "--at requires exactly one namespace; pass --namespace"
                )
            repo = repos[0]
        registered = conn.execute(
            "SELECT root FROM main.repos WHERE repo=?", (repo,)
        ).fetchone()
        if not registered or not registered[0]:
            raise ValueError("revision repository is not registered")
        from .code_build import index_revision

        database = Path(conn.execute("PRAGMA database_list").fetchone()[2])
        captured = index_revision(database.parents[2], registered[0], at)
        selected = resolve(conn, captured["snapshot"], repo)
    return activate(conn, selected["identity"], selected["repo"])


def activate(conn, at, repo=None, *, historical=True, paths=None):
    """Install graph views over immutable objects without copying graph rows."""
    snapshot = resolve(conn, at, repo)
    conn.snapshot = snapshot
    conn.snapshot_maps = {snapshot["repo"]: snapshot}
    conn.snapshot_scopes = paths
    conn.membership_cache = {}
    if getattr(conn, "graph_cache", None) is None:
        conn.graph_cache = {}
    if getattr(conn, "body_cache", None) is None:
        conn.body_cache = {}
    if getattr(conn, "path_tree", None) is None:
        conn.path_tree = PathMap(conn)
    conn.historical = historical
    conn.source_cache = {}
    conn.symbol_cache = {}
    conn.create_function(
        "hx_unpack",
        1,
        lambda value: zlib.decompress(value).decode(),
        deterministic=True,
    )
    conn.create_function(
        "hx_source",
        1,
        lambda value: (
            zlib.decompress(value).decode("utf-8", errors="replace") if value else ""
        ),
        deterministic=True,
    )
    conn.execute("CREATE TEMP TABLE IF NOT EXISTS selected_snapshot(map_root TEXT)")
    conn.execute("DELETE FROM selected_snapshot")
    conn.execute("INSERT INTO selected_snapshot VALUES(?)", (snapshot["map_root"],))
    conn.execute("DROP VIEW IF EXISTS temp.snapshot_paths")
    conn.execute("""CREATE TEMP VIEW snapshot_paths AS WITH RECURSIVE nodes(identity) AS (
      SELECT map_root FROM selected_snapshot WHERE map_root IS NOT NULL UNION ALL
      SELECT n.left_node FROM main.snapshot_nodes n JOIN nodes ON n.identity=nodes.identity WHERE n.left_node IS NOT NULL UNION ALL
      SELECT n.right_node FROM main.snapshot_nodes n JOIN nodes ON n.identity=nodes.identity WHERE n.right_node IS NOT NULL)
      SELECT n.path,n.artifact,o.sha,hx_unpack(o.data) AS data FROM nodes JOIN main.snapshot_nodes n USING(identity) JOIN main.graph_objects o ON o.identity=n.artifact""")
    if paths is not None and "." not in paths:
        conn.execute(
            "CREATE TEMP TABLE IF NOT EXISTS selected_path_objects(path TEXT PRIMARY KEY,artifact TEXT)"
        )
        conn.execute("DELETE FROM selected_path_objects")
        tree = PathMap(conn)
        chosen = {}
        for path in paths:
            path = str(path).rstrip("/")
            artifact = tree.get(snapshot["map_root"], path)
            if artifact:
                chosen[path] = artifact
            chosen.update(tree.scoped_items(snapshot["map_root"], path))
        conn.executemany(
            "INSERT INTO selected_path_objects VALUES(?,?)", sorted(chosen.items())
        )
        conn.execute("DROP VIEW temp.snapshot_paths")
        conn.execute(
            "CREATE TEMP VIEW snapshot_paths AS SELECT p.path,p.artifact,o.sha,hx_unpack(o.data) AS data FROM selected_path_objects p JOIN main.graph_objects o ON o.identity=p.artifact"
        )
    for table in (
        "files",
        "symbols",
        "refs",
        "edges",
        "projects",
        "project_edges",
        "exclusions",
        "contracts",
    ):
        columns = conn.execute(f"PRAGMA main.table_info({table})").fetchall()
        fields = []
        for column in columns:
            name = column["name"]
            if table == "files" and name == "body":
                expr = (
                    "hx_source((SELECT data FROM main.source_objects WHERE sha=p.sha))"
                )
            elif table == "files" and name == "id":
                expr = "p.artifact"
            elif table == "files" and name == "mtime_ns":
                expr = "0"
            else:
                expr = f"json_extract(j.value,'$.{name}')"
            fields.append(f'{expr} AS "{name}"')
        conn.execute(f"DROP VIEW IF EXISTS temp.{table}")
        conn.execute(
            f"CREATE TEMP VIEW {table} AS SELECT {','.join(fields)} FROM snapshot_paths p,json_each(p.data,'$.{table}') j"
        )
    return snapshot


def capture_external(conn, repo, repository):
    """Publish import/resolution writes as a new immutable overlay."""
    row = conn.execute(
        "SELECT * FROM code_snapshots WHERE identity=(SELECT snapshot FROM snapshot_refs WHERE repo=? AND ref='latest')",
        (repo,),
    ).fetchone()
    paths = {
        r[0]
        for r in conn.execute(
            "SELECT path FROM main.files WHERE repo=? UNION SELECT path FROM main.symbols WHERE repo=? UNION SELECT src_path FROM main.edges WHERE repo=?",
            (repo, repo, repo),
        )
    }
    tree = PathMap(conn)
    if row:
        paths.update(p for p, _ in tree.items(row["map_root"]))
    updates = {}
    for path in paths:
        artifact = tree.get(row["map_root"], path) if row else None
        previous = (
            conn.execute(
                "SELECT data FROM graph_objects WHERE identity=?", (artifact,)
            ).fetchone()
            if artifact
            else None
        )
        mode = unpacked(previous[0]).get("mode") if previous else None
        updates[path] = capture_path(conn, repo, path, mode=mode)
    return publish(
        conn,
        repo,
        repository or "",
        row["head"] if row else None,
        row["generation"] if row else "",
        updates,
        parent=row["identity"] if row else None,
        complete=bool(row and row["complete"]),
    )


def activate_latest(conn, repo=None):
    """Read each namespace's current immutable map with live source proof."""
    rows = conn.execute(
        "SELECT s.* FROM code_snapshots s JOIN snapshot_refs r ON r.snapshot=s.identity WHERE r.ref='latest' AND (? IS NULL OR s.repo=?) ORDER BY s.repo",
        (repo, repo),
    ).fetchall()
    if not rows:
        return None
    selected = activate(conn, rows[0]["identity"], rows[0]["repo"], historical=False)
    for row in rows[1:]:
        conn.snapshot_maps[row["repo"]] = dict(row)
        conn.execute("INSERT INTO selected_snapshot VALUES(?)", (row["map_root"],))
    if len(rows) > 1:
        conn.snapshot = dict(
            identity=None, head=None, complete=all(r["complete"] for r in rows)
        )
    return selected


def source_search_tables(conn):
    return [
        r[0]
        for r in conn.execute(
            "SELECT name FROM main.sqlite_master WHERE name IN ('source_search','source_trigram') ORDER BY name"
        )
    ]


def artifact_for(conn, repo, path):
    selected = conn.snapshot_maps.get(repo)
    if not selected:
        return None
    scopes = conn.snapshot_scopes
    if scopes is not None and not any(
        p == "." or path == p or path.startswith(str(p).rstrip("/") + "/")
        for p in scopes
    ):
        return None
    key = (repo, path)
    if key not in conn.membership_cache:
        conn.membership_cache[key] = conn.path_tree.get(selected["map_root"], path)
    return conn.membership_cache[key]


def graph_for(conn, repo, path):
    artifact = artifact_for(conn, repo, path)
    if artifact is None:
        return None
    if artifact not in conn.graph_cache:
        row = conn.execute(
            "SELECT data FROM main.graph_objects WHERE identity=?", (artifact,)
        ).fetchone()
        conn.graph_cache[artifact] = unpacked(row[0])
    return conn.graph_cache[artifact]


def body_for(conn, repo, path):
    graph = graph_for(conn, repo, path)
    if not graph or not graph["files"]:
        return None
    sha = graph["files"][0]["sha256"]
    if sha not in conn.body_cache:
        row = conn.execute(
            "SELECT data FROM main.source_objects WHERE sha=?", (sha,)
        ).fetchone()
        conn.body_cache[sha] = (
            zlib.decompress(row[0]).decode("utf-8", errors="replace") if row else ""
        )
    return conn.body_cache[sha]


def matching_facts(
    conn, table, predicate, bindings, *, repo=None, path=None, limit=None
):
    postings = {
        "symbols": "snapshot_symbol_postings",
        "refs": "snapshot_ref_postings",
        "edges": "snapshot_edge_postings",
    }[table]
    order = (
        "CASE lane WHEN 'resolved' THEN 0 WHEN 'syntax' THEN 1 WHEN 'text' THEN 2 ELSE 3 END,repo,path,line,ordinal"
        if table == "symbols"
        else "repo,path,line,lane,ordinal"
        if table == "refs"
        else "repo,path,kind,ordinal"
    )
    rows = conn.execute(
        f"SELECT * FROM main.{postings} WHERE ({predicate}) AND (? IS NULL OR repo=?) AND (? IS NULL OR path=?) ORDER BY {order}",
        (*bindings, repo, repo, path, path),
    )
    yield from selected_facts(conn, table, rows, limit=limit)


def selected_facts(conn, table, rows, *, limit=None):
    count = 0
    for row in rows:
        if artifact_for(conn, row["repo"], row["path"]) != row["artifact"]:
            continue
        graph = graph_for(conn, row["repo"], row["path"])
        yield dict(graph[table][row["ordinal"]])
        count += 1
        if limit is not None and count >= limit:
            return


def matching_files(conn, table, match, *, repo=None, limit=100):
    rows = conn.execute(
        f"SELECT f.* FROM main.{table} JOIN main.source_objects o ON o.rowid={table}.rowid JOIN main.snapshot_file_postings f ON f.sha=o.sha WHERE {table} MATCH ? AND (? IS NULL OR f.repo=?) ORDER BY bm25({table}),f.repo,f.path,f.artifact",
        (match, repo, repo),
    )
    seen = set()
    for row in rows:
        if artifact_for(conn, row["repo"], row["path"]) != row["artifact"]:
            continue
        key = (row["repo"], row["path"])
        if key in seen:
            continue
        seen.add(key)
        yield dict(graph_for(conn, *key)["files"][0])
        if len(seen) >= limit:
            return


def names_for(conn, *, repo=None, limit=5000):
    rows = conn.execute(
        "SELECT repo,path,artifact,name FROM main.snapshot_symbol_postings WHERE (? IS NULL OR repo=?) ORDER BY name,repo,path",
        (repo, repo),
    )
    seen = set()
    for row in rows:
        if row["name"] in seen:
            continue
        if artifact_for(conn, row["repo"], row["path"]) != row["artifact"]:
            continue
        seen.add(row["name"])
        yield row["name"]
        if len(seen) >= limit:
            return


def facts_in_path(conn, table, path, *, repo=None):
    """Retrieve an exact path without walking or decompressing unrelated maps."""
    identities = [repo] if repo is not None else sorted(conn.snapshot_maps)
    for identity in identities:
        graph = graph_for(conn, identity, path)
        if graph:
            for row in graph.get(table, []):
                yield dict(row)


def implementations_for(conn, interface):
    sources = {
        row["src_path"]
        for row in matching_facts(
            conn,
            "edges",
            "kind='implements' AND lane='resolved' AND (dst_folded=? OR dst_folded=?)",
            (interface["name"].casefold(), interface["qualified"].casefold()),
            repo=interface["repo"],
        )
    }
    rows = conn.execute(
        "SELECT p.* FROM snapshot_signature_terms t JOIN snapshot_symbol_postings p ON p.artifact=t.artifact AND p.ordinal=t.ordinal WHERE t.term=? AND p.repo=? ORDER BY p.path,p.line,p.ordinal",
        (interface["name"].casefold(), interface["repo"]),
    )
    selected = list(selected_facts(conn, "symbols", rows))
    seen = {
        (r["qualified"], r["path"], r["line"], r["lane"], r["extractor"])
        for r in selected
    }
    for path in sources:
        for row in facts_in_path(conn, "symbols", path, repo=interface["repo"]):
            key = (
                row["qualified"],
                row["path"],
                row["line"],
                row["lane"],
                row["extractor"],
            )
            if row["kind"] in {"class", "struct", "impl"} and key not in seen:
                selected.append(row)
                seen.add(key)
    return sorted(selected, key=lambda r: (r["path"], r["line"]))


def test_candidates(conn, names, references, *, repo=None):
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS selected_test_names(name TEXT PRIMARY KEY)"
    )
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS selected_test_references(repo TEXT,path TEXT,PRIMARY KEY(repo,path))"
    )
    conn.execute("DELETE FROM selected_test_names")
    conn.execute("DELETE FROM selected_test_references")
    conn.executemany(
        "INSERT INTO selected_test_names VALUES(?)", [(n,) for n in sorted(names)]
    )
    conn.executemany(
        "INSERT INTO selected_test_references VALUES(?,?)", sorted(references)
    )
    rows = conn.execute(
        "SELECT p.* FROM snapshot_test_postings t JOIN snapshot_symbol_postings p ON p.artifact=t.artifact AND p.ordinal=t.ordinal WHERE (? IS NULL OR t.repo=?) AND (EXISTS(SELECT 1 FROM selected_test_names n WHERE instr(t.name_folded,n.name)>0) OR EXISTS(SELECT 1 FROM selected_test_references r WHERE r.repo=t.repo AND r.path=t.path)) ORDER BY p.repo,p.path,p.line",
        (repo, repo),
    )
    return selected_facts(conn, "symbols", rows)


def backfill_counts(conn):
    """Once per schema upgrade, memoize shared subtrees across all old snapshots."""
    tree = PathMap(conn)
    memo = {}

    def total(identity):
        if not identity:
            return (0, 0)
        if identity not in memo:
            node = tree.node(identity)
            leaf = conn.execute(
                "SELECT files,symbols FROM snapshot_object_counts WHERE artifact=?",
                (node["artifact"],),
            ).fetchone()
            left = total(node["left_node"])
            right = total(node["right_node"])
            memo[identity] = (
                leaf["files"] + left[0] + right[0],
                leaf["symbols"] + left[1] + right[1],
            )
        return memo[identity]

    for row in conn.execute("SELECT identity,map_root FROM code_snapshots").fetchall():
        conn.execute(
            "UPDATE code_snapshots SET files_count=?,symbols_count=? WHERE identity=?",
            (*total(row["map_root"]), row["identity"]),
        )


def path_info(conn, repo, path):
    artifact = artifact_for(conn, repo, path)
    if not artifact:
        return None
    return conn.execute(
        "SELECT * FROM snapshot_path_postings WHERE artifact=?", (artifact,)
    ).fetchone()


def paths_with_stem(conn, repo, stem, *, folded=False):
    column = "stem_folded" if folded else "stem"
    rows = conn.execute(
        f"SELECT path,artifact,suffix FROM snapshot_path_postings WHERE repo=? AND {column}=? ORDER BY path",
        (repo, stem),
    )
    return [
        dict(row)
        for row in rows
        if artifact_for(conn, repo, row["path"]) == row["artifact"]
    ]


def metadata_facts(conn, table, name=None, *, repo=None, limit=100):
    if table == "contracts":
        rows = conn.execute(
            "SELECT * FROM snapshot_contract_postings WHERE (? IS NULL OR name_folded=?) AND (? IS NULL OR repo=?) ORDER BY repo,name,path,version",
            (name, name.casefold() if name else None, repo, repo),
        )
    else:
        rows = conn.execute(
            "SELECT * FROM snapshot_project_postings WHERE (? IS NULL OR name LIKE ? OR path LIKE ?) AND (? IS NULL OR repo=?) ORDER BY repo,path",
            (
                name,
                "%" + name + "%" if name else None,
                "%" + name + "%" if name else None,
                repo,
                repo,
            ),
        )
    return selected_facts(conn, table, rows, limit=limit)


def project_paths(conn, repo):
    rows = conn.execute(
        "SELECT path,artifact FROM snapshot_project_postings WHERE repo=? ORDER BY path",
        (repo,),
    )
    seen = set()
    for row in rows:
        if (
            row["path"] not in seen
            and artifact_for(conn, repo, row["path"]) == row["artifact"]
        ):
            seen.add(row["path"])
            yield row["path"]
