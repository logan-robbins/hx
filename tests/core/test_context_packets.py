from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from hx import checks, context_packets as packets, observer, planning, progress, unit_execution
from hx.continuity_store import Conflict, ContinuityStore, SCHEMA_VERSION
from hx.errors import ValidationError
from hx.facts import RequiredContextOverflow
from .test_appmap import mapped
from .test_map_updates import active
from .test_planning import plan, unit
from .test_unit_execution import fleet


@pytest.fixture
def assignment(fleet):
    store, repo = fleet[:2]
    body = plan(fleet)
    body["tasks"][0]["checks"]["unit"]["argv"][-1] = "assert 'a  b' != 'a b'\nprint('not discarded')"
    planning.apply(store, body)
    run = unit_execution.assign(store, "implement", 1, "eng-001")["run_id"]
    with store.transaction() as tx:
        event = tx.append_event(run, "main", "evidence", "tool_result", {"finding": "The current task needs an exact pending cursor."})
        tx.classify(run, "main", expected_revision=0, through=1, dispositions={1: "reduced"})
    return store, repo, run, event


def fact(assignment, name="F1", kind="finding", payload=None, inputs=None, **kwargs):
    store, _, _, event = assignment
    with store.transaction() as tx:
        old = tx.record(name)
        tx.put_record(name, expected_version=old["version"] if old else 0, task_id="implement", kind=kind,
            payload={"schema_version": 1, **(payload or {"text": "The cursor remains frozen until every event is classified."})},
            evidence=[event["event_id"]], inputs=inputs or {}, reason="Required by the current unit.",
            expires_when="The current source or task changes.", **kwargs)


def test_packet_preserves_exact_unit_constraints_cursor_map_and_commands(assignment, fleet):
    store, repo, run, _ = assignment
    fact(assignment, "cursor", "cursor", {"step": "fix", "phase": "implementing", "last": "Preserved the pending tail.", "next": "Run the late-event check.", "blocker": "Do not publish before integration passes."})
    fact(assignment, "decision", "decision", {"choice": "a frozen input range", "because": "late arrivals must remain unacknowledged", "when": "the companion commits its result"})
    packet = packets.issue(store, run, request_id="one", instructions="Do not change the public command contract.")
    text = packet["text"]
    assert "Do not acknowledge events arriving after the frozen boundary." in text
    assert "Run the late-event check." in text and "late arrivals must remain unacknowledged" in text
    assert "Do not publish before integration passes." in text
    assert "assert 'a  b' != 'a b'\\nprint('not discarded')" in text
    assert packet["charged_tokens"] == len(text.encode()) <= 8000
    assert packet["records"] == {"cursor": 1, "decision": 1}
    assert not packet["native_reset_ready"]


def test_required_map_nodes_are_included_without_retrieval_search(fleet, monkeypatch):
    store, repo = fleet[:2]
    body = plan(fleet)
    body["tasks"][0]["map_inputs"] = [{"repository": fleet[2]["repo_id"], "snapshot": fleet[6], "id": "compiler", "version": 1}]
    planning.apply(store, body)
    run = unit_execution.assign(store, "implement", 1, "eng-001")["run_id"]
    monkeypatch.setattr(Path, "rglob", lambda *a, **k: pytest.fail("packet searched the repository"))
    packet = packets.issue(store, run, request_id="map")
    assert '"symbol":"Compiler.build"' in packet["text"]
    assert packet["map_inputs"] == body["tasks"][0]["map_inputs"]


def test_whole_optional_facts_are_selected_without_loading_large_ones(assignment):
    store, _, run, _ = assignment
    fact(assignment, "A-small")
    fact(assignment, "Z-large", payload={"text": "irrelevant detail " * 10000})
    original = store.db.row_factory
    def bounded(cursor, row):
        for column, value in zip(cursor.description, row):
            if column[0] == "payload" and isinstance(value, str):
                assert len(value.encode()) < 32768, "compiler decoded a large optional fact"
        return original(cursor, row)
    store.db.row_factory = bounded
    try:
        packet = packets.issue(store, run, request_id="small")
    finally:
        store.db.row_factory = original
    assert "A-small" in packet["records"] and packet["omitted"]["Z-large"] == "budget"
    with pytest.raises(RequiredContextOverflow):
        packets.issue(store, run, request_id="required-large", required_ids=["Z-large"])


