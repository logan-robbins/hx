"""Dependency invalidation of syntax/reference candidates after scoped refresh."""

from collections import defaultdict

from .code_queries import resolve_path


def dependencies(conn, repo, *, changed=None, new_symbols=None, added=(), deleted=()):
    if conn.snapshot is not None and changed is not None:
        return selected_dependencies(
            conn, repo, changed, new_symbols or {}, set(added), set(deleted)
        )
    paths = {
        r[0]
        for r in conn.execute(
            "SELECT path FROM files WHERE repo=? UNION SELECT path FROM exclusions WHERE repo=?",
            (repo, repo),
        )
    }
    definitions = defaultdict(set)
    for path, name in conn.execute(
        "SELECT path,name FROM symbols WHERE repo=?", (repo,)
    ):
        definitions[name.casefold()].add(path)
    reverse = defaultdict(set)
    for path, name in conn.execute("SELECT path,name FROM refs WHERE repo=?", (repo,)):
        for target in definitions[name.casefold()]:
            if target != path:
                reverse[target].add(path)
    for src, dst in conn.execute(
        "SELECT src_path,dst_text FROM edges WHERE repo=? AND kind='imports'", (repo,)
    ):
        target = resolve_path(conn, repo, src, dst, paths)
        if target and target != src:
            reverse[target].add(src)
    return reverse


def invalidate(conn, repo, reverse, changed, refreshed):
    pending = list(changed)
    affected = set(changed)
    while pending:
        for path in sorted(reverse[pending.pop()]):
            if path not in affected:
                affected.add(path)
                pending.append(path)
    for path in sorted(affected - set(refreshed)):
        for table, key in [
            ("symbols", "path"),
            ("refs", "path"),
            ("edges", "src_path"),
        ]:
            conn.execute(
                f"UPDATE {table} SET stale=1 WHERE repo=? AND {key}=? AND lane<>'imported'",
                (repo, path),
            )


def selected_dependencies(conn, repo, changed, new_symbols, added, deleted):
    """Visit only changed definitions and their indexed dependent closure."""
    from pathlib import Path

    from .code_natural import OBJECT_KINDS
    from .code_snapshot import (
        graph_for,
        matching_facts,
        path_info,
        paths_with_stem,
        selected_facts,
    )

    class SelectedPaths:
        def __init__(self, src, dst, projected):
            self.src, self.dst, self.projected = src, dst, projected

        def __contains__(self, path):
            if self.projected and path in deleted:
                return False
            if self.projected and path in added:
                return True
            return path_info(conn, repo, path) is not None

        def __iter__(self):
            natural = Path(self.src).suffix.lower() in OBJECT_KINDS
            stem = (
                self.dst.casefold()
                if natural
                else self.dst.replace(".", "/").split("/")[-1]
            )
            rows = paths_with_stem(conn, repo, stem, folded=natural)
            paths = {r["path"] for r in rows}
            if self.projected:
                paths -= deleted
                paths.update(
                    p
                    for p in added
                    if (Path(p).stem.casefold() if natural else Path(p).stem) == stem
                )
            return iter(sorted(paths))

    reverse = defaultdict(set)
    visited = set()
    pending = list(sorted(changed))
    resolutions = {}
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        graph = graph_for(conn, repo, path)
        names = {r["name"].casefold() for r in (graph["symbols"] if graph else [])}
        names.update(r["name"].casefold() for r in new_symbols.get(path, []))
        dependents = set()
        if names:
            refs = matching_facts(
                conn,
                "refs",
                "name_folded IN (" + ",".join("?" for _ in names) + ")",
                tuple(sorted(names)),
                repo=repo,
            )
            dependents.update(row["path"] for row in refs if row["path"] != path)
        target = Path(path)
        leaves = {
            target.stem.casefold(),
            target.name.casefold(),
            target.name.split(".")[-1].casefold(),
        }
        rows = conn.execute(
            "SELECT e.* FROM snapshot_import_postings i JOIN snapshot_edge_postings e ON e.artifact=i.artifact AND e.ordinal=i.ordinal WHERE i.repo=? AND i.leaf IN ("
            + ",".join("?" for _ in leaves)
            + ") ORDER BY e.path,e.ordinal",
            (repo, *sorted(leaves)),
        )
        for row in selected_facts(conn, "edges", rows):
            src, dst = row["src_path"], row["dst_text"]
            key = (src, dst)
            if key not in resolutions:
                before = resolve_path(
                    conn, repo, src, dst, SelectedPaths(src, dst, False)
                )
                after = resolve_path(
                    conn, repo, src, dst, SelectedPaths(src, dst, True)
                )
                resolutions[key] = (before, after)
            before, after = resolutions[key]
            if src != path and (before == path or after == path or before != after):
                dependents.add(src)
        reverse[path].update(dependents)
        pending.extend(sorted(dependents - visited))
    return reverse
