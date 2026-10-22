"""Independent regression review of learning evidence, replay, budgets and publication."""
from __future__ import annotations

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Lock

import pytest

from hx import learn, learning_companion as learner, learning_protocol as protocol
from hx import companion_api, native_companion, runtime_notifications
from hx.continuity_store import Conflict, canonical
from hx.errors import NotFound, ValidationError
from .test_native_companion import planned
from .test_learning_assessment import activate, prepare, proposal, response


def set_config(store, **learning):
    path = store.root / 'config/eng-001/harness.json'
    config = json.loads(path.read_text())
    config['learning'] = {**config.get('learning', {}), **learning}
    path.write_text(json.dumps(config))


def test_artifact_preview_is_never_complete_learning_evidence(planned):
    store, run, _ = planned
    complete = 'Full evidence: preserve negation and the FINAL unsupported provider condition.'
    with store.transaction() as tx:
        artifact = tx.put_artifact(complete.encode(), owner_type='test', owner_id='full', slot='body')
        event = tx.append_event(run, 'main', 'artifact-evidence', 'tool_result',
            {'artifact_hash': artifact, 'preview': 'Full evidence: preserve negation'})
    job = prepare(planned)
    frozen = next(e for e in job['payload']['body']['events'] if e['event_id'] == event['event_id'])
    assert frozen['requires_read'] and 'payload' not in frozen
    activate(store, job)
    answer = response(job, proposals=[proposal(event['event_id'])])
    with pytest.raises(ValidationError, match='unread'):
        protocol.call(store, job['job_id'], 'submit_assessment', {'response': answer})
    result = protocol.call(store, job['job_id'], 'read_evidence', {'event_id': event['event_id']})
    assert result == {'event_id': event['event_id'], 'content': complete, 'complete': True}
    assert protocol.call(store, job['job_id'], 'submit_assessment', {'response': answer})['accepted']


def test_changed_payload_cannot_hide_behind_unchanged_hash(planned):
    store, _, event = planned
    job = prepare(planned)
    activate(store, job)
    store.db.execute('UPDATE events SET payload=? WHERE event_id=?', (canonical({'text': 'Different source bytes.'}), event['event_id']))
    with pytest.raises(Conflict, match='frozen hash'):
        protocol.call(store, job['job_id'], 'submit_assessment', {'response': response(job)})
    assert store.db.execute('SELECT assessed_seq FROM learning_cursors').fetchone()[0] == 0


def test_job_body_must_match_immutable_input_before_read_or_submit(planned):
    store, _, _ = planned
    job = prepare(planned)
    forged = job['payload']
    forged['body']['role'] = 'qa-engineer'
    store.db.execute('UPDATE learning_jobs SET payload=? WHERE job_id=?', (canonical(forged), job['job_id']))
    activate(store, job)
    with pytest.raises(Conflict, match='immutable frozen input'):
        protocol.call(store, job['job_id'], 'submit_assessment', {'response': response(job)})


def test_accepted_pending_window_does_not_repeat_without_new_evidence(planned, monkeypatch):
    store, run, _ = planned
    set_config(store, batch_records=1)
    calls = []
    def assess(store, job_id, **kwargs):
        job = learner.row(store, job_id)
        calls.append(job_id)
        activate(store, job)
        pending = [e['event_id'] for e in job['payload']['body']['events']]
        protocol.call(store, job_id, 'submit_assessment', {'response': response(job, pending=pending)})
        store.db.execute("UPDATE learning_jobs SET status='committed' WHERE job_id=?", (job_id,))
        return learner.status(store, job_id)
    monkeypatch.setattr(learner, 'execute', assess)
    assert learner.tick(store)['status'] == 'committed'
    assert learner.tick(store)['status'] == 'idle' and len(calls) == 1
    with store.transaction() as tx:
        tx.append_event(run, 'main', 'new-learning-observation', 'correction', {'text': 'A new condition.'})
    assert learner.tick(store)['status'] == 'committed' and len(calls) == 2


