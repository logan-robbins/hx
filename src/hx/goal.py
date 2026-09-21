"""`hx goal` — the fixed pointer, pasted into the pane (spec 06, 08, 02 "Goal delivery").

What is pasted never grows with the task: it names the work item and the proof line, and the
order itself is read from the file. Claude Code is always launched bare; nothing is ever a
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


def paste(name: str, text: str, env=None) -> None:
    """Paste through a tmux buffer loaded from a file, then Enter (goal build-2 item 3).

    `load-buffer` from a file rather than `set-buffer` with an argument, so what is pasted
    never passes through a command line and no shell can touch it.
    """
    command = tmux.tmux_command(env)
    buffer_name = f"hx-goal-{name.replace(':', '-')}"
    with tempfile.NamedTemporaryFile("w", prefix="hx-goal-", suffix=".txt", delete=False) as handle:
        handle.write(text)
        source = handle.name
    try:
        subprocess.run([*command, "load-buffer", "-b", buffer_name, source], check=True)
        subprocess.run([*command, "paste-buffer", "-b", buffer_name, "-t", target_of(name)], check=True)
        subprocess.run([*command, "send-keys", "-t", target_of(name), "Enter"], check=True)
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
