import json

import pytest

from hx import jev, native_launch, tool_catalog
from hx.continuity_store import Conflict
from hx.errors import ValidationError
from .test_appmap import mapped
from .test_map_updates import active
from .test_context_packets import assignment
from .test_unit_execution import fleet
from .test_native_launch import configured, prepare


def test_semantic_discovery_is_cached_but_exact_name_needs_no_model(configured, monkeypatch):
    store, _, run = configured
    calls = []
    monkeypatch.setattr(jev, 'credential', lambda *a, **kw: 'fixture-key')
    def judge(state, questions, **kw):
        calls.append(state)
        return {'model': jev.MODEL, 'answers': {name: {'type': 'noul', 'noul': .95 if name == 'WebSearch' else .01} for name in questions}}
    monkeypatch.setattr(jev, 'evaluate', judge)
    result = tool_catalog.discover(store, run, 'Find the latest official API documentation.')
    assert [item['id'] for item in result['suggested']] == ['WebSearch']
    assert tool_catalog.discover(store, run, 'Find the latest official API documentation.') == result
    assert tool_catalog.discover(store, run, 'NotebookEdit')['suggested'][0]['id'] == 'NotebookEdit'
    assert len(calls) == 1


def test_jev_failure_does_not_substitute_tool_guesses(configured, monkeypatch):
    store, _, run = configured
    monkeypatch.setattr(jev, 'credential', lambda *a, **kw: 'fixture-key')
    def fail(*a, **kw):
        raise jev.Unavailable('timeout')
    monkeypatch.setattr(jev, 'evaluate', fail)
    with pytest.raises(ValidationError, match='Jev decision stopped'):
        tool_catalog.discover(store, run, 'Find documentation.')


def test_launch_loading_keeps_required_tools_and_does_not_claim_live_reload(configured):
    store, _, run = configured
    row = prepare(configured)
    chosen = tool_catalog.load(store, run, ['WebFetch'])
    assert chosen['status'] == 'configured'
    assert set(tool_catalog.CATALOG['claude']['required']).issubset(chosen['selected'])
    native_launch.verify(store, run)
    store.db.execute("UPDATE native_launches SET status='submitted' WHERE run_id=?", (run,))
    more = tool_catalog.load(store, run, ['WebSearch'])
    assert more['status'] == 'pending_restart' and not more['confirmed']
    assert 'WebSearch' not in json.loads(tool_catalog.path(row).read_text())['selected']
    with pytest.raises(ValidationError, match='exact IDs'):
        tool_catalog.load(store, run, ['invented-tool'])


def test_pi_requires_native_ack_and_grok_remains_advisory(configured):
    store, _, run = configured
    row = prepare(configured, 'pi')
    chosen = tool_catalog.load(store, run, ['grep'])
    assert chosen['status'] == 'pending_native' and not chosen['confirmed']
    assert json.loads(tool_catalog.path(row).read_text())['selected'] == chosen['selected']
    with pytest.raises(Conflict, match='visibility differs'):
        tool_catalog.acknowledge(store, run, ['grep'])
    assert tool_catalog.acknowledge(store, run, chosen['selected'])['status'] == 'loaded'
    assert tool_catalog.initial('grok')['mode'] == 'advisory'


def test_tampered_selection_refuses_dispatch(configured):
    store, _, run = configured
    row = prepare(configured)
    tool_catalog.path(row).write_text('{}')
    with pytest.raises(Conflict, match='tool selection changed'):
        native_launch.verify(store, run)


def test_partner_can_select_optional_tools_before_native_launch(configured):
    store, _, run = configured
    selected = tool_catalog.load(store, run, ['WebSearch'], env={'HARNESS_ID': 'eng-001'})
    assert selected['status'] == 'configured'
    row = prepare(configured)
    assert 'WebSearch' in row['payload']['tool_selection']['selected']
    native_launch.verify(store, run)


def test_external_discovery_pages_and_rejects_stale_cursor(configured, monkeypatch):
    from hx import capability_catalog
    from hx.continuity_store import digest
    store, _, run = configured
    path = store.root / 'state/capabilities.json'
    entries = {f'mcp:project:tool{i:02d}': {'purpose': 'Inspect one current project resource.', 'kind': 'mcp'} for i in range(12)}
    path.write_text(json.dumps({'configuration': digest(capability_catalog.config(store.root)), 'version': digest(entries), 'entries': entries}))
    monkeypatch.setattr(jev, 'credential', lambda *a, **kw: 'fixture-key')
    sizes = []
    def judge(state, questions, **kw):
        sizes.append(len(questions))
        return {'model': jev.MODEL, 'answers': {name: {'type': 'noul', 'noul': .7} for name in questions}}
    monkeypatch.setattr(jev, 'evaluate', judge)
    first = tool_catalog.discover(store, run, 'Inspect a current project resource.')
    assert first['cursor'] and sizes == [8]
    second = tool_catalog.discover(store, run, 'Inspect a current project resource.', cursor=first['cursor'])
    assert second['cursor'] is None and sizes == [8, 8]
    assert tool_catalog.discover(store, run, 'mcp:project:tool11')['suggested'][0]['id'] == 'mcp:project:tool11'
    assert sizes == [8, 8]
    body = json.loads(path.read_text())
    body['version'] = 'changed'
    path.write_text(json.dumps(body))
    with pytest.raises(Conflict, match='catalog changed'):
        tool_catalog.discover(store, run, 'Inspect a current project resource.', cursor=first['cursor'])
