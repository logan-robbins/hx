from __future__ import annotations

import json
import os
import shlex
import subprocess
import tomllib
from io import StringIO
from pathlib import Path

import pytest

from hx import hooks, native_controller as controller, native_launch, native_producer, native_tools, unit_execution
from hx.continuity_store import Conflict, ContinuityStore, SCHEMA_VERSION
from .test_appmap import mapped
from .test_context_packets import assignment
from .test_map_updates import active
from .test_native_launch import configured
from .test_unit_execution import fleet
from .test_native_controller import runtime, flavor, ready
from .conftest import wait_for


def submitted(runtime):
    store, _, run, env = runtime
    ready(runtime)
    controller.advance(store, 'eng-001', run, 'launch', env=env)
    wait_for(lambda: native_launch._row(store, run)['status'] == 'submitted', what='submission', limit=10)
    return native_launch._row(store, run)


def payload(call='call-1', **changes):
    return {'session_id': 'test-native-session', 'tool_use_id': call, 'tool_name': 'Bash',
            'tool_input': {'command': 'true'}, **changes}


def fire(store, run, row, event, body):
    return hooks.main(['--root', str(store.root), '--id', 'eng-001', '--continuity-run', run,
        '--continuity-launch', row['launch_id'], '--continuity-adapter', 'claude', event],
        stdin=StringIO(json.dumps(body)), env={})


def installed_command(row, adapter, event):
    home = Path(row['payload']['capsule']) / 'run/eng-001/home'
    if adapter == 'pi':
        contract = json.loads((home / 'extensions/hx/hook-contract.json').read_text())
        return [contract['command'], *contract['args'], event]
    settings = tomllib.loads((home / 'config.toml').read_text()) if adapter in {'codex', 'grok'} else json.loads(
        (home / ('muse/settings.json' if adapter == 'meta' else 'settings.json')).read_text())
    native_event = {'tool-start': 'PreToolUse', 'log': 'PostToolUse', 'log-failure': 'PostToolUseFailure'}[event]
    return shlex.split(settings['hooks'][native_event][0]['hooks'][0]['command'])


@pytest.mark.parametrize('adapter', ['claude', 'codex', 'grok', 'meta', 'pi'])
def test_installed_tool_admission_drain_and_settlement(runtime, adapter):
    store, _, run, env = runtime
    flavor(runtime, adapter)
    row = submitted(runtime)
    def invoke(event, body):
        # Exercise generated commands, including adapters with cleared environments.
        return subprocess.run(installed_command(row, adapter, event), input=json.dumps(body),
                              text=True, capture_output=True, env={'PATH': env['PATH']}, timeout=10)
    for call in ('one', 'two'):
        result = invoke('tool-start', payload(call))
        assert result.returncode == 0 and not result.stderr, result.stderr
    result = controller.drain(store, 'eng-001', run, 'launch', env=env)
    assert result['status'] == 'draining' and result['pending_tool_calls'] == 2
    denied = invoke('tool-start', payload('three'))
    assert denied.returncode == 2 and 'admission is closed' in denied.stderr
    if adapter == 'codex':
        assert json.loads(denied.stdout)['hookSpecificOutput']['permissionDecision'] == 'deny'
    for call in ('one', 'two'):
        event = 'log-failure' if call == 'two' and adapter in {'claude', 'grok', 'meta'} else 'log'
        result = invoke(event, payload(call, tool_response={'exit_code': 1}))
        assert result.returncode == 0 and not result.stderr, result.stderr
    result = controller.drain(store, 'eng-001', run, 'launch', env=env)
    assert result['pending_tool_calls'] == 0 and result['status'] == 'draining'
    assert native_producer.status(store, run)['gap'] is None
    with pytest.raises(Conflict, match='session ownership'):
        unit_execution.stop(store, run)
    assert store.db.execute('SELECT count(*) FROM leases WHERE run_id=?', (run,)).fetchone()[0] > 0
    # No new paste or session is created when the launch request is retried.
    assert controller.advance(store, 'eng-001', run, 'launch', env=env)['status'] == 'draining'


def test_duplicate_call_conflicting_input_and_settled_reuse(runtime):
    store, _, run, _ = runtime
    row = submitted(runtime)
    assert fire(store, run, row, 'tool-start', payload()) == 0
    assert fire(store, run, row, 'tool-start', payload()) == 0
    assert native_tools.pending(store, row['launch_id']) == 1
    assert fire(store, run, row, 'tool-start', payload(tool_input={'command': 'false'})) == 2
    assert fire(store, run, row, 'log', payload(tool_response='done')) == 0
    assert fire(store, run, row, 'log', payload(tool_response='done')) == 0
    assert native_tools.pending(store, row['launch_id']) == 0
    assert fire(store, run, row, 'tool-start', payload()) == 2
    assert native_producer.status(store, run)['gap'] is None


