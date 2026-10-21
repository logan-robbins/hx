"""Whole source evidence remains exact across resets and Companion requests."""
import hashlib
import json

import pytest

from hx import companion_protocol as protocol, evidence, native_companion, observer
from hx.continuity_store import canonical, digest
from hx.errors import ValidationError
from hx.events import Event
from .test_native_companion import activate, planned
from .test_passes import answer, create


def capture_source(planned, text, *, actor='', omit_actor=False, response=None):
    store, run, _ = planned
    raw = text.encode()
    receipt = {'path': '/product/source.py', 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
               'files': [{'path': '/product/source.py', 'stamp': 'historical-source-version'}]}
    session = 'child-session' if actor else 'main-session'
    with store.transaction() as tx:
        tx.db.execute('INSERT OR IGNORE INTO native_launch_contracts VALUES(?,?,?,?)', ('source-launch', run, 'eng-001', 'claude'))
        tx.db.execute('INSERT OR IGNORE INTO native_launches VALUES(?,?,?,?,?,?,?)',
                      ('source-launch', run, 'source-request', 'source-hash', '{}', 'submitted', None))
        if actor:
            tx.db.execute('INSERT INTO native_children VALUES(?,?,?,?)', ('source-launch', actor, session, 'active'))
        tx.db.execute('INSERT INTO native_reads VALUES(?,?,?,?,?,?,?,?)',
                      ('source-launch', session, actor, 'source-call', 'checkpoint', 'key', canonical(receipt), 0))
        data = {'tool_name': 'Read', 'tool_input': {'file_path': receipt['path']},
                'tool_use_id': 'source-call', 'tool_response': text if response is None else response}
        if actor and not omit_actor:
            data['agent_id'] = actor
        captured = observer.append_observation(tx, run_id=run, stream_id='main', session_id=session,
            event=Event('tool_result', data, 'tool:source-call'), fallback_identity=('source', run))
    return captured, receipt


@pytest.mark.parametrize('actor,omit_actor', [('', False), ('child', False), ('child', True)])
def test_complete_source_tail_beyond_eight_kib_survives_live_receipt_retirement(planned, actor, omit_actor):
    store, _, _ = planned
    text = '# Whole maintained source\n' + 'x' * 39_800 + '\nFINAL SOURCE TAIL\n'
    event, receipt = capture_source(planned, text, actor=actor, omit_actor=omit_actor)
    with store.transaction() as tx:
        payload = json.loads(tx.db.execute('SELECT payload FROM events WHERE event_id=?', (event['event_id'],)).fetchone()[0])
        assert payload['source_receipt'] == receipt
        tx.db.execute('DELETE FROM native_reads')
    job = native_companion.prepare(store, 'eng-001', event['run_id'], 'main', 'whole-source')
    assert job['payload']['source_read_budget'] > len(text.encode())
    activate(store, job)
    result = protocol.call(store, job['job_id'], 'read_evidence', {'event_id': event['event_id']})
    assert result['complete'] and result['content'] == text
    assert result['receipt'] == receipt and result['next_offset'] is None
    assert result['bytes'] == len(text.encode()) and result['bytes'] > 8192
    assert protocol.row(store, job['job_id'])['payload']['source_read_bytes'] > result['bytes']
    assert evidence.read(store, event['event_id'])['content'] == text


def test_public_and_companion_source_offsets_are_refused_without_delivering_a_prefix(planned, run_hx):
    store, run, _ = planned
    event, _ = capture_source(planned, 'complete\n' * 2000 + 'TAIL\n')
    with pytest.raises(ValidationError, match='prohibit offsets and limits'):
        evidence.read(store, event['event_id'], offset=0, limit=32)
    result = run_hx('evidence', event['event_id'], '--root', str(store.root), '--offset', '1', '--limit', '32')
    assert result.returncode != 0 and result.stdout == '' and 'prohibit offsets' in result.stderr
    job = native_companion.prepare(store, 'eng-001', run, 'main', 'slice-refused')
    activate(store, job)
    with pytest.raises(ValidationError, match='prohibit offsets and limits'):
        protocol.call(store, job['job_id'], 'read_evidence', {'event_id': event['event_id'], 'offset': 0, 'limit': 32})


def test_complete_source_refuses_when_remaining_independent_wire_budget_is_too_small(planned):
    store, run, _ = planned
    event, _ = capture_source(planned, 'x' * 39_900 + '\nTAIL\n')
    job = native_companion.prepare(store, 'eng-001', run, 'main', 'source-overflow')
    activate(store, job)
    with store.transaction() as tx:
        protocol.update(tx, protocol.row(tx, job['job_id']), source_read_budget=2048)
    with pytest.raises(ValidationError, match='no prefix was delivered'):
        protocol.call(store, job['job_id'], 'read_evidence', {'event_id': event['event_id']})
    assert not protocol.row(store, job['job_id'])['payload']['source_reads']


