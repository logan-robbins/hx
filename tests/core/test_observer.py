from __future__ import annotations

import json
import select
import threading
import time
from pathlib import Path

import pytest

from hx.continuity_store import Conflict, ContinuityStore
from hx.events import DecodeGap, decode
from hx.observer import drain, notify, register, serve, socket_path


@pytest.fixture
def capture(tmp_path):
    with ContinuityStore(tmp_path) as store:
        with store.transaction() as tx:
            tx.put_task("T1", {"goal": "capture public execution"}, expected_revision=0)
            run = tx.start_run("T1", 1, "eng-001")
        path = tmp_path / "session.jsonl"
        path.write_bytes(b"")
        source = register(store, run_id=run, stream_id="main", path=path, decoder="hook-v1", session_id="S1")
        yield store, run, path, source


def append(path, body):
    data = (json.dumps(body, ensure_ascii=False) + "\n").encode()
    with path.open("ab") as handle:
        handle.write(data)
    return len(data)


def tool(call="C1", text="ok"):
    return {"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_use_id": call,
            "tool_input": {"command": "true"}, "tool_response": {"text": text, "exit_code": 0}}


def payloads(store):
    return [json.loads(row[0]) for row in store.db.execute("SELECT payload FROM events ORDER BY seq")]


def source_state(store, source):
    row = store.db.execute("SELECT * FROM capture_sources WHERE source_id=?", (source,)).fetchone()
    return {**dict(row), "payload": json.loads(row["payload"])}


def test_incremental_offsets_survive_restart_and_noop_decodes_nothing(capture):
    store, _, path, source = capture
    length = append(path, tool())
    first = drain(store, source)
    assert first["bytes"] == length
    assert first["offset"] == length
    assert first["events"] == 1
    with ContinuityStore(store.root) as reopened:
        assert drain(reopened, source)["bytes"] == 0
        added = append(path, tool("C2"))
        assert drain(reopened, source)["bytes"] == added
    assert len(payloads(store)) == 2
    assert source_state(store, source)["payload"]["decoded_bytes"] == length + added


def test_same_size_rewrite_outside_tail_cannot_clear_readiness(capture):
    import os
    from hx.observer import source_snapshot
    from hx.native_producer import require_drained
    store, run, path, source = capture
    append(path, tool(text="old " + "unchanged tail " * 30))
    drain(store, source)
    previous = path.stat()
    path.write_bytes(path.read_bytes().replace(b"old ", b"new "))
    os.utime(path, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1_000_000))
    with store.transaction() as tx:
        assert source_snapshot(tx, run)["sources"][0]["lag"]
        with pytest.raises(Conflict, match="registered capture sources"):
            require_drained(tx, run)
    result = drain(store, source)
    assert result["bytes"] == 0  # No ordinary full-history reread.
    assert any("metadata changed without append" in gap["reason"] for gap in result["gaps"])
    drain(store, source)  # Updating the observed timestamp must not clear the gap.
    with store.transaction() as tx:
        with pytest.raises(Conflict, match="registered capture sources"):
            require_drained(tx, run)


def test_readiness_bounds_source_scope_and_does_not_read_bodies(capture, monkeypatch):
    from hx.observer import MAX_SOURCES, source_snapshot
    from hx.native_producer import require_drained
    store, run, path, source = capture
    drain(store, source)
    for index in range(MAX_SOURCES):
        register(store, run_id=run, stream_id=f"extra-{index}", path=path,
                 decoder="hook-v1", session_id=f"extra-{index}")
    def no_reads(*args, **kwargs):
        raise AssertionError("readiness must not open transcript bodies")
    monkeypatch.setattr(Path, "open", no_reads)
    with store.transaction() as tx:
        snapshot = source_snapshot(tx, run)
        assert snapshot["overflow"] and len(snapshot["sources"]) == MAX_SOURCES
        with pytest.raises(Conflict, match="excessive scope"):
            require_drained(tx, run)


def test_incremental_reads_are_new_bytes_plus_fixed_tail_fingerprints(capture, monkeypatch):
    store, _, path, source = capture
    for index in range(100):
        append(path, tool(f"old{index}", "old output " * 20))
    drain(store, source)
    added = append(path, tool("new", "new output"))
    original = Path.open
    reads = []

    class BoundedReader:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.handle.close()

        def __getattr__(self, name):
            return getattr(self.handle, name)

        def read(self, size=-1):
            assert 0 <= size <= 128, "ordinary capture must not read the full transcript"
            data = self.handle.read(size)
            reads.append(len(data))
            return data

        def readline(self, size=-1):
            assert size > 0, "record reads need an explicit bound"
            data = self.handle.readline(size)
            reads.append(len(data))
            return data

    def opening(candidate, *args, **kwargs):
        handle = original(candidate, *args, **kwargs)
        return BoundedReader(handle) if candidate == path else handle

    monkeypatch.setattr(Path, "open", opening)
    result = drain(store, source)
    assert result["bytes"] == added
    assert sum(reads) <= added + 256
    assert result["events"] == 1


