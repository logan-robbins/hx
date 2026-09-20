"""The `stop` hook: `Stop`, main thread (spec 09.1, 09.3).

Three jobs at every turn boundary, in this order:

  1. record the turn and its `background_tasks` in `run/<id>/turn`
  2. deliver a goal the pane was owed — this is what makes the Partner's self-dispatch work:
     `hx dispatch partner` runs from inside the Partner's own Bash tool, so its pane is
     mid-turn, `hx goal` leaves `run/partner/goal-pending`, and this hook pastes it at the end
     of that turn, which is the live-verified way a slash command queued from a hook runs
     (spec 02 Goal delivery, 08, 09.1)
  3. otherwise, take a seam if one is pending

It returns no decision output: `/goal` owns whether the agent keeps working (spec 09.1).
"""

from __future__ import annotations

import json
from pathlib import Path

from . import goal as goal_mod, store, timestamps
from .errors import HxError

TURN_MARKER = "turn"


def turn_marker(root: Path, item_id: str) -> Path:
    return root / "run" / item_id / TURN_MARKER


def background_tasks(payload: dict) -> list:
    """The payload's background task list, under whichever name it arrives."""
    for key in ("background_tasks", "backgroundTasks"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
    return []


def handle(payload: dict, item_id: str, root: Path, *, env=None) -> tuple[int, str]:
    tasks = background_tasks(payload)
    store.atomic_write_json(turn_marker(root, item_id), {
        "ts": timestamps.now(),
        "background_tasks": tasks,
        "session_id": payload.get("session_id"),
    })

    # Waking the Companion is a no-op until it exists (M5); the call site is here.
    from . import flush as flush_mod

    flush_mod.flush(root, item_id, env=env)

    pending = goal_mod.pending_marker(root, item_id)
    if pending.exists():
        # `--now`: the pane is by definition at the end of a turn, and a slash command pasted
        # from inside this hook runs after it returns (spec 01.1, live-verified E3).
        goal_mod.send_goal(root, item_id, now=True, env=env)
        pending.unlink(missing_ok=True)
        return 0, ""

    from .hook_log import seam_marker

    if seam_marker(root, item_id).exists():
        # `hx seam` lands in build-7. The marker stays, so the next boundary tries again —
        # which is exactly what spec 09.3 step 2 says happens when a seam cannot be taken yet.
        from .hooks import log_error

        log_error(
            root, item_id, "stop",
            "seam marker present but `hx seam` is not implemented (build-7); marker left in place",
        )
    return 0, ""
