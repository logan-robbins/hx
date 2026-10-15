"""Partner startup context and messaging endpoint discovery.

Each boundary refreshes PARTNER.md's Status and hands the Partner its configured
persona and one pointer to its orchestration notes. Worker boundaries receive
immutable context through the native launch hook contract.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from . import compose as compose_mod, persona as persona_mod, store, streams, timestamps
from .ids import PARTNER

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
    """Write `## Status`, record the boundary, and return what the agent is handed."""
    from .companion import require_partner
    require_partner(item_id)
    source = payload.get("source") or "startup"

    if item_id == PARTNER:
        write_socket_file(root, payload, env)

    stream = compose_mod.main_stream_name(item_id)
    compose_mod.compose(root, item_id, stream, env=env)
    handed = persona_mod.boundary(root, item_id, env=env)

    # A boundary record, so the Companion sees where the conversation restarted (spec 07.1).
    # The context pointer names the Partner orchestration notes.
    streams.append_record(
        root,
        item_id,
        stream,
        {
            "event": "boundary",
            "source": source,
            "context_file": str(handed["file"]) if handed["file"] else None,
            "hook_bytes": handed["bytes"],
            "ref": {"transcript": payload.get("transcript_path")},
        },
    )


    return 0, handed["text"]
