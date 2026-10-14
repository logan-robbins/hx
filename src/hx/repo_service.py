"""Shared local repository queries; task/ownership authority stays in each ledger."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import socket
import socketserver
import sqlite3
import subprocess
import threading
import time
import tempfile
from pathlib import Path, PurePosixPath

from .errors import ValidationError

MAX_REQUEST = 65536
MAX_RESPONSE = 262144
MAX_BLOB = 512 * 1024


def configured_socket(root, env=None):
    env = os.environ if env is None else env
    if env.get("HX_REPO_SERVICE_SOCKET"):
        return Path(env["HX_REPO_SERVICE_SOCKET"])
    try:
        value = json.loads((Path(root) / "config/hx.json").read_text()).get("repository_service")
    except (OSError, ValueError):
        value = None
    return Path(value) if value else None


def request(path, body):
    try:
        return _request(path, body)
    except (OSError, ValueError) as exc:
        raise ValidationError("repository service request unavailable: " + str(exc)) from exc


def _request(path, body):
    encoded = json.dumps(body, separators=(",", ":")).encode() + b"\n"
    if len(encoded) > MAX_REQUEST:
        raise ValidationError("repository request exceeds 64 KiB")
    with socket.socket(socket.AF_UNIX) as client:
        client.settimeout(120)
        client.connect(str(path))
        client.sendall(encoded)
        stream = client.makefile("rb")
        data = stream.readline(MAX_RESPONSE + 1)
        if len(data) > MAX_RESPONSE or not data.endswith(b"\n"):
            raise ValidationError("repository service returned an incomplete response")
        answer = json.loads(data)
    if "error" in answer:
        raise ValidationError(answer["error"])
    return answer


def _git(repo, *args):
    # Git can produce an unbounded listing even when the response is capped.
    # Spill the process output to disk and decode only the bounded prefix.
    with tempfile.TemporaryFile() as output:
        result = subprocess.run(["git", "--no-optional-locks", "--literal-pathspecs", "-C", str(repo), *args],
                                stdout=output, stderr=subprocess.DEVNULL, timeout=30)
        if result.returncode:
            raise ValidationError("repository object or revision is unavailable")
        output.seek(0)
        data = output.read(MAX_RESPONSE + 1)
    if len(data) > MAX_RESPONSE:
        raise ValidationError("repository metadata exceeds response bound; narrow the scope")
    return data


def _path(value):
    if not isinstance(value, str) or not value or any(c in value for c in ('\0', '\\', '\n', '\r')):
        raise ValidationError("source path must be repository relative")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or ".git" in path.parts or str(path) != value:
        raise ValidationError("source path must be normalized and inside the repository")
    return value


def _revision(repo, value):
    if not isinstance(value, str) or not value or value.startswith("-") or "\0" in value:
        raise ValidationError("invalid source revision")
    return _git(repo, "rev-parse", "--verify", "--end-of-options", value + "^{commit}").decode().strip()


def _blobs(repo, at, paths):
    entries = {}
    for entry in _git(repo, 'ls-tree', '-z', at, '--', *paths).split(b'\0'):
        if entry:
            metadata, path = entry.split(b'\t', 1)
            entries[os.fsdecode(path)] = metadata.decode().split()
    result = []
    for path in paths:
        if path not in entries:
            raise ValidationError('materialized path is absent from the snapshot')
        mode, kind, oid = entries[path]
        if kind != 'blob' or mode not in {'100644', '100755'} or int(_git(repo, 'cat-file', '-s', oid)) > MAX_BLOB:
            raise ValidationError('materialization requires bounded regular blobs')
        result.append({'path': path, 'object': oid, 'mode': mode})
    return result


@contextlib.contextmanager
def _database(root):
    directory = Path(root) / "state/repository-service"
    directory.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(directory / "registry.sqlite", timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS repositories(namespace TEXT PRIMARY KEY,root TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS telemetry(method TEXT PRIMARY KEY,calls INTEGER NOT NULL,bytes INTEGER NOT NULL);
    """)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def register(root, repo):
    from .code_store import namespace
    repo = Path(repo).resolve()
    if not repo.is_dir() or Path(root).resolve().is_relative_to(repo):
        raise ValidationError("shared repository state must be outside the product checkout")
    _revision(repo, "HEAD")
    identity = namespace(repo)
    with _database(root) as conn:
        conn.execute("INSERT INTO repositories VALUES(?,?) ON CONFLICT(namespace) DO UPDATE SET root=excluded.root", (identity, str(repo)))
    return {"namespace": identity, "root": str(repo)}


