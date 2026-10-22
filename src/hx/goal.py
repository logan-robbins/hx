"""Generic tmux pane observation and bounded prompt transport.

Assignment authority belongs to the continuity ledger and native controller.
This module only observes panes and transports text; it never reads task files.
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from . import tmux

_BUSY_MARKERS = ("esc to interrupt",)


_PI_BUSY_MARKERS = (
    "Working...",
    "Auto-compacting",
    "Compacting context",
    "Summarizing branch",
    "Retrying (",
)


_PI_IDLE_FOOTER = re.compile(r"(?:\d+\.\d+%|\?)/[\d.]+[kM]? \(auto\)")


_CODEX_IDLE_PROMPT = re.compile(r"^\s*›\s+Ask Codex")


_REAL_PROMPT = re.compile(r"^\s*(?:[│|]\s*)?[❯>]\s*(?:[│|]\s*)?$")


_PLACEHOLDER_PROMPT = re.compile(r"^\s*(?:[│|]\s*)?❯\s+\S")


_FAKE_PROMPT = re.compile(r"^\s*hx-fake-idle>\s*$")


IDLE_PROMPTS = (_FAKE_PROMPT, _REAL_PROMPT)


def replacement_prompt(pane: str) -> str | None:
    """Recognize the observed Codex dialog, not a mention in ordinary transcript text."""
    match = re.search(
        r"(?ms)^\s*Replace goal\?\s*\n\s*New objective:\s*(.*?)"
        r"\n\s*(?:› )?1\. Replace current goal\s+Set the new objective and start it now\s*\n"
        r"\s*(?:› )?2\. Cancel\s+Keep the current goal\s*\n\s*enter select · esc back\s*\Z",
        pane,
    )
    return match.group(1) if match else None


def input_is_empty(pane_text: str) -> bool:
    """Idle with an empty input box: a reset pasted now cannot land on a human's draft.

    A placeholder line is not proof of an empty box (typed text looks the same in a
    capture), so only the bare prompt counts.
    """
    return pane_is_idle(pane_text) and any(
        prompt.match(line) for line in pane_text.split("\n") for prompt in IDLE_PROMPTS)


def pane_is_idle(pane_text: str) -> bool:
    """True when the pane is waiting for input. One function, by design.

    The real TUI and the fake are told apart by what they draw, not by a flag, so the same
    detector serves the fake suites and the live binary.
    """
    lines = pane_text.split("\n")
    if replacement_prompt(pane_text) is not None:
        return False

    # Pi, checked before Claude's "esc to interrupt": a Pi idle banner can contain
    # that phrase as a keybinding hint, while a busy Pi pane says "Working...".
    if any(marker in line for line in lines for marker in _PI_BUSY_MARKERS):
        return False
    if any(_PI_IDLE_FOOTER.search(line) for line in lines):
        return True

    # The real TUI: the status bar offers to interrupt exactly while a turn is in flight.
    if any(marker in line for line in lines for marker in _BUSY_MARKERS):
        return False
    if any(_REAL_PROMPT.match(line) or _PLACEHOLDER_PROMPT.match(line) for line in lines):
        return True
    if any(_CODEX_IDLE_PROMPT.match(line) for line in lines):
        return True

    # The fake, and anything else: the last non-empty line is the prompt, or it is not.
    for line in reversed(lines):
        if line.strip() == "":
            continue
        return bool(_FAKE_PROMPT.match(line))
    return False


def target_of(name: str) -> str:
    """Resolve an hx window name or a tmux pane id from the current hook."""
    if re.fullmatch(r"%[0-9]+", name):
        return name
    return f"={name}" if ":" in name else f"={name}:main"


def capture_pane(name: str, env=None, *, lines: int = 40) -> str | None:
    """The tail of a pane, or None when there is no such pane."""
    result = subprocess.run(
        [*tmux.tmux_command(env), "capture-pane", "-p", "-t", target_of(name), "-S", f"-{lines}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


_PROMPT_LINE = re.compile(r"^\s*(?:[│|]\s*)?(?:[❯>›]|hx-fake-idle>)")


_PROMPT_GLYPH = re.compile(r"^\s*(?:[│|]\s*)?(?:[❯>›]|hx-fake-idle>)\s?")


_POLL_ATTEMPTS = 30


_POLL_INTERVAL_S = 0.05


_ENTER_ATTEMPTS = 4


def _squash(text: str) -> str:
    """Drop every space and newline, so a probe survives the TUI wrapping what it drew."""
    return "".join(text.split())


def input_box(pane_text: str) -> str:
    """What is sitting in the input box: everything from the last prompt glyph onward.

    The transcript is drawn *above* the prompt, so text that has been submitted is not in
    here — which is the whole point. Text still waiting to be sent is.
    """
    lines = pane_text.split("\n")
    for index in range(len(lines) - 1, -1, -1):
        if _PROMPT_LINE.match(lines[index]):
            head = _PROMPT_GLYPH.sub("", lines[index], count=1)
            return "\n".join([head, *lines[index + 1 :]])
    return pane_text


def _poll(name: str, env, wanted: bool, probe: str) -> bool:
    """Poll the input box until `probe`'s presence matches `wanted`. True when it did."""
    import time

    for attempt in range(_POLL_ATTEMPTS):
        pane = capture_pane(name, env)
        if pane is None:
            return False
        if (probe in _squash(input_box(pane))) == wanted:
            return True
        if attempt + 1 < _POLL_ATTEMPTS:
            time.sleep(_POLL_INTERVAL_S)
    return False


