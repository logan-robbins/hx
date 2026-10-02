from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from hx import native_controller, native_launch, native_processes, native_shutdown, unit_execution
from hx.continuity_store import Conflict, ContinuityStore, canonical
from .conftest import wait_for
from .test_appmap import mapped
from .test_context_packets import assignment
from .test_map_updates import active
from .test_native_launch import configured
from .test_unit_execution import fleet
from .test_native_controller import runtime, ready


@pytest.fixture(autouse=True)
def cleanup_owned_processes(runtime):
    yield
    store, _, run, _ = runtime
    row = native_launch._row(store, run)
    for identity in reversed((row or {}).get('payload', {}).get('shutdown', {}).get('processes', [])):
        native_processes.send(identity, signal.SIGKILL)


@pytest.fixture
def owned_tree(runtime, tmp_path):
    store, _, run, env = runtime
    pidfile = tmp_path / 'owned-child'
    executable = Path(json.loads((store.root / 'config/claude.json').read_text())['bin'])
    source = executable.read_text()
    source = source.replace("fire('context', source='startup')", """child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
pathlib.Path(os.environ['HX_TEST_CHILD_FILE']).write_text(str(child.pid))
fire('context', source='startup')""")
    executable.write_text(source)
    env['HX_TEST_CHILD_FILE'] = str(pidfile)
    row = ready(runtime)
    wait_for(pidfile.exists, what='owned background child', limit=5)
    identity = native_processes.probe(int(pidfile.read_text()))['identity']
    try:
        yield runtime, row, identity
    finally:
        native_processes.send(identity, signal.SIGKILL)


def finish(runtime):
    store, _, run, env = runtime
    statuses = []
    def step():
        result = native_shutdown.advance(store, 'eng-001', run, 'launch', env=env)
        statuses.append(result['status'])
        return result['status'] == 'stopped_unreconciled'
    wait_for(step, what='owned-process shutdown', limit=10)
    return statuses


def test_shutdown_stops_root_and_background_child_but_retains_leases(owned_tree):
    runtime, row, child = owned_tree
    store, _, run, env = runtime
    with subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']) as unrelated:
        try:
            statuses = finish(runtime)
            assert statuses[0] == 'stopping'
            assert native_processes.current(row['payload']['pane']['process']) is None
            assert native_processes.current(child) is None
            assert unrelated.poll() is None
            result = native_shutdown.advance(store, 'eng-001', run, 'launch', env=env)
            assert result['observed_processes'] >= 3 and result['shutdown_phase'] == 'stopped'
            with pytest.raises(Conflict, match='ownership'):
                unit_execution.stop(store, run)
            assert store.db.execute('SELECT count(*) FROM leases WHERE run_id=?', (run,)).fetchone()[0] > 0
            receipt = native_launch._row(store, run)['payload']['shutdown']
            assert receipt['scope'] == 'observed_descendants' and not receipt['coverage_verified']
        finally:
            unrelated.kill()
            unrelated.wait(timeout=5)


def test_interrupt_after_signal_resumes_from_durable_identity(owned_tree, monkeypatch):
    runtime, row, child = owned_tree
    store, _, run, env = runtime
    native_shutdown.advance(store, 'eng-001', run, 'launch', env=env)
    native_shutdown.advance(store, 'eng-001', run, 'launch', env=env)
    send = native_processes.send
    def interrupted(identity, sig):
        send(identity, sig)
        raise OSError('controller interrupted after signal')
    monkeypatch.setattr(native_processes, 'send', interrupted)
    with pytest.raises(OSError, match='interrupted'):
        native_shutdown.advance(store, 'eng-001', run, 'launch', env=env)
    assert native_launch._row(store, run)['payload']['shutdown']['phase'] == 'freezing'
    monkeypatch.setattr(native_processes, 'send', send)
    with ContinuityStore(store.root) as recovered:
        finish((recovered, runtime[1], run, env))
    assert native_processes.current(child) is None


