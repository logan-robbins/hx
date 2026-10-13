"""Dependency invalidation of syntax/reference candidates after scoped refresh."""

from collections import defaultdict
from .code_queries import resolve_path


def dependencies(conn, repo):
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
