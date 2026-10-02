from __future__ import annotations

import io
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from hx import hooks, hook_contract, native_capture, native_producer as relay, observer
from hx.continuity_store import Conflict, ContinuityStore, SCHEMA_VERSION
from hx.errors import ValidationError
from .test_native_capture import ledger, tool


def send(store, run, delivery="D1", **kwargs):
    return relay.produce(store, run_id=run, worker_id="eng-001", adapter="claude",
                         session_id="S1", stream_id="main", payload=kwargs.pop("payload", tool()),
                         delivery_id=delivery, **kwargs)


def queued(store):
    return store.db.execute("SELECT count(*) FROM native_producer_queue").fetchone()[0]


def unavailable(*args, **kwargs):
    raise OSError("capture temporarily unavailable")


def test_pi_completed_messages_retry_public_data_and_retain_child_identity(ledger, monkeypatch):
    store, run = ledger
    payload = {"type": "message_end", "agent_id": "child", "message": {"role": "assistant",
        "content": [{"type": "thinking", "thinking": "PRIVATE"}, {"type": "text", "text": "Check failed."}],
        "stopReason": "error", "errorMessage": "Provider unavailable", "usage": {"input": 12}}}
    with monkeypatch.context() as m:
        m.setattr(native_capture, "enqueue", unavailable)
        result = relay.hook(store, run_id=run, worker_id="eng-001", adapter="pi", launch_id="launch",
                            event="message", payload=payload)
    assert result["queued"] and "PRIVATE" not in "\n".join(store.db.iterdump())
    assert relay.retry(store)[0]["committed"]
    public = json.loads(store.db.execute("SELECT payload FROM events").fetchone()[0])["observation"]
    assert public["data"]["agent_id"] == "child"
    assert public["data"]["errorMessage"] == "Provider unavailable"
    assert public["usage"] == {"input": 12}
    for adapter, body in [("claude", payload), ("pi", {**payload, "type": "message_update"}),
                          ("pi", {**payload, "message": {"role": "toolResult"}})]:
        with pytest.raises(ValidationError, match="completed user or assistant"):
            relay.hook(store, run_id=run, worker_id="eng-001", adapter=adapter, launch_id="launch",
                       event="message", payload=body)


def test_durable_retry_after_reopen_keeps_identity_and_only_public_observations(ledger, monkeypatch):
    store, run = ledger
    payload = {**tool(), "private_thinking": "PRIVATE TOKEN", "transcript_path": "/must/not/read"}
    with monkeypatch.context() as m:
        m.setattr(native_capture, "enqueue", unavailable)
        result = send(store, run, payload=payload)
    assert result["queued"] and queued(store) == 1
    assert "PRIVATE TOKEN" not in "\n".join(store.db.iterdump())
    with ContinuityStore(store.root) as reopened:
        result = observer.reconcile(reopened)
        assert result[0]["committed"] and queued(reopened) == 0
        assert send(reopened, run, payload=payload)["event_ids"] == result[0]["event_ids"]
        assert reopened.db.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_muse_model_attempts_retry_without_private_frames_or_duplicate_usage(ledger, monkeypatch):
    store, run = ledger
    payload = {"session_id": "S", "turn_id": "T", "request_id": "T:0:1", "status": "failed",
        "attempt": 1, "step": 0, "error": "API unavailable", "output_text_preview": "PRIVATE",
        "messages": [{"content": "PRIVATE"}], "tools": [{"description": "PRIVATE"}],
        "usage": {"input_tokens": 5, "cached_tokens": 2, "reasoning_tokens": 1}}
    def emit(body):
        return relay.hook(store, run_id=run, worker_id="eng-001", adapter="meta", launch_id="launch",
                          event="model-response", payload=body)
    with monkeypatch.context() as m:
        m.setattr(native_capture, "enqueue", unavailable)
        assert emit(payload)["queued"]
    assert "PRIVATE" not in "\n".join(store.db.iterdump())
    assert relay.retry(store)[0]["committed"]
    assert emit(payload)["committed"]
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
    assert emit({**payload, "request_id": "T:0:2", "attempt": 2})["committed"]
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 2
    item = json.loads(store.db.execute("SELECT payload FROM events ORDER BY seq DESC LIMIT 1").fetchone()[0])["observation"]
    assert item["data"]["attempt"] == 2 and item["data"]["error"] == "API unavailable"
    assert item["usage"] == payload["usage"]
    with pytest.raises(ValidationError, match="verified Muse"):
        relay.hook(store, run_id=run, worker_id="eng-001", adapter="grok", launch_id="launch",
                   event="model-response", payload=payload)
    for bad in ({**payload, "status": "unknown"}, {**payload, "request_id": None}):
        with pytest.raises(ValidationError):
            emit(bad)


