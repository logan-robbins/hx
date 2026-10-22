from __future__ import annotations

import json

import httpx
import pytest

from hx import companion_api, companion_protocol as protocol, native_companion as native, passes
from hx.continuity_store import ContinuityStore, Conflict, SCHEMA_VERSION, canonical
from hx.errors import ValidationError
from .companion_http import KEY, api_key, replay, silent
from .test_passes import answer, create
from .test_prompt_compiler import configure
from .test_appmap import mapped
from .test_map_updates import active
from .test_unit_execution import fleet
from .test_context_packets import assignment
from .test_native_launch import configured


@pytest.fixture
def planned(instance, monkeypatch):
    from hx import jev
    # Native transport/patch tests isolate the required decision service.
    # test_jev exercises its HTTP contract and failures independently.
    monkeypatch.setenv('TYPESAFE_API_KEY', 'fixture-key')
    monkeypatch.setattr(jev, 'evaluate', lambda state, questions, **kwargs: {
        'model': jev.MODEL, 'answers': {key: {'type': 'noul', 'noul': .95} for key in questions},
        'usage': {'input_tokens': 100, 'output_tokens': 20}, 'latency_ms': 1})
    config = configure(instance)
    api_key(instance)
    with ContinuityStore(instance) as store:
        with store.transaction() as tx:
            tx.put_task('T', {'goal': 'Preserve only current findings.', 'workdir': config['workdir']}, expected_revision=0)
            run = tx.start_run('T', 1, 'eng-001')
            event = tx.append_event(run, 'main', 'native-1', 'tool_result', {'text': 'The parser rejects unknown fields.'})
        yield store, run, event


def job_for(planned, request='request'):
    store, run, _ = planned
    return native.prepare(store, 'eng-001', run, 'main', request)


def activate(store, job):
    store.db.execute("UPDATE companion_jobs SET status='running' WHERE job_id=?", (job['job_id'],))


def test_scoped_evidence_patch_and_late_event(planned):
    store, run, event = planned
    job = job_for(planned)
    frozen = protocol.frozen(store, job)
    assert job_for(planned)['job_id'] == job['job_id']
    activate(store, job)
    with store.transaction() as tx:
        late = tx.append_event(run, 'main', 'native-2', 'tool_result', {'text': 'A later observation.'})
    result = protocol.call(store, job['job_id'], 'read_evidence', {'event_id': event['event_id'], 'offset': 0, 'limit': 512})
    assert 'rejects unknown fields' in result['content']
    with pytest.raises(ValidationError, match='outside'):
        protocol.call(store, job['job_id'], 'read_evidence', {'event_id': late['event_id'], 'offset': 0, 'limit': 10})
    response = answer(frozen, [create(event, text='The parser rejects unknown fields.')])
    assert protocol.call(store, job['job_id'], 'submit_patch', {'response': response})['accepted']
    assert protocol.call(store, job['job_id'], 'submit_patch', {'response': response})['accepted']
    assert store.db.execute('SELECT classified_seq FROM cursors').fetchone()[0] == 1
    assert store.db.execute('SELECT disposition FROM events WHERE event_id=?', (late['event_id'],)).fetchone()[0] is None
    assert store.db.execute('SELECT count(*) FROM records').fetchone()[0] == 1
    with pytest.raises(Conflict, match='different'):
        protocol.call(store, job['job_id'], 'submit_patch', {'response': {**response, 'operations': []}})


def test_invalid_patch_retries_once_and_preserves_cursor(planned):
    store, _, _ = planned
    job = job_for(planned)
    activate(store, job)
    for remaining in (1, 0):
        result = protocol.call(store, job['job_id'], 'submit_patch', {'response': {}})
        assert not result['accepted'] and result['retry_remaining'] == remaining
    with pytest.raises(ValidationError, match='retry exhausted'):
        protocol.call(store, job['job_id'], 'submit_patch', {'response': {}})
    assert store.db.execute('SELECT classified_seq FROM cursors').fetchone()[0] == 0
    assert store.db.execute('SELECT count(*) FROM records').fetchone()[0] == 0


def test_evidence_budget_persists_across_server_restart(planned):
    store, _, event = planned
    job = job_for(planned)
    activate(store, job)
    for _ in range(4):
        with ContinuityStore(store.root) as reopened:
            protocol.call(reopened, job['job_id'], 'read_evidence', {'event_id': event['event_id'], 'offset': 0, 'limit': 2048})
    with pytest.raises(ValidationError, match='budget exhausted'):
        protocol.call(store, job['job_id'], 'read_evidence', {'event_id': event['event_id'], 'offset': 0, 'limit': 1})
    for name in ('shell', 'write_file', 'complete', 'read_file'):
        with pytest.raises(ValidationError, match='unknown'):
            protocol.call(store, job['job_id'], name, {})


