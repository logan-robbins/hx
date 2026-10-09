from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from hx import appmap, map_updates, planning, retrieval
from hx.continuity_store import Conflict, ContinuityStore, SCHEMA_VERSION
from hx.errors import Refused, ValidationError
from hx.facts import RequiredContextOverflow
from .test_appmap import git, mapped, record
from .test_checks import definition
from .test_map_updates import active, patch


def unit(repo, name="implement", *, paths=None, prerequisites=None):
    return {"id": name, "expected_revision": 0,
        "behavior": "Continuation preserves events arriving after its frozen boundary.",
        "acceptance": ["A late event appears in the next pass and is never acknowledged early."],
        "workdir": str(repo), "write_paths": paths if paths is not None else [f"src/{name}.py", f"tests/test_{name}.py"],
        "map_inputs": [], "checks": {"unit": definition()},
        "outputs": [{"id": "proof", "version": 1, "kind": "receipt", "check_id": "unit"}],
        "prerequisites": prerequisites or []}


def plan(active, tasks=None):
    return {"schema_version": 1, "plan_id": "continuation", "expected_revision": 0,
        "repository": active[2]["repo_id"], "goal": "Preserve current obligations across continuation.",
        "constraints": ["Do not acknowledge events arriving after the frozen boundary."],
        "tasks": tasks or [unit(active[1])]}


def dependency(task):
    return {"task_id": task, "output_id": "proof", "version": 1}


def test_brief_returns_exact_contract_check_and_ownership_without_filesystem_search(active, monkeypatch):
    store, repo, _, component, _, _, snapshot, runs, _ = active
    interface = record("packet-contract", "interface", data={"contract": "Packet(goal: str, pending: list[str])", "invariants": ["Pending events remain unacknowledged."]})
    recipe = definition()
    recipe.update(id="compiler-check", argv=["python", "-c", "assert 'not acknowledged' != 'acknowledged'"])
    check = record("compiler-check", "check", data={"recipe": recipe})
    consumer = record("continuation-runner", "component", summary="Continuation uses the packet contract.",
        edges=[{"kind": "consumes", "to": "packet-contract", "status": "validated", "evidence": {"kind": "source", "anchors": [0]}}],
        anchors=copy.deepcopy(component["anchors"]))
    changed = copy.deepcopy(component)
    changed["version"] = 2
    changed["edges"] += [
        {"kind": "provides", "to": "packet-contract", "status": "validated", "evidence": {"kind": "source", "anchors": [0]}},
        {"kind": "checked_by", "to": "compiler-check", "status": "validated", "evidence": {"kind": "check_declaration", "check_id": "compiler-check", "declaration": "Assert late events remain pending."}},
    ]
    map_updates.propose(store, repo, patch(active, nodes=[changed, interface, check, consumer],
        reads={"compiler": 1, "bounded-context": 1, "packet-contract": 0, "compiler-check": 0, "continuation-runner": 0}))
    with store.transaction() as tx:
        tx.db.execute("INSERT INTO leases VALUES(?,?,?,?)", (active[2]["repo_id"], "compiler.py", runs[0], "{}"))
    monkeypatch.setattr(appmap, "source_anchor", lambda *a, **k: pytest.fail("unchanged brief reread source bodies"))
    monkeypatch.setattr(Path, "rglob", lambda *a, **k: pytest.fail("brief scanned the filesystem"))
    brief = retrieval.plan_context(store, repo, snapshot, "Preserve compiler pending events.", required=["compiler"])
    nodes = {node["id"]: node for node in brief["nodes"]}
    assert nodes["packet-contract"]["data"] == interface["data"]
    assert nodes["compiler-check"]["data"]["recipe"]["argv"] == recipe["argv"]
    assert nodes["compiler"]["anchors"][0]["symbol"] == "Compiler.build"
    assert "continuation-runner" in nodes
    assert brief["owners"][0]["task_id"] == "T0"


