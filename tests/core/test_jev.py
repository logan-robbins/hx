from __future__ import annotations

import copy
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hx import jev, jev_selection, native_companion, companion_protocol
from hx.continuity_store import Conflict
from hx.errors import ValidationError
from .test_native_companion import planned
from .test_passes import create


QUESTIONS = {'relevant': {'type': 'noul', 'instructions': 'Is the fact useful for the current goal?'},
             'next': {'type': 'choice', 'instructions': 'Which action comes next?',
                      'criteria': {'edit': 'Edit the parser.', 'test': 'Run the parser tests.'}}}
RESPONSE = {'model': jev.MODEL, 'answers': {'relevant': {'type': 'noul', 'noul': 0.97},
            'next': {'type': 'choice', 'choice': 'edit', 'confidence': 0.6, 'probabilities': {'edit': 0.8, 'test': 0.2}}},
            'usage': {'input_tokens': 123, 'output_tokens': 35}}


@contextmanager
def transport(response=RESPONSE, *, status=200, delay=0, oversized=False):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            size = int(self.headers['Content-Length'])
            assert size <= jev.INPUT_BYTES
            requests.append({'path': self.path, 'authorization': self.headers.get('Authorization'),
                             'body': json.loads(self.rfile.read(size))})
            raw = b'x' * (jev.OUTPUT_BYTES + 1) if oversized else json.dumps(response).encode()
            time.sleep(delay)
            try:
                self.send_response(status)
                self.send_header('Content-Length', str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/v1/systemone', requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_actual_http_contract_sends_one_batched_request_and_validates_response():
    with transport() as (endpoint, requests):
        result = jev.evaluate({'goal': 'Fix the parser.'}, QUESTIONS, key='local-fixture-key', endpoint=endpoint)
    assert len(requests) == 1
    assert requests[0]['path'] == '/v1/systemone'
    assert requests[0]['authorization'] == 'Bearer local-fixture-key'
    assert requests[0]['body']['model'] == 'jev-1.13.0'
    assert requests[0]['body']['questions'] == QUESTIONS
    assert result['answers'] == RESPONSE['answers'] and result['usage'] == RESPONSE['usage']


@pytest.mark.parametrize('failure', ['missing_id', 'invented_option', 'nan', 'boolean', 'unnormalized', 'wrong_type', 'wrong_model'])
def test_invalid_answers_never_become_decisions(failure):
    response = copy.deepcopy(RESPONSE)
    if failure == 'missing_id':
        del response['answers']['next']
    elif failure == 'invented_option':
        response['answers']['next']['choice'] = 'invented'
    elif failure == 'nan':
        response['answers']['relevant']['noul'] = float('nan')
    elif failure == 'boolean':
        response['answers']['relevant']['noul'] = True
    elif failure == 'unnormalized':
        response['answers']['next']['probabilities']['edit'] = 0.9
    elif failure == 'wrong_type':
        response['answers']['relevant']['type'] = 'choice'
    else:
        response['model'] = 'jev-latest'
    with pytest.raises(jev.Unavailable, match='invalid_response'):
        jev.validate(response, QUESTIONS)


@pytest.mark.parametrize('settings,reason', [({'status': 429}, 'http_429'), ({'delay': 0.2}, 'timeout'),
                                           ({'oversized': True}, 'output_budget')])
def test_transport_failure_has_no_retry_or_substitute(settings, reason):
    with transport(**settings) as (endpoint, requests):
        started = time.monotonic()
        with pytest.raises(jev.Unavailable, match=reason):
            jev.evaluate('goal', QUESTIONS, key='local-fixture-key', endpoint=endpoint,
                         timeout=0.05 if reason == 'timeout' else jev.DEADLINE)
        assert time.monotonic() - started < 1
    assert len(requests) <= 1


def test_input_bound_prevents_any_network_call(monkeypatch):
    async def forbidden(*args):
        pytest.fail('oversized inputs must not be sent')
    monkeypatch.setattr(jev, '_post', forbidden)
    with pytest.raises(jev.Unavailable, match='input_budget'):
        jev.evaluate('x' * 4000, QUESTIONS, key='fixture')


def test_named_dotenv_key_is_read_without_copying_other_entries(tmp_path):
    path = tmp_path / '.env'
    path.write_text('UNRELATED=not-used\nexport TYPESAFE_API_KEY="fixture-key" # comment\nOTHER=not-read\n')
    assert jev.credential(tmp_path, {'TYPESAFE_API_KEY_FILE': str(path / 'TYPESAFE_API_KEY')}) == 'fixture-key'
    assert jev.credential(tmp_path, {'TYPESAFE_API_KEY_FILE': str(path)}) == 'fixture-key'


def facts(planned):
    store, _, event = planned
    with store.transaction() as tx:
        for name, text in [('current', 'The parser accepts unknown fields.'), ('unrelated', 'The settings page uses a blue icon.')]:
            tx.put_record(name, expected_version=0, task_id='T', **create(event, name, text=text)['record'])
        tx.put_record('constraint', expected_version=0, task_id='T', **create(event, 'constraint', kind='constraint', text='Preserve the public API.')['record'])


def score(state, questions, **kwargs):
    return {'model': jev.MODEL, 'answers': {name: {'type': 'noul', 'noul': 0.97 if 'parser' in question['instructions']['fact'] else 0.02}
            for name, question in questions.items()}, 'usage': {'input_tokens': 100, 'output_tokens': 20}, 'latency_ms': 10}


def test_native_pass_uses_jev_selection_and_keeps_required_facts(planned, monkeypatch):
    store, run, _ = planned
    facts(planned)
    calls = []
    def recorded(*args, **kwargs):
        calls.append(args)
        assert not store.db.in_transaction, 'API call must not hold the ledger writer lock'
        return score(*args, **kwargs)
    monkeypatch.setattr(jev, 'evaluate', recorded)
    job = native_companion.prepare(store, 'eng-001', run, 'main', 'request', env={'TYPESAFE_API_KEY': 'fixture-key'})
    body = companion_protocol.frozen(store, job)
    assert set(body['record_versions']) == {'current', 'constraint'}
    assert job['payload']['jev']['status'] == 'complete' and len(calls) == 1
    assert 'scores' not in job['payload']['jev'] and 'fixture-key' not in json.dumps(job)
    assert native_companion.prepare(store, 'eng-001', run, 'main', 'request')['job_id'] == job['job_id']
    assert len(calls) == 1
    # A second prepared request for identical inputs reuses the API result too.
    native_companion.prepare(store, 'eng-001', run, 'main', 'second')
    assert len(calls) == 1


@pytest.mark.parametrize('reason', ['timeout', 'invalid_response', 'http_401'])
def test_failed_jev_stops_preparation_without_fallback_or_native_execution(planned, monkeypatch, reason):
    store, run, _ = planned
    facts(planned)
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise jev.Unavailable(reason)
    monkeypatch.setattr(jev, 'evaluate', fail)
    monkeypatch.setattr(native_companion.checks, 'execute', lambda *a, **kw: pytest.fail('must not run native model'))
    with pytest.raises(ValidationError, match='Jev selection stopped: '+reason):
        native_companion.prepare(store, 'eng-001', run, 'main', 'request', env={'TYPESAFE_API_KEY': 'fixture-key'})
    assert len(calls) == 1
    assert store.db.execute('SELECT count(*) FROM companion_jobs').fetchone()[0] == 0
    assert store.db.execute('SELECT count(*) FROM passes').fetchone()[0] == 0
    assert store.db.execute('SELECT classified_seq FROM cursors').fetchone()[0] == 0


def test_changed_candidate_or_new_event_invalidates_jev_cache(planned, monkeypatch):
    store, run, event = planned
    facts(planned)
    calls = []
    monkeypatch.setattr(jev, 'evaluate', lambda *a, **kw: (calls.append(1), score(*a, **kw))[1])
    env = {'TYPESAFE_API_KEY': 'fixture-key'}
    first = jev_selection.for_pass(store, run, 'main', env=env)
    assert jev_selection.for_pass(store, run, 'main', env=env) == first
    with store.transaction() as tx:
        tx.put_record('current', expected_version=1, task_id='T', **create(event, 'current', text='The parser rejects unknown fields.')['record'])
    second = jev_selection.for_pass(store, run, 'main', env=env)
    assert second['records']['current'] == 2 and len(calls) == 2
    with store.transaction() as tx:
        tx.append_event(run, 'main', 'new', 'tool_result', {'text': 'New next action.'})
    jev_selection.for_pass(store, run, 'main', env=env)
    assert len(calls) == 3


def test_task_change_during_jev_call_rejects_result(planned, monkeypatch):
    store, run, _ = planned
    facts(planned)
    def amend(*args, **kwargs):
        with store.transaction() as tx:
            tx.put_task('T', {'goal': 'Different assignment.'}, expected_revision=1)
        return score(*args, **kwargs)
    monkeypatch.setattr(jev, 'evaluate', amend)
    with pytest.raises(Conflict, match='amended'):
        jev_selection.for_pass(store, run, 'main', env={'TYPESAFE_API_KEY': 'fixture-key'})
    assert store.db.execute('SELECT count(*) FROM retrieval_runs').fetchone()[0] == 0


def test_independent_nouls_batch_without_truncating_candidates():
    candidates = [{'text': 'Complete fact '+str(i)+' '+('x'*500)} for i in range(12)]
    batches = jev_selection._batches({'goal': 'Use current facts.'}, candidates)
    assert 1 < len(batches) <= 4
    assert sum(len(batch) for batch in batches) == len(candidates)
    assert all(len(jev.encode({'goal': 'Use current facts.'}, batch)) <= 4000 for batch in batches)


def test_routine_bookkeeping_has_no_semantic_decision_to_send(planned, monkeypatch):
    from hx import passes
    from .test_passes import answer
    store, run, _ = planned
    facts(planned)
    initial = passes.prepare(store, run, 'main', prompt_version='test')
    passes.commit(store, initial['pass_id'], answer(initial, disposition='no_change'))
    with store.transaction() as tx:
        tx.append_event(run, 'main', 'tokens', 'boundary',
            {'observation': {'data': {'source': 'token_count', 'last_usage': {'input_tokens': 12}}}})
    monkeypatch.setattr(jev, 'evaluate', lambda *a, **kw: pytest.fail('bookkeeping needs no semantic judgment'))
    result = jev_selection.for_pass(store, run, 'main')
    assert result['records'] == {}