def test_stale_task_cannot_read_or_install_pass(planned):
    store, _, event = planned
    job = job_for(planned)
    body = protocol.frozen(store, job)
    activate(store, job)
    with store.transaction() as tx:
        tx.put_task('T', {'goal': 'Changed assignment.'}, expected_revision=1)
    with pytest.raises(Conflict, match='amended'):
        protocol.call(store, job['job_id'], 'read_evidence', {'event_id': event['event_id'], 'offset': 0, 'limit': 10})
    result = protocol.call(store, job['job_id'], 'submit_patch', {'response': answer(body, [create(event)])})
    assert not result['accepted'] and 'amended' in result['error']


def test_api_tools_are_only_pass_capabilities_and_refuse_foreign_evidence(planned):
    store, _, _ = planned
    job = job_for(planned)
    activate(store, job)
    assert [tool['name'] for tool in protocol.TOOLS] == ['read_evidence', 'submit_patch']
    assert all(set(tool) == {'name', 'description', 'input_schema'} for tool in protocol.TOOLS)
    with pytest.raises(ValidationError, match='outside'):
        protocol.call(store, job['job_id'], 'read_evidence', {'event_id': 'foreign', 'offset': 0, 'limit': 1})


def assert_exchange(sent, store, job):
    """Every round is one Messages API request with the frozen, cached system block."""
    payload = protocol.row(store, job['job_id'])['payload']
    for number, request in enumerate(sent, 1):
        assert request['url'] == 'https://api.anthropic.com/v1/messages'
        assert request['headers']['x-api-key'] == KEY
        assert request['headers']['anthropic-version'] == companion_api.API_VERSION
        body = request['body']
        assert request['content'] == canonical(body).encode()
        assert body['system'] == [{'type': 'text', 'text': payload['system'], 'cache_control': {'type': 'ephemeral'}}]
        assert body['tools'] == protocol.TOOLS and body['tool_choice'] == {'type': 'auto'}
        assert body['model'] == payload['model'] and body['max_tokens'] == payload['max_output_tokens']
        assert body['output_config'] == {'effort': payload['effort']} and 'thinking' not in body
        assert body['messages'][0] == {'role': 'user', 'content': [{'type': 'text', 'text': payload['user']}]}
        assert len(body['messages']) == 2 * number - 1
    assert len(sent) <= companion_api.MAX_ROUNDS


def test_pass_is_one_bounded_exchange_and_replays_without_request(planned):
    store, _, event = planned
    job = job_for(planned)
    body = protocol.frozen(store, job)
    read = {'event_id': event['event_id'], 'offset': 0, 'limit': 1024}
    with replay('read-evidence', 'submit-patch', read=read, patch=answer(body, [create(event)])) as sent:
        result = native.execute(store, job['job_id'], env={'PATH': '/bin', 'PRIVATE': 'PRIVATE'})
    assert result['status'] == 'committed' and result['execution']['outcome'] == 'submitted'
    assert_exchange(sent, store, job)
    # The second round carries the assistant turn unchanged and one tool_result for its call.
    second = sent[1]['body']['messages']
    assert second[1]['role'] == 'assistant' and second[1]['content'][1]['id'] == 'toolu_01ReadEvidence'
    (reply,) = second[2]['content']
    assert reply['type'] == 'tool_result' and reply['tool_use_id'] == 'toolu_01ReadEvidence'
    assert 'rejects unknown fields' in reply['content'] and 'is_error' not in reply
    # Each response's usage is kept on the job, cache counts included.
    assert [(u['round'], u['cache_creation_input_tokens'], u['cache_read_input_tokens']) for u in result['usage']] == [
        (1, 5120, 0), (2, 0, 5120)]
    with silent():
        assert native.execute(store, job['job_id']) == result
    # The key is sent in one header and persisted nowhere but seed/token.
    for path in store.root.rglob('*'):
        if path.is_file() and path != store.root / 'seed/token':
            assert KEY.encode() not in path.read_bytes(), path


def test_system_block_is_byte_stable_and_routes_ride_the_user_turn(planned):
    store, run, _ = planned
    first = job_for(planned)
    passes.commit(store, first['pass_id'], answer(protocol.frozen(store, first), disposition='no_change'))
    with store.transaction() as tx:
        tx.append_event(run, 'main', 'native-2', 'tool_result', {'text': 'A second observation.'})
    second = job_for(planned, 'second')
    assert first['payload']['system'] == second['payload']['system']
    assert first['payload']['user'] != second['payload']['user']
    assert 'Jev observation routes' in second['payload']['user'] and 'Jev' not in second['payload']['system']


