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

#: Readiness detection, in one place (goal build-2 item 3).
#:
#: The fake `claude` prints `hx-fake-idle>` when it is waiting for input. The real Claude Code
#: TUI draws an input box whose last line is a `>` prompt, optionally inside the box border;
#: `_REAL_PROMPT` is the pattern to verify against the pinned binary at M6, when the live
#: suite first runs against it. Until then only the fake's line is authoritative.
_FAKE_PROMPT = re.compile(r"^\s*hx-fake-idle>\s*$")
_REAL_PROMPT = re.compile(r"^\s*(?:[│|]\s*)?>\s*(?:[│|]\s*)?$")
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
    """True when the pane's last non-empty line is an idle prompt.

    One function, as goal build-2 item 3 asks, so M6 has a single place to correct once the
    real TUI's prompt is confirmed against the pinned binary.
    """
    for line in reversed(pane_text.split("\n")):
        if line.strip() == "":
            continue
        return any(pattern.match(line) for pattern in IDLE_PROMPTS)
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