def test_learning_scheduler_rotates_streams_before_revisiting_busy_stream(planned, monkeypatch):
    store, run, _ = planned
    set_config(store, batch_records=1)
    with store.transaction() as tx:
        tx.append_event(run, 'another', 'second-stream', 'correction', {'text': 'A separate assessment.'})
    streams = []
    def assess(store, job_id, **kwargs):
        job = learner.row(store, job_id)
        streams.append(job['payload']['body']['source_stream_id'])
        store.db.execute("UPDATE learning_jobs SET status='unresolved' WHERE job_id=?", (job_id,))
        return learner.status(store, job_id)
    monkeypatch.setattr(learner, 'execute', assess)
    learner.tick(store)
    with store.transaction() as tx:
        tx.append_event(run, streams[0], 'busy-again', 'correction', {'text': 'Another busy-stream event.'})
    learner.tick(store)
    assert len(streams) == 2 and streams[0] != streams[1]


def test_independent_learner_model_limits_cap_all_input_and_evidence(planned):
    store, _, _ = planned
    models = store.root / 'config/models.json'
    data = json.loads(models.read_text())
    data['small-learner'] = {'window': 65536, 'threshold': 48000, 'max_output_tokens': 1024}
    models.write_text(json.dumps(data))
    set_config(store, model='small-learner', request_bytes=262144)
    job = prepare(planned)
    payload = job['payload']
    assert payload['model'] == 'small-learner'
    assert payload['request_bytes'] == payload['model_input_limit'] < 48000
    assert payload['max_output_tokens'] == 1024
    assert payload['request_bytes'] + companion_api.MAX_ROUNDS * 1024 + companion_api.HOST_RESERVE <= 48000
    size = companion_api.request_bytes(payload['system'], payload['user'], protocol.TOOLS)
    assert size + payload['evidence_budget'] == payload['request_bytes']
    data['small-learner']['threshold'] = 47000
    models.write_text(json.dumps(data))
    with pytest.raises(Conflict, match='context limits changed'):
        learner.execute(store, job['job_id'])
    assert learner.row(store, job['job_id'])['status'] == 'prepared'


def test_unknown_learner_model_never_invents_context_limits(planned):
    store, _, _ = planned
    set_config(store, model='unlisted-independent-learner')
    with pytest.raises(ValidationError, match='explicit models.json'):
        prepare(planned)
    assert not store.db.execute('SELECT 1 FROM learning_jobs').fetchone()


@pytest.mark.parametrize('purpose', ['learning', 'context'])
def test_invalid_credential_never_claims_a_running_companion_slot(planned, purpose):
    store, run, _ = planned
    job = prepare(planned) if purpose == 'learning' else native_companion.prepare(store, 'eng-001', run, 'main', 'context-review')
    (store.root / 'seed/token').chmod(0o644)
    execute = learner.execute if purpose == 'learning' else native_companion.execute
    table = 'learning_jobs' if purpose == 'learning' else 'companion_jobs'
    with pytest.raises(ValidationError, match='private'):
        execute(store, job['job_id'])
    assert store.db.execute(f'SELECT status FROM {table} WHERE job_id=?', (job['job_id'],)).fetchone()[0] == 'prepared'


def test_proposal_projection_replay_survives_reviewers_category_change(planned):
    store, _, event = planned
    job = prepare(planned)
    activate(store, job)
    protocol.call(store, job['job_id'], 'submit_assessment', {'response': response(job, proposals=[proposal(event['event_id'])])})
    row = store.db.execute("SELECT payload FROM outbox WHERE kind='learning_proposal'").fetchone()
    candidate = json.loads(row[0])
    protocol.publish(store.root, candidate)
    learn.accept(store.root, candidate['id'], category='delivery', env={'HARNESS_ID': 'review-001'})
    archived = protocol.publish(store.root, candidate)
    assert archived['decision'] == 'new' and archived['category'] == 'delivery'
    with pytest.raises(Conflict, match='different content'):
        protocol.publish(store.root, {**candidate, 'category': 'domain'})


