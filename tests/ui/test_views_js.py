"""The views themselves (spec 16.2), rendered headlessly.

`tests/ui/js/render.js` runs `src/hx/ui/static/app.js` against a minimal DOM
shim and prints what each view produced. This is the only check that the page
renders exactly the fields CONTRACTS.md defines; it skips where node is absent,
so it never blocks the suite on a machine without a JS runtime.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from .conftest import FIXTURES

REPO = Path(__file__).resolve().parents[2]
RENDER = Path(__file__).parent / "js" / "render.js"
STATIC = REPO / "src" / "hx" / "ui" / "static"

node = pytest.mark.skipif(shutil.which("node") is None, reason="no node on PATH")


def render(overrides=None, tmp_path=None, open_ids=None):
    """Run the real `static/app.js` under node and return what each view produced.

    `open_ids` are the agents to open in turn; each lands in `views.agents[id]`.
    """
    if shutil.which("node") is None:
        pytest.skip("no node on PATH")
    argv = ["node", str(RENDER), str(FIXTURES), str(STATIC)]
    if overrides is not None:
        path = tmp_path / "overrides.json"
        path.write_text(json.dumps(overrides), encoding="utf-8")
        argv.append(str(path))
    elif open_ids:
        argv.append("")
    if open_ids:
        argv.append(",".join(open_ids))
    result = subprocess.run(argv, capture_output=True, text=True, check=False, cwd=REPO)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.fixture(scope="module")
def rendered():
    return render()


@node
@pytest.mark.parametrize("name", ["app.js", "domshim.js", "render.js"])
def test_the_javascript_parses(name):
    target = STATIC / name if name == "app.js" else RENDER.parent / name
    assert subprocess.run(["node", "--check", str(target)], capture_output=True).returncode == 0


@node
def test_the_page_pulls_nothing_from_a_cdn():
    """Spec 16.1: static vanilla JS and CSS from the package, no CDN, no build step."""
    for name in ("index.html", "app.js", "style.css"):
        text = (STATIC / name).read_text(encoding="utf-8")
        for marker in ("http://", "https://", "cdn.", "unpkg", "jsdelivr", "googleapis"):
            assert marker not in text, f"{name} reaches outside the package: {marker}"


def test_every_view_renders_without_an_error_banner(rendered):
    assert rendered["banner"] is None
    assert set(rendered["views"]) == {
        "board", "orders", "archive", "agentPicker", "agent", "agents", "partner",
    }
    assert rendered["navigation"] == ["board", "orders", "archive", "agent", "partner"]


def test_the_board_opens_first_and_sse_is_opened_at_load(rendered):
    urls = [request["url"] for request in rendered["requests"]]
    assert urls[0] == "/api/board"
    assert "/api/events" in urls, "SSE is opened at load"


def test_no_request_carries_a_token(rendered):
    """The token is in an HttpOnly cookie; the page cannot read it and never sends it."""
    for request in rendered["requests"]:
        assert "token" not in request["url"], request["url"]
        assert "Authorization" not in (request.get("headers") or {})
    for post in rendered["posted"]:
        assert "token" not in post["url"]


def test_every_fetch_sends_the_cookie(rendered):
    """`credentials: same-origin` is what carries the cookie on a fetch."""
    for request in rendered["requests"]:
        if not request.get("sse"):
            assert request["credentials"] == "same-origin", request["url"]
    for post in rendered["posted"]:
        assert post["credentials"] == "same-origin"


def test_the_board_renders_one_row_per_id_with_partner_first(rendered):
    board = rendered["views"]["board"]
    rows = board["rows"]
    board_json = json.loads((FIXTURES / "board.json").read_text())
    assert len(rows) == len(board_json["items"])
    assert rows[0]["class"] == "partner", "partner first, and marked"
    for row, item in zip(rows, board_json["items"]):
        assert row["cells"][0].startswith(item["id"])
        assert item["file"] in row["cells"][0]


def test_the_board_renders_exactly_the_contract_fields(rendered):
    assert rendered["views"]["board"]["headers"] == [
        "id", "pod / role", "state", "outcome", "after", "subagents",
        "goal", "session", "context", "seams", "turn",
    ]


def test_an_unmet_after_says_what_it_waits_on(rendered):
    cells = [row["cells"] for row in rendered["views"]["board"]["rows"]]
    queued = next(row for row in cells if row[0].startswith("eng-002"))
    assert queued[4] == "waits on eng-003"
    met = next(row for row in cells if row[0].startswith("eng-001"))
    assert met[4] == "eng-000 ✓"


def test_a_dead_session_is_visible_on_the_board(rendered):
    cells = [row["cells"] for row in rendered["views"]["board"]["rows"]]
    assert next(row for row in cells if row[0].startswith("res-001"))[7] == "● dead"
    assert next(row for row in cells if row[0].startswith("eng-001"))[7] == "● live"


def test_invariant_errors_are_shown_prominently(rendered):
    board = rendered["views"]["board"]
    assert board["errorBlocks"], "the errors block is rendered"
    block = board["errorBlocks"][0]
    assert block["heading"] == "invariant errors (1)"
    assert block["items"] == json.loads((FIXTURES / "board.json").read_text())["errors"]
    assert board["headings"][0] == block["heading"], "above the table, not below it"


def test_the_orders_view_draws_the_after_graph(rendered):
    edges = rendered["views"]["orders"]["edges"]
    assert len(edges) == 2
    unmet = [edge for edge in edges if "unmet" in edge["class"]]
    assert len(unmet) == 1
    assert "eng-002" in unmet[0]["text"] and "eng-003" in unmet[0]["text"]
    assert "waits on" in unmet[0]["text"]


def test_the_orders_view_shows_every_order_verbatim(rendered):
    orders = json.loads((FIXTURES / "orders.json").read_text())["orders"]
    blocks = [block["text"] for block in rendered["views"]["orders"]["pre"]]
    for order in orders:
        assert order["order"] in blocks, order["id"]
    for order in orders:
        for addendum in order["addenda"]:
            assert addendum["text"] in blocks


def test_the_orders_view_flags_a_file_edited_since_dispatch(rendered):
    labels = [pill["text"] for pill in rendered["views"]["orders"]["pills"]]
    assert "file edited since dispatch" in labels
    assert "not dispatched" in labels


def test_the_archive_view_shows_benched_bodies_and_dispatches(rendered):
    archive = json.loads((FIXTURES / "archive.json").read_text())
    text = rendered["views"]["archive"]["text"]
    assert f"archive · {len(archive['items'])} ids" in text
    for item in archive["items"]:
        assert item["id"] in text
        for entry in item["bench"] + item["archive"]:
            assert entry["path"] in text
            assert entry["digest"] in text


# -- agent view (spec 16.2) ----------------------------------------------

def test_the_agent_view_starts_as_a_picker_of_every_board_id(rendered):
    picker = rendered["views"]["agentPicker"]
    board = json.loads((FIXTURES / "board.json").read_text())
    assert picker["openable"] == [item["id"] for item in board["items"]]


def test_the_board_opens_an_agent(rendered):
    """Every board row's id is a button into the Agent view."""
    assert rendered["views"]["board"]["openable"] == [
        item["id"] for item in json.loads((FIXTURES / "board.json").read_text())["items"]
    ]
    assert "/api/show/eng-001" in [r["url"] for r in rendered["requests"]]