def test_late_failure_and_session_end_preserve_outcomes_without_settling_work(runtime):
    store, _, run, _ = runtime
    row = submitted(runtime)
    child = {'session_id': 'test-native-session', 'agent_id': 'child-a', 'child_session_id': 'child-session'}
    assert fire(store, run, row, 'subagent-start', child) == 0
    call = payload(session_id='child-session', agent_id='child-a')
    assert fire(store, run, row, 'tool-start', call) == 0
    assert fire(store, run, row, 'request', {'session_id': 'test-native-session', 'prompt': 'Newer request', 'turn_id': 'new'}) == 0
    for event in ('stop-failure', 'stop-cancelled', 'session-end'):
        assert fire(store, run, row, event, {**child, 'turn_id': 'old', 'error': 'rate_limit',
            'error_details': 'Provider unavailable', 'last_assistant_message': 'Rendered error', 'private_reasoning': 'PRIVATE'}) == 0
    observations = [json.loads(item[0])['observation'] for item in store.db.execute('SELECT payload FROM events ORDER BY rowid DESC LIMIT 3')]
    assert all(item['kind'] == 'boundary' and item['data']['settled'] is False for item in observations)
    assert {item['data']['turn_id'] for item in observations} == {'old'}
    assert {item['data']['outcome'] for item in observations} == {'failed', 'cancelled', 'session_ended'}
    assert native_tools.pending(store, row['launch_id']) == 1
    assert store.db.execute('SELECT status FROM native_children WHERE actor_id=?', ('child-a',)).fetchone()[0] == 'active'
    assert store.db.execute('SELECT count(*) FROM leases WHERE run_id=?', (run,)).fetchone()[0] > 0
    assert native_launch._row(store, run)['status'] == 'submitted'
    assert 'PRIVATE' not in '\n'.join(store.db.iterdump())


def test_unmatched_result_retained_as_gap_without_settling_call(runtime):
    store, _, run, _ = runtime
    row = submitted(runtime)
    assert fire(store, run, row, 'tool-start', payload()) == 0
    before = store.db.execute("SELECT count(*) FROM events WHERE run_id=? AND kind='tool_result'", (run,)).fetchone()[0]
    assert fire(store, run, row, 'log', payload('unknown', tool_response='done')) == 0
    assert native_tools.pending(store, row['launch_id']) == 1
    assert native_producer.status(store, run)['gap']
    assert store.db.execute("SELECT count(*) FROM events WHERE run_id=? AND kind='tool_result'", (run,)).fetchone()[0] == before + 1


def test_queued_result_settles_atomically_when_retried(runtime, monkeypatch):
    store, _, run, _ = runtime
    row = submitted(runtime)
    assert fire(store, run, row, 'tool-start', payload()) == 0
    enqueue = native_producer.native_capture.enqueue
    def unavailable(*args, **kwargs):
        raise OSError('temporarily unavailable')
    monkeypatch.setattr(native_producer.native_capture, 'enqueue', unavailable)
    assert fire(store, run, row, 'log', payload(tool_response='done')) == 0
    assert native_tools.pending(store, row['launch_id']) == 1
    assert native_producer.status(store, run)['pending_deliveries'] == 1
    monkeypatch.setattr(native_producer.native_capture, 'enqueue', enqueue)
    native_producer.retry(store)
    assert native_tools.pending(store, row['launch_id']) == 0
    assert native_producer.status(store, run)['pending_deliveries'] == 0


def test_invalidated_context_or_paused_assignment_refuses_tools(runtime):
    store, _, run, _ = runtime
    row = submitted(runtime)
    with store.transaction() as tx:
        tx.db.execute("UPDATE runs SET phase='paused' WHERE run_id=?", (run,))
    assert fire(store, run, row, 'tool-start', payload()) == 2
    with store.transaction() as tx:
        tx.db.execute("UPDATE runs SET phase='working' WHERE run_id=?", (run,))
    with pytest.raises(Conflict):
        controller.observe(store, run, row['launch_id'], 'context', {'source': 'clear'})
    assert fire(store, run, row, 'tool-start', payload()) == 2
    assert native_tools.pending(store, row['launch_id']) == 0


