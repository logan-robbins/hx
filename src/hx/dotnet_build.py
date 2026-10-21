"""Durable .NET build broker with evaluated closure and immutable artifacts."""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import shutil
import time
import uuid

from . import checks, dotnet_artifacts, dotnet_graph, dotnet_slots, fingerprints, native_processes
from .continuity_store import Conflict, ContinuityStore, _id, canonical, digest
from .errors import ValidationError

SCHEMA = """
CREATE TABLE IF NOT EXISTS dotnet_jobs (
 job_id TEXT PRIMARY KEY, kind TEXT NOT NULL, run_id TEXT REFERENCES runs(run_id),
 scope TEXT NOT NULL, request_id TEXT NOT NULL, request_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','running','finished','abandoned')),
 payload TEXT NOT NULL, payload_hash TEXT NOT NULL, UNIQUE(scope,request_id));
CREATE TABLE IF NOT EXISTS dotnet_claims (seed TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES dotnet_jobs(job_id));
CREATE TABLE IF NOT EXISTS dotnet_build_artifacts (
 build_id TEXT PRIMARY KEY,key_hash TEXT NOT NULL,artifact_hash TEXT NOT NULL REFERENCES artifacts(hash),
 payload TEXT NOT NULL,payload_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dotnet_cache (key_hash TEXT PRIMARY KEY,build_id TEXT NOT NULL REFERENCES dotnet_build_artifacts(build_id));
CREATE TABLE IF NOT EXISTS dotnet_restore_cache (seed TEXT PRIMARY KEY,payload TEXT NOT NULL,payload_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS dotnet_receipts (
 receipt_id TEXT PRIMARY KEY,job_id TEXT NOT NULL REFERENCES dotnet_jobs(job_id),
 run_id TEXT NOT NULL REFERENCES runs(run_id),request_id TEXT NOT NULL,event_id TEXT NOT NULL,
 artifact_hash TEXT REFERENCES artifacts(hash),payload TEXT NOT NULL,valid INTEGER NOT NULL CHECK(valid IN (0,1)));
"""


def initialize(db):
    db = getattr(db, 'db', db)
    for statement in SCHEMA.split(';'):
        if statement.strip():
            db.execute(statement)


def require_drained(tx, run_id):
    """Reject completion while a requested broker execution is unresolved."""
    row = tx.db.execute("SELECT job_id,status,kind FROM dotnet_jobs WHERE run_id=? AND status IN ('queued','running') ORDER BY rowid LIMIT 1",
                         (run_id,)).fetchone()
    if row:
        raise Conflict(f"managed .NET {row['kind']} job {row['job_id']} is {row['status']}; finish or reconcile it before completion")


def managed_environment(store, env):
    child = dict(os.environ if env is None else env)
    state = store.root / 'state' / 'dotnet'
    (state / 'cli-home').mkdir(parents=True, exist_ok=True)
    (state / 'packages').mkdir(parents=True, exist_ok=True)
    child.update(DOTNET_CLI_HOME=str(state / 'cli-home'), NUGET_PACKAGES=str(state / 'packages'),
                 DOTNET_CLI_TELEMETRY_OPTOUT='1', DOTNET_GENERATE_ASPNET_CERTIFICATE='false',
                 DOTNET_SKIP_FIRST_TIME_EXPERIENCE='1', DOTNET_NOLOGO='1',
                 DOTNET_CLI_USE_MSBUILD_SERVER='0', MSBUILDDISABLENODEREUSE='1')
    return child


def _owner(tx, run_id, worker_id, cwd, project=None):
    if run_id is None:
        return None
    run = tx.db.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone()
    if run is None or worker_id is not None and run['worker_id'] != worker_id:
        raise Conflict('build caller does not own this run')
    task = tx.task(run['task_id'])
    if run['ended_at'] is not None or task['revision'] != run['task_revision']:
        raise Conflict('build requires an active run at its assigned task revision')
    from .dependency_packets import effective_task, require_bound
    task = effective_task(tx, task)
    require_bound(task)
    workdir = task['payload'].get('workdir')
    if workdir is None or not Path(cwd).resolve().is_relative_to(Path(workdir).resolve()):
        raise Conflict('build cwd is outside its assigned task workdir')
    if project is not None and not Path(project).resolve().is_relative_to(Path(workdir).resolve()):
        raise Conflict('build entry point is outside its assigned task workdir')
    return run['task_revision']


def _write(tx, job_id, payload, status=None):
    tx._change()
    if status is None:
        tx.db.execute('UPDATE dotnet_jobs SET payload=?,payload_hash=? WHERE job_id=?',
                      (canonical(payload), digest(payload), job_id))
    else:
        tx.db.execute('UPDATE dotnet_jobs SET status=?,payload=?,payload_hash=? WHERE job_id=?',
                      (status, canonical(payload), digest(payload), job_id))


def _job(store, job_id):
    row = store.db.execute('SELECT * FROM dotnet_jobs WHERE job_id=?', (job_id,)).fetchone()
    if row is None:
        raise ValidationError('build job is absent')
    try:
        payload = json.loads(row['payload'])
    except ValueError as exc:
        raise Conflict('build job metadata is corrupt') from exc
    if digest(payload) != row['payload_hash']:
        raise Conflict('build job metadata failed integrity validation')
    return row, payload


