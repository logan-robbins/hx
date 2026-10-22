"""Bounded deterministic retrieval over the structural and text lanes."""

from __future__ import annotations

import posixpath
import re
from pathlib import Path

from . import code_store as store

LANES = {"resolved": 0, "syntax": 1, "text": 2, "imported": 3, "contract": 4}
CAP = 100
LINKS = 250_000
# A Natural CALLNAT/FETCH/PERFORM names an object, never a prototype or data area.
NATURAL_TARGETS = {"natural_object", "program", "subprogram", "subroutine", "function", "helproutine"}


def symbols(conn, name, *, repo=None, limit=CAP, path=None):
    key = (name, repo, limit, path)
    if key in conn.symbol_cache:
        return [dict(r) for r in conn.symbol_cache[key]]
    if conn.snapshot is not None:
        from .code_snapshot import matching_facts
        from .code_stubs import declarations

        rows = matching_facts(
            conn,
            "symbols",
            "name_folded=? OR qualified_folded=?",
            (name.casefold(), name.casefold()),
            repo=repo,
            path=path,
            limit=min(CAP, limit),
        )
        result = [store.decorate(conn, r) for r in rows]
        # Published contracts answer every checkout until it indexes the real declaration.
        result += declarations(
            conn, name, repo=repo, path=path, limit=min(CAP, limit) - len(result)
        )
        conn.symbol_cache[key] = result
        return [dict(r) for r in result]
    rows = conn.execute(
        """SELECT * FROM symbols WHERE (name_folded=? OR qualified_folded=?)
        AND (? IS NULL OR repo=?) AND (? IS NULL OR path=?)
        ORDER BY CASE lane WHEN 'resolved' THEN 0 WHEN 'syntax' THEN 1 WHEN 'text' THEN 2 ELSE 3 END,repo,path,line LIMIT ?""",
        (name.casefold(), name.casefold(), repo, repo, path, path, min(CAP, limit)),
    ).fetchall()
    result = [store.decorate(conn, r) for r in rows]
    conn.symbol_cache[key] = result
    return [dict(r) for r in result]


def snippet(body, query, *, exact=False):
    terms = re.findall(r"[\w]+", query)
    positions = [body.casefold().find(t.casefold()) for t in terms]
    pos = min([p for p in positions if p >= 0], default=0)
    result = body[max(0, pos - 100) : max(0, pos - 100) + 300]
    return result if exact else re.sub(r"\s+", " ", result).strip()


def distance(a, b):
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y))
            )
        previous = current
    return previous[-1]


def search(conn, query, *, repo=None, limit=10, fuzzy=True):
    terms = re.findall(r"\w+", query)
    if not terms or limit <= 0:
        return []
    exact = symbols(conn, query, repo=repo)
    ranking = {}
    files = {}
    for row in exact:
        key = (row["repo"], row["path"])
        ranking[key] = 10 - LANES[row["lane"]]
        files.setdefault(key, row)
    match = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
    if conn.snapshot is not None:
        from .code_snapshot import matching_files, source_search_tables

        for table in source_search_tables(conn):
            rows = matching_files(
                conn, table, match, repo=repo, limit=max(50, min(CAP, limit) * 4)
            )
            for i, row in enumerate(rows, 1):
                key = (row["repo"], row["path"])
                ranking[key] = ranking.get(key, 0) + 1 / (60 + i)
                files.setdefault(
                    key,
                    store.decorate(
                        conn,
                        dict(row)
                        | dict(
                            lane="text",
                            extractor="snapshot-fts5",
                            anchor_sha=row["sha256"],
                        ),
                    ),
                )
    if not ranking and fuzzy:
        if conn.snapshot is not None:
            from .code_snapshot import names_for

            names = list(names_for(conn, repo=repo))
        else:
            names = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT name FROM symbols WHERE (? IS NULL OR repo=?) ORDER BY name LIMIT 5000",
                    (repo, repo),
                )
            ]
        corrected = query
        for term in terms:
            if len(term) < 5:
                continue
            best = min(
                names,
                key=lambda n: (distance(n.casefold(), term.casefold()), n),
                default=None,
            )
            if best and distance(best.casefold(), term.casefold()) <= (
                2 if len(term) <= 12 else 3
            ):
                corrected = corrected.replace(term, best)
        if corrected != query:
            hits = search(conn, corrected, repo=repo, limit=limit, fuzzy=False)
            for hit in hits:
                hit["layer"] = "fuzzy"
            return hits
    result = []
    for key, score in ranking.items():
        row = files[key]
        if conn.snapshot is not None:
            from .code_snapshot import body_for

            body = body_for(conn, *key)
            body = body if body is not None else row.get("signature", "")
        else:
            body = conn.execute(
                "SELECT body FROM files WHERE repo=? AND path=?", key
            ).fetchone()
            body = body[0] if body else row.get("signature", "")
        if len(terms) > 1:
            locations = [body.casefold().find(t.casefold()) for t in terms]
            if all(p >= 0 for p in locations):
                score *= 1.5 if max(locations) - min(locations) <= 100 else 1.1
        result.append(
            {
                k: row.get(k)
                for k in (
                    "repo",
                    "path",
                    "lane",
                    "extractor",
                    "anchor_sha",
                    "stale",
                    "snapshot",
                    "source_proof",
                )
            }
            | dict(
                title=row.get("qualified", Path(row["path"]).name),
                snippet=snippet(body, query),
                score=score,
                layer="symbol" if "qualified" in row else "fts",
                signature=row.get("signature", ""),
            )
        )
    return sorted(result, key=lambda r: (-r["score"], r["repo"], r["path"]))[
        : min(CAP, limit)
    ]


