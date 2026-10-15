"""Reviewed rules enter the next fresh native context without mutating old input."""
import json
from pathlib import Path

import pytest

from hx import learn, native_controller, native_launch, native_restart, token_controls
from hx.application_loop import _state
from hx.continuity_store import Conflict, canonical
from .test_appmap import mapped
from .test_map_updates import active
from .test_unit_execution import fleet
from .test_context_packets import assignment
from .test_native_launch import configured, prepare


def accept_rule(root):
    proposal = learn.propose(root, signal='Reviewed source changed during the active native context.',
        check='Keep the FINAL exact artifact assertion at the next boundary.',
        evidence='Current reviewed observation.', category='delivery', env={'HARNESS_ID': 'eng-002'})
    learn.accept(root, proposal['id'], env={'HARNESS_ID': 'partner'})


def active_row(configured, monkeypatch):
    store, _, run = configured
    row = prepare(configured)
    payload = {**row['payload'], 'pane': {'id': '%1'}, 'session': 'fixture', 'tmux': ['tmux'],
               'native_session': 'original-session', 'submission_hash': 'original-input-hash'}
    store.db.execute("UPDATE native_launches SET status='submitted',payload=? WHERE run_id=?", (canonical(payload), run))
    monkeypatch.setattr(native_controller, '_owned_pane', lambda row: payload['pane'])
    monkeypatch.setattr(native_controller.goal, 'capture_pane', lambda *args: 'ready')
    monkeypatch.setattr(native_controller.goal, 'pane_is_idle', lambda value: True)
    return native_launch._row(store, run)


def test_threshold_queues_one_fresh_prefix_and_restarts_with_current_full_rule(configured, monkeypatch):
    store, _, run = configured
    row = active_row(configured, monkeypatch)
    prefix = Path(row['payload']['capsule']) / 'run/eng-001/persona.md'
    old = prefix.read_bytes()
    accept_rule(store.root)
    token_controls.observe(store, row, 'log', {'context_tokens': 999999})
    sent = []
    monkeypatch.setattr(native_controller.goal, 'paste', lambda *args: sent.append(args[1]))
    assert token_controls.reset_one(store)['status'] == 'instruction_restart_queued'
    queued = native_launch._row(store, run)
    assert queued['status'] == 'continuation_required'
    assert prefix.read_bytes() == old and b'FINAL exact artifact assertion' not in old
    intent = _state(store, 'instructions:' + run)
    assert intent['status'] == 'pending_restart'
    assert native_restart.request_instruction_refresh(store, queued)
    assert _state(store, 'instructions:' + run) == intent
    assert not sent and token_controls.reset_one(store) is None
    with pytest.raises(Conflict, match='controlled restart'):
        native_controller.observe(store, run, row['launch_id'], 'request',
            {'session_id': 'original-session', 'prompt': 'Unacknowledged replacement.'})
    monkeypatch.setattr(native_restart, 'drained', lambda *args: None)
    def stopped(store, worker, run_id, request_id, **kwargs):
        current = native_launch._row(store, run_id)
        payload = {**current['payload'], 'shutdown': {'processes': []}}
        store.db.execute("UPDATE native_launches SET status='stopped_unreconciled',payload=? WHERE launch_id=?",
                         (canonical(payload), current['launch_id']))
    monkeypatch.setattr(native_restart.native_shutdown, 'advance', stopped)
    def acknowledged(store, worker, run_id, request_id, **kwargs):
        current = native_launch._row(store, run_id)
        # The native transport/complete acknowledgement has independent host
        # integration tests; retain actual fresh preparation in this restart test.
        store.db.execute("UPDATE native_launches SET status='submitted' WHERE launch_id=?", (current['launch_id'],))
        return {'status': 'submitted'}
    monkeypatch.setattr(native_controller, 'advance', acknowledged)
    assert native_restart.advance_one(store, env={})['status'] == 'restart_submitted'
    fresh = native_launch._row(store, run)
    assert fresh['launch_id'] != row['launch_id']
    assert fresh['payload']['resume_from'] == row['launch_id']
    assert fresh['payload']['prompt_version'] != row['payload']['prompt_version']
    new_prefix = Path(fresh['payload']['capsule']) / 'run/eng-001/persona.md'
    assert b'FINAL exact artifact assertion' in new_prefix.read_bytes() + Path(fresh['payload']['packet_path']).read_bytes()
    assert prefix.read_bytes() == old
    assert _state(store, 'instructions:' + run)['status'] == 'loaded'
    assert native_restart.advance_one(store, env={}) is None
    assert store.db.execute('SELECT count(*) FROM native_launches').fetchone()[0] == 2


def test_prepared_continuation_superseded_before_submission_does_not_starve_companion(configured, monkeypatch):
    store, _, run = configured
    row = active_row(configured, monkeypatch)
    payload = {**row['payload'], 'continuation_input': {'phase': 'prepared'}}
    store.db.execute("UPDATE native_launches SET status='continuation_required',payload=? WHERE launch_id=?",
                     (canonical(payload), row['launch_id']))
    accept_rule(store.root)
    assert token_controls.reset_one(store)['status'] == 'instruction_restart_queued'
    assert 'continuation_input' not in native_launch._row(store, run)['payload']
    assert token_controls.reset_one(store) is None
