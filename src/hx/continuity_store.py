"""Transactional authority for current-task continuity (versioned schema).

All mutations, including artifact installation, use a short BEGIN IMMEDIATE
transaction. Model calls, tool execution, source hashing and prompt compilation
belong outside this boundary. The ledger is the sole assignment, run, proof and
publication authority.

Three openers. `ContinuityStore(root)` is the mutating opener of the loop and CLI
mutations: it creates or verifies the schema under the writer lock. `attach` joins
an initialized ledger without DDL or any lock at open; reads go through `snapshot`
(a WAL read that never waits for the writer) and only explicit writes take the
lock, each waiting at most an optional budget before `LedgerBusy`. `reader` is
`attach` with every statement refused unless it reads. Hooks attach; queries read.
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

SCHEMA_VERSION = 22
ARTIFACT_CHUNK_BYTES = 65536
DISPOSITIONS = {"reduced", "extracted", "no_change", "dropped", "pending"}
RECORD_KINDS = {"goal", "constraint", "decision", "finding", "search", "command", "cursor", "dead_end"}
EVENT_KINDS = {
    "request", "correction", "assistant_message", "tool_result", "progress",
    "source_change", "check_result", "spawn", "finish", "boundary",
    "contract_published", "packet_covered", "integration_verified", "deployable",
}
HASH = re.compile(r"[0-9a-f]{64}\Z")


class Conflict(HxError):
    """The read version no longer matches; retry from a fresh snapshot."""


class LedgerBusy(HxError):
    """The writer lock did not become available within the caller's budget."""


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


from .continuity_schema import SCHEMA



class ContinuityStore:
    """One local fleet authority; use a separate connection per thread/process."""

    def __init__(self, root: Path, *, timeout: float = 10.0, mode: str = "write", budget: float | None = None):
        if mode not in {"write", "attach", "read"}:
            raise ValidationError("continuity: opener mode is write, attach or read")
        if budget is not None and (not math.isfinite(budget) or budget <= 0):
            raise ValidationError("continuity: a ledger budget must be positive and finite")
        self.root = Path(root)
        self.path = self.root / "state" / "continuity.sqlite"
        self.artifacts = self.root / "state" / "artifacts"
        self.mode = mode
        self.budget = budget
        if mode == "write":
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.artifacts.mkdir(parents=True, exist_ok=True)
        elif not self.path.is_file():
            # Connecting would create an empty file; only the mutating opener initializes.
            raise ValidationError("continuity: ledger is not initialized; run hx install or start the loop")
        self.db = sqlite3.connect(self.path, timeout=timeout if budget is None else min(timeout, budget),
                                  isolation_level=None, check_same_thread=True)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute("PRAGMA foreign_keys=ON")
            # Per-connection page cache, not a total process RSS limit. Keep large
            # temporary sorts on disk instead of growing alongside worker count.
            self.db.execute("PRAGMA cache_size=-2048")
            self.db.execute("PRAGMA temp_store=FILE")
            self.db.execute("PRAGMA mmap_size=0")
            if mode != "write":
                # No DDL and no lock: the header read is a plain WAL read.
                version = self.db.execute("PRAGMA user_version").fetchone()[0]
                if version != SCHEMA_VERSION:
                    raise ValidationError(f"continuity: ledger schema {version} is not {SCHEMA_VERSION}; recreate this prerelease instance")
                # A reader never commits, so NORMAL only governs the checkpoints it never runs.
                self.db.execute("PRAGMA synchronous=" + ("NORMAL" if mode == "read" else "FULL"))
                if mode == "read":
                    self.db.execute("PRAGMA query_only=ON")
                return
            self.db.execute("PRAGMA journal_mode=WAL")
            self.db.execute("PRAGMA synchronous=FULL")
            self.db.execute("BEGIN IMMEDIATE")
            version = self.db.execute("PRAGMA user_version").fetchone()[0]
            if version not in (0, SCHEMA_VERSION):
                raise ValidationError(f"continuity: unsupported schema {version}; recreate this prerelease instance with schema {SCHEMA_VERSION}")
            if version == 0:
                existing = self.db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchone()
                if existing:
                    raise ValidationError("continuity: unversioned database is not a fresh instance")
                for statement in SCHEMA.split(";"):
                    if statement.strip():
                        self.db.execute(statement)
            self.db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            from . import dependency_packets, check_artifacts, change_delivery, dotnet_build
            dependency_packets.initialize(self.db)
            check_artifacts.initialize(self.db)
            change_delivery.initialize(self.db)
            dotnet_build.initialize(self.db)
            self.db.commit()
            self.db.execute("PRAGMA foreign_keys=ON")
        except BaseException:
            self.db.rollback()
            self.db.close()
            raise

    @classmethod
    def attach(cls, root: Path, *, timeout: float = 10.0, budget: float | None = None) -> ContinuityStore:
        """Join an initialized ledger: no DDL or lock at open, writes only when asked."""
        return cls(root, timeout=timeout, mode="attach", budget=budget)

    @classmethod
    def reader(cls, root: Path, *, timeout: float = 10.0, budget: float | None = None) -> ContinuityStore:
        """Read-only opener for queries; every transaction is a snapshot."""
        return cls(root, timeout=timeout, mode="read", budget=budget)

    def close(self) -> None:
        self.db.close()

    def __enter__(self) -> ContinuityStore:
        return self

    def __exit__(self, *_args) -> None:
        self.close()

    @contextmanager
    def snapshot(self) -> Iterator[Transaction]:
        """One consistent WAL read. It never takes or waits for the writer lock."""
        if self.db.in_transaction:
            raise ValidationError("continuity: nested transactions are not supported")
        guard = self.mode != "read"
        if guard:
            self.db.execute("PRAGMA query_only=ON")
        try:
            self.db.execute("BEGIN")
            tx = Transaction(self, readonly=True)
            try:
                yield tx
            finally:
                tx.active = False
                self.db.rollback()
        finally:
            if guard:
                self.db.execute("PRAGMA query_only=OFF")

    @contextmanager
    def transaction(self) -> Iterator[Transaction]:
        if self.mode == "read":
            with self.snapshot() as tx:
                yield tx
            return
        if self.db.in_transaction:
            raise ValidationError("continuity: nested transactions are not supported")
        try:
            self.db.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if self.budget is not None and busy(exc):
                raise LedgerBusy(f"continuity: the writer lock did not become available within {self.budget:g} s") from exc
            raise
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
                "UNION SELECT packet_hash FROM checkpoints "
                "UNION SELECT artifact_hash FROM check_artifacts WHERE artifact_hash IS NOT NULL "
                "UNION SELECT artifact_hash FROM dotnet_build_artifacts "
                "UNION SELECT artifact_hash FROM dotnet_receipts WHERE artifact_hash IS NOT NULL"
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


