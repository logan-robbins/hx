"""Frozen companion inputs and atomic, evidence-bound record patches.

Preparation loads a bounded event range and selected facts, not the transcript or
entire prior state. Calling the companion happens outside these transactions.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path

from .continuity_store import ContinuityStore, Conflict, canonical, digest
from .errors import ValidationError
from .facts import RequiredContextOverflow

MAX_EVENTS = 32
PASS_BYTES = 6000  # Conservative token charge until an adapter tokenizer is supplied.
PROTECTED = {"goal", "constraint"}


def _active_run(tx, run_id: str) -> dict:
    row = tx.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if not row or row["ended_at"] is not None:
        raise Conflict("pass requires an active run")
    task = tx.task(row["task_id"])
    if task["revision"] != row["task_revision"]:
        raise Conflict("task amended; rebind the run before preparing or committing a pass")
    return dict(row)


def _event_view(tx, row, available_bytes: int) -> dict:
    view = {"event_id": row["event_id"], "seq": row["seq"], "kind": row["kind"],
            "payload_hash": row["payload_hash"], "pending": row["disposition"] == "pending"}
    # Inspect lengths in SQLite; oversized bodies never cross into Python just
    # to discover that the model packet cannot accommodate them.
    if row["payload_bytes"] <= available_bytes:
        payload = tx.db.execute("SELECT payload FROM events WHERE event_id=?", (row["event_id"],)).fetchone()[0]
        view["payload"] = json.loads(payload)
    else:
        view.update(evidence=f"hx evidence {row['event_id']}", requires_read=True)
    return view


def prepare(store: ContinuityStore, run_id: str, stream_id: str, *,
            record_ids: tuple[str, ...] = (), prompt_version: str, max_bytes: int = PASS_BYTES,
            max_events: int = MAX_EVENTS, map_snapshot=None, map_record_ids=()) -> dict | None:
    from . import companion_map
    map_selection = companion_map.selection(map_snapshot, map_record_ids)
    if type(max_bytes) is not int or type(max_events) is not int or max_bytes < 1 or not 1 <= max_events <= MAX_EVENTS:
        raise ValidationError(f"pass bounds require positive bytes and 1–{MAX_EVENTS} events")
    if not isinstance(prompt_version, str) or not prompt_version:
        raise ValidationError("pass requires its prompt version")
    with store.transaction() as tx:
        run = _active_run(tx, run_id)
        cursor = tx.db.execute("SELECT * FROM cursors WHERE run_id=? AND stream_id=?", (run_id, stream_id)).fetchone()
        if not cursor:
            return None
        headers = "SELECT event_id,seq,kind,payload_hash,disposition,length(CAST(payload AS BLOB)) AS payload_bytes FROM events "
        pending = tx.db.execute(
            headers + "WHERE run_id=? AND stream_id=? AND disposition='pending' AND seq<=? ORDER BY seq LIMIT ?",
            (run_id, stream_id, cursor["classified_seq"], max_events),
        ).fetchall()
        fresh = tx.db.execute(
            headers + "WHERE run_id=? AND stream_id=? AND seq>? ORDER BY seq LIMIT ?",
            (run_id, stream_id, cursor["classified_seq"], max_events - len(pending)),
        ).fetchall()
        if not pending and not fresh:
            return None
        mandatory = [row[0] for row in tx.db.execute(
            "SELECT r.record_id FROM records r JOIN record_heads h USING(record_id,version) "
            "WHERE task_id=? AND validity='current' AND kind IN ('goal','constraint','cursor') ORDER BY record_id",
            (run["task_id"],),
        )]
        selected = {}
        for record_id in dict.fromkeys([*mandatory, *record_ids]):
            record = tx.record(record_id)
            if not record or record["task_id"] != run["task_id"] or record["validity"] != "current":
                raise Conflict(f"selected record {record_id} is absent, stale, or outside this task")
            _check_sources(record["inputs"], tx.task(run["task_id"])["payload"], tx)
            selected[record_id] = record
        pass_id = str(uuid.uuid4())
        body = {"schema_version": 1, "pass_id": pass_id, "run_id": run_id, "stream_id": stream_id,
                "task_id": run["task_id"], "task_revision": run["task_revision"],
                "task": tx.task(run["task_id"])["payload"], "prompt_version": prompt_version,
                "from_seq": cursor["classified_seq"], "to_seq": cursor["classified_seq"],
                "cursor_revision": cursor["revision"], "event_digest": "0" * 64,
                "record_versions": {key: value["version"] for key, value in selected.items()},
                "records": [{key: record[key] for key in ("record_id", "version", "kind", "payload", "evidence")}
                            for record in selected.values()], "events": []}
        if map_selection is not None:
            body["map_scope"] = companion_map.freeze(tx, body["task"], map_selection,
                max_bytes=max_bytes - len(canonical(body).encode()))
        if len(canonical(body).encode()) > max_bytes:
            raise RequiredContextOverflow("task and selected pass facts exceed the pass budget; narrow optional selection or split")
        for row in [*pending, *fresh]:
            view = _event_view(tx, row, max_bytes - len(canonical(body).encode()))
            candidate = {**body, "events": [*body["events"], view],
                         "to_seq": max(body["to_seq"], row["seq"])}
            if len(canonical(candidate).encode()) > max_bytes:
                # Give an exact evidence address, never a misleading head excerpt.
                view.pop("payload", None)
                view["evidence"] = f"hx evidence {row['event_id']}"
                view["requires_read"] = True
                candidate["events"][-1] = view
            if len(canonical(candidate).encode()) > max_bytes:
                if not body["events"]:
                    raise RequiredContextOverflow("pass cannot fit even one evidence address")
                break
            body = candidate
        body["event_digest"] = digest([(row["event_id"], row["payload_hash"]) for row in body["events"]])
        tx._change()
        tx.db.execute("INSERT INTO passes VALUES(?,?,?,?,?,?,?,?,?,?)",
                      (pass_id, run_id, stream_id, body["from_seq"], body["to_seq"], body["task_revision"],
                       body["cursor_revision"], body["event_digest"], canonical(body), "prepared"))
        tx.put_artifact(canonical(body).encode(), owner_type="pass", owner_id=pass_id, slot="input")
        return body


def _check_sources(inputs: dict, task: dict, tx=None) -> None:
    """Recheck declared files in bounded chunks; hashes never enter the model packet."""
    if not isinstance(inputs, dict) or inputs.keys() - {"files", "map"}:
        raise ValidationError("record inputs must contain declared file fingerprints")
    if "map" in inputs:
        from .map_dependencies import validate_refs
        if tx is None:
            raise ValidationError("map inputs require a ledger transaction")
        validate_refs(tx, inputs["map"], task)
    files = inputs.get("files", {})
    if not isinstance(files, dict):
        raise ValidationError("inputs.files must map exact paths to SHA-256 values")
    for name, expected in files.items():
        if not isinstance(name, str) or not isinstance(expected, str) or len(expected) != 64:
            raise ValidationError("invalid source fingerprint")
        path = Path(name)
        if not path.is_absolute():
            workdir = task.get("workdir")
            if not isinstance(workdir, str) or not Path(workdir).is_absolute():
                raise ValidationError("relative source fingerprints require an absolute task workdir")
            path = Path(workdir) / path
        hasher = hashlib.sha256()
        try:
            with path.open("rb") as handle:
                for chunk in iter(lambda: handle.read(65536), b""):
                    hasher.update(chunk)
        except OSError as exc:
            raise Conflict(f"source unavailable: {name}") from exc
        if hasher.hexdigest() != expected:
            raise Conflict(f"source changed: {name}")


def _public_event(tx, row) -> dict:
    payload = json.loads(row["payload"])
    if "artifact_hash" in payload:
        return json.loads(tx.read_artifact(payload["artifact_hash"]))["data"]
    if "observation" in payload:
        return payload["observation"]["data"]
    return payload


def _write_operation(tx, operation: dict, frozen: dict, run: dict, allowed_evidence: set[str]) -> str:
    if not isinstance(operation, dict):
        raise ValidationError("pass operations must be objects")
    if operation.keys() - {"op", "record_id", "expected_version", "record", "reason"}:
        raise ValidationError("unknown pass operation fields")
    op, record_id, version = operation.get("op"), operation.get("record_id"), operation.get("expected_version")
    if op not in {"create", "supersede", "compress", "invalidate", "drop"} or type(version) is not int or version < 0:
        raise ValidationError("operation requires a known op and nonnegative expected_version")
    old = tx.record(record_id)
    if op == "create":
        if version != 0 or old:
            raise Conflict("create requires a new record")
    elif not old or frozen["record_versions"].get(record_id) != version or old["version"] != version:
        raise Conflict(f"record {record_id} was not read at the expected version")
    if old and old["kind"] in PROTECTED:
        raise ValidationError("binding goals/constraints require an explicit task amendment")
    if old and old["kind"] == "cursor" and op in {"drop", "invalidate"}:
        raise ValidationError("an active cursor cannot be discarded")
    if op in {"drop", "invalidate"}:
        if not isinstance(operation.get("reason"), str) or not operation["reason"].strip():
            raise ValidationError("drop/invalidate requires an explicit reason")
        record = {key: old[key] for key in ("kind", "payload", "evidence", "inputs", "reason", "expires_when", "retention", "consuming_step")}
        record.update(validity="dropped" if op == "drop" else "invalid", reason=operation["reason"])
    else:
        record = operation.get("record")
        if not isinstance(record, dict):
            raise ValidationError("record operation requires its typed record")
        record = dict(record)
        required = {"kind", "payload", "evidence", "inputs", "reason", "expires_when"}
        if not required <= record.keys() or record.keys() - required - {"retention", "consuming_step"}:
            raise ValidationError("record has missing or unknown fields")
        record["validity"] = "current"
    if not isinstance(record["evidence"], list) or any(not isinstance(e, str) or e not in allowed_evidence for e in record["evidence"]):
        raise ValidationError("record cites evidence outside this frozen pass and its selected records")
    if not record["evidence"]:
        raise ValidationError("a companion fact requires captured evidence")
    if record["kind"] in PROTECTED:
        text = record["payload"].get("text")
        grounded = False
        for event_id in record["evidence"]:
            row = tx.db.execute("SELECT * FROM events WHERE event_id=?", (event_id,)).fetchone()
            if row["kind"] in {"request", "correction"}:
                source_text = _public_event(tx, row).get("text")
                grounded |= isinstance(text, str) and bool(text) and isinstance(source_text, str) and text in source_text
        if not grounded:
            raise ValidationError("new binding facts must preserve a span of the captured request/correction")
    if op == "compress":
        protected = {"command": ("command", "cwd", "when", "env"),
                     "finding": ("path", "symbol"), "decision": ("when",),
                     "dead_end": ("retry_when",), "search": ("query", "cwd", "scope", "flags"),
                     "cursor": ("step", "phase", "next", "blocker", "children")}.get(old["kind"], ())
        if any(record["payload"].get(key) != old["payload"].get(key) for key in protected):
            raise ValidationError("compression changes protected applicability or executable fields")
    if record["validity"] == "current":
        _check_sources(record["inputs"], frozen["task"], tx)
    tx.put_record(record_id, expected_version=version, task_id=run["task_id"], **record)
    return record_id


def commit(store: ContinuityStore, pass_id: str, response: dict) -> dict:
    fields = {"schema_version", "pass_id", "event_digest", "task_revision", "operations", "dispositions"}
    if (not isinstance(response, dict) or not fields <= response.keys() or response.keys() - fields - {"event_reasons", "map_patch"}
        or type(response.get("schema_version")) is not int or response["schema_version"] != 1
        or type(response.get("task_revision")) is not int):
        raise ValidationError("pass response has missing/unknown fields or schema")
    if response["pass_id"] != pass_id or not isinstance(response["operations"], list) or not isinstance(response["dispositions"], dict):
        raise ValidationError("pass response identity/operations/dispositions are invalid")
    response_hash = digest(response)
    prepared_map = None
    if "map_patch" in response:
        from . import companion_map
        with store.transaction() as tx:
            row = tx.db.execute("SELECT status,payload FROM passes WHERE pass_id=?", (pass_id,)).fetchone()
            if row is None:
                raise ValidationError("unknown companion pass")
            # Committed replay must not inspect changed or retired source inputs.
            if row["status"] == "committed":
                return _committed(tx, pass_id, response_hash)
            frozen = json.loads(row["payload"])
            _active_run(tx, frozen["run_id"])
            if response["task_revision"] != frozen["task_revision"] or response["event_digest"] != frozen["event_digest"]:
                raise Conflict("pass task revision or event digest differs from its frozen input")
        prepared_map = companion_map.prepare_patch(store, frozen, response["map_patch"])
    with store.transaction() as tx:
        row = tx.db.execute("SELECT * FROM passes WHERE pass_id=?", (pass_id,)).fetchone()
        if not row:
            raise ValidationError("unknown companion pass")
        if row["status"] == "committed":
            return _committed(tx, pass_id, response_hash)
        if row["status"] != "prepared":
            raise Conflict("pass is not eligible for commit")
        frozen = json.loads(row["payload"])
        if response["task_revision"] != frozen["task_revision"] or response["event_digest"] != frozen["event_digest"]:
            raise Conflict("pass task revision or event digest differs from its frozen input")
        run = _active_run(tx, row["run_id"])
        if run["task_revision"] != frozen["task_revision"]:
            raise Conflict("pass belongs to an earlier task revision")
        cursor = tx.db.execute("SELECT * FROM cursors WHERE run_id=? AND stream_id=?", (row["run_id"], row["stream_id"])).fetchone()
        if cursor["revision"] != row["cursor_revision"] or cursor["classified_seq"] != row["from_seq"]:
            raise Conflict("pass cursor changed during extraction")
        allowed = {event["event_id"] for event in frozen["events"]}
        if set(response["dispositions"]) != allowed:
            raise ValidationError("each frozen event needs exactly one disposition")
        reasons = response.get("event_reasons", {})
        if not isinstance(reasons, dict) or reasons.keys() - allowed or any(not isinstance(v, str) or not v.strip() for v in reasons.values()):
            raise ValidationError("event reasons must name frozen events and explain their disposition")
        actual = []
        for event in frozen["events"]:
            captured = tx.db.execute("SELECT * FROM events WHERE event_id=?", (event["event_id"],)).fetchone()
            if not captured or captured["run_id"] != run["run_id"] or captured["payload_hash"] != event["payload_hash"]:
                raise Conflict("frozen evidence is missing or changed")
            if event["pending"] and captured["disposition"] != "pending":
                raise Conflict("pending evidence was already resolved")
            actual.append(captured)
        for record_id, version in frozen["record_versions"].items():
            record = tx.record(record_id)
            if not record or record["version"] != version or record["validity"] != "current":
                raise Conflict(f"selected record {record_id} changed during extraction")
            _check_sources(record["inputs"], frozen["task"], tx)
            allowed.update(record["evidence"])
        map_result = companion_map.apply_patch(tx, frozen, prepared_map) if prepared_map is not None else None
        changed = []
        seen = set()
        for operation in response["operations"]:
            record_id = operation.get("record_id") if isinstance(operation, dict) else None
            if not isinstance(record_id, str) or not record_id or record_id in seen:
                raise ValidationError("each record may be changed once per pass")
            seen.add(record_id)
            changed.append(_write_operation(tx, operation, frozen, run, allowed))
        if prepared_map is not None:
            companion_map.finalize(tx, frozen, prepared_map, map_result)
        for event in actual:
            disposition = response["dispositions"][event["event_id"]]
            if disposition not in {"reduced", "extracted", "no_change", "dropped", "pending"}:
                raise ValidationError("unknown event disposition")
            if event["kind"] in {"request", "correction"} and disposition != "pending":
                if not any(event["event_id"] in tx.record(record_id)["evidence"]
                           and tx.record(record_id)["kind"] in PROTECTED for record_id in changed):
                    if disposition not in {"no_change", "dropped"} or event["event_id"] not in reasons:
                        raise ValidationError("uninterpreted request/correction must remain pending or have an explicit retention decision")
        fresh = {event["seq"]: response["dispositions"][event["event_id"]]
                 for event in actual if event["seq"] > row["from_seq"]}
        tx.classify(run["run_id"], row["stream_id"], expected_revision=row["cursor_revision"],
                    through=row["to_seq"], dispositions=fresh)
        for event in actual:
            if event["seq"] <= row["from_seq"]:
                tx.db.execute("UPDATE events SET disposition=? WHERE event_id=?",
                              (response["dispositions"][event["event_id"]], event["event_id"]))
        tx.db.execute("UPDATE passes SET status='committed' WHERE pass_id=?", (pass_id,))
        result = {"pass_id": pass_id, "response_hash": response_hash, "to_seq": row["to_seq"],
                  "cursor_revision": row["cursor_revision"] + 1, "changed_records": changed}
        if map_result is not None:
            result["map_patch"] = map_result
        tx.put_artifact(canonical(result).encode(), owner_type="pass", owner_id=pass_id, slot="result")
        tx.enqueue("projection", f"pass:{pass_id}", {"run_id": run["run_id"], "stream_id": row["stream_id"], **result})
        return result


def _committed(tx, pass_id, response_hash):
    ref = tx.db.execute("SELECT hash FROM artifact_refs WHERE owner_type='pass' AND owner_id=? AND slot='result'", (pass_id,)).fetchone()
    result = json.loads(tx.read_artifact(ref[0]))
    if result["response_hash"] != response_hash:
        raise Conflict("committed pass received a different response")
    return result
