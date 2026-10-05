from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

from hx import native_launch, native_producer
from hx.continuity_store import Conflict, ContinuityStore, SCHEMA_VERSION
from hx.install import install_skeleton
from .conftest import SRC
from .test_appmap import mapped
from .test_context_packets import assignment
from .test_hook_contract import commands
from .test_map_updates import active
from .test_unit_execution import fleet


@pytest.fixture
def configured(assignment):
    store, repo, run, _ = assignment
    install_skeleton(store.root)
    folder = store.root / "config" / "eng-001"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "harness.json").write_text(json.dumps({"id": "eng-001", "pod": "engineers",
        "role": "backend-engineer", "model": "claude-opus-5", "effort": "high", "workdir": str(repo)}))
    return store, repo, run


def prepare(configured, flavor="claude"):
    store, _, run = configured
    path = store.root / "config" / "eng-001" / "harness.json"
    config = json.loads(path.read_text())
    config["flavor"] = flavor
    path.write_text(json.dumps(config))
    return native_launch.prepare(store, "eng-001", run, "first", env={})


@pytest.mark.parametrize("flavor", ["claude", "codex", "meta", "grok", "pi"])
def test_private_installation_hooks_use_shared_authority(configured, child_env, tmp_path, flavor):
    store, _, run = configured
    # Old histories and personal state must never enter the fresh native home.
    old = store.root / "run" / "eng-001" / "home"
    old.mkdir(parents=True)
    (old / "history.jsonl").write_text("OBSOLETE TASK")
    token = store.root / "seed" / ("token" if flavor == "claude" else flavor + "-token")
    token.write_text("fixture-key")
    token.chmod(0o600)
    login = tmp_path / "user" / ".codex" / "auth.json"
    login.parent.mkdir(parents=True)
    login.write_text('{"fixture":true}')
    # Installation validates a pinned executable; it does not launch a model.
    # Keep this contract test independent of globally installed native CLIs.
    (store.root / 'config' / (flavor + '.json')).write_text(
        json.dumps({'bin': sys.executable, 'version': 'installation-fixture'}))
    launch = prepare(configured, flavor)
    capsule = Path(launch["payload"]["capsule"])
    env = native_launch.environment(store.root, launch, env=child_env(HOME=str(login.parents[1])))
    argv = ["bash", str(capsule / "adapters" / flavor / "install.sh")]
    if flavor == "meta":
        argv.append("--no-companion")
    result = subprocess.run([*argv, "eng-001"], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    home = capsule / "run" / "eng-001" / "home"
    assert not (home / "history.jsonl").exists()
    assert not (home / "skills" / "hx-memory").exists()
    assert not (home.parent / "companion-home").exists()
    assert native_launch.verify(store, run)["launch_id"] == launch["launch_id"]
    command = next(command for command in commands(home, flavor) if command.endswith(" log"))
    result = subprocess.run(shlex.split(command), input=json.dumps({"session_id": "S", "tool_use_id": "C",
        "tool_name": "Bash", "tool_response": "Test callback reached the assigned ledger."}),
        env={"PATH": env["PATH"], "HOME": str(login.parents[1]), "PYTHONPATH": str(SRC)},
        capture_output=True, text=True)
    assert result.returncode == 0 and not result.stderr, result.stderr
    assert not (capsule / "state" / "continuity.sqlite").exists()
    with store.transaction() as tx:
        assert native_producer.status(tx, run)["pending_deliveries"] == 0
        assert tx.db.execute("SELECT count(*) FROM native_bindings WHERE run_id=?", (run,)).fetchone()[0] == 1
    with pytest.raises(Conflict, match="newer execution evidence"):
        native_launch.verify(store, run)
    assert launch["payload"]["charged_initial_tokens"] <= 8000
    assert launch["payload"]["instruction_delivery"] == "prepared"
    assert launch["payload"]["tool_visibility"] == "unverified"


def test_retries_keep_one_capsule_and_different_request_refuses(configured):
    store, _, run = configured
    first = prepare(configured)
    assert native_launch.prepare(store, "eng-001", run, "first", env={}) == first
    with pytest.raises(Conflict, match="different inputs"):
        native_launch.prepare(store, "eng-001", run, "second", env={})
    assert store.db.execute("SELECT count(*) FROM native_launches").fetchone()[0] == 1


@pytest.mark.parametrize("target", ["configuration", "system", "packet"])
def test_altered_prepared_inputs_refuse(configured, target):
    store, _, run = configured
    launch = prepare(configured)
    capsule = Path(launch["payload"]["capsule"])
    path = {"configuration": capsule / "config" / "eng-001" / "harness.json",
            "system": capsule / "config" / "eng-001" / "AGENTS.md",
            "packet": Path(launch["payload"]["packet_path"])}[target]
    path.write_text("Changed after preparation.")
    with pytest.raises(Conflict, match="changed"):
        native_launch.verify(store, run)


def test_failed_preparation_retains_ownership_and_does_not_retry_implicitly(configured, monkeypatch):
    store, _, run = configured
    def fail(*args, **kwargs):
        raise OSError("installation interrupted")
    monkeypatch.setattr(native_launch, "_capsule", fail)
    with pytest.raises(OSError):
        prepare(configured)
    retry = native_launch.prepare(store, "eng-001", run, "first", env={})
    assert retry["status"] == "preparation_failed"
    assert retry["error"] == "OSError"
    assert store.db.execute("SELECT count(*) FROM leases WHERE run_id=?", (run,)).fetchone()[0] > 0
    with pytest.raises(Conflict, match="not ready"):
        native_launch.verify(store, run)


def test_schema_twelve_upgrade_preserves_contracts(configured):
    store, _, run = configured
    launch = prepare(configured)
    store.db.execute("DROP TABLE native_launches")
    store.db.execute("PRAGMA user_version=12")
    with ContinuityStore(store.root) as upgraded:
        assert upgraded.db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert upgraded.db.execute("SELECT run_id FROM native_launch_contracts WHERE launch_id=?",
                                   (launch["launch_id"],)).fetchone()[0] == run


def test_concurrent_same_request_prepares_only_once(configured, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    store, _, run = configured
    entered, proceed = Event(), Event()
    original = native_launch._capsule
    def held(*args, **kwargs):
        entered.set()
        assert proceed.wait(5)
        return original(*args, **kwargs)
    monkeypatch.setattr(native_launch, "_capsule", held)
    def first():
        with ContinuityStore(store.root) as other:
            return native_launch.prepare(other, "eng-001", run, "same", env={})
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(first)
        try:
            assert entered.wait(5)
            retry = native_launch.prepare(store, "eng-001", run, "same", env={})
            assert retry["status"] == "preparing"
        finally:
            proceed.set()
        assert future.result()["launch_id"] == retry["launch_id"]
    assert store.db.execute("SELECT count(*) FROM native_launches").fetchone()[0] == 1


def test_correction_after_preparation_blocks_stale_context(configured):
    store, _, run = configured
    prepare(configured)
    with store.transaction() as tx:
        tx.append_event(run, "main", "correction", "correction", {"text": "Use the new application state."})
    with pytest.raises(Conflict, match="newer execution evidence"):
        native_launch.verify(store, run)
