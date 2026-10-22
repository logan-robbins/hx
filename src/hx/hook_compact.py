"""The `precompact` and `postcompact` hooks (spec 09.1, 02 "Compaction (last resort)").

Both are **log-only**, and `precompact` deliberately does not block. Two live findings decided
that (`01-terminology.md` 1.1, E4 and E8):

  - blocking `PreCompact(auto)` suppresses compaction for the rest of that turn even if a
    later `PreCompact` is allowed, so a block is not a "not yet", it is a "not this turn";
  - `PreCompact`/`PostCompact` also fire for **subagent** compactions, with the parent's
    `session_id`, so a block aimed at the main thread would silently disable a subagent's
    compaction too.

Compaction is not what hx relies on: `config/models.json` puts the seam threshold far below
the native autocompact window (500k against about 967k on a 1M model), so the `log` hook
marks a seam long before the harness would compact. Reaching compaction at all means a single
turn grew through the whole headroom without ending. If it happens, the records these hooks
write tell the Companion so, and `SessionStart(compact)` still hands over the context file.
"""

from __future__ import annotations

from pathlib import Path

from . import streams
from .ids import PARTNER
from .subagents import handle_for

#: `PreCompact` fires for subagents too, so the record goes to the stream that is compacting.
COMPACT_PENDING = "compact_pending"
COMPACT = "compact"


def stream_for(payload: dict, item_id: str, root: Path) -> tuple[str, bool]:
    """The stream of the thread that is compacting, and whether it is the main one."""
    return handle_for(root, item_id, payload.get("agent_id"))


def pre(payload: dict, item_id: str, root: Path, *, env=None) -> tuple[int, str]:
    """Record that compaction is about to happen, so the Companion's next pass sees it.

    It never blocks and never runs a pass: holding compaction hostage for a model call
    trades a slow summary for a hung turn, and the loop's next Partner pass lands in the
    state the `SessionStart(compact)` context file is composed from. It returns nothing —
    no decision output, ever (spec 02).
    """
    from .companion import require_partner
    require_partner(item_id)
    stream, _ = stream_for(payload, item_id, root)
    streams.append_record(root, item_id, stream, {
        "event": COMPACT_PENDING,
        "trigger": payload.get("trigger"),
        "source": payload.get("source"),
    })

    return 0, ""


def post(payload: dict, item_id: str, root: Path, *, env=None) -> tuple[int, str]:
    """Record what native compaction kept, so the Companion can see its summary."""
    from .companion import require_partner
    require_partner(item_id)
    stream, _ = stream_for(payload, item_id, root)
    summary = payload.get("compact_summary")
    seq = streams.append_record(root, item_id, stream, {
        "event": COMPACT,
        "trigger": payload.get("trigger"),
        "compact_summary": streams.excerpt(summary) if summary else None,
    })
    return 0, ""
