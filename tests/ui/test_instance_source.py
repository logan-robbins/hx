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
import stat
import subprocess
from pathlib import Path

import pytest

from hx.ui.data import CommandError, InstanceSource, NotFound, SourceUnavailable, run_hx

from .conftest import FIXTURES, _serve

NOT_IMPLEMENTED = ["show", "orders", "archive", "wake"]


# -- against the real instance -------------------------------------------

def test_board_is_live_against_a_real_instance(instance_root):
    board = InstanceSource(instance_root).board()
    assert set(board) == {"root_abs", "ts", "items", "errors"}
    assert board["root_abs"] == str(instance_root)
    ids = [item["id"] for item in board["items"]]
    assert ids[0] == "partner", "partner first (CONTRACTS.md)"
    assert "eng-001" in ids
    for item in board["items"]:
        assert item["state"] in {"idle", "queued", "working", "complete"}
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


@pytest.mark.parametrize("command", NOT_IMPLEMENTED)
def test_the_readers_report_hx_own_words_until_build_2(instance_root, command):
    source = InstanceSource(instance_root)
    call = {
        "show": lambda: source.show("eng-001"),
        "orders": source.orders,
        "archive": source.archive,
        "wake": lambda: source.wake_partner("hello"),
    }[command]
    with pytest.raises(SourceUnavailable) as raised:
        call()
    assert "not implemented" in str(raised.value)
    assert command in str(raised.value)


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

def test_the_server_serves_a_real_instance(instance_root):
    server = _serve(InstanceSource(instance_root))
    handle = next(server)
    try:
        status, board = handle.client.json("/api/board")
        assert status == 200
        assert board["root_abs"] == str(instance_root)

        # Until build-2, these are an honest 503 naming the command.
        for path in ("/api/show/eng-001", "/api/orders", "/api/archive"):
            status, payload = handle.client.json(path)
            assert status == 503, path
            assert "not implemented" in payload["error"]

        assert handle.client.post("/api/partner/wake", {"text": "hi"}).status == 503
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
