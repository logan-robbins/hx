"""Atomic writes: the primitive the work items and the run-directory markers use.

A write is tmp-file + fsync + same-directory rename, so a reader never sees half a file and
an interrupted command leaves the previous version intact. `tasks.json` is not written this
way: the v1 cut (spec 14 D25) made it an ordinary `json.dump`, with no lock around it.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


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