def test_retry_does_not_signal_replacement_session(owned_tree):
    runtime, row, _ = owned_tree
    store, _, run, env = runtime
    finish(runtime)
    session = row['payload']['session']
    cmd = env['HX_TMUX'].split()
    subprocess.run([*cmd, 'new-session', '-d', '-s', session, '-n', 'main', 'sleep', '30'], check=True)
    replacement = int(subprocess.check_output([*cmd, 'display-message', '-p', '-t', '='+session+':main', '#{pane_pid}']))
    identity = native_processes.probe(replacement)['identity']
    assert native_shutdown.advance(store, 'eng-001', run, 'launch', env=env)['status'] == 'stopped_unreconciled'
    assert native_controller.advance(store, 'eng-001', run, 'launch', env=env)['status'] == 'stopped_unreconciled'
    assert native_processes.current(identity) is not None


def test_missing_process_receipt_never_adopts_current_pane(runtime):
    store, _, run, env = runtime
    row = ready(runtime)
    payload = row['payload']
    original = payload['pane'].pop('process')
    store.db.execute('UPDATE native_launches SET payload=? WHERE run_id=?', (canonical(payload), run))
    with pytest.raises(Conflict, match='process-instance receipt'):
        native_shutdown.advance(store, 'eng-001', run, 'launch', env=env)
    assert native_processes.current(original) is not None


def test_late_context_cannot_reopen_stopping_launch(runtime):
    store, _, run, env = runtime
    row = ready(runtime)
    native_shutdown.advance(store, 'eng-001', run, 'launch', env=env)
    assert native_controller.observe(store, run, row['launch_id'], 'context', {'source':'clear'}) is None
    assert native_launch._row(store, run)['status'] == 'stopping'
    with pytest.raises(Conflict, match='closed for shutdown'):
        native_controller.observe(store, run, row['launch_id'], 'request', {'prompt':'continue'})
    finish(runtime)


def test_cli_shutdown_retries_original_launch(runtime, capsys):
    from hx import lifecycle
    store, _, run, env = runtime
    ready(runtime)
    def step():
        assert lifecycle.main_launch(['eng-001','--run',run,'--request','launch','--shutdown'], store.root, env=env) == 0
        return json.loads(capsys.readouterr().out)['status'] == 'stopped_unreconciled'
    wait_for(step, what='CLI native shutdown', limit=10)


@pytest.mark.skipif(not os.environ.get('HX_MUSE_TEST_BIN'), reason='requires explicitly selected installed Muse; echo provider only')
def test_installed_muse_process_shutdown(runtime, tmp_path):
    from .test_native_controller import test_installed_muse_startup_and_request_hooks
    test_installed_muse_startup_and_request_hooks(runtime, tmp_path)
    store, _, run, _ = runtime
    identity = native_launch._row(store, run)['payload']['pane']['process']
    finish(runtime)
    assert native_processes.current(identity) is None


def test_freeze_batch_excludes_other_ledger_writers(runtime, monkeypatch):
    import sqlite3
    store, _, run, env = runtime
    ready(runtime)
    send = native_processes.send
    guarded = []
    def checked(identity, sig):
        if sig == signal.SIGSTOP:
            with sqlite3.connect(store.path, timeout=0, isolation_level=None) as other:
                with pytest.raises(sqlite3.OperationalError, match='locked'):
                    other.execute('BEGIN IMMEDIATE')
            guarded.append(identity)
        return send(identity, sig)
    monkeypatch.setattr(native_processes, 'send', checked)
    finish(runtime)
    assert guarded


def test_two_controllers_resume_the_same_shutdown_without_losing_children(owned_tree):
    from concurrent.futures import ThreadPoolExecutor
    runtime, row, child = owned_tree
    store, repo, run, env = runtime
    def drive():
        with ContinuityStore(store.root) as peer:
            return finish((peer, repo, run, env))
    with ThreadPoolExecutor(max_workers=2) as pool:
        peers = [pool.submit(drive) for _ in range(2)]
        for peer in peers:
            assert peer.result(timeout=15)[-1] == 'stopped_unreconciled'
    receipt = native_launch._row(store, run)['payload']['shutdown']
    assert child in receipt['processes']
    assert native_processes.current(child) is None