def test_lost_ack_replays_committed_delivery_even_after_run_closes(ledger, monkeypatch):
    store, run = ledger
    real = native_capture.enqueue
    def uncertain(*args, **kwargs):
        real(*args, **kwargs)
        raise OSError("ack lost after commit")
    with monkeypatch.context() as m:
        m.setattr(native_capture, "enqueue", uncertain)
        assert send(store, run)["queued"]
    with store.transaction() as tx:
        tx.finish_run(run, "stopped")
    assert relay.retry(store)[0]["committed"]
    assert queued(store) == 0
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_newer_delivery_waits_behind_failed_observation(ledger, monkeypatch):
    store, run = ledger
    with monkeypatch.context() as m:
        m.setattr(native_capture, "enqueue", unavailable)
        send(store, run, "older", payload=tool("old"))
    result = send(store, run, "newer", payload=tool("new"))
    assert result["queued"] and store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 0
    assert all(result["committed"] for result in relay.retry(store))
    assert all(result["committed"] for result in relay.retry(store))
    observations = [json.loads(row[0]) for row in store.db.execute("SELECT payload FROM events ORDER BY seq")]
    assert [row["observation"]["data"]["tool_use_id"] for row in observations] == ["old", "new"]


def test_late_queued_event_never_moves_to_workers_new_task(ledger, monkeypatch):
    store, run = ledger
    with monkeypatch.context() as m:
        m.setattr(native_capture, "enqueue", unavailable)
        send(store, run)
    with store.transaction() as tx:
        tx.finish_run(run, "stopped")
        tx.put_task("new", {"goal": "Unrelated task"}, expected_revision=0)
        newer = tx.start_run("new", 1, "eng-001")
    assert not relay.retry(store)[0]["committed"]
    assert queued(store) == 1
    assert store.db.execute("SELECT count(*) FROM events WHERE run_id=?", (newer,)).fetchone()[0] == 0


def test_stalled_binding_does_not_block_another_workers_capture(ledger, monkeypatch):
    store, run = ledger
    with monkeypatch.context() as m:
        m.setattr(native_capture, "enqueue", unavailable)
        send(store, run, "old-head")
        send(store, run, "old-tail", payload=tool("C2"))
        with store.transaction() as tx:
            tx.finish_run(run, "stopped")
            tx.put_task("other", {"goal": "Independent capture"}, expected_revision=0)
            other = tx.start_run("other", 1, "eng-002")
        relay.produce(store, run_id=other, worker_id="eng-002", adapter="claude", session_id="S2", stream_id="main",
                      payload={**tool(), "session_id": "S2"}, delivery_id="other-head")
    results = relay.retry(store)
    assert [result["committed"] for result in results] == [False, True]
    assert queued(store) == 2
    assert store.db.execute("SELECT count(*) FROM events WHERE run_id=?", (other,)).fetchone()[0] == 1


def test_observation_arriving_after_stop_is_retained_for_original_run(ledger, run_hx):
    store, run = ledger
    with store.transaction() as tx:
        tx.finish_run(run, "stopped")
        tx.put_task("next", {"goal": "A different assignment"}, expected_revision=0)
        newer = tx.start_run("next", 1, "eng-001")
    result = send(store, run, payload={"event": "stop", "last_assistant_message": "The external outcome is unknown."})
    assert result["queued"] and queued(store) == 1
    assert "external outcome is unknown" in store.db.execute("SELECT payload FROM native_producer_queue").fetchone()[0]
    assert store.db.execute("SELECT count(*) FROM events WHERE run_id=?", (newer,)).fetchone()[0] == 0
    pending = run_hx("capture", "pending", "--run", run, "--root", str(store.root), env_extra={"HARNESS_ID": "eng-001"})
    assert pending.returncode == 0 and json.loads(pending.stdout)["pending_deliveries"] == 1


