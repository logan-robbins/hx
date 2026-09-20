"""`hx board` text form, `--json`, and every invariant of spec 08.

M0 pass criterion: "`hx board` reports each invariant violation". The JSON shape is
CONTRACTS.md, which the ui lane renders from.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from hx.board import collect, render_text, require_done

CONTRACT_ITEM_KEYS = {
    "id", "pod", "role", "state", "file", "after", "ready", "outcome", "dispatched",
    "completed", "open_subagents", "goal_ts", "goal_pending", "session_alive",
    "context_tokens", "seams", "turn_ts",
}


def write_tasks(root, tasks):
    (root / "tasks.json").write_text(json.dumps(tasks, indent=2))


def goal_marker(root, item_id, ts="2026-09-20T12:00:03Z"):
    directory = root / "run" / item_id
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "goal").write_text(ts + "\n")


def errors_matching(board, needle):
    return [e for e in board["errors"] if needle in e]


# --- shape ------------------------------------------------------------------------------


def test_json_shape_matches_contracts(instance, work_item):
    work_item("partner", "idle")
    work_item("eng-001", "idle")
    board = collect(instance)
    assert set(board) == {"root_abs", "ts", "items", "errors"}
    assert board["root_abs"] == str(instance)
    assert board["ts"].endswith("Z")
    for item in board["items"]:
        assert set(item) == CONTRACT_ITEM_KEYS, set(item) ^ CONTRACT_ITEM_KEYS


def test_partner_is_first_then_by_id(instance, work_item):
    for item_id, pod in (("eng-001", "engineers"), ("partner", "partner")):
        work_item(item_id, "idle", pod=pod)
    (instance / "config" / "eng-000").mkdir()
    (instance / "config" / "eng-000" / "harness.json").write_text(
        json.dumps({"id": "eng-000", "pod": "engineers", "role": "engineer",
                    "model": "claude-opus-5", "effort": "high",
                    "workdir": str(instance / "wt" / "eng-000"), "branch": "agent/eng-000"})
    )
    ids = [item["id"] for item in collect(instance)["items"]]
    assert ids == ["partner", "eng-000", "eng-001"]


def test_role_and_pod_come_from_config(instance, work_item):
    work_item("eng-001", "idle")
    work_item("partner", "idle")
    items = {i["id"]: i for i in collect(instance)["items"]}
    assert items["eng-001"]["role"] == "engineer" and items["eng-001"]["pod"] == "engineers"
    assert items["partner"]["role"] == "partner"


def test_text_form_is_five_columns(instance, work_item):
    work_item("partner", "idle")
    work_item("eng-001", "working", after=["eng-000"])
    goal_marker(instance, "eng-001")
    write_tasks(instance, {"eng-001": {"after": ["eng-000"], "outcome": None,
                                       "dispatched": "2026-09-20T12:00:00Z", "completed": None}})
    board = collect(instance)
    line = [l for l in render_text(board).splitlines() if "eng-001-working.md" in l][0]
    assert line.split("  ") == [
        "pods/engineers/eng-001-working.md", "eng-000", "-", "0", "2026-09-20T12:00:03Z",
    ]


# --- fields that later milestones fill in -------------------------------------------------


def test_later_milestone_fields_are_null_or_false_without_data(instance, work_item):
    work_item("eng-001", "idle")
    item = {i["id"]: i for i in collect(instance)["items"]}["eng-001"]
    assert item["context_tokens"] is None
    assert item["seams"] is None
    assert item["session_alive"] is False
    assert item["goal_ts"] is None and item["goal_pending"] is False and item["turn_ts"] is None


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
    write_tasks(instance, {"eng-001": {"after": [], "outcome": None,
                                       "dispatched": "2026-09-20T12:00:00Z", "completed": None}})
    item = {i["id"]: i for i in collect(instance)["items"]}["eng-001"]
    assert item["context_tokens"] == 48211
    assert item["seams"] == 2, "seams before `dispatched` belong to the previous dispatch"


def test_open_subagents_are_counted(instance, work_item):
    work_item("eng-001", "working")
    goal_marker(instance, "eng-001")
    logs = instance / "logs" / "eng-001"
    logs.mkdir(parents=True)
    (logs / "eng-001-s001-open.jsonl").write_text("")
    (logs / "eng-001-s002-open.jsonl").write_text("")
    (logs / "eng-001-s003-closed.jsonl").write_text("")
    item = {i["id"]: i for i in collect(instance)["items"]}["eng-001"]
    assert item["open_subagents"] == 2


def test_ready_follows_tasks_json_outcomes(instance, work_item):
    work_item("eng-002", "queued", after=["eng-001"])
    (instance / "config" / "eng-002").mkdir()
    (instance / "config" / "eng-002" / "harness.json").write_text(
        json.dumps({"id": "eng-002", "pod": "engineers", "role": "engineer",
                    "model": "claude-opus-5", "effort": "high",
                    "workdir": str(instance / "wt" / "eng-002"), "branch": "agent/eng-002"})
    )
    write_tasks(instance, {
        "eng-001": {"after": [], "outcome": "blocked", "dispatched": "t", "completed": None},
        "eng-002": {"after": ["eng-001"], "outcome": None, "dispatched": "t", "completed": None},
    })
    items = {i["id"]: i for i in collect(instance)["items"]}
    assert items["eng-002"]["ready"] is False
    write_tasks(instance, {
        "eng-001": {"after": [], "outcome": "done", "dispatched": "t", "completed": "t"},
        "eng-002": {"after": ["eng-001"], "outcome": None, "dispatched": "t", "completed": None},
    })
    assert {i["id"]: i for i in collect(instance)["items"]}["eng-002"]["ready"] is True


# --- invariants (spec 08 "hx board invariants") ------------------------------------------


def test_one_work_item_per_id(instance, work_item):
    work_item("eng-001", "idle")
    (instance / "pods" / "engineers" / "eng-001-working.md").write_text(
        "---\nid: eng-001\npod: engineers\n---\n"
    )
    board = collect(instance)
    assert errors_matching(board, "2 work items")


def test_filenames_must_match_the_regex(instance, work_item):
    work_item("eng-001", "idle")
    (instance / "pods" / "engineers" / "eng-002-done.md").write_text("stray\n")
    assert errors_matching(collect(instance), "eng-002-done.md")


def test_a_work_item_needs_a_config_dir(instance, work_item):
    work_item("eng-009", "idle")
    assert errors_matching(collect(instance), "no config/eng-009/")


def test_a_config_dir_needs_a_work_item(instance):
    assert errors_matching(collect(instance), "config/eng-001/: no work item")
    assert errors_matching(collect(instance), "config/partner/: no work item")


def test_a_tasks_key_needs_a_config_dir(instance, work_item):
    work_item("eng-001", "idle")
    work_item("partner", "idle")
    write_tasks(instance, {"eng-777": {"after": [], "outcome": None}})
    assert errors_matching(collect(instance), "tasks.json: `eng-777`")


def test_a_working_item_needs_a_live_session_and_a_goal_marker(instance, work_item):
    work_item("eng-001", "working")
    board = collect(instance)
    assert errors_matching(board, "no live tmux session eng-001")
    assert errors_matching(board, "working with no run/eng-001/goal marker")


def test_a_queued_item_needs_an_unmet_after_and_no_goal_marker(instance, work_item):
    work_item("eng-001", "queued")
    assert errors_matching(collect(instance), "queued with an empty `after`")

    work_item("eng-001", "queued", after=["eng-000"])
    write_tasks(instance, {"eng-000": {"after": [], "outcome": "done"},
                           "eng-001": {"after": ["eng-000"], "outcome": None}})
    (instance / "config" / "eng-000").mkdir(exist_ok=True)
    (instance / "config" / "eng-000" / "harness.json").write_text(
        json.dumps({"id": "eng-000", "pod": "engineers", "role": "engineer",
                    "model": "claude-opus-5", "effort": "high",
                    "workdir": str(instance / "wt"), "branch": "agent/eng-000"})
    )
    assert errors_matching(collect(instance), "queued but every `after` entry is done")

    goal_marker(instance, "eng-001")
    assert errors_matching(collect(instance), "queued with a run/eng-001/goal marker")


def test_a_complete_item_may_have_no_open_streams(instance, work_item):
    work_item("eng-001", "complete", outcome="done")
    logs = instance / "logs" / "eng-001"
    logs.mkdir(parents=True)
    (logs / "eng-001-s001-open.jsonl").write_text("")
    assert errors_matching(collect(instance), "complete with 1 open subagent stream")


def test_a_home_needs_its_settings_file(instance, work_item):
    work_item("eng-001", "idle")
    home = instance / "run" / "eng-001" / "home"
    home.mkdir(parents=True)
    board = collect(instance)
    assert errors_matching(board, "run/eng-001/home/: no settings.json")


def test_the_instance_needs_its_token(instance, work_item):
    """One token for the whole instance, so this is a board-level error (spec 11 Auth)."""
    work_item("eng-001", "idle")
    work_item("partner", "idle")
    (instance / "seed" / "token").unlink()
    assert errors_matching(collect(instance), "seed/token: missing")


def test_a_world_readable_token_is_an_error(instance, work_item):
    work_item("eng-001", "idle")
    work_item("partner", "idle")
    (instance / "seed" / "token").chmod(0o644)
    assert errors_matching(collect(instance), "seed/token: readable by group or other")


def test_a_malformed_work_item_is_reported_not_raised(instance, work_item):
    path = work_item("eng-001", "working")
    path.write_text("---\nid: eng-002\npod: engineers\n---\n")
    board = collect(instance)
    assert errors_matching(board, "frontmatter `id` is `eng-002`")


def test_a_malformed_tasks_json_is_reported_not_raised(instance, work_item):
    work_item("eng-001", "idle")
    (instance / "tasks.json").write_text("{oops")
    assert errors_matching(collect(instance), "tasks.json: not valid JSON")


def test_a_clean_instance_has_no_errors(instance, work_item):
    work_item("partner", "idle")
    work_item("eng-001", "idle")
    assert collect(instance)["errors"] == []


# --- exit codes and --require-done --------------------------------------------------------


def run_board(run_hx, root, *args):
    return run_hx("board", *args, env_extra={"HARNESS_ROOT": str(root)})


def test_exit_0_when_clean_and_1_on_any_error(run_hx, instance, work_item):
    work_item("partner", "idle")
    work_item("eng-001", "idle")
    assert run_board(run_hx, instance).returncode == 0

    (instance / "pods" / "engineers" / "stray.md").write_text("x\n")
    failing = run_board(run_hx, instance)
    assert failing.returncode == 1
    assert "stray.md" in failing.stdout


def test_json_exit_code_matches_the_text_form(run_hx, instance, work_item):
    work_item("partner", "idle")
    work_item("eng-001", "working")
    text = run_board(run_hx, instance)
    as_json = run_board(run_hx, instance, "--json")
    assert text.returncode == as_json.returncode == 1
    assert json.loads(as_json.stdout)["errors"]


def test_board_on_a_missing_root_says_so(run_hx, tmp_path):
    result = run_hx("board", env_extra={"HARNESS_ROOT": str(tmp_path / "nope")})
    assert result.returncode == 2
    assert "does not exist" in result.stderr


def test_require_done(instance, work_item):
    work_item("eng-001", "complete", outcome="done")
    write_tasks(instance, {"eng-001": {"after": [], "outcome": "done"}})
    board = collect(instance)
    assert require_done(board, ["eng-001"]) == []
    assert require_done(board, ["eng-404"]) == ["require-done eng-404: no such id"]

    work_item("eng-001", "complete", outcome="blocked")
    write_tasks(instance, {"eng-001": {"after": [], "outcome": "blocked"}})
    assert "outcome is blocked" in require_done(collect(instance), ["eng-001"])[0]

    work_item("eng-001", "working")
    assert "state is working" in require_done(collect(instance), ["eng-001"])[0]


def test_require_done_exit_code_ignores_other_errors(run_hx, instance, work_item):
    """The Partner's own `### Checks` run `hx board --require-done <id>…` (spec 06)."""
    work_item("eng-001", "complete", outcome="done")
    write_tasks(instance, {"eng-001": {"after": [], "outcome": "done"}})
    result = run_board(run_hx, instance, "--require-done", "eng-001")
    assert result.returncode == 0, result.stdout
    assert "require-done eng-001: ok" in result.stdout

    failing = run_board(run_hx, instance, "--require-done", "eng-001", "eng-404")
    assert failing.returncode == 1
    assert "require-done eng-404: no such id" in failing.stdout
    assert "require-done eng-001: ok" in failing.stdout


