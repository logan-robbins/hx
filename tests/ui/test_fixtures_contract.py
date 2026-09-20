"""The fixtures are the contract.

Until the build lane's commands exist these files are the only definition of
what the views render, so they are checked against CONTRACTS.md directly, not
only through the server. `orders.json` and `archive.json` have no contract yet:
their shapes are the ui lane's proposal in handoff/to-orchestrator.md, and what
they reuse from CONTRACTS.md is asserted here.
"""

from __future__ import annotations

import json
import re

import pytest

from hx.ui.data import ID_RE

from .conftest import FIXTURES

# Spec 06: `^(partner|[a-z]+-[0-9]{3})-(idle|queued|working|complete)\.md$`.
WORK_ITEM_RE = re.compile(r"^pods/[a-z]+/(partner|[a-z]+-[0-9]{3})-(idle|queued|working|complete)\.md$")
TS_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
STATES = {"idle", "queued", "working", "complete"}
OUTCOMES = {None, "done", "blocked", "decision", "exhausted"}

BOARD_KEYS = {
    "id", "pod", "role", "state", "file", "after", "ready", "outcome", "dispatched",
    "completed", "open_subagents", "goal_ts", "goal_pending", "session_alive",
    "context_tokens", "seams", "turn_ts",
}


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def board():
    return load("board.json")


