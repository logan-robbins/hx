from __future__ import annotations

import copy
import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from hx import appmap, map_updates
from hx.continuity_store import ContinuityStore, Conflict, SCHEMA_VERSION
from hx.errors import ValidationError
from .test_appmap import commit, git, mapped, record


@pytest.fixture
def active(mapped):
    store, repo, info, component, behavior = mapped
    baseline = appmap.import_baseline(store, repo)["snapshot"]
    overlay = map_updates.create_overlay(store, repo, baseline)["snapshot"]
    runs, events = [], []
    with store.transaction() as tx:
        for index in range(2):
            task_id = f"T{index}"
            tx.put_task(task_id, {"goal": "Keep current map facts.", "workdir": str(repo)}, expected_revision=0)
            run = tx.start_run(task_id, 1, f"worker-{index}")
            event = tx.append_event(run, "main", f"evidence-{index}", "tool_result", {"finding": "Reviewed source and current responsibility."})
            runs.append(run)
            events.append(event["event_id"])
    return store, repo, info, component, behavior, baseline, overlay, runs, events


def patch(active, *, index=0, patch_id="patch-1", nodes=None, reads=None, operations=None):
    _, _, _, component, _, _, overlay, runs, events = active
    if nodes is None and operations is None:
        node = copy.deepcopy(component)
        node["version"] = 2
        node["summary"] = "The compiler retains every required task constraint."
        nodes = [node]
    if operations is None:
        operations = [{"op": "put", "record": node} for node in nodes]
    return {"schema_version": 1, "patch_id": patch_id, "task_id": f"T{index}", "run_id": runs[index],
            "snapshot": overlay, "read_versions": reads if reads is not None else {"compiler": 1, "bounded-context": 1},
            "operations": operations, "evidence_ids": [events[index]]}


def selected(active, name="compiler", *, snapshot=None):
    store, repo, _, _, _, _, overlay, _, _ = active
    return appmap.get_record(store, repo, snapshot or overlay, name)


def test_independent_concurrent_patches_both_survive(active):
    store, repo, _, component, behavior, _, _, _, _ = active
    # Distinct read sets: these new responsibilities have no implicit dependency.
    a = record("packets", summary="Packets preserve obligations.")
    b = record("tools", summary="The catalog locates current tools.")
    first = patch(active, nodes=[a], reads={"packets": 0})
    second = patch(active, index=1, patch_id="patch-2", nodes=[b], reads={"tools": 0})
    def submit(body):
        with ContinuityStore(store.root) as connection:
            return map_updates.propose(connection, repo, body)
    # Two tiny writers specifically exercise the collision requirement; the rest
    # of the suite stays serial, without worker or model processes.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, [first, second]))
    assert all(result["records"] for result in results)
    assert selected(active, "packets")["record"]["summary"] == a["summary"]
    assert selected(active, "tools")["record"]["summary"] == b["summary"]


def test_identical_stale_updates_coalesce_and_keep_both_evidence_refs(active):
    store, repo, info, _, _, _, overlay, _, events = active
    first = patch(active)
    second = patch(active, index=1, patch_id="patch-2")
    assert not map_updates.propose(store, repo, first)["records"]["compiler"]["coalesced"]
    result = map_updates.propose(store, repo, second)
    assert result["records"]["compiler"] == {"version": 2, "coalesced": True}
    refs = store.db.execute("SELECT event_id FROM map_evidence WHERE repository=? AND snapshot=? AND record_id='compiler' AND version=2", (info["repo_id"], overlay))
    assert {row[0] for row in refs} == set(events)
    assert selected(active)["record"]["version"] == 2


def test_changed_stale_patch_reports_base_current_proposed(active):
    store, repo, _, _, _, _, _, _, _ = active
    first = patch(active)
    map_updates.propose(store, repo, first)
    second = patch(active, index=1, patch_id="patch-2")
    second["operations"][0]["record"]["summary"] = "A different responsibility."
    with pytest.raises(map_updates.MapConflict) as caught:
        map_updates.propose(store, repo, second)
    details = caught.value.details
    assert details["base"]["version"] == 1 and details["current"]["version"] == 2
    assert details["proposed"]["summary"] == "A different responsibility."
    assert selected(active)["record"]["summary"] == first["operations"][0]["record"]["summary"]