def test_rejected_patch_is_answered_in_the_same_exchange(planned):
    store, _, event = planned
    job = job_for(planned)
    body = protocol.frozen(store, job)
    invalid = create(event)
    invalid['record']['evidence'] = ['unobserved']
    with replay('submit-patch', 'resubmit-patch', patch=answer(body, [invalid]),
                retry=answer(body, [create(event)])) as sent:
        result = native.execute(store, job['job_id'])
    assert result['status'] == 'committed' and result['attempts'] == 2 and len(sent) == 2
    (rejection,) = sent[1]['body']['messages'][2]['content']
    assert rejection['is_error'] and rejection['tool_use_id'] == 'toolu_01SubmitPatch'
    assert store.db.execute('SELECT count(*) FROM records').fetchone()[0] == 1


def test_third_round_is_the_last_and_says_so(planned):
    store, _, event = planned
    job = job_for(planned)
    read = {'event_id': event['event_id'], 'offset': 0, 'limit': 256}
    with replay('read-evidence', 'read-evidence', 'read-evidence', read=read) as sent:
        result = native.execute(store, job['job_id'])
    assert len(sent) == companion_api.MAX_ROUNDS and result['execution']['outcome'] == 'rounds_exhausted'
    assert sent[2]['body']['messages'][-1]['content'][-1] == {'type': 'text', 'text': companion_api.LAST_ROUND}
    assert all(m['content'][-1].get('text') != companion_api.LAST_ROUND for m in sent[1]['body']['messages'][1:])
    assert result['status'] == 'unresolved'
    assert store.db.execute('SELECT disposition FROM events').fetchone()[0] == 'pending'


@pytest.mark.parametrize('recording,outcome', [('end-turn', 'no_submission'), ('overloaded', 'http_error')])
def test_execution_without_patch_marks_evidence_pending_and_never_restarts(planned, recording, outcome):
    store, _, _ = planned
    job = job_for(planned)
    with replay(recording) as sent:
        result = native.execute(store, job['job_id'])
    assert len(sent) == 1 and result['status'] == 'unresolved'
    assert result['execution']['outcome'] == outcome and outcome in result['last_error']
    assert store.db.execute('SELECT disposition FROM events').fetchone()[0] == 'pending'
    assert store.db.execute('SELECT classified_seq FROM cursors').fetchone()[0] == 1
    with silent():
        assert native.execute(store, job['job_id']) == result


def test_a_fault_settles_the_job_instead_of_holding_the_slot(planned, monkeypatch):
    store, _, _ = planned
    job = job_for(planned)
    def broken(request):
        raise RuntimeError('fixture fault')
    monkeypatch.setattr(companion_api, 'TRANSPORT', httpx.MockTransport(broken))
    result = native.execute(store, job['job_id'])
    assert result['status'] == 'unresolved' and result['execution']['outcome'] == 'fault'
    assert 'fixture fault' in result['execution']['error']


def test_api_key_comes_from_seed_or_env_file_and_oauth_is_refused(planned, tmp_path):
    store, _, _ = planned
    root = store.root
    assert companion_api.credential(root) == KEY
    (root / 'seed/token').write_text('sk-ant-oat01-subscription\n')
    with pytest.raises(ValidationError, match='API key'):
        companion_api.credential(root)
    env_file = tmp_path / 'instance.env'
    env_file.write_text('ANTHROPIC_API_KEY=sk-ant-api03-from-env-file\n')
    env_file.chmod(0o600)
    from hx import envfile
    envfile.configure(root, str(env_file))
    assert companion_api.credential(root) == 'sk-ant-api03-from-env-file'


def test_running_job_owns_single_slot_without_expiring(planned):
    store, _, _ = planned
    job = job_for(planned)
    other = job_for(planned, 'other')
    activate(store, job)
    with silent():
        assert native.execute(store, job['job_id'])['status'] == 'running'
        with pytest.raises(Conflict, match='slot'):
            native.execute(store, other['job_id'])


def test_schema_fifteen_reopen_preserves_passes(planned):
    store, _, _ = planned
    job = job_for(planned)
    with ContinuityStore(store.root) as reopened:
        assert reopened.db.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
        assert reopened.db.execute('SELECT pass_id FROM passes').fetchone()[0] == job['pass_id']
        assert reopened.db.execute('SELECT count(*) FROM companion_jobs').fetchone()[0] == 1


