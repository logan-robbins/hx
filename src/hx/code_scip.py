"""Optional batch SCIP ingestion. Indexers run only in an explicit isolated checkout."""

from __future__ import annotations
import json
import os
import re
from pathlib import Path
from . import code_store as store
from .code_build import insert_symbol, insert_ref, insert_edge


def field(row, snake, default=None):
    camel = re.sub(r"_([a-z])", lambda m: m[1].upper(), snake)
    return row.get(snake, row.get(camel, default))


def _name(symbol):
    parts = re.findall(r"(?:`([^`]+)`|([\w$]+))[#.(\[]", symbol.split(" ")[-1])
    return next((a or b for a, b in reversed(parts)), symbol.rsplit(" ", 1)[-1])


def source_range(occurrence, *, enclosing=False):
    suffix = "_enclosing_range" if enclosing else "_range"
    single = field(occurrence, "single_line" + suffix)
    multi = field(occurrence, "multi_line" + suffix)
    if single:
        return [
            field(single, "line"),
            field(single, "start_character"),
            field(single, "end_character"),
        ]
    if multi:
        return [
            field(multi, "start_line"),
            field(multi, "start_character"),
            field(multi, "end_line"),
            field(multi, "end_character"),
        ]
    return field(occurrence, "enclosing_range" if enclosing else "range", [])


def kind_name(value):
    kinds = {
        7: "class",
        17: "function",
        21: "interface",
        26: "method",
        30: "namespace",
        41: "property",
        49: "struct",
        53: "trait",
        54: "type",
        61: "variable",
    }
    return kinds.get(value, str(value).removeprefix("Kind_").lower())


def _ingest(conn, identity, data, *, extractor="scip"):
    from .code_import import relative

    if not isinstance(data, dict) or not isinstance(data.get("documents"), list):
        raise ValueError("SCIP JSON requires documents")
    documents = data["documents"]
    mapping = {}
    definitions = []
    for document in documents:
        path = relative(field(document, "relative_path"))
        file = conn.execute(
            "SELECT body FROM files WHERE repo=? AND path=?", (identity, path)
        ).fetchone()
        if not file:
            continue
        info = {s["symbol"]: s for s in document.get("symbols", [])}
        for occurrence in document.get("occurrences", []):
            if not int(field(occurrence, "symbol_roles", 0)) & 1:
                continue
            location = source_range(occurrence)
            if len(location) not in {3, 4}:
                raise ValueError("invalid SCIP range")
            line = int(location[0]) + 1
            symbol = occurrence["symbol"]
            details = info.get(symbol, {})
            name = field(details, "display_name") or _name(symbol)
            if symbol.startswith("local "):
                source_line = file[0].splitlines()[line - 1]
                name = source_line[int(location[1]) : int(location[-1])] or name
            syntax = conn.execute(
                "SELECT * FROM symbols WHERE repo=? AND path=? AND lane='syntax' AND line<=? AND end_line>=? ORDER BY end_line-line,line",
                (identity, path, line, line),
            ).fetchall()
            matches = [dict(r) for r in syntax if r["name"] == name]
            located = [
                r
                for r in matches
                if r["line"] < line or r["column"] <= int(location[1])
            ]
            exact = (
                max(located, key=lambda r: (r["line"], r["column"]))
                if located
                else None
            )
            if exact:
                row = exact
                row["attributes"] = json.loads(row["attributes"])
            else:
                enclosure = source_range(occurrence, enclosing=True) or location
                line = int(enclosure[0]) + 1
                end_line = int(enclosure[-2]) + 1 if len(enclosure) == 4 else line
                if len(enclosure) == 4 and int(enclosure[-1]) == 0:
                    end_line -= 1
                text = file[0]
                lines = text.splitlines()
                if not 1 <= line <= end_line <= len(lines):
                    raise ValueError("SCIP definition range outside source")
                row = dict(
                    name=name,
                    qualified=name + "@" + symbol
                    if symbol.startswith("local ")
                    else name,
                    kind=kind_name(details.get("kind", "symbol")),
                    path=path,
                    line=line,
                    end_line=end_line,
                    signature=lines[line - 1].strip(),
                    attributes=[],
                    anchor_sha=store.span_sha(text, line, end_line),
                )
            signature = field(details, "signature_documentation")
            if isinstance(signature, dict) and signature.get("text"):
                row["signature"] = signature["text"]
            row.update(lane="resolved", extractor=extractor)
            mapping[(path, symbol)] = row
            if not symbol.startswith("local "):
                mapping[(None, symbol)] = row
            definitions.append((symbol, details, row))
    for table in ("symbols", "refs", "edges"):
        conn.execute(
            f"DELETE FROM {table} WHERE repo=? AND lane='resolved' AND extractor=?",
            (identity, extractor),
        )
    for symbol, details, row in definitions:
        parent = field(details, "enclosing_symbol")
        parent_row = mapping.get((row["path"], parent)) or mapping.get((None, parent))
        if parent_row:
            row["qualified"] = parent_row["qualified"] + "." + row["name"]
        insert_symbol(conn, identity, row)
        for relationship in details.get("relationships", []):
            if not field(relationship, "is_implementation", False):
                continue
            target = mapping.get((None, relationship["symbol"]))
            insert_edge(
                conn,
                identity,
                row["path"],
                target["qualified"] if target else _name(relationship["symbol"]),
                "implements",
                lane="resolved",
                extractor=extractor,
                anchor=row["anchor_sha"],
                evidence={"symbol": symbol, "target": relationship["symbol"]},
            )
    references = 0
    for document in documents:
        path = field(document, "relative_path")
        file = conn.execute(
            "SELECT sha256 FROM files WHERE repo=? AND path=?", (identity, path)
        ).fetchone()
        if not file:
            continue
        for occurrence in document.get("occurrences", []):
            if int(field(occurrence, "symbol_roles", 0)) & 1 or not occurrence.get(
                "symbol"
            ):
                continue
            target = mapping.get((path, occurrence["symbol"])) or mapping.get(
                (None, occurrence["symbol"])
            )
            if target:
                name, qualified = target["name"], target["qualified"]
            else:
                name = qualified = _name(occurrence["symbol"])
            insert_ref(
                conn,
                identity,
                dict(
                    name=name,
                    qualified=qualified,
                    path=path,
                    line=int(source_range(occurrence)[0]) + 1,
                    kind="calls",
                ),
                lane="resolved",
                extractor=extractor,
                anchor=file[0],
            )
            references += 1
    return dict(
        status="indexed",
        symbols=len(definitions),
        references=references,
        extractor=extractor,
    )