def test_changed_read_dependency_rejects_even_identical_write(active):
    store, repo, _, _, behavior, _, _, _, _ = active
    body = patch(active)
    map_updates.propose(store, repo, body)
    changed = copy.deepcopy(behavior)
    changed.update(version=2, summary="The required behavior has changed.")
    map_updates.propose(store, repo, patch(active, patch_id="behavior", nodes=[changed], reads={"bounded-context": 1}))
    with pytest.raises(map_updates.MapConflict) as caught:
        map_updates.propose(store, repo, patch(active, patch_id="retry-other", index=1))
    assert caught.value.details["record_id"] in {"compiler", "bounded-context"}
    assert selected(active)["validated_edges"] == []


def test_multi_record_graph_failure_rolls_back_every_write_and_receipt(active):
    store, repo, _, _, _, _, _, _, _ = active
    before = store.db.execute("SELECT revision FROM ledger_meta").fetchone()[0]
    added = record("new-component", edges=[{"kind": "depends_on", "to": "absent", "status": "candidate", "evidence": {}}])
    body = patch(active)
    body["operations"].append({"op": "put", "record": added})
    body["read_versions"].update({"new-component": 0, "absent": 0})
    with pytest.raises(ValidationError, match="missing endpoint"):
        map_updates.propose(store, repo, body)
    assert selected(active)["record"]["version"] == 1
    assert store.db.execute("SELECT count(*) FROM map_patches").fetchone()[0] == 0
    assert store.db.execute("SELECT revision FROM ledger_meta").fetchone()[0] == before


def test_related_new_nodes_can_be_created_together(active):
    store, repo, _, _, _, _, _, _, _ = active
    a = record("a", edges=[{"kind": "depends_on", "to": "b", "status": "candidate", "evidence": {}}])
    b = record("b")
    result = map_updates.propose(store, repo, patch(active, nodes=[a, b], reads={"a": 0, "b": 0}))
    assert set(result["records"]) == {"a", "b"}
    assert selected(active, "a")["record"]["edges"][0]["to"] == "b"


def test_incoming_relationships_validate_against_changed_endpoint_kind(active):
    store, repo, _, _, behavior, _, _, _, _ = active
    invalid_target = record("bounded-context", version=2)
    with pytest.raises(ValidationError, match="invalid typed"):
        map_updates.propose(store, repo, patch(active, nodes=[invalid_target], reads={"bounded-context": 1}))
    assert selected(active, "bounded-context")["record"] == behavior


def test_invalidating_endpoint_marks_incident_edge_stale_and_allows_explicit_revalidation(active):
    store, repo, _, component, behavior, _, _, _, _ = active
    invalidation = patch(active, reads={"bounded-context": 1}, operations=[{"op": "invalidate", "id": "bounded-context", "reason": "The requirement is obsolete."}])
    map_updates.propose(store, repo, invalidation)
    assert selected(active, "bounded-context")["applicability"] == "stale"
    assert selected(active)["record"]["edges"][0]["status"] == "stale"
    restored = copy.deepcopy(behavior)
    restored["version"] = 3
    source = copy.deepcopy(component)
    source["version"] = 2
    body = patch(active, patch_id="revalidate", nodes=[restored, source], reads={"bounded-context": 2, "compiler": 1})
    map_updates.propose(store, repo, body)
    assert selected(active)["validated_edges"]


def test_proposals_never_mutate_committed_baseline(active):
    store, repo, _, component, _, baseline, _, _, _ = active
    map_updates.propose(store, repo, patch(active))
    assert selected(active, snapshot=baseline)["record"] == component
    body = patch(active, patch_id="baseline-write")
    body["snapshot"] = baseline
    with pytest.raises(Conflict, match="overlay"):
        map_updates.propose(store, repo, body)


