"""Ledger lifecycle, root recovery, and generic pane transport contracts."""
import json
import socket
import subprocess
import tempfile
import threading
from pathlib import Path

import pytest

from hx import lifecycle, application_loop
from hx.continuity_store import Conflict, ContinuityStore


def test_every_worker_launch_requires_explicit_run_and_request(tmp_path):
    for run, request in ((None, None), ('run', None), (None, 'request')):
        with pytest.raises(Conflict, match='requires --run'):
            lifecycle.launch(tmp_path, 'eng-001', run_id=run, request_id=request, env={})
    assert not (tmp_path / 'state').exists()
    assert not (tmp_path / 'pods').exists()


def test_launch_requires_partner_before_admission(tmp_path):
    with pytest.raises(Exception, match='HARNESS_ID is `eng-001`'):
        lifecycle.launch(tmp_path, 'eng-001', run_id='R', request_id='Q', env={'HARNESS_ID': 'eng-001'})


def test_worker_launch_advances_only_named_ledger_identity(tmp_path, monkeypatch):
    from hx import native_controller
    calls = []
    def advance(ledger, worker, run, request, **kw):
        calls.append((ledger.root, worker, run, request))
        return {'run_id': run, 'status': 'ready'}
    monkeypatch.setattr(native_controller, 'advance', advance)
    assert lifecycle.launch(tmp_path, 'eng-001', run_id='R', request_id='Q', env={})['run_id'] == 'R'
    assert calls == [(tmp_path, 'eng-001', 'R', 'Q')]
    assert not (tmp_path / 'pods').exists()
    assert not (tmp_path / 'tasks.json').exists()


def test_partner_cannot_be_assigned_a_worker_run(tmp_path):
    with pytest.raises(Conflict, match='cannot own a worker run'):
        lifecycle.launch(tmp_path, 'partner', run_id='R', request_id='Q', env={})


def test_up_recovers_services_without_launching_configured_workers(tmp_path, monkeypatch):
    for worker in ('partner', 'eng-001', 'qa-001'):
        (tmp_path / 'config' / worker).mkdir(parents=True)
    launches = []
    monkeypatch.setattr(lifecycle.tmux, 'has_session', lambda *a, **kw: False)
    monkeypatch.setattr(lifecycle, 'launch', lambda root, worker, **kw: launches.append(worker) or {'id': worker, 'session': 'started'})
    monkeypatch.setattr(lifecycle, 'start_ui', lambda *a, **kw: True)
    monkeypatch.setattr(application_loop, 'ensure', lambda *a, **kw: {'pid': 77})
    result = lifecycle.up(tmp_path, env={})
    assert launches == ['partner']
    assert result['service'] == {'pid': 77}
    assert result['status']['active'] == []
    assert not (tmp_path / 'pods').exists()


def test_gated_heartbeat_does_not_start_services(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, '_recover', lambda *a, **kw: pytest.fail('gated root must stay stopped'))
    assert lifecycle.heartbeat(tmp_path, env={}, require_go=True)['gated']


def test_heartbeat_recovers_the_same_root_service(tmp_path, monkeypatch):
    monkeypatch.setattr(lifecycle, '_recover', lambda *a, **kw: {'service': {'pid': 42}, 'status': {'active': []}})
    result = lifecycle.heartbeat(tmp_path, env={})
    assert not result['gated'] and result['service']['pid'] == 42


def test_launch_cli_does_not_admit_an_unassigned_worker(tmp_path, capsys):
    with pytest.raises(SystemExit) as failed:
        lifecycle.main_launch(['eng-001'], tmp_path, env={})
    assert failed.value.code == 2
    assert 'hx plan assign' in capsys.readouterr().err
    assert not (tmp_path / 'state').exists()


def test_the_url_follows_config_ui_json(instance):
    (instance / 'config/ui.json').write_text(json.dumps({'port': 18473}))
    assert lifecycle.ui_url(instance).endswith(':18473/')


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
    """Auth line, then the user message, newline-terminated (observed live)."""
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



# --- readiness detection, against the real TUI's chrome ------------------------------------------
#
# These strings are the live pane chrome of Claude Code, captured from a logged-in session.
# They are the record of what the real TUI draws; `hx.goal.pane_is_idle` is judged against
# them.

REAL_IDLE = "\n".join([
    "❯ ",
    "─" * 100,
    "  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents",
    "",
])
#: A freshly launched pane with an empty input: the prompt carries a placeholder hint, so the
#: prompt line is not blank; a detector that misses it leaves `hx launch` waiting forever.
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
    """A fresh pane draws `❯ Try "…"`, and it is idle (observed live)."""
    from hx.goal import pane_is_idle

    assert pane_is_idle(REAL_IDLE_PLACEHOLDER) is True