def busy(exc: BaseException) -> bool:
    """SQLite lock contention, as opposed to corruption, I/O or a schema error."""
    return (isinstance(exc, sqlite3.OperationalError)
            and getattr(exc, "sqlite_errorcode", 0) & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED))


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Transaction:
    def __init__(self, store: ContinuityStore, *, readonly: bool = False):
        self.store = store
        self.db = store.db
        self.changed = False
        self.active = True
        self.readonly = readonly

    def _check(self) -> None:
        # A deferred snapshot has no SQLite transaction until its first read.
        if not self.active or not (self.readonly or self.db.in_transaction):
            raise ValidationError("continuity: transaction is closed")

    def _change(self) -> None:
        self._check()
        if self.readonly:
            raise ValidationError("continuity: a read snapshot cannot change the ledger")
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
        if previous and "plan_id" in previous["payload"]:
            from .unit_execution import guard_revision
            guard_revision(self, previous, payload)
        if "map_inputs" in payload:
            from .map_dependencies import validate_refs
            validate_refs(self, payload["map_inputs"], payload)
        body = canonical(payload)
        self._change()
        revision = actual + 1
        self.db.execute("INSERT INTO tasks VALUES(?,?,?,?,?)", (task_id, revision, parent_id, body, time.time()))
        self.db.execute(
            "INSERT INTO task_heads VALUES(?,?) ON CONFLICT(task_id) DO UPDATE SET revision=excluded.revision",
            (task_id, revision),
        )
        if "map_inputs" in payload:
            from .map_dependencies import bind_task
            bind_task(self, task_id, revision, payload)
        return revision

    def start_run(self, task_id: str, task_revision: int, worker_id: str, *, map_revision: int = 0,
                  prepared: dict | None = None) -> str:
        """`prepared` is unit_execution.prepare_admission's worktree reading for a planned task."""
        self._check()
        task = self.task(task_id)
        if task is None or task["revision"] != task_revision:
            raise Conflict(f"task {task_id}: dispatch requires current revision {task_revision}")
        from .map_dependencies import validate_task
        validate_task(self, task)
        _id(worker_id)
        if self.db.execute("SELECT 1 FROM runs WHERE worker_id=? AND ended_at IS NULL", (worker_id,)).fetchone():
            raise Conflict(f"worker {worker_id}: already assigned")
        admission = None
        if "plan_id" in task["payload"]:
            from .unit_execution import admit
            admission = admit(self, task, prepared)
        run_id = str(uuid.uuid4())
        self._change()
        self.db.execute(
            "INSERT INTO runs VALUES(?,?,?,?,?,?,NULL,NULL,?,NULL)",
            (run_id, task_id, task_revision, worker_id, map_revision, "assigned", time.time()),
        )
        if admission is not None:
            from .unit_execution import bind
            bind(self, task, run_id, admission)
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
        planned = self.db.execute("SELECT 1 FROM unit_runs WHERE run_id=?", (run_id,)).fetchone()
        if planned and outcome not in {"stopped", "failed", "completed"}:
            raise ValidationError("planned runs stop, fail, or finish through the verified completion gate")
        if planned and outcome == "completed" and not self.db.execute("SELECT 1 FROM unit_completions WHERE run_id=?", (run_id,)).fetchone():
            raise Conflict("planned completion requires verified checks and output proofs")
        if planned:
            from .native_controller import guard_release
            guard_release(self, run_id)
        self._change()
        self.db.execute("UPDATE runs SET outcome=?,ended_at=? WHERE run_id=?", (outcome, time.time(), run_id))
        self.db.execute("DELETE FROM leases WHERE run_id=?", (run_id,))
        self.db.execute("DELETE FROM unit_resources WHERE run_id=?", (run_id,))
        if outcome == 'completed':
            from .task_retention import close
            task_id = self.db.execute('SELECT task_id FROM runs WHERE run_id=?', (run_id,)).fetchone()[0]
            close(self, task_id)

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
        if "map" in inputs:
            from .map_dependencies import validate_record_refs
            validate_record_refs(self, task_id, kind, inputs, validity)
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
        if "map" in inputs:
            from .map_dependencies import bind_record
            bind_record(self, record_id, version, inputs)
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