def test_the_agent_view_has_every_section_spec_16_2_names(rendered):
    agent = rendered["views"]["agent"]
    assert agent["headings"] == [
        "eng-001 · work item", "step state", "context file", "streams",
        "subagents", "metrics", "pane · eng-001",
    ]
    for name in ("frontmatter", "order", "addenda", "tasks", "deliverables",
                 "commands", "open decision", "digest"):
        assert name in agent["subheadings"], name
    for name in ("open steps", "closed steps", "working set", "blockers", "dead ends", "tail"):
        assert name in agent["subheadings"], name


def test_the_agent_view_renders_the_work_item_sections(rendered):
    show = json.loads((FIXTURES / "show-eng-001.json").read_text())
    agent = rendered["views"]["agent"]
    text = agent["text"]
    assert "Add `--require-done`" in show["task"]["order"]
    assert "--require-done" in text, "the order is rendered"
    # Inline `code` spans become <code> elements, so compare without the backticks.
    addendum = show["task"]["addenda"][0]["text"].replace("`", "")
    assert addendum in text.replace("`", ""), "the addendum is rendered beneath the order"

    # The context file carries its own `## Tasks` copy and renders too, so match
    # the work item's four by their text rather than counting the page.
    tasks = {item["text"] for item in agent["listItems"] if item["class"].startswith("task")}
    body_tasks = [
        line.strip("- ").strip()
        for line in findSectionText(show, "Tasks").splitlines()
        if line.strip()
    ]
    assert len(body_tasks) == 4
    for line in body_tasks:
        label = line.replace("[x]", "").replace("[ ]", "").strip().replace("`", "")
        assert any(label in task for task in tasks), label