def callers(conn, name, *, repo=None, limit=CAP, count=False):
    # A Natural prototype declares an interface in the caller, not an executable
    # definition. Its file must still be eligible as a real call site.
    if conn.snapshot is not None:
        from .code_snapshot import matching_facts

        definitions = [
            store.decorate(conn, r)
            for r in matching_facts(
                conn,
                "symbols",
                "name_folded=? OR qualified_folded=?",
                (name.casefold(), name.casefold()),
                repo=repo,
            )
            if r["kind"] != "prototype"
        ]
    else:
        definitions = [
            r for r in symbols(conn, name, repo=repo) if r["kind"] != "prototype"
        ]
    defining = {(r["repo"], r["path"]) for r in definitions}
    if conn.snapshot is not None:
        from .code_snapshot import matching_facts

        rows = matching_facts(
            conn,
            "refs",
            "name_folded=? OR qualified_folded=?",
            (name.casefold().rsplit(".", 1)[-1], name.casefold()),
            repo=repo,
        )
    else:
        rows = conn.execute(
            "SELECT * FROM refs WHERE (lower(name)=? OR lower(qualified)=?) AND (? IS NULL OR repo=?) ORDER BY repo,path,line",
            (name.casefold().rsplit(".", 1)[-1], name.casefold(), repo, repo),
        )
    result = []
    seen = set()
    for row in rows:
        key = (row["repo"], row["path"], row["line"], row["lane"])
        if key[:2] in defining or key in seen:
            continue
        seen.add(key)
        value = store.decorate(conn, row)
        value["reference"] = (
            "resolved"
            if row["lane"] == "resolved"
            else "syntactic"
            if row["lane"] == "syntax"
            else "imported"
        )
        value["stale"] |= any(
            d["stale"] for d in definitions if d["repo"] == row["repo"]
        )
        result.append(value)
    if conn.snapshot is not None:
        imported = matching_facts(
            conn,
            "edges",
            "lane='imported' AND (dst_folded=? OR dst_folded=?)",
            (name.casefold(), name.casefold().rsplit(".", 1)[-1]),
            repo=repo,
        )
    else:
        imported = conn.execute(
            "SELECT * FROM edges WHERE lane='imported' AND (lower(dst_text)=? OR lower(dst_text)=?) AND (? IS NULL OR repo=?) ORDER BY repo,src_path,kind",
            (name.casefold(), name.casefold().rsplit(".", 1)[-1], repo, repo),
        )
    for row in imported:
        result.append(
            store.decorate(conn, row) | dict(path=row["src_path"], reference="imported")
        )
    if count:
        return {
            "count": len({(r["repo"], r["path"]) for r in result}),
            "stale": any(r["stale"] for r in result),
            "fan_in": "files",
        }
    return result[: min(CAP, limit)]


class Paths:
    """A namespace's indexed paths, keyed for import resolution without scans."""

    def __init__(self, paths):
        from .code_natural import OBJECT_KINDS

        self.paths = set(paths)
        self.stems, self.natural = {}, {}
        for path in sorted(self.paths):
            stem = Path(path).stem
            self.stems.setdefault(stem, []).append(path)
            if Path(path).suffix.lower() in OBJECT_KINDS:
                self.natural.setdefault(stem.casefold(), []).append(path)


