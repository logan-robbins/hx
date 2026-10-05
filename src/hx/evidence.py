"""Bounded evidence reads for companion passes and recovery."""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path

from .continuity_store import ContinuityStore, canonical
from .errors import ValidationError


def read(store: ContinuityStore, event_id: str, *, offset: int = 0, limit: int = 8000) -> dict:
    if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 262144:
        raise ValidationError("evidence requires offset>=0 and 1<=limit<=262144 bytes")
    with store.transaction() as tx:
        row = tx.db.execute("SELECT payload,payload_hash,kind FROM events WHERE event_id=?", (event_id,)).fetchone()
        if row is None:
            raise ValidationError(f"unknown or retired evidence {event_id}")
        payload = json.loads(row["payload"])
        if payload.get("retired"):
            raise ValidationError(f"retired evidence {event_id}: its task is closed and no current record cites it")
        if "artifact_hash" in payload:
            data, total = tx.read_artifact_slice(payload["artifact_hash"], offset=offset, limit=limit)
            source_hash = payload["artifact_hash"]
        else:
            full = canonical(payload.get("observation", payload)).encode()
            if offset > len(full):
                raise ValidationError("evidence offset is past the end")
            data, total = full[offset:offset + limit], len(full)
            source_hash = row["payload_hash"]
        try:
            text = data.decode("utf-8")
            encoding = "utf-8"
        except UnicodeDecodeError:
            # Byte offsets remain exact even when the requested slice splits UTF-8.
            text = base64.b64encode(data).decode("ascii")
            encoding = "base64"
        return {"event_id": event_id, "kind": row["kind"], "source_hash": source_hash,
                "offset": offset, "bytes": len(data), "total_bytes": total,
                "next_offset": offset + len(data) if offset + len(data) < total else None,
                "encoding": encoding, "content": text}


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx evidence")
    parser.add_argument("event_id")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=8000)
    parser.add_argument("--root")
    args = parser.parse_args(argv)
    with ContinuityStore(root) as store:
        print(canonical(read(store, args.event_id, offset=args.offset, limit=args.limit)))
    return 0
