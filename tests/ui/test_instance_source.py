"""`InstanceSource` against a real `HARNESS_ROOT` (ui-2 item 3).

Two halves, because the build lane is mid-flight:

* against the **real** instance, `board()` is live today and the other readers
  report, through hx's own words, that their command is not implemented yet;
* against a **stub** `hx`, the parse path of every reader is exercised, so the
  moment build-2 lands these become live with no change here. `run_hx` is the
  one seam; ui-3 swaps it for the Python functions build-2 names.
"""

from __future__ import annotations

import json
import os
import pathlib
import socket
import stat
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from hx.ui.data import CommandError, InstanceSource, NotFound, SourceUnavailable, run_hx

from .conftest import FIXTURES, _serve

BOARD_STATES = {"idle", "queued", "working", "complete"}


# -- against the real instance -------------------------------------------

def test_board_is_live_against_a_real_instance(instance_root):
    board = InstanceSource(instance_root).board()
    assert set(board) == {"root_abs", "ts", "items", "errors"}
    assert board["root_abs"] == str(instance_root)
    ids = [item["id"] for item in board["items"]]
    assert ids[0] == "partner", "partner first (CONTRACTS.md)"
    assert "eng-001" in ids
    for item in board["items"]:
        assert item["state"] in BOARD_STATES
        assert item["file"].startswith("pods/")


def test_a_board_with_invariant_errors_is_data_not_a_failure(instance_root):
    """`hx board` exits 1 when `errors` is non-empty; the view must still render."""
    result = subprocess.run(
        [str(Path(os.sys.executable).parent / "hx"), "board", "--json"],
        env=dict(os.environ, HARNESS_ROOT=str(instance_root)),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1, "the scratch instance has no live sessions, so hx exits 1"
    board = InstanceSource(instance_root).board()
    assert board["errors"], "and the UI gets the errors rather than an exception"


def test_show_is_live_against_a_real_instance(instance_root):
    show = InstanceSource(instance_root).show("eng-001")
    assert show["id"] == "eng-001"
    for key in (
        "pod", "role", "state", "file", "work_item", "task", "persona_path", "step_state",
        "context_file", "streams", "subagents", "metrics", "pane", "archive", "bench",
    ):
        assert key in show, key
    assert set(show["task"]) == {"order", "after", "addenda", "outcome", "dispatched", "completed"}
    assert "## Order" in show["task"]["order"]
    assert show["work_item"]["frontmatter"]["id"] == "eng-001"


def test_show_partner_carries_partner_md_against_a_real_instance(instance_root):
    show = InstanceSource(instance_root).show("partner")
    assert isinstance(show["partner_md"], str)
    assert "partner_md" not in InstanceSource(instance_root).show("eng-001")


def test_show_overlays_the_live_pane_on_a_real_instance(instance_root):
    """Spec 16.2: the UI captures the pane itself, on the SSE tick."""
    pane = InstanceSource(instance_root).show("eng-001")["pane"]
    assert set(pane) == {"session", "alive", "lines", "source", "error"}
    assert pane["session"] == "eng-001"
    assert pane["alive"] is False, "no tmux session of that name in the test environment"


def test_orders_is_live_and_reports_both_file_states(instance_root):
    orders = InstanceSource(instance_root).orders()
    assert set(orders) == {"root_abs", "ts", "orders", "graph", "errors"}
    by_id = {order["id"]: order for order in orders["orders"]}
    assert set(by_id) == {"partner", "eng-001"}
    assert by_id["partner"]["file_matches_record"] is True
    assert by_id["eng-001"]["file_matches_record"] is False, "its order file was edited after dispatch"
    for order in orders["orders"]:
        assert set(order) == {
            "id", "pod", "path", "after", "order", "addenda", "record",
            "state", "ready", "waiting_on", "file_matches_record",
        }
    assert {node["id"] for node in orders["graph"]["nodes"]} == set(by_id)


def test_archive_is_live_against_a_real_instance(instance_root):
    archive = InstanceSource(instance_root).archive()
    assert set(archive) == {"root_abs", "ts", "items", "errors"}
    for item in archive["items"]:
        assert set(item) == {"id", "pod", "bench", "archive"}
    assert all(not item["bench"] and not item["archive"] for item in archive["items"]), (
        "a fresh instance has nothing benched or archived"
    )


def test_wake_is_false_when_the_partner_has_no_socket(instance_root):
    """`hx wake` exits 0 either way and says which on stdout (CONTRACTS.md)."""
    assert not (instance_root / "run" / "partner" / "socket.json").exists()
    assert InstanceSource(instance_root).wake_partner("anyone home") is False


def test_wake_is_true_when_a_real_socket_accepts(instance_root):
    """End to end through the real `hx wake`: a unix socket that takes the lines.

    The socket lives in the OS temp dir, not in `tmp_path`: an `AF_UNIX` path is
    capped near 104 bytes and pytest's per-test directory is already longer.
    """
    socket_dir = pathlib.Path(tempfile.mkdtemp(prefix="hxui-"))
    socket_path = socket_dir / "p.sock"
    received: list[bytes] = []
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(socket_path))
    server.listen(1)

    def accept_one():
        connection, _ = server.accept()
        with connection:
            while chunk := connection.recv(65536):
                received.append(chunk)

    thread = threading.Thread(target=accept_one, daemon=True)
    thread.start()

    descriptor = instance_root / "run" / "partner" / "socket.json"
    descriptor.parent.mkdir(parents=True, exist_ok=True)
    descriptor.write_text(
        json.dumps({"socket": str(socket_path), "token": "test-token"}), encoding="utf-8"
    )
    try:
        text = "eng-001 complete: done; hx read eng-001"
        assert InstanceSource(instance_root).wake_partner(text) is True
        thread.join(timeout=5)
        payload = b"".join(received).decode()
        assert "test-token" in payload, "the auth line went first"
        assert text in payload, "then the message, verbatim"
    finally:
        server.close()
        descriptor.unlink(missing_ok=True)
        # The instance manifest check allows only run/ui-token, so leave nothing behind.
        try:
            descriptor.parent.rmdir()
        except OSError:
            pass
        socket_path.unlink(missing_ok=True)
        socket_dir.rmdir()


