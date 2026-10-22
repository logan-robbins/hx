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
    config['claude-opus-5-5']['max_output_tokens'] = 4096
    path.write_text(json.dumps(config))
    row = prepare(configured)
    env = native_launch.environment(store.root, row, env={})
    assert env['HX_MAX_OUTPUT_TOKENS'] == '4096'
    assert env['CLAUDE_CODE_MAX_OUTPUT_TOKENS'] == '4096'
    with pytest.raises(ValidationError, match='max_output_tokens'):
        validate_models({'m': {'window': 10000, 'threshold': 9000, 'max_output_tokens': 1000}}, 'models.json')


def submitted(configured, monkeypatch):
    """A submitted main conversation whose pane is idle; returns the row and what was pasted."""
    from hx import native_controller
    store, _, run = configured
    row = prepare(configured)
    payload = {**row['payload'], 'pane': {'id': '%1'}, 'session': 'fixture', 'tmux': ['tmux']}
    store.db.execute("UPDATE native_launches SET status='submitted',payload=? WHERE run_id=?", (json.dumps(payload), run))
    monkeypatch.setattr(native_controller, '_owned_pane', lambda row: payload['pane'])
    monkeypatch.setattr(native_controller.goal, 'capture_pane', lambda *args: 'ready')
    monkeypatch.setattr(native_controller.goal, 'pane_is_idle', lambda value: True)
    sent = []
    monkeypatch.setattr(native_controller.goal, 'paste', lambda *args: sent.append(args[1]))
    return native_launch._row(store, run), sent


def usage(store, run, **counts):
    """One main-conversation response's usage, as the observer captures it."""
    with store.transaction() as tx:
        token_controls.captured_usage(tx, run, 'native:main', Event('assistant_message', {}, 'M', counts))


def test_hard_reset_never_waits_on_classification_and_submits_once(configured, monkeypatch):
    from hx import context_packets
    store, _, run = configured
    row, sent = submitted(configured, monkeypatch)
    token_controls.observe(store, row, 'log', {'context_tokens': 999999})
    with store.transaction() as tx:
        tx.append_event(run, 'main', 'pending', 'tool_result', {'text': 'Current result.'})
    result = token_controls.reset_one(store)
    assert result == {'status': 'reset_submitted', 'run_id': run, 'seam': token_controls.HARD}
    assert sent == ['/clear']
    assert token_controls.reset_one(store) is None
    # The unclassified observation rides the forced packet the reset installs.
    assert context_packets.issue(store, run, request_id='after-reset', mode='forced', preview=True)['pending'] == 1


def test_seam_fires_without_a_jev_credential_or_a_companion_pass(configured, monkeypatch, tmp_path):
    from hx import jev, native_companion
    store, _, run = configured
    row, sent = submitted(configured, monkeypatch)
    token_controls.observe(store, row, 'log', {'context_tokens': 999999})
    with store.transaction() as tx:
        tx.append_event(run, 'main', 'pending', 'tool_result', {'text': 'Current result.'})
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    env = {'TYPESAFE_API_KEY_FILE': str(tmp_path / 'absent')}
    monkeypatch.setattr(jev, 'evaluate', lambda *a, **kw: pytest.fail('no credential, no request'))
    monkeypatch.setattr(native_companion, 'execute', lambda *a, **kw: pytest.fail('a seam never waits on a model'))
    with pytest.raises(AdmissionDenied, match='threshold'):
        token_controls.guard(store, row)
    assert token_controls.reset_one(store, env=env)['status'] == 'reset_submitted'
    assert sent == ['/clear']


PROFILE = {'soft_fraction': 0.6, 'cache_ttl_s': 300, 'min_benefit_bytes': 65536}


@pytest.mark.parametrize('state,expected', [
    ({'tokens': 200000}, token_controls.HARD),
    ({'tokens': 150000, 'cache_read': 150000, 'cache_creation': 0, 'at': 1000.0}, token_controls.HOLD),
    ({'tokens': 150000, 'cache_read': 0, 'cache_creation': 150000, 'at': 1000.0}, token_controls.COLD_MISS),
    ({'tokens': 150000, 'cache_read': 150000, 'cache_creation': 0, 'at': 1000.0 - 300}, token_controls.COLD_IDLE),
    ({'tokens': 110000, 'cache_read': 0, 'cache_creation': 110000, 'at': 1000.0}, token_controls.HOLD),
    ({'tokens': 150000, 'floor': 140000, 'cache_read': 0, 'cache_creation': 150000, 'at': 1000.0}, token_controls.HOLD),
])
def test_regime_is_hard_cold_or_hold(state, expected):
    """Hard at the threshold; past the soft fraction, a cold cache with real benefit; else hold."""
    state = {'threshold': 200000, 'floor': 1000, **state}
    assert token_controls.regime(state, now=1000.0, profile=PROFILE, packet_bytes=8192) == expected


