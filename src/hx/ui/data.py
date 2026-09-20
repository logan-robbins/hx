"""Data access for the UI (spec 16.2).

The UI observes; it does not operate. Every view is one call on a `Source`:
`board()`, `show(id)`, `orders()` and `archive()` return the JSON documents that
`CONTRACTS.md` defines, and `wake_partner(text)` is the UI's only write path.

`scan()` is the other half of the interface: it returns `{scope: mtime}` for
everything spec 16.1 says the server sweeps once a second, and the server pushes
the scopes whose mtime moved. A scope is an agent id, or one of the view scopes
below for state that is not id-shaped.

`FixtureSource` reads hand-written JSON and is what ui-1 ships. `InstanceSource`
reads a live `HARNESS_ROOT`; its `scan()` is complete here because it is pure
filesystem, and its readers are plugged into the build lane's `hx board --json`,
`hx show --json` and `hx.wake.wake_partner` in ui-2.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from hx.ui import pane

#: `hx wake partner` always exits 0 and reports on stdout: `HX-WAKE partner
#: accepted` when the socket took the message, `… no-socket` when there was none.
WAKE_ACCEPTED = "HX-WAKE partner accepted"

# Scopes that are not an agent id. `TASKS` covers tasks.json, whose every write
# can change any row of the board.
SCOPE_BOARD = "board"
SCOPE_ORDERS = "orders"
SCOPE_ARCHIVE = "archive"
SCOPE_TASKS = "tasks"
VIEW_SCOPES = (SCOPE_BOARD, SCOPE_ORDERS, SCOPE_ARCHIVE, SCOPE_TASKS)

# Work items, orders and bench files are all named `<id>-…` or `<id>.…`; spec 06.
ID_RE = re.compile(r"^(partner|[a-z]+-[0-9]{3})(?=[-.]|$)")


class SourceError(Exception):
    """The source cannot answer. Rendered as HTTP 502."""


class SourceUnavailable(SourceError):
    """The source is not wired up yet. Rendered as HTTP 503."""


class NotFound(SourceError):
    """No such id. Rendered as HTTP 404."""


def id_of(name: str) -> str | None:
    """The agent id a file or directory name belongs to, or None."""
    match = ID_RE.match(name)
    return match.group(1) if match else None


class Source:
    """What every view reads through. One instance per server."""

    def board(self) -> dict[str, Any]:
        """`hx board --json`; CONTRACTS.md."""
        raise NotImplementedError

    def show(self, agent_id: str) -> dict[str, Any]:
        """`hx show <id> --json`; CONTRACTS.md. Raises NotFound for an unknown id."""
        raise NotImplementedError

    def orders(self) -> dict[str, Any]:
        """Every order with the tasks.json record it produced, plus the after graph."""
        raise NotImplementedError

    def archive(self) -> dict[str, Any]:
        """Benched bodies and archived dispatches per id."""
        raise NotImplementedError

    def wake_partner(self, text: str) -> bool:
        """The UI's only write. True when the socket accepted the message."""
        raise NotImplementedError

    def scan(self) -> dict[str, float]:
        """`{scope: latest mtime}` over the paths spec 16.1 watches."""
        raise NotImplementedError


def _newest(table: dict[str, float], scope: str, mtime: float) -> None:
    if mtime > table.get(scope, 0.0):
        table[scope] = mtime


class FixtureSource(Source):
    """Hand-written JSON conforming to CONTRACTS.md, under one directory.

    `board.json`, `orders.json`, `archive.json` and `show-<id>.json`. Nothing is
    ever written: `wake_partner` records the text in memory so the fixture tree
    is provably untouched by any request.
    """

    def __init__(self, directory: Path | str) -> None:
        self.directory = Path(directory)
        self.woken: list[str] = []
        self.wake_accepts = True

    def _read(self, name: str) -> dict[str, Any]:
        path = self.directory / name
        try:
            text = path.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise NotFound(f"no fixture {name} in {self.directory}") from exc
        except OSError as exc:
            raise SourceError(f"cannot read {path}: {exc}") from exc
        try:
            value = json.loads(text)
        except json.JSONDecodeError as exc:
            raise SourceError(f"{path} is not valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise SourceError(f"{path} must hold a JSON object")
        return value

    def board(self) -> dict[str, Any]:
        return self._read("board.json")

    def show(self, agent_id: str) -> dict[str, Any]:
        if id_of(agent_id) != agent_id:
            raise NotFound(f"not an agent id: {agent_id!r}")
        return self._read(f"show-{agent_id}.json")

    def orders(self) -> dict[str, Any]:
        return self._read("orders.json")

    def archive(self) -> dict[str, Any]:
        return self._read("archive.json")

    def wake_partner(self, text: str) -> bool:
        self.woken.append(text)
        return self.wake_accepts

    def scan(self) -> dict[str, float]:
        table: dict[str, float] = {}
        for path in sorted(self.directory.rglob("*")):
            try:
                if not path.is_file():
                    continue
                mtime = path.stat().st_mtime
            except OSError:
                continue
            _newest(table, self._scope(path.name), mtime)
        return table

    @staticmethod
    def _scope(name: str) -> str:
        stem = name[: -len(".json")] if name.endswith(".json") else name
        if stem.startswith("show-"):
            return id_of(stem[len("show-") :]) or stem
        return stem if stem in VIEW_SCOPES else stem


class CommandError(SourceError):
    """`hx` ran and failed for a reason that is not "not implemented"."""


def _hx_binary() -> str:
    """The `hx` next to the running interpreter, else whatever is on PATH."""
    candidate = Path(sys.executable).parent / "hx"
    return str(candidate) if candidate.exists() else "hx"


def run_hx(
    root: Path, args: list[str], *, binary: str | None = None, document: bool = False
) -> str:
    """Run one `hx` command against `root` and return its stdout.

    This is the seam. When the build lane's `handoff/build-to-ui.md` names the
    Python functions, `InstanceSource` calls those instead and this goes away;
    nothing outside this module changes, because every reader goes through
    `Source`.

    `document=True` marks a command whose whole job is to print a JSON document
    and whose exit code reports the *instance*, not the call: `hx board --json`
    exits 1 whenever `errors` is non-empty, and that board still has to render.
    Every other command — `hx wake` above all — is judged on its exit code.
    """
    command = [binary or _hx_binary(), *args]
    environment = dict(os.environ, HARNESS_ROOT=str(root))
    environment.pop("HARNESS_ID", None)  # the UI is not an agent; spec 08
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False, env=environment)
    except OSError as exc:
        raise SourceUnavailable(f"cannot run {command[0]}: {exc}") from exc

    if result.returncode == 0:
        return result.stdout

    message = (result.stderr or result.stdout).strip() or f"{' '.join(args)} exited {result.returncode}"
    if "not implemented" in message:
        # hx names the build-lane goal that delivers it; pass that through verbatim.
        raise SourceUnavailable(message)
    if document and result.returncode == 1 and result.stdout.strip():
        return result.stdout
    raise CommandError(message)


