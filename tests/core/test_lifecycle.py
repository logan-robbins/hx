"""`hx goal`, `hx launch`, `hx restart`, `hx up`, `hx heartbeat`, `hx wake`, and who may call what."""

from __future__ import annotations

import json
import socket
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from .conftest import wait_for

PARTNER_ONLY = [
    ("dispatch", ["eng-001", "orders/eng-001.md"]),
    ("resume", ["eng-001", "orders/eng-001.addendum.md"]),
    ("bench", ["eng-001"]),
    ("launch", ["eng-001"]),
    ("restart", ["eng-001"]),
    ("read", ["eng-001"]),
]


def pasted(root, item_id):
    log = root / "run" / item_id / "fake-input.log"
    return log.read_text() if log.is_file() else ""


# --- who may call what (spec 08) ----------------------------------------------------------------


@pytest.mark.parametrize("command,args", PARTNER_ONLY, ids=[c for c, _ in PARTNER_ONLY])
def test_partner_commands_refuse_a_worker(instance, hx, command, args):
    result = hx(command, *args, harness_id="eng-001")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "refuse" in result.stderr
    assert "HARNESS_ID is `eng-001`" in result.stderr


@pytest.mark.parametrize("command,args", PARTNER_ONLY, ids=[c for c, _ in PARTNER_ONLY])
def test_partner_commands_allow_a_system_caller(instance, hx, command, args):
    """`hx up` and `hx heartbeat` run from systemd and cron with no HARNESS_ID (spec 08)."""
    result = hx(command, *args, harness_id=None)
    assert "refuse: `hx" not in result.stderr


# --- hx goal -------------------------------------------------------------------------------------


def test_goal_pastes_at_the_idle_prompt(instance, hx, launched, orders):
    launched("eng-001")
    orders("eng-001")
    assert hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance).returncode == 0
    (instance / "run" / "eng-001" / "fake-input.log").write_text("")

    result = hx("goal", "eng-001")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "HX-GOAL eng-001 pasted"
    wait_for(lambda: "/goal The order for" in pasted(instance, "eng-001"), what="the pointer")
    assert (instance / "run" / "eng-001" / "goal").is_file()


def test_goal_is_left_pending_when_the_pane_is_mid_turn(instance, hx, launched, orders, tmux_server):
    from .test_transitions import hold_pane

    launched("eng-001")
    orders("eng-001")
    assert hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance).returncode == 0
    (instance / "run" / "eng-001" / "fake-input.log").write_text("")
    (instance / "run" / "eng-001" / "goal").unlink()
    hold_pane(tmux_server, "eng-001")

    result = hx("goal", "eng-001")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "HX-GOAL eng-001 pending"
    assert (instance / "run" / "eng-001" / "goal-pending").is_file()
    # The marker is written in both cases, so the `working` invariant holds during the turn
    # in which a busy pane is owed its goal (spec 08).
    assert (instance / "run" / "eng-001" / "goal").is_file()
    assert "/goal The order for" not in pasted(instance, "eng-001")


def test_goal_now_pastes_into_a_busy_pane(instance, hx, launched, orders, tmux_server):
    """`--now` is used from the `context` hook on `clear` and from the `stop` hook (spec 08)."""
    from .test_transitions import hold_pane

    launched("eng-001")
    orders("eng-001")
    assert hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance).returncode == 0
    (instance / "run" / "eng-001" / "fake-input.log").write_text("")
    hold_pane(tmux_server, "eng-001")

    result = hx("goal", "eng-001", "--now")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "HX-GOAL eng-001 pasted"
    wait_for(lambda: "/goal The order for" in pasted(instance, "eng-001"), what="the pointer")


def test_goal_clears_a_pending_marker_once_pasted(instance, hx, launched, orders):
    launched("eng-001")
    orders("eng-001")
    assert hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance).returncode == 0
    (instance / "run" / "eng-001" / "goal-pending").write_text("2026-09-20T12:00:00Z\n")
    assert hx("goal", "eng-001", "--now").returncode == 0
    assert not (instance / "run" / "eng-001" / "goal-pending").exists()


def test_goal_without_a_pane_says_so(instance, hx):
    from hx.lifecycle import ensure_work_item

    ensure_work_item(instance, "eng-001")
    result = hx("goal", "eng-001")
    assert result.returncode == 2
    assert "no tmux pane" in result.stderr


