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


# --- appending (spec 07.1) ----------------------------------------------------------------
#
# "Each raw record is one line under 4 KB, written with a single write(2) on an O_APPEND
# descriptor, with `seq` assigned under a per-stream lock." Concurrent hook invocations on the
# same stream then append whole lines in order. The `log` hook (M4) uses this too.

import fcntl
import os
import tempfile

from . import timestamps

#: Spec 07.1: one line under 4 KB.
MAX_RECORD_BYTES = 4096
#: Spec 07.1: head excerpts, with `ref` pointing at the full payload in the transcript.
EXCERPT_CHARS = 2000


def stream_path(root: Path, item_id: str, stream: str) -> Path:
    """Where a handle's records live. Subagent streams carry an open/closed suffix."""
    if stream == f"{item_id}-main":
        return main_stream(root, item_id)
    directory = log_dir(root, item_id)
    for state in ("open", "closed"):
        candidate = directory / f"{stream}-{state}.jsonl"
        if candidate.is_file():
            return candidate
    return directory / f"{stream}-open.jsonl"


def _lock_path(root: Path, item_id: str, stream: str) -> Path:
    return root / "run" / item_id / f"{stream}.stream.lock"


def last_seq(path: Path) -> int:
    seq = 0
    for record in iter_records(path):
        value = record.get("seq")
        if isinstance(value, int) and not isinstance(value, bool) and value > seq:
            seq = value
    return seq


def excerpt(value: object, limit: int = EXCERPT_CHARS) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def append_record(root: Path, item_id: str, stream: str, record: dict) -> int:
    """Append one record, assigning `seq` under the per-stream lock. Returns the seq.

    Oversized records are trimmed rather than dropped: the excerpt shrinks until the line
    fits, because a stream that silently loses a record is worse than a shorter excerpt.
    """
    path = stream_path(root, item_id, stream)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = _lock_path(root, item_id, stream)
    lock.parent.mkdir(parents=True, exist_ok=True)

    handle = os.open(lock, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        entry = {
            "seq": last_seq(path) + 1,
            "ts": timestamps.now(),
            "stream": stream,
            **record,
        }
        line = json.dumps(entry, default=str)
        if len(line.encode()) > MAX_RECORD_BYTES:
            for field in ("output", "input"):
                if field in entry and len(line.encode()) > MAX_RECORD_BYTES:
                    entry[field] = excerpt(entry[field], 200)
                    line = json.dumps(entry, default=str)
        if len(line.encode()) > MAX_RECORD_BYTES:
            line = json.dumps(
                {k: v for k, v in entry.items() if k in ("seq", "ts", "stream", "event", "ref")},
                default=str,
            )
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(descriptor, line.encode() + b"\n")
        finally:
            os.close(descriptor)
        return entry["seq"]
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            os.close(handle)


# --- FIFO retention (spec 07.1) --------------------------------------------------------------
#
# "`max_records` (default 500) and `max_bytes` (default 4 MB) per stream, whichever is hit
# first. `hx-hook` truncates from the head on every append that would exceed a bound, but never
# below `state.seq − keep_behind` (default 100), so the Companion always has a window of
# already-processed evidence behind its cursor and everything ahead of it."

MAX_RECORDS = 500
MAX_BYTES = 4 * 1024 * 1024
KEEP_BEHIND = 100


def truncate(
    path: Path,
    *,
    state_seq: int,
    max_records: int = MAX_RECORDS,
    max_bytes: int = MAX_BYTES,
    keep_behind: int = KEEP_BEHIND,
) -> int:
    """Drop records from the head. Returns how many were dropped.

    Evidence is never dropped ahead of the Companion's cursor, and never within `keep_behind`
    of it: the floor wins over both bounds, so a stream whose Companion has fallen behind
    grows rather than losing what it has not read.
    """
    if not path.is_file():
        return 0
    records = list(iter_records(path))
    if len(records) <= max_records and path.stat().st_size <= max_bytes:
        return 0

    floor = max(0, state_seq - keep_behind)
    keep = records[-max_records:] if len(records) > max_records else list(records)

    while keep and path.stat().st_size > max_bytes:
        if (keep[0].get("seq") or 0) > floor:
            break
        keep.pop(0)

    # Nothing at or above the floor is ever dropped, whatever the bounds say.
    protected = [r for r in records if (r.get("seq") or 0) > floor]
    if protected and (not keep or (keep[0].get("seq") or 0) > (protected[0].get("seq") or 0)):
        keep = protected

    if len(keep) == len(records):
        return 0
    from . import store

    store.atomic_write_text(path, "".join(json.dumps(r) + "\n" for r in keep))
    return len(records) - len(keep)
