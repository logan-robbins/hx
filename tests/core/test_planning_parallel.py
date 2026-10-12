"""Partner planning: parallelism sizing and interface-first warnings."""
from __future__ import annotations

from hx import planning
from .test_planning import plan, unit
from .test_appmap import mapped  # noqa: F401 — fixture chain
from .test_map_updates import active  # noqa: F401 — fixture


def _source_unit(repo, name, prerequisites=None):
    body = unit(repo, name, prerequisites=prerequisites)
    body["outputs"] = [{"id": "lib", "version": 1, "kind": "source",
                        "paths": [f"src/{name}.py"]}]
    return body


def test_validate_reports_parallelism(active):
    store, repo, *_ = active
    a = unit(repo, "a")
    b = unit(repo, "b")
    result = planning.validate(store, plan(active, [a, b]))
    assert result["waves"] == 1
    assert result["level_widths"] == [2]
    assert result["max_parallelism"] >= 1
    assert result["initial_units"] == ["a", "b"]


def test_validate_counts_waves(active):
    store, repo, *_ = active
    a = unit(repo, "a")
    b = unit(repo, "b", prerequisites=[{"task_id": "a", "output_id": "proof", "version": 1}])
    result = planning.validate(store, plan(active, [a, b]))
    assert result["waves"] == 2
    assert result["level_widths"] == [1, 1]


def test_interface_first_warning_for_source_dependency(active):
    store, repo, *_ = active
    producer = _source_unit(repo, "producer")
    consumer = unit(repo, "consumer",
                    prerequisites=[{"task_id": "producer", "output_id": "lib", "version": 1}])
    result = planning.validate(store, plan(active, [producer, consumer]))
    warnings = result["interface_warnings"]
    assert any("producer" in w and "interface" in w for w in warnings)


def test_no_interface_warning_for_receipt_dependency(active):
    store, repo, *_ = active
    a = unit(repo, "a")
    b = unit(repo, "b", prerequisites=[{"task_id": "a", "output_id": "proof", "version": 1}])
    result = planning.validate(store, plan(active, [a, b]))
    assert result["interface_warnings"] == []