def test_the_pointer_never_carries_the_order(instance, hx, launched, orders):
    """spec 06: what `hx goal` pastes is a fixed short form that never grows with the task."""
    launched("eng-001")
    orders("eng-001", order="A very long order " * 50)
    assert hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance).returncode == 0
    wait_for(lambda: "/goal" in pasted(instance, "eng-001"), what="the pointer")
    pointer = [line for line in pasted(instance, "eng-001").split("\n") if line.startswith("/goal")][0]
    assert "A very long order" not in pointer
    assert len(pointer) < 400


# --- hx restart, up, heartbeat ---------------------------------------------------------------------


def test_restart_relaunches_and_resends_the_goal(instance, hx, launched, orders, tmux_server):
    launched("eng-001")
    # `hx dispatch` clears `run/<id>/` except home/ and persona.md (spec 08), so the launch
    # record has to be read before it.
    first = json.loads((instance / "run" / "eng-001" / "fake-argv.json").read_text())
    orders("eng-001")
    assert hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance).returncode == 0
    wait_for(lambda: "/goal" in pasted(instance, "eng-001"), what="the first pointer")
    assert not (instance / "run" / "eng-001" / "fake-argv.json").exists()

    result = hx("restart", "eng-001")
    assert result.returncode == 0, result.stderr
    assert "goal=pasted" in result.stdout
    wait_for(
        (instance / "run" / "eng-001" / "fake-argv.json").is_file, what="the relaunched agent"
    )
    second = json.loads((instance / "run" / "eng-001" / "fake-argv.json").read_text())
    assert second["pid"] != first["pid"], "restart relaunches the process"
    assert "/goal The order for" in pasted(instance, "eng-001")


def test_restart_of_an_idle_item_sends_no_goal(instance, hx, launched):
    launched("eng-001")
    result = hx("restart", "eng-001")
    assert result.returncode == 0, result.stderr
    assert "goal=none" in result.stdout


def test_up_launches_every_config_id_partner_first(instance, hx, agent):
    agent("eng-002")
    result = hx("up")
    assert result.returncode == 0, result.stderr
    launched_ids = [line.split()[1] for line in result.stdout.strip().split("\n")]
    assert launched_ids[0] == "partner", "partner first (spec 08)"
    assert set(launched_ids) == {"partner", "eng-001", "eng-002"}
    for item_id in launched_ids:
        assert (instance / "run" / item_id / "home" / "settings.json").is_file()


def test_heartbeat_restarts_a_dead_session(instance, hx, launched, orders, tmux_server):
    launched("eng-001")
    orders("eng-001")
    assert hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance).returncode == 0
    wait_for(lambda: "/goal" in pasted(instance, "eng-001"), what="the pointer")
    subprocess.run([*tmux_server, "kill-session", "-t", "=eng-001"], check=True)

    result = hx("heartbeat")
    assert result.returncode == 0, result.stderr
    assert "restarted=eng-001" in result.stdout
    assert subprocess.run(
        [*tmux_server, "has-session", "-t", "=eng-001"], capture_output=True
    ).returncode == 0


def test_heartbeat_wakes_the_partner_only_when_the_board_changed(instance, hx, launched, orders):
    launched("partner", "eng-001")
    orders("eng-001")
    assert hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance).returncode == 0

    first = hx("heartbeat")
    assert first.returncode == 0
    assert "changed=false" in first.stdout, "the first heartbeat has nothing to compare against"

    second = hx("heartbeat")
    assert "changed=false" in second.stdout, "nothing moved between the two"

    assert hx("complete", "done", harness_id="eng-001").returncode == 0
    third = hx("heartbeat")
    assert "changed=true" in third.stdout


# --- hx wake ----------------------------------------------------------------------------------------


def test_wake_partner_returns_false_without_a_socket_file(instance):
    from hx.wake import wake_partner

    assert wake_partner(instance, "anything") is False


def test_wake_partner_returns_false_when_the_socket_is_stale(instance):
    from hx.wake import wake_partner

    socket_file = instance / "run" / "partner" / "socket.json"
    socket_file.parent.mkdir(parents=True, exist_ok=True)
    socket_file.write_text(json.dumps({"socket": "/nonexistent/partner.sock", "token": "t"}))
    assert wake_partner(instance, "anything") is False