class InstanceSource(Source):
    """A live `HARNESS_ROOT`, read through `hx` itself.

    Spec 16: the UI shows what `hx board --json` and `hx show <id> --json` show,
    "read through the same code", so this composes nothing of its own. The one
    exception is the pane, which spec 16.2 refreshes on the SSE tick and which
    `hx.ui.pane` captures directly.
    """

    #: Spec 16.1: the mtimes swept once a second. `recursive` walks the tree.
    WATCHED = (
        ("tasks.json", False),
        ("pods", True),
        ("orders", True),
        ("state", True),
        ("logs", True),
    )
    #: run/<id>/turn and run/<id>/goal, one level under run/.
    RUN_MARKERS = ("turn", "goal")

    def __init__(self, root: Path | str, *, binary: str | None = None, tmux_socket: str | None = None) -> None:
        self.root = Path(root)
        self.binary = binary
        self.tmux_socket = tmux_socket

    # -- readers ---------------------------------------------------------
    def _json(self, args: list[str]) -> dict[str, Any]:
        raw = run_hx(self.root, args, binary=self.binary, document=True)
        try:
            value = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise CommandError(f"hx {' '.join(args)} did not print JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise CommandError(f"hx {' '.join(args)} did not print a JSON object")
        return value

    def board(self) -> dict[str, Any]:
        return self._json(["board", "--json"])

    def show(self, agent_id: str) -> dict[str, Any]:
        if id_of(agent_id) != agent_id:
            raise NotFound(f"not an agent id: {agent_id!r}")
        document = self._json(["show", agent_id, "--json"])
        # Spec 16.2: the pane is re-read on the SSE tick, so the UI captures it
        # itself rather than showing whatever `hx show` happened to catch.
        document["pane"] = pane.capture(self.root, agent_id, socket=self.tmux_socket)
        return document

    def orders(self) -> dict[str, Any]:
        return self._json(["orders", "--json"])

    def archive(self) -> dict[str, Any]:
        return self._json(["archive", "--json"])

    def wake_partner(self, text: str) -> bool:
        """The UI's only write.

        The text crosses a process boundary as one argv element. That is hx's own
        fixed-form message, never an order: orders are files (spec 08). Replaced
        by `hx.wake.wake_partner(root, text)` directly in ui-4.

        `hx wake` exits 0 whether or not the Partner was there and says which on
        stdout, so the exit code cannot be the answer: reporting "delivered" for
        a Partner that never heard it is the one lie this box must not tell.
        """
        try:
            out = run_hx(self.root, ["wake", "partner", text], binary=self.binary)
        except CommandError:
            return False  # CONTRACTS.md: False when no socket exists or it refused
        return any(line.strip() == WAKE_ACCEPTED for line in out.splitlines())

    # -- the sweep -------------------------------------------------------
    def scan(self) -> dict[str, float]:
        table: dict[str, float] = {}
        for name, recursive in self.WATCHED:
            path = self.root / name
            if recursive:
                self._scan_tree(path, table)
            else:
                self._scan_file(path, SCOPE_TASKS, table)
        run = self.root / "run"
        try:
            entries = sorted(run.iterdir())
        except OSError:
            entries = []
        for entry in entries:
            agent_id = id_of(entry.name)
            if agent_id is None:
                continue
            for marker in self.RUN_MARKERS:
                self._scan_file(entry / marker, agent_id, table)
        return table

    def _scan_file(self, path: Path, scope: str, table: dict[str, float]) -> None:
        try:
            _newest(table, scope, path.stat().st_mtime)
        except OSError:
            pass

    def _scan_tree(self, root: Path, table: dict[str, float]) -> None:
        try:
            entries = sorted(root.rglob("*"))
        except OSError:
            return
        for path in entries:
            try:
                if not path.is_file():
                    continue
                mtime = path.stat().st_mtime
            except OSError:
                continue
            _newest(table, self._tree_scope(root, path), mtime)

    @staticmethod
    def _tree_scope(root: Path, path: Path) -> str:
        """The id a watched path belongs to.

        `state/<id>/…` and `logs/<id>/…` are keyed by their first segment;
        `pods/<pod>/<id>-<state>.md` and `orders/<id>.md` by their filename.
        """
        relative = path.relative_to(root).parts
        for part in (relative[0], path.name):
            agent_id = id_of(part)
            if agent_id is not None:
                return agent_id
        return root.name
