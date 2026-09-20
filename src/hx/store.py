"""Locking and atomic writes: the two primitives every control-plane write uses.

`tasks.json` and the work items are written only by hx (spec 04), and `hx dispatch`,
`hx complete` and `hx resume` all mutate them under `run/tasks.lock` (spec 08). A write is
tmp-file + fsync + same-directory rename, so a reader never sees half a file and an
interrupted command leaves the previous version intact.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = "run/tasks.lock"


def lock_path(root: Path) -> Path:
    return root / LOCK_NAME


@contextmanager
def locked(root: Path):
    """Hold `run/tasks.lock` exclusively. No timeout: hx waits for the lock (spec 08)."""
    path = lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(handle, fcntl.LOCK_EX)
        yield path
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            os.close(handle)


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` so a reader sees either the old file or the whole new one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        "w", dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp", delete=False
    )
    try:
        with handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(handle.name, path)
    except BaseException:
        Path(handle.name).unlink(missing_ok=True)
        raise


def atomic_write_json(path: Path, data: object) -> None:
    atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")
