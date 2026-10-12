"""Deterministic code search index: symbols, imports and FTS5 body search (stdlib only).

One SQLite database per hx root at ``state/code-index/index.db`` (WAL mode) holding:

  - ``files(path, sha256, size, mtime_ns)`` — one row per indexed file, keyed by
    repo-relative POSIX path. Re-indexing compares the sha256 and only rewrites
    changed files; files that disappeared are deleted from every table.
  - ``symbols(name, qualified, kind, path, line, end_line)`` — Python definitions
    from ``ast`` (qualified ``Class.method`` names with line ranges), other
    languages from line regexes (best effort, ``kind`` labels the match).
  - ``edges(src_path, dst_text, kind='imports')`` — raw import strings; resolvable
    ones surface as candidate graph edges via :func:`graph_edges`.
  - ``code_search`` / ``code_trigram`` — FTS5 tables over ``(path, symbols, body)``;
    the first uses the default tokenizer, the second ``tokenize='trigram'`` so
    partial identifiers (``useEff`` → ``useEffect``) match.

Search merges the two FTS rankings with Reciprocal Rank Fusion (k=60), boosts
exact symbol definitions first (``layer='symbol'``), re-ranks multi-term hits by
term proximity, and falls back to a Levenshtein correction against symbol names
(``layer='fuzzy'``) when nothing matched. All queries are bounded and snippets
are ~300-char windows around the first matched term, never whole bodies.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

MAX_FILE_BYTES = 512 * 1024
EXTENSIONS = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go",
    ".md", ".txt", ".json", ".yaml", ".yml", ".sh",
}
SKIP_DIRS = {".git", ".venv", "node_modules", "__pycache__", "dist", "build"}
RRF_K = 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS files(
    path TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
    size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS symbols(
    name TEXT NOT NULL, qualified TEXT NOT NULL, kind TEXT NOT NULL,
    path TEXT NOT NULL, line INTEGER NOT NULL, end_line INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
CREATE INDEX IF NOT EXISTS idx_symbols_qualified ON symbols(qualified);
CREATE INDEX IF NOT EXISTS idx_symbols_path ON symbols(path);
CREATE TABLE IF NOT EXISTS edges(
    src_path TEXT NOT NULL, dst_text TEXT NOT NULL, kind TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_edges_src ON edges(src_path);
CREATE VIRTUAL TABLE IF NOT EXISTS code_search USING fts5(path, symbols, body);
CREATE VIRTUAL TABLE IF NOT EXISTS code_trigram
    USING fts5(path, symbols, body, tokenize='trigram');
"""


def db_path(root) -> Path:
    return Path(root) / "state" / "code-index" / "index.db"


def _connect(root) -> sqlite3.Connection:
    path = db_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    return conn


def _open_existing(root) -> sqlite3.Connection | None:
    if not db_path(root).is_file():
        return None
    conn = sqlite3.connect(str(db_path(root)))
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        conn.executescript(_SCHEMA)
    except sqlite3.Error:
        pass
    return conn


# --- file discovery (.gitignore-lite) ------------------------------------------------


def _gitignore_patterns(repo: Path) -> list[str]:
    patterns: list[str] = []
    try:
        text = (repo / ".gitignore").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return patterns
    for raw in text.splitlines():
        line = raw.strip()
        if line and not line.startswith("#") and not line.startswith("!"):
            patterns.append(line)
    return patterns


def _ignored(rel: str, patterns: list[str]) -> bool:
    parts = rel.split("/")
    for pat in patterns:
        p = pat.strip("/")
        if not p:
            continue
        if "/" in p:
            if fnmatch.fnmatch(rel, p) or fnmatch.fnmatch(rel, p + "/*"):
                return True
        elif any(fnmatch.fnmatch(part, p) for part in parts) or fnmatch.fnmatch(rel, p):
            return True
    return False