def test_an_unmeasured_packet_holds_every_opportunistic_seam():
    state = {'threshold': 200000, 'floor': 1000, 'tokens': 150000, 'cache_read': 0, 'cache_creation': 150000, 'at': 0}
    assert token_controls.regime(state, now=1000.0, profile=PROFILE, packet_bytes=None) == token_controls.HOLD
    assert token_controls.regime({**state, 'tokens': 200000}, now=1000.0, profile=PROFILE, packet_bytes=None) == token_controls.HARD


def test_seam_profile_comes_from_hx_json_and_is_validated(configured):
    store, _, _ = configured
    assert token_controls.seam_profile(store.root) == token_controls.SEAM
    path = store.root / 'config/hx.json'
    data = json.loads(path.read_text()) if path.is_file() else {}
    path.write_text(json.dumps({**data, 'seam': {'cache_ttl_s': 3600}}))
    assert token_controls.seam_profile(store.root)['cache_ttl_s'] == 3600
    for bad in ({'soft_fraction': 0}, {'cache_ttl_s': -1}, {'min_benefit_bytes': 1.5}, {'seam_min_interval_s': 60}):
        path.write_text(json.dumps({**data, 'seam': bad}))
        with pytest.raises(ValidationError, match='seam'):
            token_controls.seam_profile(store.root)


def test_a_warm_cache_holds_the_opportunistic_seam(configured, monkeypatch):
    store, _, run = configured
    row, sent = submitted(configured, monkeypatch)
    threshold = token_controls.limits(store.root, 'eng-001')['threshold']
    usage(store, run, input_tokens=1000)
    usage(store, run, input_tokens=200, cache_read_input_tokens=int(threshold * 0.8), cache_creation_input_tokens=500)
    assert token_controls.reset_one(store) is None and sent == []
    packet = _state(store, 'packet:' + run)
    assert packet['bytes'] > 0, 'the next packet is materialised before any seam asks for it'
    token_controls.guard(store, row)


def test_a_cold_miss_takes_the_opportunistic_seam_without_refusing_tools(configured, monkeypatch):
    store, _, run = configured
    row, sent = submitted(configured, monkeypatch)
    threshold = token_controls.limits(store.root, 'eng-001')['threshold']
    usage(store, run, input_tokens=1000)
    usage(store, run, input_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=int(threshold * 0.8))
    result = token_controls.reset_one(store)
    assert result['seam'] == token_controls.COLD_MISS and sent == ['/clear']
    state = _state(store, 'tokens:' + row['launch_id'])
    assert state['reset'] == 'submitted' and state['seam'] == token_controls.COLD_MISS
    token_controls.guard(store, row)  # Only the hard seam refuses tool calls.
    token_controls.observe(store, row, 'context', {'source': 'clear'})
    assert _state(store, 'tokens:' + row['launch_id'])['last_seam'] == token_controls.COLD_MISS


def test_an_idle_cache_takes_the_opportunistic_seam(configured, monkeypatch):
    store, _, run = configured
    row, sent = submitted(configured, monkeypatch)
    threshold = token_controls.limits(store.root, 'eng-001')['threshold']
    usage(store, run, input_tokens=1000)
    usage(store, run, input_tokens=200, cache_read_input_tokens=int(threshold * 0.8), cache_creation_input_tokens=0)
    assert token_controls.reset_one(store) is None
    key = 'tokens:' + row['launch_id']
    idle = {**_state(store, key), 'at': _state(store, key)['at'] - token_controls.SEAM['cache_ttl_s']}
    store.db.execute('UPDATE runtime_cycles SET payload=? WHERE scope=?', (json.dumps(idle), key))
    assert token_controls.reset_one(store)['seam'] == token_controls.COLD_IDLE and sent == ['/clear']


def test_a_small_benefit_holds_even_when_cold(configured, monkeypatch):
    store, _, run = configured
    row, sent = submitted(configured, monkeypatch)
    threshold = token_controls.limits(store.root, 'eng-001')['threshold']
    path = store.root / 'config/hx.json'
    data = json.loads(path.read_text()) if path.is_file() else {}
    path.write_text(json.dumps({**data, 'seam': {'min_benefit_bytes': threshold * 4}}))
    usage(store, run, input_tokens=1000)
    usage(store, run, input_tokens=200, cache_read_input_tokens=0, cache_creation_input_tokens=int(threshold * 0.8))
    assert token_controls.reset_one(store) is None and sent == []
