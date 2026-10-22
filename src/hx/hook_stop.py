"""Partner turn boundaries.

The Partner's stop hook records background work, decides a cache-aware seam
(`token_controls.partner_regime`) and takes a pending one when its conversation
is quiet. Native worker stops are captured and reconciled through their
admitted run instead.
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

    from . import streams, transcripts
    from .hook_log import seam_marker
    from .token_controls import COLD_MISS, partner_regime

    usage = transcripts.last_usage(payload.get("transcript_path"))
    if usage:
        # The turn's last response: the context the next turn reads, and when it was last read.
        counts = {field: usage[field] for field in transcripts.CONTEXT_FIELDS
                  if isinstance(usage.get(field), int) and not isinstance(usage.get(field), bool)}
        streams.append_record(root, item_id, f"{item_id}-main", {
            "event": "turn_end", "context_tokens": sum(counts.values()),
            "cache_read": counts.get("cache_read_input_tokens"),
            "cache_creation": counts.get("cache_creation_input_tokens")})
    marker = seam_marker(root, item_id)
    if not marker.exists() and partner_regime(root) == COLD_MISS:
        # The turn's last response rewrote its prefix instead of reading it from the cache.
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text(COLD_MISS)
    if marker.exists():
        # The `/clear` this queues runs after this hook returns — live-verified. A seam that
        # cannot be taken yet leaves its marker for the next boundary.
        from . import seam as seam_mod

        seam_mod.seam(root, item_id, env=env)
    return 0, ""
