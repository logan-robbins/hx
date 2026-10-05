"""Cached, bounded Jev judgments. Code owns versions, limits, and mutations."""
from __future__ import annotations

import json
import os
import uuid

from . import jev, passes, native_processes
from .continuity_store import Conflict, canonical, digest
from .errors import ValidationError


def decide(store, run_id, policy, state, questions, *, binding=None, env=None):
    encoded = jev.encode(state, questions)
    with store.transaction() as tx:
        run = passes._active_run(tx, run_id)
        identity = digest([jev.MODEL, policy, run_id, run['task_revision'], binding, encoded.decode()])
        old = tx.db.execute('SELECT payload FROM retrieval_runs WHERE task_id=? AND input_hash=?',
                            (run['task_id'], identity)).fetchone()
        if old:
            result = json.loads(old[0])
            if result['status'] == 'complete':
                return result
            owner = result.get('owner')
            if owner is None or native_processes.current(owner):
                raise Conflict('Jev decision is already in flight')
            # Judgment calls cannot mutate the application. Once the original
            # caller is gone, a fresh caller may replace its missing answer.
            tx._change()
            tx.db.execute('DELETE FROM retrieval_runs WHERE task_id=? AND input_hash=?', (run['task_id'], identity))
        try:
            key = jev.credential(store.root, env)
        except jev.Unavailable as exc:
            raise ValidationError('Jev decision stopped: ' + str(exc)) from None
        request = str(uuid.uuid4())
        tx._change()
        tx.db.execute('INSERT INTO retrieval_runs VALUES(?,?,?,?)',
                      (request, run['task_id'], identity, canonical({'status': 'running', 'policy': policy,
                          'owner': native_processes.probe(os.getpid())['identity']})))
    try:
        answer = jev.evaluate(state, questions, key=key)
        result = {'status': 'complete', 'retrieval_id': request, 'policy': policy, **answer}
        with store.transaction() as tx:
            current = passes._active_run(tx, run_id)
            if current['task_revision'] != run['task_revision']:
                raise Conflict('task changed during Jev decision')
            tx._change()
            tx.db.execute('UPDATE retrieval_runs SET payload=? WHERE retrieval_id=?', (canonical(result), request))
            tx.db.execute('''DELETE FROM retrieval_runs WHERE task_id=? AND retrieval_id IN
                (SELECT retrieval_id FROM retrieval_runs WHERE task_id=? AND json_extract(payload,'$.status')='complete'
                 ORDER BY rowid DESC LIMIT -1 OFFSET 128)''', (run['task_id'], run['task_id']))
        return result
    except (jev.Unavailable, Conflict, ValidationError) as exc:
        with store.transaction() as tx:
            tx._change()
            tx.db.execute('DELETE FROM retrieval_runs WHERE retrieval_id=?', (request,))
        if isinstance(exc, jev.Unavailable):
            raise ValidationError('Jev decision stopped: ' + str(exc)) from None
        raise
