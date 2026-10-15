"""Selected installed native host, loopback provider only; no paid model calls."""
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from hx.file_read_delivery import SEGMENT_CHARS, split_delivery
from .claude_companion_fixture import transport


@pytest.mark.skipif(not os.environ.get('HX_CLAUDE_TEST_BIN'), reason='requires explicitly selected installed Claude; loopback provider only')
@pytest.mark.parametrize('dense_source', [False, True])
def test_real_claude_receives_every_automatic_source_segment(tmp_path, dense_source):
    source = (('word ' * 40 + '\n') * 700 if dense_source else '# prefix\n' * 3500 + 'L' * 20000 + '\n' + '🦉' * 5000) + '\nTAIL_COMPLETE_SOURCE_SENTINEL\n'
    path = tmp_path / 'source.py'
    path.write_text(source)
    home = tmp_path / 'home'
    home.mkdir()
    (home / '.claude.json').write_text(json.dumps({'hasCompletedOnboarding': True,
        'projects': {str(tmp_path): {'hasTrustDialogAccepted': True}}}))
    script = tmp_path / 'whole_file_hook.py'
    script.write_text('''import json,sys
from pathlib import Path
body=json.load(sys.stdin)
if body.get('tool_name')!='Read': raise SystemExit(0)
Path(sys.argv[1]+'.native-event.json').write_text(json.dumps(body))
text=Path(sys.argv[1]).read_text()
from hx.file_read_delivery import split_delivery
index=int(sys.argv[2]); width=int(sys.argv[3])
chunk=split_delivery(text)[index]
print(json.dumps({'hookSpecificOutput':{'hookEventName':body['hook_event_name'],
    'additionalContext':f'HX complete source segment {index}\\n'+chunk}},ensure_ascii=False))
''')
    chunks = split_delivery(source)
    settings = tmp_path / 'settings.json'
    handlers = [
        {'matcher': 'Read', 'hooks': [{'type': 'command', 'command': shlex.join([
            sys.executable, str(script), str(path), str(index), str(SEGMENT_CHARS)])}]} for index in range(len(chunks))]
    settings.write_text(json.dumps({'hooks': {'PostToolUse': handlers, 'PostToolUseFailure': handlers}}))
    blocks = [{'type': 'tool_use', 'id': 'read_complete_file', 'name': 'Read', 'input': {'file_path': str(path)}},
              {'type': 'text', 'text': 'Complete source verified.'}]
    with transport({}, {}, blocks=blocks) as (url, requests):
        env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(home), 'CLAUDE_CONFIG_DIR': str(home),
               'ANTHROPIC_API_KEY': 'sk-ant-api-loopback-only', 'ANTHROPIC_BASE_URL': url,
               'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'DISABLE_AUTOUPDATER': '1',
               'PYTHONPATH': str(Path(__file__).parents[2] / 'src'), 'DISABLE_TELEMETRY': '1', 'CLAUDE_CODE_ENABLE_TELEMETRY': '0'}
        result = subprocess.run([os.environ['HX_CLAUDE_TEST_BIN'], '-p', 'Read the complete source file.',
            '--model', 'claude-sonnet-4-6', '--settings', str(settings), '--setting-sources', '',
            '--tools', 'Read', '--dangerously-skip-permissions', '--no-session-persistence',
            '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}'], cwd=tmp_path, env=env,
            capture_output=True, text=True, timeout=45)
    assert result.returncode == 0, result.stderr + result.stdout
    assert len(requests) >= 2
    second = requests[-1]
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
    texts = list(strings(second['messages']))
    for index, chunk in enumerate(chunks):
        assert any('HX complete source segment ' + str(index) + '\n' + chunk in text for text in texts), index
    assert any('TAIL_COMPLETE_SOURCE_SENTINEL' in text for text in texts)
    assert all('Full output saved to' not in text for text in texts if 'HX complete source segment' in text)
    native = json.loads(Path(str(path) + '.native-event.json').read_text())
    assert native['hook_event_name'] in {'PostToolUse', 'PostToolUseFailure'}


