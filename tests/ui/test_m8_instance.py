"""ui-4 item 3: the whole UI against a real instance in the M8 shape.

`tests/scenario/m8/` is the gtm lane's pack — the data spec 13's M8 runs on. This
builds an instance at observation point 4 of its README (the Partner working,
`eng-001` done, `eng-002` stopped on a `decision` behind it), places the pack's
real orders and personas, and serves it. No agent is launched: pane capture finds
no session and falls back to the pane log, which is the state the goal asks for.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from hx.orders import parse_order
from hx.ui.data import InstanceSource

from .conftest import _serve, manifest

REPO = Path(__file__).resolve().parents[2]
PACK = REPO / "tests" / "scenario" / "m8"

#: README step 4: the decision is open and eng-002 is waiting on the human.
STATES = {
    "partner": ("working", None, [], True),
    "eng-001": ("complete", "done", [], False),
    "eng-002": ("complete", "decision", ["eng-001"], False),
}

PANE_LOG = (
    "\x1b[2m> hx task\x1b[0m\n"
    "I have built everything that does not depend on the open question and committed it.\n"
    "\x1b[1;33mWriting the question into ## Open decision and stopping.\x1b[0m\n"
    "> hx complete decision\n"
    "HX-COMPLETE eng-002 decision\n"
)


@pytest.fixture(scope="module")
def m8_root(tmp_path_factory):
    """An instance in the M8 shape, built once and checked for writes afterwards."""
    pytest.importorskip("tests.scenario.packlib", reason="the M8 pack is the gtm lane's")
    from tests.scenario import packlib

    root = tmp_path_factory.mktemp("hx-m8")
    packlib.build_instance(root, STATES, worker_pod="engineers")

    orders = root / "orders"
    orders.mkdir(parents=True, exist_ok=True)
    for source in sorted((PACK / "orders").glob("*.md")):
        shutil.copy(source, orders / source.name)
    for source in sorted((PACK / "config").glob("*/AGENTS.md")):
        target = root / "config" / source.parent.name
        target.mkdir(parents=True, exist_ok=True)
        shutil.copy(source, target / "AGENTS.md")

    # Record what `hx dispatch` records: the parsed `## Order` text, not the file.
    tasks = json.loads((root / "tasks.json").read_text())
    for item_id, record in tasks.items():
        order_file = orders / f"{item_id}.md"
        if order_file.is_file():
            parsed = parse_order(order_file)
            record["order"] = parsed.text
            record["after"] = parsed.after
    (root / "tasks.json").write_text(json.dumps(tasks, indent=2) + "\n", encoding="utf-8")

    # The pane log the UI falls back to when the session is gone (spec 03, 11).
    log = root / "logs" / "eng-002" / "eng-002-pane.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(PANE_LOG, encoding="utf-8")

    before = manifest(root)
    yield root
    after = manifest(root)
    changed = sorted(
        name for name in set(before) | set(after)
        if before.get(name) != after.get(name) and name != "run/ui-token"
    )
    assert not changed, f"the UI wrote to the M8 instance: {changed}"


@pytest.fixture(scope="module")
def m8(m8_root):
    server = _serve(InstanceSource(m8_root))
    handle = next(server)
    try:
        yield handle
    finally:
        server.close()


# -- the pack is placed as its README says -------------------------------

def test_the_pack_is_placed(m8_root):
    assert sorted(p.name for p in (m8_root / "orders").glob("*.md")) == [
        "eng-001.md", "eng-002.addendum.md", "eng-002.md", "partner.md",
    ]
    for item_id in ("eng-001", "eng-002"):
        assert (m8_root / "config" / item_id / "AGENTS.md").is_file()
        assert "## UPDATES BELOW ONLY" in (m8_root / "config" / item_id / "AGENTS.md").read_text()
    assert (m8_root / "pods" / "partner" / "partner-working.md").is_file()
    assert (m8_root / "pods" / "engineers" / "eng-001-complete.md").is_file()
    assert (m8_root / "pods" / "engineers" / "eng-002-complete.md").is_file()


# -- every view ----------------------------------------------------------

def test_the_board_is_the_m8_decision_point(m8):
    status, board = m8.client.json("/api/board")
    assert status == 200
    rows = {item["id"]: item for item in board["items"]}
    assert [item["id"] for item in board["items"]] == ["partner", "eng-001", "eng-002"]
    assert rows["partner"]["state"] == "working"
    assert (rows["eng-001"]["state"], rows["eng-001"]["outcome"]) == ("complete", "done")
    assert (rows["eng-002"]["state"], rows["eng-002"]["outcome"]) == ("complete", "decision")
    assert rows["eng-002"]["after"] == ["eng-001"]
    assert rows["eng-002"]["ready"] is True, "its dependency finished done"
    assert all(item["session_alive"] is False for item in board["items"]), "no agent is launched"


def test_show_eng_002_carries_the_decision(m8):
    status, show = m8.client.json("/api/show/eng-002")
    assert status == 200
    assert show["id"] == "eng-002"
    assert show["state"] == "complete"
    assert show["task"]["outcome"] == "decision"
    assert show["task"]["after"] == ["eng-001"]
    assert "--lang" in show["task"]["order"], "the pack's real order"


def test_the_pane_falls_back_to_the_log(m8):
    """No session, so the last thing the agent said comes from the pane log."""
    _, show = m8.client.json("/api/show/eng-002")
    pane = show["pane"]
    assert pane["alive"] is False
    assert pane["source"] == "log"
    assert pane["error"]
    assert pane["lines"] == [
        "> hx task",
        "I have built everything that does not depend on the open question and committed it.",
        "Writing the question into ## Open decision and stopping.",
        "> hx complete decision",
        "HX-COMPLETE eng-002 decision",
    ], "ANSI stripped, in order"


def test_a_partner_with_no_log_says_the_session_is_gone(m8):
    _, show = m8.client.json("/api/show/partner")
    assert show["pane"]["source"] == "none"
    assert show["pane"]["lines"] == []
    assert "partner" in show["pane"]["error"]


def test_orders_shows_the_after_chain_and_the_edited_file(m8, m8_root):
    status, orders = m8.client.json("/api/orders")
    assert status == 200
    by_id = {order["id"]: order for order in orders["orders"]}
    assert by_id["eng-002"]["after"] == ["eng-001"]
    assert by_id["eng-002"]["waiting_on"] == [], "eng-001 finished done"
    assert orders["graph"]["edges"] == [{"from": "eng-001", "to": "eng-002", "met": True}]
    assert all(order["file_matches_record"] for order in orders["orders"]), (
        "nothing has been edited since dispatch"
    )


def test_archive_is_empty_at_this_point(m8):
    status, archive = m8.client.json("/api/archive")
    assert status == 200
    assert {item["id"] for item in archive["items"]} == {"partner", "eng-001", "eng-002"}
    assert all(not item["bench"] and not item["archive"] for item in archive["items"]), (
        "nothing is benched until README step 8"
    )


def test_the_wake_says_the_partner_has_no_socket(m8):
    response = m8.client.post("/api/partner/wake", {"text": "eng-002 needs a decision"})
    assert response.status == 200
    assert json.loads(response.body) == {"delivered": False, "status": "no-socket"}


# -- the views render it -------------------------------------------------

def test_every_view_renders_the_m8_instance(m8_root, tmp_path):
    from .test_views_js import render

    source = InstanceSource(m8_root)
    rendered = render(
        {
            "/api/board": source.board(),
            "/api/orders": source.orders(),
            "/api/archive": source.archive(),
            "/api/show/partner": source.show("partner"),
            "/api/show/eng-001": source.show("eng-001"),
        },
        tmp_path,
    )
    assert rendered["banner"] is None
    assert rendered["views"]["board"]["headings"] == ["invariant errors (1)", "board · 3 items"]
    assert rendered["views"]["orders"]["headings"] == ["after graph · 1 edge", "orders · 3"]
    assert rendered["views"]["archive"]["headings"] == ["archive · 3 ids"]
    assert "not composed yet" in rendered["views"]["agent"]["text"], (
        "a context file hx has named but not written is not `0 chars`"
    )
    assert len(rendered["views"]["partner"]["rows"]) == 3
