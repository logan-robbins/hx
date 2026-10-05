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
