from __future__ import annotations

import copy
import json

import pytest

from hx import checks, planning, dependency_packets as packets, unit_execution as units
from hx.continuity_store import Conflict, ContinuityStore
from hx.errors import Refused, ValidationError
from .test_appmap import git, mapped
from .test_map_updates import active
from .test_checks import definition
from .test_planning import plan, unit
from .test_unit_execution import fleet, commit


def packet(repo, name='implement', *, paths=None, prerequisites=None, resources=None):
    body = unit(repo,name,paths=paths,prerequisites=prerequisites)
    body['outputs'] = [{'id':'proof','version':1,'kind':'receipt','obligation':'Checks prove the declared packet behavior.'}]
    body['resources'] = resources or []
    body['check_policy'] = {'reviewer':'qa-001','required_checks':['unit'],'role':'backend-engineer'}
    return body


def packet_plan(fleet,tasks):
    body = plan(fleet,tasks)
    body['schema_version'] = 2
    return body


def binding(task, *, recipes=None):
    primary=next(iter(recipes)) if recipes else 'unit'
    return {'definition_of_done':['Every packet obligation has a current passing check.'],
            'checks':recipes or {'unit':definition()},
            'coverage':{name:[primary] for name in packets.obligations(task)}}


def bind(store, run, *, recipes=None):
    task = store.db.execute('SELECT t.payload FROM tasks t JOIN runs r ON r.task_id=t.task_id AND r.task_revision=t.revision WHERE run_id=?',(run,)).fetchone()
    body = binding(json.loads(task[0]),recipes=recipes)
    proposed = packets.bind_checks(store,run,body)
    packets.approve_binding(store,run,proposed['binding_hash'],'qa-001')
    return body


def run_receipts(store,run,names=('unit',)):
    return {name:checks.run_check(store,run,name)['receipt_id'] for name in names}


def test_partner_packets_omit_scripts_worker_binds_before_edits_and_policy_reviewer_is_independent(fleet):
    store,repo = fleet[:2]
    planning.apply(store,packet_plan(fleet,[packet(repo)]))
    assigned = units.assign(store,'implement',1,'eng-001')
    run = assigned['run_id']
    assert assigned['assignment']['checks']=={}
    with pytest.raises(Conflict,match='approval'):
        units.complete(store,run,{})
    task = planning.assignment(store,'implement')
    body = binding(task)
    proposed = packets.bind_checks(store,run,body)
    assert planning.assignment(store,'implement')['binding_status']=='proposed'
    with pytest.raises(Refused,match='independent'):
        packets.approve_binding(store,run,proposed['binding_hash'],'eng-001')
    with pytest.raises(Conflict,match='exact'):
        packets.approve_binding(store,run,'incorrect','qa-001')
    packets.approve_binding(store,run,proposed['binding_hash'],'qa-001')
    changed=copy.deepcopy(body)
    changed['checks']['unit']['argv'][-1]='print("different")'
    with pytest.raises(Conflict,match='frozen'):
        packets.bind_checks(store,run,changed)
    result=units.complete(store,run,run_receipts(store,run))
    assert result['readiness']['packet_covered'] and result['readiness']['deployable']
    assert store.db.execute('SELECT count(*) FROM leases WHERE run_id=?',(run,)).fetchone()[0]==0


def test_binding_rejects_incomplete_coverage_and_after_first_edit(fleet):
    store,repo=fleet[:2]
    planning.apply(store,packet_plan(fleet,[packet(repo)]))
    run=units.assign(store,'implement',1,'eng-001')['run_id']
    body=binding(planning.assignment(store,'implement'))
    body['coverage'].pop('acceptance.0')
    with pytest.raises(ValidationError,match='cover every'):
        packets.bind_checks(store,run,body)
    body=binding(planning.assignment(store,'implement'))
    (repo/'src').mkdir()
    (repo/'src/implement.py').write_text('edited = True\n')
    with pytest.raises(Conflict,match='clean|first edit'):
        packets.bind_checks(store,run,body)
    assert not units.audit(store,run)['within_scope']


