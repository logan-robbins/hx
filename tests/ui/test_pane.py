"""Pane capture (spec 16.2, 16.4), against a real tmux on a private socket.

Every tmux test runs with `-L <unique socket>`, so it never touches the user's
tmux server or any lane's session. Nothing here sends keys to a pane it did not
create: `hx.ui.pane` is read-only by construction — it runs `capture-pane` and
`has-session`, and nothing else.
"""

from __future__ import annotations

import shutil
import subprocess
import time
import uuid

import pytest

from hx.ui.pane import PANE_LINES, capture, log_fallback, pane_targets, session_alive, strip_ansi

tmux_only = pytest.mark.skipif(shutil.which("tmux") is None, reason="no tmux on PATH")


@pytest.fixture
def tmux_socket():
    """A private tmux server, killed afterwards whatever happens."""
    if shutil.which("tmux") is None:
        pytest.skip("no tmux on PATH")
    name = f"hx-ui-test-{uuid.uuid4().hex[:12]}"
    yield name
    subprocess.run(["tmux", "-L", name, "kill-server"], capture_output=True, check=False)


def tmux(socket, *args):
    return subprocess.run(["tmux", "-L", socket, *args], capture_output=True, text=True, check=False)


def run_in_pane(socket, session, command, *, expect, tries=200):
    """Type a command into the pane and wait until its output shows up.

    Polling beats sleeping: a shell's startup time is not ours to guess, and the
    spec forbids timeouts in hx itself — this is a test waiting for a condition.
    """
    tmux(socket, "send-keys", "-t", f"={session}:", command, "Enter")
    for _ in range(tries):
        result = tmux(socket, "capture-pane", "-p", "-t", f"={session}:", "-S", "-200")
        if result.returncode == 0 and expect in result.stdout:
            return result.stdout
        time.sleep(0.02)
    raise AssertionError(f"{expect!r} never appeared in the pane; last capture:\n{result.stdout}")


@pytest.fixture
def live_session(tmux_socket):
    """A real session running a real shell, so sent commands actually execute."""
    session = "eng-001"
    created = tmux(tmux_socket, "new-session", "-d", "-s", session, "-x", "200", "-y", "50", "sh")
    assert created.returncode == 0, created.stderr
    for _ in range(200):
        if session_alive(session, socket=tmux_socket):
            break
        time.sleep(0.02)
    assert session_alive(session, socket=tmux_socket), "the test session never came up"
    return tmux_socket, session


# -- ANSI ----------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, clean",
    [
        ("\x1b[1;32mgreen\x1b[0m", "green"),
        ("\x1b]0;a title\x07after", "after"),
        ("\x1b]8;;https://example.com\x1b\\link\x1b]8;;\x1b\\", "link"),
        ("plain", "plain"),
        ("\x1b[38;5;213mfancy\x1b[m tail", "fancy tail"),
        ("keep\ttab\nand newline", "keep\ttab\nand newline"),
        ("\x07bell\x08back", "bellback"),
    ],
)
def test_strip_ansi(raw, clean):
    assert strip_ansi(raw) == clean


# -- live pane -----------------------------------------------------------

@tmux_only
def test_a_live_pane_is_captured_and_stripped(tmp_path, live_session):
    socket, session = live_session
    run_in_pane(socket, session, "printf '\\033[1;31mRED LINE\\033[0m\\n'", expect="RED LINE")
    pane = capture(tmp_path, session, socket=socket)
    assert pane["alive"] is True
    assert pane["source"] == "session"
    assert pane["error"] is None
    assert pane["session"] == session
    text = "\n".join(pane["lines"])
    assert "RED LINE" in text
    assert "\x1b" not in text, "ANSI is stripped even though capture-pane -e emits it"


@tmux_only
def test_capture_never_returns_more_than_the_limit(tmp_path, live_session):
    socket, session = live_session
    run_in_pane(socket, session, "for i in $(seq 1 400); do echo line-$i; done", expect="line-400")
    pane = capture(tmp_path, session, socket=socket)
    assert len(pane["lines"]) <= PANE_LINES
    assert PANE_LINES == 120, "spec 16.2: the last 120 lines"


@tmux_only
def test_a_smaller_limit_is_honoured(tmp_path, live_session):
    socket, session = live_session
    run_in_pane(socket, session, "for i in $(seq 1 60); do echo row-$i; done", expect="row-60")
    assert len(capture(tmp_path, session, lines=10, socket=socket)["lines"]) <= 10


@tmux_only
def test_session_alive_is_exact(live_session):
    socket, session = live_session
    assert session_alive(session, socket=socket) is True
    assert session_alive("eng-00", socket=socket) is False, "no prefix matching"
    assert session_alive("eng-0011", socket=socket) is False
    assert session_alive("nothing-here", socket=socket) is False


# -- dead pane -----------------------------------------------------------

def test_a_dead_session_with_no_log_says_so(tmp_path):
    pane = capture(tmp_path, "definitely-not-a-session")
    assert pane["alive"] is False
    assert pane["lines"] == []
    assert pane["source"] == "none"
    assert pane["error"]


