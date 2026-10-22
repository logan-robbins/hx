"""Hermetic MSBuild boundary tests plus an optional installed-SDK smoke test."""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap
from concurrent.futures import ThreadPoolExecutor

import pytest

from hx import dotnet_artifacts, dotnet_build as broker, dotnet_graph, dotnet_slots, host_jobs, native_processes
from hx.continuity_store import Conflict, ContinuityStore, canonical, digest
from hx.errors import ValidationError

FAKE = r'''
import hashlib,json,os,sys,time
from pathlib import Path
args=sys.argv[1:]
log=Path(os.environ['FAKE_LOG'])
with log.open('a') as f: f.write(json.dumps({'args':args,'packages':os.environ.get('NUGET_PACKAGES'),'cli_home':os.environ.get('DOTNET_CLI_HOME')})+'\n')
if args==['--info']:
 print('.NET SDK fake 10.0.400'); sys.exit()
if args[:2]==['msbuild','-help']:
 print('-getProperty -getItem -graphBuild' if not os.environ.get('FAKE_OLD') else '-graphBuild'); sys.exit()
if args[0]=='sln':
 print('Project(s)\n----------\n'+'\n'.join(p.name for p in Path(args[1]).parent.glob('*.csproj'))); sys.exit()
if args==['test','--help']:
 print('--no-build --results-directory '+('--test-modules' if os.environ.get('FAKE_RUNNER')=='MTP' else '--filter --logger')); sys.exit()
if args[0]=='test':
 assert '--no-build' in args
 assert not any(a in args for a in ['--configuration','--framework','--runtime','--no-restore'])
 target=Path(args[args.index('--test-modules')+1] if '--test-modules' in args else args[1])
 assert target.is_file() and 'shards' in target.parts
 assert '--results-directory' in args
 if os.environ.get('FAKE_RUNNER')=='MTP': assert '--logger' not in args and '--filter' not in args
 else: assert '--test-modules' not in args and '--logger' in args
 print('Tests passed for '+target.read_text()); sys.exit(0)
assert args[0]=='msbuild'
entry=Path(args[1]); project=entry.parent/'Test.csproj' if entry.suffix in ('.sln','.slnx') else entry; directory=project.parent
assert '-nodeReuse:false' in args and '-noAutoResponse' in args
props={}
for a in args:
 if a.startswith('-p:'): name,value=a[3:].split('=',1); props[name]=value
configuration=props.get('Configuration','Debug'); tfm=props.get('TargetFramework','net10.0')
name=project.stem; tree=Path(props['ArtifactsPath']) if 'ArtifactsPath' in props else directory/'obj-default'
obj=tree/'obj'/name; target=tree/'bin'/name/configuration.lower()/tfm/(name+'.dll')
assets=obj/'project.assets.json'
package_path=Path(os.environ['NUGET_PACKAGES'])/'pkg'/'1.0.0'
packages=[{'Identity':'Pkg','Version':os.environ.get('FAKE_PACKAGE_VERSION','1.0.0')}] if os.environ.get('FAKE_PACKAGES') else []
refs=[]
if project.name=='Test.csproj' and (directory/'Lib.csproj').exists(): refs=[{'Identity':'Lib.csproj','FullPath':str(directory/'Lib.csproj')}]
if '-target:Restore' in args:
 assert '-graphBuild' in args and '-maxCpuCount:1' in args and props['UseSharedCompilation']=='false'
 for p in [project]+([directory/'Lib.csproj'] if refs else []):
  o=tree/'obj'/p.stem; o.mkdir(parents=True,exist_ok=True)
  if packages:
   package_path.mkdir(parents=True,exist_ok=True)
   if not (package_path/'generator.dll').exists(): (package_path/'generator.dll').write_text('generator-v1')
  (o/'project.assets.json').write_text(json.dumps({'libraries':{'pkg/1.0.0':{'type':'package','path':'pkg/1.0.0'}} if packages else {},'packageFolders':{os.environ['NUGET_PACKAGES']:{}}}))
  (o/(p.stem+'.nuget.g.props')).write_text('<Project/>')
  if os.environ.get('FAKE_PACKAGE_TARGET') and not (package_path/'Custom.targets').exists():
   (package_path/'Custom.targets').write_text('<Project><Target Name="ReadExternal"/></Project>')
 if os.environ.get('FAKE_RESTORE_MUTATE'):
  changed=directory/'Code.cs'; old=changed.read_bytes(); changed.write_text('temporary restore mutation'); changed.write_bytes(old)
 print('Restore complete'); sys.exit()
if '-target:Build' in args:
 assert '-graphBuild' in args and '-maxCpuCount:1' in args and props['UseSharedCompilation']=='false'
 assert assets.is_file()
 if os.environ.get('FAKE_READY'):
  Path(os.environ['FAKE_READY']).write_text('running')
  while not Path(os.environ['FAKE_RELEASE']).exists(): time.sleep(.02)
 if os.environ.get('FAKE_MUTATE'):
  path=directory/'Code.cs'; old=path.read_bytes(); path.write_text('temporary mutation'); path.write_bytes(old)
 if os.environ.get('FAKE_BUILD_FAIL'): print('failure'); sys.exit(7)
 for p in [project]+([directory/'Lib.csproj'] if refs else []):
  t=tree/'bin'/p.stem/configuration.lower()/tfm/(p.stem+'.dll'); t.parent.mkdir(parents=True,exist_ok=True)
  t.write_text(configuration+':'+tfm+':'+hashlib.sha256((directory/'Code.cs').read_bytes()).hexdigest())
 print('Build complete'); sys.exit()
if '-preprocess' in args:
 imports=[project,Path(os.environ['FAKE_SDK'])/'Sdk.props']
 for candidate in [directory/'Directory.Build.props',directory/'Custom.targets',obj/(name+'.nuget.g.props'),package_path/'Custom.targets']:
  if candidate.exists(): imports.append(candidate)
 print(''.join('<!--\n====\n'+str(p)+'\n====\n-->\n' for p in imports)+'<Project/>'); sys.exit()
assert any(a.startswith('-getProperty:') for a in args) and any(a.startswith('-getItem:') for a in args)
props_requested=next(a.split(':',1)[1].split(',') for a in args if a.startswith('-getProperty:'))
item_requested=next(a.split(':',1)[1].split(',') for a in args if a.startswith('-getItem:'))
values=dict.fromkeys(props_requested,'')
values.update(MSBuildAllProjects=str(project),MSBuildToolsPath=os.environ['FAKE_SDK'],NETCoreSdkVersion='10.0.400',TargetFramework=tfm,
 RuntimeIdentifier=props.get('RuntimeIdentifier',''),Configuration=configuration,TargetPath=str(target),ProjectAssetsFile=str(assets),
 ArtifactsPath=str(tree),BaseIntermediateOutputPath=str(obj),BaseOutputPath=str(tree/'bin'/name),MSBuildProjectName=name,
 MSBuildSDKsPath=os.environ['FAKE_SDK'],NetCoreTargetingPackRoot=os.environ['FAKE_PACKS'],UsingMicrosoftNETSdk='true',
 RestorePackagesPath=props.get('RestorePackagesPath',''),IsTestProject='true' if project.name=='Test.csproj' else '',IsTestingPlatformApplication='true' if os.environ.get('FAKE_RUNNER')=='MTP' and project.name=='Test.csproj' else '')
if os.environ.get('FAKE_CONSTANT_FILE'): values['DefineConstants']=Path(os.environ['FAKE_CONSTANT_FILE']).read_text()
items={name:[] for name in item_requested}
items.update(Compile=[{'Identity':'Code.cs','FullPath':str(directory/'Code.cs')}],ProjectReference=refs,PackageReference=packages)
if (directory/'settings.json').exists(): items['AdditionalFiles']=[{'Identity':'settings.json','FullPath':str(directory/'settings.json')}]
print(json.dumps({'Properties':values,'Items':items}))
'''


