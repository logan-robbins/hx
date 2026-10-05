import json

import pytest
from hx import native_launch, token_controls
from hx.application_loop import _state
from hx.events import Event
from hx.native_tools import AdmissionDenied
from hx.config_models import validate_models
from hx.errors import ValidationError
from .test_appmap import mapped
from .test_map_updates import active
from .test_unit_execution import fleet
from .test_context_packets import assignment
from .test_native_launch import configured, prepare


def test_incremental_usage_blocks_then_verified_reset_reopens(configured):
    store, _, run = configured
    row = prepare(configured)
    maximum = token_controls.limits(store.root, 'eng-001')
    with store.transaction() as tx:
        token_controls.captured_usage(tx, run, 'native:main', Event('assistant_message', {}, 'M',
            {'input_tokens': 12, 'cache_read_input_tokens': maximum['threshold']}))
    with pytest.raises(AdmissionDenied, match='threshold'):
        token_controls.guard(store, row)
    token_controls.observe(store, row, 'context', {'source': 'compact'})
    token_controls.guard(store, row)
    assert _state(store, 'tokens:' + row['launch_id'])['tokens'] == 0


def test_child_usage_and_cumulative_totals_do_not_trigger_main_reset(configured):
    store, _, run = configured
    row = prepare(configured, 'codex')
    with store.transaction() as tx:
        token_controls.captured_usage(tx, run, 'child:one', Event('boundary', {}, 'C', {'input_tokens': 9999999}))
        token_controls.captured_usage(tx, run, 'native:main', Event('boundary',
            {'usage_scope': 'session_total', 'last_usage': {'input_tokens': 100}}, 'S', {'input_tokens': 9999999}))
    assert _state(store, 'tokens:' + row['launch_id'])['tokens'] == 100
    token_controls.guard(store, row)


def test_model_cap_is_validated_and_carried_to_private_environment(configured):
    store, _, _ = configured
    path = store.root / 'config/models.json'
    config = json.loads(path.read_text())
    config['claude-opus-5']['max_output_tokens'] = 4096
    path.write_text(json.dumps(config))
    row = prepare(configured)
    env = native_launch.environment(store.root, row, env={})
    assert env['HX_MAX_OUTPUT_TOKENS'] == '4096'
    assert env['CLAUDE_CODE_MAX_OUTPUT_TOKENS'] == '4096'
    with pytest.raises(ValidationError, match='max_output_tokens'):
        validate_models({'m': {'window': 10000, 'threshold': 9000, 'max_output_tokens': 1000}}, 'models.json')


def test_reset_waits_for_classification_and_submits_once(configured, monkeypatch):
    from hx import application_loop, native_controller, context_packets, token_controls
    store, _, run = configured
    row = prepare(configured)
    payload = {**row['payload'], 'pane': {'id': '%1'}, 'session': 'fixture', 'tmux': ['tmux']}
    store.db.execute("UPDATE native_launches SET status='submitted',payload=? WHERE run_id=?", (json.dumps(payload), run))
    token_controls.observe(store, row, 'log', {'context_tokens': 999999})
    with store.transaction() as tx:
        event = tx.append_event(run, 'main', 'pending', 'tool_result', {'text': 'Current result.'})
    monkeypatch.setattr(native_controller, '_owned_pane', lambda row: payload['pane'])
    monkeypatch.setattr(native_controller.goal, 'capture_pane', lambda *args: 'ready')
    monkeypatch.setattr(native_controller.goal, 'pane_is_idle', lambda value: True)
    sent = []
    monkeypatch.setattr(native_controller.goal, 'paste', lambda *args: sent.append(args[1]))
    assert token_controls.reset_one(store) is None
    with store.transaction() as tx:
        cursor = tx.db.execute('SELECT * FROM cursors WHERE run_id=? AND stream_id=?', (run, 'main')).fetchone()
        tx.classify(run, 'main', expected_revision=cursor['revision'], through=cursor['head_seq'], dispositions={i: 'reduced' for i in range(cursor['classified_seq'] + 1, cursor['head_seq'] + 1)})
    assert token_controls.reset_one(store)['status'] == 'reset_submitted'
    assert sent == ['/clear']
    assert token_controls.reset_one(store) is None
