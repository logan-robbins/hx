"""Repository-scoped derived graph storage; never an ownership authority."""
from __future__ import annotations
import hashlib
import json
import sqlite3
import subprocess
from pathlib import Path

SCHEMA = '''
CREATE TABLE IF NOT EXISTS repos(repo TEXT PRIMARY KEY, root TEXT NOT NULL, head TEXT, dirty INTEGER NOT NULL, indexed_at REAL);
CREATE TABLE IF NOT EXISTS files(id INTEGER PRIMARY KEY, repo TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
 size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, symbols TEXT NOT NULL, body TEXT NOT NULL, UNIQUE(repo,path));
CREATE TABLE IF NOT EXISTS symbols(repo TEXT NOT NULL,name TEXT NOT NULL,qualified TEXT NOT NULL,kind TEXT NOT NULL,
 path TEXT NOT NULL,line INTEGER NOT NULL,end_line INTEGER NOT NULL,signature TEXT NOT NULL,lane TEXT NOT NULL,
 extractor TEXT NOT NULL,anchor_sha TEXT,name_folded TEXT NOT NULL,attributes TEXT NOT NULL,stale INTEGER NOT NULL DEFAULT 0,
 unanchored INTEGER NOT NULL DEFAULT 0, UNIQUE(repo,qualified,path,line,lane,extractor));
CREATE INDEX IF NOT EXISTS symbol_name ON symbols(repo,name_folded);
CREATE INDEX IF NOT EXISTS symbol_path ON symbols(repo,path);
CREATE TABLE IF NOT EXISTS refs(repo TEXT NOT NULL,name TEXT NOT NULL,qualified TEXT NOT NULL,path TEXT NOT NULL,
 line INTEGER NOT NULL,kind TEXT NOT NULL,lane TEXT NOT NULL,extractor TEXT NOT NULL,anchor_sha TEXT,stale INTEGER NOT NULL DEFAULT 0);
CREATE INDEX IF NOT EXISTS ref_name ON refs(repo,name);
CREATE TABLE IF NOT EXISTS edges(repo TEXT NOT NULL,src_path TEXT NOT NULL,dst_text TEXT NOT NULL,kind TEXT NOT NULL,
 lane TEXT NOT NULL,extractor TEXT NOT NULL,anchor_sha TEXT,stale INTEGER NOT NULL DEFAULT 0,evidence TEXT NOT NULL DEFAULT '{}',
 UNIQUE(repo,src_path,dst_text,kind,lane,extractor));
CREATE TABLE IF NOT EXISTS projects(repo TEXT NOT NULL,name TEXT NOT NULL,path TEXT NOT NULL,data TEXT NOT NULL,PRIMARY KEY(repo,path));
CREATE TABLE IF NOT EXISTS project_edges(repo TEXT NOT NULL,src_path TEXT NOT NULL,dst_text TEXT NOT NULL,kind TEXT NOT NULL,
 PRIMARY KEY(repo,src_path,dst_text));
CREATE TABLE IF NOT EXISTS contracts(repo TEXT NOT NULL,name TEXT NOT NULL,path TEXT NOT NULL,version TEXT NOT NULL,
 producer TEXT NOT NULL,sha TEXT NOT NULL,PRIMARY KEY(repo,name,path,version));
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY,value TEXT NOT NULL);
'''

def db_path(root):
    return Path(root) / 'state' / 'code-index' / 'index.db'


def connect(root, *, existing=False, trigram=True):
    path = db_path(root)
    if existing and not path.is_file():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    # The old index had no repository identity. It is derived, so rebuild it.
    old = conn.execute("SELECT 1 FROM sqlite_master WHERE name='files'").fetchone()
    if old and 'repo' not in {r[1] for r in conn.execute('PRAGMA table_info(files)')}:
        for table in ('files', 'symbols', 'edges', 'code_search', 'code_trigram'):
            conn.execute('DROP TABLE IF EXISTS ' + table)
    conn.executescript(SCHEMA)
    for table, tokenizer in [('code_search', 'porter unicode61'), ('code_trigram', 'trigram')]:
        if table == 'code_trigram' and not trigram:
            continue
        try:
            conn.execute(f"CREATE VIRTUAL TABLE IF NOT EXISTS {table} USING fts5(path,symbols,body,content='files',content_rowid='id',tokenize='{tokenizer}')")
        except sqlite3.OperationalError:
            if table != 'code_trigram':
                raise
    conn.commit()
    return conn


def git(repo, *args):
    result = subprocess.run(['git', '-C', str(repo), *args], capture_output=True, timeout=30)
    return result.stdout.decode('utf-8', errors='replace').strip() if result.returncode == 0 else None


def namespace(repo):
    repo = Path(repo).resolve()
    try:
        identity = json.loads((repo / '.hx/map/manifest.json').read_text())['repo_id']
        if isinstance(identity, str) and identity:
            return identity
    except (OSError, ValueError, KeyError):
        pass
    remotes = git(repo, 'remote', 'get-url', 'origin')
    if not remotes:
        names = git(repo, 'remote')
        remotes = git(repo, 'remote', 'get-url', names.splitlines()[0]) if names else None
    return remotes or str(repo)


def span_sha(text, line, end_line):
    return hashlib.sha256(''.join(text.splitlines(keepends=True)[line-1:end_line]).encode()).hexdigest()


def fts_tables(conn):
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE name IN ('code_search','code_trigram')")]


def delete_file(conn, repo, path):
    row = conn.execute('SELECT * FROM files WHERE repo=? AND path=?', (repo,path)).fetchone()
    if row:
        for table in fts_tables(conn):
            conn.execute(f"INSERT INTO {table}({table},rowid,path,symbols,body) VALUES('delete',?,?,?,?)",
                         (row['id'],path,row['symbols'],row['body']))
    conn.execute('DELETE FROM files WHERE repo=? AND path=?', (repo,path))
    for table, key in [('symbols','path'),('refs','path'),('edges','src_path'),('projects','path'),('project_edges','src_path')]:
        conn.execute(f"DELETE FROM {table} WHERE repo=? AND {key}=? AND " +
                     ("lane IN ('syntax','text','resolved')" if table in {'symbols','refs','edges'} else '1'), (repo,path))


def decorate(conn, row):
    result = dict(row)
    if 'attributes' in result:
        result['attributes'] = json.loads(result['attributes'])
    if 'evidence' in result:
        result['evidence'] = json.loads(result['evidence'])
    repo = conn.execute('SELECT root FROM repos WHERE repo=?', (result['repo'],)).fetchone()
    file = conn.execute('SELECT sha256 FROM files WHERE repo=? AND path=?', (result['repo'],result.get('path',result.get('src_path')))).fetchone()
    stale = bool(result.get('stale'))
    if repo and not result.get('unanchored'):
        try:
            path = result.get('path',result.get('src_path'))
            data = (Path(repo[0])/path).read_bytes()
            stale |= bool(file and hashlib.sha256(data).hexdigest() != file[0])
            if not file and result.get('anchor_sha') and result.get('line'):
                stale |= span_sha(data.decode(),result['line'],result['end_line']) != result['anchor_sha']
        except OSError:
            stale = True
    result['stale'] = stale
    return result
