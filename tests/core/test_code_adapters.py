import json
import shutil
from pathlib import Path
import pytest
from hx import code_adapters,code_index
from hx.code_store import connect,namespace
from hx.code_scip import ingest
from hx.code_import import import_graph


def test_pinned_tags_format():
 # tree-sitter v0.25.10 cli/src/tags.rs; Point's Display is zero-based (row, column).
 rows=code_adapters.parse_tags('Foo       \t | class   \tdef (0, 0) - (3, 1) `class Foo {`\nRun       \t | call    \tref (2, 3) - (2, 8) `Run()`\n')
 assert rows[0]['line']==1 and rows[0]['end_line']==4 and rows[1]['role']=='ref'
 with pytest.raises(ValueError): code_adapters.parse_tags('unknown output')


def test_ctags_source_span_merge():
 source='class Foo {\n public int Run(string name) { return 1; }\n}\n'
 symbols,refs,_=__import__('hx.code_extract',fromlist=['extract']).extract(source,'a.cs')
 result,_=code_adapters._merge(source,'a.cs',symbols,refs,[dict(name='Run',kind='method',line=2,end_line=2,scope='Foo')],'universal-ctags')
 assert [s for s in result if s['name']=='Run'][0]['signature']=='public int Run(string name)'


def test_scip_superset_and_lane_rank(tmp_path):
 repo=tmp_path/'repo'; repo.mkdir(); root=tmp_path/'root'
 (repo/'a.py').write_text('def login(user):\n return user\n'); (repo/'b.py').write_text('login(1)\n')
 code_index.index_repo(root,repo); conn=connect(root); identity=namespace(repo)
 data={'documents':[{'relative_path':'a.py','symbols':[{'symbol':'scip-python pkg 1 a/login().','display_name':'login','signature_documentation':{'text':'def login(user: str) -> str'},'kind':12}],
 'occurrences':[{'range':[0,4,9],'symbol':'scip-python pkg 1 a/login().','symbol_roles':1}]},
 {'relative_path':'b.py','occurrences':[{'range':[0,0,5],'symbol':'scip-python pkg 1 a/login().','symbol_roles':0}]}]}
 result=ingest(conn,identity,data); conn.commit(); conn.close()
 assert result['references']==1
 rows=code_index.symbols_for(root,'login')
 assert rows[0]['lane']=='resolved' and rows[0]['signature']=='def login(user: str) -> str'
 assert code_index.callers(root,'login')[0]['path']=='b.py'
 assert any(r['reference']=='resolved' for r in code_index.callers(root,'login'))


def test_import_custom_kinds_and_idempotency(tmp_path,capsys):
 root=tmp_path/'root'; repo=tmp_path/'repo'; repo.mkdir(); (repo/'a.nat').write_text('PROGRAM SOURCE\n')
 code_index.index_repo(root,repo); ns=namespace(repo)
 symbol=tmp_path/'symbols.jsonl'; edge=tmp_path/'edges.jsonl'
 symbol.write_text(json.dumps(dict(name='Source',qualified='Source',kind='program',path='a.nat',line=1,end_line=1,signature='PROGRAM SOURCE'))+'\n'+json.dumps(dict(name='Missing',qualified='Missing',kind='program',path='absent.nat',line=1,end_line=1,signature='PROGRAM MISSING'))+'\n')
 edge.write_text('\n'.join(json.dumps({'from':'Source','to':'Missing','kind':kind,'evidence':{'tool':'legacy'}}) for kind in ['CALLS_STATIC','PERFORMS','INPUTS_MAP']))
 for _ in range(2): import_graph(root,ns,symbol,edge)
 assert code_index.symbols_for(root,'Missing')[0]['unanchored']
 assert len(code_index.graph_edges(root))==3
 assert len(code_index.callers(root,'Missing'))==3
 assert code_index.main(['signature','Missing','--expect','fresh'],root)==3
 capsys.readouterr()
 conn=connect(root); assert conn.execute("SELECT count(*) FROM symbols WHERE lane='imported'").fetchone()[0]==2; conn.close()


@pytest.mark.parametrize('tool',['tree-sitter','ctags','scip'])
def test_optional_live_binary_detection(tool):
 detected=code_adapters.detect()
 if tool=='scip':
  if not detected['scip'] or not any(detected['indexers'].values()): pytest.skip('SCIP CLI/language indexer not installed')
 elif not detected[tool]['available']: pytest.skip(tool+' prerequisites/version not installed')
 assert shutil.which(tool)
