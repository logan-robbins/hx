"""Real packet evidence, intent approval, Git CAS and crash reconciliation."""

import json
import subprocess

import pytest

from hx import change_delivery as delivery, planning
from hx.continuity_store import Conflict
from hx.errors import Refused, ValidationError
from .test_map_updates import active
from .test_unit_execution import fleet, source_unit, finish, commit
from .test_planning import plan
from .test_appmap import git, mapped


def covered(fleet, tmp_path):
    store, repo = fleet[:2]
    task = source_unit(repo, "feature", "feature.py")
    planning.apply(store, plan(fleet, [task]))
    from hx import unit_execution
    run = unit_execution.assign(store, "feature", 1, "eng-001")["run_id"]
    base = git(repo, "rev-parse", "HEAD")
    (repo / "feature.py").write_text("def feature():\n    return 1\n")
    commit(repo)
    proof = finish(store, run)
    body = {"id": "feature-change", "run_id": run, "base": base, "head": proof["commit"], "behavior": task["behavior"]}
    for who, role in [("qa-001", "qa-engineer"), ("rel-001", "release-engineer")]:
        path = store.root / "config" / who
        path.mkdir(parents=True)
        (path / "harness.json").write_text(json.dumps({"role": role}))
    bare = tmp_path / "integration.git"
    subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
    git(bare, "fetch", str(repo), base)
    git(bare, "update-ref", "refs/heads/main", base)
    return store, repo, body, bare


def test_intent_preservation_independent_semantic_review_and_exact_snapshot(fleet, tmp_path):
    store, repo, body, bare = covered(fleet, tmp_path)
    wrong = {**body, "behavior": "Do a different task"}
    with pytest.raises(Conflict, match="intent"):
        delivery.submit(store, wrong)
    change = delivery.submit(store, body, env={"HARNESS_ID": "eng-001"})
    assert change["payload"]["kind"] == "interface"
    assert delivery.submit(store, body)["revision"] == 1
    with pytest.raises(Refused, match="own change"):
        delivery.review(store, body["id"], decision="approve", expected_revision=1, reason="Good", env={"HARNESS_ID": "eng-001"})
    with pytest.raises(Conflict, match="approval"):
        delivery.land(store, body["id"], repository=bare, expected_head=body["base"])
    delivery.review(store, body["id"], decision="approve", expected_revision=1, reason="Behavior and contract are covered", env={"HARNESS_ID": "qa-001"})
    with pytest.raises(Conflict, match="release approval"):
        delivery.land(store, body["id"], repository=bare, expected_head=body["base"])
    delivery.review(store, body["id"], decision="approve", expected_revision=1, reason="Public contract is compatible", env={"HARNESS_ID": "rel-001"})
    result = delivery.land(store, body["id"], repository=bare, expected_head=body["base"])
    assert result["integration"] == "pending" and not result["deployable"]
    assert git(bare, "show", result["head"] + ":feature.py").startswith("def feature")
    assert delivery.status(store, change_id=body["id"])["status"] == "landed"
    with pytest.raises(Conflict, match="different source"):
        delivery.validate(store, result["batch"], body["run_id"])


def test_stale_head_does_not_publish_and_changed_evidence_cannot_land(fleet, tmp_path):
    store, repo, body, bare = covered(fleet, tmp_path)
    delivery.submit(store, body)
    delivery.review(store, body["id"], decision="approve", expected_revision=1, reason="Reviewed")
    git(bare, "fetch", str(repo), body["head"])
    git(bare, "update-ref", "refs/heads/main", body["head"], body["base"])
    with pytest.raises(Conflict, match="head advanced"):
        delivery.land(store, body["id"], repository=bare, expected_head=body["base"])
    git(bare, "update-ref", "refs/heads/main", body["base"], body["head"])
    receipt = store.db.execute("SELECT receipt_id FROM receipts WHERE run_id=?", (body["run_id"],)).fetchone()[0]
    store.db.execute("UPDATE receipts SET valid=0 WHERE receipt_id=?", (receipt,))
    with pytest.raises(Conflict, match="evidence"):
        delivery.land(store, body["id"], repository=bare, expected_head=body["base"])


def test_crash_after_ref_publication_recovers_without_remerging(fleet, tmp_path, monkeypatch):
    store, repo, body, bare = covered(fleet, tmp_path)
    delivery.submit(store, body)
    delivery.review(store, body["id"], decision="approve", expected_revision=1, reason="Reviewed")
    original = delivery._event
    def interrupted(tx, kind, identity, payload):
        if kind == "change_landed":
            raise OSError("crash after compare-and-swap")
        return original(tx, kind, identity, payload)
    with monkeypatch.context() as m:
        m.setattr(delivery, "_event", interrupted)
        with pytest.raises(OSError):
            delivery.land(store, body["id"], repository=bare, expected_head=body["base"])
    batch = store.db.execute("SELECT batch_id FROM delivery_batches WHERE status='prepared'").fetchone()[0]
    head = git(bare, "rev-parse", "refs/heads/main")
    assert delivery.reconcile(store, batch)["landed"]
    assert delivery.reconcile(store, batch)["landed"]
    assert git(bare, "rev-parse", "refs/heads/main") == head
    assert delivery.status(store)["candidates"][0]["status"] == "pending"


def test_landing_requires_bare_control_repo_and_current_review(fleet, tmp_path):
    store, repo, body, bare = covered(fleet, tmp_path)
    delivery.submit(store, body)
    with pytest.raises(ValidationError, match="bare"):
        delivery.land(store, body["id"], repository=repo, expected_head=body["base"])
    with pytest.raises(Conflict, match="revision"):
        delivery.review(store, body["id"], decision="approve", expected_revision=2, reason="Wrong revision")
