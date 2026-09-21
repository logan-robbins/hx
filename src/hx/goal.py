"""`hx goal` — the fixed pointer, pasted into the pane (spec 06, 08, 02 "Goal delivery").

What is pasted never grows with the task: it names the work item and the proof line, and the
goal itself is read from the file. Claude Code is always launched bare; nothing is ever a
prompt argument.

Workers only: the Partner has no work item and no goal, and is never a target (spec 12).

Delivery follows spec 08: `hx goal` waits for the idle prompt and then pastes. `--now` skips
the wait and is used only from the `context` hook on `clear` (E3), where the pane is by
construction about to be ready. The v1 cut (spec 14 D25) removed `run/<id>/goal-pending` and
its delivery from the `stop` hook: it existed only for the Partner dispatching itself.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
from pathlib import Path

from . import timestamps, tmux
from .errors import HxError, NotFound, Refused
from .ids import PARTNER
from .workitems import require_work_item, state_of

#: Spec 06, verbatim. `<outcome>` is literal: the agent chooses it when it finishes.
POINTER = (
    "/goal The goal for {id} is in {path}; read it first. "
    "Done when `hx complete <outcome>` has been run and its output line "
    "`HX-COMPLETE {id} <outcome>` appears."
)

# Readiness detection, in one place (goal build-2 item 3), verified against the pinned binary
# 2.1.278 on 2026-09-20 by capturing live panes (goal build-3 item 8).
#
# What the real TUI actually shows, which is not what the docs suggest:
#
#   ──────────────────────────────────────────────────────────────────────
#   ❯
#   ──────────────────────────────────────────────────────────────────────
#     ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents            <- idle
#     ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · …    <- busy
#
# Three things follow, and all three matter:
#
#   1. The prompt glyph is `❯` (U+276F), not `>`.
#   2. **The input box is drawn while the session is working**, so the presence of a prompt
#      says nothing about whether the pane is ready. The status bar is the signal: it carries
#      `esc to interrupt` exactly while a turn is in flight.
#   3. **The empty input carries a placeholder**, so the prompt line is often
#      `❯ Try "create a util logging.py that..."` rather than a bare `❯` (live, 2.1.278,
#      build-7). A regex that demanded an empty prompt line hung `hx launch` forever on a
#      freshly launched Companion, whose pane is idle and drawing exactly that.
#
# So a pane is busy when the status bar offers to interrupt it, and idle when the TUI is up
# and it does not. Matching on the prompt alone — which is what this did before the live
# check — would have read every real pane as busy forever, and `hx launch`'s wait has no
# timeout.
_BUSY_MARKERS = ("esc to interrupt",)
#: Pi 0.84 draws these only while a turn, compaction, or retry is in flight
#: (`WorkingStatusIndicator` and the compaction/retry indicators). The startup
#: banner says "to interrupt" and must not count: that line is on screen when idle.
_PI_BUSY_MARKERS = (
    "Working...",
    "Auto-compacting",
    "Compacting context",
    "Summarizing branch",
    "Retrying (",
)
#: Footer from `footer.js` when auto-compaction is on: `12.4%/200k (auto)` or `?/1.0M (auto)`.
_PI_IDLE_FOOTER = re.compile(r"(?:\d+\.\d+%|\?)/[\d.]+[kM]? \(auto\)")
#: A bare prompt, with or without the box borders around it.
_REAL_PROMPT = re.compile(r"^\s*(?:[│|]\s*)?[❯>]\s*(?:[│|]\s*)?$")
#: The same prompt with the empty-input placeholder after it. The `❯` glyph is required here:
#: a plain `>` followed by text is ordinary output, not a prompt.
_PLACEHOLDER_PROMPT = re.compile(r"^\s*(?:[│|]\s*)?❯\s+\S")
_FAKE_PROMPT = re.compile(r"^\s*hx-fake-idle>\s*$")
IDLE_PROMPTS = (_FAKE_PROMPT, _REAL_PROMPT)

GOAL_MARKER = "goal"


def run_dir(root: Path, item_id: str) -> Path:
    return root / "run" / item_id


def marker(root: Path, item_id: str) -> Path:
    return run_dir(root, item_id) / GOAL_MARKER


def pointer_text(root: Path, item_id: str) -> str:
    """The pointer for an id, naming the absolute path of its work item (spec 06)."""
    if item_id == PARTNER:
        raise Refused(
            "refuse: the Partner is never sent a `/goal`; the human tells it what to do in "
            "chat (spec 06, 12, spec 14 D25)"
        )
    return POINTER.format(id=item_id, path=require_work_item(root, item_id).resolve())


def pane_is_idle(pane_text: str) -> bool:
    """True when `<id>:main` is waiting for input. One function, by design.

    The real TUI and the fake are told apart by what they draw, not by a flag, so the same
    detector serves the fake suites and the live binary.
    """
    lines = pane_text.split("\n")

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

    # The fake, and anything else: the last non-empty line is the prompt, or it is not.
    for line in reversed(lines):
        if line.strip() == "":
            continue
        return bool(_FAKE_PROMPT.match(line))
    return False


def target_of(name: str) -> str:
    """`eng-001` means `eng-001:main`; `eng-001:companion` is taken as written."""
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


# --- submitting a paste (build-8 item 10) ------------------------------------------------------
#
# Live, 2.1.278, twice on 2026-09-20 (build-7's Companion check at 04:10 and the orchestrator's
# Partner rehearsal at 21:17): an Enter sent immediately after `paste-buffer` is **swallowed**.
# The TUI is still ingesting the paste, the Enter is eaten with it, and the text sits in the
# input box forever — on a fresh pane, where the first paste of a session lands. A later Enter
# submits the same text fine, so this is a race, not a rejection.
#
# Every paste in hx goes through this one function — the `/goal` pointer, `hx seam`'s `/clear`,
# and both pastes of every Companion wake — so the fix belongs here and nowhere else: watch the
# input box rather than guess at a delay.

#: The line that draws the input prompt, in the real TUI or in the fake.
_PROMPT_LINE = re.compile(r"^\s*(?:[│|]\s*)?(?:[❯>]|hx-fake-idle>)")
#: The glyph itself, stripped so the rest of that line is what is *in* the box.
_PROMPT_GLYPH = re.compile(r"^\s*(?:[│|]\s*)?(?:[❯>]|hx-fake-idle>)\s?")

#: How many capture-pane polls each stage gets before the next step happens anyway. This is
#: not a timeout in the spec-08 sense — nothing fails when it runs out, hx simply presses
#: Enter again — and it is what keeps a paste from hanging a launch forever.
_POLL_ATTEMPTS = 30
_POLL_INTERVAL_S = 0.05

#: How many times Enter is pressed before hx stops trying. Two are routinely needed: a
#: **slash command** opens the autocomplete menu as it is typed, and the first Enter goes to
#: the menu, not to the prompt (live, 2.1.278, 2026-09-20 — `hx seam` pasted `/clear` from
#: inside the `stop` hook and it sat in the box with the menu open until a second Enter).
#: Everything hx pastes but the `/goal` pointer and the Companion pass is a slash command.
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


def paste(name: str, text: str, env=None) -> None:
    """Paste through a tmux buffer loaded from a file, then submit it (build-2 item 3).

    `load-buffer` from a file rather than `set-buffer` with an argument, so what is pasted
    never passes through a command line and no shell can touch it.

    Submitting is not one `send-keys Enter`: wait for the pasted text to appear in the input
    box, press Enter, wait for the box to let go of it, and press Enter once more if it has
    not. No fixed sleep anywhere — each stage returns the moment its condition holds.
    """
    command = tmux.tmux_command(env)
    buffer_name = f"hx-goal-{name.replace(':', '-')}"
    with tempfile.NamedTemporaryFile("w", prefix="hx-goal-", suffix=".txt", delete=False) as handle:
        handle.write(text)
        source = handle.name
    try:
        subprocess.run([*command, "load-buffer", "-b", buffer_name, source], check=True)
        subprocess.run([*command, "paste-buffer", "-b", buffer_name, "-t", target_of(name)], check=True)
        submit(name, text, env)
    finally:
        subprocess.run([*command, "delete-buffer", "-b", buffer_name], capture_output=True, check=False)
        Path(source).unlink(missing_ok=True)


def submit(name: str, text: str, env=None) -> bool:
    """Press Enter until the pasted text has left the input box (build-8 item 10).

    Two things eat an Enter, and both were found live on 2.1.278:

      1. the paste itself — an Enter arriving while the TUI is still ingesting it is
         swallowed with it, which is what stranded the first paste of every session;
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
        subprocess.run([*command, "send-keys", "-t", target_of(name), "Enter"], check=True)
        if _poll(name, env, wanted=False, probe=probe):
            return True
    return False


