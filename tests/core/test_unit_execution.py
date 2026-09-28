from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from hx import checks, planning, unit_execution as units
from hx.continuity_store import Conflict, ContinuityStore
from hx.errors import ValidationError
from .test_appmap import git, mapped
from .test_map_updates import active
from .test_planning import unit, plan, dependency


@pytest.fixture
def fleet(active):
    with active[0].transaction() as tx:
        for run in active[7]:
            tx.finish_run(run, "stopped")
    return active


def statuses(store):
    return {item["task_id"]: item for item in units.ready(store, "continuation")["units"]}


def finish(store, run, *, env=None):
    receipt = checks.run_check(store, run, "unit", env=env)
    return units.complete(store, run, {"unit": receipt["receipt_id"]}, env=env)


def commit(repo):
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Complete exact unit outputs")


def source_unit(repo, name, path="feature.py", prerequisites=None):
    task = unit(repo, name, paths=[path], prerequisites=prerequisites)
    task["outputs"].append({"id": "source", "version": 1, "kind": "source", "paths": [path]})
    return task


def test_ready_and_completion_unlock_receipt_dependency(fleet):
    store, repo = fleet[:2]
    planning.apply(store, plan(fleet, [unit(repo, "a"), unit(repo, "b", prerequisites=[dependency("a")])]))
    assert statuses(store)["a"]["status"] == "ready"
    assert statuses(store)["b"]["status"] == "blocked"
    first = units.assign(store, "a", 1, "eng-001")["run_id"]
    with pytest.raises(Conflict, match="verified"):
        with store.transaction() as tx:
            tx.finish_run(first, "completed")
    assert statuses(store)["b"]["status"] == "blocked"
    completed = finish(store, first)
    assert units.complete(store, first, completed["checks"]) == completed
    assert store.db.execute("SELECT count(*) FROM leases").fetchone()[0] == 0
    assert statuses(store)["b"]["status"] == "ready"
    second = units.assign(store, "b", 1, "eng-002")["run_id"]
    finish(store, second)
    assert all(item["status"] == "completed" for item in statuses(store).values())


def test_completion_waits_for_native_delivery_and_retains_ownership(fleet, monkeypatch):
    from hx import native_producer as relay, native_capture
    store, repo = fleet[:2]
    planning.apply(store, plan(fleet))
    run = units.assign(store, "implement", 1, "eng-001")["run_id"]
    with monkeypatch.context() as m:
        def unavailable(*args, **kwargs):
            raise OSError("temporary capture failure")
        m.setattr(native_capture, "enqueue", unavailable)
        relay.produce(store, run_id=run, worker_id="eng-001", adapter="pi", session_id="S", stream_id="native",
                      payload={"event": "log", "tool_use_id": "C", "tool_response": "Check output"})
    with pytest.raises(Conflict, match="unacknowledged"):
        finish(store, run)
    assert store.db.execute("SELECT count(*) FROM leases WHERE run_id=?", (run,)).fetchone()[0] == 2
    assert not store.db.execute("SELECT 1 FROM unit_completions WHERE run_id=?", (run,)).fetchone()
    relay.retry(store)
    assert finish(store, run)["run_id"] == run


def test_late_producer_evidence_blocks_downstream_admission(fleet):
    from hx import native_producer as relay
    store, repo = fleet[:2]
    planning.apply(store, plan(fleet, [unit(repo, "a"), unit(repo, "b", prerequisites=[dependency("a")])]))
    run = units.assign(store, "a", 1, "eng-001")["run_id"]
    finish(store, run)
    assert statuses(store)["b"]["status"] == "ready"
    relay.produce(store, run_id=run, worker_id="eng-001", adapter="claude", session_id="late-session", stream_id="native",
                  payload={"event": "stop", "last_assistant_message": "A late background operation failed."})
    assert statuses(store)["b"]["status"] == "blocked"
    with pytest.raises(Conflict, match="unacknowledged"):
        units.assign(store, "b", 1, "eng-002")