def test_brief_exposes_missing_checks_and_bounded_high_fanout(active):
    store, repo, _, _, _, _, snapshot, _, _ = active
    nodes = [record(f"neighbor-{i}") for i in range(15)]
    hub = record("hub", edges=[{"kind": "depends_on", "to": node["id"], "status": "candidate", "evidence": {}} for node in nodes])
    map_updates.propose(store, repo, patch(active, nodes=[hub, *nodes], reads={node["id"]: 0 for node in [hub, *nodes]}))
    brief = retrieval.plan_context(store, repo, snapshot, "hub", required=["hub"])
    assert brief["coverage"]["omitted_edges"] > 0
    assert brief["relations"] == []
    assert any("check" in item for item in brief["discovery"] if isinstance(item, str))
    assert len(json.dumps(brief).encode()) < 16000


def test_brief_required_overflow_and_unknown_records_fail_explicitly(active):
    store, repo, _, _, _, _, snapshot, _, _ = active
    with pytest.raises(Conflict, match="required planning record"):
        retrieval.plan_context(store, repo, snapshot, "Unknown responsibility", required=["absent"])
    with pytest.raises(RequiredContextOverflow):
        retrieval.plan_context(store, repo, snapshot, "compiler", required=["compiler"], max_bytes=2000)


def test_search_index_tracks_new_semantics_and_isolates_baseline(active):
    store, repo, _, _, _, baseline, snapshot, _, _ = active
    body = patch(active)
    body["operations"][0]["record"]["summary"] = "Zephyrunique routing preserves context."
    map_updates.propose(store, repo, body)
    assert "compiler" in retrieval.plan_context(store, repo, snapshot, "Zephyrunique")["coverage"]["seed_ids"]
    assert retrieval.plan_context(store, repo, baseline, "Zephyrunique")["coverage"]["seed_ids"] == []


def test_apply_focuses_unit_and_preserves_constraints_and_exact_check(active):
    store, repo, _, _, _, _, _, _, _ = active
    body = plan(active)
    body["tasks"][0]["checks"]["unit"]["argv"][-1] = "assert 'a  b' != 'a b'\nprint('not lost')"
    validated = planning.validate(store, body)
    assert validated["initial_units"] == ["implement"]
    result = planning.apply(store, body)
    assert planning.apply(store, body) == result
    assigned = planning.assignment(store, "implement")
    assert assigned["constraints"] == body["constraints"]
    assert assigned["checks"] == body["tasks"][0]["checks"]
    assert assigned["goal"] == body["tasks"][0]["behavior"]
    assert assigned["revision"] == 1


def test_plan_distinguishes_dependency_order_from_parallel_worktrees(active, tmp_path):
    store, repo, _, _, _, _, _, _, _ = active
    other = tmp_path / "other"
    git(repo, "worktree", "add", "-qb", "planning-other", str(other))
    a, b = unit(repo, "adapter-a"), unit(other, "adapter-b")
    integration = unit(repo, "integration", paths=["src"], prerequisites=[dependency("adapter-a"), dependency("adapter-b")])
    result = planning.validate(store, plan(active, [a, b, integration]))
    assert result["dependency_levels"] == [["adapter-a", "adapter-b"], ["integration"]]
    assert result["potential_parallel_groups"][0] == ["adapter-a", "adapter-b"]
    b["workdir"] = str(repo)
    result = planning.validate(store, plan(active, [a, b, integration]))
    assert ["adapter-a", "adapter-b"] not in result["potential_parallel_groups"]