def test_partial_utf8_and_json_are_not_acknowledged(capture):
    store, _, path, source = capture
    data = (json.dumps(tool(text="你好"), ensure_ascii=False) + "\n").encode()
    split = data.index("好".encode()) + 1
    path.write_bytes(data[:split])
    first = drain(store, source)
    assert first["pending_tail"] and first["offset"] == 0 and not first["gaps"]
    with path.open("ab") as handle:
        handle.write(data[split:])
    second = drain(store, source)
    assert second["offset"] == len(data) and not second["pending_tail"]
    assert payloads(store)[0]["observation"]["data"]["tool_response"]["text"] == "你好"


def test_decode_gap_keeps_complete_prefix_but_never_skips_bad_line(capture):
    store, _, path, source = capture
    length = append(path, tool())
    with path.open("ab") as handle:
        handle.write(b"not JSON\n")
    append(path, tool("C2"))
    result = drain(store, source)
    assert result["offset"] == length and result["gaps"] and result["pending_tail"]
    assert len(payloads(store)) == 1
    assert drain(store, source)["bytes"] == 0


def test_rotation_and_same_inode_truncation_are_new_generations(capture):
    store, _, path, source = capture
    append(path, tool(text="first"))
    drain(store, source)
    original = source_state(store, source)["generation"]
    rotated = path.with_suffix(".old")
    path.rename(rotated)
    append(path, tool("C2", "second"))
    result = drain(store, source)
    second = source_state(store, source)["generation"]
    assert second != original and result["gaps"]
    # Regrow to greater than the old offset so a size-only check would miss it.
    path.write_text(json.dumps(tool("C3", "third " * 100)) + "\n")
    drain(store, source)
    assert source_state(store, source)["generation"] != second
    assert len(payloads(store)) == 3


def test_duplicate_delivery_uses_native_id_not_text(capture):
    store, _, path, source = capture
    append(path, tool("C1", "same"))
    append(path, tool("C1", "same"))
    append(path, tool("C2", "same"))
    drain(store, source)
    assert len(payloads(store)) == 2
    # Every observed location remains addressable even when the event is deduplicated.
    assert store.db.execute("SELECT count(*) FROM event_origins").fetchone()[0] == 3


def test_cli_register_drain_status_use_the_requested_root(capture, run_hx):
    store, run, path, source = capture
    registered = run_hx("observe", "register", "--root", str(store.root), "--run", run,
                        "--stream", "main", "--path", str(path), "--decoder", "hook-v1", "--session", "S1")
    assert registered.returncode == 0, registered.stderr
    assert registered.stdout.strip() == source
    append(path, tool())
    drained = run_hx("observe", "drain", "--root", str(store.root))
    assert drained.returncode == 0, drained.stderr
    assert json.loads(drained.stdout)[0]["events"] == 1
    status = run_hx("observe", "status", "--root", str(store.root))
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)[0]["committed_offset"] == path.stat().st_size


def test_complementary_observations_share_logical_identity_without_overwrite(capture):
    store, _, path, source = capture
    first = tool()
    append(path, first)
    first["source_fingerprints"] = {"src/a.py": "hash1"}
    append(path, first)
    drain(store, source)
    one, two = payloads(store)
    assert one["logical_id"] == two["logical_id"]
    assert one["observation_hash"] != two["observation_hash"]
    assert "source_fingerprints" not in one["observation"]["data"]
    assert two["observation"]["data"]["source_fingerprints"] == {"src/a.py": "hash1"}


def test_unknown_identity_preserves_equal_distinct_records(capture):
    store, _, path, source = capture
    for _ in range(2):
        append(path, {"hook_event_name": "UserPromptSubmit", "prompt": "continue"})
    drain(store, source)
    events = payloads(store)
    assert len(events) == 2
    assert events[0]["logical_id"] != events[1]["logical_id"]
    assert all(not x["identity_certain"] for x in events)


def test_large_public_payload_is_owned_before_offset_commit(capture):
    store, _, path, source = capture
    text = "output\n" * 1000 + "ERROR: decisive middle\n" + "more\n" * 1000
    append(path, tool(text=text))
    drain(store, source)
    payload = payloads(store)[0]
    assert "observation" not in payload
    path.unlink()
    with store.transaction() as tx:
        data = json.loads(tx.read_artifact(payload["artifact_hash"]))
        assert data["data"]["tool_response"]["text"] == text
    assert store.collect_artifacts() == []


