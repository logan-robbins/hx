"""Immutable, bounded continuation packets scoped to one planned assignment."""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from .continuity_store import Conflict, canonical, digest, _id
from .errors import ValidationError
from .facts import RequiredContextOverflow, render_fact, _text
from .passes import _check_sources
from .unit_execution import _run

MAX_RECORDS = 64
MAX_PENDING = 32
MAX_STREAMS = 32


def _count(text, counter):
    value = counter(text) if counter else len(text.encode())
    if type(value) is not int or value < 0:
        raise ValidationError("context token counter must return a nonnegative integer")
    return value


def _sources(tx, run_id):
    from .native_producer import status
    from .observer import source_snapshot
    snapshot = source_snapshot(tx, run_id)
    if snapshot["overflow"]:
        raise RequiredContextOverflow("checkpoint exceeds 16 capture sources; reconcile the assignment's capture scope")
    sources = snapshot["sources"]
    producer = status(tx, run_id)
    if producer["lag"]:
        sources.append({"id": "native-producer", "revision": None, "generation": None, "offset": None,
                        "lag": True, "gaps": [producer["gap"]] if producer["gap"] else [], **producer})
    return sources


def _pending(tx, run_id):
    cursors = tx.db.execute("SELECT stream_id,head_seq,classified_seq,revision FROM cursors WHERE run_id=? ORDER BY stream_id LIMIT ?", (run_id, MAX_STREAMS + 1)).fetchall()
    if len(cursors) > MAX_STREAMS:
        raise RequiredContextOverflow("checkpoint exceeds 32 streams; reconcile open child work first")
    pending = []
    for cursor in cursors:
        # Fetch headers first; large transcripts/tool bodies never enter Python.
        rows = tx.db.execute("""SELECT event_id,stream_id,seq,kind,payload_hash,length(CAST(payload AS BLOB)) AS bytes
            FROM events WHERE run_id=? AND stream_id=? AND (seq>? OR disposition='pending')
            ORDER BY seq LIMIT ?""", (run_id, cursor["stream_id"], cursor["classified_seq"], MAX_PENDING + 1 - len(pending))).fetchall()
        pending.extend(dict(row) for row in rows)
        if len(pending) > MAX_PENDING:
            raise RequiredContextOverflow("more than 32 unresolved events require reduction before another execution turn")
    return [dict(row) for row in cursors], pending


def _record(tx, task, record_id, *, required, max_bytes):
    header = tx.db.execute("""SELECT r.task_id,r.kind,r.validity,r.storage_bytes FROM records r
        JOIN record_heads h USING(record_id,version) WHERE record_id=?""", (record_id,)).fetchone()
    if header is None or header["task_id"] != task["task_id"] or header["validity"] != "current":
        if required:
            raise Conflict("required context fact is absent, invalid, or outside this task")
        return None, "absent_or_stale"
    if header["storage_bytes"] > max_bytes:
        if required:
            raise RequiredContextOverflow("required context fact exceeds the packet bound; compress or split explicitly")
        return None, "budget"
    record = tx.record(record_id)
    try:
        _check_sources(record["inputs"], task["payload"], tx)
    except Conflict:
        if required:
            raise
        return None, "source_changed"
    return record, None


def _retire(tx, run_id, keep):
    for row in tx.db.execute("SELECT checkpoint_id FROM checkpoints WHERE run_id=?", (run_id,)).fetchall():
        if row[0] not in keep:
            tx.db.execute("DELETE FROM artifact_refs WHERE owner_type='checkpoint' AND owner_id=?", (row[0],))
            tx.db.execute("DELETE FROM checkpoints WHERE checkpoint_id=?", (row[0],))


