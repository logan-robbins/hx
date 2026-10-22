"""Plans at fleet scale: prerequisites on other applied plans and 256-unit plans."""
from __future__ import annotations

import copy
import time

import pytest

from hx import planning, unit_execution as units
from hx.continuity_store import Conflict
from hx.errors import ValidationError
from .test_appmap import git, mapped  # noqa: F401 — fixture chain
from .test_map_updates import active  # noqa: F401 — fixture chain
from .test_checks import definition
from .test_planning import bind, dependency, plan, unit
from .test_unit_execution import commit, finish, fleet  # noqa: F401 — fixture
from .test_dependency_packets import packet, publish_interface, run_receipts


def named(fleet, plan_id, tasks):
    body = plan(fleet, tasks)
    body["plan_id"] = plan_id
    return body


def ready(store, plan_id):
    return {item["task_id"]: item["status"] for item in units.ready(store, plan_id)["units"]}


def across(task, plan_id="platform"):
    return {**dependency(task), "plan_id": plan_id}


def consumer_worktree(repo, tmp_path):
    other = tmp_path / "consumer"
    git(repo, "worktree", "add", "-qb", "consumer", str(other))
    return other


def test_a_hard_prerequisite_on_another_plan_blocks_until_its_producer_completes(fleet, tmp_path):
    store, repo = fleet[:2]
    other = consumer_worktree(repo, tmp_path)
    planning.apply(store, named(fleet, "platform", [unit(repo, "schema")]))
    result = planning.apply(store, named(fleet, "reports", [unit(other, "report", prerequisites=[across("schema")])]))
    assert result["dependency_levels"] == [["report"]] and result["initial_units"] == []
    assert result["fleet"] == {"plans": 2, "units": 2, "waves": 2, "max_parallelism": 1}
    assert ready(store, "reports") == {"report": "blocked"}
    with pytest.raises(Conflict, match="completion proof"):
        units.assign(store, "report", 1, "eng-002")
    run = units.assign(store, "schema", 1, "eng-001")["run_id"]
    bind(store, run)
    finish(store, run)
    assert ready(store, "reports") == {"report": "ready"}
    second = units.assign(store, "report", 1, "eng-002")["run_id"]
    bind(store, second)
    finish(store, second)
    assert ready(store, "reports") == {"report": "deployable"}


def test_a_contract_on_another_plan_starts_its_consumer_before_the_producer_completes(fleet, tmp_path):
    store, repo = fleet[:2]
    other = consumer_worktree(repo, tmp_path)
    producer = packet(repo, "producer", paths=["api.py", "provider.py"])
    producer["outputs"] += [{"id": "interface", "version": 1, "kind": "contract", "paths": ["api.py"], "obligation": "The immutable API accepts one value and returns that value."},
                            {"id": "provider", "version": 1, "kind": "source", "paths": ["provider.py"], "obligation": "Real provider returns the required value."}]
    need = {"plan_id": "platform", "task_id": "producer", "output_id": "interface", "version": 1, "need": "contract",
            "stand_in": False, "provider_output": "provider", "provider_version": 1}
    consumer = packet(other, "consumer", paths=["app.py"], prerequisites=[need])
    consumer["outputs"] += [{"id": "application", "version": 1, "kind": "source", "paths": ["app.py"], "obligation": "Consumer behavior works against the published interface."}]
    planning.apply(store, named(fleet, "platform", [producer]))
    result = planning.apply(store, named(fleet, "app", [consumer]))
    assert result["hard_dependency_levels"] == [["consumer"]]
    assert result["early_start_dependencies"] == [{"consumer": "consumer", **need}]
    # Producer and consumer start together: separate worktrees, disjoint scopes.
    assert result["fleet"]["max_parallelism"] == 2 and result["fleet"]["waves"] == 2
    with pytest.raises(Conflict, match="interface"):
        units.assign(store, "consumer", 1, "eng-002")
    producer_run, _ = publish_interface(fleet)
    consumer_run = units.assign(store, "consumer", 1, "eng-002")["run_id"]
    bind(store, consumer_run, recipes={"unit": definition("from pathlib import Path; assert Path('app.py').read_text() == 'consumer_value = 42\\n'")})
    (other / "app.py").write_text("consumer_value = 42\n")
    commit(other)
    readiness = units.complete(store, consumer_run, run_receipts(store, consumer_run))["readiness"]
    assert readiness["packet_covered"] and readiness["pending_integration"] == ["producer/interface"]
    assert store.db.execute("SELECT ended_at FROM runs WHERE run_id=?", (producer_run,)).fetchone()[0] is None


