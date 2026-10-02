from __future__ import annotations

import io
import json
import os
import sys

import pytest

from hx import companion_protocol as protocol, native_companion as native, passes
from hx.continuity_store import ContinuityStore, Conflict, SCHEMA_VERSION
from hx.errors import ValidationError
from .test_passes import answer, create
from .test_prompt_compiler import configure


@pytest.fixture
def planned(instance):
    config = configure(instance)
    (instance / 'config/claude.json').write_text(json.dumps({'bin': sys.executable}))
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


def test_mcp_lists_only_pass_capabilities_and_refuses_foreign_evidence(planned):
    store, _, _ = planned
    job = job_for(planned)
    activate(store, job)
    requests = [{'id': 1, 'method': 'initialize'}, {'method': 'notifications/initialized'},
                {'id': 2, 'method': 'tools/list'}, {'id': 3, 'method': 'tools/call',
                'params': {'name': 'read_evidence', 'arguments': {'event_id': 'foreign', 'offset': 0, 'limit': 1}}}]
    source = io.BytesIO(('\n'.join(json.dumps(r) for r in requests)+'\n').encode())
    output = io.StringIO()
    protocol.serve(store.root, job['job_id'], source, output)
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert [r['id'] for r in replies] == [1, 2, 3]
    assert {tool['name'] for tool in replies[1]['result']['tools']} == {'read_evidence', 'submit_patch'}
    assert replies[2]['result']['isError']


def test_native_execution_uses_fresh_restricted_home_and_replays_without_call(planned, monkeypatch):
    store, _, event = planned
    job = job_for(planned)
    seen = []
    def run(argv, cwd, env, output, timeout, max_bytes, **kwargs):
        seen.append(cwd)
        assert cwd != store.root and env['HOME'] == str(cwd)
        assert 'PRIVATE' not in repr(env)
        assert argv[argv.index('--tools')+1] == ''
        assert '--no-session-persistence' in argv and '--strict-mcp-config' in argv
        body = json.loads(kwargs['stdin'].read())
        assert body['pass_id'] == job['pass_id'] and len(body['events']) == 1
        assert protocol.call(store, job['job_id'], 'submit_patch', {'response': answer(body, [create(event)])})['accepted']
        return {'exit_code': 0, 'reason': None, 'output_bytes': 0}
    monkeypatch.setattr(native.checks, 'execute', run)
    result = native.execute(store, job['job_id'], env={'PATH': '/bin', 'PRIVATE': 'PRIVATE'})
    assert result['status'] == 'committed' and not seen[0].exists()
    assert native.execute(store, job['job_id']) == result and len(seen) == 1


def test_execution_without_patch_marks_evidence_pending_and_never_restarts(planned, monkeypatch):
    store, _, _ = planned
    job = job_for(planned)
    monkeypatch.setattr(native.checks, 'execute', lambda *a, **kw: {'exit_code': 1, 'reason': 'timeout', 'output_bytes': 0})
    result = native.execute(store, job['job_id'])
    assert result['status'] == 'unresolved'
    assert store.db.execute('SELECT disposition FROM events').fetchone()[0] == 'pending'
    assert store.db.execute('SELECT classified_seq FROM cursors').fetchone()[0] == 1
    monkeypatch.setattr(native.checks, 'execute', lambda *a, **kw: pytest.fail('must not restart'))
    assert native.execute(store, job['job_id']) == result


def test_running_job_owns_single_slot_without_expiring(planned, monkeypatch):
    store, _, _ = planned
    job = job_for(planned)
    other = job_for(planned, 'other')
    activate(store, job)
    monkeypatch.setattr(native.checks, 'execute', lambda *a, **kw: pytest.fail('must not spawn'))
    assert native.execute(store, job['job_id'])['status'] == 'running'
    with pytest.raises(Conflict, match='slot'):
        native.execute(store, other['job_id'])


def test_schema_fifteen_upgrade_preserves_passes(planned):
    store, _, _ = planned
    job = job_for(planned)
    store.db.execute('DROP TABLE companion_jobs')
    store.db.execute('PRAGMA user_version=15')
    with ContinuityStore(store.root) as reopened:
        assert reopened.db.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
        assert reopened.db.execute('SELECT pass_id FROM passes').fetchone()[0] == job['pass_id']
        assert reopened.db.execute('SELECT count(*) FROM companion_jobs').fetchone()[0] == 0


