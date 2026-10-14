"""Scoped check closure, execution reuse, ownership, and provenance regressions."""
from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from hx import check_artifacts, checks, native_processes
from hx.continuity_store import Conflict, ContinuityStore, canonical
from hx.errors import ValidationError
from hx.evidence import read


def recipe(script="print('scoped proof')", complete=True):
    return {"schema_version": 1, "id": "unit", "argv": [sys.executable, "-c", script], "cwd": ".",
            "inputs": [], "source": {"complete": complete, "inputs": ["unit.py"], "dependencies": ["dependency.py"]},
            "environment": {"complete": True, "executables": [], "inputs": [], "external_versions": {}},
            "timeout_s": 3, "max_output_bytes": 10000}


@pytest.fixture
def ledger(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for name in ("unit.py", "dependency.py", "unrelated.py"):
        (repo / name).write_text("original\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    with ContinuityStore(tmp_path / "ledger") as store:
        with store.transaction() as tx:
            tx.put_task("T", {"goal": "Verify scope", "workdir": str(repo)}, expected_revision=0)
            run = tx.start_run("T", 1, "eng-001")
        yield store, run, repo, {"PATH": os.defpath, "LANG": "C", "PYTHONDONTWRITEBYTECODE": "1"}


def next_run(store, run):
    with store.transaction() as tx:
        tx.finish_run(run, "done")
        return tx.start_run("T", 1, "eng-002")


def test_spawn_capture_uses_owned_exit_proof_when_native_probe_races(ledger,monkeypatch):
    store,run,repo,env=ledger
    probe=native_processes.probe
    captured=[]
    def callback(process):
        def race(pid):
            if pid==process.pid:
                process.wait(timeout=3)
                raise Conflict('native process audit token is unavailable')
            return probe(pid)
        monkeypatch.setattr(native_processes,'probe',race)
        captured.append(check_artifacts.owned_child(process))
        execution=store.db.execute("SELECT execution_id FROM check_artifacts WHERE status='running'").fetchone()[0]
        return check_artifacts.spawned(store,execution,process)
    execute=checks.execute
    def boundary(*args,**options):
        options['on_spawn']=callback
        return execute(*args,**options)
    monkeypatch.setattr(checks,'execute',boundary)
    result=checks.run_check(store,run,'unit',recipe=recipe(),env=env)
    assert result['valid'],result
    assert captured[0]['identity'] is None and captured[0]['owned_exit_code']==0 and captured[0]['group_absent']
    payload=json.loads(store.db.execute('SELECT payload FROM receipts WHERE receipt_id=?',(result['receipt_id'],)).fetchone()[0])
    assert payload['execution']['child']==captured[0] and payload['execution']['process_group_absent']
    again=checks.run_check(store,next_run(store,run),'unit',recipe=recipe(),env=env)
    assert again['valid'] and again['reused']


def test_unavailable_live_child_identity_stays_unresolved(tmp_path,monkeypatch):
    process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],start_new_session=True)
    def inaccessible(pid):
        raise Conflict('native process audit token is unavailable')
    monkeypatch.setattr(native_processes,'probe',inaccessible)
    try:
        with pytest.raises(Conflict,match='still live'):
            check_artifacts.owned_child(process)
        assert process.poll() is None
    finally:
        checks._stop(process)


def test_exited_child_with_live_group_cannot_supply_quiescence(tmp_path,monkeypatch):
    process=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],process_group=0)
    sibling=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'],process_group=process.pid)
    process.terminate(); process.wait(timeout=3)
    monkeypatch.setattr(native_processes,'probe',lambda pid:None)
    try:
        with pytest.raises(Conflict,match='group is still present'):
            check_artifacts.owned_child(process)
    finally:
        sibling.kill(); sibling.wait(timeout=3)


@pytest.mark.parametrize('metadata',[pytest.param(b'123\n456\n',id='absent'),pytest.param(b'123\n99999999\n',id='present'),pytest.param(b'incomplete\n',id='malformed'),pytest.param(b'',id='incomplete'),pytest.param(b'1 '*131073,id='bound')])
def test_group_permission_refusal_requires_complete_kernel_absence(metadata,monkeypatch):
    def inaccessible(*args): raise PermissionError('kernel group refusal')
    monkeypatch.setattr(os,'killpg',inaccessible)
    def census(argv,*,stdout,stderr,timeout,check):
        assert argv==['/bin/ps','-axo','pgid='] and timeout<=10
        stdout.write((str(os.getpgrp()).encode()+b'\n' if metadata else b'')+metadata)
        return subprocess.CompletedProcess(argv,0)
    monkeypatch.setattr(subprocess,'run',census)
    if metadata==b'123\n456\n':
        assert not check_artifacts.group_present(99999999)
    else:
        with pytest.raises(Conflict,match='inaccessible|malformed|exceeded|incomplete'):
            check_artifacts.group_present(99999999)


