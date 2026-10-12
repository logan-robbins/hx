"""General persistent knowledge store: chunk, index, and full-text search local or fetched content.

Standalone SQLite (WAL) at ``<root>/state/content/index.db`` with two FTS5 indexes over the
same chunks — a porter-stemmed one for word search and a trigram one for substring search —
merged at query time with reciprocal rank fusion. Stdlib only.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

MAX_CHUNK_BYTES = 4096
MAX_FILES = 200
MAX_DEPTH = 5
CODE_EXTS = {".md", ".mdx", ".txt", ".ts", ".tsx", ".js", ".jsx", ".py", ".rs", ".go", ".sh",
             ".json", ".yaml", ".yml"}
MARKDOWN_EXTS = {".md", ".mdx", ".txt"}
SKIP_DIRS = {"node_modules", ".git", "dist", "build", ".venv", "__pycache__"}
BLOCKED_V4 = ["169.254.0.0/16", "224.0.0.0/4", "0.0.0.0/8"]
BLOCKED_V6 = ["ff00::/8", "fe80::/10"]


def db_path(root) -> Path:
    return Path(root) / "state" / "content" / "index.db"


def _connect(root) -> sqlite3.Connection:
    path = db_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sources(
            id INTEGER PRIMARY KEY, label TEXT UNIQUE NOT NULL, hash TEXT,
            indexed_at REAL, chunk_count INTEGER DEFAULT 0,
            file_path TEXT, file_hash TEXT, url TEXT);
        CREATE TABLE IF NOT EXISTS chunks(
            id INTEGER PRIMARY KEY, title TEXT, content TEXT, content_type TEXT,
            has_code INTEGER DEFAULT 0,
            source_id INTEGER REFERENCES sources(id) ON DELETE CASCADE);
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts
            USING fts5(title, content, tokenize='porter unicode61');
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_trigram
            USING fts5(title, content, tokenize='trigram');
    """)
    return conn


# --- chunking -------------------------------------------------------------------------------

def _fenced_ratio(text: str) -> float:
    total = len(text)
    if not total:
        return 0.0
    fenced, in_fence = 0, False
    for line in text.splitlines(keepends=True):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            fenced += len(line)
        elif in_fence:
            fenced += len(line)
    return fenced / total


def _make_chunk(title: str, content: str) -> dict:
    content = content.strip("\n")
    has_code = "```" in content
    ctype = "code" if _fenced_ratio(content) > 0.5 else "prose"
    return {"title": title, "content": content, "has_code": has_code, "content_type": ctype}


def _split_oversized(title: str, content: str) -> list[dict]:
    if len(content.encode()) <= MAX_CHUNK_BYTES:
        return [_make_chunk(title, content)] if content.strip() else []
    out, buf = [], ""
    for para in re.split(r"\n\s*\n", content):
        piece = para if not buf else buf + "\n\n" + para
        if len(piece.encode()) <= MAX_CHUNK_BYTES:
            buf = piece
            continue
        if buf:
            out.append(_make_chunk(title, buf))
            buf = ""
        if len(para.encode()) <= MAX_CHUNK_BYTES:
            buf = para
        else:  # single huge paragraph: hard-split on line boundaries, then bytes
            for line in para.splitlines(keepends=True):
                if len((buf + line).encode()) > MAX_CHUNK_BYTES and buf:
                    out.append(_make_chunk(title, buf))
                    buf = ""
                while len(line.encode()) > MAX_CHUNK_BYTES:
                    cut = MAX_CHUNK_BYTES
                    while cut > 0 and (line.encode()[cut:cut + 1] and
                                       (line.encode()[cut] & 0xC0) == 0x80):
                        cut -= 1
                    out.append(_make_chunk(title, line.encode()[:cut].decode(errors="ignore")))
                    line = line.encode()[cut:].decode(errors="ignore")
                buf += line
    if buf.strip():
        out.append(_make_chunk(title, buf))
    return out


def chunk_markdown(text: str) -> list[dict]:
    """Split by ATX headings; never split inside a fenced code block."""
    sections: list[tuple[str, list[str]]] = []
    title, lines, in_fence = "", [], False

    def flush():
        if lines or title:
            sections.append((title, list(lines)))

    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("```"):
            in_fence = not in_fence
            lines.append(line)
            continue
        m = None if in_fence else re.match(r"^(#{1,6})\s+(.*?)\s*#*\s*$", line)
        if m:
            flush()
            title, lines = m.group(2).strip(), [line]
        else:
            lines.append(line)
    flush()
    chunks: list[dict] = []
    for sec_title, sec_lines in sections:
        chunks.extend(_split_oversized(sec_title, "\n".join(sec_lines)))
    return chunks