def test_unprocessed_correction_and_large_pending_output_survive_forced_boundary(assignment):
    store, _, run, _ = assignment
    with store.transaction() as tx:
        correction = tx.append_event(run, "main", "correction", "correction", {"text": "Do not use the old framework; use the current adapter."})
        huge = tx.append_event(run, "main", "huge", "tool_result", {"output": "x" * 100000})
    original = store.db.row_factory
    def bounded(cursor, row):
        for column, value in zip(cursor.description, row):
            if column[0] == "payload" and isinstance(value, str):
                assert len(value.encode()) < 32768
        return original(cursor, row)
    store.db.row_factory = bounded
    try:
        packet = packets.issue(store, run, request_id="forced")
    finally:
        store.db.row_factory = original
    assert "Do not use the old framework" in packet["text"]
    assert "hx evidence " + huge["event_id"] in packet["text"]
    assert [item["event_id"] for item in packet["pending"]] == [correction["event_id"], huge["event_id"]]
    assert not packet["extraction_ready"]
    with pytest.raises(Conflict, match="planned checkpoint waits"):
        packets.issue(store, run, request_id="planned", mode="planned")


def test_classified_pending_events_still_gate_planned_boundary(assignment):
    store, _, run, _ = assignment
    with store.transaction() as tx:
        event = tx.append_event(run, "main", "pending", "assistant_message", {"text": "Unresolved external operation."})
        tx.classify(run, "main", expected_revision=1, through=2, dispositions={2: "pending"})
    packet = packets.issue(store, run, request_id="forced")
    assert packet["pending"][0]["event_id"] == event["event_id"]
    with pytest.raises(Conflict):
        packets.issue(store, run, request_id="planned", mode="planned")


def test_packet_retry_is_immutable_and_ack_does_not_skip_late_events(assignment):
    store, _, run, _ = assignment
    first = packets.issue(store, run, request_id="one")
    with store.transaction() as tx:
        late = tx.append_event(run, "main", "late", "correction", {"text": "Keep this new obligation."})
    assert packets.issue(store, run, request_id="one") == first
    packets.acknowledge(store, first["checkpoint_id"], run)
    assert store.db.execute("SELECT disposition FROM events WHERE event_id=?", (late["event_id"],)).fetchone()[0] is None
    next_packet = packets.issue(store, run, request_id="two")
    assert next_packet["pending"][0]["event_id"] == late["event_id"]
    assert "Keep this new obligation" not in first["text"]


def test_superseded_checkpoints_retire_and_ack_releases_predecessor(assignment):
    store, _, run, _ = assignment
    first = packets.issue(store, run, request_id="one")
    second = packets.issue(store, run, request_id="two")
    third = packets.issue(store, run, request_id="three")
    assert store.db.execute("SELECT count(*) FROM checkpoints").fetchone()[0] == 2
    with pytest.raises(ValidationError):
        packets.read(store, first["checkpoint_id"], run)
    with pytest.raises(Conflict, match="retired"):
        packets.issue(store, run, request_id="one")
    with pytest.raises(Conflict, match="currently issued"):
        packets.acknowledge(store, second["checkpoint_id"], run)
    packets.acknowledge(store, third["checkpoint_id"], run)
    assert store.db.execute("SELECT count(*) FROM checkpoints").fetchone()[0] == 1
    deleted = store.collect_artifacts()
    assert first["packet_hash"] in deleted and second["packet_hash"] in deleted
    assert packets.read(store, third["checkpoint_id"], run)["text"] == third["text"]


