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
from .conftest import _serve, isolated_source, manifest, pack_instance_is_stale
from .test_views_js import render

ITEM = "eng-001"


@pytest.fixture(scope="module")
def m4_root(tmp_path_factory):
    """A whole M4-shaped run: a boundary, three tool calls, three subagents."""
    pytest.importorskip("tests.scenario.packlib", reason="the scenario packs are the gtm lane's")
    from tests.scenario import packlib

    root = tmp_path_factory.mktemp("hx-m4")
    # The gtm lane's `packlib.State` is mid-cut: its width has changed while the
    # packs' own STEPS have not. Skip rather than fail — reported in
    # handoff/ui-to-gtm.md, and the pack is theirs.
    try:
        packlib.build_instance(
            root,
            # v1 cut: `packlib.State` is (state, outcome, has_goal) — no `after`.
            {ITEM: ("working", None, True)},
            worker_pod="engineers",
        )
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        pytest.skip(f"the scenario packlib is still pre-cut: {type(exc).__name__}: {exc}")
    m4.drive(root, ITEM, subagents=3, leave_open=1)

    stale = pack_instance_is_stale(root)
    if stale:
        pytest.skip(stale)

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
    # Streams, subagents and step state render on the agent's Session page (the drawer
    # keeps the work item), so that is the view these tests read.
    return rendered["views"]["sessions"][ITEM]


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


def test_an_id_with_no_stream_reads_null_not_zero(m4_root):
    """The distinction the ui lane asked the build lane to keep (ui-3).

    `partner` is not a board item in v1, so this is asserted with a second
    worker that was never dispatched rather than with the Partner.
    """
    from tests.scenario import packlib

    quiet = m4_root.parent / "quiet"
    packlib.build_instance(quiet, {"eng-009": ("idle", None, False)}, worker_pod="engineers")
    item = next(i for i in isolated_source(quiet).board()["items"] if i["id"] == "eng-009")
    assert item["seams"] is None
    assert item["context_tokens"] is None
    assert item["turn_ts"] is None


def test_the_board_fields_are_rendered(m4_root, tmp_path):
    """ui-8 replaced the board table with the graph, the cards and the drawer.

    The contract's fields did not move away, they moved apart: the agent table
    carries the state and the seams, and the agent's own page carries the
    counts the table has no column for.
    """
    source = isolated_source(m4_root)
    rendered = render({"/api/board": board_override(source)}, tmp_path, open_ids=[ITEM])
    row = next(r for r in rendered["views"]["agentTable"]["rows"] if r["agent"] == ITEM)
    assert row["cells"][6] == "0", "seams"
    assert row["cells"][5] == "none", "session alive"

    agent = rendered["views"]["agents"][ITEM]
    assert "1 open subagent" in agent["text"]
    assert "61,300" in agent["text"], "context tokens"


def board_override(source):
    return source.board()


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


# -- ui-7: the Companion's real writes, through hx's own acceptance path -----

@pytest.fixture(scope="module")
def companion_root(tmp_path_factory):
    """Its own instance, with a step state hx accepted and real digests.

    Deliberately not the shared `m4_root`: that one asserts on teardown that
    nothing wrote to it but `run/ui-token`, and the Companion writes here are
    the test's own setup rather than the UI's.

    `m4.companion_pass` writes the candidate to `run/<id>/companion/<stream>.out.json`
    and calls `hx.companion.ingest` — the function the Companion's own `stop`
    hook uses. Anything the 07.2 schema rejects never lands, so a state that is
    here is one hx wrote.
    """
    from tests.scenario import packlib

    root = tmp_path_factory.mktemp("hx-companion")
    try:
        packlib.build_instance(root, {ITEM: ("working", None, True)}, worker_pod="engineers")
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        pytest.skip(f"the scenario packlib is still pre-cut: {type(exc).__name__}: {exc}")
    m4.drive(root, ITEM, subagents=3, leave_open=1)

    m4.companion_pass(root, ITEM, f"{ITEM}-main", {
        "goal": "add `--require-done` to hx board",
        "constraints": ["stdlib only", "no timeouts anywhere"],
        "decisions": [{"d": "the flag takes ids", "why": "spec 08 shows it inline", "ev": [4]}],
        "open_steps": [
            {"id": "st7", "intent": "refuse an unknown id", "next": "raise before the lookup", "ev": [3, 4]},
        ],
        "closed_steps": [
            {"id": "st6", "outcome": "the flag parses", "verified": True, "commit": "1b90c3d", "ev": [2]},
        ],
        "dead_ends": ["a manual argv scan, before finding argparse already had the group"],
        "blockers": [],
        "subagents_open": [f"{ITEM}-s001"],
        "working_set": {
            "commits": ["1b90c3d board: parse the flag"],
            "dirty": ["src/hx/board.py"],
            "files": [{"path": "spec/08-hx-cli.md", "note": "the exit rule lives here"}],
            "last_failure": "test_unknown_id: expected 1, got 0",
            "hypothesis": "a missing id is treated as not-done rather than refused",
        },
    })
    for handle in (f"{ITEM}-s002", f"{ITEM}-s003"):
        m4.write_digest(root, ITEM, handle, f"{handle}: surveyed the assertions; two found.\n")
    return root


