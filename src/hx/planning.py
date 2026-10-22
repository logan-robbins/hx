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

PLAN_BYTES = 1048576
MAX_UNITS = 256
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


def _declared(declared, dependency):
    """The producer output a prerequisite names, at its exact version and the kind its need requires."""
    output = declared.get(dependency["output_id"])
    if output is None or type(dependency["version"]) is not int or output["version"] != dependency["version"]:
        raise ValidationError("prerequisite output is absent or has a different version")
    if dependency["need"] != "hard":
        provider = declared.get(dependency["provider_output"])
        if output["kind"] != "contract" or not provider or provider["kind"] != "source" or type(dependency["provider_version"]) is not int or provider["version"] != dependency["provider_version"]:
            raise ValidationError("early dependency needs an exact contract and real source provider output")
    return output


def _definition(body):
    """The plan's own units, their declared outputs, and its prerequisites on other applied plans."""
    if not isinstance(body, dict) or body.keys() != {"plan_id", "expected_revision", "repository", "goal", "constraints", "tasks"}:
        raise ValidationError("plan requires exactly plan_id, expected_revision, repository, goal, constraints and tasks")
    if len(canonical(body).encode()) > PLAN_BYTES:
        raise ValidationError("plan exceeds 1 MiB; decompose the goal into smaller planning scopes")
    _id(body["plan_id"])
    if type(body["expected_revision"]) is not int or body["expected_revision"] < 0:
        raise ValidationError("plan expected revision must be nonnegative")
    _text(body["repository"], "repository identity")
    _text(body["goal"], "goal")
    _statements(body["constraints"], "constraints")
    if not isinstance(body["tasks"], list) or not 1 <= len(body["tasks"]) <= MAX_UNITS:
        raise ValidationError(f"plan requires 1–{MAX_UNITS} behavior units")
    tasks, outputs, repositories = {}, {}, {}
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
        if workdir not in repositories:
            repositories[workdir] = workdir.is_dir() and appmap.manifest(workdir)["repo_id"]
        if repositories[workdir] != body["repository"]:
            raise ValidationError("unit workdir does not belong to the plan repository")
        if not isinstance(unit["write_paths"], list) or len(unit["write_paths"]) > 64:
            raise ValidationError("unit write paths must contain at most 64 files/reserved prefixes")
        scope = sorted(set(write_scope(workdir, path) for path in unit["write_paths"]))
        policy = unit["check_policy"]
        if not isinstance(policy, dict) or policy.keys() != {"reviewer", "required_checks", "role"}:
            raise ValidationError("packet check policy needs independent reviewer, required check IDs, and the executing seat role")
        _id(policy["reviewer"])
        _id(policy["role"])
        if "partner" in (policy["reviewer"], policy["role"]):
            raise ValidationError("packet policy reviewer must be an independent worker and its role a worker seat role")
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
        tasks[unit["id"]], outputs[unit["id"]] = payload, declared
    externals = []
    for unit in body["tasks"]:
        seen = set()
        for dependency in unit["prerequisites"]:
            base_fields = {"task_id", "output_id", "version", "need"}
            early = isinstance(dependency, dict) and dependency.get("need") in ("contract", "substitutable")
            if early:
                base_fields |= {"provider_output", "provider_version", "stand_in"}
            if not isinstance(dependency, dict) or dependency.keys() - {"plan_id"} != base_fields:
                raise ValidationError("prerequisite requires task_id, output_id, version, and need; plan_id names another applied plan")
            _id(dependency["task_id"])
            _id(dependency["output_id"])
            if dependency["need"] not in ("hard", "contract", "substitutable"):
                raise ValidationError("dependency need must be hard, contract, or substitutable")
            if early:
                _id(dependency["provider_output"])
                if type(dependency["stand_in"]) is not bool or (dependency["need"] == "substitutable" and not dependency["stand_in"]):
                    raise ValidationError("substitutable dependency requires explicit stand-in permission")
            if "plan_id" in dependency:
                _id(dependency["plan_id"])
                if dependency["plan_id"] == body["plan_id"] or dependency["task_id"] in tasks:
                    raise ValidationError("plan_id names another applied plan; a prerequisite inside this plan omits it")
                externals.append(dependency)
            else:
                _declared(outputs.get(dependency["task_id"], {}), dependency)
            identity = (dependency["task_id"], dependency["output_id"])
            if identity in seen:
                raise ValidationError("prerequisite output is repeated")
            seen.add(identity)
    return tasks, outputs, externals


