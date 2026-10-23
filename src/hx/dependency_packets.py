"""Worker-bound dependency packets and immutable early interface proofs.

Partner declarations describe behavior and obligations. Exact recipes are frozen
by the worker before edits and approved by a separately named policy reviewer.
Early publication grants interface access, never a completed provider claim.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import checks, fingerprints
from .continuity_store import Conflict, canonical, digest
from .errors import Refused, ValidationError

SCHEMA = """
CREATE TABLE IF NOT EXISTS unit_check_bindings (
 task_id TEXT NOT NULL, task_revision INTEGER NOT NULL, payload TEXT NOT NULL,
 run_id TEXT NOT NULL, approved_by TEXT, PRIMARY KEY(task_id,task_revision));
CREATE TABLE IF NOT EXISTS unit_contracts (
 task_id TEXT NOT NULL, task_revision INTEGER NOT NULL, output_id TEXT NOT NULL,
 version INTEGER NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(task_id,output_id,version));
CREATE TABLE IF NOT EXISTS unit_resources (
 repository TEXT NOT NULL, resource_id TEXT NOT NULL, run_id TEXT NOT NULL,
 mode TEXT NOT NULL, PRIMARY KEY(repository,resource_id,run_id));
CREATE TABLE IF NOT EXISTS unit_milestones (
 task_id TEXT NOT NULL, task_revision INTEGER NOT NULL, kind TEXT NOT NULL,
 identity TEXT NOT NULL, payload TEXT NOT NULL,
 PRIMARY KEY(task_id,task_revision,kind,identity));
