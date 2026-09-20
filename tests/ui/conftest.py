"""Shared fixtures for the UI tests.

The whole suite runs against `tests/ui/fixtures/`, and the session-scoped
`fixtures_untouched` check is the standing assertion that the UI observes and
never operates: the tree's manifest — contents, mode and mtime of every file —
must be byte-identical after the last test. A test that needs to touch a file
(the SSE sweep) copies the tree into `tmp_path` first.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from hx.ui.data import FixtureSource
from hx.ui.server import build_server

FIXTURES = Path(__file__).parent / "fixtures"
TOKEN = "test-token-not-a-real-one"
#: Short enough that "within 1 s" is a real assertion rather than a sleep.
TEST_INTERVAL = 0.05


def manifest(root: Path) -> dict[str, tuple[int, int, int, str]]:
    """Every file under `root`: size, mode, mtime and content hash."""
    table = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        stat = path.stat()
        table[str(path.relative_to(root))] = (
            stat.st_size,
            stat.st_mode & 0o777,
            stat.st_mtime_ns,
            hashlib.sha256(path.read_bytes()).hexdigest(),
        )
    return table


@pytest.fixture(scope="session", autouse=True)
def fixtures_untouched():
    """No endpoint mutates the instance: the fixture tree is identical afterwards."""
    before = manifest(FIXTURES)
    assert before, f"no fixtures under {FIXTURES}"
    yield
    after = manifest(FIXTURES)
    changed = sorted(
        name for name in set(before) | set(after) if before.get(name) != after.get(name)
    )
    assert not changed, f"the UI test run modified the fixture tree: {changed}"


class Client:
    """A tiny stdlib HTTP client, so the tests carry no dependency either."""

    def __init__(self, host: str, port: int, token: str) -> None:
        self.host, self.port, self.token = host, port, token

    @property
    def origin(self) -> str:
        return f"http://{self.host}:{self.port}"

    def request(self, method, path, *, token=None, query_token=False, body=None, headers=None):
        if query_token and token:
            path += ("&" if "?" in path else "?") + f"token={token}"
        sent = dict(headers or {})
        if token and not query_token:
            sent["Authorization"] = f"Bearer {token}"
        payload = None
        if body is not None:
            payload = body if isinstance(body, bytes) else json.dumps(body).encode()
            sent.setdefault("Content-Type", "application/json")
        connection = http.client.HTTPConnection(self.host, self.port, timeout=10)
        try:
            connection.request(method, path, body=payload, headers=sent)
            response = connection.getresponse()
            return SimpleNamespace(
                status=response.status,
                headers=dict(response.getheaders()),
                body=response.read(),
            )
        finally:
            connection.close()

    def get(self, path, *, token="__default__", **kwargs):
        return self.request("GET", path, token=self.token if token == "__default__" else token, **kwargs)

    def post(self, path, body, *, token="__default__", **kwargs):
        return self.request(
            "POST", path, token=self.token if token == "__default__" else token, body=body, **kwargs
        )

    def json(self, path, **kwargs):
        response = self.get(path, **kwargs)
        return response.status, json.loads(response.body)

    def stream(self, path, *, token=None, timeout=10):
        """Open an SSE connection and hand back the raw file object."""
        if token:
            path += ("&" if "?" in path else "?") + f"token={token}"
        connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        connection.request("GET", path)
        return connection, connection.getresponse()


def _serve(source, token=TOKEN, interval=TEST_INTERVAL):
    server = build_server(source, token, host="127.0.0.1", port=0, interval=interval)
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    handle = SimpleNamespace(
        server=server,
        source=source,
        token=token,
        client=Client(host, port, token),
        url=f"http://{host}:{port}",
    )
    try:
        yield handle
    finally:
        server.shutdown()
        server.hx_watcher.stop()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture
def ui():
    """A server over the shipped fixture tree."""
    yield from _serve(FixtureSource(FIXTURES))


@pytest.fixture
def scratch_tree(tmp_path):
    """A writable copy of the fixture tree, for the tests that touch files."""
    target = tmp_path / "fixtures"
    target.mkdir()
    for path in FIXTURES.iterdir():
        if path.is_file():
            (target / path.name).write_bytes(path.read_bytes())
    return target


@pytest.fixture
def scratch_ui(scratch_tree):
    """A server over the writable copy."""
    yield from _serve(FixtureSource(scratch_tree))
