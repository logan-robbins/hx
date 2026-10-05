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
