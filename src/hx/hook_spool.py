"""Planned hook deliveries deferred to the application loop.

A planned hook that cannot take the ledger writer within its budget lets a
non-mutating native operation proceed and leaves its whole delivery here, one
file per hook, in arrival order under its run; a file edit or shell command it
denied is left here marked `denied`. While a run has deferred deliveries its
later hooks defer too, so the loop replays them in their original order. The
loop is the writer: it replays each through `hooks.replay`. A delivery replay
cannot apply is a lost observation and becomes the run's capture gap.
"""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path

from .continuity_store import digest
from .errors import HxError

#: The hook payload bound (8 MiB) plus its envelope.
RECORD_BYTES = 9 * 1024 * 1024
INGEST_RECORDS = 256


def directory(root: Path) -> Path:
    return Path(root) / "state" / "hook-spool"


def _run_directory(root, run_id):
    return directory(root) / digest(run_id)[:32]


def record(root: Path, delivery: dict, reason: str) -> Path:
    """Atomically write one deferred delivery; never touches the ledger."""
    target = _run_directory(root, delivery["run_id"])
    target.mkdir(parents=True, exist_ok=True)
    body = json.dumps({"delivery": delivery, "reason": reason[:512], "at": time.time()},
                      sort_keys=True).encode()
    if len(body) > RECORD_BYTES:
        raise HxError("deferred hook delivery exceeds its spool bound")
    fd, name = tempfile.mkstemp(prefix=".pending-", dir=target)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(body)
            handle.flush()
            os.fsync(handle.fileno())
        # Nanosecond names keep arrival order within the run.
        path = target / f"{time.time_ns():020d}-{uuid.uuid4().hex}.json"
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
    return path


def pending(root: Path, run_id: str | None = None) -> list[Path]:
    """Deferred deliveries of one run, or of every run, in replay order."""
    runs = [_run_directory(root, run_id)] if run_id is not None else (
        sorted(path for path in directory(root).iterdir() if path.is_dir()) if directory(root).is_dir() else [])
    found = []
    for target in runs:
        if target.is_dir():
            found += sorted(path for path in target.iterdir() if path.suffix == ".json" and not path.name.startswith("."))
    return found


def ingest(store, *, limit: int = INGEST_RECORDS) -> list[dict]:
    """Replay deferred deliveries in order; one that cannot apply records a capture gap."""
    from .hooks import replay
    from .native_producer import gap

    applied = []
    for path in pending(store.root)[:limit]:
        try:
            with path.open("rb") as handle:
                delivery = json.loads(handle.read(RECORD_BYTES + 1))["delivery"]
        except (OSError, ValueError, KeyError, TypeError):
            path.unlink(missing_ok=True)  # Unreadable: the hook error log is its trace.
            continue
        try:
            replay(store, delivery)
            applied.append({"run_id": delivery["run_id"], "event": delivery["event"]})
        except sqlite3.Error:
            raise  # A ledger failure keeps the delivery for the next tick.
        except Exception as exc:
            try:
                gap(store, delivery["run_id"], delivery["worker_id"],
                    f"deferred hook could not apply: {delivery['event']}: {exc}")
            except HxError:
                pass  # The run ended or belongs to no such worker; nothing remains to gate.
            applied.append({"run_id": delivery["run_id"], "event": delivery["event"], "gap": str(exc)[:512]})
        path.unlink(missing_ok=True)
        try:
            path.parent.rmdir()
        except OSError:
            pass  # More deliveries of this run remain.
    return applied
