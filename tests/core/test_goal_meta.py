"""Meta goal delivery on Muse 1.4: a plain-text pointer, verified as submitted.

Two defects, both found live on Muse 1.4.0:

1. Muse owns `/goal` as a native command and tries to write its own
   `goals.db`, so the hx pointer must arrive as ordinary text for Meta
   workers while staying `/goal ...` for every other flavor.
2. Muse redraws the composer after the first Enter, so hx could report
   `goal=pasted` while the pointer was still sitting unsent in the input
   box. For Meta, `send_goal` re-checks the box after a delay and
   resubmits, and refuses to report `pasted` when the box never clears.
"""

from __future__ import annotations

import json
import time

import pytest

from hx import goal as goal_mod
from hx.errors import HxError


def _set_flavor(instance, item_id: str, flavor: str) -> None:
    harness = instance / "config" / item_id / "harness.json"
    body = json.loads(harness.read_text())
    body["flavor"] = flavor
    harness.write_text(json.dumps(body))


def test_meta_pointer_is_plain_text(instance, work_item):
    work_item("eng-001", "working")
    _set_flavor(instance, "eng-001", "meta")
    text = goal_mod.pointer_text(instance, "eng-001")
    assert not text.startswith("/goal")
    assert "eng-001" in text
    assert "HX-COMPLETE eng-001" in text


@pytest.mark.parametrize("flavor", ["claude", "pi", "grok", "codex"])
def test_other_flavors_keep_the_slash_pointer(instance, work_item, flavor):
    work_item("eng-001", "working")
    _set_flavor(instance, "eng-001", flavor)
    assert goal_mod.pointer_text(instance, "eng-001").startswith("/goal ")


def _boxed(text: str) -> str:
    """A pane whose input box still holds `text`."""
    return "transcript above\n\u276f " + text


def _idle() -> str:
    """A pane whose input box is empty: the pointer left it, i.e. was sent."""
    return "transcript above\n\u276f "


def _quiet(monkeypatch):
    monkeypatch.setattr(time, "sleep", lambda seconds: None)
    monkeypatch.setattr(goal_mod, "wait_for_prompt", lambda *args, **kwargs: None)


def test_meta_send_goal_resubmits_until_the_box_clears(instance, work_item, monkeypatch):
    work_item("eng-001", "working")
    _set_flavor(instance, "eng-001", "meta")
    _quiet(monkeypatch)
    monkeypatch.setattr(goal_mod, "paste", lambda *args, **kwargs: None)
    text = goal_mod.pointer_text(instance, "eng-001")
    # One capture for the pre-paste pane-exists check, then two loop polls that
    # still see the pointer (one resubmit each) and a third that sees it gone.
    panes = iter([_boxed(text), _boxed(text), _boxed(text), _idle()])
    monkeypatch.setattr(goal_mod, "capture_pane", lambda *args, **kwargs: next(panes))
    submits = []
    monkeypatch.setattr(goal_mod, "submit", lambda *args, **kwargs: submits.append(args))

    assert goal_mod.send_goal(instance, "eng-001") == "pasted"
    assert len(submits) == 2
    assert (instance / "run" / "eng-001" / "goal").is_file()


def test_meta_send_goal_refuses_pasted_while_the_box_holds_text(
    instance, work_item, monkeypatch
):
    work_item("eng-001", "working")
    _set_flavor(instance, "eng-001", "meta")
    _quiet(monkeypatch)
    monkeypatch.setattr(goal_mod, "paste", lambda *args, **kwargs: None)
    text = goal_mod.pointer_text(instance, "eng-001")
    monkeypatch.setattr(goal_mod, "capture_pane", lambda *args, **kwargs: _boxed(text))
    monkeypatch.setattr(goal_mod, "submit", lambda *args, **kwargs: False)
    with pytest.raises(HxError, match="remains in the input box"):
        goal_mod.send_goal(instance, "eng-001")
    assert not (instance / "run" / "eng-001" / "goal").exists()


def test_non_meta_send_goal_does_not_recheck_the_box(instance, work_item, monkeypatch):
    work_item("eng-001", "working")
    _set_flavor(instance, "eng-001", "claude")
    monkeypatch.setattr(goal_mod, "wait_for_prompt", lambda *args, **kwargs: None)
    monkeypatch.setattr(goal_mod, "paste", lambda *args, **kwargs: None)
    captures = []
    monkeypatch.setattr(
        goal_mod, "capture_pane", lambda *args, **kwargs: captures.append(args) or _idle()
    )
    assert goal_mod.send_goal(instance, "eng-001") == "pasted"
    # One capture: the pre-paste pane-exists check. No post-paste re-poll.
    assert len(captures) == 1