def test_hx_accepted_the_step_state(companion_root):
    state = isolated_source(companion_root).show(ITEM)["step_state"][f"{ITEM}-main"]
    # Stamped by hx after validation, never sent by the Companion (build-6).
    assert "prompt_version" in state and "ts" in state
    assert state["seq"] >= 11, "hx owns the cursor: max(what was sent, the log head)"
    assert state["open_steps"][0]["next"] == "raise before the lookup"


def test_the_agent_view_renders_a_real_step_state(companion_root, tmp_path):
    source = isolated_source(companion_root)
    show = source.show(ITEM)
    rendered = render(
        {"/api/board": source.board(), f"/api/show/{ITEM}": show,
         "/api/show/partner": source.show("partner")},
        tmp_path, open_ids=[ITEM],
    )
    agent = rendered["views"]["sessions"][ITEM]
    assert agent["banner"] is None
    text = agent["text"].replace("`", "")

    state = show["step_state"][f"{ITEM}-main"]
    assert state["goal"].replace("`", "") in text
    assert state["open_steps"][0]["next"] in text
    assert state["closed_steps"][0]["outcome"] in text
    assert state["working_set"]["files"][0]["note"] in text
    assert state["working_set"]["hypothesis"] in text
    assert f"caught up through record {state['seq']}" in text
    assert "tokens (est.)" in text, "the budget bar"


def test_the_real_digests_replace_the_placeholder(companion_root, tmp_path):
    source = isolated_source(companion_root)
    show = source.show(ITEM)
    closed = [s for s in show["streams"] if not s["open"] and not s["handle"].endswith("-main")]
    assert len(closed) == 2
    for stream in closed:
        assert "pending companion" not in stream["digest"]

    rendered = render(
        {"/api/board": source.board(), f"/api/show/{ITEM}": show,
         "/api/show/partner": source.show("partner")},
        tmp_path, open_ids=[ITEM],
    )
    text = rendered["views"]["sessions"][ITEM]["text"]
    for stream in closed:
        assert stream["digest"].strip() in text


def test_the_v1_shapes_are_what_the_real_commands_return(companion_root):
    """The cut, against the real `hx` rather than against the fixtures."""
    source = isolated_source(companion_root)

    board = source.board()
    assert set(board) == {"root_abs", "ts", "items", "memory"}
    assert "partner" not in {item["id"] for item in board["items"]}
    assert set(board["items"][0]) == {
        "id", "pod", "role", "state", "file", "outcome", "dispatched", "completed",
        "open_subagents", "goal_ts", "session_alive", "context_tokens", "seams", "turn_ts", "companion_pass", "companion_ts",
    }

    orders = source.orders()
    assert set(orders) == {"root_abs", "ts", "orders"}

    show = source.show(ITEM)
    assert "after" not in show["task"]
    assert "after" not in show["work_item"]["frontmatter"]

    assert set(source.show("partner")) == {"id", "partner_md", "pane", "streams", "companion", "compactions"}
