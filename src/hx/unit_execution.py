"""Verified unit admission, repository-wide leases, and exact prerequisite outputs.

This is the deterministic execution boundary. Native process launch remains the
controller's responsibility; starting a ledger run cannot bypass these guards.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from . import appmap, checks, fingerprints
from .continuity_store import Conflict, canonical, digest
from .errors import ValidationError
from .map_dependencies import validate_task


def _git(workdir, *args):
    # All callers ask for metadata or named paths, never source bodies. Bound
    # even anomalous Git output without accumulating a whole repository listing.
    with subprocess.Popen(["git", "--no-optional-locks", "--literal-pathspecs", "-C", str(workdir), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        output = process.stdout.read(262145)
        if len(output) > 262144:
            process.kill()
            process.wait()
            raise Conflict("unit Git metadata exceeds 256 KiB; narrow this work unit")
        if process.wait() != 0:
            raise Conflict("unit requires a valid Git worktree and reachable revisions")
    return output


def workspace(payload, *, clean=False):
    root = Path(payload["workdir"])
    if appmap.manifest(root)["repo_id"] != payload["repository"]:
        raise Conflict("unit repository identity changed")
    if Path(os.fsdecode(_git(root, "rev-parse", "--show-toplevel")).strip()).resolve() != root:
        raise Conflict("unit workdir must be the Git worktree root")
    if clean and _git(root, "status", "--porcelain=v1", "--untracked-files=all"):
        raise Conflict("unit requires a clean, committed worktree")
    return {"commit": _git(root, "rev-parse", "HEAD").decode().strip(),
            "tree": _git(root, "rev-parse", "HEAD^{tree}").decode().strip(),
            "git_dir": os.fsdecode(_git(root, "rev-parse", "--absolute-git-dir")).strip(),
            "branch": _git(root, "symbolic-ref", "--quiet", "HEAD").decode().strip()}


def _task(tx, task_id):
    task = tx.task(task_id)
    if task is None or "plan_id" not in task["payload"]:
        raise ValidationError("unknown planned unit")
    validate_task(tx, task)
    return task


def _run(tx, run_id):
    row = tx.db.execute("SELECT r.*,u.payload AS admission FROM runs r JOIN unit_runs u USING(run_id) WHERE run_id=?", (run_id,)).fetchone()
    if row is None or row["ended_at"] is not None:
        raise Conflict("operation requires an active planned run")
    task = _task(tx, row["task_id"])
    if task["revision"] != row["task_revision"]:
        raise Conflict("planned assignment changed")
    return dict(row), task, json.loads(row["admission"])


def guard_revision(tx, previous, payload):
    if tx.db.execute("SELECT 1 FROM runs WHERE task_id=? AND ended_at IS NULL", (previous["task_id"],)).fetchone():
        raise Conflict("stop the active unit before revising its frozen assignment")
    if previous["payload"].get("plan_id") != payload.get("plan_id"):
        raise Conflict("a planned task cannot change plan identity")
    if tx.db.execute("""WITH RECURSIVE consumers(id) AS (
        SELECT ? UNION SELECT p.task_id FROM plan_prerequisites p JOIN task_heads h ON h.task_id=p.task_id AND h.revision=p.task_revision JOIN consumers c ON p.producer=c.id
        ) SELECT 1 FROM runs r JOIN consumers c ON c.id=r.task_id WHERE r.ended_at IS NULL LIMIT 1""", (previous["task_id"],)).fetchone():
        raise Conflict("stop active dependent units before revising their prerequisite contract")
    # A version is the delivery contract, including its acceptance evidence.
    # Revised completed units must publish new output versions, even if source
    # happens to remain identical after the new acceptance criteria are checked.
    published = {row["output_id"]: row["version"] for row in tx.db.execute(
        "SELECT output_id,max(version) AS version FROM unit_outputs WHERE task_id=? GROUP BY output_id", (previous["task_id"],))}
    for output in payload.get("outputs", []):
        if output["id"] in published and output["version"] <= published[output["id"]]:
            raise Conflict("revising a completed unit requires new output versions")


def _output(tx, dependency):
    producer = _task(tx, dependency["task_id"])
    row = tx.db.execute("SELECT payload,version FROM unit_outputs WHERE task_id=? AND task_revision=? AND output_id=?",
        (producer["task_id"], producer["revision"], dependency["output_id"])).fetchone()
    if row is None or row["version"] != dependency["version"]:
        raise Conflict(f"prerequisite {dependency['task_id']}/{dependency['output_id']}@{dependency['version']} has no current completion proof")
    proof = json.loads(row["payload"])
    for receipt_id in proof["checks"].values():
        receipt = tx.db.execute("SELECT valid,artifact_hash FROM receipts WHERE receipt_id=?", (receipt_id,)).fetchone()
        if receipt is None or not receipt["valid"] or not checks._artifact_intact(tx.store, receipt["artifact_hash"]):
            raise Conflict("prerequisite acceptance evidence is invalid or unavailable")
    return proof


def _entries(root, commit, paths):
    entries = {path: None for path in paths}
    raw = _git(root, "ls-tree", "-z", commit, "--", *paths)
    for line in raw.split(b"\0"):
        if not line:
            continue
        header, name = line.split(b"\t", 1)
        mode, kind, oid = header.decode().split()
        path = os.fsdecode(name)
        if path not in entries or kind != "blob":
            raise Conflict("source outputs must name exact regular files or symlinks, not directories/submodules")
        entries[path] = {"mode": mode, "object": oid}
    return entries


def _prerequisites(tx, task, state):
    proofs = {}
    for dependency in task["payload"]["prerequisites"]:
        proof = _output(tx, dependency)
        identity = dependency["task_id"] + "/" + dependency["output_id"]
        if proof["kind"] == "source":
            row = tx.db.execute("SELECT payload,integration_run FROM unit_materializations WHERE task_id=? AND task_revision=? AND producer=? AND output_id=?",
                (task["task_id"], task["revision"], dependency["task_id"], dependency["output_id"])).fetchone()
            if row is None or json.loads(row[0])["output_hash"] != digest(proof):
                raise Conflict(f"prerequisite {identity} requires an integration materialization receipt")
            integrated = tx.db.execute("SELECT c.payload FROM unit_completions c JOIN task_heads h ON h.task_id=c.task_id AND h.revision=c.task_revision WHERE c.run_id=?", (row["integration_run"],)).fetchone()
            if integrated is None or json.loads(integrated[0])["commit"] != json.loads(row["payload"])["commit"]:
                raise Conflict("materialization requires successful integration completion at the attested commit")
            for receipt_id in json.loads(integrated[0])["checks"].values():
                receipt = tx.db.execute("SELECT valid,artifact_hash FROM receipts WHERE receipt_id=?", (receipt_id,)).fetchone()
                if receipt is None or not receipt["valid"] or not checks._artifact_intact(tx.store, receipt["artifact_hash"]):
                    raise Conflict("integration acceptance evidence is invalid or unavailable")
            if _entries(task["payload"]["workdir"], state["commit"], list(proof["files"])) != proof["files"]:
                raise Conflict(f"prerequisite {identity} is not present at the pinned output version")
        proofs[identity] = digest(proof)
    return proofs


def _scope(payload):
    from .planning import write_scope
    scopes = sorted(set(write_scope(Path(payload["workdir"]), path) for path in payload["write_paths"]))
    if scopes != payload["write_scope"]:
        raise Conflict("write paths no longer resolve to their frozen lease scopes")
    return scopes


def guard_legacy_admission(tx, task):
    """A legacy run cannot enter a worktree currently held by a planned run."""
    root = task["payload"].get("workdir")
    if not root:
        return
    for row in tx.db.execute("SELECT u.workdir FROM unit_runs u JOIN runs r USING(run_id) WHERE r.ended_at IS NULL"):
        try:
            if Path(root).samefile(row[0]):
                raise Conflict("worktree is owned by an active planned assignment")
        except OSError:
            pass


def admit(tx, task):
    """Called inside start_run's writer transaction before any run is inserted."""
    from .planning import overlaps
    payload = task["payload"]
    if not tx.db.execute("SELECT 1 FROM plan_units u JOIN plan_heads h ON h.plan_id=u.plan_id AND h.revision=u.plan_revision WHERE u.task_id=? AND u.task_revision=?", (task["task_id"], task["revision"])).fetchone():
        raise Conflict("current plan does not include this task revision")
    if tx.db.execute("SELECT 1 FROM unit_completions WHERE task_id=? AND task_revision=?", (task["task_id"], task["revision"])).fetchone():
        raise Conflict("unit revision is already completed")
    if tx.db.execute("""SELECT 1 FROM runs r JOIN tasks t ON t.task_id=r.task_id AND t.revision=r.task_revision
        WHERE r.ended_at IS NULL AND (r.task_id=? OR json_extract(t.payload,'$.workdir')=?)""",
        (task["task_id"], payload["workdir"])).fetchone():
        raise Conflict("unit or worktree already has an active assignment")
    # Real filesystem identity catches case aliases as well as symlinked paths.
    for owner in tx.db.execute("SELECT json_extract(t.payload,'$.workdir') FROM runs r JOIN tasks t ON t.task_id=r.task_id AND t.revision=r.task_revision WHERE r.ended_at IS NULL"):
        if owner[0]:
            try:
                if Path(payload["workdir"]).samefile(owner[0]):
                    raise Conflict("worktree already has an active assignment through another path")
            except OSError:
                pass
    scopes = _scope(payload)
    for row in tx.db.execute("SELECT canonical_path FROM leases l JOIN runs r USING(run_id) WHERE repository=? AND r.ended_at IS NULL", (payload["repository"],)):
        if any(overlaps(path, row[0]) for path in scopes):
            raise Conflict(f"write lease overlaps active owner: {row[0]}")
    state = workspace(payload, clean=True)
    return {"workspace": state, "prerequisites": _prerequisites(tx, task, state)}


