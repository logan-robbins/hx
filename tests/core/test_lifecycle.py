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
    ("dispatch", ["eng-001", "run/goal-eng-001.md"]),
    ("resume", ["eng-001", "run/addendum-eng-001.md"]),
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


def test_goal_pastes_at_the_idle_prompt(instance, hx, launched, goals):
    launched("eng-001")
    goals("eng-001")
    assert hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance).returncode == 0
    (instance / "run" / "eng-001" / "fake-input.log").write_text("")

    result = hx("goal", "eng-001")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "HX-GOAL eng-001 pasted"
    wait_for(lambda: "/goal The goal for" in pasted(instance, "eng-001"), what="the pointer")
    assert (instance / "run" / "eng-001" / "goal").is_file()


def test_goal_waits_for_the_idle_prompt(instance, hx, launched, goals, tmux_server):
    """spec 08: `hx goal` waits for the pane, with no timeout. `goal-pending` is gone (D25)."""
    import threading

    from .test_transitions import hold_pane

    launched("eng-001")
    goals("eng-001")
    assert hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance).returncode == 0
    (instance / "run" / "eng-001" / "fake-input.log").write_text("")
    hold_pane(tmux_server, "eng-001")

    held = {}

    def send():
        held["result"] = hx("goal", "eng-001")

    thread = threading.Thread(target=send)
    thread.start()
    try:
        # It is still waiting: nothing was pasted and no pending marker was left behind.
        thread.join(1.0)
        assert thread.is_alive(), "hx goal returned instead of waiting for the prompt"
        assert not (instance / "run" / "eng-001" / "goal-pending").exists()
        assert "/goal The goal for" not in pasted(instance, "eng-001")
    finally:
        subprocess.run(
            [*tmux_server, "send-keys", "-t", "=eng-001:main", "/fake-release", "Enter"],
            check=True,
        )
        thread.join(30)

    assert held["result"].stdout.strip() == "HX-GOAL eng-001 pasted"
    wait_for(lambda: "/goal The goal for" in pasted(instance, "eng-001"), what="the pointer")


def test_goal_now_pastes_into_a_busy_pane(instance, hx, launched, goals, tmux_server):
    """`--now` is used from the `context` hook on `clear` and from the `stop` hook (spec 08)."""
    from .test_transitions import hold_pane

    launched("eng-001")
    goals("eng-001")
    assert hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance).returncode == 0
    (instance / "run" / "eng-001" / "fake-input.log").write_text("")
    hold_pane(tmux_server, "eng-001")

    result = hx("goal", "eng-001", "--now")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "HX-GOAL eng-001 pasted"
    wait_for(lambda: "/goal The goal for" in pasted(instance, "eng-001"), what="the pointer")


def test_goal_without_a_pane_says_so(instance, hx):
    from hx.lifecycle import ensure_work_item

    ensure_work_item(instance, "eng-001")
    result = hx("goal", "eng-001")
    assert result.returncode == 2
    assert "no tmux pane" in result.stderr


def test_the_pointer_never_carries_the_order(instance, hx, launched, goals):
    """spec 06: what `hx goal` pastes is a fixed short form that never grows with the task."""
    launched("eng-001")
    goals("eng-001", goal="A very long order " * 50)
    assert hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance).returncode == 0
    wait_for(lambda: "/goal" in pasted(instance, "eng-001"), what="the pointer")
    pointer = [line for line in pasted(instance, "eng-001").split("\n") if line.startswith("/goal")][0]
    assert "A very long order" not in pointer
    assert len(pointer) < 400


# --- hx restart, up, heartbeat ---------------------------------------------------------------------


def test_restart_relaunches_and_resends_the_goal(instance, hx, launched, goals, tmux_server):
    launched("eng-001")
    # `hx dispatch` clears `run/<id>/` except home/ and persona.md (spec 08), so the launch
    # record has to be read before it.
    first = json.loads((instance / "run" / "eng-001" / "fake-argv.json").read_text())
    goals("eng-001")
    assert hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance).returncode == 0
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
    assert "/goal The goal for" in pasted(instance, "eng-001")


def test_restart_of_an_idle_item_sends_no_goal(instance, hx, launched):
    launched("eng-001")
    result = hx("restart", "eng-001")
    assert result.returncode == 0, result.stderr
    assert "goal=none" in result.stdout