def test_overlay_is_bound_to_worktree_branch_and_head(active, tmp_path):
    store, repo, _, _, _, baseline, overlay, _, _ = active
    other = tmp_path / "other"
    git(repo, "worktree", "add", "-qb", "other", str(other))
    with pytest.raises(Conflict, match="different worktree"):
        appmap.get_record(store, other, overlay, "compiler")
    independent = map_updates.create_overlay(store, other, baseline)
    assert independent["snapshot"] != overlay
    map_updates.propose(store, repo, patch(active))
    assert appmap.get_record(store, other, independent["snapshot"], "compiler")["record"]["version"] == 1
    git(repo, "checkout", "-qb", "switched")
    with pytest.raises(Conflict, match="different worktree"):
        selected(active)
    replacement = map_updates.create_overlay(store, repo, baseline)["snapshot"]
    (repo / "new.txt").write_text("new commit")
    commit(repo)
    with pytest.raises(Conflict, match="different worktree"):
        selected(active, snapshot=replacement)


def test_patch_replay_is_stable_after_run_closes_but_id_reuse_is_rejected(active):
    store, repo, _, _, _, _, _, runs, _ = active
    body = patch(active)
    result = map_updates.propose(store, repo, body)
    with store.transaction() as tx:
        tx.finish_run(runs[0], "done")
    assert map_updates.propose(store, repo, body) == result
    body["operations"][0]["record"]["summary"] = "Changed request."
    with pytest.raises(Conflict, match="different content"):
        map_updates.propose(store, repo, body)
    body["patch_id"] = "new-patch"
    with pytest.raises(Conflict, match="active run"):
        map_updates.propose(store, repo, body)


def test_source_change_during_preflight_rolls_back(active, monkeypatch):
    store, repo, _, _, _, _, _, _, _ = active
    original = map_updates._preflight
    def change(*args, **kwargs):
        result = original(*args, **kwargs)
        (repo / "compiler.py").write_text("class Compiler:\n    def build(self):\n        return 'changed'\n")
        return result
    monkeypatch.setattr(map_updates, "_preflight", change)
    with pytest.raises(Conflict, match="source changed"):
        map_updates.propose(store, repo, patch(active))
    assert selected(active)["record"]["version"] == 1


def test_dirty_source_can_be_updated_with_current_evidence(active):
    store, repo, _, component, _, _, _, _, _ = active
    (repo / "compiler.py").write_text("class Compiler:\n    def build(self):\n        return 'new context'\n")
    node = copy.deepcopy(component)
    node.update(version=2, anchors=[appmap.source_anchor(repo, "compiler.py", "Compiler.build")])
    map_updates.propose(store, repo, patch(active, nodes=[node]))
    assert selected(active)["applicability"] == "current"


@pytest.mark.parametrize("fault", ["owner", "evidence", "revision", "workdir", "endpoint_read"])
def test_proposal_rejects_wrong_ownership_evidence_revision_or_read_set(active, fault):
    store, repo, _, _, _, _, _, _, events = active
    body = patch(active)
    worker_id = None
    if fault == "owner":
        worker_id = "worker-1"
    elif fault == "evidence":
        body["evidence_ids"] = [events[1]]
    elif fault in {"revision", "workdir"}:
        with store.transaction() as tx:
            tx.put_task("T0", {"workdir": str(repo if fault == "revision" else repo.parent)}, expected_revision=1)
    else:
        del body["read_versions"]["bounded-context"]
    with pytest.raises((Conflict, ValidationError)):
        map_updates.propose(store, repo, body, worker_id=worker_id)
    assert selected(active)["record"]["version"] == 1


def test_overlay_creation_is_idempotent_and_map_proposal_cli_works(active, run_hx, tmp_path):
    store, repo, _, _, _, baseline, overlay, _, _ = active
    assert map_updates.create_overlay(store, repo, baseline)["snapshot"] == overlay
    body = patch(active)
    path = tmp_path / "patch.json"
    path.write_text(json.dumps(body))
    result = run_hx("map", "propose", "--repo", str(repo), "--file", str(path), "--root", str(store.root))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["records"]["compiler"]["version"] == 2


def test_schema_six_upgrade_backfills_relation_index(active):
    store, repo, info, _, _, baseline, _, _, _ = active
    store.db.execute("DELETE FROM map_relations")
    store.db.execute("PRAGMA user_version=6")
    with ContinuityStore(store.root) as upgraded:
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert appmap.get_record(upgraded, repo, baseline, "compiler")["validated_edges"]


