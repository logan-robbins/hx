from __future__ import annotations

import copy
import json
import os
import uuid

import pytest

from hx import appmap, checks, map_dependencies, map_refresh, map_updates, passes
from hx.continuity_store import Conflict, ContinuityStore, SCHEMA_VERSION
from hx.errors import ValidationError
from .test_appmap import mapped, record
from .test_checks import definition
from .test_map_updates import active, patch, selected


def reference(active, entity="compiler", version=1):
    return {"repository": active[2]["repo_id"], "snapshot": active[6], "id": entity, "version": version}


@pytest.fixture
def bound(active):
    store, repo, _, _, _, _, _, _, _ = active
    with store.transaction() as tx:
        tx.put_task("bound", {"goal": "Preserve constraints.", "workdir": str(repo), "map_inputs": [reference(active)]}, expected_revision=0)
        run = tx.start_run("bound", 1, "bound-worker")
        event = tx.append_event(run, "main", "observed", "tool_result", {"text": "Compiler preserves current obligations."})
        for name, inputs in (("dependent", {"map": [reference(active)]}), ("independent", {})):
            tx.put_record(name, expected_version=0, task_id="bound", kind="finding",
                payload={"schema_version": 1, "text": "The compiler preserves obligations."}, evidence=[event["event_id"]],
                inputs=inputs, reason="Needed to complete the goal.", expires_when="Source changes.")
        tx.put_record("required", expected_version=0, task_id="bound", kind="constraint",
            payload={"schema_version": 1, "text": "Retain exact commands."}, evidence=[event["event_id"]],
            inputs={}, reason="User requirement.", expires_when="Goal closes.")
    return active, run, {"PATH": os.defpath, "LANG": "C", "PYTHONDONTWRITEBYTECODE": "1"}


def changed_source(active):
    (active[1] / "compiler.py").write_text("class Compiler:\n    def build(self):\n        return 'different'\n")


def queue(active, paths=None):
    return map_refresh.queue_sources(active[0], active[1], active[6], paths or ["compiler.py"])


def drain(active, **kwargs):
    return map_refresh.drain(active[0], active[1], active[6], **kwargs)


def test_source_change_invalidates_only_bound_facts_receipts_and_tasks(bound):
    active, run, env = bound
    store, repo, _, _, _, baseline, _, runs, _ = active
    receipt = checks.run_check(store, run, "unit", recipe=definition(), request_id="proof", env=env)
    unrelated = checks.run_check(store, runs[1], "unit", recipe=definition(), env=env)
    assert receipt["valid"] and unrelated["valid"]
    changed_source(active)
    queue(active)
    assert selected(active)["applicability"] in {"pending", "stale"}
    with pytest.raises(Conflict, match="refresh"):
        with store.transaction() as tx:
            map_dependencies.validate_task(tx, tx.task("bound"))
    result = drain(active)
    assert result["invalidated"] == ["compiler"] and not result["more"]
    with store.transaction() as tx:
        assert tx.record("dependent")["validity"] == "invalid"
        assert tx.record("dependent")["version"] == 2
        assert tx.record("independent")["validity"] == tx.record("required")["validity"] == "current"
    assert store.db.execute("SELECT valid FROM receipts WHERE receipt_id=?", (receipt["receipt_id"],)).fetchone()[0] == 0
    assert store.db.execute("SELECT valid FROM receipts WHERE receipt_id=?", (unrelated["receipt_id"],)).fetchone()[0] == 1
    assert {row[0] for row in store.db.execute("SELECT task_id FROM task_replan_queue")} == {"bound"}
    assert checks.run_check(store, run, "unit", request_id="proof", env=env)["valid"] is False
    assert appmap.get_record(store, repo, baseline, "compiler")["record"]["version"] == 1


def test_semantic_map_change_uses_same_consumer_invalidation_transaction(bound):
    active, _, _ = bound
    map_updates.propose(active[0], active[1], patch(active))
    with active[0].transaction() as tx:
        assert tx.record("dependent")["validity"] == "invalid"
    assert active[0].db.execute("SELECT count(*) FROM task_replan_queue").fetchone()[0] == 1


def test_unchanged_source_notification_preserves_versions_and_proof(bound, monkeypatch):
    active, _, _ = bound
    queue(active)
    monkeypatch.setattr(appmap, "source_anchor", lambda *a, **k: pytest.fail("unchanged source was reread"))
    result = drain(active)
    assert result["refreshed"] == ["compiler"] and result["invalidated"] == []
    assert selected(active)["record"]["version"] == 1
    with active[0].transaction() as tx:
        assert tx.record("dependent")["validity"] == "current"


