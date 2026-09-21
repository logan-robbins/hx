"""ui-4: `InstanceSource` bound to the build lane's published functions.

`handoff/build-to-ui.md` (build-2) publishes a plain function per reader, each
taking the root first and returning the `CONTRACTS.md` document. The UI calls
those in process now. `run_hx` stays as the fallback for a reader whose function
is not importable, so these tests hold both paths to the same answers.
"""

from __future__ import annotations

import http.client
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from hx.ui.data import PUBLISHED, CommandError, InstanceSource, NotFound, published

from .conftest import archive_is_broken

HX = Path(sys.executable).parent / "hx"

#: Every reader the UI binds, and whether the build lane has shipped it.
READERS = ["board", "show", "orders", "archive", "wake"]


# -- the binding ---------------------------------------------------------

def test_the_binding_table_covers_every_reader():
    assert set(PUBLISHED) == set(READERS) | {"metrics"}


@pytest.mark.parametrize("name", READERS)
def test_every_reader_is_bound_to_a_real_function(name):
    module, attribute = PUBLISHED[name]
    function = published(name)
    assert callable(function), f"{module}.{attribute} is not importable"
    assert function.__module__ == module
    assert function.__name__ == attribute


def test_metrics_has_nothing_to_bind_to_yet():
    """`hx metrics` is M7. The entry is here so it binds itself when it lands."""
    assert published("metrics") is None
    assert PUBLISHED["metrics"] == ("hx.metrics", "collect")


def test_the_bound_readers_never_run_hx(instance_root, monkeypatch):
    """The point of ui-4: no `hx` process in the read path.

    Not "no subprocess at all" — `hx.board.collect` runs `tmux list-sessions` to
    answer `session_alive`, and `hx.ui.pane` runs `capture-pane`. Both are
    reading the world, which is the UI's whole job. What must be gone is the
    `hx` binary.
    """
    seen: list[list[str]] = []
    real = subprocess.run

    def record(argv, *args, **kwargs):
        seen.append(list(argv))
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", record)
    source = InstanceSource(instance_root)
    assert source.board()["items"]
    assert source.orders()["orders"]
    assert source.show("eng-001")["id"] == "eng-001"
    if not archive_is_broken(instance_root):
        assert source.archive()["items"]

    programs = {Path(argv[0]).name for argv in seen if argv}
    assert "hx" not in programs, f"a bound reader ran hx: {seen}"
    assert programs <= {"tmux"}, f"unexpected subprocess: {programs}"


def test_prefer_subprocess_really_uses_the_fallback(instance_root, monkeypatch):
    calls: list[list[str]] = []
    real = subprocess.run

    def record(argv, *args, **kwargs):
        calls.append(list(argv))
        return real(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", record)
    source = InstanceSource(instance_root, prefer_subprocess=True)
    assert source.board()["items"]
    assert calls and calls[0][1:] == ["board", "--json"]


@pytest.mark.parametrize("reader", ["board", "orders", "archive"])
def test_both_paths_return_the_same_document(instance_root, reader):
    """The fallback is not a different answer, only a different route to it."""
    if reader == "archive":
        broken = archive_is_broken(instance_root)
        if broken:
            pytest.skip(broken)
    direct = getattr(InstanceSource(instance_root), reader)()
    fallback = getattr(InstanceSource(instance_root, prefer_subprocess=True), reader)()
    direct.pop("ts", None), fallback.pop("ts", None)
    assert direct == fallback


def test_show_matches_across_both_paths(instance_root):
    direct = InstanceSource(instance_root).show("eng-001")
    fallback = InstanceSource(instance_root, prefer_subprocess=True).show("eng-001")
    assert direct == fallback


# -- the raise contract --------------------------------------------------

def test_an_unknown_id_raises_not_found(instance_root):
    """`hx.errors.NotFound` is the 404 (handoff/build-to-ui.md)."""
    with pytest.raises(NotFound):
        InstanceSource(instance_root).show("eng-404")


def test_an_unknown_id_is_404_over_http(instance_root):
    from .conftest import _serve

    server = _serve(InstanceSource(instance_root))
    handle = next(server)
    try:
        status, payload = handle.client.json("/api/show/eng-404")
        assert status == 404
        assert payload["error"], "and the message is one line, safe to show"
    finally:
        server.close()


def test_a_broken_instance_is_502_not_404(instance_root, tmp_path):
    """Everything that is not NotFound is the instance being broken."""
    broken = tmp_path / "broken"
    broken.mkdir()
    for name in ("config", "pods", "run", "orders"):
        (broken / name).mkdir()
    (broken / "tasks.json").write_text("{ this is not json", encoding="utf-8")
    (broken / "config" / "eng-001").mkdir()
    (broken / "config" / "eng-001" / "harness.json").write_text("{", encoding="utf-8")

    from .conftest import _serve

    server = _serve(InstanceSource(broken))
    handle = next(server)
    try:
        status, payload = handle.client.json("/api/show/eng-001")
        assert status == 502, payload
        assert payload["error"]
    finally:
        server.close()


def test_a_malformed_tasks_file_is_refused(instance_root, tmp_path):
    """v1 cut: there is no `errors` list left to put the breakage in."""
    root = tmp_path / "half-built"
    root.mkdir()
    for name in ("config", "pods", "run", "orders", "logs", "state"):
        (root / name).mkdir()
    (root / "tasks.json").write_text("{ not json", encoding="utf-8")
    # v1 cut: `hx board` has no `errors` and polices nothing, so a malformed
    # `tasks.json` is now a refusal from hx rather than a line in the document.
    with pytest.raises((CommandError, NotFound)):
        InstanceSource(root).orders()


# -- `hx ui`, the build lane's subcommand --------------------------------

def free_port() -> int:
    import socket as socketlib

    with socketlib.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def test_hx_ui_starts_this_server(instance_root):
    """ui-2 asked for `hx ui` to call `serve(root, port)`; this proves it does."""
    port = free_port()
    process = subprocess.Popen(
        [str(HX), "ui", "--port", str(port)],
        env=dict(os.environ, HARNESS_ROOT=str(instance_root)),
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    try:
        token = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(f"hx ui exited: {process.stdout.read()}")
            token_file = instance_root / "run" / "ui-token"
            if token_file.exists() and token_file.read_text().strip():
                token = token_file.read_text().strip()
                break
            time.sleep(0.05)
        assert token, "hx ui never created run/ui-token"

        payload = None
        while time.monotonic() < deadline:
            try:
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
                connection.request("GET", "/api/board", headers={"Authorization": f"Bearer {token}"})
                response = connection.getresponse()
                payload = json.loads(response.read())
                connection.close()
                break
            except OSError:
                time.sleep(0.05)
        assert payload is not None, "hx ui never answered on its port"
        assert payload["root_abs"] == str(instance_root)
        ids = [item["id"] for item in payload["items"]]
        assert ids == sorted(ids), "v1 cut: by id, and the Partner is not an item"
        assert "partner" not in ids
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_hx_ui_refuses_a_root_that_does_not_exist(tmp_path):
    result = subprocess.run(
        [str(HX), "ui"],
        env=dict(os.environ, HARNESS_ROOT=str(tmp_path / "nowhere")),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "does not exist" in (result.stdout + result.stderr)