def _iter_files(repo: Path, max_files: int) -> list[Path]:
    patterns = _gitignore_patterns(repo)
    found: list[Path] = []
    for dirpath, dirnames, filenames in __import__("os").walk(repo):
        rel_dir = Path(dirpath).relative_to(repo)
        kept = []
        for d in sorted(dirnames):
            if d in SKIP_DIRS:
                continue
            if d.startswith(".") and d != ".hx":
                continue
            rel = (rel_dir / d).as_posix() if str(rel_dir) != "." else d
            if _ignored(rel, patterns):
                continue
            kept.append(d)
        dirnames[:] = kept
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if p.suffix.lower() not in EXTENSIONS:
                continue
            rel = p.relative_to(repo).as_posix()
            if _ignored(rel, patterns):
                continue
            found.append(p)
            if len(found) >= max_files:
                found.sort(key=lambda x: x.relative_to(repo).as_posix())
                return found
    found.sort(key=lambda x: x.relative_to(repo).as_posix())
    return found


# --- symbol / import extraction -------------------------------------------------------

_PY_KIND = {ast.ClassDef: "class"}


def _python_symbols(text: str, rel: str) -> list[tuple]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _regex_symbols(text, rel)
    out: list[tuple] = []

    def visit(node, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                kind = "class" if isinstance(child, ast.ClassDef) else "function"
                qualified = f"{prefix}.{child.name}" if prefix else child.name
                out.append((child.name, qualified, kind, rel,
                            child.lineno, getattr(child, "end_lineno", None) or child.lineno))
                visit(child, qualified)
            else:
                visit(child, prefix)

    visit(tree, "")
    return out


def _python_imports(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            out.append(("." * node.level + mod) if mod else "." * node.level)
    return [o for o in out if o and o != "."]


_REGEX_SYMBOLS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"^\s*(?:export\s+)?(?:default\s+)?class\s+([A-Za-z_$][\w$]*)"), "class"),
    (re.compile(r"^\s*(?:export\s+)?interface\s+([A-Za-z_$][\w$]*)"), "class"),
    (re.compile(r"^\s*(?:export\s+)?type\s+([A-Za-z_$][\w$]*)\s*[=<]"), "class"),
    (re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:struct|enum|trait)\s+([A-Za-z_][\w]*)"), "class"),
    (re.compile(r"^\s*(?:export\s+)?(?:async\s+)?function\s*\*?\s*([A-Za-z_$][\w$]*)"), "function"),
    (re.compile(r"^\s*(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>"), "function"),
    (re.compile(r"^\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_][\w]*)"), "function"),
    (re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?([A-Za-z_][\w]*)"), "function"),
    (re.compile(r"^\s*def\s+([A-Za-z_][\w]*)"), "function"),
    (re.compile(r"^\s*(?:async\s+)?def\s+([A-Za-z_][\w]*)"), "function"),
]


def _regex_symbols(text: str, rel: str) -> list[tuple]:
    out: list[tuple] = []
    seen: set[tuple] = set()
    for lineno, line in enumerate(text.splitlines(), 1):
        for pat, kind in _REGEX_SYMBOLS:
            m = pat.match(line)
            if m:
                name = m.group(1)
                if name in {"if", "for", "while", "switch", "catch", "return"}:
                    break
                key = (name, lineno)
                if key not in seen:
                    seen.add(key)
                    out.append((name, name, kind, rel, lineno, lineno))
                break
    return out


_JS_IMPORT_RE = re.compile(r"""import\s+(?:[^'"]*?\s+from\s+)?['"]([^'"]+)['"]""")
_JS_EXPORT_RE = re.compile(r"""export\s+[^'"]*?\s+from\s+['"]([^'"]+)['"]""")
_JS_REQUIRE_RE = re.compile(r"""require\s*\(\s*['"]([^'"]+)['"]\s*\)""")
_GO_BLOCK_RE = re.compile(r"import\s*\(([^)]*)\)", re.S)
_GO_SINGLE_RE = re.compile(r'^\s*import\s+(?:[\w.]+\s+)?"([^"]+)"', re.M)
_GO_QUOTED_RE = re.compile(r'"([^"]+)"')


def _other_imports(text: str, suffix: str) -> list[str]:
    out: list[str] = []
    if suffix in {".ts", ".tsx", ".js", ".jsx"}:
        out += _JS_IMPORT_RE.findall(text)
        out += _JS_EXPORT_RE.findall(text)
        out += _JS_REQUIRE_RE.findall(text)
    elif suffix == ".go":
        for block in _GO_BLOCK_RE.findall(text):
            out += _GO_QUOTED_RE.findall(block)
        stripped = _GO_BLOCK_RE.sub("", text)
        out += _GO_SINGLE_RE.findall(stripped)
    return out