@pytest.mark.parametrize("fault", ["own-plan", "unknown-plan", "foreign-unit", "version", "collision"])
def test_a_cross_plan_prerequisite_names_an_exact_output_of_another_applied_plan(fleet, fault):
    store, repo = fleet[:2]
    planning.apply(store, named(fleet, "platform", [unit(repo, "schema")]))
    planning.apply(store, named(fleet, "tools", [unit(repo, "linter", paths=["tools/lint.py"])]))
    need = across("schema")
    if fault == "own-plan":
        need["plan_id"] = "reports"
    elif fault == "unknown-plan":
        need["plan_id"] = "absent"
    elif fault == "foreign-unit":
        need["plan_id"] = "tools"
    elif fault == "version":
        need["version"] = 2
    tasks = [unit(repo, "report", paths=["reports/report.py"], prerequisites=[need] if fault != "collision" else [])]
    if fault == "collision":
        tasks.append(unit(repo, "schema", paths=["reports/schema.py"]))
    error = Conflict if fault == "collision" else ValidationError
    with pytest.raises(error):
        planning.validate(store, named(fleet, "reports", tasks))
    with pytest.raises(error):
        planning.apply(store, named(fleet, "reports", tasks))
    assert store.db.execute("SELECT count(*) FROM plan_heads").fetchone()[0] == 2


def test_a_cycle_through_another_plan_is_refused_and_a_dropped_output_is_reported(fleet):
    store, repo = fleet[:2]
    platform = named(fleet, "platform", [unit(repo, "schema")])
    planning.apply(store, platform)
    planning.apply(store, named(fleet, "reports", [unit(repo, "report", prerequisites=[across("schema")])]))
    revised = copy.deepcopy(platform)
    revised["expected_revision"] = 1
    revised["tasks"][0]["expected_revision"] = 1
    revised["tasks"][0]["prerequisites"] = [{**dependency("report"), "plan_id": "reports"}]
    with pytest.raises(ValidationError, match="cycle"):
        planning.apply(store, revised)
    revised["tasks"][0]["prerequisites"] = []
    revised["tasks"][0]["outputs"][0]["version"] = 2
    result = planning.apply(store, revised)
    assert result["cross_plan_warnings"] == [
        "unit report of plan reports needs schema/proof v1, which this plan no longer declares; replan reports"]


def test_256_unit_plans_validate_and_apply_in_under_two_seconds_over_the_fleet(fleet):
    store, repo = fleet[:2]

    def wide(prefix, plan_id, upstream=None):
        tasks = []
        for index in range(planning.MAX_UNITS):
            needs = [dependency(f"{prefix}{index - 1:03d}")] if index % 4 else []
            if upstream and not index % 4:
                needs = [{**dependency(f"{upstream}{index + 3:03d}"), "plan_id": "first"}]
            tasks.append(unit(repo, f"{prefix}{index:03d}", prerequisites=needs))
        return named(fleet, plan_id, tasks)

    first, second = wide("a", "first"), wide("b", "second", upstream="a")
    timings = {}
    for label, body in (("validate-first", first), ("apply-first", first), ("validate-second", second), ("apply-second", second)):
        started = time.perf_counter()
        result = (planning.validate if label.startswith("validate") else planning.apply)(store, body)
        timings[label] = time.perf_counter() - started
    assert max(timings.values()) < 2, timings
    assert result["level_widths"] == [64] * 4 and len(result["task_versions"]) == planning.MAX_UNITS
    assert result["fleet"] == {"plans": 2, "units": 512, "waves": 8, "max_parallelism": 1}
    too_many = wide("c", "third")
    too_many["tasks"].append(unit(repo, "c256"))
    with pytest.raises(ValidationError, match="1–256"):
        planning.validate(store, too_many)
