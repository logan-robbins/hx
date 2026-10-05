"""Portable semantic application maps and bounded source/graph validation.

Map validation is an explicit batch operation. Normal readers use selected ledger
records; they do not load the complete portable baseline into a model context.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import io
import json
import os
import re
import sqlite3
import stat
import subprocess
import tempfile
import tokenize
import uuid
from contextlib import closing, contextmanager
from pathlib import Path, PurePosixPath

from . import checks
from .continuity_store import HASH, ContinuityStore, Conflict, canonical, digest
from .errors import ValidationError
from .store import atomic_write_text

SCHEMA_VERSION = 1
RECORD_BYTES = 65536
SOURCE_BYTES = 1024 * 1024
SOURCE_TOKENS = 40000
ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
KINDS = {"behavior": "behaviors", "component": "components", "interface": "interfaces",
         "file": "files", "check": "checks", "resource": "resources"}
CLAIMS = {"required", "observed", "hypothesis"}
RELATIONS = {"implements", "contains", "depends_on", "provides", "consumes", "checked_by"}
STATUSES = {"candidate", "validated", "stale", "disputed"}
DATA_FIELDS = {
    "behavior": {"outcome", "preconditions", "inputs", "outputs", "side_effects", "failure_modes", "invariants"},
    "component": {"responsibility"},
    "interface": {"contract", "invariants"},
    "file": {"path"},
    "check": {"recipe"},
    "resource": {"responsibility", "resource_type"},
}


def identifier(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ValidationError("map IDs must use lowercase letters, digits, dots, underscores, or hyphens (max 128)")


def relative_path(value):
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        raise ValidationError("map path must be normalized and repository-relative")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) != value or str(path) == ".":
        raise ValidationError("map path must be normalized and repository-relative")
    if path == PurePosixPath(".hx/map") or PurePosixPath(".hx/map") in path.parents:
        raise ValidationError("source anchors cannot fingerprint map files themselves")
    return value


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"map {field} must be nonempty text")


def _texts(value, field):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValidationError(f"map {field} must be an array of nonempty strings")


def validate_record(record: dict) -> None:
    fields = {"schema_version", "id", "version", "kind", "claim", "summary", "data", "anchors", "edges", "attributes", "replaces"}
    if not isinstance(record, dict) or record.keys() != fields or type(record["schema_version"]) is not int or record["schema_version"] != SCHEMA_VERSION:
        raise ValidationError("map record has unknown structure or schema; migration is required")
    if len(canonical(record).encode()) > RECORD_BYTES:
        raise ValidationError("map record exceeds 64 KiB; split its responsibility")
    identifier(record["id"])
    if type(record["version"]) is not int or record["version"] < 1:
        raise ValidationError("map version must be positive")
    if not isinstance(record["kind"], str) or record["kind"] not in KINDS or not isinstance(record["claim"], str) or record["claim"] not in CLAIMS:
        raise ValidationError("map kind/claim is unknown; structural changes need migration")
    _text(record["summary"], "summary")
    if not isinstance(record["data"], dict) or record["data"].keys() != DATA_FIELDS[record["kind"]]:
        raise ValidationError(f"map {record['kind']} has missing or unknown semantic fields")
    data = record["data"]
    for name, value in data.items():
        if name in {"preconditions", "inputs", "outputs", "side_effects", "failure_modes", "invariants"}:
            _texts(value, name)
        elif name == "path":
            relative_path(value)
        elif name == "contract":
            if isinstance(value, str):
                _text(value, name)
            elif not isinstance(value, dict) or not value:
                raise ValidationError("interface contract must be a signature or a nonempty schema object")
        elif name == "recipe":
            checks.validate(value)
            if value["id"] != record["id"]:
                raise ValidationError("check recipe ID must match its map record")
            for path in [value["cwd"], *value["inputs"], *value["environment"]["inputs"],
                         *[item for item in value["environment"]["external_versions"].values() if item is not None]]:
                if path != ".":
                    relative_path(path)
            for executable in [value["argv"][0], *value["environment"]["executables"]]:
                if "/" in executable:
                    relative_path(executable)
        else:
            _text(value, name)
    if not isinstance(record["attributes"], dict) or any(not isinstance(key, str) or "." not in key for key in record["attributes"]):
        raise ValidationError("stack attributes require namespaced keys")
    if not isinstance(record["replaces"], list):
        raise ValidationError("map replaces must be an ID array")
    for replaced in record["replaces"]:
        identifier(replaced)
        if replaced == record["id"]:
            raise ValidationError("a map record cannot replace itself")
    if not isinstance(record["anchors"], list) or len(record["anchors"]) > 64:
        raise ValidationError("map anchors must be an array of at most 64 entries")
    for anchor in record["anchors"]:
        if not isinstance(anchor, dict) or not {"path", "symbol", "sha256"} <= anchor.keys() or anchor.keys() - {"path", "symbol", "sha256", "line", "end_line"}:
            raise ValidationError("source anchor requires path, symbol, sha256 and optional display lines")
        relative_path(anchor["path"])
        if anchor["symbol"] is not None:
            _text(anchor["symbol"], "anchor.symbol")
        if not isinstance(anchor["sha256"], str) or not HASH.fullmatch(anchor["sha256"]):
            raise ValidationError("anchor requires a SHA-256 content identity")
        for line in ("line", "end_line"):
            if line in anchor and (type(anchor[line]) is not int or anchor[line] < 1):
                raise ValidationError("anchor display lines must be positive integers")
    if record["claim"] == "observed" and not record["anchors"]:
        raise ValidationError("observed portable claims require source anchors")
    if record["kind"] == "file" and record["claim"] == "observed" and not any(anchor["path"] == data["path"] for anchor in record["anchors"]):
        raise ValidationError("an observed file record needs an anchor to its declared path")
    if not isinstance(record["edges"], list) or len(record["edges"]) > 128:
        raise ValidationError("map edges must be an array of at most 128 entries")
    seen = set()
    for edge in record["edges"]:
        if not isinstance(edge, dict) or edge.keys() != {"kind", "to", "status", "evidence"}:
            raise ValidationError("map edge requires kind, to, status, evidence")
        identifier(edge["to"])
        if not isinstance(edge["kind"], str) or edge["kind"] not in RELATIONS or not isinstance(edge["status"], str) or edge["status"] not in STATUSES:
            raise ValidationError("map relation/status is unknown; structural changes need migration")
        key = (edge["kind"], edge["to"])
        if key in seen or edge["to"] == record["id"]:
            raise ValidationError("map has a duplicate or self edge")
        seen.add(key)
        proof = edge["evidence"]
        if not isinstance(proof, dict):
            raise ValidationError("edge evidence must be an object")
        if edge["status"] == "validated":
            if record["claim"] == "hypothesis":
                raise ValidationError("hypotheses cannot establish validated relationships")
            if edge["kind"] == "checked_by":
                if proof.keys() != {"kind", "check_id", "declaration"} or proof["kind"] != "check_declaration" or proof["check_id"] != edge["to"]:
                    raise ValidationError("checked_by needs an explicit check declaration, not a passing-test inference")
                _text(proof["declaration"], "check declaration")
            elif proof.keys() != {"kind", "anchors"} or proof["kind"] != "source" or not isinstance(proof["anchors"], list) or not proof["anchors"] or any(
                type(index) is not int or not 0 <= index < len(record["anchors"]) for index in proof["anchors"]
            ):
                raise ValidationError("validated edge needs source anchors")


def semantic_identity(record: dict) -> str:
    """Display locations and revision counters cannot cause dependency replanning."""
    return digest({**record, "version": 0,
                   "anchors": [{key: value for key, value in anchor.items() if key not in {"line", "end_line"}}
                               for anchor in record["anchors"]]})


def _source_stamp(repository: Path, path: str) -> str:
    original = repository.resolve() / path
    target = original.resolve()
    if not target.is_relative_to(repository.resolve()):
        raise ValidationError("map source escapes the repository")
    try:
        rows = [original.lstat(), target.stat()]
    except OSError as exc:
        raise ValidationError(f"map source unavailable: {path}") from exc
    return canonical([str(target), *[[s.st_dev, s.st_ino, s.st_size, s.st_mode, s.st_mtime_ns, s.st_ctime_ns] for s in rows]])


@contextmanager
def _source_file(target):
    with os.fdopen(os.open(target, os.O_RDONLY | os.O_NONBLOCK), "rb") as handle:
        opened = os.fstat(handle.fileno())
        if not stat.S_ISREG(opened.st_mode):
            raise ValidationError("source is no longer a regular file")
        yield handle, opened.st_size
        closed = os.fstat(handle.fileno())
        if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != (
            closed.st_dev, closed.st_ino, closed.st_size, closed.st_mtime_ns, closed.st_ctime_ns
        ):
            raise ValidationError("source changed during its bounded read")


def source_anchor(repository: Path, path: str, symbol: str | None = None, *, with_stamp=False) -> dict:
    relative_path(path)
    repository = repository.resolve()
    target = (repository / path).resolve()
    if not target.is_relative_to(repository) or not target.is_file():
        raise ValidationError(f"map source is missing or escapes the repository: {path}")
    before = _source_stamp(repository, path)
    line = end_line = None
    try:
        if symbol is None:
            hasher = hashlib.sha256()
            with _source_file(target) as (handle, remaining):
                while remaining:
                    chunk = handle.read(min(65536, remaining))
                    if not chunk:
                        raise ValidationError("source shortened during its bounded read")
                    hasher.update(chunk)
                    remaining -= len(chunk)
            content_hash = hasher.hexdigest()
        else:
            if target.suffix != ".py":
                raise ValidationError("this language has no qualified-symbol resolver; use a verified file anchor and candidate symbol relationships")
            with _source_file(target) as (handle, _):
                raw = handle.read(SOURCE_BYTES + 1)
            if len(raw) > SOURCE_BYTES:
                raise ValidationError("symbol validation exceeds its source budget; use a file anchor")
            for count, _ in enumerate(tokenize.tokenize(io.BytesIO(raw).readline), 1):
                if count > SOURCE_TOKENS:
                    raise ValidationError("symbol validation exceeds its syntax budget; use a file anchor")
            tree = ast.parse(raw)
            found = []
            pending = [(tree, ())]
            while pending:
                node, parents = pending.pop()
                qualified = parents
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    qualified = (*parents, node.name)
                    if ".".join(qualified) == symbol:
                        found.append(node)
                for child in ast.iter_child_nodes(node):
                    pending.append((child, qualified))
            if len(found) != 1:
                raise ValidationError(f"symbol {symbol} is absent or ambiguous in {path}")
            node = found[0]
            line = min([node.lineno, *[decorator.lineno for decorator in node.decorator_list]])
            end_line = node.end_lineno
            content_hash = hashlib.sha256(b"".join(raw.splitlines(keepends=True)[line - 1:end_line])).hexdigest()
    except (OSError, SyntaxError, tokenize.TokenError, RecursionError) as exc:
        raise ValidationError(f"cannot validate source anchor {path}: {type(exc).__name__}") from exc
    after = _source_stamp(repository, path)
    if before != after:
        raise ValidationError(f"map source changed while validating: {path}")
    result = {"path": path, "symbol": symbol, "sha256": content_hash}
    if line is not None:
        result.update(line=line, end_line=end_line)
    return {"anchor": result, "source_stamp": after} if with_stamp else result


def read_json(path: Path, limit=RECORD_BYTES) -> dict:
    if path.is_symlink():
        raise ValidationError("map JSON files cannot be symlinks")
    with path.open("rb") as handle:
        raw = handle.read(limit + 1)
    if len(raw) > limit:
        raise ValidationError(f"map file exceeds {limit} bytes: {path.name}")
    try:
        result = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError(f"invalid map JSON: {path.name}") from exc
    if not isinstance(result, dict):
        raise ValidationError(f"map file must be an object: {path.name}")
    return result


def manifest(repository: Path) -> dict:
    body = read_json(_directory(repository) / "manifest.json", 4096)
    if body.keys() != {"schema_version", "repo_id"} or type(body["schema_version"]) is not int or body["schema_version"] != SCHEMA_VERSION:
        raise ValidationError("unknown map manifest structure; migrate the schema")
    try:
        if str(uuid.UUID(body["repo_id"])) != body["repo_id"]:
            raise ValueError
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValidationError("map repo_id must be a canonical UUID") from exc
    return body


def _directory(repository: Path) -> Path:
    directory = repository / ".hx" / "map"
    if (repository / ".hx").is_symlink() or directory.is_symlink():
        raise ValidationError("portable map directories cannot be symlinks")
    return directory


def records(repository: Path):
    directory = _directory(repository)
    for entry in sorted(directory.iterdir()):
        if entry.name in {"manifest.json", "README.md", "vocabulary.json"}:
            continue
        if entry.name not in KINDS.values() or not entry.is_dir() or entry.is_symlink():
            raise ValidationError(f"unknown map structural directory: {entry.name}")
        kind = next(key for key, folder in KINDS.items() if folder == entry.name)
        with os.scandir(entry) as scan:
            for item in scan:
                path = Path(item.path)
                if path.suffix != ".json" or not item.is_file(follow_symlinks=False):
                    raise ValidationError(f"map shards must be ordinary JSON files: {path.name}")
                record = read_json(path)
                validate_record(record)
                if record["id"] != path.stem or record["kind"] != kind:
                    raise ValidationError(f"map shard path disagrees with its ID/kind: {path}")
                yield path, record


def validate_relation(source_kind, edge_kind, target_kind):
    allowed = {
        "implements": source_kind in {"component", "file"} and target_kind in {"behavior", "interface"},
        "contains": source_kind == "component" and target_kind in {"component", "file", "interface"},
        "depends_on": True,
        "provides": source_kind in {"component", "resource"} and target_kind == "interface",
        "consumes": source_kind in {"component", "check"} and target_kind in {"interface", "resource"},
        "checked_by": source_kind != "check" and target_kind == "check",
    }
    if not allowed[edge_kind]:
        raise ValidationError(f"invalid typed relationship: {source_kind} {edge_kind} {target_kind}")


@contextmanager
def staged(repository: Path):
    """Freeze a validated graph on disk, one record at a time."""
    info = manifest(repository)
    nodes = edges = 0
    with tempfile.TemporaryDirectory(prefix="hx-map-check-") as temporary:
        with closing(sqlite3.connect(Path(temporary) / "graph.sqlite")) as stage:
            stage.execute("PRAGMA cache_size=-1024")
            stage.execute("PRAGMA temp_store=FILE")
            stage.execute("CREATE TABLE nodes(id TEXT PRIMARY KEY,kind TEXT,payload TEXT)")
            stage.execute("CREATE TABLE edges(source TEXT,kind TEXT,target TEXT)")
            stage.execute("CREATE TABLE anchors(path TEXT,symbol TEXT,stamp TEXT,payload TEXT,PRIMARY KEY(path,symbol))")
            for _, record in records(repository):
                try:
                    stage.execute("INSERT INTO nodes VALUES(?,?,?)", (record["id"], record["kind"], canonical(record)))
                except sqlite3.IntegrityError as exc:
                    raise ValidationError(f"duplicate map ID {record['id']}") from exc
                for anchor in record["anchors"]:
                    captured = source_anchor(repository, anchor["path"], anchor["symbol"], with_stamp=True)
                    actual = captured["anchor"]
                    if actual["sha256"] != anchor["sha256"]:
                        raise ValidationError(f"stale map anchor: {record['id']} -> {anchor['path']}")
                    stage.execute("INSERT OR REPLACE INTO anchors VALUES(?,?,?,?)", (anchor["path"], anchor["symbol"] or "", captured["source_stamp"], canonical(actual)))
                for edge in record["edges"]:
                    stage.execute("INSERT INTO edges VALUES(?,?,?)", (record["id"], edge["kind"], edge["to"]))
                    edges += 1
                nodes += 1
            for source, relation, target, source_kind, target_kind in stage.execute(
                "SELECT e.source,e.kind,e.target,s.kind,t.kind FROM edges e JOIN nodes s ON e.source=s.id LEFT JOIN nodes t ON e.target=t.id"
            ):
                if target_kind is None:
                    raise ValidationError(f"map edge {source} -> {target} has no endpoint")
                validate_relation(source_kind, relation, target_kind)
            hasher = hashlib.sha256(canonical(info).encode())
            for row in stage.execute("SELECT payload FROM nodes ORDER BY id"):
                hasher.update(row[0].encode() + b"\n")
            from .map_learning import vocabulary
            hints = vocabulary(repository)
            if hints is not None:
                hasher.update(canonical(hints).encode())
            yield stage, {"valid": True, "repo_id": info["repo_id"], "records": nodes, "edges": edges, "map_hash": hasher.hexdigest()}


def check(repository: Path) -> dict:
    with staged(repository) as (_, result):
        return result


def _git(repository: Path, *arguments) -> str:
    result = subprocess.run(["git", "-C", str(repository), *arguments], capture_output=True, text=True, check=False)
    if result.returncode:
        raise ValidationError("map operation requires a valid Git repository and revision")
    return result.stdout.strip()


def _dirty(repository: Path, *scope) -> bool:
    with subprocess.Popen(["git", "-C", str(repository), "status", "--porcelain", "--", *scope],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        changed = bool(process.stdout.read(1))
        if changed:
            process.terminate()
        process.stdout.close()
        result = process.wait()
        if result and not changed:
            raise ValidationError("could not inspect map worktree state")
    return changed


@contextmanager
def _blob(repository: Path, commit: str, path: str):
    with subprocess.Popen(["git", "-C", str(repository), "cat-file", "blob", f"{commit}:{path}"],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        try:
            yield process.stdout
        finally:
            process.stdout.close()
            result = process.wait()
        if result:
            raise ValidationError(f"map source is not available in the committed tree: {path}")


def _committed_anchor(repository, commit, anchor):
    with _blob(repository, commit, anchor["path"]) as stream:
        if anchor["symbol"] is None:
            hasher = hashlib.sha256()
            while chunk := stream.read(65536):
                hasher.update(chunk)
            return hasher.hexdigest()
        with tempfile.TemporaryDirectory(prefix="hx-map-symbol-") as temporary:
            target = Path(temporary) / "source.py"
            with target.open("wb") as handle:
                remaining = SOURCE_BYTES + 1
                while remaining:
                    chunk = stream.read(min(65536, remaining))
                    if not chunk:
                        break
                    handle.write(chunk)
                    remaining -= len(chunk)
            if remaining == 0:
                raise ValidationError("committed symbol source exceeds its syntax budget")
            return source_anchor(Path(temporary), "source.py", anchor["symbol"])["sha256"]


def _committed_json(repository, commit, path, expected):
    with _blob(repository, commit, path) as stream:
        raw = stream.read(RECORD_BYTES + 1)
        if len(raw) > RECORD_BYTES:
            raise ValidationError("committed map record exceeds its byte bound")
        try:
            same = json.loads(raw) == expected
        except (ValueError, UnicodeDecodeError):
            same = False
        if not same:
            raise Conflict(f"map file differs from its committed contents: {path}")


def index_relations(db, repository, snapshot, record):
    db.execute('DELETE FROM map_aliases WHERE repository=? AND snapshot=? AND record_id=? AND version<>?', (repository, snapshot, record['id'], record['version']))
    db.execute('DELETE FROM map_publications WHERE repository=? AND snapshot=? AND record_id=? AND version<>?', (repository, snapshot, record['id'], record['version']))
    db.execute("DELETE FROM map_relations WHERE repository=? AND snapshot=? AND source=?",
               (repository, snapshot, record["id"]))
    db.executemany("INSERT INTO map_relations VALUES(?,?,?,?,?,?)",
        ((repository, snapshot, record["id"], edge["kind"], edge["to"], edge["status"]) for edge in record["edges"]))
    db.execute("DELETE FROM map_sources WHERE repository=? AND snapshot=? AND record_id=?", (repository, snapshot, record["id"]))
    db.executemany("INSERT OR REPLACE INTO map_sources VALUES(?,?,?,?,?,?)",
        ((repository, snapshot, record["id"], anchor["path"], anchor["symbol"] or "", anchor["sha256"]) for anchor in record["anchors"]))
    db.execute("INSERT OR IGNORE INTO map_search_keys(repository,snapshot,record_id) VALUES(?,?,?)", (repository, snapshot, record["id"]))
    key = db.execute("SELECT rowid FROM map_search_keys WHERE repository=? AND snapshot=? AND record_id=?", (repository, snapshot, record["id"])).fetchone()[0]
    db.execute("INSERT OR REPLACE INTO map_search(rowid,summary,semantic) VALUES(?,?,?)",
        (key, record["summary"], record["id"] + " " + canonical(record["data"]) + " " + canonical(record["anchors"]) + " " + canonical(record["attributes"])))


def import_baseline(store: ContinuityStore, repository: Path) -> dict:
    """Import one immutable committed baseline; dirty overlays use proposal APIs."""
    commit = _git(repository, "rev-parse", "HEAD")
    if _dirty(repository):
        raise Conflict("commit source and map together before importing a baseline")
    snapshot = f"git:{commit}"
    with staged(repository) as (stage, result):
        from .map_learning import vocabulary, import_vocabulary
        hints = vocabulary(repository)
        if hints is not None:
            _committed_json(repository, commit, '.hx/map/vocabulary.json', hints)
        _committed_json(repository, commit, ".hx/map/manifest.json", manifest(repository))
        for row in stage.execute("SELECT payload FROM nodes ORDER BY id"):
            record = json.loads(row[0])
            _committed_json(repository, commit, f".hx/map/{KINDS[record['kind']]}/{record['id']}.json", record)
            for anchor in record["anchors"]:
                if _committed_anchor(repository, commit, anchor) != anchor["sha256"]:
                    raise Conflict(f"source anchor differs from the committed tree: {anchor['path']}")
        with store.transaction() as tx:
            existing = tx.db.execute("SELECT map_hash FROM map_snapshots WHERE repository=? AND snapshot=?", (result["repo_id"], snapshot)).fetchone()
            if existing:
                if existing[0] != result["map_hash"]:
                    raise Conflict("an immutable map snapshot has different contents")
                return {**result, "snapshot": snapshot}
            tx._change()
            tx.db.execute("INSERT INTO map_snapshots VALUES(?,?,?,?,?,?)",
                          (result["repo_id"], snapshot, commit, str(repository.resolve()), "baseline", result["map_hash"]))
            for path, symbol, stamp, payload in stage.execute("SELECT * FROM anchors"):
                tx.db.execute("INSERT INTO map_anchor_cache VALUES(?,?,?,?,?,?) ON CONFLICT(repository,worktree,path,symbol) DO UPDATE SET source_stamp=excluded.source_stamp,payload=excluded.payload",
                              (result["repo_id"], str(repository.resolve()), path, symbol, stamp, payload))
            for row in stage.execute("SELECT id,kind,payload FROM nodes ORDER BY id"):
                record = json.loads(row[2])
                tx.db.execute("INSERT INTO map_records VALUES(?,?,?,?,?,?,?,?,?)",
                    (result["repo_id"], snapshot, record["id"], record["version"], record["kind"], row[2], "[]",
                     canonical({"anchors": record["anchors"]}), "current"))
                tx.db.execute("INSERT INTO map_heads VALUES(?,?,?,?)", (result["repo_id"], snapshot, record["id"], record["version"]))
                index_relations(tx.db, result["repo_id"], snapshot, record)
            import_vocabulary(tx, repository, result['repo_id'], snapshot, hints)
            if _git(repository, "rev-parse", "HEAD") != commit or _dirty(repository):
                raise Conflict("repository changed while importing its map")
    return {**result, "snapshot": snapshot}


def _snapshot(store, repository, snapshot):
    info = manifest(repository)
    row = store.db.execute("SELECT * FROM map_snapshots WHERE repository=? AND snapshot=?", (info["repo_id"], snapshot)).fetchone()
    if row is None:
        raise ValidationError("unknown map snapshot for this repository")
    if row["kind"] == "overlay":
        from .map_updates import require_worktree
        require_worktree(store.db, repository, info["repo_id"], snapshot)
    ancestor = subprocess.run(["git", "-C", str(repository), "merge-base", "--is-ancestor", row["base_commit"], "HEAD"], capture_output=True)
    if ancestor.returncode != 0:
        raise Conflict("map baseline is not an ancestor of this worktree; import its branch baseline")
    return info, row


def get_record(store: ContinuityStore, repository: Path, snapshot: str, record_id: str, *, refresh=False) -> dict:
    identifier(record_id)
    info, _ = _snapshot(store, repository, snapshot)
    row = store.db.execute("SELECT r.payload,r.applicability FROM map_records r JOIN map_heads h USING(repository,snapshot,record_id,version) WHERE repository=? AND snapshot=? AND record_id=?",
                          (info["repo_id"], snapshot, record_id)).fetchone()
    if row is None:
        raise ValidationError(f"unknown map record {record_id}")
    record = json.loads(row["payload"])
    statuses = {(item["kind"], item["target"]): item["status"] for item in store.db.execute(
        "SELECT kind,target,status FROM map_relations WHERE repository=? AND snapshot=? AND source=?",
        (info["repo_id"], snapshot, record_id))}
    for edge in record["edges"]:
        edge["status"] = statuses.get((edge["kind"], edge["to"]), "stale")
    applicability = row["applicability"]
    issues = []
    if store.db.execute("SELECT 1 FROM map_refresh_queue WHERE repository=? AND snapshot=? AND record_id=?", (info["repo_id"], snapshot, record_id)).fetchone():
        applicability = "pending"
        issues.append("Source refresh is pending.")
    for anchor in record["anchors"]:
        try:
            worktree = str(repository.resolve())
            stamp = _source_stamp(repository, anchor["path"])
            cached = store.db.execute("SELECT source_stamp,payload FROM map_anchor_cache WHERE repository=? AND worktree=? AND path=? AND symbol=?",
                                      (info["repo_id"], worktree, anchor["path"], anchor["symbol"] or "")).fetchone()
            if not refresh and cached and cached["source_stamp"] == stamp:
                actual = json.loads(cached["payload"])
                if _source_stamp(repository, anchor["path"]) != stamp:
                    raise ValidationError("source changed during map lookup")
            else:
                captured = source_anchor(repository, anchor["path"], anchor["symbol"], with_stamp=True)
                actual = captured["anchor"]
                # This is a rebuildable lookup cache, never a new map assertion.
                with store.transaction() as tx:
                    tx._change()
                    tx.db.execute("INSERT INTO map_anchor_cache VALUES(?,?,?,?,?,?) ON CONFLICT(repository,worktree,path,symbol) DO UPDATE SET source_stamp=excluded.source_stamp,payload=excluded.payload",
                                  (info["repo_id"], worktree, anchor["path"], anchor["symbol"] or "", captured["source_stamp"], canonical(actual)))
            if actual["sha256"] != anchor["sha256"]:
                raise ValidationError(f"source changed: {anchor['path']}")
            anchor.update(actual)
        except ValidationError as exc:
            applicability = "stale"
            issues.append(str(exc))
    return {"record": record, "applicability": applicability, "issues": issues,
            "validated_edges": [edge for edge in record["edges"] if edge["status"] == "validated"]
            if applicability == "current" and record["claim"] != "hypothesis" else []}


def export_snapshot(store: ContinuityStore, repository: Path, snapshot: str) -> dict:
    """Validate a staged export before touching portable shards; output is deterministic."""
    info, _ = _snapshot(store, repository, snapshot)
    if _dirty(repository, ".hx/map"):
        raise Conflict("map export would overwrite uncommitted map edits; commit/import or reconcile them first")
    with tempfile.TemporaryDirectory(prefix="hx-map-export-") as temporary:
        staged_repo = Path(temporary)
        destination = staged_repo / ".hx" / "map"
        destination.mkdir(parents=True)
        atomic_write_text(destination / "manifest.json", canonical(info) + "\n")
        count = 0
        rows = store.db.execute("SELECT record_id FROM map_heads WHERE repository=? AND snapshot=? ORDER BY record_id", (info["repo_id"], snapshot))
        for row in rows:
            selected = get_record(store, repository, snapshot, row[0], refresh=True)
            if selected["applicability"] != "current":
                raise Conflict(f"cannot export stale map record {row[0]}")
            record = selected["record"]
            validate_record(record)
            atomic_write_text(destination / KINDS[record["kind"]] / f"{record['id']}.json", canonical(record) + "\n")
            count += 1
        # Nodes/edges were validated by import/proposal transactions. Source
        # anchors were revalidated above against the actual combined worktree.
        target = repository / ".hx" / "map"
        for path, record in records(staged_repo):
            atomic_write_text(target / path.relative_to(destination), canonical(record) + "\n")
        for path, record in records(repository):
            if not (destination / path.relative_to(target)).exists():
                path.unlink()
        from .map_learning import portable_vocabulary
        vocabulary_nodes = (get_record(store, repository, snapshot, row[0])['record'] for row in store.db.execute('SELECT DISTINCT record_id FROM map_aliases WHERE repository=? AND snapshot=? ORDER BY record_id LIMIT 128', (info['repo_id'], snapshot)))
        vocabulary_write = portable_vocabulary(store, repository, snapshot, vocabulary_nodes)
        if vocabulary_write:
            atomic_write_text(*vocabulary_write)
        atomic_write_text(target / "manifest.json", canonical(info) + "\n")
    return {"repo_id": info["repo_id"], "snapshot": snapshot, "records": count}


def export_records(store, repository, snapshot, record_ids):
    """Replayable scoped publication; unrelated stale shards are left untouched."""
    import fcntl
    ids = list(dict.fromkeys(record_ids))
    if not ids or len(ids) > 64:
        raise ValidationError('scoped export requires 1–64 record IDs')
    _snapshot(store, repository, snapshot)
    git_dir = Path(_git(repository, 'rev-parse', '--absolute-git-dir'))
    with (git_dir / 'hx-map-publication.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        selected = {}
        for identity in ids:
            current = get_record(store, repository, snapshot, identity, refresh=True)
            if current['applicability'] != 'current':
                raise Conflict('cannot publish stale finding: ' + identity)
            selected[identity] = current['record']
            for edge in current['record']['edges']:
                if edge['to'] not in ids:
                    if len(ids) >= 64:
                        raise ValidationError('published dependency closure exceeds 64 records')
                    # Include only dependencies missing from the portable map.
                    target = get_record(store, repository, snapshot, edge['to'])['record']
                    path = repository / '.hx/map' / KINDS[target['kind']] / (target['id'] + '.json')
                    if not path.exists():
                        ids.append(edge['to'])
        writes = []
        for record in selected.values():
            target = repository / '.hx/map' / KINDS[record['kind']] / (record['id'] + '.json')
            text = canonical(record) + '\n'
            if target.exists() and read_json(target) == record:
                continue  # A prior interrupted export already wrote this shard.
            if _dirty(repository, target.relative_to(repository).as_posix()):
                raise Conflict('publication would overwrite local map edits: ' + record['id'])
            writes.append((target, text))
        from .map_learning import portable_vocabulary
        vocabulary_write = portable_vocabulary(store, repository, snapshot, selected.values())
        if vocabulary_write:
            writes.append(vocabulary_write)
        # All conflicts are checked before the first write; atomic files make an
        # interrupted identical request replayable without discarding local edits.
        for target, text in writes:
            atomic_write_text(target, text)
        return {'records': sorted(selected), 'written': len(writes) - bool(vocabulary_write), 'vocabulary_written': bool(vocabulary_write)}


def initialize(repository: Path) -> dict:
    directory = _directory(repository)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "manifest.json"
    if path.exists():
        return manifest(repository)
    body = {"schema_version": SCHEMA_VERSION, "repo_id": str(uuid.uuid4())}
    # Publish a complete manifest without replacing another initializer's ID.
    # Keep temporary files outside the map's structural directory.
    with tempfile.TemporaryDirectory(prefix=".map-init-", dir=directory.parent) as temporary:
        candidate = Path(temporary) / "manifest.json"
        atomic_write_text(candidate, canonical(body) + "\n")
        try:
            os.link(candidate, path)
        except FileExistsError:
            return manifest(repository)
    return body


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx map")
    parser.add_argument("--root")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("init", "check", "anchor", "import", "export", "publish", "get", "overlay", "propose", "refresh", "plan-context"):
        command = commands.add_parser(name)
        command.add_argument("--repo", required=True)
        command.add_argument("--root", default=argparse.SUPPRESS)
        if name == "anchor":
            command.add_argument("path")
            command.add_argument("--symbol")
        if name in {"export", "publish", "get", "overlay", "refresh", "plan-context"}:
            command.add_argument("--snapshot", required=True)
        if name == 'publish':
            command.add_argument('--run', required=True)
        if name == "refresh":
            command.add_argument("--path", action="append", default=[])
            command.add_argument("--limit", type=int, default=16)
        if name == "plan-context":
            command.add_argument("--goal", required=True)
            command.add_argument("--require", action="append", default=[])
            command.add_argument("--max-bytes", type=int, default=16000)
        if name == "propose":
            command.add_argument("--file", required=True)
        if name == "get":
            command.add_argument("record_id")
    args = parser.parse_args(argv)
    repository = Path(args.repo).resolve()
    if args.command == "init":
        result = initialize(repository)
    elif args.command == "check":
        result = check(repository)
    elif args.command == "anchor":
        result = source_anchor(repository, args.path, args.symbol)
    else:
        with ContinuityStore(root) as store:
            if args.command == "import":
                result = import_baseline(store, repository)
            elif args.command == "get":
                result = get_record(store, repository, args.snapshot, args.record_id)
            elif args.command == "overlay":
                from .map_updates import create_overlay
                result = create_overlay(store, repository, args.snapshot)
            elif args.command == "propose":
                from .map_updates import PATCH_BYTES, propose
                result = propose(store, repository, read_json(Path(args.file), PATCH_BYTES),
                                 worker_id=(os.environ if env is None else env).get("HARNESS_ID"))
            elif args.command == "refresh":
                from .map_refresh import queue_sources, drain
                if args.path:
                    queue_sources(store, repository, args.snapshot, args.path)
                result = drain(store, repository, args.snapshot, limit=args.limit)
            elif args.command == 'publish':
                from .map_learning import transfer
                transfer(store, repository, args.snapshot, args.run)
                ids = [row[0] for row in store.db.execute('SELECT record_id FROM map_publications WHERE run_id=? AND repository=? ORDER BY record_id LIMIT 65', (args.run, manifest(repository)['repo_id']))]
                result = export_records(store, repository, args.snapshot, ids) if ids else {'records': [], 'written': 0}
            elif args.command == "plan-context":
                from .retrieval import plan_context
                with Path(args.goal).open("rb") as handle:
                    raw = handle.read(16001)
                if len(raw) > 16000:
                    raise ValidationError("planning goal exceeds 16 KiB; use a focused goal")
                try:
                    goal = raw.decode("utf-8")
                except UnicodeDecodeError as exc:
                    raise ValidationError("planning goal must be UTF-8") from exc
                result = plan_context(store, repository, args.snapshot, goal, required=args.require, max_bytes=args.max_bytes)
            else:
                result = export_snapshot(store, repository, args.snapshot)
    print(canonical(result))
    return 0
