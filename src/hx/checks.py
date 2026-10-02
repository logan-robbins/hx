"""Input-bound checks, bounded output capture, and reusable execution receipts."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import selectors
import signal
import subprocess
import time
import uuid
from pathlib import Path

from . import fingerprints
from .continuity_store import ContinuityStore, Conflict, _id, canonical, digest
from .errors import ValidationError

MAX_RECIPE_BYTES = 16384


def validate(recipe: dict) -> str:
    fields = {"schema_version", "id", "argv", "cwd", "inputs", "environment", "timeout_s", "max_output_bytes"}
    if not isinstance(recipe, dict) or recipe.keys() != fields or type(recipe["schema_version"]) is not int or recipe["schema_version"] != 1:
        raise ValidationError("check recipe requires schema_version=1, id, argv, cwd, inputs, environment, timeout_s, max_output_bytes")
    if len(canonical(recipe).encode()) > MAX_RECIPE_BYTES:
        raise ValidationError("check recipe exceeds 16 KiB")
    _id(recipe["id"])
    for field in ("argv", "inputs"):
        if not isinstance(recipe[field], list) or len(recipe[field]) > 128 or any(not isinstance(x, str) or not x or "\0" in x for x in recipe[field]):
            raise ValidationError(f"check {field} must be a bounded string array")
    if not recipe["argv"] or not isinstance(recipe["cwd"], str) or not recipe["cwd"] or "\0" in recipe["cwd"]:
        raise ValidationError("check needs argv and cwd")
    if type(recipe["timeout_s"]) not in (int, float) or not math.isfinite(recipe["timeout_s"]) or not 0 < recipe["timeout_s"] <= 3600:
        raise ValidationError("check timeout must be positive and at most one hour")
    if type(recipe["max_output_bytes"]) is not int or not 1 <= recipe["max_output_bytes"] <= 64 * 1024 * 1024:
        raise ValidationError("check output bound must be 1 byte through 64 MiB")
    env = recipe["environment"]
    if not isinstance(env, dict) or env.keys() != {"complete", "executables", "inputs", "external_versions"} or type(env["complete"]) is not bool:
        raise ValidationError("environment recipe requires complete, executables, inputs, external_versions")
    for field in ("executables", "inputs"):
        if not isinstance(env[field], list) or len(env[field]) > 128 or any(not isinstance(x, str) or not x or "\0" in x for x in env[field]):
            raise ValidationError(f"environment {field} must be a bounded string array")
    if not isinstance(env["external_versions"], dict) or len(env["external_versions"]) > 32:
        raise ValidationError("external_versions must map at most 32 names to version files or null")
    for name, path in env["external_versions"].items():
        _id(name)
        if path is not None and (not isinstance(path, str) or not path or "\0" in path):
            raise ValidationError("external version source must be a path or null")
    return digest(recipe)


def _assignment(tx, run_id, check_id, recipe, worker_id=None):
    run = tx.db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if run is None or (worker_id is not None and run["worker_id"] != worker_id):
        raise Conflict("check caller does not own this run")
    task = tx.task(run["task_id"])
    if run["ended_at"] is not None or task["revision"] != run["task_revision"]:
        raise Conflict("check requires an active run pinned to the current task revision")
    from .map_dependencies import validate_task
    validate_task(tx, task)
    assigned = task["payload"].get("checks", {}).get(check_id)
    if recipe is None:
        if assigned is None:
            raise ValidationError(f"check {check_id} has no assigned recipe")
        recipe = assigned
    version = validate(recipe)
    if recipe["id"] != check_id or (assigned is not None and digest(assigned) != version):
        raise Conflict("check recipe differs from the assigned check version")
    base = task["payload"].get("workdir")
    if not isinstance(base, str) or not Path(base).is_absolute():
        raise ValidationError("check task needs an absolute workdir")
    cwd = (Path(base) / recipe["cwd"]).resolve()
    if not cwd.is_relative_to(Path(base).resolve()) or not cwd.is_dir():
        raise ValidationError("check cwd must be an existing directory inside the task workdir")
    return dict(run), recipe, version, cwd


def _stop(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def execute(argv, cwd, env, output: Path, timeout_s, max_bytes, *, stdin=None, on_spawn=None):
    """Drain one pipe in 64 KiB chunks; output overflow/timeouts cannot pass."""
    started = time.monotonic()
    reason = None
    size = 0
    with output.open("xb") as target:
        os.chmod(output, 0o600)
        try:
            process = subprocess.Popen(argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL if stdin is None else stdin,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
        except OSError as exc:
            message = f"Check could not start: {type(exc).__name__}.\n".encode()[:max_bytes]
            target.write(message)
            return {"exit_code": 127, "duration": time.monotonic() - started, "reason": "spawn_failed", "output_bytes": len(message)}
        try:
            if on_spawn is not None:
                on_spawn(process)
            os.set_blocking(process.stdout.fileno(), False)
            with selectors.DefaultSelector() as selected:
                selected.register(process.stdout, selectors.EVENT_READ)
                while selected.get_map():
                    remaining = timeout_s - (time.monotonic() - started)
                    if remaining <= 0:
                        reason = "timeout"
                        break
                    for key, _ in selected.select(min(remaining, 0.1)):
                        chunk = os.read(key.fd, fingerprints.CHUNK_BYTES)
                        if not chunk:
                            selected.unregister(key.fileobj)
                            break
                        available = max_bytes - size
                        target.write(chunk[:available])
                        size += min(len(chunk), available)
                        if len(chunk) > available:
                            reason = "output_limit"
                            break
                    if reason:
                        break
            if reason:
                _stop(process)
            else:
                try:
                    process.wait(timeout=max(0.001, timeout_s - (time.monotonic() - started)))
                except subprocess.TimeoutExpired:
                    reason = "timeout"
                    _stop(process)
                if reason is None:
                    try:
                        os.killpg(process.pid, 0)
                    except ProcessLookupError:
                        pass
                    else:
                        reason = "background_processes"
                        _stop(process)
        except BaseException:
            _stop(process)
            raise
        finally:
            process.stdout.close()
        target.flush()
        os.fsync(target.fileno())
    return {"exit_code": process.returncode, "duration": time.monotonic() - started,
            "reason": reason, "output_bytes": size}


def _artifact_intact(store, sha):
    try:
        with (store.artifacts / sha).open("rb") as handle:
            hasher = hashlib.sha256()
            for chunk in iter(lambda: handle.read(fingerprints.CHUNK_BYTES), b""):
                hasher.update(chunk)
        return hasher.hexdigest() == sha
    except OSError:
        return False


def reusable(store, run_id, check_id, version, source, environment):
    if not source["complete"] or not environment["complete"]:
        return None
    rows = store.db.execute("SELECT receipt_id,artifact_hash,payload FROM receipts WHERE run_id=? AND check_id=? AND check_version=? AND inputs_hash=? AND environment_hash=? AND valid=1 ORDER BY rowid DESC",
        (run_id, check_id, version, source["hash"], environment["hash"]))
    for row in rows:
        payload = json.loads(row["payload"])
        if payload["reusable"] and _artifact_intact(store, row["artifact_hash"]):
            return {**payload["result"], "reused": True}
    return None


def current(store: ContinuityStore, run_id: str, receipt_id: str, *,
            worker_id: str | None = None, env: dict | None = None, check_id: str | None = None) -> dict:
    """Revalidate a reusable receipt at an admission/completion boundary.

    The eventual completion transaction must also validate its captured workspace
    generation and task revision; this read alone is not a completion operation.
    """
    row = store.db.execute("SELECT * FROM receipts WHERE receipt_id=? AND run_id=?", (receipt_id, run_id)).fetchone()
    if row is None:
        raise ValidationError("receipt is absent or belongs to another run")
    if check_id is not None and row["check_id"] != check_id:
        raise Conflict("receipt belongs to another check")
    payload = json.loads(row["payload"])
    with store.transaction() as tx:
        _run, recipe, version, cwd = _assignment(tx, run_id, row["check_id"], payload["recipe"], worker_id)
    child = dict(os.environ if env is None else env)
    source = fingerprints.source(cwd, recipe["inputs"], child)
    environment, _ = fingerprints.environment(cwd, recipe["argv"], recipe["environment"], child)
    reasons = []
    if not row["valid"] or not payload["reusable"]:
        reasons.append("receipt_not_reusable")
    if not source["complete"] or source["hash"] != row["inputs_hash"]:
        reasons.append("source_changed_or_unknown")
    if not environment["complete"] or environment["hash"] != row["environment_hash"]:
        reasons.append("environment_changed_or_unknown")
    if version != row["check_version"]:
        reasons.append("check_version_changed")
    if not _artifact_intact(store, row["artifact_hash"]):
        reasons.append("output_missing_or_corrupt")
    with store.transaction() as tx:
        _assignment(tx, run_id, row["check_id"], payload["recipe"], worker_id)
        if not tx.db.execute("SELECT valid FROM receipts WHERE receipt_id=?", (receipt_id,)).fetchone()[0]:
            if "receipt_not_reusable" not in reasons:
                reasons.append("receipt_not_reusable")
    return {"receipt_id": receipt_id, "current": not reasons, "reasons": reasons,
            "inputs_hash": source["hash"], "environment_hash": environment["hash"]}


def run_check(store: ContinuityStore, run_id: str, check_id: str, *, recipe: dict | None = None,
              request_id: str | None = None, worker_id: str | None = None, env: dict | None = None,
              allow_reuse: bool = True) -> dict:
    request_id = request_id or str(uuid.uuid4())
    _id(request_id)
    child_env = dict(os.environ if env is None else env)
    with store.transaction() as tx:
        existing = tx.db.execute("SELECT * FROM check_executions WHERE run_id=? AND request_id=?", (run_id, request_id)).fetchone()
        if existing:
            owner = tx.db.execute("SELECT worker_id FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if worker_id is not None and owner[0] != worker_id:
                raise Conflict("check caller does not own this run")
            if existing["check_id"] != check_id or (recipe is not None and existing["check_version"] != validate(recipe)):
                raise Conflict("check request ID belongs to a different command/version")
            if existing["status"] != "finished":
                raise Conflict("check request has an unresolved execution; inspect it before choosing a new request ID")
            result = json.loads(existing["payload"])["result"]
            receipt = tx.db.execute("SELECT valid FROM receipts WHERE receipt_id=?", (result["receipt_id"],)).fetchone()
            if result["valid"] and (receipt is None or not receipt[0]):
                result = {**result, "valid": False, "reusable": False,
                          "reasons": list(dict.fromkeys([*result["reasons"], "receipt_invalidated"]))}
            return result
        run, recipe, version, cwd = _assignment(tx, run_id, check_id, recipe, worker_id)
        execution_id = str(uuid.uuid4())
        output = store.root / "state" / "check-output" / execution_id
        tx._change()
        tx.db.execute("INSERT INTO check_executions VALUES(?,?,?,?,?,'running',?)", (execution_id, run_id, request_id, check_id, version,
            canonical({"started_at": time.time(), "owner_pid": os.getpid(), "output": str(output)})))
    # No ledger write lock is held while reading sources or executing the check.
    before = fingerprints.source(cwd, recipe["inputs"], child_env)
    before_env, argv = fingerprints.environment(cwd, recipe["argv"], recipe["environment"], child_env)
    if allow_reuse:
        reuse = reusable(store, run_id, check_id, version, before, before_env)
        if reuse is not None:
            with store.transaction() as tx:
                _assignment(tx, run_id, check_id, recipe, worker_id)
                tx._change()
                tx.db.execute("UPDATE check_executions SET status='finished',payload=? WHERE execution_id=?",
                              (canonical({"result": reuse}), execution_id))
            return reuse
    output.parent.mkdir(parents=True, exist_ok=True)
    observed = execute(argv, cwd, child_env, output, recipe["timeout_s"], recipe["max_output_bytes"])
    after = fingerprints.source(cwd, recipe["inputs"], child_env)
    after_env, _ = fingerprints.environment(cwd, recipe["argv"], recipe["environment"], child_env)
    reasons = []
    if observed["exit_code"] != 0:
        reasons.append("nonzero_exit")
    if observed["reason"]:
        reasons.append(observed["reason"])
    if not before["complete"] or not after["complete"]:
        reasons.append("incomplete_source_identity")
    if any(before[key] != after[key] for key in ("hash", "observation_hash")):
        reasons.append("source_changed_during_check")
    if any(before_env[key] != after_env[key] for key in ("hash", "observation_hash")):
        reasons.append("environment_changed_during_check")
    with store.transaction() as tx:
        try:
            _assignment(tx, run_id, check_id, recipe, worker_id)
        except (Conflict, ValidationError):
            reasons.append("assignment_changed_during_check")
        artifact = tx.put_artifact_file(output, owner_type="receipt", owner_id=execution_id,
                                        slot="output", max_bytes=recipe["max_output_bytes"])
        valid = not reasons
        result = {"receipt_id": execution_id, "check_id": check_id, "check_version": version,
                  "exit_code": observed["exit_code"], "valid": valid, "reused": False,
                  "reusable": valid and before_env["complete"] and after_env["complete"],
                  "reasons": reasons, "artifact_hash": artifact}
        event = tx.append_event(run_id, "checks", f"check:{execution_id}", "check_result", result)
        result["event_id"] = event["event_id"]
        payload = {"schema_version": 1, "result": result, "reusable": result["reusable"],
                   "argv": recipe["argv"], "resolved_argv": argv, "cwd": str(cwd), "recipe": recipe,
                   "source_before": before, "source_after": after, "environment_before": before_env,
                   "environment_after": after_env, "execution": observed, "completed_at": time.time()}
        tx.db.execute("INSERT INTO receipts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)", (execution_id, run_id, check_id, version,
            canonical(payload), before["hash"], before_env["hash"], before["hash"], after["hash"],
            observed["exit_code"], artifact, observed["duration"], int(valid)))
        # The check result's artifact remains readable through hx evidence.
        tx.db.execute("INSERT INTO artifact_refs VALUES(?,?,?,?)", ("event", event["event_id"], "output", artifact))
        cursor = tx.db.execute("SELECT revision FROM cursors WHERE run_id=? AND stream_id='checks'", (run_id,)).fetchone()
        tx.classify(run_id, "checks", expected_revision=cursor[0], through=event["seq"], dispositions={event["seq"]: "reduced"})
        tx.db.execute("UPDATE check_executions SET status='finished',payload=? WHERE execution_id=?", (canonical({"result": result}), execution_id))
        tx.enqueue("projection", f"check:{execution_id}", {"run_id": run_id, "receipt_id": execution_id, "event_id": event["event_id"]})
    output.unlink(missing_ok=True)
    return result


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx check")
    parser.add_argument("check_id")
    parser.add_argument("--run")
    parser.add_argument("--definition", help="explicit exploratory recipe; cannot replace an assigned acceptance check")
    parser.add_argument("--request", help="reuse the same ID when retrying an uncertain acknowledgement")
    parser.add_argument("--fresh", action="store_true")
    parser.add_argument("--receipt", help="revalidate a receipt against current inputs without executing a command")
    parser.add_argument("--root")
    args = parser.parse_args(argv)
    if args.receipt and (args.definition or args.request or args.fresh):
        raise ValidationError("--receipt cannot be combined with execution options")
    child = os.environ if env is None else env
    worker = child.get("HARNESS_ID")
    recipe = None
    if args.definition:
        with Path(args.definition).open("rb") as handle:
            raw = handle.read(MAX_RECIPE_BYTES + 1)
        if len(raw) > MAX_RECIPE_BYTES:
            raise ValidationError("check recipe exceeds 16 KiB")
        try:
            recipe = json.loads(raw)
        except ValueError as exc:
            raise ValidationError("check recipe must be a JSON object") from exc
    with ContinuityStore(root) as store:
        run_id = args.run
        if run_id is None:
            row = store.db.execute("SELECT run_id FROM runs WHERE worker_id=? AND ended_at IS NULL", (worker,)).fetchone()
            if row is None:
                raise ValidationError("check needs --run or an active HARNESS_ID assignment")
            run_id = row[0]
        if args.receipt:
            result = current(store, run_id, args.receipt, check_id=args.check_id, worker_id=worker, env=dict(child))
        else:
            result = run_check(store, run_id, args.check_id, recipe=recipe, request_id=args.request,
                               worker_id=worker, env=dict(child), allow_reuse=not args.fresh)
        print(canonical(result))
        return 0 if result["current" if args.receipt else "valid"] else 1
