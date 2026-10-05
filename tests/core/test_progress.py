from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from hx.continuity_store import Conflict, ContinuityStore, Transaction
from hx.errors import ValidationError
from hx.passes import prepare
from hx.progress import snapshot, update


@pytest.fixture
def ledger(tmp_path):
    with ContinuityStore(tmp_path) as store:
        with store.transaction() as tx:
            tx.put_task("T", {"goal": "Keep the current execution state."}, expected_revision=0)
            run = tx.start_run("T", 1, "eng-001")
        yield store, run


def request(run, revision=0, **kwargs):
    return {"task_id": "T", "run_id": run, "expected_revision": revision, "phase": "implementing",
            "step_updates": [{"step_id": "edit", "status": "active", "summary": "Change the cursor writer.",
                              "last": "Located the cursor update."}],
            "next": "Patch the transaction boundary.", "blocker": None, "deliverables": [], "evidence_ids": [], **kwargs}


def test_progress_reduces_directly_and_exposes_cursor_to_companion(ledger):
    store, run = ledger
    result = update(store, request(run), worker_id="eng-001")
    assert result["revision"] == 1
    event = store.db.execute("SELECT * FROM events WHERE event_id=?", (result["event_id"],)).fetchone()
    assert event["disposition"] == "reduced" and json.loads(event["payload"])["claim"]
    assert store.db.execute("SELECT phase FROM runs").fetchone()[0] == "implementing"
    assert store.db.execute("SELECT count(*) FROM outbox WHERE kind='capture_ready'").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 0
    assert prepare(store, run, "progress", prompt_version="p1") is None
    with store.transaction() as tx:
        tx.append_event(run, "main", "new", "tool_result", {"exit": 0})
    frozen = prepare(store, run, "main", prompt_version="p1")
    cursor = next(record for record in frozen["records"] if record["record_id"] == result["cursor_id"])
    assert cursor["payload"]["next"] == "Patch the transaction boundary."


def test_sparse_update_preserves_unrelated_records_and_original_field_evidence(ledger):
    store, run = ledger
    first = update(store, request(run, deliverables=[{"path": "src/cursor.py", "op": "upsert", "description": "Updated cursor writer."}]))
    with store.transaction() as tx:
        for name, kind, payload in (("constraint", "constraint", {"text": "Preserve late events."}),
                                    ("decision", "decision", {"choice": "SQLite", "because": "Cursor and facts commit together."})):
            tx.put_record(name, expected_version=0, task_id="T", kind=kind,
                payload={"schema_version": 1, **payload}, evidence=[first["event_id"]], inputs={},
                reason="Current task requirement.", expires_when="Task closes.")
    for revision in range(1, 6):
        update(store, request(run, revision, step_updates=[], next=f"Continue attempt {revision}."))
    step = store.db.execute("SELECT payload,evidence FROM progress_steps").fetchone()
    assert json.loads(step["payload"])["summary"] == "Change the cursor writer."
    assert json.loads(step["evidence"])["last"] == [first["event_id"]]
    assert store.db.execute("SELECT count(*) FROM progress_deliverables").fetchone()[0] == 1
    with store.transaction() as tx:
        assert tx.record("constraint")["version"] == tx.record("decision")["version"] == 1
        cursor = tx.record(first["cursor_id"])
        assert cursor["payload"]["last"] == "Located the cursor update."
        assert first["event_id"] in cursor["evidence"] and len(cursor["evidence"]) == 2


def test_step_transition_and_blocker_clear_are_atomic(ledger):
    store, run = ledger
    first = update(store, request(run, blocker="Waiting for the schema."))
    second = update(store, request(run, 1, phase="verifying", step_updates=[
        {"step_id": "edit", "status": "done", "last": "Patched the cursor transaction."},
        {"step_id": "check", "status": "active", "summary": "Run the late-event regression."}],
        next="Run the targeted check."))
    with store.transaction() as tx:
        cursor = tx.record(first["cursor_id"])
        assert cursor["payload"]["step"] == "check" and "blocker" not in cursor["payload"]
        assert "last" not in cursor["payload"]  # Do not misattribute another step's action.
    assert second["revision"] == 2
    assert store.db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 0
    assert store.db.execute("SELECT ended_at FROM runs").fetchone()[0] is None


def test_second_active_step_rolls_back_every_change(ledger):
    store, run = ledger
    update(store, request(run))
    with pytest.raises(ValidationError, match="one active step"):
        update(store, request(run, 1, step_updates=[{"step_id": "check", "status": "active"}]))
    assert store.db.execute("SELECT revision FROM progress_heads").fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM progress_steps").fetchone()[0] == 1


