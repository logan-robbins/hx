"""Atomic, idempotent external graph ingestion with explicit provenance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import code_store as store
from .code_build import insert_edge, insert_symbol


def relative(path):
    if (
        not isinstance(path, str)
        or not path
        or Path(path).is_absolute()
        or ".." in Path(path).parts
        or "\\" in path
    ):
        raise ValueError("graph paths must be repository-relative POSIX paths")
    return path


def jsonl(file):
    with Path(file).open() as handle:
        for line in handle:
            if len(line) > 256 * 1024:
                raise ValueError("import row exceeds 256 KiB")
            if line.strip():
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("import rows must be objects")
                yield row


def import_graph(
    root, identity, symbols_file, edges_file, *, repo_path=None, extractor="external"
):
    if not identity or not extractor:
        raise ValueError("namespace and extractor must be nonempty")
    conn = store.connect(root)
    try:
        registered = conn.execute(
            "SELECT root FROM repos WHERE repo=?", (identity,)
        ).fetchone()
        repo = (
            Path(repo_path).resolve()
            if repo_path
            else Path(registered[0])
            if registered and registered[0]
            else None
        )
        if repo is not None:
            if not repo.is_dir():
                raise ValueError("import repository does not exist")
            if store.db_path(root).resolve().is_relative_to(repo):
                raise ValueError("graph state must be outside the repository")
            if registered and registered[0] and Path(registered[0]).resolve() != repo:
                raise ValueError("namespace already names another repository")
        conn.execute(
            "INSERT OR IGNORE INTO repos VALUES(?,?,?,?,?)",
            (
                identity,
                str(repo) if repo else "",
                store.git(repo, "rev-parse", "HEAD") if repo else None,
                0,
                None,
            ),
        )
        conn.execute(
            "DELETE FROM symbols WHERE repo=? AND lane='imported' AND extractor=?",
            (identity, extractor),
        )
        conn.execute(
            "DELETE FROM edges WHERE repo=? AND lane='imported' AND extractor=?",
            (identity, extractor),
        )
        symbols = edges = 0
        locations = {}
        for row in jsonl(symbols_file):
            required = {
                "name",
                "qualified",
                "kind",
                "path",
                "line",
                "end_line",
                "signature",
            }
            if not required <= row.keys() or row.keys() - required - {"attributes"}:
                raise ValueError(
                    "import symbol requires name,qualified,kind,path,line,end_line,signature"
                )
            if any(
                not isinstance(row[k], str) or not row[k]
                for k in ("name", "qualified", "kind", "path")
            ) or not isinstance(row["signature"], str):
                raise ValueError("invalid symbol text")
            if (
                type(row["line"]) is not int
                or type(row["end_line"]) is not int
                or not 1 <= row["line"] <= row["end_line"]
            ):
                raise ValueError("invalid declaration span")
            relative(row["path"])
            anchor = None
            if repo:
                target = (repo / row["path"]).resolve()
                if not target.is_relative_to(repo):
                    raise ValueError("import path escapes repository")
                try:
                    text = target.read_text()
                    if row["end_line"] <= len(text.splitlines()):
                        anchor = store.span_sha(text, row["line"], row["end_line"])
                except OSError, UnicodeError:
                    pass
            row.update(
                lane="imported",
                extractor=extractor,
                anchor_sha=anchor,
                unanchored=anchor is None,
            )
            insert_symbol(conn, identity, row)
            symbols += 1
            locations[row["qualified"]] = row["path"]
            locations.setdefault(row["name"], row["path"])
        for row in jsonl(edges_file):
            if row.keys() != {"from", "to", "kind", "evidence"} or any(
                not isinstance(row[k], str) or not row[k]
                for k in ("from", "to", "kind")
            ):
                raise ValueError("edge requires from,to,kind,evidence")
            src = locations.get(row["from"], row["from"])
            relative(src)
            anchor = None
            if repo:
                target = (repo / src).resolve()
                if not target.is_relative_to(repo):
                    raise ValueError("edge path escapes repository")
                try:
                    anchor = hashlib.sha256(target.read_bytes()).hexdigest()
                except OSError:
                    pass
            insert_edge(
                conn,
                identity,
                src,
                row["to"],
                row["kind"],
                lane="imported",
                extractor=extractor,
                anchor=anchor,
                evidence=row["evidence"],
            )
            edges += 1
        from .code_snapshot import capture_external

        snapshot = capture_external(conn, identity, str(repo) if repo else "")
        conn.commit()
        return dict(
            repo=identity,
            snapshot=snapshot,
            symbols=symbols,
            edges=edges,
            lane="imported",
            extractor=extractor,
        )
    finally:
        conn.close()
