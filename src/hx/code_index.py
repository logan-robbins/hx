"""CLI facade for the shared, rebuildable structural code graph."""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

from . import code_queries as query
from . import code_store as store
from .code_build import index_repo
from .code_store import db_path as db_path
from .errors import HxError


def _read(root, method, *args, at=None, **kwargs):
    conn = store.connect(root, existing=True)
    if conn is None:
        return []
    try:
        if at is not None:
            from .code_snapshot import activate_query

            activate_query(conn, at, kwargs.get("repo"))
        else:
            from .code_snapshot import activate_latest

            activate_latest(conn, kwargs.get("repo"))
        return method(conn, *args, **kwargs)
    finally:
        conn.close()


def symbols_for(root, name, *, repo=None, limit=100, path=None, at=None):
    return _read(root, query.symbols, name, repo=repo, limit=limit, path=path, at=at)


def search(root, name, *, repo=None, limit=10, at=None):
    return _read(root, query.search, name, repo=repo, limit=limit, at=at)


def callers(root, name, *, repo=None, limit=100, count=False, at=None):
    return _read(root, query.callers, name, repo=repo, limit=limit, count=count, at=at)


def graph_edges(root, *, repo=None, path=None, limit=100, at=None):
    return _read(root, query.edges, repo=repo, path=path, limit=limit, at=at)


def impact(root, paths, *, repo=None, limit=100, at=None):
    conn = store.connect(root, existing=True)
    symbols = []
    if conn:
        try:
            if at is not None:
                from .code_snapshot import activate_query

                activate_query(conn, at, repo)
            else:
                from .code_snapshot import activate_latest

                activate_latest(conn, repo)
            for path in paths:
                if conn.snapshot is not None:
                    from .code_snapshot import facts_in_path

                    rows = sorted(
                        facts_in_path(conn, "symbols", str(path), repo=repo),
                        key=lambda r: (r["repo"], r["line"]),
                    )[:limit]
                else:
                    rows = conn.execute(
                        "SELECT * FROM symbols WHERE path=? AND (? IS NULL OR repo=?) ORDER BY repo,line LIMIT ?",
                        (str(path), repo, repo, limit),
                    )
                symbols.extend(store.decorate(conn, r) for r in rows)
        finally:
            conn.close()
    references = set()
    for row in symbols[:limit]:
        references.update(
            r["path"]
            for r in callers(root, row["name"], repo=row["repo"], limit=limit, at=at)
        )
    references = sorted(references - set(paths))[:limit]
    tests = [p for p in references if "test" in p.lower() or "spec" in p.lower()]
    return dict(
        symbols=symbols[:limit],
        symbol_names=sorted({s["name"] for s in symbols}),
        referencing_files=references,
        referencing=references,
        candidate_tests=tests,
        candidate_test_files=tests,
        test_files=tests,
    )


def sift_results(root, results, question, env=None):
    if not results:
        return results
    from . import search_sift

    candidates = [
        dict(
            id=str(i) + ":" + r["path"],
            snippet=r.get("snippet", r.get("signature", "")),
        )
        for i, r in enumerate(results)
    ]
    sifted = search_sift.sift(
        root, question, candidates[: search_sift.MAX_CANDIDATES], env=env
    )
    scores = {r["id"]: r.get("jev_score") for r in sifted.get("results", [])}
    for i, row in enumerate(results):
        row["jev_score"] = scores.get(str(i) + ":" + row["path"])
        row["sift_status"] = (
            sifted["status"] if i < search_sift.MAX_CANDIDATES else "not-sifted"
        )
    return sorted(results, key=lambda r: -(r["jev_score"] or 0))


def search_sifted(root, name, question, *, limit=10, env=None):
    return sift_results(root, search(root, name, limit=limit), question, env)


