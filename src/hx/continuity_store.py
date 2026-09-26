"""Transactional authority for current-task continuity (schema 5).

All mutations, including artifact installation, use a short BEGIN IMMEDIATE
transaction. Model calls and tool execution belong outside this boundary.
Legacy files remain authoritative until the explicit migration cutover.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .errors import HxError, ValidationError
from .facts import validate_payload

SCHEMA_VERSION = 5
ARTIFACT_CHUNK_BYTES = 65536
DISPOSITIONS = {"reduced", "extracted", "no_change", "dropped", "pending"}
RECORD_KINDS = {"goal", "constraint", "decision", "finding", "search", "command", "cursor", "dead_end"}
EVENT_KINDS = {
    "request", "correction", "assistant_message", "tool_result", "progress",
    "source_change", "check_result", "spawn", "finish", "boundary",
}
HASH = re.compile(r"[0-9a-f]{64}\Z")


class Conflict(HxError):
    """The read version no longer matches; retry from a fresh snapshot."""


def canonical(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"continuity: invalid JSON value: {exc}") from exc


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def _id(value: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 512 or any(ord(c) < 32 for c in value):
        raise ValidationError("continuity: IDs must be nonempty text without control characters (max 512)")
    return value


SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger_meta (
    singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL
);
INSERT OR IGNORE INTO ledger_meta VALUES(1,0);
CREATE TABLE IF NOT EXISTS task_heads (
    task_id TEXT PRIMARY KEY, revision INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    task_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>0),
    parent_id TEXT REFERENCES task_heads(task_id), payload TEXT NOT NULL,
    created_at REAL NOT NULL, PRIMARY KEY(task_id,revision)
);
CREATE TABLE IF NOT EXISTS runs (
    run_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, task_revision INTEGER NOT NULL,
    worker_id TEXT NOT NULL, map_revision INTEGER NOT NULL, phase TEXT NOT NULL,
    checkpoint_id TEXT, outcome TEXT, started_at REAL NOT NULL, ended_at REAL,
    FOREIGN KEY(task_id,task_revision) REFERENCES tasks(task_id,revision)
);
CREATE UNIQUE INDEX IF NOT EXISTS active_worker ON runs(worker_id) WHERE ended_at IS NULL;
CREATE TABLE IF NOT EXISTS artifacts (
    hash TEXT PRIMARY KEY, size INTEGER NOT NULL CHECK(size>=0), created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS artifact_refs (
    owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, slot TEXT NOT NULL,
    hash TEXT NOT NULL REFERENCES artifacts(hash),
    PRIMARY KEY(owner_type,owner_id,slot)
);
CREATE INDEX IF NOT EXISTS artifact_ref_hash ON artifact_refs(hash);
CREATE TABLE IF NOT EXISTS cursors (
    run_id TEXT NOT NULL REFERENCES runs(run_id), stream_id TEXT NOT NULL,
    head_seq INTEGER NOT NULL DEFAULT 0, classified_seq INTEGER NOT NULL DEFAULT 0,
    revision INTEGER NOT NULL DEFAULT 0,
    CHECK(classified_seq>=0 AND classified_seq<=head_seq), PRIMARY KEY(run_id,stream_id)
);
CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stream_id TEXT NOT NULL,
    seq INTEGER NOT NULL, capture_key TEXT NOT NULL, kind TEXT NOT NULL,
    payload TEXT NOT NULL, payload_hash TEXT NOT NULL, disposition TEXT,
    created_at REAL NOT NULL,
    CHECK(disposition IS NULL OR disposition IN ('reduced','extracted','no_change','dropped','pending')),
    UNIQUE(run_id,stream_id,seq), UNIQUE(run_id,capture_key),
    FOREIGN KEY(run_id,stream_id) REFERENCES cursors(run_id,stream_id)
);
CREATE INDEX IF NOT EXISTS pending_events ON events(run_id,stream_id,seq) WHERE disposition='pending';
CREATE TABLE IF NOT EXISTS capture_sources (
    source_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
    stream_id TEXT NOT NULL, revision INTEGER NOT NULL, generation TEXT NOT NULL,
    committed_offset INTEGER NOT NULL CHECK(committed_offset>=0),
    decoder_version TEXT NOT NULL, payload TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS passes (
    pass_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, stream_id TEXT NOT NULL,
    from_seq INTEGER NOT NULL, to_seq INTEGER NOT NULL CHECK(to_seq>=from_seq),
    task_revision INTEGER NOT NULL, cursor_revision INTEGER NOT NULL,
    event_digest TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
    FOREIGN KEY(run_id,stream_id) REFERENCES cursors(run_id,stream_id)
);
CREATE TABLE IF NOT EXISTS record_heads (
    record_id TEXT PRIMARY KEY, version INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
    record_id TEXT NOT NULL, version INTEGER NOT NULL CHECK(version>0),
    task_id TEXT NOT NULL REFERENCES task_heads(task_id), kind TEXT NOT NULL,
    payload TEXT NOT NULL, evidence TEXT NOT NULL, inputs TEXT NOT NULL,
    supersedes INTEGER, validity TEXT NOT NULL CHECK(validity IN ('current','invalid','dropped')),
    retention TEXT NOT NULL CHECK(retention IN ('context','store')),
    reason TEXT NOT NULL, consuming_step TEXT, expires_when TEXT NOT NULL,
    storage_bytes INTEGER NOT NULL CHECK(storage_bytes>=0),
    PRIMARY KEY(record_id,version)
);
CREATE INDEX IF NOT EXISTS task_records ON records(task_id,validity);
CREATE TABLE IF NOT EXISTS map_heads (
    repository TEXT NOT NULL, snapshot TEXT NOT NULL, record_id TEXT NOT NULL,
    version INTEGER NOT NULL, PRIMARY KEY(repository,snapshot,record_id)
);
CREATE TABLE IF NOT EXISTS map_records (
    repository TEXT NOT NULL, snapshot TEXT NOT NULL, record_id TEXT NOT NULL,
    version INTEGER NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
    evidence TEXT NOT NULL, inputs TEXT NOT NULL, applicability TEXT NOT NULL,
    PRIMARY KEY(repository,snapshot,record_id,version)
);
CREATE TABLE IF NOT EXISTS record_entities (
    record_id TEXT NOT NULL, version INTEGER NOT NULL,
    repository TEXT NOT NULL, snapshot TEXT NOT NULL, entity_id TEXT NOT NULL, entity_version INTEGER NOT NULL,
    PRIMARY KEY(record_id,version,repository,snapshot,entity_id),
    FOREIGN KEY(record_id,version) REFERENCES records(record_id,version),
    FOREIGN KEY(repository,snapshot,entity_id,entity_version)
        REFERENCES map_records(repository,snapshot,record_id,version)
);
CREATE TABLE IF NOT EXISTS receipts (
    receipt_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
    check_id TEXT NOT NULL, check_version TEXT NOT NULL, payload TEXT NOT NULL,
    inputs_hash TEXT NOT NULL, environment_hash TEXT NOT NULL,
    start_hash TEXT NOT NULL, end_hash TEXT NOT NULL, exit_code INTEGER NOT NULL,
    artifact_hash TEXT NOT NULL REFERENCES artifacts(hash), duration REAL NOT NULL,
    valid INTEGER NOT NULL CHECK(valid IN (0,1))
);
CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
    ledger_revision INTEGER NOT NULL, map_revision INTEGER NOT NULL,
    task_revision INTEGER NOT NULL, payload TEXT NOT NULL,
    packet_hash TEXT NOT NULL REFERENCES artifacts(hash), acknowledged INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS retrieval_runs (
    retrieval_id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES task_heads(task_id),
    input_hash TEXT NOT NULL, payload TEXT NOT NULL, UNIQUE(task_id,input_hash)
);
CREATE TABLE IF NOT EXISTS leases (
    repository TEXT NOT NULL, canonical_path TEXT NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id), payload TEXT NOT NULL,
    PRIMARY KEY(repository,canonical_path)
);
CREATE TABLE IF NOT EXISTS outbox (
    operation_id TEXT PRIMARY KEY, kind TEXT NOT NULL, idempotency_key TEXT NOT NULL,
    payload TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
    attempts INTEGER NOT NULL DEFAULT 0, available_at REAL NOT NULL,
    lease_token TEXT, lease_until REAL, last_error TEXT,
    CHECK(status IN ('pending','claimed','delivered')), UNIQUE(kind,idempotency_key)
);
CREATE INDEX IF NOT EXISTS outbox_ready ON outbox(status,available_at,lease_until);
"""