def _chunk_code(text: str, filename: str = "") -> list[dict]:
    chunks: list[dict] = []
    buf = ""
    for para in re.split(r"\n\s*\n", text):
        piece = para if not buf else buf + "\n\n" + para
        if len(piece.encode()) <= MAX_CHUNK_BYTES:
            buf = piece
            continue
        if buf.strip():
            first = next((ln.strip() for ln in buf.splitlines() if ln.strip()), filename)
            chunks.append({"title": first[:80], "content": buf.strip("\n"),
                           "has_code": True, "content_type": "code"})
        buf = para
        while len(buf.encode()) > MAX_CHUNK_BYTES:
            chunks.append({"title": filename, "content": buf.encode()[:MAX_CHUNK_BYTES].decode(errors="ignore"),
                           "has_code": True, "content_type": "code"})
            buf = buf.encode()[MAX_CHUNK_BYTES:].decode(errors="ignore")
    if buf.strip():
        first = next((ln.strip() for ln in buf.splitlines() if ln.strip()), filename)
        chunks.append({"title": first[:80], "content": buf.strip("\n"),
                       "has_code": True, "content_type": "code"})
    return chunks


# --- indexing ---------------------------------------------------------------------------------

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _store(root, label, chunks, content_hash, file_path=None, file_hash=None, url=None) -> dict:
    conn = _connect(root)
    try:
        with conn:
            row = conn.execute("SELECT id FROM sources WHERE label=?", (label,)).fetchone()
            if row:
                old = [r[0] for r in conn.execute("SELECT id FROM chunks WHERE source_id=?", (row[0],))]
                for cid in old:
                    conn.execute("DELETE FROM chunks_fts WHERE rowid=?", (cid,))
                    conn.execute("DELETE FROM chunks_trigram WHERE rowid=?", (cid,))
                conn.execute("DELETE FROM chunks WHERE source_id=?", (row[0],))
                conn.execute(
                    "UPDATE sources SET hash=?, indexed_at=?, chunk_count=?, file_path=?, file_hash=?, url=? WHERE id=?",
                    (content_hash, time.time(), len(chunks), file_path, file_hash, url, row[0]))
                sid = row[0]
            else:
                cur = conn.execute(
                    "INSERT INTO sources(label, hash, indexed_at, chunk_count, file_path, file_hash, url) VALUES(?,?,?,?,?,?,?)",
                    (label, content_hash, time.time(), len(chunks), file_path, file_hash, url))
                sid = cur.lastrowid
            for ch in chunks:
                cur = conn.execute(
                    "INSERT INTO chunks(title, content, content_type, has_code, source_id) VALUES(?,?,?,?,?)",
                    (ch["title"], ch["content"], ch["content_type"], 1 if ch.get("has_code") else 0, sid))
                conn.execute("INSERT INTO chunks_fts(rowid, title, content) VALUES(?,?,?)",
                             (cur.lastrowid, ch["title"], ch["content"]))
                conn.execute("INSERT INTO chunks_trigram(rowid, title, content) VALUES(?,?,?)",
                             (cur.lastrowid, ch["title"], ch["content"]))
    finally:
        conn.close()
    return {"label": label, "chunks": len(chunks),
            "code_chunks": sum(1 for c in chunks if c["content_type"] == "code")}


def index_content(root, content: str, source: str, *, path=None) -> dict:
    file_hash = file_path = None
    if path is not None:
        p = Path(path)
        file_path = str(p)
        try:
            file_hash = hashlib.sha256(p.read_bytes()).hexdigest()
        except OSError:
            file_hash = None
    return _store(root, source, chunk_markdown(content), _sha(content),
                  file_path=file_path, file_hash=file_hash)


def _gitignore_names(base: Path) -> set[str]:
    names: set[str] = set()
    gi = base / ".gitignore"
    if gi.is_file():
        try:
            for line in gi.read_text(errors="ignore").splitlines():
                line = line.strip().strip("/")
                if line and not line.startswith("#") and "*" not in line and "?" not in line:
                    names.add(line)
        except OSError:
            pass
    return names


