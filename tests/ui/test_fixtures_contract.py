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
STATES = {"idle", "working", "complete"}
OUTCOMES = {None, "done", "blocked", "decision", "exhausted"}

#: v1 cut (CONTRACTS.md): no `after`, no `ready`, no `goal_pending`.
BOARD_KEYS = {
    "id", "pod", "role", "state", "file", "outcome", "dispatched", "completed",
    "open_subagents", "goal_ts", "session_alive", "context_tokens", "seams", "turn_ts",
    "companion_pass", "companion_ts",
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
    """v1 cut: no `errors` — `hx board` exits 0 always and polices nothing."""
    assert set(board) == {"root_abs", "ts", "items", "memory"}
    assert board["root_abs"].startswith("/")


def test_the_partner_is_not_a_board_item(board):
    """v1 cut: the Partner has no work item, so it is not on the board."""
    assert "partner" not in {item["id"] for item in board["items"]}


def test_board_items_are_in_id_order(board):
    ids = [item["id"] for item in board["items"]]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids), "one entry per id"


def test_every_board_item_has_every_key_with_null_never_omitted(board):
    for item in board["items"]:
        assert set(item) == BOARD_KEYS, item["id"]
        assert ID_RE.match(item["id"]) and ID_RE.match(item["id"]).group(1) == item["id"]
        assert item["state"] in STATES
        assert item["outcome"] in OUTCOMES
        assert WORK_ITEM_RE.match(item["file"]), item["file"]
        assert item["file"].split("/")[1] == item["pod"]
        assert item["file"].endswith(f"-{item['state']}.md")
        assert isinstance(item["session_alive"], bool)
        assert item["context_tokens"] is None or isinstance(item["context_tokens"], int)


def test_the_board_states_agree_with_their_other_columns(board):
    for item in board["items"]:
        if item["state"] == "idle":
            assert item["outcome"] is None and item["goal_ts"] is None
        if item["state"] == "complete":
            assert item["outcome"] in OUTCOMES - {None}
            assert item["completed"] is not None
        if item["state"] == "working":
            assert item["outcome"] is None
            assert item["goal_ts"] is not None


def test_the_partner_show_is_the_reduced_shape():
    """v1 cut: `hx show partner --json` is only these keys (plus its Companion's activity)."""
    assert set(load("show-partner.json")) == {"id", "partner_md", "pane", "streams", "companion"}


@pytest.mark.parametrize("name", ["show-eng-001.json"])
def test_each_show_fixture_agrees_with_its_board_row(board, name):
    show = load(name)
    row = next(item for item in board["items"] if item["id"] == show["id"])
    for key in ("pod", "role", "state", "file"):
        assert show[key] == row[key], key
    assert show["task"]["outcome"] == row["outcome"]
    assert show["task"]["dispatched"] == row["dispatched"]
    assert show["task"]["completed"] == row["completed"]
    assert len(show["streams"]) >= 1
    open_subagents = sum(
        1 for s in show["streams"] if s["open"] and s["handle"] != f"{show['id']}-main"
    )
    assert open_subagents == row["open_subagents"]


@pytest.mark.parametrize("name", ["show-eng-001.json"])
def test_each_show_fixture_has_the_contract_shape(name):
    show = load(name)
    assert set(show["work_item"]) == {"frontmatter", "body"}
    assert show["work_item"]["frontmatter"]["id"] == show["id"]
    assert set(show["task"]) == {"order", "addenda", "outcome", "dispatched", "completed"}
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


# -- the real record vocabulary (spec 07.1, handoff/build-to-ui.md build-5) ---
#
# `show-eng-001.json`'s `streams` and `subagents` are **not hand-written**: they
# are regenerated by `tests/ui/regen_fixtures.py`, which drives the real
# `python -m hx.hooks` through an M4-shaped run and reads `hx show --json`. These
# tests hold the fixture to the vocabulary those hooks actually write, so it
# cannot drift back into being invented.

RECORD_KEYS = {"seq", "ts", "stream", "event"}
EVENTS = {"boundary", "post_tool", "spawned", "closed", "subagent_result", "open", "close", "seam"}
BOUNDARY_SOURCES = {"startup", "resume", "clear", "compact"}


def streams():
    return load("show-eng-001.json")["streams"]


def main_tail():
    return next(s for s in streams() if s["handle"] == "eng-001-main")["tail"]


def test_every_record_carries_the_four_fields_every_record_has():
    for stream in streams():
        for record in stream["tail"]:
            assert RECORD_KEYS <= set(record), record
            assert record["event"] in EVENTS, record["event"]
            assert record["stream"] == stream["handle"]
            assert isinstance(record["seq"], int)
            assert TS_RE.match(record["ts"])


def test_seqs_are_monotonic_within_a_stream():
    """`seq` is assigned under a per-stream lock (spec 07.1)."""
    for stream in streams():
        seqs = [record["seq"] for record in stream["tail"]]
        assert seqs == sorted(seqs)
        assert len(set(seqs)) == len(seqs)


def test_the_main_stream_opens_with_a_boundary():
    first = main_tail()[0]
    assert first["event"] == "boundary"
    assert first["source"] in BOUNDARY_SOURCES
    assert first["context_file"], "a boundary names the context file it handed over"