@pytest.mark.parametrize('with_map', [False, True])
def test_recorded_exchange_reads_scoped_evidence_and_commits_patch(planned, with_map):
    store, run, event = planned
    map_options = {}
    if with_map:
        from pathlib import Path
        from hx import appmap, map_updates
        from .test_appmap import record, write, commit
        with store.transaction() as tx:
            repository = Path(tx.task('T')['payload']['workdir'])
        (repository / 'parser.py').write_text('def parse():\n    return "reject unknown fields"\n')
        appmap.initialize(repository)
        node = record('parser', claim='observed', anchors=[appmap.source_anchor(repository, 'parser.py')])
        write(repository, node)
        commit(repository)
        baseline = appmap.import_baseline(store, repository)['snapshot']
        snapshot = map_updates.create_overlay(store, repository, baseline)['snapshot']
        map_options = {'map_snapshot': snapshot, 'map_record_ids': ['parser']}
    job = native.prepare(store, 'eng-001', run, 'main', 'request', **map_options)
    body = protocol.frozen(store, job)
    response = answer(body, [create(event, text='The parser rejects unknown fields.')])
    if with_map:
        response['map_patch'] = {'schema_version': 1, 'patch_id': 'pass-'+body['pass_id'], 'task_id': 'T',
            'run_id': run, 'snapshot': snapshot, 'read_versions': {'parser': 1}, 'evidence_ids': [event['event_id']],
            'operations': [{'op': 'put', 'record': {**node, 'version': 2, 'summary': 'The parser rejects unknown fields.'}}]}
    read = {'event_id': event['event_id'], 'offset': 0, 'limit': 1024}
    with replay('read-evidence', 'submit-patch', read=read, patch=response) as sent:
        result = native.execute(store, job['job_id'])
    assert result['status'] == 'committed', result
    assert result['attempts'] == 1 and result['read_bytes'] == 1024 and len(sent) == 2
    assert store.db.execute('SELECT count(*) FROM records').fetchone()[0] == 1
    assert {tool['name'] for request in sent for tool in request['body']['tools']} == {'read_evidence', 'submit_patch'}
    if with_map:
        assert 'map' in sent[0]['body']['messages'][0]['content'][0]['text'].lower()
        assert result['result']['map_patch']['records']['parser']['version'] == 2
        assert appmap.get_record(store, repository, snapshot, 'parser')['record']['summary'] == 'The parser rejects unknown fields.'
        with pytest.raises(Conflict, match='different inputs'):
            job_for(planned)


def test_stale_cursor_is_rejected_before_any_request(planned):
    store, _, _ = planned
    job = job_for(planned)
    body = protocol.frozen(store, job)
    passes.commit(store, body['pass_id'], answer(body, disposition='no_change'))
    with silent(), pytest.raises(Conflict, match='cursor changed'):
        native.execute(store, job['job_id'])
    assert native.status(store, job['job_id'])['status'] == 'prepared'


def test_interrupted_execution_keeps_slot_and_does_not_resend(planned, monkeypatch):
    store, _, _ = planned
    job = job_for(planned)
    def interrupted(request):
        raise KeyboardInterrupt()
    monkeypatch.setattr(companion_api, 'TRANSPORT', httpx.MockTransport(interrupted))
    with pytest.raises(KeyboardInterrupt):
        native.execute(store, job['job_id'])
    with ContinuityStore(store.root) as reopened, silent():
        assert native.execute(reopened, job['job_id'])['status'] == 'running'
    assert store.db.execute('SELECT classified_seq FROM cursors').fetchone()[0] == 0


def test_cli_prepares_and_inspects_same_job_without_native_start(configured, monkeypatch, capsys):
    from hx import companion
    from hx import jev
    store, _, run = configured
    monkeypatch.setattr(jev, 'evaluate', lambda state, questions, **kwargs: {
        'model': jev.MODEL, 'answers': {key: {'type': 'noul', 'noul': .95} for key in questions},
        'usage': {'input_tokens': 100, 'output_tokens': 20}, 'latency_ms': 1})
    api_key(store.root)
    with store.transaction() as tx:
        tx.append_event(run, 'main', 'native-cli', 'tool_result', {'text': 'Current unit observation.'})
    args = ['eng-001', '--run', run, '--stream', 'main', '--request', 'cli', '--prepare-only']
    env = {'TYPESAFE_API_KEY': 'fixture-key'}
    assert companion.main(args, store.root, env=env) == 0
    first = json.loads(capsys.readouterr().out)
    assert first['status'] == 'prepared'
    assert companion.main(args, store.root, env=env) == 0
    assert json.loads(capsys.readouterr().out) == first
    assert companion.main(['eng-001', '--job', first['job_id']], store.root, env=env) == 0
    assert json.loads(capsys.readouterr().out) == first