def ingest(conn, identity, data, *, extractor="scip"):
    conn.execute("SAVEPOINT scip_ingestion")
    try:
        result = _ingest(conn, identity, data, extractor=extractor)
        conn.execute("RELEASE scip_ingestion")
        conn.symbol_cache.clear()
        conn.source_cache.clear()
        return result
    except Exception:
        conn.execute("ROLLBACK TO scip_ingestion")
        conn.execute("RELEASE scip_ingestion")
        raise


def index(conn, identity, repo, checkout):
    from .code_adapters import detect, run

    detected = detect()
    binaries = {name: path for name, path in detected["indexers"].items() if path}
    if not binaries or not detected["scip"]:
        return dict(status="skipped", reason="SCIP CLI or language indexer absent")
    if not checkout:
        return dict(
            status="skipped",
            reason="resolved indexing requires --indexer-checkout outside the product checkout",
        )
    target = Path(checkout).resolve()
    repo = Path(repo).resolve()
    if target == repo or target.is_relative_to(repo) or repo.is_relative_to(target):
        raise ValueError(
            "indexer checkout must be separate from product and worker checkouts"
        )
    head = store.git(repo, "rev-parse", "HEAD")
    if (
        not head
        or store.git(target, "rev-parse", "HEAD") != head
        or store.git(target, "status", "--porcelain")
        or store.git(repo, "status", "--porcelain")
    ):
        raise ValueError("indexer checkout must be clean at the same committed HEAD")
    commands = json.loads(os.environ.get("HX_SCIP_COMMANDS", "{}"))
    suffixes = {
        Path(r[0]).suffix
        for r in conn.execute("SELECT path FROM files WHERE repo=?", (identity,))
    }
    defaults = {
        ".py": ("scip-python", ["index", "--project-name", "hx"]),
        ".ts": ("scip-typescript", ["index"]),
        ".js": ("scip-typescript", ["index"]),
        ".go": ("scip-go", []),
        ".cs": ("scip-dotnet", ["index"]),
        ".java": ("scip-java", ["index"]),
        ".c": ("scip-clang", []),
        ".cpp": ("scip-clang", []),
        ".rb": ("scip-ruby", ["index"]),
        ".rs": ("rust-analyzer", ["scip", "."]),
    }
    selected = sorted(
        {
            defaults[s][0]
            for s in suffixes
            if s in defaults and defaults[s][0] in binaries
        }
    )
    results = []
    for binary in selected:
        args = commands.get(
            binary, next(v[1] for v in defaults.values() if v[0] == binary)
        )
        if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
            raise ValueError("HX_SCIP_COMMANDS values must be argv lists")
        output = target / "index.scip"
        if output.exists():
            raise ValueError(
                "indexer checkout already contains index.scip; refuse overwrite"
            )
        try:
            run(
                [binaries[binary], *args],
                cwd=target,
                timeout=600,
                maximum=32 * 1024 * 1024,
            )
            data = json.loads(
                run(
                    [detected["scip"], "print", "--json", str(output)],
                    cwd=target,
                    timeout=120,
                    maximum=64 * 1024 * 1024,
                )
            )
            if store.git(target, "rev-parse", "HEAD") != head or store.git(
                target, "diff", "--name-only", "HEAD"
            ):
                raise ValueError("indexer changed committed source")
            results.append(ingest(conn, identity, data, extractor=binary))
        except (OSError, ValueError) as exc:
            results.append(dict(status="degraded", extractor=binary, reason=str(exc)))
        finally:
            output.unlink(missing_ok=True)
    return dict(
        status="indexed"
        if any(r["status"] == "indexed" for r in results)
        else "skipped",
        results=results,
    )
