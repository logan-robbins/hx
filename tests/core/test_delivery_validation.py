"""Fixed Git source, real checks, build bytes, attribution and inverse CAS."""
import json
import os
from pathlib import Path

import pytest

from hx import change_delivery as delivery, delivery_validation as validation, delivery_recovery as recovery
from hx import checks, planning, unit_execution
from hx.continuity_store import Conflict
from hx.errors import Refused
from .test_change_delivery import covered
from .test_checks import definition
from .test_unit_execution import fleet, source_unit, finish, commit
from .test_planning import plan, bind
from .test_map_updates import active
from .test_appmap import git, mapped


def configure(store, *, failing=False):
    script="from runpy import run_path; assert run_path('feature.py')['feature']() == 1"
    if failing:
        script="from pathlib import Path; from runpy import run_path; p=Path('feature.py'); assert not p.exists() or run_path(str(p))['feature']() == 0"
    integration=definition(script)
    integration['id']='integration'
    integration['source']={'complete':True,'inputs':['.'] if failing else ['feature.py']}
    build=definition("from pathlib import Path; from runpy import run_path; Path('dist').mkdir(); Path('dist/product.bin').write_text(str(run_path('feature.py')['feature']()))")
    build['id']='build'
    build['source']={'complete':True,'inputs':['feature.py']}
    deploy=definition("from pathlib import Path; assert Path('dist/product.bin').read_text()=='1'")
    deploy['id']='deploy'
    deploy['inputs']=['dist/product.bin']
    deploy['source']={'complete':True,'inputs':['feature.py']}
    policy={'post_merge_checks':['integration'],'pre_deploy_checks':['deploy'],'build_checks':['build'],
            'artifact_paths':['dist/product.bin'],'check_definitions':{'integration':integration,'build':build,'deploy':deploy}}
    (store.root/'config/delivery.json').write_text(json.dumps(policy))
    return policy


def landed(fleet,tmp_path,*,failing=False):
    store,repo,body,bare=covered(fleet,tmp_path)
    configure(store,failing=failing)
    delivery.submit(store,body)
    delivery.review(store,body['id'],decision='approve',expected_revision=1,reason='The assigned intent is preserved.')
    landing=delivery.land(store,body['id'],repository=bare,expected_head=body['base'])
    return store,repo,body,bare,landing


def next_change(fleet,repo,bare,expected,*,name='later',path='later.py'):
    store=fleet[0]
    task=source_unit(repo,name,path)
    body=plan(fleet,[task])
    body['plan_id']=name
    planning.apply(store,body)
    run=unit_execution.assign(store,name,1,'eng-002')['run_id']
    bind(store,run)
    base=git(repo,'rev-parse','HEAD')
    (repo/path).write_text('later_value = 7\n')
    commit(repo)
    proof=finish(store,run)
    change={'id':name,'run_id':run,'base':base,'head':proof['commit'],'behavior':task['behavior']}
    delivery.submit(store,change)
    delivery.review(store,name,decision='approve',expected_revision=1,reason='Independent scope is preserved.')
    return delivery.land(store,name,repository=bare,expected_head=expected)


def test_fixed_candidate_finishes_after_main_advances_and_rejects_stale_recipe_proof(fleet,tmp_path,monkeypatch):
    store,repo,body,bare,land=landed(fleet,tmp_path)
    frozen=validation.candidate(store,bare,land['head'])
    original=checks.execute
    advanced=[]
    def execute(*args,**kwargs):
        if not advanced:
            advanced.append(None)
            advanced[0]=next_change(fleet,repo,bare,land['head'])
        return original(*args,**kwargs)
    monkeypatch.setattr(checks,'execute',execute)
    result=validation.run_validation(store,frozen['batch'],tmp_path/'fixed')
    assert result['ready'] and result['head']==land['head']
    assert git(bare,'rev-parse','main')==advanced[0]['head'] and advanced[0]['head']!=result['head']
    assert not (tmp_path/'fixed/later.py').exists()
    policy=json.loads((store.root/'config/delivery.json').read_text())
    policy['check_definitions']['integration']['argv'][-1]='print("unrelated")'
    (store.root/'config/delivery.json').write_text(json.dumps(policy))
    assert not validation.readiness(store,frozen['batch'])['ready']