MIGRATION_2 = """
CREATE TABLE IF NOT EXISTS event_origins (
    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES capture_sources(source_id),
    generation TEXT NOT NULL, byte_offset INTEGER NOT NULL,
    decoder_version TEXT NOT NULL,
    PRIMARY KEY(event_id,source_id,generation,byte_offset)
);
CREATE INDEX IF NOT EXISTS origin_source ON event_origins(source_id,generation,byte_offset);
"""

MIGRATION_3 = """
CREATE TABLE IF NOT EXISTS artifact_chunks (
    hash TEXT NOT NULL REFERENCES artifacts(hash) ON DELETE CASCADE,
    offset INTEGER NOT NULL, size INTEGER NOT NULL, chunk_hash TEXT NOT NULL,
    PRIMARY KEY(hash,offset)
);
"""

MIGRATION_4 = """
CREATE TABLE IF NOT EXISTS native_bindings (
    binding_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
    stream_id TEXT NOT NULL, adapter TEXT NOT NULL, session_id TEXT NOT NULL,
    decoder_version TEXT NOT NULL, latest_usage TEXT,
    UNIQUE(adapter,session_id,stream_id),
    FOREIGN KEY(run_id,stream_id) REFERENCES cursors(run_id,stream_id)
);
CREATE TABLE IF NOT EXISTS native_deliveries (
    binding_id TEXT NOT NULL REFERENCES native_bindings(binding_id),
    delivery_id TEXT NOT NULL, payload_hash TEXT NOT NULL, event_ids TEXT NOT NULL,
    PRIMARY KEY(binding_id,delivery_id)
);
CREATE TABLE IF NOT EXISTS native_event_origins (
    binding_id TEXT NOT NULL, delivery_id TEXT NOT NULL,
    event_id TEXT NOT NULL REFERENCES events(event_id) ON DELETE CASCADE,
    PRIMARY KEY(binding_id,delivery_id,event_id),
    FOREIGN KEY(binding_id,delivery_id) REFERENCES native_deliveries(binding_id,delivery_id)
);
CREATE TABLE IF NOT EXISTS progress_heads (
    task_id TEXT PRIMARY KEY REFERENCES task_heads(task_id), revision INTEGER NOT NULL,
    run_id TEXT NOT NULL REFERENCES runs(run_id), cursor_id TEXT NOT NULL REFERENCES record_heads(record_id)
);
CREATE TABLE IF NOT EXISTS progress_steps (
    task_id TEXT NOT NULL REFERENCES task_heads(task_id), step_id TEXT NOT NULL,
    status TEXT NOT NULL, payload TEXT NOT NULL, evidence TEXT NOT NULL,
    PRIMARY KEY(task_id,step_id)
);
CREATE TABLE IF NOT EXISTS progress_deliverables (
    task_id TEXT NOT NULL REFERENCES task_heads(task_id), path TEXT NOT NULL,
    payload TEXT NOT NULL, evidence TEXT NOT NULL, PRIMARY KEY(task_id,path)
);
CREATE TABLE IF NOT EXISTS progress_updates (
    run_id TEXT NOT NULL REFERENCES runs(run_id), request_hash TEXT NOT NULL,
    payload TEXT NOT NULL, PRIMARY KEY(run_id,request_hash)
);
"""