@pytest.mark.parametrize('role', [None, 'qa-engineer'])
def test_oversized_rule_acceptance_preserves_proposal_and_existing_rules(planned, role):
    store, _, _ = planned
    root = store.root
    directory = learn.role_dir(root, role) if role else learn.learnings_path(root)
    directory.mkdir(exist_ok=True)
    path = learn.category_file(directory, 'testing')
    header = learn.category_header('testing', role)
    before = header + '\n<!--' + 'x' * (39950 - len(header.encode()) - 10) + '-->\n'
    path.write_text(before)
    candidate = learn.propose(root, signal='A new condition', check='Preserve the complete reviewed rule and all negation.',
        evidence='A concrete regression record.', category='testing', role=role, env={'HARNESS_ID': 'eng-001'})
    with pytest.raises(ValidationError, match='proposal remains pending'):
        learn.accept(root, candidate['id'], env={'HARNESS_ID': 'review-001'})
    assert path.read_text() == before and len(path.read_bytes()) <= 40000
    assert learn.load_proposal(learn.learnings_path(root), candidate['id'])['id'] == candidate['id']
    assert not (learn.learnings_path(root) / 'accepted' / f"{candidate['id']}.json").exists()


def test_oversized_evidence_merge_leaves_original_rule_and_proposal(planned):
    store, _, _ = planned
    root = store.root
    candidate = learn.propose(root, signal='First signal', check='First check', evidence='First observation.', category='testing', env={'HARNESS_ID': 'eng-001'})
    accepted = learn.accept(root, candidate['id'], env={'HARNESS_ID': 'review-001'})
    path = learn.category_file(learn.learnings_path(root), 'testing')
    original = path.read_text()
    before = original + '\n<!--' + 'x' * (39950 - len(original.encode()) - 10) + '-->\n'
    path.write_text(before)
    extra = learn.propose(root, signal='', check='', evidence='new complete proof ' * 30, evidence_for=accepted['rule'], env={'HARNESS_ID': 'eng-001'})
    with pytest.raises(ValidationError, match='proposal remains pending'):
        learn.accept(root, extra['id'], env={'HARNESS_ID': 'review-001'})
    assert path.read_text() == before
    assert learn.load_proposal(learn.learnings_path(root), extra['id'])