def test_cross_run_execution_reuse_issues_new_receipt_and_event(ledger, monkeypatch):
    store, run, repo, env = ledger
    definition = recipe()
    first = checks.run_check(store, run, "unit", recipe=definition, env=env)
    run2 = next_run(store, run)
    (repo / "unrelated.py").write_text("changed outside closure")
    monkeypatch.setattr(checks, "execute", lambda *a, **kw: pytest.fail("reused execution was rerun"))
    second = checks.run_check(store, run2, "unit", recipe=definition, env=env, request_id="new-run")
    assert second["reused"] and second["receipt_id"] != first["receipt_id"]
    assert second["execution_id"] == first["execution_id"]
    assert second["event_id"] != first["event_id"]
    assert read(store, second["event_id"])["content"] == "scoped proof\n"
    assert checks.current(store, run2, second["receipt_id"], env=env)["current"]
    with pytest.raises(ValidationError, match="another run"):
        checks.current(store, run2, first["receipt_id"], env=env)
    assert checks.run_check(store, run2, "unit", recipe=definition, env=env, request_id="new-run") == second
    assert store.db.execute("SELECT count(*) FROM check_artifacts").fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 2
    assert all(row[0] == "reduced" for row in store.db.execute("SELECT disposition FROM events"))


@pytest.mark.parametrize("path", ["unit.py", "dependency.py"])
def test_declared_transitive_source_changes_invalidate_reuse(ledger, path):
    store, run, repo, env = ledger
    definition = recipe()
    first = checks.run_check(store, run, "unit", recipe=definition, env=env)
    (repo / path).write_text("dependency changed")
    assert not checks.current(store, run, first["receipt_id"], env=env)["current"]
    second = checks.run_check(store, run, "unit", recipe=definition, env=env)
    assert second["valid"] and not second["reused"]


def test_incomplete_closure_preserves_whole_repository_default(ledger):
    store, run, repo, env = ledger
    definition = recipe(complete=False)
    first = checks.run_check(store, run, "unit", recipe=definition, env=env)
    (repo / "unrelated.py").write_text("must invalidate conservative identity")
    second = checks.run_check(store, run, "unit", recipe=definition, env=env)
    assert not second["reused"]
    assert not checks.current(store, run, first["receipt_id"], env=env)["current"]


def test_scoped_write_restore_detected_by_observation_stamps(ledger):
    store, run, _, env = ledger
    definition = recipe("from pathlib import Path; p=Path('dependency.py'); b=p.read_bytes(); p.write_text('temporary'); p.write_bytes(b)")
    result = checks.run_check(store, run, "unit", recipe=definition, env=env)
    assert not result["valid"] and "source_changed_during_check" in result["reasons"]


@pytest.mark.parametrize("change", ["environment", "cwd", "toolchain", "external"])
def test_matching_source_does_not_hide_execution_environment_changes(ledger, change):
    store, run, repo, env = ledger
    definition = recipe()
    tool = repo / "tool"
    tool.write_text("#!/bin/sh\nexit 0\n")
    tool.chmod(0o755)
    external = repo / "version"
    external.write_text("v1")
    definition["environment"]["executables"] = ["tool"]
    definition["environment"]["external_versions"] = {"service": "version"}
    first = checks.run_check(store, run, "unit", recipe=definition, env=env)
    if change == "environment":
        env["OBSERVABLE_FLAG"] = "different"
    elif change == "toolchain":
        tool.write_text("#!/bin/sh\nexit 1\n")
    elif change == "external":
        external.write_text("v2")
    else:
        moved = repo.parent / "moved"
        repo.rename(moved)
        with store.transaction() as tx:
            tx.finish_run(run, "moved")
            tx.put_task("T", {"goal": "Verify scope", "workdir": str(moved)}, expected_revision=1)
            run = tx.start_run("T", 2, "eng-001")
    second = checks.run_check(store, run, "unit", recipe=definition, env=env)
    assert first["valid"] and second["valid"] and not second["reused"]


@pytest.mark.parametrize("change", ["artifact", "provenance"])
def test_corrupt_execution_evidence_cannot_be_reused(ledger, change):
    store, run, _, env = ledger
    definition = recipe()
    first = checks.run_check(store, run, "unit", recipe=definition, env=env)
    if change == "artifact":
        # Fresh execution must repair corrupt bytes from captured output.
        (store.artifacts / first["artifact_hash"]).write_bytes(b"corrupt")
    else:
        row = store.db.execute("SELECT payload FROM check_artifacts").fetchone()
        payload = json.loads(row[0])
        payload["execution"]["exit_code"] = 99
        store.db.execute("UPDATE check_artifacts SET payload=?", (canonical(payload),))
    assert not checks.current(store, run, first["receipt_id"], env=env)["current"]
    fresh = checks.run_check(store, run, "unit", recipe=definition, env=env)
    assert fresh["valid"] and not fresh["reused"]


