"""The step state: schema, validation, budget, and eviction (spec 07.2, 10).

The Companion returns one JSON object per call and hx is the thing that decides whether to
keep it. An invalid or oversized write is discarded and the previous state stands, because a
corrupted step state is worse than a stale one: it is what the agent is rehydrated from.

The budget is `companion.state_budget_tokens`, estimated at 4 characters per token (spec 10).
The prompt asks the Companion to stay inside it; this is the deterministic backstop for when
it overshoots, evicting in the order `companion/BASE.md` fixes.
"""

from __future__ import annotations

import json
from pathlib import Path

#: Spec 07.2. Every key is optional on the wire — a Companion that has nothing to say about
#: dead ends should not have to send an empty list — but anything present must have this shape.
SCHEMA: dict[str, type | tuple[type, ...]] = {
    "seq": int,
    "prompt_version": dict,
    "goal": str,
    "constraints": list,
    "decisions": list,
    "open_steps": list,
    "closed_steps": list,
    "dead_ends": list,
    "working_set": dict,
    "blockers": list,
    "subagents_open": list,
}

#: The shape of each entry in the list-valued fields, as {field: {key: type}}. Only the keys
#: named here are checked; a Companion may add its own and `hx compose` renders them.
ENTRY_SHAPES: dict[str, dict[str, type | tuple[type, ...]]] = {
    "decisions": {"d": str, "why": str, "ev": list},
    "open_steps": {"id": str, "intent": str, "next": str, "ev": list},
    "closed_steps": {"id": str, "outcome": str, "verified": bool, "commit": str, "ev": list},
}

WORKING_SET_FIELDS: dict[str, type | tuple[type, ...]] = {
    "commits": list,
    "dirty": list,
    "files": list,
    "last_failure": str,
    "hypothesis": str,
}

#: Spec 10 "Evict under budget, in order".
EVICTION_ORDER = (
    "collapse_closed_steps",
    "oldest_dead_ends",
    "closed_step_working_set",
    "untouched_file_notes",
)

#: Spec 10: the budget is in tokens, estimated at 4 characters per token.
CHARS_PER_TOKEN = 4


class InvalidState(ValueError):
    """The Companion returned something hx will not write. The previous state stands."""


def estimate_tokens(state: dict) -> int:
    return len(json.dumps(state, separators=(",", ":"))) // CHARS_PER_TOKEN


def validate(data: object, *, previous_seq: int | None = None) -> dict:
    """Return the state, or raise `InvalidState` naming what was wrong (spec 07.2)."""
    if not isinstance(data, dict):
        raise InvalidState(f"step state must be a JSON object, got {type(data).__name__}")

    unknown = sorted(set(data) - set(SCHEMA))
    if unknown:
        raise InvalidState(f"unknown field(s): {', '.join(unknown)}")

    for field, expected in SCHEMA.items():
        if field not in data or data[field] is None:
            continue
        value = data[field]
        if expected is int and isinstance(value, bool):
            raise InvalidState(f"`{field}` must be an integer, got a boolean")
        if not isinstance(value, expected):
            raise InvalidState(
                f"`{field}` must be {getattr(expected, '__name__', expected)}, "
                f"got {type(value).__name__}"
            )

    seq = data.get("seq")
    if seq is None:
        raise InvalidState("`seq` is required: it is the cursor into the raw stream")
    if seq < 0:
        raise InvalidState(f"`seq` must not be negative, got {seq}")
    if previous_seq is not None and seq < previous_seq:
        # The cursor only moves forward. A state that goes backwards would make hx re-feed
        # records the Companion has already folded in.
        raise InvalidState(f"`seq` went backwards: {seq} < {previous_seq}")

    for field, shape in ENTRY_SHAPES.items():
        for index, entry in enumerate(data.get(field) or []):
            if not isinstance(entry, dict):
                raise InvalidState(f"`{field}[{index}]` must be an object, got {type(entry).__name__}")
            for key, expected in shape.items():
                if key in entry and entry[key] is not None and not isinstance(entry[key], expected):
                    raise InvalidState(
                        f"`{field}[{index}].{key}` must be "
                        f"{getattr(expected, '__name__', expected)}, got {type(entry[key]).__name__}"
                    )

    working_set = data.get("working_set")
    if isinstance(working_set, dict):
        for key, expected in WORKING_SET_FIELDS.items():
            if key in working_set and working_set[key] is not None:
                if not isinstance(working_set[key], expected):
                    raise InvalidState(
                        f"`working_set.{key}` must be "
                        f"{getattr(expected, '__name__', expected)}, "
                        f"got {type(working_set[key]).__name__}"
                    )
        for index, entry in enumerate(working_set.get("files") or []):
            if not isinstance(entry, dict) or not isinstance(entry.get("path", ""), str):
                raise InvalidState(f"`working_set.files[{index}]` must be an object with a `path`")

    return data