@pytest.fixture
def sdk(tmp_path, monkeypatch):
    repo=tmp_path/'repo'; repo.mkdir()
    subprocess.run(['git','init','-q',str(repo)],check=True)
    (repo/'Test.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net10.0</TargetFramework></PropertyGroup></Project>')
    (repo/'Code.cs').write_text('class Code {}')
    (repo/'settings.json').write_text('{"generator":"v1"}')
    (repo/'Directory.Build.props').write_text('<Project><PropertyGroup><DefineConstants>ONE</DefineConstants></PropertyGroup></Project>')
    fake=tmp_path/'dotnet'; fake.write_text('#!'+sys.executable+'\n'+FAKE); fake.chmod(0o755)
    tools=tmp_path/'sdk'; tools.mkdir(); (tools/'Sdk.props').write_text('<Project/>'); (tools/'tool.dll').write_bytes(b'sdk-v1')
    packs=tmp_path/'packs'; packs.mkdir(); (packs/'ref.dll').write_bytes(b'ref-v1')
    log=tmp_path/'sdk.log'
    env={'PATH':os.defpath,'LANG':'C','FAKE_LOG':str(log),'FAKE_SDK':str(tools),'FAKE_PACKS':str(packs)}
    monkeypatch.setattr(dotnet_slots,'directory',lambda:tmp_path/'host-slots')
    with ContinuityStore(tmp_path/'ledger') as store:
        yield store,repo,env,str(fake),log


@pytest.fixture(autouse=True)
def installed_sdk_host_slots(request,tmp_path,monkeypatch):
    if not request.node.name.startswith('test_real_installed_sdk_'):
        return
    # The live SDK fixtures test evaluation and immutable reuse. Give each its
    # own host admission database so concurrent pytest runs cannot consume one
    # another's production slots. Cross-process/shared-ledger admission is
    # exercised separately using the hermetic SDK and an explicitly shared DB.
    monkeypatch.setattr(dotnet_slots,'directory',lambda:tmp_path/'installed-sdk-host-slots')


def calls(log, target):
    return [json.loads(line) for line in log.read_text().splitlines() if target in json.loads(line)['args']]


def build(sdk, **kw):
    store,repo,env,dotnet,_=sdk
    return broker.build(store,'Test.csproj',cwd=repo,env=env,dotnet=dotnet,**kw)


def test_evaluated_graph_and_transitive_references_use_sdk_inputs(sdk):
    store,repo,env,dotnet,log=sdk
    (repo/'Lib.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"/>')
    result=broker.graph(store,'Test.csproj',cwd=repo,env=env,dotnet=dotnet)
    assert result['complete'],result['reasons']
    assert len(result['nodes'])==2
    assert any(path.endswith('Directory.Build.props') for path in result['source']['declared_inputs'])
    assert any(item['kind']=='AdditionalFiles' for item in result['nodes'][0]['inputs'])
    assert len(calls(log,'-preprocess'))==2