def test_up_launches_every_config_id_partner_first(instance, hx, agent):
    agent("eng-002")
    result = hx("up")
    assert result.returncode == 0, result.stderr
    lines = [l for l in result.stdout.strip().split("\n") if l.startswith("HX-LAUNCH")]
    launched_ids = [line.split()[1] for line in lines]
    assert launched_ids[0] == "partner", "partner first (spec 08)"
    assert set(launched_ids) == {"partner", "eng-001", "eng-002"}
    for item_id in launched_ids:
        assert (instance / "run" / item_id / "home" / "settings.json").is_file()


def test_heartbeat_restarts_a_dead_session(instance, hx, launched, goals, tmux_server):
    launched("eng-001")
    goals("eng-001")
    assert hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance).returncode == 0
    wait_for(lambda: "/goal" in pasted(instance, "eng-001"), what="the pointer")
    subprocess.run([*tmux_server, "kill-session", "-t", "=eng-001"], check=True)

    result = hx("heartbeat")
    assert result.returncode == 0, result.stderr
    assert "restarted=eng-001" in result.stdout
    assert subprocess.run(
        [*tmux_server, "has-session", "-t", "=eng-001"], capture_output=True
    ).returncode == 0


def test_heartbeat_launches_a_dead_partner(instance, hx, launched, tmux_server):
    """spec 08, build-8 item 9: the Partner is not a board item, so it is checked on its own."""
    launched("partner")
    subprocess.run([*tmux_server, "kill-session", "-t", "=partner"], check=True)

    result = hx("heartbeat")
    assert result.returncode == 0, result.stderr
    assert "partner=launched" in result.stdout
    assert subprocess.run(
        [*tmux_server, "has-session", "-t", "=partner"], capture_output=True
    ).returncode == 0


def test_heartbeat_leaves_a_live_partner_alone(instance, hx, launched):
    launched("partner")
    assert "partner=alive" in hx("heartbeat").stdout


def test_heartbeat_repastes_the_goal_to_an_idle_working_agent(instance, hx, launched, goals):
    """build-8 item 12: a `/goal` evaluator that cleared itself leaves the agent stranded."""
    launched("eng-001")
    goals("eng-001")
    assert hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance).returncode == 0
    wait_for(lambda: "/goal" in pasted(instance, "eng-001"), what="the pointer")
    (instance / "run" / "eng-001" / "fake-input.log").write_text("")

    result = hx("heartbeat")
    assert result.returncode == 0, result.stderr
    assert "regoaled=eng-001" in result.stdout
    wait_for(lambda: "/goal The goal for" in pasted(instance, "eng-001"), what="the pointer again")


def test_heartbeat_does_not_repaste_to_an_agent_that_completed(instance, hx, launched, goals):
    """`HX-COMPLETE` in the stream means the agent is done, whatever the pane looks like."""
    from hx.streams import append_record

    launched("eng-001")
    goals("eng-001")
    assert hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance).returncode == 0
    wait_for(lambda: "/goal" in pasted(instance, "eng-001"), what="the pointer")
    append_record(instance, "eng-001", "eng-001-main", {
        "event": "post_tool", "tool": "Bash", "output": "HX-COMPLETE eng-001 done",
    })
    (instance / "run" / "eng-001" / "fake-input.log").write_text("")

    assert "regoaled=none" in hx("heartbeat").stdout
    assert "/goal" not in pasted(instance, "eng-001")


def test_heartbeat_does_not_repaste_to_a_busy_agent(instance, hx, launched, goals, tmux_server):
    from .test_transitions import hold_pane

    launched("eng-001")
    goals("eng-001")
    assert hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance).returncode == 0
    wait_for(lambda: "/goal" in pasted(instance, "eng-001"), what="the pointer")
    hold_pane(tmux_server, "eng-001")
    (instance / "run" / "eng-001" / "fake-input.log").write_text("")

    assert "regoaled=none" in hx("heartbeat").stdout


def test_heartbeat_wakes_the_partner_only_when_the_board_changed(instance, hx, launched, goals):
    launched("partner", "eng-001")
    goals("eng-001")
    assert hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance).returncode == 0

    first = hx("heartbeat")
    assert first.returncode == 0
    assert "changed=false" in first.stdout, "the first heartbeat has nothing to compare against"

    second = hx("heartbeat")
    assert "changed=false" in second.stdout, "nothing moved between the two"

    assert hx("complete", "done", harness_id="eng-001").returncode == 0
    third = hx("heartbeat")
    assert "changed=true" in third.stdout