def test_concurrent_review_of_one_proposal_cannot_publish_duplicate_rules(planned, monkeypatch):
    store, _, _ = planned
    candidate = learn.propose(store.root, signal='One observed condition', check='One preventive assertion', evidence='One evidence.', category='testing', env={'HARNESS_ID': 'eng-001'})
    original = learn.load_proposal
    barrier, lock = Barrier(2), Lock()
    calls = 0
    def synchronized(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        with lock:
            calls += 1
            wait = calls <= 2
        if wait:
            barrier.wait(timeout=5)
        return result
    monkeypatch.setattr(learn, 'load_proposal', synchronized)
    def review():
        try:
            return learn.accept(store.root, candidate['id'], env={'HARNESS_ID': 'review-001'})
        except NotFound:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        answers = list(pool.map(lambda _: review(), range(2)))
    assert sum(answer is not None for answer in answers) == 1
    assert len(learn.read_all_rules(store.root)) == 1


def test_partner_capture_excerpt_cannot_be_promoted_to_complete_original_evidence(instance):
    from hx import streams
    from hx.continuity_store import ContinuityStore
    streams.append_record(instance, 'partner', 'partner-main', {'event': 'tool',
        'input': 'A bounded input preview', 'output': 'Only the initial result',
        'ref': {'transcript': '/unavailable/original.jsonl', 'tool_use_id': 'tool-1'}})
    with ContinuityStore(instance) as store:
        job = learner.prepare(store, 'partner', 'partner-main', 'legacy-partner-review')
        event = job['payload']['body']['events'][0]
        assert event['requires_read'] and event['original_unavailable']
        activate(store, job)
        result = protocol.call(store, job['job_id'], 'read_evidence', {'event_id': event['event_id']})
        assert result['complete'] is False and result['original_unavailable']
        with pytest.raises(ValidationError, match='unread'):
            protocol.call(store, job['job_id'], 'submit_assessment',
                {'response': response(job, proposals=[proposal(event['event_id'])])})
        assert protocol.call(store, job['job_id'], 'submit_assessment',
            {'response': response(job, pending=[event['event_id']])})['accepted']
        assert store.db.execute('SELECT assessed_seq FROM learning_cursors').fetchone()[0] == 0


def test_sparse_oversized_material_is_rejected_before_content_allocation(tmp_path, monkeypatch):
    from hx import instruction_materials as materials
    refs, files = materials.prepare('Mandatory complete condition.', tmp_path)
    materials.install(refs, files)
    target = __import__('pathlib').Path(refs[0]['path'])
    with target.open('r+b') as handle:
        handle.truncate(1_000_000_000)
    original = type(target).open
    def refuse_content(path, *args, **kwargs):
        if path == target:
            pytest.fail('oversized immutable material must be refused before opening its body')
        return original(path, *args, **kwargs)
    monkeypatch.setattr(type(target), 'open', refuse_content)
    with pytest.raises(ValidationError, match='altered'):
        materials.verify_file(refs[0])


def test_unavailable_irrelevant_partner_capture_can_be_ignored_with_reason(instance):
    from hx import streams
    from hx.continuity_store import ContinuityStore
    streams.append_record(instance, 'partner', 'partner-main', {'event': 'post_tool',
        'input': 'truncated diagnostic', 'output': 'partial observation', 'ref': {'transcript': '/missing'}})
    with ContinuityStore(instance) as store:
        job = learner.prepare(store, 'partner', 'partner-main', 'ignore-no-proof')
        event = job['payload']['body']['events'][0]
        activate(store, job)
        answer = {**response(job), 'ignored_event_reasons': {event['event_id']: 'A diagnostic excerpt establishes no reusable preventive lesson or whole-file claim.'}}
        assert protocol.call(store, job['job_id'], 'submit_assessment', {'response': answer})['accepted']
        assert store.db.execute('SELECT assessed_seq FROM learning_cursors').fetchone()[0] == 1
        assert not store.db.execute("SELECT 1 FROM outbox WHERE kind='learning_proposal'").fetchone()


def test_partner_broker_artifact_and_receipt_are_complete_learning_evidence(instance):
    import hashlib
    from hx import streams
    from hx.continuity_store import ContinuityStore
    with ContinuityStore(instance) as store:
        complete = 'The entire existing helper. ' * 500 + 'FINAL: preserve the current provider.'
        exact = complete.encode()
        receipt = {'path': '/source/helpers.py', 'sha256': hashlib.sha256(exact).hexdigest(), 'bytes': len(exact), 'version': 'immutable-source'}
        with store.transaction() as tx:
            artifact = tx.put_artifact(exact, owner_type='test', owner_id='whole-partner-source', slot='content')
            receipt_artifact = tx.put_artifact(canonical(receipt).encode(), owner_type='test', owner_id='whole-partner-source', slot='receipt')
        streams.append_record(instance, 'partner', 'partner-main', {'event': 'complete_file_read',
            'source': 'native-whole-file-broker', 'artifact_hash': artifact, 'receipt_artifact_hash': receipt_artifact,
            'bytes': len(exact), 'sha256': receipt['sha256'], 'read_identity': {'session_id': 's', 'actor_id': '', 'call_id': 'c'}, 'context_generation': 'g'})
        job = learner.prepare(store, 'partner', 'partner-main', 'whole-partner-source')
        event = job['payload']['body']['events'][0]
        assert event['requires_read'] and not event.get('original_unavailable')
        activate(store, job)
        candidate = proposal(event['event_id'])
        with pytest.raises(ValidationError, match='unread'):
            protocol.call(store, job['job_id'], 'submit_assessment', {'response': response(job, proposals=[candidate])})
        delivered = protocol.call(store, job['job_id'], 'read_evidence', {'event_id': event['event_id']})
        assert delivered['complete'] and delivered['content'] == complete and delivered['receipt'] == receipt
        assert protocol.call(store, job['job_id'], 'submit_assessment', {'response': response(job, proposals=[candidate])})['accepted']
        retained = {r[0] for r in store.db.execute("SELECT hash FROM artifact_refs WHERE owner_type='learning' AND owner_id=?", (job['job_id'],))}
        assert artifact in retained and receipt_artifact in retained


@pytest.mark.parametrize('purpose', ['context', 'learning'])
def test_new_reviewed_rule_replaces_only_unpaid_preparation(planned, purpose):
    store, run, _ = planned
    def prepare_same():
        return (learner.prepare(store, 'eng-001', 'main', 'same-reviewed-request', run_id=run) if purpose == 'learning'
                else native_companion.prepare(store, 'eng-001', run, 'main', 'same-reviewed-request'))
    original = prepare_same()
    candidate = learn.propose(store.root, signal='Reviewed rules advance during an active assignment.',
        check='Preserve the FINAL independent reviewer condition.', evidence='Observed prepared boundary.',
        category='delivery', env={'HARNESS_ID': 'eng-002'})
    learn.accept(store.root, candidate['id'], env={'HARNESS_ID': 'partner'})
    fresh = prepare_same()
    assert fresh['job_id'] != original['job_id']
    assert 'FINAL independent reviewer condition.' in fresh['payload']['system']
    assert 'FINAL independent reviewer condition.' not in original['payload']['system']
    table = 'learning_jobs' if purpose == 'learning' else 'companion_jobs'
    assert store.db.execute(f'SELECT status FROM {table} WHERE job_id=?', (original['job_id'],)).fetchone()[0] == 'superseded'
    store.db.execute(f"UPDATE {table} SET status='running' WHERE job_id=?", (fresh['job_id'],))
    rule = learn.rule_files(store.root)[0]
    rule.write_text(rule.read_text() + '\nA later reviewed condition.\n')
    assert prepare_same()['job_id'] == fresh['job_id']


def test_legacy_native_source_preview_cannot_support_learning(planned):
    store, run, _ = planned
    with store.transaction() as tx:
        event = tx.append_event(run, 'main', 'legacy-read-preview', 'tool_result',
            {'evidence_scope': 'whole_file', 'observation': {'kind': 'tool_result',
             'data': {'tool_name': 'Read', 'tool_use_id': 'legacy-source-call', 'tool_response': 'Only an old captured prefix.'}}})
    job = prepare(planned)
    selected = next(item for item in job['payload']['body']['events'] if item['event_id'] == event['event_id'])
    assert selected['requires_read'] and selected['source_file_evidence']
    activate(store, job)
    with pytest.raises(ValidationError, match='complete|receipt|source'):
        protocol.call(store, job['job_id'], 'read_evidence', {'event_id': event['event_id']})
    assert event['event_id'] not in learner.row(store, job['job_id'])['payload']['reads']
    with pytest.raises(ValidationError, match='unread'):
        protocol.call(store, job['job_id'], 'submit_assessment', {'response': response(job)})
    ignored = {**response(job), 'ignored_event_reasons': {event['event_id']: 'The historical prefix lacks complete source proof; no lesson uses it.'}}
    assert protocol.call(store, job['job_id'], 'submit_assessment', {'response': ignored})['accepted']


def test_learning_source_uses_verified_original_and_pins_immutable_proof(planned, monkeypatch):
    import hashlib
    from hx import evidence
    store, run, _ = planned
    original = 'The complete original source ends with FINAL: never omit the negation.\n'
    receipt = {'bytes': len(original.encode()), 'sha256': hashlib.sha256(original.encode()).hexdigest()}
    captured = {'kind': 'tool_result', 'data': {'tool_name': 'Read', 'tool_response': 'native wrapper is not the source'}}
    with store.transaction() as tx:
        artifact = tx.put_artifact(canonical(captured).encode(), owner_type='test', owner_id='native-wrapper', slot='body')
        event = tx.append_event(run, 'main', 'full-source-proof', 'tool_result',
            {'evidence_scope': 'whole_file', 'artifact_hash': artifact})
    seen = []
    def verified(tx, row, payload, data):
        seen.append((row['event_id'], json.loads(data)))
        return original.encode(), receipt
    monkeypatch.setattr(evidence, 'verified_source', verified)
    job = prepare(planned)
    activate(store, job)
    result = protocol.call(store, job['job_id'], 'read_evidence', {'event_id': event['event_id']})
    assert result == {'event_id': event['event_id'], 'content': original, 'receipt': receipt, 'complete': True}
    assert seen == [(event['event_id'], captured)]
    refs = store.db.execute("SELECT slot,hash FROM artifact_refs WHERE owner_type='learning' AND owner_id=? AND slot LIKE 'source%'", (job['job_id'],)).fetchall()
    assert {row['slot'] for row in refs} == {'source:' + event['event_id'], 'source-receipt:' + event['event_id']}
    with store.transaction() as tx:
        contents = [tx.read_artifact(row['hash']) for row in refs]
    assert original.encode() in contents and canonical(receipt).encode() in contents