def test_delivery_reuse_and_bound_fail_before_more_queue_bytes(ledger, monkeypatch):
    store, run = ledger
    with monkeypatch.context() as m:
        m.setattr(native_capture, "enqueue", unavailable)
        send(store, run)
        with pytest.raises(Conflict, match="different public"):
            send(store, run, payload=tool(text="different"))
        with pytest.raises(Conflict, match="queue full"):
            send(store, run, "second", queue_bytes=1)
    assert queued(store) == 1


def test_native_session_alias_mismatch_is_not_normalized_away(ledger):
    store, run = ledger
    with pytest.raises(Conflict, match="native session"):
        send(store, run, payload={**tool(), "sessionId": "different-session"})
    assert queued(store) == 0


def test_retry_reads_one_large_record_then_yields(ledger, monkeypatch):
    store, run = ledger
    with monkeypatch.context() as m:
        m.setattr(native_capture, "enqueue", unavailable)
        send(store, run, "large", payload=tool("large", "x" * 300000))
        send(store, run, "small", payload=tool("small"))
    assert len(relay.retry(store)) == 1 and queued(store) == 1
    assert len(relay.retry(store)) == 1 and queued(store) == 0


@pytest.mark.parametrize("adapter", ["claude", "codex", "grok", "meta", "pi"])
def test_installed_entrypoint_routes_planned_hooks_without_legacy_memory(instance, monkeypatch, adapter):
    config = instance / "config" / "eng-001" / "harness.json"
    data = json.loads(config.read_text())
    data["flavor"] = adapter
    config.write_text(json.dumps(data))
    with ContinuityStore(instance) as store:
        with store.transaction() as tx:
            tx.put_task("T", {"goal": "Actual planned capture"}, expected_revision=0)
            run = tx.start_run("T", 1, "eng-001")
        env = {"HARNESS_ROOT": str(instance), "HARNESS_ID": "eng-001", "HX_CONTINUITY_RUN": run,
               "HX_CONTINUITY_LAUNCH": "L1", "HX_CONTINUITY_ADAPTER": adapter}
        hook_contract.installation_args(instance, "eng-001", adapter, env)
        monkeypatch.setitem(hooks._HANDLERS, "log", lambda *a, **k: pytest.fail("legacy log handler"))
        monkeypatch.setitem(hooks._HANDLERS, "context", lambda *a, **k: pytest.fail("legacy memory composer"))
        for event, payload in [("context", {"source": "startup"}), ("log", tool()),
                               ("request", {"prompt": "Do not change the release target."}),
                               ("log-failure", {"error": "timeout", "tool_use_id": "failed"}),
                               ("stop", {"last_assistant_message": "Verification remains open."})]:
            assert hooks.main(["--id", "eng-001", event], stdin=io.StringIO(json.dumps(payload)), env=env) == 0
        kinds = [row[0] for row in store.db.execute("SELECT kind FROM events ORDER BY rowid")]
        assert kinds == ["boundary", "tool_result", "request", "tool_result", "assistant_message", "finish"]
        assert queued(store) == 0
        with store.transaction() as tx:
            assert not relay.status(tx, run)["lag"]


def test_sessionless_children_and_launches_have_distinct_streams(ledger):
    store, run = ledger
    for launch, child in [("L1", "C1"), ("L1", "C2"), ("L2", "C1")]:
        relay.hook(store, run_id=run, worker_id="eng-001", adapter="pi", launch_id=launch,
                   event="subagent-stop", payload={"agent_id": child, "last_assistant_message": "Child result"})
    assert store.db.execute("SELECT count(*) FROM cursors").fetchone()[0] == 3
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 6


@pytest.mark.parametrize("session", [None, ""])
def test_sessionless_child_native_ids_cannot_collide(ledger, session):
    store, run = ledger
    for child in ("C1", "C2"):
        relay.hook(store, run_id=run, worker_id="eng-001", adapter="pi", launch_id="L1", event="request",
                   payload={"session_id": session, "agent_id": child, "native_event_id": "message-1", "prompt": "Verify the contract."})
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 2
    assert queued(store) == 0


