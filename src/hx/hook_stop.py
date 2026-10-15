"""Partner turn boundaries and orchestration-companion results.

The Partner's stop hook records background work, signals an owed companion pass,
and takes a pending reset when its conversation is quiet. Native worker stops
are captured and reconciled through their admitted run instead.
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
    from .companion import require_partner
    require_partner(item_id)
    tasks = background_tasks(payload)
    store.atomic_write_json(turn_marker(root, item_id), {
        "ts": timestamps.now(),
        "background_tasks": tasks,
        "session_id": payload.get("session_id"),
    })

    # Spec 10 wake trigger: `run/<id>/turn` touched. Batch-gated: the turn end is
    # routine, and a forced wake per turn is Companion churn (see the module docstring).
    from . import companion as companion_mod

    companion_mod.wake_due(root, item_id, env=env)

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
    companion_mod.require_partner(item_id)

    installed = []
    for stream in companion_mod.open_streams(root, item_id):
        if companion_mod.out_path(root, item_id, stream).is_file():
            if companion_mod.ingest(root, item_id, stream, env=env) is not None:
                installed.append(stream)

    # Whatever is owed — a retry the ingest above rewrote, or a stream with new records —
    # goes out on this wake.
    companion_mod.wake_due(root, item_id, force=True, env=env)
    return 0, ""
