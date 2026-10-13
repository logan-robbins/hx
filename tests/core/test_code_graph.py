"""Graph contracts: namespaces, provenance, scale, lints and manifest facts."""
import json
import subprocess
from pathlib import Path
import pytest
from hx import code_index
from hx.code_store import connect,namespace


def git(repo,*args):
 return subprocess.run(['git','-C',str(repo),*args],check=True,capture_output=True).stdout.decode().strip()


def make_repo(tmp_path,name='repo'):
 p=tmp_path/name; p.mkdir(); git(p,'init'); git(p,'config','user.email','test@example.com'); git(p,'config','user.name','Test')
 (p/'a.cs').write_text('namespace Acme;\npublic interface IFoo { int Run(string name); }\npublic class Foo : IFoo { public int Run(string value) { return 1; } }\n')
 (p/'app.cs').write_text('services.AddScoped<IFoo,Foo>();\nnew Foo().Run("x");\n')
 (p/'test.cs').write_text('[TestFixture]\nclass FooTests { [Test] public void Works() { new Foo().Run("x"); } }\n')
 git(p,'add','.'); git(p,'commit','-m','fixture'); return p


def test_namespace_isolation_and_incremental(tmp_path):
 root=tmp_path/'root'; a=make_repo(tmp_path,'a'); b=make_repo(tmp_path,'b')
 code_index.index_repo(root,a); code_index.index_repo(root,b)
 assert len(code_index.symbols_for(root,'IFOO'))==2
 assert len(code_index.symbols_for(root,'ifoo',repo=namespace(a)))==1
 (a/'a.cs').unlink(); code_index.index_repo(root,a)
 assert len(code_index.symbols_for(root,'IFoo'))==1
 assert code_index.index_repo(root,b)['changed']==0


def test_no_cap_and_single_body_external_fts(tmp_path):
 repo=tmp_path/'repo'; repo.mkdir(); root=tmp_path/'root'
 for i in range(2005): (repo/f'{i}.py').write_text('def hello(): return 1\n')
 assert code_index.index_repo(root,repo)['files']==2005
 conn=connect(root)
 assert "content='files'" in conn.execute("SELECT sql FROM sqlite_master WHERE name='code_search'").fetchone()[0]
 conn.close()


def test_provenance_and_dirty_staleness(tmp_path):
 repo=make_repo(tmp_path); root=tmp_path/'root'; code_index.index_repo(root,repo)
 row=code_index.symbols_for(root,'Acme.Foo.Run')[0]
 assert row['signature']=='public int Run(string value)'
 assert row['lane']=='syntax' and not row['stale']
 (repo/'a.cs').write_text((repo/'a.cs').read_text().replace('return 1','return 2'))
 assert code_index.symbols_for(root,'Acme.Foo.Run')[0]['stale']


@pytest.mark.parametrize('expect,query,expected',[('exists','Foo',0),('exists','Missing',3),('absent','Missing',0),('absent','Foo',3),('one','Acme.Foo',0),('one','Run',3),('fresh','Foo',0),('fresh','Missing',3)])
def test_lint_modes(tmp_path,capsys,expect,query,expected):
 repo=make_repo(tmp_path); root=tmp_path/'root'; code_index.index_repo(root,repo)
 assert code_index.main(['signature',query,'--expect',expect],root)==expected
 assert json.loads(capsys.readouterr().out) is not None


def test_project_manifests_and_text_fallback(tmp_path,capsys):
 repo=tmp_path/'repo'; repo.mkdir(); root=tmp_path/'root'
 (repo/'A.csproj').write_text('<Project><ItemGroup><ProjectReference Include="B.csproj"/><PackageReference Include="Microsoft.NET.Test.Sdk" Version="1"/></ItemGroup></Project>')
 (repo/'B.csproj').write_text('<Project/>'); (repo/'legacy.nat').write_text('LEGACY-NATURAL-CAPABILITY\n')
 code_index.index_repo(root,repo)
 assert code_index.search(root,'LEGACY-NATURAL-CAPABILITY')
 assert code_index.main(['projects','A'],root)==0
 project=json.loads(capsys.readouterr().out)[0]
 assert project['data']['test_project'] and project['dependencies'][0]['dst_text']=='B.csproj'