def status(store, job_id):
    row, payload = _job(store, job_id)
    result = payload.get('result')
    if result and result.get('valid') and result.get('build_id'):
        artifact = artifact_metadata(store, result['build_id'])
        if not dotnet_artifacts.intact(store, artifact):
            result = {**result, 'valid': False, 'reasons': [*result['reasons'], 'build_artifact_missing_or_corrupt']}
    return {'job_id': job_id, 'kind': row['kind'], 'status': row['status'], 'run_id': row['run_id'],
            'request_id': row['request_id'], 'phase': payload.get('phase'), 'result': result,
            'owner': payload.get('owner'), 'children': payload.get('children', []),
            'commands': payload.get('commands', [])}


def artifact_metadata(store, build_id):
    row = store.db.execute('SELECT * FROM dotnet_build_artifacts WHERE build_id=?', (build_id,)).fetchone()
    if row is None:
        raise ValidationError('build artifact metadata is absent')
    value = json.loads(row['payload'])
    if digest(value) != row['payload_hash'] or value.get('artifact_hash') != row['artifact_hash']:
        raise Conflict('build artifact metadata failed integrity validation')
    return value


def _location(project, cwd):
    if cwd is None:
        entry = Path(project).resolve()
        return str(entry), entry.parent
    folder = Path(cwd).resolve()
    return str((folder / project).resolve()), folder


def graph(store, project, *, cwd=None, env=None, **options):
    child = managed_environment(store, env)
    project, cwd = _location(project, cwd)
    options.setdefault('support_store', store)
    return dotnet_graph.evaluate(project, cwd, child, **options)


def _request(store, kind, run_id, request_id, recipe, cwd, worker_id):
    request_id = request_id or str(uuid.uuid4())
    _id(request_id)
    scope = run_id or 'unbound'
    request_hash = digest(recipe)
    with store.transaction() as tx:
        initialize(tx)
        row = tx.db.execute('SELECT * FROM dotnet_jobs WHERE scope=? AND request_id=?', (scope, request_id)).fetchone()
        if row:
            if worker_id is not None and run_id is not None:
                owner = tx.db.execute('SELECT worker_id FROM runs WHERE run_id=?', (run_id,)).fetchone()
                if owner is None or owner[0] != worker_id:
                    raise Conflict('build caller does not own this run')
            if row['request_hash'] != request_hash or row['kind'] != kind:
                raise Conflict('build request ID belongs to different execution options')
            _, payload = _job(store, row['job_id'])
            if row['status'] == 'finished':
                return row['job_id'], payload, status(store, row['job_id'])['result']
            if row['status'] != 'queued':
                raise Conflict('build request has an unresolved execution; inspect/reconcile its owner')
            _owner(tx, run_id, worker_id, cwd, recipe.get('project'))
            return row['job_id'], payload, None
        revision = _owner(tx, run_id, worker_id, cwd, recipe.get('project'))
        job_id = str(uuid.uuid4())
        payload = {'schema_version': 1, 'recipe': recipe, 'phase': 'queued', 'children': [],
                   'task_revision': revision, 'started_at': time.time(), 'owner': None,
                   'host_slot': str(store.root.resolve()) + '/' + job_id}
        tx._change()
        tx.db.execute('INSERT INTO dotnet_jobs VALUES(?,?,?,?,?,?,\'queued\',?,?)',
                      (job_id, kind, run_id, scope, request_id, request_hash, canonical(payload), digest(payload)))
    return job_id, payload, None


def _spawn(store, job_id, process):
    from .check_artifacts import owned_child
    observed = owned_child(process)
    row, payload = _job(store, job_id)
    dotnet_slots.child(payload['host_slot'], observed)
    with store.transaction() as tx:
        _, payload = _job(store, job_id)
        payload['children'].append(observed)
        payload['spawn_pending'] = False
        _write(tx, job_id, payload)
    return observed


def _begin_preparation(store,job_id):
    owner=native_processes.probe(os.getpid())
    if owner is None:
        raise Conflict('build preparation owner identity is unavailable')
    with store.transaction() as tx:
        row,payload=_job(store,job_id)
        if row['status']!='queued' or payload['phase']!='queued':
            raise Conflict('build preparation is already owned or the request was reconciled')
        payload.update(owner=owner['identity'],phase='evaluating',preparation_pending=True)
        _write(tx,job_id,payload)


def _end_preparation(store,job_id):
    with store.transaction() as tx:
        row,payload=_job(store,job_id)
        if row['status']!='queued' or payload['phase']!='evaluating':
            raise Conflict('build preparation was reconciled while evaluation was active')
        payload.update(phase='evaluated',preparation_pending=False)
        _write(tx,job_id,payload)