def paste(name: str, text: str, env=None) -> bool:
    """Paste through a tmux buffer loaded from a file, then submit it.

    `load-buffer` from a file rather than `set-buffer` with an argument, so what is pasted
    never passes through a command line and no shell can touch it.

    Submitting is not one `send-keys Enter`: wait for the pasted text to appear in the input
    box, press Enter, wait for the box to let go of it, and press Enter once more if it has
    not. No fixed sleep anywhere — each stage returns the moment its condition holds.
    """
    command = tmux.tmux_command(env)
    buffer_name = f"hx-paste-{name.replace(':', '-')}"
    with tempfile.NamedTemporaryFile("w", prefix="hx-paste-", suffix=".txt", delete=False) as handle:
        handle.write(text)
        source = handle.name
    try:
        subprocess.run([*command, "load-buffer", "-b", buffer_name, source], check=True)
        subprocess.run([*command, "paste-buffer", "-p", "-r", "-b", buffer_name, "-t", target_of(name)], check=True)
        return submit(name, text, env)
    finally:
        subprocess.run([*command, "delete-buffer", "-b", buffer_name], capture_output=True, check=False)
        Path(source).unlink(missing_ok=True)


def submit(name: str, text: str, env=None) -> bool:
    """Press Enter until the pasted text has left the input box.

    Two things eat an Enter, and both were found live:

      1. the paste itself — an Enter arriving while the TUI is still ingesting it is
         swallowed with it, which strands the first paste of a session;
      2. the slash-command autocomplete — pasting `/clear` opens the command menu, and the
         first Enter goes to the menu rather than to the prompt.

    So this waits for the text to appear, then presses Enter and waits for the box to let go
    of it, and tries again if it has not. An Enter on an empty prompt submits nothing, so an
    extra press costs nothing. Returns whether the box did let go.
    """
    command = tmux.tmux_command(env)
    #: A short distinctive slice is enough, and short enough to survive any wrapping.
    probe = _squash(text)[:40]

    _poll(name, env, wanted=True, probe=probe)
    for _ in range(_ENTER_ATTEMPTS):
        from .board import pane_awaits_input

        pane = capture_pane(name, env)
        if pane is None or pane_awaits_input(pane.splitlines()):
            return False
        subprocess.run([*command, "send-keys", "-t", target_of(name), "Enter"], check=True)
        if _poll(name, env, wanted=False, probe=probe):
            return True
    return False