def issue(store, run_id, *, request_id, record_ids=(), required_ids=(), mode="forced",
          max_tokens=8000, optional_tokens=1000, instructions="", count_tokens=None, tokenizer_id=None,
          prompt_manifest=None):
    """Freeze exactly what a worker will read, without acknowledging any events.

    Planned mode gates on extraction and known capture lag. Native reset also
    needs its adapter's turn-boundary barrier; this function never resets a model.
    """
    _id(request_id)
    if mode not in {"forced", "planned"} or type(max_tokens) is not int or not 1 <= max_tokens <= 64000 or type(optional_tokens) is not int or not 0 <= optional_tokens <= max_tokens:
        raise ValidationError("context requires forced/planned mode and bounded token budgets")
    if len(record_ids) > 16 or len(required_ids) > MAX_RECORDS or not isinstance(instructions, str) or len(instructions.encode()) > 262144:
        raise ValidationError("context allows 16 optional and 64 required fact IDs")
    for record_id in [*record_ids, *required_ids]:
        _id(record_id)
    if (count_tokens is None) != (tokenizer_id is None):
        raise ValidationError("a supplied token counter requires its stable tokenizer ID")
    checkpoint_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "hx-context:" + run_id + ":" + request_id))
    request_hash = digest({"run": run_id, "request": request_id, "records": record_ids, "required": required_ids,
                           "mode": mode, "max_tokens": max_tokens, "optional_tokens": optional_tokens,
                           "instructions": instructions, "tokenizer": tokenizer_id})
    with store.transaction() as tx:
        run, task, admission = _run(tx, run_id)
        prompt_bundle = None
        if prompt_manifest:
            if instructions:
                raise ValidationError("resolved instructions and prompt manifest cannot both be supplied")
            from .prompt_compiler import context_instructions
            path = Path(prompt_manifest).resolve()
            instructions, version = context_instructions(store.root, run["worker_id"], path, workdir=task["payload"]["workdir"])
            prompt_bundle = {"manifest": str(path), "version": version}
            request_hash = digest({"request": request_hash, "prompt": prompt_bundle})
        existing = tx.db.execute("SELECT request_hash FROM context_requests WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()
        if existing:
            if existing[0] != request_hash:
                raise Conflict("checkpoint request ID was reused with different inputs")
            if not tx.db.execute("SELECT 1 FROM checkpoints WHERE checkpoint_id=?", (checkpoint_id,)).fetchone():
                raise Conflict("checkpoint request has retired; use the current checkpoint or a new boundary request")
            return _read(tx, checkpoint_id, run_id)
        payload = task["payload"]
        cursors, pending = _pending(tx, run_id)
        sources = _sources(tx, run_id)
        lag = any(source["lag"] for source in sources)
        if mode == "planned" and (pending or lag):
            raise Conflict("planned checkpoint waits for unresolved events and capture lag to be reduced")
        lines = [f"Task {task['task_id']}@{task['revision']}.",
                 f"Goal: {_text(payload['goal'])}", f"Parent goal: {_text(payload['parent_goal'])}",
                 f"Repository {payload['repository']}; workdir {canonical(payload['workdir'])}.",
                 f"Assignment phase: {canonical(run['phase'])}."]
        lines.extend("Constraint: " + _text(value) for value in payload["constraints"])
        lines.extend("Acceptance: " + _text(value) for value in payload["acceptance"])
        if run["phase"] == "paused":
            lines.append("This assignment is paused. Do not mutate files; ask the Partner to reconcile its scope.")
        if instructions:
            lines.extend(["Operator and role instructions:", instructions])
        mandatory = [row[0] for row in tx.db.execute("""SELECT r.record_id FROM records r JOIN record_heads h USING(record_id,version)
            WHERE r.task_id=? AND r.validity='current' AND r.kind IN ('goal','constraint','cursor') ORDER BY r.kind,r.record_id LIMIT ?""",
            (task["task_id"], MAX_RECORDS + 1))]
        mandatory = list(dict.fromkeys([*mandatory, *required_ids]))
        if len(mandatory) > MAX_RECORDS:
            raise RequiredContextOverflow("required context exceeds 64 facts; compress the current task state")
        selected, omitted, seen, mandatory_records = {}, {}, set(), {}
        # Bound decoded metadata too, even when a provider's tokenizer is supplied.
        max_fact_bytes = min(262144, max_tokens * 4)
        def render(record):
            return render_fact(record["record_id"], record["version"], record["kind"], record["payload"])
        for record_id in mandatory:
            record, _ = _record(tx, task, record_id, required=True, max_bytes=max_fact_bytes)
            mandatory_records[record_id] = record
            selected[record_id] = record["version"]
            line = render(record)
            if line.split("] ", 1)[-1] not in seen:
                lines.append(line)
                seen.add(line.split("] ", 1)[-1])
        if not any(record["kind"] == "cursor" for record in mandatory_records.values()):
            lines.append("No cursor yet. Begin from the acceptance criteria and declared inputs.")
        lines.append("Allowed write paths: " + canonical(payload["write_paths"]) + ".")
        lines.append("Required outputs: " + canonical(payload["outputs"]) + ".")
        lines.append("Prerequisites: " + canonical(payload["prerequisites"]) + ".")
        map_scope = None
        for ref in payload["map_inputs"]:
            current = tx.db.execute("SELECT h.version,r.applicability FROM map_heads h JOIN map_records r USING(repository,snapshot,record_id,version) WHERE repository=? AND snapshot=? AND record_id=?",
                (ref['repository'], ref['snapshot'], ref['id'])).fetchone()
            refreshing = tx.db.execute('SELECT 1 FROM map_refresh_queue WHERE repository=? AND snapshot=? AND record_id=?',
                (ref['repository'], ref['snapshot'], ref['id'])).fetchone()
            if refreshing:
                lines.append(f"Map input {ref['id']}@{ref['version']} awaits source refresh. Its source-dependent claims require revalidation.")
                continue
            if not current or current['version'] != ref['version'] or current['applicability'] != 'current':
                if run['phase'] != 'paused' and mode != 'forced':
                    raise Conflict('required map input changed; rebind the assignment before composing context')
                lines.append(f"Map input {ref['id']}@{ref['version']} is no longer current. Rebind required inputs before dependent execution.")
                continue
            row = tx.db.execute("SELECT length(CAST(payload AS BLOB)) FROM map_records WHERE repository=? AND snapshot=? AND record_id=? AND version=?",
                (ref["repository"], ref["snapshot"], ref["id"], ref["version"])).fetchone()
            if row[0] > max_fact_bytes:
                raise RequiredContextOverflow("required map record exceeds the packet bound; narrow its responsibility")
            record = json.loads(tx.db.execute("SELECT payload FROM map_records WHERE repository=? AND snapshot=? AND record_id=? AND version=?",
                (ref["repository"], ref["snapshot"], ref["id"], ref["version"])).fetchone()[0])
            if map_scope != ref["snapshot"]:
                lines.append("Map snapshot: " + canonical(ref["snapshot"]) + ".")
                map_scope = ref["snapshot"]
            # Fingerprints and verbose provenance remain behind the pinned ID.
            # Keep every current semantic claim, location, condition, and edge.
            view = {key: record[key] for key in ("kind", "claim", "summary", "data", "attributes", "edges")}
            view["anchors"] = [{key: value for key, value in anchor.items() if key != "sha256"} for anchor in record["anchors"]]
            if record["kind"] == "check":
                assigned = next((name for name, recipe in payload["checks"].items() if recipe == record["data"]["recipe"]), None)
                if assigned:
                    view["data"] = {"assigned_check_recipe": assigned}
            lines.append(f"Map [{record['id']}@{record['version']}]: " + canonical(view))
        receipts = {}
        for check_id, recipe in payload["checks"].items():
            lines.append("Check recipe: " + canonical(recipe))
            row = tx.db.execute("SELECT receipt_id,valid,exit_code,inputs_hash,environment_hash,artifact_hash,json_extract(payload,'$.result.event_id') AS event_id FROM receipts WHERE run_id=? AND check_id=? AND check_version=? ORDER BY rowid DESC LIMIT 1", (run_id, check_id, digest(recipe))).fetchone()
            if row:
                receipts[check_id] = dict(row)
                state = "passed" if row["valid"] else "failed or was invalidated"
                lines.append(f"Receipt {row['receipt_id']}: check {canonical(check_id)} {state} for its recorded source/environment; exit {row['exit_code']}. Revalidate its inputs before claiming current success.")
                if row["event_id"]:
                    lines.append("Read its bounded evidence with hx evidence " + row["event_id"] + ".")
            else:
                lines.append("Check " + canonical(check_id) + " has no receipt for this assignment.")
        # Exact cursor/version metadata stays in the checkpoint, not repeated prose.
        lines.append("Unprocessed observations follow; later events remain pending.")
        lines.append("Capture state: " + canonical([{key: source[key] for key in ("id", "lag", "gaps")} for source in sources if source["lag"] or source["gaps"]]) + "." if sources else "No log sources are registered; native capture completeness is unproven.")
        if pending:
            lines.append("Resolve pending observations before acting on affected obligations.")
        for event in pending:
            line = f"Pending {event['kind']}: hx evidence {event['event_id']}"
            if event["bytes"] <= 768:
                observation = json.loads(tx.db.execute("SELECT payload FROM events WHERE event_id=?", (event["event_id"],)).fetchone()[0])
                if isinstance(observation.get('observation'), dict):
                    observation = observation['observation'].get('data', observation)
                line += "; " + canonical(observation)
            lines.append(line)
        lines.append("Acknowledgement leaves pending events unresolved.")
        def text():
            return "\n".join(lines) + "\n"
        if _count(text(), count_tokens) > max_tokens or len(text().encode()) > 262144:
            raise RequiredContextOverflow("required assignment, facts, or pending evidence exceed context budget; reduce or split before execution")
        optional = list(dict.fromkeys(record_ids))
        if not optional:
            cursor_step = next((record["payload"]["step"] for record in mandatory_records.values() if record["kind"] == "cursor"), None)
            optional = [row[0] for row in tx.db.execute("""SELECT r.record_id FROM records r JOIN record_heads h USING(record_id,version)
                WHERE r.task_id=? AND r.validity='current' AND r.retention='context' AND r.kind NOT IN ('goal','constraint','cursor')
                  AND (r.consuming_step IS NULL OR r.consuming_step=?)
                ORDER BY CASE WHEN r.consuming_step=? THEN 0 ELSE 1 END,r.record_id LIMIT 16""", (task["task_id"], cursor_step, cursor_step))]
        optional_lines = []
        for record_id in optional:
            if record_id in selected:
                continue
            record, reason = _record(tx, task, record_id, required=False, max_bytes=min(max_fact_bytes, max(1, optional_tokens * 4)))
            if record is None:
                omitted[record_id] = reason
                continue
            line = render(record)
            content = line.split("] ", 1)[-1]
            if content in seen:
                selected[record_id] = record["version"]
                continue
            if _count("\n".join([*optional_lines, line]), count_tokens) > optional_tokens or _count(text() + line + "\n", count_tokens) > max_tokens or len((text() + line + "\n").encode()) > 262144:
                omitted[record_id] = "budget"
                continue
            lines.append(line)
            optional_lines.append(line)
            seen.add(content)
            selected[record_id] = record["version"]
        packet = text()
        metadata = {"schema_version": 1, "request_hash": request_hash, "run_id": run_id,
                    "task_id": task["task_id"], "task_revision": task["revision"], "phase": run["phase"], "mode": mode,
                    "records": selected, "omitted": omitted, "map_inputs": payload["map_inputs"],
                    "prerequisite_proofs": admission["prerequisites"],
                    "prompt_bundle": prompt_bundle,
                    "cursors": cursors, "pending": pending, "capture": sources, "receipts": receipts,
                    "charged_tokens": _count(packet, count_tokens), "tokenizer": tokenizer_id or "utf8-bytes",
                    "extraction_ready": not pending and not lag, "native_reset_ready": False}
        tx._change()
        artifact = tx.put_artifact(packet.encode(), owner_type="checkpoint", owner_id=checkpoint_id, slot="packet")
        revision = tx.db.execute("SELECT revision FROM ledger_meta WHERE singleton=1").fetchone()[0] + 1
        tx.db.execute("INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?,0)", (checkpoint_id, run_id, revision, run["map_revision"], task["revision"], canonical(metadata), artifact))
        tx.db.execute("INSERT INTO context_requests VALUES(?,?,?,?)", (run_id, request_id, checkpoint_id, request_hash))
        tx.db.execute("UPDATE runs SET checkpoint_id=? WHERE run_id=?", (checkpoint_id, run_id))
        _retire(tx, run_id, {checkpoint_id, run["checkpoint_id"]})
        return {"checkpoint_id": checkpoint_id, "packet_hash": artifact, "text": packet, **metadata}


def _read(tx, checkpoint_id, run_id):
    run, task, _ = _run(tx, run_id)
    row = tx.db.execute("SELECT * FROM checkpoints WHERE checkpoint_id=? AND run_id=?", (checkpoint_id, run_id)).fetchone()
    if row is None:
        raise ValidationError("checkpoint is unavailable or belongs to another run")
    metadata = json.loads(row["payload"])
    if row["task_revision"] != task["revision"]:
        raise Conflict("checkpoint assignment changed; issue current context")
    if metadata["phase"] != run["phase"]:
        raise Conflict("assignment phase changed; issue current context")
    if metadata.get("prompt_bundle"):
        from .prompt_compiler import context_instructions
        prompt = metadata["prompt_bundle"]
        _, version = context_instructions(tx.store.root, run["worker_id"], Path(prompt["manifest"]), workdir=task["payload"]["workdir"])
        if version != prompt["version"]:
            raise Conflict("checkpoint instructions changed; issue current context")
    for current in tx.db.execute("""SELECT r.record_id,r.version FROM records r JOIN record_heads h USING(record_id,version)
        WHERE task_id=? AND validity='current' AND kind IN ('goal','constraint','cursor') LIMIT ?""", (task["task_id"], MAX_RECORDS + 1)):
        if metadata["records"].get(current["record_id"]) != current["version"]:
            raise Conflict("required context changed; issue current context")
    for record_id, version in metadata["records"].items():
        header = tx.db.execute("SELECT r.version,r.validity FROM records r JOIN record_heads h USING(record_id,version) WHERE record_id=?", (record_id,)).fetchone()
        if not header or header["version"] != version or header["validity"] != "current":
            raise Conflict("checkpoint facts changed; issue current context")
        record = tx.record(record_id)
        _check_sources(record["inputs"], task["payload"], tx)
    return {"checkpoint_id": checkpoint_id, "packet_hash": row["packet_hash"],
            "text": tx.read_artifact(row["packet_hash"]).decode(), **metadata}


def read(store, checkpoint_id, run_id):
    with store.transaction() as tx:
        return _read(tx, checkpoint_id, run_id)


def acknowledge(store, checkpoint_id, run_id):
    with store.transaction() as tx:
        run, _, _ = _run(tx, run_id)
        if run["checkpoint_id"] != checkpoint_id:
            raise Conflict("acknowledgement must name the currently issued checkpoint")
        packet = _read(tx, checkpoint_id, run_id)
        tx._change()
        tx.db.execute("UPDATE checkpoints SET acknowledged=1 WHERE checkpoint_id=?", (checkpoint_id,))
        _retire(tx, run_id, {checkpoint_id})
        return {"checkpoint_id": checkpoint_id, "packet_hash": packet["packet_hash"], "acknowledged": True}
