"""Current-task recovery using synthetic work items, native SQLite and mocked panes.

No live tmux server, model, worker configuration or application is used.
"""

import json
import sqlite3
from unittest.mock import Mock

import pytest

from hx import board, goal, lifecycle, resume, tasks
from hx.errors import HxError, Refused


ID = "eng-001"
OLD = "2026-10-02T02:00:00Z"
NEW = "2026-10-02T15:00:00Z"
IDLE = "› Ask Codex to do anything\nGPT-6 · xhigh\n"
BUSY = "◦ Working (7s • esc to interrupt)\n" + IDLE


@pytest.fixture
def root(tmp_path, monkeypatch):
    config = tmp_path / "config" / ID
    config.mkdir(parents=True)
    (config / "harness.json").write_text(json.dumps({"flavor": "codex"}))
    pods = tmp_path / "pods" / "eng"
    pods.mkdir(parents=True)
    (pods / f"{ID}-working.md").write_text(
        f"---\nid: {ID}\npod: eng\noutcome:\ndispatched: {OLD}\n---\n"
        "## Goal\nValidate the new candidate.\n"
    )
    task = tasks.new_entry("Validate new candidate", OLD)
    task["addenda"] = [{"ts": NEW, "text": "Resume on new candidate"}]
    tasks.write_tasks(tmp_path, {ID: task})
    monkeypatch.setattr(goal, "capture_pane", Mock(return_value=IDLE))
    monkeypatch.setattr(goal, "_POLL_ATTEMPTS", 3)
    monkeypatch.setattr(goal, "_POLL_INTERVAL_S", 0)
    # Any unintended OS/tmux command fails the test, rather than reaching a fleet.
    monkeypatch.setattr(goal.subprocess, "run", Mock(side_effect=AssertionError("unexpected command")))
    return tmp_path


def native(root, status, *, objective=None, goal_id="prior", updated=1):
    p = root / "run" / ID / "home" / "goals_1.sqlite"
    p.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(p) as c:
        c.execute("CREATE TABLE IF NOT EXISTS thread_goals "
                  "(goal_id TEXT, objective TEXT, status TEXT, updated_at_ms INTEGER)")
        c.execute("DELETE FROM thread_goals")
        c.execute("INSERT INTO thread_goals VALUES (?,?,?,?)", (
            goal_id, objective or legacy(root), status, updated,
        ))
    return p


def legacy(root):
    return goal.POINTER.format(id=ID, path=goal.require_work_item(root, ID).resolve()).removeprefix("/goal ")


def modal(root, objective=None):
    return ("Replace goal?\nNew objective: " + (objective or goal.pointer_text(root, ID).removeprefix("/goal "))
            + "\n\n› 1. Replace current goal  Set the new objective and start it now\n"
            "  2. Cancel                Keep the current goal\n\n  enter select · esc back\n")


def live_background(root):
    p = root / "run" / ID / "turn"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"background_tasks": ["live-test-handle"]}))


