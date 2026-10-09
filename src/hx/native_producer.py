"""Bounded durable relay from installed hooks to the exact planned run.

Queue only decoded public observations. Commit before attempting delivery; delete
only after the capture transaction acknowledges the same bytes and delivery ID.
The root observer retries a bounded batch, without starting another resident.
"""

from __future__ import annotations

import json
import sqlite3
import uuid

from . import native_capture
from .continuity_store import Conflict, _id, canonical, digest
from .errors import ValidationError
from .events import decode
from .observer import BATCH_BYTES, MAX_RECORD_BYTES, SPOOL_BYTES, notify, source_snapshot

QUEUE_RECORDS = 1024
RETRY_RECORDS = 16


def gap(store, run_id, worker_id, reason):
    """Make a lost/unknown observation visible to every readiness gate."""
    with store.transaction() as tx:
        row = tx.db.execute("SELECT worker_id FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None or row[0] != worker_id:
            raise Conflict("capture gap does not belong to this worker")
        tx._change()
        tx.db.execute("INSERT OR IGNORE INTO native_capture_gaps VALUES(?,?)", (run_id, reason[:512]))
        tx.enqueue("capture_gap", "native-gap:" + run_id, {"run_id": run_id})
    notify(store.root)


def status(tx, run_id):
    row = tx.db.execute("""SELECT count(*),coalesce(sum(q.bytes),0) FROM native_producer_queue q
        JOIN native_bindings b USING(binding_id) WHERE b.run_id=?""", (run_id,)).fetchone()
    fault = tx.db.execute("SELECT reason FROM native_capture_gaps WHERE run_id=?", (run_id,)).fetchone()
    return {"pending_deliveries": row[0], "pending_bytes": row[1], "gap": fault[0] if fault else None,
            "lag": bool(row[0] or fault)}


def require_drained(tx, run_id):
    if tx.db.execute("SELECT 1 FROM runtime_cycles WHERE scope LIKE ? AND json_extract(payload,'$.status')='running' LIMIT 1", ('capability-call:' + run_id + ':%',)).fetchone():
        raise Conflict('external capability operation is still active or has an uncertain outcome')
    if status(tx, run_id)["lag"]:
        raise Conflict("native capture has unacknowledged deliveries or a gap; reconcile evidence before completion")
    snapshot = source_snapshot(tx, run_id)
    if snapshot["overflow"] or any(source["lag"] for source in snapshot["sources"]):
        raise Conflict("registered capture sources have unread bytes, changed metadata, a gap, or excessive scope; reconcile evidence before completion")


def _deliver(store, binding_id, delivery_id, payload, worker_id=None):
    first = store.db.execute("SELECT delivery_id FROM native_producer_queue WHERE binding_id=? ORDER BY rowid LIMIT 1",
                             (binding_id,)).fetchone()
    if first and first[0] != delivery_id and not store.db.execute(
            "SELECT 1 FROM native_deliveries WHERE binding_id=? AND delivery_id=?", (binding_id, delivery_id)).fetchone():
        return {"binding_id": binding_id, "delivery_id": delivery_id, "committed": False, "queued": True}
    result = native_capture.enqueue(store, binding_id, delivery_id, payload, worker_id=worker_id)
    with store.transaction() as tx:
        tx._change()
        tx.db.execute("DELETE FROM native_producer_queue WHERE binding_id=? AND delivery_id=?",
                      (binding_id, delivery_id))
    return result


def stage(store, *, binding_id, delivery_id, payload, worker_id, queue_bytes=SPOOL_BYTES):
    _id(delivery_id)
    encoded = canonical(payload)
    size = len(encoded.encode())
    if size > MAX_RECORD_BYTES or type(queue_bytes) is not int or queue_bytes < 1:
        raise ValidationError("public hook delivery exceeds its bounded queue contract")
    # Validate before a poison record can enter the durable retry queue.
    decode("public-batch-v1", payload)
    payload_hash = digest(payload)
    with store.transaction() as tx:
        binding = tx.db.execute("SELECT * FROM native_bindings WHERE binding_id=?", (binding_id,)).fetchone()
        if binding is None or binding["decoder_version"] != "public-batch-v1":
            raise ValidationError("producer requires a public-batch capture binding")
        owner = tx.db.execute("SELECT worker_id FROM runs WHERE run_id=?", (binding["run_id"],)).fetchone()[0]
        if owner != worker_id or payload.get("session_id") != binding["session_id"]:
            raise Conflict("producer caller or session differs from its binding")
        for table in ("native_producer_queue", "native_deliveries"):
            previous = tx.db.execute(f"SELECT payload_hash FROM {table} WHERE binding_id=? AND delivery_id=?",
                                     (binding_id, delivery_id)).fetchone()
            if previous:
                if previous[0] != payload_hash:
                    raise Conflict("producer delivery ID reused with different public observations")
                return
        # Retain late bytes against their original binding. Enqueue's active-run
        # gate leaves them quarantined rather than applying them to a new task.
        count, used = tx.db.execute("SELECT count(*),coalesce(sum(bytes),0) FROM native_producer_queue").fetchone()
        if count >= QUEUE_RECORDS or used + size > queue_bytes:
            raise Conflict("native producer queue full; retain the original source and reconcile the capture gap")
        tx._change()
        tx.db.execute("INSERT INTO native_producer_queue(binding_id,delivery_id,payload_hash,payload,bytes) VALUES(?,?,?,?,?)",
                      (binding_id, delivery_id, payload_hash, encoded, size))
    notify(store.root)


def produce(store, *, run_id, worker_id, adapter, session_id, stream_id, payload,
            decoder="hook-v1", delivery_id=None, queue_bytes=SPOOL_BYTES):
    if decoder not in {"hook-v1", "pi-v1"} or (decoder == "pi-v1" and adapter != "pi"):
        raise ValidationError("producer decoder does not match the native adapter")
    for key in ("session_id", "sessionId"):
        if payload.get(key) not in (None, "") and payload[key] != session_id:
            raise Conflict("producer observation differs from its native session")
    events = decode(decoder, payload)
    public = {"schema_version": 1, "session_id": session_id, "events": [
        {"schema_version": 1, "kind": event.kind, "data": event.data,
         "native_event_id": event.native_id, "usage": event.usage} for event in events]}
    binding = native_capture.bind(store, run_id=run_id, worker_id=worker_id, adapter=adapter,
                                 session_id=session_id, stream_id=stream_id, decoder="public-batch-v1", retain_late=True)
    delivery = delivery_id or str(uuid.uuid4())
    stage(store, binding_id=binding, delivery_id=delivery, payload=public, worker_id=worker_id, queue_bytes=queue_bytes)
    try:
        return _deliver(store, binding, delivery, public, worker_id)
    except (OSError, sqlite3.Error, Conflict):
        # Durable ownership is already established. The observer retries; no
        # model wake or second daemon is needed, and the queue gates readiness.
        return {"binding_id": binding, "delivery_id": delivery, "committed": False, "queued": True}


def retry(store, *, limit=RETRY_RECORDS, byte_budget=BATCH_BYTES):
    if type(limit) is not int or not 1 <= limit <= RETRY_RECORDS or type(byte_budget) is not int or byte_budget < 1:
        raise ValidationError("native retry requires a bounded batch")
    # One head per binding preserves native order and lets other workers make
    # progress while a stopped/pressured session waits for reconciliation.
    headers = store.db.execute("""SELECT q.binding_id,q.delivery_id,q.bytes FROM native_producer_queue q
        WHERE q.rowid=(SELECT min(head.rowid) FROM native_producer_queue head WHERE head.binding_id=q.binding_id)
        ORDER BY q.attempts,q.rowid LIMIT ?""", (limit,)).fetchall()
    results, used = [], 0
    for header in headers:
        if used and used + header["bytes"] > byte_budget:
            break
        row = store.db.execute("SELECT payload FROM native_producer_queue WHERE binding_id=? AND delivery_id=?",
                               (header["binding_id"], header["delivery_id"])).fetchone()
        if row is None:
            continue
        used += header["bytes"]
        try:
            result = _deliver(store, header["binding_id"], header["delivery_id"], json.loads(row[0]))
        except (OSError, sqlite3.Error, Conflict, ValidationError) as exc:
            try:
                with store.transaction() as tx:
                    tx._change()
                    tx.db.execute("UPDATE native_producer_queue SET attempts=attempts+1,error=? WHERE binding_id=? AND delivery_id=?",
                                  (str(exc)[:512], header["binding_id"], header["delivery_id"]))
            except sqlite3.Error:
                pass  # The original queued bytes remain durable.
            result = {"binding_id": header["binding_id"], "delivery_id": header["delivery_id"], "committed": False}
        results.append(result)
    return results


def hook(store, *, run_id, worker_id, adapter, launch_id, event, payload):
    """Session-less children stay within their launch, never the newest task."""
    _id(launch_id)
    if payload.get("_hx_capture_error"):
        raise ValidationError("native hook input could not be read within its capture bound")
    if event == "model-response" and adapter != "meta":
        raise ValidationError("model response capture requires the verified Muse hook contract")
    if event == "subagent-result":
        # PostToolUse already captured this observation; this legacy hook exists
        # solely to compose child digests and must not duplicate a tool result.
        return None
    session = payload.get("session_id") or payload.get("sessionId") or payload.get("transcript_path")
    child = payload.get("agent_id") or payload.get("agentId") or payload.get("subagent_id")
    if not session:
        session = "launch:" + digest([launch_id, child])
    stream = "child:" + digest([launch_id, child]) if child else "native:" + digest([launch_id, session])
    observed = dict(payload)
    observed.setdefault("hook_event_name", {"log-failure": "PostToolUseFailure", "request": "UserPromptSubmit",
                                           "tool-start": "PreToolUse"}.get(event, event))
    decoder = "hook-v1"
    if event == "message":
        if (adapter != "pi" or payload.get("type") != "message_end"
                or not isinstance(payload.get("message"), dict)
                or payload["message"].get("role") not in {"user", "assistant"}):
            raise ValidationError("message capture requires a Pi completed user or assistant message")
        decoder = "pi-v1"
    return produce(store, run_id=run_id, worker_id=worker_id, adapter=adapter, session_id=session,
                   stream_id=stream, payload=observed, decoder=decoder)