def test_independent_units_parallel_only_in_distinct_worktrees(fleet, tmp_path):
    store, repo = fleet[:2]
    other = tmp_path / "other"
    git(repo, "worktree", "add", "-qb", "parallel", str(other))
    planning.apply(store, plan(fleet, [unit(repo, "a"), unit(other, "b"), unit(repo, "c")]))
    a = units.assign(store, "a", 1, "eng-001")["run_id"]
    b = units.assign(store, "b", 1, "eng-002")["run_id"]
    with pytest.raises(Conflict, match="worktree"):
        units.assign(store, "c", 1, "eng-003")
    units.stop(store, a)
    assert units.assign(store, "c", 1, "eng-003")["run_id"]
    assert store.db.execute("SELECT ended_at FROM runs WHERE run_id=?", (b,)).fetchone()[0] is None


def test_overlapping_leases_conflict_across_plans_and_worktrees(fleet, tmp_path):
    store, repo = fleet[:2]
    other = tmp_path / "other"
    git(repo, "worktree", "add", "-qb", "collision", str(other))
    planning.apply(store, plan(fleet, [unit(repo, "a", paths=["src"])]))
    second = plan(fleet, [unit(other, "b", paths=["SRC/new.py"])])
    second["plan_id"] = "second"
    planning.apply(store, second)
    a = units.assign(store, "a", 1, "eng-001")["run_id"]
    with pytest.raises(Conflict, match="lease overlaps"):
        units.assign(store, "b", 1, "eng-002")
    units.stop(store, a)
    assert units.assign(store, "b", 1, "eng-002")["run_id"]


def test_start_run_cannot_bypass_planning_readiness(fleet):
    store, repo = fleet[:2]
    planning.apply(store, plan(fleet, [unit(repo, "a"), unit(repo, "b", prerequisites=[dependency("a")])]))
    with pytest.raises(Conflict, match="completion proof"):
        with store.transaction() as tx:
            tx.start_run("b", 1, "eng-001")
    assert store.db.execute("SELECT count(*) FROM unit_runs").fetchone()[0] == 0


def test_unexpected_write_pauses_and_retains_leases(fleet):
    store, repo = fleet[:2]
    planning.apply(store, plan(fleet))
    run = units.assign(store, "implement", 1, "eng-001")["run_id"]
    (repo / "unexpected.py").write_text("unsafe = True\n")
    audit = units.audit(store, run)
    assert audit["unexpected_paths"] == ["unexpected.py"] and not audit["within_scope"]
    (repo / "unexpected.py").unlink()
    assert not units.audit(store, run)["within_scope"]
    assert store.db.execute("SELECT count(*) FROM leases WHERE run_id=?", (run,)).fetchone()[0] == 2
    with pytest.raises(Conflict, match="paused"):
        finish(store, run)
    units.stop(store, run)
    assert store.db.execute("SELECT count(*) FROM leases WHERE run_id=?", (run,)).fetchone()[0] == 0


def test_failed_or_changed_check_cannot_complete(fleet):
    store, repo = fleet[:2]
    task = source_unit(repo, "a")
    planning.apply(store, plan(fleet, [task]))
    run = units.assign(store, "a", 1, "eng-001")["run_id"]
    receipt = checks.run_check(store, run, "unit")
    (repo / "feature.py").write_text("value = 2\n")
    commit(repo)
    with pytest.raises(Conflict, match="no longer current"):
        units.complete(store, run, {"unit": receipt["receipt_id"]})
    assert store.db.execute("SELECT count(*) FROM unit_outputs").fetchone()[0] == 0
    finish(store, run)


def test_completion_requires_all_checks_and_declared_changed_outputs(fleet):
    store, repo = fleet[:2]
    planning.apply(store, plan(fleet, [unit(repo, "a", paths=["feature.py"])]))
    run = units.assign(store, "a", 1, "eng-001")["run_id"]
    with pytest.raises(ValidationError, match="every assigned check"):
        units.complete(store, run, {})
    (repo / "feature.py").write_text("value = 1\n")
    commit(repo)
    with pytest.raises(Conflict, match="explicit source outputs"):
        finish(store, run)