def interface_plan(fleet,tmp_path):
    store,repo=fleet[:2]
    other=tmp_path/'consumer'
    git(repo,'worktree','add','-qb','consumer',str(other))
    producer=packet(repo,'producer',paths=['api.py','provider.py'])
    producer['outputs'] += [{'id':'interface','version':1,'kind':'contract','paths':['api.py'],'obligation':'The immutable API accepts one value and returns that value.'},
                            {'id':'provider','version':1,'kind':'source','paths':['provider.py'],'obligation':'Real provider returns the required value.'}]
    need={'task_id':'producer','output_id':'interface','version':1,'need':'substitutable','stand_in':True,'provider_output':'provider','provider_version':1}
    consumer=packet(other,'consumer',paths=['app.py'],prerequisites=[need])
    consumer['outputs'] += [{'id':'application','version':1,'kind':'source','paths':['app.py'],'obligation':'Consumer behavior works with the allowed stand-in.'}]
    planning.apply(store,packet_plan(fleet,[producer,consumer]))
    return producer,consumer,other


def publish_interface(fleet):
    store,repo=fleet[:2]
    run=units.assign(store,'producer',1,'eng-001')['run_id']
    contract_recipe=definition("from pathlib import Path; assert Path('api.py').read_text() == 'def adapt(value): return value\\n'")
    contract_recipe['id']='contract-check'
    contract_recipe['source']={'complete':True,'inputs':['api.py'],'dependencies':[]}
    recipe=definition("from pathlib import Path; assert Path('provider.py').read_text() == 'provider_value = 42\\n'")
    body=binding(planning.assignment(store,'producer'),recipes={'unit':recipe,'contract-check':contract_recipe})
    body['coverage']['output.interface']=['contract-check']
    proposal=packets.bind_checks(store,run,body)
    packets.approve_binding(store,run,proposal['binding_hash'],'qa-001')
    (repo/'api.py').write_text('def adapt(value): return value\n')
    commit(repo)
    receipts=run_receipts(store,run,['contract-check'])
    proof=packets.publish_contract(store,run,'interface',receipts)
    return run,proof


def test_early_interface_unblocks_consumer_without_completing_producer_and_keeps_integration_pending(fleet,tmp_path):
    store,repo=fleet[:2]
    _,_,other=interface_plan(fleet,tmp_path)
    with pytest.raises(Conflict,match='interface'):
        units.assign(store,'consumer',1,'eng-002')
    producer_run,proof=publish_interface(fleet)
    consumer_run=units.assign(store,'consumer',1,'eng-002')['run_id']
    assert store.db.execute('SELECT ended_at FROM runs WHERE run_id=?',(producer_run,)).fetchone()[0] is None
    snapshot=tmp_path/'contract-snapshot'
    exported=packets.acquire_contract(store,'consumer','producer','interface',snapshot)
    assert (snapshot/'api.py').read_text()=='def adapt(value): return value\n'
    assert exported['files']==proof['files']
    bind(store,consumer_run,recipes={'unit':definition("from pathlib import Path; assert Path('app.py').read_text() == 'consumer_value = 42\\n'")})
    (other/'app.py').write_text('consumer_value = 42\n')
    commit(other)
    result=units.complete(store,consumer_run,run_receipts(store,consumer_run))
    assert result['readiness']['packet_covered']
    assert result['readiness']['pending_integration']==['producer/interface']
    assert not result['readiness']['deployable']
    assert store.db.execute('SELECT count(*) FROM leases WHERE run_id=?',(consumer_run,)).fetchone()[0]==0
    assert store.db.execute('SELECT ended_at FROM runs WHERE run_id=?',(producer_run,)).fetchone()[0] is None
    with pytest.raises(Conflict,match='completion proof'):
        packets.verify_integration(store,'consumer',consumer_run,'producer','interface')
    (repo/'provider.py').write_text('provider_value = 42\n')
    commit(repo)
    units.complete(store,producer_run,run_receipts(store,producer_run,['unit','contract-check']))
    assert not planning.assignment(store,'consumer')['readiness']['deployable']