def index_path(root, path, source=None) -> dict:
    p = Path(path)
    if p.is_file():
        data = p.read_bytes()
        text = data.decode(errors="ignore")
        label = source or str(p)
        chunks = chunk_markdown(text) if p.suffix.lower() in MARKDOWN_EXTS else _chunk_code(text, p.name)
        return _store(root, label, chunks, _sha(text), file_path=str(p),
                      file_hash=hashlib.sha256(data).hexdigest())
    if not p.is_dir():
        raise FileNotFoundError(str(p))
    ignored = _gitignore_names(p)
    skip = SKIP_DIRS | ignored
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(p):
        depth = len(Path(dirpath).relative_to(p).parts) if Path(dirpath) != p else 0
        dirnames[:] = sorted(d for d in dirnames if d not in skip and depth < MAX_DEPTH)
        if depth > MAX_DEPTH:
            dirnames[:] = []
            continue
        for name in sorted(filenames):
            if name in ignored or Path(name).suffix.lower() not in CODE_EXTS:
                continue
            files.append(Path(dirpath) / name)
            if len(files) >= MAX_FILES:
                break
        if len(files) >= MAX_FILES:
            break
    total = code = 0
    for f in files[:MAX_FILES]:
        rel = str(f.relative_to(p))
        label = f"{source}/{rel}" if source else str(f)
        data = f.read_bytes()
        text = data.decode(errors="ignore")
        chunks = chunk_markdown(text) if f.suffix.lower() in MARKDOWN_EXTS else _chunk_code(text, f.name)
        r = _store(root, label, chunks, _sha(text), file_path=str(f),
                   file_hash=hashlib.sha256(data).hexdigest())
        total += r["chunks"]
        code += r["code_chunks"]
    return {"label": source or str(p), "chunks": total, "code_chunks": code, "files": len(files[:MAX_FILES])}


# --- search -------------------------------------------------------------------------------------

def _levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def _snippet(content: str, terms: list[str]) -> str:
    if len(content) <= 600:
        return content
    low = content.lower()
    pos = -1
    for t in terms:
        pos = low.find(t.lower())
        if pos >= 0:
            break
    if pos < 0:
        return content[:600]
    start = max(0, pos - 300)
    end = min(len(content), start + 600)
    start = max(0, end - 600)
    return content[start:end]


def _is_stale(file_path, file_hash) -> bool:
    if not file_path or not file_hash:
        return False
    p = Path(file_path)
    if not p.is_file():
        return True
    try:
        return hashlib.sha256(p.read_bytes()).hexdigest() != file_hash
    except OSError:
        return True


def _fts_list(conn, table, expr, source, content_type, limit=50):
    sql = (f"SELECT c.id, c.title, c.content, c.content_type, s.label, s.file_path, s.file_hash "
           f"FROM {table} JOIN chunks c ON c.id={table}.rowid "
           f"JOIN sources s ON s.id=c.source_id WHERE {table} MATCH ?")
    params: list = [expr]
    if source:
        sql += " AND (s.label=? OR s.label LIKE ?)"
        params += [source, source + "#%"]
    if content_type:
        sql += " AND c.content_type=?"
        params.append(content_type)
    sql += f" ORDER BY bm25({table}) LIMIT ?"
    params.append(limit)
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.Error:
        return []


def _search_one(conn, query, limit, source, content_type) -> list[dict]:
    terms = re.findall(r"\w+", query)
    if not terms:
        return []
    esc = lambda t: '"' + t.replace('"', '""') + '"'  # noqa: E731
    porter_expr = " OR ".join(esc(t) for t in terms)
    title_expr = " OR ".join(f"title: {esc(t)}" for t in terms)
    tri_terms = [t for t in terms if len(t) >= 3]
    tri_expr = " OR ".join(esc(t) for t in tri_terms) if tri_terms else None

    merged: dict[int, dict] = {}

    def absorb(rows, weight, layer):
        for rank, row in enumerate(rows, 1):
            entry = merged.setdefault(row["id"], {"row": row, "score": 0.0, "layers": set()})
            entry["score"] += weight * (1.0 / (60 + rank))
            entry["layers"].add(layer)

    absorb(_fts_list(conn, "chunks_fts", porter_expr, source, content_type), 1.0, "porter")
    if tri_expr:
        absorb(_fts_list(conn, "chunks_trigram", tri_expr, source, content_type), 1.0, "trigram")
    absorb(_fts_list(conn, "chunks_fts", title_expr, source, content_type), 2.0, "title")

    results: list[dict] = []
    if merged:
        for entry in merged.values():
            score = entry["score"]
            if len(terms) > 1:  # proximity boost
                low = entry["row"]["content"].lower()
                positions = [low.find(t.lower()) for t in terms]
                if all(p >= 0 for p in positions) and max(positions) - min(positions) < 200:
                    score *= 1.5
            entry["score"] = score
        ordered = sorted(merged.values(), key=lambda e: -e["score"])[:limit]
        for e in ordered:
            row = e["row"]
            layer = "title" if "title" in e["layers"] else sorted(e["layers"])[0]
            results.append({"title": row["title"], "content": _snippet(row["content"], terms),
                            "source": row["label"], "content_type": row["content_type"],
                            "rank": e["score"], "layer": layer,
                            "stale": _is_stale(row["file_path"], row["file_hash"])})
        return results

    # Fuzzy Levenshtein fallback when FTS found nothing.
    sql = ("SELECT c.id, c.title, c.content, c.content_type, s.label, s.file_path, s.file_hash "
           "FROM chunks c JOIN sources s ON s.id=c.source_id WHERE 1=1")
    params: list = []
    if source:
        sql += " AND (s.label=? OR s.label LIKE ?)"
        params += [source, source + "#%"]
    if content_type:
        sql += " AND c.content_type=?"
        params.append(content_type)
    scored = []
    for row in conn.execute(sql, params).fetchall():
        words = re.findall(r"\w+", (row["title"] + " " + row["content"]).lower())
        best = None
        for t in terms:
            tl = t.lower()
            thr = 1 if len(tl) <= 4 else 2
            for w in words:
                if abs(len(w) - len(tl)) > thr:
                    continue
                d = _levenshtein(tl, w)
                if best is None or d < best:
                    best = d
            if best == 0:
                break
        if best is not None and best <= (1 if min(len(t) for t in terms) <= 4 else 2):
            scored.append((best, row))
    scored.sort(key=lambda x: x[0])
    for dist, row in scored[:limit]:
        results.append({"title": row["title"], "content": _snippet(row["content"], terms),
                        "source": row["label"], "content_type": row["content_type"],
                        "rank": 1.0 / (1 + dist), "layer": "fuzzy",
                        "stale": _is_stale(row["file_path"], row["file_hash"])})
    return results


