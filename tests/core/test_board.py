"""`hx board`: a plain listing of what is on disk (spec 08, CONTRACTS.md).

The v1 cut (spec 14 D25) removed the nine invariants, the `errors` list and `--require-done`.
What is left is the shape the ui lane renders, and the fact that it always exits 0.
"""

from __future__ import annotations

import json
import subprocess

from hx.board import collect, render_text

CONTRACT_ITEM_KEYS = {
    "id", "pod", "role", "state", "file", "outcome", "dispatched", "completed",
    "open_subagents", "goal_ts", "session_alive", "context_tokens", "seams", "turn_ts",
}


def write_tasks(root, tasks):
    (root / "tasks.json").write_text(json.dumps(tasks, indent=2))


def goal_marker(root, item_id, ts="2026-09-20T12:00:03Z"):
    directory = root / "run" / item_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "goal").write_text(ts + "\n")


# --- shape ------------------------------------------------------------------------------


def test_json_shape_matches_contracts(instance, work_item):
    work_item("eng-001", "idle")
    board = collect(instance)
    assert set(board) == {"root_abs", "ts", "items"}
    assert board["root_abs"] == str(instance)
    assert board["ts"].endswith("Z")
    for item in board["items"]:
        assert set(item) == CONTRACT_ITEM_KEYS, set(item) ^ CONTRACT_ITEM_KEYS


def test_the_partner_is_not_an_item(instance, work_item):
    """The Partner has no work item, so it is not on the board (CONTRACTS.md, spec 14 D25)."""
    work_item("eng-001", "idle")
    work_item("partner", "idle")  # even if something wrote one, it is not listed
    assert [i["id"] for i in collect(instance)["items"]] == ["eng-001"]
    assert "partner" not in render_text(collect(instance))


def test_a_config_dir_with_no_work_item_is_listed_with_no_state(instance):
    item = {i["id"]: i for i in collect(instance)["items"]}["eng-001"]
    assert item["state"] is None and item["file"] is None


def test_ids_are_sorted(instance, work_item):
    for item_id in ("eng-002", "eng-001"):
        work_item(item_id, "idle")
    assert [i["id"] for i in collect(instance)["items"]] == ["eng-001", "eng-002"]


def test_role_and_pod_come_from_config(instance, work_item):
    work_item("eng-001", "idle")
    items = {i["id"]: i for i in collect(instance)["items"]}
    assert items["eng-001"]["role"] == "engineer" and items["eng-001"]["pod"] == "engineers"


def test_text_form_is_the_spec_08_columns(instance, work_item):
    work_item("eng-001", "working")
    goal_marker(instance, "eng-001")
    write_tasks(instance, {"eng-001": {"outcome": None,
                                       "dispatched": "2026-09-20T12:00:00Z", "completed": None}})
    line = render_text(collect(instance)).splitlines()[0]
    assert line.split("  ") == [
        "eng-001", "engineers", "working", "-", "2026-09-20T12:00:00Z", "dead",
        "subagents=0", "context=-", "seams=-",
    ]


def test_the_state_is_whatever_the_suffix_says(instance, work_item):
    """No regex gate: hx reads the suffix and reports it (spec 06, spec 14 D25)."""
    path = work_item("eng-001", "working")
    path.rename(path.with_name("eng-001-halfway.md"))
    assert {i["id"]: i for i in collect(instance)["items"]}["eng-001"]["state"] == "halfway"


# --- fields that later milestones fill in -------------------------------------------------


def test_later_milestone_fields_are_null_or_false_without_data(instance, work_item):
    work_item("eng-001", "idle")
    item = {i["id"]: i for i in collect(instance)["items"]}["eng-001"]
    assert item["context_tokens"] is None
    assert item["seams"] is None
    assert item["session_alive"] is False
    assert item["goal_ts"] is None and item["turn_ts"] is None