def test_contract_is_immutable_source_snapshot_and_revision_requires_version_bump(fleet,tmp_path):
    store,repo=fleet[:2]
    producer,consumer,other=interface_plan(fleet,tmp_path)
    run,proof=publish_interface(fleet)
    (repo/'api.py').write_text('def adapt(value): return None\n')
    commit(repo)
    with pytest.raises(Conflict,match='current|immutable'):
        packets.publish_contract(store,run,'interface',proof['checks'])
    snapshot=tmp_path/'old-interface'
    packets.acquire_contract(store,'consumer','producer','interface',snapshot)
    assert (snapshot/'api.py').read_text()=='def adapt(value): return value\n'
    units.stop(store,run)
    revised=packet_plan(fleet,[producer,consumer])
    revised['expected_revision']=1
    for task in revised['tasks']:
        task['expected_revision']=1
    producer['behavior']='Updated behavior requires a different interface.'
    from hx import code_index
    code_index.index_repo(store.root,repo)
    with pytest.raises(Conflict,match='new output versions'):
        planning.apply(store,revised)


def test_substitutable_needs_require_explicit_permission_and_exact_provider(fleet,tmp_path):
    store,repo=fleet[:2]
    producer,consumer,other=interface_plan(fleet,tmp_path)
    bad=packet_plan(fleet,[producer,consumer])
    consumer['prerequisites'][0]['stand_in']=False
    with pytest.raises(ValidationError,match='permission'):
        planning.validate(store,bad)
    consumer['prerequisites'][0]['stand_in']=True
    consumer['prerequisites'][0]['provider_version']=999
    with pytest.raises(ValidationError,match='real source'):
        planning.validate(store,bad)


@pytest.mark.parametrize('modes,blocked',[(['exclusive','shared'],True),(['shared','exclusive'],True),(['shared','shared'],False)])
def test_shared_resource_admission_is_atomic_and_stop_frees_capacity(fleet,tmp_path,modes,blocked):
    store,repo=fleet[:2]
    other=tmp_path/'other'
    git(repo,'worktree','add','-qb','resource-other',str(other))
    a=packet(repo,'a',resources=[{'id':'database','mode':modes[0]}])
    b=packet(other,'b',resources=[{'id':'database','mode':modes[1]}])
    planning.apply(store,packet_plan(fleet,[a,b]))
    first=units.assign(store,'a',1,'eng-001')['run_id']
    if blocked:
        with pytest.raises(Conflict,match='resource'):
            units.assign(store,'b',1,'eng-002')
        units.stop(store,first)
    assert units.assign(store,'b',1,'eng-002')['run_id']


