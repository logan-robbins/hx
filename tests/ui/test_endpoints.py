"""What each endpoint answers, against fixtures conforming to CONTRACTS.md."""

from __future__ import annotations

import json

import pytest


def test_board_matches_the_contract(ui):
    """v1 cut: no `errors`, no `after`/`ready`/`goal_pending`, no `partner` row."""
    status, payload = ui.client.json("/api/board")
    assert status == 200
    assert set(payload) == {"root_abs", "ts", "items", "memory"}
    ids = [item["id"] for item in payload["items"]]
    assert ids == sorted(ids), "by id"
    assert "partner" not in ids, "the Partner has no work item"
    for item in payload["items"]:
        assert set(item) == {
            "id", "pod", "role", "state", "file", "outcome", "dispatched", "completed",
            "open_subagents", "goal_ts", "session_alive", "context_tokens", "seams", "turn_ts",
    "companion_pass", "companion_ts",
        }
        assert item["state"] in {"idle", "working", "complete"}
        assert item["outcome"] in {None, "done", "blocked", "decision", "exhausted"}


def test_board_carries_the_cases_the_views_must_render(ui):
    _, payload = ui.client.json("/api/board")
    items = {item["id"]: item for item in payload["items"]}
    assert any(i["state"] == "complete" and i["outcome"] == "decision" for i in items.values())
    assert any(i["state"] == "idle" for i in items.values())
    assert any(i["session_alive"] is False for i in items.values()), (
        "a dead session is now only visible in its column; there are no invariant errors"
    )
    assert any(i["seams"] is None for i in items.values()), "an id with no stream reads null"


def test_show_partner_is_the_reduced_shape(ui):
    """v1 cut: the Partner has no work item, task or step state."""
    status, payload = ui.client.json("/api/show/partner")
    assert status == 200
    assert set(payload) == {"id", "partner_md", "pane", "streams", "companion", "compactions"}


@pytest.mark.parametrize("agent_id", ["eng-001"])
def test_show_matches_the_contract(ui, agent_id):
    status, payload = ui.client.json(f"/api/show/{agent_id}")
    assert status == 200
    assert payload["id"] == agent_id
    for key in (
        "pod", "role", "state", "file", "work_item", "task", "persona_path", "step_state",
        "context_file", "streams", "subagents", "metrics", "pane", "archive", "bench",
        "compactions",
    ):
        assert key in payload, key
    assert set(payload["work_item"]) == {"frontmatter", "body"}
    assert set(payload["task"]) == {"order", "addenda", "outcome", "dispatched", "completed"}
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


def test_orders_is_the_v1_shape(ui):
    """One entry per `tasks.json` id: no graph, no file comparison."""
    status, payload = ui.client.json("/api/orders")
    assert status == 200
    assert set(payload) == {"root_abs", "ts", "orders"}
    by_id = {entry["id"]: entry for entry in payload["orders"]}
    assert by_id, "the fixture has dispatched orders"
    for entry in payload["orders"]:
        assert set(entry) == {
            "id", "pod", "state", "outcome", "order", "addenda", "dispatched", "completed",
        }


def test_orders_keeps_the_order_text_verbatim(ui):
    _, orders = ui.client.json("/api/orders")
    _, show = ui.client.json("/api/show/eng-001")
    recorded = next(o for o in orders["orders"] if o["id"] == "eng-001")["order"]
    assert recorded == show["task"]["order"]
    assert "## Order" in recorded and "### Checks" in recorded


def test_archive_holds_benched_bodies_and_dispatches(ui):
    status, payload = ui.client.json("/api/archive")
    assert status == 200
    assert set(payload) == {"root_abs", "ts", "items"}
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
