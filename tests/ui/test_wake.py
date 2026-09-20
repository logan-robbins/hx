"""`POST /api/partner/wake` is the UI's only write path (spec 16)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hx.ui.data import FixtureSource

from .conftest import FIXTURES, manifest


class SpySource(FixtureSource):
    """Records every call, so "exactly once, and nothing else" is checkable."""

    def __init__(self, directory: Path) -> None:
        super().__init__(directory)
        self.calls: list[tuple[str, tuple]] = []

    def board(self):
        self.calls.append(("board", ()))
        return super().board()

    def show(self, agent_id):
        self.calls.append(("show", (agent_id,)))
        return super().show(agent_id)

    def orders(self):
        self.calls.append(("orders", ()))
        return super().orders()

    def archive(self):
        self.calls.append(("archive", ()))
        return super().archive()

    def wake_partner(self, text):
        self.calls.append(("wake_partner", (text,)))
        return super().wake_partner(text)

    def wake_partner_status(self, text):
        self.calls.append(("wake_partner_status", (text,)))
        return super().wake_partner_status(text)


@pytest.fixture
def spy_ui():
    from .conftest import _serve

    yield from _serve(SpySource(FIXTURES))


def test_wake_calls_the_source_exactly_once_with_the_text(spy_ui):
    before = manifest(FIXTURES)
    text = "eng-003 complete: decision; hx read eng-003"
    response = spy_ui.client.post("/api/partner/wake", {"text": text})
    assert response.status == 200
    assert json.loads(response.body) == {"delivered": True, "status": "accepted"}
    assert spy_ui.source.calls == [("wake_partner_status", (text,))], (
        "one call, and no read alongside it"
    )
    assert spy_ui.source.woken == [text]
    assert manifest(FIXTURES) == before, "the wake wrote nothing to the instance"


def test_wake_reports_a_refused_socket(spy_ui):
    spy_ui.source.wake_accepts = False
    response = spy_ui.client.post("/api/partner/wake", {"text": "anyone home"})
    assert response.status == 200
    assert json.loads(response.body) == {"delivered": False, "status": "refused"}
    assert spy_ui.source.woken == ["anyone home"]


@pytest.mark.parametrize("status", ["accepted", "no-socket", "refused"])
def test_wake_passes_each_contract_status_through(spy_ui, status):
    """CONTRACTS.md has three answers, and the page shows which one it was."""
    spy_ui.source.wake_status = status
    response = spy_ui.client.post("/api/partner/wake", {"text": "hello"})
    assert response.status == 200
    assert json.loads(response.body) == {"delivered": status == "accepted", "status": status}


@pytest.mark.parametrize(
    "body", [{}, {"text": ""}, {"text": "   "}, {"text": None}, {"text": 7}, {"text": ["a"]}]
)
def test_wake_refuses_a_body_without_text(spy_ui, body):
    response = spy_ui.client.post("/api/partner/wake", body)
    assert response.status == 400
    assert spy_ui.source.calls == []


@pytest.mark.parametrize("raw", [b"not json", b"[1,2]", b'"text"'])
def test_wake_refuses_a_body_that_is_not_a_json_object(spy_ui, raw):
    response = spy_ui.client.post("/api/partner/wake", raw)
    assert response.status == 400
    assert spy_ui.source.calls == []


def test_wake_keeps_the_text_verbatim(spy_ui):
    text = 'newline\nquote " backslash \\ unicode ✓ and a trailing space '
    assert spy_ui.client.post("/api/partner/wake", {"text": text}).status == 200
    assert spy_ui.source.woken == [text]


def test_an_oversized_body_is_refused_and_not_delivered(spy_ui):
    """Over the cap the server answers 400 and ends the connection rather than
    draining megabytes, so the client may see the refusal or a broken pipe.
    Either way nothing reaches the Partner."""
    try:
        assert spy_ui.client.post("/api/partner/wake", b"x" * (1_000_001)).status == 400
    except (BrokenPipeError, ConnectionResetError):
        pass
    assert spy_ui.source.calls == []


def test_a_refused_post_leaves_the_connection_usable(spy_ui):
    """A 404 POST must drain its body, or the next keep-alive request misparses."""
    import http.client

    connection = http.client.HTTPConnection(
        spy_ui.client.host, spy_ui.client.port, timeout=10
    )
    try:
        headers = {"Authorization": f"Bearer {spy_ui.token}", "Content-Type": "application/json"}
        connection.request("POST", "/api/nope", body=json.dumps({"text": "x"}).encode(), headers=headers)
        assert connection.getresponse().read() is not None
        connection.request("GET", "/api/board", headers=headers)
        assert connection.getresponse().status == 200
    finally:
        connection.close()


def test_no_endpoint_touches_the_instance(spy_ui):
    """Every read path, then the manifest again."""
    before = manifest(FIXTURES)
    for path in ("/api/board", "/api/orders", "/api/archive", "/api/show/partner", "/api/show/eng-001", "/"):
        spy_ui.client.get(path)
    assert manifest(FIXTURES) == before