def test_packet_commands_are_reachable_and_worker_ownership_is_enforced(fleet,tmp_path,run_hx):
    store,repo=fleet[:2]
    policy=store.root/'config/planning.json'
    policy.parent.mkdir(parents=True,exist_ok=True)
    policy.write_text(json.dumps({'schema_version':1,'reviewer':'qa-001','required_checks':['unit'],'role':'backend-engineer'}))
    request=tmp_path/'goal.json'
    request.write_text(json.dumps({'plan_id':'continuation','task_id':'implement','goal':'Preserve compiler behavior.','require':['compiler']}))
    generated=run_hx('plan','draft','--repo',str(repo),'--snapshot',fleet[6],'--file',str(request),'--root',str(store.root),env_extra={'HOME':str(tmp_path/'operator-home')})
    assert generated.returncode==0,generated.stderr
    draft=json.loads(generated.stdout)
    assert draft['ready'] and draft['binding_required']
    body=draft['plan']
    assert body['schema_version']==2 and 'checks' not in body['tasks'][0]
    path=tmp_path/'packet.json'
    path.write_text(json.dumps(body))
    operator_env={'HOME':str(tmp_path/'operator-home')}
    result=run_hx('plan','apply','--root',str(store.root),'--file',str(path),env_extra=operator_env)
    assert result.returncode==0,result.stderr
    run=units.assign(store,'implement',1,'eng-001')['run_id']
    bound=binding(planning.assignment(store,'implement'),recipes={'unit':definition("from runpy import run_path; assert run_path('compiler.py')['Compiler']().build() == 'context'")})
    source=tmp_path/'checks.json'
    source.write_text(json.dumps(bound))
    worker_env={'HARNESS_ID':'eng-001','HOME':str(tmp_path/'worker-home')}
    reviewer_env={'HARNESS_ID':'qa-001','HOME':str(tmp_path/'reviewer-home')}
    forbidden=run_hx('plan','bind',run,'--file',str(source),'--root',str(store.root),env_extra={**worker_env,'HARNESS_ID':'eng-002'})
    assert forbidden.returncode!=0 and 'own assignment' in forbidden.stderr
    proposed=run_hx('plan','bind',run,'--file',str(source),'--root',str(store.root),env_extra=worker_env)
    assert proposed.returncode==0,proposed.stderr
    viewed=run_hx('plan','binding',run,'--root',str(store.root),env_extra=reviewer_env)
    assert viewed.returncode==0,viewed.stderr
    review=json.loads(viewed.stdout)
    assert review['binding']==bound and review['goal']==body['tasks'][0]['behavior']
    approved=run_hx('plan','approve-binding',run,'--binding',review['binding_hash'],'--root',str(store.root),env_extra=reviewer_env)
    assert approved.returncode==0,approved.stderr
    checked=run_hx('check','unit','--run',run,'--root',str(store.root),env_extra=worker_env)
    assert checked.returncode==0,checked.stderr
    receipt_source=tmp_path/'receipts.json'
    receipt_source.write_text(json.dumps({'unit':json.loads(checked.stdout)['receipt_id']}))
    finished=run_hx('plan','finish',run,'--file',str(receipt_source),'--root',str(store.root),env_extra=worker_env)
    assert finished.returncode==0,finished.stderr
    assert json.loads(finished.stdout)['state']=='packet_covered'