def test_reuse_restore_and_build_and_replay_are_bound_to_artifacts(sdk):
    store,repo,env,dotnet,log=sdk
    first=build(sdk,request_id='one',configuration='Release',framework='net10.0',runtime='fake-arm64')
    assert first['valid'] and not first['reused'],first
    second=build(sdk,request_id='two',configuration='Release',framework='net10.0',runtime='fake-arm64')
    assert second['valid'] and second['reused'] and second['build_id']==first['build_id']
    assert second['job_id']!=first['job_id']
    assert build(sdk,request_id='one',configuration='Release',framework='net10.0',runtime='fake-arm64')==first
    assert len(calls(log,'-target:Restore'))==1 and len(calls(log,'-target:Build'))==1
    fresh=build(sdk,request_id='fresh',configuration='Release',framework='net10.0',runtime='fake-arm64',fresh=True)
    assert fresh['valid'] and not fresh['reused']
    assert len(calls(log,'-target:Restore'))==1 and len(calls(log,'-target:Build'))==2
    metadata=broker.artifact_metadata(store,fresh['build_id'])
    assert any(file['path'].endswith('project.assets.json') for file in metadata['manifest'])
    assert metadata['graph']['nodes'][0]['properties']['RuntimeIdentifier']=='fake-arm64'
    with pytest.raises(Conflict,match='different execution options'):
        build(sdk,request_id='one',configuration='Debug')


@pytest.mark.parametrize('change',['source','import','generator','SDK','package'])
def test_complete_closure_changes_invalidate_artifacts(sdk,change):
    store,repo,env,dotnet,log=sdk
    env['FAKE_PACKAGES']='1'
    first=build(sdk)
    assert first['valid'],first
    if change=='source': (repo/'Code.cs').write_text('class Changed {}')
    elif change=='import': (repo/'Directory.Build.props').write_text('<Project><PropertyGroup><DefineConstants>TWO</DefineConstants></PropertyGroup></Project>')
    elif change=='generator': (repo/'settings.json').write_text('{"generator":"v2"}')
    elif change=='SDK': (Path(env['FAKE_SDK'])/'tool.dll').write_bytes(b'sdk-v2')
    else: (store.root/'state/dotnet/packages/pkg/1.0.0/generator.dll').write_text('changed-package')
    second=build(sdk)
    assert second['valid'] and not second['reused'],second


def test_unmodeled_generator_and_old_sdk_are_honestly_incomplete(sdk):
    store,repo,env,dotnet,log=sdk
    (repo/'Custom.targets').write_text('<Project><Target Name="Generate"><Exec Command="read-hidden-input"/></Target></Project>')
    value=build(sdk)
    assert value['success'] and not value['valid'] and 'incomplete_evaluated_closure' in value['reasons']
    assert not broker.artifact_metadata(store,value['build_id'])['complete']
    with pytest.raises(Conflict,match='complete intact'):
        broker.test(store,value['build_id'],env=env)
    env['FAKE_OLD']='1'
    graph=broker.graph(store,'Test.csproj',cwd=repo,env=env,dotnet=dotnet)
    assert not graph['complete'] and 'query support' in graph['reasons'][0]


def test_write_restore_during_build_never_certifies_success(sdk):
    sdk[2]['FAKE_MUTATE']='1'
    result=build(sdk)
    assert result['success'] and not result['valid']
    assert 'inputs_changed_during_build' in result['reasons']


@pytest.mark.parametrize('runner',['VSTest','MTP'])
def test_shards_run_exact_artifacts_without_rebuild_and_runner_flag_mixups(sdk,runner):
    store,repo,env,dotnet,log=sdk
    env['FAKE_RUNNER']=runner
    first=build(sdk,configuration='Release',framework='net10.0')
    assert first['valid'],first
    result=broker.test(store,first['build_id'],env=env,request_id='shard',filter_expression='Category=Unit' if runner=='VSTest' else None,
                       runner_args=['--native-option','value'] if runner=='MTP' else [])
    assert result['valid'] and result['runner']==runner
    assert len(calls(log,'-target:Build'))==1
    commands=calls(log,'--no-build')
    assert len(commands)==1
    assert '--test-modules' in commands[0]['args'] if runner=='MTP' else '--filter' in commands[0]['args']
    assert broker.test(store,first['build_id'],env=env,request_id='shard',filter_expression='Category=Unit' if runner=='VSTest' else None,
                       runner_args=['--native-option','value'] if runner=='MTP' else [])==result


def test_materialization_checks_tar_manifest_and_output_integrity(sdk,tmp_path):
    store,repo,env,dotnet,log=sdk
    first=build(sdk)
    destination=tmp_path/'materialized'
    result=broker.materialize(store,first['build_id'],destination)
    assert result['files']>0 and list(destination.rglob('Test.dll'))
    (store.artifacts/first['artifact_hash']).write_bytes(b'corrupt')
    assert not broker.status(store,first['job_id'])['result']['valid']
    with pytest.raises(ValidationError,match='missing or corrupt'):
        broker.materialize(store,first['build_id'],tmp_path/'bad')


def test_planned_run_builds_emit_new_run_receipts_and_reject_other_owners(sdk):
    store,repo,env,dotnet,log=sdk
    with store.transaction() as tx:
        tx.put_task('T',{'goal':'Build','workdir':str(repo)},expected_revision=0)
        run=tx.start_run('T',1,'eng-001')
    first=build(sdk,run_id=run,worker_id='eng-001',request_id='one')
    assert first['valid'] and first['receipt_id']
    second=build(sdk,run_id=run,worker_id='eng-001',request_id='two')
    assert second['reused'] and second['receipt_id']!=first['receipt_id']
    assert store.db.execute('SELECT count(*) FROM dotnet_receipts').fetchone()[0]==2
    assert all(row[0]=='reduced' for row in store.db.execute("SELECT disposition FROM events WHERE stream_id='builds'"))
    with pytest.raises(Conflict,match='does not own'):
        build(sdk,run_id=run,worker_id='eng-002',request_id='one')


