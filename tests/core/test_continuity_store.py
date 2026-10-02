"""Durability and concurrency contracts; no model or live agent required."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from hx.continuity_store import Conflict, ContinuityStore, SCHEMA_VERSION, canonical
from hx.errors import ValidationError


@pytest.fixture
def ledger(tmp_path):
    with ContinuityStore(tmp_path) as store:
        with store.transaction() as tx:
            tx.put_task("T1", {"goal": "preserve late events"}, expected_revision=0)
            run = tx.start_run("T1", 1, "eng-001")
        yield store, run


def record(tx, **overrides):
    kwargs = dict(expected_version=0, task_id="T1", kind="finding",
                  payload={"text": "ingest advances a cursor"}, evidence=[], inputs={},
                  reason="needed for edit", expires_when="source changes")
    kwargs.update(overrides)
    kwargs["payload"] = {"schema_version": 1, **kwargs["payload"]}
    return tx.put_record("F1", **kwargs)


def test_durable_schema_and_foreign_keys(ledger):
    store, run = ledger
    assert store.db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store.db.execute("PRAGMA synchronous").fetchone()[0] == 2
    with ContinuityStore(store.root) as reopened:
        with reopened.transaction() as tx:
            assert tx.task("T1")["payload"]["goal"] == "preserve late events"
        assert reopened.db.execute("SELECT task_revision FROM runs WHERE run_id=?", (run,)).fetchone()[0] == 1
        with pytest.raises(sqlite3.IntegrityError), reopened.transaction() as tx:
            tx.append_event("not-a-run", "main", "call1", "tool_result", {})
        assert not reopened.db.execute("PRAGMA foreign_key_check").fetchall()


def test_newer_schema_refused_without_rewrite(tmp_path):
    with ContinuityStore(tmp_path) as store:
        store.db.execute("PRAGMA user_version=999")
    with pytest.raises(ValidationError, match="unsupported schema"):
        ContinuityStore(tmp_path)
    with sqlite3.connect(tmp_path / "state" / "continuity.sqlite") as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 999


def test_schema_one_migrates_without_losing_existing_tasks(ledger):
    store, _ = ledger
    store.db.execute("DROP TABLE event_origins")
    store.db.execute("DROP TABLE artifact_chunks")
    store.db.execute("PRAGMA user_version=1")
    with ContinuityStore(store.root) as upgraded:
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        with upgraded.transaction() as tx:
            assert tx.task("T1")["payload"]["goal"] == "preserve late events"
        assert upgraded.db.execute("SELECT count(*) FROM event_origins").fetchone()[0] == 0


def test_schema_fourteen_indexes_existing_capture_without_resetting_offsets(ledger, tmp_path):
    from hx import observer
    store, run = ledger
    path = tmp_path / "source.jsonl"
    path.write_text('{"event":"stop"}\n')
    source = observer.register(store, run_id=run, stream_id="native", path=path, decoder="hook-v1", session_id="S")
    observer.drain(store, source)
    before = tuple(store.db.execute("SELECT * FROM capture_sources WHERE source_id=?", (source,)).fetchone())
    store.db.execute("DROP INDEX capture_run_sources")
    store.db.execute("PRAGMA user_version=14")
    with ContinuityStore(store.root) as upgraded:
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert tuple(upgraded.db.execute("SELECT * FROM capture_sources WHERE source_id=?", (source,)).fetchone()) == before
        query = upgraded.db.execute("EXPLAIN QUERY PLAN SELECT * FROM capture_sources WHERE run_id=? ORDER BY source_id LIMIT 17", (run,))
        assert any("capture_run_sources" in row[3] for row in query)


def test_atomic_rollback_covers_records_cursor_outbox_and_artifacts(ledger):
    store, run = ledger
    with store.transaction() as tx:
        event = tx.append_event(run, "main", "call1", "tool_result", {"exit": 1})
    before = store.db.execute("SELECT revision FROM ledger_meta").fetchone()[0]
    with pytest.raises(RuntimeError), store.transaction() as tx:
        record(tx, evidence=[event["event_id"]])
        tx.classify(run, "main", expected_revision=0, through=1, dispositions={1: "extracted"})
        tx.put_artifact(b"failure details", owner_type="event", owner_id=event["event_id"], slot="result")
        tx.enqueue("projection", "one", {"revision": 1})
        raise RuntimeError("crash before commit")
    assert store.db.execute("SELECT revision FROM ledger_meta").fetchone()[0] == before
    assert store.db.execute("SELECT classified_seq FROM cursors").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM records").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM artifact_refs").fetchone()[0] == 0
    assert len(store.collect_artifacts()) == 1


def test_native_identity_deduplication_does_not_merge_distinct_calls(ledger):
    store, run = ledger
    with store.transaction() as tx:
        first = tx.append_event(run, "main", "session:call1", "tool_result", {"text": "same"})
        repeated = tx.append_event(run, "main", "session:call1", "tool_result", {"text": "same"})
        second = tx.append_event(run, "main", "session:call2", "tool_result", {"text": "same"})
    assert first["event_id"] == repeated["event_id"]
    assert second["event_id"] != first["event_id"]
    assert (first["seq"], second["seq"]) == (1, 2)
    with pytest.raises(Conflict), store.transaction() as tx:
        tx.append_event(run, "main", "session:call1", "tool_result", {"text": "different"})


def test_frozen_cursor_does_not_acknowledge_late_event(ledger):
    store, run = ledger
    with store.transaction() as tx:
        for seq in range(1, 41):
            tx.append_event(run, "main", f"call{seq}", "tool_result", {"n": seq})
    # Extraction starts with head 40, then event 41 arrives before commit.
    with store.transaction() as tx:
        tx.append_event(run, "main", "call41", "correction", {"text": "keep negative conditions"})
    with store.transaction() as tx:
        tx.classify(run, "main", expected_revision=0, through=40,
                    dispositions={seq: "no_change" for seq in range(1, 41)})
    row = store.db.execute("SELECT * FROM cursors").fetchone()
    assert (row["head_seq"], row["classified_seq"]) == (41, 40)
    assert store.db.execute("SELECT disposition FROM events WHERE seq=41").fetchone()[0] is None
    with pytest.raises(ValidationError, match="outside"), store.transaction() as tx:
        tx.classify(run, "main", expected_revision=1, through=42, dispositions={41: "pending", 42: "pending"})
    with pytest.raises(Conflict), store.transaction() as tx:
        tx.classify(run, "main", expected_revision=0, through=41, dispositions={41: "pending"})
    with pytest.raises(ValidationError, match="every event"), store.transaction() as tx:
        tx.classify(run, "main", expected_revision=1, through=41, dispositions={})


def test_pending_evidence_advances_classification_but_remains_queued(ledger):
    store, run = ledger
    with store.transaction() as tx:
        tx.append_event(run, "main", "correction1", "correction", {"text": "do not publish"})
        tx.classify(run, "main", expected_revision=0, through=1, dispositions={1: "pending"})
    assert store.db.execute("SELECT classified_seq FROM cursors").fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM events WHERE disposition='pending'").fetchone()[0] == 1


def test_record_cas_and_evidence_scoping(ledger):
    store, run = ledger
    with store.transaction() as tx:
        event = tx.append_event(run, "main", "one", "tool_result", {})
        assert record(tx, evidence=[event["event_id"]]) == 1
    with pytest.raises(Conflict), store.transaction() as tx:
        record(tx, payload={"text": "lost update"})
    with store.transaction() as tx:
        assert record(tx, expected_version=1, payload={"text": "updated"}) == 2
        assert tx.record("F1")["payload"] == {"schema_version": 1, "text": "updated"}
        tx.put_task("T2", {"goal": "different"}, expected_revision=0)
        other = tx.start_run("T2", 1, "eng-002")
        evidence = tx.append_event(other, "main", "two", "tool_result", {})
    with pytest.raises(ValidationError, match="another task"), store.transaction() as tx:
        record(tx, expected_version=2, evidence=[evidence["event_id"]])
    with pytest.raises(ValidationError, match="absent"), store.transaction() as tx:
        record(tx, expected_version=2, evidence=["invented"])
    with pytest.raises(ValidationError, match="consuming step"), store.transaction() as tx:
        record(tx, expected_version=2, retention="store")


def test_multi_record_conflict_rolls_back_the_entire_proposal(ledger):
    store, _ = ledger
    with store.transaction() as tx:
        record(tx)
    with pytest.raises(Conflict), store.transaction() as tx:
        record(tx, expected_version=1, payload={"text": "must roll back"})
        record(tx, expected_version=1, payload={"text": "stale"})
    with store.transaction() as tx:
        assert tx.record("F1")["version"] == 1


def test_task_identity_survives_worker_reuse_and_reassignment(ledger):
    store, run = ledger
    with store.transaction() as tx:
        with pytest.raises(Conflict, match="already assigned"):
            tx.start_run("T1", 1, "eng-001")
        tx.finish_run(run, "interrupted")
        tx.put_task("T1", {"goal": "amended"}, expected_revision=1)
        with pytest.raises(Conflict, match="current revision"):
            tx.start_run("T1", 1, "eng-001")
        new_run = tx.start_run("T1", 2, "eng-002")
        tx.put_task("T2", {"goal": "independent"}, expected_revision=0)
        tx.start_run("T2", 1, "eng-001")
    assert run != new_run
    assert store.db.execute("SELECT count(*) FROM tasks WHERE task_id='T1'").fetchone()[0] == 2


def test_parent_is_immutable_and_must_exist(ledger):
    store, _ = ledger
    with store.transaction() as tx:
        tx.put_task("child", {"goal": "part"}, expected_revision=0, parent_id="T1")
    with pytest.raises(ValidationError, match="immutable"), store.transaction() as tx:
        tx.put_task("child", {}, expected_revision=1)
    with pytest.raises(sqlite3.IntegrityError), store.transaction() as tx:
        tx.put_task("orphan", {}, expected_revision=0, parent_id="missing")
    with pytest.raises(ValidationError), store.transaction() as tx:
        tx.put_task("cycle", {}, expected_revision=0, parent_id="cycle")


def test_artifact_shared_ownership_and_exact_byte_preservation(ledger):
    store, _ = ledger
    data = "Traceback\n  assert x != '成功'\n".encode()
    with store.transaction() as tx:
        sha = tx.put_artifact(data, owner_type="event", owner_id="E1", slot="result")
        assert tx.put_artifact(data, owner_type="record", owner_id="F1@1", slot="evidence") == sha
        assert tx.read_artifact(sha) == data
        tx.release_artifacts("event", "E1")
    assert store.collect_artifacts() == []
    with store.transaction() as tx:
        tx.release_artifacts("record", "F1@1")
    assert store.collect_artifacts() == [sha]
    assert not (store.artifacts / sha).exists()


def test_artifact_replacement_releases_original_and_detects_corruption(ledger):
    store, _ = ledger
    with store.transaction() as tx:
        old = tx.put_artifact(b"verbose", owner_type="record", owner_id="F1", slot="evidence")
        new = tx.put_artifact(b"short", owner_type="record", owner_id="F1", slot="evidence")
    assert store.collect_artifacts() == [old]
    (store.artifacts / new).write_bytes(b"tampered")
    with pytest.raises(ValidationError, match="corrupt"), store.transaction() as tx:
        tx.read_artifact(new)
    with pytest.raises(ValidationError, match="unknown"), store.transaction() as tx:
        tx.read_artifact("../../outside")


def test_outbox_crash_retry_and_stale_claim(ledger):
    store, _ = ledger
    with store.transaction() as tx:
        operation = tx.enqueue("wake", "T1-complete", {"task": "T1"})
        assert tx.enqueue("wake", "T1-complete", {"task": "T1"}) == operation
    now = time.time() + 1
    with store.transaction() as tx:
        first = tx.claim_outbox(now=now, lease_seconds=2)
        assert first["attempts"] == 1
        assert tx.claim_outbox(now=now + 1) is None
    # A crashed consumer's lease expires; another process claims the same operation.
    with ContinuityStore(store.root) as second_store, second_store.transaction() as tx:
        second = tx.claim_outbox(now=now + 3)
        assert second["operation_id"] == operation
        assert second["lease_token"] != first["lease_token"]
        assert second["attempts"] == 2
    with pytest.raises(Conflict), store.transaction() as tx:
        tx.finish_outbox(operation, first["lease_token"])
    with store.transaction() as tx:
        tx.finish_outbox(operation, second["lease_token"])
        assert tx.claim_outbox(now=now + 100) is None
    with pytest.raises(Conflict), store.transaction() as tx:
        tx.enqueue("wake", "T1-complete", {"task": "T2"})


def test_outbox_retry_with_error(ledger):
    store, _ = ledger
    with store.transaction() as tx:
        op = tx.enqueue("projection", "rev1", {})
        claimed = tx.claim_outbox(now=time.time() + 1)
        tx.finish_outbox(op, claimed["lease_token"], error="temporary filesystem failure", retry_after=60)
        assert tx.claim_outbox() is None
        assert tx.claim_outbox(now=time.time() + 61)["attempts"] == 2


def test_concurrent_writers_allocate_every_sequence_exactly_once(ledger):
    store, run = ledger

    def write(worker):
        with ContinuityStore(store.root) as connection:
            for index in range(20):
                with connection.transaction() as tx:
                    tx.append_event(run, "main", f"{worker}:{index}", "tool_result", {"text": "identical"})

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(4)))
    assert [r[0] for r in store.db.execute("SELECT seq FROM events ORDER BY seq")] == list(range(1, 81))
    assert store.db.execute("SELECT head_seq FROM cursors").fetchone()[0] == 80


def test_concurrent_cas_has_exactly_one_winner(ledger):
    store, _ = ledger

    def update(index):
        with ContinuityStore(store.root) as connection:
            try:
                with connection.transaction() as tx:
                    return record(tx, payload={"text": str(index)})
            except Conflict:
                return None

    with ThreadPoolExecutor(max_workers=4) as pool:
        outcomes = list(pool.map(update, range(4)))
    assert outcomes.count(1) == 1
    assert outcomes.count(None) == 3


@pytest.mark.parametrize("commit", [False, True])
def test_process_exit_preserves_only_committed_state(tmp_path, commit):
    # os._exit skips context manager cleanup, unlike raising an exception.
    source = """
