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


@pytest.fixture(scope="module")
def rendered():
    if shutil.which("node") is None:
        pytest.skip("no node on PATH")
    result = subprocess.run(
        ["node", str(RENDER), str(FIXTURES), str(STATIC)],
        capture_output=True, text=True, check=False, cwd=REPO,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


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
    assert set(rendered["views"]) == {"board", "orders", "archive", "agentPicker", "agent", "partner"}
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

    tasks = [item for item in agent["listItems"] if item["class"].startswith("task")]
    assert len(tasks) == 4, "the four `## Tasks` checkboxes"
    assert sum(1 for t in tasks if "done" in t["class"]) == 2
    assert sum(1 for t in tasks if "open" in t["class"]) == 2


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
    assert any(show["context_file"]["text"] == block["text"] for block in rendered["views"]["agent"]["pre"])


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
    assert "tool_calls_next_10_turns" in text, "hx metrics is rendered"
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
