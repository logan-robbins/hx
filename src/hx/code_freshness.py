"""Source freshness admission and recoverable completion-time graph refresh."""

from __future__ import annotations
import hashlib
import subprocess
from pathlib import Path
from . import code_store as store


def in_scope(path, scopes):
    return any(
        path == p or path.startswith(p.rstrip("/") + "/") or p == "." for p in scopes
    )


def scope(root, repository, paths, *, bootstrap=False):
    repo = Path(repository).resolve()
    identity = store.namespace(repo)
    paths = list(paths)
    if not paths:
        return dict(fresh=True, repo=identity, reasons=[], paths=[])
    conn = store.connect(root, existing=True)
    record = (
        conn.execute("SELECT * FROM repos WHERE repo=?", (identity,)).fetchone()
        if conn
        else None
    )
    if bootstrap and (record is None or Path(record["root"]).resolve() != repo):
        if conn:
            conn.close()
        from .code_build import index_repo

        index_repo(root, repo)
        conn = store.connect(root, existing=True)
        record = conn.execute(
            "SELECT * FROM repos WHERE repo=?", (identity,)
        ).fetchone()
    reasons = []
    changed = []
    try:
        if record is None:
            return dict(
                fresh=False,
                repo=identity,
                reasons=["scope index is missing"],
                paths=paths,
            )
        head = store.git(repo, "rev-parse", "HEAD")
        if record["head"] and head:
            ancestor = subprocess.run(
                [
                    "git",
                    "-C",
                    str(repo),
                    "merge-base",
                    "--is-ancestor",
                    record["head"],
                    head,
                ],
                capture_output=True,
                timeout=30,
            )
            if ancestor.returncode:
                reasons.append("indexed HEAD is not an ancestor of HEAD")
            diff = store.git(
                repo,
                "--literal-pathspecs",
                "diff",
                "--name-only",
                record["head"],
                head,
                "--",
                *paths,
            )
            if diff is None:
                reasons.append("indexed HEAD is unavailable")
            elif diff:
                changed.extend(diff.splitlines())
                reasons.append("scope changed since indexed HEAD")
        rows = conn.execute(
            "SELECT path,sha256,generation FROM files WHERE repo=? ORDER BY path",
            (identity,),
        )
        from .code_exclusions import fingerprint

        excluded = conn.execute(
            "SELECT path,sha256 FROM exclusions WHERE repo=?", (identity,)
        ).fetchall()
        indexed = {r["path"] for r in excluded if in_scope(r["path"], paths)}
        for row in excluded:
            if not in_scope(row["path"], paths):
                continue
            target = (repo / row["path"]).resolve()
            try:
                actual = fingerprint(target) if target.is_relative_to(repo) else None
            except OSError, ValueError:
                actual = None
            if actual != row["sha256"]:
                changed.append(row["path"])
        generation = conn.execute(
            "SELECT value FROM metadata WHERE key=?", ("extractors:" + identity,)
        ).fetchone()
        for row in rows:
            if not in_scope(row["path"], paths):
                continue
            indexed.add(row["path"])
            try:
                target = (repo / row["path"]).resolve()
                if not target.is_relative_to(repo):
                    raise OSError("path escapes repository")
                actual = hashlib.sha256(target.read_bytes()).hexdigest()
            except OSError:
                actual = None
            if (
                actual != row["sha256"]
                or generation
                and row["generation"] != generation[0]
            ):
                changed.append(row["path"])
        for table, key in [
            ("symbols", "path"),
            ("refs", "path"),
            ("edges", "src_path"),
        ]:
            rows = conn.execute(
                f"SELECT {key} FROM {table} WHERE repo=? AND stale=1", (identity,)
            )
            changed.extend(row[0] for row in rows if in_scope(row[0], paths))
        from .code_build import discover

        missing = [
            p
            for p in discover(repo)
            if in_scope(p, paths) and p not in indexed and (repo / p).is_file()
        ]
        # Binary/oversize files deliberately stay in the degraded coverage lane;
        # their existence alone cannot claim source freshness for graph queries.
        if missing:
            reasons.append("scope has files absent from the index")
            changed.extend(missing)
        if changed and "scope changed since indexed HEAD" not in reasons:
            reasons.append("scope has stale source rows")
        return dict(
            fresh=not reasons,
            repo=identity,
            head=head,
            indexed_head=record["head"],
            paths=sorted(set(changed)),
            reasons=reasons,
        )
    finally:
        if conn:
            conn.close()


def require(root, payload, *, bootstrap=True):
    result = scope(
        root, payload["workdir"], payload["write_paths"], bootstrap=bootstrap
    )
    if not result["fresh"]:
        from .continuity_store import Conflict

        raise Conflict(
            "stale code scope: "
            + ", ".join(result["reasons"])
            + "; rebuild with hx code index --repo "
            + payload["workdir"]
        )
    return result


def refresh_completion(store_obj, workdir, paths):
    """Graph writes are derived; retry the same bounded refresh after a crash."""
    from .code_build import index_repo

    if paths:
        return index_repo(store_obj.root, workdir, paths=paths)
    return dict(changed=0)