def _levels(parents):
    """Kahn layering by indegree; None when the graph has a cycle."""
    children, waiting = {name: [] for name in parents}, {}
    for name, needs in parents.items():
        waiting[name] = len(needs)
        for parent in needs:
            children[parent].append(name)
    level, levels, placed = sorted(name for name, count in waiting.items() if not count), [], 0
    while level:
        levels.append(level)
        placed += len(level)
        following = []
        for name in level:
            for child in children[name]:
                waiting[child] -= 1
                if not waiting[child]:
                    following.append(child)
        level = sorted(following)
    return levels if placed == len(parents) else None


def _prefixes(path):
    """The path and every reserved prefix that owns it."""
    parts = path.split("/")
    return ["/".join(parts[:end]) for end in range(1, len(parts) + 1)]


def _fits(bucket, unit):
    if unit["workdir"] in bucket["workdirs"]:
        return False
    for path in unit["write_scope"]:
        if path in bucket["prefixes"] or any(prefix in bucket["paths"] for prefix in _prefixes(path)):
            return False
    return not any(resource["id"] in bucket["exclusive"] or (resource["mode"] == "exclusive" and resource["id"] in bucket["shared"])
                   for resource in unit["resources"])


def _groups(levels, units):
    """Greedy worktree-, scope-, and resource-disjoint buckets per start level."""
    groups = []
    for level in levels:
        buckets, crowded = [], {}
        for name in level:
            unit = units[name]
            # Every bucket already holds this worktree: no scan can place it.
            fits = (bucket for bucket in buckets if _fits(bucket, unit)) if crowded.get(unit["workdir"], 0) < len(buckets) else iter(())
            bucket = next(fits, None)
            if bucket is None:
                bucket = {"units": [], "workdirs": set(), "paths": set(), "prefixes": set(), "shared": set(), "exclusive": set()}
                buckets.append(bucket)
            bucket["units"].append(name)
            bucket["workdirs"].add(unit["workdir"])
            crowded[unit["workdir"]] = crowded.get(unit["workdir"], 0) + 1
            for path in unit["write_scope"]:
                bucket["paths"].add(path)
                bucket["prefixes"].update(_prefixes(path)[:-1])
            for resource in unit["resources"]:
                bucket[resource["mode"]].add(resource["id"])
        groups.extend(bucket["units"] for bucket in buckets)
    return groups