def test_the_agent_view_renders_step_state(rendered):
    agent = rendered["views"]["agent"]
    text = agent["text"]
    show = json.loads((FIXTURES / "show-eng-001.json").read_text())
    main = show["step_state"]["eng-001-main"]

    assert main["open_steps"][0]["intent"] in text
    assert main["open_steps"][0]["next"] in text, "an open step shows its next action"
    assert main["closed_steps"][0]["outcome"] in text
    commits = [c["text"] for c in agent["code"] if c["class"] == "commit"]
    assert main["closed_steps"][0]["commit"] in commits, "a closed step shows its commit"
    assert main["working_set"]["commits"][0]["msg"] in text
    assert main["working_set"]["files"][0]["note"] in text
    assert main["dead_ends"][0] in text


def test_the_agent_view_renders_the_context_file_with_its_seam(rendered):
    show = json.loads((FIXTURES / "show-eng-001.json").read_text())
    text = rendered["views"]["agent"]["text"]
    assert show["context_file"]["path"] in text
    assert "seam 12:50:00Z" in text
    # Rendered as markdown since ui-5, so match its prose rather than the blob.
    plain = text.replace("`", "")
    for line in (
        "Prefer same-directory renames; spec 08 forbids timeouts anywhere.",
        "Also refuse an id with no config/<id>/ directory; exit 1 and name it.",
        "open: st7 refuse an id with no config/<id>/",
    ):
        assert line in plain, line


def findSectionText(show, name):
    """The named `## ` section of the work-item body."""
    current, lines = None, []
    for line in show["work_item"]["body"].splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            continue
        if current == name:
            lines.append(line)
    return "\n".join(lines).strip()


def test_the_agent_view_renders_every_stream_tail_and_marks_the_seam(rendered):
    show = json.loads((FIXTURES / "show-eng-001.json").read_text())
    text = rendered["views"]["agent"]["text"]
    for stream in show["streams"]:
        assert stream["handle"] in text
        assert stream["path"] in text
        if stream.get("digest"):
            assert stream["digest"] in text, "a closed subagent stream shows its digest"
    assert "context file 2184 B" in text, "spec 7.4: a seam shows the context file size"


def test_the_agent_view_renders_subagents_and_metrics_and_the_pane(rendered):
    show = json.loads((FIXTURES / "show-eng-001.json").read_text())
    text = rendered["views"]["agent"]["text"]
    for claude_id, handle in show["subagents"].items():
        assert claude_id in text and handle in text
    assert rendered["views"]["agent"]["metrics"] is not None, "hx metrics is rendered"
    for line in show["pane"]["lines"]:
        assert line in text


def test_an_absent_value_reads_not_yet(rendered):
    """The goal: render `null` as "not yet", never as an empty box."""
    assert rendered["views"]["agent"]["notYet"] > 0
    assert "not yet" in rendered["views"]["agent"]["text"]


# -- partner view (spec 16.2) --------------------------------------------

def test_the_partner_view_renders_partner_md_the_board_and_chat(rendered):
    partner = rendered["views"]["partner"]
    show = json.loads((FIXTURES / "show-partner.json").read_text())
    board = json.loads((FIXTURES / "board.json").read_text())

    assert "PARTNER.md" in partner["headings"]
    assert "chat" in partner["headings"]
    assert any(h.startswith("pane · partner") for h in partner["headings"])

    for line in show["partner_md"].splitlines():
        body = line.lstrip("#- ").strip()
        if body:
            assert body.replace("`", "") in partner["text"].replace("`", ""), body

    assert len(partner["rows"]) == len(board["items"]), "the whole board is on the Partner view"


def test_the_partner_view_says_where_full_control_is(rendered):
    """Spec 16.2: the page says so."""
    text = rendered["views"]["partner"]["text"]
    assert "tmux attach -t partner" in text
    assert "slash commands" in text and "interrupts" in text


def test_the_chat_box_posts_the_trimmed_text_and_nothing_else(rendered):
    assert rendered["posted"] == [
        {
            "url": "/api/partner/wake",
            "body": {"text": "eng-003 complete: decision; hx read eng-003"},
            "credentials": "same-origin",
        }
    ]