def test_line_movement_refreshes_location_without_invalidating_semantic_consumers(bound):
    active, _, _ = bound
    source = active[1] / "compiler.py"
    source.write_text("\n\n" + source.read_text())
    queue(active)
    assert drain(active)["invalidated"] == []
    assert selected(active)["record"]["anchors"][0]["line"] == 4
    assert selected(active)["record"]["version"] == 1
    with active[0].transaction() as tx:
        assert tx.record("dependent")["version"] == 1


def test_config_anchor_change_invalidates_recipe_fact(active):
    store, repo, _, component, _, _, _, _, _ = active
    (repo / "build.cfg").write_text("mode=debug\n")
    updated = copy.deepcopy(component)
    updated.update(version=2, anchors=[*component["anchors"], appmap.source_anchor(repo, "build.cfg")])
    map_updates.propose(store, repo, patch(active, nodes=[updated]))
    with store.transaction() as tx:
        tx.put_record("recipe-fact", expected_version=0, task_id="T0", kind="finding",
            payload={"schema_version": 1, "text": "Use the debug build configuration."}, evidence=[active[8][0]],
            inputs={"map": [reference(active, version=2)]}, reason="Build requirement.", expires_when="Build configuration changes.")
    (repo / "build.cfg").write_text("mode=release\n")
    queue(active, ["build.cfg"])
    assert drain(active)["invalidated"] == ["compiler"]
    with store.transaction() as tx:
        assert tx.record("recipe-fact")["validity"] == "invalid"


def test_deleted_source_fails_closed_and_does_not_revive_when_restored(active):
    source = active[1] / "compiler.py"
    original = source.read_bytes()
    source.unlink()
    queue(active)
    assert drain(active)["invalidated"] == ["compiler"]
    source.write_bytes(original)
    queue(active)
    assert drain(active)["invalidated"] == []
    assert selected(active)["applicability"] == "stale"


def test_bounded_queue_survives_reopen_and_does_not_load_unrelated_records(active):
    store, repo, _, component, _, _, overlay, _, _ = active
    nodes = [record(f"component-{index}", claim="observed", anchors=component["anchors"]) for index in range(5)]
    map_updates.propose(store, repo, patch(active, nodes=nodes, reads={node["id"]: 0 for node in nodes}))
    changed_source(active)
    queue(active)
    first = drain(active, limit=2)
    assert len(first["refreshed"]) == 2 and first["more"]
    with ContinuityStore(store.root) as reopened:
        second = map_refresh.drain(reopened, repo, overlay, limit=2)
        third = map_refresh.drain(reopened, repo, overlay, limit=2)
    assert len(second["refreshed"]) == len(third["refreshed"]) == 2
    assert not third["more"]
    assert selected(active, "bounded-context")["record"]["version"] == 1


def test_new_notification_during_refresh_is_not_acknowledged(active, monkeypatch):
    queue(active)
    original = map_refresh._inspect
    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        queue(active)
        return result
    monkeypatch.setattr(map_refresh, "_inspect", race)
    assert drain(active) == {"snapshot": active[6], "refreshed": [], "invalidated": [], "more": True}
    monkeypatch.setattr(map_refresh, "_inspect", original)
    assert not drain(active)["more"]


def test_source_write_during_refresh_defers_commit(active, monkeypatch):
    queue(active)
    original = map_refresh._inspect
    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        changed_source(active)
        return result
    monkeypatch.setattr(map_refresh, "_inspect", race)
    result = drain(active)
    assert result["more"] and not result["refreshed"]
    assert selected(active)["record"]["version"] == 1


def test_failed_consumer_invalidation_rolls_back_source_changes_and_queue(bound, monkeypatch):
    active, _, _ = bound
    changed_source(active)
    queue(active)
    def fail(*args, **kwargs):
        raise RuntimeError("injected failure")
    monkeypatch.setattr(map_refresh, "invalidate_consumers", fail)
    with pytest.raises(RuntimeError):
        drain(active)
    assert selected(active)["record"]["version"] == 1
    assert active[0].db.execute("SELECT count(*) FROM map_refresh_queue").fetchone()[0] == 1


def test_stale_map_fact_is_rejected_even_before_watcher_notification(bound):
    active, run, _ = bound
    changed_source(active)
    with pytest.raises(Conflict, match="source changed"):
        passes.prepare(active[0], run, "main", prompt_version="v1", record_ids=["dependent"])