def test_malformed_hook_marks_gap_without_importing_private_input(instance):
    with ContinuityStore(instance) as store:
        with store.transaction() as tx:
            tx.put_task("T", {"goal": "Keep gaps visible"}, expected_revision=0)
            run = tx.start_run("T", 1, "eng-001")
        env = {"HARNESS_ROOT": str(instance), "HARNESS_ID": "eng-001", "HX_CONTINUITY_RUN": run}
        assert hooks.main(["--id", "eng-001", "log"], stdin=io.StringIO("private malformed input"), env=env) == 0
        with store.transaction() as tx:
            assert relay.status(tx, run)["gap"]
            with pytest.raises(Conflict, match="gap"):
                relay.require_drained(tx, run)
        assert "private malformed input" not in "\n".join(store.db.iterdump())


def test_hook_reader_never_requests_an_unbounded_read():
    class Bounded(io.StringIO):
        def read(self, size=-1):
            assert 0 < size <= observer.MAX_RECORD_BYTES + 1
            return super().read(size)
    assert hooks.read_payload(Bounded('{"source":"startup"}')) == {"source": "startup"}


@pytest.mark.parametrize("adapter", ["codex", "grok", "meta"])
def test_native_normalizer_reaches_planned_ledger(instance, child_env, adapter):
    config = instance / "config" / "eng-001" / "harness.json"
    data = json.loads(config.read_text())
    data["flavor"] = adapter
    config.write_text(json.dumps(data))
    with ContinuityStore(instance) as store:
        with store.transaction() as tx:
            tx.put_task("T", {"goal": "Native normalized capture"}, expected_revision=0)
            run = tx.start_run("T", 1, "eng-001")
        env = child_env(HARNESS_ROOT=str(instance), HARNESS_ID="eng-001", HX_CONTINUITY_RUN=run,
                        HX_CONTINUITY_LAUNCH="native-launch", HX_CONTINUITY_ADAPTER=adapter)
        hook_contract.installation_args(instance, "eng-001", adapter, env)
        command = [sys.executable, str(instance / "adapters" / adapter / "hook.py"), "--id", "eng-001",
                   "--hook-bin", f"{sys.executable} -m hx.hooks"]
        if adapter in {"codex", "meta"}:
            command += ["--root", str(instance)]
        result = subprocess.run([*command, "log"], input=json.dumps({"toolName": "Bash", "toolUseId": "C1",
            "sessionId": "S1", "toolResult": "The check failed."}), text=True, capture_output=True, env=env)
        assert result.returncode == 0, result.stderr
        row = store.db.execute("SELECT payload FROM events").fetchone()
        assert "The check failed." in row[0]
        assert "Bash" in row[0] and queued(store) == 0


def test_capture_pending_and_retry_cli_scope(ledger, run_hx, monkeypatch):
    store, run = ledger
    with monkeypatch.context() as m:
        m.setattr(native_capture, "enqueue", unavailable)
        send(store, run)
    pending = run_hx("capture", "pending", "--run", run, "--root", str(store.root))
    assert pending.returncode == 0
    assert json.loads(pending.stdout)["pending_deliveries"] == 1
    refused = run_hx("capture", "retry", "--root", str(store.root), env_extra={"HARNESS_ID": "eng-001"})
    assert refused.returncode != 0 and queued(store) == 1
    retried = run_hx("capture", "retry", "--root", str(store.root))
    assert retried.returncode == 0 and json.loads(retried.stdout)[0]["committed"]


def test_concurrent_hook_retries_capture_once(ledger):
    store, run = ledger
    def execute(_):
        with ContinuityStore(store.root) as other:
            return send(other, run)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(execute, range(2)))
    assert results[0]["event_ids"] == results[1]["event_ids"]
    assert queued(store) == 0
    assert store.db.execute("SELECT count(*) FROM events").fetchone()[0] == 1


def test_schema_ten_upgrade_installs_empty_producer_state(tmp_path):
    with ContinuityStore(tmp_path) as store:
        store.db.execute("DROP TABLE native_producer_queue")
        store.db.execute("DROP TABLE native_capture_gaps")
        store.db.execute("PRAGMA user_version=10")
    with ContinuityStore(tmp_path) as upgraded:
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert queued(upgraded) == 0