def test_malformed_or_wrong_session_cannot_admit(runtime):
    store, _, run, _ = runtime
    row = submitted(runtime)
    assert fire(store, run, row, 'tool-start', payload(session_id='other')) == 2
    assert fire(store, run, row, 'tool-start', payload(tool_use_id=None)) == 2
    assert native_tools.pending(store, row['launch_id']) == 0
    assert native_producer.status(store, run)['gap']


def test_drain_refuses_requests_and_cli_is_replayable(runtime, capsys):
    from hx import lifecycle
    store, _, run, env = runtime
    row = submitted(runtime)
    args = ['eng-001', '--run', run, '--request', 'launch', '--drain']
    for _ in range(2):
        assert lifecycle.main_launch(args, store.root, env=env) == 0
        assert json.loads(capsys.readouterr().out)['status'] == 'draining'
    assert fire(store, run, row, 'request', {'session_id': 'test-native-session', 'prompt': 'continue'}) == 2
    assert native_launch._row(store, run)['status'] == 'draining'
    assert native_producer.status(store, run)['gap'] is None


def test_schema_thirteen_upgrade_preserves_launch_and_adds_tool_registry(configured):
    store, _, run = configured
    native_launch.prepare(store, 'eng-001', run, 'launch', env={})
    store.db.execute('DROP TABLE native_tool_calls')
    store.db.execute('PRAGMA user_version=13')
    with ContinuityStore(store.root) as upgraded:
        assert upgraded.db.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
        assert native_launch._row(upgraded, run)['status'] == 'prepared'
        assert upgraded.db.execute('SELECT count(*) FROM native_tool_calls').fetchone()[0] == 0


@pytest.mark.parametrize('separate_session', [False, True])
def test_child_calls_require_registration_and_stay_in_their_session(runtime, separate_session):
    store, _, run, _ = runtime
    row = submitted(runtime)
    session = 'child-session' if separate_session else 'test-native-session'
    child_call = payload(agent_id='child-a', session_id=session)
    assert fire(store, run, row, 'tool-start', child_call) == 2
    started = {'session_id': 'test-native-session', 'subagent_id': 'child-a', 'child_session_id': session}
    assert fire(store, run, row, 'subagent-start', started) == 0
    child_call['tool_use_id'] = 'registered-call'
    assert fire(store, run, row, 'tool-start', child_call) == 0
    # Muse reports child_session_id at spawn but omits actor from tool payloads.
    result = {**child_call, 'tool_response': 'done'}
    if separate_session:
        result.pop('agent_id')
    assert fire(store, run, row, 'log', result) == 0
    assert native_tools.pending(store, row['launch_id']) == 0
    assert fire(store, run, row, 'subagent-stop', started) == 0
    assert fire(store, run, row, 'subagent-start', started) == 0
    assert fire(store, run, row, 'tool-start', {**child_call, 'tool_use_id': 'new'}) == 2
    assert native_producer.status(store, run)['gap'] is None


def test_sessionless_children_have_distinct_call_ownership(runtime):
    store, _, run, _ = runtime
    row = submitted(runtime)
    for actor in ('first', 'second'):
        assert fire(store, run, row, 'subagent-start', {'agent_id': actor}) == 0
        call = payload(agent_id=actor)
        call.pop('session_id')
        assert fire(store, run, row, 'tool-start', call) == 0
        assert fire(store, run, row, 'log', {**call, 'tool_response': 'done'}) == 0
    assert native_tools.pending(store, row['launch_id']) == 0
    assert store.db.execute('SELECT count(*) FROM native_tool_calls WHERE launch_id=?', (row['launch_id'],)).fetchone()[0] == 2
    assert native_producer.status(store, run)['gap'] is None


def test_tool_admission_serializes_with_drain(runtime):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    store, _, run, env = runtime
    row = submitted(runtime)
    barrier = Barrier(2)
    def admit():
        with ContinuityStore(store.root) as other:
            barrier.wait(timeout=5)
            try:
                native_tools.admit(other, run, row['launch_id'], payload())
                return True
            except native_tools.AdmissionDenied:
                return False
    def drain():
        with ContinuityStore(store.root) as other:
            barrier.wait(timeout=5)
            return controller.drain(other, 'eng-001', run, 'launch', env=env)
    with ThreadPoolExecutor(max_workers=2) as pool:
        admitted, drained = pool.submit(admit), pool.submit(drain)
        did_admit = admitted.result(timeout=10)
        result = drained.result(timeout=10)
    assert result['status'] == 'draining'
    assert native_tools.pending(store, row['launch_id']) == int(did_admit)
    assert fire(store, run, row, 'tool-start', payload('later')) == 2