def _source(root, namespace):
    with _database(root) as conn:
        row = conn.execute("SELECT root FROM repositories WHERE namespace=?", (namespace,)).fetchone()
    if row is None:
        raise ValidationError("repository namespace is not registered with this service")
    return Path(row[0])


def dispatch(root, body):
    started = time.monotonic()
    result = _dispatch(root, body)
    if isinstance(body, dict) and body.get('method') != 'status':
        with _database(root) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS latency(method TEXT PRIMARY KEY,calls INTEGER NOT NULL,total_seconds REAL NOT NULL,max_seconds REAL NOT NULL)')
            elapsed = time.monotonic() - started
            conn.execute('INSERT INTO latency VALUES(?,1,?,?) ON CONFLICT(method) DO UPDATE SET calls=calls+1,total_seconds=total_seconds+excluded.total_seconds,max_seconds=max(max_seconds,excluded.max_seconds)', (body['method'], elapsed, elapsed))
    return result


def _dispatch(root, body):
    """All tiers use this protocol; replies name exact revisions and completeness."""
    if not isinstance(body, dict):
        raise ValidationError("repository request must be an object")
    method = body.get("method")
    if method == "register":
        return register(root, body["repo"])
    if method == "status":
        with _database(root) as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS latency(method TEXT PRIMARY KEY,calls INTEGER NOT NULL,total_seconds REAL NOT NULL,max_seconds REAL NOT NULL)')
            return {"repositories": [dict(r) for r in conn.execute("SELECT * FROM repositories ORDER BY namespace")],
                    "telemetry": [dict(r) for r in conn.execute("SELECT * FROM telemetry ORDER BY method")],
                    "latency": [dict(r) for r in conn.execute("SELECT * FROM latency ORDER BY method")],
                    "request_bytes": MAX_REQUEST, "response_bytes": MAX_RESPONSE}
    if method == "scope":
        from .code_freshness import scope
        return scope(root, body["repository"], body["paths"], bootstrap=body.get("bootstrap", False), local=True)
    if method == "code":
        from . import code_index
        args = body.get("argv")
        if not isinstance(args, list) or not all(isinstance(a, str) for a in args) or any(a == "--root" or a.startswith("--root=") for a in args):
            raise ValidationError("invalid shared code-query arguments")
        if "--at" in args or any(a.startswith("--at=") for a in args):
            # Catalog registration is enough for an explicit historical query;
            # bootstrapping source objects does not require a client checkout.
            from . import code_store
            graph = code_store.connect(root)
            try:
                with _database(root) as catalog:
                    for row in catalog.execute("SELECT namespace,root FROM repositories"):
                        graph.execute("INSERT OR IGNORE INTO repos VALUES(?,?,NULL,0,NULL)", (row["namespace"], row["root"]))
                graph.commit()
            finally:
                graph.close()
        status, result = code_index.execute(args, Path(root), read_ledger=False)
        return {"exit_code": status, "result": result}
    if method not in {"diff", "merge-base", "history", "file", "materialize", "expand"}:
        raise ValidationError("unknown repository operation")
    repo = _source(root, body.get("namespace"))
    at = _revision(repo, body.get("at", "HEAD"))
    limit = body.get("limit", 20)
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValidationError("repository limit must be 1–100")
    answer = {"namespace": body["namespace"], "at": at, "complete": True}
    if method == 'expand':
        output = Path(body['out']).resolve()
        paths = [_path(p) for p in body.get('paths', [])]
        if output.is_relative_to(repo.resolve()) or not paths or len(paths) > 100:
            raise ValidationError('sparse expansion requires 1–100 paths in a private copy outside the registered repository')
        if _git(output, 'rev-parse', '--show-toplevel').decode().strip() != str(output):
            raise ValidationError('sparse expansion requires the working copy root')
        if _revision(output, 'HEAD') != at or _git(output, 'status', '--porcelain').strip():
            raise ValidationError('expand only a clean copy at the exact source snapshot; commit or reconcile before changing its read set')
        if _git(output, 'config', '--get', 'core.sparseCheckout').strip() != b'true':
            raise ValidationError('working copy is not sparse')
        for path in paths:
            entry = _git(repo, 'ls-tree', '-z', at, '--', path).split(b'\0')[0]
            if not entry or entry.split(b'\t', 1)[1] != os.fsencode(path):
                raise ValidationError('expanded path is absent from source snapshot')
            mode, kind, oid = entry.split(b'\t', 1)[0].decode().split()
            if kind != 'blob' or mode not in {'100644', '100755'} or int(_git(repo, 'cat-file', '-s', oid)) > MAX_BLOB:
                raise ValidationError('expansion requires bounded regular source blobs')
        sparse_path = Path(_git(output, 'rev-parse', '--git-path', 'info/sparse-checkout').decode().strip())
        sparse_path = sparse_path if sparse_path.is_absolute() else output / sparse_path
        if sparse_path.stat().st_size > MAX_REQUEST:
            raise ValidationError('sparse read set exceeds request bound')
        patterns = sparse_path.read_text().splitlines()
        patterns.extend('/' + ''.join('\\' + c if c in '*?[]!#' else c for c in p) for p in paths)
        encoded_patterns = ('\n'.join(dict.fromkeys(patterns)) + '\n').encode()
        if len(encoded_patterns) > MAX_REQUEST:
            raise ValidationError('expanded sparse read set exceeds 64 KiB')
        subprocess.run(['git', '-C', str(output), 'sparse-checkout', 'set', '--no-cone', '--stdin'],
                       input=encoded_patterns, check=True, capture_output=True, timeout=30)
        if _git(output, 'status', '--porcelain').strip():
            raise ValidationError('expanded copy no longer matches the source snapshot')
        answer.update(out=str(output), paths=paths, working_copy=True)
    elif method == "materialize":
        anticipated = []
        if 'prefixes' in body:
            paths = set()
            for value in body['prefixes']:
                prefix = _path(value) if value != '.' else '.'
                names = [os.fsdecode(p) for p in _git(repo, 'ls-tree', '-r', '--name-only', '-z', at, '--', prefix).split(b'\0') if p]
                if not names:
                    anticipated.append(value)
                paths.update(names)
                if len(paths) > 100:
                    raise ValidationError('assignment read set exceeds 100 paths; narrow the packet or materialize dependencies separately')
            body = {**body, 'paths': sorted(paths)}
        paths = [_path(p) for p in body.get("paths", [])]
        if not paths or len(paths) > 100:
            raise ValidationError("materialization needs 1–100 exact paths from the dependency closure")
        selected = list(dict.fromkeys(paths))
        if body.get('working_copy') and '.hx/map/manifest.json' not in selected:
            manifest = _git(repo, 'ls-tree', '-z', at, '--', '.hx/map/manifest.json')
            if manifest:
                selected.append('.hx/map/manifest.json')
        if len(selected) > 100:
            raise ValidationError('materialized paths including repository manifest exceed 100')
        # Validate every selected object before any checkout or source write,
        # including automatically selected identity metadata.
        installed = _blobs(repo, at, selected)
        for path, entry in body.get('required_files', {}).items():
            actual = next((row for row in installed if row['path'] == path), None)
            if (entry is None and actual is not None) or (entry is not None and (actual is None or actual['object'] != entry['object'] or actual['mode'] != entry['mode'])):
                raise ValidationError('assignment source does not contain the exact hard dependency; integrate it before provisioning')
        output = Path(body["out"]).absolute()
        if output.exists() or output.is_symlink() or output.resolve().is_relative_to(repo.resolve()):
            raise ValidationError("materialization requires a fresh directory outside the registered source repository")
        output.mkdir(parents=True)
        try:
            if body.get('working_copy'):
                # Share node-local Git objects; only requested source paths are
                # written. The client retains ordinary clean Git identity.
                output.rmdir()
                subprocess.run(['git', 'clone', '--shared', '--no-checkout', '--quiet', str(repo), str(output)], check=True, capture_output=True, timeout=60)
                _git(output, 'sparse-checkout', 'init', '--no-cone')
                patterns = ['/' + ''.join('\\' + c if c in '*?[]!#' else c for c in p) for p in selected]
                subprocess.run(['git', '-C', str(output), 'sparse-checkout', 'set', '--no-cone', '--stdin'], input=('\n'.join(patterns) + '\n').encode(), check=True, capture_output=True, timeout=30)
                _git(output, 'checkout', '-q', '-b', 'hx/snapshot-' + at[:16], at)
            for entry in installed:
                path, mode, oid = entry['path'], entry['mode'], entry['object']
                if not body.get('working_copy'):
                    target = output / path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    raw = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", oid], capture_output=True, check=True, timeout=30).stdout
                    target.write_bytes(raw)
                    target.chmod(0o755 if mode == "100755" else 0o644)
        except BaseException:
            import shutil
            shutil.rmtree(output)
            raise
        answer.update(out=str(output), files=installed)
        if 'assignment' in body:
            answer['assignment'] = {**body['assignment'], 'anticipated_paths': anticipated}
        if body.get('working_copy'):
            if _git(output, 'status', '--porcelain').strip():
                raise ValidationError('sparse working copy did not reproduce a clean source snapshot')
            answer['working_copy'] = True
    elif method == "diff":
        base = _revision(repo, body.get("base", "HEAD"))
        paths = [_path(p) for p in body.get("paths", [])]
        names = [os.fsdecode(p) for p in _git(repo, "diff", "--name-only", "--no-renames", "-z", base, at, "--", *paths).split(b"\0") if p]
        answer.update(base=base, paths=names[:limit], complete=len(names) <= limit)
    elif method == "merge-base":
        base = _revision(repo, body["base"])
        answer.update(base=base, merge_base=_git(repo, "merge-base", at, base).decode().strip())
    elif method == "history":
        path = _path(body["path"])
        commits = _git(repo, "log", "--format=%H", "-n", str(limit + 1), at, "--", path).decode().splitlines()
        answer.update(path=path, commits=commits[:limit], complete=len(commits) <= limit)
    elif method == "file":
        path = _path(body["path"])
        first, lines = body.get("line", 1), body.get("lines", 160)
        if type(first) is not int or first < 1 or type(lines) is not int or not 1 <= lines <= 160:
            raise ValidationError("file range requires a positive line and at most 160 lines")
        entries = _git(repo, "ls-tree", "-z", at, "--", path).split(b"\0")
        entry = next((r for r in entries if r and os.fsdecode(r.split(b"\t", 1)[1]) == path), None)
        if entry is None:
            raise ValidationError("file is absent from this snapshot")
        mode, kind, oid = entry.split(b"\t", 1)[0].decode().split()
        if kind != "blob" or mode not in {"100644", "100755"}:
            raise ValidationError("file query requires a regular source blob")
        size = int(_git(repo, "cat-file", "-s", oid))
        if size > MAX_BLOB:
            raise ValidationError("source blob exceeds 512 KiB")
        # Already size-checked. No working-copy file is materialized or followed.
        raw = subprocess.run(["git", "-C", str(repo), "cat-file", "blob", oid], capture_output=True, check=True, timeout=30).stdout
        if b"\0" in raw:
            raise ValidationError("source blob is binary")
        source = raw.decode("utf-8", errors="replace").splitlines(keepends=True)
        answer.update(path=path, object=oid, mode=mode, line=first, end_line=min(first + lines - 1, len(source)),
                      text="".join(source[first - 1:first - 1 + lines]), complete=first == 1 and lines >= len(source))
    else:
        raise ValidationError("unknown repository operation")
    encoded = json.dumps(answer).encode()
    if len(encoded) + 1 > MAX_RESPONSE:
        raise ValidationError("repository response exceeds 256 KiB; narrow the request")
    with _database(root) as conn:
        conn.execute("INSERT INTO telemetry VALUES(?,1,?) ON CONFLICT(method) DO UPDATE SET calls=calls+1,bytes=bytes+excluded.bytes", (method, len(encoded)))
    return answer


