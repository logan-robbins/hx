"""`hx metrics`: per-agent seam metrics and the fleet rollup (spec 07.4, 08).

The per-id document follows CONTRACTS.md exactly; `next_10_turns` counts
assistant turns from the referenced transcript and is `null` throughout when
no parseable transcript exists. The fleet document enriches every board item
with harness, ties and links plus a summary.
"""

from __future__ import annotations

import json
import threading
import time

import pytest

from hx.errors import NotFound
from hx.metrics import (
    collect_fleet,
    per_id,
    render_fleet_text,
    render_per_id_text,
    snapshot_scopes,
    summarize,
    watch,
)
from hx import streams

DISPATCHED = "2026-09-20T12:00:00Z"


def write_tasks(root, tasks):
    (root / "tasks.json").write_text(json.dumps(tasks, indent=2))


def write_stream(root, item_id, records):
    path = streams.main_stream(root, item_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        for seq, record in enumerate(records, start=1):
            record.setdefault("seq", seq)
            record.setdefault("stream", f"{item_id}-main")
            handle.write(json.dumps(record) + "\n")
    return path


def write_transcript(root, name="t.jsonl"):
    """Two assistant turns: a context-file Read, then an unrelated Edit."""
    path = root / "run" / "eng-001" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {"type": "assistant", "isSidechain": False, "timestamp": "2026-09-20T12:51:00Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": "Read",
              "input": {"file_path": "/x/run/eng-001/eng-001-main.context.md"}}]}},
        {"type": "assistant", "isSidechain": False, "timestamp": "2026-09-20T12:52:00Z",
         "message": {"role": "assistant", "content": [
             {"type": "tool_use", "name": "Edit", "input": {"file_path": "/x/a.py"}},
             {"type": "tool_use", "name": "Read",
              "input": {"file_path": "/x/src/wip.py"}}]}},
    ]
    with path.open("w") as handle:
        for record in records:
            handle.write(json.dumps(record) + "\n")
    return path


def seam_record(transcript=None):
    record = {"ts": "2026-09-20T12:50:00Z", "event": "seam", "source": "clear",
              "prompt_version": {"base": "b1", "role": "r2"},
              "context_tokens_before": 91044, "context_file_bytes": 18422,
              "working_set_size": 1}
    return record


def boundary_record(transcript):
    return {"ts": "2026-09-20T12:50:30Z", "event": "boundary",
            "ref": {"transcript": str(transcript)}}


# --- per-id contract ------------------------------------------------------------


def test_per_id_shape_matches_contracts(instance, work_item):
    work_item("eng-001", "working", dispatched=DISPATCHED)
    write_tasks(instance, {"eng-001": {"goal": "## Goal\n\nDo it.", "dispatched": DISPATCHED}})
    transcript = write_transcript(instance)
    write_stream(instance, "eng-001", [seam_record(), boundary_record(transcript)])
    (instance / "state" / "eng-001").mkdir(parents=True, exist_ok=True)
    (instance / "state" / "eng-001" / "s.json").write_text(json.dumps(
        {"working_set": {"files": [{"path": "/x/src/wip.py", "note": "n"}]}}))
    doc = per_id(instance, "eng-001")
    assert set(doc) == {"id", "stream", "dispatched", "seams", "totals"}
    assert (doc["id"], doc["stream"], doc["dispatched"]) == ("eng-001", "eng-001-main", DISPATCHED)
    (seam,) = doc["seams"]
    assert set(seam) == {"seq", "ts", "source", "prompt_version", "context_tokens_before",
                         "context_file_bytes", "working_set_size", "next_10_turns"}
    assert seam["prompt_version"] == "b1/r2"
    assert seam["next_10_turns"] == {"turns": 2, "tool_calls": 3,
                                    "reads_of_context_file": 1,
                                    "reads_of_working_set": 1, "other": 1}
    assert doc["totals"] == {"seams": 1, "tool_calls": 3, "reads_of_context_file": 1,
                             "reads_of_working_set": 1, "other": 1}


