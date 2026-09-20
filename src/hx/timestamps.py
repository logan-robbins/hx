"""ISO 8601 UTC timestamps with a `Z` suffix, the one format hx writes (CONTRACTS.md)."""

from __future__ import annotations

import datetime as _dt
import os


def now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_mtime(path: os.PathLike[str] | str) -> str:
    ts = _dt.datetime.fromtimestamp(os.stat(path).st_mtime, _dt.timezone.utc)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse(value: str) -> _dt.datetime | None:
    """Parse a timestamp hx or a hook wrote; `None` when it is not one."""
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        # The compact form `20260920T101500Z` also appears in spec 08's tasks.json example.
        try:
            parsed = _dt.datetime.strptime(value.strip(), "%Y%m%dT%H%M%SZ")
        except ValueError:
            return None
        return parsed.replace(tzinfo=_dt.timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=_dt.timezone.utc)
    return parsed


def looks_like(value: str) -> bool:
    return parse(value) is not None