def _parser():
    parser = argparse.ArgumentParser(prog="hx code")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in (
        "index",
        "search",
        "symbols",
        "type",
        "signature",
        "callers",
        "register",
        "tests-for",
        "conform",
        "precedent",
        "projects",
        "map",
        "stats",
        "edges",
        "impact",
        "fresh",
        "import",
        "contracts",
        "contract",
        "submaps",
        "journal",
    ):
        p = sub.add_parser(name)
        p.add_argument("--root", help=argparse.SUPPRESS)
        p.add_argument("--namespace")
        if name != "import":
            p.add_argument("--at", help="indexed commit, ref or immutable snapshot ID")
        if name == "index":
            p.add_argument("--repo", required=True)
            p.add_argument("--paths", nargs="+")
            p.add_argument("--max-file-bytes", type=int)
            p.add_argument("--porter-only", action="store_true")
            p.add_argument("--resolved", action="store_true")
            p.add_argument("--indexer-checkout")
        elif name == "import":
            p.add_argument("--repo")
            p.add_argument("--symbols", required=True)
            p.add_argument("--edges", required=True)
            p.add_argument("--extractor", default="external")
        else:
            p.add_argument(
                "--expect", choices=["exists", "absent", "one", "clean", "fresh"]
            )
            p.add_argument("--owners", action="store_true")
            p.add_argument("--limit", type=int, default=20)
            if name in {
                "search",
                "symbols",
                "type",
                "signature",
                "callers",
                "register",
                "tests-for",
                "conform",
                "precedent",
                "contract",
            }:
                p.add_argument("name")
            if name in {"projects", "contracts"}:
                p.add_argument("name", nargs="?")
            if name in {"map", "submaps"}:
                p.add_argument("--focus", nargs="+", default=[])
            if name == "map":
                p.add_argument("--budget", type=int, required=True)
            if name == "edges":
                p.add_argument("path", nargs="?")
            if name in {"impact", "fresh"}:
                p.add_argument("paths", nargs="+")
            if name == "callers":
                p.add_argument("--count", action="store_true")
            if name in {"search", "precedent"}:
                p.add_argument("--sift", metavar="QUESTION")
            if name == "precedent":
                p.add_argument("--language")
            if name == "contracts":
                p.add_argument("--register", metavar="FILE")
            if name in {"contract", "contracts"}:
                p.add_argument(
                    "--task",
                    help="consumer task for exact publication permission metadata",
                )
            if name == "journal":
                p.add_argument("--replay", metavar="CHANGESET")
    return parser


def expectation(result, expect):
    if expect is None:
        return True
    from .code_metadata import rows_of

    rows = rows_of(result)
    if expect == "exists":
        return len(rows) > 0
    if expect == "absent":
        return len(rows) == 0
    if expect == "one":
        return len(rows) == 1
    if expect == "clean":
        return isinstance(result, dict) and result.get("clean") is True
    return bool(rows) and all(
        not r.get("stale", True) and not r.get("unanchored", False) for r in rows
    )