# --- build-8 item 8: the UI is a tmux session hx starts --------------------------------------


def test_up_starts_the_ui_session_and_prints_the_url(instance, hx, tmux_server):
    """spec 16.1, 17.2: `hx up` brings up the read-only view with the fleet."""
    result = hx("up")
    assert result.returncode == 0, result.stderr
    assert "HX-UI http://127.0.0.1:8765/" in result.stdout
    assert subprocess.run(
        [*tmux_server, "has-session", "-t", "=ui"], capture_output=True
    ).returncode == 0


def test_starting_the_ui_is_idempotent(instance, hx, tmux_server):
    from hx.lifecycle import start_ui

    env = {"HX_TMUX": " ".join(tmux_server)}
    assert start_ui(instance, env=env) is True
    assert start_ui(instance, env=env) is False, "a live UI session is left alone"


def test_the_url_follows_config_ui_json(instance):
    import json

    from hx.lifecycle import ui_url

    assert ui_url(instance) == "http://127.0.0.1:8765/"
    (instance / "config" / "ui.json").write_text(json.dumps({"port": 9111}))
    assert ui_url(instance) == "http://127.0.0.1:9111/"


def test_heartbeat_restarts_the_ui_when_it_is_dead(instance, hx, launched, tmux_server):
    launched("partner")
    assert "ui=started" in hx("heartbeat").stdout
    assert "ui=alive" in hx("heartbeat").stdout

    subprocess.run([*tmux_server, "kill-session", "-t", "=ui"], check=True)
    assert "ui=started" in hx("heartbeat").stdout
    assert subprocess.run(
        [*tmux_server, "has-session", "-t", "=ui"], capture_output=True
    ).returncode == 0


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

    assert wake_partner(instance, "eng-001 done") is True
    thread.join(timeout=10)
    server.close()

    lines = [json.loads(line) for line in received[0].decode().strip().split("\n")]
    assert lines == [
        {"type": "auth", "token": "tok"},
        {"type": "user", "message": {"role": "user",
                                     "content": "eng-001 done"}},
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

    result = hx("wake", "partner", "eng-001 done")
    thread.join(timeout=10)
    server.close()
    assert result.stdout.strip().split("\n")[-1] == "HX-WAKE partner accepted"
    assert result.returncode == 0


def test_a_failed_wake_is_a_warning_not_a_failure(instance, hx, launched, goals):
    """`hx complete` still succeeds and still prints HX-COMPLETE last (spec 08)."""
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, goals)
    result = hx("complete", "done", harness_id="eng-001")
    assert result.returncode == 0
    assert result.stdout.strip().split("\n")[-1] == "HX-COMPLETE eng-001 done"
    assert "the Partner was not woken (no-socket)" in result.stderr


# --- readiness detection, against the real TUI's chrome ------------------------------------------
#
# These strings are the live pane chrome of Claude Code 2.1.278, captured on 2026-09-20 from
# a logged-in session (goal build-3 item 8, and `REAL_IDLE_PLACEHOLDER` from build-7's
# Companion live check). They are the record of what the real TUI draws; `hx.goal.pane_is_idle`
# is judged against them.

REAL_IDLE = "\n".join([
    "❯ ",
    "─" * 100,
    "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents",
    "",
])
#: A freshly launched pane with an empty input: the prompt carries a placeholder hint, so the
#: prompt line is not blank. This hung `hx launch` forever until build-7 (live, 2.1.278).
REAL_IDLE_PLACEHOLDER = "\n".join([
    "─" * 80,
    '❯ Try "create a util logging.py that..."',
    "─" * 80,
    "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents",
    "",
])
REAL_BUSY = "\n".join([
    "⏺ Bash(tmux capture-pane -p …)",
    "  ⎿  Running…",
    "",
    "· Seasoning… (11m 20s · ↓ 45.1k tokens)",
    "",
    "─" * 100,
    "❯ ",
    "─" * 100,
    "  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ← for agents"
    "                    ◎ /goal active (11m)",
    "",
])


def test_the_real_tui_idle_pane_reads_as_idle():
    from hx.goal import pane_is_idle

    assert pane_is_idle(REAL_IDLE) is True