@pytest.mark.skipif(not os.environ.get('HX_CLAUDE_TEST_BIN'), reason='requires explicitly selected installed Claude; local provider fixture only')
@pytest.mark.parametrize('with_map', [False, True])
def test_installed_claude_companion_reads_scoped_evidence_and_commits_patch(planned, with_map):
    from .claude_companion_fixture import transport
    store, run, event = planned
    (store.root / 'config/claude.json').write_text(json.dumps({'bin': os.environ['HX_CLAUDE_TEST_BIN']}))
    (store.root / 'seed/token').write_text('sk-ant-api-fixture-local-only')
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
    with transport(body, response) as (url, requests):
        result = native.execute(store, job['job_id'], env={'PATH': os.environ['PATH'], 'ANTHROPIC_BASE_URL': url})
    assert result['status'] == 'committed', result
    assert result['attempts'] == 1 and result['read_bytes'] == 1024
    assert store.db.execute('SELECT count(*) FROM records').fetchone()[0] == 1
    assert requests
    tool_names = {tool['name'] for request in requests for tool in request.get('tools', [])}
    assert {'mcp__continuity__read_evidence', 'mcp__continuity__submit_patch'} <= tool_names
    assert tool_names <= {'mcp__continuity__read_evidence', 'mcp__continuity__submit_patch', 'EndConversation', 'ToolSearch'}
    assert any('accepted' in json.dumps(request['messages']) for request in requests)
    if with_map:
        assert result['result']['map_patch']['records']['parser']['version'] == 2
        assert appmap.get_record(store, repository, snapshot, 'parser')['record']['summary'] == 'The parser rejects unknown fields.'
        with pytest.raises(Conflict, match='different inputs'):
            job_for(planned)


def test_stale_cursor_is_rejected_before_any_native_call(planned, monkeypatch):
    store, _, _ = planned
    job = job_for(planned)
    body = protocol.frozen(store, job)
    passes.commit(store, body['pass_id'], answer(body, disposition='no_change'))
    monkeypatch.setattr(native.checks, 'execute', lambda *a, **kw: pytest.fail('stale pass must not start'))
    with pytest.raises(Conflict, match='cursor changed'):
        native.execute(store, job['job_id'])
    assert native.status(store, job['job_id'])['status'] == 'prepared'


def test_interrupted_execution_keeps_slot_and_does_not_resend(planned, monkeypatch):
    store, _, _ = planned
    job = job_for(planned)
    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt()
    monkeypatch.setattr(native.checks, 'execute', interrupted)
    with pytest.raises(KeyboardInterrupt):
        native.execute(store, job['job_id'])
    with ContinuityStore(store.root) as reopened:
        assert native.execute(reopened, job['job_id'])['status'] == 'running'
    assert store.db.execute('SELECT classified_seq FROM cursors').fetchone()[0] == 0


def test_cli_prepares_and_inspects_same_job_without_native_start(planned, capsys):
    from hx import companion
    store, run, _ = planned
    args = ['eng-001', '--run', run, '--stream', 'main', '--request', 'cli', '--prepare-only']
    assert companion.main(args, store.root, env={}) == 0
    first = json.loads(capsys.readouterr().out)
    assert first['status'] == 'prepared'
    assert companion.main(args, store.root, env={}) == 0
    assert json.loads(capsys.readouterr().out) == first
    assert companion.main(['eng-001', '--job', first['job_id']], store.root, env={}) == 0
    assert json.loads(capsys.readouterr().out) == first


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


def test_bookkeeping_commits_without_native_execution_and_preserves_late_events(planned, monkeypatch):
    store, run, _ = planned
    initial = job_for(planned)
    passes.commit(store, initial['pass_id'], answer(protocol.frozen(store, initial), disposition='no_change'))
    with store.transaction() as tx:
        event = tx.append_event(run, 'main', 'usage', 'boundary',
            {'observation': {'data': {'source': 'token_count', 'last_usage': {'input_tokens': 10}}}})
    job = job_for(planned, 'bookkeeping')
    monkeypatch.setattr(native.checks, 'execute', lambda *a, **kw: pytest.fail('bookkeeping requires no model'))
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
    monkeypatch.setattr(native.checks, 'execute', lambda *a, **kw: pytest.fail('must not start'))
    assert native.execute(store, job_id)['status'] == 'committed'
    assert store.db.execute('SELECT classified_seq FROM cursors').fetchone()[0] == 2