def test_candidates_coalesce_landed_units_and_archive_preparation_has_no_writer_lock(fleet,tmp_path,monkeypatch):
    store,repo,body,bare,first=landed(fleet,tmp_path)
    second=next_change(fleet,repo,bare,first['head'])
    original=validation._archive
    def archive(*args,**kwargs):
        assert not store.db.in_transaction
        return original(*args,**kwargs)
    monkeypatch.setattr(validation,'_archive',archive)
    frozen=validation.candidate(store,bare,second['head'])
    assert frozen['members']==[body['id'],'later']
    result=validation.run_validation(store,frozen['batch'],tmp_path/'coalesced')
    assert result['ready'] and result['head']==second['head']
    assert not (tmp_path/'coalesced/later.py').exists()
    assert result['source_coverage']=={'kind':'declared_closure','paths':['feature.py']}


def test_predeploy_manifest_names_exact_tested_build_and_corruption_retracts_readiness(fleet,tmp_path):
    store,repo,body,bare,land=landed(fleet,tmp_path)
    integration=validation.candidate(store,bare,land['head'])
    validation.run_validation(store,integration['batch'],tmp_path/'integration')
    deploy=validation.candidate(store,bare,land['head'],boundary='deploy')
    result=validation.run_validation(store,deploy['batch'],tmp_path/'build')
    assert result['deployable'] and result['build_id']
    manifest=json.loads((store.artifacts/result['build_manifest']).read_text())
    assert manifest['source_commit']==land['head'] and manifest['source_tree']==result['source_tree']
    artifact=manifest['artifacts']['dist/product.bin']
    assert (store.artifacts/artifact).read_text()=='1'
    delivery.review(store,body['id'],decision='reject',expected_revision=1,reason='A later independent intent audit withdrew approval.',env={'HARNESS_ID':'qa-001'})
    assert not validation.readiness(store,deploy['batch'])['deployable']
    # A separate corruption failure remains visible even after earlier successful checks.
    (store.artifacts/artifact).write_text('corrupt')
    assert not validation.readiness(store,deploy['batch'])['ready']


def test_real_failure_is_attributed_and_surgical_revert_preserves_later_changes(fleet,tmp_path):
    store,repo,body,bare,land=landed(fleet,tmp_path,failing=True)
    frozen=validation.candidate(store,bare,land['head'])
    failed=validation.run_validation(store,frozen['batch'],tmp_path/'failed')
    assert failed['status']=='failed' and not failed['ready']
    packet=json.loads(store.db.execute('SELECT payload FROM delivery_repairs').fetchone()[0])
    assert packet['attribution']=='confirmed' and packet['binding_required']
    assert packet['failed_receipt'] and packet['baseline_receipt'] and packet['scope_paths']==['feature.py']
    later=next_change(fleet,repo,bare,land['head'])
    reverted=recovery.revert(store,body['id'],frozen['batch'],bare,later['head'])
    assert reverted['reverted']
    assert git(bare,'show',reverted['head']+':later.py')=='later_value = 7'
    with pytest.raises(Exception):
        git(bare,'show',reverted['head']+':feature.py')
    assert recovery.revert(store,body['id'],frozen['batch'],bare,later['head'])['head']==reverted['head']
    repaired=validation.candidate(store,bare,reverted['head'])
    assert repaired['members']==['later'] and repaired['reversions'][0]['change']==body['id']
    verified=validation.run_validation(store,repaired['batch'],tmp_path/'reverted-source')
    assert verified['ready'] and (tmp_path/'reverted-source/later.py').exists()
    assert not (tmp_path/'reverted-source/feature.py').exists()