def test_denied_tool_error_result_does_not_manufacture_a_capture_gap(runtime):
    store, _, run, env = runtime
    row = submitted(runtime)
    controller.drain(store, 'eng-001', run, 'launch', env=env)
    assert fire(store, run, row, 'tool-start', payload()) == 2
    assert fire(store, run, row, 'log-failure', payload(error='blocked by hx')) == 0
    assert native_tools.pending(store, row['launch_id']) == 0
    assert native_producer.status(store, run)['gap'] is None


@pytest.mark.skipif(not os.environ.get('HX_PI_EXTENSION_LOADER'), reason='requires explicitly selected installed Pi loader; no model calls')
@pytest.mark.parametrize('as_child', [False, True])
def test_installed_pi_loader_admits_and_blocks_tools(runtime, tmp_path, as_child):
    store, _, run, env = runtime
    flavor(runtime, 'pi')
    row = submitted(runtime)
    extension = Path(row['payload']['capsule']) / 'run/eng-001/home/extensions/hx/index.ts'
    if as_child:
        native_producer.hook(store, run_id=run, worker_id='eng-001', adapter='pi', launch_id=row['launch_id'],
                             event='subagent-start', payload={'agent_id': 'pi-child'})
    script = tmp_path / 'tools.mjs'
    script.write_text('''import {pathToFileURL} from 'node:url';
const [loader, extension, cwd, mode] = process.argv.slice(2);
const {loadExtensions} = await import(pathToFileURL(loader).href);
const loaded = await loadExtensions([extension], cwd);
if (loaded.errors.length) throw new Error(JSON.stringify(loaded.errors));
const handlers = loaded.extensions[0].handlers;
const calls = handlers.get('tool_call');
if (!calls?.length) throw new Error('native tool_call callback missing');
const event = {toolName:'bash',toolCallId:mode,input:{command:'true'}};
const ctx = {sessionManager:{getSessionFile:()=> 'test-native-session'}};
const answer = await calls[0](event, ctx);
if (mode === 'allowed' && answer?.block) throw new Error('valid tool denied');
if (mode === 'denied' && !answer?.block) throw new Error('draining tool allowed');
for (const fn of handlers.get('tool_result') ?? []) await fn({...event,
  content:[{type:'text',text:mode}], isError:mode==='denied'},ctx);
for (const fn of handlers.get('message_end') ?? []) await fn({type:'message_end',message:{role:'assistant',
  content:[{type:'text',text:'Current tool status is '+mode+'.'},{type:'thinking',thinking:'PRIVATE'}],
  usage:{input:3,output:5},stopReason:'stop'}},ctx);
''')
    child_env = {**env, 'HARNESS_ID': 'eng-001', 'HARNESS_ROOT': str(store.root)}
    if as_child:
        child_env.update(PI_SUBAGENT='1', HX_PI_CHILD_ID='pi-child')
    for mode in ('allowed', 'denied'):
        if mode == 'denied':
            controller.drain(store, 'eng-001', run, 'launch', env=env)
        result = subprocess.run(['node', str(script), os.environ['HX_PI_EXTENSION_LOADER'], str(extension), str(tmp_path), mode],
                                env=child_env, capture_output=True, text=True, timeout=20)
        assert result.returncode == 0, result.stderr
        assert native_tools.pending(store, row['launch_id']) == 0
        assert native_producer.status(store, run)['gap'] is None
    calls = store.db.execute('SELECT status FROM native_tool_calls WHERE launch_id=? ORDER BY status', (row['launch_id'],)).fetchall()
    assert [item[0] for item in calls] == ['denied', 'settled']
    messages = [json.loads(item[0])["observation"] for item in store.db.execute(
        "SELECT payload FROM events WHERE run_id=? AND kind='assistant_message'", (run,))]
    assert len(messages) == 2
    assert {message["data"].get("agent_id") for message in messages} == ({"pi-child"} if as_child else {None})
    assert all(message["usage"] == {"input": 3, "output": 5} for message in messages)
    assert 'PRIVATE' not in repr(messages)


@pytest.mark.parametrize('adapter', ['codex', 'grok', 'meta'])
def test_missing_hook_executable_denies_planned_tool(adapter, tmp_path):
    import sys
    script = Path(__file__).parents[2] / 'src/hx/skeleton/adapters' / adapter / 'hook.py'
    result = subprocess.run([sys.executable, str(script), '--id', 'eng-001', '--root', str(tmp_path),
        '--hook-bin', str(tmp_path / 'missing-hook'), '--continuity-run', 'run', '--continuity-launch', 'launch',
        '--continuity-adapter', adapter, 'tool-start'], input=json.dumps(payload()), text=True, capture_output=True)
    assert result.returncode == 2 and 'cannot run' in result.stderr
