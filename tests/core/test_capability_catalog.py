import json
import sys

import pytest
from hx import capability_catalog as capabilities
from hx.continuity_store import ContinuityStore, Conflict
from hx.evidence import read


@pytest.fixture
def indexed(tmp_path):
    server = tmp_path / 'server.py'
    server.write_text('''import json,sys
for line in sys.stdin:
 q=json.loads(line)
 if 'id' not in q: continue
 method=q['method']
 result={'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':{'name':'fixture','version':'1'}}
 if method=='tools/list': result={'tools':[{'name':'lookup','description':'Read the current test status.','inputSchema':{'type':'object'}}]}
 if method=='tools/call': result={'content':[{'type':'text','text':'x'*5000}]}
 print(json.dumps({'jsonrpc':'2.0','id':q['id'],'result':result}),flush=True)
''')
    skill = tmp_path / 'SKILL.md'
    skill.write_text('---\nname: checks\ndescription: Run focused checks.\n---\nUse the assigned check command.\n')
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/capabilities.json').write_text(json.dumps({'schema_version': 1,
        'servers': {'local': {'command': [sys.executable, str(server)]}}, 'skills': {'checks': str(skill)}}))
    capabilities.index(tmp_path)
    with ContinuityStore(tmp_path) as store:
        with store.transaction() as tx:
            rev = tx.put_task('T', {'goal': 'Check current status.'}, expected_revision=0)
            run = tx.start_run('T', rev, 'eng-001')
        yield store, run


def test_index_load_invoke_once_and_read_bounded_result(indexed, monkeypatch):
    store, run = indexed
    catalog = capabilities.read(store.root)
    assert set(catalog['entries']) == {'mcp:local:lookup', 'skill:checks'}
    loaded = capabilities.load(store, run, 'mcp:local:lookup')
    assert loaded['visibility'] == 'shell_facade'
    result = capabilities.invoke(store, run, 'mcp:local:lookup', {}, 'R1')
    assert 'read' in result and result['bytes'] > 4096
    assert len(read(store, result['event_id'], limit=512)['content']) == 512
    monkeypatch.setattr(capabilities, 'MCP', lambda *a: pytest.fail('must reuse completed call'))
    assert capabilities.invoke(store, run, 'mcp:local:lookup', {}, 'R1') == result
    with pytest.raises(Conflict, match='different inputs'):
        capabilities.invoke(store, run, 'mcp:local:lookup', {'different': True}, 'R1')


def test_skill_source_change_requires_reindex(indexed):
    store, run = indexed
    assert 'assigned check' in capabilities.load(store, run, 'skill:checks')['body']
    (store.root / 'SKILL.md').write_text('Changed instructions.')
    with pytest.raises(Conflict, match='skill changed'):
        capabilities.load(store, run, 'skill:checks')


def test_shipped_skills_load_whole_with_current_catalog_purposes(tmp_path):
    """Both delivery and conversion instructions reach the worker without truncation."""
    from pathlib import Path

    package = Path(capabilities.__file__).resolve().parent / 'skills'
    names = ('hx-signoff', 'hx-conversion')
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/capabilities.json').write_text(json.dumps({'schema_version': 1, 'servers': {},
        'skills': {name: str(package / name / 'SKILL.md') for name in names}}))
    assert capabilities.index(tmp_path)['capabilities'] == 2
    with ContinuityStore(tmp_path) as store:
        with store.transaction() as tx:
            rev = tx.put_task('T', {'goal': 'Sign off the release.'}, expected_revision=0)
            run = tx.start_run('T', rev, 'rel-001')
        for name in names:
            loaded = capabilities.load(store, run, 'skill:' + name)
            text = (package / name / 'SKILL.md').read_text()
            assert loaded['body'] == text
            assert len(loaded['body'].encode()) <= 8192, name
            assert capabilities.read(tmp_path)['entries']['skill:' + name]['purpose'].startswith(
                'Verify packet proof' if name == 'hx-signoff' else 'How to run a code conversion')


def test_index_records_the_protocol_version_each_server_answers_with(indexed):
    """TOOL_SKILL_PLAN step 8: the catalog keeps the `initialize` answer per server."""
    store, _ = indexed
    catalog = capabilities.read(store.root)
    assert catalog['servers'] == {'local': {'protocol_version': '2024-11-05'}}
    assert capabilities.index(store.root)['servers'] == {'local': {'protocol_version': '2024-11-05'}}


#: A modern-only stdio server (MCP 2026-07-28): `initialize` is an unknown method to it.
#: It logs every method it receives so the test can count the discover calls.
MODERN_SERVER = '''import json,sys
log=open(sys.argv[1],'a')
for line in sys.stdin:
 q=json.loads(line)
 log.write(q.get('method','')+chr(10)); log.flush()
 if 'id' not in q: continue
 if q['method']=='server/discover' and sys.argv[2]=='discover':
  body={'result':{'resultType':'complete','supportedVersions':['2026-07-28'],'capabilities':{'tools':{}}}}
 elif q['method']=='server/discover':
  body={'error':{'code':-32601,'message':'Method not found'}}
 else:
  body={'error':{'code':-32601,'message':'Method not found'}}
 print(json.dumps({'jsonrpc':'2.0','id':q['id'],**body}),flush=True)
'''


def _modern(tmp_path, mode):
    server, log = tmp_path / 'modern.py', tmp_path / 'methods.log'
    server.write_text(MODERN_SERVER)
    (tmp_path / 'config').mkdir()
    (tmp_path / 'config/capabilities.json').write_text(json.dumps({'schema_version': 1,
        'servers': {'modern': {'command': [sys.executable, str(server), str(log), mode]}}, 'skills': {}}))
    return log


def test_a_refused_initialize_calls_server_discover_once_and_names_the_versions(tmp_path):
    log = _modern(tmp_path, 'discover')
    with pytest.raises(Conflict, match='refused initialize') as refusal:
        capabilities.index(tmp_path)
    assert '2026-07-28' in str(refusal.value) and 'server/discover' in str(refusal.value)
    assert log.read_text().splitlines() == ['initialize', 'server/discover']
    assert not (tmp_path / 'state/capabilities.json').exists()


def test_a_server_that_refuses_discover_too_is_reported_without_versions(tmp_path):
    log = _modern(tmp_path, 'legacy-error')
    with pytest.raises(Conflict, match='refused initialize') as refusal:
        capabilities.index(tmp_path)
    assert 'reported no supported versions' in str(refusal.value)
    assert log.read_text().splitlines() == ['initialize', 'server/discover']