def test_state_never_written_in_product(tmp_path):
 repo=tmp_path/'repo'; repo.mkdir()
 with pytest.raises(ValueError,match='outside'): code_index.index_repo(repo,repo)
 assert not (repo/'state').exists()


def test_reuse_queries_and_parity(tmp_path,capsys):
 repo=make_repo(tmp_path); root=tmp_path/'root'; code_index.index_repo(root,repo)
 for command,name in [('type','ifoo'),('signature','Acme.Foo.Run'),('register','IFoo'),('tests-for','Foo'),('precedent','AddScoped')]:
  assert code_index.main([command,name,'--expect','exists'],root)==0
  assert json.loads(capsys.readouterr().out)
 assert code_index.main(['conform','IFoo','--expect','clean'],root)==0
 assert json.loads(capsys.readouterr().out)['clean']
 (repo/'a.cs').write_text((repo/'a.cs').read_text().replace('public int Run(string value)','public int Run(int value)'))
 code_index.index_repo(root,repo)
 assert code_index.main(['conform','IFoo','--expect','clean'],root)==3
 assert json.loads(capsys.readouterr().out)['diffs'][0]['kind']=='signature'


def test_count_and_usage_errors(tmp_path,capsys):
 repo=make_repo(tmp_path); root=tmp_path/'root'; code_index.index_repo(root,repo)
 assert code_index.main(['callers','Run','--count','--expect','exists'],root)==0
 assert json.loads(capsys.readouterr().out)['count']==2
 assert code_index.main(['type','Foo','--limit','101'],root)==2


def test_contract_registry_shared_namespaces(tmp_path,capsys):
 a=make_repo(tmp_path,'source'); b=make_repo(tmp_path,'target'); root=tmp_path/'root'
 code_index.index_repo(root,a); code_index.index_repo(root,b)
 records=[dict(name='IFoo',path='a.cs',version='1',producer='source-unit' if p==a else 'target-unit',repo=namespace(p),sha=git(p,'rev-parse','HEAD')) for p in [a,b]]
 file=tmp_path/'contracts.json'; file.write_text(json.dumps(records))
 assert code_index.main(['contracts','--register',str(file)],root)==0; capsys.readouterr()
 assert code_index.main(['contract','ifoo','--expect','exists'],root)==0
 rows=json.loads(capsys.readouterr().out)
 assert {r['producer'] for r in rows}=={'source-unit','target-unit'}
 assert all(r['sha'] and not r['stale'] for r in rows)
 assert code_index.main(['contract','ifoo','--expect','absent'],root)==3


def test_ownership_read_through_without_graph_columns(tmp_path,capsys):
 from hx.continuity_store import ContinuityStore
 root=tmp_path/'root'; repo=make_repo(tmp_path); code_index.index_repo(root,repo)
 with ContinuityStore(root) as ledger:
  with ledger.transaction() as tx:
   tx.db.execute("INSERT INTO task_heads VALUES('T',1)")
   tx.db.execute("INSERT INTO tasks VALUES('T',1,NULL,'{}',0)")
   tx.db.execute("INSERT INTO runs VALUES('R','T',1,'eng-001',0,'working',NULL,NULL,0,NULL)")
   tx.db.execute('INSERT INTO leases VALUES(?,?,?,?)',(namespace(repo),'a.cs','R','{}'))
  assert code_index.main(['type','Foo','--owners'],root)==0
  rows=json.loads(capsys.readouterr().out)
  assert rows[0]['owners'][0]['worker']=='eng-001'
  with ledger.transaction() as tx: tx.db.execute("DELETE FROM leases WHERE run_id='R'")
  assert code_index.main(['type','Foo','--owners'],root)==0
  assert json.loads(capsys.readouterr().out)[0]['owners']==[]
 conn=connect(root)
 for table in ['symbols','refs','edges']: assert 'owner' not in {r[1] for r in conn.execute('PRAGMA table_info('+table+')')}
 conn.close()