def _execute(store, job_id, argv, cwd, env, phase, timeout_s, max_bytes):
    directory = store.root / 'state' / 'dotnet' / 'requests' / job_id
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f'{phase}-{uuid.uuid4()}.log'
    with store.transaction() as tx:
        _, payload = _job(store, job_id)
        payload['phase'] = phase
        # A crash between spawn and identity capture must stay unresolved.
        payload['spawn_pending'] = True
        _write(tx, job_id, payload)
    from . import credential_mask
    observation = checks.execute(argv, cwd, env, output, timeout_s, max_bytes,
                                 mask=credential_mask.for_root(store.root), on_spawn=lambda process: _spawn(store, job_id, process))
    with store.transaction() as tx:
        _, payload = _job(store, job_id)
        payload['spawn_pending'] = False
        artifact = tx.put_artifact_file(output, owner_type='dotnet_job', owner_id=job_id,
                                        slot=phase, max_bytes=max_bytes)
        payload.setdefault('commands', []).append({'phase': phase, 'argv': argv, 'execution': observation, 'output_hash': artifact})
        _write(tx, job_id, payload)
    output.unlink()
    return observation


def _admit(store, job_id, seed, jobs, run_id, worker_id, cwd):
    with store.transaction() as tx:
        _owner(tx, run_id, worker_id, cwd)
        row,payload=_job(store,job_id)
        if row['status']!='queued':
            raise Conflict('build admission cannot revive a reconciled or active request')
        active = tx.db.execute('SELECT j.status FROM dotnet_claims c JOIN dotnet_jobs j USING(job_id) WHERE c.seed=?', (seed,)).fetchone()
        if active and active[0] == 'running':
            raise Conflict('equal build inputs already have an unresolved job; no duplicate build was started')
    slot = str(store.root.resolve()) + '/' + job_id
    dotnet_slots.acquire(slot, jobs)
    try:
        with store.transaction() as tx:
            _owner(tx, run_id, worker_id, cwd)
            active = tx.db.execute('SELECT j.status FROM dotnet_claims c JOIN dotnet_jobs j USING(job_id) WHERE c.seed=?', (seed,)).fetchone()
            if active and active[0] == 'running':
                raise Conflict('equal build inputs already have an unresolved job; no duplicate build was started')
            row, payload = _job(store, job_id)
            if row['status']!='queued':
                raise Conflict('build admission cannot revive a reconciled or active request')
            payload.update(seed=seed, host_slot=slot, owner=native_processes.probe(os.getpid())['identity'], phase='admitted')
            _write(tx, job_id, payload, 'running')
            tx.db.execute('INSERT INTO dotnet_claims VALUES(?,?) ON CONFLICT(seed) DO UPDATE SET job_id=excluded.job_id', (seed, job_id))
    except BaseException:
        dotnet_slots.release(slot)
        raise


def _finish(store, job_id, result, run_id, worker_id, cwd, *, live_graph=None, env=None):
    with store.transaction() as tx:
        row, payload = _job(store, job_id)
        if row['status'] not in ('queued','running'):
            raise Conflict('build completion cannot revive a reconciled or finished request')
        try:
            _owner(tx, run_id, worker_id, cwd)
        except Conflict:
            result = {**result, 'valid': False, 'reasons': [*result['reasons'], 'run_assignment_changed_during_job']}
        if result['valid'] and result.get('build_id'):
            metadata = artifact_metadata(store, result['build_id'])
            if not metadata['complete'] or not dotnet_artifacts.intact(store, metadata):
                result = {**result, 'valid': False, 'reasons': [*result['reasons'], 'build_artifact_missing_or_corrupt']}
        if result['valid'] and live_graph is not None:
            source, environment = dotnet_graph.observe(live_graph, env)
            if not source['complete'] or not environment['complete'] or not _same(live_graph['source'], source) or not _same(live_graph['environment'], environment):
                result = {**result, 'valid': False, 'reasons': [*result['reasons'], 'inputs_changed_at_publication']}
        if run_id:
            receipt_id = str(uuid.uuid4())
            result = {**result, 'receipt_id': receipt_id}
            event = tx.append_event(run_id, 'builds', f'dotnet:{job_id}', 'check_result', result)
            result['event_id'] = event['event_id']
            tx.db.execute('INSERT INTO dotnet_receipts VALUES(?,?,?,?,?,?,?,?)',
                          (receipt_id, job_id, run_id, row['request_id'], event['event_id'], result.get('artifact_hash'), canonical(result), int(result['valid'])))
            if result.get('artifact_hash'):
                tx.db.execute('INSERT INTO artifact_refs VALUES(?,?,?,?)', ('event', event['event_id'], 'build', result['artifact_hash']))
            cursor = tx.db.execute("SELECT revision FROM cursors WHERE run_id=? AND stream_id='builds'", (run_id,)).fetchone()
            tx.classify(run_id, 'builds', expected_revision=cursor[0], through=event['seq'], dispositions={event['seq']: 'reduced'})
        payload.update(result=result, phase='finished', completed_at=time.time())
        _write(tx, job_id, payload, 'finished')
        tx.db.execute('DELETE FROM dotnet_claims WHERE job_id=?', (job_id,))
    if payload.get('host_slot'):
        dotnet_slots.release(payload['host_slot'])
    return result


def _same(before, after):
    return all(before[k] == after[k] for k in ('hash', 'observation_hash'))


