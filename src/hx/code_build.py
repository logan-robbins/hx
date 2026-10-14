"""Incremental repository indexing and syntax-lane persistence."""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from . import code_extract, code_projects
from . import code_store as store

MAX_FILE_BYTES = 512 * 1024
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", "build"}


def discover(repo, paths=None):
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
            *(["--", *paths] if paths is not None else []),
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
    return [
        n
        for n in names
        if not any(p in SKIP_DIRS for p in Path(n).parts)
        and (
            paths is None
            or any(
                n == p or n.startswith(p.rstrip("/") + "/") or p == "." for p in paths
            )
        )
    ]


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


def scoped_names(repo, paths):
    """Exact files are inspected directly; directories use bounded pathspecs."""
    scopes = [str(p).rstrip("/") or "." for p in paths]
    if any(Path(p).is_absolute() or ".." in Path(p).parts for p in scopes):
        raise ValueError("index paths must be repository relative")
    names = set()
    directories = []
    for scope in scopes:
        target = repo / scope
        if scope == "." or target.is_dir():
            directories.append(scope)
        elif (
            target.is_file() or target.is_symlink()
        ) and target.parent.resolve().is_relative_to(repo):
            ignored = subprocess.run(
                ["git", "-C", str(repo), "check-ignore", "--quiet", "--", scope],
                capture_output=True,
                timeout=30,
            )
            if ignored.returncode != 0:
                names.add(scope)
    if directories:
        names.update(discover(repo, directories))
    return sorted(n for n in names if not any(p in SKIP_DIRS for p in Path(n).parts))


def git_changes(repo, base, head):
    names = set()
    if base and head and base != head:
        result = subprocess.run(
            ["git", "-C", str(repo), "diff", "--name-only", "-z", base, head, "--"],
            capture_output=True,
            timeout=60,
        )
        if result.returncode:
            return None
        names.update(os.fsdecode(p) for p in result.stdout.split(b"\0") if p)
    result = subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
        ],
        capture_output=True,
        timeout=60,
    )
    if result.returncode:
        return None
    records = iter(result.stdout.split(b"\0"))
    for row in records:
        if not row:
            continue
        names.add(os.fsdecode(row[3:]))
        if row[:1] in {b"R", b"C"} or row[1:2] in {b"R", b"C"}:
            previous = next(records, b"")
            if previous:
                names.add(os.fsdecode(previous))
    return names


def _context(repo):
    """Build configuration participates in extraction identity, independently of HEAD."""
    from . import code_snapshot

    names = (
        "pyproject.toml",
        "tsconfig.json",
        "package.json",
        "Cargo.toml",
        "go.mod",
        "Directory.Build.props",
        "Directory.Build.targets",
        ".hx/code-context.json",
    )
    values = []
    for name in names:
        try:
            values.append(
                (name, hashlib.sha256((repo / name).read_bytes()).hexdigest())
            )
        except OSError:
            pass
    return code_snapshot.digest(values)


def extractor_generation(detected, context):
    from . import code_adapters

    grammar_bytes = b""
    for grammar in detected.get("tree-sitter", {}).get("grammars", []):
        for relative in ("src/parser.c", "src/grammar.json", "queries/tags.scm"):
            try:
                grammar_bytes += (Path(grammar) / relative).read_bytes()
            except OSError:
                pass
    generation = hashlib.sha256(
        grammar_bytes
        + Path(code_extract.__file__).read_bytes()
        + Path(code_extract.code_natural.__file__).read_bytes()
        + Path(code_adapters.__file__).read_bytes()
        + Path(code_projects.__file__).read_bytes()
        + json.dumps(detected, sort_keys=True).encode()
    ).hexdigest()
    generation = hashlib.sha256((generation + context).encode()).hexdigest()
    return generation


