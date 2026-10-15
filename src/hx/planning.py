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
    if not isinstance(body, dict) or body.keys() != {"schema_version", "plan_id", "expected_revision", "repository", "goal", "constraints", "tasks"} or type(body["schema_version"]) is not int or body["schema_version"] != 2:
        raise ValidationError("plan requires schema_version=2 and the current packet fields")
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
        fields = {"id", "expected_revision", "behavior", "acceptance", "workdir", "write_paths", "map_inputs", "outputs", "prerequisites", "resources", "check_policy"}
        if not isinstance(unit, dict) or unit.keys() != fields:
            raise ValidationError("each unit needs identity, behavior, acceptance, workdir, scope, map inputs, check policy, resources, outputs, and prerequisites")
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
        policy = unit["check_policy"]
        if not isinstance(policy, dict) or policy.keys() != {"reviewer", "required_checks"}:
            raise ValidationError("packet check policy needs independent reviewer and required check IDs")
        _id(policy["reviewer"])
        if policy["reviewer"] == "partner":
            raise ValidationError("packet policy reviewer must be an independent worker")
        if not isinstance(policy["required_checks"], list) or not 1 <= len(policy["required_checks"]) <= 16:
            raise ValidationError("packet policy requires 1–16 standard check IDs")
        for check_id in policy["required_checks"]:
            _id(check_id)
        if len(set(policy["required_checks"])) != len(policy["required_checks"]):
            raise ValidationError("policy check IDs must be unique")
        if not isinstance(unit["resources"], list) or len(unit["resources"]) > 32:
            raise ValidationError("packet requires at most 32 shared resources")
        resources = set()
        for resource in unit["resources"]:
            if not isinstance(resource, dict) or resource.keys() != {"id", "mode"} or resource["mode"] not in ("shared", "exclusive"):
                raise ValidationError("resource requires identity and shared or exclusive ownership")
            _id(resource["id"])
            if resource["id"] in resources:
                raise ValidationError("resource IDs must be unique")
            resources.add(resource["id"])
        if not isinstance(unit["outputs"], list) or not 1 <= len(unit["outputs"]) <= 32:
            raise ValidationError("every unit requires explicit versioned outputs")
        declared = {}
        for output in unit["outputs"]:
            if not isinstance(output, dict) or output.get("kind") not in {"source", "receipt", "contract"}:
                raise ValidationError("output kind must be source, receipt, or contract")
            fields = {"id", "version", "kind", "obligation"}
            if output["kind"] in {"source", "contract"}:
                fields.add("paths")
            _text(output.get("obligation"), "output obligation")
            if output.keys() != fields:
                raise ValidationError("output has missing or unknown fields")
            _id(output["id"])
            if output["id"] in declared or type(output["version"]) is not int or output["version"] < 1:
                raise ValidationError("output IDs must be unique and versions positive")
            if output["kind"] != "receipt":
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
                   "map_inputs": unit["map_inputs"], "checks": {}, "outputs": unit["outputs"],
                   "prerequisites": unit["prerequisites"]}
        payload.update(dependency_packet=True, resources=unit["resources"], check_policy=unit["check_policy"])
        if len(canonical(payload).encode()) > 16000:
            raise ValidationError("unit assignment exceeds 16 KiB; split its behavior before assignment")
        tasks[unit["id"]], outputs[unit["id"]], parents[unit["id"]] = payload, declared, set()
    for unit in body["tasks"]:
        seen = set()
        for dependency in unit["prerequisites"]:
            base_fields = {"task_id", "output_id", "version", "need"}
            early = isinstance(dependency, dict) and dependency.get("need") in ("contract", "substitutable")
            if early:
                base_fields |= {"provider_output", "provider_version", "stand_in"}
            if not isinstance(dependency, dict) or dependency.keys() != base_fields:
                raise ValidationError("prerequisite requires task_id, output_id, version, and need")
            _id(dependency["task_id"])
            _id(dependency["output_id"])
            output = outputs.get(dependency["task_id"], {}).get(dependency["output_id"])
            if output is None or type(dependency["version"]) is not int or output["version"] != dependency["version"]:
                raise ValidationError("prerequisite output is absent or has a different version")
            if dependency["need"] not in ("hard", "contract", "substitutable"):
                raise ValidationError("dependency need must be hard, contract, or substitutable")
            if early:
                provider = outputs.get(dependency["task_id"], {}).get(dependency["provider_output"])
                if output["kind"] != "contract" or not provider or provider["kind"] != "source" or type(dependency["provider_version"]) is not int or provider["version"] != dependency["provider_version"]:
                    raise ValidationError("early dependency needs an exact contract and real source provider output")
                if type(dependency["stand_in"]) is not bool or (dependency["need"] == "substitutable" and not dependency["stand_in"]):
                    raise ValidationError("substitutable dependency requires explicit stand-in permission")
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
    start_levels = levels
    early_dependencies = [{"consumer": unit["id"], **need} for unit in body["tasks"] for need in unit["prerequisites"] if need.get("need", "hard") != "hard"]
    if early_dependencies:
        # Ordering here is the publication graph; hard starts also require completion.
        pending = set(tasks)
        hard_parents = {name: {need["task_id"] for need in task["prerequisites"] if need.get("need", "hard") == "hard"} for name, task in tasks.items()}
        start_levels = []
        while pending:
            wave = sorted(name for name in pending if not hard_parents[name] & pending)
            start_levels.append(wave)
            pending.difference_update(wave)
    groups = []
    for level in start_levels:
        buckets = []
        for task in level:
            for bucket in buckets:
                if all(tasks[other]["workdir"] != tasks[task]["workdir"]
                       and not any(overlaps(a,b) for a in tasks[other]["write_scope"] for b in tasks[task]["write_scope"])
                       and not any(a["id"] == b["id"] and "exclusive" in {a["mode"],b["mode"]} for a in tasks[other].get("resources",[]) for b in tasks[task].get("resources",[])) for other in bucket):
                    bucket.append(task)
                    break
            else:
                buckets.append([task])
        groups.extend(buckets)
    # Interface-first: a consumer of a source output cannot start (even to
    # stub) until the producer finishes, unless the producer is split so an
    # interface/contract unit lands first. Warn, never refuse: the Partner
    # decides whether the coupling is worth an explicit split.
    size_warnings = [
        f"unit {unit['id']} spans {len(unit['write_paths'])} write paths; "
        "plan at module level, one behaviour per unit, so sign-off stays small"
        for unit in body["tasks"] if len(unit["write_paths"]) > 8]
    interface_warnings = []
    for unit in body["tasks"]:
        for dependency in unit["prerequisites"]:
            produced = outputs.get(dependency["task_id"], {}).get(dependency["output_id"])
            if produced and produced["kind"] == "source" and dependency.get("need", "hard") == "hard":
                interface_warnings.append(
                    f"consumer {unit['id']} builds on source output of {dependency['task_id']} "
                    "without an interface published first; split an interface/stub unit ahead "
                    "of it so consumers can be discovered, searched, and stubbed early")
    return tasks, {"valid": True, "plan_id": body["plan_id"], "dependency_levels": levels,
                   "potential_parallel_groups": groups, "hard_dependency_levels": start_levels, "early_start_dependencies": early_dependencies,
                   "waves": len(levels),
                   "level_widths": [len(level) for level in levels],
                   "max_parallelism": max((len(group) for group in groups), default=0),
                   "interface_warnings": interface_warnings,
                   "size_warnings": size_warnings,
                   "initial_units": levels[0], "note": "Dependency levels describe ordering; dispatch still requires current inputs, output materialization, and free leases. Max parallelism is bounded by worktree-disjoint groups, free leases, and host capacity at dispatch time."}