def test_per_id_unknown_id_raises(instance):
    with pytest.raises(NotFound):
        per_id(instance, "eng-404")


def test_per_id_missing_transcript_is_null_not_fabricated(instance, work_item):
    work_item("eng-001", "working", dispatched=DISPATCHED)
    write_tasks(instance, {"eng-001": {"goal": "## Goal\n\nDo it.", "dispatched": DISPATCHED}})
    write_stream(instance, "eng-001", [seam_record()])
    (seam,) = per_id(instance, "eng-001")["seams"]
    assert seam["next_10_turns"] == {"turns": None, "tool_calls": None,
                                    "reads_of_context_file": None,
                                    "reads_of_working_set": None, "other": None}


def test_per_id_text_form(instance, work_item):
    work_item("eng-001", "working", dispatched=DISPATCHED)
    write_tasks(instance, {"eng-001": {"goal": "## Goal\n\nDo it.", "dispatched": DISPATCHED}})
    write_stream(instance, "eng-001", [seam_record()])
    text = render_per_id_text(per_id(instance, "eng-001"))
    assert "eng-001" in text and "totals seams=1" in text


# --- fleet ----------------------------------------------------------------------


def test_fleet_shape_and_summary(instance, work_item):
    work_item("eng-001", "working", dispatched=DISPATCHED)
    work_item("qa-002", "idle", pod="qa")
    write_tasks(instance, {
        "eng-001": {"goal": "## Goal\n\nBuild it for qa-002.", "dispatched": DISPATCHED,
                    "addenda": [{"text": "note", "ts": DISPATCHED}]},
        "qa-002": {"goal": "## Goal\n\nVerdict.", "dispatched": DISPATCHED},
    })
    doc = collect_fleet(instance)
    assert set(doc) == {"root_abs", "ts", "agents", "summary", "partner", "graph"}
    assert doc["graph"] is None
    by_id = {agent["id"]: agent for agent in doc["agents"]}
    assert set(by_id) == {"eng-001", "qa-002"}
    eng = by_id["eng-001"]
    for key in ("harness", "ties", "links", "limit_paused", "scope", "state"):
        assert key in eng, key
    assert eng["harness"]["model"] == "claude-opus-5"
    assert eng["ties"] == {"mentions": ["qa-002"]}
    assert eng["links"]["work_item"] is not None
    assert doc["summary"]["agents"] == 2
    assert doc["summary"]["working"] == 1
    assert render_fleet_text(doc).startswith("# fleet ")


def test_summarize_is_pure():
    agents = [
        {"id": "eng-001", "pod": "eng", "state": "working", "session_alive": True,
         "limit_paused": True, "needs_input": False},
        {"id": "qa-002", "pod": "qa", "state": "idle", "session_alive": False,
         "limit_paused": False, "needs_input": True},
    ]
    assert summarize(agents) == {
        "agents": 2, "working": 1, "by_pod": {"eng": {"working": 1}, "qa": {"idle": 1}},
        "paused_limit": ["eng-001"], "needs_input": ["qa-002"], "dead": [],
    }


# --- watch ----------------------------------------------------------------------


def test_snapshot_scopes_cover_tasks(instance):
    (instance / "tasks.json").write_text("{}")
    scopes = snapshot_scopes(instance)
    assert "tasks" in scopes and scopes["tasks"] > 0


def test_watch_emits_snapshot_then_delta(instance, work_item):
    work_item("eng-001", "idle")
    events = watch(instance, interval=0.05, slow=3600)
    first = next(events)
    assert first["type"] == "snapshot"
    assert [agent["id"] for agent in first["fleet"]["agents"]] == ["eng-001"]

    def touch():
        time.sleep(0.2)
        (instance / "tasks.json").write_text("{}")

    thread = threading.Thread(target=touch)
    thread.start()
    second = next(events)
    thread.join()
    assert second["type"] == "delta"
    assert "tasks" in second["changed"]