def test_stale_optional_source_is_omitted_and_stale_checkpoint_cannot_rehydrate(assignment):
    store, repo, run, _ = assignment
    path = repo / "compiler.py"
    fact(assignment, inputs={"files": {"compiler.py": hashlib.sha256(path.read_bytes()).hexdigest()}})
    first = packets.issue(store, run, request_id="one")
    path.write_text("changed current application state\n")
    with pytest.raises(Conflict, match="source changed"):
        packets.read(store, first["checkpoint_id"], run)
    next_packet = packets.issue(store, run, request_id="two")
    assert next_packet["omitted"]["F1"] == "source_changed"
    assert "F1" not in next_packet["records"]


def test_facts_do_not_cross_tasks_when_worker_is_reused(assignment):
    store, repo, run, _ = assignment
    fact(assignment, payload={"text": "OLD TASK SPECIFIC FACT"})
    packets.issue(store, run, request_id="old")
    unit_execution.stop(store, run)
    body = {"schema_version": 1, "plan_id": "new-plan", "expected_revision": 0,
            "repository": planning.assignment(store, "implement")["repository"],
            "goal": "Verify a different goal.", "constraints": [], "tasks": [unit(repo, "new-unit")]}
    planning.apply(store, body)
    new_run = unit_execution.assign(store, "new-unit", 1, "eng-001")["run_id"]
    packet = packets.issue(store, new_run, request_id="new")
    assert "OLD TASK SPECIFIC FACT" not in packet["text"] and packet["records"] == {}
    with pytest.raises(Conflict, match="outside this task"):
        packets.issue(store, new_run, request_id="wrong", required_ids=["F1"])


def test_overflow_never_truncates_required_obligations_or_marks_events(assignment):
    store, _, run, event = assignment
    with pytest.raises(RequiredContextOverflow):
        packets.issue(store, run, request_id="tiny", max_tokens=100, optional_tokens=0)
    assert store.db.execute("SELECT count(*) FROM checkpoints").fetchone()[0] == 0
    assert store.db.execute("SELECT disposition FROM events WHERE event_id=?", (event["event_id"],)).fetchone()[0] == "reduced"
    with store.transaction() as tx:
        for index in range(33):
            tx.append_event(run, "main", f"tail-{index}", "tool_result", {"result": index})
    with pytest.raises(RequiredContextOverflow, match="32 unresolved"):
        packets.issue(store, run, request_id="too-many")


def test_capture_lag_includes_bytes_not_yet_seen_by_observer(assignment, tmp_path):
    store, _, run, _ = assignment
    source = tmp_path / "native.jsonl"
    source.write_bytes(b"")
    source_id = observer.register(store, run_id=run, stream_id="native", path=source, decoder="hook-v1", session_id="S")
    observer.drain(store, source_id)
    ready = packets.issue(store, run, request_id="empty", mode="planned")
    assert ready["extraction_ready"]
    source.write_bytes(b"new bytes without a complete record")
    forced = packets.issue(store, run, request_id="late")
    assert forced["capture"][0]["lag"] and not forced["extraction_ready"]
    with pytest.raises(Conflict):
        packets.issue(store, run, request_id="planned", mode="planned")


def test_receipts_are_scoped_evidence_with_recovery_not_unqualified_success(assignment):
    store, _, run, _ = assignment
    checked = checks.run_check(store, run, "unit")
    packet = packets.issue(store, run, request_id="checked")
    assert packet["receipts"]["unit"]["receipt_id"] == checked["receipt_id"]
    assert "Revalidate its inputs before claiming current success" in packet["text"]
    assert "hx evidence " + checked["event_id"] in packet["text"]