def validate(store, body):
    tasks, result = _definition(body)
    _prepare_code(store,tasks)
    with store.transaction() as tx:
        for task_id, payload in tasks.items():
            from .code_freshness import require
            require(store.root, payload,bootstrap=False)
            current = tx.task(task_id)
            if current and current["payload"] == payload:
                active = tx.db.execute("SELECT run_id FROM runs WHERE task_id=? AND ended_at IS NULL", (current["task_id"],)).fetchone()
                validate_task(tx, current, active_run=active[0] if active else None)
            else:
                validate_refs(tx, payload["map_inputs"], payload)
    return result


def apply(store, body):
    tasks, result = _definition(body)
    request_hash = digest(body)
    previous=store.db.execute("SELECT p.* FROM plans p JOIN plan_heads h USING(plan_id,revision) WHERE plan_id=?",(body['plan_id'],)).fetchone()
    if previous and previous['request_hash']==request_hash:
        return json.loads(previous['payload'])['result']
    _prepare_code(store,tasks)
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
            from .code_freshness import require
            require(store.root, payload,bootstrap=False)
            current = tx.task(task_id)
            if current and current["payload"].get("plan_id") != body["plan_id"]:
                raise Conflict("task ID belongs to a different assignment/plan")
            if (current["revision"] if current else 0) != unit["expected_revision"]:
                raise Conflict(f"task {task_id} changed since this plan was drafted")
            if current and current["payload"] == payload:
                active = tx.db.execute("SELECT run_id FROM runs WHERE task_id=? AND ended_at IS NULL", (current["task_id"],)).fetchone()
                validate_task(tx, current, active_run=active[0] if active else None)
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