def test_the_chat_box_reports_delivery_and_clears(rendered):
    assert "delivered" in rendered["wakeStatus"]
    assert rendered["wakeCleared"] is True


# -- metrics (spec 07.4, CONTRACTS.md `hx metrics --json`) ---------------

def metrics_document():
    return json.loads((FIXTURES / "metrics-eng-001.json").read_text())


def test_the_metrics_table_has_a_column_per_contract_field(rendered):
    table = rendered["views"]["agent"]["metrics"]
    assert table is not None, "metrics render as a table, not as raw JSON"
    assert table["headers"] == [
        "seq", "ts", "source", "prompt", "ctx tokens before", "context file",
        "working set", "turns", "tool calls", "ctx-file reads", "working-set reads", "other",
    ]


def test_one_row_per_seam_in_order(rendered):
    seams = metrics_document()["seams"]
    rows = rendered["views"]["agent"]["metrics"]["rows"]
    assert len(rows) == len(seams)
    for row, seam in zip(rows, seams):
        cells = [cell["text"] for cell in row["cells"]]
        assert cells[0] == str(seam["seq"])
        assert cells[2] == seam["source"]
        assert cells[3] == seam["prompt_version"]
        assert cells[4] == f"{seam['context_tokens_before']:,}"
        assert cells[5] == f"{seam['context_file_bytes']:,} B"
        assert cells[6] == str(seam["working_set_size"])
        next_10 = seam["next_10_turns"]
        assert cells[7] == str(next_10["turns"])
        assert cells[8] == str(next_10["tool_calls"])
        assert cells[9] == str(next_10["reads_of_context_file"])
        assert cells[10] == str(next_10["reads_of_working_set"])
        assert cells[11] == str(next_10["other"])


def test_a_seam_is_marked_when_it_did_not_hand_over_cleanly(rendered):
    """Red on `reads_of_context_file != 1` or any `reads_of_working_set`."""
    seams = metrics_document()["seams"]
    rows = rendered["views"]["agent"]["metrics"]["rows"]
    for row, seam in zip(rows, seams):
        next_10 = seam["next_10_turns"]
        bad = next_10["reads_of_context_file"] != 1 or next_10["reads_of_working_set"] > 0
        assert ("bad-row" in row["class"]) is bad, seam["seq"]
    assert sum(1 for row in rows if "bad-row" in row["class"]) == 3, "the fixture has three"


@pytest.mark.parametrize(
    "seq, offending, why",
    [
        (203, ["3"], "re-read 3 working-set files"),
        (318, ["0"], "never read its context file"),
        (401, ["2", "1"], "read the context file 2 times; re-read 1 working-set file"),
    ],
)
def test_the_offending_number_is_marked_and_explained(rendered, seq, offending, why):
    rows = rendered["views"]["agent"]["metrics"]["rows"]
    row = next(r for r in rows if r["cells"][0]["text"] == str(seq))
    assert [c["text"] for c in row["cells"] if "bad-cell" in c["class"]] == offending
    assert row["title"] == why, "hovering says why, so the colour is not the only signal"


def test_a_clean_seam_marks_nothing(rendered):
    rows = rendered["views"]["agent"]["metrics"]["rows"]
    for seq in ("96", "412"):
        row = next(r for r in rows if r["cells"][0]["text"] == seq)
        assert row["class"] == ""
        assert row["title"] is None
        assert not [c for c in row["cells"] if "bad-cell" in c["class"]]


def test_a_stream_that_ended_early_shows_its_real_turn_count(rendered):
    rows = rendered["views"]["agent"]["metrics"]["rows"]
    row = next(r for r in rows if r["cells"][0]["text"] == "412")
    turns = row["cells"][7]
    assert turns["text"] == "3", "next_10_turns.turns is fewer than 10 when the stream ended sooner"
    assert "short" in turns["class"], "and is marked as a partial window"


def test_the_totals_row_matches_the_contract_totals(rendered):
    totals = metrics_document()["totals"]
    cells = [cell["text"] for cell in rendered["views"]["agent"]["metrics"]["totals"]["cells"]]
    assert cells[0] == "totals"
    assert cells[2] == f"{totals['seams']} seams"
    assert cells[8] == str(totals["tool_calls"])
    assert cells[9] == str(totals["reads_of_context_file"])
    assert cells[10] == str(totals["reads_of_working_set"])
    assert cells[11] == str(totals["other"])


