"""Reading the raw streams under `logs/<id>/` (spec 07).

The streams are written by `hx-hook` from M2 onward. M0 only needs to see them from the
board: how many subagent streams are open, the last `context_tokens`, and how many seam
records a dispatch has taken.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from . import timestamps

MAIN_SUFFIX = "-main.jsonl"
_SUBAGENT_RE = re.compile(r"^(?P<id>.+)-(?P<handle>s[0-9]{3})-(?P<state>open|closed)\.jsonl$")


def log_dir(root: Path, item_id: str) -> Path:
    return root / "logs" / item_id


def main_stream(root: Path, item_id: str) -> Path:
    return log_dir(root, item_id) / f"{item_id}{MAIN_SUFFIX}"


def subagent_streams(root: Path, item_id: str, *, state: str | None = None) -> list[Path]:
    """Subagent stream files for an id, optionally filtered to `open` or `closed`."""
    directory = log_dir(root, item_id)
    if not directory.is_dir():
        return []
    found = []
    for entry in sorted(directory.iterdir()):
        match = _SUBAGENT_RE.match(entry.name)
        if not match or match.group("id") != item_id:
            continue
        if state is not None and match.group("state") != state:
            continue
        found.append(entry)
    return found


def open_subagents(root: Path, item_id: str) -> int:
    return len(subagent_streams(root, item_id, state="open"))


def iter_records(path: Path):
    """Yield the parsed records of a stream, skipping lines that are not JSON objects.

    A hook appends one line per event under a per-stream lock (spec 07); a partial final
    line can only be a crash artefact, and the board reports the stream rather than failing.
    """
    if not path.is_file():
        return
    with path.open("r", errors="replace") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(record, dict):
                yield record


def last_context_tokens(root: Path, item_id: str) -> int | None:
    """`context_tokens` from the last main-stream record that carries one (CONTRACTS.md)."""
    path = main_stream(root, item_id)
    if not path.is_file():
        return None
    value = None
    for record in iter_records(path):
        tokens = record.get("context_tokens")
        if isinstance(tokens, int) and not isinstance(tokens, bool):
            value = tokens
    return value


def count_seams(root: Path, item_id: str, since: str | None) -> int | None:
    """Seam records on the main stream since `dispatched`; `None` when there is no stream."""
    path = main_stream(root, item_id)
    if not path.is_file():
        return None
    floor = timestamps.parse(since) if since else None
    count = 0
    for record in iter_records(path):
        if record.get("event") != "seam":
            continue
        if floor is not None:
            ts = record.get("ts")
            parsed = timestamps.parse(ts) if isinstance(ts, str) else None
            if parsed is not None and parsed < floor:
                continue
        count += 1
    return count