def test_escaped_source_result_charges_its_exact_request_bytes(planned):
    store, run, _ = planned
    event, _ = capture_source(planned, '\x01' * 4000 + '\nTAIL\n')
    job = native_companion.prepare(store, 'eng-001', run, 'main', 'escaped-source')
    activate(store, job)
    result = evidence.read(store, event['event_id'])
    # The tool_result content is canonical(result), itself JSON-escaped in the request body.
    wire = len(canonical(canonical(result)).encode())
    with store.transaction() as tx:
        protocol.update(tx, protocol.row(tx, job['job_id']), source_read_budget=wire - 1)
    with pytest.raises(ValidationError, match='no prefix was delivered'):
        protocol.call(store, job['job_id'], 'read_evidence', {'event_id': event['event_id']})
    with store.transaction() as tx:
        protocol.update(tx, protocol.row(tx, job['job_id']), source_read_budget=wire)
    assert protocol.call(store, job['job_id'], 'read_evidence', {'event_id': event['event_id']}) == result
    assert protocol.row(store, job['job_id'])['payload']['source_read_bytes'] == wire


def test_native_preview_cannot_become_complete_source_proof(planned):
    store, _, _ = planned
    event, _ = capture_source(planned, 'whole source\n' * 1000 + 'TAIL\n', response='prefix only')
    with pytest.raises(ValidationError, match='complete original source evidence is unavailable'):
        evidence.read(store, event['event_id'])


def test_changed_frozen_source_identity_is_refused(planned):
    store, run, _ = planned
    event, _ = capture_source(planned, 'whole source\n' * 1000 + 'TAIL\n')
    job = native_companion.prepare(store, 'eng-001', run, 'main', 'changed-source')
    activate(store, job)
    with store.transaction() as tx:
        payload = json.loads(tx.db.execute('SELECT payload FROM events WHERE event_id=?', (event['event_id'],)).fetchone()[0])
        payload['unexpected_change'] = True
        tx.db.execute('UPDATE events SET payload=?,payload_hash=? WHERE event_id=?', (canonical(payload), digest(payload), event['event_id']))
    from hx.continuity_store import Conflict
    with pytest.raises(Conflict, match='changed after preparation'):
        protocol.call(store, job['job_id'], 'read_evidence', {'event_id': event['event_id']})


def test_source_fact_requires_a_complete_evidence_read_but_can_remain_pending(planned):
    store, run, _ = planned
    event, _ = capture_source(planned, 'source finding\n' * 1000 + 'TAIL\n')
    job = native_companion.prepare(store, 'eng-001', run, 'main', 'unread-source')
    activate(store, job)
    frozen = protocol.frozen(store, job)
    invalid = answer(frozen, [create(event, text='source finding')])
    result = protocol.call(store, job['job_id'], 'submit_patch', {'response': invalid})
    assert not result['accepted'] and 'unread complete source evidence' in result['error']
    pending = answer(frozen, disposition='pending')
    assert protocol.call(store, job['job_id'], 'submit_patch', {'response': pending})['accepted']


def test_source_can_support_a_fact_after_the_complete_call(planned):
    store, run, _ = planned
    event, _ = capture_source(planned, 'source finding\n' * 1000 + 'TAIL\n')
    job = native_companion.prepare(store, 'eng-001', run, 'main', 'read-source')
    activate(store, job)
    protocol.call(store, job['job_id'], 'read_evidence', {'event_id': event['event_id']})
    response = answer(protocol.frozen(store, job), [create(event, text='source finding')])
    assert protocol.call(store, job['job_id'], 'submit_patch', {'response': response})['accepted']


@pytest.mark.parametrize('caller', [{'HARNESS_ID': 'partner'}, {'HARNESS_ID': 'eng-001'}, {'HX_CONTINUITY_RUN': 'native-run'}])
def test_managed_shell_source_evidence_refuses_before_loading_or_stdout(planned, caller, monkeypatch, capsys):
    store, _, _ = planned
    event, _ = capture_source(planned, 'whole source\n' * 3000 + 'REQUIRED TAIL\n')
    monkeypatch.setattr(evidence, 'read', lambda *args, **kwargs: pytest.fail('Refuse before reading source content.'))
    with pytest.raises(ValidationError, match='native complete-file Read operation'):
        evidence.main([event['event_id']], store.root, env=caller)
    assert capsys.readouterr().out == ''


def test_operator_source_evidence_stays_complete_and_managed_non_file_evidence_stays_bounded(planned, capsys):
    store, _, ordinary = planned
    text = 'whole source\n' * 3000 + 'REQUIRED TAIL\n'
    event, _ = capture_source(planned, text)
    assert evidence.main([event['event_id']], store.root, env={}) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['complete'] and result['content'] == text and result['next_offset'] is None
    assert evidence.main([ordinary['event_id'], '--offset', '0', '--limit', '20'], store.root,
                         env={'HARNESS_ID': 'eng-001', 'HX_CONTINUITY_RUN': event['run_id']}) == 0
    result = json.loads(capsys.readouterr().out)
    assert result['bytes'] == 20 and result['next_offset'] == 20