def test_a_post_tool_record_has_what_the_view_renders():
    calls = [r for r in main_tail() if r["event"] == "post_tool"]
    assert calls, "the fixture has tool calls"
    for record in calls:
        assert record["tool"]
        assert "input" in record and "output" in record
        assert "context_tokens" in record
        assert set(record["ref"]) >= {"transcript", "tool_use_id"}


def test_every_spawned_subagent_is_in_the_handle_map():
    """`run/<id>/subagents.json` is {agent_id: sNNN} (build-5)."""
    document = load("show-eng-001.json")
    spawned = [r for r in main_tail() if r["event"] == "spawned"]
    assert spawned, "the fixture spawns subagents"
    for record in spawned:
        assert document["subagents"][record["agent_id"]] == record["handle"]


def test_a_closed_subagent_has_its_pair_on_the_main_stream():
    tail = main_tail()
    closed = {r["handle"] for r in tail if r["event"] == "closed"}
    spawned = {r["handle"] for r in tail if r["event"] == "spawned"}
    assert closed <= spawned, "nothing closes that was never spawned"
    for handle in closed:
        results = [r for r in tail if r["event"] == "subagent_result" and r["handle"] == handle]
        assert results, f"{handle} closed without a result returned to the parent"


def test_the_fixture_has_two_closed_subagent_streams_and_one_open():
    """ui-6 item 5 asks for exactly this shape."""
    subagent_streams = [s for s in streams() if not s["handle"].endswith("-main")]
    assert len([s for s in subagent_streams if not s["open"]]) == 2
    assert len([s for s in subagent_streams if s["open"]]) == 1


def test_a_subagent_stream_opens_and_closes_with_its_own_records():
    for stream in streams():
        if stream["handle"].endswith("-main"):
            continue
        events = [record["event"] for record in stream["tail"]]
        assert events[0] == "open"
        assert stream["path"].endswith("-open.jsonl" if stream["open"] else "-closed.jsonl")
        if stream["open"]:
            assert "close" not in events
        else:
            assert events[-1] == "close"


def test_a_closed_stream_carries_a_digest_and_an_open_one_does_not():
    for stream in streams():
        if stream["handle"].endswith("-main"):
            continue
        if stream["open"]:
            assert stream.get("digest") is None
        else:
            assert stream["digest"], "a closed stream has a digest file"


def test_a_subagent_tool_call_names_the_agent_it_came_from():
    """The deliberate fallback: the call lands on a stream with `agent_id` set."""
    for stream in streams():
        if stream["handle"].endswith("-main"):
            continue
        calls = [r for r in stream["tail"] if r["event"] == "post_tool"]
        assert calls
        for record in calls:
            assert record["agent_id"], "the view badges this as `from <agent_id>`"


def test_no_seam_record_yet_because_hx_seam_is_build_7():
    """The counterpart to the seam rendering tests, which declare the shape.

    When build-7 lands and a real run writes one, regenerate the fixture and
    this test comes out.
    """
    assert not [r for r in main_tail() if r["event"] == "seam"]


def test_the_orders_fixture_matches_the_v1_contract(board):
    """v1 cut: one entry per id in `tasks.json`, no graph, no file comparison.

    `hx dispatch` deletes the order file it read, so the text lives only in
    `tasks.json` and the work item — there is nothing left on disk to compare
    against, which is why `path` and `file_matches_record` are gone.
    """
    orders = load("orders.json")
    assert set(orders) == {"root_abs", "ts", "orders"}
    rows = {item["id"]: item for item in board["items"]}
    for entry in orders["orders"]:
        assert set(entry) == {
            "id", "pod", "state", "outcome", "order", "addenda", "dispatched", "completed",
        }
        assert ID_RE.match(entry["id"])
        assert entry["state"] in STATES
        assert entry["outcome"] in OUTCOMES
        assert "## Order" in entry["order"]
        assert "### Checks" in entry["order"]
        for addendum in entry["addenda"]:
            assert set(addendum) == {"ts", "text"}
            assert TS_RE.match(addendum["ts"])
        if entry["id"] in rows:
            assert entry["state"] == rows[entry["id"]]["state"]
            assert entry["outcome"] == rows[entry["id"]]["outcome"]
            assert entry["dispatched"] == rows[entry["id"]]["dispatched"]


def test_an_idle_never_dispatched_id_is_not_an_order(board):
    """`hx orders` reads `tasks.json`, which only has dispatched ids."""
    dispatched = {entry["id"] for entry in load("orders.json")["orders"]}
    for item in board["items"]:
        if item["dispatched"] is None:
            assert item["id"] not in dispatched


def test_the_archive_fixture_reuses_the_contract_entry_shape():
    archive = load("archive.json")
    assert set(archive) == {"root_abs", "ts", "items"}
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


def test_the_metrics_document_is_keyed_on_a_seam_seq(metrics):
    """Spec 16.2 renders the tail marker from the metrics document, by `seq`.

    The fixture's stream tails carry no `seam` record — `hx seam` is build-7 —
    so what is asserted here is that the metrics document is shaped to be looked
    up that way, and that the seq the rendering tests use exists in it.
    """
    seqs = {seam["seq"] for seam in metrics["seams"]}
    assert 412 in seqs, "the seq tests/ui/test_views_js.py declares its seam record with"
    assert all(isinstance(seq, int) for seq in seqs)