def test_session_alive_uses_tmux(instance, work_item, tmux_server, monkeypatch):
    work_item("eng-001", "working")
    goal_marker(instance, "eng-001")
    env = {"HX_TMUX": " ".join(tmux_server)}
    board = collect(instance, env=env)
    assert {i["id"]: i for i in board["items"]}["eng-001"]["session_alive"] is False
    assert errors_matching(board, "no live tmux session eng-001")

    subprocess.run([*tmux_server, "new-session", "-d", "-s", "eng-001", "sleep 60"], check=True)
    board = collect(instance, env=env)
    assert {i["id"]: i for i in board["items"]}["eng-001"]["session_alive"] is True
    assert not errors_matching(board, "no live tmux session eng-001")


def test_the_pane_log_is_not_a_stream(instance, work_item):
    """`logs/<id>/<id>-pane.log` is the tmux `pipe-pane` capture (spec 03, 11), not a stream.

    It lands in build-2; the stream regex must ignore it from the start.
    """
    work_item("eng-001", "working")
    goal_marker(instance, "eng-001")
    logs = instance / "logs" / "eng-001"
    logs.mkdir(parents=True)
    (logs / "eng-001-pane.log").write_text("ansi noise\n")
    (logs / "eng-001-s001-open.jsonl").write_text("")
    item = {i["id"]: i for i in collect(instance)["items"]}["eng-001"]
    assert item["open_subagents"] == 1
    assert item["context_tokens"] is None and item["seams"] is None