def test_separate_integration_proves_real_provider_and_publishes_deployability(fleet,tmp_path):
    store,repo=fleet[:2]
    producer,consumer,other=interface_plan(fleet,tmp_path)
    integrated=tmp_path/'integrated'
    git(repo,'worktree','add','-qb','integration',str(integrated))
    integration=packet(integrated,'integration',paths=['provider.py','app.py'],prerequisites=[
        {'task_id':'producer','output_id':'proof','version':1,'need':'hard'},
        {'task_id':'consumer','output_id':'proof','version':1,'need':'hard'}])
    integration['outputs'].append({'id':'installed','version':1,'kind':'source','paths':['provider.py','app.py'],'obligation':'Installed consumer runs against the exact real provider.'})
    revised=packet_plan(fleet,[producer,consumer,integration])
    revised['expected_revision']=1
    producer['expected_revision']=consumer['expected_revision']=1
    planning.apply(store,revised)
    producer_run,_=publish_interface(fleet)
    consumer_run=units.assign(store,'consumer',1,'eng-002')['run_id']
    integration_recipe=definition("from runpy import run_path; assert run_path('provider.py')['provider_value'] == run_path('app.py')['consumer_value'] == 42")
    integration_recipe['id']='real-integration'
    consumer_binding=binding(planning.assignment(store,'consumer'),recipes={'unit':definition("from pathlib import Path; assert Path('app.py').read_text() == 'consumer_value = 42\\n'"),'real-integration':integration_recipe})
    consumer_binding['coverage']['integration.producer/interface']=['real-integration']
    proposal=packets.bind_checks(store,consumer_run,consumer_binding)
    packets.approve_binding(store,consumer_run,proposal['binding_hash'],'qa-001')
    (other/'app.py').write_text('consumer_value = 42\n')
    commit(other)
    units.complete(store,consumer_run,run_receipts(store,consumer_run))
    with pytest.raises(Conflict,match='completion proof|lease'):
        units.assign(store,'integration',1,'eng-003')
    (repo/'provider.py').write_text('provider_value = 42\n')
    commit(repo)
    units.complete(store,producer_run,run_receipts(store,producer_run,['unit','contract-check']))
    integration_run=units.assign(store,'integration',1,'eng-003')['run_id']
    integration_binding=binding(planning.assignment(store,'integration'),recipes={'unit':definition(),'real-integration':integration_recipe})
    integration_binding['coverage']['output.installed'].append('real-integration')
    proposal=packets.bind_checks(store,integration_run,integration_binding)
    packets.approve_binding(store,integration_run,proposal['binding_hash'],'qa-001')
    with pytest.raises(Conflict,match='completed separate'):
        packets.verify_integration(store,'consumer',integration_run,'producer','interface')
    (integrated/'provider.py').write_bytes((repo/'provider.py').read_bytes())
    (integrated/'app.py').write_bytes((other/'app.py').read_bytes())
    commit(integrated)
    integration_result=units.complete(store,integration_run,run_receipts(store,integration_run,['unit','real-integration']))
    assert not integration_result['readiness']['deployable']
    assert integration_result['readiness']['upstream_pending']==['consumer']
    verified=packets.verify_integration(store,'consumer',integration_run,'producer','interface')
    assert verified['state']=='integration_verified'
    assert verified['provider_hash'] and verified['contract_hash']
    readiness=planning.assignment(store,'consumer')['readiness']
    assert readiness['deployable'] and readiness['pending_integration']==[]
    assert packets.verify_integration(store,'consumer',integration_run,'producer','interface')==verified
    assert store.db.execute("SELECT count(*) FROM outbox WHERE kind IN ('contract_published','packet_covered','integration_verified','deployable')").fetchone()[0]>=5
    store.db.execute('UPDATE receipts SET valid=0 WHERE run_id=?',(integration_run,))
    stale=planning.assignment(store,'consumer')['readiness']
    assert not stale['deployable'] and stale['pending_integration']==['producer/interface']


def test_draft_uses_configured_qa_authority_without_partner_check_scripts(fleet):
    from hx import goal_templates
    store,repo=fleet[:2]
    configured=store.root/'config/qa-002/harness.json'
    configured.parent.mkdir(parents=True)
    configured.write_text(json.dumps({'id':'qa-002','role':'qa-engineer'}))
    request={'plan_id':'drafted','goal':'Preserve compiler behavior.','require':['compiler']}
    unnamed=goal_templates.draft(store,repo,fleet[6],request)
    assert not unnamed['ready'] and any('seat role' in item for item in unnamed['unknowns'])
    drafted=goal_templates.draft(store,repo,fleet[6],{**request,'role':'backend-engineer'})
    assert drafted['ready'],drafted['unknowns']
    task=drafted['plan']['tasks'][0]
    assert task['check_policy']=={'reviewer':'qa-002','required_checks':['unit'],'role':'backend-engineer'}
    assert 'checks' not in task and drafted['recipe_suggestions']=={}
    assert task['behavior']=='Preserve compiler behavior.'


def test_pending_real_provider_obligations_survive_revisions_and_resource_owner_restart(fleet,tmp_path):
    store,repo=fleet[:2]
    producer,consumer,other=interface_plan(fleet,tmp_path)
    producer_run,_=publish_interface(fleet)
    consumer_run=units.assign(store,'consumer',1,'eng-002')['run_id']
    bind(store,consumer_run)
    (other/'app.py').write_text('consumer_value = 42\n')
    commit(other)
    units.complete(store,consumer_run,run_receipts(store,consumer_run))
    with store.transaction() as tx:
        current=tx.task('consumer')
        changed=copy.deepcopy(current['payload'])
        changed['prerequisites']=[]
        for output in changed['outputs']:
            output['version']+=1
        with pytest.raises(Conflict,match='discard pending'):
            tx.put_task('consumer',changed,expected_revision=1)
    # The producer still owns its original source scopes after another connection opens.
    with ContinuityStore(store.root) as reopened:
        assert reopened.db.execute('SELECT count(*) FROM leases WHERE run_id=?',(producer_run,)).fetchone()[0]==2
        assert planning.assignment(reopened,'consumer')['readiness']['pending_integration']==['producer/interface']


