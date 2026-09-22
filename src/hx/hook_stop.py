"""The `stop` hook: `Stop`, main thread (spec 09.1, 09.3).

Two jobs at every turn boundary, in this order:

  1. record the turn and its `background_tasks` in `run/<id>/turn`, and wake the Companion
  2. take a seam if one is pending — `hx seam` reads the marker this hook just wrote, so the
     `background_tasks` it gates on are this turn's

It returns no decision output: `/goal` owns whether the agent keeps working (spec 09.1).
The v1 cut (spec 14 D25) removed `goal-pending` delivery: it existed only for the Partner
dispatching itself, and the Partner has no work item and no goal.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import store, timestamps

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

    from .hook_log import seam_marker

    marker = seam_marker(root, item_id)
    if marker.exists():
        # The `/clear` this queues runs after this hook returns — live-verified, spec 09.2
        # step 3. A seam that cannot be taken yet leaves its marker for the next boundary.
        from . import seam as seam_mod

        seam_mod.seam(root, item_id, env=env)
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
