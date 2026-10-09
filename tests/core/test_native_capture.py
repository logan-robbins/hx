from __future__ import annotations

import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from hx.continuity_store import Conflict, ContinuityStore, Transaction
from hx.errors import ValidationError
from hx.events import DecodeGap
from hx.native_capture import bind, enqueue
from hx.observer import drain, register


@pytest.fixture
def ledger(tmp_path):
    with ContinuityStore(tmp_path) as store:
        with store.transaction() as tx:
            tx.put_task("T", {"goal": "Capture the original run."}, expected_revision=0)
            run = tx.start_run("T", 1, "eng-001")
        yield store, run


def tool(call="C1", text="ok"):
    return {"hook_event_name": "PostToolUse", "session_id": "S1", "tool_use_id": call,
            "tool_name": "Bash", "tool_input": {"command": "true"}, "tool_response": {"text": text}}


def binding(store, run, **kwargs):
    return bind(store, run_id=run, stream_id="main", session_id="S1", **{"adapter": "claude", **kwargs})


@pytest.mark.parametrize("adapter", ["claude", "codex", "meta", "grok", "pi"])
def test_hook_transports_capture_without_reading_a_transcript(ledger, adapter, monkeypatch):
    store, run = ledger
    session = binding(store, run, adapter=adapter)
    payload = tool()
    payload["transcript_path"] = "/unreadable/vendor/session.jsonl"
    from hx import transcripts
    monkeypatch.setattr(transcripts, "context_tokens", lambda *_: pytest.fail("capture rescanned a transcript"))
    result = enqueue(store, session, "D1", payload)
    assert result["committed"] and len(result["event_ids"]) == 1
    assert store.db.execute("SELECT head_seq FROM cursors").fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM outbox WHERE kind='capture_ready'").fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM native_event_origins").fetchone()[0] == 1


def test_uncertain_ack_retry_returns_same_evidence_after_reopen_and_close(ledger):
    store, run = ledger
    session = binding(store, run)
    result = enqueue(store, session, "D1", tool())
    with store.transaction() as tx:
        tx.finish_run(run, "finished")
        tx.put_task("T2", {"goal": "A new task."}, expected_revision=0)
        newer = tx.start_run("T2", 1, "eng-001")
    with ContinuityStore(store.root) as reopened:
        assert enqueue(reopened, session, "D1", tool()) == result
        with pytest.raises(Conflict, match="original active run"):
            enqueue(reopened, session, "late", tool("C2"))
        with pytest.raises(Conflict, match="already bound"):
            binding(reopened, newer)
        assert reopened.db.execute("SELECT count(*) FROM events WHERE run_id=?", (newer,)).fetchone()[0] == 0


def test_delivery_conflicts_and_owner_or_session_mismatch_do_not_advance(ledger):
    store, run = ledger
    session = binding(store, run)
    assert binding(store, run) == session
    enqueue(store, session, "D1", tool())
    with pytest.raises(Conflict, match="different payload"):
        enqueue(store, session, "D1", tool(text="changed"))
    with pytest.raises(Conflict, match="caller"):
        enqueue(store, session, "D2", tool(), worker_id="eng-002")
    with pytest.raises(Conflict, match="session"):
        enqueue(store, session, "D2", {**tool(), "sessionId": "wrong"})
    assert store.db.execute("SELECT head_seq FROM cursors").fetchone()[0] == 1


def test_amended_task_requires_explicit_rebind(ledger):
    store, run = ledger
    session = binding(store, run)
    with store.transaction() as tx:
        tx.put_task("T", {"goal": "Changed goal."}, expected_revision=1)
    with pytest.raises(Conflict, match="task changed"):
        enqueue(store, session, "D1", tool())
    assert store.db.execute("SELECT count(*) FROM native_deliveries").fetchone()[0] == 0


def test_repeated_native_identity_keeps_provenance_but_distinct_calls_survive(ledger):
    store, run = ledger
    session = binding(store, run)
    first = enqueue(store, session, "D1", tool())
    second = enqueue(store, session, "D2", tool())
    assert first["event_ids"] == second["event_ids"]
    other = enqueue(store, session, "D3", tool("C2"))
    assert other["event_ids"] != first["event_ids"]
    source_path = store.root / "native.jsonl"
    source_path.write_text(json.dumps(tool()) + "\n")
    source = register(store, run_id=run, stream_id="main", session_id="S1", path=source_path, decoder="hook-v1")
    drain(store, source)
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 2
    assert store.db.execute("SELECT event_id FROM event_origins").fetchone()[0] == first["event_ids"][0]


def test_missing_native_ids_never_deduplicate_by_output(ledger):
    store, run = ledger
    session = binding(store, run)
    payload = tool()
    del payload["tool_use_id"]
    first = enqueue(store, session, "D1", payload)
    second = enqueue(store, session, "D2", payload)
    assert first["event_ids"] != second["event_ids"]
    for row in store.db.execute("SELECT payload FROM events"):
        assert not json.loads(row[0])["identity_certain"]


