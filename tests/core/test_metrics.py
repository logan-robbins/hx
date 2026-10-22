"""Execution metrics use ledger records and captured events, never work item text or transcript files."""
import json

from hx import metrics, native_producer, observer
from hx.codex_rollout import DECODER, VERSION
from hx.continuity_store import ContinuityStore
from .test_board import seed


def test_ledger_metrics(tmp_path):
    seed(tmp_path)
    result=metrics.collect_fleet(tmp_path)
    assert result['packets']==1
    assert result['active_runs']==1
    assert result['leases']==1
    assert result['valid_checks']==1
    assert result['failed_checks']==0
    assert result['check_seconds']==0.5
    assert result['acknowledged_checkpoints']==1
    assert result['agents']=={'be-001': {'flavors': [], 'chat_bytes': None, 'output_tokens': None, 'turns': None}}
    assert metrics.per_id(tmp_path,'be-001')['packets']==1


def test_metrics_empty_without_ledger(tmp_path):
    (tmp_path/'tasks.json').write_text('obsolete')
    assert metrics.collect_fleet(tmp_path)['packets']==0


def test_json_cli(tmp_path,capsys):
    seed(tmp_path)
    assert metrics.main(['--json'],tmp_path)==0
    assert '"active_runs": 1' in capsys.readouterr().out


def transcript(store, run, stream, path, decoder, session, *records):
    path.write_text(''.join(json.dumps(record) + '\n' for record in records))
    observer.drain(store, observer.register(store, run_id=run, stream_id=stream, path=path, decoder=decoder, session_id=session))


def hook(store, run, worker, adapter, session, payload, decoder='hook'):
    native_producer.produce(store, run_id=run, worker_id=worker, adapter=adapter, session_id=session,
                            stream_id='native:' + adapter, payload=payload, decoder=decoder)


def claude(uuid, message_id, content, output):
    return {'type': 'assistant', 'uuid': uuid, 'message': {'id': message_id, 'role': 'assistant', 'content': content,
            'usage': {'input_tokens': 10, 'output_tokens': output}}}


def pi(text, output, *, role='assistant'):
    return {'type': 'message_end', 'message': {'role': role, 'content': [{'type': 'text', 'text': text}],
            'usage': {'input': 20, 'output': output}}}


def codex(kind, **payload):
    return {'timestamp': '2026-10-01T00:00:00Z', 'type': kind, 'payload': payload}


def test_agent_figures_count_only_captured_main_session_records_per_flavor(tmp_path):
    seed(tmp_path)
    workers = {'be-001': ('run-1', 'claude'), 'pi-001': ('run-2', 'pi'), 'cx-001': ('run-3', 'codex'),
               'mt-001': ('run-4', 'meta'), 'gk-001': ('run-5', 'grok')}
    with ContinuityStore(tmp_path) as store:
        for worker, (run, adapter) in workers.items():
            if run != 'run-1':
                store.db.execute("INSERT INTO runs VALUES(?,'orders.work',1,?,0,'implementing',NULL,NULL,1,NULL)", (run, worker))
            store.db.execute('INSERT INTO native_launch_contracts VALUES(?,?,?,?)', ('launch-' + run, run, worker, adapter))
        store.db.commit()
        transcript(store, 'run-1', 'native:claude', tmp_path / 'claude.jsonl', 'claude', 'S',
            {'type': 'user', 'uuid': 'u1', 'message': {'role': 'user', 'content': 'Fix the parser.'}},
            claude('a1', 'msg_1', [{'type': 'text', 'text': 'Done.'}], 3),
            claude('a2', 'msg_1', [{'type': 'tool_use', 'id': 'T1', 'name': 'Bash', 'input': {'command': 'true'}}], 7),
            claude('a3', 'msg_2', [{'type': 'text', 'text': 'Ünïcode'}], 5))
        # A child transcript and the Stop hook's copy of the last message are not main-session chat.
        transcript(store, 'run-1', 'child:helper', tmp_path / 'child.jsonl', 'claude', 'S',
            claude('c1', 'msg_9', [{'type': 'text', 'text': 'child chat'}], 50))
        hook(store, 'run-1', 'be-001', 'claude', 'S', {'hook_event_name': 'Stop', 'last_assistant_message': 'Done.'})
        hook(store, 'run-2', 'pi-001', 'pi', 'P', pi('Fix the parser.', 0, role='user'), 'pi')
        hook(store, 'run-2', 'pi-001', 'pi', 'P', pi('Patched.', 6), 'pi')
        hook(store, 'run-2', 'pi-001', 'pi', 'P', pi('x' * 5000, 4), 'pi')
        transcript(store, 'run-3', 'native:codex', tmp_path / 'rollout.jsonl', DECODER, 'C',
            codex('session_meta', id='C', session_id='C', cli_version=VERSION),
            codex('response_item', type='function_call', call_id='K', name='exec_command', arguments='{"cmd":"true"}'),
            codex('token_usage_record', response_id='r1', usage={'input_tokens': 8, 'output_tokens': 4}),
            codex('response_item', type='message', role='assistant', id='A', content=[{'type': 'output_text', 'text': 'Check failed.'}]),
            codex('token_usage_record', response_id='r2', usage={'input_tokens': 9, 'output_tokens': 6}))
        for request, status, output in (('q1', 'success', 9), ('q2', 'failed', 4)):
            hook(store, 'run-4', 'mt-001', 'meta', 'M', {'hook_event_name': 'PostLLMCall', 'request_id': request,
                 'status': status, 'usage': {'output_tokens': output}})
        hook(store, 'run-4', 'mt-001', 'meta', 'M', {'hook_event_name': 'Stop', 'last_assistant_message': 'Finished.'})
        hook(store, 'run-5', 'gk-001', 'grok', 'G', {'hook_event_name': 'Stop', 'last_assistant_message': 'Finished.'})
    agents = metrics.collect_fleet(tmp_path)['agents']
    assert agents == {
        'be-001': {'flavors': ['claude'], 'chat_bytes': 14, 'output_tokens': 12, 'turns': 2},
        'cx-001': {'flavors': ['codex'], 'chat_bytes': 13, 'output_tokens': 10, 'turns': 2},
        'gk-001': {'flavors': ['grok'], 'chat_bytes': None, 'output_tokens': None, 'turns': None},
        'mt-001': {'flavors': ['meta'], 'chat_bytes': None, 'output_tokens': 9, 'turns': 1},
        'pi-001': {'flavors': ['pi'], 'chat_bytes': 5008, 'output_tokens': 10, 'turns': 2}}
    assert metrics.per_id(tmp_path, 'pi-001')['agents'] == {'pi-001': agents['pi-001']}
    with ContinuityStore(tmp_path) as store:
        store.db.execute("""UPDATE events SET payload='{"retired":true}' WHERE run_id='run-2'
            AND kind='assistant_message' AND json_extract(payload,'$.artifact_hash') IS NOT NULL""")
        store.db.commit()
    assert metrics.collect_fleet(tmp_path)['agents']['pi-001'] == {'flavors': ['pi'], 'chat_bytes': 8, 'output_tokens': 6, 'turns': 1}