def test_source_producer_integration_consumer_end_to_end(fleet, tmp_path):
    store, repo = fleet[:2]
    consumer_repo = tmp_path / "consumer"
    git(repo, "worktree", "add", "-qb", "consume", str(consumer_repo))
    producer = source_unit(repo, "producer")
    integration = source_unit(consumer_repo, "integration", prerequisites=[dependency("producer")])
    consumer = unit(consumer_repo, "consumer", prerequisites=[
        {"task_id": "producer", "output_id": "source", "version": 1}, dependency("integration")])
    planning.apply(store, plan(fleet, [producer, integration, consumer]))
    producing = units.assign(store, "producer", 1, "eng-001")["run_id"]
    (repo / "feature.py").write_text("value = 42\n")
    commit(repo)
    produced = finish(store, producing)
    assert statuses(store)["integration"]["status"] == "ready"
    assert statuses(store)["consumer"]["status"] == "blocked"
    integrating = units.assign(store, "integration", 1, "eng-002")["run_id"]
    with pytest.raises(Conflict, match="not installed"):
        units.materialize(store, "consumer", integrating, "producer", "source")
    git(consumer_repo, "checkout", produced["commit"], "--", "feature.py")
    commit(consumer_repo)
    materialized = units.materialize(store, "consumer", integrating, "producer", "source")
    assert materialized["output_hash"] == produced["outputs"]["source"]
    assert statuses(store)["consumer"]["status"] == "blocked"
    finish(store, integrating)
    assert statuses(store)["consumer"]["status"] == "ready"
    consuming = units.assign(store, "consumer", 1, "eng-003")["run_id"]
    finish(store, consuming)
    assert all(item["status"] == "completed" for item in statuses(store).values())


def test_materialization_does_not_survive_failed_integration(fleet, tmp_path):
    store, repo = fleet[:2]
    other = tmp_path / "other"
    git(repo, "worktree", "add", "-qb", "failed-integration", str(other))
    producer = source_unit(repo, "a", "compiler.py")
    integration = source_unit(other, "b", "compiler.py", prerequisites=[dependency("a")])
    consumer = unit(other, "c", prerequisites=[{"task_id": "a", "output_id": "source", "version": 1}, dependency("b")])
    planning.apply(store, plan(fleet, [producer, integration, consumer]))
    first = units.assign(store, "a", 1, "eng-001")["run_id"]
    finish(store, first)
    second = units.assign(store, "b", 1, "eng-002")["run_id"]
    units.materialize(store, "c", second, "a", "source")
    units.stop(store, second, "failed")
    assert statuses(store)["c"]["status"] == "blocked"


def test_plan_revision_cannot_replace_active_assignment(fleet):
    store = fleet[0]
    body = plan(fleet)
    planning.apply(store, body)
    run = units.assign(store, "implement", 1, "eng-001")["run_id"]
    revised = copy.deepcopy(body)
    revised["expected_revision"] = 1
    revised["tasks"][0]["expected_revision"] = 1
    revised["tasks"][0]["behavior"] = "Validate a revised behavior."
    with pytest.raises(Conflict, match="active unit"):
        planning.apply(store, revised)
    units.stop(store, run)
    planning.apply(store, revised)


def test_completed_contract_needs_new_versions_and_live_consumers_stop_first(fleet, tmp_path):
    store, repo = fleet[:2]
    other = tmp_path / "other"
    git(repo, "worktree", "add", "-qb", "versioning", str(other))
    body = plan(fleet, [unit(repo, "a"), unit(other, "b", prerequisites=[dependency("a")])])
    planning.apply(store, body)
    run = units.assign(store, "a", 1, "eng-001")["run_id"]
    finish(store, run)
    consumer = units.assign(store, "b", 1, "eng-002")["run_id"]
    revised = copy.deepcopy(body)
    revised["expected_revision"] = 1
    for item in revised["tasks"]:
        item["expected_revision"] = 1
    revised["tasks"][0]["behavior"] = "Assert the revised acceptance criteria."
    with pytest.raises(Conflict, match="dependent units"):
        planning.apply(store, revised)
    units.stop(store, consumer)
    with pytest.raises(Conflict, match="new output versions"):
        planning.apply(store, revised)
    revised["tasks"][0]["outputs"][0]["version"] = 2
    revised["tasks"][1]["prerequisites"][0]["version"] = 2
    planning.apply(store, revised)
    assert statuses(store)["b"]["status"] == "blocked"