def resolve_path(conn, repo, src, dst, indexed=None):
    if not isinstance(indexed, Paths):
        indexed = Paths(
            indexed
            if indexed is not None
            else (
                r[0]
                for r in conn.execute(
                    "SELECT path FROM files WHERE repo=? UNION SELECT path FROM exclusions WHERE repo=?",
                    (repo, repo),
                )
            )
        )
    paths = indexed.paths
    if dst in paths:
        return dst
    from .code_natural import OBJECT_KINDS

    if Path(src).suffix.lower() in OBJECT_KINDS:
        # Natural library/steplib resolution is a runtime contract. Prefer a
        # unique local-library file, then a unique repository candidate.
        natural = indexed.natural.get(dst.casefold(), [])
        local = [p for p in natural if Path(p).parent == Path(src).parent]
        if len(local) == 1:
            return local[0]
        return natural[0] if len(natural) == 1 else None
    if dst.startswith("./") or dst.startswith("../"):
        dst = posixpath.normpath(posixpath.join(str(Path(src).parent), dst))
    candidates = [dst, dst.replace(".", "/")]
    for base in list(candidates):
        candidates.extend(
            base + suffix
            for suffix in (".py", ".cs", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go")
        )
        candidates.extend(
            base + "/index" + suffix for suffix in (".ts", ".js", ".tsx", ".jsx")
        )
        candidates.append(base + "/__init__.py")
    for candidate in candidates:
        if candidate in paths:
            return candidate
    candidates = indexed.stems.get(dst.replace(".", "/").split("/")[-1], [])
    return candidates[0] if len(candidates) == 1 else None


def edges(conn, *, repo=None, path=None, limit=CAP):
    rows = conn.execute(
        "SELECT * FROM edges WHERE (? IS NULL OR repo=?) ORDER BY repo,src_path,dst_text,kind",
        (repo, repo),
    )
    indexed = {}
    result = []
    for row in rows:
        identity = row["repo"]
        if identity not in indexed:
            indexed[identity] = Paths(
                r[0]
                for r in conn.execute(
                    "SELECT path FROM files WHERE repo=? UNION SELECT path FROM exclusions WHERE repo=?",
                    (identity, identity),
                )
            )
        dst = resolve_path(
            conn, identity, row["src_path"], row["dst_text"], indexed[identity]
        )
        if not dst:
            targets = symbols(conn, row["dst_text"], repo=identity)
            dst = targets[0]["path"] if len({t["path"] for t in targets}) == 1 else None
        if not dst and row["lane"] != "imported":
            continue
        dst = dst or row["dst_text"]
        if path and path not in {row["src_path"], dst}:
            continue
        value = store.decorate(conn, row) | dict(
            from_path=row["src_path"], to_path=dst, status="candidate"
        )
        if dst in indexed[identity].paths:
            value["stale"] |= store.decorate(conn, dict(repo=identity, path=dst))[
                "stale"
            ]
        result.append(value)
        if len(result) >= limit:
            break
    # Calls/tests derive from reference candidates without pretending to resolve overloads.
    if len(result) < limit:
        refs = conn.execute(
            "SELECT * FROM refs WHERE kind='calls' AND (? IS NULL OR repo=?) ORDER BY repo,path,line",
            (repo, repo),
        )
        seen = {(r["repo"], r["from_path"], r["to_path"], r["kind"]) for r in result}
        for ref in refs:
            targets = symbols(
                conn,
                ref["qualified"] if ref["lane"] == "resolved" else ref["name"],
                repo=ref["repo"],
            )
            for target in targets:
                if (
                    ref["extractor"] == "stdlib-natural"
                    and target["kind"] not in NATURAL_TARGETS
                ):
                    continue
                if (
                    ref["path"] == target["path"]
                    or path
                    and path not in {ref["path"], target["path"]}
                ):
                    continue
                kind = (
                    "tests"
                    if "test" in ref["path"].lower() or "spec" in ref["path"].lower()
                    else "calls"
                )
                key = (ref["repo"], ref["path"], target["path"], kind)
                if key in seen:
                    continue
                seen.add(key)
                value = store.decorate(conn, ref) | dict(
                    from_path=ref["path"],
                    to_path=target["path"],
                    kind=kind,
                    status="candidate",
                )
                value["stale"] |= target["stale"]
                result.append(value)
                if len(result) >= limit:
                    return result
    return result


def links(conn, *, repo=None, limit=LINKS):
    """The selected checkouts' file links (imports, calls, tests) in one bounded pass.

    Joins the postings of each checkout's current artifacts only: no JSON views and
    no per-reference symbol lookups. A `calls` reference links to every same-named
    declaration in another file, as `edges` does, and imports resolve through
    `resolve_path`. Returns `{files: {(repo, path): {size, declarations}}, links,
    truncated}` with at most `limit` links; a link's `stale` covers both files.
    """
    conn.execute(
        "CREATE TEMP TABLE IF NOT EXISTS link_paths(repo TEXT NOT NULL,path TEXT NOT NULL,"
        "artifact TEXT NOT NULL,PRIMARY KEY(artifact,path))"
    )
    conn.execute("DELETE FROM temp.link_paths")
    scopes = conn.snapshot_scopes
    for identity, snapshot in sorted(conn.snapshot_maps.items()):
        if repo is None or identity == repo:
            conn.executemany(
                "INSERT OR IGNORE INTO temp.link_paths VALUES(?,?,?)",
                (
                    (identity, path, artifact)
                    for path, artifact in conn.path_tree.items(snapshot["map_root"])
                    if scopes is None
                    or any(
                        p == "." or path == p or path.startswith(str(p).rstrip("/") + "/")
                        for p in scopes
                    )
                ),
            )
    files = {
        (r["repo"], r["path"]): {"size": r["size"], "declarations": r["symbols"] or 0}
        for r in conn.execute(
            "SELECT a.repo,a.path,p.size,c.symbols FROM temp.link_paths a "
            "JOIN main.snapshot_path_postings p ON p.artifact=a.artifact AND p.is_text=1 "
            "LEFT JOIN main.snapshot_object_counts c ON c.artifact=a.artifact "
            "ORDER BY a.repo,a.path"
        )
    }
    paths = {}
    for identity, path in conn.execute("SELECT repo,path FROM temp.link_paths"):
        paths.setdefault(identity, []).append(path)
    paths = {identity: Paths(values) for identity, values in paths.items()}
    stale = {}

    def changed(identity, path):
        if (identity, path) not in stale:
            row = {"repo": identity, "path": path}
            stale[identity, path] = store.decorate(conn, row)["stale"]
        return stale[identity, path]

    result, seen = [], set()

    def add(link):
        key = tuple(
            link[k] for k in ("repo", "from_path", "to_path", "kind", "ordinal")
        )
        if key in seen or link["from_path"] == link["to_path"]:
            return True
        if len(result) >= limit:
            return False
        seen.add(key)
        link["stale"] = changed(link["repo"], link["from_path"]) or changed(
            link["repo"], link["to_path"]
        )
        result.append(link)
        return True

    calls = " UNION ".join(
        "SELECT a.repo,a.path AS from_path,r.lane,r.extractor,s.path AS to_path,s.ordinal,s.name,"
        "s.qualified_folded,s.kind FROM temp.link_paths a "
        "JOIN main.snapshot_ref_postings r ON r.artifact=a.artifact AND r.kind='calls' "
        f"JOIN main.snapshot_symbol_postings s ON s.{column}=(CASE WHEN r.lane='resolved' "
        "THEN r.qualified_folded ELSE r.name_folded END) AND s.repo=a.repo "
        "JOIN temp.link_paths b ON b.artifact=s.artifact AND b.path=s.path WHERE s.path<>a.path"
        for column in ("name_folded", "qualified_folded")
    )
    complete = True
    for row in conn.execute(calls + " ORDER BY 1,2,5,6,3"):
        if row["extractor"] == "stdlib-natural" and row["kind"] not in NATURAL_TARGETS:
            continue
        source = row["from_path"].lower()
        kind = "tests" if "test" in source or "spec" in source else "calls"
        link = dict(
            repo=row["repo"],
            from_path=row["from_path"],
            to_path=row["to_path"],
            kind=kind,
            lane=row["lane"],
            name=row["name"],
            qualified=row["qualified_folded"],
            symbol_kind=row["kind"],
            ordinal=row["ordinal"],
        )
        if not add(link):
            complete = False
            break
    declared = {}
    edges = conn.execute(
        "SELECT a.repo,a.path AS from_path,e.kind,e.lane,e.dst_text FROM temp.link_paths a "
        "JOIN main.snapshot_edge_postings e ON e.artifact=a.artifact "
        "WHERE e.kind IN ('imports','calls','tests') ORDER BY a.repo,a.path,e.dst_text,e.kind"
    ) if complete else ()
    for row in edges:
        identity, text = row["repo"], row["dst_text"]
        target = resolve_path(conn, identity, row["from_path"], text, paths[identity])
        if target is None:
            # An unresolved import names a declaration when exactly one file declares it.
            if (identity, text) not in declared:
                found = conn.execute(
                    "SELECT DISTINCT s.path FROM main.snapshot_symbol_postings s "
                    "JOIN temp.link_paths b ON b.artifact=s.artifact AND b.path=s.path "
                    "WHERE (s.name_folded=? OR s.qualified_folded=?) AND s.repo=? LIMIT 2",
                    (text.casefold(), text.casefold(), identity),
                ).fetchall()
                declared[identity, text] = found[0][0] if len(found) == 1 else None
            target = declared[identity, text]
        if target is None:
            continue
        link = dict(
            repo=identity,
            from_path=row["from_path"],
            to_path=target,
            kind=row["kind"],
            lane=row["lane"],
            name=None,
            qualified=None,
            symbol_kind=None,
            ordinal=None,
        )
        if not add(link):
            complete = False
            break
    return {"files": files, "links": result, "truncated": not complete}
