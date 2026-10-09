"""Quiet protocol: `hx complete` wakes the Partner only for outcomes needing action."""

from __future__ import annotations


def _dispatched(instance, hx, launched, goals):
    launched("partner", "eng-001")
    goals("eng-001")
    assert hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance).returncode == 0


def test_complete_done_does_not_wake_the_partner(instance, hx, launched, goals):
    _dispatched(instance, hx, launched, goals)
    assert hx("complete", "done", harness_id="eng-001").returncode == 0
    log = instance / "run" / "partner" / "fake-input.log"
    pasted = log.read_text() if log.is_file() else ""
    assert "eng-001 done" not in pasted, "a done completion must be silent; the board poll discovers it"


def test_complete_blocked_still_wakes_the_partner(instance, hx, launched, goals, monkeypatch):
    import hx.complete as complete_mod

    _dispatched(instance, hx, launched, goals)
    calls = []
    monkeypatch.setattr(
        complete_mod.wake, "wake_partner_status", lambda root, text: calls.append(text) or "accepted"
    )
    result = complete_mod.complete(instance, "blocked", item_id="eng-001", env=None)
    assert result["outcome"] == "blocked"
    assert result["woke_partner"] == "accepted"
    assert calls == ["eng-001 blocked"]