def test_revert_publication_crash_is_reconciled_without_reapplying(fleet,tmp_path,monkeypatch):
    store,repo,body,bare,land=landed(fleet,tmp_path,failing=True)
    frozen=validation.candidate(store,bare,land['head'])
    validation.run_validation(store,frozen['batch'],tmp_path/'failed')
    original=delivery._event
    def crash(tx,kind,identity,payload):
        if kind=='change_reverted':
            raise OSError('crash after revert ref CAS')
        return original(tx,kind,identity,payload)
    with monkeypatch.context() as scoped:
        scoped.setattr(delivery,'_event',crash)
        with pytest.raises(OSError):
            recovery.revert(store,body['id'],frozen['batch'],bare,land['head'])
    journal=store.db.execute("SELECT revert_id FROM delivery_reverts WHERE status='prepared'").fetchone()[0]
    head=git(bare,'rev-parse','main')
    assert delivery.reconcile(store,journal)['reverted']
    assert delivery.reconcile(store,journal)['reverted']
    assert git(bare,'rev-parse','main')==head


def test_cli_candidate_and_fixed_validation_are_reachable_with_named_authority(fleet,tmp_path,run_hx):
    store,repo,body,bare,land=landed(fleet,tmp_path)
    denied=run_hx('change','candidate','--repo',str(bare),'--expected-head',land['head'],'--root',str(store.root),env_extra={'HARNESS_ID':'eng-001'})
    assert denied.returncode!=0
    frozen=run_hx('change','candidate','--repo',str(bare),'--expected-head',land['head'],'--root',str(store.root),env_extra={'HARNESS_ID':'qa-001'})
    assert frozen.returncode==0,frozen.stderr
    batch=json.loads(frozen.stdout)['batch']
    checked=run_hx('change','run-validation',batch,'--snapshot',str(tmp_path/'cli-source'),'--root',str(store.root),env_extra={'HARNESS_ID':'qa-001'})
    assert checked.returncode==0,checked.stderr
    assert json.loads(checked.stdout)['ready']


def test_final_publication_failure_retains_run_until_explicit_safe_reconciliation(fleet,tmp_path,monkeypatch,run_hx):
    store,repo,body,bare,land=landed(fleet,tmp_path)
    policy=json.loads((store.root/'config/delivery.json').read_text())
    policy['validation_resources']=[{'id':'integration-service','mode':'exclusive'}]
    (store.root/'config/delivery.json').write_text(json.dumps(policy))
    frozen=validation.candidate(store,bare,land['head'])
    original=delivery._event
    def crash(tx,kind,identity,payload):
        if kind=='candidate_verified':
            raise OSError('publication interrupted after successful actual checks')
        return original(tx,kind,identity,payload)
    with monkeypatch.context() as scoped:
        scoped.setattr(delivery,'_event',crash)
        with pytest.raises(OSError):
            validation.run_validation(store,frozen['batch'],tmp_path/'interrupted')
    row=validation._row(store,frozen['batch'])
    run=row['payload']['run_id']
    assert row['status']=='interrupted' and row['payload']['proof'] is None
    assert store.db.execute('SELECT ended_at FROM runs WHERE run_id=?',(run,)).fetchone()[0] is None
    assert store.db.execute('SELECT 1 FROM unit_resources WHERE run_id=?',(run,)).fetchone()
    assert store.db.execute('SELECT valid FROM receipts WHERE run_id=?',(run,)).fetchone()[0]
    command=run_hx('change','reconcile',frozen['batch'],'--root',str(store.root),env_extra={'HARNESS_ID':'qa-001'})
    assert command.returncode==0,command.stderr
    settled=json.loads(command.stdout)
    assert settled['reconciled'] and settled['unresolved'] and not settled['ready']
    assert store.db.execute('SELECT ended_at FROM runs WHERE run_id=?',(run,)).fetchone()[0] is not None
    assert not store.db.execute('SELECT 1 FROM unit_resources WHERE run_id=?',(run,)).fetchone()
    with pytest.raises(Conflict,match='existing attempt'):
        validation.run_validation(store,frozen['batch'],tmp_path/'no-silent-retry')
    command=run_hx('change','candidate','--repo',str(bare),'--expected-head',land['head'],'--retry',frozen['batch'],'--root',str(store.root),env_extra={'HARNESS_ID':'qa-001'})
    assert command.returncode==0,command.stderr
    retry=json.loads(command.stdout)
    assert retry['batch']!=frozen['batch']
    assert validation.run_validation(store,retry['batch'],tmp_path/'explicit-retry')['ready']
    assert not validation.readiness(store,frozen['batch'])['ready']