def file_snapshot(root):
    return {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def amend_current_task(root, ts=NEW):
    records = tasks.load_tasks(root)
    # Same timestamp deliberately: revision identity must include the actual amendment.
    records[ID]["addenda"].append({"ts": ts, "text": "New current requirement"})
    tasks.write_tasks(root, records)


def test_historical_completion_and_fresh_quotation_do_not_complete_resumed_task(root):
    log = root / "logs" / ID / f"{ID}-main.jsonl"
    log.parent.mkdir(parents=True)
    log.write_text("\n".join(json.dumps(r) for r in [
        {"ts": OLD, "event": "post_tool", "output": f"HX-COMPLETE {ID} done"},
        {"ts": NEW, "event": "post_tool", "input": f"Already saw HX-COMPLETE {ID} done"},
    ]))
    native(root, "complete")
    assert lifecycle.goal_was_lost(root, ID)


def test_only_current_control_plane_completion_suppresses_recovery(root):
    records = tasks.load_tasks(root)
    records[ID].update(outcome="done", completed=OLD)
    tasks.write_tasks(root, records)
    assert lifecycle.goal_was_lost(root, ID), "old receipt precedes resume"
    records[ID]["completed"] = NEW
    tasks.write_tasks(root, records)
    assert not lifecycle.goal_was_lost(root, ID)


def test_fresh_dispatch_changes_pointer_even_at_same_timestamp(root):
    first = goal.pointer_text(root, ID)
    records = tasks.load_tasks(root)
    records[ID]["goal"] = "Different candidate at the same timestamp"
    tasks.write_tasks(root, records)
    second = goal.pointer_text(root, ID)
    assert first != second
    assert goal.pointer_text(root, ID) == second
    assert "earlier completion is not completion of this task" in second


@pytest.mark.parametrize("status", ["active", "blocked", "paused", "usage_limited", "budget_limited"])
def test_native_live_or_suspended_goals_are_not_replaced_by_heartbeat(root, status):
    native(root, status)
    assert not lifecycle.goal_was_lost(root, ID)


def test_busy_pane_and_background_handles_are_preserved(root, monkeypatch):
    goal.capture_pane.return_value = BUSY
    assert not lifecycle.goal_was_lost(root, ID)
    goal.capture_pane.return_value = IDLE
    p = root / "run" / ID / "turn"
    p.parent.mkdir(parents=True)
    p.write_text(json.dumps({"background_tasks": ["live-test-handle"]}))
    assert not lifecycle.goal_was_lost(root, ID)


def test_unreadable_native_status_does_not_authorize_recovery(root):
    p = native(root, "active")
    p.write_text("broken store")
    assert not lifecycle.goal_was_lost(root, ID)


def test_modal_is_input_blocked_not_idle_or_a_lost_goal(root):
    goal.capture_pane.return_value = modal(root)
    assert board.pane_awaits_input(modal(root).splitlines())
    assert not goal.pane_is_idle(modal(root))
    assert not lifecycle.goal_was_lost(root, ID)
    assert not board.pane_awaits_input((modal(root) + BUSY).splitlines()), "historical modal is not live"


def test_submit_never_blindly_confirms_a_modal(root):
    goal.capture_pane.return_value = modal(root)
    assert not goal.submit(ID, goal.pointer_text(root, ID))
    goal.subprocess.run.assert_not_called()


def test_codex_input_box_excludes_old_transcript_pointer(root):
    text = goal.pointer_text(root, ID)
    assert text not in goal.input_box(text + "\n" + IDLE)
    assert text in goal.input_box("› " + text)


@pytest.mark.parametrize("status,objective", [
    ("active", None), ("active", "Unrelated live mission"),
    ("blocked", "Unrelated blocked mission"), ("paused", None),
])
def test_explicit_resume_preserves_active_or_unrelated_native_goals(root, status, objective):
    native(root, status, objective=objective)
    with pytest.raises(Refused, match="preserving existing native goal"):
        goal.send_goal(root, ID, resume_blocked=True, env={})
    goal.subprocess.run.assert_not_called()
    assert not goal.marker(root, ID).exists()


def test_non_explicit_delivery_does_not_accept_blocked_replacement(root):
    native(root, "blocked")
    goal.capture_pane.return_value = modal(root)
    with pytest.raises(Refused):
        goal.send_goal(root, ID)
    goal.subprocess.run.assert_not_called()


@pytest.mark.parametrize("existing_modal", [False, True])
def test_explicit_blocked_resume_confirms_same_goal_once_and_requires_native_ack(root, monkeypatch, existing_modal):
    native(root, "blocked")
    goal.capture_pane.return_value = modal(root) if existing_modal else IDLE
    sent = []

    def paste(*args):
        goal.capture_pane.return_value = modal(root)
        return False  # generic submission correctly refuses the replacement dialog

    def confirm(command, **kwargs):
        sent.append(command)
        native(root, "active", objective=goal.pointer_text(root, ID).removeprefix("/goal "), goal_id="new")
        goal.capture_pane.return_value = BUSY

    monkeypatch.setattr(goal, "paste", Mock(side_effect=paste))
    monkeypatch.setattr(goal.subprocess, "run", confirm)
    assert goal.send_goal(root, ID, resume_blocked=True, env={}) == "pasted"
    assert len(sent) == 1 and sent[0][-1] == "Enter"
    assert goal.marker(root, ID).exists()
    assert goal.paste.call_count == (0 if existing_modal else 1)


def test_no_native_ack_returns_failure_without_success_marker_or_restart(root, monkeypatch):
    monkeypatch.setattr(goal, "paste", Mock(return_value=True))
    with pytest.raises(HxError, match="acknowledgment not observed.*no restart"):
        goal.send_goal(root, ID)
    goal.subprocess.run.assert_not_called()
    assert not goal.marker(root, ID).exists()


def test_modal_with_other_objective_is_never_confirmed(root):
    native(root, "blocked")
    goal.capture_pane.return_value = modal(root, "Unrelated requested objective")
    with pytest.raises(Refused, match="not safely attributable"):
        goal.send_goal(root, ID, resume_blocked=True, env={})
    goal.subprocess.run.assert_not_called()


def test_cancel_selected_modal_is_blocked_and_never_confirmed(root):
    native(root, "blocked")
    pane = modal(root).replace("› 1.", "  1.").replace("  2.", "› 2.")
    goal.capture_pane.return_value = pane
    assert board.pane_awaits_input(pane.splitlines())
    assert not goal.pane_is_idle(pane)
    with pytest.raises(Refused, match="not safely attributable"):
        goal.send_goal(root, ID, resume_blocked=True, env={})
    goal.subprocess.run.assert_not_called()


def test_modal_disappearing_without_new_native_goal_is_not_ack(root, monkeypatch):
    native(root, "blocked")
    goal.capture_pane.return_value = modal(root)
    def confirm(*args, **kwargs):
        goal.capture_pane.return_value = IDLE
    monkeypatch.setattr(goal.subprocess, "run", Mock(side_effect=confirm))
    with pytest.raises(HxError, match="acknowledgment not observed"):
        goal.send_goal(root, ID, resume_blocked=True, env={})
    assert goal.subprocess.run.call_count == 1
    assert not goal.marker(root, ID).exists()


def test_modal_redraw_after_confirmation_does_not_press_enter_twice(root, monkeypatch):
    native(root, "blocked")
    goal.capture_pane.return_value = modal(root)
    monkeypatch.setattr(goal.subprocess, "run", Mock())
    with pytest.raises(HxError, match="acknowledgment not observed"):
        goal.send_goal(root, ID, resume_blocked=True, env={})
    assert goal.subprocess.run.call_count == 1
    assert not goal.marker(root, ID).exists()


def test_historical_completed_native_rows_do_not_prevent_explicit_blocked_resume(root, monkeypatch):
    p = native(root, "blocked")
    with sqlite3.connect(p) as connection:
        connection.execute("INSERT INTO thread_goals VALUES (?,?,?,?)",
                           ("historical", "Old completed mission", "complete", 0))
    goal.capture_pane.return_value = modal(root)

    def confirm(*args, **kwargs):
        native(root, "active", objective=goal.pointer_text(root, ID).removeprefix("/goal "), goal_id="new")
        goal.capture_pane.return_value = BUSY

    monkeypatch.setattr(goal.subprocess, "run", Mock(side_effect=confirm))
    assert goal.send_goal(root, ID, resume_blocked=True, env={}) == "pasted"
    assert goal.subprocess.run.call_count == 1


def test_failed_transport_does_not_write_delivery_marker(root, monkeypatch):
    (root / "config" / ID / "harness.json").write_text('{"flavor":"claude"}')
    monkeypatch.setattr(goal, "paste", Mock(return_value=False))
    with pytest.raises(HxError, match="not submitted"):
        goal.send_goal(root, ID)
    assert not goal.marker(root, ID).exists()


def test_resume_refuses_active_native_goal_before_mutating_task(root):
    native(root, "active")
    item = goal.require_work_item(root, ID)
    item.write_text(item.read_text().replace("outcome:\n", "outcome: blocked\n"))
    item = item.rename(item.with_name(f"{ID}-complete.md"))
    addendum = root / "resume.md"
    addendum.write_text("Continue with the new dependency.")
    before = (item.read_bytes(), (root / "tasks.json").read_bytes())
    with pytest.raises(Refused, match="preserving existing native goal"):
        resume.resume(root, ID, addendum, env={})
    assert (item.read_bytes(), (root / "tasks.json").read_bytes()) == before
    assert addendum.exists()


def test_blocked_resume_preflight_matches_working_path_before_rename(root):
    native(root, "blocked")
    item = goal.require_work_item(root, ID)
    item.rename(item.with_name(f"{ID}-complete.md"))
    assert goal.check_native_delivery(root, ID, resume_blocked=True)["status"] == "blocked"


@pytest.mark.parametrize("now", [False, True])
def test_explicit_blocked_delivery_with_live_background_refuses_without_writes(root, monkeypatch, now):
    native(root, "blocked")
    live_background(root)
    goal.capture_pane.return_value = modal(root)

    def confirm(*args, **kwargs):
        native(root, "active", objective=goal.pointer_text(root, ID).removeprefix("/goal "), goal_id="new")
        goal.capture_pane.return_value = BUSY

    monkeypatch.setattr(goal.subprocess, "run", Mock(side_effect=confirm))
    before = file_snapshot(root)
    with pytest.raises(Refused, match="preserving live background tasks"):
        goal.send_goal(root, ID, resume_blocked=True, now=now, env={"HARNESS_ID": "partner"})
    goal.subprocess.run.assert_not_called()
    assert file_snapshot(root) == before


@pytest.mark.parametrize("stage", ["paste", "modal-read"])
def test_background_arriving_during_delivery_prevents_modal_confirmation(root, monkeypatch, stage):
    p = native(root, "blocked")
    before = p.read_bytes()
    original_native = goal.native_goals

    def read_native(*args):
        rows = original_native(*args)
        if stage == "modal-read" and goal.replacement_prompt(goal.capture_pane.return_value):
            live_background(root)
        return rows

    def paste(*args):
        goal.capture_pane.return_value = modal(root)
        if stage == "paste":
            live_background(root)
        return False

    monkeypatch.setattr(goal, "native_goals", read_native)
    monkeypatch.setattr(goal, "paste", Mock(side_effect=paste))
    with pytest.raises(Refused, match="preserving live background tasks"):
        goal.send_goal(root, ID, resume_blocked=True, env={})
    goal.subprocess.run.assert_not_called()
    assert p.read_bytes() == before
    assert not goal.marker(root, ID).exists()


def test_resume_with_live_background_refuses_before_any_state_mutation(root):
    native(root, "blocked")
    live_background(root)
    item = goal.require_work_item(root, ID)
    item.write_text(item.read_text().replace("outcome:\n", "outcome: blocked\n"))
    item.rename(item.with_name(f"{ID}-complete.md"))
    addendum = root / "resume.md"
    addendum.write_text("Resume the blocked task.")
    before = file_snapshot(root)
    with pytest.raises(Refused, match="preserving live background tasks"):
        resume.resume(root, ID, addendum, env={})
    goal.subprocess.run.assert_not_called()
    assert file_snapshot(root) == before


@pytest.mark.parametrize("stage", ["paste", "ack-read"])
def test_old_revision_ack_after_amendment_does_not_mark_success(root, monkeypatch, stage):
    original_native = goal.native_goals

    def read_native(*args):
        rows = original_native(*args)
        if stage == "ack-read" and rows:
            amend_current_task(root)
        return rows

    def paste(item_id, text, env):
        native(root, "active", objective=text.removeprefix("/goal "), goal_id="new")
        goal.capture_pane.return_value = BUSY
        if stage == "paste":
            amend_current_task(root, ts="2026-10-02T15:01:00Z")
        return True

    monkeypatch.setattr(goal, "native_goals", read_native)
    monkeypatch.setattr(goal, "paste", Mock(side_effect=paste))
    with pytest.raises(Refused, match="task revision changed.*no restart"):
        goal.send_goal(root, ID)
    goal.subprocess.run.assert_not_called()
    assert not goal.marker(root, ID).exists()
    assert original_native(root, ID)[0]["status"] == "active", "leave the observed session intact"


@pytest.mark.parametrize("stage", ["paste", "modal-read"])
def test_revision_drift_before_modal_confirmation_never_sends_enter(root, monkeypatch, stage):
    p = native(root, "blocked")
    before = p.read_bytes()
    original_native = goal.native_goals

    def read_native(*args):
        rows = original_native(*args)
        if stage == "modal-read" and goal.replacement_prompt(goal.capture_pane.return_value):
            amend_current_task(root)
        return rows

    def paste(*args):
        goal.capture_pane.return_value = modal(root)
        if stage == "paste":
            amend_current_task(root)
        return False

    monkeypatch.setattr(goal, "native_goals", read_native)
    monkeypatch.setattr(goal, "paste", Mock(side_effect=paste))
    with pytest.raises(Refused, match="task revision changed.*no restart"):
        goal.send_goal(root, ID, resume_blocked=True, env={})
    goal.subprocess.run.assert_not_called()
    assert p.read_bytes() == before
    assert not goal.marker(root, ID).exists()


def test_revision_drift_after_modal_confirmation_does_not_mark_success(root, monkeypatch):
    native(root, "blocked")
    old_objective = goal.pointer_text(root, ID).removeprefix("/goal ")
    goal.capture_pane.return_value = modal(root)

    def confirm(*args, **kwargs):
        native(root, "active", objective=old_objective, goal_id="new")
        goal.capture_pane.return_value = BUSY
        amend_current_task(root)

    monkeypatch.setattr(goal.subprocess, "run", Mock(side_effect=confirm))
    with pytest.raises(Refused, match="task revision changed.*no restart"):
        goal.send_goal(root, ID, resume_blocked=True, env={})
    assert goal.subprocess.run.call_count == 1
    assert not goal.marker(root, ID).exists()


@pytest.mark.parametrize("existing_modal", [False, True])
def test_revision_change_after_ack_is_rechecked_before_marker(root, monkeypatch, existing_modal):
    if existing_modal:
        native(root, "blocked")
        goal.capture_pane.return_value = modal(root)
    monkeypatch.setattr(goal, "paste", Mock(return_value=True))
    # The observer has just returned an ACK when another actor amends the task.
    monkeypatch.setattr(goal, "_confirm_codex_delivery", lambda *args: amend_current_task(root))
    with pytest.raises(Refused, match="task revision changed.*no restart"):
        goal.send_goal(root, ID, resume_blocked=existing_modal, env={})
    goal.subprocess.run.assert_not_called()
    assert not goal.marker(root, ID).exists()