def test_the_totals_row_marks_the_fleet_level_waste(rendered):
    """One context-file read per seam is the target; any working-set read is waste."""
    totals = rendered["views"]["agent"]["metrics"]["totals"]
    marked = [cell["text"] for cell in totals["cells"] if "bad-cell" in cell["class"]]
    assert marked == ["4"], "5 ctx-file reads over 5 seams is right; 4 working-set reads is not"


def test_the_metrics_section_says_how_many_seams_were_dirty(rendered):
    warn = rendered["views"]["agent"]["warn"]
    assert len(warn) == 1
    assert warn[0].startswith("3 of 5 seams did not hand over cleanly")
    assert not rendered["views"]["agent"]["ok"]


def test_a_clean_run_says_so_instead(tmp_path):
    show = json.loads((FIXTURES / "show-eng-001.json").read_text())
    for seam in show["metrics"]["seams"]:
        seam["next_10_turns"]["reads_of_context_file"] = 1
        seam["next_10_turns"]["reads_of_working_set"] = 0
    show["metrics"]["totals"]["reads_of_context_file"] = len(show["metrics"]["seams"])
    show["metrics"]["totals"]["reads_of_working_set"] = 0
    agent = render({"/api/show/eng-001": show}, tmp_path)["views"]["agent"]
    assert agent["ok"] == ["every seam handed over cleanly."]
    assert not agent["warn"]
    assert not [row for row in agent["metrics"]["rows"] if row["class"]]
    assert not [c for c in agent["metrics"]["totals"]["cells"] if "bad-cell" in c["class"]]


def test_an_agent_with_no_seams_yet_reads_not_yet(tmp_path):
    show = json.loads((FIXTURES / "show-eng-001.json").read_text())
    show["metrics"] = {"id": "eng-001", "stream": "eng-001-main", "dispatched": None,
                       "seams": [], "totals": {"seams": 0, "tool_calls": 0,
                                               "reads_of_context_file": 0,
                                               "reads_of_working_set": 0, "other": 0}}
    agent = render({"/api/show/eng-001": show}, tmp_path)["views"]["agent"]
    assert agent["metrics"] is None
    assert agent["notYet"] > 0


# -- seams in the stream tail (spec 16.2) --------------------------------

def test_a_seam_record_shows_the_turns_that_followed_it(rendered):
    """Spec 16.2: the marker carries the context file size and the ten turns after."""
    followups = rendered["views"]["agent"]["followups"]
    assert len(followups) == 1, "one seam record in the fixture's tails"
    text = followups[0]["text"]
    seam = next(s for s in metrics_document()["seams"] if s["seq"] == 412)
    next_10 = seam["next_10_turns"]
    assert text.startswith("next 3 turns: "), "the real window, not a hardcoded 10"
    assert f"{next_10['tool_calls']} tool calls" in text
    assert f"{next_10['reads_of_context_file']} ctx-file" in text
    assert f"{next_10['reads_of_working_set']} working-set" in text
    assert f"{next_10['other']} other" in text
    assert "context file 2184 B" in rendered["views"]["agent"]["text"]


def test_a_clean_seam_marker_is_not_marked(rendered):
    assert rendered["views"]["agent"]["followups"][0]["class"] == "followup"
    assert rendered["views"]["agent"]["followups"][0]["title"] is None


def test_a_dirty_seam_marker_is_marked_and_explained(tmp_path):
    show = json.loads((FIXTURES / "show-eng-001.json").read_text())
    seam = next(s for s in show["metrics"]["seams"] if s["seq"] == 412)
    seam["next_10_turns"]["reads_of_working_set"] = 2
    agent = render({"/api/show/eng-001": show}, tmp_path)["views"]["agent"]
    followup = agent["followups"][0]
    assert "bad" in followup["class"]
    assert followup["title"] == "re-read 2 working-set files"
    assert "2 working-set" in followup["text"]


def test_a_seam_with_no_metrics_entry_says_not_yet(tmp_path):
    """The tail can outrun the metrics document; it must not render a wrong number."""
    show = json.loads((FIXTURES / "show-eng-001.json").read_text())
    show["metrics"]["seams"] = [s for s in show["metrics"]["seams"] if s["seq"] != 412]
    agent = render({"/api/show/eng-001": show}, tmp_path)["views"]["agent"]
    assert agent["followups"][0]["text"] == "next 10 turns: not yet"
    assert "context file 2184 B" in agent["text"], "the size still comes from the record itself"