def bind(tx, task, run_id, admission):
    payload = task["payload"]
    tx.db.execute("INSERT INTO unit_runs VALUES(?,?,?)", (run_id, payload["workdir"], canonical(admission)))
    tx.db.execute("DELETE FROM leases WHERE repository=? AND run_id IN (SELECT run_id FROM runs WHERE ended_at IS NOT NULL)", (payload["repository"],))
    for path in payload["write_scope"]:
        tx.db.execute("INSERT INTO leases VALUES(?,?,?,?)", (payload["repository"], path, run_id,
            canonical({"task_id": task["task_id"], "revision": task["revision"]})))
    tx.enqueue("unit_assigned", f"unit:{run_id}", {"run_id": run_id, "task_id": task["task_id"], "revision": task["revision"]})


def ready(store, plan_id):
    result = []
    with store.transaction() as tx:
        rows = tx.db.execute("SELECT u.task_id,u.task_revision FROM plan_units u JOIN plan_heads h ON h.plan_id=u.plan_id AND h.revision=u.plan_revision WHERE u.plan_id=? ORDER BY u.task_id", (plan_id,)).fetchall()
        if not rows:
            raise ValidationError("unknown plan")
        for row in rows:
            try:
                task = _task(tx, row["task_id"])
                if task["revision"] != row["task_revision"]:
                    raise Conflict("plan no longer names the current task revision")
                if tx.db.execute("SELECT 1 FROM unit_completions WHERE task_id=? AND task_revision=?", tuple(row)).fetchone():
                    result.append({"task_id": row["task_id"], "status": "completed"})
                    continue
                admit(tx, task)
                result.append({"task_id": row["task_id"], "status": "ready"})
            except (Conflict, ValidationError) as exc:
                result.append({"task_id": row["task_id"], "status": "blocked", "reason": str(exc)})
    return {"plan_id": plan_id, "units": result, "note": "Readiness is a snapshot; assignment atomically rechecks ownership and prerequisites."}