def _prepare_code(store,tasks):
    """Cold source extraction belongs outside the fleet ledger writer."""
    from .code_freshness import require
    if store.db.in_transaction:
        raise Conflict('code scope preparation requires an unheld continuity writer')
    for payload in tasks.values():
        require(store.root,payload,bootstrap=True)


def assignment(store, task_id):
    with store.transaction() as tx:
        task = tx.task(task_id)
        if task is None or not task["payload"].get("dependency_packet") or "plan_id" not in task["payload"]:
            raise ValidationError("unknown planned unit")
        validate_task(tx, task)
        from .dependency_packets import effective_task, summary
        task = effective_task(tx, task)
        return {"task_id": task_id, "revision": task["revision"], **task["payload"], "readiness": summary(tx, task)}


def main(argv, root, *, env=None):
    parser = argparse.ArgumentParser(prog="hx plan")
    parser.add_argument("--root")
    subcommands = parser.add_subparsers(dest="command", required=True)
    for action in ("validate", "apply", "draft", "replan", "changes", "unit", "ready", "assign", "audit", "finish", "materialize", "stop", "bind", "binding", "approve-binding", "publish-contract", "acquire-contract", "verify-integration"):
        examples = {
            "bind": "hx plan bind RUN_ID --file worker-checks.json",
            "binding": "hx plan binding RUN_ID",
            "approve-binding": "hx plan approve-binding RUN_ID --binding SHA256",
            "publish-contract": "hx plan publish-contract RUN_ID --output api --file receipts.json",
            "acquire-contract": "hx plan acquire-contract consumer --producer provider --output api --out /tmp/api-v1",
            "verify-integration": "hx plan verify-integration consumer --integration-run RUN_ID --producer provider --output api",
        }
        command = subcommands.add_parser(action, epilog="example: " + examples[action] if action in examples else None)
        command.add_argument("--root", default=argparse.SUPPRESS)
        if action in {"unit", "assign", "materialize", "acquire-contract", "verify-integration"}:
            command.add_argument("task_id")
        if action in {"ready", "replan"}:
            command.add_argument("plan_id")
        if action == "assign":
            command.add_argument("--revision", required=True, type=int)
            command.add_argument("--worker", required=True)
        if action in {"audit", "finish", "stop", "bind", "binding", "approve-binding", "publish-contract"}:
            command.add_argument("run_id")
        if action in {"validate", "apply", "finish", "draft", "bind", "publish-contract"}:
            command.add_argument("--file", required=True)
        if action == "changes":
            command.add_argument("--plan", dest="plan_id")
            command.add_argument("--detail", action="store_true")
        if action in {"draft", "replan", "acquire-contract"}:
            command.add_argument("--out", required=action == "acquire-contract")
        if action == "draft":
            command.add_argument("--repo", required=True)
            command.add_argument("--snapshot", required=True)
        if action == "approve-binding":
            command.add_argument("--binding", required=True)
        if action == "publish-contract":
            command.add_argument("--output", required=True)
        if action in {"materialize", "verify-integration"}:
            command.add_argument("--integration-run", required=True)
            command.add_argument("--producer", required=True)
            command.add_argument("--output", required=True)
        if action == "acquire-contract":
            command.add_argument("--producer", required=True)
            command.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    who = caller(env)
    worker_action = args.command in {"audit", "finish", "materialize", "bind", "binding", "approve-binding", "publish-contract", "acquire-contract", "verify-integration"} and who not in {None, "partner"}
    if not worker_action:
        require_partner_caller("plan", env)
    from . import unit_execution
    with ContinuityStore(root) as store:
        if args.command in {"bind", "approve-binding", "publish-contract"} and not worker_action:
            raise Refused("packet check bindings, approval, and contract proof require a worker caller")
        if worker_action and args.command == "acquire-contract":
            if not store.db.execute("SELECT 1 FROM runs WHERE task_id=? AND worker_id=? AND ended_at IS NULL", (args.task_id,who)).fetchone():
                raise Refused("worker may acquire contracts only for its active assignment")
        elif worker_action and args.command not in {"approve-binding", "binding"}:
            run_id = args.integration_run if args.command in {"materialize", "verify-integration"} else args.run_id
            if not store.db.execute("SELECT 1 FROM runs WHERE run_id=? AND worker_id=?", (run_id, who)).fetchone():
                raise Refused("worker may audit, finish, or materialize only its own assignment")
        if args.command in {"bind", "binding", "approve-binding", "publish-contract", "acquire-contract", "verify-integration"}:
            from . import dependency_packets as packets
            if args.command == "bind":
                result = packets.bind_checks(store,args.run_id,appmap.read_json(Path(args.file),16000))
            elif args.command == "binding":
                result = packets.binding_view(store,args.run_id,who)
            elif args.command == "approve-binding":
                result = packets.approve_binding(store,args.run_id,args.binding,who)
            elif args.command == "publish-contract":
                result = packets.publish_contract(store,args.run_id,args.output,appmap.read_json(Path(args.file),16000),env=env)
            elif args.command == "acquire-contract":
                result = packets.acquire_contract(store,args.task_id,args.producer,args.output,args.out)
            else:
                result = packets.verify_integration(store,args.task_id,args.integration_run,args.producer,args.output)
        elif args.command in {'draft', 'replan', 'changes'}:
            from . import goal_templates
            if args.command == 'draft':
                result = goal_templates.draft(store, Path(args.repo).resolve(), args.snapshot, appmap.read_json(Path(args.file), 16000))
            elif args.command == 'replan':
                result = goal_templates.replan(store, args.plan_id)
            else:
                result = goal_templates.changes(store, plan_id=args.plan_id, details=args.detail)
        elif args.command == "unit":
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
            if 'plan' in body:
                if not body.get('ready'):
                    raise ValidationError('resolve the generated assignment unknowns before applying')
                body = body['plan']
            result = validate(store, body) if args.command == "validate" else apply(store, body)
    if args.command in {'draft', 'replan'} and args.out:
        from .store import atomic_write_text
        destination = Path(args.out).resolve()
        atomic_write_text(destination, canonical(result) + '\n')
        result = {key: result[key] for key in ('ready', 'unknowns', 'affected_tasks', 'first_action') if key in result}
        result['file'] = str(destination)
    print(canonical(result))
    return 0
