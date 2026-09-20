"""The `context` hook: `SessionStart`, matcher `startup|resume|clear|compact` (spec 09.1).

At every conversation boundary hx composes the context file and the hook prints one line: the
path. Hooks never inject file contents (spec 02 Continuity) — the agent reads the file with
one tool call, and its persona is already in the system prompt, so it costs nothing to have.

On `source=clear` and a `working` item the hook also sends the goal, which is what finishes a
seam: `Stop` → `/clear` → `SessionStart(clear)` → `/goal` → one Read (spec 09.3). On `startup`
and `resume` it sends none: `hx launch` and `hx restart` send it from outside once the pane is
ready, and `resume` restores it natively (spec 01.1).

For the Partner it also records the messaging socket and token, which are per process and are
exported before `SessionStart` runs, so `run/partner/socket.json` survives `/clear`
(spec 09.1, 12; verified against code.claude.com/docs/en/cross-session-messaging).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import compose as compose_mod, goal as goal_mod, store, streams, timestamps
from .ids import PARTNER
from .workitems import find_work_item

#: Spec 09.1, verbatim. It names the *tool*, because a live agent read the file twice —
#: `Bash cat` and then `Read` — when the line only named the action (build-3 live check).
#: Never JSON, never a leading brace: plain stdout is what `SessionStart` injects as context.
CONTEXT_LINE = (
    "Use the Read tool once on {path} before anything else; "
    "do not cat it and do not read it twice."
)

SOCKET_ENV = "CLAUDE_CODE_MESSAGING_SOCKET"
TOKEN_ENV = "CLAUDE_CODE_MESSAGING_TOKEN"


def write_socket_file(root: Path, payload: dict, env) -> Path | None:
    """`run/partner/socket.json` in the CONTRACTS.md four-key form, or None when unavailable."""
    env = os.environ if env is None else env
    address = env.get(SOCKET_ENV)
    if not address:
        return None
    path = root / "run" / PARTNER / "socket.json"
    store.atomic_write_json(
        path,
        {
            "socket": address,
            "token": env.get(TOKEN_ENV, ""),
            "ts": timestamps.now(),
            "session_id": payload.get("session_id"),
        },
    )
    return path


def handle(payload: dict, item_id: str, root: Path, *, env=None) -> tuple[int, str]:
    """Returns (exit code, the single stdout line)."""
    source = payload.get("source") or "startup"

    if item_id == PARTNER:
        write_socket_file(root, payload, env)

    stream = compose_mod.main_stream_name(item_id)
    path = compose_mod.compose(root, item_id, stream, env=env)

    # A boundary record, so the Companion sees where the conversation restarted (spec 07.1).
    streams.append_record(
        root,
        item_id,
        stream,
        {
            "event": "boundary",
            "source": source,
            "context_file": str(path),
            "ref": {"transcript": payload.get("transcript_path")},
        },
    )

    if source == "clear":
        work_item = find_work_item(root, item_id)
        if work_item is not None and work_item.name.endswith("-working.md"):
            # This completes a seam: the item is working, so the pointer goes in now (spec 09.3).
            goal_mod.send_goal(root, item_id, now=True, env=env)

    return 0, CONTEXT_LINE.format(path=path)