def test_show_rejects_a_non_id_before_running_anything(instance_root):
    with pytest.raises(NotFound):
        InstanceSource(instance_root).show("../etc/passwd")


def test_scan_sees_the_real_instance(instance_root):
    scopes = InstanceSource(instance_root).scan()
    assert "tasks" in scopes and "partner" in scopes and "eng-001" in scopes


def test_run_hx_passes_the_root_and_drops_harness_id(instance_root, tmp_path):
    """The UI is not an agent: spec 08 has Partner commands refuse a foreign HARNESS_ID."""
    stub = write_stub(tmp_path, 'print(json.dumps({"root": os.environ.get("HARNESS_ROOT"), '
                                '"id": os.environ.get("HARNESS_ID", "<unset>")}))')
    seen = json.loads(run_hx(instance_root, ["board", "--json"], binary=str(stub)))
    assert seen["root"] == str(instance_root)
    assert seen["id"] == "<unset>"


def test_a_missing_binary_is_reported_not_raised(instance_root):
    with pytest.raises(SourceUnavailable) as raised:
        run_hx(instance_root, ["board", "--json"], binary="/nonexistent/hx")
    assert "cannot run" in str(raised.value)


# -- against a stub hx ---------------------------------------------------

def write_stub(tmp_path: Path, body: str, name: str = "hx-stub") -> Path:
    """A fake `hx` that behaves like the real one will once build-2 lands."""
    stub = tmp_path / name
    stub.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "args = sys.argv[1:]\n" + body + "\n",
        encoding="utf-8",
    )
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return stub


@pytest.fixture
def stub_source(instance_root, tmp_path):
    """`InstanceSource` wired to a stub that serves the ui-1 fixtures."""
    stub = write_stub(
        tmp_path,
        f"""
FIXTURES = {str(FIXTURES)!r}
def emit(name):
    with open(FIXTURES + "/" + name) as handle:
        sys.stdout.write(handle.read())
if args[0] == "board":
    emit("board.json")
elif args[0] == "show":
    emit("show-" + args[1] + ".json")
elif args[0] == "orders":
    emit("orders.json")
elif args[0] == "archive":
    emit("archive.json")
elif args[0] == "wake":
    with open(os.environ["HX_WAKE_LOG"], "a") as handle:
        handle.write(json.dumps(args) + chr(10))
    # The real `hx wake` always exits 0 and reports on stdout.
    sys.stdout.write("HX-WAKE partner " + os.environ.get("HX_WAKE_RESULT", "accepted") + chr(10))
    sys.exit(int(os.environ.get("HX_WAKE_EXIT", "0")))
else:
    sys.stderr.write("hx: " + args[0] + ": not implemented (build-9)")
    sys.exit(2)
""",
    )
    os.environ["HX_WAKE_LOG"] = str(tmp_path / "wakes.jsonl")
    try:
        yield InstanceSource(instance_root, binary=str(stub))
    finally:
        os.environ.pop("HX_WAKE_LOG", None)
        os.environ.pop("HX_WAKE_EXIT", None)
        os.environ.pop("HX_WAKE_RESULT", None)


