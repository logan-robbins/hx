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


def scope(root, repository, paths, *, bootstrap=False, local=False):
    # Read-only and discovery packets still depend on actual repository graph
    # evidence. An empty write set means repository orientation, not no evidence.
    paths = list(paths) or ["."]
    if not local:
        from .repo_service import configured_socket, request

        socket = configured_socket(root)
        if socket is not None:
            return request(
                socket,
                dict(
                    method="scope",
                    repository=str(Path(repository).resolve()),
                    paths=paths,
                    bootstrap=bootstrap,
                ),
            )
    repo = Path(repository).resolve()
    identity = store.namespace(repo)
    paths = list(paths)
    conn = store.connect(root, existing=True)
    record = (
        conn.execute("SELECT * FROM repos WHERE repo=?", (identity,)).fetchone()
        if conn
        else None
    )
    checkout = (
        conn.execute(
            "SELECT snapshot FROM checkout_heads WHERE root=? AND repo=?",
            (str(repo), identity),
        ).fetchone()
        if conn
        else None
    )
    if bootstrap and (record is None or checkout is None):
        if conn:
            conn.close()
        from .code_build import index_repo

        index_repo(root, repo)
        conn = store.connect(root, existing=True)
        record = conn.execute(
            "SELECT * FROM repos WHERE repo=?", (identity,)
        ).fetchone()
    selected = None
    if conn:
        from .code_snapshot import activate

        selected = conn.execute(
            "SELECT snapshot FROM checkout_heads WHERE root=? AND repo=?",
            (str(repo), identity),
        ).fetchone()
        if selected:
            snap = activate(conn, selected[0], identity, historical=False, paths=paths)
            record = dict(record) if record else {}
            record.update(root=str(repo), head=snap["head"])
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
        predicates = " OR ".join(
            "(path=? OR substr(path,1,length(?)+1)=? || '/' OR ?='.')" for _ in paths
        )
        bindings = tuple(v for p in paths for v in (p.rstrip("/") or ".",) * 4)
        rows = conn.execute(
            "SELECT path,sha256,generation FROM files WHERE repo=? AND ("
            + predicates
            + ") ORDER BY path",
            (identity, *bindings),
        )
        from .code_exclusions import fingerprint

        excluded = conn.execute(
            "SELECT path,sha256 FROM exclusions WHERE repo=? AND (" + predicates + ")",
            (identity, *bindings),
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
        from .code_adapters import detect
        from .code_build import _context, extractor_generation

        generation = extractor_generation(detect(), _context(repo))
        modes = (
            {
                r["path"]: r["mode"]
                for r in conn.execute(
                    "SELECT path,json_extract(data,'$.mode') AS mode FROM snapshot_paths"
                )
            }
            if selected
            else {}
        )
        for row in rows:
            if not in_scope(row["path"], paths):
                continue
            indexed.add(row["path"])
            try:
                target = repo / row["path"]
                if not target.parent.resolve().is_relative_to(repo):
                    raise OSError("path escapes repository")
                actual = hashlib.sha256(store.source_bytes(target)).hexdigest()
                stat = target.lstat()
                mode = (
                    "120000"
                    if target.is_symlink()
                    else "100755"
                    if stat.st_mode & 0o111
                    else "100644"
                )
            except OSError:
                actual = None
                mode = None
            if (
                actual != row["sha256"]
                or row["generation"] != generation
                or modes.get(row["path"]) not in {None, mode}
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
        from .code_build import scoped_names

        missing = [
            p
            for p in scoped_names(repo, paths)
            if in_scope(p, paths) and p not in indexed and (repo / p).is_file()
        ]
        # Binary files retain explicit fingerprints. Oversized text aborts
        # indexing rather than publishing missing source as complete coverage.
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
        from .repo_service import code_request

        shared = code_request(
            store_obj.root,
            ["index", "--repo", str(workdir), "--paths", *(str(p) for p in paths)],
        )
        if shared is not None:
            if shared["exit_code"]:
                raise ValueError(
                    "shared scope refresh failed: " + str(shared["result"])
                )
            return shared["result"]
        return index_repo(store_obj.root, workdir, paths=paths)
    return dict(changed=0)


def snapshot_scope(conn, selected, paths):
    """Freshness of the captured derived facts, independent of a live checkout."""
    changed = set()
    for row in conn.execute(
        "SELECT path,generation FROM files WHERE repo=?", (selected["repo"],)
    ):
        if in_scope(row["path"], paths) and row["generation"] != selected["generation"]:
            changed.add(row["path"])
    for table, key in (("symbols", "path"), ("refs", "path"), ("edges", "src_path")):
        for row in conn.execute(
            f"SELECT {key} FROM {table} WHERE repo=? AND stale=1", (selected["repo"],)
        ):
            if in_scope(row[0], paths):
                changed.add(row[0])
    reasons = []
    if not selected["complete"]:
        reasons.append("snapshot coverage is incomplete")
    if changed:
        reasons.append("snapshot scope has stale derived rows")
    return dict(
        fresh=not reasons,
        repo=selected["repo"],
        snapshot=selected["identity"],
        paths=sorted(changed),
        reasons=reasons,
        source_proof="immutable-snapshot",
    )