def test_a_dead_session_falls_back_to_the_pane_log(tmp_path):
    """spec 03 / 11: `logs/<id>/<id>-pane.log`, written by `tmux pipe-pane -o`."""
    log = tmp_path / "logs" / "eng-009" / "eng-009-pane.log"
    log.parent.mkdir(parents=True)
    log.write_text("\x1b[32mfirst\x1b[0m\nsecond\nthird\n", encoding="utf-8")
    pane = capture(tmp_path, "eng-009")
    assert pane["alive"] is False, "a log is history, not a live pane"
    assert pane["source"] == "log"
    assert pane["lines"] == ["first", "second", "third"]
    assert "eng-009" in pane["error"]


def test_the_pane_log_is_tailed_not_read_whole(tmp_path):
    log = tmp_path / "logs" / "eng-009" / "eng-009-pane.log"
    log.parent.mkdir(parents=True)
    log.write_text("\n".join(f"line-{n}" for n in range(500)) + "\n", encoding="utf-8")
    lines = log_fallback(tmp_path, "eng-009")
    assert len(lines) == PANE_LINES
    assert lines[-1] == "line-499"


def test_no_pane_log_is_an_empty_list(tmp_path):
    assert log_fallback(tmp_path, "eng-404") == []


@tmux_only
def test_a_live_pane_wins_over_a_stale_log(tmp_path, live_session):
    socket, session = live_session
    log = tmp_path / "logs" / session / f"{session}-pane.log"
    log.parent.mkdir(parents=True)
    log.write_text("STALE LOG CONTENT\n", encoding="utf-8")
    run_in_pane(socket, session, "echo FRESH-FROM-THE-PANE", expect="FRESH-FROM-THE-PANE")
    pane = capture(tmp_path, session, socket=socket)
    assert pane["source"] == "session"
    assert "STALE LOG CONTENT" not in "\n".join(pane["lines"])


# -- read-only -----------------------------------------------------------

@tmux_only
def test_capture_does_not_disturb_the_session(tmp_path, live_session):
    socket, session = live_session
    before = tmux(socket, "list-sessions", "-F", "#{session_name} #{session_windows}").stdout
    for _ in range(5):
        capture(tmp_path, session, socket=socket)
    after = tmux(socket, "list-sessions", "-F", "#{session_name} #{session_windows}").stdout
    assert before == after
    assert session_alive(session, socket=socket)


@tmux_only
def test_capture_creates_no_session_for_an_unknown_id(tmp_path, tmux_socket):
    tmux(tmux_socket, "new-session", "-d", "-s", "keeper", "cat")
    capture(tmp_path, "not-a-session", socket=tmux_socket)
    names = tmux(tmux_socket, "list-sessions", "-F", "#{session_name}").stdout.split()
    assert names == ["keeper"], "capture must never create a pane"


# -- window selection ----------------------------------------------------

def test_pane_targets_prefer_the_main_window():
    """`hx launch` puts the agent in `main` and its Companion in `companion`."""
    assert pane_targets("eng-001") == ["=eng-001:main", "=eng-001:"]
    assert pane_targets("eng-001", "companion")[0] == "=eng-001:companion"


@tmux_only
def test_capture_reads_the_main_window_not_the_companion(tmp_path, tmux_socket):
    session = "eng-007"
    tmux(tmux_socket, "new-session", "-d", "-s", session, "-n", "main", "-x", "200", "-y", "50", "sh")
    for _ in range(200):
        if session_alive(session, socket=tmux_socket):
            break
        time.sleep(0.02)
    tmux(tmux_socket, "new-window", "-d", "-t", f"={session}:", "-n", "companion", "sh")
    run_in_pane(tmux_socket, session, "echo FROM-MAIN", expect="FROM-MAIN")
    tmux(tmux_socket, "send-keys", "-t", f"={session}:companion", "echo FROM-COMPANION", "Enter")
    time.sleep(0.4)

    pane = capture(tmp_path, session, socket=tmux_socket)
    text = "\n".join(pane["lines"])
    assert "FROM-MAIN" in text
    assert "FROM-COMPANION" not in text, "the Agent view shows the agent, not its Companion"

    companion = capture(tmp_path, session, socket=tmux_socket, window="companion")
    assert "FROM-COMPANION" in "\n".join(companion["lines"])


@tmux_only
def test_a_session_without_a_main_window_still_captures(tmp_path, tmux_socket):
    """The fallback target: a bare `=<session>:` resolves to its active pane."""
    session = "eng-008"
    tmux(tmux_socket, "new-session", "-d", "-s", session, "-n", "solo", "-x", "200", "-y", "50", "sh")
    for _ in range(200):
        if session_alive(session, socket=tmux_socket):
            break
        time.sleep(0.02)
    run_in_pane(tmux_socket, session, "echo NO-MAIN-WINDOW", expect="NO-MAIN-WINDOW")
    pane = capture(tmp_path, session, socket=tmux_socket)
    assert pane["alive"] is True
    assert "NO-MAIN-WINDOW" in "\n".join(pane["lines"])
