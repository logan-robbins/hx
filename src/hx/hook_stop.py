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

    # Spec 10 wake trigger: `run/<id>/turn` touched.
    from . import companion as companion_mod

    companion_mod.wake_due(root, item_id, force=True, env=env)

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


def companion_handle(payload: dict, item_id: str, root: Path, *, env=None) -> tuple[int, str]:
    """The Companion's own `Stop`: install what its pass produced (spec 10, CONTRACTS.md).

    It runs in the Companion's home, not the agent's, so it never sees the agent's turn. Its
    job is the other half of the pass protocol: take `out.json`, validate it, stamp it, move
    it into `state/`, and — if the pass was for a stream that still has new records, or a
    retry is owed — wake the Companion again.
    """
    from . import companion as companion_mod

    installed = []
    for stream in companion_mod.open_streams(root, item_id):
        if companion_mod.out_path(root, item_id, stream).is_file():
            if companion_mod.ingest(root, item_id, stream, env=env) is not None:
                installed.append(stream)

    # Whatever is owed — a retry the ingest above rewrote, or a stream with new records —
    # goes out on this wake.
    companion_mod.wake_due(root, item_id, force=True, env=env)
    return 0, ""