def search(root, queries: list[str], *, limit=3, source=None, content_type=None,
           sort="relevance") -> dict:
    """Return ``{query: [results]}`` merging porter + trigram FTS via reciprocal rank fusion."""
    if isinstance(queries, str):
        queries = [queries]
    if not db_path(root).is_file():
        return {q: [] for q in queries}
    conn = _connect(root)
    try:
        return {q: _search_one(conn, q, limit, source, content_type) for q in queries}
    finally:
        conn.close()


# --- fetch --------------------------------------------------------------------------------------

_SECRET_RE = re.compile(
    r"(?i)\b(authorization|api[_-]?key|apikey|access[_-]?token|refresh[_-]?token|token|secret|password|passwd)\b"
    r"(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;\"']+)")
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-+/=]+")


def redact(text: str) -> str:
    """Mask values of authorization/token/secret/password/api_key-style patterns."""
    text = _BEARER_RE.sub("Bearer ***", text)
    return _SECRET_RE.sub(lambda m: f"{m.group(1)}{m.group(2)}***", text)


def _ip_blocked(ip_str: str, strict: bool) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return True
    nets = BLOCKED_V4 if ip.version == 4 else BLOCKED_V6
    if any(ip in ipaddress.ip_network(n) for n in nets):
        return True
    if strict and (ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved):
        return True
    return False


def _validate_url(url: str, strict: bool) -> urllib.parse.ParseResult:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"blocked scheme: {parsed.scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise ValueError("missing host")
    try:
        ipaddress.ip_address(host)
        ips = [host]
    except ValueError:
        try:
            infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80),
                                       proto=socket.IPPROTO_TCP)
        except (socket.gaierror, OSError) as exc:
            raise ValueError(f"cannot resolve {host}: {exc}") from exc
        ips = sorted({info[4][0] for info in infos})
        if not ips:
            raise ValueError(f"cannot resolve {host}")
    for ip in ips:
        if _ip_blocked(ip, strict):
            raise ValueError(f"blocked address {ip} for host {host}")
    return parsed