def timestamps(value, path="$"):
    """Every value under a `*_ts`, `dispatched`, `completed` or `ts` key."""
    found = []
    if isinstance(value, dict):
        for key, item in value.items():
            if key in ("ts", "dispatched", "completed", "goal_ts", "turn_ts", "seam_ts") and isinstance(item, str):
                found.append((f"{path}.{key}", item))
            found += timestamps(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found += timestamps(item, f"{path}[{index}]")
    return found


@pytest.mark.parametrize(
    "name", ["board.json", "orders.json", "archive.json", "show-partner.json", "show-eng-001.json"]
)
def test_every_required_fixture_is_present_and_is_an_object(name):
    assert isinstance(load(name), dict)


@pytest.mark.parametrize(
    "name", ["board.json", "orders.json", "archive.json", "show-partner.json", "show-eng-001.json"]
)
def test_every_timestamp_is_iso_8601_utc_with_a_z(name):
    bad = [(where, value) for where, value in timestamps(load(name)) if not TS_RE.match(value)]
    assert not bad, bad


def test_the_board_top_level_matches_the_contract(board):
    assert set(board) == {"root_abs", "ts", "items", "errors"}
    assert board["root_abs"].startswith("/")
    assert all(isinstance(error, str) for error in board["errors"])


def test_every_board_item_has_every_key_with_null_never_omitted(board):
    for item in board["items"]:
        assert set(item) == BOARD_KEYS, item["id"]
        assert ID_RE.match(item["id"]) and ID_RE.match(item["id"]).group(1) == item["id"]
        assert item["state"] in STATES
        assert item["outcome"] in OUTCOMES
        assert WORK_ITEM_RE.match(item["file"]), item["file"]
        assert item["file"].split("/")[1] == item["pod"]
        assert item["file"].endswith(f"-{item['state']}.md")
        assert isinstance(item["after"], list)
        assert isinstance(item["ready"], bool)
        assert isinstance(item["goal_pending"], bool)
        assert isinstance(item["session_alive"], bool)
        assert item["context_tokens"] is None or isinstance(item["context_tokens"], int)


def test_partner_is_first_then_ids_in_order(board):
    ids = [item["id"] for item in board["items"]]
    assert ids[0] == "partner"
    assert ids[1:] == sorted(ids[1:])
    assert len(set(ids)) == len(ids), "one entry per id"


def test_ready_is_true_exactly_when_every_after_entry_is_done(board):
    outcomes = {item["id"]: item["outcome"] for item in board["items"]}
    for item in board["items"]:
        expected = all(outcomes.get(dep) == "done" for dep in item["after"])
        assert item["ready"] is expected, item["id"]


def test_the_board_states_obey_the_spec_06_invariants(board):
    for item in board["items"]:
        if item["state"] == "queued":
            assert item["after"], "a queued item has a non-empty after"
            assert not item["ready"], "with at least one entry not done"
            assert item["goal_ts"] is None, "and no goal marker"
        if item["state"] == "complete":
            assert item["outcome"] in OUTCOMES - {None}
            assert item["completed"] is not None
        if item["state"] == "working":
            assert item["outcome"] is None
            assert item["goal_ts"] is not None


def test_the_board_carries_one_invariant_error_naming_a_real_item(board):
    assert len(board["errors"]) == 1
    error = board["errors"][0]
    files = {item["file"] for item in board["items"]}
    assert any(error.startswith(name + ":") for name in files), error
    # The violation spec 06 names: a `working` item with no live session.
    offender = next(item for item in board["items"] if error.startswith(item["file"] + ":"))
    assert offender["state"] == "working" and offender["session_alive"] is False


@pytest.mark.parametrize("name", ["show-partner.json", "show-eng-001.json"])
def test_each_show_fixture_agrees_with_its_board_row(board, name):
    show = load(name)
    row = next(item for item in board["items"] if item["id"] == show["id"])
    for key in ("pod", "role", "state", "file"):
        assert show[key] == row[key], key
    assert show["task"]["after"] == row["after"]
    assert show["task"]["outcome"] == row["outcome"]
    assert show["task"]["dispatched"] == row["dispatched"]
    assert show["task"]["completed"] == row["completed"]
    assert len(show["streams"]) >= 1
    open_subagents = sum(
        1 for s in show["streams"] if s["open"] and s["handle"] != f"{show['id']}-main"
    )
    assert open_subagents == row["open_subagents"]


@pytest.mark.parametrize("name", ["show-partner.json", "show-eng-001.json"])
def test_each_show_fixture_has_the_contract_shape(name):
    show = load(name)
    assert set(show["work_item"]) == {"frontmatter", "body"}
    assert show["work_item"]["frontmatter"]["id"] == show["id"]
    assert set(show["task"]) == {"order", "after", "addenda", "outcome", "dispatched", "completed"}
    assert "## Order" in show["task"]["order"]
    assert "## Definition of done" in show["task"]["order"]
    assert "### Checks" in show["task"]["order"]
    assert "```bash" in show["task"]["order"]
    assert set(show["context_file"]) == {"path", "text", "seam_ts"}
    assert show["context_file"]["path"].startswith(f"run/{show['id']}/")
    assert show["persona_path"] == f"run/{show['id']}/persona.md"
    for handle, state in show["step_state"].items():
        assert handle.startswith(show["id"] + "-")
        assert {"seq", "prompt_version", "open_steps", "closed_steps", "working_set"} <= set(state)
    for stream in show["streams"]:
        assert stream["path"].startswith(f"logs/{show['id']}/")
        assert stream["open"] is not stream["path"].endswith("-closed.jsonl")
        assert len(stream["tail"]) <= 50
    for entry in show["archive"] + show["bench"]:
        assert set(entry) == {"ts", "path", "digest"}
    assert show["pane"]["session"] == show["id"]
    assert len(show["pane"]["lines"]) <= 120


def test_only_the_partner_fixture_carries_partner_md():
    assert isinstance(load("show-partner.json")["partner_md"], str)
    assert "partner_md" not in load("show-eng-001.json")


def test_a_seam_record_appears_in_the_main_stream_tail():
    """Spec 7.4: a seam is a marker in the stream with the context file size."""
    main = next(s for s in load("show-eng-001.json")["streams"] if s["handle"] == "eng-001-main")
    seam = next(record for record in main["tail"] if record["event"] == "seam")
    assert "prompt_version" in seam and "context_file_bytes" in seam


def test_the_orders_fixture_agrees_with_the_board(board):
    orders = load("orders.json")
    rows = {item["id"]: item for item in board["items"]}
    for order in orders["orders"]:
        assert set(order) == {
            "id", "pod", "path", "after", "order", "addenda", "record",
            "state", "ready", "waiting_on", "file_matches_record",
        }
        assert order["path"] == f"orders/{order['id']}.md"
        assert "## Order" in order["order"] and "### Checks" in order["order"]
        if order["record"] is None:
            assert order["state"] is None, "no tasks.json record, so no state"
            continue
        # The record reuses the CONTRACTS.md `hx show` task block verbatim.
        assert set(order["record"]) == {"order", "after", "addenda", "outcome", "dispatched", "completed"}
        assert order["record"]["after"] == order["after"]
        if order["id"] in rows:
            assert order["state"] == rows[order["id"]]["state"]
            assert order["record"]["outcome"] == rows[order["id"]]["outcome"]
            assert order["ready"] == rows[order["id"]]["ready"]


def test_the_orders_graph_says_which_queued_item_waits_on_which_id():
    orders = load("orders.json")
    by_id = {order["id"]: order for order in orders["orders"]}
    outcomes = {i: (o["record"] or {}).get("outcome") for i, o in by_id.items()}
    for order in orders["orders"]:
        assert order["waiting_on"] == [dep for dep in order["after"] if outcomes.get(dep) != "done"]
        assert order["ready"] is (not order["waiting_on"])
    assert {node["id"] for node in orders["graph"]["nodes"]} == set(by_id)
    edges = [(edge["from"], edge["to"], edge["met"]) for edge in orders["graph"]["edges"]]
    assert edges == [
        (dep, order["id"], dep not in order["waiting_on"])
        for order in orders["orders"]
        for dep in order["after"]
    ]
    assert any(not met for _, _, met in edges), "a queued item with an unmet after"


def test_the_archive_fixture_reuses_the_contract_entry_shape():
    archive = load("archive.json")
    assert set(archive) == {"root_abs", "ts", "items", "errors"}
    for item in archive["items"]:
        assert set(item) == {"id", "pod", "bench", "archive"}
        assert ID_RE.match(item["id"])
        for entry in item["bench"]:
            assert set(entry) == {"ts", "path", "digest"}
            assert entry["path"] == f"pods/{item['pod']}/archive/{item['id']}-{entry['ts']}.md"
        for entry in item["archive"]:
            assert set(entry) == {"ts", "path", "digest"}
            assert entry["path"] == f"archive/{item['id']}/{entry['ts']}"


# -- hx metrics --json (CONTRACTS.md) ------------------------------------

SOURCES = {"clear", "compact", "restart", "resume", "startup"}
SEAM_KEYS = {
    "seq", "ts", "source", "prompt_version", "context_tokens_before",
    "context_file_bytes", "working_set_size", "next_10_turns",
}
NEXT_KEYS = {"turns", "tool_calls", "reads_of_context_file", "reads_of_working_set", "other"}
TOTALS_KEYS = {"seams", "tool_calls", "reads_of_context_file", "reads_of_working_set", "other"}


@pytest.fixture(scope="module")
def metrics():
    return load("metrics-eng-001.json")


def test_the_metrics_document_has_the_contract_shape(metrics):
    assert set(metrics) == {"id", "stream", "dispatched", "seams", "totals"}
    assert ID_RE.match(metrics["id"])
    assert metrics["stream"] == f"{metrics['id']}-main", "one entry per seam in the main stream"
    assert TS_RE.match(metrics["dispatched"])
    assert set(metrics["totals"]) == TOTALS_KEYS


def test_every_seam_has_the_contract_shape(metrics):
    for seam in metrics["seams"]:
        assert set(seam) == SEAM_KEYS, seam["seq"]
        assert set(seam["next_10_turns"]) == NEXT_KEYS
        assert seam["source"] in SOURCES
        assert isinstance(seam["seq"], int)
        assert isinstance(seam["context_tokens_before"], int)
        assert isinstance(seam["context_file_bytes"], int)
        assert isinstance(seam["working_set_size"], int)
        assert 0 < seam["next_10_turns"]["turns"] <= 10, "fewer than 10 when the stream ended sooner"


def test_seams_are_in_stream_order(metrics):
    seqs = [seam["seq"] for seam in metrics["seams"]]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs), "one entry per seam record"