def test_pi_sessionless_events_preserve_public_text_and_usage_only(ledger):
    store, run = ledger
    session = binding(store, run, adapter="pi", decoder="pi-v1")
    ignored = enqueue(store, session, "streaming", {"type": "message_update", "private": "secret thought"})
    assert ignored["event_ids"] == []
    result = enqueue(store, session, "final", {"type": "message_end", "message": {
        "role": "assistant", "content": [{"type": "thinking", "thinking": "secret thought"},
                                         {"type": "text", "text": "The check failed."}],
        "usage": {"input": 100, "output": 10}}})
    payload = store.db.execute("SELECT payload FROM events WHERE event_id=?", (result["event_ids"][0],)).fetchone()[0]
    assert "The check failed." in payload and "secret thought" not in payload
    assert "secret thought" not in "\n".join(store.db.iterdump())
    assert json.loads(store.db.execute("SELECT latest_usage FROM native_bindings").fetchone()[0]) == {"input": 100, "output": 10}


def test_camelcase_boundary_preserves_final_text_and_background_work(ledger):
    store, run = ledger
    session = binding(store, run, adapter="grok")
    enqueue(store, session, "stop", {"hookEventName": "Stop", "sessionId": "S1",
        "lastAssistantMessage": "The check is still running.", "backgroundTasks": ["check-1"], "turnId": "turn-1"})
    payloads = [json.loads(row[0])["observation"]["data"] for row in store.db.execute("SELECT payload FROM events ORDER BY seq")]
    assert payloads[0]["text"] == "The check is still running."
    assert payloads[1] == {"background_tasks": ["check-1"], "turn_id": "turn-1"}


def test_unknown_format_and_branch_payload_are_not_acknowledged(ledger):
    store, run = ledger
    session = binding(store, run)
    with pytest.raises(DecodeGap):
        enqueue(store, session, "bad", {"hook_event_name": "Unknown"})
    with pytest.raises(ValidationError, match="ancestry"):
        enqueue(store, session, "branch", {**tool(), "parentId": "old"})
    assert store.db.execute("SELECT count(*) FROM native_deliveries").fetchone()[0] == 0


def test_spool_rejection_installs_no_artifacts_and_can_retry(ledger):
    store, run = ledger
    session = binding(store, run)
    payload = tool(text="evidence" * 2000)
    with pytest.raises(Conflict, match="spool full"):
        enqueue(store, session, "large", payload, spool_limit=2000)
    assert not list(store.artifacts.iterdir())
    assert store.db.execute("SELECT count(*) FROM native_deliveries").fetchone()[0] == 0
    accepted = enqueue(store, session, "large", payload)
    assert accepted["event_ids"]
    # A committed acknowledgement survives pressure and does not reread evidence.
    assert enqueue(store, session, "large", payload, spool_limit=1) == accepted


def test_storage_failure_rolls_back_delivery_and_event(ledger, monkeypatch):
    store, run = ledger
    session = binding(store, run)
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(Transaction, "enqueue", fail)
    with pytest.raises(OSError, match="disk full"):
        enqueue(store, session, "D1", tool())
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM native_deliveries").fetchone()[0] == 0
    assert store.db.execute("SELECT head_seq FROM cursors").fetchone()[0] == 0


def test_concurrent_retry_has_one_delivery_and_event(ledger):
    store, run = ledger
    session = binding(store, run)
    def deliver(_):
        with ContinuityStore(store.root) as connection:
            return enqueue(connection, session, "same", tool())
    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(deliver, range(2)))
    assert results[0] == results[1]
    assert store.db.execute("SELECT count(*) FROM native_deliveries").fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_schema_three_upgrade_preserves_existing_task(ledger):
    store, _ = ledger
    for table in ("native_event_origins", "native_deliveries", "native_bindings"):
        store.db.execute(f"DROP TABLE {table}")
    store.db.execute("PRAGMA user_version=3")
    with ContinuityStore(store.root) as upgraded:
        with upgraded.transaction() as tx:
            assert tx.task("T")["payload"]["goal"] == "Capture the original run."
        assert upgraded.db.execute("SELECT count(*) FROM native_bindings").fetchone()[0] == 0
        assert not upgraded.db.execute("PRAGMA foreign_key_check").fetchall()


def test_capture_cli_binds_and_acknowledges_durable_delivery(ledger, run_hx, child_env):
    store, run = ledger
    result = run_hx("capture", "bind", "--run", run, "--stream", "main", "--adapter", "codex",
                    "--session", "S1", "--root", str(store.root))
    assert result.returncode == 0, result.stderr
    session = result.stdout.strip()
    process = subprocess.run([sys.executable, "-m", "hx", "capture", "enqueue", session,
        "--delivery", "D1", "--root", str(store.root)], input=json.dumps(tool()),
        env=child_env(HARNESS_ID="eng-001"), text=True, capture_output=True)
    assert process.returncode == 0, process.stderr
    receipt = json.loads(process.stdout)
    assert receipt["committed"]
    assert store.db.execute("SELECT event_id FROM events").fetchone()[0] == receipt["event_ids"][0]
