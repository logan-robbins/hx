"""`hx goal` — the fixed pointer, pasted into the pane (spec 06, 08, 02 "Goal delivery").

What is pasted never grows with the task: it names the work item and the proof line, and the
order itself is read from the file. Claude Code is always launched bare; nothing is ever a
prompt argument.

Delivery follows spec 08: at the idle prompt, paste now; mid-turn (the Partner dispatching or
resuming itself from its own Bash tool), leave `run/<id>/goal-pending` and return — the `stop`
hook pastes it at the end of that turn. `--now` skips the check and is used from the `context`
hook on `clear` and from the `stop` hook.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import tempfile
from pathlib import Path

from . import timestamps, tmux
from .errors import HxError, NotFound
from .ids import PARTNER
from .workitems import require_work_item, state_of

#: Spec 06, verbatim. `<outcome>` is literal: the agent chooses it when it finishes.
POINTER = (
    "/goal The order for {id} is in {path}; read it first. "
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
# Two things follow, and both matter:
#
#   1. The prompt glyph is `❯` (U+276F), not `>`.
#   2. **The input box is drawn while the session is working**, so the presence of a prompt
#      says nothing about whether the pane is ready. The status bar is the signal: it carries
#      `esc to interrupt` exactly while a turn is in flight.
#
# So a pane is busy when the status bar offers to interrupt it, and idle when the TUI is up
# and it does not. Matching on the prompt alone — which is what this did before the live
# check — would have read every real pane as busy forever, and `hx launch`'s wait has no
# timeout.
_BUSY_MARKERS = ("esc to interrupt",)
_REAL_PROMPT = re.compile(r"^\s*(?:[│|]\s*)?[❯>]\s*(?:[│|]\s*)?$")
_FAKE_PROMPT = re.compile(r"^\s*hx-fake-idle>\s*$")
IDLE_PROMPTS = (_FAKE_PROMPT, _REAL_PROMPT)

GOAL_MARKER = "goal"
GOAL_PENDING_MARKER = "goal-pending"


def run_dir(root: Path, item_id: str) -> Path:
    return root / "run" / item_id


def marker(root: Path, item_id: str) -> Path:
    return run_dir(root, item_id) / GOAL_MARKER


def pending_marker(root: Path, item_id: str) -> Path:
    return run_dir(root, item_id) / GOAL_PENDING_MARKER


def pointer_text(root: Path, item_id: str) -> str:
    """The pointer for an id, naming the absolute path of its work item (spec 06)."""
    return POINTER.format(id=item_id, path=require_work_item(root, item_id).resolve())


def pane_is_idle(pane_text: str) -> bool:
    """True when `<id>:main` is waiting for input. One function, by design.

    The real TUI and the fake are told apart by what they draw, not by a flag, so the same
    detector serves the fake suites and the live binary.
    """
    lines = pane_text.split("\n")

    # The real TUI: the status bar offers to interrupt exactly while a turn is in flight.
    if any(marker in line for line in lines for marker in _BUSY_MARKERS):
        return False
    if any(_REAL_PROMPT.match(line) for line in lines):
        return True

    # The fake, and anything else: the last non-empty line is the prompt, or it is not.
    for line in reversed(lines):
        if line.strip() == "":
            continue
        return bool(_FAKE_PROMPT.match(line))
    return False


def capture_pane(item_id: str, env=None, *, lines: int = 40) -> str | None:
    """The tail of `<id>:main`, or None when there is no such pane."""
    result = subprocess.run(
        [*tmux.tmux_command(env), "capture-pane", "-p", "-t", f"={item_id}:main", "-S", f"-{lines}"],
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def paste(item_id: str, text: str, env=None) -> None:
    """Paste through a tmux buffer loaded from a file, then Enter (goal build-2 item 3).

    `load-buffer` from a file rather than `set-buffer` with an argument, so the pointer never
    passes through a command line and no shell can touch it.
    """
    command = tmux.tmux_command(env)
    buffer_name = f"hx-goal-{item_id}"
    with tempfile.NamedTemporaryFile("w", prefix="hx-goal-", suffix=".txt", delete=False) as handle:
        handle.write(text)
        source = handle.name
    try:
        subprocess.run([*command, "load-buffer", "-b", buffer_name, source], check=True)
        subprocess.run([*command, "paste-buffer", "-b", buffer_name, "-t", f"={item_id}:main"], check=True)
        subprocess.run([*command, "send-keys", "-t", f"={item_id}:main", "Enter"], check=True)
    finally:
        subprocess.run([*command, "delete-buffer", "-b", buffer_name], capture_output=True, check=False)
        Path(source).unlink(missing_ok=True)


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
    """Deliver the pointer. Returns `pasted` or `pending`.

    `now` pastes without looking at the pane; `wait` blocks until the prompt appears and is
    what `hx launch` and `hx restart` use once the pane is up.
    """
    text = pointer_text(root, item_id)
    run_dir(root, item_id).mkdir(parents=True, exist_ok=True)

    if not now:
        if wait:
            wait_for_prompt(item_id, env)
        else:
            pane = capture_pane(item_id, env)
            if pane is None:
                raise NotFound(
                    f"{item_id}: no tmux pane {item_id}:main to paste the goal into; "
                    f"`hx launch {item_id}` starts it"
                )
            if not pane_is_idle(pane):
                # Mid-turn: the `stop` hook pastes it at the end of this turn (spec 08, 09).
                # The marker is written in both cases, with the timestamp of the call, so the
                # `working` invariant holds during the turn in which a busy pane is owed its
                # goal; the stop hook rewrites it when it pastes (spec 08).
                ts = timestamps.now()
                pending_marker(root, item_id).write_text(ts + "\n")
                marker(root, item_id).write_text(ts + "\n")
                return "pending"

    paste(item_id, text, env)
    marker(root, item_id).write_text(timestamps.now() + "\n")
    pending_marker(root, item_id).unlink(missing_ok=True)
    return "pasted"


def clear_marker(root: Path, item_id: str) -> None:
    marker(root, item_id).unlink(missing_ok=True)


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx goal", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--now", action="store_true", help="paste without checking the pane (hooks only)")
    parser.add_argument("--wait", action="store_true", help="wait for the idle prompt, then paste")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.now and args.wait:
        raise HxError("goal: --now and --wait are mutually exclusive")
    outcome = send_goal(root, args.id, now=args.now, wait=args.wait, env=env)
    print(f"HX-GOAL {args.id} {outcome}")
    return 0
