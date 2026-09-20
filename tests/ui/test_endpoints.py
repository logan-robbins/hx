"""What each endpoint answers, against fixtures conforming to CONTRACTS.md."""

from __future__ import annotations

import json

import pytest


def test_board_matches_the_contract(ui):
    status, payload = ui.client.json("/api/board")
    assert status == 200
    assert set(payload) == {"root_abs", "ts", "items", "errors"}
    assert [item["id"] for item in payload["items"]][0] == "partner", "partner first"
    rest = [item["id"] for item in payload["items"]][1:]
    assert rest == sorted(rest), "then by id"
    for item in payload["items"]:
        assert set(item) == {
            "id", "pod", "role", "state", "file", "after", "ready", "outcome",
            "dispatched", "completed", "open_subagents", "goal_ts", "goal_pending",
            "session_alive", "context_tokens", "seams", "turn_ts",
        }
        assert item["state"] in {"idle", "queued", "working", "complete"}
        assert item["outcome"] in {None, "done", "blocked", "decision", "exhausted"}


def test_board_carries_the_cases_the_views_must_render(ui):
    _, payload = ui.client.json("/api/board")
    items = {item["id"]: item for item in payload["items"]}
    queued = [i for i in items.values() if i["state"] == "queued"]
    assert queued and all(not i["ready"] and i["after"] for i in queued), "a queued item with an unmet after"
    assert any(i["state"] == "complete" and i["outcome"] == "decision" for i in items.values())
    assert len(payload["errors"]) == 1, "exactly one invariant error"


@pytest.mark.parametrize("agent_id", ["partner", "eng-001"])
def test_show_matches_the_contract(ui, agent_id):
    status, payload = ui.client.json(f"/api/show/{agent_id}")
    assert status == 200
    assert payload["id"] == agent_id
    for key in (
        "pod", "role", "state", "file", "work_item", "task", "persona_path", "step_state",
        "context_file", "streams", "subagents", "metrics", "pane", "archive", "bench",
    ):
        assert key in payload, key
    assert set(payload["work_item"]) == {"frontmatter", "body"}
    assert set(payload["task"]) == {"order", "after", "addenda", "outcome", "dispatched", "completed"}
    assert set(payload["context_file"]) == {"path", "text", "seam_ts"}
    for stream in payload["streams"]:
        assert {"handle", "path", "open", "records", "tail"} <= set(stream)
        assert len(stream["tail"]) <= 50, "tail holds the last 50 records"
        if not stream["open"]:
            assert "digest" in stream


def test_show_partner_carries_partner_md(ui):
    _, payload = ui.client.json("/api/show/partner")
    assert isinstance(payload["partner_md"], str) and payload["partner_md"]
    _, other = ui.client.json("/api/show/eng-001")
    assert "partner_md" not in other


def test_show_unknown_id_is_404(ui):
    status, payload = ui.client.json("/api/show/eng-999")
    assert status == 404
    assert "eng-999" in payload["error"] or "fixture" in payload["error"]


@pytest.mark.parametrize("bad", ["../board", "not-an-id", "eng-1", ""])
def test_show_rejects_a_non_id(ui, bad):
    assert ui.client.get(f"/api/show/{bad}").status == 404


def test_orders_carries_the_after_graph(ui):
    status, payload = ui.client.json("/api/orders")
    assert status == 200
    assert set(payload) == {"root_abs", "ts", "orders", "graph", "errors"}
    by_id = {order["id"]: order for order in payload["orders"]}
    assert by_id["eng-002"]["waiting_on"] == ["eng-003"], "which queued item waits on which id"
    assert by_id["eng-001"]["waiting_on"] == []
    assert by_id["eng-004"]["record"] is None, "an order written but never dispatched"
    edges = {(edge["from"], edge["to"]): edge["met"] for edge in payload["graph"]["edges"]}
    assert edges == {("eng-000", "eng-001"): True, ("eng-003", "eng-002"): False}
    assert {node["id"] for node in payload["graph"]["nodes"]} == set(by_id)


def test_orders_keeps_the_order_text_verbatim(ui):
    _, orders = ui.client.json("/api/orders")
    _, show = ui.client.json("/api/show/eng-001")
    recorded = next(o for o in orders["orders"] if o["id"] == "eng-001")["record"]["order"]
    assert recorded == show["task"]["order"]
    assert "## Order" in recorded and "### Checks" in recorded


def test_archive_holds_benched_bodies_and_dispatches(ui):
    status, payload = ui.client.json("/api/archive")
    assert status == 200
    assert set(payload) == {"root_abs", "ts", "items", "errors"}
    for item in payload["items"]:
        assert set(item) == {"id", "pod", "bench", "archive"}
        for entry in item["bench"] + item["archive"]:
            assert set(entry) == {"ts", "path", "digest"}
    assert any(not item["bench"] and not item["archive"] for item in payload["items"]), "the empty case"


def test_static_assets_are_served_with_their_types(ui):
    for name, prefix in (("app.js", "text/javascript"), ("style.css", "text/css")):
        response = ui.client.get(f"/static/{name}")
        assert response.status == 200
        assert response.headers["Content-Type"].startswith(prefix)
        assert response.body


@pytest.mark.parametrize(
    "name", ["index.html", "../data.py", "../../__init__.py", "nope.js", "sub/app.js", "server.py"]
)
def test_static_serves_nothing_else(ui, name):
    assert ui.client.get(f"/static/{name}").status == 404


@pytest.mark.parametrize("path", ["/nope", "/api/nope", "/api/", "/apixyz"])
def test_unknown_paths_are_404(ui, path):
    assert ui.client.get(path).status == 404


@pytest.mark.parametrize("path", ["/api/board", "/", "/nope"])
def test_post_goes_nowhere_but_wake(ui, path):
    assert ui.client.post(path, {"text": "x"}).status == 404
    assert ui.source.woken == []


def test_responses_are_not_cached(ui):
    assert ui.client.get("/api/board").headers["Cache-Control"] == "no-store"
