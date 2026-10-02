"""Jev selects optional current-task facts before the native companion loads them."""

from __future__ import annotations

import json
import uuid

from . import jev, passes
from .continuity_store import Conflict, canonical, digest
from .errors import ValidationError
from .facts import RequiredContextOverflow, render_fact
from .events import is_bookkeeping

POLICY = "companion-record-selection-v1"
MAX_CANDIDATES = 16
MAX_REQUESTS = 4
MAX_INPUT_BYTES = 16000
THRESHOLD = 0.90


def _snapshot(store, run_id, stream_id, exclude_ids):
    with store.transaction() as tx:
        run = passes._active_run(tx, run_id)
        task = tx.task(run["task_id"])
        state = {"goal": task["payload"]["goal"], "constraints": task["payload"].get("constraints", [])}
        required = tx.db.execute("""SELECT r.record_id,r.version,length(CAST(r.payload AS BLOB)) AS bytes
            FROM records r JOIN record_heads h USING(record_id,version)
            WHERE r.task_id=? AND r.validity='current' AND r.kind IN ('goal','constraint','cursor')
            ORDER BY r.record_id LIMIT 65""", (run["task_id"],)).fetchall()
        if len(required) > 64 or sum(row["bytes"] for row in required) > jev.INPUT_BYTES:
            raise RequiredContextOverflow("Jev selection requires a smaller current goal/constraint/cursor state")
        state["required"] = [render_fact(row["record_id"], row["version"], record["kind"], record["payload"])
                             for row in required for record in [tx.record(row["record_id"])]]
        step = next((tx.record(row["record_id"])["payload"]["step"] for row in required
                     if tx.record(row["record_id"])["kind"] == "cursor"), None)
        cursor = tx.db.execute("SELECT head_seq,classified_seq,revision FROM cursors WHERE run_id=? AND stream_id=?",
                               (run_id, stream_id)).fetchone()
        if cursor is None or cursor['head_seq'] == 0:
            return task, state, [], None, False
        events = [dict(row) for row in tx.db.execute("""SELECT event_id,seq,kind,payload_hash,
            length(CAST(payload AS BLOB)) AS bytes FROM events WHERE run_id=? AND stream_id=?
            AND (seq>? OR disposition='pending') ORDER BY seq LIMIT 32""", (run_id, stream_id, cursor['classified_seq']))]
        if not events:
            return task, state, [], None, False
        state["recent"] = []
        bookkeeping_only = True
        for event in events:
            # Whole small observations only; no truncated claims or full transcript.
            if event["bytes"] <= 512:
                value = tx.db.execute("SELECT payload FROM events WHERE event_id=?", (event['event_id'],)).fetchone()[0]
                payload = json.loads(value)
                bookkeeping_only = bookkeeping_only and is_bookkeeping({"kind": event["kind"], "payload": payload})
                if len(state["recent"]) < 4:
                    state["recent"].append({"kind": event["kind"], "observation": payload})
            else:
                bookkeeping_only = False
        if bookkeeping_only:
            return task, state, [], None, False
        rows = tx.db.execute("""SELECT r.record_id,r.version,length(CAST(r.payload AS BLOB)) AS bytes
            FROM records r JOIN record_heads h USING(record_id,version)
            WHERE r.task_id=? AND r.validity='current' AND r.kind NOT IN ('goal','constraint','cursor')
            AND (r.retention='context' OR r.consuming_step=?)
            ORDER BY CASE WHEN r.consuming_step=? THEN 0 ELSE 1 END,r.record_id LIMIT 17""",
            (run["task_id"], step, step)).fetchall()
        candidates = []
        for row in rows[:MAX_CANDIDATES]:
            if row['record_id'] in exclude_ids:
                continue
            if row["bytes"] > jev.INPUT_BYTES:
                raise RequiredContextOverflow("a Jev selection candidate exceeds the request budget; compress the fact")
            record = tx.record(row["record_id"])
            passes._check_sources(record["inputs"], task["payload"], tx)
            candidates.append({"id": row["record_id"], "version": row["version"], "inputs": record["inputs"],
                "text": render_fact(row["record_id"], row["version"], record["kind"], record["payload"])})
        snapshot = {"run": run_id, "stream": stream_id, "task_revision": task["revision"],
            "cursor": dict(cursor), "events": events, "required": [dict(row) for row in required]}
        return task, state, candidates, snapshot, len(rows) > MAX_CANDIDATES