@pytest.mark.parametrize("fault", ["cycle", "missing-output", "output-version", "missing-check", "overlap", "prefix", "case", "outside-output", "oversized"])
def test_invalid_decomposition_is_rejected(active, fault):
    store, repo, _, _, _, _, _, _, _ = active
    a, b = unit(repo, "a"), unit(repo, "b")
    if fault == "cycle":
        a["prerequisites"], b["prerequisites"] = [dependency("b")], [dependency("a")]
    elif fault in {"missing-output", "output-version"}:
        b["prerequisites"] = [dependency("a")]
        b["prerequisites"][0]["output_id" if fault == "missing-output" else "version"] = "absent" if fault == "missing-output" else 2
    elif fault == "missing-check":
        a["checks"] = {}
    elif fault in {"overlap", "prefix", "case"}:
        b["write_paths"] = ["src/a.py" if fault == "overlap" else "src" if fault == "prefix" else "SRC/A.PY"]
    elif fault == "outside-output":
        a["outputs"] = [{"id": "source", "version": 1, "kind": "source", "paths": ["other.py"]}]
    else:
        a["behavior"] = "x" * 17000
    with pytest.raises(ValidationError):
        planning.apply(store, plan(active, [a, b]))
    assert store.db.execute("SELECT count(*) FROM plans").fetchone()[0] == 0


def test_symlink_aliases_cannot_disguise_overlapping_writes(active):
    store, repo, _, _, _, _, _, _, _ = active
    (repo / "src").mkdir()
    (repo / "alias").symlink_to(repo / "src", target_is_directory=True)
    with pytest.raises(ValidationError, match="overlapping"):
        planning.validate(store, plan(active, [unit(repo, "a", paths=["src"]), unit(repo, "b", paths=["alias/new.py"])]))


def test_apply_rolls_back_all_units_when_map_input_is_stale(active):
    store, repo, _, _, _, _, snapshot, _, _ = active
    a, b = unit(repo, "a"), unit(repo, "b")
    b["map_inputs"] = [{"repository": active[2]["repo_id"], "snapshot": snapshot, "id": "compiler", "version": 99}]
    with pytest.raises(Conflict):
        planning.apply(store, plan(active, [a, b]))
    with store.transaction() as tx:
        assert tx.task("a") is None and tx.task("b") is None


def test_plan_revision_preserves_stable_units_and_checks_task_cas(active):
    store = active[0]
    body = plan(active)
    planning.apply(store, body)
    changed = copy.deepcopy(body)
    changed["expected_revision"] = 1
    changed["tasks"][0]["expected_revision"] = 1
    changed["tasks"][0]["acceptance"].append("The check also covers a boundary event with an empty body.")
    result = planning.apply(store, changed)
    assert result["task_versions"] == {"implement": 2}
    with pytest.raises(Conflict, match="plan expected revision"):
        planning.apply(store, body)


def test_brief_to_plan_cli_has_operator_boundary(active, tmp_path, run_hx):
    store, repo, _, _, _, _, snapshot, _, _ = active
    goal = tmp_path / "goal.md"
    goal.write_text("Preserve compiler obligations.")
    brief = run_hx("map", "plan-context", "--repo", str(repo), "--snapshot", snapshot, "--goal", str(goal), "--root", str(store.root))
    assert brief.returncode == 0, brief.stderr
    assert json.loads(brief.stdout)["nodes"]
    source = tmp_path / "plan.json"
    source.write_text(json.dumps(plan(active)))
    applied = run_hx("plan", "apply", "--file", str(source), "--root", str(store.root))
    assert applied.returncode == 0, applied.stderr
    assert json.loads(applied.stdout)["task_versions"] == {"implement": 1}
    with pytest.raises(Refused):
        planning.main(["apply", "--file", str(source)], store.root, env={"HARNESS_ID": "eng-001"})


def test_schema_eight_upgrade_builds_search_index(active):
    store, repo, _, _, _, _, snapshot, _, _ = active
    store.db.execute("DELETE FROM map_search")
    store.db.execute("PRAGMA user_version=8")
    with ContinuityStore(store.root) as upgraded:
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert retrieval.plan_context(upgraded, repo, snapshot, "compiler")["nodes"]
