"""The UI server (spec 16.1).

Python stdlib `http.server` on `127.0.0.1`, no build step and no CDN. Every
request but the static index carries the bearer token from `run/ui-token`,
either as `Authorization: Bearer <token>` (API clients, `hx` itself, curl) or as
the `hx_ui_token` cookie that `GET /` sets.

The cookie is what the browser uses: `<link>`, `<script>` and `EventSource`
cannot set a request header, and a token in a query string ends up in browser
history, in referrers and in every access log that records the URL. The cookie is
`HttpOnly`, so the page's own JavaScript cannot read the token either — nothing
in `static/` ever sees it.

The only write path in the whole server is `POST /api/partner/wake`, which calls
`Source.wake_partner` and nothing else. Everything else reads.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import secrets
import sys
import threading
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

from hx.ui.data import (
    WAKE_ACCEPTED,
    FixtureSource,
    InstanceSource,
    NotFound,
    Source,
    SourceError,
    SourceUnavailable,
)

LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
MAX_BODY_BYTES = 1_000_000
#: Set by `GET /`; the browser's only way of carrying the token.
TOKEN_COOKIE = "hx_ui_token"
#: Spec 16.1: the mtime sweep runs once a second.
SCAN_INTERVAL = 1.0
#: Scans between SSE comment frames, so a proxy or a dead peer is noticed without
#: putting a timeout on the wait itself.
HEARTBEAT_SCANS = 15

STATIC = Path(__file__).parent / "static"
CONTENT_TYPES = {".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8"}


def instance_port(root: Path) -> int:
    """`config/ui.json` `{"port": …}`, else the default."""
    try:
        config = json.loads((root / "config" / "ui.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return DEFAULT_PORT
    port = config.get("port") if isinstance(config, dict) else None
    return port if isinstance(port, int) and 0 < port < 65536 else DEFAULT_PORT


def instance_token(root: Path) -> str:
    """The bearer token from `run/ui-token`, created with mode 0600 if missing."""
    path = root / "run" / "ui-token"
    try:
        existing = path.read_text(encoding="utf-8").strip()
    except OSError:
        existing = ""
    if existing:
        return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    handle = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(token + "\n")
    os.chmod(path, 0o600)
    return token


class Watcher(threading.Thread):
    """One mtime sweep for every browser: scan, diff, push the scopes that moved."""

    def __init__(self, source: Source, interval: float = SCAN_INTERVAL) -> None:
        super().__init__(daemon=True, name="hx-ui-watcher")
        self.source = source
        self.interval = interval
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._clients: list[queue.Queue[dict[str, Any] | None]] = []
        self._seen: dict[str, float] = {}

    def subscribe(self) -> queue.Queue[dict[str, Any] | None]:
        client: queue.Queue[dict[str, Any] | None] = queue.Queue()
        with self._lock:
            self._clients.append(client)
        return client

    def unsubscribe(self, client: queue.Queue[dict[str, Any] | None]) -> None:
        with self._lock:
            if client in self._clients:
                self._clients.remove(client)

    def _publish(self, event: dict[str, Any] | None) -> None:
        with self._lock:
            clients = list(self._clients)
        for client in clients:
            client.put(event)

    def stop(self) -> None:
        self._stop.set()
        self._publish(None)

    def _changed(self) -> list[str]:
        try:
            current = self.source.scan()
        except Exception:  # a half-written instance must not kill the sweep
            return []
        previous, self._seen = self._seen, current
        if not previous:
            return []
        return sorted(
            scope for scope, mtime in current.items() if mtime > previous.get(scope, 0.0)
        ) + sorted(scope for scope in previous if scope not in current)

    def run(self) -> None:
        try:
            self._seen = self.source.scan()
        except Exception:
            self._seen = {}
        ticks = 0
        while not self._stop.wait(self.interval):
            changed = self._changed()
            ticks += 1
            if changed:
                self._publish({"changed": changed})
                ticks = 0
            elif ticks >= HEARTBEAT_SCANS:
                self._publish({})
                ticks = 0


class UIServer(ThreadingHTTPServer):
    """`ThreadingHTTPServer` that does not treat a closed browser tab as a fault."""

    daemon_threads = True
    allow_reuse_address = True

    def handle_error(self, request, client_address) -> None:
        exception = sys.exception()
        if isinstance(exception, (BrokenPipeError, ConnectionResetError, TimeoutError)):
            return  # the peer went away between requests; nothing to report
        super().handle_error(request, client_address)


class UIHandler(BaseHTTPRequestHandler):
    """Bound per server by `build_server`."""

    server_version = "hx-ui"
    protocol_version = "HTTP/1.1"  # every response carries Content-Length; SSE closes
    source: Source
    token: str
    watcher: Watcher

    # -- plumbing ----------------------------------------------------------
    def _send(
        self, status: HTTPStatus, body: bytes, content_type: str, extra: list[tuple[str, str]] | None = None
    ) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in extra or ():
                self.send_header(name, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            # The browser navigated away, or is still uploading a body we refused.
            self.close_connection = True

    def _json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        self._send(status, json.dumps(payload, indent=2, sort_keys=True).encode(), "application/json")

    def _error(self, status: HTTPStatus, message: str) -> None:
        self._json(status, {"error": message})

    def _cookie_token(self) -> str | None:
        jar = SimpleCookie()
        for header in self.headers.get_all("Cookie") or ():
            try:
                jar.load(header)
            except CookieError:
                continue
        morsel = jar.get(TOKEN_COOKIE)
        return morsel.value if morsel else None

    def _offered_token(self) -> str | None:
        header = self.headers.get("Authorization", "")
        if header.startswith("Bearer "):
            return header[len("Bearer ") :]
        return self._cookie_token()

    def _authorized(self) -> bool:
        offered = self._offered_token()
        if offered is not None and secrets.compare_digest(offered, self.token):
            return True
        self._error(HTTPStatus.UNAUTHORIZED, "invalid or missing hx ui token")
        return False

    def _drain(self) -> bytes:
        """Read the whole request body before answering.

        Every POST path does this first, refusal included: on a keep-alive
        connection an undrained body is parsed as the next request line.
        """
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except ValueError:
            length = -1
        if length < 0:
            self.close_connection = True
            raise ValueError("Content-Length is not a number")
        if length > MAX_BODY_BYTES:
            self.close_connection = True  # too large to drain; end the connection
            raise ValueError("request body is too large")
        return self.rfile.read(length) if length else b""

    @staticmethod
    def _as_object(raw: bytes) -> dict[str, Any]:
        if not raw:
            return {}
        value = json.loads(raw)
        if not isinstance(value, dict):
            raise TypeError("request body must be a JSON object")
        return value

    def _from_source(self, read) -> None:
        try:
            self._json(HTTPStatus.OK, read())
        except NotFound as exc:
            self._error(HTTPStatus.NOT_FOUND, str(exc))
        except SourceUnavailable as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
        except SourceError as exc:
            self._error(HTTPStatus.BAD_GATEWAY, str(exc))

    # -- routes ------------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        path = urlparse(self.path).path
        if path == "/" or path == "/index.html":
            self._index()
            return
        if path.startswith("/static/"):
            if self._authorized():
                self._static(path[len("/static/") :])
            return
        if not path.startswith("/api/"):
            self._error(HTTPStatus.NOT_FOUND, f"no such path: {path}")
            return
        if not self._authorized():
            return
        if path == "/api/board":
            self._from_source(self.source.board)
        elif path == "/api/orders":
            self._from_source(self.source.orders)
        elif path == "/api/archive":
            self._from_source(self.source.archive)
        elif path.startswith("/api/show/"):
            agent_id = path[len("/api/show/") :]
            self._from_source(lambda: self.source.show(agent_id))
        elif path == "/api/events":
            self._events()
        else:
            self._error(HTTPStatus.NOT_FOUND, f"no such path: {path}")

    def do_HEAD(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        self.do_GET()

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        try:
            raw = self._drain()
        except ValueError as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        path = urlparse(self.path).path
        if path != "/api/partner/wake":
            self._error(HTTPStatus.NOT_FOUND, f"no such path: {path}")
            return
        if not self._authorized():
            return
        try:
            text = self._as_object(raw).get("text")
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            self._error(HTTPStatus.BAD_REQUEST, str(exc))
            return
        if not isinstance(text, str) or not text.strip():
            self._error(HTTPStatus.BAD_REQUEST, "text must be a non-empty string")
            return
        try:
            status = self.source.wake_partner_status(text)
        except SourceUnavailable as exc:
            self._error(HTTPStatus.SERVICE_UNAVAILABLE, str(exc))
            return
        except SourceError as exc:
            self._error(HTTPStatus.BAD_GATEWAY, str(exc))
            return
        # `status` tells a Partner that has never started (`no-socket`) from one
        # that is not listening (`refused`); `delivered` stays the plain answer.
        self._json(HTTPStatus.OK, {"delivered": status == WAKE_ACCEPTED, "status": status})

    # -- handlers ----------------------------------------------------------
    def _index(self) -> None:
        """The one unauthenticated response. It sets the cookie every other request needs.

        The token is never written into the page: the cookie is `HttpOnly`, so
        `static/app.js` cannot read it and no view has to carry it in a URL.
        """
        try:
            template = (STATIC / "index.html").read_text(encoding="utf-8")
        except OSError as exc:
            self._error(HTTPStatus.INTERNAL_SERVER_ERROR, f"cannot read the index: {exc}")
            return
        cookie = (
            f"{TOKEN_COOKIE}={quote(self.token, safe='')}; HttpOnly; SameSite=Strict; Path=/"
        )  # no Secure: this server is http on 127.0.0.1 and a Secure cookie would never be sent
        self._send(
            HTTPStatus.OK, template.encode(), "text/html; charset=utf-8", [("Set-Cookie", cookie)]
        )

    def _static(self, name: str) -> None:
        target = (STATIC / name).resolve()
        if target.parent != STATIC.resolve() or not target.is_file() or name == "index.html":
            self._error(HTTPStatus.NOT_FOUND, f"no such asset: {name}")
            return
        content_type = CONTENT_TYPES.get(target.suffix)
        if content_type is None:
            self._error(HTTPStatus.NOT_FOUND, f"no such asset: {name}")
            return
        self._send(HTTPStatus.OK, target.read_bytes(), content_type)

    def _events(self) -> None:
        client = self.watcher.subscribe()
        try:
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(b": hx ui events\n\n")
            self.wfile.flush()
            while True:
                event = client.get()
                if event is None:
                    return
                frame = (
                    b": tick\n\n"
                    if not event
                    else b"data: " + json.dumps(event, sort_keys=True).encode() + b"\n\n"
                )
                self.wfile.write(frame)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError, ValueError):
            return
        finally:
            self.watcher.unsubscribe(client)

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"hx ui: {self.address_string()} - {fmt % args}", flush=True)


def build_server(
    source: Source,
    token: str,
    *,
    host: str = LOOPBACK_HOST,
    port: int = DEFAULT_PORT,
    interval: float = SCAN_INTERVAL,
) -> UIServer:
    """A started watcher and a bound, not yet serving, HTTP server."""
    watcher = Watcher(source, interval=interval)
    handler = type("BoundUIHandler", (UIHandler,), {"source": source, "token": token, "watcher": watcher})
    server = UIServer((host, port), handler)
    server.hx_watcher = watcher  # type: ignore[attr-defined]
    server.hx_source = source  # type: ignore[attr-defined]
    server.hx_token = token  # type: ignore[attr-defined]
    watcher.start()
    return server


def _run(server: UIServer, token: str, where: str) -> None:
    host, port = server.server_address[:2]
    print(f"hx ui ({where}): http://{host}:{port}/", flush=True)
    print(f"hx ui token: {token}", flush=True)
    try:
        server.serve_forever()
    finally:
        server.hx_watcher.stop()  # type: ignore[attr-defined]
        server.server_close()


def serve(root: Path | str, port: int | None = None, *, host: str = LOOPBACK_HOST) -> None:
    """Serve one instance. This is what the `hx ui` CLI entry calls.

    `port` is positional so `hx ui` can pass an override straight through;
    omitted, it comes from `config/ui.json`, then the 8765 default.
    """
    root = Path(root)
    token = instance_token(root)
    server = build_server(
        InstanceSource(root), token, host=host, port=instance_port(root) if port is None else port
    )
    _run(server, token, str(root))


def serve_fixtures(directory: Path | str, *, port: int = DEFAULT_PORT, host: str = LOOPBACK_HOST) -> None:
    """Serve a fixture tree. The token is ephemeral: nothing under it is written."""
    token = secrets.token_urlsafe(32)
    server = build_server(FixtureSource(directory), token, host=host, port=port)
    _run(server, token, f"fixtures {directory}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m hx.ui", description="the hx UI (spec 16)")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--root", help="HARNESS_ROOT of a live instance")
    group.add_argument("--fixtures", help="a directory of CONTRACTS.md fixtures")
    parser.add_argument("--port", type=int, default=None, help=f"default {DEFAULT_PORT}, or config/ui.json")
    parser.add_argument("--host", default=LOOPBACK_HOST, help=f"default {LOOPBACK_HOST}")
    args = parser.parse_args(argv)
    try:
        if args.root:
            serve(args.root, port=args.port, host=args.host)
        else:
            serve_fixtures(args.fixtures, port=DEFAULT_PORT if args.port is None else args.port, host=args.host)
    except KeyboardInterrupt:
        return 0
    except OSError as exc:
        print(f"hx ui: {exc}", file=sys.stderr, flush=True)
        return 1
    return 0