def test_the_real_tui_busy_pane_reads_as_busy():
    """The input box is drawn mid-turn, so the prompt alone says nothing."""
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
    """`> text` is ordinary output; `❯ text` is the input box with its hint."""
    from hx.goal import _PLACEHOLDER_PROMPT, pane_is_idle

    assert _PLACEHOLDER_PROMPT.match('❯ Try "create a util logging.py that..."')
    assert not _PLACEHOLDER_PROMPT.match("> quoted output from a tool")
    assert pane_is_idle("> quoted output from a tool\n") is False


def test_a_pane_that_has_not_drawn_yet_is_not_idle(instance):
    """`hx launch` waits rather than pasting into a shell that has not become the TUI."""
    from hx.goal import pane_is_idle

    assert pane_is_idle("") is False
    assert pane_is_idle("$ \n") is False


#: Codex idle chrome, captured live. The `›` is U+203A.
CODEX_IDLE = "\n".join([
    "  >_ OpenAI Codex (v0.159.2)",
    "     /data/worktrees/hx-eng-032",
    "  permissions: YOLO mode",
    "",
    "› Ask Codex to do anything",
    "",
    "  GPT-6-Astra xhigh · /data/worktrees/hx-eng-032",
    "  ? for shortcuts                                     ⚠ 2 warnings · f2 to view",
    "",
])
#: The same box while a turn is in flight: the prompt says nothing, the
#: `esc to interrupt` working line is the signal — the Claude lesson, repeated.
CODEX_BUSY = "\n".join([
    "• Goal active Objective: The goal for eng-032 is in /home/azureuser/hx-dfa/pods/",
    "eng/eng-032-working.md; read it first.",
    "",
    "◦ Working (7s • esc to interrupt)",
    "",
    "› Ask Codex to do anything",
    "",
    "  GPT-6-Astra xhigh · /data/worktrees/hx-eng-032            Pursuing goal (7s)",
    "",
])


def test_a_codex_idle_pane_reads_as_idle():
    """`› Ask Codex…` is the idle box; a detector that misses it hangs dispatch (observed live)."""
    from hx.goal import pane_is_idle

    assert pane_is_idle(CODEX_IDLE) is True


def test_a_codex_busy_pane_reads_as_busy():
    """The input box stays drawn mid-turn; `esc to interrupt` decides."""
    from hx.goal import pane_is_idle

    assert "› Ask Codex" in CODEX_BUSY, "the prompt is present while Codex works"
    assert pane_is_idle(CODEX_BUSY) is False


# --- the input box, and what is still sitting in it -------------------------------------------
#
# The live chrome of a pane holding an unsubmitted paste.
# The Enter that followed `paste-buffer` was swallowed and the text stayed put. The pasted
# pointer is a stand-in; the box, the wrap and the transcript echo are as captured.

REAL_UNSUBMITTED = "\n".join([
    "❯ /clear",
    "",
    "─" * 80,
    "❯ The goal for eng-001 is in /abs/run/eng-001/goal/eng-001-main.md; read it and do",
    "  what it says.",
    "",
    "─" * 80,
    "  ⏵⏵ bypass permissions on (shift+tab to cycle)",
    "",
])
REAL_SUBMITTED = "\n".join([
    "❯ The goal for eng-001 is in /abs/run/eng-001/goal/eng-001-main.md; read it and do",
    "  what it says.",
    "",
    "  Reading /abs/run/eng-001/goal/eng-001-main.md",
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
    assert holds(REAL_UNSUBMITTED, "The goal for eng-001 is in /abs/run/eng-001/goal/"
                                   "eng-001-main.md; read it and do what it says.")


def test_a_submitted_paste_is_above_the_prompt_not_in_the_box():
    """The transcript is drawn above the prompt, so the echo there must not read as pending."""
    assert not holds(REAL_SUBMITTED, "The goal for eng-001 is in /abs/run/eng-001/goal/"
                                     "eng-001-main.md; read it and do what it says.")


def test_the_placeholder_is_not_mistaken_for_pending_text():
    assert not holds(REAL_SUBMITTED, "/clear")


def test_the_fake_pane_reads_the_same_way():
    """One detector for both, as with `pane_is_idle`."""
    pointer = "/goal The goal for eng-001 is in /abs/item.md"
    assert holds(FAKE_UNSUBMITTED, pointer)
    assert not holds(FAKE_SUBMITTED, pointer)


def test_a_pane_with_no_prompt_yet_holds_everything():
    """Before the TUI draws, there is no box; the paste cannot have been submitted."""
    assert holds("starting up\n", "starting up")