def _limits(jobs, timeout_s, max_output_bytes):
    if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not 0 < timeout_s <= 3600 or type(max_output_bytes) is not int or not 1 <= max_output_bytes <= 64*1024*1024:
        raise ValidationError('build/test needs a timeout up to one hour and output bound up to 64 MiB')
    if jobs is not None and (type(jobs) is not int or not 1 <= jobs <= 64):
        raise ValidationError('host build concurrency must be 1 through 64')


def build(store, project, *, cwd=None, env=None, configuration='Debug', framework=None, runtime=None,
          source_contract=None, dotnet='dotnet', platform=None, run_id=None, request_id=None, worker_id=None,
          jobs=None, timeout_s=900, max_output_bytes=8*1024*1024, fresh=False):
    project, cwd = _location(project, cwd)
    _limits(jobs, timeout_s, max_output_bytes)
    dotnet_graph.properties(configuration, framework, runtime, platform=platform)
    if source_contract is not None:
        checks.validate({'schema_version': 1, 'id': 'closure', 'argv': [dotnet], 'cwd': '.', 'inputs': [],
            'source': source_contract, 'environment': {'complete': True, 'executables': [], 'inputs': [], 'external_versions': {}},
            'timeout_s': timeout_s, 'max_output_bytes': max_output_bytes})
    child = managed_environment(store, env)
    recipe = {'project': project, 'cwd': str(cwd), 'configuration': configuration, 'framework': framework,
              'runtime': runtime, 'platform': platform, 'source_contract': source_contract, 'dotnet': dotnet, 'environment_hash': digest(child),
              'timeout_s': timeout_s, 'max_output_bytes': max_output_bytes, 'fresh': fresh}
    job_id, payload, replay = _request(store, 'build', run_id, request_id, recipe, cwd, worker_id)
    if replay is not None:
        return replay
    _begin_preparation(store,job_id)
    options = dict(dotnet=dotnet, configuration=configuration, framework=framework, runtime=runtime, source_contract=source_contract, platform=platform, support_store=store)
    initial = dotnet_graph.evaluate(project, cwd, child, **options)
    if not initial['nodes'] or not initial['sdk']['query'] or not initial['sdk']['graph_build']:
        _end_preparation(store,job_id)
        return _finish(store, job_id, {'job_id': job_id, 'build_id': None, 'valid': False, 'success': False,
            'reused': False, 'reasons': initial['reasons'], 'graph': initial}, run_id, worker_id, cwd)
    # The seed includes the evaluated source readset even when restore state is
    # missing. Equal seeds serialize the shared deterministic execution paths.
    seed = digest({'recipe': {k:v for k,v in recipe.items() if k != 'fresh'},
                   'source': initial['source']['hash'], 'environment': initial['environment']['hash']})
    tree = store.root / 'state' / 'dotnet' / 'jobs' / seed / 'artifacts'
    current = dotnet_graph.evaluate(project, cwd, child, artifacts_path=tree, sdk=initial['sdk'], **options)
    _end_preparation(store,job_id)
    isolation = [reason for reason in current['reasons'] if 'isolated SDK' in reason or 'collide in SDK artifacts' in reason or 'does not use SDK artifacts' in reason]
    if isolation:
        return _finish(store, job_id, {'job_id': job_id, 'build_id': None, 'valid': False,
            'success': False, 'reused': False, 'reasons': isolation}, run_id, worker_id, cwd)
    with store.transaction() as tx:
        _owner(tx, run_id, worker_id, cwd)
        claim = tx.db.execute('SELECT j.status FROM dotnet_claims c JOIN dotnet_jobs j USING(job_id) WHERE c.seed=?', (seed,)).fetchone()
        if claim and claim[0] == 'running':
            raise Conflict('equal build inputs already have an unresolved job; no duplicate build was started')
        cached = tx.db.execute('SELECT build_id FROM dotnet_cache WHERE key_hash=?', (current['hash'],)).fetchone()
        if not fresh and current['complete'] and cached:
            artifact = artifact_metadata(store, cached[0])
            if dotnet_artifacts.intact(store, artifact):
                result = {'job_id': job_id, 'build_id': artifact['build_id'], 'artifact_hash': artifact['artifact_hash'],
                          'valid': True, 'success': True, 'reused': True, 'reasons': [], 'graph_hash': current['hash']}
            else:
                result = None
        else:
            result = None
    if result:
        return _finish(store, job_id, result, run_id, worker_id, cwd, live_graph=current, env=child)
    try:
        _admit(store, job_id, seed, jobs, run_id, worker_id, cwd)
    except Conflict:
        # A known admission refusal starts no product process. Preserve request
        # replay after host capacity becomes available; do not reset live jobs
        # or an evaluation whose child ownership is still unknown.
        with store.transaction() as tx:
            row,payload=_job(store,job_id)
            if row['status']=='queued' and payload['phase']=='evaluated' and not payload.get('preparation_pending'):
                payload.update(phase='queued',owner=None)
                _write(tx,job_id,payload)
        raise
    with store.transaction() as tx:
        _, payload = _job(store, job_id)
        payload.update(artifact_tree=str(tree), seed_graph_hash=initial['hash'])
        _write(tx, job_id, payload)
        saved_restore = tx.db.execute('SELECT payload,payload_hash FROM dotnet_restore_cache WHERE seed=?', (seed,)).fetchone()
    restore = json.loads(saved_restore[0]) if saved_restore and digest(json.loads(saved_restore[0])) == saved_restore[1] else None
    if restore and current['complete'] and current['hash'] == restore['graph_hash'] and dotnet_artifacts.intact(store, restore):
        shutil.rmtree(tree, ignore_errors=True)
        dotnet_artifacts.materialize(store, restore, tree)
        restored = dotnet_graph.evaluate(project, cwd, child, artifacts_path=tree, sdk=initial['sdk'], **options)
    else:
        shutil.rmtree(tree, ignore_errors=True)
        tree.mkdir(parents=True, exist_ok=True)
        command = [current['resolved_dotnet'], 'msbuild', project, '-nologo', '-noAutoResponse', '-nodeReuse:false',
                   '-maxCpuCount:1', '-graphBuild', '-target:Restore', *dotnet_graph.properties(configuration, framework, runtime, str(tree), child['NUGET_PACKAGES'], platform)]
        observed = _execute(store, job_id, command, cwd, child, 'restore', timeout_s, max_output_bytes)
        if observed['exit_code'] or observed['reason']:
            return _finish(store, job_id, {'job_id': job_id, 'build_id': None, 'valid': False, 'success': False,
                'reused': False, 'reasons': ['restore_failed', *([observed['reason']] if observed['reason'] else [])],
                'exit_code': observed['exit_code']}, run_id, worker_id, cwd)
        restored = dotnet_graph.evaluate(project, cwd, child, artifacts_path=tree, sdk=initial['sdk'], **options)
        restored_source, restored_environment = dotnet_graph.observe(initial, child)
        restore_stable = _same(initial['source'], restored_source) and _same(initial['environment'], restored_environment)
        if restored['complete'] and restore_stable:
            archive = tree.parent / f'{job_id}.restore.tar'
            try:
                with store.transaction() as tx:
                    sha, manifest = dotnet_artifacts.capture(tx, tree, job_id + '.restore', archive)
                    restore = {'build_id': job_id + '.restore', 'artifact_hash': sha, 'manifest': manifest,
                               'manifest_hash': digest(manifest), 'graph_hash': restored['hash']}
                    tx.db.execute('INSERT INTO dotnet_restore_cache VALUES(?,?,?) ON CONFLICT(seed) DO UPDATE SET payload=excluded.payload,payload_hash=excluded.payload_hash',
                                  (seed, canonical(restore), digest(restore)))
            except (ValidationError, OSError) as exc:
                archive.unlink(missing_ok=True)
                return _finish(store, job_id, {'job_id': job_id, 'build_id': None, 'valid': False, 'success': False,
                    'reused': False, 'reasons': ['restore_artifact_capture_failed', str(exc)]}, run_id, worker_id, cwd)
            archive.unlink(missing_ok=True)
    source_after_restore, environment_after_restore = dotnet_graph.observe(initial, child)
    restore_changed = not _same(initial['source'], source_after_restore) or not _same(initial['environment'], environment_after_restore)
    command = [current['resolved_dotnet'], 'msbuild', project, '-nologo', '-noAutoResponse', '-nodeReuse:false',
               '-maxCpuCount:1', '-graphBuild', '-target:Build', *dotnet_graph.properties(configuration, framework, runtime, str(tree), child['NUGET_PACKAGES'], platform)]
    observed = _execute(store, job_id, command, cwd, child, 'build', timeout_s, max_output_bytes)
    after = dotnet_graph.evaluate(project, cwd, child, artifacts_path=tree, sdk=initial['sdk'], **options)
    reasons = []
    if restore_changed:
        reasons.append('inputs_changed_during_restore')
    if observed['exit_code'] or observed['reason']:
        reasons.append('build_failed')
        if observed['reason']:
            reasons.append(observed['reason'])
    if not restored['complete'] or not after['complete']:
        reasons.extend(['incomplete_evaluated_closure', *restored['reasons'], *after['reasons']])
    if not _same(restored['source'], after['source']) or not _same(restored['environment'], after['environment']) or restored['hash'] != after['hash']:
        reasons.append('inputs_changed_during_build')
    modules = []
    for node in after['nodes']:
        target = node['properties']['TargetPath']
        if target and node['framework']:
            target = Path(target).resolve()
            if not target.is_relative_to(tree.resolve()) or not target.is_file():
                reasons.append('evaluated_target_output_missing_or_unisolated')
            elif node['properties']['IsTestProject'].lower() == 'true' or node['properties']['IsTestingPlatformApplication'].lower() == 'true':
                modules.append({'project': node['project'], 'framework': node['framework'], 'target': target.relative_to(tree).as_posix(),
                                'mtp': node['properties']['IsTestingPlatformApplication'].lower() == 'true',
                                'mtp_compat': node['properties']['TestingPlatformDotnetTestSupport'].lower() == 'true'})
    result = {'job_id': job_id, 'build_id': None, 'valid': not reasons, 'success': observed['exit_code'] == 0 and not observed['reason'],
              'reused': False, 'reasons': sorted(set(reasons)), 'exit_code': observed['exit_code'], 'graph_hash': after['hash']}
    if result['success']:
        archive = tree.parent / f'{job_id}.build.tar'
        try:
            with store.transaction() as tx:
                sha, manifest = dotnet_artifacts.capture(tx, tree, job_id, archive)
                artifact = {'schema_version': 1, 'build_id': job_id, 'artifact_hash': sha, 'manifest': manifest,
                            'manifest_hash': digest(manifest), 'recipe': recipe, 'graph': after,
                            'graph_hash': after['hash'], 'modules': modules, 'complete': not reasons, 'tree': str(tree)}
                tx.db.execute('INSERT INTO dotnet_build_artifacts VALUES(?,?,?,?,?)', (job_id, after['hash'], sha, canonical(artifact), digest(artifact)))
                if not reasons:
                    tx.db.execute('INSERT INTO dotnet_cache VALUES(?,?) ON CONFLICT(key_hash) DO UPDATE SET build_id=excluded.build_id', (after['hash'], job_id))
                result.update(build_id=job_id, artifact_hash=sha)
        except (ValidationError, OSError) as exc:
            result.update(valid=False, build_id=None, reasons=[*result['reasons'], 'build_artifact_capture_failed', str(exc)])
        archive.unlink(missing_ok=True)
    return _finish(store, job_id, result, run_id, worker_id, cwd, live_graph=after, env=child)