def test_records_that_are_not_seams_keep_their_rendering(rendered):
    """Only the seam record gained anything; the rest of the tail is unchanged."""
    text = rendered["views"]["agent"]["text"]
    show = json.loads((FIXTURES / "show-eng-001.json").read_text())
    main = next(s for s in show["streams"] if s["handle"] == "eng-001-main")
    for record in main["tail"]:
        if record["event"] == "seam":
            continue
        assert record["tool"] in text
        assert str(record["input"]) in text
        assert f"exit {record['exit']}" in text
    assert len(rendered["views"]["agent"]["followups"]) == 1


# -- the views against a real instance -----------------------------------

def test_every_view_renders_against_a_real_instance(instance_root, tmp_path):
    """Fixtures are hand-written; a real instance is full of nulls and empties.

    `hx show --json` on a fresh instance returns `metrics: null`, no streams, no
    step state and an empty context file, which is exactly the shape the
    hand-written fixtures do not have. The views have to survive it.
    """
    from hx.ui.data import InstanceSource

    source = InstanceSource(instance_root)
    overrides = {
        "/api/board": source.board(),
        "/api/orders": source.orders(),
        "/api/archive": source.archive(),
        "/api/show/partner": source.show("partner"),
        "/api/show/eng-001": source.show("eng-001"),
    }
    rendered = render(overrides, tmp_path)

    assert rendered["banner"] is None, "no view failed"

    agent = rendered["views"]["agent"]
    assert agent["headings"] == [
        "eng-001 · work item", "step state", "context file", "streams",
        "subagents", "metrics", "pane · eng-001",
    ]
    assert agent["metrics"] is None, "a fresh instance has no seams yet"
    assert agent["notYet"] > 0, "and the empty sections say so"
    assert not agent["warn"] and not agent["ok"], "no metrics verdict without metrics"

    partner = rendered["views"]["partner"]
    assert "PARTNER.md" in partner["headings"]
    assert "chat" in partner["headings"]
    assert "tmux attach -t partner" in partner["text"]
    # PARTNER.md renders its own markdown tables now, so count board rows only:
    # the board has one cell per COLUMNS entry.
    board_rows = [row for row in partner["rows"] if len(row["cells"]) == 11]
    assert len(board_rows) == len(overrides["/api/board"]["items"])

    board = rendered["views"]["board"]
    assert [row["cells"][0].split("pods/")[0] for row in board["rows"]] == ["partner", "eng-001"]
    assert board["errorBlocks"], "a scratch instance has invariant errors and the board shows them"


def test_the_orders_view_shows_a_real_file_edited_since_dispatch(instance_root, tmp_path):
    """The badge, against a real `orders/<id>.md` that no longer matches tasks.json."""
    from hx.ui.data import InstanceSource

    source = InstanceSource(instance_root)
    orders = source.orders()
    by_id = {order["id"]: order for order in orders["orders"]}
    assert by_id["partner"]["file_matches_record"] is True
    assert by_id["eng-001"]["file_matches_record"] is False

    rendered = render({"/api/board": source.board(), "/api/orders": orders}, tmp_path)
    labels = [pill["text"] for pill in rendered["views"]["orders"]["pills"]]
    assert labels.count("file edited since dispatch") == 1, "exactly the one that drifted"


# -- ui-5: the orchestrator's browser pass -------------------------------

def order_with(record, order_text):
    return {
        "id": "eng-009", "pod": "engineers", "path": "orders/eng-009.md", "after": [],
        "order": order_text, "addenda": [], "record": record, "state": "working" if record else None,
        "ready": True, "waiting_on": [], "file_matches_record": None if record is None else
        (order_text is not None and order_text == record["order"]),
    }


def orders_payload(entry):
    return {
        "root_abs": "/srv/hx", "ts": "2026-09-20T13:10:00Z", "orders": [entry],
        "graph": {"nodes": [], "edges": []}, "errors": [],
    }


RECORD = {
    "order": "## Order\nDo the thing.\n", "after": [], "addenda": [],
    "outcome": None, "dispatched": "2026-09-20T12:00:00Z", "completed": None,
}


def badges(rendered):
    return [pill["text"] for pill in rendered["views"]["orders"]["pills"]]


def test_an_order_file_that_matches_gets_no_badge(tmp_path):
    entry = order_with(RECORD, RECORD["order"])
    assert entry["file_matches_record"] is True
    rendered = render({"/api/orders": orders_payload(entry)}, tmp_path)
    assert "file edited since dispatch" not in badges(rendered)
    assert "order file missing" not in badges(rendered)


