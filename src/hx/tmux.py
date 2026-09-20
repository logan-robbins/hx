"""The thin tmux layer.

`HX_TMUX` lets a test point hx at a private tmux server (`tmux -L hx-test-<pid>`) so a
test never touches the user's tmux sessions. Everything else here is read-only in M0.
"""

from __future__ import annotations

import os
import shutil
import subprocess


def tmux_command(env: dict[str, str] | None = None) -> list[str]:
    env = os.environ if env is None else env
    override = env.get("HX_TMUX")
    if override:
        return override.split()
    return ["tmux"]


def available(env: dict[str, str] | None = None) -> bool:
    return shutil.which(tmux_command(env)[0]) is not None


def version(env: dict[str, str] | None = None) -> str | None:
    if not available(env):
        return None
    result = subprocess.run(
        [*tmux_command(env), "-V"], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() or None


def live_sessions(env: dict[str, str] | None = None) -> set[str] | None:
    """Names of the live tmux sessions, or `None` when tmux itself is unavailable.

    No tmux server running is an empty set, not an error: it means no agent is alive.
    """
    if not available(env):
        return None
    result = subprocess.run(
        [*tmux_command(env), "list-sessions", "-F", "#{session_name}"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def has_session(name: str, env: dict[str, str] | None = None) -> bool:
    sessions = live_sessions(env)
    return bool(sessions and name in sessions)


def pane_log(root, item_id: str):
    """`logs/<id>/<id>-pane.log`: the raw pane capture (spec 03, 11).

    It is the UI's fallback for a dead session, not a Companion stream, so
    `hx.streams` ignores it and only `hx dispatch` (which archives all of `logs/<id>/`)
    ever moves it.
    """
    return root / "logs" / item_id / f"{item_id}-pane.log"


def arm_pane_log(root, item_id: str, env: dict[str, str] | None = None) -> bool:
    """Point `tmux pipe-pane -o` at the pane log, replacing any pipe already running.

    `start.sh` arms this at launch; `hx dispatch` re-arms it after archiving `logs/<id>/`,
    because a pipe started before the archive keeps writing into the moved file.
    """
    if not has_session(item_id, env):
        return False
    target = pane_log(root, item_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    quoted = str(target).replace("'", "'\\''")
    subprocess.run(
        [*tmux_command(env), "pipe-pane", "-t", f"={item_id}:main"],
        capture_output=True,
        check=False,
    )
    result = subprocess.run(
        [*tmux_command(env), "pipe-pane", "-o", "-t", f"={item_id}:main", f"cat >> '{quoted}'"],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0
