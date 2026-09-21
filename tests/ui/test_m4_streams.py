"""ui-6: the stream, subagent and board rendering, against records the hooks wrote.

Every instance here is built by `tests/ui/m4.py`, which drives the real
`python -m hx.hooks` — the same entry point `install.sh` bakes into an agent's
settings. No record in this file is hand-written, and the view is asserted
against what `hx show --json` and `hx board --json` actually return for it.

The instance is read through a private tmux server, as everywhere else in this
suite: `hx board` matches a live session by the bare id, and another lane runs
real sessions on this machine.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from . import m4
from .conftest import _serve, isolated_source, manifest
from .test_views_js import render

ITEM = "eng-001"


@pytest.fixture(scope="module")
def m4_root(tmp_path_factory):
    """A whole M4-shaped run: a boundary, three tool calls, three subagents."""
    pytest.importorskip("tests.scenario.packlib", reason="the scenario packs are the gtm lane's")
    from tests.scenario import packlib

    root = tmp_path_factory.mktemp("hx-m4")
    packlib.build_instance(
        root,
        {"partner": ("working", None, [], True), ITEM: ("working", None, [], True)},
        worker_pod="engineers",
    )
    m4.drive(root, ITEM, subagents=3, leave_open=1)

    before = manifest(root)
    yield root
    after = manifest(root)
    changed = sorted(
        name for name in set(before) | set(after)
        if before.get(name) != after.get(name) and name != "run/ui-token"
    )
    assert not changed, f"the UI wrote to the instance: {changed}"


@pytest.fixture(scope="module")
def m4_show(m4_root):
    return isolated_source(m4_root).show(ITEM)


@pytest.fixture(scope="module")
def m4_board(m4_root):
    return isolated_source(m4_root).board()


@pytest.fixture(scope="module")
def m4_agent(m4_root, tmp_path_factory):
    source = isolated_source(m4_root)
    overrides = {"/api/board": source.board()}
    for item_id in (item["id"] for item in source.board()["items"]):
        overrides[f"/api/show/{item_id}"] = source.show(item_id)
    rendered = render(overrides, tmp_path_factory.mktemp("render"), open_ids=[ITEM])
    assert rendered["banner"] is None
    return rendered["views"]["agents"][ITEM]


# -- the records the hooks really wrote ----------------------------------

def test_the_hooks_wrote_the_streams(m4_root):
    names = sorted(p.name for p in (m4_root / "logs" / ITEM).glob("*.jsonl"))
    assert names == [
        f"{ITEM}-main.jsonl",
        f"{ITEM}-s001-open.jsonl",
        f"{ITEM}-s002-closed.jsonl",
        f"{ITEM}-s003-closed.jsonl",
    ]
    handles = json.loads((m4_root / "run" / ITEM / "subagents.json").read_text())
    assert sorted(handles.values()) == ["s001", "s002", "s003"]


def test_the_main_stream_has_the_whole_vocabulary(m4_show):
    main = next(s for s in m4_show["streams"] if s["handle"] == f"{ITEM}-main")
    assert [r["event"] for r in main["tail"]] == [
        "boundary", "post_tool", "post_tool", "post_tool",
        "spawned", "spawned", "closed", "subagent_result",
        "spawned", "closed", "subagent_result",
    ]


# -- item 1: the record shape in the tail --------------------------------

def test_every_event_type_renders_as_its_own_pill(m4_agent):
    labels = [pill["text"] for pill in m4_agent["pills"]]
    for event in ("boundary", "post_tool", "spawned", "closed", "subagent_result", "open", "close"):
        assert event in labels, event


def test_a_boundary_renders_as_a_marker_naming_its_source(m4_agent, m4_show):
    main = next(s for s in m4_show["streams"] if s["handle"] == f"{ITEM}-main")
    boundary = main["tail"][0]
    assert "session start · " + boundary["source"] in m4_agent["text"]
    assert Path(boundary["context_file"]).name in m4_agent["text"], "and the file it handed over"


def test_a_tool_call_shows_its_tool_excerpts_and_ref(m4_agent, m4_show):
    main = next(s for s in m4_show["streams"] if s["handle"] == f"{ITEM}-main")
    calls = [r for r in main["tail"] if r["event"] == "post_tool"]
    code = [c["text"] for c in m4_agent["code"]]
    excerpts = [block["text"] for block in m4_agent["pre"] if "excerpt" in block["class"]]
    for record in calls:
        assert record["tool"] in code
        assert record["input"] in excerpts
        assert record["output"] in excerpts
        assert record["ref"]["tool_use_id"] in code, "the pointer to the full payload"


def test_a_failing_tool_call_shows_its_exit_code(m4_agent):
    labels = [pill["text"] for pill in m4_agent["pills"]]
    assert "exit 0" in labels and "exit 1" in labels


def test_a_subagents_tool_call_says_whose_it_was(m4_agent, m4_show):
    """build-5's deliberate fallback: the call lands on a stream with agent_id."""
    badges = [pill["text"] for pill in m4_agent["pills"] if pill["text"].startswith("from ")]
    assert badges, "a subagent's call is badged, not shown as the agent's own"
    for agent_id in m4_show["subagents"]:
        assert f"from {agent_id}" in badges