def test_cli_cannot_prepare_worker_execution_for_a_raw_persistence_fixture(planned):
    from hx import companion
    store, run, _ = planned
    with pytest.raises(Conflict, match='active planned run'):
        companion.main(['eng-001', '--run', run, '--request', 'raw', '--prepare-only'], store.root, env={})
    assert not store.db.execute('SELECT 1 FROM companion_jobs').fetchone()


def test_success_after_one_rejected_patch_installs_only_the_valid_response(planned):
    store, _, event = planned
    job = job_for(planned)
    activate(store, job)
    body = protocol.frozen(store, job)
    invalid = create(event)
    invalid['record']['evidence'] = ['unobserved']
    assert not protocol.call(store, job['job_id'], 'submit_patch', {'response': answer(body, [invalid])})['accepted']
    assert store.db.execute('SELECT count(*) FROM records').fetchone()[0] == 0
    assert protocol.call(store, job['job_id'], 'submit_patch', {'response': answer(body, [create(event)])})['accepted']
    assert store.db.execute('SELECT count(*) FROM records').fetchone()[0] == 1
    assert native.status(store, job['job_id'])['attempts'] == 2


def test_bookkeeping_commits_without_a_request_and_preserves_late_events(planned):
    store, run, _ = planned
    initial = job_for(planned)
    passes.commit(store, initial['pass_id'], answer(protocol.frozen(store, initial), disposition='no_change'))
    with store.transaction() as tx:
        event = tx.append_event(run, 'main', 'usage', 'boundary',
            {'observation': {'data': {'source': 'token_count', 'last_usage': {'input_tokens': 10}}}})
    job = job_for(planned, 'bookkeeping')
    with silent():
        result = native.execute(store, job['job_id'])
    assert result['status'] == 'committed' and result['deterministic']
    assert result['attempts'] == 0 and result['read_bytes'] == 0
    with store.transaction() as tx:
        late = tx.append_event(run, 'main', 'late', 'correction', {'text': 'Preserve this correction.'})
    assert job_for(planned, 'bookkeeping')['job_id'] == job['job_id']
    assert store.db.execute('SELECT disposition FROM events WHERE event_id=?', (event['event_id'],)).fetchone()[0] == 'reduced'
    assert store.db.execute('SELECT disposition FROM events WHERE event_id=?', (late['event_id'],)).fetchone()[0] is None


@pytest.mark.parametrize('data', [
    {'source': 'model_response', 'status': 'failed', 'error': 'Request rejected.'},
    {'source': 'model_response', 'status': 'success', 'error': 'Incomplete output.'},
    {'source': 'token_count', 'correction': 'Use a different goal.'},
    {'source': 'model_response', 'status': 'success', 'finding': 'New application fact.'},
    {'source': 'unrecognized'},
])
def test_unknown_or_diagnostic_boundaries_require_classification(data):
    assert not native._bookkeeping({'kind': 'boundary', 'payload': {'observation': {'data': data}}})


def test_interrupted_deterministic_commit_replays_without_model_or_inspection_writes(planned, monkeypatch):
    store, run, _ = planned
    initial = job_for(planned)
    passes.commit(store, initial['pass_id'], answer(protocol.frozen(store, initial), disposition='no_change'))
    with store.transaction() as tx:
        tx.append_event(run, 'main', 'usage', 'boundary',
            {'observation': {'data': {'source': 'token_count', 'last_usage': {'input_tokens': 10}}}})
    original = native._reduce
    def interrupted(store, job):
        original(store, job)
        store.db.execute("UPDATE companion_jobs SET status='reducing' WHERE job_id=?", (job['job_id'],))
        raise KeyboardInterrupt()
    monkeypatch.setattr(native, '_reduce', interrupted)
    with pytest.raises(KeyboardInterrupt):
        job_for(planned, 'bookkeeping')
    job_id = store.db.execute("SELECT job_id FROM companion_jobs WHERE status='reducing'").fetchone()[0]
    assert native.status(store, job_id)['status'] == 'reducing'
    monkeypatch.setattr(native, '_reduce', original)
    with silent():
        assert native.execute(store, job_id)['status'] == 'committed'
    assert store.db.execute('SELECT classified_seq FROM cursors').fetchone()[0] == 2


