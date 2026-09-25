from __future__ import annotations

import copy
import hashlib
import json

import pytest

from hx.continuity_store import Conflict, ContinuityStore
from hx.errors import ValidationError
from hx.facts import RequiredContextOverflow
from hx.passes import commit, prepare


@pytest.fixture
def ledger(tmp_path):
    with ContinuityStore(tmp_path) as store:
        with store.transaction() as tx:
            tx.put_task("T1", {"goal": "preserve late events", "workdir": str(tmp_path)}, expected_revision=0)
            run = tx.start_run("T1", 1, "eng-001")
            event = tx.append_event(run, "main", "E1", "tool_result", {"text": "observed cursor 41"})
        yield store, run, event


def answer(frozen, operations=(), disposition="extracted"):
    return {"schema_version": 1, "pass_id": frozen["pass_id"], "task_revision": frozen["task_revision"],
            "event_digest": frozen["event_digest"], "operations": list(operations),
            "dispositions": {e["event_id"]: disposition for e in frozen["events"]}}


def create(event, identifier="F1", kind="finding", **payload):
    return {"op": "create", "record_id": identifier, "expected_version": 0, "record": {
        "kind": kind, "payload": {"schema_version": 1, **(payload or {"text": "Ingest advanced to cursor 41."})},
        "evidence": [event["event_id"]], "inputs": {}, "reason": "needed for the cursor fix",
        "expires_when": "source changes"}}


def test_late_event_is_not_in_frozen_pass_or_acknowledged(ledger):
    store, run, event = ledger
    frozen = prepare(store, run, "main", prompt_version="p1")
    with store.transaction() as tx:
        late = tx.append_event(run, "main", "E2", "tool_result", {"text": "late"})
    result = commit(store, frozen["pass_id"], answer(frozen, [create(event)]))
    assert result["to_seq"] == 1
    next_pass = prepare(store, run, "main", prompt_version="p1")
    assert [e["event_id"] for e in next_pass["events"]] == [late["event_id"]]
    assert next_pass["from_seq"] == 1 and next_pass["to_seq"] == 2
    original = json.loads(store.db.execute("SELECT payload FROM passes WHERE pass_id=?", (frozen["pass_id"],)).fetchone()[0])
    assert original == frozen


def test_oversized_event_bodies_never_enter_python_during_preparation(ledger):
    store, run, _ = ledger
    with store.transaction() as tx:
        for index in range(12):
            tx.append_event(run, "main", f"bulk{index}", "tool_result", {"text": "X" * 65536})
    original = store.db.row_factory
    seen = []

    def bounded_rows(cursor, row):
        for column, value in zip(cursor.description, row):
            if column[0] == "payload" and isinstance(value, str):
                seen.append(len(value))
                assert len(value.encode()) <= 2500, "pass loaded an oversized event body"
        return original(cursor, row)

    store.db.row_factory = bounded_rows
    try:
        frozen = prepare(store, run, "main", prompt_version="p1", max_bytes=2500)
    finally:
        store.db.row_factory = original
    assert seen
    assert "payload" in frozen["events"][0]
    assert frozen["events"][1]["requires_read"]
    assert "payload" not in frozen["events"][1]


def test_pass_cannot_expand_event_window_without_bound(ledger):
    store, run, _ = ledger
    with pytest.raises(ValidationError, match="events"):
        prepare(store, run, "main", prompt_version="p1", max_events=1000000)


def test_commit_is_idempotent_even_after_further_events(ledger):
    store, run, event = ledger
    frozen = prepare(store, run, "main", prompt_version="p1")
    response = answer(frozen, [create(event)])
    first = commit(store, frozen["pass_id"], response)
    with store.transaction() as tx:
        tx.append_event(run, "main", "E2", "boundary", {})
    assert commit(store, frozen["pass_id"], response) == first
    assert store.db.execute("SELECT count(*) FROM records").fetchone()[0] == 1
    changed = copy.deepcopy(response)
    changed["dispositions"][event["event_id"]] = "no_change"
    with pytest.raises(Conflict, match="different response"):
        commit(store, frozen["pass_id"], changed)


def test_all_writes_and_cursor_roll_back_if_one_patch_is_invalid(ledger):
    store, run, event = ledger
    frozen = prepare(store, run, "main", prompt_version="p1")
    valid = create(event)
    bad = create(event, "F2")
    bad["record"]["evidence"] = ["invented"]
    with pytest.raises(ValidationError, match="outside"):
        commit(store, frozen["pass_id"], answer(frozen, [valid, bad]))
    assert store.db.execute("SELECT count(*) FROM records").fetchone()[0] == 0
    assert store.db.execute("SELECT classified_seq FROM cursors").fetchone()[0] == 0
    assert store.db.execute("SELECT status FROM passes").fetchone()[0] == "prepared"


def test_task_amendment_invalidates_a_prepared_pass(ledger):
    store, run, _ = ledger
    frozen = prepare(store, run, "main", prompt_version="p1")
    with store.transaction() as tx:
        tx.put_task("T1", {"goal": "different"}, expected_revision=1)
    with pytest.raises(Conflict, match="amended"):
        commit(store, frozen["pass_id"], answer(frozen))


def test_selected_record_change_rejects_pass_without_overwriting(ledger):
    store, run, event = ledger
    with store.transaction() as tx:
        tx.put_record("F1", expected_version=0, task_id="T1", **create(event)["record"])
    frozen = prepare(store, run, "main", record_ids=("F1",), prompt_version="p1")
    with store.transaction() as tx:
        tx.put_record("F1", expected_version=1, task_id="T1", **create(event)["record"])
    with pytest.raises(Conflict, match="changed during extraction"):
        commit(store, frozen["pass_id"], answer(frozen))
    assert store.db.execute("SELECT classified_seq FROM cursors").fetchone()[0] == 0