class _HTMLToMarkdown(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._pre = False
        self._heading = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head"):
            self._skip += 1
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading = int(tag[1])
            self.parts.append("\n\n" + "#" * self._heading + " ")
        elif tag == "p":
            self.parts.append("\n\n")
        elif tag in ("pre",):
            self._pre = True
            self.parts.append("\n\n```\n")
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head") and self._skip:
            self._skip -= 1
        elif tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self._heading = 0
            self.parts.append("\n")
        elif tag == "pre":
            self._pre = False
            self.parts.append("\n```\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)

    def text(self) -> str:
        return re.sub(r"\n{3,}", "\n\n", "".join(self.parts)).strip()


def _html_to_markdown(html: str) -> str:
    parser = _HTMLToMarkdown()
    parser.feed(html)
    return parser.text()


def fetch_and_index(root, url: str, source=None, *, ttl_ms=86400000, force=False, timeout=30) -> dict:
    strict = os.environ.get("HX_FETCH_STRICT") == "1"
    _validate_url(url, strict)
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    base = source or url
    label = f"{base}#{url_hash}"

    if not force and db_path(root).is_file():
        conn = _connect(root)
        try:
            row = conn.execute("SELECT chunk_count, indexed_at FROM sources WHERE label=?",
                               (label,)).fetchone()
        finally:
            conn.close()
        if row and (time.time() - float(row["indexed_at"] or 0)) * 1000 < ttl_ms:
            return {"cached": True, "label": label, "chunks": row["chunk_count"]}

    class _Redirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            _validate_url(newurl, strict)
            return super().redirect_request(req, fp, code, msg, headers, newurl)

    req = urllib.request.Request(url, headers={"User-Agent": "hx-content-store/1.0"})
    opener = urllib.request.build_opener(_Redirect)
    with opener.open(req, timeout=timeout) as resp:
        raw = resp.read(5_000_000)
        ctype = resp.headers.get_content_type() if resp.headers else ""
    text = raw.decode(errors="ignore")
    if "html" in ctype or text.lstrip()[:20].lower().startswith(("<!doctype", "<html")):
        text = _html_to_markdown(text)
    text = redact(text)
    result = _store(root, label, chunk_markdown(text), _sha(text), url=url)
    result["cached"] = False
    result["url"] = url
    return result


# --- stats + CLI ----------------------------------------------------------------------------------

def stats(root) -> dict:
    if not db_path(root).is_file():
        return {"sources": 0, "chunks": 0, "code_chunks": 0, "bytes_indexed": 0}
    conn = _connect(root)
    try:
        row = conn.execute(
            "SELECT (SELECT COUNT(*) FROM sources), (SELECT COUNT(*) FROM chunks), "
            "(SELECT COUNT(*) FROM chunks WHERE content_type='code'), "
            "(SELECT COALESCE(SUM(LENGTH(CAST(content AS BLOB))),0) FROM chunks)").fetchone()
        return {"sources": row[0], "chunks": row[1], "code_chunks": row[2], "bytes_indexed": row[3]}
    finally:
        conn.close()


def main(argv: list[str], root, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx content", add_help=True)
    parser.add_argument("--root", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    idx = sub.add_parser("index")
    idx.add_argument("--content", default=None)
    idx.add_argument("--path", default=None)
    idx.add_argument("--source", default=None)
    idx.add_argument("--root", help=argparse.SUPPRESS)

    sea = sub.add_parser("search")
    sea.add_argument("query", nargs="+")
    sea.add_argument("--source", default=None)
    sea.add_argument("--limit", type=int, default=3)
    sea.add_argument("--root", help=argparse.SUPPRESS)

    fet = sub.add_parser("fetch")
    fet.add_argument("url")
    fet.add_argument("--source", default=None)
    fet.add_argument("--ttl-ms", type=int, default=86400000)
    fet.add_argument("--force", action="store_true")
    fet.add_argument("--root", help=argparse.SUPPRESS)

    sta = sub.add_parser("stats")
    sta.add_argument("--root", help=argparse.SUPPRESS)

    args = parser.parse_args(argv)
    root = Path(root)
    try:
        if args.command == "index":
            if args.path:
                result = index_path(root, args.path, source=args.source)
            else:
                result = index_content(root, args.content or "", args.source or "content")
            print(json.dumps(result, indent=2))
            return 0
        if args.command == "search":
            print(json.dumps(search(root, args.query, limit=args.limit, source=args.source), indent=2))
            return 0
        if args.command == "fetch":
            if env and env.get("HX_FETCH_STRICT") is not None:
                old = os.environ.get("HX_FETCH_STRICT")
                os.environ["HX_FETCH_STRICT"] = str(env["HX_FETCH_STRICT"])
                try:
                    result = fetch_and_index(root, args.url, source=args.source,
                                             ttl_ms=args.ttl_ms, force=args.force)
                finally:
                    if old is None:
                        os.environ.pop("HX_FETCH_STRICT", None)
                    else:
                        os.environ["HX_FETCH_STRICT"] = old
            else:
                result = fetch_and_index(root, args.url, source=args.source,
                                         ttl_ms=args.ttl_ms, force=args.force)
            print(json.dumps(result, indent=2))
            return 0
        print(json.dumps(stats(root), indent=2))
        return 0
    except (ValueError, FileNotFoundError, urllib.error.URLError) as exc:
        print(f"hx: content: {exc}", file=sys.stderr)
        return 2
