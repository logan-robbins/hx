"""Coalesced, fixed source validation and immutable tested build manifests."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from contextlib import nullcontext
from pathlib import Path, PurePosixPath

from . import checks, dependency_packets, fingerprints
from .continuity_store import Conflict, canonical, digest
from .errors import Refused, ValidationError

SCHEMA = """
CREATE TABLE IF NOT EXISTS delivery_validations (
 batch_id TEXT PRIMARY KEY,repository TEXT NOT NULL,head TEXT NOT NULL,
 boundary TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS delivery_repairs (
 repair_id TEXT PRIMARY KEY,batch_id TEXT NOT NULL,payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS delivery_reverts (
 revert_id TEXT PRIMARY KEY,status TEXT NOT NULL,payload TEXT NOT NULL);
"""


def initialize(db):
    for statement in SCHEMA.split(';'):
        if statement.strip():
            db.execute(statement)


def authority(store, env=None, *, release=False):
    from .change_delivery import _role
    from .caller import caller
    actor = caller(env) or 'operator'
    roles = {'release-engineer','operator'} if release else {'qa-engineer','release-engineer','operator'}
    if _role(store.root,actor) not in roles:
        raise Refused('fixed validation belongs to configured QA or release authority')
    return actor


def policy(store, boundary):
    from .change_delivery import _policy
    body = _policy(store.root)
    names = body.get('post_merge_checks' if boundary=='integration' else 'pre_deploy_checks',[])
    builds = body.get('build_checks',[]) if boundary=='deploy' else []
    definitions = body.get('check_definitions',{})
    if not names or len(set(names+builds)) != len(names+builds) or len(names+builds)>16:
        raise ValidationError('boundary policy requires 1–16 unique exact checks')
    recipes = {}
    for name in builds+names:
        recipe = definitions.get(name)
        checks.validate(recipe)
        if recipe['id'] != name or recipe.get('source',{}).get('complete') is not True:
            raise ValidationError('fixed source checks need matching IDs and complete declared source closures')
        recipes[name] = recipe
    artifacts = body.get('artifact_paths',[]) if boundary=='deploy' else []
    if boundary=='deploy' and (not builds or not artifacts):
        raise ValidationError('pre-deploy policy requires build checks and explicit artifact paths')
    for path in artifacts:
        relative = PurePosixPath(path)
        if relative.is_absolute() or '..' in relative.parts or str(relative)!=path or not path or '.git' in relative.parts:
            raise ValidationError('artifact paths must be normalized inside the fixed source snapshot')
        for name in names:
            recipe = recipes[name]
            inputs = recipe['inputs'] + recipe['source']['inputs'] + recipe['source'].get('dependencies',[])
            cwd = PurePosixPath(recipe['cwd'])
            if not any(path==str(cwd/input) or path.startswith(str(cwd/input).rstrip('/')+'/') for input in inputs):
                raise ValidationError('every pre-deploy check must fingerprint every tested artifact')
    return {'hash':digest(body),'recipes':recipes,'checks':names,'build_checks':builds,
            'artifacts':artifacts,'resources':body.get('validation_resources',[])}


def _row(store,batch_id):
    row = store.db.execute('SELECT * FROM delivery_validations WHERE batch_id=?',(batch_id,)).fetchone()
    if row is None:
        raise ValidationError('unknown fixed validation snapshot')
    return {**dict(row),'payload':json.loads(row['payload'])}


def source_readset(contract):
    """Relative declared source closures; an explicit whole-root input stays whole."""
    paths=set()
    for recipe in contract['recipes'].values():
        executable_inputs=[value for value in [recipe['argv'][0],*recipe['environment']['executables']] if '/' in value]
        declared=recipe['inputs']+recipe['source']['inputs']+recipe['source'].get('dependencies',[])+recipe['environment']['inputs']+[value for value in recipe['environment']['external_versions'].values() if value is not None]+executable_inputs
        for value in declared:
            if PurePosixPath(value).is_absolute():
                continue  # External inputs are fingerprinted; never copied into the snapshot.
            relative=os.path.normpath(str(PurePosixPath(recipe['cwd'])/value))
            if relative=='.':
                return None
            if relative=='..' or relative.startswith('../'):
                raise ValidationError('fixed validation source closure escapes its snapshot')
            paths.add(relative)
    return sorted(paths)


def _external_environment(contract):
    """Recheck declared absolute version/tool inputs without persisting env secrets."""
    identity=fingerprints.Fingerprint()
    paths=set()
    for recipe in contract['recipes'].values():
        declared=[recipe['argv'][0],*recipe['environment']['executables'],*recipe['environment']['inputs'],*recipe['environment']['external_versions'].values()]
        paths.update(value for value in declared if value is not None and Path(value).is_absolute())
    for value in sorted(paths):
        identity.path(Path(value),'validation-environment:'+value)
    return identity.result()


def _archive(store,repo,head,readset=None):
    directory=store.root/'state/validation-archives'
    directory.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(dir=directory)
    try:
        selected=None
        if readset is not None:
            # Expand only declared Git prefixes; absent baseline inputs remain absent.
            result=subprocess.run(['git','--no-optional-locks','-C',str(repo),'ls-tree','-rz','--name-only',head,'--',*readset],capture_output=True,timeout=60) if readset else None
            if result and result.returncode:
                raise Conflict('fixed source closure could not be resolved')
            selected=[os.fsdecode(path) for path in result.stdout.split(b'\0') if path] if result else []
            if len(selected)>8192:
                raise ValidationError('declared source closure exceeds 8192 files; split the validation boundary')
        with os.fdopen(fd,'wb') as output:
            if selected==[]:
                with tarfile.open(fileobj=output,mode='w'):
                    pass
                result=None
            else:
                result=subprocess.run(['git','--no-optional-locks','-C',str(repo),'archive','--format=tar',head,*(['--',*selected] if selected is not None else [])],stdout=output,stderr=subprocess.PIPE,timeout=60)
        if result and result.returncode:
            raise Conflict('fixed source archive could not be acquired')
        if Path(name).stat().st_size>256*1024*1024:
            raise ValidationError('fixed source archive exceeds 256 MiB')
        files=[]
        with tarfile.open(name,'r') as archive:
            for member in archive:
                path=PurePosixPath(member.name)
                if path.is_absolute() or '..' in path.parts or '.git' in path.parts:
                    raise ValidationError('unsafe fixed source path')
                if member.isdir():
                    continue
                if member.issym():
                    # Keep interface/source identity inside the snapshot.
                    target=path.parent/PurePosixPath(member.linkname)
                    if PurePosixPath(member.linkname).is_absolute() or '..' in target.parts:
                        raise ValidationError('fixed snapshot contains an external source link')
                    files.append({'path':member.name,'link':member.linkname,'mode':member.mode})
                elif member.isfile():
                    stream=archive.extractfile(member)
                    hasher=hashlib.sha256()
                    while chunk:=stream.read(65536):
                        hasher.update(chunk)
                    files.append({'path':member.name,'hash':hasher.hexdigest(),'mode':member.mode})
                else:
                    raise ValidationError('fixed snapshot requires regular files or contained symlinks')
                if len(files)>8192:
                    raise ValidationError('fixed validation exceeds 8192 source files; split the validation boundary')
        return Path(name),files
    except BaseException:
        Path(name).unlink(missing_ok=True)
        raise


def candidate(store,repository,expected_head,*,branch='main',boundary='integration',env=None,_pinned=False,retry=None):
    from .change_delivery import _git,_commit,_change,_approved,_completion,_event
    authority(store,env)
    if boundary not in {'integration','deploy'}:
        raise ValidationError('unknown validation boundary')
    repo=Path(repository).resolve()
    if _git(repo,'rev-parse','--is-bare-repository').strip()!=b'true':
        raise ValidationError('fixed validation requires the bare integration repository')
    head=_commit(repo,expected_head)
    observed=_commit(repo,'refs/heads/'+branch)
    if observed!=head and (not _pinned or subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',head,observed],capture_output=True).returncode):
        raise Conflict('candidate freeze requires the expected integration head')
    contract=policy(store,boundary)
    with store.transaction() as tx:
        reversions=[]
        for journal in tx.db.execute("SELECT * FROM delivery_reverts WHERE status='reverted' ORDER BY rowid"):
            reverted=json.loads(journal['payload'])
            if Path(reverted['repo']).resolve()==repo and reverted['branch']==branch and subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',reverted['head'],head],capture_output=True).returncode==0:
                reversions.append({'id':journal['revert_id'],'head':reverted['head'],'change':reverted['change'],'revision':reverted['revision'],'payload_hash':digest(reverted)})
        reversed_intents=_reversions_ready(store,reversions,tx)
        if retry:
            interrupted=_row(store,retry)
            body=interrupted['payload']
            if interrupted['status'] not in {'failed','interrupted'} or interrupted['head']!=head or interrupted['boundary']!=boundary or Path(body['repo']).resolve()!=repo or not body.get('reconciliation',{}).get('reconciled'):
                raise Conflict('retry requires a safely reconciled attempt at this exact source boundary')
            for name in ('run_id','baseline_run_id'):
                if body.get(name) and tx.db.execute('SELECT 1 FROM runs WHERE run_id=? AND ended_at IS NULL',(body[name],)).fetchone():
                    raise Conflict('retry still has an active prior validation owner')
        checkpoint=None
        for prior in tx.db.execute("SELECT * FROM delivery_validations WHERE boundary='integration' AND status='verified' ORDER BY rowid DESC"):
            previous=json.loads(prior['payload'])
            if Path(previous['repo']).resolve()!=repo or previous['branch']!=branch:
                continue
            if subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',prior['head'],head],capture_output=True).returncode:
                continue
            checkpoint={'batch':prior['batch_id'],'head':prior['head'],'proof_hash':digest(previous['proof']),'source_archive':previous['source_archive']}
            _checkpoint_ready(store,checkpoint,tx,reversed_intents=reversed_intents)
            if prior['head']==head and boundary=='integration' and previous['policy']['hash']==contract['hash']:
                return view(store,prior['batch_id'],_tx=tx)
            break
        landed=[]
        for row in tx.db.execute("SELECT * FROM delivery_batches WHERE status NOT IN ('prepared','verified','deployable') ORDER BY rowid"):
            body=json.loads(row['payload'])
            if Path(body['repo']).resolve()!=repo or body['branch']!=branch:
                continue
            if subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',row['head'],head],capture_output=True).returncode:
                continue
            if checkpoint and subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',row['head'],checkpoint['head']],capture_output=True).returncode==0:
                continue
            change=_change(tx,body['change'])
            if (change['change_id'],change['revision']) in reversed_intents and change['status']=='reverted':
                continue
            if change['revision']!=body['revision'] or change['status']!='landed':
                raise Conflict('coalesced member intent changed')
            _approved(store,change)
            _completion(store,change['payload']['run_id'],head=change['head'])
            landed.append({'batch':row['batch_id'],'change':change['change_id'],'revision':change['revision'],
                           'intent_hash':digest(change['payload']),'landed_head':row['head'],'base':body['base']})
            if len(landed)>128:
                raise Conflict('coalesced pending frontier exceeds 128 changes; validate a smaller fixed snapshot first')
        if not landed and not checkpoint and not reversions or not any(member['landed_head']==head for member in landed) and (not checkpoint or checkpoint['head']!=head) and not any(item['head']==head for item in reversions):
            raise Conflict('candidate source has no bounded authoritative landed intent')
        if boundary=='deploy':
            prior=tx.db.execute("SELECT batch_id FROM delivery_validations WHERE head=? AND boundary='integration' AND status='verified'",(head,)).fetchone()
            if prior is None or not readiness(store,prior[0],_tx=tx)['ready']:
                raise Conflict('pre-deploy candidate needs verified integration at this exact snapshot')
            _members_ready(store,{'boundary':'deploy','payload':{'members':landed,'checkpoint':checkpoint,'reversions':reversions}},tx)
        batch=digest({'source':head,'boundary':boundary,'policy':contract['hash'],'members':landed,'checkpoint':checkpoint,'retry':retry,'reversions':reversions})
        existing=tx.db.execute('SELECT 1 FROM delivery_validations WHERE batch_id=?',(batch,)).fetchone()
        if existing:
            return view(store,batch,_tx=tx)
        repository_id=_change(tx,landed[-1]['change'])['payload']['repository'] if landed else _row(store,checkpoint['batch'])['repository'] if checkpoint else _change(tx,reversions[-1]['change'])['payload']['repository']
    # Slow source acquisition and hashing hold no ledger writer lease.
    readset=source_readset(contract)
    archive,files=_archive(store,repo,head,readset)
    external_environment=_external_environment(contract)
    try:
        with store.transaction() as tx:
            if policy(store,boundary)['hash']!=contract['hash']:
                raise Conflict('boundary policy changed during candidate preparation')
            if not external_environment['complete'] or _external_environment(contract)['hash']!=external_environment['hash']:
                raise Conflict('declared external validation environment changed during preparation')
            current=_commit(repo,'refs/heads/'+branch)
            if subprocess.run(['git','-C',str(repo),'merge-base','--is-ancestor',head,current],capture_output=True).returncode:
                raise Conflict('candidate source left integration history during preparation')
            for member in landed:
                change=_change(tx,member['change'])
                if change['revision']!=member['revision'] or digest(change['payload'])!=member['intent_hash']:
                    raise Conflict('candidate intent changed during source preparation')
                _approved(store,change)
                _completion(store,change['payload']['run_id'],head=change['head'])
            if checkpoint:
                _checkpoint_ready(store,checkpoint,tx,reversed_intents=reversed_intents)
            _reversions_ready(store,reversions,tx)
            if tx.db.execute('SELECT 1 FROM delivery_validations WHERE batch_id=?',(batch,)).fetchone():
                return view(store,batch,_tx=tx)
            source=tx.put_artifact_file(archive,owner_type='validation_source',owner_id=batch,slot='archive',max_bytes=256*1024*1024)
            payload={'repo':str(repo),'branch':branch,'tree':_git(repo,'rev-parse',head+'^{tree}').decode().strip(),
                     'source_archive':source,'files':files,'source_coverage':{'kind':'whole_tree' if readset is None else 'declared_closure','paths':readset},'external_environment':external_environment,'members':landed,'checkpoint':checkpoint,'reversions':reversions,'policy':contract,'proof':None,'previous_attempt':retry}
            tx._change()
            tx.db.execute("INSERT INTO delivery_validations VALUES(?,?,?,?,'queued',?)",(batch,repository_id,head,boundary,canonical(payload)))
            _event(tx,'validation_queued',batch,{'batch':batch,'head':head,'boundary':boundary,'changes':[m['change'] for m in landed]})
    finally:
        archive.unlink(missing_ok=True)
    return view(store,batch)


def _source_intact(directory,files):
    for item in files:
        path=directory/item['path']
        if 'link' in item:
            if not path.is_symlink() or os.readlink(path)!=item['link']:
                return False
        else:
            if path.is_symlink() or not path.is_file() or path.stat().st_mode&0o111 != item['mode']&0o111:
                return False
            hasher=hashlib.sha256()
            with path.open('rb') as source:
                while chunk:=source.read(65536):
                    hasher.update(chunk)
            if hasher.hexdigest()!=item['hash']:
                return False
    return True


def materialize(store,row,destination):
    target=Path(destination).resolve()
    if target.exists() or target.is_relative_to(Path(row['payload']['repo'])):
        raise Conflict('validation requires a fresh snapshot directory outside the integration repository')
    workdirs=[Path(r[0]) for r in store.db.execute("SELECT DISTINCT json_extract(payload,'$.workdir') FROM tasks WHERE json_extract(payload,'$.plan_id') IS NOT NULL")]
    if any(target.is_relative_to(root) for root in workdirs):
        raise Conflict('validation snapshot may not overwrite a task worktree')
    with store.transaction() as tx:
        tx.read_artifact(row['payload']['source_archive'])
    target.mkdir(parents=True)
    try:
        with tarfile.open(store.artifacts/row['payload']['source_archive'],'r') as archive:
            archive.extractall(target,filter='data')
        if not _source_intact(target,row['payload']['files']):
            raise Conflict('materialized source does not match the immutable archive')
        for recipe in row['payload']['policy']['recipes'].values():
            cwd=(target/recipe['cwd']).resolve()
            if not cwd.is_relative_to(target):
                raise Conflict('declared validation cwd escapes its snapshot')
            cwd.mkdir(parents=True,exist_ok=True)
    except BaseException:
        shutil.rmtree(target)
        raise
    (target/'.hx-validation-owner').write_text(row['batch_id'])
    return target


def _artifact_files(store,directory,paths,owner):
    result={}
    with store.transaction() as tx:
        for relative in paths:
            path=directory/relative
            if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(directory):
                raise Conflict('build did not produce the exact declared regular artifacts')
            result[relative]=tx.put_artifact_file(path,owner_type='validation_build',owner_id=owner,slot=digest(relative))
    return result


def run_validation(store,batch_id,workdir,*,env=None):
    """Own one fixed attempt; interruption never implies successful validation."""
    from . import native_processes
    authority(store,env)
    owner=native_processes.probe(os.getpid())
    if owner is None:
        raise Conflict('validation controller identity is unavailable')
    with store.transaction() as tx:
        row=_row(store,batch_id)
        if row['status'] in {'verified','deployable'}:
            return view(store,batch_id,_tx=tx)
        if row['status']!='queued':
            raise Conflict('validation has an existing attempt; reconcile interruption without silently rerunning')
        payload=row['payload']
        payload['controller']={'owner':owner['identity'],'released':False}
        payload['controller_hash']=digest(owner['identity'])
        tx._change()
        tx.db.execute("UPDATE delivery_validations SET status='preparing',payload=? WHERE batch_id=?",(canonical(payload),batch_id))
    try:
        result=_run_validation(store,batch_id,workdir,env=env)
    except BaseException as exc:
        with store.transaction() as tx:
            row=_row(store,batch_id)
            payload=row['payload']
            payload['controller']['released']=True
            payload['interruption']={'type':type(exc).__name__,'reason':str(exc),'unresolved':True}
            tx._change()
            # A failing receipt remains a failure if attribution replay was interrupted.
            state='failed' if row['status']=='failed' else 'interrupted'
            tx.db.execute('UPDATE delivery_validations SET status=?,payload=? WHERE batch_id=?',(state,canonical(payload),batch_id))
        raise
    with store.transaction() as tx:
        payload=_row(store,batch_id)['payload']
        payload['controller']['released']=True
        tx._change()
        tx.db.execute('UPDATE delivery_validations SET payload=? WHERE batch_id=?',(canonical(payload),batch_id))
    return result


def _run_validation(store,batch_id,workdir,*,env=None):
    from .change_delivery import _event
    actor=authority(store,env)
    row=_row(store,batch_id)
    if row['status'] in {'verified','deployable'}:
        return view(store,batch_id)
    if row['status']!='preparing':
        raise Conflict('validation is already running or settled; prepare a new source candidate')
    if policy(store,row['boundary'])['hash']!=row['payload']['policy']['hash']:
        raise Conflict('boundary policy changed after candidate freeze')
    _environment_ready(row)
    with store.transaction() as tx:
        _members_ready(store,row,tx)
    directory=materialize(store,row,workdir)
    contract=row['payload']['policy']
    task_id='validation.'+batch_id
    worker='validator.'+batch_id
    with store.transaction() as tx:
        current=_row(store,batch_id)
        if current['status']!='preparing':
            raise Conflict('validation snapshot has another owner')
        task_payload={'goal':'Verify fixed '+row['boundary']+' snapshot '+row['head'], 'workdir':str(directory),
                      'checks':contract['recipes'],'repository':row['repository'],'resources':contract['resources'],
                      'validation_snapshot':batch_id,'source_commit':row['head'],'source_archive':row['payload']['source_archive']}
        revision=tx.put_task(task_id,task_payload,expected_revision=0)
        task=tx.task(task_id)
        dependency_packets.guard_resources(tx,task)
        run=tx.start_run(task_id,revision,worker)
        dependency_packets.acquire_resources(tx,task,run)
        payload={**row['payload'],'run_id':run,'workdir':str(directory),'actor':actor}
        tx.db.execute("UPDATE delivery_validations SET status='running',payload=? WHERE batch_id=?",(canonical(payload),batch_id))
    child=dict(os.environ if env is None else env)
    receipts={}
    built={}
    failure=None
    for name in contract['build_checks']+contract['checks']:
        checked=checks.run_check(store,run,name,worker_id=worker,env=child,allow_reuse=False)
        receipts[name]=checked['receipt_id']
        if not checked['valid']:
            failure=checked['receipt_id']
            break
        if contract['build_checks'] and name==contract['build_checks'][-1]:
            built=_artifact_files(store,directory,contract['artifacts'],batch_id)
    with store.transaction() as tx:
        payload=_row(store,batch_id)['payload']
        payload['proof']={'run_id':run,'receipts':receipts,'failure':failure,'head':row['head'],'tree':payload['tree'],'policy_hash':contract['hash']}
        if failure is None:
            _environment_ready(row)
            _members_ready(store,row,tx)
            if not _source_intact(directory,payload['files']) or policy(store,row['boundary'])['hash']!=contract['hash']:
                raise Conflict('source or policy changed during fixed validation')
            for name,receipt in receipts.items():
                record=tx.db.execute('SELECT * FROM receipts WHERE receipt_id=?',(receipt,)).fetchone()
                recipe=contract['recipes'][name]
                cwd=(directory/recipe['cwd']).resolve()
                source=checks.source_identity(cwd,recipe,child)
                environment,_=fingerprints.environment(cwd,recipe['argv'],recipe['environment'],child)
                if not checks.receipt_intact(store,record) or source['hash']!=record['inputs_hash'] or environment['hash']!=record['environment_hash'] or not source['complete'] or not environment['complete']:
                    raise Conflict('fixed validation receipt is stale at publication')
            if built:
                for path,artifact in built.items():
                    if hashlib.sha256((directory/path).read_bytes()).hexdigest()!=artifact:
                        raise Conflict('tested build changed before candidate publication')
                manifest={'schema_version':1,'source_commit':row['head'],'source_tree':payload['tree'],'source_archive':payload['source_archive'],
                          'policy_hash':contract['hash'],'artifacts':built,'checks':receipts}
                payload['build_id']=digest(manifest)
                payload['build_manifest']=tx.put_artifact(canonical(manifest).encode(),owner_type='validation_build',owner_id=batch_id,slot='manifest')
            status='deployable' if row['boundary']=='deploy' else 'verified'
            # Current intent/review and real-provider gates are rechecked after execution.
            payload['proof']['source_archive']=payload['source_archive']
            tx.finish_run(run,'completed')
            if row['boundary']=='integration':
                for member in payload['members']:
                    tx.db.execute("UPDATE delivery_batches SET status='verified' WHERE batch_id=?",(member['batch'],))
        else:
            status='failed'
            tx.finish_run(run,'failed')
        tx._change()
        tx.db.execute('UPDATE delivery_validations SET status=?,payload=? WHERE batch_id=?',(status,canonical(payload),batch_id))
        _event(tx,'candidate_'+status,batch_id,{'batch':batch_id,'head':row['head'],'boundary':row['boundary'],'failure':failure,'build_id':payload.get('build_id')})
    if failure:
        from .delivery_recovery import fail
        fail(store,batch_id,failure,env=env)
    elif not readiness(store,batch_id)['ready']:
        raise Conflict('candidate checks passed but current review or real-provider obligations are invalid')
    return view(store,batch_id)


def reconcile(store,batch_id,*,env=None):
    """Settle owned interrupted runs only after every child claim is quiescent."""
    from . import native_processes, check_artifacts
    from .change_delivery import _event
    authority(store,env)
    row=_row(store,batch_id)
    payload=row['payload']
    if row['status'] in {'verified','deployable'}:
        current=view(store,batch_id)
        return {**current,'reconciled':True,'unresolved':not current['ready']}
    controller=payload.get('controller',{})
    owner=controller.get('owner')
    if not owner or digest(owner)!=payload.get('controller_hash'):
        raise Conflict('validation controller ownership is missing or corrupt')
    if not controller.get('released') and native_processes.current(owner) is not None:
        raise Conflict('validation controller is still alive')
    run_ids=[payload[name] for name in ('run_id','baseline_run_id') if payload.get(name)]
    pending=[]
    for run in run_ids:
        pending.extend(store.db.execute("SELECT e.execution_id,a.status,a.payload,a.payload_hash FROM check_executions e LEFT JOIN check_artifacts a ON a.execution_id=e.execution_id WHERE e.run_id=? AND (e.status='running' OR a.status='running')",(run,)).fetchall())
    for claim in pending:
        if claim['status'] is None:
            raise Conflict('validation check ownership is unknown; retain its resource leases')
        metadata=json.loads(claim['payload'])
        if digest(metadata)!=claim['payload_hash'] or metadata.get('owner')!=owner:
            raise Conflict('validation check ownership differs from its controller')
        if claim['status']=='running':
            # This helper refuses live owners, unknown spawn windows and live groups.
            check_artifacts.reconcile(store,claim['execution_id'])
    with store.transaction() as tx:
        current=_row(store,batch_id)
        body=current['payload']
        if body.get('controller')!=controller:
            raise Conflict('validation ownership changed during reconciliation')
        evidence=[]
        for run in run_ids:
            claims=tx.db.execute("SELECT e.execution_id,a.status FROM check_executions e LEFT JOIN check_artifacts a ON a.execution_id=e.execution_id WHERE e.run_id=? AND e.status='running'",(run,)).fetchall()
            if any(c['status']!='finished' for c in claims):
                raise Conflict('validation still has unresolved child execution claims')
            for claim in claims:
                tx.db.execute("UPDATE check_executions SET status='finished',payload=? WHERE execution_id=?",(canonical({'interrupted':True,'reconciled':True}),claim['execution_id']))
            evidence.extend(r[0] for r in tx.db.execute('SELECT receipt_id FROM receipts WHERE run_id=?',(run,)))
            active=tx.db.execute('SELECT ended_at FROM runs WHERE run_id=?',(run,)).fetchone()
            if active is None:
                raise Conflict('validation run ownership disappeared')
            if active[0] is None:
                tx.finish_run(run,'failed')
        body['reconciliation']={'reconciled':True,'receipts':evidence,'run_ids':run_ids,'unresolved':True}
        body['controller']['released']=True
        state=current['status'] if current['status'] in {'failed','verified','deployable'} else 'interrupted'
        tx._change()
        tx.db.execute('UPDATE delivery_validations SET status=?,payload=? WHERE batch_id=?',(state,canonical(body),batch_id))
        _event(tx,'validation_interrupted',batch_id,{'batch':batch_id,'run_ids':run_ids,'receipts':evidence,'unresolved':True})
    return {**view(store,batch_id),'reconciled':True,'unresolved':True}


def _members_ready(store,row,tx,*,_skip_checkpoint=False,_reversed_intents=None):
    from .change_delivery import _change,_approved,_completion
    reversed_intents=set(_reversed_intents or ())|_reversions_ready(store,row['payload'].get('reversions',[]),tx)
    if not _skip_checkpoint and row['payload'].get('checkpoint'):
        _checkpoint_ready(store,row['payload']['checkpoint'],tx,deploy=row['boundary']=='deploy',reversed_intents=reversed_intents)
    for member in row['payload']['members']:
        change=_change(tx,member['change'])
        if (change['change_id'],change['revision']) in reversed_intents and change['status']=='reverted':
            continue
        if change['revision']!=member['revision'] or digest(change['payload'])!=member['intent_hash'] or change['status']!='landed':
            raise Conflict('source snapshot intent changed or was reverted')
        _approved(store,change)
        _completion(store,change['payload']['run_id'],head=change['head'])
        task=dependency_packets.effective_task(tx,tx.task(change['payload']['evidence']['task_id']))
        if row['boundary']=='deploy' and task['payload'].get('dependency_packet') and not dependency_packets.summary(tx,task)['deployable']:
            raise Conflict('real-provider integration obligations remain pending')


def _reversions_ready(store,references,tx):
    from .delivery_recovery import _failure_receipt
    from .change_delivery import _change
    result=set()
    for reference in references:
        journal=tx.db.execute('SELECT * FROM delivery_reverts WHERE revert_id=?',(reference['id'],)).fetchone()
        if journal is None or journal['status']!='reverted':
            raise Conflict('candidate reversal publication is unresolved')
        body=json.loads(journal['payload'])
        change=_change(tx,reference['change'])
        if digest(body)!=reference['payload_hash'] or body['head']!=reference['head'] or body['revision']!=reference['revision'] or change['revision']!=reference['revision'] or change['status']!='reverted':
            raise Conflict('candidate reversal intent changed')
        repair=tx.db.execute('SELECT payload FROM delivery_repairs WHERE repair_id=?',(body['repair_id'],)).fetchone()
        if repair is None:
            raise Conflict('candidate reversal lost its regression attribution')
        proof=json.loads(repair[0])
        _failure_receipt(store,proof['failed_receipt'])
        baseline=tx.db.execute('SELECT * FROM receipts WHERE receipt_id=?',(proof['baseline_receipt'],)).fetchone()
        if proof['attribution']!='confirmed' or proof['changes']!=[reference['change']] or not checks.receipt_intact(store,baseline):
            raise Conflict('candidate reversal regression evidence is invalid')
        result.add((reference['change'],reference['revision']))
    return result


def _checkpoint_ready(store,reference,tx,*,deploy=False,reversed_intents=None):
    """Verify each bounded baseline once without recursive chain growth."""
    seen=set()
    reversed_intents=set(reversed_intents or ())
    while reference:
        if reference['batch'] in seen:
            raise Conflict('validation checkpoint chain contains a cycle')
        seen.add(reference['batch'])
        row=_row(store,reference['batch'])
        payload=row['payload']
        reversed_intents.update(_reversions_ready(store,payload.get('reversions',[]),tx))
        proof=payload.get('proof')
        if row['status']!='verified' or row['boundary']!='integration' or row['head']!=reference['head'] or digest(proof)!=reference['proof_hash'] or payload['source_archive']!=reference['source_archive'] or not checks._artifact_intact(store,payload['source_archive']):
            raise Conflict('coalesced baseline proof changed or became invalid')
        if not proof or set(proof['receipts'])!=set(payload['policy']['recipes']):
            raise Conflict('coalesced baseline lacks exact check proof')
        for name,receipt in proof['receipts'].items():
            record=tx.db.execute('SELECT * FROM receipts WHERE receipt_id=? AND run_id=?',(receipt,proof['run_id'])).fetchone()
            if not checks.receipt_intact(store,record) or record['check_version']!=digest(payload['policy']['recipes'][name]):
                raise Conflict('coalesced baseline execution proof is invalid')
        _members_ready(store,{**row,'boundary':'deploy' if deploy else 'integration'},tx,_skip_checkpoint=True,_reversed_intents=reversed_intents)
        reference=payload.get('checkpoint')


def readiness(store,batch_id,*,_tx=None):
    from .change_delivery import _change,_approved,_completion
    row=_row(store,batch_id)
    payload=row['payload']
    reasons=[]
    try:
        if row['status'] not in {'verified','deployable'}:
            raise Conflict('required fixed snapshot checks have not passed')
        if policy(store,row['boundary'])['hash']!=payload['policy']['hash']:
            raise Conflict('required policy check identities changed')
        _environment_ready(row)
        if not checks._artifact_intact(store,payload['source_archive']):
            raise Conflict('fixed source archive is missing or corrupt')
        proof=payload['proof']
        if set(proof['receipts'])!=set(payload['policy']['recipes']):
            raise Conflict('snapshot proof omits required checks')
        for name,receipt in proof['receipts'].items():
            record=store.db.execute('SELECT * FROM receipts WHERE receipt_id=? AND run_id=?',(receipt,proof['run_id'])).fetchone()
            if not checks.receipt_intact(store,record) or record['check_version']!=digest(payload['policy']['recipes'][name]):
                raise Conflict('snapshot recipe or execution evidence is invalid')
        with (store.transaction() if _tx is None else nullcontext(_tx)) as tx:
            _members_ready(store,row,tx)
        if row['boundary']=='deploy':
            if not payload.get('build_id') or not checks._artifact_intact(store,payload.get('build_manifest','')):
                raise Conflict('tested build manifest is missing or corrupt')
            manifest=json.loads((store.artifacts/payload['build_manifest']).read_text())
            if digest(manifest)!=payload['build_id'] or manifest['source_commit']!=row['head'] or manifest['checks']!=proof['receipts']:
                raise Conflict('build identity differs from the tested artifact manifest')
            if any(not checks._artifact_intact(store,artifact) for artifact in manifest['artifacts'].values()):
                raise Conflict('tested deploy artifact is missing or corrupt')
    except (Conflict,ValidationError,KeyError,ValueError) as exc:
        reasons.append(str(exc))
    return {'batch':batch_id,'head':row['head'],'boundary':row['boundary'],'ready':not reasons,'deployable':not reasons and row['boundary']=='deploy','reasons':reasons,'build_id':payload.get('build_id')}


def view(store,batch_id,*,_tx=None):
    row=_row(store,batch_id)
    return {**readiness(store,batch_id,_tx=_tx),'status':row['status'],'source_tree':row['payload']['tree'],
            'source_archive':row['payload']['source_archive'],'members':[m['change'] for m in row['payload']['members']],
            'required_checks':{name:digest(recipe) for name,recipe in row['payload']['policy']['recipes'].items()},
            'proof':row['payload'].get('proof'),'build_manifest':row['payload'].get('build_manifest'),
            'checkpoint':row['payload'].get('checkpoint'),'source_coverage':row['payload'].get('source_coverage'),'reversions':row['payload'].get('reversions',[])}


def contains_change(store,batch_id,change_id):
    """Bounded members and sealed checkpoint ancestry determine membership."""
    seen=set()
    row=_row(store,batch_id)
    while True:
        if row['batch_id'] in seen:
            raise Conflict('validation checkpoint chain contains a cycle')
        seen.add(row['batch_id'])
        if any(member['change']==change_id for member in row['payload']['members']):
            return True
        reference=row['payload'].get('checkpoint')
        if not reference:
            return False
        row=_row(store,reference['batch'])
        if row['head']!=reference['head'] or digest(row['payload'].get('proof'))!=reference['proof_hash'] or row['payload']['source_archive']!=reference['source_archive']:
            raise Conflict('validation membership checkpoint changed')


def attest_unit(store,batch_id,run_id,*,env=None):
    """Accept a completed integration worker only for exact policy/source proof."""
    from .change_delivery import _completion,_event
    authority(store,env)
    row=_row(store,batch_id)
    if row['status'] not in {'queued','verified','deployable'}:
        raise Conflict('integration attestation may not replace an owned or interrupted fixed attempt')
    contract=row['payload']['policy']
    if policy(store,row['boundary'])['hash']!=contract['hash']:
        raise Conflict('candidate required policy changed')
    result=_completion(store,run_id,head=row['head'],required=contract['recipes'])
    assignment=store.db.execute('SELECT t.payload FROM runs r JOIN tasks t ON t.task_id=r.task_id AND t.revision=r.task_revision WHERE r.run_id=?',(run_id,)).fetchone()
    task=json.loads(assignment[0])
    if task.get('repository')!=row['repository']:
        raise Conflict('integration worker belongs to a different source repository')
    directory=Path(task['workdir'])
    child=dict(os.environ if env is None else env)
    for name,recipe in contract['recipes'].items():
        record=store.db.execute('SELECT * FROM receipts WHERE receipt_id=? AND run_id=?',(result['checks'][name],run_id)).fetchone()
        source=checks.source_identity((directory/recipe['cwd']).resolve(),recipe,child)
        if not checks.receipt_intact(store,record) or record['check_version']!=digest(recipe) or not source['complete'] or source['hash']!=record['inputs_hash']:
            raise Conflict('completed integration unit has unrelated or stale policy checks')
    built=_artifact_files(store,directory,contract['artifacts'],batch_id) if row['boundary']=='deploy' else {}
    with store.transaction() as tx:
        current=_row(store,batch_id)
        if current['status']!=row['status'] or current['payload'].get('proof')!=row['payload'].get('proof'):
            raise Conflict('candidate ownership changed before unit proof publication')
        _environment_ready(row)
        _members_ready(store,row,tx)
        if policy(store,row['boundary'])['hash']!=contract['hash']:
            raise Conflict('candidate policy changed before unit proof publication')
        for name,recipe in contract['recipes'].items():
            record=tx.db.execute('SELECT * FROM receipts WHERE receipt_id=? AND run_id=?',(result['checks'][name],run_id)).fetchone()
            cwd=(directory/recipe['cwd']).resolve()
            source=checks.source_identity(cwd,recipe,child)
            environment,_=fingerprints.environment(cwd,recipe['argv'],recipe['environment'],child)
            if not checks.receipt_intact(store,record) or record['check_version']!=digest(recipe) or not source['complete'] or source['hash']!=record['inputs_hash'] or not environment['complete'] or environment['hash']!=record['environment_hash']:
                raise Conflict('integration worker proof changed before publication')
        if any(hashlib.sha256((directory/path).read_bytes()).hexdigest()!=artifact for path,artifact in built.items()):
            raise Conflict('tested integration build changed before publication')
        payload=dict(row['payload'])
        payload['proof']={'run_id':run_id,'receipts':{name:result['checks'][name] for name in contract['recipes']},
            'failure':None,'head':row['head'],'tree':payload['tree'],'policy_hash':contract['hash'],'source_archive':payload['source_archive']}
        if built:
            manifest={'schema_version':1,'source_commit':row['head'],'source_tree':payload['tree'],'source_archive':payload['source_archive'],
                'policy_hash':contract['hash'],'artifacts':built,'checks':payload['proof']['receipts']}
            payload['build_id']=digest(manifest)
            payload['build_manifest']=tx.put_artifact(canonical(manifest).encode(),owner_type='validation_build',owner_id=batch_id,slot='manifest')
        state='deployable' if row['boundary']=='deploy' else 'verified'
        if row['status'] in {'verified','deployable'}:
            if row['payload']['proof']!=payload['proof']:
                raise Conflict('verified snapshot proof is immutable')
            return view(store,batch_id,_tx=tx)
        tx._change()
        tx.db.execute('UPDATE delivery_validations SET status=?,payload=? WHERE batch_id=?',(state,canonical(payload),batch_id))
        if row['boundary']=='integration':
            for member in payload['members']:
                tx.db.execute("UPDATE delivery_batches SET status='verified' WHERE batch_id=?",(member['batch'],))
        _event(tx,'candidate_'+state,batch_id,{'batch':batch_id,'head':row['head'],'run_id':run_id,'build_id':payload.get('build_id')})
    return view(store,batch_id)


def _environment_ready(row):
    frozen=row['payload'].get('external_environment')
    current=_external_environment(row['payload']['policy'])
    if frozen is None or not current['complete'] or not frozen['complete'] or current['hash']!=frozen['hash']:
        raise Conflict('declared external validation environment changed')