def test_the_spawn_and_close_pair_name_their_handle(m4_agent, m4_show):
    text = m4_agent["text"]
    main = next(s for s in m4_show["streams"] if s["handle"] == f"{ITEM}-main")
    for record in main["tail"]:
        if record["event"] in ("spawned", "closed", "subagent_result"):
            assert record["handle"] in [c["text"] for c in m4_agent["code"]]
    assert "stream closed" in text
    assert "result returned to the parent" in text


def test_the_last_turn_is_in_the_agent_header(m4_agent, m4_board, m4_root):
    """item 1: the `turn` marker, via the board's `turn_ts` — no file is read."""
    marker = json.loads((m4_root / "run" / ITEM / "turn").read_text())
    row = next(item for item in m4_board["items"] if item["id"] == ITEM)
    assert row["turn_ts"] == marker["ts"], "the board reports the stop hook's marker"
    assert "last turn " + marker["ts"][11:19] + "Z" in m4_agent["text"]


# -- item 2: the subagent handles ----------------------------------------

def test_the_subagents_table_is_the_handle_map(m4_agent, m4_show):
    rows = [row for row in m4_agent["rows"] if len(row["cells"]) == 4]
    assert len(rows) == len(m4_show["subagents"])
    cells = {row["cells"][0]: row["cells"] for row in rows}
    for agent_id, handle in m4_show["subagents"].items():
        assert handle in cells
        assert cells[handle][1] == agent_id


def test_each_handle_shows_its_stream_state_and_digest(m4_agent, m4_show):
    rows = {row["cells"][0]: row["cells"] for row in m4_agent["rows"] if len(row["cells"]) == 4}
    for stream in m4_show["streams"]:
        if stream["handle"].endswith("-main"):
            continue
        handle = stream["handle"].rsplit("-", 1)[1]
        state, digest = rows[handle][2], rows[handle][3]
        assert ("open" if stream["open"] else "closed") in state
        assert str(stream["records"]) in state
        if stream["open"]:
            assert digest == "not yet", "an open stream has no digest yet"
        else:
            assert "pending companion" in digest, "build-5's placeholder until build-6"


def test_a_closed_streams_digest_is_shown_on_its_card_too(m4_agent, m4_show):
    closed = [s for s in m4_show["streams"] if not s["open"] and not s["handle"].endswith("-main")]
    assert len(closed) == 2, "item 5 asks for two closed subagent streams"
    assert m4_agent["text"].count("pending companion") >= len(closed)


# -- item 3: the board columns from real data ----------------------------

def test_open_subagents_counts_the_open_streams(m4_board, m4_show):
    row = next(item for item in m4_board["items"] if item["id"] == ITEM)
    open_streams = [
        s for s in m4_show["streams"] if s["open"] and not s["handle"].endswith("-main")
    ]
    assert row["open_subagents"] == len(open_streams) == 1


def test_context_tokens_is_the_last_records_value(m4_board, m4_show):
    """build-5: input plus cache reads, from the latest `post_tool`."""
    row = next(item for item in m4_board["items"] if item["id"] == ITEM)
    main = next(s for s in m4_show["streams"] if s["handle"] == f"{ITEM}-main")
    with_tokens = [r for r in main["tail"] if r.get("context_tokens") is not None]
    assert row["context_tokens"] == with_tokens[-1]["context_tokens"]
    assert row["context_tokens"] == 12_400 + 48_900, "input + cache reads, not output"


def test_seams_is_zero_until_hx_seam_lands(m4_board):
    """A stream that exists with no seam records in it reads `0`, not `null`."""
    row = next(item for item in m4_board["items"] if item["id"] == ITEM)
    assert row["seams"] == 0


def test_an_id_with_no_stream_reads_null_not_zero(m4_board):
    """The distinction the ui lane asked the build lane to keep (ui-3)."""
    partner = next(item for item in m4_board["items"] if item["id"] == "partner")
    assert partner["seams"] is None
    assert partner["context_tokens"] is None
    assert partner["turn_ts"] is None


def test_the_board_renders_those_columns(m4_root, tmp_path):
    source = isolated_source(m4_root)
    board = source.board()
    rendered = render({"/api/board": board}, tmp_path)["views"]["board"]
    row = next(r for r in rendered["rows"] if r["cells"][0].startswith(ITEM))
    assert row["cells"][5] == "1", "open subagents"
    assert row["cells"][8] == "61,300", "context tokens"
    assert row["cells"][9] == "0", "seams"
    partner = next(r for r in rendered["rows"] if r["cells"][0].startswith("partner"))
    assert partner["cells"][8] == "—" and partner["cells"][9] == "—"


# -- served --------------------------------------------------------------

def test_the_server_serves_this_instance(m4_root):
    server = _serve(isolated_source(m4_root))
    handle = next(server)
    try:
        status, show = handle.client.json(f"/api/show/{ITEM}")
        assert status == 200
        assert len(show["streams"]) == 4
        assert len(show["subagents"]) == 3
    finally:
        server.close()