def materialize(store, build_id, destination):
    return dotnet_artifacts.materialize(store, artifact_metadata(store, build_id), destination)


def current(store, run_id, receipt_id, *, env=None, worker_id=None):
    """Revalidate a run-bound broker receipt before accepting build/test proof.

    Callers must also admit/drain managed jobs at their final completion boundary.
    This API never treats an unknown solution mapping as a completed proof.
    """
    row = store.db.execute('SELECT * FROM dotnet_receipts WHERE receipt_id=? AND run_id=?', (receipt_id, run_id)).fetchone()
    if row is None:
        raise ValidationError('build receipt is absent or belongs to another run')
    job, payload = _job(store, row['job_id'])
    result = json.loads(row['payload'])
    reasons = []
    if not row['valid'] or job['status'] != 'finished' or not result.get('valid'):
        reasons.append('build_receipt_not_valid')
    if result != payload.get('result') or row['artifact_hash'] != result.get('artifact_hash'):
        reasons.append('build_receipt_provenance_corrupt')
    build_id = result.get('build_id')
    if not build_id:
        return {'receipt_id': receipt_id, 'current': False, 'reasons': [*reasons, 'build_artifact_absent']}
    artifact = artifact_metadata(store, build_id)
    recipe = artifact['recipe']
    cwd = Path(recipe['cwd'])
    with store.transaction() as tx:
        revision = _owner(tx, run_id, worker_id, cwd)
        if revision != payload['task_revision']:
            reasons.append('run_assignment_changed')
    child = managed_environment(store, env)
    if digest(child) != recipe['environment_hash']:
        reasons.append('build_environment_changed')
    evaluated = dotnet_graph.evaluate(recipe['project'], cwd, child, dotnet=recipe['dotnet'], configuration=recipe['configuration'],
        framework=recipe['framework'], runtime=recipe['runtime'], platform=recipe.get('platform'), support_store=store, source_contract=recipe['source_contract'], artifacts_path=Path(artifact['tree']))
    if not artifact['complete'] or not evaluated['complete'] or evaluated['hash'] != artifact['graph_hash']:
        reasons.append('build_inputs_changed_or_unknown')
    if not dotnet_artifacts.intact(store, artifact):
        reasons.append('build_artifact_missing_or_corrupt')
    with store.transaction() as tx:
        _owner(tx, run_id, worker_id, cwd)
        fresh = tx.db.execute('SELECT valid FROM dotnet_receipts WHERE receipt_id=?', (receipt_id,)).fetchone()
        if not fresh or not fresh[0]:
            reasons.append('build_receipt_invalidated')
        if evaluated.get('source') and evaluated.get('environment'):
            source, environment = dotnet_graph.observe(evaluated, child)
            if not _same(evaluated['source'], source) or not _same(evaluated['environment'], environment):
                reasons.append('inputs_changed_during_revalidation')
        if not dotnet_artifacts.intact(store, artifact_metadata(store, build_id)):
            reasons.append('build_artifact_missing_or_corrupt')
    return {'receipt_id': receipt_id, 'current': not reasons, 'reasons': sorted(set(reasons)),
            'build_id': build_id, 'graph_hash': evaluated['hash']}