def test_equal_live_jobs_are_joined_even_by_fresh_requests(sdk,tmp_path):
    store,repo,env,dotnet,log=sdk
    ready=tmp_path/'ready'; release=tmp_path/'release'
    env.update(FAKE_READY=str(ready),FAKE_RELEASE=str(release))
    def requested(request_id,**options):
        with ContinuityStore(store.root) as other:
            return broker.build(other,'Test.csproj',cwd=repo,env=env,dotnet=dotnet,request_id=request_id,**options)
    with ThreadPoolExecutor(3) as pool:
        first=pool.submit(requested,'first')
        import time
        end=time.monotonic()+10
        while not ready.exists() and time.monotonic()<end: time.sleep(.02)
        assert ready.exists()
        try:
            second=pool.submit(requested,'second')
            fresh=pool.submit(requested,'fresh',fresh=True)
            def joining():
                rows=store.db.execute("SELECT payload FROM dotnet_jobs WHERE request_id IN ('second','fresh')").fetchall()
                return len(rows)==2 and all(json.loads(row[0]).get('joining') for row in rows)
            while not joining() and time.monotonic()<end+20: time.sleep(.02)
            assert joining() and not second.done() and not fresh.done()
        finally:
            release.write_text('done')
        first,second,fresh=(future.result(timeout=20) for future in (first,second,fresh))
    assert first['valid'] and 'joined' not in first,first
    for joined in (second,fresh):
        assert joined['valid'] and not joined['reused'],joined
        assert joined['joined']=={'root':str(store.root.resolve()),'job_id':first['job_id']} and joined['build_id']==first['build_id']
    assert len(calls(log,'-target:Build'))==1


def test_host_limit_applies_across_separate_ledgers_and_queued_retries(sdk,tmp_path):
    store,repo,env,dotnet,log=sdk
    (store.root/'config').mkdir(exist_ok=True)
    (store.root/'config'/'hx.json').write_text(json.dumps({'pools':{'build':1}}))
    dotnet_slots.acquire('external-job','build',tmp_path/'other-root')
    try:
        with pytest.raises(Conflict,match='concurrency limit'):
            build(sdk,request_id='queued')
        row=store.db.execute("SELECT status FROM dotnet_jobs WHERE request_id='queued'").fetchone()
        assert row[0]=='queued'
    finally:
        dotnet_slots.release('external-job')
    assert build(sdk,request_id='queued')['valid']


def test_recovery_requires_dead_owner_and_known_quiescent_groups(sdk,monkeypatch):
    store,repo,env,dotnet,log=sdk
    original=broker._execute
    def interrupted(*args,**kwargs):
        original(*args,**kwargs)
        raise RuntimeError('acknowledgement interrupted')
    monkeypatch.setattr(broker,'_execute',interrupted)
    with pytest.raises(RuntimeError): build(sdk,request_id='interrupted')
    job=store.db.execute("SELECT job_id FROM dotnet_jobs WHERE request_id='interrupted'").fetchone()[0]
    with pytest.raises(Conflict,match='still alive'): broker.reconcile(store,job)
    monkeypatch.setattr(native_processes,'current',lambda identity:None)
    monkeypatch.setattr(os,'killpg',lambda *args:None)
    with pytest.raises(Conflict,match='group is still present'): broker.reconcile(store,job)
    def gone(*args): raise ProcessLookupError
    monkeypatch.setattr(os,'killpg',gone)
    assert broker.reconcile(store,job)['reconciled']
    monkeypatch.setattr(broker,'_execute',original)
    assert build(sdk,request_id='recovered')['valid']
    with pytest.raises(Conflict,match='unresolved execution'): build(sdk,request_id='interrupted')


def test_real_installed_sdk_builds_no_network_tiny_fixture(tmp_path):
    dotnet=shutil.which('dotnet')
    if dotnet is None: pytest.skip('no local .NET SDK')
    repo=tmp_path/'real'; repo.mkdir()
    child={**os.environ,'DOTNET_CLI_HOME':str(tmp_path/'sdk-home'),'DOTNET_GENERATE_ASPNET_CERTIFICATE':'false',
           'DOTNET_CLI_TELEMETRY_OPTOUT':'1','DOTNET_NOLOGO':'1'}
    result=subprocess.run([dotnet,'--list-sdks'],env=child,capture_output=True,text=True,timeout=10)
    major=next((line.split('.')[0] for line in reversed(result.stdout.splitlines()) if line[:1].isdigit()),None)
    if major is None or int(major)<8: pytest.skip('evaluated queries/artifacts output require SDK 8+')
    (repo/'Real.csproj').write_text(f'<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net{major}.0</TargetFramework></PropertyGroup></Project>')
    (repo/'Code.cs').write_text('public class Code { public static int Answer() => 42; }')
    (repo/'NuGet.Config').write_text('<configuration><packageSources><clear /></packageSources></configuration>')
    with ContinuityStore(tmp_path/'ledger') as store:
        contract={'complete':True,'inputs':['Real.csproj','Code.cs','NuGet.Config']}
        first=broker.build(store,'Real.csproj',cwd=repo,dotnet=dotnet,env={'PATH':os.environ['PATH'],'LANG':'C'},source_contract=contract,request_id='real-first')
        assert first['valid'],first
        second=broker.build(store,'Real.csproj',cwd=repo,dotnet=dotnet,env={'PATH':os.environ['PATH'],'LANG':'C'},source_contract=contract,request_id='real-second')
        assert second['valid'] and second['reused'] and first['build_id']==second['build_id']
        broker.materialize(store,first['build_id'],tmp_path/'outputs')
        assert list((tmp_path/'outputs').rglob('Real.dll'))
        assert not (repo/'obj').exists() and not (repo/'bin').exists()


