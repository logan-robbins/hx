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
                         "context_file_bytes", "working_set_size", "next_10_turns",
                         "searches_before_first_edit", "modules_read_before_first_write",
                         "task_reprints"}
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


# --- learn_repeats (TOOL_SKILL_PLAN step 4) -------------------------------------------------


def test_fleet_summary_counts_completion_repeats_since_each_rules_last_accept(instance, monkeypatch):
    monkeypatch.delenv("HX_LEARNINGS", raising=False)
    repeats = instance / "state" / "learn" / "repeats.jsonl"
    repeats.parent.mkdir(parents=True, exist_ok=True)
    rows = [
        {"id": "eng-001", "rule": "L1", "score": 0.91, "ts": "2026-10-01T10:00:00Z", "check": "### Checks"},
        {"id": "eng-001", "rule": "L1", "score": 0.88, "ts": "2026-10-03T10:00:00Z", "check": "### Checks"},
        {"id": "eng-002", "rule": "L2", "score": 0.40, "ts": "2026-10-02T10:00:00Z", "check": "unit"},
    ]
    repeats.write_text("".join(json.dumps(row) + "\n" for row in rows) + "not json\n")
    decisions = instance / "LEARNINGS" / "decisions.jsonl"
    decisions.parent.mkdir(parents=True, exist_ok=True)
    decisions.write_text(json.dumps({"ts": "2026-10-02T00:00:00Z", "decision": "merged", "rule": "L1"}) + "\n")
    doc = collect_fleet(instance)
    assert doc["summary"]["learn_repeats"] == {"L1": 1, "L2": 1}
    assert "learn_repeats(2): L1=1 L2=1" in render_fleet_text(doc)


def test_fleet_summary_has_empty_learn_repeats_without_records(instance):
    assert collect_fleet(instance)["summary"]["learn_repeats"] == {}


# --- post-seam orientation per seam and `--commands` (TOOL_SKILL_PLAN step 5) --------------------


def _tool(tool, ts="2026-09-20T12:51:00Z", **data):
    return {"ts": ts, "event": "post_tool", "tool": tool, "input": json.dumps(data), "output": ""}


def test_per_id_seam_carries_post_seam_orientation(instance, work_item):
    work_item("eng-001", "working", dispatched=DISPATCHED)
    write_tasks(instance, {"eng-001": {"goal": "## Goal\n\nDo it.", "dispatched": DISPATCHED}})
    write_stream(instance, "eng-001", [
        seam_record(),
        _tool("Read", file_path="/x/run/eng-001/eng-001-main.context.md"),
        _tool("Grep", pattern="thing"),
        _tool("Read", file_path="/x/src/a.py"),
        _tool("Bash", command="hx task"),
        _tool("Edit", file_path="/x/src/a.py"),
        _tool("Read", file_path="/x/src/b.py"),
    ])
    (seam,) = per_id(instance, "eng-001")["seams"]
    assert (seam["searches_before_first_edit"], seam["modules_read_before_first_write"],
            seam["task_reprints"]) == (1, 1, 1)
    text = render_per_id_text(per_id(instance, "eng-001"))
    assert text.splitlines()[1].split()[-3:] == ["1", "1", "1"]


def test_per_id_post_seam_window_stops_at_the_next_seam(instance, work_item):
    work_item("eng-001", "working", dispatched=DISPATCHED)
    write_tasks(instance, {"eng-001": {"goal": "## Goal\n\nDo it.", "dispatched": DISPATCHED}})
    second = dict(seam_record(), ts="2026-09-20T13:00:00Z")
    write_stream(instance, "eng-001", [
        seam_record(), _tool("Grep", pattern="a"), _tool("Grep", pattern="b"),
        second, _tool("Edit", ts="2026-09-20T13:01:00Z", file_path="/x/a.py"),
    ])
    first, last = per_id(instance, "eng-001")["seams"]
    assert first["searches_before_first_edit"] == 2
    assert last["searches_before_first_edit"] == 0


def _commands_fixture(instance):
    write_tasks(instance, {
        "eng-001": {"goal": "## Goal\n\nDo it.", "dispatched": DISPATCHED},
        "qa-002": {"goal": "## Goal\n\nCheck it.", "dispatched": DISPATCHED},
    })
    write_stream(instance, "eng-001", [
        _tool("Bash", ts="2026-09-20T11:00:00Z", command="hx task"),  # before dispatch
        _tool("Bash", command="hx task"),
        _tool("Bash", ts="2026-09-20T12:40:00Z",
              command=".venv/bin/hx code search pull && hx complete done --summary s"),
        _tool("exec_command", ts="2026-09-20T12:45:00Z",
              cmd=["bash", "-lc", "hx memory search 'flaky test'"]),
        _tool("Bash", command="hx frobnicate now; ls ~/workspace/hx/src"),  # not an hx command
        _tool("Read", file_path="/x/hx task.md"),                           # not a shell tool
    ])
    write_stream(instance, "qa-002", [
        _tool("Bash", ts="2026-09-20T12:30:00Z", command="hx task"),
        _tool("Bash", ts="2026-09-20T12:55:00Z", command="hx discipline pull --repo . --expect fresh"),
    ])


def test_commands_counts_five_commands_across_two_ids(instance):
    from hx.metrics import command_counts

    _commands_fixture(instance)
    counts = command_counts(instance)
    assert counts == {
        "task": {"calls": 2, "agents": {"eng-001": 1, "qa-002": 1}},
        "code search": {"calls": 1, "agents": {"eng-001": 1}},
        "complete": {"calls": 1, "agents": {"eng-001": 1}},
        "discipline pull": {"calls": 1, "agents": {"qa-002": 1}},
        "memory search": {"calls": 1, "agents": {"eng-001": 1}},
    }
    assert list(counts)[0] == "task"  # most calls first


def test_commands_since_overrides_the_dispatch_floor(instance):
    from hx.metrics import command_counts

    _commands_fixture(instance)
    assert set(command_counts(instance, since="2026-09-20T12:44:00Z")) == {
        "memory search", "discipline pull", "task"}
    every = command_counts(instance, since="2026-09-20T00:00:00Z")
    assert every["task"] == {"calls": 3, "agents": {"eng-001": 2, "qa-002": 1}}


def test_commands_cli_json_text_and_usage(instance, capsys):
    from hx.metrics import main

    _commands_fixture(instance)
    assert main(["--commands", "--json"], instance) == 0
    assert json.loads(capsys.readouterr().out)["task"]["calls"] == 2
    assert main(["--commands"], instance) == 0
    text = capsys.readouterr().out
    assert text.splitlines()[-1] == "HX-METRICS-COMMANDS commands=5 calls=6"
    assert "eng-001=1 qa-002=1" in text
    for argv in (["eng-001", "--commands"], ["--since", DISPATCHED], ["--commands", "--since", "soon"]):
        with pytest.raises(SystemExit) as raised:
            main(argv, instance)
        assert raised.value.code == 2


def test_commands_with_no_streams_is_empty(instance, capsys):
    from hx.metrics import command_counts, main

    assert command_counts(instance) == {}
    assert main(["--commands"], instance) == 0
    assert capsys.readouterr().out == "HX-METRICS-COMMANDS commands=0 calls=0\n"
