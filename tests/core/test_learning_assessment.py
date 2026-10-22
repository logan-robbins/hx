"""Independent assessment, evidence scope, durable proposals and restricted transport."""
from __future__ import annotations

import json

import pytest

from hx import learning_companion as learner, learning_protocol as protocol, native_companion, passes
from hx.continuity_store import Conflict
from hx.errors import ValidationError
from .companion_http import api_key, replay, silent
from .test_native_companion import planned
from .test_passes import answer


def prepare(planned, request='assessment'):
    store, run, _ = planned
    return learner.prepare(store, 'eng-001', 'main', request, run_id=run)


def activate(store, job):
    store.db.execute("UPDATE learning_jobs SET status='running' WHERE job_id=?", (job['job_id'],))


def response(job, *, proposals=None, pending=None):
    return {'event_digest': job['payload']['body']['event_digest'],
            'proposals': proposals or [], 'pending_event_ids': pending or []}


def proposal(event_id, scope='global'):
    return {'category': 'testing', 'scope': scope, 'signal': 'An input contains an undeclared field.',
            'check': 'Assert the parser rejects unknown fields before publishing a result.',
            'evidence_ids': [event_id]}


def test_context_and_learning_assess_same_event_with_independent_cursors(planned):
    store, run, event = planned
    context = native_companion.prepare(store, 'eng-001', run, 'main', 'context')
    job = prepare(planned)
    assert prepare(planned)['job_id'] == job['job_id']
    assert job['payload']['body']['events'][0]['event_id'] == event['event_id']
    passes.commit(store, context['pass_id'], answer(json.loads(store.db.execute('SELECT payload FROM passes WHERE pass_id=?', (context['pass_id'],)).fetchone()[0]), disposition='no_change'))
    assert store.db.execute('SELECT classified_seq FROM cursors').fetchone()[0] == 1
    assert store.db.execute('SELECT assessed_seq FROM learning_cursors').fetchone()[0] == 0
    activate(store, job)
    assert protocol.call(store, job['job_id'], 'submit_assessment', {'response': response(job)})['accepted']
    assert store.db.execute('SELECT assessed_seq FROM learning_cursors').fetchone()[0] == 1
    assert store.db.execute('SELECT classified_seq FROM cursors').fetchone()[0] == 1
    assert store.db.execute('SELECT count(*) FROM records').fetchone()[0] == 0


def test_assessment_creates_reviewable_proposal_without_accepting_rule(planned):
    from hx import learn, runtime_notifications
    store, _, event = planned
    job = prepare(planned)
    activate(store, job)
    body = response(job, proposals=[proposal(event['event_id'])])
    accepted = protocol.call(store, job['job_id'], 'submit_assessment', {'response': body})
    assert accepted['accepted'] and len(accepted['result']['proposal_ids']) == 1
    assert protocol.call(store, job['job_id'], 'submit_assessment', {'response': body}) == accepted
    runtime_notifications.drain(store, wake=lambda *_: pytest.fail('proposal projection must not wake another model'))
    pending = list((store.root / 'LEARNINGS/proposals').glob('*.json'))
    assert len(pending) == 1
    candidate = json.loads(pending[0].read_text())
    assert candidate['author'] == 'eng-001' and candidate['scope'] is None
    assert event['event_id'] in candidate['evidence']
    assert learn.read_all_rules(store.root) == []
    with pytest.raises(ValidationError, match='cannot accept'):
        learn.accept(store.root, candidate['id'], env={'HARNESS_ID': 'eng-001'})
    result = learn.accept(store.root, candidate['id'], env={'HARNESS_ID': 'review-001'})
    assert result and len(learn.read_all_rules(store.root)) == 1
    assert store.db.execute("SELECT count(*) FROM artifact_refs WHERE owner_type='learning' AND slot LIKE 'proposal:%'").fetchone()[0] == 1


@pytest.mark.parametrize('scope,evidence_id', [('another-role', None), ('global', 'foreign')])
def test_foreign_role_or_unobserved_evidence_never_creates_proposals(planned, scope, evidence_id):
    store, _, event = planned
    job = prepare(planned)
    activate(store, job)
    candidate = proposal(evidence_id or event['event_id'], scope)
    with pytest.raises(ValidationError, match='scope|frozen evidence'):
        protocol.call(store, job['job_id'], 'submit_assessment', {'response': response(job, proposals=[candidate])})
    assert not store.db.execute("SELECT 1 FROM outbox WHERE kind='learning_proposal'").fetchone()
    assert store.db.execute('SELECT assessed_seq FROM learning_cursors').fetchone()[0] == 0