def test_wake_partner_sends_the_auth_line_then_the_message(instance):
    """spec 08, live-verified E6: auth line, then the user message, newline-terminated."""
    from hx.wake import wake_partner

    address = str(Path(tempfile.mkdtemp(prefix="hxw")) / "p.sock")
    received: list[bytes] = []
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(address)
    server.listen(1)

    def accept_once():
        connection, _ = server.accept()
        with connection:
            received.append(connection.recv(65536))

    thread = threading.Thread(target=accept_once, daemon=True)
    thread.start()

    socket_file = instance / "run" / "partner" / "socket.json"
    socket_file.parent.mkdir(parents=True, exist_ok=True)
    socket_file.write_text(json.dumps({"socket": address, "token": "tok"}))

    assert wake_partner(instance, "eng-001 complete: done; hx read eng-001") is True
    thread.join(timeout=10)
    server.close()

    lines = [json.loads(line) for line in received[0].decode().strip().split("\n")]
    assert lines == [
        {"type": "auth", "token": "tok"},
        {"type": "user", "message": {"role": "user",
                                     "content": "eng-001 complete: done; hx read eng-001"}},
    ]


def test_wake_reads_the_hook_variable_names_too(instance):
    """The `context` hook writes what Claude Code exported to it (spec 09)."""
    from hx.wake import read_socket

    socket_file = instance / "run" / "partner" / "socket.json"
    socket_file.parent.mkdir(parents=True, exist_ok=True)
    socket_file.write_text(
        json.dumps({"CLAUDE_CODE_MESSAGING_SOCKET": "/tmp/s.sock", "CLAUDE_CODE_MESSAGING_TOKEN": "t"})
    )
    assert read_socket(instance) == ("/tmp/s.sock", "t")


def test_wake_refuses_a_target_that_is_not_the_partner(instance, hx):
    result = hx("wake", "eng-001", "hello")
    assert result.returncode == 1
    assert "refuse" in result.stderr and "only the Partner" in result.stderr


def test_wake_cli_exit_code_is_a_contract(instance, hx):
    """CONTRACTS.md: exit 0 only for `accepted`, exit 3 otherwise."""
    from hx.wake import NOT_REACHED_EXIT

    result = hx("wake", "partner", "check on each HarnessAgent")
    assert result.stdout.strip().split("\n")[-1] == "HX-WAKE partner no-socket"
    assert result.returncode == NOT_REACHED_EXIT == 3


def test_wake_cli_distinguishes_refused_from_no_socket(instance, hx):
    """A stale socket file from a dead session is `refused`, not `no-socket`."""
    socket_file = instance / "run" / "partner" / "socket.json"
    socket_file.parent.mkdir(parents=True, exist_ok=True)
    socket_file.write_text(json.dumps({"socket": "/nonexistent/p.sock", "token": "t"}))
    result = hx("wake", "partner", "anything")
    assert result.stdout.strip().split("\n")[-1] == "HX-WAKE partner refused"
    assert result.returncode == 3


def test_wake_cli_accepts_and_exits_0(instance, hx):
    address = str(Path(tempfile.mkdtemp(prefix="hxw")) / "p.sock")
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(address)
    server.listen(1)
    thread = threading.Thread(target=lambda: server.accept()[0].close(), daemon=True)
    thread.start()

    socket_file = instance / "run" / "partner" / "socket.json"
    socket_file.parent.mkdir(parents=True, exist_ok=True)
    socket_file.write_text(json.dumps({"socket": address, "token": "t"}))

    result = hx("wake", "partner", "eng-001 complete: done; hx read eng-001")
    thread.join(timeout=10)
    server.close()
    assert result.stdout.strip().split("\n")[-1] == "HX-WAKE partner accepted"
    assert result.returncode == 0


def test_a_failed_wake_is_a_warning_not_a_failure(instance, hx, launched, orders):
    """`hx complete` still succeeds and still prints HX-COMPLETE last (spec 08)."""
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, orders)
    result = hx("complete", "done", harness_id="eng-001")
    assert result.returncode == 0
    assert result.stdout.strip().split("\n")[-1] == "HX-COMPLETE eng-001 done"
    assert "the Partner was not woken (no-socket)" in result.stderr