def main(argv, root, *, env=None, _emit=print, read_ledger=True):
    parser = _parser()
    args = parser.parse_args(argv)
    root = Path(root)
    try:
        if hasattr(args, "limit") and not 1 <= args.limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        name = getattr(args, "name", None)
        if name is not None and not name.strip():
            raise ValueError("name must not be empty")
        if args.command == "index":
            if args.at:
                from .code_build import index_revision

                if args.paths or args.resolved:
                    raise ValueError(
                        "revision indexing captures a complete bounded commit; --paths/--resolved require a live checkout"
                    )
                result = index_revision(
                    root,
                    args.repo,
                    args.at,
                    max_file_bytes=args.max_file_bytes,
                    trigram=not args.porter_only,
                )
            else:
                result = index_repo(
                    root,
                    args.repo,
                    paths=args.paths,
                    max_file_bytes=args.max_file_bytes,
                    trigram=not args.porter_only,
                    resolve=args.resolved,
                    indexer_checkout=args.indexer_checkout,
                )
        elif args.command == "import":
            from .code_import import import_graph

            if not args.namespace:
                raise ValueError("import requires --namespace")
            result = import_graph(
                root,
                args.namespace,
                args.symbols,
                args.edges,
                repo_path=args.repo,
                extractor=args.extractor,
            )
        elif args.command == "journal":
            from .code_journal import list_changesets, replay

            if args.at:
                raise ValueError(
                    "journal uses changeset identities; --at is for graph revisions"
                )
            result = (
                replay(root, args.replay, repo=args.namespace)
                if args.replay
                else list_changesets(root, repo=args.namespace, limit=args.limit)
            )
        elif args.command == "fresh":
            from .code_freshness import scope

            conn = store.connect(root, existing=True)
            try:
                repos = (
                    conn.execute(
                        "SELECT * FROM repos WHERE (? IS NULL OR repo=?)",
                        (args.namespace, args.namespace),
                    ).fetchall()
                    if conn
                    else []
                )
                if len(repos) != 1:
                    raise ValueError(
                        "fresh requires exactly one namespace; index it or pass --namespace"
                    )
                if args.at:
                    from .code_snapshot import activate_query

                    selected = activate_query(conn, args.at, args.namespace)
                    from .code_freshness import snapshot_scope

                    result = snapshot_scope(conn, selected, args.paths)
                else:
                    result = scope(root, repos[0]["root"], args.paths, local=True)
            finally:
                if conn:
                    conn.close()
        elif args.command == "impact":
            result = impact(
                root, args.paths, repo=args.namespace, limit=args.limit, at=args.at
            )
        else:
            conn = store.connect(
                root, existing=args.command != "contracts" or not args.register
            )
            try:
                if conn is not None and args.at:
                    if getattr(args, "register", None):
                        raise ValueError("--at is read-only")
                    from .code_snapshot import activate_query

                    activate_query(conn, args.at, args.namespace)
                elif conn is not None and not getattr(args, "register", None):
                    from .code_snapshot import activate_latest

                    activate_latest(conn, args.namespace)
                if conn is None:
                    result = []
                elif args.command in {"symbols", "signature", "type"}:
                    result = query.symbols(
                        conn, name, repo=args.namespace, limit=args.limit
                    )
                    if args.command == "type":
                        result = [
                            r
                            for r in result
                            if r["kind"]
                            in {
                                "class",
                                "interface",
                                "type",
                                "struct",
                                "enum",
                                "trait",
                                "data_area",
                                "ddm",
                            }
                        ]
                elif args.command == "search":
                    result = query.search(
                        conn, name, repo=args.namespace, limit=args.limit
                    )
                elif args.command == "callers":
                    result = query.callers(
                        conn,
                        name,
                        repo=args.namespace,
                        limit=args.limit,
                        count=args.count,
                    )
                elif args.command == "edges":
                    result = query.edges(
                        conn, repo=args.namespace, path=args.path, limit=args.limit
                    )
                elif args.command in {"register", "tests-for", "conform", "precedent"}:
                    from . import code_reuse

                    result = code_reuse.run(
                        conn,
                        args.command,
                        name,
                        repo=args.namespace,
                        limit=args.limit,
                        language=getattr(args, "language", None),
                    )
                elif args.command in {"map", "submaps"}:
                    from . import code_analysis

                    result = code_analysis.run(
                        conn,
                        args.command,
                        repo=args.namespace,
                        focus=args.focus,
                        budget=getattr(args, "budget", None),
                        limit=args.limit,
                    )
                else:
                    from . import code_metadata

                    result = code_metadata.run(
                        conn,
                        args.command,
                        name,
                        repo=args.namespace,
                        register=getattr(args, "register", None),
                        root=root,
                        limit=args.limit,
                    )
            finally:
                if conn:
                    conn.close()
            if getattr(args, "sift", None):
                result = sift_results(root, result, args.sift, env)
        if read_ledger and args.command in {"contract", "contracts"}:
            from .code_metadata import join_publications

            result = join_publications(
                root,
                result,
                name,
                repo=args.namespace,
                limit=args.limit,
                task=args.task,
            )
        if getattr(args, "owners", False):
            from .code_metadata import owners

            owners(root, result)
        _emit(json.dumps(result))
        if getattr(args, "expect", None) and not db_path(root).is_file():
            if not (args.command in {"contract", "contracts"} and result):
                return 3
        if getattr(args, "expect", None) == "absent" and expectation(result, "absent"):
            conn = store.connect(root, existing=True)
            try:
                if conn and getattr(args, "at", None):
                    from .code_snapshot import activate_query

                    activate_query(conn, args.at, args.namespace)
                elif conn:
                    from .code_snapshot import activate_latest

                    activate_latest(conn, args.namespace)
                if conn and (
                    conn.snapshot is None
                    or not conn.snapshot["complete"]
                    or conn.execute(
                        "SELECT 1 FROM exclusions WHERE reason='oversize' AND (? IS NULL OR repo=?) LIMIT 1",
                        (args.namespace, args.namespace),
                    ).fetchone()
                ):
                    _emit(
                        "hx: code: absence unverified: incomplete snapshot or excluded oversize bodies; index the complete scope or raise --max-file-bytes",
                        file=sys.stderr,
                    )
                    return 3
                if conn and not getattr(args, "at", None):
                    from .code_freshness import scope

                    for identity in conn.snapshot_maps:
                        record = conn.execute(
                            "SELECT root FROM repos WHERE repo=?", (identity,)
                        ).fetchone()
                        if (
                            record is None
                            or not scope(root, record["root"], ["."], local=True)[
                                "fresh"
                            ]
                        ):
                            _emit(
                                "hx: code: absence unverified: stale live source scope; refresh with hx code index",
                                file=sys.stderr,
                            )
                            return 3
            finally:
                if conn:
                    conn.close()
        return 0 if expectation(result, getattr(args, "expect", None)) else 3
    except (
        ValueError,
        OSError,
        KeyError,
        TypeError,
        sqlite3.Error,
        subprocess.SubprocessError,
        HxError,
    ) as exc:
        _emit(f"hx: code: {exc}", file=sys.stderr)
        return 2


def execute(argv, root, *, env=None, read_ledger=True):
    """Return a command result without process-global stdout redirection."""
    captured = []
    errors = []

    def emit(value, **kwargs):
        if kwargs.get("file") is sys.stderr:
            errors.append(str(value))
        else:
            captured.append(json.loads(value))

    status = main(argv, root, env=env, _emit=emit, read_ledger=read_ledger)
    result = captured[-1] if captured else {"error": "; ".join(errors)}
    return status, result
