"""Partner-owned behavior units, exact output dependencies, and write ownership."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path, PurePosixPath

from . import appmap, checks
from .caller import caller, require_partner_caller
from .continuity_store import Conflict, ContinuityStore, canonical, digest
from .errors import Refused, ValidationError
from .map_dependencies import validate_refs, validate_task

PLAN_BYTES = 262144
IDENTITY = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _id(value):
    if not isinstance(value, str) or not IDENTITY.fullmatch(value):
        raise ValidationError("plan identifiers require 1–128 letters, digits, dots, underscores, or hyphens")


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"plan {label} must be a complete nonempty statement")


def _statements(value, label, *, required=False):
    if not isinstance(value, list) or len(value) > 32 or (required and not value):
        raise ValidationError(f"plan {label} must contain {'1' if required else '0'}–32 statements")
    for item in value:
        _text(item, label)


def write_scope(workdir, path):
    if not isinstance(path, str) or not path or "\\" in path or "\0" in path:
        raise ValidationError("write paths must be normalized repository-relative paths")
    relative = PurePosixPath(path)
    if relative.is_absolute() or str(relative) != path or path == "." or ".." in relative.parts or ".git" in relative.parts:
        raise ValidationError("write paths must name a file or reserved prefix inside the repository")
    resolved = (workdir / path).resolve()
    if not resolved.is_relative_to(workdir):
        raise ValidationError("write scope escapes the task worktree through a symlink")
    # Conservative case folding also serializes case aliases on insensitive hosts.
    return resolved.relative_to(workdir).as_posix().casefold()


def overlaps(a, b):
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def _definition(body):
    if not isinstance(body, dict) or body.keys() != {"schema_version", "plan_id", "expected_revision", "repository", "goal", "constraints", "tasks"} or type(body["schema_version"]) is not int or body["schema_version"] != 1:
        raise ValidationError("plan has missing or unknown fields/schema")
    if len(canonical(body).encode()) > PLAN_BYTES:
        raise ValidationError("plan exceeds 256 KiB; decompose the goal into smaller planning scopes")
    _id(body["plan_id"])
    if type(body["expected_revision"]) is not int or body["expected_revision"] < 0:
        raise ValidationError("plan expected revision must be nonnegative")
    _text(body["repository"], "repository identity")
    _text(body["goal"], "goal")
    _statements(body["constraints"], "constraints")
    if not isinstance(body["tasks"], list) or not 1 <= len(body["tasks"]) <= 32:
        raise ValidationError("plan requires 1–32 behavior units")
    tasks, parents, outputs = {}, {}, {}
    for unit in body["tasks"]:
        fields = {"id", "expected_revision", "behavior", "acceptance", "workdir", "write_paths", "map_inputs", "checks", "outputs", "prerequisites"}
        if not isinstance(unit, dict) or unit.keys() != fields:
            raise ValidationError("each unit needs identity, behavior, acceptance, workdir, scope, map inputs, checks, outputs, and prerequisites")
        _id(unit["id"])
        if unit["id"] in tasks or type(unit["expected_revision"]) is not int or unit["expected_revision"] < 0:
            raise ValidationError("unit IDs must be unique and expected revisions nonnegative")
        _text(unit["behavior"], "behavior")
        _statements(unit["acceptance"], "acceptance", required=True)
        if not isinstance(unit["workdir"], str) or not Path(unit["workdir"]).is_absolute():
            raise ValidationError("unit workdir must be an absolute repository worktree")
        workdir = Path(unit["workdir"]).resolve()
        if not workdir.is_dir() or appmap.manifest(workdir)["repo_id"] != body["repository"]:
            raise ValidationError("unit workdir does not belong to the plan repository")
        if not isinstance(unit["write_paths"], list) or len(unit["write_paths"]) > 64:
            raise ValidationError("unit write paths must contain at most 64 files/reserved prefixes")
        scope = sorted(set(write_scope(workdir, path) for path in unit["write_paths"]))
        if not isinstance(unit["checks"], dict) or not 1 <= len(unit["checks"]) <= 16:
            raise ValidationError("every behavior unit requires 1–16 explicit acceptance checks")
        for check_id, recipe in unit["checks"].items():
            checks.validate(recipe)
            if check_id != recipe["id"]:
                raise ValidationError("acceptance check key must match its recipe ID")
        if not isinstance(unit["outputs"], list) or not 1 <= len(unit["outputs"]) <= 32:
            raise ValidationError("every unit requires explicit versioned outputs")
        declared = {}
        for output in unit["outputs"]:
            if not isinstance(output, dict) or output.get("kind") not in {"source", "receipt"}:
                raise ValidationError("output kind must be source or receipt")
            fields = {"id", "version", "kind", "paths" if output["kind"] == "source" else "check_id"}
            if output.keys() != fields:
                raise ValidationError("output has missing or unknown fields")
            _id(output["id"])
            if output["id"] in declared or type(output["version"]) is not int or output["version"] < 1:
                raise ValidationError("output IDs must be unique and versions positive")
            if output["kind"] == "receipt":
                if output["check_id"] not in unit["checks"]:
                    raise ValidationError("receipt output must identify an assigned acceptance check")
            else:
                if not isinstance(output["paths"], list) or not 1 <= len(output["paths"]) <= 64:
                    raise ValidationError("source output requires 1–64 exact file paths")
                for path in output["paths"]:
                    canonical_path = write_scope(workdir, path)
                    if not any(canonical_path == owner or canonical_path.startswith(owner + "/") for owner in scope):
                        raise ValidationError("source output lies outside the unit's declared write scope")
            declared[output["id"]] = output
        if not isinstance(unit["prerequisites"], list) or len(unit["prerequisites"]) > 64:
            raise ValidationError("unit prerequisites must contain at most 64 exact outputs")
        payload = {"plan_id": body["plan_id"], "repository": body["repository"], "goal": unit["behavior"],
                   "parent_goal": body["goal"], "constraints": body["constraints"], "acceptance": unit["acceptance"],
                   "workdir": str(workdir), "write_paths": unit["write_paths"], "write_scope": scope,
                   "map_inputs": unit["map_inputs"], "checks": unit["checks"], "outputs": unit["outputs"],
                   "prerequisites": unit["prerequisites"]}
        if len(canonical(payload).encode()) > 16000:
            raise ValidationError("unit assignment exceeds 16 KiB; split its behavior before assignment")
        tasks[unit["id"]], outputs[unit["id"]], parents[unit["id"]] = payload, declared, set()
    for unit in body["tasks"]:
        seen = set()
        for dependency in unit["prerequisites"]:
            if not isinstance(dependency, dict) or dependency.keys() != {"task_id", "output_id", "version"}:
                raise ValidationError("prerequisite requires task_id, output_id, version")
            _id(dependency["task_id"])
            _id(dependency["output_id"])
            output = outputs.get(dependency["task_id"], {}).get(dependency["output_id"])
            if output is None or type(dependency["version"]) is not int or output["version"] != dependency["version"]:
                raise ValidationError("prerequisite output is absent or has a different version")
            identity = (dependency["task_id"], dependency["output_id"])
            if identity in seen:
                raise ValidationError("prerequisite output is repeated")
            seen.add(identity)
            parents[unit["id"]].add(dependency["task_id"])
    remaining, ancestors, levels = set(tasks), {}, []
    while remaining:
        ready = sorted(task for task in remaining if not (parents[task] & remaining))
        if not ready:
            raise ValidationError("task prerequisites contain a cycle")
        levels.append(ready)
        for task in ready:
            ancestors[task] = set(parents[task]).union(*(ancestors[parent] for parent in parents[task]))
        remaining.difference_update(ready)
    names = sorted(tasks)
    for index, left in enumerate(names):
        for right in names[index + 1:]:
            if left in ancestors[right] or right in ancestors[left]:
                continue
            if any(overlaps(a, b) for a in tasks[left]["write_scope"] for b in tasks[right]["write_scope"]):
                raise ValidationError(f"independent units {left} and {right} have overlapping write scopes; establish ownership or an explicit prerequisite")
    groups = []
    for level in levels:
        buckets = []
        for task in level:
            for bucket in buckets:
                if all(tasks[other]["workdir"] != tasks[task]["workdir"] for other in bucket):
                    bucket.append(task)
                    break
            else:
                buckets.append([task])
        groups.extend(buckets)
    return tasks, {"valid": True, "plan_id": body["plan_id"], "dependency_levels": levels,
                   "potential_parallel_groups": groups,
                   "initial_units": levels[0], "note": "Dependency levels describe ordering; dispatch still requires current inputs, output materialization, and free leases."}


def validate(store, body):
    tasks, result = _definition(body)
    with store.transaction() as tx:
        for payload in tasks.values():
            validate_refs(tx, payload["map_inputs"], payload)
    return result


def apply(store, body):
    tasks, result = _definition(body)
    request_hash = digest(body)
    with store.transaction() as tx:
        previous = tx.db.execute("SELECT p.* FROM plans p JOIN plan_heads h USING(plan_id,revision) WHERE plan_id=?", (body["plan_id"],)).fetchone()
        if previous and previous["request_hash"] == request_hash:
            return json.loads(previous["payload"])["result"]
        actual = previous["revision"] if previous else 0
        if actual != body["expected_revision"]:
            raise Conflict(f"plan expected revision {body['expected_revision']}, found {actual}")
        if previous:
            old_ids = {row[0] for row in tx.db.execute("SELECT task_id FROM plan_units WHERE plan_id=? AND plan_revision=?", (body["plan_id"], actual))}
            if old_ids - tasks.keys():
                raise Conflict("plan revision cannot silently discard existing units; preserve their obligations")
        revision, task_versions = actual + 1, {}
        for unit in body["tasks"]:
            task_id, payload = unit["id"], tasks[unit["id"]]
            current = tx.task(task_id)
            if current and current["payload"].get("plan_id") != body["plan_id"]:
                raise Conflict("task ID belongs to a different assignment/plan")
            if (current["revision"] if current else 0) != unit["expected_revision"]:
                raise Conflict(f"task {task_id} changed since this plan was drafted")
            if current and current["payload"] == payload:
                validate_refs(tx, payload["map_inputs"], payload)
                task_versions[task_id] = current["revision"]
            else:
                task_versions[task_id] = tx.put_task(task_id, payload, expected_revision=unit["expected_revision"])
        result = {**result, "revision": revision, "task_versions": task_versions}
        tx._change()
        tx.db.execute("INSERT INTO plans VALUES(?,?,?,?)", (body["plan_id"], revision, request_hash, canonical({"definition": body, "result": result})))
        tx.db.execute("INSERT INTO plan_heads VALUES(?,?) ON CONFLICT(plan_id) DO UPDATE SET revision=excluded.revision", (body["plan_id"], revision))
        for task_id, task_revision in task_versions.items():
            tx.db.execute("INSERT INTO plan_units VALUES(?,?,?,?)", (body["plan_id"], revision, task_id, task_revision))
            for dependency in tasks[task_id]["prerequisites"]:
                tx.db.execute("INSERT OR IGNORE INTO plan_prerequisites VALUES(?,?,?,?,?)",
                    (task_id, task_revision, dependency["task_id"], dependency["output_id"], dependency["version"]))
        tx.enqueue("plan_changed", f"plan:{body['plan_id']}:{revision}", result)
    return result


def assignment(store, task_id):
    with store.transaction() as tx:
        task = tx.task(task_id)
        if task is None or "plan_id" not in task["payload"]:
            raise ValidationError("unknown planned unit")
        validate_task(tx, task)
        return {"task_id": task_id, "revision": task["revision"], **task["payload"]}


def main(argv, root, *, env=None):
    parser = argparse.ArgumentParser(prog="hx plan")
    parser.add_argument("--root")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for action in ("validate", "apply", "unit", "ready", "assign", "audit", "finish", "materialize", "stop"):
        command = subcommands.add_parser(action)
        command.add_argument("--root", default=argparse.SUPPRESS)
        if action in {"unit", "assign", "materialize"}:
            command.add_argument("task_id")
        if action == "ready":
            command.add_argument("plan_id")
        if action == "assign":
            command.add_argument("--revision", required=True, type=int)
            command.add_argument("--worker", required=True)
        if action in {"audit", "finish", "stop"}:
            command.add_argument("run_id")
        if action in {"validate", "apply", "finish"}:
            command.add_argument("--file", required=True)
        if action == "materialize":
            command.add_argument("--integration-run", required=True)
            command.add_argument("--producer", required=True)
            command.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    who = caller(env)
    worker_action = args.command in {"audit", "finish", "materialize"} and who not in {None, "partner"}
    if not worker_action:
        require_partner_caller("plan", env)
    from . import unit_execution
    with ContinuityStore(root) as store:
        if worker_action:
            run_id = args.integration_run if args.command == "materialize" else args.run_id
            if not store.db.execute("SELECT 1 FROM runs WHERE run_id=? AND worker_id=?", (run_id, who)).fetchone():
                raise Refused("worker may audit, finish, or materialize only its own assignment")
        if args.command == "unit":
            result = assignment(store, args.task_id)
        elif args.command == "ready":
            result = unit_execution.ready(store, args.plan_id)
        elif args.command == "assign":
            result = unit_execution.assign(store, args.task_id, args.revision, args.worker)
        elif args.command == "audit":
            result = unit_execution.audit(store, args.run_id)
        elif args.command == "stop":
            result = unit_execution.stop(store, args.run_id)
        elif args.command == "materialize":
            result = unit_execution.materialize(store, args.task_id, args.integration_run, args.producer, args.output)
        elif args.command == "finish":
            result = unit_execution.complete(store, args.run_id, appmap.read_json(Path(args.file), 16000), env=env)
        else:
            body = appmap.read_json(Path(args.file), PLAN_BYTES)
            result = validate(store, body) if args.command == "validate" else apply(store, body)
    print(canonical(result))
    return 0