def test_late_events_and_changed_evidence_do_not_cross_frozen_window(planned):
    store, run, event = planned
    job = prepare(planned)
    with store.transaction() as tx:
        late = tx.append_event(run, 'main', 'later', 'correction', {'text': 'A later observation.'})
    activate(store, job)
    with pytest.raises(ValidationError, match='outside'):
        protocol.call(store, job['job_id'], 'read_evidence', {'event_id': late['event_id']})
    with pytest.raises(ValidationError, match='without slices'):
        protocol.call(store, job['job_id'], 'read_evidence', {'event_id': event['event_id'], 'offset': 0})
    store.db.execute("UPDATE events SET payload_hash='changed' WHERE event_id=?", (event['event_id'],))
    with pytest.raises(Conflict, match='changed'):
        protocol.call(store, job['job_id'], 'submit_assessment', {'response': response(job)})
    assert store.db.execute('SELECT assessed_seq FROM learning_cursors').fetchone()[0] == 0


def test_failed_learning_model_does_not_advance_or_repeat_assessment(planned):
    store, _, _ = planned
    job = prepare(planned)
    with replay('overloaded') as sent:
        result = learner.execute(store, job['job_id'])
    assert result['status'] == 'unresolved' and result['execution']['outcome'] == 'http_error'
    assert 'overloaded_error' in result['execution']['error']
    assert store.db.execute('SELECT assessed_seq FROM learning_cursors').fetchone()[0] == 0
    with silent():
        assert learner.execute(store, job['job_id'])['status'] == 'unresolved'
    assert len(sent) == 1
    assert store.db.execute('SELECT classified_seq FROM cursors').fetchone()[0] == 0


def test_learner_exchange_has_only_assessment_tools_and_cached_system(planned):
    store, _, event = planned
    job = prepare(planned)
    assessment = response(job, proposals=[proposal(event['event_id'])])
    with replay('submit-assessment', assessment=assessment) as sent:
        result = learner.execute(store, job['job_id'], env={'PATH': '/bin', 'PRIVATE': 'secret'})
    assert result['status'] == 'committed'
    (request,) = sent
    payload = learner.row(store, job['job_id'])['payload']
    assert [tool['name'] for tool in request['body']['tools']] == ['read_evidence', 'submit_assessment']
    assert request['body']['system'] == [{'type': 'text', 'text': payload['system'], 'cache_control': {'type': 'ephemeral'}}]
    assert request['body']['messages'][0]['content'][0]['text'] == learner.user_turn(job['payload']['body'])
    assert event['event_id'] in request['body']['messages'][0]['content'][0]['text']
    assert result['usage'][0]['cache_creation_input_tokens'] == 3968
    with silent():
        assert learner.execute(store, job['job_id'])['status'] == 'committed'


def test_learner_cannot_use_context_patch_or_filesystem_tools(planned):
    store, _, _ = planned
    job = prepare(planned)
    activate(store, job)
    for name in ('submit_patch', 'read_file', 'shell', 'approve_rule'):
        with pytest.raises(ValidationError, match='unknown'):
            protocol.call(store, job['job_id'], name, {})


def test_partner_gets_independent_learning_assessment(instance):
    from hx import streams
    from .test_prompt_compiler import configure
    configure(instance, item_id='partner', role='partner', flavor='claude')
    api_key(instance)
    streams.append_record(instance, 'partner', 'partner-main', {'event': 'correction', 'text': 'Always check provider interfaces before dispatch.'})
    from hx.continuity_store import ContinuityStore
    with ContinuityStore(instance) as store:
        job = learner.prepare(store, 'partner', 'partner-main', 'partner-assessment')
        assert job['run_id'] is None and job['payload']['body']['source'] == 'partner'
        activate(store, job)
        assert protocol.call(store, job['job_id'], 'submit_assessment', {'response': response(job)})['accepted']
        assert store.db.execute('SELECT assessed_seq FROM learning_cursors').fetchone()[0] == 1