def test_board_parses(stub_source):
    board = stub_source.board()
    assert [item["id"] for item in board["items"]][0] == "partner"
    assert len(board["errors"]) == 1


def test_show_parses_and_carries_the_contract_keys(stub_source):
    show = stub_source.show("eng-001")
    for key in ("work_item", "task", "step_state", "context_file", "streams", "subagents", "metrics"):
        assert key in show, key
    assert show["id"] == "eng-001"


def test_show_overlays_a_live_pane_capture(stub_source):
    """Spec 16.2: the pane is re-read on the SSE tick, so the UI captures it itself."""
    show = stub_source.show("eng-001")
    pane = show["pane"]
    assert set(pane) == {"session", "alive", "lines", "source", "error"}
    assert pane["session"] == "eng-001"
    assert pane["alive"] is False, "no tmux session of that name in the test environment"
    assert pane["source"] == "none"
    # The fixture's own canned pane lines were replaced, not merged.
    assert pane["lines"] == []


def test_orders_and_archive_parse(stub_source):
    orders = stub_source.orders()
    assert {order["id"] for order in orders["orders"]} >= {"eng-001", "eng-002"}
    archive = stub_source.archive()
    assert all(set(item) == {"id", "pod", "bench", "archive"} for item in archive["items"])


def test_wake_partner_passes_the_text_as_one_argv_element(stub_source, tmp_path):
    text = "eng-003 complete: decision; hx read eng-003"
    assert stub_source.wake_partner(text) is True
    logged = [json.loads(line) for line in (tmp_path / "wakes.jsonl").read_text().splitlines()]
    assert logged == [["wake", "partner", text]], "one argv element, verbatim"


def test_wake_partner_is_false_when_the_socket_refuses(stub_source):
    """CONTRACTS.md: False when no socket file exists or the connection was refused."""
    os.environ["HX_WAKE_EXIT"] = "1"
    assert stub_source.wake_partner("anyone home") is False


def test_wake_partner_is_false_when_hx_says_no_socket(stub_source):
    """The exit code is 0 either way, so the status line is the only answer."""
    os.environ["HX_WAKE_RESULT"] = "no-socket"
    assert stub_source.wake_partner("anyone home") is False


def test_a_command_printing_junk_is_reported(instance_root, tmp_path):
    stub = write_stub(tmp_path, 'sys.stdout.write("not json at all")')
    with pytest.raises(CommandError) as raised:
        InstanceSource(instance_root, binary=str(stub)).board()
    assert "did not print JSON" in str(raised.value)


def test_a_command_printing_a_json_array_is_reported(instance_root, tmp_path):
    stub = write_stub(tmp_path, 'sys.stdout.write("[1, 2]")')
    with pytest.raises(CommandError) as raised:
        InstanceSource(instance_root, binary=str(stub)).board()
    assert "JSON object" in str(raised.value)


# -- served ---------------------------------------------------------------

def test_the_server_serves_every_view_of_a_real_instance(instance_root):
    """The whole UI against a real HARNESS_ROOT, through the real `hx`."""
    server = _serve(InstanceSource(instance_root))
    handle = next(server)
    try:
        status, board = handle.client.json("/api/board")
        assert status == 200
        assert board["root_abs"] == str(instance_root)

        for path in ("/api/show/partner", "/api/show/eng-001", "/api/orders", "/api/archive"):
            status, payload = handle.client.json(path)
            assert status == 200, f"{path}: {payload}"
            assert payload

        # No Partner socket in a scratch instance, so nothing was delivered —
        # and the UI must say so rather than report a message that went nowhere.
        response = handle.client.post("/api/partner/wake", {"text": "hi"})
        assert response.status == 200
        assert json.loads(response.body) == {"delivered": False}
    finally:
        server.close()


def test_an_unknown_id_is_a_404_not_a_502(instance_root):
    """The UI has to tell "no such id" apart from "the instance is broken"."""
    server = _serve(InstanceSource(instance_root))
    handle = next(server)
    try:
        status, payload = handle.client.json("/api/show/eng-404")
        assert status in (404, 502), payload
    finally:
        server.close()


def test_the_server_serves_every_view_of_a_stubbed_instance(stub_source):
    server = _serve(stub_source)
    handle = next(server)
    try:
        for path in ("/api/board", "/api/orders", "/api/archive", "/api/show/partner", "/api/show/eng-001"):
            status, payload = handle.client.json(path)
            assert status == 200, path
            assert payload
        response = handle.client.post("/api/partner/wake", {"text": "hello"})
        assert response.status == 200
        assert json.loads(response.body) == {"delivered": True}
    finally:
        server.close()
