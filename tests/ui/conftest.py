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
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from hx.ui.data import FixtureSource
from hx.ui.server import TOKEN_COOKIE, build_server

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

    def request(self, method, path, *, token=None, as_cookie=False, body=None, headers=None):
        sent = dict(headers or {})
        if token:
            # The browser carries the token in the HttpOnly cookie GET / sets;
            # API clients use the header. Both are accepted, `?token=` is not.
            if as_cookie:
                sent["Cookie"] = f"{TOKEN_COOKIE}={token}"
            else:
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

    def stream(self, path, *, token=None, as_cookie=True, timeout=10):
        """Open an SSE connection and hand back the response to read frames from.

        `as_cookie` by default because that is what a browser's `EventSource`
        does: it cannot set a header, and the token is no longer in the URL.
        """
        headers = {}
        if token:
            headers = {"Cookie": f"{TOKEN_COOKIE}={token}"} if as_cookie else {"Authorization": f"Bearer {token}"}
        connection = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        connection.request("GET", path, headers=headers)
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


# -- a live instance -----------------------------------------------------

HX = Path(sys.executable).parent / "hx"

ORDER = """## Order
Stand in for a dispatched order while the ui lane runs against a real instance.

## Definition of done
- The board renders this item.

### Checks
```bash
true
```
"""

WORK_ITEM = """---
id: {id}
pod: {pod}
after: {after}
outcome:
dispatched: 2026-09-20T12:00:00Z
---
## Order
Stand in for a dispatched order while the ui lane runs against a real instance.

## Definition of done
- The board renders this item.

### Checks
```bash
true
```

## Tasks
- [x] Exist on the board.
- [ ] Be opened in the Agent view.

## Deliverables

## Commands

## Open decision

## Digest
"""


def build_instance(root: Path) -> Path:
    """A real HARNESS_ROOT: `hx install --skeleton-only`, then hand-made items.

    The skeleton gives `config/partner/` and the directory layout; the work items
    and `tasks.json` are written here because `hx dispatch` is build-2 and does
    not exist yet.
    """
    subprocess.run(
        [str(HX), "install", "--skeleton-only", "--root", str(root)],
        capture_output=True, text=True, check=True,
    )
    worker_template = root / "templates" / "worker"
    for agent_id, pod, after in (("partner", "partner", "[]"), ("eng-001", "engineers", "[]")):
        config = root / "config" / agent_id
        config.mkdir(parents=True, exist_ok=True)
        for name in ("AGENTS.md", "SUBAGENTS.md", "harness.json"):
            target = config / name
            if not target.exists():
                # The skeleton ships templates; `hx launch` renders them. It is
                # build-2, so render the two placeholders here.
                rendered = (
                    (worker_template / name)
                    .read_text(encoding="utf-8")
                    .replace("{{id}}", agent_id)
                    .replace("{{pod}}", pod)
                )
                target.write_text(rendered, encoding="utf-8")
        pod_dir = root / "pods" / pod
        pod_dir.mkdir(parents=True, exist_ok=True)
        (pod_dir / f"{agent_id}-working.md").write_text(
            WORK_ITEM.format(id=agent_id, pod=pod, after=after), encoding="utf-8"
        )
    # Orders, and the tasks.json records they produced. `partner`'s file still
    # matches what was dispatched; `eng-001`'s was edited afterwards, so the
    # Orders view has both `file_matches_record` cases against real data.
    orders = root / "orders"
    orders.mkdir(parents=True, exist_ok=True)
    (orders / "partner.md").write_text(ORDER, encoding="utf-8")
    (orders / "eng-001.md").write_text(
        ORDER + "\n## Order addendum, typed into the file after dispatch\nRescope to the board only.\n",
        encoding="utf-8",
    )
    (root / "tasks.json").write_text(
        json.dumps(
            {
                agent_id: {
                    "order": ORDER,
                    "after": [],
                    "addenda": [],
                    "outcome": None,
                    "dispatched": "2026-09-20T12:00:00Z",
                    "completed": None,
                }
                for agent_id in ("partner", "eng-001")
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return root


@pytest.fixture(scope="session")
def instance_root(tmp_path_factory):
    """One real instance for the whole session.

    Built once, and checked on teardown: the UI writes nothing under
    `HARNESS_ROOT` but `run/ui-token` (spec 16.1), so every other file must be
    byte-identical — contents, mode and mtime — after the last test that used it.
    """
    root = build_instance(tmp_path_factory.mktemp("hx-instance"))
    before = manifest(root)
    yield root
    after = manifest(root)
    allowed = {"run/ui-token"}
    changed = sorted(
        name
        for name in set(before) | set(after)
        if before.get(name) != after.get(name) and name not in allowed
    )
    assert not changed, f"the UI test run modified the instance: {changed}"