def wait_for_prompt(item_id: str, env=None) -> None:
    """Block until `<id>:main` shows the idle prompt. No timeout (spec 08)."""
    import time

    while True:
        pane = capture_pane(item_id, env)
        if pane is None:
            raise NotFound(f"{item_id}: no tmux pane {item_id}:main; `hx launch {item_id}` starts it")
        if pane_is_idle(pane):
            return
        time.sleep(0.05)


def send_goal(root: Path, item_id: str, *, now: bool = False, wait: bool = False, env=None) -> str:
    """Deliver the pointer. Returns `pasted`.

    It waits for the idle prompt first (no timeout, spec 08) unless `now` is set, which is
    the `context` hook on `clear`, where the pane is by construction about to be ready.
    """
    text = pointer_text(root, item_id)
    run_dir(root, item_id).mkdir(parents=True, exist_ok=True)

    if not now:
        if capture_pane(item_id, env) is None:
            raise NotFound(
                f"{item_id}: no tmux pane {item_id}:main to paste the goal into; "
                f"`hx launch {item_id}` starts it"
            )
        wait_for_prompt(item_id, env)

    paste(item_id, text, env)
    marker(root, item_id).write_text(timestamps.now() + "\n")
    return "pasted"


def clear_marker(root: Path, item_id: str) -> None:
    marker(root, item_id).unlink(missing_ok=True)


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx goal", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--now", action="store_true", help="paste without waiting (the `clear` hook)")
    parser.add_argument("--wait", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.now and args.wait:
        raise HxError("goal: --now and --wait are mutually exclusive")
    outcome = send_goal(root, args.id, now=args.now, wait=args.wait, env=env)
    print(f"HX-GOAL {args.id} {outcome}")
    return 0