def _extract(text: str, rel: str, suffix: str) -> tuple[list[tuple], list[str]]:
    if suffix == ".py":
        return _python_symbols(text, rel), _python_imports(text)
    symbols = _regex_symbols(text, rel)
    return symbols, _other_imports(text, suffix)


# --- indexing -------------------------------------------------------------------------


def index_repo(root, repo_path, *, max_files: int = 2000) -> dict:
    root, repo = Path(root), Path(repo_path)
    conn = _connect(root)
    try:
        existing = {r[0]: r[1] for r in conn.execute("SELECT path, sha256 FROM files")}
        current: dict[str, Path] = {}
        for p in _iter_files(repo, max_files):
            current[p.relative_to(repo).as_posix()] = p

        for gone in sorted(set(existing) - set(current)):
            conn.execute("DELETE FROM files WHERE path=?", (gone,))
            conn.execute("DELETE FROM symbols WHERE path=?", (gone,))
            conn.execute("DELETE FROM edges WHERE src_path=?", (gone,))
            conn.execute("DELETE FROM code_search WHERE path=?", (gone,))
            conn.execute("DELETE FROM code_trigram WHERE path=?", (gone,))

        for rel in sorted(current):
            p = current[rel]
            try:
                data = p.read_bytes()
                stat = p.stat()
            except OSError:
                continue
            if len(data) > MAX_FILE_BYTES:
                continue
            sha = hashlib.sha256(data).hexdigest()
            if existing.get(rel) == sha:
                continue
            text = data.decode("utf-8", errors="replace")
            symbols, imports = _extract(text, rel, p.suffix.lower())
            conn.execute("DELETE FROM symbols WHERE path=?", (rel,))
            conn.execute("DELETE FROM edges WHERE src_path=?", (rel,))
            conn.execute("DELETE FROM code_search WHERE path=?", (rel,))
            conn.execute("DELETE FROM code_trigram WHERE path=?", (rel,))
            conn.execute(
                "INSERT OR REPLACE INTO files(path, sha256, size, mtime_ns) VALUES (?,?,?,?)",
                (rel, sha, len(data), stat.st_mtime_ns))
            conn.executemany(
                "INSERT INTO symbols(name, qualified, kind, path, line, end_line)"
                " VALUES (?,?,?,?,?,?)", symbols)
            conn.executemany(
                "INSERT INTO edges(src_path, dst_text, kind) VALUES (?,?, 'imports')",
                [(rel, d) for d in dict.fromkeys(imports)])
            sym_text = " ".join(q for _, q, *_ in symbols)
            conn.execute("INSERT INTO code_search(path, symbols, body) VALUES (?,?,?)",
                         (rel, sym_text, text))
            conn.execute("INSERT INTO code_trigram(path, symbols, body) VALUES (?,?,?)",
                         (rel, sym_text, text))
        conn.commit()
        files = conn.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        symbols = conn.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        return {"files": files, "symbols": symbols}
    finally:
        conn.close()


# --- search ---------------------------------------------------------------------------

_TERM_RE = re.compile(r"[A-Za-z0-9_]+")


