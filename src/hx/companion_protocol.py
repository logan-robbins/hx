"""Two bounded companion capabilities: read frozen evidence and propose a patch."""

from __future__ import annotations

import json
import sys

from . import evidence, passes
from .continuity_store import ContinuityStore, Conflict, canonical, digest
from .errors import ValidationError

READ_BUDGET = 8192
READ_LIMIT = 2048
MAX_CALLS = 8
MAX_PATCH = 16384

TOOLS = [
    {"name": "read_evidence", "description": "Read a bounded byte slice of evidence named in this frozen pass. No filesystem access.",
     "inputSchema": {"type": "object", "properties": {"event_id": {"type": "string"}, "offset": {"type": "integer", "minimum": 0},
        "limit": {"type": "integer", "minimum": 1, "maximum": READ_LIMIT}}, "required": ["event_id", "offset", "limit"], "additionalProperties": False}},
    {"name": "submit_patch", "description": "Submit the frozen pass response for atomic validation. At most two attempts; invalid changes never commit. Return after acceptance.",
     "inputSchema": {"type": "object", "properties": {"response": {"type": "object"}}, "required": ["response"], "additionalProperties": False}},
]


def row(store, job_id):
    found = store.db.execute("SELECT * FROM companion_jobs WHERE job_id=?", (job_id,)).fetchone()
    if found is None:
        raise ValidationError("unknown companion job")
    return {**dict(found), "payload": json.loads(found["payload"])}


def frozen(store, job):
    found = store.db.execute("SELECT payload FROM passes WHERE pass_id=?", (job["pass_id"],)).fetchone()
    return json.loads(found[0])


def update(tx, job, **changes):
    payload = {**job["payload"], **changes}
    tx._change()
    tx.db.execute("UPDATE companion_jobs SET payload=? WHERE job_id=?", (canonical(payload), job["job_id"]))


def call(store, job_id, name, args):
    if not isinstance(args, dict):
        raise ValidationError("tool arguments must be an object")
    if name == "read_evidence":
        if set(args) != {"event_id", "offset", "limit"} or not isinstance(args["event_id"], str):
            raise ValidationError("read requires an event ID, byte offset, and limit")
        if type(args["offset"]) is not int or args["offset"] < 0 or type(args["limit"]) is not int or not 1 <= args["limit"] <= READ_LIMIT:
            raise ValidationError("evidence slice exceeds the tool bound")
        with store.transaction() as tx:
            job = row(tx, job_id)
            if job["status"] != "running":
                raise Conflict("companion job is not running")
            if job["payload"].get("result") is not None:
                raise Conflict("companion pass is already accepted")
            body = frozen(tx, job)
            passes._active_run(tx, job["run_id"])
            allowed = {event["event_id"] for event in body["events"]}
            allowed.update(event for record in body["records"] for event in record["evidence"])
            if args["event_id"] not in allowed:
                raise ValidationError("evidence is outside the frozen pass")
            reads, used = job["payload"].get("reads", 0), job["payload"].get("read_bytes", 0)
            if reads >= MAX_CALLS or used + args["limit"] > READ_BUDGET:
                raise ValidationError("companion evidence budget exhausted; leave missing evidence pending")
            update(tx, job, reads=reads + 1, read_bytes=used + args["limit"])
        # The public evidence reader verifies only the requested artifact chunks.
        return evidence.read(store, **args)
    if name != "submit_patch" or set(args) != {"response"}:
        raise ValidationError("unknown companion capability")
    response = args["response"]
    if len(canonical(response).encode()) > MAX_PATCH:
        raise ValidationError("companion patch exceeds 16 KiB")
    with store.transaction() as tx:
        job = row(tx, job_id)
        if job["status"] != "running":
            raise Conflict("companion job is not running")
        if job["payload"].get("result") is not None:
            if digest(response) != job["payload"]["result"]["response_hash"]:
                raise Conflict("accepted pass received a different response")
            return {"accepted": True, "result": job["payload"]["result"]}
        attempts = job["payload"].get("attempts", 0)
        if attempts >= 2:
            raise ValidationError("companion retry exhausted; unresolved evidence remains pending")
        update(tx, job, attempts=attempts + 1)
    try:
        result = passes.commit(store, job["pass_id"], response)
    except (ValidationError, Conflict) as exc:
        with store.transaction() as tx:
            job = row(tx, job_id)
            update(tx, job, last_error=str(exc)[:512])
        return {"accepted": False, "retry_remaining": 1 - attempts, "error": str(exc)[:512]}
    with store.transaction() as tx:
        job = row(tx, job_id)
        update(tx, job, result=result)
    return {"accepted": True, "result": result}


def serve(root, job_id, input_stream=None, output_stream=None):
    source, target = input_stream or sys.stdin.buffer, output_stream or sys.stdout
    with ContinuityStore(root) as store:
        for _ in range(64):
            line = source.readline(MAX_PATCH + 4097)
            if not line:
                return
            if len(line) > MAX_PATCH + 4096:
                return  # Never parse an unbounded or truncated RPC frame.
            request = json.loads(line)
            if not isinstance(request, dict) or "id" not in request:
                continue
            try:
                method, params = request.get("method"), request.get("params", {})
                if method == "initialize":
                    result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                              "serverInfo": {"name": "hx-frozen-pass", "version": "1"}}
                elif method == "ping":
                    result = {}
                elif method == "tools/list":
                    result = {"tools": TOOLS}
                elif method == "tools/call":
                    value = call(store, job_id, params.get("name"), params.get("arguments"))
                    result = {"content": [{"type": "text", "text": canonical(value)}], "isError": False}
                else:
                    raise ValidationError("unsupported companion RPC method")
                reply = {"jsonrpc": "2.0", "id": request["id"], "result": result}
            except (ValidationError, Conflict) as exc:
                reply = {"jsonrpc": "2.0", "id": request["id"], "result": {
                    "content": [{"type": "text", "text": str(exc)[:512]}], "isError": True}}
            target.write(canonical(reply) + "\n")
            target.flush()


if __name__ == "__main__":
    from pathlib import Path
    serve(Path(sys.argv[1]), sys.argv[2])