def test_live_claim_blocks_fresh_execution_and_reconciliation(ledger, monkeypatch):
    store, run, _, env = ledger
    def interrupted(*args, **kwargs):
        raise RuntimeError("interrupted before spawn")
    monkeypatch.setattr(checks, "execute", interrupted)
    with pytest.raises(RuntimeError):
        checks.run_check(store, run, "unit", recipe=recipe(), env=env, request_id="interrupted")
    for allow_reuse in (True, False):
        with pytest.raises(Conflict, match="unresolved execution"):
            checks.run_check(store, run, "unit", recipe=recipe(), env=env, allow_reuse=allow_reuse)
    execution = store.db.execute("SELECT execution_id FROM check_artifacts").fetchone()[0]
    with pytest.raises(Conflict, match="still alive"):
        check_artifacts.reconcile(store, execution)


def test_dead_claim_requires_known_spawn_and_absent_process_group(ledger, monkeypatch):
    store, run, _, env = ledger
    real = checks.execute
    def interrupted(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("receipt commit interrupted")
    monkeypatch.setattr(checks, "execute", interrupted)
    with pytest.raises(RuntimeError):
        checks.run_check(store, run, "unit", recipe=recipe(), env=env)
    execution = store.db.execute("SELECT execution_id FROM check_artifacts").fetchone()[0]
    monkeypatch.setattr(native_processes, "current", lambda identity: None)
    def present_group(*args):
        return None
    monkeypatch.setattr(os, "killpg", present_group)
    with pytest.raises(Conflict, match="group is still present"):
        check_artifacts.reconcile(store, execution)
    def gone_group(*args):
        raise ProcessLookupError
    monkeypatch.setattr(os, "killpg", gone_group)
    assert check_artifacts.reconcile(store, execution)["reconciled"]
    monkeypatch.setattr(checks, "execute", real)
    result = checks.run_check(store, run, "unit", recipe=recipe(), env=env, allow_reuse=False)
    assert result["valid"] and not result["reused"]


@pytest.mark.parametrize("source", [{"complete": 1, "inputs": []}, {"complete": True, "inputs": [None]},
                                    {"complete": True, "inputs": [], "dependencies": "oops"},
                                    {"complete": True, "inputs": [], "unexpected": []}])
def test_explicit_source_contract_rejects_malformed_coverage(source):
    definition = recipe()
    definition["source"] = source
    with pytest.raises(ValidationError, match="source"):
        checks.validate(definition)


def test_request_replay_does_not_claim_valid_when_output_is_corrupt(ledger, monkeypatch):
    store, run, _, env = ledger
    first = checks.run_check(store, run, "unit", recipe=recipe(), env=env, request_id="stable")
    (store.artifacts / first["artifact_hash"]).write_bytes(b"corrupt")
    monkeypatch.setattr(checks, "execute", lambda *a, **kw: pytest.fail("request replay executed again"))
    replay = checks.run_check(store, run, "unit", env=env, request_id="stable")
    assert replay["receipt_id"] == first["receipt_id"] and not replay["valid"]
    assert "output_missing_or_corrupt" in replay["reasons"]


def test_receipt_integrity_is_rechecked_at_final_revalidation_boundary(ledger, monkeypatch):
    store, run, _, env = ledger
    first = checks.run_check(store, run, "unit", recipe=recipe(), env=env)
    original = checks._assignment
    assignments = 0
    def assignment(*args, **kwargs):
        nonlocal assignments
        value = original(*args, **kwargs)
        assignments += 1
        if assignments == 2:
            (store.artifacts / first["artifact_hash"]).unlink()
        return value
    monkeypatch.setattr(checks, "_assignment", assignment)
    result = checks.current(store, run, first["receipt_id"], env=env)
    assert not result["current"]
    assert "receipt_evidence_changed_during_revalidation" in result["reasons"]


def test_dead_owner_with_unknown_spawn_cannot_release_claim(ledger, monkeypatch):
    store, run, _, env = ledger
    def interrupted(*args, **kwargs):
        raise RuntimeError("unknown spawn gap")
    monkeypatch.setattr(checks, "execute", interrupted)
    with pytest.raises(RuntimeError):
        checks.run_check(store, run, "unit", recipe=recipe(), env=env)
    execution = store.db.execute("SELECT execution_id FROM check_artifacts").fetchone()[0]
    monkeypatch.setattr(native_processes, "current", lambda identity: None)
    with pytest.raises(Conflict, match="spawn ownership is unknown"):
        check_artifacts.reconcile(store, execution)
