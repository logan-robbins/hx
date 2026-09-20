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
    found = None
    try:
        with path.open(errors="replace") as handle:
            for line in handle:
                line = line.strip()
                if not line or '"usage"' not in line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                message = record.get("message")
                if not isinstance(message, dict):
                    continue
                if message.get("role") not in (None, "assistant"):
                    continue
                usage = message.get("usage")
                if isinstance(usage, dict):
                    found = usage
    except OSError:
        return None
    return found


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