def _levenshtein(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 2 and max(len(a), len(b)) > 12:
        pass  # still compute; threshold applied by caller
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _body_for(conn, path: str) -> str:
    try:
        row = conn.execute("SELECT body FROM code_search WHERE path=? LIMIT 1", (path,)).fetchone()
    except sqlite3.Error:
        row = None
    return row[0] if row else ""


def _snippet(body: str, terms: list[str]) -> str:
    if not body:
        return ""
    lower = body.lower()
    pos = -1
    for term in terms:
        idx = lower.find(term.lower())
        if idx != -1 and (pos == -1 or idx < pos):
            pos = idx
    if pos == -1:
        snippet = body[:300]
    else:
        start = max(0, pos - 100)
        snippet = body[start:start + 300]
    return re.sub(r"\s+", " ", snippet).strip()


def _fts_paths(conn, table: str, terms: list[str], fetch: int) -> list[str]:
    if not terms:
        return []
    query = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
    try:
        rows = conn.execute(
            f"SELECT path FROM {table} WHERE {table} MATCH ? ORDER BY bm25({table}) LIMIT ?",
            (query, fetch)).fetchall()
    except sqlite3.Error:
        return []
    return [r[0] for r in rows]


def _search_core(conn, query: str, limit: int) -> list[dict]:
    terms = _TERM_RE.findall(query)
    if not terms or limit <= 0:
        return []
    fetch = max(limit * 4, 50)

    # Exact symbol matches (boosted first).
    q = query.strip()
    try:
        sym_rows = conn.execute(
            "SELECT name, qualified, kind, path, line, end_line FROM symbols"
            " WHERE name=? OR qualified=? ORDER BY path, line LIMIT 100",
            (q, q)).fetchall()
        if not sym_rows and len(terms) == 1:
            sym_rows = conn.execute(
                "SELECT name, qualified, kind, path, line, end_line FROM symbols"
                " WHERE name=? ORDER BY path, line LIMIT 100", (terms[0],)).fetchall()
    except sqlite3.Error:
        sym_rows = []
    symbol_by_path: dict[str, tuple] = {}
    for row in sym_rows:
        symbol_by_path.setdefault(row[3], row)

    porter = _fts_paths(conn, "code_search", terms, fetch)
    trigram = _fts_paths(conn, "code_trigram", terms, fetch)

    rrf: dict[str, float] = {}
    for ranking in (porter, trigram):
        for rank, path in enumerate(ranking, 1):
            rrf[path] = rrf.get(path, 0.0) + 1.0 / (RRF_K + rank)
    for path in symbol_by_path:
        rrf.setdefault(path, 0.0)
    if not rrf:
        return []

    bodies = {p: _body_for(conn, p) for p in rrf}
    scores = dict(rrf)
    if len(terms) >= 2:
        for path, body in bodies.items():
            lower = body.lower()
            positions = [lower.find(t.lower()) for t in terms]
            if all(p != -1 for p in positions):
                spread = max(positions) - min(positions)
                boost = 0.5 if spread <= 100 else 0.25 if spread <= 300 else 0.1 if spread <= 1000 else 0.0
                scores[path] = scores[path] * (1.0 + boost) if scores[path] else boost

    results: list[dict] = []
    for path, score in scores.items():
        is_symbol = path in symbol_by_path
        results.append({
            "path": path,
            "title": symbol_by_path[path][1] if is_symbol else Path(path).name,
            "snippet": _snippet(bodies.get(path, ""), terms),
            "score": float(score + (10.0 if is_symbol else 0.0)),
            "layer": "symbol" if is_symbol else "fts",
            "_sym": is_symbol,
        })
    results.sort(key=lambda r: (not r["_sym"], -r["score"], r["path"]))
    for r in results:
        r.pop("_sym", None)
    return results[:limit]


def search(root, query, *, limit: int = 10) -> list[dict]:
    conn = _open_existing(root)
    if conn is None:
        return []
    try:
        results = _search_core(conn, query, limit)
        if results:
            return results
        # Fuzzy fallback: correct long-enough terms against symbol names.
        terms = _TERM_RE.findall(query or "")
        long_terms = [t for t in terms if len(t) >= 5]
        if not long_terms:
            return []
        try:
            names = [r[0] for r in conn.execute(
                "SELECT DISTINCT name FROM symbols LIMIT 5000").fetchall()]
        except sqlite3.Error:
            return []
        corrected = query
        changed = False
        for term in long_terms:
            max_dist = 2 if len(term) <= 12 else 3
            best, best_dist = None, max_dist + 1
            for name in names:
                if name == term:
                    best = None
                    break
                d = _levenshtein(term.lower(), name.lower())
                if d < best_dist:
                    best, best_dist = name, d
            else:
                if best is not None and best_dist <= max_dist:
                    corrected = corrected.replace(term, best)
                    changed = True
                continue
            # exact name exists; no correction for this term
        if not changed:
            return []
        results = _search_core(conn, corrected, limit)
        for r in results:
            r["layer"] = "fuzzy"
        return results
    finally:
        conn.close()


def search_sifted(root, query, question, *, limit: int = 10, env=None) -> list[dict]:
    """Deterministic code search, then Jev sifting over its candidates.

    The index nominates; Jev ranks semantically. Every deterministic hit is
    returned with its ``jev_score`` (null when unsifted) — the model never
    drops a fact the index found.
    """
    results = search(root, query, limit=limit)
    if not results:
        return results
    from . import search_sift
    sifted = search_sift.sift(root, question,
                              [{"id": r["path"], "snippet": r.get("snippet", "")}
                               for r in results], env=env)
    scores = {row["id"]: row.get("jev_score") for row in sifted.get("results", [])}
    for row in results:
        row["jev_score"] = scores.get(row["path"])
    results.sort(key=lambda r: (-(r["jev_score"] or 0), -r["score"]))
    return results


# --- symbol / graph queries -------------------------------------------------------------


def symbols_for(root, name) -> list[dict]:
    conn = _open_existing(root)
    if conn is None:
        return []
    try:
        rows = conn.execute(
            "SELECT name, qualified, kind, path, line, end_line FROM symbols"
            " WHERE name=? OR qualified=? ORDER BY path, line LIMIT 100",
            (name, name)).fetchall()
        return [{"name": r[0], "qualified": r[1], "kind": r[2],
                 "path": r[3], "line": r[4], "end_line": r[5]} for r in rows]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def callers(root, symbol) -> list[dict]:
    conn = _open_existing(root)
    if conn is None:
        return []
    try:
        defining = {r[0] for r in conn.execute(
            "SELECT DISTINCT path FROM symbols WHERE name=? OR qualified=? LIMIT 100",
            (symbol, symbol)).fetchall()}
        query = '"' + symbol.replace('"', '""') + '"'
        try:
            rows = conn.execute(
                "SELECT path FROM code_trigram WHERE code_trigram MATCH ?"
                " ORDER BY bm25(code_trigram) LIMIT 100", (query,)).fetchall()
            paths = [r[0] for r in rows]
        except sqlite3.Error:
            paths = []
        # Fallback scan-free safety: trigram needs >=3 chars; short symbols use LIKE-free
        # body scan over the porter table only when trigram returned nothing.
        if not paths and len(symbol) < 3:
            paths = [r[0] for r in conn.execute(
                "SELECT path FROM code_search LIMIT 1000").fetchall()
                if symbol in _body_for(conn, r[0])]
        return [{"path": p} for p in sorted(set(paths) - defining)]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def impact(root, changed_paths) -> dict:
    changed = [str(p) for p in changed_paths]
    conn = _open_existing(root)
    if conn is None:
        empty: list = []
        return {"symbols": empty, "symbol_names": [], "referencing_files": [],
                "referencing": [], "candidate_tests": [], "candidate_test_files": [],
                "test_files": []}
    try:
        symbols: list[dict] = []
        if changed:
            placeholders = ",".join("?" for _ in changed)
            try:
                rows = conn.execute(
                    f"SELECT name, qualified, kind, path, line, end_line FROM symbols"
                    f" WHERE path IN ({placeholders}) ORDER BY path, line LIMIT 500",
                    changed).fetchall()
            except sqlite3.Error:
                rows = []
            symbols = [{"name": r[0], "qualified": r[1], "kind": r[2],
                        "path": r[3], "line": r[4], "end_line": r[5]} for r in rows]
    finally:
        conn.close()

    referencing: set[str] = set()
    for sym in symbols:
        for name in {sym["name"], sym["qualified"]}:
            for caller in callers(root, name):
                referencing.add(caller["path"])
    referencing -= set(changed)
    referencing_sorted = sorted(referencing)
    tests = [p for p in referencing_sorted
             if "test" in p.lower() or "spec" in p.lower()]
    return {"symbols": symbols,
            "symbol_names": sorted({s["name"] for s in symbols}),
            "referencing_files": referencing_sorted,
            "referencing": referencing_sorted,
            "candidate_tests": tests,
            "candidate_test_files": tests,
            "test_files": tests}


def graph_edges(root) -> list[dict]:
    conn = _open_existing(root)
    if conn is None:
        return []
    try:
        indexed = [r[0] for r in conn.execute("SELECT path FROM files").fetchall()]
        edges = conn.execute(
            "SELECT src_path, dst_text FROM edges WHERE kind='imports'"
            " ORDER BY src_path, dst_text LIMIT 10000").fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    indexed_set = set(indexed)
    by_stem: dict[str, list[str]] = {}
    by_module: dict[str, list[str]] = {}
    for p in indexed:
        stem = p.rsplit(".", 1)[0]
        by_stem.setdefault(Path(p).stem, []).append(p)
        by_module.setdefault(stem.replace("/", "."), []).append(p)
        by_module.setdefault(stem, []).append(p)

    def resolve(src: str, dst: str) -> str | None:
        if dst in indexed_set:
            return dst
        # Relative JS/TS imports: ./x, ../x relative to the importing file.
        if dst.startswith("."):
            base = Path(src).parent
            joined = (base / dst) if str(base) != "." else Path(dst)
            # Normalise ./ and ../ segments lexically.
            parts: list[str] = []
            for seg in joined.as_posix().split("/"):
                if seg in {"", "."}:
                    continue
                if seg == "..":
                    if parts:
                        parts.pop()
                else:
                    parts.append(seg)
            norm = "/".join(parts)
            for cand in [norm] + [f"{norm}{e}" for e in sorted(EXTENSIONS)] + \
                    [f"{norm}/index{e}" for e in sorted(EXTENSIONS)]:
                if cand in indexed_set:
                    return cand
            dst = norm
        dotted = dst.replace(".", "/")
        for cand in [dst, dotted] + [f"{dst}{e}" for e in sorted(EXTENSIONS)] + \
                [f"{dotted}{e}" for e in sorted(EXTENSIONS)]:
            if cand in indexed_set:
                return cand
        if dst in by_module and by_module[dst]:
            return sorted(by_module[dst])[0]
        if dotted in by_module and by_module[dotted]:
            return sorted(by_module[dotted])[0]
        last = dst.replace(".", "/").rstrip("/").split("/")[-1]
        if last in by_stem and by_stem[last]:
            return sorted(by_stem[last])[0]
        return None

    out: list[dict] = []
    seen: set[tuple] = set()
    for src, dst in edges:
        target = resolve(src, dst)
        if target and target != src and (src, target) not in seen:
            seen.add((src, target))
            out.append({"from_path": src, "to_path": target,
                        "kind": "imports", "status": "candidate"})
    out.sort(key=lambda e: (e["from_path"], e["to_path"]))
    return out


# --- CLI ----------------------------------------------------------------------------------


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx code-index", add_help=True)
    parser.add_argument("--root", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    p_index = sub.add_parser("index", help="index a repository")
    p_index.add_argument("--repo", required=True)
    p_index.add_argument("--root", help=argparse.SUPPRESS)

    p_search = sub.add_parser("search", help="search the code index")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=10)
    p_search.add_argument("--sift", metavar="QUESTION",
                          help="Jev semantic sifting over the deterministic candidates")
    p_search.add_argument("--root", help=argparse.SUPPRESS)

    p_symbols = sub.add_parser("symbols", help="find symbol definitions")
    p_symbols.add_argument("name")
    p_symbols.add_argument("--root", help=argparse.SUPPRESS)

    p_impact = sub.add_parser("impact", help="impact of changed paths")
    p_impact.add_argument("paths", nargs="+")
    p_impact.add_argument("--root", help=argparse.SUPPRESS)

    p_edges = sub.add_parser("edges", help="candidate import graph edges for the application map")
    p_edges.add_argument("--root", help=argparse.SUPPRESS)

    args = parser.parse_args(argv)
    root = Path(root)
    try:
        if args.command == "index":
            result = index_repo(root, Path(args.repo))
        elif args.command == "search":
            result = (search_sifted(root, args.query, args.sift, limit=args.limit, env=env)
                      if args.sift else search(root, args.query, limit=args.limit))
        elif args.command == "symbols":
            result = symbols_for(root, args.name)
        elif args.command == "edges":
            result = graph_edges(root)
        else:
            result = impact(root, args.paths)
        print(json.dumps(result))
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"hx: code-index: {exc}", file=sys.stderr)
        return 2
