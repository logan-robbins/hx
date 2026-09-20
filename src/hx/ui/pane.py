"""tmux pane capture for the Agent and Partner views (spec 16.2, 16.4).

Adapted from autodev's `fleet.py` (`agent_output`, `session_snapshot`): run
`capture-pane`, strip ANSI, fall back to a log file when the pane is gone. Spec
16.4 says keep that pattern; what changed is the shape it feeds — a board row and
an agent view rather than a pillar.

Read-only: nothing here sends keys, creates or kills a session. The UI observes.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

#: Spec 16.2: the Agent view shows the last 120 lines of the pane.
PANE_LINES = 120

#: `hx launch` runs the agent in window `main` and its Companion in `companion`.
MAIN_WINDOW = "main"

#: CSI sequences, OSC strings (title sets, hyperlinks) and lone escapes. tmux
#: `-e` deliberately emits these; the view wants the text underneath.
ANSI_RE = re.compile(
    r"""
    \x1b\][^\x07\x1b]*(?:\x07|\x1b\\)   # OSC … BEL or ST
    | \x1b[@-Z\\-_]                     # two-character escapes
    | \x1b\[[0-?]*[ -/]*[@-~]           # CSI …
    | [\x00-\x08\x0b\x0c\x0e-\x1f\x7f]  # stray control bytes, keeping \t and \n
    """,
    re.VERBOSE,
)


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _tmux(args: list[str], *, socket: str | None = None) -> subprocess.CompletedProcess | None:
    """Run tmux read-only. None when tmux is not installed."""
    binary = shutil.which("tmux")
    if binary is None:
        return None
    command = [binary]
    if socket:
        command += ["-L", socket]
    return subprocess.run(command + args, capture_output=True, text=True, check=False)


def session_alive(session: str, *, socket: str | None = None) -> bool:
    """`=name` is tmux's exact-match session target: `eng-001` never matches `eng-0011`."""
    result = _tmux(["has-session", "-t", f"={session}"], socket=socket)
    return bool(result and result.returncode == 0)


def pane_targets(session: str, window: str = MAIN_WINDOW) -> list[str]:
    """Exact-match pane targets to try, best first.

    A pane target is `session:window.pane`, and the `=` exact-match prefix binds
    to the session part — `=eng-001:` resolves, while a bare `=eng-001` is not a
    pane target at all and tmux rejects it. `hx launch` puts the agent in window
    `main` and its Companion in `companion` (spec 08), so `main` is what the
    Agent view wants; the bare form is the fallback for a session that has no
    window by that name.
    """
    return [f"={session}:{window}", f"={session}:"]


def log_fallback(root: Path, agent_id: str, lines: int = PANE_LINES) -> list[str]:
    """The pane log for a dead session.

    `logs/<id>/<id>-pane.log` (spec 03, spec 11): raw pane text, written by
    `tmux pipe-pane -o` which `adapters/claude/start.sh` starts right after
    launch, and archived with `logs/<id>/` at the next dispatch. It is not a
    Companion stream. This is the one moment the human most wants it — the
    session is gone and its last output is only here.

    Until build-2 item 12 lands nothing writes the file, and a dead session
    shows "no live tmux session <id>" instead.
    """
    path = Path(root) / "logs" / agent_id / f"{agent_id}-pane.log"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return strip_ansi(text).splitlines()[-lines:]


def capture(
    root: Path,
    agent_id: str,
    *,
    session: str | None = None,
    lines: int = PANE_LINES,
    socket: str | None = None,
    window: str = MAIN_WINDOW,
) -> dict[str, Any]:
    """The `pane` block of `hx show <id> --json` (CONTRACTS.md).

    `{"session": …, "alive": bool, "lines": [… last `lines`, ANSI stripped …]}`,
    plus `source` ("session", "log" or "none") and `error` for the view to show
    instead of an empty box. `alive` is true only for a live pane: a capture
    served from the pane log says so with `source` and leaves `alive` false.
    """
    session = session or agent_id
    pane: dict[str, Any] = {"session": session, "alive": False, "lines": [], "source": "none", "error": None}

    if shutil.which("tmux") is None:
        pane["error"] = "tmux is not installed; the pane cannot be observed"
        return pane

    result = None
    for target in pane_targets(session, window):
        result = _tmux(["capture-pane", "-p", "-e", "-t", target, "-S", f"-{lines}"], socket=socket)
        if result is not None and result.returncode == 0:
            pane["alive"] = True
            pane["source"] = "session"
            pane["lines"] = strip_ansi(result.stdout).splitlines()[-lines:]
            return pane

    stderr = (result.stderr if result else "").strip()
    fallback = log_fallback(root, agent_id, lines)
    if fallback:
        pane["source"] = "log"
        pane["lines"] = fallback
        pane["error"] = stderr or f"no live tmux session {session}; showing the pane log"
        return pane

    pane["error"] = stderr or f"no live tmux session {session}"
    return pane
