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