def apply_changeset(
    conn, journal_id, *, detected=None, resolve=False, indexer_checkout=None
):
    """Apply durable prepared objects atomically; retries never duplicate graph rows."""
    from . import code_snapshot as snapshots
    from .code_invalidation import dependencies, invalidate

    journal = conn.execute(
        "SELECT * FROM code_changesets WHERE identity=?", (journal_id,)
    ).fetchone()
    if not journal:
        raise ValueError("changeset does not exist")
    if journal["state"] == "applied":
        return dict(
            repo=journal["repo"],
            snapshot=journal["snapshot"],
            journal=journal_id,
            changed=0,
            replayed=True,
            resolved=snapshots.unpacked(journal["resolved"])
            if journal["resolved"]
            else {"status": "not-requested"},
        )
    if journal["state"] == "superseded":
        raise ValueError("changeset was superseded by a concurrent refresh")
    identity = journal["repo"]
    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM code_changes WHERE identity=? ORDER BY path", (journal_id,)
        )
    ]
    # Dependency discovery and all parser work precede the writer transaction.
    snapshot_base = journal["snapshot_base"] or journal["base"]
    prepared = {
        r["extraction"]: snapshots.extraction(conn, r["extraction"])
        for r in rows
        if r["extraction"]
    }
    sources = {
        r["sha"]: bytes(
            conn.execute(
                "SELECT data FROM source_objects WHERE sha=?", (r["sha"],)
            ).fetchone()[0]
        )
        for r in rows
        if r["operation"] == "upsert"
    }
    import zlib

    sources = {
        sha: zlib.decompress(data).decode("utf-8", errors="replace")
        for sha, data in sources.items()
    }
    resolved_prepared = (
        snapshots.unpacked(journal["resolved"]) if journal["resolved"] else None
    )
    from collections import defaultdict

    reverse = defaultdict(set)
    if rows and snapshot_base:
        import sqlite3

        read = sqlite3.connect(
            conn.execute("PRAGMA database_list").fetchone()[2],
            factory=store.GraphConnection,
        )
        read.row_factory = sqlite3.Row
        read.source_cache = {}
        read.symbol_cache = {}
        try:
            snapshots.activate(read, snapshot_base, identity)
            new_symbols = {
                r["path"]: prepared[r["extraction"]]["symbols"]
                for r in rows
                if r["extraction"]
            }
            reverse = dependencies(
                read,
                identity,
                changed={r["path"] for r in rows},
                new_symbols=new_symbols,
                added={r["path"] for r in rows if r["operation"] != "delete"},
                deleted={r["path"] for r in rows if r["operation"] == "delete"},
            )
        finally:
            read.close()
    elif rows:
        reverse = dependencies(conn, identity)
    conn.execute("BEGIN IMMEDIATE")
    try:
        applied = conn.execute(
            "SELECT state,snapshot FROM code_changesets WHERE identity=?", (journal_id,)
        ).fetchone()
        if applied["state"] == "applied":
            conn.commit()
            return dict(
                repo=identity,
                snapshot=applied["snapshot"],
                journal=journal_id,
                changed=0,
                replayed=True,
            )
        current = conn.execute(
            "SELECT snapshot FROM snapshot_refs WHERE repo=? AND ref='latest'",
            (identity,),
        ).fetchone()
        if (current[0] if current else None) != journal["base"]:
            conn.execute(
                "UPDATE code_changesets SET state='superseded' WHERE identity=?",
                (journal_id,),
            )
            conn.commit()
            raise ValueError("concurrent graph refresh; retry indexing")
        generation_key = "extractors:" + identity
        previous = conn.execute(
            "SELECT value FROM metadata WHERE key=?", (generation_key,)
        ).fetchone()
        generation_changed = bool(previous and previous[0] != journal["generation"])
        affected = set()
        parent_generation = (
            conn.execute(
                "SELECT generation,map_root FROM code_snapshots WHERE identity=?",
                (snapshot_base,),
            ).fetchone()
            if snapshot_base
            else None
        )
        generation_changed = bool(
            parent_generation
            and parent_generation["generation"] != journal["generation"]
        )
        if generation_changed:
            affected.update(
                p
                for p, _ in snapshots.PathMap(conn).items(parent_generation["map_root"])
            )
            for table, key in [
                ("symbols", "path"),
                ("refs", "path"),
                ("edges", "src_path"),
            ]:
                conn.execute(
                    f"UPDATE {table} SET stale=1 WHERE repo=? AND lane<>'imported'",
                    (identity,),
                )
        refreshed = set()
        changed_paths = set()
        modes = {}
        for row in rows:
            rel = row["path"]
            modes[rel] = row["mode"]
            changed_paths.add(rel)
            store.delete_file(conn, identity, rel)
            if row["operation"] == "delete":
                continue
            refreshed.add(rel)
            if row["operation"] == "exclude":
                conn.execute(
                    "INSERT OR REPLACE INTO exclusions VALUES(?,?,?,?,?)",
                    (identity, rel, row["sha"], row["size"], row["reason"]),
                )
                continue
            result = prepared[row["extraction"]]
            text = sources[row["sha"]]
            conn.execute(
                "INSERT INTO files(repo,path,sha256,size,mtime_ns,symbols,body,generation) VALUES(?,?,?,?,?,?,?,?)",
                (
                    identity,
                    rel,
                    row["sha"],
                    row["size"],
                    row["mtime_ns"],
                    " ".join(s["qualified"] for s in result["symbols"]),
                    text,
                    journal["generation"],
                ),
            )
            file = conn.execute(
                "SELECT id,symbols FROM files WHERE repo=? AND path=?", (identity, rel)
            ).fetchone()
            for table in store.fts_tables(conn):
                conn.execute(
                    f"INSERT INTO {table}(rowid,repo,path,symbols,body) VALUES(?,?,?,?,?)",
                    (file["id"], identity, rel, file["symbols"], text),
                )
            for symbol in result["symbols"]:
                insert_symbol(conn, identity, symbol)
            for ref in result["refs"]:
                insert_ref(
                    conn,
                    identity,
                    ref,
                    extractor=ref.get(
                        "extractor",
                        "stdlib-"
                        + code_extract.LANGUAGES.get(Path(rel).suffix.lower(), "text"),
                    ),
                    anchor=row["sha"],
                )
            for src, dst, kind in result["edges"]:
                insert_edge(
                    conn,
                    identity,
                    src,
                    dst,
                    kind,
                    anchor=row["sha"],
                    extractor="stdlib-natural"
                    if Path(rel).suffix.lower()
                    in code_extract.code_natural.OBJECT_KINDS
                    else "stdlib",
                )
            project = result["project"]
            if project:
                conn.execute(
                    "INSERT INTO projects VALUES(?,?,?,?)",
                    (identity, project["name"], rel, json.dumps(project["data"])),
                )
                for dst in result["dependencies"]:
                    conn.execute(
                        "INSERT OR IGNORE INTO project_edges VALUES(?,?,?,?)",
                        (identity, rel, dst, "project_depends_on"),
                    )
        invalidate(conn, identity, reverse, changed_paths, refreshed)
        # The changed dependency rows must be captured too, while their source is shared.
        pending = list(changed_paths)
        affected.update(changed_paths)
        while pending:
            for path in reverse[pending.pop()]:
                if path not in affected:
                    affected.add(path)
                    pending.append(path)
        parent = conn.execute(
            "SELECT map_root FROM code_snapshots WHERE identity=?", (snapshot_base,)
        ).fetchone()
        tree = snapshots.PathMap(conn)
        updates = {}
        for path in sorted(affected):
            mode = modes.get(path)
            if mode is None and parent:
                artifact = tree.get(parent[0], path)
                if artifact:
                    old = conn.execute(
                        "SELECT data FROM graph_objects WHERE identity=?", (artifact,)
                    ).fetchone()
                    mode = snapshots.unpacked(old[0]).get("mode")
            if path not in changed_paths and parent:
                artifact = tree.get(parent[0], path)
                old = (
                    conn.execute(
                        "SELECT sha,data FROM graph_objects WHERE identity=?",
                        (artifact,),
                    ).fetchone()
                    if artifact
                    else None
                )
                if old:
                    value = snapshots.unpacked(old["data"])
                    for table in ("symbols", "refs", "edges"):
                        for row in value[table]:
                            if row["lane"] != "imported":
                                row["stale"] = 1
                    artifact = snapshots.put_graph(conn, value, old["sha"])
                    updates[path] = artifact
            else:
                updates[path] = snapshots.capture_path(conn, identity, path, mode=mode)
        resolved = {"status": "not-requested"}
        if resolved_prepared:
            from .code_scip import ingest

            resolved = dict(resolved_prepared)
            results = []
            for result in resolved_prepared.get("results", []):
                results.append(
                    ingest(
                        conn,
                        identity,
                        result["data"],
                        extractor=result["extractor"],
                        snapshot=False,
                    )
                    if result.get("status") == "prepared"
                    else result
                )
            if "results" in resolved:
                resolved["results"] = results
            for path in [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT path FROM symbols WHERE repo=? AND lane='resolved'",
                    (identity,),
                )
            ]:
                updates[path] = snapshots.capture_path(
                    conn, identity, path, mode=modes.get(path)
                )
        snapshot = snapshots.publish(
            conn,
            identity,
            journal["root"],
            journal["head"],
            journal["generation"],
            updates,
            parent=snapshot_base,
            complete=bool(journal["complete"]),
        )
        conn.execute(
            "INSERT INTO repos VALUES(?,?,?,?,?) ON CONFLICT(repo) DO UPDATE SET root=excluded.root,head=excluded.head,dirty=excluded.dirty,indexed_at=excluded.indexed_at",
            (identity, journal["root"], journal["head"], journal["dirty"], time.time()),
        )
        conn.execute(
            "INSERT OR REPLACE INTO metadata VALUES(?,?)",
            (generation_key, journal["generation"]),
        )
        if detected is not None:
            conn.execute(
                "INSERT OR REPLACE INTO metadata VALUES('extractors',?)",
                (json.dumps(detected),),
            )
        conn.execute(
            "UPDATE code_changesets SET state='applied',snapshot=?,resolved=? WHERE identity=?",
            (snapshot, snapshots.packed(resolved), journal_id),
        )
        conn.commit()
        return dict(
            repo=identity,
            snapshot=snapshot,
            journal=journal_id,
            changed=len(rows),
            resolved=resolved,
        )
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise


def _index_repo(
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
    from . import code_adapters, code_journal
    from . import code_snapshot as snapshots

    identity = store.namespace(repo)
    detected = code_adapters.detect()
    context = _context(repo)
    generation = extractor_generation(detected, context)
    conn = store.connect(root, trigram=trigram)
    try:
        current = conn.execute(
            "SELECT * FROM code_snapshots WHERE identity=(SELECT snapshot FROM snapshot_refs WHERE repo=? AND ref='latest')",
            (identity,),
        ).fetchone()
        base = current["identity"] if current else None
        checkout = conn.execute(
            "SELECT * FROM code_snapshots WHERE identity=(SELECT snapshot FROM checkout_heads WHERE root=? AND repo=?)",
            (str(repo), identity),
        ).fetchone()
        snapshot_base = checkout["identity"] if paths is not None and checkout else base
        scopes = (
            [str(p).rstrip("/") or "." for p in paths] if paths is not None else None
        )
        if scopes is not None and any(
            Path(p).is_absolute() or ".." in Path(p).parts for p in scopes
        ):
            raise ValueError("index paths must be repository relative")

        def scope_sql(column="path"):
            if scopes is None or "." in scopes:
                return "1", ()
            clauses = []
            parameters = []
            for scope in scopes:
                clauses.append(f"({column}=? OR ({column}>=? AND {column}<?))")
                parameters.extend((scope, scope + "/", scope + "/" + chr(0x10FFFF)))
            return " OR ".join(clauses) or "0", tuple(parameters)

        path_filter, path_bindings = scope_sql()
        edge_filter, edge_bindings = scope_sql("src_path")
        existing = {
            r["path"]: dict(r)
            for r in conn.execute(
                "SELECT path,sha256,generation FROM files WHERE repo=? AND ("
                + path_filter
                + ")",
                (identity, *path_bindings),
            )
        }
        excluded = {
            r["path"]: dict(r)
            for r in conn.execute(
                "SELECT * FROM exclusions WHERE repo=? AND (" + path_filter + ")",
                (identity, *path_bindings),
            )
        }
        stale = {
            r[0]
            for r in conn.execute(
                "SELECT path FROM symbols WHERE repo=? AND stale=1 AND ("
                + path_filter
                + ") UNION SELECT path FROM refs WHERE repo=? AND stale=1 AND ("
                + path_filter
                + ") UNION SELECT src_path FROM edges WHERE repo=? AND stale=1 AND ("
                + edge_filter
                + ")",
                (
                    identity,
                    *path_bindings,
                    identity,
                    *path_bindings,
                    identity,
                    *edge_bindings,
                ),
            )
        }
        head = store.git(repo, "rev-parse", "HEAD")
        if paths is not None:
            scopes = [str(p).rstrip("/") or "." for p in paths]
            if checkout and checkout["identity"] != base:
                branch = store.connect(root, existing=True)
                try:
                    snapshots.activate(
                        branch, checkout["identity"], identity, paths=scopes
                    )
                    for row in branch.execute(
                        "SELECT path,sha256,generation FROM files"
                    ):
                        existing.setdefault(row["path"], dict(row))
                    for row in branch.execute("SELECT * FROM exclusions"):
                        excluded.setdefault(row["path"], dict(row))
                finally:
                    branch.close()
            names = set(scoped_names(repo, scopes))
            old = {
                p
                for p in set(existing) | set(excluded)
                if any(p == s or p.startswith(s + "/") or s == "." for s in scopes)
            }
            names.update(old)
        elif current and current["complete"] and head and current["head"]:
            delta = git_changes(repo, current["head"], head)
            names = (
                set(discover(repo)) | set(existing) | set(excluded)
                if delta is None
                else delta | stale
            )
            if current["generation"] != generation:
                names.update(existing)
            # A coverage budget increase must reconsider previously excluded bodies.
            names.update(
                p
                for p, r in excluded.items()
                if r["reason"] == "oversize" and r["size"] <= maximum
            )
        else:
            names = set(discover(repo)) | set(existing) | set(excluded)
        changes = []
        objects = []
        extractions = []
        skipped = 0
        cached = 0
        prepared_symbols = {}

        def prepare_path(rel, *, force=False):
            nonlocal skipped, cached
            path = repo / rel
            if any(p in SKIP_DIRS for p in Path(rel).parts):
                return
            if not (
                path.is_file() or path.is_symlink()
            ) or not path.parent.resolve().is_relative_to(repo):
                if rel in existing or rel in excluded:
                    changes.append(dict(path=rel, operation="delete"))
                return
            before = path.lstat()
            if not path.is_symlink() and before.st_size > maximum:
                from .code_exclusions import fingerprint

                sha = fingerprint(path)
                data = None
            else:
                data = store.source_bytes(path)
                sha = hashlib.sha256(data).hexdigest()
            after = path.lstat()
            if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                after.st_size,
                after.st_mtime_ns,
                after.st_ctime_ns,
            ):
                raise ValueError("source changed while preparing index")
            mode = (
                "120000"
                if path.is_symlink()
                else "100755"
                if before.st_mode & 0o111
                else "100644"
            )
            old_mode = None
            if current:
                artifact = snapshots.PathMap(conn).get(current["map_root"], rel)
                if artifact:
                    old_mode = snapshots.unpacked(
                        conn.execute(
                            "SELECT data FROM graph_objects WHERE identity=?",
                            (artifact,),
                        ).fetchone()[0]
                    ).get("mode")
            reason = "oversize" if data is None else "binary" if b"\0" in data else None
            if reason:
                skipped += 1
                if (
                    rel in excluded
                    and excluded[rel]["sha256"] == sha
                    and excluded[rel]["reason"] == reason
                    and old_mode == mode
                ):
                    return
                changes.append(
                    dict(
                        path=rel,
                        operation="exclude",
                        sha=sha,
                        mode=mode,
                        size=before.st_size,
                        mtime_ns=before.st_mtime_ns,
                        reason=reason,
                    )
                )
                # Excluded source bodies retain only content identity and coverage facts.
                return
            if (
                not force
                and rel in existing
                and existing[rel]["sha256"] == sha
                and existing[rel]["generation"] == generation
                and rel not in stale
                and old_mode == mode
            ):
                return
            key, language = snapshots.extraction_key(sha, generation, rel, context)
            result = snapshots.extraction(conn, key)
            if result is None:
                text = data.decode("utf-8", errors="replace")
                symbols, refs, edges = code_extract.extract(text, rel)
                symbols, refs = code_adapters.syntax(
                    repo, rel, text, symbols, refs, detected
                )
                project, deps = code_projects.extract(text, rel)
                result = dict(
                    symbols=symbols,
                    refs=refs,
                    edges=edges,
                    project=project,
                    dependencies=deps,
                )
                extractions.append(
                    (key, sha, generation, language, rel, context, result)
                )
            else:
                cached += 1
            prepared_symbols[rel] = result["symbols"]
            objects.append(data)
            changes.append(
                dict(
                    path=rel,
                    operation="upsert",
                    sha=sha,
                    mode=mode,
                    extraction=key,
                    size=len(data),
                    mtime_ns=before.st_mtime_ns,
                )
            )

        for rel in sorted(names):
            prepare_path(rel)
        if paths is None and changes and snapshot_base:
            # Full refresh also rebinds existing dependent facts. Scoped refresh
            # intentionally leaves untouched dependents stale for their owner.
            # Cached extraction reuses unchanged source without parser work.
            import sqlite3

            from .code_invalidation import dependencies

            read = sqlite3.connect(
                conn.execute("PRAGMA database_list").fetchone()[2],
                factory=store.GraphConnection,
            )
            read.row_factory = sqlite3.Row
            read.source_cache = {}
            read.symbol_cache = {}
            changed_paths = {r["path"] for r in changes}
            try:
                snapshots.activate(read, snapshot_base, identity)
                reverse = dependencies(
                    read,
                    identity,
                    changed=changed_paths,
                    new_symbols=prepared_symbols,
                    added={r["path"] for r in changes if r["operation"] != "delete"},
                    deleted={r["path"] for r in changes if r["operation"] == "delete"},
                )
                dependents = set().union(*reverse.values()) if reverse else set()
            finally:
                read.close()
            for rel in sorted(dependents - changed_paths):
                prepare_path(rel, force=True)
        resolved_prepared = None
        if resolve:
            from .code_scip import index as prepare_resolved

            resolved_prepared = prepare_resolved(
                conn,
                identity,
                repo,
                indexer_checkout,
                prepare_only=True,
                suffixes={Path(p).suffix for p in set(existing) | names},
            )
        # No writer is held while files are read, adapters run or syntax is parsed.
        for data in objects:
            snapshots.source(conn, data)
        for args in extractions:
            snapshots.put_extraction(conn, *args)
        baseline = checkout or current
        commit_changes = (
            git_changes(repo, baseline["head"], head) if baseline and head else None
        )
        complete = paths is None or bool(
            baseline
            and baseline["complete"]
            and commit_changes is not None
            and commit_changes <= names
        )
        journal = code_journal.prepare(
            conn,
            identity,
            repo,
            base,
            head,
            generation,
            changes,
            complete=complete,
            snapshot_base=snapshot_base,
            dirty=bool(store.git(repo, "status", "--porcelain")),
            resolved=resolved_prepared,
        )
        result = apply_changeset(
            conn,
            journal,
            detected=detected,
            resolve=resolve,
            indexer_checkout=indexer_checkout,
        )
        if paths is None and not store.git(repo, "status", "--porcelain") and head:
            conn.execute(
                "INSERT OR REPLACE INTO snapshot_refs VALUES(?,?,?)",
                (identity, "commit:" + head, result["snapshot"]),
            )
            for ref in ("HEAD", store.git(repo, "symbolic-ref", "--short", "HEAD")):
                if ref:
                    conn.execute(
                        "INSERT OR REPLACE INTO snapshot_refs VALUES(?,?,?)",
                        (identity, ref, result["snapshot"]),
                    )
            conn.commit()
        counts = conn.execute(
            "SELECT files_count,symbols_count FROM code_snapshots WHERE identity=?",
            (result["snapshot"],),
        ).fetchone()
        result.update(
            files=counts["files_count"],
            symbols=counts["symbols_count"],
            skipped=skipped,
            head=head,
            reused_extractions=cached,
        )
        return result
    finally:
        conn.close()