def test_retry_returns_original_receipt_after_later_progress_and_finish(ledger):
    store, run = ledger
    body = request(run)
    first = update(store, body)
    update(store, request(run, 1, step_updates=[], next="Run verification."))
    with store.transaction() as tx:
        tx.finish_run(run, "finished")
    assert update(store, body) == first
    with pytest.raises(Conflict, match="active run"):
        update(store, request(run, 2))
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 2


def test_wrong_owner_stale_revision_and_foreign_evidence_are_rejected(ledger):
    store, run = ledger
    with pytest.raises(Conflict, match="caller"):
        update(store, request(run), worker_id="eng-002")
    with pytest.raises(Conflict, match="revision"):
        update(store, request(run, 100))
    with store.transaction() as tx:
        tx.put_task("other", {"goal": "Unrelated."}, expected_revision=0)
        other_run = tx.start_run("other", 1, "eng-002")
        event = tx.append_event(other_run, "main", "outside", "tool_result", {})
    with pytest.raises(ValidationError, match="another task"):
        update(store, request(run, evidence_ids=[event["event_id"]]))
    assert store.db.execute("SELECT count(*) FROM progress_heads").fetchone()[0] == 0


def test_deliverable_drop_is_explicit_and_does_not_delete_file(ledger):
    store, run = ledger
    path = store.root / "result.txt"
    path.write_text("current application output")
    update(store, request(run, deliverables=[{"path": "result.txt", "op": "upsert", "description": "Generated output."}]))
    update(store, request(run, 1, step_updates=[], deliverables=[]))
    assert store.db.execute("SELECT count(*) FROM progress_deliverables").fetchone()[0] == 1
    update(store, request(run, 2, step_updates=[], deliverables=[{"path": "result.txt", "op": "drop"}]))
    assert store.db.execute("SELECT count(*) FROM progress_deliverables").fetchone()[0] == 0
    assert path.read_text() == "current application output"


def test_failure_before_projection_rolls_back_and_retry_can_commit(ledger, monkeypatch):
    store, run = ledger
    original = Transaction.enqueue
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(Transaction, "enqueue", fail)
    with pytest.raises(OSError, match="disk full"):
        update(store, request(run))
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM records").fetchone()[0] == 0
    monkeypatch.setattr(Transaction, "enqueue", original)
    assert update(store, request(run))["revision"] == 1


def test_concurrent_corrections_have_one_cas_winner(ledger):
    store, run = ledger
    def attempt(action):
        with ContinuityStore(store.root) as connection:
            try:
                update(connection, request(run, next=action))
                return "committed"
            except Conflict:
                return "conflict"
    with ThreadPoolExecutor(max_workers=2) as workers:
        outcomes = list(workers.map(attempt, ["Run check A.", "Run check B."]))
    assert sorted(outcomes) == ["committed", "conflict"]


@pytest.mark.parametrize("changes", [
    {"expected_revision": True}, {"phase": ""}, {"blocker": ""}, {"next": "x" * 17000},
    {"step_updates": [{"step_id": "edit", "status": "verified"}]},
    {"deliverables": [{"path": "../escape", "op": "upsert", "description": "Bad path."}]},
])
def test_invalid_progress_is_rejected_before_mutation(ledger, changes):
    store, run = ledger
    with pytest.raises(ValidationError):
        update(store, request(run, **changes))
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 0


def test_progress_cli_returns_bound_revision(ledger, run_hx):
    store, run = ledger
    path = store.root / "delta.json"
    path.write_text(json.dumps(request(run)))
    result = run_hx("progress", "--file", str(path), "--root", str(store.root), env_extra={"HARNESS_ID": "eng-001"})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["revision"] == 1
    read = run_hx("progress", "--run", run, "--root", str(store.root), env_extra={"HARNESS_ID": "eng-001"})
    assert read.returncode == 0, read.stderr
    state = json.loads(read.stdout)
    assert state["revision"] == 1 and state["cursor"]["payload"]["next"] == "Patch the transaction boundary."


def test_snapshot_reads_only_the_current_cursor_and_checks_owner(ledger):
    store, run = ledger
    assert snapshot(store, run)["revision"] == 0
    with pytest.raises(Conflict, match="worker"):
        snapshot(store, run, worker_id="eng-002")
    update(store, request(run))
    queries = []
    store.db.set_trace_callback(queries.append)
    try:
        current = snapshot(store, run)
    finally:
        store.db.set_trace_callback(None)
    assert current["revision"] == 1
    assert not any("progress_steps" in query or "progress_deliverables" in query for query in queries)


def test_amendment_rejects_old_run_reads_and_writes(ledger):
    store, run = ledger
    update(store, request(run))
    with store.transaction() as tx:
        tx.put_task("T", {"goal": "A changed current goal."}, expected_revision=1)
    with pytest.raises(Conflict, match="current task revision"):
        update(store, request(run, 1))
    with pytest.raises(Conflict, match="current task revision"):
        snapshot(store, run)