def test_reconcile_refuses_pending_live_or_unknown_check_ownership(fleet,tmp_path,monkeypatch):
    from hx import check_artifacts
    from hx.continuity_store import canonical,digest
    store,repo,body,bare,land=landed(fleet,tmp_path)
    frozen=validation.candidate(store,bare,land['head'])
    def interrupt(store,run,name,**kwargs):
        with store.transaction() as tx:
            check_artifacts.reserve(tx,'pending-owner',digest({'pending':run}))
            tx.db.execute("INSERT INTO check_executions VALUES(?,?,?,?,?,'running',?)",('pending-owner',run,'pending',name,digest(validation.policy(store,'integration')['recipes'][name]),canonical({})))
        raise OSError('controller released with unknown spawn outcome')
    monkeypatch.setattr(checks,'run_check',interrupt)
    with pytest.raises(OSError):
        validation.run_validation(store,frozen['batch'],tmp_path/'pending')
    run=validation._row(store,frozen['batch'])['payload']['run_id']
    with pytest.raises(Conflict,match='owner is still alive'):
        delivery.reconcile(store,frozen['batch'])
    assert store.db.execute('SELECT ended_at FROM runs WHERE run_id=?',(run,)).fetchone()[0] is None


def test_pending_frontier_advances_beyond_129_historical_landings_and_checks_baseline_review(fleet,tmp_path):
    from hx.continuity_store import canonical
    store,repo,body,bare,land=landed(fleet,tmp_path)
    frozen=validation.candidate(store,bare,land['head'])
    validation.run_validation(store,frozen['batch'],tmp_path/'baseline')
    journal=store.db.execute('SELECT * FROM delivery_batches WHERE batch_id=?',(land['batch'],)).fetchone()
    # Already validated journal history has no pending work; its proof is the checkpoint.
    with store.transaction() as tx:
        for number in range(130):
            tx.db.execute("INSERT INTO delivery_batches VALUES(?,?,?,'verified',?)",('history.'+str(number),journal['repository'],journal['head'],journal['payload']))
    later=next_change(fleet,repo,bare,land['head'])
    candidate=validation.candidate(store,bare,later['head'])
    assert candidate['members']==['later'] and candidate['checkpoint']['batch']==frozen['batch']
    result=validation.run_validation(store,candidate['batch'],tmp_path/'advanced')
    assert result['ready'] and result['head']==later['head']
    delivery.review(store,body['id'],decision='reject',expected_revision=1,reason='Baseline contract approval withdrawn.',env={'HARNESS_ID':'qa-001'})
    assert not validation.readiness(store,candidate['batch'])['ready']


def test_sparse_validation_does_not_archive_large_unrelated_tree(fleet,tmp_path):
    import subprocess
    store,repo,body,bare,land=landed(fleet,tmp_path)
    # Real unrelated source entries exceed the whole-tree archive file bound.
    prefix=tmp_path/'unrelated'
    prefix.mkdir()
    for number in range(8200):
        (prefix/str(number)).write_bytes(b'x')
    import shutil
    shutil.copytree(prefix,repo/'unrelated')
    commit(repo)
    head=git(repo,'rev-parse','HEAD')
    git(bare,'fetch',str(repo),head)
    # Exercise the actual fixed archive API with the same harness-owned readset.
    archive,files=validation._archive(store,bare,head,validation.source_readset(validation.policy(store,'integration')))
    try:
        assert [file['path'] for file in files]==['feature.py']
        assert archive.stat().st_size<1024*1024
    finally:
        archive.unlink()


