"""Bound current task facts and erase superseded payloads after validated updates."""
from .facts import RequiredContextOverflow

CONTEXT_BYTES = 8192
STORED_BYTES = 32768
MAX_FACTS = 64


def enforce(tx, task_id):
    row = tx.db.execute('''SELECT count(*),coalesce(sum(storage_bytes),0),
        coalesce(sum(CASE WHEN retention='context' THEN storage_bytes ELSE 0 END),0)
        FROM records r JOIN record_heads h USING(record_id,version)
        WHERE task_id=? AND validity='current' ''', (task_id,)).fetchone()
    if row[0] > MAX_FACTS or row[1] > STORED_BYTES or row[2] > CONTEXT_BYTES:
        raise RequiredContextOverflow('task facts exceed 64 records, 8192 context bytes, or 32768 stored bytes; compress or drop obsolete facts in this patch')


def collect(tx, task_id, *, limit=32):
    # Prepared passes and checkpoints contain their own immutable input. CAS
    # still compares heads; deleting old payloads does not reset any version.
    old = tx.db.execute('''SELECT r.record_id,r.version FROM records r JOIN record_heads h USING(record_id)
        WHERE r.task_id=? AND r.version<>h.version ORDER BY r.record_id,r.version LIMIT ?''', (task_id, limit)).fetchall()
    for row in old:
        key = tuple(row)
        tx.db.execute('DELETE FROM record_entities WHERE record_id=? AND version=?', key)
        tx.db.execute('DELETE FROM records WHERE record_id=? AND version=?', key)
    # Keep only identity/version/reason for explicit drops. The obsolete claim,
    # commands and evidence links no longer remain in task memory.
    dropped = tx.db.execute('''SELECT r.record_id,r.version FROM records r JOIN record_heads h USING(record_id,version)
        WHERE task_id=? AND validity='dropped' AND payload<>'{"schema_version":1}' LIMIT ?''', (task_id, limit)).fetchall()
    for row in dropped:
        tx.db.execute('DELETE FROM record_entities WHERE record_id=? AND version=?', tuple(row))
        tx.db.execute('''UPDATE records SET payload='{"schema_version":1}',evidence='[]',inputs='{}',storage_bytes=0
                        WHERE record_id=? AND version=?''', tuple(row))
    if old or dropped:
        tx._change()
    return {'deleted_versions': len(old), 'erased_claims': len(dropped)}


def close(tx, task_id):
    """Closed assignments leave no reusable worker facts or semantic cache."""
    if tx.db.execute('SELECT 1 FROM runs WHERE task_id=? AND ended_at IS NULL LIMIT 1', (task_id,)).fetchone():
        return False
    tx._change()
    tx.db.execute('DELETE FROM record_entities WHERE record_id IN (SELECT record_id FROM records WHERE task_id=?)', (task_id,))
    tx.db.execute('''DELETE FROM records WHERE task_id=? AND (record_id,version) NOT IN
                     (SELECT record_id,version FROM record_heads)''', (task_id,))
    tx.db.execute('''UPDATE records SET validity='dropped',payload='{"schema_version":1}',evidence='[]',inputs='{}',
                    reason='Closed task.',expires_when='Task closed.',consuming_step=NULL,storage_bytes=0 WHERE task_id=?''', (task_id,))
    tx.db.execute('DELETE FROM retrieval_runs WHERE task_id=?', (task_id,))
    tx.db.execute('DELETE FROM progress_steps WHERE task_id=?', (task_id,))
    tx.db.execute('DELETE FROM progress_deliverables WHERE task_id=?', (task_id,))
    tx.db.execute('DELETE FROM native_reads WHERE launch_id IN (SELECT l.launch_id FROM native_launches l JOIN runs r USING(run_id) WHERE r.task_id=?)', (task_id,))
    tx.db.execute('DELETE FROM search_calls WHERE launch_id IN (SELECT l.launch_id FROM native_launches l JOIN runs r USING(run_id) WHERE r.task_id=?)', (task_id,))
    return True