def test_compose_cli_uses_ledger_without_personal_memory_and_enforces_owner(assignment, run_hx):
    store, _, run, _ = assignment
    old_memory = store.root / "config" / "eng-001" / "AGENTS.md"
    old_memory.parent.mkdir(parents=True)
    old_memory.write_text("Persona\n## UPDATES BELOW ONLY\nUNRELATED PERSONAL MEMORY\n")
    result = run_hx("compose", "eng-001", "--run", run, "--request", "cli", "--json", "--root", str(store.root))
    assert result.returncode == 0, result.stderr
    metadata = json.loads(result.stdout)
    assert "UNRELATED PERSONAL MEMORY" not in Path(metadata["path"]).read_text()
    own = {"HARNESS_ID": "eng-001"}
    replay = run_hx("compose", "eng-001", "--run", run, "--checkpoint", metadata["checkpoint_id"], "--root", str(store.root), env_extra=own)
    assert replay.returncode == 0 and replay.stdout.strip() == metadata["path"]
    wrong = run_hx("compose", "eng-001", "--run", run, "--checkpoint", metadata["checkpoint_id"], "--root", str(store.root), env_extra={"HARNESS_ID": "eng-002"})
    assert wrong.returncode != 0
    ack = run_hx("compose", "eng-001", "--run", run, "--checkpoint", metadata["checkpoint_id"], "--ack", "--root", str(store.root), env_extra=own)
    assert ack.returncode == 0 and json.loads(ack.stdout)["acknowledged"]


def test_changed_request_or_fact_cannot_reuse_a_checkpoint(assignment):
    store, _, run, _ = assignment
    fact(assignment)
    first = packets.issue(store, run, request_id="one")
    with pytest.raises(Conflict, match="different inputs"):
        packets.issue(store, run, request_id="one", instructions="New mandatory instruction.")
    fact(assignment, payload={"text": "The current implementation uses a different cursor."})
    with pytest.raises(Conflict, match="facts changed"):
        packets.read(store, first["checkpoint_id"], run)


def test_provider_counter_is_versioned_and_byte_fallback_preserves_unicode(assignment):
    store, _, run, _ = assignment
    packet = packets.issue(store, run, request_id="counter", instructions="必须保留当前约束。", count_tokens=lambda text: len(text.encode()), tokenizer_id="test-byte-counter-v1")
    assert packet["tokenizer"] == "test-byte-counter-v1"
    assert packet["charged_tokens"] == len(packet["text"].encode())
    with pytest.raises(ValidationError, match="counter requires"):
        packets.issue(store, run, request_id="unnamed", count_tokens=lambda text: len(text))


def test_schema_nine_upgrade_adds_checkpoint_replay_keys(assignment):
    store, _, run, _ = assignment
    store.db.execute("DROP TABLE context_requests")
    store.db.execute("PRAGMA user_version=9")
    with ContinuityStore(store.root) as upgraded:
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        first = packets.issue(upgraded, run, request_id="one")
        assert packets.issue(upgraded, run, request_id="one") == first


def test_progress_cannot_clear_scope_pause_and_context_preserves_it(assignment):
    store, repo, run, _ = assignment
    before = packets.issue(store, run, request_id="before-pause")
    unexpected = repo / "unexpected.py"
    unexpected.write_text("outside_scope = True\n")
    assert not unit_execution.audit(store, run)["within_scope"]
    unexpected.unlink()
    progress.update(store, {"task_id": "implement", "run_id": run, "expected_revision": 0,
        "phase": "implementing", "step_updates": [], "next": "Request scope reconciliation.",
        "blocker": "The assignment is paused.", "deliverables": [], "evidence_ids": []})
    assert store.db.execute("SELECT phase FROM runs WHERE run_id=?", (run,)).fetchone()[0] == "paused"
    with pytest.raises(Conflict, match="phase changed"):
        packets.read(store, before["checkpoint_id"], run)
    packet = packets.issue(store, run, request_id="paused")
    assert "Do not mutate files" in packet["text"]
    assert not unit_execution.audit(store, run)["within_scope"]


def test_new_binding_constraint_requires_a_fresh_checkpoint(assignment):
    store, _, run, _ = assignment
    first = packets.issue(store, run, request_id="before")
    fact(assignment, "new-constraint", "constraint", {"text": "Do not deploy before the integration check passes."})
    with pytest.raises(Conflict, match="required context changed"):
        packets.read(store, first["checkpoint_id"], run)
    current = packets.issue(store, run, request_id="after")
    assert "Do not deploy before the integration check passes." in current["text"]