def test(store, build_id, *, request_id=None, run_id=None, worker_id=None, env=None, project=None, framework=None,
         filter_expression=None, runner_args=None, jobs=None, timeout_s=900, max_output_bytes=8*1024*1024):
    _limits(jobs, timeout_s, max_output_bytes)
    artifact = artifact_metadata(store, build_id)
    if not artifact['complete'] or not dotnet_artifacts.intact(store, artifact):
        raise Conflict('test requires a complete intact immutable build artifact')
    recipe = artifact['recipe']
    cwd = Path(recipe['cwd'])
    child = managed_environment(store, env)
    if digest(child) != recipe['environment_hash']:
        raise Conflict('test execution environment differs from its build artifact')
    modules = [module for module in artifact['modules'] if (project is None or module['project'] == str((cwd / project).resolve()))
               and (framework is None or module['framework'] == framework)]
    if not modules:
        raise ValidationError('artifact contains no matching evaluated test modules')
    runner_args = runner_args or []
    if not isinstance(runner_args, list) or len(runner_args) > 128 or any(not isinstance(v, str) or '\0' in v for v in runner_args):
        raise ValidationError('test runner arguments must be a bounded string array')
    requested = {'build_id': build_id, 'modules': modules, 'filter': filter_expression, 'runner_args': runner_args,
                 'environment_hash': digest(child), 'timeout_s': timeout_s, 'max_output_bytes': max_output_bytes}
    job_id, payload, replay = _request(store, 'test', run_id, request_id, requested, cwd, worker_id)
    if replay is not None:
        return replay
    help_result, help_text = dotnet_graph.invoke([artifact['graph']['resolved_dotnet'], 'test', '--help'], cwd, child)
    mtp_runner = '--test-modules' in help_text
    if help_result['exit_code'] or '--no-build' not in help_text:
        raise Conflict('test runner does not support the required exact-artifact invocation')
    if mtp_runner and any(not module['mtp'] for module in modules):
        raise Conflict('MTP runner cannot execute VSTest artifacts')
    if not mtp_runner and any(module['mtp'] or module['mtp_compat'] for module in modules):
        raise Conflict('MTP artifact requires its matching native test runner; VSTest compatibility is unsupported')
    if mtp_runner and filter_expression is not None:
        raise ValidationError('MTP filters are runner-specific; pass native runner arguments')
    current = dotnet_graph.evaluate(recipe['project'], cwd, child, dotnet=recipe['dotnet'], configuration=recipe['configuration'],
        framework=recipe['framework'], runtime=recipe['runtime'], platform=recipe.get('platform'), support_store=store, source_contract=recipe['source_contract'], artifacts_path=Path(artifact['tree']))
    if not current['complete'] or current['hash'] != artifact['graph_hash']:
        raise Conflict('build source, SDK, package state, configuration, or runner identity changed')
    seed = 'test:' + digest(requested)
    _admit(store, job_id, seed, jobs, run_id, worker_id, cwd)
    destination = store.root / 'state' / 'dotnet' / 'shards' / job_id / 'artifacts'
    dotnet_artifacts.materialize(store, artifact, destination)
    before = dotnet_artifacts.files(destination)
    outcomes = []
    for index, module in enumerate(modules):
        target = str(destination / module['target'])
        command = [artifact['graph']['resolved_dotnet'], 'test']
        command.extend(['--test-modules', target] if mtp_runner else [target])
        command.append('--no-build')
        results = destination.parent / f'results-{index}'
        command.extend(['--results-directory', str(results)])
        if not mtp_runner:
            command.extend(['--logger', 'trx'])
            if filter_expression is not None:
                command.extend(['--filter', filter_expression])
        if runner_args:
            command.extend(['--', *runner_args])
        outcomes.append(_execute(store, job_id, command, cwd, child, f'test-{index}', timeout_s, max_output_bytes))
    reasons = []
    if any(value['exit_code'] or value['reason'] for value in outcomes):
        reasons.append('test_failed')
    if before != dotnet_artifacts.files(destination):
        reasons.append('test_modified_build_artifact')
    after = dotnet_graph.evaluate(recipe['project'], cwd, child, dotnet=recipe['dotnet'], configuration=recipe['configuration'],
        framework=recipe['framework'], runtime=recipe['runtime'], platform=recipe.get('platform'), support_store=store, source_contract=recipe['source_contract'], artifacts_path=Path(artifact['tree']), sdk=current['sdk'])
    if not _same(current['source'], after['source']) or not _same(current['environment'], after['environment']) or current['hash'] != after['hash']:
        reasons.append('inputs_changed_during_test')
    result = {'job_id': job_id, 'build_id': build_id, 'artifact_hash': artifact['artifact_hash'], 'valid': not reasons,
              'success': not reasons, 'reused': False, 'reasons': reasons, 'modules': modules,
              'configuration': recipe['configuration'], 'runtime': recipe['runtime'], 'runner': 'MTP' if mtp_runner else 'VSTest'}
    return _finish(store, job_id, result, run_id, worker_id, cwd, live_graph=after, env=child)