@pytest.mark.parametrize('extension',['.sln','.slnx'])
def test_solution_sdk_fallback_builds_original_entry_without_certifying_unknown_mappings(sdk,extension):
    store,repo,env,dotnet,log=sdk
    (repo/'Lib.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"/>')
    entry=repo/('All'+extension); entry.write_text('SDK enumerates this solution')
    result=broker.build(store,entry.name,cwd=repo,env=env,dotnet=dotnet)
    assert result['success'] and not result['valid'],result
    metadata=broker.artifact_metadata(store,result['build_id'])
    assert len(metadata['graph']['nodes'])==2
    assert not metadata['complete']
    assert any('solution configuration mapping' in reason for reason in result['reasons'])
    assert calls(log,'-target:Build')[-1]['args'][1]==str(entry)
    assert not list(repo.glob('*.metaproj'))


@pytest.mark.parametrize('extension',['.sln','.slnx'])
@pytest.mark.parametrize('runner',['VSTest','MTP'])
def test_sdk_mapped_solution_reuses_build_for_exact_output_shards(sdk,monkeypatch,extension,runner):
    from hx import dotnet_solution
    store,repo,env,dotnet,log=sdk
    env['FAKE_RUNNER']=runner
    (repo/'Lib.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"/>')
    entry=repo/('All'+extension); entry.write_text('authoritative fake SDK solution fixture')
    def mapping(project,cwd,child,**options):
        # This substitutes the SDK metadata boundary, preserving all actual
        # broker evaluation/build/test calls and exact global property values.
        props={'Configuration':options['configuration'],'Platform':options['platform'] or 'AnyCPU',
               'RestorePackagesPath':child['NUGET_PACKAGES'],'UseSharedCompilation':'false'}
        if options['framework']: props['TargetFramework']=options['framework']
        if options['artifacts_path']:
            props.update(ArtifactsPath=str(options['artifacts_path']),UseArtifactsOutput='true')
        reference={'project':str(repo/'Lib.csproj'),'global_properties':props}
        return {'schema_version':1,'complete':True,'platform':props['Platform'],'reader_hash':'fake-sdk-v1',
                'nodes':[{'project':str(repo/'Test.csproj'),'global_properties':props,'references':[reference]},
                         {**reference,'references':[]}]}
    monkeypatch.setattr(dotnet_solution,'mapping',mapping)
    options=dict(cwd=repo,env=env,dotnet=dotnet,configuration='Release',framework='net10.0',platform='AnyCPU')
    first=broker.build(store,entry.name,request_id='solution-first',**options)
    second=broker.build(store,entry.name,request_id='solution-second',**options)
    assert first['valid'] and second['valid'] and second['reused'],(first,second)
    assert first['build_id']==second['build_id'] and first['job_id']!=second['job_id']
    artifact=broker.artifact_metadata(store,first['build_id'])
    assert all(node['global_properties']['Platform']=='AnyCPU' for node in artifact['graph']['nodes'])
    for request in ('unit-shard','integration-shard'):
        shard=broker.test(store,second['build_id'],env=env,request_id=request,project='Test.csproj')
        assert shard['valid'],shard
    assert len(calls(log,'-target:Build'))==1
    assert calls(log,'-target:Build')[0]['args'][1]==str(entry)
    shard_calls=calls(log,'--no-build')
    assert len(shard_calls)==2
    targets=[Path(call['args'][call['args'].index('--test-modules')+1] if runner=='MTP' else call['args'][1]) for call in shard_calls]
    expected=Path(artifact['tree'])/artifact['modules'][0]['target']
    assert targets[0]!=targets[1] and targets[0].read_bytes()==targets[1].read_bytes()==expected.read_bytes()


@pytest.mark.parametrize('extension',['sln','slnx'])
def test_real_installed_sdk_reuses_multi_project_solution_with_sdk_mapping(tmp_path,extension):
    dotnet=shutil.which('dotnet')
    if dotnet is None: pytest.skip('no local .NET SDK')
    repo=tmp_path/'solution'; repo.mkdir()
    child={**os.environ,'DOTNET_CLI_HOME':str(tmp_path/'sdk-home'),'DOTNET_GENERATE_ASPNET_CERTIFICATE':'false',
           'DOTNET_CLI_TELEMETRY_OPTOUT':'1','DOTNET_NOLOGO':'1'}
    output=subprocess.run([dotnet,'--list-sdks'],env=child,capture_output=True,text=True,timeout=10)
    major=next((line.split('.')[0] for line in reversed(output.stdout.splitlines()) if line[:1].isdigit()),None)
    if major is None or int(major)<8: pytest.skip('requires SDK 8+')
    for name in ('Lib','App'):
        folder=repo/name; folder.mkdir()
        reference='<ItemGroup><ProjectReference Include="../Lib/Lib.csproj"/></ItemGroup>' if name=='App' else ''
        (folder/(name+'.csproj')).write_text(f'<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><TargetFramework>net{major}.0</TargetFramework></PropertyGroup>{reference}</Project>')
        (folder/'Code.cs').write_text(f'public class {name}Class {{ public static int Value => 42; }}')
    (repo/'NuGet.Config').write_text('<configuration><packageSources><clear /></packageSources></configuration>')
    if extension=='slnx' and int(major)<9: pytest.skip('slnx requires SDK 9+')
    format_args=['--format',extension] if int(major)>=9 else []
    created=subprocess.run([dotnet,'new','sln','--name','All','--output',str(repo),*format_args],env=child,capture_output=True,text=True,timeout=20)
    assert created.returncode==0,created.stdout+created.stderr
    solution=next(iter(repo.glob('All.sln*')))
    added=subprocess.run([dotnet,'sln',str(solution),'add',str(repo/'Lib/Lib.csproj'),str(repo/'App/App.csproj')],env=child,capture_output=True,text=True,timeout=20)
    assert added.returncode==0,added.stdout+added.stderr
    # A solution configuration can choose a different configuration for one
    # project. Production reads this through the SDK API, never this text.
    if extension=='sln':
        content=solution.read_text()
        import re
        lib_id=re.search(r'Project\("[^"]+"\) = "Lib", "[^"]+", "([^"]+)"',content)[1]
        content=re.sub(r'('+re.escape(lib_id)+r'\.Release\|[^=]+ = )Release\|',r'\1Debug|',content)
        solution.write_text(content)
    with ContinuityStore(tmp_path/'ledger') as store:
        result=broker.build(store,str(solution),cwd=repo,env={'PATH':os.environ['PATH'],'LANG':'C'},dotnet=dotnet,
                            configuration='Release',source_contract={'complete':True,'inputs':[str(repo)]},request_id='solution-first')
        assert result['success'] and result['valid'],result
        again=broker.build(store,str(solution),cwd=repo,env={'PATH':os.environ['PATH'],'LANG':'C'},dotnet=dotnet,
                            configuration='Release',source_contract={'complete':True,'inputs':[str(repo)]},request_id='solution-second')
        assert again['valid'] and again['reused'] and again['build_id']==result['build_id'],again
        assert again['job_id']!=result['job_id']
        metadata=broker.artifact_metadata(store,result['build_id'])
        assert metadata['graph']['solution_mapping']['complete']
        configurations={(Path(node['project']).stem,node['properties']['Configuration']) for node in metadata['graph']['nodes']}
        assert ('App','Release') in configurations
        assert ('Lib','Debug' if extension=='sln' else 'Release') in configurations
        broker.materialize(store,result['build_id'],tmp_path/'outputs')
        assert list((tmp_path/'outputs').rglob('Lib.dll')) and list((tmp_path/'outputs').rglob('App.dll'))
        assert not list(repo.rglob('obj')) and not list(repo.rglob('bin')) and not list(repo.rglob('*.metaproj'))


