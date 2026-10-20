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


def git_changes(repo, base, head, *, status=None):
    """Paths changed between two commits plus the working tree's status paths."""
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
    if status is not None:
        return names | status
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
        # Checkouts publish independently; only this checkout's binding may not move.
        current = conn.execute(
            "SELECT snapshot FROM checkout_heads WHERE root=? AND repo=?",
            (journal["root"], identity),
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
            # Resolved rows are a namespace registry; every other fact of an
            # unchanged path stays as this checkout's parent captured it.
            for path in sorted(
                r[0]
                for r in conn.execute(
                    "SELECT path FROM symbols WHERE repo=? AND lane='resolved' UNION SELECT path FROM refs WHERE repo=? AND lane='resolved' UNION SELECT src_path FROM edges WHERE repo=? AND lane='resolved'",
                    (identity, identity, identity),
                )
            ):
                if path in changed_paths:
                    updates[path] = snapshots.capture_path(
                        conn, identity, path, mode=modes.get(path)
                    )
                    continue
                artifact = (
                    updates[path]
                    if path in updates
                    else tree.get(parent[0], path)
                    if parent
                    else None
                )
                updates[path] = snapshots.overlay_lanes(
                    conn, identity, path, snapshots.graph_value(conn, artifact), {"resolved"}
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
            dirty=snapshots.unpacked(journal["dirty_paths"]),
        )
        conn.execute(
            "INSERT INTO repos VALUES(?,?,?,?,?) ON CONFLICT(repo,root) DO UPDATE SET head=excluded.head,dirty=excluded.dirty,indexed_at=excluded.indexed_at",
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
    from .file_limits import scan_repo

    # Every refresh exposes all legacy outliers, including unchanged files
    # outside a scoped refresh. Indexing never turns large source into absence.
    file_sizes = scan_repo(repo)
    from . import code_adapters, code_journal
    from . import code_snapshot as snapshots

    identity = store.namespace(repo)
    detected = code_adapters.detect()
    context = _context(repo)
    generation = extractor_generation(detected, context)
    conn = store.connect(root, trigram=trigram)
    try:
        own = conn.execute(
            "SELECT s.* FROM checkout_heads h JOIN code_snapshots s ON s.identity=h.snapshot WHERE h.root=? AND h.repo=?",
            (str(repo), identity),
        ).fetchone()
        head = store.git(repo, "rev-parse", "HEAD")
        status = git_changes(repo, None, None)
        if status is None:
            if head:
                raise ValueError("repository status is unavailable")
            # A directory outside Git has no HEAD to differ from.
            status = set()
        # The delta base is this checkout's own snapshot, else a complete sibling
        # snapshot at the same HEAD (clean first); its recorded dirty and stale
        # paths name everything that can differ from this working tree.
        base = own or conn.execute(
            "SELECT s.* FROM code_snapshots s LEFT JOIN snapshot_refs r ON r.repo=s.repo AND r.ref=? AND r.snapshot=s.identity WHERE s.repo=? AND s.head=? AND s.complete=1 AND s.generation=? ORDER BY r.snapshot IS NULL,s.created_at DESC LIMIT 1",
            ("commit:" + str(head), identity, head, generation),
        ).fetchone()
        snapshot_base = base["identity"] if base else None
        tree = snapshots.PathMap(conn)
        entries = {}

        def entry(rel):
            if rel not in entries:
                artifact = tree.get(base["map_root"], rel) if base else None
                entries[rel] = snapshots.graph_value(conn, artifact) or {}
            return entries[rel]

        def base_paths(scope="."):
            if not base:
                return set()
            if scope == ".":
                return {p for p, _ in tree.items(base["map_root"])}
            return {p for p, _ in tree.scoped_items(base["map_root"], scope)}

        stale = snapshots.path_set(base, "stale_paths")
        divergence = None
        if base and base["complete"] and head and base["head"]:
            delta = git_changes(repo, base["head"], head, status=status)
            if delta is not None:
                divergence = delta | snapshots.path_set(base, "dirty_paths") | stale
        scopes = (
            [str(p).rstrip("/") or "." for p in paths] if paths is not None else None
        )
        if scopes is not None and any(
            Path(p).is_absolute() or ".." in Path(p).parts for p in scopes
        ):
            raise ValueError("index paths must be repository relative")
        if scopes is not None:
            names = set(scoped_names(repo, scopes))
            names.update(p for scope in scopes for p in base_paths(scope))
            if base is not own:
                # A borrowed base is exact outside the scope only after its divergence.
                names.update(divergence if divergence is not None else base_paths())
        elif divergence is not None:
            names = set(divergence)
            if base["generation"] != generation:
                names.update(base_paths())
        else:
            names = set(discover(repo)) | base_paths()
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
            indexed = entry(rel)
            file = (indexed.get("files") or [None])[0]
            exclusion = (indexed.get("exclusions") or [None])[0]
            if not (
                path.is_file() or path.is_symlink()
            ) or not path.parent.resolve().is_relative_to(repo):
                if file or exclusion:
                    changes.append(dict(path=rel, operation="delete"))
                return
            before = path.lstat()
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
            old_mode = indexed.get("mode")
            reason = "binary" if b"\0" in data else None
            if reason is None and len(data) > maximum:
                raise ValueError(
                    f"{rel}: complete source is {len(data)} bytes, exceeding the "
                    f"explicit {maximum}-byte indexing budget; index aborted without "
                    "publishing partial coverage; raise --max-file-bytes to index the complete file"
                )
            if reason:
                skipped += 1
                if (
                    exclusion
                    and exclusion["sha256"] == sha
                    and exclusion["reason"] == reason
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
                and file
                and file["sha256"] == sha
                and file["generation"] == generation
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
                suffixes={Path(p).suffix for p in names | base_paths()},
            )
        # No writer is held while files are read, adapters run or syntax is parsed.
        for data in objects:
            snapshots.source(conn, data)
        for args in extractions:
            snapshots.put_extraction(conn, *args)
        commit_changes = (
            git_changes(repo, base["head"], head, status=status)
            if base and head
            else None
        )
        complete = paths is None or bool(
            base
            and base["complete"]
            and commit_changes is not None
            and commit_changes <= names
        )
        # Every candidate of a full or borrowed-base refresh was read from disk, so
        # only the working tree's status differs from HEAD; a scoped refresh of the
        # own base also keeps whatever its parent and the commit delta left behind.
        dirty_paths = (
            status
            if paths is None or base is not own
            else status
            | snapshots.path_set(base, "dirty_paths")
            | (commit_changes or set())
        )
        journal = code_journal.prepare(
            conn,
            identity,
            repo,
            own["identity"] if own else None,
            head,
            generation,
            changes,
            complete=complete,
            snapshot_base=snapshot_base,
            dirty=bool(status),
            dirty_paths=dirty_paths,
            resolved=resolved_prepared,
        )
        result = apply_changeset(
            conn,
            journal,
            detected=detected,
            resolve=resolve,
            indexer_checkout=indexer_checkout,
        )
        # Clean before preparation and after publication: the snapshot is the commit.
        if (
            paths is None
            and head
            and not status
            and not store.git(repo, "status", "--porcelain")
        ):
            conn.execute(
                "INSERT OR REPLACE INTO snapshot_refs VALUES(?,?,?)",
                (identity, "commit:" + head, result["snapshot"]),
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
            file_sizes=file_sizes,
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
from .code_revision import index_revision