def test_spool_pressure_does_not_advance_or_lose_required_payload(capture):
    store, _, path, source = capture
    append(path, tool())
    blocked = drain(store, source, spool_limit=1)
    assert blocked["offset"] == 0 and blocked["backpressure"]
    assert payloads(store) == []
    result = drain(store, source)
    assert result["offset"] == path.stat().st_size and not result["backpressure"]


def test_offset_and_events_roll_back_together_on_storage_failure(capture, monkeypatch):
    from hx.continuity_store import Transaction
    store, _, path, source = capture
    append(path, tool())
    original = Transaction.enqueue

    def fail(*args, **kwargs):
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(Transaction, "enqueue", fail)
    with pytest.raises(RuntimeError, match="injected"):
        drain(store, source)
    assert source_state(store, source)["committed_offset"] == 0
    assert payloads(store) == []
    monkeypatch.setattr(Transaction, "enqueue", original)
    assert drain(store, source)["events"] == 1


def test_artifact_io_failure_is_not_mistaken_for_a_readable_source_gap(capture, monkeypatch):
    from hx.continuity_store import Transaction
    store, _, path, source = capture
    append(path, tool(text="large evidence " * 1000))

    def fail(*args, **kwargs):
        raise OSError("disk full while installing evidence")

    monkeypatch.setattr(Transaction, "put_artifact", fail)
    with pytest.raises(OSError, match="disk full"):
        drain(store, source)
    assert source_state(store, source)["committed_offset"] == 0
    assert payloads(store) == []


def test_claude_captures_public_messages_failures_usage_but_no_thinking():
    events = decode("claude-v1", {"type": "assistant", "uuid": "A1", "message": {
        "role": "assistant", "usage": {"input_tokens": 50, "cache_read_input_tokens": 80},
        "content": [{"type": "thinking", "thinking": "PRIVATE SECRET"},
                    {"type": "text", "text": "The interface changed."}]}})
    assert events[0].data["text"] == "The interface changed."
    assert events[0].usage == {"input_tokens": 50, "cache_read_input_tokens": 80}
    assert "PRIVATE SECRET" not in repr(events)
    result = decode("claude-v1", {"type": "user", "uuid": "U1", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "C1", "content": "assertion failed", "is_error": True}]}})
    assert result[0].kind == "tool_result" and result[0].data["is_error"]


@pytest.mark.parametrize("adapter", ["codex", "claude", "meta", "grok", "pi"])
def test_all_adapter_hook_payloads_preserve_actions(adapter):
    body = {"hookEventName": "post_tool_use", "toolName": "read_file", "toolUseId": "C1",
            "toolInput": {"path": "src/x"}, "toolResult": {"text": "exact result"}}
    if adapter in {"claude", "pi"}:
        body = {"event": "log", "tool_name": "read", "tool_use_id": "C1",
                "tool_input": {"path": "src/x"}, "tool_response": {"text": "exact result"}}
    result = decode("hook-v1", body)[0]
    assert result.native_id == "tool:C1"
    assert result.data["tool_response"]["text"] == "exact result"


def test_stop_preserves_message_even_when_transcript_is_not_flushed():
    events = decode("hook-v1", {"event": "stop", "last_assistant_message": "Do not reuse R1.",
                               "background_tasks": [{"agent_id": "s1"}]})
    assert [x.kind for x in events] == ["assistant_message", "finish"]
    assert events[0].data["text"] == "Do not reuse R1."
    assert events[1].data["background_tasks"] == [{"agent_id": "s1"}]


def test_pi_sessionless_stdout_needs_no_transcript():
    result = decode("pi-v1", {"type": "tool_execution_end", "toolCallId": "call1",
                             "toolName": "bash", "result": {"content": [{"type": "text", "text": "failure"}]}, "isError": True})
    assert result[0].kind == "tool_result" and result[0].data["is_error"]


