"""Run one task-scoped command with durable identity and bounded pre-delivery output."""
from __future__ import annotations

import argparse
import json
import os
import uuid
from pathlib import Path

from . import checks, output_reduction, passes
from .continuity_store import Conflict, ContinuityStore, canonical, digest, _id
from .errors import ValidationError

CAPTURE_BYTES = 1024 * 1024
TASK_CAPTURE_BYTES = 16 * CAPTURE_BYTES


def execute(store, run_id, request_id, argv, *, cwd=None, env=None):
    _id(request_id)
    child = dict(os.environ if env is None else env)
    if not argv or len(argv) > 128 or any(not isinstance(value, str) or not value or '\0' in value for value in argv):
        raise ValidationError('tool execution requires a bounded argument array')
    if len(canonical(argv).encode()) > 16384:
        raise ValidationError('tool command exceeds 16384 bytes')
    with store.transaction() as tx:
        run = passes._active_run(tx, run_id)
        if child.get('HARNESS_ID') not in {None, run['worker_id']}:
            raise Conflict('tool caller does not own this assignment')
        task = tx.task(run['task_id'])
        workdir = Path(task['payload']['workdir']).resolve()
        directory = (workdir / (cwd or '.')).resolve()
        if not directory.is_relative_to(workdir):
            raise ValidationError('tool execution directory must remain inside the assignment')
        identity = digest([run['task_revision'], argv, str(directory)])
        previous = tx.db.execute('SELECT * FROM tool_executions WHERE run_id=? AND request_id=?', (run_id, request_id)).fetchone()
        if previous:
            if previous['input_hash'] != identity:
                raise Conflict('tool request identity was reused with different arguments')
            if previous['status'] == 'running':
                raise Conflict('tool execution remains uncertain; do not rerun it')
            result = json.loads(previous['payload'])
        else:
            used = tx.db.execute("SELECT coalesce(sum(json_extract(payload,'$.output_bytes')),0) FROM tool_executions WHERE run_id=?", (run_id,)).fetchone()[0]
            if used + CAPTURE_BYTES > TASK_CAPTURE_BYTES:
                raise ValidationError('task tool-output quota reached; reduce retained output before executing more commands')
            result = None
            output = store.root / 'state/tool-output' / str(uuid.uuid4())
            tx._change()
            tx.db.execute("INSERT INTO tool_executions VALUES(?,?,?,'running',?)", (run_id, request_id, identity, canonical({'path': str(output), 'output_bytes': CAPTURE_BYTES})))
    if result is None:
        output.parent.mkdir(parents=True, exist_ok=True)
        observed = checks.execute(argv, directory, child, output, 60, CAPTURE_BYTES)
        with store.transaction() as tx:
            artifact = tx.put_artifact_file(output, owner_type='tool', owner_id=run_id + ':' + request_id, slot='output', max_bytes=CAPTURE_BYTES)
            result = {**observed, 'artifact_hash': artifact, 'command': argv}
            event = tx.append_event(run_id, 'tool-exec', request_id, 'tool_result', result)
            result['event_id'] = event['event_id']
            tx.db.execute('INSERT INTO artifact_refs VALUES(?,?,?,?)', ('event', event['event_id'], 'output', artifact))
            tx.db.execute("UPDATE tool_executions SET status='captured',payload=? WHERE run_id=? AND request_id=?", (canonical(result), run_id, request_id))
        output.unlink(missing_ok=True)
    # Provider failure leaves captured output and command identity intact. A
    # deliberate retry can reduce that same output; it never repeats the command.
    reduced = output_reduction.reduce(store, run_id, result['event_id'], env=child)
    return {key: result[key] for key in ('exit_code', 'reason', 'output_bytes', 'event_id')} | reduced


def main(argv, root, *, env=None):
    parser = argparse.ArgumentParser(prog='hx tool-exec')
    parser.add_argument('--run')
    parser.add_argument('--request', required=True)
    parser.add_argument('--cwd')
    parser.add_argument('--root')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    child = dict(os.environ if env is None else env)
    run_id = args.run or child.get('HX_CONTINUITY_RUN')
    if not run_id:
        raise ValidationError('tool execution requires its planned run')
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    with ContinuityStore(root) as store:
        result = execute(store, run_id, args.request, command, cwd=args.cwd, env=child)
    print(canonical(result))
    return 2 if result.get('requires_read') or result.get('reason') else int(result['exit_code'] != 0)