MIGRATION_5 = """
CREATE TABLE IF NOT EXISTS check_executions (
    execution_id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
    request_id TEXT NOT NULL, check_id TEXT NOT NULL, check_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('running','finished')),
    payload TEXT NOT NULL, UNIQUE(run_id,request_id)
);
CREATE INDEX IF NOT EXISTS reusable_receipts ON receipts(run_id,check_id,check_version,inputs_hash,environment_hash,valid);
"""


class ContinuityStore:
    """One local fleet authority; use a separate connection per thread/process."""

    def __init__(self, root: Path, *, timeout: float = 10.0):
        self.root = Path(root)
        self.path = self.root / "state" / "continuity.sqlite"
        self.artifacts = self.root / "state" / "artifacts"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.artifacts.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=timeout, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA foreign_keys=ON")
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            # Per-connection page cache, not a total process RSS limit. Keep large
            # temporary sorts on disk instead of growing alongside worker count.
            self.db.execute("PRAGMA cache_size=-2048")
            self.db.execute("PRAGMA temp_store=FILE")
            self.db.execute("PRAGMA mmap_size=0")
            self.db.execute("BEGIN IMMEDIATE")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, 1, 2, 3, 4, SCHEMA_VERSION):
                raise ValidationError(f"continuity: unsupported schema {version}; expected {SCHEMA_VERSION}")
            if version == 0:
                # executescript commits implicitly; individual statements preserve the lock.
                for statement in SCHEMA.split(";"):
                    if statement.strip():
                        self.db.execute(statement)
            if version < 2:
                for statement in MIGRATION_2.split(";"):
                    if statement.strip():
                        self.db.execute(statement)
            if version < 3:
                self.db.execute(MIGRATION_3)
                # One explicit schema-upgrade batch, never a normal capture read.
                for row in self.db.execute("SELECT hash FROM artifacts"):
                    with (self.artifacts / row[0]).open("rb") as handle:
                        offset = 0
                        hasher = hashlib.sha256()
                        while chunk := handle.read(ARTIFACT_CHUNK_BYTES):
                            hasher.update(chunk)
                            self.db.execute("INSERT INTO artifact_chunks VALUES(?,?,?,?)",
                                            (row[0], offset, len(chunk), hashlib.sha256(chunk).hexdigest()))
                            offset += len(chunk)
                        if hasher.hexdigest() != row[0]:
                            raise ValidationError(f"corrupt artifact {row[0]} during schema upgrade")
            if version < 4:
                for statement in MIGRATION_4.split(";"):
                    if statement.strip():
                        self.db.execute(statement)
            if version < 5:
                for statement in MIGRATION_5.split(";"):
                    if statement.strip():
                        self.db.execute(statement)
            self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            self.db.commit()
        except BaseException:
            self.db.rollback()
            self.db.close()
            raise

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> ContinuityStore:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    @contextmanager
    def transaction(self) -> Iterator[Transaction]:
        if self.db.in_transaction:
            raise ValidationError("continuity: nested transactions are not supported")
        self.db.execute("BEGIN IMMEDIATE")
        tx = Transaction(self)
        try:
            yield tx
            if tx.changed:
                self.db.execute("UPDATE ledger_meta SET revision=revision+1 WHERE singleton=1")
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise
        finally:
            tx.active = False

    def collect_artifacts(self) -> list[str]:
        """Delete unreferenced committed and interrupted artifacts under the writer lock.

        Direct hash columns count as roots too. Deleting a file before committing
        its metadata removal is safe only because no live owner refers to it.
        """
        with self.transaction() as tx:
            live = {row[0] for row in self.db.execute(
                "SELECT hash FROM artifact_refs UNION SELECT artifact_hash FROM receipts "
                "UNION SELECT packet_hash FROM checkpoints"
            )}
            removed = []
            for path in self.artifacts.iterdir():
                if path.is_file() and (HASH.fullmatch(path.name) or path.name.startswith(".pending-")):
                    if path.name not in live:
                        path.unlink()
                        removed.append(path.name)
                        self.db.execute("DELETE FROM artifacts WHERE hash=?", (path.name,))
            # A previous interrupted GC may have removed files but left metadata.
            for row in self.db.execute("SELECT hash FROM artifacts").fetchall():
                if row[0] not in live and not (self.artifacts / row[0]).exists():
                    self.db.execute("DELETE FROM artifacts WHERE hash=?", (row[0],))
                    tx.changed = True
            if removed:
                _sync_directory(self.artifacts)
                tx.changed = True
            return removed


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Transaction:
    def __init__(self, store: ContinuityStore):
        self.store = store
        self.db = store.db
        self.changed = False
        self.active = True

    def _check(self) -> None:
        if not self.active or not self.db.in_transaction:
            raise ValidationError("continuity: transaction is closed")

    def _change(self) -> None:
        self._check()
        self.changed = True

    def task(self, task_id: str) -> dict | None:
        self._check()
        row = self.db.execute(
            "SELECT t.* FROM tasks t JOIN task_heads h USING(task_id,revision) WHERE task_id=?", (task_id,)
        ).fetchone()
        return {**dict(row), "payload": json.loads(row["payload"])} if row else None

    def put_task(self, task_id: str, payload: dict, *, expected_revision: int, parent_id: str | None = None) -> int:
        _id(task_id)
        previous = self.task(task_id)
        actual = previous["revision"] if previous else 0
        if actual != expected_revision:
            raise Conflict(f"task {task_id}: expected revision {expected_revision}, found {actual}")
        if previous and previous["parent_id"] != parent_id:
            raise ValidationError(f"task {task_id}: parent is immutable")
        if task_id == parent_id:
            raise ValidationError("task cannot parent itself")
        body = canonical(payload)
        self._change()
        revision = actual + 1
        self.db.execute("INSERT INTO tasks VALUES(?,?,?,?,?)", (task_id, revision, parent_id, body, time.time()))
        self.db.execute(
            "INSERT INTO task_heads VALUES(?,?) ON CONFLICT(task_id) DO UPDATE SET revision=excluded.revision",
            (task_id, revision),
        )
        return revision

    def start_run(self, task_id: str, task_revision: int, worker_id: str, *, map_revision: int = 0) -> str:
        self._check()
        task = self.task(task_id)
        if task is None or task["revision"] != task_revision:
            raise Conflict(f"task {task_id}: dispatch requires current revision {task_revision}")
        _id(worker_id)
        if self.db.execute("SELECT 1 FROM runs WHERE worker_id=? AND ended_at IS NULL", (worker_id,)).fetchone():
            raise Conflict(f"worker {worker_id}: already assigned")
        run_id = str(uuid.uuid4())
        self._change()
        self.db.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?,?,NULL,NULL,?,NULL)",
            (run_id, task_id, task_revision, worker_id, map_revision, "assigned", time.time()),
        )
        return run_id

    def finish_run(self, run_id: str, outcome: str) -> None:
        self._check()
        _id(outcome)
        row = self.db.execute("SELECT outcome,ended_at FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise ValidationError(f"unknown run {run_id}")
        if row["ended_at"] is not None:
            if row["outcome"] != outcome:
                raise Conflict(f"run {run_id}: already finished as {row['outcome']}")
            return
        self._change()
        self.db.execute("UPDATE runs SET outcome=?,ended_at=? WHERE run_id=?", (outcome, time.time(), run_id))

    def put_artifact(self, data: bytes, *, owner_type: str, owner_id: str, slot: str) -> str:
        """Install and reference bytes in this same transaction; rollback leaves only an orphan."""
        self._check()
        for value in (owner_type, owner_id, slot):
            _id(value)
        sha = hashlib.sha256(data).hexdigest()
        path = self.store.artifacts / sha
        if path.exists():
            hasher = hashlib.sha256()
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(ARTIFACT_CHUNK_BYTES), b""):
                    hasher.update(chunk)
            if hasher.hexdigest() != sha:
                raise ValidationError(f"corrupt artifact {sha}")
        else:
            fd, name = tempfile.mkstemp(prefix=".pending-", dir=self.store.artifacts)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(data)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(name, path)
                _sync_directory(self.store.artifacts)
            finally:
                Path(name).unlink(missing_ok=True)
        self._change()
        self.db.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?,?)", (sha, len(data), time.time()))
        view = memoryview(data)
        self.db.executemany("INSERT OR IGNORE INTO artifact_chunks VALUES(?,?,?,?)", (
            (sha, offset, min(ARTIFACT_CHUNK_BYTES, len(data) - offset),
             hashlib.sha256(view[offset:offset + ARTIFACT_CHUNK_BYTES]).hexdigest())
            for offset in range(0, len(data), ARTIFACT_CHUNK_BYTES)
        ))
        self.db.execute(
            "INSERT INTO artifact_refs VALUES(?,?,?,?) ON CONFLICT(owner_type,owner_id,slot) "
            "DO UPDATE SET hash=excluded.hash", (owner_type, owner_id, slot, sha),
        )
        return sha

    def read_artifact(self, sha: str) -> bytes:
        self._check()
        if not HASH.fullmatch(sha) or not self.db.execute("SELECT 1 FROM artifacts WHERE hash=?", (sha,)).fetchone():
            raise ValidationError(f"unknown artifact {sha}")
        data = (self.store.artifacts / sha).read_bytes()
        if hashlib.sha256(data).hexdigest() != sha:
            raise ValidationError(f"corrupt artifact {sha}")
        return data

    def put_artifact_file(self, source: Path, *, owner_type: str, owner_id: str, slot: str,
                          max_bytes: int = 64 * 1024 * 1024) -> str:
        """Install output from disk without loading it into memory."""
        self._check()
        for value in (owner_type, owner_id, slot):
            _id(value)
        if type(max_bytes) is not int or max_bytes < 1:
            raise ValidationError("artifact file requires a positive byte bound")
        fd, name = tempfile.mkstemp(prefix=".pending-", dir=self.store.artifacts)
        pending = Path(name)
        try:
            hasher = hashlib.sha256()
            size = 0
            with os.fdopen(fd, "wb") as target, Path(source).open("rb") as handle:
                for chunk in iter(lambda: handle.read(ARTIFACT_CHUNK_BYTES), b""):
                    size += len(chunk)
                    if size > max_bytes:
                        raise ValidationError("artifact file exceeds its byte bound")
                    hasher.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            sha = hasher.hexdigest()
            path = self.store.artifacts / sha
            if path.exists():
                existing = hashlib.sha256()
                with path.open("rb") as handle:
                    for chunk in iter(lambda: handle.read(ARTIFACT_CHUNK_BYTES), b""):
                        existing.update(chunk)
                if existing.hexdigest() != sha:
                    raise ValidationError(f"corrupt artifact {sha}")
            else:
                os.replace(pending, path)
                _sync_directory(self.store.artifacts)
            self._change()
            self.db.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?,?)", (sha, size, time.time()))
            with path.open("rb") as handle:
                offset = 0
                while chunk := handle.read(ARTIFACT_CHUNK_BYTES):
                    self.db.execute("INSERT OR IGNORE INTO artifact_chunks VALUES(?,?,?,?)",
                                    (sha, offset, len(chunk), hashlib.sha256(chunk).hexdigest()))
                    offset += len(chunk)
            self.db.execute("INSERT INTO artifact_refs VALUES(?,?,?,?) ON CONFLICT(owner_type,owner_id,slot) DO UPDATE SET hash=excluded.hash",
                            (owner_type, owner_id, slot, sha))
            return sha
        finally:
            pending.unlink(missing_ok=True)

    def release_artifacts(self, owner_type: str, owner_id: str) -> None:
        self._change()
        self.db.execute("DELETE FROM artifact_refs WHERE owner_type=? AND owner_id=?", (owner_type, owner_id))

    def read_artifact_slice(self, sha: str, *, offset: int = 0, limit: int = 8000) -> tuple[bytes, int]:
        """Verify and read only overlapping chunks; return (bytes, full byte length)."""
        self._check()
        if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 262144:
            raise ValidationError("artifact slice requires offset>=0 and 1<=limit<=262144 bytes")
        if not isinstance(sha, str) or not HASH.fullmatch(sha):
            raise ValidationError("invalid artifact hash")
        row = self.db.execute("SELECT size FROM artifacts WHERE hash=?", (sha,)).fetchone()
        if row is None:
            raise ValidationError(f"unknown artifact {sha}")
        size = row[0]
        if offset > size:
            raise ValidationError("artifact offset is past the end")
        end = min(size, offset + limit)
        pieces = []
        with (self.store.artifacts / sha).open("rb") as handle:
            if os.fstat(handle.fileno()).st_size != size:
                raise ValidationError(f"artifact size changed: {sha}")
            for chunk_offset in range(offset // ARTIFACT_CHUNK_BYTES * ARTIFACT_CHUNK_BYTES, end, ARTIFACT_CHUNK_BYTES):
                chunk_row = self.db.execute("SELECT size,chunk_hash FROM artifact_chunks WHERE hash=? AND offset=?",
                                            (sha, chunk_offset)).fetchone()
                if chunk_row is None:
                    raise ValidationError(f"artifact chunk index missing: {sha}")
                handle.seek(chunk_offset)
                chunk = handle.read(chunk_row[0])
                if hashlib.sha256(chunk).hexdigest() != chunk_row[1]:
                    raise ValidationError(f"corrupt artifact chunk: {sha}")
                pieces.append(chunk[max(0, offset - chunk_offset):end - chunk_offset])
        return b"".join(pieces), size

    def append_event(self, run_id: str, stream_id: str, capture_key: str, kind: str, payload: dict) -> dict:
        self._check()
        for value in (stream_id, capture_key):
            _id(value)
        if kind not in EVENT_KINDS:
            raise ValidationError(f"unknown event kind {kind}")
        body = canonical(payload)
        payload_hash = hashlib.sha256(body.encode()).hexdigest()
        existing = self.db.execute("SELECT * FROM events WHERE run_id=? AND capture_key=?", (run_id, capture_key)).fetchone()
        if existing:
            if existing["payload_hash"] != payload_hash or existing["kind"] != kind or existing["stream_id"] != stream_id:
                raise Conflict(f"capture key {capture_key}: conflicting observation")
            return dict(existing)
        self._change()
        self.db.execute("INSERT OR IGNORE INTO cursors(run_id,stream_id) VALUES(?,?)", (run_id, stream_id))
        seq = self.db.execute(
            "UPDATE cursors SET head_seq=head_seq+1 WHERE run_id=? AND stream_id=? RETURNING head_seq",
            (run_id, stream_id),
        ).fetchone()[0]
        event_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO events VALUES(?,?,?,?,?,?,?,?,NULL,?)",
            (event_id, run_id, stream_id, seq, capture_key, kind, body, payload_hash, time.time()),
        )
        return dict(self.db.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone())

    def classify(self, run_id: str, stream_id: str, *, expected_revision: int, through: int, dispositions: dict[int, str]) -> None:
        self._check()
        row = self.db.execute("SELECT * FROM cursors WHERE run_id=? AND stream_id=?", (run_id, stream_id)).fetchone()
        if row is None or row["revision"] != expected_revision:
            raise Conflict("cursor revision changed")
        if through < row["classified_seq"] or through > row["head_seq"]:
            raise ValidationError("cursor outside captured range")
        if set(dispositions) != set(range(row["classified_seq"] + 1, through + 1)):
            raise ValidationError("every event in the frozen range requires a disposition")
        if any(value not in DISPOSITIONS for value in dispositions.values()):
            raise ValidationError("unknown event disposition")
        self._change()
        self.db.executemany(
            "UPDATE events SET disposition=? WHERE run_id=? AND stream_id=? AND seq=?",
            [(value, run_id, stream_id, seq) for seq, value in dispositions.items()],
        )
        self.db.execute(
            "UPDATE cursors SET classified_seq=?,revision=revision+1 WHERE run_id=? AND stream_id=?",
            (through, run_id, stream_id),
        )

    def record(self, record_id: str) -> dict | None:
        self._check()
        row = self.db.execute(
            "SELECT r.* FROM records r JOIN record_heads h USING(record_id,version) WHERE record_id=?", (record_id,)
        ).fetchone()
        if not row:
            return None
        result = dict(row)
        for key in ("payload", "evidence", "inputs"):
            result[key] = json.loads(result[key])
        return result

    def put_record(self, record_id: str, *, expected_version: int, task_id: str, kind: str,
                   payload: dict, evidence: list[str], inputs: dict, reason: str,
                   expires_when: str, retention: str = "context", consuming_step: str | None = None,
                   validity: str = "current") -> int:
        self._check()
        _id(record_id)
        old = self.record(record_id)
        actual = old["version"] if old else 0
        if expected_version != actual:
            raise Conflict(f"record {record_id}: expected version {expected_version}, found {actual}")
        if kind not in RECORD_KINDS or retention not in {"context", "store"} or validity not in {"current", "invalid", "dropped"}:
            raise ValidationError("record kind/retention/validity is invalid")
        if old and (old["task_id"] != task_id or old["kind"] != kind):
            raise ValidationError("record task and kind are immutable")
        validate_payload(kind, payload)
        if not reason or not expires_when or (retention == "store" and not consuming_step):
            raise ValidationError("retention needs reason, expiry, and a consuming step for stored facts")
        if len(evidence) != len(set(evidence)):
            raise ValidationError("duplicate evidence IDs")
        for event_id in evidence:
            found = self.db.execute(
                "SELECT r.task_id FROM events e JOIN runs r USING(run_id) WHERE event_id=?", (event_id,)
            ).fetchone()
            if not found or found[0] != task_id:
                raise ValidationError(f"evidence {event_id} is absent or belongs to another task")
        body, refs, fingerprints = canonical(payload), canonical(evidence), canonical(inputs)
        size = len((body + refs + fingerprints + reason + expires_when + (consuming_step or "")).encode("utf-8"))
        self._change()
        version = actual + 1
        self.db.execute(
            "INSERT INTO records VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (record_id, version, task_id, kind, body, refs, fingerprints, actual or None,
             validity, retention, reason, consuming_step, expires_when, size),
        )
        self.db.execute(
            "INSERT INTO record_heads VALUES(?,?) ON CONFLICT(record_id) DO UPDATE SET version=excluded.version",
            (record_id, version),
        )
        return version

    def enqueue(self, kind: str, key: str, payload: dict) -> str:
        self._check()
        _id(kind)
        _id(key)
        body = canonical(payload)
        row = self.db.execute("SELECT * FROM outbox WHERE kind=? AND idempotency_key=?", (kind, key)).fetchone()
        if row:
            if row["payload"] != body:
                raise Conflict(f"outbox {kind}/{key}: idempotency key reused with different payload")
            return row["operation_id"]
        self._change()
        operation_id = str(uuid.uuid4())
        self.db.execute(
            "INSERT INTO outbox(operation_id,kind,idempotency_key,payload,available_at) VALUES(?,?,?,?,?)",
            (operation_id, kind, key, body, time.time()),
        )
        return operation_id

    def claim_outbox(self, *, now: float | None = None, lease_seconds: float = 30.0) -> dict | None:
        self._check()
        if not math.isfinite(lease_seconds) or lease_seconds <= 0:
            raise ValidationError("outbox lease must be positive and finite")
        now = time.time() if now is None else now
        row = self.db.execute(
            "SELECT * FROM outbox WHERE (status='pending' AND available_at<=?) "
            "OR (status='claimed' AND lease_until<=?) ORDER BY available_at,operation_id LIMIT 1", (now, now)
        ).fetchone()
        if not row:
            return None
        token = str(uuid.uuid4())
        self._change()
        self.db.execute(
            "UPDATE outbox SET status='claimed',attempts=attempts+1,lease_token=?,lease_until=? WHERE operation_id=?",
            (token, now + lease_seconds, row["operation_id"]),
        )
        return {**dict(row), "status": "claimed", "payload": json.loads(row["payload"]),
                "attempts": row["attempts"] + 1, "lease_token": token, "lease_until": now + lease_seconds}

    def finish_outbox(self, operation_id: str, lease_token: str, *, error: str | None = None, retry_after: float = 1.0) -> None:
        self._check()
        if not math.isfinite(retry_after) or retry_after < 0:
            raise ValidationError("retry delay must be nonnegative and finite")
        row = self.db.execute("SELECT status,lease_token FROM outbox WHERE operation_id=?", (operation_id,)).fetchone()
        if not row or row["status"] != "claimed" or row["lease_token"] != lease_token:
            raise Conflict("outbox claim is no longer owned by this worker")
        self._change()
        self.db.execute(
            "UPDATE outbox SET status=?,lease_token=NULL,lease_until=NULL,last_error=?,available_at=? WHERE operation_id=?",
            ("pending" if error is not None else "delivered", error, time.time() + retry_after, operation_id),
        )