def test_context_tokens_and_seams_are_computed_when_the_stream_exists(instance, work_item):
    work_item("eng-001", "working")
    goal_marker(instance, "eng-001")
    logs = instance / "logs" / "eng-001"
    logs.mkdir(parents=True)
    records = [
        {"seq": 1, "ts": "2026-09-20T11:00:00Z", "event": "seam"},
        {"seq": 2, "ts": "2026-09-20T12:10:00Z", "event": "post_tool", "context_tokens": 48211},
        {"seq": 3, "ts": "2026-09-20T12:20:00Z", "event": "seam"},
        {"seq": 4, "ts": "2026-09-20T12:30:00Z", "event": "seam"},
    ]
    (logs / "eng-001-main.jsonl").write_text("".join(json.dumps(r) + "\n" for r in records))
    write_tasks(instance, {"eng-001": {"outcome": None,
                                       "dispatched": "2026-09-20T12:00:00Z", "completed": None}})
    item = {i["id"]: i for i in collect(instance)["items"]}["eng-001"]
    assert item["context_tokens"] == 48211
    assert item["seams"] == 2, "seams before `dispatched` belong to the previous dispatch"


def test_open_subagents_are_counted(instance, work_item):
    work_item("eng-001", "working")
    logs = instance / "logs" / "eng-001"
    logs.mkdir(parents=True)
    (logs / "eng-001-s001-open.jsonl").write_text("")
    (logs / "eng-001-s002-open.jsonl").write_text("")
    (logs / "eng-001-s003-closed.jsonl").write_text("")
    item = {i["id"]: i for i in collect(instance)["items"]}["eng-001"]
    assert item["open_subagents"] == 2


# --- it judges nothing ----------------------------------------------------------------------


def test_a_malformed_work_item_is_listed_not_refused(instance, work_item):
    path = work_item("eng-001", "working")
    path.write_text("---\nid: eng-002\npod: engineers\n---\n")
    item = {i["id"]: i for i in collect(instance)["items"]}["eng-001"]
    assert item["state"] == "working"


def test_a_malformed_tasks_json_does_not_raise(instance, work_item):
    work_item("eng-001", "idle")
    (instance / "tasks.json").write_text("{oops")
    assert [i["id"] for i in collect(instance)["items"]] == ["eng-001"]


def test_a_stray_file_under_pods_is_ignored(instance, work_item):
    work_item("eng-001", "idle")
    (instance / "pods" / "engineers" / "notes.txt").write_text("x\n")
    assert [i["id"] for i in collect(instance)["items"]] == ["eng-001"]


def test_exit_0_always(run_hx, instance, work_item):
    work_item("eng-001", "working")
    env = {"HARNESS_ROOT": str(instance)}
    assert run_hx("board", env_extra=env).returncode == 0
    (instance / "pods" / "engineers" / "stray.md").write_text("x\n")
    assert run_hx("board", env_extra=env).returncode == 0
    assert run_hx("board", "--json", env_extra=env).returncode == 0


def test_json_has_no_errors_key(run_hx, instance, work_item):
    work_item("eng-001", "working")
    board = json.loads(run_hx("board", "--json", env_extra={"HARNESS_ROOT": str(instance)}).stdout)
    assert "errors" not in board


def test_board_on_a_missing_root_says_so(run_hx, tmp_path):
    result = run_hx("board", env_extra={"HARNESS_ROOT": str(tmp_path / "nope")})
    assert result.returncode == 2
    assert "does not exist" in result.stderr


def test_session_alive_uses_tmux(instance, work_item, tmux_server):
    work_item("eng-001", "working")
    env = {"HX_TMUX": " ".join(tmux_server)}
    board = collect(instance, env=env)
    assert {i["id"]: i for i in board["items"]}["eng-001"]["session_alive"] is False

    subprocess.run([*tmux_server, "new-session", "-d", "-s", "eng-001", "sleep 60"], check=True)
    board = collect(instance, env=env)
    assert {i["id"]: i for i in board["items"]}["eng-001"]["session_alive"] is True


def test_the_pane_log_is_not_a_stream(instance, work_item):
    """`logs/<id>/<id>-pane.log` is the tmux `pipe-pane` capture (spec 03, 11), not a stream."""
    work_item("eng-001", "working")
    logs = instance / "logs" / "eng-001"
    logs.mkdir(parents=True)
    (logs / "eng-001-pane.log").write_text("ansi noise\n")
    (logs / "eng-001-s001-open.jsonl").write_text("")
    item = {i["id"]: i for i in collect(instance)["items"]}["eng-001"]
    assert item["open_subagents"] == 1
    assert item["context_tokens"] is None and item["seams"] is None