def test_unchanged_proposal_sources_use_cache_instead_of_file_reads(active, monkeypatch):
    store, repo, _, _, _, _, _, _, _ = active
    def unexpected(*args, **kwargs):
        pytest.fail("unchanged proposal reread a source body")
    monkeypatch.setattr(appmap, "source_anchor", unexpected)
    map_updates.propose(store, repo, patch(active))


def test_two_concurrent_identical_writes_advance_one_version(active):
    store, repo, _, _, _, _, _, _, events = active
    def submit(body):
        with ContinuityStore(store.root) as connection:
            return map_updates.propose(connection, repo, body)
    bodies = [patch(active), patch(active, index=1, patch_id="patch-2")]
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, bodies))
    assert [result["records"]["compiler"]["version"] for result in results] == [2, 2]
    assert sum(result["records"]["compiler"]["coalesced"] for result in results) == 1
    assert {row[0] for row in store.db.execute("SELECT event_id FROM map_evidence")} == set(events)


def test_read_dependency_racing_with_preflight_rejects_patch(active, monkeypatch):
    store, repo, _, _, behavior, _, _, _, _ = active
    original = map_updates._preflight
    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        monkeypatch.setattr(map_updates, "_preflight", original)
        changed = copy.deepcopy(behavior)
        changed.update(version=2, summary="A changed obligation.")
        with ContinuityStore(store.root) as contender:
            map_updates.propose(contender, repo, patch(active, index=1, patch_id="contender", nodes=[changed], reads={"bounded-context": 1}))
        return result
    monkeypatch.setattr(map_updates, "_preflight", race)
    with pytest.raises(map_updates.MapConflict):
        map_updates.propose(store, repo, patch(active))
    assert selected(active)["record"]["version"] == 1


def test_line_hint_refresh_preserves_incoming_relationships(active):
    store, repo, _, component, _, _, _, _, _ = active
    consumer = record("consumer", claim="observed", anchors=component["anchors"],
        edges=[{"kind": "depends_on", "to": "compiler", "status": "validated", "evidence": {"kind": "source", "anchors": [0]}}])
    map_updates.propose(store, repo, patch(active, nodes=[consumer], reads={"consumer": 0, "compiler": 1}))
    source = repo / "compiler.py"
    source.write_text("\n\n" + source.read_text())
    refreshed = copy.deepcopy(component)
    refreshed.update(version=2, anchors=[appmap.source_anchor(repo, "compiler.py", "Compiler.build")])
    result = map_updates.propose(store, repo, patch(active, patch_id="moved-lines", nodes=[refreshed]))
    assert result["changed"] == []
    assert selected(active, "consumer")["validated_edges"]


@pytest.mark.parametrize("fault", ["bytes", "reads", "operations", "missing_evidence"])
def test_proposal_has_bounded_inputs(active, fault):
    body = patch(active)
    if fault == "bytes":
        body["operations"][0]["record"]["summary"] = "x" * map_updates.PATCH_BYTES
    elif fault == "reads":
        body["read_versions"].update({f"record-{index}": 0 for index in range(65)})
    elif fault == "operations":
        body["operations"] *= 17
    else:
        body["evidence_ids"] = []
    with pytest.raises(ValidationError):
        map_updates.validate(body)


def test_identical_invalidations_coalesce_without_invalidating_changed_content(active):
    store, repo, _, component, _, _, _, _, events = active
    operation = {"op": "invalidate", "id": "compiler", "reason": "Source evidence is obsolete."}
    first = patch(active, operations=[operation], reads={"compiler": 1})
    second = patch(active, index=1, patch_id="other", operations=[operation], reads={"compiler": 1})
    map_updates.propose(store, repo, first)
    result = map_updates.propose(store, repo, second)
    assert result["records"]["compiler"] == {"version": 2, "coalesced": True}
    assert {row[0] for row in store.db.execute("SELECT event_id FROM map_evidence")} == set(events)
    changed = copy.deepcopy(component)
    changed.update(version=3, summary="The responsibility has changed.")
    map_updates.propose(store, repo, patch(active, patch_id="change", nodes=[changed], reads={"compiler": 2, "bounded-context": 1}))
    map_updates.propose(store, repo, patch(active, patch_id="invalidate-new", operations=[operation], reads={"compiler": 3}))
    second["patch_id"] = "stale-invalidation"
    with pytest.raises(map_updates.MapConflict):
        map_updates.propose(store, repo, second)