import os, sys
from pathlib import Path
from hx.continuity_store import ContinuityStore
store = ContinuityStore(Path(sys.argv[1]))
with store.transaction() as tx:
    tx.put_task('T', {'goal': 'recover'}, expected_revision=0)
    run = tx.start_run('T', 1, 'eng-001')
    tx.put_artifact(b'evidence', owner_type='run', owner_id=run, slot='result')
    tx.enqueue('projection', 'one', {'task': 'T'})
    if sys.argv[2] == 'False':
        os._exit(23)
os._exit(23)
"""
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[2] / "src")}
    proc = subprocess.run([sys.executable, "-c", source, str(tmp_path), str(commit)], env=env, capture_output=True)
    assert proc.returncode == 23, proc.stderr
    with ContinuityStore(tmp_path) as store:
        with store.transaction() as tx:
            assert (tx.task("T") is not None) == commit
            claimed = tx.claim_outbox(now=time.time() + 1)
            assert (claimed is not None) == commit
        removed = store.collect_artifacts()
        assert len(removed) == (0 if commit else 1)
        assert store.db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_transaction_cannot_escape_context_or_nest(ledger):
    store, _ = ledger
    with store.transaction() as tx:
        with pytest.raises(ValidationError, match="nested"), store.transaction():
            pass
    with pytest.raises(ValidationError, match="closed"):
        tx.put_task("T2", {}, expected_revision=0)
    with pytest.raises(ValidationError, match="invalid JSON"):
        canonical({"probability": float("nan")})
