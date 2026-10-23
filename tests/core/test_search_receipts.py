"""A binding cites receipts of searches that cover where the packet would be built."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hx import cli, code_index, dependency_packets as packets, planning, runtime_policy, search_receipts, task as task_cmd, unit_execution as units
from hx.errors import ValidationError
from hx.native_tools import AdmissionDenied
from .test_appmap import mapped  # noqa: F401 — fixture chain
from .test_map_updates import active  # noqa: F401 — fixture chain
from .test_unit_execution import fleet  # noqa: F401 — fixture
from .test_planning import plan, unit
from .test_dependency_packets import binding
from .test_native_tools import runtime, configured, assignment, submitted, payload, fire  # noqa: F401 — fixture chain


def receipt(store, run, kind, query, scope, repository, complete=True):
    with store.transaction() as tx:
        return search_receipts.record(tx, run, kind, query, scope, repository, complete)


def test_packet_areas_are_the_write_directories_and_the_map_anchor_directories(active):
    store, repo, info, component, _, baseline, *_ = active
    payload = {"workdir": str(repo), "write_paths": ["src/billing/tax.py", "tests/test_tax.py"],
               "map_inputs": [{"repository": info["repo_id"], "snapshot": baseline, "id": component["id"], "version": 1}]}
    # The component is anchored in compiler.py at the repository root, so the root is an area.
    assert search_receipts.areas(store.db, payload) == [".", "src/billing", "tests"]
    (repo / "src").mkdir()
    assert search_receipts.areas(store.db, {**payload, "write_paths": ["src"], "map_inputs": []}) == ["src"]
    assert search_receipts.areas(store.db, {**payload, "write_paths": [], "map_inputs": []}) == ["."]
    with pytest.raises(ValidationError, match="not in the ledger"):
        search_receipts.areas(store.db, {**payload, "map_inputs": [{**payload["map_inputs"][0], "version": 9}]})


def test_a_binding_cites_complete_area_maps_of_every_packet_area(fleet):
    store, repo, info = fleet[:3]
    planning.apply(store, plan(fleet, [unit(repo, "implement", paths=["src/billing/tax.py", "tests/test_tax.py"])]))
    run = units.assign(store, "implement", 1, "eng-001")["run_id"]
    task = planning.assignment(store, "implement")
    body = binding(store, run, task)
    body.pop("searches")
    with pytest.raises(ValidationError, match="searches"):
        packets.bind_checks(store, run, body)
    unmapped = "without a complete area map: src/billing, tests;"
    for cited, message in (([], "1–32 unique"), (["absent"], "not a search of this run"),
                           # A name lookup, a grep and an incomplete map establish nothing about an area.
                           ([receipt(store, run, "graph", "hx code type Tax", ".", info["repo_id"])], unmapped),
                           ([receipt(store, run, "native", "Grep tax", "src", info["repo_id"])], unmapped),
                           ([receipt(store, run, "area", "hx code map --focus src --budget 2", "src", info["repo_id"], False)], unmapped),
                           ([receipt(store, run, "area", "hx code map --focus tests --budget 4096", "tests", info["repo_id"])], "without a complete area map: src/billing;"),
                           ([receipt(store, run, "area", "hx code map --focus . --budget 65536", ".", "other-repository")], "another repository")):
        with pytest.raises(ValidationError, match=message):
            packets.bind_checks(store, run, {**body, "searches": cited})
    assert planning.assignment(store, "implement")["binding_status"] == "unbound"
    # A complete map at or above each area covers it; traversal receipts ride beside them.
    whole = receipt(store, run, "area", "hx code map --focus . --budget 65536", ".", info["repo_id"])
    payload = planning.assignment(store, "implement")
    with store.transaction() as tx:
        search_receipts.cite(tx, run, payload, [whole])
    billing = receipt(store, run, "area", "hx code map --focus src/billing --budget 4096", "src/billing", info["repo_id"])
    tests = receipt(store, run, "area", "hx code map --focus tests --budget 4096", "tests", info["repo_id"])
    callers = receipt(store, run, "graph", "hx code callers round_cents --count", ".", info["repo_id"])
    assert billing == search_receipts.receipt_id("area", "hx code map --focus src/billing --budget 4096", "src/billing", info["repo_id"])
    proposed = packets.bind_checks(store, run, {**body, "searches": [billing, tests, callers]})
    view = packets.binding_view(store, run, "qa-001")
    assert view["binding"]["searches"] == [billing, tests, callers]
    assert packets.approve_binding(store, run, proposed["binding_hash"], "qa-001")["status"] == "approved"
    assert {"id": tests, "kind": "area", "query": "hx code map --focus tests --budget 4096", "scope": "tests",
            "repository": info["repo_id"], "complete": True} in search_receipts.listing(store.db, run)


def test_the_first_edit_is_refused_until_a_cited_binding_is_approved():
    task = {"dependency_packet": True, "binding_status": "proposed", "workdir": "/repo"}
    with pytest.raises(AdmissionDenied, match="search the packet areas, bind packet checks citing the search receipts"):
        runtime_policy.inspect_call({"payload": {}}, task, {"tool_name": "Write", "tool_input": {"file_path": "/repo/x.py", "content": ""}})


def test_native_searches_are_receipted_when_they_settle_and_when_the_cache_serves_them(runtime):
    store, repo, run, _ = runtime
    row = submitted(runtime)
    src = Path(repo) / "src"
    src.mkdir()
    (src / "tax.py").write_text("Compiler = 1\n")
    body = payload("search-1", tool_name="Grep", tool_input={"path": str(src), "pattern": "Compiler", "head_limit": 40})
    assert fire(store, run, row, "tool-start", body) == 0
    assert [r for r in search_receipts.listing(store.db, run) if r["kind"] == "native"] == []
    assert fire(store, run, row, "log", {**body, "tool_response": {"matches": ["Compiler = 1"], "complete": True}}) == 0
    native = [r for r in search_receipts.listing(store.db, run) if r["kind"] == "native"]
    repository = json.loads((Path(repo) / ".hx/map/manifest.json").read_text())["repo_id"]
    assert native == [{"id": search_receipts.receipt_id("native", "Grep Compiler", "src", repository), "kind": "native",
                       "query": "Grep Compiler", "scope": "src", "repository": repository, "complete": True}]
    # The cache answers the repeated search: the worker searched, and holds the same receipt once.
    assert fire(store, run, row, "tool-start", {**body, "tool_use_id": "search-2"}) == 2
    assert len([r for r in search_receipts.listing(store.db, run) if r["kind"] == "native"]) == 1
    failed = payload("search-3", tool_name="Grep", tool_input={"path": str(src), "pattern": "missing", "head_limit": 40})
    assert fire(store, run, row, "tool-start", failed) == 0
    assert fire(store, run, row, "log", {**failed, "tool_response": {"error": "failed"}}) == 0
    assert len([r for r in search_receipts.listing(store.db, run) if r["kind"] == "native"]) == 1
    shown = json.loads(task_cmd.task(store.root, "eng-001", run_id=run))
    assert shown["search_areas"] == ["src", "tests"] and shown["searches"][-1] == native[0]


def test_a_worker_graph_query_is_receipted_and_the_partner_leaves_none(fleet, monkeypatch, capsys):
    store, repo, info = fleet[:3]
    code_index.index_repo(store.root, repo)
    planning.apply(store, plan(fleet, [unit(repo)]))
    run = units.assign(store, "implement", 1, "eng-001")["run_id"]
    monkeypatch.setenv("HARNESS_ROOT", str(store.root))
    monkeypatch.setenv("HARNESS_ID", "partner")
    assert cli.main(["code", "type", "Compiler", "--repo", str(repo)]) == 0
    assert search_receipts.listing(store.db, run) == []
    monkeypatch.setenv("HARNESS_ID", "eng-001")
    assert cli.main(["code", "type", "Compiler", "--repo", str(repo)]) == 0
    assert cli.main(["code", "type", "Nothing", "--repo", str(repo), "--expect", "exists"]) == 3
    assert cli.main(["code", "map", "--budget", "100", "--focus", "src/", "tests", "--repo", str(repo)]) == 0
    assert cli.main(["code", "map", "--budget", "100", "--focus", ".", "--repo", str(repo)]) == 0
    assert cli.main(["code", "map", "--budget", "4096", "--focus", ".", "--repo", str(repo)]) == 0
    assert cli.main(["code", "fresh", "compiler.py", "--repo", str(repo)]) in {0, 3}  # Not a search: no receipt.
    capsys.readouterr()
    assert sorted((r["kind"], r["query"], r["scope"], r["complete"], r["repository"]) for r in search_receipts.listing(store.db, run)) == sorted([
        ("graph", f"hx code type Compiler --repo {repo}", ".", True, info["repo_id"]),
        ("graph", f"hx code type Nothing --repo {repo} --expect exists", ".", True, info["repo_id"]),
        ("area", f"hx code map --budget 100 --focus src/ tests --repo {repo}", "src", True, info["repo_id"]),
        ("area", f"hx code map --budget 100 --focus src/ tests --repo {repo}", "tests", True, info["repo_id"]),
        # Too small a budget to name compiler.py: the map is not complete over the repository.
        ("area", f"hx code map --budget 100 --focus . --repo {repo}", ".", False, info["repo_id"]),
        ("area", f"hx code map --budget 4096 --focus . --repo {repo}", ".", True, info["repo_id"])])
    # A worker without an active run searches without a receipt.
    monkeypatch.setenv("HARNESS_ID", "eng-002")
    assert cli.main(["code", "type", "Compiler", "--repo", str(repo)]) == 0
    assert len(search_receipts.listing(store.db, run)) == 6