def assign(store, task_id, revision, worker_id):
    with store.transaction() as tx:
        _task(tx, task_id)
        run_id = tx.start_run(task_id, revision, worker_id)
        task = tx.task(task_id)
        return {"run_id": run_id, "task_id": task_id, "revision": revision, "worker_id": worker_id, "assignment": task["payload"]}


def _changed(payload, admission, state):
    root = payload["workdir"]
    if any(state[name] != admission["workspace"][name] for name in ("branch", "git_dir")):
        raise Conflict("unit changed its assigned branch/worktree identity")
    _git(root, "merge-base", "--is-ancestor", admission["workspace"]["commit"], state["commit"])
    paths = set(filter(None, (os.fsdecode(path) for path in _git(root, "diff", "--no-ext-diff", "--no-renames", "--name-only", "-z", admission["workspace"]["commit"], "--").split(b"\0"))))
    paths.update(filter(None, (os.fsdecode(path) for path in _git(root, "ls-files", "--others", "--exclude-standard", "-z").split(b"\0"))))
    if len(paths) > 1024:
        raise Conflict("changed path set exceeds the unit scope check bound")
    return sorted(paths)


def audit(store, run_id):
    """Persist a pause on unexpected writes; keep leases until the run stops."""
    from .planning import write_scope
    with store.transaction() as tx:
        run, task, admission = _run(tx, run_id)
        payload = task["payload"]
        try:
            scopes = _scope(payload)
            state = workspace(payload)
            changed = _changed(payload, admission, state)
            unexpected = []
            for path in changed:
                candidate = write_scope(Path(payload["workdir"]), path)
                if not any(candidate == scope or candidate.startswith(scope + "/") for scope in scopes):
                    unexpected.append(path)
            reason = "writes exceed the frozen assignment scope" if unexpected else None
        except (Conflict, ValidationError) as exc:
            changed, unexpected, reason = [], [], str(exc)
        if reason:
            tx._change()
            tx.db.execute("UPDATE runs SET phase='paused' WHERE run_id=?", (run_id,))
            tx.enqueue("unit_scope_violation", f"scope:{run_id}:{digest([reason,unexpected])}", {"run_id": run_id, "reason": reason, "paths": unexpected})
        return {"run_id": run_id, "within_scope": reason is None and run["phase"] != "paused", "changed_paths": changed,
                "unexpected_paths": unexpected, "reason": reason or ("run remains paused; stop and reconcile its assignment" if run["phase"] == "paused" else None)}


