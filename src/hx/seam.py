"""`hx seam <id>` — cut the conversation and hand the agent its context file (spec 08, 09.2).

A seam is the planned end of a conversation: the Companion is at the head of the stream, the
context file is recomposed from what it recorded, `/clear` is pasted, and the `context` hook
on `source=clear` sends the `/goal` pointer again. The agent's first action in the new
conversation is one Read of that file. Native compaction is never reached on this path.

Called from the `stop` hook when `run/<id>/seam` exists (spec 09.1). The one refusal is
background work: a turn that ended with `background_tasks` still running is not a boundary,
so the marker stays and the next `stop` tries again (spec 09.2 step 2).

`hx seam` cannot wait for the `/clear` to take effect: the queued slash command only runs
once the `Stop` hook has returned, and this runs inside it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import compose as compose_mod, flush as flush_mod, goal as goal_mod, streams
from .config_harness import flavor_of
from .errors import NotFound
from .hook_log import seam_marker
from .ids import PARTNER
from .workitems import find_work_item

#: What the `context` hook sees as `source` when the queued `/clear` runs (spec 09.1).
SEAM_SOURCE = "clear"

#: Returned by `seam()` when the turn left background work running (spec 09.2 step 2).
DEFERRED = "deferred"
TAKEN = "taken"


def background_tasks(root: Path, item_id: str) -> list:
    """What the last `stop` hook recorded in `run/<id>/turn` (spec 09.1)."""
    from .hook_stop import turn_marker

    path = turn_marker(root, item_id)
    if not path.is_file():
        return []
    try:
        marker = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    value = marker.get("background_tasks") if isinstance(marker, dict) else None
    return value if isinstance(value, list) else []


def step_state(root: Path, item_id: str) -> dict:
    from .companion import state_path
    from .stepstate import load as load_state

    return load_state(state_path(root, item_id, f"{item_id}-main")) or {}


def seam_record(root: Path, item_id: str, context_file: Path, *, source: str = SEAM_SOURCE) -> dict:
    """The `seam` record of spec 07.4, which `hx metrics` reads (CONTRACTS.md)."""
    state = step_state(root, item_id)
    working_set = state.get("working_set") or {}
    files = working_set.get("files") if isinstance(working_set, dict) else None
    return {
        "event": "seam",
        "source": source,
        "prompt_version": state.get("prompt_version"),
        "context_tokens_before": streams.last_context_tokens(root, item_id),
        "context_file_bytes": context_file.stat().st_size if context_file.is_file() else 0,
        "working_set_size": len(files) if isinstance(files, list) else 0,
    }


def seam_slash(root: Path, item_id: str) -> str:
    """The slash command that cuts the conversation. Claude pastes `/clear`; Pi pastes `/new`."""
    path = root / "adapters" / flavor_of(root, item_id) / "seam-command"
    if path.is_file():
        line = path.read_text().strip()
        if line.startswith("/") and " " not in line and "\n" not in line:
            return line
    return "/clear"


def seam(root: Path, item_id: str, *, env=None) -> dict:
    """Take the seam, or defer it. Returns `{"id", "outcome", "seq", "background_tasks"}`."""
    marker = seam_marker(root, item_id)
    pending = background_tasks(root, item_id)
    if pending:
        # Not a boundary: the turn ended but work is still running behind it. The marker
        # stays exactly where it is and the next `stop` retries (spec 09.2 step 2).
        return {"id": item_id, "outcome": DEFERRED, "seq": None, "background_tasks": pending}

    if find_work_item(root, item_id) is None and item_id != PARTNER:
        raise NotFound(f"{item_id}: no work item; a seam belongs to a dispatched agent (spec 06)")
    # The Partner has no work item and no goal: its seam is /clear + rehydrate from the
    # context file, and the `context` hook on `clear` sends no pointer for it.

    # Sequence is spec 08's: the Companion reaches the head, the context file is composed from
    # what it recorded, then the `/clear` is queued and the record written.
    flush_mod.flush(root, item_id, env=env)
    context_file = Path(compose_mod.compose(root, item_id, f"{item_id}-main", env=env))

    goal_mod.paste(item_id, seam_slash(root, item_id), env)
    seq = streams.append_record(root, item_id, f"{item_id}-main", seam_record(root, item_id, context_file))

    # A seam is the one boundary where the whole conversation ends, so the state at that point
    # is the most complete episode this agent will produce before the next one (docs/memory.md).
    from . import memory as memory_mod

    memory_mod.enqueue_quietly(
        root, item_id, f"{item_id}-main", "seam", step_state(root, item_id), seq=seq
    )

    marker.unlink(missing_ok=True)
    return {"id": item_id, "outcome": TAKEN, "seq": seq, "background_tasks": []}


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx seam", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    result = seam(root, args.id, env=env)
    if result["outcome"] == DEFERRED:
        print(
            f"HX-SEAM {result['id']} deferred "
            f"background_tasks={len(result['background_tasks'])}"
        )
        return 0
    print(f"HX-SEAM {result['id']} taken seq={result['seq']}")
    return 0