class RepositoryServer(socketserver.ThreadingMixIn, socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 64

    def __init__(self, path, root, *, workers=4):
        self.root = Path(root)
        self.slots = threading.BoundedSemaphore(workers)
        super().__init__(str(path), Handler)

    def process_request(self, request, address):
        # Bound queued/running upstream work; a cache miss cannot fan out forever.
        self.slots.acquire()
        try:
            super().process_request(request, address)
        except BaseException:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()


class Handler(socketserver.StreamRequestHandler):
    def handle(self):
        self.connection.settimeout(30)
        try:
            data = self.rfile.readline(MAX_REQUEST + 1)
            if len(data) > MAX_REQUEST or not data.endswith(b"\n"):
                raise ValidationError("incomplete or oversized repository request")
            answer = dispatch(self.server.root, json.loads(data))
        except (OSError, ValueError, KeyError, subprocess.SubprocessError, SystemExit, ValidationError) as exc:
            answer = {"error": str(exc)}
        encoded = json.dumps(answer).encode() + b"\n"
        if len(encoded) > MAX_RESPONSE:
            encoded = b'{"error":"response bound exceeded"}\n'
        self.wfile.write(encoded)


def serve(root, path=None, *, workers=4):
    path = Path(path) if path else default_socket(root)
    if not 1 <= workers <= 64:
        raise ValidationError("service workers must be 1–64")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        # Never unlink an active endpoint or an arbitrary non-socket file.
        raise ValidationError("repository socket already exists; reconcile the existing service")
    try:
        with RepositoryServer(path, root, workers=workers) as server:
            path.chmod(0o600)
            server.serve_forever(poll_interval=0.2)
    finally:
        if path.is_socket():
            path.unlink()


def default_socket(root):
    path = Path(root) / "state/repository-service/service.sock"
    if len(os.fsencode(path)) >= 100:
        identity = hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:20]
        path = Path("/tmp") / f"hx-repo-{os.getuid()}-{identity}.sock"
    return path