def reconcile(store, job_id):
    with store.transaction() as tx:
        row, payload = _job(store, job_id)
        if row['status'] not in ('running', 'queued', 'finished'):
            raise Conflict('job is already resolved')
        slot = dotnet_slots.metadata(payload['host_slot']) if payload.get('host_slot') else None
        owner = payload.get('owner') or (slot['owner'] if slot else None)
        if row['status'] != 'finished' and owner and native_processes.current(owner) is not None:
            raise Conflict('build job owner is still alive')
        if payload.get('preparation_pending'):
            raise Conflict('build graph preparation spawn ownership is unknown; quiescence cannot be established')
        children = slot['children'] if slot and len(slot['children']) >= len(payload.get('children', [])) else payload.get('children', [])
        if payload.get('spawn_pending') and len(children) <= len(payload.get('children', [])):
            raise Conflict('spawn identity capture is incomplete; process quiescence is unknown')
        for child in children:
            if child['boot'] != native_processes.boot():
                continue
            if child['identity'] and native_processes.current(child['identity']) is not None:
                raise Conflict('build child is still alive')
            from .check_artifacts import group_present
            if group_present(child['group']):
                raise Conflict('build process group is still present')
        payload.update(phase='finished' if row['status'] == 'finished' else 'abandoned', reconciled_at=time.time())
        _write(tx, job_id, payload, 'finished' if row['status'] == 'finished' else 'abandoned')
        tx.db.execute('DELETE FROM dotnet_claims WHERE job_id=?', (job_id,))
    if payload.get('host_slot'):
        dotnet_slots.release(payload['host_slot'])
    return {'job_id': job_id, 'status': 'finished' if row['status'] == 'finished' else 'abandoned', 'reconciled': True}


