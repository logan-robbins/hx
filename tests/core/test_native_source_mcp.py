"""Installed Claude must retain whole MCP source evidence past host defaults."""
import json
import os

import pytest

from hx import companion_protocol, learning_companion, learning_protocol, native_companion
from hx.continuity_store import canonical
from .claude_companion_fixture import transport
from .test_source_evidence import capture_source
from .test_native_companion import planned
from .test_passes import answer


@pytest.mark.skipif(not os.environ.get('HX_CLAUDE_TEST_BIN'), reason='requires explicitly selected installed Claude; loopback provider only')
@pytest.mark.parametrize('purpose', ['context', 'learning'])
def test_installed_claude_preserves_whole_mcp_source_over_fifty_thousand_characters(planned, purpose):
    store, run, _ = planned
    (store.root / 'config/claude.json').write_text(json.dumps({'bin': os.environ['HX_CLAUDE_TEST_BIN']}))
    (store.root / 'seed/token').write_text('sk-ant-api-fixture-local-only')
    path = store.root / 'config/eng-001/harness.json'
    config = json.loads(path.read_text())
    config['learning'] = {**config.get('learning', {}), 'request_bytes': 131072}
    path.write_text(json.dumps(config))
    # A legacy oversized source remains a single complete historical read. Dense
    # punctuation exercises JSON escaping; Unicode and tail must survive intact.
    source = ('[]{}(),;:=+-*/!?\\\"' * 2800) + '\n' + '🦉界' * 500 + '\nFINAL_COMPLETE_MCP_SOURCE_DO_NOT_DROP_NEGATION\n'
    event, receipt = capture_source(planned, source)
    if purpose == 'context':
        job = native_companion.prepare(store, 'eng-001', run, 'main', 'installed-complete-source')
        frozen = companion_protocol.frozen(store, job)
        response = answer(frozen, disposition='no_change')
        submit_tool = 'submit_patch'
    else:
        job = learning_companion.prepare(store, 'eng-001', 'main', 'installed-complete-source', run_id=run)
        frozen = job['payload']['body']
        response = {'schema_version': 1, 'event_digest': frozen['event_digest'], 'proposals': [], 'pending_event_ids': []}
        submit_tool = 'submit_assessment'
    blocks = [{'type': 'tool_use', 'id': 'read_source', 'name': 'mcp__continuity__read_evidence',
               'input': {'event_id': event['event_id']}},
              {'type': 'tool_use', 'id': 'submit_result', 'name': 'mcp__continuity__' + submit_tool,
               'input': {'response': response}},
              {'type': 'text', 'text': 'Complete source verified.'}]
    with transport(frozen, response, blocks=blocks) as (url, requests):
        execute = native_companion.execute if purpose == 'context' else learning_companion.execute
        result = execute(store, job['job_id'], env={'PATH': os.environ['PATH'], 'ANTHROPIC_BASE_URL': url})
    assert result['status'] == 'committed', result
    assert len(requests) >= 2
    messages = requests[1]['messages']
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
    decoded = []
    texts = list(strings(messages))
    for text in texts:
        try:
            body = json.loads(text)
        except ValueError:
            continue
        if isinstance(body, dict) and body.get('event_id') == event['event_id']:
            decoded.append(body)
    assert any(body.get('content') == source and body.get('receipt') == receipt and body.get('complete') for body in decoded), (
        'Native MCP response did not preserve exact source; received text lengths=' + str(sorted(map(len, texts), reverse=True)[:8]))
    assert any(len(canonical(body)) > 50000 for body in decoded)
    # Native Claude consumes this metadata locally, then strips it from the
    # provider tool schema. The full second request above proves its effect.
    declarations = companion_protocol.TOOLS if purpose == 'context' else learning_protocol.TOOLS
    assert declarations[0]['_meta']['anthropic/maxResultSizeChars'] == 500000


@pytest.mark.parametrize('purpose', ['context', 'learning'])
def test_complete_mcp_ceiling_counts_native_utf16_units_before_evidence_credit(planned, monkeypatch, purpose):
    from hx import evidence
    from hx.errors import ValidationError
    store, run, _ = planned
    event, receipt = capture_source(planned, '🦉' * 20 + '\nFINAL whole source\n')
    if purpose == 'context':
        job = native_companion.prepare(store, 'eng-001', run, 'main', 'native-character-ceiling')
        module = companion_protocol
        result = evidence.read(store, event['event_id'])
        table = 'companion_jobs'
    else:
        job = learning_companion.prepare(store, 'eng-001', 'main', 'native-character-ceiling', run_id=run)
        module = learning_protocol
        result = {'event_id': event['event_id'], 'content': '🦉' * 20 + '\nFINAL whole source\n', 'receipt': receipt, 'complete': True}
        table = 'learning_jobs'
    # Python counts each owl once; the native JavaScript client counts its two
    # UTF-16 units. Refuse the whole result before granting any source proof.
    monkeypatch.setattr(module, 'MCP_MAX_RESULT_CHARS', len(canonical(result)))
    store.db.execute(f"UPDATE {table} SET status='running' WHERE job_id=?", (job['job_id'],))
    with pytest.raises(ValidationError, match='MCP result ceiling'):
        module.call(store, job['job_id'], 'read_evidence', {'event_id': event['event_id']})
    if purpose == 'context':
        assert not companion_protocol.row(store, job['job_id'])['payload']['source_reads']
    else:
        assert event['event_id'] not in learning_companion.row(store, job['job_id'])['payload']['reads']