def _union(tx, body, tasks, outputs, externals):
    """Levels, groups, and write ownership over every applied plan in this root.

    The plan's own units replace its previous head; every other plan contributes
    its head units at their current task revisions. The report lists only this
    plan's units, ordered by their fleet level, plus a fleet summary.
    """
    others = {}
    for row in tx.db.execute("""SELECT u.plan_id,u.task_id,json_extract(t.payload,'$.workdir') AS workdir,
            json_extract(t.payload,'$.write_scope') AS scope,json_extract(t.payload,'$.resources') AS resources,
            json_extract(t.payload,'$.outputs') AS outputs,json_extract(t.payload,'$.prerequisites') AS prerequisites
        FROM plan_heads h JOIN plan_units u ON u.plan_id=h.plan_id AND u.plan_revision=h.revision
        JOIN task_heads th ON th.task_id=u.task_id JOIN tasks t ON t.task_id=th.task_id AND t.revision=th.revision
        WHERE h.plan_id<>?""", (body["plan_id"],)):
        others[row["task_id"]] = {"plan_id": row["plan_id"], "workdir": row["workdir"],
            **{field: json.loads(row[column] or "[]") for field, column in (("write_scope", "scope"), ("resources", "resources"),
                                                                           ("outputs", "outputs"), ("prerequisites", "prerequisites"))}}
    for task_id in sorted(tasks.keys() & others.keys()):
        raise Conflict(f"task ID belongs to a different assignment/plan: {task_id} is a unit of {others[task_id]['plan_id']}")
    declared = dict(outputs)
    for dependency in externals:
        producer = others.get(dependency["task_id"])
        if producer is None or producer["plan_id"] != dependency["plan_id"]:
            raise ValidationError(f"prerequisite {dependency['task_id']} is not a unit of applied plan {dependency['plan_id']}")
        declared[dependency["task_id"]] = {output["id"]: output for output in producer["outputs"]}
        _declared(declared[dependency["task_id"]], dependency)
    cross_plan_warnings = []
    for name, other in sorted(others.items()):
        for need in other["prerequisites"]:
            if need["task_id"] in tasks:
                try:
                    _declared(outputs[need["task_id"]], need)
                except ValidationError:
                    cross_plan_warnings.append(
                        f"unit {name} of plan {other['plan_id']} needs {need['task_id']}/{need['output_id']} "
                        f"v{need['version']}, which this plan no longer declares; replan {other['plan_id']}")
    units = {**others, **{name: {"plan_id": body["plan_id"], **payload} for name, payload in tasks.items()}}
    parents = {name: {need["task_id"] for need in unit["prerequisites"] if need["task_id"] in units} for name, unit in units.items()}
    levels = _levels(parents)
    if levels is None:
        raise ValidationError("task prerequisites contain a cycle")
    # Ordering here is the publication graph; hard starts also require completion.
    start_levels = _levels({name: {need["task_id"] for need in unit["prerequisites"]
                                   if need.get("need", "hard") == "hard" and need["task_id"] in units} for name, unit in units.items()})
    # Ancestor sets as bitmasks in topological order: reachability is one AND.
    bit, ancestors = {}, {}
    for name in (name for level in levels for name in level):
        bit[name] = 1 << len(bit)
        mask = 0
        for parent in parents[name]:
            mask |= ancestors[parent] | bit[parent]
        ancestors[name] = mask
    owners, clashes = {}, set()
    for name, payload in tasks.items():
        for path in payload["write_scope"]:
            owners.setdefault(path, []).append(name)
    for name, payload in tasks.items():
        for path in payload["write_scope"]:
            for prefix in _prefixes(path):
                for other in owners.get(prefix, ()):
                    if other != name and not (ancestors[name] & bit[other] or ancestors[other] & bit[name]):
                        clashes.add(tuple(sorted((name, other))))
    if clashes:
        left, right = min(clashes)
        raise ValidationError(f"independent units {left} and {right} have overlapping write scopes; establish ownership or an explicit prerequisite")
    groups = _groups(start_levels, units)

    def own(rows):
        return [kept for kept in ([name for name in row if name in tasks] for row in rows) if kept]

    dependency_levels, plan_groups = own(levels), own(groups)
    early_dependencies = [{"consumer": unit["id"], **need} for unit in body["tasks"] for need in unit["prerequisites"] if need["need"] != "hard"]
    size_warnings = [
        f"unit {unit['id']} spans {len(unit['write_paths'])} write paths; "
        "plan at module level, one behaviour per unit, so sign-off stays small"
        for unit in body["tasks"] if len(unit["write_paths"]) > 8]
    # Interface-first: a consumer of a source output cannot start (even to
    # stub) until the producer finishes, unless the producer is split so an
    # interface/contract unit lands first. Warn, never refuse: the Partner
    # decides whether the coupling is worth an explicit split.
    interface_warnings = []
    for unit in body["tasks"]:
        for dependency in unit["prerequisites"]:
            produced = declared.get(dependency["task_id"], {}).get(dependency["output_id"])
            if produced and produced["kind"] == "source" and dependency["need"] == "hard":
                interface_warnings.append(
                    f"consumer {unit['id']} builds on source output of {dependency['task_id']} "
                    "without an interface published first; split an interface/stub unit ahead "
                    "of it so consumers can be discovered, searched, and stubbed early")
    return {"valid": True, "plan_id": body["plan_id"], "dependency_levels": dependency_levels,
            "potential_parallel_groups": plan_groups, "hard_dependency_levels": own(start_levels),
            "early_start_dependencies": early_dependencies,
            "waves": len(dependency_levels),
            "level_widths": [len(level) for level in dependency_levels],
            "max_parallelism": max((len(group) for group in plan_groups), default=0),
            "fleet": {"plans": len({unit["plan_id"] for unit in units.values()}), "units": len(units), "waves": len(levels),
                      "max_parallelism": max((len(group) for group in groups), default=0)},
            "interface_warnings": interface_warnings,
            "cross_plan_warnings": cross_plan_warnings,
            "size_warnings": size_warnings,
            "initial_units": sorted(name for name, payload in tasks.items() if not payload["prerequisites"]),
            "note": "Levels and groups are computed over every applied plan in this root; this plan's units are listed in fleet order. Dependency levels describe ordering; dispatch still requires current inputs, output materialization, and free leases. Max parallelism is bounded by worktree-disjoint groups, free leases, and host capacity at dispatch time."}


def validate(store, body):
    tasks, outputs, externals = _definition(body)
    _prepare_code(store, tasks)
    with store.transaction() as tx:
        result = _union(tx, body, tasks, outputs, externals)
        _fresh(store.root, tasks, bootstrap=False)
        for task_id, payload in tasks.items():
            current = tx.task(task_id)
            if current and current["payload"] == payload:
                active = tx.db.execute("SELECT run_id FROM runs WHERE task_id=? AND ended_at IS NULL", (current["task_id"],)).fetchone()
                validate_task(tx, current, active_run=active[0] if active else None)
            else:
                validate_refs(tx, payload["map_inputs"], payload)
    return result