def test_an_edited_order_file_says_edited(tmp_path):
    entry = order_with(RECORD, RECORD["order"] + "\nAnd one more thing.\n")
    assert entry["file_matches_record"] is False
    rendered = render({"/api/orders": orders_payload(entry)}, tmp_path)
    assert "file edited since dispatch" in badges(rendered)
    assert "order file missing" not in badges(rendered)


def test_a_missing_order_file_says_missing_not_edited(tmp_path):
    """`hx orders` reports `file_matches_record: false` for both; only one is an edit."""
    entry = order_with(RECORD, None)
    entry["path"] = None
    assert entry["file_matches_record"] is False
    rendered = render({"/api/orders": orders_payload(entry)}, tmp_path)
    assert "order file missing" in badges(rendered)
    assert "file edited since dispatch" not in badges(rendered), (
        "a file that does not exist was not edited"
    )


def test_an_undispatched_order_gets_neither_badge(tmp_path):
    entry = order_with(None, "## Order\nNot dispatched yet.\n")
    rendered = render({"/api/orders": orders_payload(entry)}, tmp_path)
    assert "order file missing" not in badges(rendered)
    assert "file edited since dispatch" not in badges(rendered)
    assert "not dispatched" in badges(rendered)


# -- markdown tables -----------------------------------------------------

SKELETON_PARTNER = REPO / "src" / "hx" / "skeleton" / "PARTNER.md"


def test_the_skeleton_partner_md_tables_render_as_tables(tmp_path):
    """`PARTNER.md`'s fleet table was showing as raw pipes."""
    show = json.loads((FIXTURES / "show-partner.json").read_text())
    show["partner_md"] = SKELETON_PARTNER.read_text()
    rendered = render({"/api/show/partner": show}, tmp_path)
    partner = rendered["views"]["partner"]

    assert "|---|" not in partner["text"], "no separator row leaked through as text"
    assert "| id |" not in partner["text"]
    for header in ("what it is for", "asked in chat", "what landed"):
        assert header in partner["headers"], f"{header!r} is a table header now"


def test_a_table_renders_one_cell_per_header(tmp_path):
    show = json.loads((FIXTURES / "show-partner.json").read_text())
    show["partner_md"] = (
        "# Fleet\n\n"
        "| id | role | notes |\n"
        "|---|:---:|---:|\n"
        "| eng-001 | engineer | first |\n"
        "| eng-002 | engineer |\n"
    )
    rendered = render({"/api/show/partner": show}, tmp_path)
    rows = [row for row in rendered["views"]["partner"]["rows"] if len(row["cells"]) == 3]
    assert [cell["text"] if isinstance(cell, dict) else cell for cell in rows[0]["cells"]] == [
        "eng-001", "engineer", "first",
    ]
    short = rows[1]["cells"]
    assert len(short) == 3, "a short row is padded rather than shifting the columns"
    assert (short[2]["text"] if isinstance(short[2], dict) else short[2]) == ""


def test_a_lone_pipe_line_is_still_a_paragraph(tmp_path):
    """Only a header followed by a rule row is a table."""
    show = json.loads((FIXTURES / "show-partner.json").read_text())
    show["partner_md"] = "Use `a | b` to pipe.\n"
    rendered = render({"/api/show/partner": show}, tmp_path)
    assert "Use a | b to pipe." in rendered["views"]["partner"]["text"]


# -- pane wording --------------------------------------------------------

def test_a_pane_with_no_session_and_no_log_says_so(tmp_path):
    show = json.loads((FIXTURES / "show-eng-001.json").read_text())
    show["pane"] = {"session": "eng-001", "alive": False, "lines": [], "source": "none", "error": "gone"}
    agent = render({"/api/show/eng-001": show}, tmp_path)["views"]["agent"]
    assert "no session, no log" in agent["text"]
    assert "from the none" not in agent["text"]


@pytest.mark.parametrize("source, said", [("session", "from the session"), ("log", "from the log")])
def test_the_other_two_pane_sources_keep_their_wording(tmp_path, source, said):
    show = json.loads((FIXTURES / "show-eng-001.json").read_text())
    show["pane"] = {"session": "eng-001", "alive": source == "session",
                    "lines": ["a line"], "source": source, "error": None}
    agent = render({"/api/show/eng-001": show}, tmp_path)["views"]["agent"]
    assert said in agent["text"]


# -- the agent id switcher -----------------------------------------------