def test_real_process_equal_jobs_and_host_concurrency_share_durable_admission(sdk,tmp_path):
    store,repo,env,dotnet,log=sdk
    ready=tmp_path/'process-ready'; release=tmp_path/'process-release'
    env.update(FAKE_READY=str(ready),FAKE_RELEASE=str(release))
    body=tmp_path/'child-options.json'
    body.write_text(json.dumps({'repo':str(repo),'env':env,'dotnet':dotnet,'root':str(store.root),'host':str(dotnet_slots.directory()),
                                'jobs':str(host_jobs.directory())}))
    script=r'''
import json,sys
from pathlib import Path
from hx import dotnet_build as broker,dotnet_slots,host_jobs
from hx.continuity_store import ContinuityStore,canonical
args=json.loads(Path(sys.argv[1]).read_text())
dotnet_slots.directory=lambda:Path(args['host'])
host_jobs.directory=lambda:Path(args['jobs'])
with ContinuityStore(Path(args['root'])) as store:
 result=broker.build(store,'Test.csproj',cwd=Path(args['repo']),env=args['env'],dotnet=args['dotnet'],request_id='child-process')
 print(canonical(result))
'''
    process=subprocess.Popen([sys.executable,'-c',script,str(body)],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
    import time
    def duplicate():
        with ContinuityStore(store.root) as ledger:
            return broker.build(ledger,'Test.csproj',cwd=repo,env=env,dotnet=dotnet,request_id='duplicate-process')
    with ThreadPoolExecutor(1) as pool:
        try:
            end=time.monotonic()+15
            while not ready.exists() and process.poll() is None and time.monotonic()<end: time.sleep(.02)
            assert ready.exists(),process.communicate(timeout=1)
            joiner=pool.submit(duplicate)
            with ContinuityStore(tmp_path/'other-ledger') as other:
                (other.root/'config').mkdir(exist_ok=True)
                (other.root/'config'/'hx.json').write_text(json.dumps({'pools':{'build':1}}))
                # A different build needs its own slot; the box holds the only one.
                with pytest.raises(Conflict,match='concurrency limit'):
                    broker.build(other,'Test.csproj',cwd=repo,env=env,dotnet=dotnet,configuration='Release',request_id='other-process')
            active=store.db.execute("SELECT job_id FROM dotnet_jobs WHERE request_id='child-process'").fetchone()[0]
            observed=broker.status(store,active)
            assert observed['status']=='running' and observed['owner']['pid']==process.pid
            assert observed['children'] and not joiner.done()
        finally:
            release.write_text('done')
            stdout,stderr=process.communicate(timeout=15)
        joined=joiner.result(timeout=15)
    assert process.returncode==0,stderr
    assert json.loads(stdout)['valid']
    assert joined['valid'] and joined['joined']['job_id']==active,joined
    assert len(calls(log,'-target:Build'))==1


def test_two_roots_requesting_the_same_build_get_one_process_and_two_receipts(sdk,tmp_path):
    """Root A builds its checkout; root B asks for the same build from a second worktree at the same commit."""
    import time
    store,repo,env,dotnet,log=sdk
    commit_sources(repo)
    worktree=tmp_path/'second-worktree'
    git(repo,'worktree','add','-q','--detach',str(worktree))
    ready=tmp_path/'ready'; release=tmp_path/'release'
    env.update(FAKE_READY=str(ready),FAKE_RELEASE=str(release))
    def requested(root,checkout,worker):
        with ContinuityStore(root) as ledger:
            with ledger.transaction() as tx:
                tx.put_task('T',{'goal':'Build','workdir':str(checkout)},expected_revision=0)
                run=tx.start_run('T',1,worker)
            started=time.perf_counter()
            result=broker.build(ledger,'Test.csproj',cwd=checkout,env=env,dotnet=dotnet,run_id=run,worker_id=worker)
            return run,result,time.perf_counter()-started
    with ThreadPoolExecutor(2) as pool:
        first=pool.submit(requested,store.root,repo,'eng-001')
        end=time.monotonic()+10
        while not ready.exists() and time.monotonic()<end: time.sleep(.02)
        try:
            assert ready.exists()
            second=pool.submit(requested,tmp_path/'other-root',worktree,'eng-002')
            time.sleep(.5)
            assert not second.done()
        finally:
            release.write_text('done')
        (_,owner,owned),(run,joiner,waited)=first.result(timeout=30),second.result(timeout=30)
    assert owner['valid'] and 'joined' not in owner,owner
    assert joiner['valid'] and not joiner['reused'] and joiner['joined']=={'root':str(store.root.resolve()),'job_id':owner['job_id']},joiner
    assert joiner['artifact_hash']==owner['artifact_hash'] and joiner['build_id']!=owner['build_id']
    assert joiner['receipt_id']!=owner['receipt_id'] and joiner['graph_hash']==owner['graph_hash']
    assert len(calls(log,'-target:Restore'))==1 and len(calls(log,'-target:Build'))==1
    assert store.db.execute('SELECT count(*) FROM dotnet_receipts').fetchone()[0]==1
    with ContinuityStore(tmp_path/'other-root') as other:
        assert other.db.execute('SELECT count(*) FROM dotnet_receipts').fetchone()[0]==1
        adopted=broker.artifact_metadata(other,joiner['build_id'])
        assert adopted['origin']=={'root':str(store.root.resolve()),'build_id':owner['build_id']}
        assert Path(adopted['tree']).is_relative_to(other.root) and adopted['recipe']['cwd']==str(worktree.resolve())
        assert broker.current(other,run,joiner['receipt_id'],env=env,worker_id='eng-002')['current']
        assert broker.test(other,joiner['build_id'],env=env)['valid']
        (worktree/'Code.cs').write_text('class Diverged {}')
        stale=broker.current(other,run,joiner['receipt_id'],env=env,worker_id='eng-002')
        assert not stale['current'] and 'build_inputs_changed_or_unknown' in stale['reasons']
    print(f'\njoined build: owner {owned:.2f}s, joiner {waited:.2f}s from another root and worktree; '
          f"restore={len(calls(log,'-target:Restore'))} build={len(calls(log,'-target:Build'))} processes, 2 receipts")


def test_run_bound_receipt_revalidation_tracks_source_and_archive_integrity(sdk):
    store,repo,env,dotnet,log=sdk
    with store.transaction() as tx:
        tx.put_task('T',{'goal':'Build','workdir':str(repo)},expected_revision=0)
        run=tx.start_run('T',1,'eng-001')
    result=build(sdk,run_id=run,worker_id='eng-001')
    assert broker.current(store,run,result['receipt_id'],env=env,worker_id='eng-001')['current']
    (repo/'Code.cs').write_text('class Changed {}')
    changed=broker.current(store,run,result['receipt_id'],env=env,worker_id='eng-001')
    assert not changed['current'] and 'build_inputs_changed_or_unknown' in changed['reasons']
    with pytest.raises(ValidationError,match='another run'):
        broker.current(store,'missing-run',result['receipt_id'],env=env)


def test_nested_relative_entrypoint_uses_one_project_path_and_cli_examples(sdk,monkeypatch,capsys):
    store,repo,env,dotnet,log=sdk
    monkeypatch.chdir(repo.parent)
    result=broker.build(store,'repo/Test.csproj',env=env,dotnet=dotnet)
    assert result['valid'],result
    assert calls(log,'-target:Build')[-1]['args'][1]==str(repo/'Test.csproj')
    for name in ('graph','build','test','status','materialize','reconcile'):
        with pytest.raises(SystemExit) as stopped:
            broker.main([name,'--help'],store.root,env=env)
        assert stopped.value.code==0
        assert 'example: hx build '+name in capsys.readouterr().out


def test_restore_write_restore_does_not_certify_a_build(sdk):
    sdk[2]['FAKE_RESTORE_MUTATE']='1'
    result=build(sdk)
    assert result['success'] and not result['valid']
    assert 'inputs_changed_during_restore' in result['reasons']


def test_unresolved_run_jobs_gate_completion_until_known_queued_reconciliation(sdk):
    store,repo,env,dotnet,log=sdk
    with store.transaction() as tx:
        tx.put_task('T',{'goal':'Build','workdir':str(repo)},expected_revision=0)
        run=tx.start_run('T',1,'eng-001')
    job,_,_=broker._request(store,'build',run,'queued',{'project':str(repo/'Test.csproj')},repo,'eng-001')
    with store.transaction() as tx:
        with pytest.raises(Conflict,match='queued'):
            broker.require_drained(tx,run)
    assert broker.reconcile(store,job)['status']=='abandoned'
    with store.transaction() as tx:
        broker.require_drained(tx,run)


def test_build_preparation_owner_blocks_cancel_and_dead_unknown_queries_stay_unresolved(sdk,monkeypatch):
    store,repo,env,dotnet,log=sdk
    job,_,_=broker._request(store,'build',None,'preparing',{'project':str(repo/'Test.csproj')},repo,None)
    broker._begin_preparation(store,job)
    assert broker.status(store,job)['phase']=='evaluating'
    with pytest.raises(Conflict,match='owner is still alive'):
        broker.reconcile(store,job)
    monkeypatch.setattr(native_processes,'current',lambda identity:None)
    with pytest.raises(Conflict,match='preparation spawn ownership is unknown'):
        broker.reconcile(store,job)
    broker._end_preparation(store,job)
    assert broker.reconcile(store,job)['status']=='abandoned'


def test_cancelled_queued_request_cannot_be_revived_by_admission_or_completion(sdk):
    store,repo,env,dotnet,log=sdk
    job,_,_=broker._request(store,'build',None,'cancelled',{'project':str(repo/'Test.csproj')},repo,None)
    broker.reconcile(store,job)
    with pytest.raises(Conflict,match='cannot revive'):
        broker._admit(store,job,'build',None,None,repo)
    with pytest.raises(Conflict,match='cannot revive'):
        broker._finish(store,job,{'valid':False,'reasons':[]},None,None,repo)
    assert broker.status(store,job)['status']=='abandoned'
    with closing(dotnet_slots.connect()) as db:
        assert db.execute('SELECT count(*) FROM slots').fetchone()[0]==0


def test_expected_capture_failure_settles_native_job_without_reuse(sdk,monkeypatch):
    store,repo,env,dotnet,log=sdk
    def corrupt(*args,**kwargs):
        raise ValidationError('output changed while snapshotting')
    monkeypatch.setattr(dotnet_artifacts,'capture',corrupt)
    result=build(sdk)
    assert not result['valid'] and 'restore_artifact_capture_failed' in result['reasons']
    assert broker.status(store,result['job_id'])['status']=='finished'
    assert host_jobs.get(result['job_id'])['status']=='finished' and not host_jobs.get(result['job_id'])['result']['shareable']
    with closing(dotnet_slots.connect()) as db:
        assert db.execute('SELECT count(*) FROM slots').fetchone()[0]==0


def test_restore_mutation_does_not_seed_a_reusable_restore(sdk):
    store,repo,env,dotnet,log=sdk
    env['FAKE_RESTORE_MUTATE']='1'
    result=build(sdk)
    assert not result['valid']
    assert store.db.execute('SELECT count(*) FROM dotnet_restore_cache').fetchone()[0]==0


def test_unmodeled_response_files_are_explicitly_incomplete(sdk):
    store,repo,env,dotnet,log=sdk
    (repo/'Directory.Build.rsp').write_text('-p:CustomFlag=Unexpected')
    result=broker.graph(store,'Test.csproj',cwd=repo,env=env,dotnet=dotnet)
    assert not result['complete'] and any('response files' in reason for reason in result['reasons'])


def test_actual_evaluated_compiler_properties_bind_artifact_identity(sdk,tmp_path):
    constant=tmp_path/'computed-constant'; constant.write_text('ONE')
    sdk[2]['FAKE_CONSTANT_FILE']=str(constant)
    first=build(sdk)
    assert first['valid'],first
    constant.write_text('TWO')
    second=build(sdk)
    assert second['valid'] and not second['reused'],second


def git(repo,*args):
    return subprocess.run(['git','-c','core.hooksPath=/dev/null','-c','commit.gpgSign=false','-C',str(repo),*args],capture_output=True,text=True,check=True).stdout.strip()


def commit_sources(repo):
    git(repo,'add','.')
    git(repo,'-c','user.name=Fixture','-c','user.email=fixture@example.test','commit','-qm','Initial sources')


def test_repository_revision_is_a_build_input_even_when_file_bytes_match(sdk):
    store,repo,env,dotnet,log=sdk
    commit_sources(repo)
    first=build(sdk)
    assert first['valid'],first
    git(repo,'-c','user.name=Fixture','-c','user.email=fixture@example.test','commit','--allow-empty','-qm','Different source revision')
    second=build(sdk)
    assert second['valid'] and not second['reused'],second
    assert second['graph_hash']!=first['graph_hash']


def test_scoped_artifacts_ignore_unrelated_repository_edits_and_staging(sdk):
    store,repo,env,dotnet,log=sdk
    (repo/'unrelated.txt').write_text('one')
    commit_sources(repo)
    contract={'complete':True,'inputs':['Test.csproj','Code.cs','settings.json','Directory.Build.props']}
    first=build(sdk,source_contract=contract)
    (repo/'unrelated.txt').write_text('two')
    git(repo,'add','unrelated.txt')
    second=build(sdk,source_contract=contract)
    assert first['valid'] and second['valid'] and second['reused'],second


def test_package_custom_targets_require_a_declared_complete_side_input_closure(sdk):
    store,repo,env,dotnet,log=sdk
    env.update(FAKE_PACKAGES='1',FAKE_PACKAGE_TARGET='1')
    first=build(sdk)
    assert first['success'] and not first['valid'],first
    assert any('custom targets/generators' in reason for reason in first['reasons'])
    second=build(sdk,source_contract={'complete':True,'inputs':['Code.cs','Test.csproj','settings.json','Directory.Build.props']})
    assert second['valid'],second


def test_run_cannot_build_an_entrypoint_outside_its_assigned_workdir(sdk,tmp_path):
    store,repo,env,dotnet,log=sdk
    outside=tmp_path/'outside.csproj'; outside.write_text('<Project Sdk="Microsoft.NET.Sdk"/>')
    with store.transaction() as tx:
        tx.put_task('T',{'goal':'Own one source tree','workdir':str(repo)},expected_revision=0)
        run=tx.start_run('T',1,'eng-001')
    with pytest.raises(Conflict,match='entry point is outside'):
        broker.build(store,str(outside),cwd=repo,env=env,dotnet=dotnet,run_id=run,worker_id='eng-001')
    assert store.db.execute('SELECT count(*) FROM dotnet_jobs').fetchone()[0]==0


def test_nested_project_git_include_write_restore_changes_observation(sdk):
    from hx import dotnet_git
    store,repo,env,dotnet,log=sdk
    nested=repo/'Nested'; nested.mkdir()
    included=repo/'.git'/'extra-config'
    included.write_text('[source]\n url = initial\n')
    git(repo,'config','include.path','extra-config')
    before=dotnet_git.identity(nested,[str(repo/'Code.cs')],env)
    included.write_text('[source]\n url = temporary\n')
    included.write_text('[source]\n url = initial\n')
    after=dotnet_git.identity(nested,[str(repo/'Code.cs')],env)
    assert before['complete'] and after['complete']
    assert before['hash']==after['hash']
    assert before['observation_hash']!=after['observation_hash']
