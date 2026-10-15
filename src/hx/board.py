"""Read-only packet, run, ownership and delivery projections from the ledger."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3

from . import timestamps
from .errors import ValidationError

LIMIT = 256
INPUT_PATTERNS = ('Enter to confirm', 'Esc to cancel', 'Do you want to proceed?',
                  'Would you like to proceed?', "Yes, and don't ask again")


def pane_awaits_input(lines):
    from .goal import replacement_prompt
    lines = list(lines)
    return replacement_prompt('\n'.join(lines)) is not None or any(
        marker in line for line in lines for marker in INPUT_PATTERNS)


@contextmanager
def ledger_reader(root):
    """A view never creates, migrates or changes the authoritative database."""
    path = Path(root).resolve() / 'state' / 'continuity.sqlite'
    if not path.is_file():
        yield None
        return
    try:
        db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    except sqlite3.Error as exc:
        raise ValidationError(f'cannot open packet ledger: {exc}') from exc
    db.row_factory = sqlite3.Row
    try:
        db.execute('BEGIN')
        yield db
    except (sqlite3.Error, ValueError, TypeError) as exc:
        raise ValidationError(f'cannot read packet ledger: {exc}') from exc
    finally:
        db.close()


def rows(db, sql, args=()):
    result = []
    for row in db.execute(sql, args):
        item = dict(row)
        for key in ('payload', 'evidence', 'assignment'):
            if key in item and item[key] is not None:
                item[key] = json.loads(item[key])
        result.append(item)
    return result


def snapshot(root, identity=None):
    """One consistent recorded snapshot; readiness/proofs must be revalidated before acting.

    An identity can name a task, run, worker or plan. Overview lists are capped;
    identity reads select their records directly rather than filtering that cap.
    """
    result = dict(schema_version=2, root_abs=str(Path(root).resolve()), ts=timestamps.now(),
                  ledger_revision=0, plans=[], tasks=[], runs=[], leases=[], progress=[],
                  checkpoints=[], checks=[], milestones=[], delivery={'changes': [], 'candidates': []},
                  truncated=False, proof_note='Recorded state; plan ready and change status revalidate current proof.')
    with ledger_reader(root) as db:
        if db is None:
            return result
        result['ledger_revision'] = db.execute('SELECT revision FROM ledger_meta WHERE singleton=1').fetchone()[0]
        selected = ''
        args = ()
        if identity is not None:
            selected = ''' AND (t.task_id=? OR json_extract(t.payload,'$.plan_id')=? OR EXISTS
                (SELECT 1 FROM runs r WHERE r.task_id=t.task_id AND (r.run_id=? OR r.worker_id=?)))'''
            args = (identity,) * 4
        tasks = rows(db, '''SELECT t.* FROM tasks t JOIN task_heads h USING(task_id,revision)
            WHERE json_extract(t.payload,'$.dependency_packet')=1 AND EXISTS
            (SELECT 1 FROM plan_units u JOIN plan_heads ph ON ph.plan_id=u.plan_id AND ph.revision=u.plan_revision
             WHERE u.task_id=t.task_id AND u.task_revision=t.revision)''' + selected + ' ORDER BY t.task_id LIMIT ?', (*args, LIMIT + 1))
        result['truncated'] = len(tasks) > LIMIT
        tasks = tasks[:LIMIT]
        ids = [t['task_id'] for t in tasks]
        if not ids:
            return result
        marks = ','.join('?' for _ in ids)
        result['runs'] = rows(db, f'''SELECT r.*,t.payload AS assignment,l.launch_id,l.status AS native_status,l.error AS native_error
            FROM runs r JOIN tasks t ON t.task_id=r.task_id AND t.revision=r.task_revision
            LEFT JOIN native_launches l ON l.run_id=r.run_id AND l.status<>'archived'
            WHERE r.task_id IN ({marks}) ORDER BY r.ended_at IS NOT NULL,r.started_at DESC LIMIT ?''', (*ids, LIMIT + 1))
        result['truncated'] |= len(result['runs']) > LIMIT
        result['runs'] = result['runs'][:LIMIT]
        result['leases'] = rows(db, f'SELECT l.* FROM leases l JOIN runs r USING(run_id) WHERE r.task_id IN ({marks}) ORDER BY l.repository,l.canonical_path', ids)
        result['progress'] = rows(db, f'''SELECT h.task_id,h.revision,h.run_id,assigned.task_revision,assigned.ended_at,r.payload AS payload
            FROM progress_heads h JOIN runs assigned ON assigned.run_id=h.run_id
            JOIN record_heads rh ON rh.record_id=h.cursor_id
            JOIN records r ON r.record_id=rh.record_id AND r.version=rh.version
            WHERE h.task_id IN ({marks}) ORDER BY h.task_id''', ids)
        for progress in result['progress']:
            progress['steps'] = rows(db, 'SELECT step_id,status,payload,evidence FROM progress_steps WHERE task_id=? ORDER BY step_id', (progress['task_id'],))
            progress['deliverables'] = rows(db, 'SELECT path,payload,evidence FROM progress_deliverables WHERE task_id=? ORDER BY path', (progress['task_id'],))
        result['checkpoints'] = rows(db, f'''SELECT c.checkpoint_id,c.run_id,c.ledger_revision,c.task_revision,c.packet_hash,c.acknowledged
            FROM checkpoints c JOIN runs r USING(run_id) WHERE r.task_id IN ({marks}) ORDER BY c.rowid DESC LIMIT ?''', (*ids, LIMIT + 1))
        result['checks'] = rows(db, f'''SELECT c.receipt_id,c.run_id,c.check_id,c.exit_code,c.valid,c.duration,c.artifact_hash
            FROM receipts c JOIN runs r USING(run_id) WHERE r.task_id IN ({marks}) ORDER BY c.rowid DESC LIMIT ?''', (*ids, LIMIT + 1))
        result['milestones'] = rows(db, f'SELECT * FROM unit_milestones WHERE task_id IN ({marks}) ORDER BY rowid DESC LIMIT ?', (*ids, LIMIT + 1))
        completions = {(r['task_id'], r['task_revision']): r for r in rows(db, f'SELECT * FROM unit_completions WHERE task_id IN ({marks})', ids)}
        bindings = {r['run_id']: r for r in rows(db, f'''SELECT b.* FROM unit_check_bindings b JOIN runs r USING(run_id)
            WHERE r.task_id IN ({marks})''', ids)}
        for t in tasks:
            active = next((r for r in result['runs'] if r['task_id'] == t['task_id'] and r['ended_at'] is None), None)
            complete = completions.get((t['task_id'], t['revision']))
            t['status'] = ('packet_covered' if complete else active['phase'] if active else 'unassigned')
            t['active_run_id'] = active['run_id'] if active else None
            t['worker_id'] = active['worker_id'] if active else None
            t['completion'] = complete['payload'] if complete else None
            t['binding'] = bindings.get(t['active_run_id'])
            result['tasks'].append(t)
        plan_ids = sorted({t['payload']['plan_id'] for t in tasks})
        if plan_ids:
            pm = ','.join('?' for _ in plan_ids)
            result['plans'] = rows(db, f'SELECT p.* FROM plans p JOIN plan_heads h USING(plan_id,revision) WHERE plan_id IN ({pm}) ORDER BY plan_id', plan_ids)
        # Delivery is instance-wide in the overview and selected by exact run identity in a detail.
        changes = rows(db, '''SELECT c.* FROM delivery_changes c WHERE revision=(SELECT max(revision) FROM delivery_changes d WHERE d.change_id=c.change_id)
            ORDER BY c.rowid DESC LIMIT ?''', (LIMIT + 1,))
        if identity is not None:
            run_ids = {r['run_id'] for r in result['runs']}
            changes = [c for c in changes if c['payload'].get('run_id') in run_ids]
        result['delivery']['changes'] = changes
        if identity is None:
            result['delivery']['candidates'] = rows(db, 'SELECT batch_id,repository,head,boundary,status FROM delivery_validations ORDER BY rowid DESC LIMIT ?', (LIMIT + 1,))
        for key in ('checkpoints', 'checks', 'milestones'):
            result['truncated'] |= len(result[key]) > LIMIT
            result[key] = result[key][:LIMIT]
        for key in ('changes', 'candidates'):
            result['truncated'] |= len(result['delivery'][key]) > LIMIT
            result['delivery'][key] = result['delivery'][key][:LIMIT]
    return result


def collect(root, *, env=None):
    return snapshot(root)


def render_text(document):
    lines = [f"packets={len(document['tasks'])} runs={len(document['runs'])} leases={len(document['leases'])} revision={document['ledger_revision']}"]
    for task in document['tasks']:
        lines.append(f"{task['task_id']}@{task['revision']} {task['status']} worker={task['worker_id'] or '-'} run={task['active_run_id'] or '-'} {task['payload']['goal']}")
    if document['truncated']:
        lines.append('Overview capped; use hx show TASK or PLAN for selected records.')
    return '\n'.join(lines)


def main(argv, root, *, env=None):
    parser = argparse.ArgumentParser(prog='hx board', description='Recorded packet and run state.')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--root', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    result = collect(root, env=env)
    print(json.dumps(result, indent=2) if args.json else render_text(result))
    return 0
