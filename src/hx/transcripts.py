"""Reading Claude Code's own transcript, for the one number hx needs from it.

`context_tokens` is the size of the context the model was last sent, read from the `usage`
block of the latest assistant record in the transcript the hook was handed (spec 07.1). It is
what the `log` hook compares against the seam threshold, and what `hx board` reports.
"""

from __future__ import annotations

import json
from pathlib import Path

#: What counts as context: everything sent to the model, cached or not. Output tokens are what
#: came back, so they are not part of the window the next turn has to fit in.
CONTEXT_FIELDS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def last_usage(transcript: str | Path | None) -> dict | None:
    """The `usage` of the latest assistant record, or None."""
    if not transcript:
        return None
    path = Path(transcript)
    if not path.is_file():
        return None
    try:
        # Legacy hook consumers need only the latest usage. Read a bounded tail;
        # planned execution gets usage from the incremental observer instead.
        with path.open('rb') as handle:
            end = handle.seek(0, 2)
            start = max(0, end - 65536)
            handle.seek(start)
            lines = handle.read(65536).splitlines()
        if start:
            lines = lines[1:]  # The first record may start before the selected tail.
        for line in reversed(lines):
            if b'"usage"' not in line:
                continue
            try:
                record = json.loads(line)
            except (ValueError, UnicodeDecodeError):
                continue
            message = record.get("message")
            if isinstance(message, dict) and message.get("role") in (None, "assistant") and isinstance(message.get("usage"), dict):
                return message['usage']
    except OSError:
        pass
    return None


def context_tokens(transcript: str | Path | None) -> int | None:
    usage = last_usage(transcript)
    if usage is None:
        return None
    total = 0
    seen = False
    for field in CONTEXT_FIELDS:
        value = usage.get(field)
        if isinstance(value, int) and not isinstance(value, bool):
            total += value
            seen = True
    return total if seen else None