def preparation_lock(root, repo_path):
    """Bounded cross-process preparation stripes; never a ledger writer lock."""
    import fcntl
    from contextlib import contextmanager

    @contextmanager
    def locked():
        repo = Path(repo_path).resolve()
        if not repo.is_dir():
            raise ValueError("repository does not exist")
        if store.db_path(root).resolve().is_relative_to(repo):
            raise ValueError("code index state must be outside the product repository")
        identity = store.namespace(repo)
        stripe = int(hashlib.sha256(identity.encode()).hexdigest()[:8], 16) % 64
        directory = store.db_path(root).parent / "preparation-locks"
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / f"{stripe:02x}.lock").open("a") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    return locked()


def index_repo(root, repo_path, **kwargs):
    """One cold preparation per namespace; concurrent readers stay independent."""
    with preparation_lock(root, repo_path):
        for attempt in range(4):
            try:
                return _index_repo(root, repo_path, **kwargs)
            except ValueError as exc:
                if (
                    str(exc) != "concurrent graph refresh; retry indexing"
                    or attempt == 3
                ):
                    raise


def _index_revision(
    root, repo_path, at, *, max_file_bytes=None, trigram=True, max_revision_bytes=None
):
    """Capture an explicitly requested commit directly from bounded Git objects.

    This never checks out files or changes the live graph projection. If the
    commit's parent was indexed, only its changed source paths are extracted.
    """
    import tempfile

    from . import code_adapters
    from . import code_snapshot as snapshots

    repo = Path(repo_path).resolve()
    if store.db_path(root).resolve().is_relative_to(repo):
        raise ValueError("code index state must be outside the product repository")
    head = store.git(repo, "rev-parse", "--verify", str(at) + "^{commit}")
    if not head:
        raise ValueError("revision is not an available Git commit")
    maximum = (
        max_file_bytes
        if max_file_bytes is not None
        else int(os.environ.get("HX_CODE_MAX_FILE_BYTES", MAX_FILE_BYTES))
    )
    budget = (
        max_revision_bytes
        if max_revision_bytes is not None
        else int(os.environ.get("HX_CODE_REVISION_MAX_BYTES", 64 * 1024 * 1024))
    )
    if min(maximum, budget) < 1:
        raise ValueError("revision and file byte budgets must be positive")
    identity = store.namespace(repo)
    conn = store.connect(root, trigram=trigram)
    try:
        previous = conn.execute(
            "SELECT snapshot FROM snapshot_refs WHERE repo=? AND ref=?",
            (identity, "commit:" + head),
        ).fetchone()
        if previous:
            return dict(
                repo=identity,
                head=head,
                snapshot=previous[0],
                changed=0,
                historical=True,
            )
        parent_heads = store.git(
            repo, "rev-list", "--parents", "-n", "1", head
        ).split()[1:]
        parent = None
        for parent_head in parent_heads:
            row = conn.execute(
                "SELECT s.* FROM code_snapshots s JOIN snapshot_refs r ON r.snapshot=s.identity WHERE r.repo=? AND r.ref=?",
                (identity, "commit:" + parent_head),
            ).fetchone()
            if row:
                parent = row
                break
        raw = subprocess.run(
            ["git", "-C", str(repo), "ls-tree", "-r", "-z", "-l", head],
            capture_output=True,
            timeout=60,
            check=True,
        ).stdout
        if len(raw) > 16 * 1024 * 1024:
            raise ValueError(
                "revision tree metadata exceeds 16 MiB; narrow the repository"
            )
        entries = {}
        for record in raw.split(b"\0"):
            if not record:
                continue
            info, path = record.split(b"\t", 1)
            mode, kind, oid, size = info.decode().split()
            path = os.fsdecode(path)
            if kind != "blob" or any(p in SKIP_DIRS for p in Path(path).parts):
                continue
            entries[path] = (mode, oid, int(size))
        path_budget = int(os.environ.get("HX_CODE_REVISION_MAX_PATHS", "100000"))
        if len(entries) > path_budget:
            raise ValueError(
                f"revision exceeds explicit {path_budget}-path budget; raise HX_CODE_REVISION_MAX_PATHS"
            )
        if sum(size for _, _, size in entries.values()) > budget:
            raise ValueError(
                f"revision exceeds explicit {budget}-byte source budget; raise HX_CODE_REVISION_MAX_BYTES"
            )
        blobs = {}

        def read_blob(path):
            if path not in blobs:
                value = subprocess.run(
                    ["git", "-C", str(repo), "cat-file", "blob", entries[path][1]],
                    capture_output=True,
                    timeout=30,
                    check=True,
                ).stdout
                if len(value) != entries[path][2]:
                    raise ValueError("Git blob size does not match tree")
                blobs[path] = value
            return blobs[path]

        context_names = (
            "pyproject.toml",
            "tsconfig.json",
            "package.json",
            "Cargo.toml",
            "go.mod",
            "Directory.Build.props",
            "Directory.Build.targets",
            ".hx/code-context.json",
        )
        context = snapshots.digest(
            [
                (path, hashlib.sha256(read_blob(path)).hexdigest())
                for path in context_names
                if path in entries
            ]
        )
        detected = code_adapters.detect()
        generation = extractor_generation(detected, context)
        tree = snapshots.PathMap(conn)
        before = dict(tree.items(parent["map_root"])) if parent else {}
        if parent and parent["generation"] == generation:
            delta = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "diff",
                    "--name-only",
                    "-z",
                    "--no-renames",
                    parent["head"],
                    head,
                    "--",
                ],
                capture_output=True,
                timeout=60,
                check=True,
            ).stdout
            changed = {os.fsdecode(p) for p in delta.split(b"\0") if p}
        else:
            changed = set(entries) | set(before)
        prepared = {}
        cached = 0
        skipped = 0
        with tempfile.TemporaryDirectory(prefix="hx-revision-") as temporary:
            scratch = Path(temporary)
            for path in sorted(changed):
                if path not in entries:
                    prepared[path] = None
                    continue
                mode, oid, size = entries[path]
                data = read_blob(path)
                sha = hashlib.sha256(data).hexdigest()
                reason = (
                    "oversize"
                    if size > maximum
                    else "binary"
                    if b"\0" in data
                    else None
                )
                if reason:
                    skipped += 1
                    value = {
                        t: []
                        for t in (
                            "files",
                            "symbols",
                            "refs",
                            "edges",
                            "projects",
                            "project_edges",
                            "contracts",
                        )
                    }
                    value.update(
                        mode=mode,
                        exclusions=[
                            dict(
                                repo=identity,
                                path=path,
                                sha256=sha,
                                size=size,
                                reason=reason,
                            )
                        ],
                    )
                    prepared[path] = (sha, value, None, data, None)
                    continue
                key, language = snapshots.extraction_key(sha, generation, path, context)
                result = snapshots.extraction(conn, key)
                if result is None:
                    text = data.decode("utf-8", errors="replace")
                    symbols, refs, edges = code_extract.extract(text, path)
                    target = scratch / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(data)
                    symbols, refs = code_adapters.syntax(
                        scratch, path, text, symbols, refs, detected
                    )
                    project, deps = code_projects.extract(text, path)
                    result = dict(
                        symbols=symbols,
                        refs=refs,
                        edges=edges,
                        project=project,
                        dependencies=deps,
                    )
                else:
                    cached += 1
                value = graph_value(identity, path, sha, size, mode, generation, result)
                prepared[path] = (
                    sha,
                    value,
                    (key, sha, generation, language, path, context, result),
                    data,
                    key,
                )
        # Parsing and Git reads finish before the short immutable-object write.
        updates = {}
        for path, value in prepared.items():
            if value is None:
                updates[path] = None
                continue
            sha, graph, extracted, data, key = value
            if not graph["exclusions"]:
                snapshots.source(conn, data)
            if extracted:
                snapshots.put_extraction(conn, *extracted)
            artifact = snapshots.put_graph(conn, graph, sha)
            updates[path] = artifact
        snapshot = snapshots.publish(
            conn,
            identity,
            str(repo),
            head,
            generation,
            updates,
            parent=parent["identity"] if parent else None,
            complete=True,
            activate_checkout=False,
        )
        conn.execute(
            "INSERT OR REPLACE INTO snapshot_refs VALUES(?,?,?)",
            (identity, "commit:" + head, snapshot),
        )
        # Registration enables later explicit lookup without binding the live HEAD.
        conn.execute(
            "INSERT OR IGNORE INTO repos VALUES(?,?,?,?,?)",
            (identity, str(repo), None, 0, None),
        )
        conn.commit()
        return dict(
            repo=identity,
            head=head,
            snapshot=snapshot,
            changed=len(changed),
            historical=True,
            files=len(entries) - skipped,
            skipped=skipped,
            reused_extractions=cached,
        )
    finally:
        conn.close()