def complete(store, run_id, receipt_ids, *, env=None):
    child = dict(os.environ if env is None else env)
    previous = store.db.execute("SELECT payload FROM unit_completions WHERE run_id=?", (run_id,)).fetchone()
    if previous:
        result = json.loads(previous[0])
        if result["checks"] != receipt_ids:
            raise Conflict("completion retry changed its acceptance receipts")
        return result
    scope = audit(store, run_id)
    if not scope["within_scope"]:
        raise Conflict(scope["reason"])
    with store.transaction() as tx:
        _, task, admission = _run(tx, run_id)
        before = workspace(task["payload"], clean=True)
    if not isinstance(receipt_ids, dict) or receipt_ids.keys() != task["payload"]["checks"].keys() or any(not isinstance(value, str) or not value for value in receipt_ids.values()):
        raise ValidationError("completion must name exactly one receipt for every assigned check")
    for check_id, receipt_id in receipt_ids.items():
        result = checks.current(store, run_id, receipt_id, check_id=check_id, env=child)
        if not result["current"]:
            raise Conflict("acceptance receipt is no longer current: " + ", ".join(result["reasons"]))
    with store.transaction() as tx:
        run, task, admission = _run(tx, run_id)
        payload = task["payload"]
        if run["phase"] == "paused":
            raise Conflict("paused unit cannot complete")
        state = workspace(payload, clean=True)
        if state != before:
            raise Conflict("worktree changed during completion verification")
        # Recheck exact inputs at the final boundary, including declared ignored
        # fixtures and external version files that a clean Git tree cannot prove.
        for check_id, receipt_id in receipt_ids.items():
            row = tx.db.execute("SELECT * FROM receipts WHERE receipt_id=? AND run_id=? AND check_id=?", (receipt_id, run_id, check_id)).fetchone()
            recipe = payload["checks"][check_id]
            cwd = (Path(payload["workdir"]) / recipe["cwd"]).resolve()
            source = fingerprints.source(cwd, recipe["inputs"], child)
            environment, _ = fingerprints.environment(cwd, recipe["argv"], recipe["environment"], child)
            if row is None or not row["valid"] or not source["complete"] or not environment["complete"] or source["hash"] != row["inputs_hash"] or environment["hash"] != row["environment_hash"]:
                raise Conflict("acceptance inputs changed at completion")
        if _prerequisites(tx, task, admission["workspace"]) != admission["prerequisites"]:
            raise Conflict("prerequisite proof changed since admission")
        _scope(payload)
        changed = _changed(payload, admission, state)
        declared_paths = {path for output in payload["outputs"] if output["kind"] == "source" for path in output["paths"]}
        if set(changed) - declared_paths:
            raise Conflict("committed changes must all belong to explicit source outputs")
        proofs = {}
        for output in payload["outputs"]:
            proof = {**output, "producer": task["task_id"], "task_revision": task["revision"], "run_id": run_id, "checks": receipt_ids}
            if output["kind"] == "source":
                files = _entries(payload["workdir"], state["commit"], output["paths"])
                if any(entry is None and path not in changed for path, entry in files.items()):
                    raise Conflict("absent output path is not a verified deletion")
                proof.update(commit=state["commit"], tree=state["tree"], files=files)
            else:
                proof["artifact_hash"] = tx.db.execute("SELECT artifact_hash FROM receipts WHERE receipt_id=?", (receipt_ids[output["check_id"]],)).fetchone()[0]
            proofs[output["id"]] = proof
        if workspace(payload, clean=True) != state:
            raise Conflict("source changed before output publication")
        result = {"task_id": task["task_id"], "revision": task["revision"], "run_id": run_id, "checks": receipt_ids,
                  "commit": state["commit"], "outputs": {name: digest(proof) for name, proof in proofs.items()}}
        tx._change()
        tx.db.execute("INSERT INTO unit_completions VALUES(?,?,?,?)", (task["task_id"], task["revision"], run_id, canonical(result)))
        for output_id, proof in proofs.items():
            tx.db.execute("INSERT INTO unit_outputs VALUES(?,?,?,?,?)", (task["task_id"], task["revision"], output_id, proof["version"], canonical(proof)))
        tx.finish_run(run_id, "completed")
        tx.enqueue("unit_completed", f"complete:{run_id}", result)
    return result


