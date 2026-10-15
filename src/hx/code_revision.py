"""Whole-file indexing of immutable Git revisions, independent of live checkouts."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path

from . import code_extract, code_projects
from . import code_store as store

def _index_revision(
    root, repo_path, at, *, max_file_bytes=None, trigram=True, max_revision_bytes=None
):
    """Capture an explicitly requested commit directly from bounded Git objects.

    This never checks out files or changes the live graph projection. If the
    commit's parent was indexed, only its changed source paths are extracted.
    """
    import tempfile

    from .code_build import MAX_FILE_BYTES, SKIP_DIRS, extractor_generation

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

        from .file_limits import file_record, report

        records, binary = [], []
        for path in sorted(entries):
            data = read_blob(path)
            try:
                data.decode("utf-8")
                text = b"\0" not in data
            except UnicodeDecodeError:
                text = False
            if text:
                records.append(file_record(data, path=path))
            else:
                binary.append(path)
        file_sizes = report(records, repository=repo, binary_paths=binary)
        file_sizes["commit"] = head
        if previous:
            return dict(repo=identity, head=head, snapshot=previous[0], changed=0,
                        historical=True, file_sizes=file_sizes)

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
                reason = "binary" if b"\0" in data else None
                if reason is None and size > maximum:
                    raise ValueError(
                        f"{path}: complete source is {size} bytes, exceeding the "
                        f"explicit {maximum}-byte indexing budget; index aborted without "
                        "publishing partial coverage; raise --max-file-bytes to index the complete file"
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
            file_sizes=file_sizes,
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
    from .code_build import preparation_lock

    with preparation_lock(root, repo_path):
        return _index_revision(root, repo_path, at, **kwargs)
