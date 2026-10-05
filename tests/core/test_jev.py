from __future__ import annotations

import copy
import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from hx import jev, jev_deltas, passes, native_companion, companion_protocol
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
    return {'model': jev.MODEL, 'answers': {name: {'type': 'noul', 'noul': .95}
            for name in questions}, 'usage': {'input_tokens': 100, 'output_tokens': 20}, 'latency_ms': 10}


def test_native_pass_classifies_deltas_without_selecting_facts(planned, monkeypatch):
    store, run, event = planned
    facts(planned)
    calls = []
    def recorded(*args, **kwargs):
        calls.append(args)
        assert not store.db.in_transaction, 'API call must not hold the ledger writer lock'
        return score(*args, **kwargs)
    monkeypatch.setattr(jev, 'evaluate', recorded)
    job = native_companion.prepare(store, 'eng-001', run, 'main', 'request', record_ids=('unrelated',))
    body = companion_protocol.frozen(store, job)
    assert set(body['record_versions']) == {'unrelated', 'constraint'}
    assert job['payload']['jev']['events'][event['event_id']]['route'] == 'review_delta'
    assert 'observation routes' in job['payload']['system']
    assert 'fixture-key' not in json.dumps(job)
    assert native_companion.prepare(store, 'eng-001', run, 'main', 'request', record_ids=('unrelated',))['job_id'] == job['job_id']
    native_companion.prepare(store, 'eng-001', run, 'main', 'second', record_ids=('unrelated',))
    assert len(calls) == 1


@pytest.mark.parametrize('reason', ['timeout', 'invalid_response', 'http_401'])
def test_failed_jev_stops_preparation_without_fallback_or_native_execution(planned, monkeypatch, reason):
    store, run, _ = planned
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise jev.Unavailable(reason)
    monkeypatch.setattr(jev, 'evaluate', fail)
    monkeypatch.setattr(native_companion.checks, 'execute', lambda *a, **kw: pytest.fail('must not run native model'))
    with pytest.raises(ValidationError, match='Jev decision stopped: '+reason):
        native_companion.prepare(store, 'eng-001', run, 'main', 'request')
    assert len(calls) == 1
    assert store.db.execute('SELECT count(*) FROM companion_jobs').fetchone()[0] == 0
    assert store.db.execute('SELECT classified_seq FROM cursors').fetchone()[0] == 0


def test_changed_observation_or_current_record_invalidates_jev_cache(planned, monkeypatch):
    store, run, event = planned
    facts(planned)
    calls = []
    monkeypatch.setattr(jev, 'evaluate', lambda *a, **kw: (calls.append(1), score(*a, **kw))[1])
    def classify():
        body = passes.prepare(store, run, 'main', prompt_version='test', record_ids=('current',))
        return jev_deltas.classify(store, body)
    first = classify()
    assert classify() == first
    with store.transaction() as tx:
        tx.put_record('current', expected_version=1, task_id='T', **create(event, 'current', text='The parser rejects unknown fields.')['record'])
    classify()
    assert len(calls) == 2
    with store.transaction() as tx:
        tx.append_event(run, 'main', 'new', 'tool_result', {'text': 'New next action.'})
    classify()
    assert len(calls) == 3


def test_task_change_during_jev_call_rejects_result(planned, monkeypatch):
    store, run, _ = planned
    body = passes.prepare(store, run, 'main', prompt_version='test')
    def amend(*args, **kwargs):
        with store.transaction() as tx:
            tx.put_task('T', {'goal': 'Different assignment.'}, expected_revision=1)
        return score(*args, **kwargs)
    monkeypatch.setattr(jev, 'evaluate', amend)
    with pytest.raises(Conflict, match='amended'):
        jev_deltas.classify(store, body)
    assert store.db.execute('SELECT count(*) FROM retrieval_runs').fetchone()[0] == 0


def test_large_observation_requests_scoped_read_without_truncating(planned, monkeypatch):
    store, run, _ = planned
    body = passes.prepare(store, run, 'main', prompt_version='test')
    body['events'][0]['payload'] = {'text': 'x' * 6000}
    monkeypatch.setattr(jev, 'evaluate', lambda *a, **kw: pytest.fail('no unbounded requests'))
    result = jev_deltas.classify(store, body)
    assert list(result['events'].values()) == [{'route': 'read_evidence'}]


def test_routine_bookkeeping_has_no_semantic_decision_to_send(planned, monkeypatch):
    from .test_passes import answer
    store, run, _ = planned
    initial = passes.prepare(store, run, 'main', prompt_version='test')
    passes.commit(store, initial['pass_id'], answer(initial, disposition='no_change'))
    with store.transaction() as tx:
        tx.append_event(run, 'main', 'tokens', 'boundary',
            {'observation': {'data': {'source': 'token_count', 'last_usage': {'input_tokens': 12}}}})
    monkeypatch.setattr(jev, 'evaluate', lambda *a, **kw: pytest.fail('bookkeeping needs no semantic judgment'))
    body = passes.prepare(store, run, 'main', prompt_version='test')
    result = jev_deltas.classify(store, body)
    assert result['requests'] == []


def test_repetition_reduces_without_a_generative_pass(planned, monkeypatch):
    store, run, event = planned
    facts(planned)
    def repetition(state, questions, **kwargs):
        return {**score(state, questions), 'answers': {name: {'type': 'noul', 'noul': .01} for name in questions}}
    monkeypatch.setattr(jev, 'evaluate', repetition)
    job = native_companion.prepare(store, 'eng-001', run, 'main', 'repeat', record_ids=('current',))
    assert job['status'] == 'committed'
    assert job['payload']['semantic_reduction'] and not job['payload']['deterministic']
    assert store.db.execute('SELECT disposition FROM events WHERE event_id=?', (event['event_id'],)).fetchone()[0] == 'no_change'
    with store.transaction() as tx:
        assert tx.record('current')['version'] == 1


def test_failure_and_uncertainty_require_companion_review(planned, monkeypatch):
    store, run, _ = planned
    facts(planned)
    with store.transaction() as tx:
        tx.append_event(run, 'main', 'failure', 'tool_result', {'is_error': True, 'error': 'A test failed.'})
    monkeypatch.setattr(jev, 'evaluate', lambda state, questions, **kwargs: {
        **score(state, questions), 'answers': {name: {'type': 'noul', 'noul': .01} for name in questions}})
    job = native_companion.prepare(store, 'eng-001', run, 'main', 'failure', record_ids=('current',))
    assert job['status'] == 'prepared'


def test_pi_failure_flag_never_becomes_repetition():
    assert jev_deltas._failed({'observation': {'data': {'tool_response': {'isError': True}}}})
