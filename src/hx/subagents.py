"""Subagent handles: `run/<id>/subagents.json` and the `sNNN` vocabulary (spec 01, 09.1).

A subagent is addressed by handle `<id>-sNNN`. Claude Code gives hooks an opaque `agent_id`;
hx maps it to the next free `sNNN` at `SubagentStart` and every later hook looks it up here,
so one subagent has one stream for its whole life.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
from pathlib import Path

from . import store

FILENAME = "subagents.json"
HANDLE_RE = re.compile(r"^s(?P<n>[0-9]{3})$")


def path_for(root: Path, item_id: str) -> Path:
    return root / "run" / item_id / FILENAME


def load(root: Path, item_id: str) -> dict[str, str]:
    """`{claude agent_id: sNNN}` — the shape CONTRACTS.md gives for `hx show`."""
    path = path_for(root, item_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def next_handle(existing: dict[str, str]) -> str:
    """The next free `sNNN`, counting from every handle already assigned."""
    highest = 0
    for value in existing.values():
        match = HANDLE_RE.match(str(value).rsplit("-", 1)[-1])
        if match:
            highest = max(highest, int(match.group("n")))
    return f"s{highest + 1:03d}"


def assign(root: Path, item_id: str, agent_id: str) -> str:
    """Map `agent_id` to a handle, assigning the next one under a lock. Idempotent.

    The lock matters: several subagents can start at once, and two of them taking the same
    `sNNN` would merge two agents into one stream.
    """
    path = path_for(root, item_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    handle_fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(handle_fd, fcntl.LOCK_EX)
        existing = load(root, item_id)
        if agent_id in existing:
            return existing[agent_id]
        assigned = next_handle(existing)
        existing[agent_id] = assigned
        store.atomic_write_json(path, existing)
        return assigned
    finally:
        try:
            fcntl.flock(handle_fd, fcntl.LOCK_UN)
        finally:
            os.close(handle_fd)


def stream_for(item_id: str, handle: str) -> str:
    """`s001` → `eng-001-s001`; a full handle is returned unchanged."""
    return handle if handle.startswith(f"{item_id}-") else f"{item_id}-{handle}"


def handle_for(root: Path, item_id: str, agent_id: str | None) -> tuple[str, bool]:
    """The stream a hook's payload belongs to, and whether it is the main one (spec 09.1).

    An `agent_id` hx has never seen — a subagent that started before hx was watching — falls
    back to the main stream rather than inventing a handle, because a record on the wrong
    stream is worse than a record on the busy one.
    """
    main = f"{item_id}-main"
    if not agent_id:
        return main, True
    assigned = load(root, item_id).get(str(agent_id))
    if not assigned:
        return main, True
    return stream_for(item_id, assigned), False
