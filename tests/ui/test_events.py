"""Server-sent events: the once-a-second mtime sweep of spec 16.1."""

from __future__ import annotations

import json
import time

import pytest

from hx.ui import server as server_module

BUDGET = 1.0  # "within 1 s of touching a file"


def read_preamble(response):
    """Consume the opening comment frame."""
    assert response.status == 200
    assert response.getheader("Content-Type").startswith("text/event-stream")
    assert response.readline().startswith(b":")
    assert response.readline() == b"\n"


def next_event(response, budget):
    """The next `data:` frame, or None once `budget` seconds have passed."""
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        line = response.readline()
        if not line:
            return None
        if line.startswith(b"data: "):
            return json.loads(line[len(b"data: ") :])
    return None


@pytest.fixture
def stream(scratch_ui):
    connection, response = scratch_ui.client.stream("/api/events", token=scratch_ui.token, timeout=10)
    read_preamble(response)
    try:
        yield response
    finally:
        connection.close()


def touch(path):
    """A write the way an hx command makes one: rewrite, then a newer mtime."""
    path.write_bytes(path.read_bytes())
    stamp = time.time() + 1
    import os

    os.utime(path, (stamp, stamp))


def test_the_sweep_runs_once_a_second_by_default():
    assert server_module.SCAN_INTERVAL == 1.0


def test_a_touched_fixture_is_pushed_within_a_second(scratch_tree, stream):
    touch(scratch_tree / "board.json")
    started = time.monotonic()
    event = next_event(stream, BUDGET)
    elapsed = time.monotonic() - started
    assert event is not None, f"no SSE frame within {BUDGET}s"
    assert elapsed < BUDGET
    assert "board" in event["changed"]


def test_the_changed_scope_is_the_agent_id(scratch_tree, stream):
    touch(scratch_tree / "show-eng-001.json")
    event = next_event(stream, BUDGET)
    assert event is not None
    assert "eng-001" in event["changed"]


def test_nothing_is_pushed_while_nothing_changes(stream):
    assert next_event(stream, 0.5) is None


def test_several_browsers_each_get_the_frame(scratch_tree, scratch_ui, stream):
    connection, second = scratch_ui.client.stream("/api/events", token=scratch_ui.token, timeout=10)
    try:
        read_preamble(second)
        touch(scratch_tree / "goals.json")
        assert "goals" in (next_event(stream, BUDGET) or {}).get("changed", [])
        assert "goals" in (next_event(second, BUDGET) or {}).get("changed", [])
    finally:
        connection.close()


def test_the_sweep_survives_an_unreadable_source(scratch_tree, scratch_ui, stream):
    """A half-written instance must not kill the watcher."""
    (scratch_tree / "board.json").unlink()
    assert next_event(stream, BUDGET) is not None  # the removal itself is a change
    touch(scratch_tree / "archive.json")
    assert "archive" in (next_event(stream, BUDGET) or {}).get("changed", [])
