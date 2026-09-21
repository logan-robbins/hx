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
from .subagents import handle_for

#: `PreCompact` fires for subagents too, so the record goes to the stream that is compacting.
COMPACT_PENDING = "compact_pending"
COMPACT = "compact"


def stream_for(payload: dict, item_id: str, root: Path) -> tuple[str, bool]:
    """The stream of the thread that is compacting, and whether it is the main one."""
    return handle_for(root, item_id, payload.get("agent_id"))


def pre(payload: dict, item_id: str, root: Path, *, env=None) -> tuple[int, str]:
    """Record that compaction is about to happen, and get the Companion to the head first.

    The flush is the whole value of this hook: whatever Claude Code is about to summarise
    away, the Companion has already read and turned into step state, so the context file
    stays the authority. It returns nothing — no decision output, ever (spec 02).
    """
    stream, is_main = stream_for(payload, item_id, root)
    streams.append_record(root, item_id, stream, {
        "event": COMPACT_PENDING,
        "trigger": payload.get("trigger"),
        "source": payload.get("source"),
    })

    from . import flush as flush_mod

    # Only the main thread's Companion is worth waiting for; a subagent stream's pass comes
    # with its close (spec 09.1 `subagent-stop`).
    if is_main:
        flush_mod.flush(root, item_id, env=env)
    return 0, ""


def post(payload: dict, item_id: str, root: Path, *, env=None) -> tuple[int, str]:
    """Record what Claude Code kept, so the Companion can see the fallback path's summary."""
    stream, _ = stream_for(payload, item_id, root)
    summary = payload.get("compact_summary") or payload.get("summary")
    seq = streams.append_record(root, item_id, stream, {
        "event": COMPACT,
        "trigger": payload.get("trigger"),
        "compact_summary": streams.excerpt(summary) if summary else None,
    })

    # Native compaction is the fallback path, and what it kept is the only record of the part
    # of the conversation it threw away. It goes into episode memory next to the Companion's
    # own chunks, marked `compact` so a search can tell them apart (docs/memory.md).
    if summary:
        from . import memory as memory_mod
        from .companion import state_path
        from .stepstate import load as load_state

        state = load_state(state_path(root, item_id, stream)) or {}
        memory_mod.enqueue_quietly(
            root, item_id, stream, "compact", state,
            seq=seq, text=streams.excerpt(summary),
        )
    return 0, ""