def materialize(store, task_id, integration_run, producer, output_id):
    """Attest outputs installed by an explicit integration run, without Git merges.

The integration worker installs/commits outputs using its normal tools and owns
the destination worktree while doing so. Consumers remain blocked until it stops.
"""
    if not audit(store, integration_run)["within_scope"]:
        raise Conflict("integration assignment has writes outside its scope")
    with store.transaction() as tx:
        run, integration, _ = _run(tx, integration_run)
        task = _task(tx, task_id)
        if integration["task_id"] == task_id or integration["payload"]["workdir"] != task["payload"]["workdir"] or integration["payload"]["repository"] != task["payload"]["repository"]:
            raise Conflict("materialization requires a separate integration unit in the consumer worktree")
        integration_proofs = {output["id"]: output["version"] for output in integration["payload"]["outputs"] if output["kind"] == "receipt"}
        if not any(item["task_id"] == integration["task_id"] and integration_proofs.get(item["output_id"]) == item["version"] for item in task["payload"]["prerequisites"]):
            raise Conflict("consumer must explicitly depend on the integration unit's acceptance output")
        dependency = next((item for item in task["payload"]["prerequisites"] if item["task_id"] == producer and item["output_id"] == output_id), None)
        if dependency is None:
            raise ValidationError("output is not an assigned consumer prerequisite")
        proof = _output(tx, dependency)
        if proof["kind"] != "source":
            raise ValidationError("receipt artifacts are already materialized in the shared ledger")
        state = workspace(task["payload"], clean=True)
        if _entries(task["payload"]["workdir"], state["commit"], list(proof["files"])) != proof["files"]:
            raise Conflict("integration has not installed the exact prerequisite files")
        result = {"task_id": task_id, "revision": task["revision"], "producer": producer, "output_id": output_id,
                  "output_hash": digest(proof), "commit": state["commit"], "integration_run": integration_run}
        tx._change()
        tx.db.execute("INSERT INTO unit_materializations VALUES(?,?,?,?,?,?) ON CONFLICT(task_id,task_revision,producer,output_id) DO UPDATE SET integration_run=excluded.integration_run,payload=excluded.payload",
            (task_id, task["revision"], producer, output_id, integration_run, canonical(result)))
        tx.enqueue("unit_materialized", f"materialize:{digest(result)}", result)
        return result


def stop(store, run_id, outcome="stopped"):
    if outcome not in {"stopped", "failed"}:
        raise ValidationError("stop outcome must be stopped or failed")
    with store.transaction() as tx:
        if not tx.db.execute("SELECT 1 FROM unit_runs WHERE run_id=?", (run_id,)).fetchone():
            raise ValidationError("unknown planned run")
        tx.finish_run(run_id, outcome)
        tx.enqueue("unit_stopped", f"stop:{run_id}", {"run_id": run_id, "outcome": outcome})
    return {"run_id": run_id, "outcome": outcome}