def _open_step_ids(state: dict) -> set[str]:
    return {str(step.get("id")) for step in state.get("open_steps") or [] if isinstance(step, dict)}


def _touched_paths(state: dict) -> set[str]:
    """Paths an open step names, which are the ones worth keeping notes for."""
    touched: set[str] = set()
    for step in state.get("open_steps") or []:
        if isinstance(step, dict):
            for key in ("intent", "next"):
                text = step.get(key)
                if isinstance(text, str):
                    touched.update(word.strip("`,.()") for word in text.split() if "/" in word)
    return touched


def evict(state: dict, budget_tokens: int) -> tuple[dict, list[str]]:
    """Bring the state inside the budget, in spec 10's order. Returns (state, what was done).

    Deterministic on purpose: two runs over the same state evict the same things, so a state
    that shrinks is reproducible and a regression in the Companion's own output is visible.
    """
    applied: list[str] = []
    state = json.loads(json.dumps(state))
    if estimate_tokens(state) <= budget_tokens:
        return state, applied

    # 1. Collapsed closed steps to one line: outcome, commit and evidence, nothing else.
    closed = state.get("closed_steps")
    if isinstance(closed, list) and closed:
        state["closed_steps"] = [
            {k: v for k, v in entry.items() if k in ("id", "outcome", "verified", "commit", "ev")}
            if isinstance(entry, dict) else entry
            for entry in closed
        ]
        applied.append("collapse_closed_steps")
        if estimate_tokens(state) <= budget_tokens:
            return state, applied

    # 2. Oldest dead ends first: the newest are the ones still worth not repeating.
    dead_ends = state.get("dead_ends")
    while isinstance(dead_ends, list) and dead_ends and estimate_tokens(state) > budget_tokens:
        dead_ends.pop(0)
        if "oldest_dead_ends" not in applied:
            applied.append("oldest_dead_ends")

    if estimate_tokens(state) <= budget_tokens:
        return state, applied

    # 3. Working-set detail of closed steps: commits stay (they are one line and they are how
    #    the agent recovers what it did), `dirty` goes.
    working_set = state.get("working_set")
    if isinstance(working_set, dict) and working_set.get("dirty"):
        working_set["dirty"] = []
        applied.append("closed_step_working_set")
        if estimate_tokens(state) <= budget_tokens:
            return state, applied

    # 4. Notes on files no open step touches. Each one costs a Read after the seam, so these
    #    go last.
    if isinstance(working_set, dict) and isinstance(working_set.get("files"), list):
        touched = _touched_paths(state)
        files = working_set["files"]
        keep = [f for f in files if isinstance(f, dict) and str(f.get("path", "")) in touched]
        dropped = [f for f in files if f not in keep]
        while dropped and estimate_tokens(state) > budget_tokens:
            dropped.pop(0)
            working_set["files"] = keep + dropped
            if "untouched_file_notes" not in applied:
                applied.append("untouched_file_notes")
        working_set["files"] = keep + dropped

    return state, applied


def load(path: Path) -> dict | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    return data if isinstance(data, dict) else None


def empty(seq: int = 0) -> dict:
    return {"seq": seq, "open_steps": [], "closed_steps": [], "subagents_open": []}