def test_unrelated_record_update_does_not_block_independent_pass(ledger):
    store, run, event = ledger
    frozen = prepare(store, run, "main", prompt_version="p1")
    with store.transaction() as tx:
        tx.put_record("unrelated", expected_version=0, task_id="T1", **create(event)["record"])
    commit(store, frozen["pass_id"], answer(frozen, [create(event)]))
    assert store.db.execute("SELECT count(*) FROM records").fetchone()[0] == 2


def test_pending_event_is_reselected_and_resolved_without_rewinding_cursor(ledger):
    store, run, event = ledger
    frozen = prepare(store, run, "main", prompt_version="p1")
    commit(store, frozen["pass_id"], answer(frozen, disposition="pending"))
    with store.transaction() as tx:
        later = tx.append_event(run, "main", "E2", "tool_result", {"text": "late"})
    pending = prepare(store, run, "main", prompt_version="p1")
    assert pending["from_seq"] == 1 and pending["to_seq"] == 2
    assert pending["events"][0]["pending"]
    commit(store, pending["pass_id"], answer(pending, [create(event)]))
    assert store.db.execute("SELECT classified_seq FROM cursors").fetchone()[0] == 2
    assert store.db.execute("SELECT count(*) FROM events WHERE disposition='pending'").fetchone()[0] == 0
    assert prepare(store, run, "main", prompt_version="p1") is None


def test_request_cannot_silently_disappear_but_irrelevant_text_can_be_dropped(ledger):
    store, run, _ = ledger
    with store.transaction() as tx:
        request = tx.append_event(run, "main", "U1", "request", {"text": "continue"})
    frozen = prepare(store, run, "main", prompt_version="p1")
    response = answer(frozen, disposition="no_change")
    with pytest.raises(ValidationError, match="uninterpreted"):
        commit(store, frozen["pass_id"], response)
    response["event_reasons"] = {request["event_id"]: "Continuation signal adds no requirement beyond the active task."}
    assert commit(store, frozen["pass_id"], response)["to_seq"] == 2


def test_binding_correction_preserves_exact_span_and_cannot_be_rewritten(ledger):
    store, run, _ = ledger
    with store.transaction() as tx:
        request = tx.append_event(run, "main", "U1", "correction", {"text": "Do not publish. Keep current-state facts only."})
    frozen = prepare(store, run, "main", prompt_version="p1")
    operation = create(request, "U1", kind="constraint", text="Do not publish.")
    commit(store, frozen["pass_id"], answer(frozen, [operation]))
    with store.transaction() as tx:
        tx.append_event(run, "main", "E3", "tool_result", {"text": "more"})
    next_pass = prepare(store, run, "main", prompt_version="p1")
    assert next_pass["record_versions"] == {"U1": 1}
    patch = {"op": "drop", "record_id": "U1", "expected_version": 1, "reason": "save tokens"}
    with pytest.raises(ValidationError, match="explicit task amendment"):
        commit(store, next_pass["pass_id"], answer(next_pass, [patch]))


def test_source_change_rejects_stale_patch(ledger, tmp_path):
    store, run, event = ledger
    path = tmp_path / "source.py"
    path.write_text("old")
    operation = create(event)
    operation["record"]["inputs"] = {"files": {"source.py": hashlib.sha256(b"old").hexdigest()}}
    with store.transaction() as tx:
        tx.put_record("F1", expected_version=0, task_id="T1", **operation["record"])
    frozen = prepare(store, run, "main", record_ids=("F1",), prompt_version="p1")
    path.write_text("new")
    with pytest.raises(Conflict, match="source changed"):
        commit(store, frozen["pass_id"], answer(frozen))


def test_compression_cannot_change_a_command_or_its_condition(ledger):
    store, run, event = ledger
    operation = create(event, "C1", kind="command", command="pytest -q", cwd="/repo", purpose="acceptance", when="the DB is available")
    with store.transaction() as tx:
        tx.put_record("C1", expected_version=0, task_id="T1", **operation["record"])
    frozen = prepare(store, run, "main", record_ids=("C1",), prompt_version="p1")
    operation.update(op="compress", expected_version=1)
    operation["record"]["payload"]["command"] = "pytest"
    with pytest.raises(ValidationError, match="protected"):
        commit(store, frozen["pass_id"], answer(frozen, [operation]))


def test_pass_uses_bounded_events_and_never_loads_unselected_records(ledger):
    store, run, event = ledger
    with store.transaction() as tx:
        for index in range(100):
            tx.append_event(run, "main", f"large{index}", "tool_result", {"text": "X" * 10000})
        unrelated = create(event)["record"]
        unrelated["payload"]["text"] = "DO NOT LOAD THIS" * 10000
        tx.put_record("unrelated", expected_version=0, task_id="T1", **unrelated)
    frozen = prepare(store, run, "main", prompt_version="p1", max_bytes=2500, max_events=4)
    assert len(json.dumps(frozen, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()) <= 2500
    assert len(frozen["events"]) <= 4 and frozen["to_seq"] < 101
    assert "DO NOT LOAD THIS" not in str(frozen)
    assert any(e.get("requires_read") for e in frozen["events"])


def test_mandatory_pass_input_overflow_is_explicit(ledger):
    store, run, _ = ledger
    with pytest.raises(RequiredContextOverflow):
        prepare(store, run, "main", prompt_version="p1", max_bytes=10)