@pytest.mark.skipif(not os.environ.get('HX_CODEX_TEST_BIN'), reason='requires explicitly selected installed Codex; loopback provider only')
def test_real_codex_shell_tool_receives_complete_broker_context(tmp_path):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    requests = []
    source = '# source\n' * 3300 + 'L' * 22000 + '\n' + '🦉' * 5000 + '\nTAIL_CODEX_COMPLETE_SOURCE\n'
    path = tmp_path / 'source.py'
    path.write_text(source)
    hook = tmp_path / 'source_hook.py'
    hook.write_text('''import json,sys
from pathlib import Path
body=json.load(sys.stdin)
Path(sys.argv[1]+'.hook-event.json').write_text(json.dumps(body))
if body.get('tool_name','').rsplit('.',1)[-1] not in {'exec_command','shell_command','Bash','shell'}: raise SystemExit(0)
print(json.dumps({'hookSpecificOutput':{'hookEventName':'PostToolUse',
    'additionalContext':Path(sys.argv[1]).read_text()}},ensure_ascii=False))
''')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
            requests.append(body)
            def functions(tools, namespace=None):
                for tool in tools:
                    if tool.get('type') == 'namespace':
                        yield from functions(tool.get('tools', []), tool['name'])
                    elif tool.get('type') == 'function':
                        yield namespace, tool
            if len(requests) == 1:
                selected = next(((ns, tool) for ns, tool in functions(body.get('tools', []))
                                 if tool.get('name') in {'exec_command', 'shell_command'}), None)
                if selected is None:
                    self.send_error(422, 'native shell tool unavailable')
                    return
                namespace, tool = selected
                key = 'cmd' if tool['name'] == 'exec_command' else 'command'
                item = {'type': 'function_call', 'id': 'fc_read', 'call_id': 'call_read', 'name': tool['name'],
                        'arguments': json.dumps({key: 'printf HX_SOURCE_BROKER_MARKER'})}
                if namespace:
                    item['namespace'] = namespace
            else:
                item = {'type': 'message', 'id': 'msg_done', 'role': 'assistant', 'status': 'completed',
                        'content': [{'type': 'output_text', 'text': 'Complete source verified.', 'annotations': []}]}
            response = {'id': 'resp_' + str(len(requests)), 'object': 'response', 'status': 'completed',
                        'output': [item], 'usage': {'input_tokens': 10, 'output_tokens': 5, 'total_tokens': 15}}
            events = [('response.created', {'response': {**response, 'status': 'in_progress', 'output': []}}),
                      ('response.output_item.added', {'output_index': 0, 'item': item}),
                      ('response.output_item.done', {'output_index': 0, 'item': item}),
                      ('response.completed', {'response': response})]
            wire = ''.join('event: ' + kind + '\ndata: ' + json.dumps({'type': kind, **data}) + '\n\n'
                           for kind, data in events).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)
    server = HTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    home = tmp_path / 'codex-home'
    home.mkdir()
    command = shlex.join([sys.executable, str(hook), str(path)])
    (home / 'config.toml').write_text('''model = "loopback-test"
model_provider = "loopback"
[model_providers.loopback]
name = "Loopback fixture"
base_url = "http://127.0.0.1:PORT"
wire_api = "responses"
requires_openai_auth = false
[[hooks.PostToolUse]]
  [[hooks.PostToolUse.hooks]]
  type = "command"
  additionalContextLimit = 0
  command = COMMAND
'''.replace('PORT', str(server.server_port)).replace('COMMAND', json.dumps(command)))
    env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(home), 'CODEX_HOME': str(home),
           'CODEX_DISABLE_UPDATE_CHECK': '1', 'DO_NOT_TRACK': '1'}
    try:
        result = subprocess.run([os.environ['HX_CODEX_TEST_BIN'], 'exec', '--skip-git-repo-check',
            '--dangerously-bypass-approvals-and-sandbox', '--dangerously-bypass-hook-trust', '--ephemeral',
            '-C', str(tmp_path), 'Read the complete source through the shell broker.'],
            cwd=tmp_path, env=env, capture_output=True, text=True, timeout=45)
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=2)
    assert result.returncode == 0, result.stderr + result.stdout
    assert len(requests) == 2
    def strings(value):
        if isinstance(value, str):
            yield value
        elif isinstance(value, dict):
            for item in value.values():
                yield from strings(item)
        elif isinstance(value, list):
            for item in value:
                yield from strings(item)
    received = list(strings(requests[-1]))
    assert any(source in text for text in received), ('native host did not send complete broker context to provider; '
        + result.stderr + result.stdout + '\nhook_called=' + str(Path(str(path)+'.hook-event.json').exists())
        + '\nrequest_string_lengths=' + str(sorted(map(len, received), reverse=True)[:8]))