def test_real_completed_integration_worker_must_execute_exact_candidate_policy(fleet,tmp_path):
    store,repo,body,bare,land=landed(fleet,tmp_path)
    frozen=validation.candidate(store,bare,land['head'])
    git(repo,'fetch',str(bare),land['head'])
    git(repo,'reset','--hard',land['head'])
    task=source_unit(repo,'integration-worker')
    recipe=validation.policy(store,'integration')['recipes']['integration']
    task['check_policy']={'reviewer':'rel-001','required_checks':['integration']}
    proposal=plan(fleet,[task])
    proposal['plan_id']='integration-worker'
    planning.apply(store,proposal)
    run=unit_execution.assign(store,'integration-worker',1,'qa-001')['run_id']
    bind(store,run,recipes={'integration':recipe})
    execution_env={**os.environ,'HARNESS_ID':'qa-001'}
    receipt=checks.run_check(store,run,'integration',allow_reuse=False,env=execution_env)
    unit_execution.complete(store,run,{'integration':receipt['receipt_id']},env=execution_env)
    result=validation.attest_unit(store,frozen['batch'],run,env=execution_env)
    assert result['ready'] and result['head']==land['head']
    assert result['proof']['receipts']=={'integration':receipt['receipt_id']}
    store.db.execute('UPDATE receipts SET valid=0 WHERE receipt_id=?',(receipt['receipt_id'],))
    assert not validation.readiness(store,frozen['batch'])['ready']


def test_external_provider_version_change_retracts_fixed_readiness(fleet,tmp_path):
    store,repo,body,bare,land=landed(fleet,tmp_path)
    version=tmp_path/'provider.version'
    version.write_text('provider-v1')
    policy=json.loads((store.root/'config/delivery.json').read_text())
    policy['check_definitions']['integration']['environment']['external_versions']={'provider':str(version)}
    (store.root/'config/delivery.json').write_text(json.dumps(policy))
    frozen=validation.candidate(store,bare,land['head'])
    assert validation.run_validation(store,frozen['batch'],tmp_path/'provider-source')['ready']
    version.write_text('provider-v2')
    stale=validation.readiness(store,frozen['batch'])
    assert not stale['ready'] and any('external validation environment' in reason for reason in stale['reasons'])


def test_dead_controller_reconciliation_waits_for_actual_child_group_quiescence(fleet,tmp_path):
    import signal
    import subprocess
    import sys
    import time
    store,repo,body,bare,land=landed(fleet,tmp_path)
    policy=json.loads((store.root/'config/delivery.json').read_text())
    policy['validation_resources']=[{'id':'crash-service','mode':'exclusive'}]
    (store.root/'config/delivery.json').write_text(json.dumps(policy))
    frozen=validation.candidate(store,bare,land['head'])
    program="""
import os,subprocess,sys
from pathlib import Path
from hx import checks,delivery_validation
from hx.continuity_store import ContinuityStore
def crash(argv,cwd,env,output,timeout,maximum,mask,on_spawn,report):
    with output.open('wb') as stream:
        child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],cwd=cwd,env=env,stdout=stream,stderr=subprocess.STDOUT,start_new_session=True)
    on_spawn(child)
    os._exit(23)
checks.execute=crash
with ContinuityStore(Path(sys.argv[1])) as store:
    delivery_validation.run_validation(store,sys.argv[2],sys.argv[3])
"""
    controller=subprocess.run([sys.executable,'-c',program,str(store.root),frozen['batch'],str(tmp_path/'crashed-source')],capture_output=True,text=True,timeout=20)
    assert controller.returncode==23,controller.stderr
    row=validation._row(store,frozen['batch'])
    run=row['payload']['run_id']
    claim=store.db.execute("SELECT a.* FROM check_artifacts a JOIN check_executions e ON e.execution_id=a.execution_id WHERE e.run_id=?",(run,)).fetchone()
    ownership=json.loads(claim['payload'])
    group=ownership['child']['group']
    try:
        with pytest.raises(Conflict,match='child is still alive|group is still present'):
            delivery.reconcile(store,frozen['batch'])
        assert store.db.execute('SELECT 1 FROM unit_resources WHERE run_id=?',(run,)).fetchone()
    finally:
        os.killpg(group,signal.SIGTERM)
    until=time.monotonic()+5
    while time.monotonic()<until:
        try:
            os.killpg(group,0)
        except ProcessLookupError:
            break
        time.sleep(.05)
    settled=delivery.reconcile(store,frozen['batch'])
    assert settled['reconciled'] and settled['status']=='interrupted' and not settled['ready']
    assert not store.db.execute('SELECT 1 FROM unit_resources WHERE run_id=?',(run,)).fetchone()
    assert store.db.execute('SELECT status FROM check_artifacts WHERE execution_id=?',(claim['execution_id'],)).fetchone()[0]=='finished'
