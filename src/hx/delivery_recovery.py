"""Failure receipts, controlled regression replay, and surgical CAS rollback."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tarfile
from pathlib import Path

from . import checks, dependency_packets, delivery_validation as validation
from .continuity_store import Conflict, canonical, digest
from .errors import ValidationError


def _failure_receipt(store,receipt_id):
    record=store.db.execute('SELECT * FROM receipts WHERE receipt_id=?',(receipt_id,)).fetchone()
    if record is None or record['valid'] or not checks._artifact_intact(store,record['artifact_hash']):
        raise Conflict('failure requires an intact actual failing execution receipt')
    payload=json.loads(record['payload'])
    provenance=payload.get('provenance',{})
    execution=store.db.execute('SELECT * FROM check_artifacts WHERE execution_id=?',(provenance.get('execution_id'),)).fetchone()
    if execution is None or execution['status']!='finished' or execution['payload_hash']!=provenance.get('payload_hash') or digest(json.loads(execution['payload']))!=execution['payload_hash'] or execution['artifact_hash']!=record['artifact_hash']:
        raise Conflict('failed execution provenance is missing or changed')
    from .check_artifacts import identity
    if execution['identity_hash']!=provenance.get('identity_hash') or execution['identity_hash']!=identity(record['check_version'],payload['source_before'],payload['environment_before']) or digest(payload['recipe'])!=record['check_version'] or payload['source_before']['hash']!=record['inputs_hash'] or payload['environment_before']['hash']!=record['environment_hash'] or payload['execution']['exit_code']!=record['exit_code']:
        raise Conflict('failed execution identity differs from its receipt')
    frozen=json.loads(execution['payload'])
    if any(frozen.get(key)!=payload.get(key) for key in ('recipe','source_before','source_after','environment_before','environment_after','execution')):
        raise Conflict('failed execution receipt changed its immutable evidence')
    if record['exit_code']==0 and not payload['execution'].get('reason'):
        raise Conflict('receipt does not prove an actual check failure')
    return dict(record),payload


def _restore(store,row,archive,files):
    directory=Path(row['payload']['workdir'])
    token=directory/'.hx-validation-owner'
    if directory.resolve()!=directory or token.is_symlink() or not token.is_file() or token.read_text()!=row['batch_id']:
        raise Conflict('controlled replay lost its owned snapshot directory')
    shutil.rmtree(directory)
    directory.mkdir()
    with tarfile.open(store.artifacts/archive,'r') as source:
        source.extractall(directory,filter='data')
    token.write_text(row['batch_id'])
    if not validation._source_intact(directory,files):
        raise Conflict('controlled replay source differs from its pinned archive')
    return directory


def _baseline(store,row,receipt,*,env=None):
    """Replay exactly the failing recipe in the same cwd/environment at the base."""
    record,payload=_failure_receipt(store,receipt)
    if not row['payload']['members']:
        return None
    if not payload['source_before']['complete'] or not payload['environment_before']['complete'] or record['start_hash']!=record['end_hash']:
        return None
    base=row['payload']['members'][0]['base']
    staged,files=validation._archive(store,row['payload']['repo'],base,validation.source_readset(row['payload']['policy']))
    try:
        with store.transaction() as tx:
            archive=tx.put_artifact_file(staged,owner_type='regression_baseline',owner_id=row['batch_id'],slot='source',max_bytes=256*1024*1024)
    finally:
        staged.unlink(missing_ok=True)
    directory=Path(row['payload']['workdir'])
    name=record['check_id']
    worker='baseline.'+row['batch_id']
    task_id=worker
    with store.transaction() as tx:
        if tx.db.execute('SELECT 1 FROM runs r JOIN tasks t ON t.task_id=r.task_id AND t.revision=r.task_revision WHERE r.ended_at IS NULL AND json_extract(t.payload,\'$.workdir\')=?',(str(directory),)).fetchone():
            raise Conflict('controlled replay source still has an active owner')
        revision=tx.put_task(task_id,{'goal':'Reproduce the baseline for fixed regression '+row['batch_id'],
            'workdir':str(directory),'checks':{name:payload['recipe']},'repository':row['repository'],
            'resources':row['payload']['policy']['resources'],'source_commit':base,'source_archive':archive},expected_revision=0)
        task=tx.task(task_id)
        dependency_packets.guard_resources(tx,task)
        run=tx.start_run(task_id,revision,worker)
        dependency_packets.acquire_resources(tx,task,run)
        current=validation._row(store,row['batch_id'])['payload']
        current['baseline_run_id']=run
        tx._change()
        tx.db.execute('UPDATE delivery_validations SET payload=? WHERE batch_id=?',(canonical(current),row['batch_id']))
    try:
        _restore(store,row,archive,files)
        result=checks.run_check(store,run,name,worker_id=worker,env=env,allow_reuse=False)
        with store.transaction() as tx:
            if not validation._source_intact(directory,files):
                tx.db.execute('UPDATE receipts SET valid=0 WHERE receipt_id=?',(result['receipt_id'],))
                result['valid']=False
            tx.finish_run(run,'completed' if result['valid'] else 'failed')
        return result['receipt_id']
    finally:
        settled=store.db.execute('SELECT ended_at FROM runs WHERE run_id=?',(run,)).fetchone()[0] is not None
        if settled:
            _restore(store,row,row['payload']['source_archive'],row['payload']['files'])


def fail(store,batch_id,receipt_id,*,baseline_receipt=None,env=None):
    from .change_delivery import _change,_event
    validation.authority(store,env)
    row=validation._row(store,batch_id)
    record,payload=_failure_receipt(store,receipt_id)
    proof=row['payload'].get('proof')
    if not proof or proof.get('failure')!=receipt_id or record['run_id']!=proof['run_id'] or record['check_version']!=digest(row['payload']['policy']['recipes'].get(record['check_id'])):
        raise Conflict('failure receipt does not belong to the exact candidate policy')
    existing=store.db.execute('SELECT payload FROM delivery_repairs WHERE batch_id=?',(batch_id,)).fetchone()
    if existing:
        prior=json.loads(existing[0])
        if prior['failed_receipt']!=receipt_id or baseline_receipt not in {None,prior['baseline_receipt']}:
            raise Conflict('failure retry changed its immutable attribution proof')
        return prior
    if baseline_receipt is None:
        baseline_receipt=_baseline(store,row,receipt_id,env=env)
    baseline=store.db.execute('SELECT * FROM receipts WHERE receipt_id=?',(baseline_receipt,)).fetchone() if baseline_receipt else None
    regression=bool(baseline and checks.receipt_intact(store,baseline) and baseline['check_version']==record['check_version'] and baseline['environment_hash']==record['environment_hash'])
    if regression:
        baseline_task=store.db.execute('SELECT t.payload FROM runs r JOIN tasks t ON t.task_id=r.task_id AND t.revision=r.task_revision WHERE r.run_id=?',(baseline['run_id'],)).fetchone()
        if not baseline_task or json.loads(baseline_task[0]).get('source_commit')!=row['payload']['members'][0]['base']:
            raise Conflict('regression baseline is not the exact pre-change source')
    members=row['payload']['members']
    attribution='confirmed' if regression and len(members)==1 else 'bounded_regression' if regression else 'unconfirmed'
    with store.transaction() as tx:
        paths=sorted({path for member in members for path in _change(tx,member['change'])['payload']['paths']}) or [file['path'] for file in row['payload']['files']]
        repair_id='repair.'+batch_id
        packet={'repair_id':repair_id,'batch':batch_id,
                'goal':f"Restore the behavior rejected by {record['check_id']} at the pinned source snapshot; preserve unrelated later changes.",
                'failed_head':row['head'],'source_tree':row['payload']['tree'],'failed_receipt':receipt_id,
                'failure_artifact':record['artifact_hash'],'check_version':record['check_version'],
                'baseline_receipt':baseline_receipt,'attribution':attribution,
                'changes':[member['change'] for member in members],'scope_paths':paths,
                'recipe_suggestions':{record['check_id']:payload['recipe']},'binding_required':True,
                'next_action':'Assign a scoped repair packet; its worker binds checks and Definition of done before edits.'}
        tx._change()
        tx.db.execute('INSERT INTO delivery_repairs VALUES(?,?,?)',(repair_id,batch_id,canonical(packet)))
        _event(tx,'repair_packet_created',repair_id,{'batch':batch_id,'repair_id':repair_id,'attribution':attribution,'changes':packet['changes']})
    return packet


def revert(store,change_id,batch_id,repository,expected_head,*,branch='main',env=None):
    from .change_delivery import _git,_commit,_event
    actor=validation.authority(store,env,release=True)
    repair=store.db.execute('SELECT payload FROM delivery_repairs WHERE batch_id=?',(batch_id,)).fetchone()
    if repair is None:
        raise Conflict('surgical revert requires an attributed failed validation snapshot')
    repair=json.loads(repair[0])
    _failure_receipt(store,repair['failed_receipt'])
    baseline=store.db.execute('SELECT * FROM receipts WHERE receipt_id=?',(repair['baseline_receipt'],)).fetchone()
    if repair['attribution']!='confirmed' or repair['changes']!=[change_id] or not checks.receipt_intact(store,baseline):
        raise Conflict('surgical revert requires a confirmed individual regression')
    previous=store.db.execute("SELECT revert_id FROM delivery_reverts WHERE json_extract(payload,'$.change')=? AND json_extract(payload,'$.batch')=? ORDER BY rowid DESC LIMIT 1",(change_id,batch_id)).fetchone()
    if previous and reconcile(store,previous[0],env=env)['reverted']:
        return reconcile(store,previous[0],env=env)
    row=validation._row(store,batch_id)
    member=row['payload']['members'][0]
    repo=Path(repository).resolve()
    if repo!=Path(row['payload']['repo']).resolve() or _git(repo,'rev-parse','--is-bare-repository').strip()!=b'true':
        raise ValidationError('surgical revert requires the original bare integration repository')
    _git(repo,'check-ref-format','refs/heads/'+branch)
    expected=_commit(repo,expected_head)
    current=_commit(repo,'refs/heads/'+branch)
    if expected!=current:
        raise Conflict('integration head advanced before surgical revert')
    tree=_git(repo,'merge-tree','--write-tree','--merge-base='+member['landed_head'],current,member['base']).decode().splitlines()[0]
    head=_git(repo,'-c','user.name=HX delivery','-c','user.email=hx@localhost','commit-tree',tree,'-p',current,'-m','Revert confirmed regression '+change_id).decode().strip()
    identity=digest({'change':change_id,'batch':batch_id,'base':current,'head':head})
    body={'change':change_id,'revision':member['revision'],'batch':batch_id,'repair_id':repair['repair_id'],
          'repo':str(repo),'branch':branch,'base':current,'head':head,'actor':actor}
    with store.transaction() as tx:
        tx._change()
        tx.db.execute("INSERT OR IGNORE INTO delivery_reverts VALUES(?,'prepared',?)",(identity,canonical(body)))
        _event(tx,'revert_prepared',identity,body)
    with store.transaction() as tx:
        _failure_receipt(store,repair['failed_receipt'])
        if not checks.receipt_intact(store,baseline):
            raise Conflict('regression baseline evidence changed before revert publication')
        _git(repo,'update-ref','refs/heads/'+branch,head,current)
        tx.db.execute("UPDATE delivery_reverts SET status='reverted' WHERE revert_id=?",(identity,))
        tx.db.execute("UPDATE delivery_changes SET status='reverted' WHERE change_id=? AND revision=?",(change_id,member['revision']))
        tx.db.execute("UPDATE delivery_validations SET status='reverted' WHERE batch_id=?",(batch_id,))
        _event(tx,'change_reverted',identity,body)
    return {'revert':identity,'change':change_id,'head':head,'reverted':True}


def reconcile(store,revert_id,*,env=None):
    from .change_delivery import _commit,_event
    validation.authority(store,env)
    row=store.db.execute('SELECT * FROM delivery_reverts WHERE revert_id=?',(revert_id,)).fetchone()
    if row is None:
        raise ValidationError('unknown surgical revert journal')
    body=json.loads(row['payload'])
    current=_commit(body['repo'],'refs/heads/'+body['branch'])
    observed=subprocess.run(['git','-C',body['repo'],'merge-base','--is-ancestor',body['head'],current],capture_output=True).returncode==0
    if observed:
        with store.transaction() as tx:
            tx._change()
            tx.db.execute("UPDATE delivery_reverts SET status='reverted' WHERE revert_id=?",(revert_id,))
            tx.db.execute("UPDATE delivery_changes SET status='reverted' WHERE change_id=? AND revision=?",(body['change'],body['revision']))
            _event(tx,'change_reverted',revert_id,body)
    return {'revert':revert_id,'change':body['change'],'head':body['head'],'reverted':observed,'current_head':current}
