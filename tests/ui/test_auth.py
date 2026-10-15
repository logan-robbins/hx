"""Every request but the static index carries the bearer token (spec 16.1)."""

from __future__ import annotations

import json

import pytest

from hx.ui.server import TOKEN_COOKIE, token_cookie_name

# Everything the server answers, other than `GET /`.
GUARDED_GET = [
    "/api/board",
    "/api/goals",
        "/api/show/be-001",
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
def test_the_cookie_is_accepted(ui, path):
    """What a browser actually sends: `<link>`, `<script>` and `EventSource`
    cannot set a header, so the cookie `GET /` set is the only thing they carry."""
    if path == "/api/events":
        pytest.skip("covered by the SSE tests, which must not block here")
    assert ui.client.get(path, as_cookie=True).status == 200


@pytest.mark.parametrize("path", GUARDED_GET)
def test_the_query_token_is_no_longer_accepted(ui, path):
    """A token in a URL lands in browser history, referrers and access logs."""
    response = ui.client.request("GET", f"{path}?token={ui.token}", token=None)
    assert response.status == 401


def test_wrong_token_is_rejected(ui):
    assert ui.client.get("/api/board", token="wrong").status == 401
    assert ui.client.get("/api/board", token="wrong", as_cookie=True).status == 401


def test_a_malformed_cookie_header_does_not_crash_the_server(ui):
    response = ui.client.get("/api/board", token=None, headers={"Cookie": "=;;; broken"})
    assert response.status == 401
    assert ui.client.get("/api/board").status == 200, "the server is still serving"


def test_wake_needs_the_token(ui):
    response = ui.client.post("/api/partner/wake", {"text": "hello"}, token=None)
    assert response.status == 401
    assert ui.source.woken == []


def test_index_is_the_one_unauthenticated_response(ui):
    response = ui.client.get("/", token=None)
    assert response.status == 200
    assert response.headers["Content-Type"].startswith("text/html")


def test_index_html_is_also_the_index(ui):
    assert ui.client.get("/index.html", token=None).status == 200


def test_the_index_sets_the_token_cookie(ui):
    """`GET /` is how the browser gets the token, and the only way it does."""
    header = ui.client.get("/", token=None).headers["Set-Cookie"]
    assert header.startswith(f"{token_cookie_name(ui.client.port)}={ui.token}")
    assert "HttpOnly" in header, "the page's own JavaScript must not be able to read it"
    assert "SameSite=Strict" in header
    assert "Path=/" in header
    assert "Secure" not in header, "this server is http on 127.0.0.1; a Secure cookie is never sent"


def test_the_token_never_reaches_the_page(ui):
    """Nothing in what the browser can read carries the token."""
    for path in ("/", "/static/app.js", "/static/style.css"):
        body = ui.client.get(path).body.decode()
        assert ui.token not in body, path
        assert "token=" not in body, f"{path} puts a token in a URL"


def test_two_instances_on_one_host_keep_separate_cookies(ui, second_ui):
    """Browsers scope cookies by host, never by port (RFC 6265 §8.5), so a fleet of
    instances on 127.0.0.1 shares one jar. Each server names its cookie after its own
    port and reads only that one: a jar holding both is accepted by both, the other
    server's cookie alone is not, and the old fixed name is not either."""
    mine = f"{token_cookie_name(ui.client.port)}={ui.token}"
    theirs = f"{token_cookie_name(second_ui.client.port)}={second_ui.token}"
    assert ui.client.port != second_ui.client.port
    both = {"Cookie": f"{theirs}; {mine}"}
    assert ui.client.get("/api/board", token=None, headers=both).status == 200
    assert second_ui.client.get("/api/board", token=None, headers=both).status == 200
    assert ui.client.get("/api/board", token=None, headers={"Cookie": theirs}).status == 401
    assert second_ui.client.get("/api/board", token=None, headers={"Cookie": mine}).status == 401
    legacy = {"Cookie": f"{TOKEN_COOKIE}={ui.token}"}
    assert ui.client.get("/api/board", token=None, headers=legacy).status == 401
    header = second_ui.client.get("/", token=None).headers["Set-Cookie"]
    assert header.startswith(f"{token_cookie_name(second_ui.client.port)}=")
