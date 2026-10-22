"""Whole source evidence reaches the Messages API exactly, as one tool result."""
import json

import pytest

from hx import companion_protocol, learning_companion, learning_protocol, native_companion
from hx.continuity_store import canonical
from .companion_http import replay
from .test_source_evidence import capture_source
from .test_native_companion import planned
from .test_passes import answer


@pytest.mark.parametrize('purpose', ['context', 'learning'])
def test_recorded_exchange_preserves_whole_source_over_fifty_thousand_characters(planned, purpose):
    store, run, _ = planned
    path = store.root / 'config/eng-001/harness.json'
    config = json.loads(path.read_text())
    config['learning'] = {**config.get('learning', {}), 'request_bytes': 131072}
    path.write_text(json.dumps(config))
    # Dense punctuation exercises JSON escaping; Unicode and the tail must survive intact.
    source = ('[]{}(),;:=+-*/!?\\\"' * 2800) + '\n' + '🦉界' * 500 + '\nFINAL_COMPLETE_SOURCE_DO_NOT_DROP_NEGATION\n'
    event, receipt = capture_source(planned, source)
    if purpose == 'context':
        job = native_companion.prepare(store, 'eng-001', run, 'main', 'complete-source')
        submit = ('submit-patch', {'patch': answer(companion_protocol.frozen(store, job), disposition='no_change')})
        execute = native_companion.execute
    else:
        job = learning_companion.prepare(store, 'eng-001', 'main', 'complete-source', run_id=run)
        body = job['payload']['body']
        submit = ('submit-assessment', {'assessment': {'schema_version': 1, 'event_digest': body['event_digest'],
                                                       'proposals': [], 'pending_event_ids': []}})
        execute = learning_companion.execute
    with replay('read-evidence', submit, read={'event_id': event['event_id']}) as sent:
        result = execute(store, job['job_id'])
    assert result['status'] == 'committed', result
    assert len(sent) == 2
    (reply,) = sent[1]['body']['messages'][2]['content']
    assert reply['type'] == 'tool_result' and 'is_error' not in reply
    delivered = json.loads(reply['content'])
    assert delivered['content'] == source and delivered['receipt'] == receipt and delivered['complete']
    assert len(reply['content']) > 50000


@pytest.mark.parametrize('purpose', ['context', 'learning'])
def test_result_ceiling_refuses_whole_source_before_evidence_credit(planned, monkeypatch, purpose):
    from hx import evidence
    from hx.errors import ValidationError
    store, run, _ = planned
    event, receipt = capture_source(planned, '🦉' * 20 + '\nFINAL whole source\n')
    if purpose == 'context':
        job = native_companion.prepare(store, 'eng-001', run, 'main', 'result-ceiling')
        module = companion_protocol
        result = evidence.read(store, event['event_id'])
        table = 'companion_jobs'
    else:
        job = learning_companion.prepare(store, 'eng-001', 'main', 'result-ceiling', run_id=run)
        module = learning_protocol
        result = {'event_id': event['event_id'], 'content': '🦉' * 20 + '\nFINAL whole source\n', 'receipt': receipt, 'complete': True}
        table = 'learning_jobs'
    # One character over the ceiling refuses the whole result; no prefix is delivered or credited.
    monkeypatch.setattr(module, 'MAX_RESULT_CHARS', len(canonical(result)) - 1)
    store.db.execute(f"UPDATE {table} SET status='running' WHERE job_id=?", (job['job_id'],))
    with pytest.raises(ValidationError, match='tool result ceiling'):
        module.call(store, job['job_id'], 'read_evidence', {'event_id': event['event_id']})
    if purpose == 'context':
        assert not companion_protocol.row(store, job['job_id'])['payload'].get('source_reads')
    else:
        assert event['event_id'] not in learning_companion.row(store, job['job_id'])['payload']['reads']
