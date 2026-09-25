"""Durable native-event delivery bound to an exact run and native session.

The producer retains an unacknowledged delivery and retries its same ID. Capture
never looks up a worker's newest assignment, reads a transcript, or wakes an LLM.
Bindings are installed by the lifecycle controller before starting a native run.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from .continuity_store import ContinuityStore, Conflict, _id, canonical, digest
from .errors import ValidationError
from .events import decode
from .observer import MAX_RECORD_BYTES, SPOOL_BYTES, append_observation, encode_observation, notify, spool_size

TRANSPORTS = {name: {"hook-v1"} for name in ("codex", "claude", "meta", "grok", "pi")}
TRANSPORTS["pi"] = {"hook-v1", "pi-v1"}


def _run(tx, run_id: str, worker_id: str | None = None):
    row = tx.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if row is None or row["ended_at"] is not None:
        raise Conflict("native capture requires the original active run")
    if worker_id is not None and row["worker_id"] != worker_id:
        raise Conflict("native capture caller does not own this run")
    # Task amendments require an explicit lifecycle rebind, never automatic
    # reassignment of a late event to whatever task the worker is running now.
    if tx.task(row["task_id"])["revision"] != row["task_revision"]:
        raise Conflict("native capture task changed; lifecycle rebind required")
    return row


def bind(store: ContinuityStore, *, run_id: str, stream_id: str, adapter: str,
         session_id: str, decoder: str = "hook-v1", worker_id: str | None = None) -> str:
    for value in (run_id, stream_id, session_id):
        _id(value)
    if stream_id == "progress":
        raise ValidationError("progress is reserved for validated hx progress updates")
    if adapter not in TRANSPORTS or decoder not in TRANSPORTS[adapter]:
        raise ValidationError("native capture requires a supported adapter/transport pair")
    binding_id = digest([run_id, stream_id, adapter, session_id, decoder])
    with store.transaction() as tx:
        _run(tx, run_id, worker_id)
        old = tx.db.execute("SELECT * FROM native_bindings WHERE adapter=? AND session_id=? AND stream_id=?",
                            (adapter, session_id, stream_id)).fetchone()
        if old:
            if old["binding_id"] != binding_id:
                raise Conflict("native session already bound; use a new session identity for a new run or decoder")
            return binding_id
        tx._change()
        tx.db.execute("INSERT OR IGNORE INTO cursors(run_id,stream_id) VALUES(?,?)", (run_id, stream_id))
        tx.db.execute("INSERT INTO native_bindings VALUES(?,?,?,?,?,?,NULL)",
                      (binding_id, run_id, stream_id, adapter, session_id, decoder))
    return binding_id


def enqueue(store: ContinuityStore, binding_id: str, delivery_id: str, payload: dict, *,
            worker_id: str | None = None, spool_limit: int = SPOOL_BYTES) -> dict:
    """Acknowledge only after events, delivery identity, and notification commit.

    A repeated delivery returns its original IDs, even after the run closes.
    Different native deliveries with identical public observations share one event;
    deliveries without native IDs keep their distinct, explicitly uncertain identity.
    """
    _id(binding_id)
    _id(delivery_id)
    if not isinstance(payload, dict) or type(spool_limit) is not int or spool_limit < 1:
        raise ValidationError("native capture needs an object and a positive spool limit")
    encoded = canonical(payload).encode()
    if len(encoded) > MAX_RECORD_BYTES:
        raise ValidationError("native event exceeds capture bound; retain source and reduce before retrying")
    payload_hash = hashlib.sha256(encoded).hexdigest()
    del encoded
    with store.transaction() as tx:
        binding = tx.db.execute("SELECT * FROM native_bindings WHERE binding_id=?", (binding_id,)).fetchone()
        if binding is None:
            raise ValidationError("unknown native capture binding")
        owner = tx.db.execute("SELECT worker_id FROM runs WHERE run_id=?", (binding["run_id"],)).fetchone()[0]
        if worker_id is not None and owner != worker_id:
            raise Conflict("native capture caller does not own this run")
        previous = tx.db.execute("SELECT * FROM native_deliveries WHERE binding_id=? AND delivery_id=?",
                                (binding_id, delivery_id)).fetchone()
        if previous:
            if previous["payload_hash"] != payload_hash:
                raise Conflict("delivery ID reused for a different payload")
            return {"binding_id": binding_id, "delivery_id": delivery_id,
                    "event_ids": json.loads(previous["event_ids"]), "committed": True}
        _run(tx, binding["run_id"], worker_id)
        for key in ("session_id", "sessionId"):
            if payload.get(key) is not None and payload[key] != binding["session_id"]:
                raise Conflict("event session differs from its capture binding")
        if "parentId" in payload:
            raise ValidationError("tree records require the registered observer's active ancestry filter")
        events = decode(binding["decoder_version"], payload)
        # Admit before installing any artifact, so repeated pressure rejections
        # cannot grow a disk orphan pile. Serialize one public event at a time.
        charge = 0
        for index, event in enumerate(events):
            key, public_payload, encoded = encode_observation(session_id=binding["session_id"], event=event,
                fallback_identity=("delivery", binding_id, delivery_id, index))
            if not tx.db.execute("SELECT 1 FROM events WHERE run_id=? AND capture_key=?",
                                 (binding["run_id"], key)).fetchone():
                charge += len(canonical(public_payload).encode())
                if "artifact_hash" in public_payload:
                    charge += len(encoded)
            del public_payload, encoded
        if charge and spool_size(store) + charge > spool_limit:
            raise Conflict("capture spool full; retain this delivery and retry after reduction")
        identifiers = []
        latest_usage = None
        for index, event in enumerate(events):
            captured = append_observation(tx, run_id=binding["run_id"], stream_id=binding["stream_id"],
                session_id=binding["session_id"], event=event,
                fallback_identity=("delivery", binding_id, delivery_id, index))
            identifiers.append(captured["event_id"])
            if event.usage is not None:
                latest_usage = event.usage
        tx._change()
        tx.db.execute("INSERT INTO native_deliveries VALUES(?,?,?,?)",
                      (binding_id, delivery_id, payload_hash, canonical(identifiers)))
        tx.db.executemany("INSERT OR IGNORE INTO native_event_origins VALUES(?,?,?)",
                          ((binding_id, delivery_id, event_id) for event_id in identifiers))
        if latest_usage is not None:
            tx.db.execute("UPDATE native_bindings SET latest_usage=? WHERE binding_id=?",
                          (canonical(latest_usage), binding_id))
        if identifiers:
            tx.enqueue("capture_ready", f"native:{binding_id}:{delivery_id}",
                       {"run_id": binding["run_id"], "stream_id": binding["stream_id"], "binding_id": binding_id})
    notify(store.root)
    return {"binding_id": binding_id, "delivery_id": delivery_id, "event_ids": identifiers, "committed": True}


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx capture")
    parser.add_argument("--root")
    commands = parser.add_subparsers(dest="command", required=True)
    registration = commands.add_parser("bind")
    for field in ("run", "stream", "session"):
        registration.add_argument(f"--{field}", required=True)
    registration.add_argument("--adapter", choices=sorted(TRANSPORTS), required=True)
    registration.add_argument("--decoder", default="hook-v1")
    delivery = commands.add_parser("enqueue")
    delivery.add_argument("binding")
    delivery.add_argument("--delivery", required=True, help="producer ID reused for every retry of these bytes")
    for child in commands.choices.values():
        child.add_argument("--root", default=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    worker_id = (os.environ if env is None else env).get("HARNESS_ID")
    with ContinuityStore(root) as store:
        if args.command == "bind":
            print(bind(store, run_id=args.run, stream_id=args.stream, adapter=args.adapter,
                       session_id=args.session, decoder=args.decoder, worker_id=worker_id))
        else:
            raw = sys.stdin.buffer.read(MAX_RECORD_BYTES + 1)
            if len(raw) > MAX_RECORD_BYTES:
                raise ValidationError("native event exceeds capture bound")
            try:
                payload = json.loads(raw)
            except (ValueError, UnicodeDecodeError) as exc:
                raise ValidationError("native event must be one JSON object") from exc
            del raw
            print(canonical(enqueue(store, args.binding, args.delivery, payload, worker_id=worker_id)))
    return 0