def apply(store, body):
    tasks, outputs, externals = _definition(body)
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
        result = _union(tx, body, tasks, outputs, externals)
        _fresh(store.root, tasks, bootstrap=False)
        revision, task_versions = actual + 1, {}
        for unit in body["tasks"]:
            task_id, payload = unit["id"], tasks[unit["id"]]
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
    # A new producer revision supersedes its earlier publications and their graph stubs.
    from .code_stubs import prune
    prune(store.root, {task_id: version for task_id, version in task_versions.items() if version > 1})
    return result


def _fresh(root, tasks, *, bootstrap):
    """One freshness proof per worktree covers every unit's write scope in it.

    A read-only unit (no write paths) depends on the whole repository graph.
    """
    from .code_freshness import require
    scopes = {}
    for payload in tasks.values():
        scopes.setdefault(payload["workdir"], set()).update(payload["write_paths"] or ["."])
    for workdir, paths in sorted(scopes.items()):
        require(root, {"workdir": workdir, "write_paths": ["."] if "." in paths else sorted(paths)}, bootstrap=bootstrap)


def _prepare_code(store,tasks):
    """Cold source extraction belongs outside the fleet ledger writer."""
    if store.db.in_transaction:
        raise Conflict('code scope preparation requires an unheld continuity writer')
    _fresh(store.root, tasks, bootstrap=True)


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
    for action in ("validate", "apply", "draft", "replan", "changes", "unit", "ready", "run", "assign", "audit", "finish", "materialize", "stop", "bind", "binding", "approve-binding", "publish-contract", "acquire-contract", "verify-integration", "partition"):
        examples = {
            "bind": "hx plan bind RUN_ID --file worker-checks.json",
            "binding": "hx plan binding RUN_ID",
            "approve-binding": "hx plan approve-binding RUN_ID --binding SHA256",
            "publish-contract": "hx plan publish-contract RUN_ID --output api --file receipts.json",
            "acquire-contract": "hx plan acquire-contract consumer --producer provider --output api --out /tmp/api-v1",
            "verify-integration": "hx plan verify-integration consumer --integration-run RUN_ID --producer provider --output api",
            "run": "hx plan run PLAN_ID --dry-run",
            "partition": "hx plan partition --repo WT --scope src/billing src/ledger --out /tmp/plan.json",
        }
        command = subcommands.add_parser(action, epilog="example: " + examples[action] if action in examples else None)
        command.add_argument("--root", default=argparse.SUPPRESS)
        if action in {"unit", "assign", "materialize", "acquire-contract", "verify-integration"}:
            command.add_argument("task_id")
        if action in {"ready", "replan", "run"}:
            command.add_argument("plan_id")
        if action == "run":
            command.add_argument("--dry-run", action="store_true", help="report what one pass would admit and launch")
            command.add_argument("--max", type=int, help="admit at most N units in this pass")
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
        if action in {"draft", "replan", "acquire-contract", "partition"}:
            command.add_argument("--out", required=action == "acquire-contract")
        if action in {"draft", "partition"}:
            command.add_argument("--repo", required=True)
        if action == "draft":
            command.add_argument("--snapshot", required=True)
        if action == "partition":
            command.add_argument("--scope", nargs="+", help="lane paths; default: each top-level directory")
            command.add_argument("--plan-id", default="partition", help="plan_id of the --out draft")
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
        elif args.command == "partition":
            from . import goal_templates, plan_partition
            result = plan_partition.report(store.root, Path(args.repo), args.scope, plan_partition.merge_rules(store.root))
            if args.out:
                _id(args.plan_id)
                destination = Path(args.out).resolve()
                if destination.is_relative_to(result["checkout"]):
                    raise ValidationError("partition --out writes outside the product checkout")
                from .store import atomic_write_text
                atomic_write_text(destination, canonical(plan_partition.draft(result, goal_templates.check_policy(store), args.plan_id)) + "\n")
                result["file"] = str(destination)
        elif args.command == "unit":
            result = assignment(store, args.task_id)
        elif args.command == "ready":
            result = unit_execution.ready(store, args.plan_id)
        elif args.command == "run":
            if args.max is not None and args.max < 1:
                raise ValidationError("--max admits at least one unit")
            from . import plan_dispatch
            result = plan_dispatch.run(store, args.plan_id, env=env, dry_run=args.dry_run, maximum=args.max)
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