def code_request(root, argv, env=None):
    path = configured_socket(root, env)
    if path is None:
        return None
    clean = []
    skip = False
    for value in argv:
        if skip:
            skip = False
        elif value == "--root":
            skip = True
        elif not value.startswith("--root="):
            clean.append(value)
    want_owners = "--owners" in clean
    clean = [a for a in clean if a != "--owners"]
    result = request(path, {"method": "code", "argv": clean})
    if clean and clean[0] in {'contract', 'contracts'} and isinstance(result['result'], list):
        from .code_metadata import join_publications
        from .code_index import expectation, _parser
        options = _parser().parse_args(clean)
        original = result['result']
        result['result'] = join_publications(root, original, options.name,
            repo=options.namespace, limit=options.limit, task=options.task)
        expected = options.expect
        if result['exit_code'] in {0, 3} and result['result'] != original:
            result['exit_code'] = 0 if expectation(result['result'], expected) else 3
    if want_owners:
        from .code_metadata import owners
        owners(root, result["result"])
    return result


def main(argv, root, *, env=None):
    parser = argparse.ArgumentParser(prog="hx repo", epilog="example: hx repo file --namespace NS --at SHA --path src/main.cs")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "register", "status", "file", "diff", "history", "merge-base", "materialize", "assignment", "expand"):
        p = sub.add_parser(name, epilog=f"example: hx repo {name}" + (" --namespace NS --at SHA" if name not in {"serve", "register", "status"} else ""))
        p.add_argument("--root", help=argparse.SUPPRESS)
        p.add_argument("--socket")
        if name == "serve":
            p.add_argument("--workers", type=int, default=4)
        elif name == "register":
            p.add_argument("--repo", required=True)
        elif name == 'assignment':
            p.add_argument('task')
            p.add_argument('--out', required=True)
            p.add_argument('--at', default='HEAD')
        elif name != "status":
            p.add_argument("--namespace", required=True)
            p.add_argument("--at", default="HEAD")
            p.add_argument("--limit", type=int, default=20)
            if name in {"diff", "merge-base"}:
                p.add_argument("--base", required=True)
            if name in {"file", "history"}:
                p.add_argument("--path", required=True)
            if name == "diff":
                p.add_argument("--paths", nargs="*", default=[])
            if name in {"materialize", "expand"}:
                p.add_argument("--paths", nargs="+", required=True)
                p.add_argument("--out", required=True)
                if name == 'materialize':
                    p.add_argument("--working-copy", action="store_true")
            if name == "file":
                p.add_argument("--line", type=int, default=1)
                p.add_argument("--lines", type=int, default=160)
    args = parser.parse_args(argv)
    if args.command == "serve":
        serve(root, args.socket, workers=args.workers)
        return 0
    body = {k: v for k, v in vars(args).items() if k not in {"root", "socket", "command"}}
    body["method"] = args.command
    if args.command == 'assignment':
        from .continuity_store import ContinuityStore
        from .assignment_materialization import request_body
        from .caller import require_partner_caller
        require_partner_caller('repo assignment', env)
        with ContinuityStore(root) as store:
            body = request_body(store, args.task, args.out, args.at, env=env)
    path = Path(args.socket) if args.socket else configured_socket(root, env)
    result = request(path, body) if path else dispatch(root, body)
    print(json.dumps(result))
    return 0
