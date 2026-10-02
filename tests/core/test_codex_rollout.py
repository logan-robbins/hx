from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from hx import observer
from hx.codex_rollout import DECODER, POLICY, VERSION
from hx.continuity_store import Conflict, ContinuityStore, digest
from hx.events import DecodeGap, decode
from .test_appmap import mapped
from .test_context_packets import assignment
from .test_map_updates import active
from .test_native_capture import ledger
from .test_native_launch import configured
from .test_native_sources import launched, report, hook
from .test_observer import source_state
from .test_unit_execution import fleet


def row(kind, **payload):
    return {"timestamp": "2026-10-01T00:00:00Z", "type": kind, "payload": payload}


def meta(**changes):
    return row("session_meta", **{"id": "S", "session_id": "S", "cli_version": VERSION, **changes})


def append(path, *bodies):
    with path.open("a") as output:
        for body in bodies:
            output.write(json.dumps(body) + "\n")


@pytest.fixture
def captured(ledger):
    store, run = ledger
    home = store.root.resolve()
    info = home.stat()
    path = home / "rollout.jsonl"
    path.touch()
    source = observer.register(store, run_id=run, stream_id="native", path=path, decoder=DECODER, session_id="S",
        native_scope={"root": str(home), "root_identity": [info.st_dev, info.st_ino],
            "actor_id": None, "launch_id": "launch", "branch_policy": POLICY})
    return store, run, path, source


def test_public_rollout_preserves_tool_evidence_usage_and_excludes_private_fields(captured):
    store, run, path, source = captured
    append(path, meta(), row("response_item", type="reasoning", id="R", summary=[{"text": "PRIVATE"}], encrypted_content="PRIVATE"),
        row("world_state", full=True, state={"managed_developer_instructions": "PRIVATE"}),
        row("response_item", type="message", role="developer", content=[{"type": "input_text", "text": "PRIVATE"}]),
        row("response_item", type="message", role="user", id="U", content=[{"type": "input_text", "text": "Do not reuse stale evidence."}]),
        row("response_item", type="function_call", call_id="C", name="exec_command", arguments='{"cmd":"false"}', encrypted_function_args="PRIVATE"),
        row("event_msg", type="item_completed", item={"type": "CommandExecution", "id": "C", "exit_code": 1, "stderr": "failed", "status": "completed"}),
        row("response_item", type="function_call_output", call_id="C", output="Process exited with code 1"),
        row("response_item", type="message", role="assistant", id="A", content=[{"type": "output_text", "text": "Check failed."}]),
        row("event_msg", type="item_completed", item={"type": "AgentMessage", "id": "A", "content": "Check failed."}),
        row("token_usage_record", response_id="response", usage={"input_tokens": 8, "cached_input_tokens": 2, "output_tokens": 4, "total_tokens": 12},
            turn_token_usage={"total_tokens": 12}, thread_token_usage={"total_tokens": 12}),
        row("event_msg", type="task_complete", turn_id="turn"))
    first = observer.drain(store, source, max_records=4)
    assert first["pending_tail"] and not first["gaps"]
    with ContinuityStore(store.root) as reopened:
        last = observer.drain(reopened, source)
    assert not last["pending_tail"] and not last["gaps"]
    assert "PRIVATE" not in "\n".join(store.db.iterdump())
    events = [json.loads(item[0]) for item in store.db.execute("SELECT payload FROM events ORDER BY seq")]
    tools = [item for item in events if item["observation"]["kind"] == "tool_result"]
    assert len(tools) == 2 and len({item["logical_id"] for item in tools}) == 1
    assert any(item["observation"]["data"].get("is_error") for item in tools)
    assert sum(item["observation"]["data"].get("text") == "Check failed." for item in events) == 1
    assert source_state(store, source)["payload"]["latest_usage"]["cached_input_tokens"] == 2
    assert not events[-1]["observation"]["data"]["settled"]


@pytest.mark.parametrize("invalid", [meta(cli_version="unknown"), meta(id="other"), meta(forked_from_id="old"),
    row("response_item", type="message", role="user", content="Unbound source.")])
def test_unverified_identity_version_and_missing_metadata_do_not_advance(captured, invalid):
    store, _, path, source = captured
    append(path, invalid)
    result = observer.drain(store, source)
    assert result["offset"] == 0 and result["events"] == 0 and result["gaps"]


@pytest.mark.parametrize("invalid", [row("compacted", message="obsolete summary"), row("event_msg", type="thread_rolled_back"),
    meta(), row("response_item", type="context_compaction", encrypted_content="PRIVATE"),
    row("new_native_record", contents="unknown"), row("event_msg", type="item_completed", item={"type": "Unknown"}),
    row("token_usage_record", session_id="other", usage={"input_tokens": 2})])
