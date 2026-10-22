"""Dispatch ready plan units onto idle configured seats under fleet budgets.

`tick` runs in the application loop after reconciliation when config/hx.json
`dispatch.auto` is true; `hx plan run PLAN` runs one pass for one plan. Admission is
`unit_execution.assign` and launch is `native_controller.advance`, unchanged: the
executor chooses what to admit and when, and records every choice under the launch
key sha256(plan, task, revision, head), so a repeated or reconciled pass never
launches one unit revision at one head twice.

A pass:
1. Reap finished launches; resume the ones a stopped process left.
2. List candidates in one ledger read: incomplete head units of every applied plan
   with no active run, whose exact prerequisite outputs or contracts exist.
3. Order them downstream-first: the `downstream_roles` rank, then fleet depth.
4. Give each an idle seat whose role is the unit's `check_policy.role` and which is
   not its reviewer, within the root quota, the writer budget (a unit without write
   paths is read-only and exempt) and merge backpressure (no new engineering unit
   for a lane with more than 2 × `delivery.json max_batch` landed-but-unvalidated
   batches; a unit whose role is in `downstream_roles` is not engineering).
5. Ask the host pool for box sessions, restore tokens and the growth breaker.
6. In parallel: the stale-base check, the role's precheck, then `assign`.
7. Launch each admitted run as a loop job, spawn only; the loop's launch
   reconciliation submits the prompt once the native session is idle.

A precheck exit, a stale base or an admission refusal defers the unit for
`retry_seconds` without consuming a failure; a producer completing or publishing a
contract clears its consumers' deferrals (`wake_consumers`). Nothing replays an
uncertain native launch. `hx fleet pause` stops every pass until `hx fleet resume`.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

from . import appmap, host_pool, native_processes
from .continuity_store import Conflict, ContinuityStore, canonical
from .errors import HxError, ValidationError

SCHEMA = """
CREATE TABLE IF NOT EXISTS dispatch_launches (
 launch_key TEXT PRIMARY KEY, plan_id TEXT NOT NULL, task_id TEXT NOT NULL, task_revision INTEGER NOT NULL,
 head TEXT NOT NULL, worker_id TEXT NOT NULL, run_id TEXT, status TEXT NOT NULL, payload TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS dispatch_launches_task ON dispatch_launches(task_id,task_revision);
CREATE TABLE IF NOT EXISTS dispatch_ready (
 task_id TEXT NOT NULL, task_revision INTEGER NOT NULL, at REAL NOT NULL, PRIMARY KEY(task_id,task_revision))
"""

INTEGERS = {"sessions": (1, 4096), "quota": (1, 4096), "writers": (0, 4096), "per_tick": (1, 1024),
            "prepare_workers": (1, 64), "retry_seconds": (0, 86400), "max_behind": (0, 1_000_000),
            "precheck_timeout": (1, 3600)}
DEFAULTS = {"auto": False, "sessions": 60, "quota": 60, "writers": 60, "per_tick": 8, "prepare_workers": 8,
            "retry_seconds": 60, "max_behind": 20, "base_ref": None, "precheck": {}, "precheck_timeout": 120,
            "downstream_roles": [], "restore": {"concurrency": 8, "per_second": 2},
            "growth": {"max_descendants": 1500, "per_second": 40}}
#: Launch-key states that own the key: a pass never selects the key again.
HELD = {"reserved", "assigned", "launched", "launch_failed"}


def initialize(db):
    for statement in SCHEMA.split(";"):
        if statement.strip():
            db.execute(statement)


def _number(value, label, low, high, *, integer=True):
    if type(value) is bool or not isinstance(value, int if integer else (int, float)) or not low <= value <= high:
        raise ValidationError(f"config/hx.json dispatch.{label} must be {'an integer' if integer else 'a number'} from {low} to {high}")
    return value


def settings(root):
    """The `dispatch` section of config/hx.json over the mechanism defaults."""
    try:
        body = json.loads((Path(root) / "config" / "hx.json").read_text())
    except FileNotFoundError:
        body = {}
    except (OSError, ValueError) as exc:
        raise ValidationError("config/hx.json is unreadable: " + str(exc)) from exc
    section = body.get("dispatch", {}) if isinstance(body, dict) else {}
    if not isinstance(section, dict) or section.keys() - DEFAULTS.keys():
        raise ValidationError("config/hx.json dispatch has unknown fields")
    result = {**DEFAULTS, **section}
    if type(result["auto"]) is not bool:
        raise ValidationError("config/hx.json dispatch.auto must be true or false")
    for key, (low, high) in INTEGERS.items():
        _number(result[key], key, low, high)
    if result["base_ref"] is not None and (not isinstance(result["base_ref"], str) or not result["base_ref"] or result["base_ref"].startswith("-")):
        raise ValidationError("config/hx.json dispatch.base_ref must name a revision")
    roles = result["downstream_roles"]
    if not isinstance(roles, list) or len(set(roles)) != len(roles) or any(not isinstance(role, str) or not role for role in roles):
        raise ValidationError("config/hx.json dispatch.downstream_roles must list unique seat roles")
    prechecks = result["precheck"]
    if not isinstance(prechecks, dict) or any(not isinstance(argv, list) or not 1 <= len(argv) <= 64
                                              or any(not isinstance(item, str) or not item for item in argv) for argv in prechecks.values()):
        raise ValidationError("config/hx.json dispatch.precheck maps a seat role to an argv list")
    for name, fields in (("restore", {"concurrency": (1, 4096, True), "per_second": (0.001, 10000, False)}),
                         ("growth", {"max_descendants": (1, 1_000_000, True), "per_second": (0.001, 1_000_000, False)})):
        value = result[name]
        if not isinstance(value, dict) or value.keys() - fields.keys():
            raise ValidationError(f"config/hx.json dispatch.{name} has unknown fields")
        result[name] = value = {**DEFAULTS[name], **value}
        for key, (low, high, integer) in fields.items():
            _number(value[key], f"{name}.{key}", low, high, integer=integer)
    from .change_delivery import _policy
    result["max_batch"] = _policy(root).get("max_batch")
    return result


def paused(root):
    return (Path(root) / "run" / "dispatch-paused").exists()


def launch_key(plan_id, task_id, revision, head):
    return hashlib.sha256(canonical([plan_id, task_id, revision, head]).encode()).hexdigest()


def _seats(root):
    """Configured worker seats, id → role: config/<id>/harness.json whose id is its folder."""
    seats = {}
    for path in sorted((Path(root) / "config").glob("*/harness.json")):
        try:
            body = appmap.read_json(path, 16000)
        except (OSError, ValueError, HxError):
            continue
        if isinstance(body, dict) and body.get("id") == path.parent.name != "partner" and isinstance(body.get("role"), str):
            seats[body["id"]] = body["role"]
    return seats


def _git(workdir, *args):
    result = subprocess.run(["git", "-C", str(workdir), *args], capture_output=True, text=True, timeout=60, check=False)
    if result.returncode:
        raise Conflict(f"git {' '.join(args[:2])} failed in {workdir}: {result.stderr.strip()[:200]}")
    return result.stdout.strip()


CANDIDATES = """SELECT h.plan_id,u.task_id,u.task_revision,t.payload FROM plan_heads h
 JOIN plan_units u ON u.plan_id=h.plan_id AND u.plan_revision=h.revision
 JOIN task_heads th ON th.task_id=u.task_id AND th.revision=u.task_revision
 JOIN tasks t ON t.task_id=u.task_id AND t.revision=u.task_revision
 WHERE (? IS NULL OR h.plan_id=?)
 AND NOT EXISTS (SELECT 1 FROM unit_completions c WHERE c.task_id=u.task_id AND c.task_revision=u.task_revision)
 AND NOT EXISTS (SELECT 1 FROM runs r WHERE r.task_id=u.task_id AND r.ended_at IS NULL)
 AND NOT EXISTS (SELECT 1 FROM json_each(t.payload,'$.prerequisites') p WHERE CASE json_extract(p.value,'$.need')
   WHEN 'hard' THEN NOT EXISTS (SELECT 1 FROM task_heads ph JOIN unit_outputs o ON o.task_id=ph.task_id AND o.task_revision=ph.revision
     WHERE ph.task_id=json_extract(p.value,'$.task_id') AND o.output_id=json_extract(p.value,'$.output_id')
     AND o.version=json_extract(p.value,'$.version'))
   ELSE NOT EXISTS (SELECT 1 FROM unit_contracts k WHERE k.task_id=json_extract(p.value,'$.task_id')
     AND k.output_id=json_extract(p.value,'$.output_id') AND k.version=json_extract(p.value,'$.version')) END)
 ORDER BY h.plan_id,u.task_id"""

ACTIVE = """SELECT r.worker_id,json_extract(t.payload,'$.workdir') AS workdir,
 coalesce(json_array_length(t.payload,'$.write_paths'),1) AS writes FROM runs r
 JOIN tasks t ON t.task_id=r.task_id AND t.revision=r.task_revision WHERE r.ended_at IS NULL"""


def _depths(tx):
    """Fleet depth: the longest prerequisite chain under each current unit."""
    producers = {}
    for row in tx.db.execute("""SELECT p.task_id,p.producer FROM plan_prerequisites p
            JOIN task_heads h ON h.task_id=p.task_id AND h.revision=p.task_revision"""):
        producers.setdefault(row["task_id"], set()).add(row["producer"])
    depth = {}
    for start in producers:
        stack = [start]
        while stack:
            name = stack[-1]
            if name in depth:
                stack.pop()
                continue
            waiting = [parent for parent in producers.get(name, ()) if parent not in depth and parent not in stack]
            if waiting:
                stack.extend(waiting)
                continue
            depth[name] = 1 + max((depth.get(parent, 0) for parent in producers.get(name, ())), default=-1)
            stack.pop()
    return depth


def _descendants(identities):
    """Sampled native process count under this root's live supervisors."""
    live = {identity["pid"] for identity in identities if identity and native_processes.current(identity)}
    if not live:
        return 0
    census = subprocess.run(["/bin/ps", "-axo", "pid=,ppid="], capture_output=True, timeout=10, check=False)
    children = {}
    for line in census.stdout.splitlines():
        fields = line.split()
        if len(fields) == 2 and fields[0].isdigit() and fields[1].isdigit():
            children.setdefault(int(fields[1]), []).append(int(fields[0]))
    seen, frontier = set(live), list(live)
    while frontier:
        for child in children.get(frontier.pop(), ()):
            if child not in seen:
                seen.add(child)
                frontier.append(child)
    return len(seen) - len(live)


def _owner():
    return native_processes.probe(os.getpid())["identity"]


def _update(store, key, status, **fields):
    with store.transaction() as tx:
        row = tx.db.execute("SELECT payload FROM dispatch_launches WHERE launch_key=?", (key,)).fetchone()
        payload = {**json.loads(row["payload"]), **{name: value for name, value in fields.items() if name != "run_id"}}
        tx._change()
        tx.db.execute("UPDATE dispatch_launches SET status=?,payload=?,run_id=coalesce(?,run_id) WHERE launch_key=?",
                      (status, canonical(payload), fields.get("run_id"), key))


def _launch(store, key, env):
    """Spawn one admitted run's native session; the loop submits its prompt later."""
    from .native_controller import advance
    row = store.db.execute("SELECT * FROM dispatch_launches WHERE launch_key=?", (key,)).fetchone()
    try:
        result = advance(store, row["worker_id"], row["run_id"], key, env=env, submit=False)
        status, fields = "launched", {"launch_status": result["status"]}
    except Exception as exc:  # The job boundary: a failed launch is recorded, never retried.
        status, fields = "launch_failed", {"reason": f"{type(exc).__name__}: {exc}"[:400]}
    finally:
        host_pool.release(key)
    _update(store, key, status, launched_at=time.time(), **fields)
    return {"launch_key": key, "status": status, **fields}


def _resume(store, jobs, env):
    """Launches a stopped process left: reserved without a run, or admitted without a launch.

    Only rows whose owning process is gone are touched; an uncertain native launch is
    recorded as failed, never replayed.
    """
    from .native_launch import _row
    jobs.finished("dispatch")
    running, mine, resumed = {key[1] for key in jobs.active("dispatch")}, _owner(), []
    for row in store.db.execute("SELECT * FROM dispatch_launches WHERE status IN ('reserved','assigned')").fetchall():
        owner = json.loads(row["payload"]).get("owner")
        # A pass is synchronous: this process's own reservation without a job is stale.
        if row["launch_key"] in running or (owner and owner != mine and native_processes.current(owner)):
            continue
        run_id = row["run_id"] or (store.db.execute(
            "SELECT run_id FROM runs WHERE task_id=? AND task_revision=? AND worker_id=? AND ended_at IS NULL",
            (row["task_id"], row["task_revision"], row["worker_id"])).fetchone() or [None])[0]
        if run_id is None:
            with store.transaction() as tx:
                tx._change()
                tx.db.execute("DELETE FROM dispatch_launches WHERE launch_key=? AND status='reserved'", (row["launch_key"],))
            continue
        native = _row(store, run_id)
        if native is not None and native["status"] != "prepared":
            failed = native["status"].endswith(("_uncertain", "_failed"))
            _update(store, row["launch_key"], "launch_failed" if failed else "launched", run_id=run_id,
                    launch_status=native["status"], **({"reason": "native launch is " + native["status"]} if failed else {}))
            continue
        _update(store, row["launch_key"], "assigned", run_id=run_id, owner=_owner())
        jobs.submit(("dispatch", row["launch_key"]), lambda attached, key=row["launch_key"]: _launch(attached, key, env), store)
        resumed.append(row["task_id"])
    return resumed


def _admit(store, item, config, env):
    """Stale base, precheck, then admission; any refusal defers without a failure."""
    from . import unit_execution
    payload = item["payload"]
    try:
        if config["base_ref"]:
            behind = int(_git(payload["workdir"], "rev-list", "--count", "HEAD.." + config["base_ref"]))
            if behind > config["max_behind"]:
                return {"defer": f"stale base: {behind} commits behind {config['base_ref']} (max {config['max_behind']})"}
        argv = config["precheck"].get(item["role"])
        if argv:
            unit = {"plan_id": item["plan_id"], "task_id": item["task_id"], "revision": item["revision"],
                    "workdir": payload["workdir"], "write_paths": payload["write_paths"], "role": item["role"],
                    "worker_id": item["seat"]}
            child = {**(os.environ if env is None else env), "HARNESS_ROOT": str(store.root)}
            child.pop("HARNESS_ID", None)
            checked = subprocess.run(argv, input=canonical(unit), text=True, capture_output=True, cwd=payload["workdir"],
                                     env=child, timeout=config["precheck_timeout"], check=False)
            if checked.returncode:
                return {"defer": f"precheck exited {checked.returncode}: {checked.stderr.strip()[-300:]}"}
        assigned = unit_execution.assign(store, item["task_id"], item["revision"], item["seat"])
        return {"run_id": assigned["run_id"]}
    except (HxError, OSError, subprocess.SubprocessError, ValueError) as exc:
        return {"defer": str(exc)[:400]}


def _admit_all(store, chosen, config, env):
    if len(chosen) < 2 or config["prepare_workers"] < 2:
        return [_admit(store, item, config, env) for item in chosen]

    def attached(item):
        with ContinuityStore.attach(store.root) as own:
            return _admit(own, item, config, env)

    with ThreadPoolExecutor(min(len(chosen), config["prepare_workers"]), thread_name_prefix="hx-dispatch") as pool:
        return list(pool.map(attached, chosen))


def dispatch(store, *, config, env=None, jobs, plan_id=None, dry_run=False, maximum=None):
    """One executor pass; see the module docstring for its steps."""
    root, now = store.root, time.time()
    seats = _seats(root)
    with store.snapshot() as tx:
        candidates = tx.db.execute(CANDIDATES, (plan_id, plan_id)).fetchall()
        active = tx.db.execute(ACTIVE).fetchall()
        depth = _depths(tx) if candidates else {}
        seen = {(row["task_id"], row["task_revision"]): row["at"] for row in tx.db.execute("SELECT * FROM dispatch_ready")}
        held = {}
        for row in tx.db.execute("SELECT * FROM dispatch_launches WHERE task_id IN (SELECT value FROM json_each(?))",
                                 (canonical([row["task_id"] for row in candidates]),)):
            held[row["launch_key"]] = row
        pending = {}
        if config["max_batch"]:
            for row in tx.db.execute("SELECT repository,count(*) AS landed FROM delivery_batches WHERE status='pending' GROUP BY repository"):
                pending[row["repository"]] = row["landed"]
        supervisors = [json.loads(row[0]) for row in tx.db.execute(
            """SELECT json_extract(n.payload,'$.supervisor_process') FROM native_launches n JOIN runs r USING(run_id)
               WHERE r.ended_at IS NULL AND n.status<>'archived' AND json_extract(n.payload,'$.supervisor_process') IS NOT NULL""")]
    ranks = {role: index for index, role in enumerate(config["downstream_roles"])}
    units = []
    for row in candidates:
        payload = json.loads(row["payload"])
        policy = payload.get("check_policy") or {}
        units.append({"plan_id": row["plan_id"], "task_id": row["task_id"], "revision": row["task_revision"],
                      "payload": payload, "role": policy.get("role"), "reviewer": policy.get("reviewer")})
    units.sort(key=lambda unit: (ranks.get(unit["role"], len(ranks)), -depth.get(unit["task_id"], 0), unit["plan_id"], unit["task_id"]))
    busy_workers = {row["worker_id"] for row in active}
    busy_workdirs = {row["workdir"] for row in active}
    count, writers = len(active), sum(1 for row in active if row["writes"])
    limit = min(config["per_tick"], maximum or config["per_tick"])
    blocked = {lane for lane, landed in pending.items() if landed > 2 * config["max_batch"]} if config["max_batch"] else set()
    chosen, waiting = [], []

    def wait(unit, reason):
        if len(waiting) < 32:
            waiting.append({"task_id": unit["task_id"], "plan_id": unit["plan_id"], "reason": reason})

    for unit in units:
        payload = unit["payload"]
        if len(chosen) >= limit:
            wait(unit, "per-tick dispatch budget is spent")
            continue
        if count >= config["quota"]:
            wait(unit, "root session quota is spent")
            continue
        if payload["workdir"] in busy_workdirs:
            wait(unit, "worktree has an active assignment")
            continue
        writer = bool(payload["write_paths"])
        if writer and writers >= config["writers"]:
            wait(unit, "writer budget is spent")
            continue
        if writer and unit["role"] not in ranks and payload["repository"] in blocked:
            wait(unit, f"merge backpressure: lane has more than {2 * config['max_batch']} landed batches awaiting validation")
            continue
        seat = next((seat for seat, role in seats.items()
                     if role == unit["role"] and seat != unit["reviewer"] and seat not in busy_workers), None)
        if seat is None:
            wait(unit, f"no idle seat has role {unit['role']}")
            continue
        try:
            head = _git(payload["workdir"], "rev-parse", "HEAD")
        except (Conflict, OSError, subprocess.SubprocessError) as exc:
            wait(unit, str(exc))
            continue
        key = launch_key(unit["plan_id"], unit["task_id"], unit["revision"], head)
        prior = held.get(key)
        if prior is not None and prior["status"] in HELD:
            wait(unit, f"launch key already {prior['status']} at this head")
            continue
        if prior is not None and json.loads(prior["payload"]).get("retry_at", 0) > now:
            wait(unit, "deferred: " + json.loads(prior["payload"])["reason"])
            continue
        chosen.append({**unit, "seat": seat, "head": head, "key": key, "writer": writer})
        busy_workers.add(seat)
        busy_workdirs.add(payload["workdir"])
        count += 1
        writers += writer
    report = {"status": "idle", "launched": [], "deferred": [], "waiting": waiting,
              "budget": {"active": len(active), "selected": len(chosen), "box": None}}
    if dry_run:
        report.update(status="dry_run", selected=[{"task_id": item["task_id"], "plan_id": item["plan_id"], "worker_id": item["seat"],
                                                    "role": item["role"], "launch_key": item["key"]} for item in chosen])
        return report
    if chosen:
        granted, reason = host_pool.admit(root, [item["key"] for item in chosen], census=len(active),
                                          descendants=_descendants(supervisors), limits=config)
        for item in chosen[len(granted):]:
            wait(item, reason)
        chosen = chosen[:len(granted)]
        report["budget"]["box"] = reason
    fresh = [unit for unit in units if (unit["task_id"], unit["revision"]) not in seen]
    if chosen or fresh:
        owner, reserved = (_owner() if chosen else None), []
        with store.transaction() as tx:
            tx._change()
            tx.db.executemany("INSERT OR IGNORE INTO dispatch_ready VALUES(?,?,?)",
                              [(unit["task_id"], unit["revision"], now) for unit in fresh])
            for item in chosen:
                # Compare and set: a concurrent pass that reserved this key first keeps it.
                current = tx.db.execute("SELECT status FROM dispatch_launches WHERE launch_key=?", (item["key"],)).fetchone()
                if current is not None and current["status"] in HELD:
                    host_pool.release(item["key"])
                    wait(item, f"launch key already {current['status']} at this head")
                    continue
                tx.db.execute("INSERT OR REPLACE INTO dispatch_launches VALUES(?,?,?,?,?,?,NULL,'reserved',?)",
                    (item["key"], item["plan_id"], item["task_id"], item["revision"], item["head"], item["seat"],
                     canonical({"owner": owner, "reserved_at": now, "role": item["role"]})))
                reserved.append(item)
        chosen = reserved
    outcomes = _admit_all(store, chosen, config, env)
    for item, outcome in zip(chosen, outcomes):
        if "defer" in outcome:
            host_pool.release(item["key"])
            _update(store, item["key"], "deferred", reason=outcome["defer"], retry_at=time.time() + config["retry_seconds"])
            report["deferred"].append({"task_id": item["task_id"], "plan_id": item["plan_id"], "reason": outcome["defer"]})
            continue
        _update(store, item["key"], "assigned", run_id=outcome["run_id"], assigned_at=time.time())
        jobs.submit(("dispatch", item["key"]), lambda attached, key=item["key"]: _launch(attached, key, env), store)
        report["launched"].append({"task_id": item["task_id"], "plan_id": item["plan_id"], "worker_id": item["seat"],
                                   "run_id": outcome["run_id"], "launch_key": item["key"],
                                   "ready_seconds": round(now - seen.get((item["task_id"], item["revision"]), now), 3)})
    if report["launched"] or report["deferred"]:
        report["status"] = "dispatched"
    return report


def tick(store, *, env=None, jobs):
    """The loop's pass over every applied plan, when `dispatch.auto` is true and not paused."""
    config = settings(store.root)
    if paused(store.root):
        return None
    resumed = _resume(store, jobs, env)
    if not config["auto"]:
        return {"status": "resumed", "resumed": resumed} if resumed else None
    report = dispatch(store, config=config, env=env, jobs=jobs)
    if resumed:
        report["resumed"] = resumed
    return report if report["status"] != "idle" or resumed else None


def run(store, plan_id, *, env=None, dry_run=False, maximum=None):
    """`hx plan run PLAN`: one pass for one plan; launches run inline and the loop takes over."""
    from .loop_jobs import Jobs
    if not store.db.execute("SELECT 1 FROM plan_heads WHERE plan_id=?", (plan_id,)).fetchone():
        raise ValidationError("unknown plan")
    config = settings(store.root)
    if paused(store.root) and not dry_run:
        return {"status": "paused", "plan_id": plan_id, "note": "hx fleet resume re-enables dispatch"}
    jobs = Jobs.inline()
    if not dry_run:
        _resume(store, jobs, env)
    report = dispatch(store, config=config, env=env, jobs=jobs, plan_id=plan_id, dry_run=dry_run, maximum=maximum)
    if report["launched"]:
        from .application_loop import ensure
        ensure(store.root, env=env)
    return {"plan_id": plan_id, **report}


def wake_consumers(tx, producer):
    """A producer completed or published a contract: its consumers' deferrals end now."""
    tx.db.execute("""DELETE FROM dispatch_launches WHERE status='deferred' AND task_id IN (
        SELECT p.task_id FROM plan_prerequisites p JOIN task_heads h ON h.task_id=p.task_id AND h.revision=p.task_revision
        WHERE p.producer=?)""", (producer,))
