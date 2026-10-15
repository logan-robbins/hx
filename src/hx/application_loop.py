"""Observe and schedule independent context and learning Companions in one slot."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import time
from pathlib import Path

from . import companion_protocol, map_refresh, native_companion, native_processes, observer, passes
from .continuity_store import Conflict, ContinuityStore, canonical, digest
from .errors import HxError


def _state(store, scope):
    row = store.db.execute('SELECT payload FROM runtime_cycles WHERE scope=?', (scope,)).fetchone()
    return json.loads(row[0]) if row else {}


def _save(store, scope, value):
    with store.transaction() as tx:
        tx._change()
        tx.db.execute('INSERT INTO runtime_cycles VALUES(?,?) ON CONFLICT(scope) DO UPDATE SET payload=excluded.payload',
                      (scope, canonical(value)))


def watch_map(store, *, limit=32):
    """Round-robin indexed source metadata; inspect text only after a source changes."""
    after = _state(store, 'map-watch').get('after', ['', '', ''])
    active_maps = '''WITH active_maps AS (SELECT DISTINCT json_extract(j.value,'$.repository') AS repository,
        json_extract(j.value,'$.snapshot') AS snapshot FROM runs r JOIN tasks t
        ON t.task_id=r.task_id AND t.revision=r.task_revision,json_each(t.payload,'$.map_inputs') j
        WHERE r.ended_at IS NULL) '''
    rows = store.db.execute(active_maps + '''SELECT DISTINCT s.repository,s.snapshot,s.path,m.import_path AS worktree FROM map_sources s JOIN active_maps USING(repository,snapshot)
        JOIN map_overlays o USING(repository,snapshot) JOIN map_snapshots m USING(repository,snapshot) WHERE (s.repository,s.snapshot,s.path)>(?,?,?)
        ORDER BY s.repository,s.snapshot,s.path LIMIT ?''', (*after, limit)).fetchall()
    if not rows and after != ['', '', '']:
        _save(store, 'map-watch', {'after': ['', '', '']})
    changes = []
    checked = set()
    for row in rows:
        key = tuple(row[name] for name in ('repository', 'snapshot', 'path'))
        repository = Path(row['worktree'])
        if key[:2] not in checked:
            from .map_updates import advance_head
            try:
                advance_head(store, repository, *key[:2])
                checked.add(key[:2])
            except (HxError, OSError) as exc:
                changes.append({'snapshot': row['snapshot'], 'error': str(exc)})
                _save(store, 'map-watch', {'after': list(key)})
                continue
        stamp = map_refresh._stamp(repository, row['path'])
        prior = store.db.execute('SELECT stamp FROM map_source_observations WHERE repository=? AND snapshot=? AND path=?', key).fetchone()
        if prior is None or prior[0] != stamp:
            try:
                map_refresh.queue_sources(store, repository, row['snapshot'], [row['path']])
            except (HxError, OSError) as exc:
                changes.append({'snapshot': row['snapshot'], 'error': str(exc)})
                _save(store, 'map-watch', {'after': list(key)})
                continue
            changes.append({'snapshot': row['snapshot'], 'path': row['path']})
            with store.transaction() as tx:
                tx._change()
                tx.db.execute('INSERT INTO map_source_observations VALUES(?,?,?,?) ON CONFLICT(repository,snapshot,path) DO UPDATE SET stamp=excluded.stamp', (*key, stamp))
        _save(store, 'map-watch', {'after': list(key)})
    # The refresh queue is durable; recover entries even when no new path changed.
    queued = store.db.execute(active_maps + '''SELECT DISTINCT q.repository,q.snapshot,m.import_path AS worktree FROM map_refresh_queue q JOIN active_maps USING(repository,snapshot)
        JOIN map_overlays o USING(repository,snapshot) JOIN map_snapshots m USING(repository,snapshot) ORDER BY q.repository,q.snapshot LIMIT 4''').fetchall()
    for item in queued:
        try:
            map_refresh.drain(store, Path(item['worktree']), item['snapshot'], limit=8)
        except (HxError, OSError) as exc:
            changes.append({'snapshot': item['snapshot'], 'error': str(exc)})
    return changes


def reconcile_companion(store):
    row = store.db.execute("SELECT job_id FROM companion_jobs WHERE status='running' LIMIT 1").fetchone()
    if row is None:
        return None
    job = companion_protocol.row(store, row[0])
    identity = job['payload'].get('process')
    if not identity:
        raise Conflict('interrupted companion lacks a process identity; execution remains unresolved')
    if native_processes.current(identity['identity']):
        return {'job_id': job['job_id'], 'status': 'running'}
    if native_processes.probe(identity['identity']['pid']) is not None:
        raise Conflict('companion process identity changed while its PID is live; retain its execution slot')
    # The original kernel process is gone. Recover an accepted patch, never rerun it.
    with store.transaction() as tx:
        current = companion_protocol.row(tx, job['job_id'])
        if current['status'] != 'running':
            return native_companion.status(store, job['job_id'])
        body = companion_protocol.frozen(tx, current)
        pending = {'schema_version': 1, 'pass_id': body['pass_id'], 'event_digest': body['event_digest'],
                   'task_revision': body['task_revision'], 'operations': [],
                   'dispositions': {event['event_id']: 'pending' for event in body['events']}}
        ref = tx.db.execute("SELECT hash FROM artifact_refs WHERE owner_type='pass' AND owner_id=? AND slot='result'", (job['pass_id'],)).fetchone()
        result = json.loads(tx.read_artifact(ref[0])) if ref else None
        committed = result is not None and result['response_hash'] != digest(pending)
        status = 'committed' if committed else 'unresolved'
        tx._change()
        tx.db.execute('UPDATE companion_jobs SET status=? WHERE job_id=?', (status, job['job_id']))
        companion_protocol.update(tx, current, last_error=None if committed else 'Companion exited before committing its patch.')
    return {'job_id': job['job_id'], 'status': status}


def _records(store, task_id):
    # Current task only, small whole records. The companion can compress/drop these.
    rows = store.db.execute('''SELECT r.record_id,r.storage_bytes FROM records r JOIN record_heads h USING(record_id,version)
        WHERE r.task_id=? AND r.validity='current' AND r.kind NOT IN ('goal','constraint','cursor')
        ORDER BY CASE WHEN r.retention='context' THEN 0 ELSE 1 END,r.record_id LIMIT 16''', (task_id,)).fetchall()
    ids, size = [], 0
    for row in rows:
        if size + row['storage_bytes'] <= 2048:
            ids.append(row['record_id'])
            size += row['storage_bytes']
    return tuple(ids)


def tick(store, *, env=None):
    from .runtime_notifications import drain
    drain(store)
    from .task_retention import retire_closed
    retire_closed(store)
    observed = observer.reconcile(store)
    from .native_controller import reconcile_launches
    reconcile_launches(store, env=env)
    changed = watch_map(store)
    from .native_completion import advance_one
    try:
        completion = advance_one(store, env=env)
        if _state(store, 'completion-error'):
            _save(store, 'completion-error', {})
    except HxError as exc:
        completion = {'status': 'unresolved', 'error': str(exc)}
        if _state(store, 'completion-error') != completion:
            _save(store, 'completion-error', completion)
    if completion and completion['status'] in {'advancing', 'completed', 'failed'}:
        return {**completion, 'map_changes': changed}
    from . import learning_companion
    running = []
    for kind, reconcile in (('context', reconcile_companion), ('learning', learning_companion.reconcile)):
        try:
            current = reconcile(store)
        except (HxError, OSError) as exc:
            current = {'status': 'unresolved', 'error': str(exc)}
        if current and current.get('status') == 'running':
            running.append({'kind': kind, **current})
        elif current and current.get('error'):
            _save(store, kind + '-companion-error', current)
        elif current and _state(store, kind + '-companion-error'):
            _save(store, kind + '-companion-error', {})
    # An ambiguous process remains an occupied paid slot until reconciled.
    occupied = bool(store.db.execute("SELECT 1 FROM companion_jobs WHERE status='running' UNION ALL SELECT 1 FROM learning_jobs WHERE status='running' LIMIT 1").fetchone())
    if running or occupied:
        return {'status': 'waiting', 'companions': running, 'map_changes': changed}
    from .native_restart import advance_one as restart_one
    restart = restart_one(store, env=env)
    if restart:
        return {**restart, 'map_changes': changed}
    from .token_controls import reset_one
    reset = reset_one(store, env=env)
    if reset:
        return {**reset, 'map_changes': changed}
    from . import learning_companion
    first = _state(store, 'companion-turn').get('next', 'context')
    order = (first, 'context' if first == 'learning' else 'learning')
    fallback = {'status': 'idle'}
    for kind in order:
        try:
            result = (learning_companion.tick(store, env=env) if kind == 'learning'
                      else _context_tick(store, env=env))
        except (HxError, OSError) as exc:
            result = {'status': 'unresolved', 'error': str(exc)}
        result = result or {'status': 'idle'}
        _save(store, 'companion-turn', {'next': 'context' if kind == 'learning' else 'learning'})
        if result.get('status') == 'unresolved' and result.get('error'):
            _save(store, kind + '-companion-error', result)
        elif result.get('status') not in {'idle', 'no_work'} and _state(store, kind + '-companion-error'):
            _save(store, kind + '-companion-error', {})
        if result.get('status') not in {'idle', 'no_work'}:
            return {**result, 'companion_kind': kind, 'map_changes': changed}
        fallback = result
    return {**fallback, 'map_changes': changed}


def _context_tick(store, *, env=None):
    """Advance only the context Companion's cursor and frozen observation pass."""
    after = _state(store, 'companion-round').get('after', ['', ''])
    candidates = store.db.execute('''SELECT r.run_id,r.worker_id,r.task_id,r.task_revision,c.stream_id,c.head_seq,c.classified_seq,c.revision
        FROM runs r JOIN cursors c USING(run_id) WHERE r.ended_at IS NULL AND
        (c.head_seq>c.classified_seq OR EXISTS(SELECT 1 FROM events e WHERE e.run_id=r.run_id AND e.stream_id=c.stream_id AND e.disposition='pending'))
        AND (r.run_id,c.stream_id)>(?,?) ORDER BY r.run_id,c.stream_id LIMIT 1''', after).fetchall()
    if not candidates:
        _save(store, 'companion-round', {'after': ['', '']})
        return {'status': 'idle'}
    candidate = dict(candidates[0])
    candidate['retry'] = _state(store, 'retry:' + candidate['run_id']).get('generation', 0)
    _save(store, 'companion-round', {'after': [candidate['run_id'], candidate['stream_id']]})
    # Failures stick to this window until a new observation or explicit retry.
    scope = 'companion:' + candidate['run_id'] + ':' + candidate['stream_id']
    version = digest(candidate)
    old = _state(store, scope)
    if old.get('version') == version:
        return {'status': old['status'], 'run_id': candidate['run_id'], **({'error': old['error']} if old.get('error') else {})}
    try:
        job = native_companion.prepare(store, candidate['worker_id'], candidate['run_id'], candidate['stream_id'],
            'auto-' + version, record_ids=_records(store, candidate['task_id']), env=env)
        result = native_companion.execute(store, job['job_id'], env=env) if job else {'status': 'idle'}
    except HxError as exc:
        result = {'status': 'unresolved', 'error': str(exc)}
    # Persist the post-pass cursor too: pending events do not cause a paid retry loop.
    cursor = store.db.execute('SELECT head_seq,classified_seq,revision FROM cursors WHERE run_id=? AND stream_id=?',
                              (candidate['run_id'], candidate['stream_id'])).fetchone()
    candidate.update(classified_seq=cursor['classified_seq'], revision=cursor['revision'])
    _save(store, scope, {'version': digest(candidate), **result})
    return {**result, 'run_id': candidate['run_id']}