def test_a_prompt_with_the_empty_input_placeholder_reads_as_idle():
    """Live, build-7: a fresh Companion pane draws `❯ Try "…"`, and it is idle."""
    from hx.goal import pane_is_idle

    assert pane_is_idle(REAL_IDLE_PLACEHOLDER) is True


def test_the_real_tui_busy_pane_reads_as_busy():
    """The input box is drawn mid-turn, so the prompt alone says nothing (build-3 item 8)."""
    from hx.goal import pane_is_idle

    assert "❯" in REAL_BUSY, "the prompt is present while the session works"
    assert pane_is_idle(REAL_BUSY) is False


def test_the_prompt_glyph_is_the_one_the_tui_draws():
    """`❯` (U+276F), not `>`; a pattern matching only `>` never matches a real pane."""
    from hx.goal import _REAL_PROMPT

    assert _REAL_PROMPT.match("❯ ")
    assert _REAL_PROMPT.match("❯")
    assert not _REAL_PROMPT.match("❯ something typed")


def test_only_the_prompt_glyph_carries_a_placeholder():
    """`> text` is ordinary output; `❯ text` is the input box with its hint (build-7)."""
    from hx.goal import _PLACEHOLDER_PROMPT, pane_is_idle

    assert _PLACEHOLDER_PROMPT.match('❯ Try "create a util logging.py that..."')
    assert not _PLACEHOLDER_PROMPT.match("> quoted output from a tool")
    assert pane_is_idle("> quoted output from a tool\n") is False


def test_a_pane_that_has_not_drawn_yet_is_not_idle(instance):
    """`hx launch` waits rather than pasting into a shell that has not become the TUI."""
    from hx.goal import pane_is_idle

    assert pane_is_idle("") is False
    assert pane_is_idle("$ \n") is False


# --- build-8 item 10: the input box, and what is still sitting in it ----------------------------
#
# The live chrome of a pane holding an unsubmitted paste (2.1.278, 2026-09-20 04:10 and 21:17).
# The Enter that followed `paste-buffer` was swallowed and the text stayed put.

REAL_UNSUBMITTED = "\n".join([
    "❯ /clear",
    "",
    "─" * 80,
    "❯ Companion pass: read /abs/run/eng-001/companion/eng-001-main.pass.md and do",
    "  what it says.",
    "",
    "─" * 80,
    "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    "",
])
REAL_SUBMITTED = "\n".join([
    "❯ Companion pass: read /abs/run/eng-001/companion/eng-001-main.pass.md and do",
    "  what it says.",
    "",
    "  Reading /abs/run/eng-001/companion/eng-001-main.pass.md",
    "",
    "─" * 80,
    '❯ Try "create a util logging.py that..."',
    "─" * 80,
    "  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt",
    "",
])
FAKE_UNSUBMITTED = "hx-fake-idle>\n/goal The goal for eng-001 is in /abs/item.md\n"
FAKE_SUBMITTED = "hx-fake-idle>\n/goal The goal for eng-001 is in /abs/item.md\nhx-fake-idle>\n"


def holds(pane, text):
    """What `hx.goal.submit` asks: is this text still waiting in the input box?"""
    from hx.goal import _squash, input_box

    return _squash(text)[:40] in _squash(input_box(pane))


def test_an_unsubmitted_paste_is_still_in_the_input_box():
    assert holds(REAL_UNSUBMITTED, "Companion pass: read /abs/run/eng-001/companion/"
                                   "eng-001-main.pass.md and do what it says.")


def test_a_submitted_paste_is_above_the_prompt_not_in_the_box():
    """The transcript is drawn above the prompt, so the echo there must not read as pending."""
    assert not holds(REAL_SUBMITTED, "Companion pass: read /abs/run/eng-001/companion/"
                                     "eng-001-main.pass.md and do what it says.")


def test_the_placeholder_is_not_mistaken_for_pending_text():
    assert not holds(REAL_SUBMITTED, "/clear")


def test_the_fake_pane_reads_the_same_way():
    """One detector for both, as with `pane_is_idle` (ORCHESTRATION.md "Fake claude")."""
    pointer = "/goal The goal for eng-001 is in /abs/item.md"
    assert holds(FAKE_UNSUBMITTED, pointer)
    assert not holds(FAKE_SUBMITTED, pointer)


def test_a_pane_with_no_prompt_yet_holds_everything():
    """Before the TUI draws, there is no box; the paste cannot have been submitted."""
    assert holds("starting up\n", "starting up")
