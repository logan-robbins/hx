"""`hx wake partner`: socket for a Claude Partner, pane paste for any other flavor."""

from __future__ import annotations

import json
import subprocess

from hx.wake import ACCEPTED, NO_SOCKET, REFUSED, wake_partner_status


def _codex_partner(instance):
    harness = instance / "config" / "partner" / "harness.json"
    body = json.loads(harness.read_text())
    body["flavor"] = "codex"
    harness.write_text(json.dumps(body))


def test_claude_partner_without_socket_is_no_socket(instance):
    """The unchanged default: no socket file, no wake."""
    assert wake_partner_status(instance, "hi") == NO_SOCKET


def test_codex_partner_without_pane_is_no_socket(instance, monkeypatch):
    from hx import goal as goal_mod

    _codex_partner(instance)
    monkeypatch.setattr(goal_mod, "capture_pane", lambda *args, **kwargs: None)
    assert wake_partner_status(instance, "hi") == NO_SOCKET


def test_codex_partner_paste_failure_is_no_socket(instance, monkeypatch):
    """The pane vanished between the capture and the paste: nothing to wake."""
    from hx import goal as goal_mod

    _codex_partner(instance)
    monkeypatch.setattr(goal_mod, "capture_pane", lambda *args, **kwargs: "pane")
    def _gone(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "tmux")
    monkeypatch.setattr(goal_mod, "paste", _gone)
    assert wake_partner_status(instance, "hi") == NO_SOCKET


def test_codex_partner_paste_is_accepted(instance, monkeypatch):
    from hx import goal as goal_mod

    _codex_partner(instance)
    seen = {}
    monkeypatch.setattr(goal_mod, "capture_pane", lambda *args, **kwargs: "pane")
    def _paste(name, text, env=None):
        seen["name"], seen["text"] = name, text
    monkeypatch.setattr(goal_mod, "paste", _paste)
    assert wake_partner_status(instance, "eng-001 done") == ACCEPTED
    assert seen == {"name": "partner", "text": "eng-001 done"}


def test_codex_partner_stale_socket_falls_back_to_paste(instance, monkeypatch):
    """A dead session's socket file must not strand the wake: the pane still takes it."""
    from hx import goal as goal_mod

    _codex_partner(instance)
    socket_file = instance / "run" / "partner" / "socket.json"
    socket_file.parent.mkdir(parents=True, exist_ok=True)
    socket_file.write_text(json.dumps({"socket": "/nonexistent/hx-test.sock", "token": "x"}))
    monkeypatch.setattr(goal_mod, "capture_pane", lambda *args, **kwargs: "pane")
    monkeypatch.setattr(goal_mod, "paste", lambda *args, **kwargs: None)
    assert wake_partner_status(instance, "hi") == ACCEPTED


def test_claude_partner_stale_socket_stays_refused(instance, monkeypatch):
    """Claude behavior is unchanged: a stale socket is refused, never pasted."""
    from hx import goal as goal_mod

    socket_file = instance / "run" / "partner" / "socket.json"
    socket_file.parent.mkdir(parents=True, exist_ok=True)
    socket_file.write_text(json.dumps({"socket": "/nonexistent/hx-test.sock", "token": "x"}))
    def _boom(*args, **kwargs):
        raise AssertionError("a Claude wake must never touch the pane")
    monkeypatch.setattr(goal_mod, "capture_pane", _boom)
    monkeypatch.setattr(goal_mod, "paste", _boom)
    assert wake_partner_status(instance, "hi") == REFUSED