"""


def initialize(db):
    for statement in SCHEMA.split(';'):
        if statement.strip():
            db.execute(statement)


def obligations(payload):
    result = {f"acceptance.{index}" for index, _ in enumerate(payload['acceptance'])}
    result.update('output.' + output['id'] for output in payload['outputs'])
    result.update('integration.' + need['task_id'] + '/' + need['output_id']
                  for need in payload['prerequisites'] if need.get('need', 'hard') != 'hard')
    return result


def effective_task(tx, task):
    """Overlay separately frozen worker recipes without rewriting a task revision."""
    if not task or not task['payload'].get('dependency_packet'):
        return task
    row = tx.db.execute('SELECT payload,approved_by FROM unit_check_bindings WHERE task_id=? AND task_revision=?',
                        (task['task_id'], task['revision'])).fetchone()
    payload = dict(task['payload'])
    payload['binding_status'] = 'approved' if row and row['approved_by'] else 'proposed' if row else 'unbound'
    if row:
        binding = json.loads(row['payload'])
        payload['definition_of_done'] = binding['definition_of_done']
        payload['check_coverage'] = binding['coverage']
        payload['binding_hash'] = digest(binding)
        if row['approved_by']:
            payload['checks'] = binding['checks']
            payload['binding_reviewer'] = row['approved_by']
    return {**task, 'payload': payload}


def require_bound(task):
    if task['payload'].get('dependency_packet') and task['payload'].get('binding_status') != 'approved':
        raise Conflict('worker checks and Definition of done require independent policy approval before edits or proof')


def bind_checks(store, run_id, body):
    from .unit_execution import _run, workspace, _changed
    if not isinstance(body, dict) or body.keys() != {'definition_of_done', 'checks', 'coverage', 'searches'}:
        raise ValidationError('binding requires definition_of_done, checks, coverage, searches')
    from .planning import _statements
    _statements(body['definition_of_done'], 'worker Definition of done', required=True)
    if not isinstance(body['checks'], dict) or not 1 <= len(body['checks']) <= 16:
        raise ValidationError('binding requires 1–16 exact checks')
    for name, recipe in body['checks'].items():
        checks.validate(recipe)
        if name != recipe['id']:
            raise ValidationError('binding check identity differs from recipe')
    if len(canonical(body).encode()) > 16000:
        raise ValidationError('worker binding exceeds 16 KiB')
    with store.transaction() as tx:
        run, task, admission = _run(tx, run_id)
        if not task['payload'].get('dependency_packet'):
            raise ValidationError('only dependency packets use worker bindings')
        policy = task['payload']['check_policy']
        if not set(policy['required_checks']).issubset(body['checks']):
            raise ValidationError('binding omits a required policy check')
        if not isinstance(body['coverage'], dict) or set(body['coverage']) != obligations(task['payload']):
            raise ValidationError('binding must cover every acceptance, output, and integration obligation exactly')
        for ids in body['coverage'].values():
            if not isinstance(ids, list) or not ids or any(not isinstance(name,str) or name not in body['checks'] for name in ids) or len(ids) != len(set(ids)):
                raise ValidationError('coverage needs unique assigned check IDs for each obligation')
        from .search_receipts import cite
        cite(tx, run_id, task['payload'], body['searches'])
        existing = tx.db.execute('SELECT payload FROM unit_check_bindings WHERE task_id=? AND task_revision=?', (task['task_id'],task['revision'])).fetchone()
        if existing:
            if json.loads(existing[0]) != body:
                raise Conflict('worker binding is frozen; stop and revise the assignment to change it')
            return {'run_id':run_id, 'binding_hash':digest(body), 'status':task['payload'].get('binding_status')}
        state = workspace(task['payload'], clean=True)
        if state != admission['workspace'] or _changed(task['payload'], admission, state):
            raise Conflict('worker must bind checks before the first edit')
        if run['worker_id'] == policy['reviewer']:
            raise Conflict('binding reviewer must be independent of the implementing worker')
        tx._change()
        tx.db.execute('INSERT INTO unit_check_bindings VALUES(?,?,?,?,NULL)', (task['task_id'],task['revision'],canonical(body),run_id))
        tx.enqueue('packet_binding_proposed', f'binding:{run_id}', {'run_id':run_id,'binding_hash':digest(body),'reviewer':policy['reviewer']})
    return {'run_id':run_id,'binding_hash':digest(body),'status':'proposed'}


def binding_view(store, run_id, who):
    """Expose the exact frozen proposal to its worker and configured reviewer."""
    from .unit_execution import _run
    with store.transaction() as tx:
        run, task, _ = _run(tx,run_id)
        policy = task['payload'].get('check_policy',{})
        if who not in {None,'partner',run['worker_id'],policy.get('reviewer')}:
            raise Refused('binding inspection requires the implementing worker or configured reviewer')
        row = tx.db.execute('SELECT payload,approved_by FROM unit_check_bindings WHERE task_id=? AND task_revision=?', (task['task_id'],task['revision'])).fetchone()
        if row is None:
            raise Conflict('worker has not proposed a check binding')
        body = json.loads(row['payload'])
        return {'run_id':run_id,'task_id':task['task_id'],'revision':task['revision'],'goal':task['payload']['goal'],
                'obligations':sorted(obligations(task['payload'])),'policy':policy,'binding':body,
                'binding_hash':digest(body),'approved_by':row['approved_by']}


def approve_binding(store, run_id, binding_hash, reviewer):
    from .unit_execution import _run, workspace
    with store.transaction() as tx:
        run, task, admission = _run(tx, run_id)
        if reviewer != task['payload'].get('check_policy', {}).get('reviewer') or reviewer == run['worker_id']:
            raise Refused('binding approval requires the configured independent reviewer')
        row = tx.db.execute('SELECT payload,approved_by FROM unit_check_bindings WHERE task_id=? AND task_revision=?', (task['task_id'],task['revision'])).fetchone()
        if row is None or digest(json.loads(row['payload'])) != binding_hash:
            raise Conflict('binding approval must name the exact proposed binding hash')
        if workspace(task['payload'], clean=True) != admission['workspace']:
            raise Conflict('binding approval requires the unchanged pre-edit source')
        tx._change()
        tx.db.execute('UPDATE unit_check_bindings SET approved_by=? WHERE task_id=? AND task_revision=?', (reviewer,task['task_id'],task['revision']))
        tx.enqueue('packet_binding_approved', f'binding-approved:{run_id}', {'run_id':run_id,'binding_hash':binding_hash,'reviewer':reviewer})
    return {'run_id':run_id,'binding_hash':binding_hash,'status':'approved','reviewer':reviewer}


def guard_resources(tx, task):
    for resource in task['payload'].get('resources', []):
        row = tx.db.execute('''SELECT u.mode FROM unit_resources u JOIN runs r USING(run_id)
            WHERE u.repository=? AND u.resource_id=? AND r.ended_at IS NULL
            AND (u.mode='exclusive' OR ?='exclusive') LIMIT 1''',
            (task['payload']['repository'], resource['id'], resource['mode'])).fetchone()
        if row:
            raise Conflict('shared resource has an incompatible active owner: ' + resource['id'])


def acquire_resources(tx, task, run_id):
    guard_resources(tx, task)
    tx.db.execute('DELETE FROM unit_resources WHERE run_id IN (SELECT run_id FROM runs WHERE ended_at IS NOT NULL)')
    for resource in task['payload'].get('resources', []):
        tx.db.execute('INSERT INTO unit_resources VALUES(?,?,?,?)', (task['payload']['repository'],resource['id'],run_id,resource['mode']))


def milestone(tx, task, kind, identity, proof):
    if kind not in {'contract_published','packet_covered','integration_verified','deployable'}:
        raise ValidationError('unknown packet milestone')
    result = {'task_id':task['task_id'],'revision':task['revision'],'state':kind, **proof}
    existing = tx.db.execute('SELECT payload FROM unit_milestones WHERE task_id=? AND task_revision=? AND kind=? AND identity=?', (task['task_id'],task['revision'],kind,identity)).fetchone()
    if existing:
        if json.loads(existing[0]) != result:
            raise Conflict('immutable milestone already names different proof')
        return result
    tx._change()
    tx.db.execute('INSERT INTO unit_milestones VALUES(?,?,?,?,?)', (task['task_id'],task['revision'],kind,identity,canonical(result)))
    key = f'{kind}:{task["task_id"]}:{task["revision"]}:{digest(identity)}'
    tx.enqueue(kind, key, result)
    run_id = proof.get('integration_run', proof.get('run_id'))
    if run_id:
        event = tx.append_event(run_id, 'packet-milestones', key, kind, result)
        cursor = tx.db.execute("SELECT revision FROM cursors WHERE run_id=? AND stream_id='packet-milestones'", (run_id,)).fetchone()
        tx.classify(run_id,'packet-milestones',expected_revision=cursor[0],through=event['seq'],dispositions={event['seq']:'reduced'})
    return result


def _receipt(tx, receipt_id):
    row = tx.db.execute('SELECT * FROM receipts WHERE receipt_id=?', (receipt_id,)).fetchone()
    if row is None or not checks.receipt_intact(tx.store,row):
        raise Conflict('contract proof check evidence is invalid or unavailable')
    return row


def contract_proof(tx, dependency):
    row = tx.db.execute('SELECT * FROM unit_contracts WHERE task_id=? AND output_id=? AND version=?',
        (dependency['task_id'],dependency['output_id'],dependency['version'])).fetchone()
    if row is None:
        raise Conflict('exact prerequisite interface has not been published')
    task = tx.task(dependency['task_id'])
    if task is None or task['revision'] != row['task_revision']:
        raise Conflict('interface publication belongs to a superseded producer revision')
    if dependency.get('plan_id',task['payload'].get('plan_id')) != task['payload'].get('plan_id'):
        raise Conflict('interface producer is no longer a unit of the named plan')
    proof = json.loads(row['payload'])
    from .native_producer import require_drained
    require_drained(tx,proof['run_id'])
    for receipt_id in proof['checks'].values():
        _receipt(tx,receipt_id)
    for artifact in proof['artifacts'].values():
        if not checks._artifact_intact(tx.store,artifact):
            raise Conflict('immutable interface artifact is unavailable')
    return proof


def publish_contract(store, run_id, output_id, receipt_ids, *, env=None):
    from .unit_execution import _run, _entries, _git, workspace, audit
    if not audit(store,run_id)['within_scope']:
        raise Conflict('contract publication requires an in-scope active run')
    with store.snapshot() as tx:
        _, task, _ = _run(tx,run_id)
        require_bound(task)
        output = next((item for item in task['payload']['outputs'] if item['id']==output_id and item['kind']=='contract'),None)
        if output is None:
            raise ValidationError('unknown declared interface contract output')
        coverage = task['payload']['check_coverage']['output.'+output_id]
        if not isinstance(receipt_ids,dict) or set(receipt_ids) != set(coverage):
            raise ValidationError('publication needs exactly the checks covering its output obligation')
        before = workspace(task['payload'], clean=True)
    for name, receipt in receipt_ids.items():
        current = checks.current(store,run_id,receipt,check_id=name,env=env)
        if not current['current']:
            raise Conflict('contract check proof is no longer current')
    # Read the published bytes and fingerprint the check inputs before taking
    # the writer; the transaction rechecks the worktree identity they came from.
    workdir = task['payload']['workdir']
    files = _entries(workdir,before['commit'],output['paths'])
    blobs = {path: _git(workdir,'show',before['commit']+':'+path) for path, entry in files.items() if entry is not None}
    child = dict(os.environ if env is None else env)
    identities = {}
    for name in receipt_ids:
        recipe = task['payload']['checks'][name]
        cwd = (Path(workdir) / recipe['cwd']).resolve()
        identities[name] = (checks.source_identity(cwd,recipe,child),
                            fingerprints.environment(cwd,recipe['argv'],recipe['environment'],child)[0])
    from . import code_stubs
    stubs = code_stubs.extract(blobs)
    with store.transaction() as tx:
        _, task, _ = _run(tx,run_id)
        if task['payload']['workdir'] != workdir or workspace(task['payload'],clean=True) != before:
            raise Conflict('contract source changed during proof verification')
        from .native_producer import require_drained
        require_drained(tx,run_id)
        if any(value is None or value['mode'] not in {'100644','100755'} for value in files.values()):
            raise Conflict('interface contract must publish existing exact regular files')
        proof = {**output,'producer':task['task_id'],'task_revision':task['revision'],'run_id':run_id,
                 'checks':receipt_ids,'commit':before['commit'],'tree':before['tree'],'files':files,'artifacts':{}}
        previous = tx.db.execute('SELECT payload FROM unit_contracts WHERE task_id=? AND output_id=? AND version=?', (task['task_id'],output_id,output['version'])).fetchone()
        if previous:
            old = json.loads(previous[0])
            if any(old[key] != proof[key] for key in ('files','checks','task_revision')):
                raise Conflict('published contract version is immutable; revise the output version')
            proof = old
        else:
            for path, entry in files.items():
                proof['artifacts'][path] = tx.put_artifact(blobs[path],owner_type='contract',owner_id=digest([task['task_id'],output_id,output['version']]),slot=digest(path))
            for name, receipt_id in receipt_ids.items():
                receipt = _receipt(tx,receipt_id)
                source, environment = identities[name]
                if not source['complete'] or not environment['complete'] or source['hash'] != receipt['inputs_hash'] or environment['hash'] != receipt['environment_hash']:
                    raise Conflict('contract check inputs changed at publication')
            tx._change()
            tx.db.execute('INSERT INTO unit_contracts VALUES(?,?,?,?,?)',(task['task_id'],task['revision'],output_id,output['version'],canonical(proof)))
            milestone(tx,task,'contract_published',output_id,{'run_id':run_id,'output_id':output_id,'version':output['version'],'contract_hash':digest(proof)})
    # The ledger row is the authority; its graph stubs are derived, so a retried
    # publication re-registers them after a failed graph write.
    code_stubs.send(store.root,code_stubs.publication(store.root,task,proof,stubs),env)
    return proof


def acquire_contract(store, task_id, producer, output_id, destination):
    """Export immutable bytes to a caller-selected snapshot outside its worktree."""
    with store.transaction() as tx:
        task = tx.task(task_id)
        if not task:
            raise ValidationError('unknown consumer unit')
        need = next((need for need in task['payload'].get('prerequisites',[]) if need['task_id']==producer and need['output_id']==output_id and need.get('need','hard')!='hard'),None)
        if need is None:
            raise Refused('consumer has no permission to acquire this interface')
        proof = contract_proof(tx,need)
        target = Path(destination).resolve()
        workdirs = [Path(row[0]) for row in tx.db.execute("SELECT DISTINCT json_extract(t.payload,'$.workdir') FROM tasks t JOIN task_heads h USING(task_id,revision) WHERE json_extract(t.payload,'$.plan_id') IS NOT NULL")]
        if any(target.is_relative_to(workdir) for workdir in workdirs) or target.exists():
            raise Conflict('interface snapshot needs a fresh directory outside the consumer worktree')
        contents = {path:tx.read_artifact(artifact) for path,artifact in proof['artifacts'].items()}
    target.mkdir(parents=True)
    for path,data in contents.items():
        file = target / path
        file.parent.mkdir(parents=True,exist_ok=True)
        file.write_bytes(data)
    return {'task_id':task_id,'producer':producer,'output_id':output_id,'version':proof['version'],
            'contract_hash':digest(proof),'snapshot':str(target),'files':proof['files'],
            'need':need['need'],'stand_in_allowed':need['stand_in'],
            'pending_real_provider':{'task_id':producer,'output_id':need['provider_output'],'version':need['provider_version']}}


def _completion_intact(tx, task):
    row = tx.db.execute('SELECT payload FROM unit_completions WHERE task_id=? AND task_revision=?', (task['task_id'],task['revision'])).fetchone()
    if row is None:
        return False
    proof = json.loads(row[0])
    try:
        from .native_producer import require_drained
        require_drained(tx,proof['run_id'])
        for receipt in proof['checks'].values():
            _receipt(tx,receipt)
        return True
    except (Conflict, ValidationError):
        return False


def _integration_current(tx, task, need, milestone_proof):
    from .unit_execution import _output, workspace
    try:
        contract = contract_proof(tx,need)
        provider = _output(tx,{'task_id':need['task_id'],'output_id':need['provider_output'],'version':need['provider_version']})
        if digest(contract) != milestone_proof['contract_hash'] or digest(provider) != milestone_proof['provider_hash']:
            return False
        row = tx.db.execute('SELECT c.*,t.payload AS assignment FROM unit_completions c '
            'JOIN task_heads h ON h.task_id=c.task_id AND h.revision=c.task_revision '
            'JOIN tasks t ON t.task_id=c.task_id AND t.revision=c.task_revision WHERE c.run_id=?', (milestone_proof['integration_run'],)).fetchone()
        if row is None:
            return False
        proof = json.loads(row['payload'])
        if proof['commit'] != milestone_proof['commit'] or proof['checks'] != milestone_proof['checks']:
            return False
        integration = {'task_id':row['task_id'],'revision':row['task_revision'],'payload':json.loads(row['assignment'])}
        return _completion_intact(tx,integration) and workspace(integration['payload'],clean=True)['commit'] == proof['commit']
    except (Conflict, ValidationError, KeyError):
        return False


def summary(tx, task, *, _seen=None, _memo=None):
    """Compact current proof status; a historical milestone alone is not authority."""
    memo = {} if _memo is None else _memo
    key = (task['task_id'],task['revision'])
    if key in memo:
        return memo[key]
    seen = set() if _seen is None else set(_seen)
    if task['task_id'] in seen:
        return {'binding':'blocked','contracts':[],'packet_covered':False,'pending_integration':[], 'upstream_pending':[task['task_id']],'deployable':False}
    seen.add(task['task_id'])
    rows = tx.db.execute('SELECT kind,identity,payload FROM unit_milestones WHERE task_id=? AND task_revision=?', (task['task_id'],task['revision'])).fetchall()
    published = [row['identity'] for row in rows if row['kind']=='contract_published']
    verified = {row['identity']:json.loads(row['payload']) for row in rows if row['kind']=='integration_verified'}
    pending, upstream = [], []
    for need in task['payload'].get('prerequisites',[]):
        identity = need['task_id']+'/'+need['output_id']
        if need.get('need','hard') != 'hard':
            if identity not in verified or not _integration_current(tx,task,need,verified[identity]):
                pending.append(identity)
        else:
            producer = tx.task(need['task_id'])
            if producer and producer['payload'].get('dependency_packet'):
                producer_status = summary(tx,effective_task(tx,producer),_seen=seen,_memo=memo)
                if not producer_status['deployable']:
                    upstream.append(need['task_id'])
    is_covered = any(row['kind']=='packet_covered' for row in rows) and _completion_intact(tx,task)
    result = {'binding':task['payload'].get('binding_status','unbound'),'contracts':published,
            'packet_covered':is_covered,'pending_integration':sorted(pending),'upstream_pending':sorted(set(upstream)),
            'deployable':bool(task['payload'].get('dependency_packet') and is_covered and not pending and not upstream)}
    memo[key] = result
    return result


def completion_checks(payload):
    names = set(payload['check_policy']['required_checks'])
    for obligation, ids in payload.get('check_coverage', {}).items():
        if not obligation.startswith('integration.'):
            names.update(ids)
    return names


def receipt_output(payload, output, receipt_ids):
    """A packet receipt proves its complete bound obligation, not a single label."""
    names = payload['check_coverage']['output.'+output['id']]
    return {name:receipt_ids[name] for name in names}


def guard_contract_files(tx, task, state):
    from .unit_execution import _entries
    for row in tx.db.execute('SELECT payload FROM unit_contracts WHERE task_id=? AND task_revision=?', (task['task_id'],task['revision'])):
        proof = json.loads(row[0])
        if _entries(task['payload']['workdir'],state['commit'],list(proof['files'])) != proof['files']:
            raise Conflict('published interface changed; stop and revise its immutable contract version')


def covered(tx, task, proof):
    milestone(tx,task,'packet_covered','packet',proof)
    if summary(tx,task)['deployable']:
        milestone(tx,task,'deployable','packet',{'run_id':proof['run_id'],'commit':proof['commit'],'integration_obligations':[]})


def verify_integration(store, task_id, integration_run, producer, output_id):
    """Discharge a covered packet's real-provider obligation after separate proof.

    The integration worker owns its own exact checks, source scope and completed
    run. Its completed source must contain both consumer and real provider files.
    """
    from .unit_execution import _task, _output, _entries, workspace
    with store.transaction() as tx:
        task = _task(tx,task_id)
        if not task['payload'].get('dependency_packet'):
            raise ValidationError('integration milestones require a dependency packet')
        completed = tx.db.execute('SELECT payload FROM unit_completions WHERE task_id=? AND task_revision=?', (task_id,task['revision'])).fetchone()
        if completed is None:
            raise Conflict('packet must be covered before its future integration obligation can discharge')
        dependency = next((need for need in task['payload']['prerequisites'] if need['task_id']==producer and need['output_id']==output_id and need.get('need','hard')!='hard'),None)
        if dependency is None:
            raise ValidationError('unknown future real-provider integration obligation')
        contract = contract_proof(tx,dependency)
        provider = _output(tx,{'task_id':producer,'output_id':dependency['provider_output'],'version':dependency['provider_version']})
        row = tx.db.execute('SELECT * FROM unit_completions WHERE run_id=?',(integration_run,)).fetchone()
        if row is None:
            raise Conflict('integration requires a successfully completed separate integration run')
        integration = _task(tx,row['task_id'])
        if integration['task_id'] in {task_id,producer} or integration['revision'] != row['task_revision'] or integration['payload']['repository'] != task['payload']['repository']:
            raise Conflict('integration must be a current separate unit in the same repository')
        declared = {need['task_id'] for need in integration['payload']['prerequisites'] if need.get('need','hard')=='hard'}
        if not {task_id,producer}.issubset(declared):
            raise Conflict('integration unit must explicitly require the covered consumer and completed provider')
        proof = json.loads(row['payload'])
        required = task['payload']['check_coverage']['integration.'+producer+'/'+output_id]
        for name in required:
            if name not in proof['checks'] or digest(integration['payload']['checks'].get(name)) != digest(task['payload']['checks'][name]):
                raise Conflict('integration must execute the exact frozen consumer integration checks')
        from .native_producer import require_drained
        require_drained(tx,integration_run)
        for receipt in proof['checks'].values():
            _receipt(tx,receipt)
        state = workspace(integration['payload'],clean=True)
        if state['commit'] != proof['commit']:
            raise Conflict('integration source moved after completion')
        source_proofs = [provider]
        for output in task['payload']['outputs']:
            if output['kind'] in {'source','contract'}:
                source_proofs.append(_output(tx,{'task_id':task_id,'output_id':output['id'],'version':output['version']}))
        for source in source_proofs:
            if _entries(integration['payload']['workdir'],proof['commit'],list(source['files'])) != source['files']:
                raise Conflict('integration must install exact completed consumer and real-provider source versions')
        identity = producer+'/'+output_id
        result = milestone(tx,task,'integration_verified',identity,{'integration_run':integration_run,'commit':proof['commit'],
            'contract_hash':digest(contract),'provider_hash':digest(provider),'consumer_hash':digest(json.loads(completed[0])),
            'checks':proof['checks']})
        if summary(tx,task)['deployable']:
            milestone(tx,task,'deployable','packet',{'integration_run':integration_run,'commit':proof['commit'],
                'integration_obligations':sorted(need['task_id']+'/'+need['output_id'] for need in task['payload']['prerequisites'] if need.get('need','hard')!='hard')})
        # Covered hard consumers also become eligible when the upstream binding
        # discharges, in any applied plan. Emit that transition once; historical
        # rows remain immutable.
        downstream = tx.db.execute("""WITH RECURSIVE consumers(id) AS (
            SELECT ? UNION SELECT p.task_id FROM plan_prerequisites p JOIN task_heads h ON h.task_id=p.task_id AND h.revision=p.task_revision JOIN consumers c ON p.producer=c.id
            ) SELECT id FROM consumers ORDER BY id""", (task_id,)).fetchall()
        for candidate in downstream:
            other = effective_task(tx,tx.task(candidate[0]))
            if other['task_id'] == task_id or not other['payload'].get('dependency_packet'):
                continue
            exists = tx.db.execute("SELECT 1 FROM unit_milestones WHERE task_id=? AND task_revision=? AND kind='deployable'", (other['task_id'],other['revision'])).fetchone()
            if not exists and summary(tx,other)['deployable']:
                completion = json.loads(tx.db.execute('SELECT payload FROM unit_completions WHERE task_id=? AND task_revision=?', (other['task_id'],other['revision'])).fetchone()[0])
                milestone(tx,other,'deployable','packet',{'run_id':completion['run_id'],'commit':completion['commit'],'upstream_binding_verified':task_id})
        return result