def test_each_seams_tool_calls_add_up(metrics):
    for seam in metrics["seams"]:
        next_10 = seam["next_10_turns"]
        assert next_10["tool_calls"] == (
            next_10["reads_of_context_file"] + next_10["reads_of_working_set"] + next_10["other"]
        ), seam["seq"]


def test_the_totals_are_the_sum_of_the_seams(metrics):
    assert metrics["totals"]["seams"] == len(metrics["seams"])
    for key in TOTALS_KEYS - {"seams"}:
        assert metrics["totals"][key] == sum(s["next_10_turns"][key] for s in metrics["seams"]), key


def test_the_fixture_covers_every_case_the_table_marks(metrics):
    """A clean seam, a missed read, a double read, working-set waste, a short window."""
    windows = [seam["next_10_turns"] for seam in metrics["seams"]]
    assert any(w["reads_of_context_file"] == 1 and w["reads_of_working_set"] == 0 for w in windows)
    assert any(w["reads_of_context_file"] == 0 for w in windows)
    assert any(w["reads_of_context_file"] > 1 for w in windows)
    assert any(w["reads_of_working_set"] > 0 for w in windows)
    assert any(w["turns"] < 10 for w in windows)
    assert {seam["source"] for seam in metrics["seams"]} == SOURCES, "every source form"


def test_show_carries_exactly_the_metrics_document(metrics):
    """CONTRACTS.md: "the `metrics` object of `hx show --json` is exactly this document"."""
    assert load("show-eng-001.json")["metrics"] == metrics


def test_the_partner_show_metrics_are_a_valid_document():
    partner = load("show-partner.json")["metrics"]
    assert set(partner) == {"id", "stream", "dispatched", "seams", "totals"}
    assert partner["id"] == "partner"
    assert partner["stream"] == "partner-main"
    for seam in partner["seams"]:
        assert set(seam) == SEAM_KEYS
        assert seam["source"] in SOURCES


def test_a_seam_in_the_stream_tail_has_a_metrics_entry(metrics):
    """Spec 16.2 renders the tail marker from the metrics document, keyed by seq."""
    main = next(s for s in load("show-eng-001.json")["streams"] if s["handle"] == "eng-001-main")
    tail_seams = [record["seq"] for record in main["tail"] if record["event"] == "seam"]
    assert tail_seams, "the fixture has a seam in the tail to render"
    assert set(tail_seams) <= {seam["seq"] for seam in metrics["seams"]}