def test_pi_full_stdout_lifecycle_excludes_thinking_updates():
    # Shapes from the installed Pi docs/json.md; stdout is not message_end-only.
    stream = [
        {"type": "session", "version": 3, "id": "S"},
        {"type": "agent_start"}, {"type": "turn_start"},
        {"type": "message_start", "message": {"role": "assistant", "content": []}},
        {"type": "message_update", "assistantMessageEvent": {"type": "thinking_delta", "delta": "PRIVATE"}},
        {"type": "tool_execution_start", "toolCallId": "C", "toolName": "bash", "args": {"command": "true"}},
        {"type": "tool_execution_end", "toolCallId": "C", "toolName": "bash", "result": {"text": "ok"}},
        {"type": "message_end", "message": {"role": "assistant", "content": [{"type": "text", "text": "Done."}]}},
        {"type": "turn_end", "toolResults": []}, {"type": "agent_end", "messages": []},
    ]
    events = [event for row in stream for event in decode("pi-v1", row)]
    assert "PRIVATE" not in repr(events)
    assert any(event.kind == "tool_result" for event in events)
    assert any(event.data.get("tool_input") == {"command": "true"} for event in events)
    assert events[-1].data["settled"] is False


@pytest.mark.parametrize("content", [None, {}, [{"type": "text", "text": {}}], [{"type": "new-public-kind"}]])
def test_pi_unknown_completed_content_requires_reconciliation(content):
    with pytest.raises(DecodeGap):
        decode("pi-v1", {"type": "message_end", "message": {"role": "assistant", "content": content}})


def test_unknown_native_shape_is_a_gap_not_an_empty_result():
    with pytest.raises(DecodeGap):
        decode("claude-v1", {"type": "future-message-format", "text": "important"})
    with pytest.raises(DecodeGap):
        decode("claude-v1", {"type": ["not", "a", "type"]})


def test_branch_capture_excludes_inactive_siblings(capture):
    store, run, path, _ = capture
    path = path.with_name("pi.jsonl")
    source = register(store, run_id=run, stream_id="pi", path=path, decoder="pi-v1", session_id="PI",
                      branch_ids=["a", "b"])
    for identifier, parent, text in [("a", None, "root"), ("old", "a", "inactive sibling"),
                                     ("b", "a", "active"), ("c", "b", "new continuation"),
                                     ("d", "c", "later continuation")]:
        append(path, {"type": "message", "id": identifier, "parentId": parent,
                      "message": {"role": "user", "content": text}})
    drain(store, source)
    text = str(payloads(store))
    assert "inactive sibling" not in text
    assert "later continuation" in text
    with pytest.raises(Conflict, match="branch changed"):
        register(store, run_id=run, stream_id="other", path=path, decoder="pi-v1", session_id="PI", branch_ids=["a", "old"])


def test_tree_source_without_active_ancestry_is_not_assumed_current(capture):
    store, run, path, _ = capture
    source = register(store, run_id=run, stream_id="pi", path=path, decoder="pi-v1", session_id="PI")
    append(path, {"type": "message", "id": "a", "parentId": None, "message": {"role": "user", "content": "unknown branch"}})
    result = drain(store, source)
    assert result["gaps"] and result["offset"] == 0
    assert payloads(store) == []


def test_resident_reconciles_missed_notifications_and_rejects_second_owner(capture):
    store, _, path, source = capture
    stop = threading.Event()
    errors = []

    def resident():
        try:
            serve(store.root, interval=0.05, stop=stop)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=resident)
    thread.start()
    try:
        deadline = time.monotonic() + 3
        while not socket_path(store.root).exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        with pytest.raises(Exception, match="already running"):
            serve(store.root, interval=0.05, stop=stop)
        append(path, tool())  # Deliberately do not notify.
        while not payloads(store) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(payloads(store)) == 1
        append(path, tool("C2"))
        assert notify(store.root, source)
        while len(payloads(store)) < 2 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(payloads(store)) == 2
    finally:
        stop.set()
        notify(store.root)
        thread.join(timeout=3)
    assert not thread.is_alive() and not errors
    assert not socket_path(store.root).exists()


@pytest.mark.skipif(not hasattr(select, "kqueue"), reason="native vnode notifications are macOS/BSD-specific")
def test_native_file_notification_drains_before_reconciliation(capture, monkeypatch):
    from hx.observer import FileNotifications
    store, _, path, _ = capture
    stop, ready = threading.Event(), threading.Event()
    original = FileNotifications.refresh
    errors = []

    def refresh(self, ledger):
        original(self, ledger)
        ready.set()

    def resident():
        try:
            serve(store.root, interval=30, stop=stop)
        except BaseException as exc:
            errors.append(exc)

    monkeypatch.setattr(FileNotifications, "refresh", refresh)
    thread = threading.Thread(target=resident)
    thread.start()
    try:
        assert ready.wait(3)
        append(path, tool())  # Neither a hook notification nor a reconciliation tick.
        deadline = time.monotonic() + 3
        while not payloads(store) and time.monotonic() < deadline:
            time.sleep(0.01)
        assert len(payloads(store)) == 1
    finally:
        stop.set()
        notify(store.root)
        thread.join(timeout=3)
    assert not thread.is_alive() and not errors