def graph_value(repo, path, sha, size, mode, generation, result):
    """Normalize extracted facts exactly like the live SQL projection."""
    symbols = []
    for row in result["symbols"]:
        symbols.append(
            dict(row)
            | dict(
                repo=repo,
                name_folded=row["name"].casefold(),
                qualified_folded=row["qualified"].casefold(),
                attributes=json.dumps(row.get("attributes", [])),
                stale=int(row.get("stale", False)),
                unanchored=int(row.get("unanchored", False)),
                column=row.get("column", 0),
            )
        )
    refs = [
        dict(
            repo=repo,
            name=r["name"],
            qualified=r["qualified"],
            path=r["path"],
            line=r["line"],
            kind=r["kind"],
            lane="syntax",
            extractor=r.get(
                "extractor",
                "stdlib-"
                + code_extract.LANGUAGES.get(Path(path).suffix.lower(), "text"),
            ),
            anchor_sha=sha,
            stale=0,
        )
        for r in result["refs"]
    ]
    edges = [
        dict(
            repo=repo,
            src_path=src,
            dst_text=dst,
            kind=kind,
            lane="syntax",
            extractor="stdlib-natural"
            if Path(path).suffix.lower() in code_extract.code_natural.OBJECT_KINDS
            else "stdlib",
            anchor_sha=sha,
            stale=0,
            evidence="{}",
        )
        for src, dst, kind in result["edges"]
    ]
    project = result["project"]
    return dict(
        mode=mode,
        files=[
            dict(
                repo=repo,
                path=path,
                sha256=sha,
                size=size,
                symbols=" ".join(s["qualified"] for s in symbols),
                generation=generation,
            )
        ],
        symbols=symbols,
        refs=refs,
        edges=edges,
        projects=[
            dict(
                repo=repo,
                name=project["name"],
                path=path,
                data=json.dumps(project["data"]),
            )
        ]
        if project
        else [],
        project_edges=[
            dict(repo=repo, src_path=path, dst_text=dst, kind="project_depends_on")
            for dst in result["dependencies"]
        ]
        if project
        else [],
        contracts=[],
        exclusions=[],
    )


def index_revision(root, repo_path, at, **kwargs):
    with preparation_lock(root, repo_path):
        return _index_revision(root, repo_path, at, **kwargs)