def main(argv, root, *, env=None):
    parser = argparse.ArgumentParser(prog='hx build', description='Evaluate, build, and test immutable .NET artifacts with isolated writable state.',
                                     epilog='example: hx build build src/App/App.csproj --configuration Release --request compile-1')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('graph', 'build'):
        item = commands.add_parser(name, epilog=f'example: hx build {name} src/App/App.csproj --configuration Release')
        item.add_argument('project')
        item.add_argument('--cwd')
        item.add_argument('--configuration', default='Debug')
        item.add_argument('--framework')
        item.add_argument('--runtime')
        item.add_argument('--platform')
        item.add_argument('--source-contract', help='JSON file declaring complete source inputs/dependencies')
        item.add_argument('--dotnet', default='dotnet')
        item.add_argument('--root')
        if name == 'build':
            item.add_argument('--run')
            item.add_argument('--request')
            item.add_argument('--jobs', type=int)
            item.add_argument('--timeout', type=float, default=900)
            item.add_argument('--max-output-bytes', type=int, default=8*1024*1024)
            item.add_argument('--fresh', action='store_true')
    item = commands.add_parser('test', epilog='example: hx build test <build-id> --filter "Category=Unit" --request shard-1')
    item.add_argument('build_id')
    item.add_argument('--run')
    item.add_argument('--request')
    item.add_argument('--project')
    item.add_argument('--framework')
    item.add_argument('--filter')
    item.add_argument('--runner-arg', action='append', default=[])
    item.add_argument('--jobs', type=int)
    item.add_argument('--timeout', type=float, default=900)
    item.add_argument('--max-output-bytes', type=int, default=8*1024*1024)
    item.add_argument('--root')
    for name in ('status', 'reconcile'):
        item = commands.add_parser(name, epilog=f'example: hx build {name} <job-id>')
        item.add_argument('job_id')
        item.add_argument('--root')
    item = commands.add_parser('materialize', epilog='example: hx build materialize <build-id> /tmp/private-build-output')
    item.add_argument('build_id')
    item.add_argument('destination')
    item.add_argument('--root')
    args = parser.parse_args(argv)
    child = dict(os.environ if env is None else env)
    with ContinuityStore(root) as store:
        with store.transaction() as tx:
            initialize(tx)
        if args.command in ('graph', 'build'):
            contract = None
            if args.source_contract:
                path = Path(args.source_contract)
                if path.stat().st_size > 16384:
                    raise ValidationError('source contract exceeds 16 KiB')
                contract = json.loads(path.read_text())
            options = dict(cwd=args.cwd, env=child, configuration=args.configuration, framework=args.framework,
                           runtime=args.runtime, platform=args.platform, source_contract=contract, dotnet=args.dotnet)
            if args.command == 'graph':
                result = graph(store, args.project, **options)
            else:
                result = build(store, args.project, **options, run_id=args.run, request_id=args.request,
                               worker_id=child.get('HARNESS_ID'), jobs=args.jobs, timeout_s=args.timeout,
                               max_output_bytes=args.max_output_bytes, fresh=args.fresh)
        elif args.command == 'test':
            result = test(store, args.build_id, env=child, run_id=args.run, request_id=args.request,
                          worker_id=child.get('HARNESS_ID'), project=args.project, framework=args.framework,
                          filter_expression=args.filter, runner_args=args.runner_arg, jobs=args.jobs,
                          timeout_s=args.timeout, max_output_bytes=args.max_output_bytes)
        elif args.command == 'materialize':
            result = materialize(store, args.build_id, args.destination)
        elif args.command == 'reconcile':
            result = reconcile(store, args.job_id)
        else:
            result = status(store, args.job_id)
        print(canonical(result))
        if args.command == 'graph':
            return 0 if result['complete'] else 1
        return 0 if result.get('valid', result.get('status') not in ('running', 'queued')) else 1