def _batches(state, candidates):
    batches, questions = [], {}
    for index, candidate in enumerate(candidates):
        name = "candidate_" + str(index)
        question = {"type": "noul", "instructions": {
            "question": "Does this fact help interpret the recent observations or carry out the current next action?",
            "fact": candidate["text"]}}
        try:
            jev.encode(state, {**questions, name: question})
        except jev.Unavailable:
            if not questions:
                raise RequiredContextOverflow("Jev state and one complete candidate exceed the request budget") from None
            batches.append(questions)
            questions = {}
            try:
                jev.encode(state, {name: question})
            except jev.Unavailable:
                raise RequiredContextOverflow("Jev state and one complete candidate exceed the request budget") from None
        questions[name] = question
    if questions:
        batches.append(questions)
    if len(batches) > MAX_REQUESTS or sum(len(jev.encode(state, batch)) for batch in batches) > MAX_INPUT_BYTES:
        raise RequiredContextOverflow("Jev selection exceeds four requests or 16,000 input bytes; narrow current facts")
    return batches


def for_pass(store, run_id, stream_id, *, env=None, exclude_ids=()):
    task, state, candidates, snapshot, has_more = _snapshot(store, run_id, stream_id, exclude_ids)
    if not candidates:
        return {"status": "no_candidates", "records": {}, "has_more": False}
    batches = _batches(state, candidates)
    input_hash = digest({"model": jev.MODEL, "policy": POLICY, "snapshot": snapshot,
                         "state": state, "candidates": candidates, "questions": batches})
    request_id = str(uuid.uuid4())
    with store.transaction() as tx:
        old = tx.db.execute("SELECT payload FROM retrieval_runs WHERE task_id=? AND input_hash=?", (task["task_id"], input_hash)).fetchone()
        if old:
            result = json.loads(old[0])
            if result["status"] != "complete":
                raise Conflict("Jev selection is already in flight; no substitute selection is available")
            return result
        # Resolve credentials before reserving; missing configuration never pretends
        # to be a semantic result, and secrets never enter the ledger.
        try:
            key = jev.credential(store.root, env)
        except jev.Unavailable as exc:
            raise ValidationError("Jev selection stopped: " + str(exc)) from None
        tx._change()
        tx.db.execute("INSERT INTO retrieval_runs VALUES(?,?,?,?)", (request_id, task["task_id"], input_hash,
            canonical({"status": "running", "policy": POLICY})))
    answers, usage, latency = {}, {"input_tokens": 0, "output_tokens": 0}, 0
    try:
        for questions in batches:
            result = jev.evaluate(state, questions, key=key)
            answers.update(result["answers"])
            for name in usage:
                usage[name] += result["usage"][name]
            latency += result["latency_ms"]
        records = {candidate["id"]: candidate["version"] for index, candidate in enumerate(candidates)
                   if answers['candidate_'+str(index)]["noul"] >= THRESHOLD}
        result = {"status": "complete", "retrieval_id": request_id, "policy": POLICY, "model": jev.MODEL,
            "records": records, "has_more": has_more, "scores": {candidate["id"]: answers['candidate_'+str(index)]["noul"]
                for index, candidate in enumerate(candidates)}, "usage": usage, "requests": len(batches), "latency_ms": latency}
        with store.transaction() as tx:
            run = passes._active_run(tx, run_id)
            if run["task_revision"] != task["revision"]:
                raise Conflict("task changed during Jev selection")
            for candidate in candidates:
                current = tx.record(candidate["id"])
                if current is None or current["version"] != candidate["version"] or current["validity"] != "current":
                    raise Conflict("candidate changed during Jev selection")
            tx._change()
            tx.db.execute("UPDATE retrieval_runs SET payload=? WHERE retrieval_id=?", (canonical(result), request_id))
        return result
    except BaseException as exc:
        # No retries or fallback inside this operation. A later explicit attempt
        # may call again; uncertain interruption retains the in-flight reservation.
        if isinstance(exc, (jev.Unavailable, Conflict, ValidationError)):
            with store.transaction() as tx:
                tx._change()
                tx.db.execute("DELETE FROM retrieval_runs WHERE retrieval_id=?", (request_id,))
        if isinstance(exc, jev.Unavailable):
            raise ValidationError("Jev selection stopped: " + str(exc)) from None
        raise