def test_boundary_changes_and_unknown_shapes_remain_gaps(captured, invalid):
    store, _, path, source = captured
    append(path, meta())
    first = observer.drain(store, source)
    append(path, invalid)
    result = observer.drain(store, source)
    assert result["offset"] == first["offset"] and result["events"] == 0 and result["gaps"]


def test_discovered_tools_and_multimodal_outputs_preserve_public_information():
    search = decode(DECODER, row("response_item", type="tool_search_output", call_id="C", execution="client", status="completed",
        tools=[{"type": "namespace", "name": "repo", "tools": [{"type": "function", "name": "lookup",
            "parameters": {"type": "object"}, "encrypted_content": "PRIVATE"}]}]))[0]
    assert search.data["tool_response"]["tools"][0]["name"] == "repo"
    assert search.data["tool_response"]["tools"][0]["tools"][0]["name"] == "lookup"
    assert "PRIVATE" not in repr(search)
    output = decode(DECODER, row("response_item", type="function_call_output", call_id="C", output=[
        {"type": "input_text", "text": "Observed file."}, {"type": "input_image", "image_url": "PRIVATE"},
        {"type": "encrypted_content", "encrypted_content": "PRIVATE"}]))[0]
    assert output.data["tool_response"]["text"] == "Observed file." and "PRIVATE" not in repr(output)
    with pytest.raises(DecodeGap):
        decode(DECODER, row("response_item", type="message", role="assistant", content=[{"type": "new-block"}]))
    with pytest.raises(DecodeGap):
        decode(DECODER, row("response_item", type="web_search_call", action={"type": "other"}))
    with pytest.raises(DecodeGap):
        decode(DECODER, row("response_item", type="tool_search_output", tools=[{"type": "other", "name": "unknown"}]))


@pytest.mark.parametrize("launched", ["codex"], indirect=True)
def test_planned_codex_hook_registers_its_versioned_source(launched):
    store, run, _, home, _ = launched
    path = home / "S.jsonl"
    assert hook(launched, path, last_assistant_message="Final hook evidence.") == 0
    source = store.db.execute("SELECT source_id,decoder_version FROM capture_sources WHERE run_id=?", (run,)).fetchone()
    assert source["decoder_version"] == DECODER
    append(path, meta(), row("response_item", type="message", id="A", role="assistant", content=[{"type": "output_text", "text": "Delayed final evidence."}]))
    assert observer.drain(store, source["source_id"])["events"] == 2
    with pytest.raises(Conflict, match="child ancestry"):
        report(launched, path, adapter="codex", event="subagent-stop", agent_id="child", agent_transcript_path=str(home / "child.jsonl"))


@pytest.mark.skipif(not os.environ.get("HX_CODEX_TEST_BIN"), reason="requires an explicitly selected installed Codex CLI; local provider fixture only")
@pytest.mark.parametrize("launched", ["codex"], indirect=True)
def test_installed_codex_rollout_and_hook_contract(launched, configured, tmp_path):
    from .codex_native_fixture import run as native_run
    store, run, _, home, _ = launched
    native_events = native_run(os.environ["HX_CODEX_TEST_BIN"], home, configured[1], tmp_path)
    names = {item["hook_event_name"] for item in native_events}
    assert {"SessionStart", "UserPromptSubmit", "PreToolUse", "PostToolUse", "Stop"} <= names
    first = native_events[0]
    path = Path(first["transcript_path"])
    sources = report(launched, path, adapter="codex", session_id=first["session_id"])
    result = observer.drain(store, sources[0])
    assert not result["gaps"] and not result["pending_tail"]
    assert result["offset"] == path.stat().st_size
    assert observer.drain(store, sources[0])["bytes"] == 0
    payloads = [json.loads(item[0]) for item in store.db.execute("""SELECT e.payload FROM events e
        JOIN event_origins o USING(event_id) WHERE o.source_id=? ORDER BY e.seq""", (sources[0],))]
    tool = next(item for item in native_events if item["hook_event_name"] == "PostToolUse")
    expected = digest(("native", first["session_id"], "tool_result", "tool:" + tool["tool_use_id"]))
    results = [item["observation"]["data"] for item in payloads if item["logical_id"] == expected]
    assert len(results) == 2
    assert any(isinstance(item["tool_response"], dict) and item["tool_response"].get("exit_code") == 0 for item in results)
    assert "native-tool-receipt" in json.dumps(results)
    assert any(item["observation"]["data"].get("text") == "Native transcript contract observed." for item in payloads)
    assert "PRIVATE" not in "\n".join(store.db.iterdump())
    assert source_state(store, sources[0])["payload"]["latest_usage"]["total_tokens"] == 36
