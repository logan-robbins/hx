"""Every request but the static index carries the bearer token (spec 16.1)."""

from __future__ import annotations

import json

import pytest

# Everything the server answers, other than `GET /`.
GUARDED_GET = [
    "/api/board",
    "/api/orders",
    "/api/archive",
    "/api/show/eng-001",
    "/api/show/partner",
    "/api/events",
    "/static/app.js",
    "/static/style.css",
]


@pytest.mark.parametrize("path", GUARDED_GET)
def test_guarded_get_needs_the_token(ui, path):
    response = ui.client.get(path, token=None)
    assert response.status == 401
    assert "token" in json.loads(response.body)["error"]


@pytest.mark.parametrize("path", GUARDED_GET)
def test_guarded_get_with_the_token(ui, path):
    assert ui.client.get(path if path != "/api/events" else "/api/board").status == 200


@pytest.mark.parametrize("path", GUARDED_GET)
def test_query_token_is_accepted(ui, path):
    """`<link>`, `<script>` and `EventSource` cannot set a header."""
    if path == "/api/events":
        pytest.skip("covered by the SSE tests, which must not block here")
    assert ui.client.get(path, query_token=True).status == 200


def test_wrong_token_is_rejected(ui):
    assert ui.client.get("/api/board", token="wrong").status == 401
    assert ui.client.get("/api/board", token="wrong", query_token=True).status == 401


def test_wake_needs_the_token(ui):
    response = ui.client.post("/api/partner/wake", {"text": "hello"}, token=None)
    assert response.status == 401
    assert ui.source.woken == []


def test_index_is_the_one_unauthenticated_response(ui):
    response = ui.client.get("/", token=None)
    assert response.status == 200
    assert response.headers["Content-Type"].startswith("text/html")
    body = response.body.decode()
    assert "__BOOTSTRAP__" not in body and "__TOKEN__" not in body
    assert ui.token in body, "the index carries the token to the page"


def test_index_html_is_also_the_index(ui):
    assert ui.client.get("/index.html", token=None).status == 200


def test_index_bootstrap_parses_and_holds_the_token(ui):
    body = ui.client.get("/", token=None).body.decode()
    start = body.index('<script id="bootstrap" type="application/json">') + len(
        '<script id="bootstrap" type="application/json">'
    )
    bootstrap = json.loads(body[start : body.index("</script>", start)])
    assert bootstrap == {"token": ui.token}
