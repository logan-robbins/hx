"""Independent learning assessment sidecar paired with every agent, across flavors.

It observes the captured event stream, not the context Companion's derived state.
Its cursor, frozen jobs, budgets and proposals cannot acknowledge context events or
modify current task facts. Reviewed rules remain owned by hx learn's review boundary.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from . import evidence, native_processes, prompt_compiler, sidecar_process
from .config_harness import load_harness
from .continuity_store import Conflict, canonical, digest, _id
from .errors import HxError, ValidationError


def ledger_stream(run_id, stream_id):
    return 'run:' + run_id + ':' + stream_id


def row(store, job_id):
    found = store.db.execute('SELECT * FROM learning_jobs WHERE job_id=?', (job_id,)).fetchone()
    if found is None:
        raise ValidationError('unknown learning assessment job')
    return {**dict(found), 'payload': json.loads(found['payload'])}



def frozen(store, job):
    reference = store.db.execute("SELECT hash FROM artifact_refs WHERE owner_type='learning' AND owner_id=? AND slot='input'", (job['job_id'],)).fetchone()
    if reference is None:
        raise Conflict('learning assessment lost its frozen input artifact')
    body = json.loads(store.read_artifact(reference[0]) if hasattr(store, 'read_artifact') else _artifact(store, reference[0]))
    if body != job['payload']['body']:
        raise Conflict('learning assessment body differs from its immutable frozen input')
    return body


def _artifact(store, sha256):
    with store.transaction() as tx:
        return tx.read_artifact(sha256)

def update(tx, job, **changes):
    tx._change()
    tx.db.execute('UPDATE learning_jobs SET payload=? WHERE job_id=?',
                  (canonical({**job['payload'], **changes}), job['job_id']))


def register(store):
    """Register independent consumers before raw event retirement can run."""
    from . import streams
    configured = {}
    for path in (store.root / 'config').glob('*/harness.json'):
        config = load_harness(path, check_cross_file=False)
        if not config.learning.get('disabled'):
            configured[config.id] = config
    with store.transaction() as tx:
        for item in tx.db.execute('SELECT DISTINCT r.worker_id,e.run_id,e.stream_id FROM events e JOIN runs r USING(run_id)').fetchall():
            if item['worker_id'] in configured:
                tx.db.execute('INSERT OR IGNORE INTO learning_cursors VALUES(?,?,0,0)',
                              (item['worker_id'], ledger_stream(item['run_id'], item['stream_id'])))
        if 'partner' in configured:
            names = ['partner-main', *[p.name.removesuffix('-open.jsonl').removesuffix('-closed.jsonl')
                                      for p in streams.subagent_streams(store.root, 'partner')]]
            for stream in names:
                if streams.stream_path(store.root, 'partner', stream).is_file():
                    tx.db.execute('INSERT OR IGNORE INTO learning_cursors VALUES(?,?,0,0)', ('partner', stream))


def _source(store, agent, stream, run_id, after, limit, budget):
    """Select whole events; large evidence is addressed, never head-excerpted here."""
    events, used = [], 0
    root = store.root if hasattr(store, 'root') else store.store.root
    if run_id:
        run = store.db.execute('SELECT * FROM runs WHERE run_id=?', (run_id,)).fetchone()
        if run is None or run['worker_id'] != agent:
            raise Conflict('learning assessment must belong to the paired agent')
        candidates = store.db.execute('SELECT * FROM events WHERE run_id=? AND stream_id=? AND seq>? ORDER BY seq LIMIT ?',
                                      (run_id, stream, after, limit)).fetchall()
        for item in candidates:
            payload = json.loads(item['payload'])
            if payload.get('retired'):
                raise Conflict('learning evidence retired before independent assessment')
            event = {'event_id': item['event_id'], 'seq': item['seq'], 'kind': item['kind'], 'payload_hash': item['payload_hash']}
            source_file = evidence.source_evidence(payload, item['kind'])
            if source_file:
                event['source_file_evidence'] = True
            if not source_file and not payload.get('artifact_hash') and used + len(canonical(payload).encode()) <= budget:
                event['payload'] = payload
                used += len(canonical(payload).encode())
            else:
                event['requires_read'] = True
            events.append(event)
    else:
        if agent != 'partner':
            raise ValidationError('worker learning requires an explicit source run')
        from . import streams
        for payload in streams.iter_records(streams.stream_path(root, agent, stream)):
            seq = payload.get('seq')
            if type(seq) is not int or seq <= after:
                continue
            size = len(canonical(payload).encode())
            if used + size > budget:
                if not events:
                    raise ValidationError('complete Partner learning event exceeds assessment request budget')
                break
            # Partner stream records explicitly retain their transcript references;
            # excerpts do not establish facts absent from the captured record.
            event = {'event_id': 'partner:' + stream + ':' + str(seq), 'seq': seq,
                     'kind': payload.get('event', 'observation'), 'payload_hash': digest(payload), 'payload': payload}
            if payload.get('artifact_hash'):
                event.update(requires_read=True, content_scope='artifact_reference')
            elif payload.get('ref') or any(key in payload for key in ('input', 'output', 'truncated', 'excerpt')):
                # A complete capture wrapper does not establish completeness of
                # the original native input/output to which its excerpt refers.
                event.update(requires_read=True, original_unavailable=True, original_complete=False, content_scope='captured_excerpt')
            events.append(event)
            used += size
            if len(events) == limit:
                break
    return events


def prepare(store, agent_id, stream_id, request_id, *, run_id=None):
    for value in (agent_id, stream_id, request_id):
        _id(value)
    config = load_harness(store.root / 'config' / agent_id / 'harness.json', check_cross_file=False)
    if config.learning.get('disabled'):
        return None
    cursor_key = ledger_stream(run_id, stream_id) if run_id else stream_id
    request_key = digest([agent_id, cursor_key, request_id])
    prior = store.db.execute('SELECT job_id FROM learning_jobs WHERE request_key=?', (request_key,)).fetchone()
    if prior and row(store, prior[0])['status'] != 'prepared':
        return row(store, prior[0])
    prompt = prompt_compiler.build(store.root, agent_id, audience='learning')
    if prior and not sidecar_process.supersede_unpaid(store, 'learning_jobs', prior[0], prompt['version']):
        return row(store, prior[0])
    _, rendered = prompt_compiler.verified_bundle(store.root, agent_id, Path(prompt['manifest_path']),
                                                workdir=prompt['identity']['workdir'], audience='learning')
    from .learning_protocol import TOOLS, instructions
    system = rendered['system'] + rendered['context'] + '\n' + instructions()
    runtime = sidecar_process.runtime(store.root, config, purpose='learning')
    request_bytes = min(config.learning.get('request_bytes', 65536), runtime['model_input_limit'])
    remaining = request_bytes - len(system.encode()) - len(canonical(TOOLS).encode()) - 2048
    if remaining < 1024:
        raise ValidationError('complete learning instructions exhaust the independent request budget')
    with store.transaction() as tx:
        prior = tx.db.execute('SELECT job_id FROM learning_jobs WHERE request_key=?', (request_key,)).fetchone()
        if prior:
            return row(tx, prior[0])
        tx.db.execute('INSERT OR IGNORE INTO learning_cursors VALUES(?,?,0,0)', (agent_id, cursor_key))
        cursor = tx.db.execute('SELECT * FROM learning_cursors WHERE agent_id=? AND stream_id=?', (agent_id, cursor_key)).fetchone()
        events = _source(tx, agent_id, stream_id, run_id, cursor['assessed_seq'],
                         config.learning.get('batch_records', 32), remaining)
        if not events:
            return None
        body = {'schema_version': 1, 'agent_id': agent_id, 'role': config.role, 'flavor': config.flavor,
                'source': 'ledger' if run_id else 'partner', 'source_run_id': run_id, 'source_stream_id': stream_id,
                'stream_id': cursor_key, 'from_seq': cursor['assessed_seq'], 'to_seq': events[-1]['seq'],
                'cursor_revision': cursor['revision'], 'events': events,
                'event_digest': digest([(e['event_id'], e['payload_hash']) for e in events])}
        size = len(system.encode()) + len(canonical(body).encode()) + len(canonical(TOOLS).encode())
        if size > request_bytes:
            raise ValidationError('complete learning request exceeds its independent budget')
        payload = {**runtime, 'body': body, 'system': system,
                   'manifest': prompt['manifest_path'], 'prompt_version': prompt['version'], 'workdir': prompt['identity']['workdir'],
                   'request_bytes': request_bytes, 'max_proposals': config.learning.get('max_proposals', 4),
                   'evidence_budget': request_bytes - size,
                   'attempts': 0, 'reads': [], 'read_bytes': 0}
        job_id = str(uuid.uuid4())
        tx._change()
        tx.db.execute('INSERT INTO learning_jobs VALUES(?,?,?,?,?,?,?)',
                      (job_id, request_key, agent_id, cursor_key, run_id, 'prepared', canonical(payload)))
        tx.put_artifact(canonical(body).encode(), owner_type='learning', owner_id=job_id, slot='input')
    return row(store, job_id)


def status(store, job_id):
    job = row(store, job_id)
    return {'job_id': job_id, 'agent_id': job['agent_id'], 'stream_id': job['stream_id'], 'status': job['status'],
            **{key: job['payload'][key] for key in ('result', 'last_error', 'execution') if key in job['payload']}}


def execute(store, job_id, *, env=None, timeout_s=60):
    job = row(store, job_id)
    if job['status'] != 'prepared':
        return status(store, job_id)
    payload, body = job['payload'], frozen(store, job)
    _, rendered = prompt_compiler.verified_bundle(store.root, job['agent_id'], Path(payload['manifest']),
                                   workdir=payload['workdir'], audience='learning')
    from .learning_protocol import instructions
    if payload['system'] != rendered['system'] + rendered['context'] + '\n' + instructions():
        raise Conflict('learning instructions differ from their immutable prepared sources')
    sidecar_process.verify_runtime(payload)
    transport = sidecar_process.preflight(store.root, payload, body, protocol='learning_protocol', timeout_s=timeout_s)
    with store.transaction() as tx:
        if row(tx, job_id)['status'] != 'prepared':
            return status(tx, job_id)
        if tx.db.execute("SELECT 1 FROM companion_jobs WHERE status='running'").fetchone() or tx.db.execute("SELECT 1 FROM learning_jobs WHERE status='running'").fetchone():
            raise Conflict('another Companion owns the root execution slot')
        cursor = tx.db.execute('SELECT assessed_seq,revision FROM learning_cursors WHERE agent_id=? AND stream_id=?',
                               (job['agent_id'], job['stream_id'])).fetchone()
        if tuple(cursor) != (body['from_seq'], body['cursor_revision']):
            raise Conflict('learning cursor changed before execution')
        tx._change()
        tx.db.execute("UPDATE learning_jobs SET status='running' WHERE job_id=?", (job_id,))
    def spawned(process):
        with store.transaction() as tx:
            update(tx, row(tx, job_id), process=native_processes.probe(process.pid))
    execution = sidecar_process.execute(store.root, job_id, payload, body, protocol='learning_protocol',
        tools=('read_evidence', 'submit_assessment'), on_spawn=spawned, env=env, timeout_s=timeout_s, transport=transport)
    with store.transaction() as tx:
        current = row(tx, job_id)
        update(tx, current, execution=execution, last_error=None if current['payload'].get('result') else 'Learning Companion returned without an accepted assessment.')
        tx.db.execute('UPDATE learning_jobs SET status=? WHERE job_id=?',
                      ('committed' if current['payload'].get('result') else 'unresolved', job_id))
    return status(store, job_id)


def reconcile(store):
    found = store.db.execute("SELECT job_id FROM learning_jobs WHERE status='running' LIMIT 1").fetchone()
    if found is None:
        return None
    job = row(store, found[0])
    process = job['payload'].get('process')
    if not process:
        raise Conflict('interrupted learning Companion lacks its original process identity')
    if native_processes.current(process['identity']):
        return status(store, job['job_id'])
    if native_processes.probe(process['identity']['pid']) is not None:
        raise Conflict('learning Companion PID identity changed; retain ownership')
    with store.transaction() as tx:
        current = row(tx, job['job_id'])
        tx._change()
        tx.db.execute('UPDATE learning_jobs SET status=? WHERE job_id=?',
                      ('committed' if current['payload'].get('result') else 'unresolved', job['job_id']))
    return status(store, job['job_id'])


def tick(store, *, env=None):
    """One separately scheduled assessment; unchanged failed windows never rerun."""
    register(store)
    running = reconcile(store)
    if running and running['status'] == 'running':
        return running
    if store.db.execute("SELECT 1 FROM companion_jobs WHERE status='running'").fetchone():
        return {'status': 'waiting', 'purpose': 'learning'}
    from .application_loop import _state, _save
    after = _state(store, 'learning-round').get('after', ['', ''])
    for cursor in store.db.execute('SELECT * FROM learning_cursors ORDER BY (agent_id,stream_id)<=(?,?),agent_id,stream_id', after).fetchall():
        agent, key = cursor['agent_id'], cursor['stream_id']
        _save(store, 'learning-round', {'after': [agent, key]})
        config = load_harness(store.root / 'config' / agent / 'harness.json', check_cross_file=False)
        if config.learning.get('disabled'):
            continue
        run_id = None
        stream = key
        if key.startswith('run:'):
            _, run_id, stream = key.split(':', 2)
            head = store.db.execute('SELECT max(seq) FROM events WHERE run_id=? AND stream_id=?', (run_id, stream)).fetchone()[0]
            ended = store.db.execute('SELECT ended_at FROM runs WHERE run_id=?', (run_id,)).fetchone()[0] is not None
            important = bool(store.db.execute('''SELECT 1 FROM events WHERE run_id=? AND stream_id=? AND seq>?
                AND (kind IN ('correction','check_result','completion','finish')
                     OR json_extract(payload,'$.is_error')=1 OR json_extract(payload,'$.exit_code')>0
                     OR json_extract(payload,'$.observation.data.is_error')=1
                     OR json_extract(payload,'$.observation.data.exit_code')>0) LIMIT 1''',
                (run_id, stream, cursor['assessed_seq'])).fetchone())
        else:
            from . import streams
            head = streams.last_seq(streams.stream_path(store.root, agent, stream))
            ended = False
            important = any(r.get('seq', 0) > cursor['assessed_seq'] and
                            (r.get('event') in {'correction', 'stop', 'close', 'seam'} or r.get('exit', 0))
                            for r in streams.iter_records(streams.stream_path(store.root, agent, stream)))
        if head is None or head <= cursor['assessed_seq']:
            continue
        if not ended and not important and head - cursor['assessed_seq'] < config.learning.get('batch_records', 32):
            continue
        retry = store.db.execute('SELECT payload FROM runtime_cycles WHERE scope=?', ('retry:' + (run_id or agent),)).fetchone()
        generation = json.loads(retry[0]).get('generation', 0) if retry else 0
        request = 'auto-' + digest([agent, key, cursor['assessed_seq'], head, generation])
        try:
            job = prepare(store, agent, stream, request, run_id=run_id)
            if job and job['status'] == 'prepared':
                return execute(store, job['job_id'], env=env)
        except (HxError, OSError) as exc:
            return {'status': 'unresolved', 'purpose': 'learning', 'agent_id': agent, 'error': str(exc)}
    return {'status': 'idle', 'purpose': 'learning'}
