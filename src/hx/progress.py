"""Atomic, sparse progress updates; agent assertions never become check receipts."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath

from .continuity_store import ContinuityStore, Conflict, _id, canonical, digest
from .errors import ValidationError

MAX_BYTES = 16384
STATUSES = {"planned", "active", "blocked", "done", "cancelled"}


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"progress {label} must be nonempty text")


def validate(body: dict) -> None:
    required = {"task_id", "run_id", "expected_revision", "phase", "step_updates", "next", "blocker",
                "deliverables", "evidence_ids"}
    if not isinstance(body, dict) or body.keys() != required:
        raise ValidationError("progress requires exactly task_id, run_id, expected_revision, phase, step_updates, next, blocker, deliverables, evidence_ids")
    if len(canonical(body).encode()) > MAX_BYTES:
        raise ValidationError("progress exceeds the 16 KiB update bound; submit a smaller delta")
    for field in ("task_id", "run_id"):
        _id(body[field])
    if type(body["expected_revision"]) is not int or body["expected_revision"] < 0:
        raise ValidationError("progress expected_revision must be a nonnegative integer")
    for field in ("phase", "next"):
        _text(body[field], field)
    if body["blocker"] is not None:
        _text(body["blocker"], "blocker")
    for field, limit in (("step_updates", 32), ("deliverables", 16), ("evidence_ids", 32)):
        if not isinstance(body[field], list) or len(body[field]) > limit:
            raise ValidationError(f"progress {field} must be an array of at most {limit} entries")
    seen = set()
    for step in body["step_updates"]:
        if not isinstance(step, dict) or not {"step_id", "status"} <= step.keys() or step.keys() - {"step_id", "status", "summary", "last"}:
            raise ValidationError("step update requires step_id/status; only summary/last are optional")
        _id(step["step_id"])
        if step["step_id"] in seen or not isinstance(step["status"], str) or step["status"] not in STATUSES:
            raise ValidationError("progress has a duplicate step or unknown step status")
        seen.add(step["step_id"])
        for field in ("summary", "last"):
            if field in step:
                _text(step[field], f"step.{field}")
    seen.clear()
    for item in body["deliverables"]:
        if not isinstance(item, dict) or not {"path", "op"} <= item.keys() or item.keys() - {"path", "op", "description"}:
            raise ValidationError("deliverable requires path/op and optional description")
        _text(item["path"], "deliverable.path")
        path = PurePosixPath(item["path"])
        if path.is_absolute() or ".." in path.parts or "\\" in item["path"] or str(path) != item["path"] or str(path) == ".":
            raise ValidationError("deliverable path must be normalized and relative to the task workdir")
        if item["path"] in seen or item["op"] not in ("upsert", "drop"):
            raise ValidationError("progress has a duplicate deliverable or unknown operation")
        seen.add(item["path"])
        if item["op"] == "upsert":
            _text(item.get("description"), "deliverable.description")
        elif "description" in item:
            raise ValidationError("a dropped deliverable does not take a description")
    seen.clear()
    for evidence in body["evidence_ids"]:
        _id(evidence)
        if evidence in seen:
            raise ValidationError("progress evidence IDs must be unique")
        seen.add(evidence)


def update(store: ContinuityStore, body: dict, *, worker_id: str | None = None) -> dict:
    validate(body)
    request_hash = digest(body)
    task_id, run_id = body["task_id"], body["run_id"]
    with store.transaction() as tx:
        run = tx.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None or run["task_id"] != task_id or (worker_id is not None and run["worker_id"] != worker_id):
            raise Conflict("progress caller/run does not belong to this task")
        previous = tx.db.execute("SELECT payload FROM progress_updates WHERE run_id=? AND request_hash=?",
                                 (run_id, request_hash)).fetchone()
        if previous:
            return json.loads(previous[0])
        if run["ended_at"] is not None or tx.task(task_id)["revision"] != run["task_revision"]:
            raise Conflict("progress requires an active run bound to the current task revision")
        head = tx.db.execute("SELECT * FROM progress_heads WHERE task_id=?", (task_id,)).fetchone()
        revision = head["revision"] if head else 0
        if body["expected_revision"] != revision:
            raise Conflict(f"progress expected revision {body['expected_revision']}, found {revision}")
        for event_id in body["evidence_ids"]:
            evidence = tx.db.execute("SELECT r.task_id FROM events e JOIN runs r USING(run_id) WHERE event_id=?", (event_id,)).fetchone()
            if evidence is None or evidence[0] != task_id:
                raise ValidationError("progress evidence is absent or belongs to another task")
        # The exact submitted claim is evidence of what the worker reported. It
        # cannot establish tool execution, check success, or task completion.
        event = tx.append_event(run_id, "progress", f"progress:{request_hash}", "progress", {"claim": True, "update": body})
        evidence = [event["event_id"], *body["evidence_ids"]]
        refs = canonical(evidence)
        tx._change()
        for step in body["step_updates"]:
            old = tx.db.execute("SELECT payload,evidence FROM progress_steps WHERE task_id=? AND step_id=?", (task_id, step["step_id"])).fetchone()
            payload = {**(json.loads(old[0]) if old else {}), **step, "claim": True}
            field_evidence = {**(json.loads(old[1]) if old else {}), **{field: evidence for field in step}}
            tx.db.execute("INSERT INTO progress_steps VALUES(?,?,?,?,?) ON CONFLICT(task_id,step_id) DO UPDATE SET status=excluded.status,payload=excluded.payload,evidence=excluded.evidence",
                          (task_id, step["step_id"], step["status"], canonical(payload), canonical(field_evidence)))
        active = tx.db.execute("SELECT step_id,payload FROM progress_steps WHERE task_id=? AND status='active' LIMIT 2", (task_id,)).fetchall()
        if len(active) > 1:
            raise ValidationError("progress must have at most one active step; transition the previous step in the same update")
        for item in body["deliverables"]:
            if item["op"] == "drop":
                tx.db.execute("DELETE FROM progress_deliverables WHERE task_id=? AND path=?", (task_id, item["path"]))
            else:
                tx.db.execute("INSERT INTO progress_deliverables VALUES(?,?,?,?) ON CONFLICT(task_id,path) DO UPDATE SET payload=excluded.payload,evidence=excluded.evidence",
                              (task_id, item["path"], canonical({"path": item["path"], "description": item["description"], "claim": True}), refs))
        cursor_id = f"cursor-{digest(task_id)[:32]}"
        old_cursor = tx.record(cursor_id)
        step_name = active[0]["step_id"] if active else (old_cursor["payload"]["step"] if old_cursor else task_id)
        cursor = {"schema_version": 1, "step": step_name, "phase": body["phase"], "next": body["next"]}
        if body["blocker"] is not None:
            cursor["blocker"] = body["blocker"]
        step_row = tx.db.execute("SELECT payload,evidence FROM progress_steps WHERE task_id=? AND step_id=?", (task_id, step_name)).fetchone()
        cursor_evidence = list(evidence)
        if step_row:
            step = json.loads(step_row["payload"])
            sources = json.loads(step_row["evidence"])
            cursor_evidence.extend(sources.get("step_id", []))
            if step.get("last"):
                cursor["last"] = step["last"]
                cursor_evidence.extend(sources.get("last", []))
        cursor_version = tx.put_record(cursor_id, expected_version=old_cursor["version"] if old_cursor else 0,
            task_id=task_id, kind="cursor", payload=cursor, evidence=list(dict.fromkeys(cursor_evidence)), inputs={},
            reason="Current execution step and next action.", expires_when="A newer progress update supersedes this cursor.")
        revision += 1
        result = {"task_id": task_id, "run_id": run_id, "revision": revision,
                  "event_id": event["event_id"], "cursor_id": cursor_id, "cursor_version": cursor_version}
        tx.db.execute("INSERT INTO progress_heads VALUES(?,?,?,?) ON CONFLICT(task_id) DO UPDATE SET revision=excluded.revision,run_id=excluded.run_id,cursor_id=excluded.cursor_id",
                      (task_id, revision, run_id, cursor_id))
        tx.db.execute("INSERT INTO progress_updates VALUES(?,?,?)", (run_id, request_hash, canonical(result)))
        tx.db.execute("UPDATE runs SET phase=? WHERE run_id=?", (body["phase"], run_id))
        cursor_row = tx.db.execute("SELECT * FROM cursors WHERE run_id=? AND stream_id='progress'", (run_id,)).fetchone()
        tx.classify(run_id, "progress", expected_revision=cursor_row["revision"], through=event["seq"],
                    dispositions={event["seq"]: "reduced"})
        tx.enqueue("projection", f"progress:{task_id}:{revision}", result)
        return result


def snapshot(store: ContinuityStore, run_id: str, *, worker_id: str | None = None) -> dict:
    """Read the update revision and current cursor, not the full prior task state."""
    with store.transaction() as tx:
        run = tx.db.execute("SELECT task_id,task_revision,worker_id,ended_at FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if run is None or (worker_id is not None and run["worker_id"] != worker_id):
            raise Conflict("progress caller/run is unknown or belongs to another worker")
        if run["ended_at"] is not None or tx.task(run["task_id"])["revision"] != run["task_revision"]:
            raise Conflict("progress snapshot requires an active run bound to the current task revision")
        head = tx.db.execute("SELECT revision,cursor_id FROM progress_heads WHERE task_id=?", (run["task_id"],)).fetchone()
        cursor = tx.record(head["cursor_id"]) if head else None
        return {"task_id": run["task_id"], "run_id": run_id, "task_revision": run["task_revision"],
                "revision": head["revision"] if head else 0, "active": run["ended_at"] is None,
                "cursor": {key: cursor[key] for key in ("record_id", "version", "payload", "evidence")} if cursor else None}


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx progress")
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--file", help="submit a sparse progress update")
    action.add_argument("--run", help="read this run's update revision and current cursor")
    parser.add_argument("--root")
    args = parser.parse_args(argv)
    worker_id = (os.environ if env is None else env).get("HARNESS_ID")
    if args.run:
        with ContinuityStore(root) as store:
            print(canonical(snapshot(store, args.run, worker_id=worker_id)))
        return 0
    with Path(args.file).open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValidationError("progress exceeds the 16 KiB update bound")
    try:
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValidationError("progress file must contain one JSON object") from exc
    with ContinuityStore(root) as store:
        print(canonical(update(store, body, worker_id=worker_id)))
    return 0