def test_contract_publication_rechecks_declared_external_input_at_commit_boundary(fleet,tmp_path,monkeypatch):
    store,repo=fleet[:2]
    interface_plan(fleet,tmp_path)
    run=units.assign(store,'producer',1,'eng-001')['run_id']
    fixture=tmp_path/'external-contract-version'
    fixture.write_text('v1')
    recipe=definition("from pathlib import Path; assert Path('api.py').read_text() == 'def adapt(value): return value\\n'")
    recipe['id']='contract-check'
    recipe['inputs']=[str(fixture)]
    body=binding(planning.assignment(store,'producer'),recipes={'unit':definition(),'contract-check':recipe})
    body['coverage']['output.interface']=['contract-check']
    proposal=packets.bind_checks(store,run,body)
    packets.approve_binding(store,run,proposal['binding_hash'],'qa-001')
    (repo/'api.py').write_text('def adapt(value): return value\n')
    commit(repo)
    receipt=run_receipts(store,run,['contract-check'])
    original=checks.current
    def changed_after_validation(*args,**kwargs):
        result=original(*args,**kwargs)
        assert result['current']
        fixture.write_text('v2')
        return result
    monkeypatch.setattr(checks,'current',changed_after_validation)
    with pytest.raises(Conflict,match='inputs changed at publication'):
        packets.publish_contract(store,run,'interface',receipt)
    assert store.db.execute('SELECT count(*) FROM unit_contracts').fetchone()[0]==0


def test_source_queries_read_exact_early_contract_without_making_graph_authority(fleet,tmp_path,monkeypatch):
    from hx import code_metadata,code_index,repo_service
    store,repo=fleet[:2]
    interface_plan(fleet,tmp_path)
    run,proof=publish_interface(fleet)
    before=store.db.execute('SELECT revision FROM ledger_meta WHERE singleton=1').fetchone()[0]
    rows=code_metadata.published_contracts(store.root,name='interface',task='consumer')
    assert len(rows)==1 and rows[0]['authority']=='continuity-ledger'
    assert not rows[0]['stale'] and rows[0]['permission']['available']
    assert rows[0]['permission']['stand_in'] and rows[0]['commit']==proof['commit']
    assert store.db.execute('SELECT ended_at FROM runs WHERE run_id=?',(run,)).fetchone()[0] is None
    assert store.db.execute('SELECT revision FROM ledger_meta WHERE singleton=1').fetchone()[0]==before
    unrelated=tmp_path/'shared-source-service'
    unrelated.mkdir()
    def server(socket,body):
        status,result=code_index.execute(body['argv'],unrelated,read_ledger=False)
        return {'exit_code':status,'result':result}
    monkeypatch.setattr(repo_service,'request',server)
    joined=repo_service.code_request(store.root,['contract','interface','--task','consumer','--expect','fresh'],env={'HX_REPO_SERVICE_SOCKET':str(tmp_path/'source.sock')})
    assert joined['exit_code']==0 and joined['result'][0]['permission']['available']
    assert joined['result'][0]['authority']=='continuity-ledger'
    assert store.db.execute('SELECT revision FROM ledger_meta WHERE singleton=1').fetchone()[0]==before
    unbound=code_metadata.published_contracts(store.root,name='interface',task='producer')
    assert not unbound[0]['permission']['declared']
    artifact=next(iter(proof['artifacts'].values()))
    (store.artifacts/artifact).write_bytes(b'corrupt')
    stale=code_metadata.published_contracts(store.root,name='interface',task='consumer')
    assert stale[0]['stale'] and not stale[0]['permission']['available']