def retire_closed(store, *, limit=64):
    """Erase closed-task raw bodies in bounded batches; keep live cited evidence."""
    from .continuity_store import canonical
    with store.transaction() as tx:
        # Cited application findings/checks remain useful beyond worker completion.
        rows = tx.db.execute('''SELECT e.event_id,e.run_id FROM events e JOIN runs r USING(run_id)
            WHERE r.ended_at IS NOT NULL AND r.outcome='completed'
            AND json_extract(e.payload,'$.retired') IS NULL
            AND NOT EXISTS(SELECT 1 FROM records f JOIN record_heads h USING(record_id,version),json_each(f.evidence) j
                           WHERE f.validity='current' AND j.value=e.event_id)
            AND NOT EXISTS(SELECT 1 FROM map_records m JOIN map_heads h USING(repository,snapshot,record_id,version),json_each(m.evidence) j
                           WHERE m.applicability='current' AND j.value=e.event_id)
            ORDER BY e.rowid LIMIT ?''', (limit,)).fetchall()
        for row in rows:
            tx.release_artifacts('event', row['event_id'])
            tx.db.execute("UPDATE events SET payload=? WHERE event_id=?", (canonical({'retired': True}), row['event_id']))
        closed = tx.db.execute("SELECT run_id FROM runs WHERE ended_at IS NOT NULL AND outcome='completed' AND NOT EXISTS(SELECT 1 FROM companion_jobs j WHERE j.run_id=runs.run_id AND j.status='running') AND NOT EXISTS(SELECT 1 FROM runtime_cycles x WHERE x.scope='retained-closed:'||runs.run_id) ORDER BY ended_at LIMIT 16").fetchall()
        for row in closed:
            run_id = row['run_id']
            for frozen in tx.db.execute("SELECT pass_id FROM passes WHERE run_id=? AND status<>'prepared'", (run_id,)).fetchall():
                tx.db.execute("DELETE FROM artifact_refs WHERE owner_type='pass' AND owner_id=? AND slot='input'", (frozen[0],))
            # Completed pass bodies contain duplicate events/current-state snapshots.
            tx.db.execute("UPDATE passes SET payload=? WHERE run_id=? AND status<>'prepared'", (canonical({'retired': True}), run_id))
            jobs = tx.db.execute("SELECT job_id FROM companion_jobs WHERE run_id=? AND status<>'running'", (run_id,)).fetchall()
            for job in jobs:
                tx.release_artifacts('companion', job[0])
                tx.db.execute('UPDATE companion_jobs SET payload=? WHERE job_id=?', (canonical({'retired': True}), job[0]))
            tx.db.execute("DELETE FROM runtime_cycles WHERE scope LIKE ?", ('capability-call:' + run_id + ':%',))
            tx.db.execute("DELETE FROM runtime_cycles WHERE scope=?", ('capabilities:' + run_id,))
            tx.db.execute('INSERT OR REPLACE INTO runtime_cycles VALUES(?,?)', ('retained-closed:' + run_id, '{}'))
        if rows or closed:
            tx._change()
    removed = 0
    if rows:
        with store.transaction() as tx:
            garbage = tx.db.execute('''SELECT hash FROM artifacts a WHERE
                NOT EXISTS(SELECT 1 FROM artifact_refs r WHERE r.hash=a.hash) AND
                NOT EXISTS(SELECT 1 FROM receipts r WHERE r.artifact_hash=a.hash) AND
                NOT EXISTS(SELECT 1 FROM checkpoints c WHERE c.packet_hash=a.hash) LIMIT 64''').fetchall()
            for item in garbage:
                (store.artifacts / item[0]).unlink(missing_ok=True)
                tx.db.execute('DELETE FROM artifacts WHERE hash=?', (item[0],))
                removed += 1
            if removed:
                tx._change()
    return {'retired_events': len(rows), 'deleted_artifacts': removed}