def test_the_agent_view_offers_every_other_id(rendered):
    board = json.loads((FIXTURES / "board.json").read_text())
    ids = [item["id"] for item in board["items"]]
    agent = rendered["views"]["agent"]
    assert agent["openable"] == [i for i in ids if i != "eng-001"], (
        "every id but the one being shown, which is not a button"
    )
    assert "eng-001" in agent["text"]


def test_the_switcher_opens_another_agent(tmp_path):
    board = json.loads((FIXTURES / "board.json").read_text())
    overrides = {"/api/board": board}
    for name in ("eng-001", "partner"):
        overrides[f"/api/show/{name}"] = json.loads((FIXTURES / f"show-{name}.json").read_text())
    rendered = render(overrides, tmp_path, open_ids=["eng-001"])
    # The switcher is what `openable` lists, and clicking one is what the M8
    # tests do for every worker; here it is enough that it is reachable.
    assert rendered["views"]["agents"]["eng-001"]["openable"], "other ids are offered"


def test_a_single_id_fleet_has_no_switcher(tmp_path):
    board = json.loads((FIXTURES / "board.json").read_text())
    board["items"] = [item for item in board["items"] if item["id"] == "eng-001"]
    board["errors"] = []
    show = json.loads((FIXTURES / "show-eng-001.json").read_text())
    rendered = render({"/api/board": board, "/api/show/eng-001": show}, tmp_path,
                      open_ids=["eng-001"])
    assert rendered["views"]["agents"]["eng-001"]["openable"] == [], (
        "nothing to switch to, so no switcher"
    )


# -- narrow width (CSS only, so asserted as rules rather than pixels) -----

STYLE = STATIC / "style.css"


def css() -> str:
    return STYLE.read_text(encoding="utf-8")


def test_the_board_id_column_is_sticky_while_the_table_scrolls():
    """At 375 px the board scrolls in its own container; the id must stay put."""
    text = css()
    assert ".scroll table td.id" in text
    block = text.split(".scroll table thead th:first-child,")[1].split("}")[0]
    assert "position: sticky" in block
    assert "left: 0" in block
    assert "background:" in block, "a transparent sticky cell shows the rows sliding under it"


def test_the_markdown_tables_are_not_pinned():
    """Only the wide board needs a sticky column; a PARTNER.md table does not."""
    assert ".scroll table.md td.id { position: static; background: none; }" in css()


def test_the_nav_wraps_without_pushing_live_onto_its_own_line():
    narrow = css().split("@media (max-width: 640px)")[1]
    assert "nav { flex-basis: 100%; order: 3; }" in narrow
    assert ".live { order: 2; margin-left: auto; }" in narrow, (
        "LIVE stays on the title row; the nav takes the row below"
    )


def test_every_view_keeps_a_side_gutter():
    """Nothing may sit flush against a 375 px edge."""
    assert "main {" in css()
    narrow = css().split("@media (max-width: 640px)")[1]
    assert "main { padding: 14px 16px; }" in narrow


def test_the_context_file_renders_as_markdown(tmp_path):
    """build-3: it has a fixed section order and is meant to be read, not dumped."""
    show = json.loads((FIXTURES / "show-eng-001.json").read_text())
    show["context_file"] = {
        "path": "run/eng-001/eng-001-main.context.md",
        "seam_ts": "2026-09-20T12:50:00Z",
        "text": (
            "# Context for eng-001-main\n\n"
            "## Memory\n_source: `config/eng-001/AGENTS.md`_\n\nPrefer same-directory renames.\n\n"
            "## Tasks\n- [x] Read spec 08.\n- [ ] Refuse an unknown id.\n\n"
            "## Step state\n_none yet_\n"
        ),
    }
    agent = render({"/api/show/eng-001": show}, tmp_path)["views"]["agent"]

    text = agent["text"]
    for heading in ("Context for eng-001-main", "Memory", "Step state"):
        assert heading in text, heading
    assert "_source:" not in text, "the source line is rendered, not shown as raw markdown"
    assert "config/eng-001/AGENTS.md" in [code["text"] for code in agent["code"]], (
        "the file it came from renders as code"
    )
    context_tasks = [item for item in agent["listItems"] if item["class"].startswith("task")]
    assert len(context_tasks) >= 2, "the `## Tasks` section renders as checkboxes"
    assert not any(
        block["text"].startswith("# Context for") for block in agent["pre"]
    ), "no longer dumped into a <pre>"