def test_fact_bound_in_frozen_pass_cannot_commit_after_source_changes(bound):
    active, run, _ = bound
    frozen = passes.prepare(active[0], run, "main", prompt_version="v1", record_ids=["dependent"])
    changed_source(active)
    queue(active)
    drain(active)
    response = {"schema_version": 1, "pass_id": frozen["pass_id"], "event_digest": frozen["event_digest"],
        "task_revision": frozen["task_revision"], "operations": [],
        "dispositions": {event["event_id"]: "no_change" for event in frozen["events"]}}
    with pytest.raises(Conflict):
        passes.commit(active[0], frozen["pass_id"], response)


def test_check_finishing_after_map_change_cannot_produce_valid_proof(bound, monkeypatch):
    active, run, env = bound
    original = checks.execute
    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        map_updates.propose(active[0], active[1], patch(active))
        return result
    monkeypatch.setattr(checks, "execute", race)
    result = checks.run_check(active[0], run, "unit", recipe=definition(), env=env)
    assert not result["valid"] and "assignment_changed_during_check" in result["reasons"]


def test_binding_obligations_cannot_be_made_source_expirable(bound):
    active, _, _ = bound
    with pytest.raises(ValidationError, match="cannot expire"):
        with active[0].transaction() as tx:
            tx.put_record("bad-goal", expected_version=0, task_id="bound", kind="goal",
                payload={"schema_version": 1, "text": "Keep the user's original goal."}, evidence=[],
                inputs={"map": [reference(active)]}, reason="Goal.", expires_when="Source changes.")


def test_explicit_task_revision_can_rebind_after_reconciliation(bound):
    active, run, _ = bound
    store, repo, _, _, _, _, _, _, _ = active
    map_updates.propose(store, repo, patch(active))
    with store.transaction() as tx:
        tx.finish_run(run, "blocked")
        tx.put_task("bound", {"goal": "Preserve constraints.", "workdir": str(repo), "map_inputs": [reference(active, version=2)]}, expected_revision=1)
        tx.start_run("bound", 2, "bound-worker")
        map_dependencies.validate_task(tx, tx.task("bound"))
    assert store.db.execute("SELECT task_revision FROM task_replan_queue WHERE task_id='bound'").fetchone()[0] == 1


def test_schema_seven_upgrade_backfills_source_index_and_cli_drains(active, run_hx):
    store, repo, _, _, _, _, overlay, _, _ = active
    store.db.execute("DELETE FROM map_sources")
    store.db.execute("PRAGMA user_version=7")
    with ContinuityStore(store.root) as upgraded:
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert upgraded.db.execute("SELECT count(*) FROM map_sources").fetchone()[0] == 2
    changed_source(active)
    result = run_hx("map", "refresh", "--repo", str(repo), "--snapshot", overlay, "--path", "compiler.py", "--limit", "1", "--root", str(store.root))
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["invalidated"] == ["compiler"]


def test_dispatch_rejects_obsolete_task_map_inputs(bound):
    active, run, _ = bound
    map_updates.propose(active[0], active[1], patch(active))
    with active[0].transaction() as tx:
        tx.finish_run(run, "blocked")
        with pytest.raises(Conflict, match="changed|stale"):
            tx.start_run("bound", 1, "bound-worker")
    assert active[0].db.execute("SELECT count(*) FROM runs WHERE task_id='bound' AND ended_at IS NULL").fetchone()[0] == 0


def test_invalid_binding_fails_before_writing_task_or_fact_even_if_caller_catches(active):
    store, repo, _, _, _, _, _, _, _ = active
    missing = reference(active, version=99)
    with store.transaction() as tx:
        with pytest.raises(Conflict):
            tx.put_task("invalid-binding", {"workdir": str(repo), "map_inputs": [missing]}, expected_revision=0)
        with pytest.raises(Conflict):
            tx.put_record("invalid-fact", expected_version=0, task_id="T0", kind="finding",
                payload={"schema_version": 1, "text": "Unverified relationship."}, evidence=[], inputs={"map": [missing]},
                reason="Discovery.", expires_when="Source changes.")
        assert tx.task("invalid-binding") is None and tx.record("invalid-fact") is None


def test_task_input_checks_repository_identity_as_well_as_worktree(bound):
    active, _, _ = bound
    manifest = active[1] / ".hx" / "map" / "manifest.json"
    body = json.loads(manifest.read_text())
    body["repo_id"] = str(uuid.uuid4())
    manifest.write_text(json.dumps(body))
    with pytest.raises(Conflict, match="repository identity"):
        with active[0].transaction() as tx:
            map_dependencies.validate_task(tx, tx.task("bound"))
