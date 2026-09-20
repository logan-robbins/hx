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
    assert set(rendered["views"]) == {"board", "orders", "archive", "agent", "partner"}
    assert rendered["navigation"] == ["board", "orders", "archive", "agent", "partner"]


def test_the_board_opens_first_and_is_the_only_first_fetch(rendered):
    urls = [request["url"] for request in rendered["requests"]]
    assert urls[0] == "/api/board"
    assert any(url.startswith("/api/events?token=") for url in urls), "SSE is opened at load"


def test_every_request_carries_the_bearer_token(rendered):
    for request in rendered["requests"]:
        if request.get("sse"):
            assert "token=" in request["url"]
        else:
            assert request["headers"]["Authorization"].startswith("Bearer ")


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
    blocks = rendered["views"]["orders"]["pre"]
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


def test_the_agent_and_partner_views_are_navigable_shells(rendered):
    agent = rendered["views"]["agent"]["text"]
    partner = rendered["views"]["partner"]["text"]
    assert "ui-2" in agent and "/api/show/" in agent
    assert "ui-2" in partner and "hx wake partner" in partner
    assert "tmux attach -t partner" in partner, "spec 16.2: the page says where full control is"