def test_leases_survive_restart_without_automatic_expiry(fleet):
    store, repo = fleet[:2]
    planning.apply(store, plan(fleet))
    run = units.assign(store, "implement", 1, "eng-001")["run_id"]
    with ContinuityStore(store.root) as other:
        assert statuses(other)["implement"]["status"] == "blocked"
        units.stop(other, run)
    assert statuses(store)["implement"]["status"] == "ready"


def test_simultaneous_prefix_reservations_have_exactly_one_owner(fleet, tmp_path):
    store, repo = fleet[:2]
    other = tmp_path / "other"
    git(repo, "worktree", "add", "-qb", "race", str(other))
    planning.apply(store, plan(fleet, [unit(repo, "a", paths=["src"])]))
    second = plan(fleet, [unit(other, "b", paths=["src/child.py"])])
    second["plan_id"] = "second"
    planning.apply(store, second)
    def attempt(name):
        with ContinuityStore(store.root) as connection:
            try:
                return units.assign(connection, name, 1, "worker-" + name)
            except Conflict:
                return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, ["a", "b"]))
    assert sum(result is not None for result in results) == 1
    assert store.db.execute("SELECT count(*) FROM unit_runs").fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM leases").fetchone()[0] == 1


def test_legacy_assignment_cannot_enter_a_planned_worktree(fleet):
    store, repo = fleet[:2]
    planning.apply(store, plan(fleet))
    units.assign(store, "implement", 1, "eng-001")
    with pytest.raises(Conflict, match="owned by an active planned"):
        with store.transaction() as tx:
            tx.start_run("T0", 1, "eng-002")


def test_failing_acceptance_retains_run_and_cannot_publish_outputs(fleet):
    store, repo = fleet[:2]
    task = unit(repo, "a")
    task["checks"]["unit"]["argv"][-1] = "raise SystemExit(2)"
    planning.apply(store, plan(fleet, [task]))
    run = units.assign(store, "a", 1, "eng-001")["run_id"]
    with pytest.raises(Conflict, match="receipt_not_reusable"):
        finish(store, run)
    assert store.db.execute("SELECT ended_at FROM runs WHERE run_id=?", (run,)).fetchone()[0] is None
    assert store.db.execute("SELECT count(*) FROM unit_outputs").fetchone()[0] == 0


def test_plan_execution_cli_round_trip(fleet, tmp_path, run_hx):
    store, repo = fleet[:2]
    source = tmp_path / "plan.json"
    source.write_text(json.dumps(plan(fleet)))
    def command(*args, env_extra=None):
        result = run_hx("plan", *args, "--root", str(store.root), env_extra=env_extra)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    command("apply", "--file", str(source))
    assert command("ready", "continuation")["units"][0]["status"] == "ready"
    run = command("assign", "implement", "--revision", "1", "--worker", "eng-001")["run_id"]
    worker = {"HARNESS_ID": "eng-001"}
    checked = run_hx("check", "unit", "--run", run, "--root", str(store.root), env_extra=worker)
    assert checked.returncode == 0, checked.stderr
    receipts = tmp_path / "receipts.json"
    receipts.write_text(json.dumps({"unit": json.loads(checked.stdout)["receipt_id"]}))
    wrong = run_hx("plan", "finish", run, "--file", str(receipts), "--root", str(store.root), env_extra={"HARNESS_ID": "eng-002"})
    assert wrong.returncode != 0 and "only its own assignment" in wrong.stderr
    command("finish", run, "--file", str(receipts), env_extra=worker)
    assert command("ready", "continuation")["units"][0]["status"] == "completed"