@contextmanager
def service_lock(root):
    (root / 'run').mkdir(parents=True, exist_ok=True)
    with (root / 'run/application-loop.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise Conflict('application loop already running for this root') from None
        yield


def serve(root, *, env=None, stop=None, interval=5, idle_exit=False):
    import os
    with service_lock(root):
        with ContinuityStore(root) as store:
            _save(store, 'application-service', {'process': native_processes.probe(os.getpid())['identity'], 'ready': True})
            try:
                while stop is None or not stop.is_set():
                    if idle_exit and not store.db.execute('SELECT 1 FROM runs WHERE ended_at IS NULL LIMIT 1').fetchone() and not store.db.execute("SELECT 1 FROM outbox WHERE status IN ('pending','claimed') LIMIT 1").fetchone():
                        return
                    try:
                        result = tick(store, env=env)
                        if _state(store, 'service-error'):
                            _save(store, 'service-error', {})
                    except (HxError, OSError) as exc:
                        result = {'status': 'unresolved', 'error': str(exc)}
                        _save(store, 'service-error', result)
                    brief = {key: result[key] for key in ('status', 'run_id', 'error', 'reason') if key in result}
                    failures = [item for item in result.get('map_changes', []) if 'error' in item]
                    if failures:
                        brief['map_errors'] = failures[:4]
                    if _state(store, 'last-cycle') != brief:
                        _save(store, 'last-cycle', brief)
                    if result['status'] in {'idle', 'waiting', 'unresolved'}:
                        if stop is not None:
                            stop.wait(interval)
                        else:
                            time.sleep(interval)
            finally:
                state = _state(store, 'application-service')
                if state.get('process', {}).get('pid') == os.getpid():
                    _save(store, 'application-service', {**state, 'ready': False})


def main(argv, root, *, env=None):
    parser = argparse.ArgumentParser(prog='hx loop')
    parser.add_argument('--root')
    parser.add_argument('--idle-exit', action='store_true', help=argparse.SUPPRESS)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--once', action='store_true', help='advance one bounded observation/companion cycle')
    action.add_argument('--status', action='store_true', help='show current owners, progress and blockers without reading logs')
    action.add_argument('--retry', metavar='RUN', help='explicitly retry this run after resolving a companion/Jev failure')
    parser.add_argument('--after', default='', help='status pagination after a run ID')
    args = parser.parse_args(argv)
    if args.status:
        with ContinuityStore(root) as store:
            print(canonical(status(store, after=args.after)))
    elif args.retry:
        from .caller import require_partner_caller
        require_partner_caller('loop retry', env)
        with ContinuityStore(root) as store:
            with store.transaction() as tx:
                passes._active_run(tx, args.retry)
            generation = _state(store, 'retry:' + args.retry).get('generation', 0) + 1
            _save(store, 'retry:' + args.retry, {'generation': generation})
            print(canonical({'run_id': args.retry, 'retry_requested': generation}))
    elif args.once:
        with service_lock(root), ContinuityStore(root) as store:
            print(canonical(tick(store, env=env)))
    else:
        serve(root, env=env, idle_exit=args.idle_exit)
    return 0


def status(store, *, after=''):
    """Partner projection: indexed headers only, no model calls or receipt rereads."""
    rows = store.db.execute('''SELECT r.run_id,r.task_id,r.task_revision,r.worker_id,r.phase,
        l.status AS native_status,l.error AS native_error,
        (SELECT count(*) FROM task_replan_queue q WHERE q.task_id=r.task_id AND q.task_revision=r.task_revision) AS changed_inputs,
        (SELECT coalesce(sum(head_seq-classified_seq),0) FROM cursors c WHERE c.run_id=r.run_id) AS unclassified,
        (SELECT count(*) FROM events e WHERE e.run_id=r.run_id AND e.disposition='pending') AS pending
        FROM runs r LEFT JOIN native_launches l ON l.run_id=r.run_id AND l.status<>'archived'
        WHERE r.ended_at IS NULL AND r.run_id>? ORDER BY r.run_id LIMIT 17''', (after,)).fetchall()
    units = []
    for row in rows[:16]:
        unit = dict(row)
        unit['context_companion'] = dict(store.db.execute(
            'SELECT job_id,status FROM companion_jobs WHERE run_id=? ORDER BY rowid DESC LIMIT 1',
            (row['run_id'],)).fetchone() or {})
        unit['learning_companion'] = dict(store.db.execute(
            'SELECT job_id,status FROM learning_jobs WHERE run_id=? ORDER BY rowid DESC LIMIT 1',
            (row['run_id'],)).fetchone() or {})
        unit['unassessed_learning'] = store.db.execute('''SELECT coalesce(sum(c.head_seq-l.assessed_seq),0)
            FROM cursors c JOIN learning_cursors l ON l.agent_id=?
            AND l.stream_id='run:'||c.run_id||':'||c.stream_id WHERE c.run_id=?''',
            (row['worker_id'], row['run_id'])).fetchone()[0]
        intent = _state(store, 'finish:' + row['run_id'])
        if intent:
            unit['completion'] = {key: intent[key] for key in ('status', 'failed_check', 'evidence') if key in intent}
        errors = store.db.execute("SELECT json_extract(payload,'$.error') FROM runtime_cycles WHERE scope LIKE ? AND json_extract(payload,'$.status')='unresolved' LIMIT 1", ('companion:' + row['run_id'] + ':%',)).fetchone()
        if errors and errors[0]:
            unit['blocker'] = errors[0]
        launch_error = _state(store, 'launch-error:' + row['run_id'])
        if launch_error:
            unit['launch_error'] = launch_error['error']
        units.append(unit)
    completed = [dict(row) for row in store.db.execute('''SELECT c.task_id,c.task_revision,c.run_id FROM unit_completions c
        JOIN runs r USING(run_id) ORDER BY r.ended_at DESC LIMIT 8''')]
    from .goal_templates import changes
    milestones = [dict(row) for row in store.db.execute('''SELECT task_id,task_revision,kind,identity
        FROM unit_milestones ORDER BY rowid DESC LIMIT 8''')]
    builds = [dict(row) for row in store.db.execute('''SELECT job_id,kind,run_id,status
        FROM dotnet_jobs ORDER BY rowid DESC LIMIT 8''')]
    validations = [dict(row) for row in store.db.execute('''SELECT batch_id,head,boundary,status
        FROM delivery_validations ORDER BY rowid DESC LIMIT 8''')]
    learning_jobs = [dict(row) for row in store.db.execute('''SELECT job_id,agent_id,stream_id,run_id,status
        FROM learning_jobs ORDER BY rowid DESC LIMIT 16''')]
    service = _state(store, 'application-service')
    running = bool(service.get('ready') and service.get('process') and native_processes.current(service['process']))
    return {'service': {**service, 'running': running, 'ready': running},
            'active': units, 'replanning': changes(store, limit=4), 'recent_completions': completed, 'last_cycle': _state(store, 'last-cycle'),
            'recent_milestones': milestones, 'build_jobs': builds, 'validation_snapshots': validations,
            'learning_assessments': learning_jobs,
            'context_companion_error': _state(store, 'context-companion-error'),
            'learning_companion_error': _state(store, 'learning-companion-error'),
            'proof_note': 'Recorded lifecycle headers; plan ready/change status revalidate current proof before scheduling or release.',
            'service_error': _state(store, 'service-error'), 'completion_error': _state(store, 'completion-error'),
            'next_after': rows[15]['run_id'] if len(rows)>16 else None}


def ensure(root, *, env=None):
    """Start one root service before dispatch; kernel identity prevents PID adoption."""
    import os
    import subprocess
    import sys
    directory = root / 'run'
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / 'application-loop-start.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        with ContinuityStore(root) as store:
            state = _state(store, 'application-service')
            prior = state.get('process')
            if state.get('ready') and prior and native_processes.current(prior):
                return prior
        child = dict(os.environ if env is None else env)
        child['PYTHONPATH'] = str(Path(__file__).resolve().parent.parent)
        child['HARNESS_ROOT'] = str(root)
        child.pop('HARNESS_ID', None)
        for key in tuple(child):
            if key.startswith('HX_CONTINUITY_'):
                child.pop(key)
        process = subprocess.Popen([sys.executable, '-m', 'hx.cli', 'loop', '--root', str(root)],
            cwd=root, env=child, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True)
        actual = native_processes.probe(process.pid)
        if actual is None:
            raise Conflict('application loop exited during startup')
        identity = actual['identity']
        deadline = time.monotonic() + 5
        while process.poll() is None and time.monotonic() < deadline:
            with ContinuityStore(root) as store:
                state = _state(store, 'application-service')
            ready = state.get('process', {})
            # A Python launcher can exec again on Darwin, changing pidversion.
            # This is still our unreaped Popen child; accept its post-exec token.
            if state.get('ready') and ready.get('pid') == process.pid and native_processes.current(ready):
                return ready
            time.sleep(.02)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        raise Conflict('application loop did not acknowledge service readiness')
