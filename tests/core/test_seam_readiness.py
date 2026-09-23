"""The seam path never holds a turn hostage (spec 09.2).

The `stop`-hook seam once blocked inside `flush()` until the Companion caught
up, and a slow companion turned the turn into a hung "running Stop hook"
indicator — while the hook-timeout killer circled. Now: signal once, take only
when the pane can receive `/clear` and the Companion is caught up, else append
a `seam_waiting` watcher record and leave the marker for the next boundary.
"""

from __future__ import annotations

import pytest


def _behind(instance):
    """One main-stream record with no state: the Companion is behind."""
    from hx import streams

    streams.append_record(
        instance, "partner", "partner-main",
        {"event": "post_tool", "tool": "Bash",
         "input": "ls", "output": "a", "context_tokens": 1000},
    )


def _touch_marker(instance):
    from hx.hook_log import seam_marker

    marker = seam_marker(instance, "partner")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    return marker


def test_stop_defers_while_companion_behind(instance, monkeypatch):
    """Marker stays, a watcher record lands, nothing is pasted, hook returns."""
    from hx import goal as goal_mod
    from hx import hook_stop
    from hx.streams import iter_records, stream_path

    monkeypatch.setattr(goal_mod, "capture_pane", lambda *a, **k: "idle\n❯ ")
    _behind(instance)
    marker = _touch_marker(instance)

    code, _ = hook_stop.handle(
        {"hook_event_name": "Stop", "background_tasks": []}, "partner", instance, env={})
    assert code == 0
    assert marker.is_file(), "not ready: the marker stays for the next boundary"
    records = list(iter_records(stream_path(instance, "partner", "partner-main")))
    assert records[-1]["event"] == "seam_waiting"
    assert records[-1]["ready"] == "behind"
    assert records[-1]["streams"]["partner-main"][0] < records[-1]["streams"]["partner-main"][1]


def test_take_waits_out_an_open_menu(instance, monkeypatch):
    """A `/clear` pasted over a menu awaiting the human would nuke the question."""
    from hx import goal as goal_mod
    from hx.seam import seam

    monkeypatch.setattr(
        goal_mod, "capture_pane", lambda *a, **k: "pick one\nEnter to select · Esc to cancel\n❯ ")
    pasted = []
    monkeypatch.setattr(goal_mod, "paste", lambda *a, **k: pasted.append(a))
    _touch_marker(instance)

    result = seam(instance, "partner", env={})
    assert result["outcome"] == "waiting"
    assert pasted == [], "no paste over an open menu"


def test_take_proceeds_when_ready(instance, monkeypatch):
    """Pane idle and Companion caught up: compose, paste `/clear`, record, unmark."""
    from hx import goal as goal_mod
    from hx.hook_log import seam_marker
    from hx.seam import seam
    from hx.streams import iter_records, stream_path

    monkeypatch.setattr(goal_mod, "capture_pane", lambda *a, **k: "idle\n❯ ")
    pasted = []
    monkeypatch.setattr(goal_mod, "paste", lambda *a, **k: pasted.append(a))
    marker = _touch_marker(instance)

    result = seam(instance, "partner", env={})
    assert result["outcome"] == "taken"
    assert not marker.exists()
    assert pasted and pasted[0][1] == "/clear"
    records = list(iter_records(stream_path(instance, "partner", "partner-main")))
    assert records[-1]["event"] == "seam"


def test_precompact_signals_without_blocking(instance):
    """`precompact` nudges and returns even with the Companion behind."""
    from hx import hook_compact

    _behind(instance)
    code, out = hook_compact.pre(
        {"hook_event_name": "PreCompact", "trigger": "auto"}, "partner", instance, env={})
    assert (code, out) == (0, "")
