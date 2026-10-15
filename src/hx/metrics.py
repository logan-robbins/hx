"""Recorded packet, execution and proof metrics from the authoritative ledger."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import time

from . import board

FIGURES = ('chat_bytes', 'output_tokens', 'turns')


def _observation(root, payload):
    """The captured public observation; a retired or unreadable one contributes nothing."""
    if 'observation' in payload:
        return payload['observation']
    if 'artifact_hash' not in payload:
        return None
    try:
        data = (Path(root) / 'state' / 'artifacts' / payload['artifact_hash']).read_bytes()
    except OSError:
        return None
    return json.loads(data) if hashlib.sha256(data).hexdigest() == payload['artifact_hash'] else None


def agent_figures(root, runs):
    """Per-worker chat bytes, output tokens and model turns from captured main-session events.

    Only sources that record a figure contribute (docs/prompt-runtime.md, per flavor);
    a figure no captured record reports stays None. Nothing is estimated.
    """
    from .codex_rollout import DECODER
    workers = {run['run_id']: run['worker_id'] for run in runs}
    result = {worker: {'flavors': set(), **dict.fromkeys(FIGURES)} for worker in workers.values()}
    if not workers:
        return {}
    def add(figures, key, value):
        figures[key] = (figures[key] or 0) + value
    claude = {}
    with board.ledger_reader(root) as db:
        if db is None:
            return {}
        marks = ','.join('?' for _ in workers)
        for row in db.execute(f'SELECT DISTINCT run_id,adapter FROM native_launch_contracts WHERE run_id IN ({marks})', tuple(workers)):
            result[workers[row['run_id']]]['flavors'].add(row['adapter'])
        # Child streams and hook copies of transcript messages are excluded.
        for row in db.execute(f"""SELECT e.event_id,e.run_id,e.kind,e.payload,
                (SELECT o.decoder_version FROM event_origins o WHERE o.event_id=e.event_id LIMIT 1) AS decoder,
                (SELECT b.adapter FROM native_event_origins n JOIN native_bindings b USING(binding_id)
                 WHERE n.event_id=e.event_id LIMIT 1) AS adapter
                FROM events e WHERE e.run_id IN ({marks}) AND e.stream_id LIKE 'native:%'
                AND e.kind IN ('assistant_message','boundary') ORDER BY e.rowid""", tuple(workers)):
            source = row['decoder'] if row['decoder'] in {'claude-v1', DECODER} else row['adapter']
            if source not in {'claude-v1', DECODER, 'pi', 'meta'}:
                continue
            observation = _observation(root, json.loads(row['payload']))
            if observation is None:
                continue
            figures, data, usage = result[workers[row['run_id']]], observation['data'], observation.get('usage') or {}
            if row['kind'] == 'assistant_message' and source != 'meta' and data.get('claim') and isinstance(data.get('text'), str):
                add(figures, 'chat_bytes', len(data['text'].encode()))
            if source == 'claude-v1' and row['kind'] == 'assistant_message' and type(usage.get('output_tokens')) is int:
                # Records of one API response repeat its usage; count the response once.
                key = (workers[row['run_id']], data.get('message_id') or row['event_id'])
                claude[key] = max(claude.get(key, 0), usage['output_tokens'])
            elif source == DECODER and data.get('source') == 'token_usage_record' and type(usage.get('output_tokens')) is int:
                add(figures, 'turns', 1)
                add(figures, 'output_tokens', usage['output_tokens'])
            elif source == 'pi' and row['kind'] == 'assistant_message' and type(usage.get('output')) is int:
                add(figures, 'turns', 1)
                add(figures, 'output_tokens', usage['output'])
            elif (source == 'meta' and data.get('source') == 'model_response' and data.get('status') == 'success'
                  and type(output := usage.get('output_tokens', usage.get('output'))) is int):
                add(figures, 'turns', 1)
                add(figures, 'output_tokens', output)
    for (worker, _), output in claude.items():
        add(result[worker], 'turns', 1)
        add(result[worker], 'output_tokens', output)
    return {worker: {**figures, 'flavors': sorted(figures['flavors'])} for worker, figures in sorted(result.items())}


def collect_fleet(root, *, env=None, identity=None):
    view = board.snapshot(root, identity)
    return {'schema_version': 2, 'root_abs': view['root_abs'], 'ts': view['ts'],
            'ledger_revision': view['ledger_revision'], 'identity': identity,
            'packets': len(view['tasks']), 'packet_statuses': dict(Counter(t['status'] for t in view['tasks'])),
            'runs': len(view['runs']), 'active_runs': sum(r['ended_at'] is None for r in view['runs']),
            'leases': len(view['leases']), 'check_receipts': len(view['checks']),
            'valid_checks': sum(bool(c['valid']) for c in view['checks']),
            'failed_checks': sum(c['exit_code'] != 0 for c in view['checks']),
            'invalid_checks': sum(not c['valid'] for c in view['checks']),
            'check_seconds': sum(c['duration'] for c in view['checks']),
            'checkpoints': len(view['checkpoints']),
            'acknowledged_checkpoints': sum(bool(c['acknowledged']) for c in view['checkpoints']),
            'milestones': dict(Counter(m['kind'] for m in view['milestones'])),
            'delivery_statuses': dict(Counter(c['status'] for c in view['delivery']['changes'])),
            'agents': agent_figures(root, [r for r in view['runs'] if identity in {None, r['worker_id']}]
                                    or view['runs']),
            'truncated': view['truncated'], 'scope': 'Recorded rows in the bounded projection.'}


def per_id(root, item_id):
    from .show import collect
    collect(root, item_id)  # unknown identities must not look like zero activity
    return collect_fleet(root, identity=item_id)


def render_fleet_text(doc):
    return '\n'.join(f'{key}: {json.dumps(value, ensure_ascii=False)}' for key, value in doc.items())


render_per_id_text = render_fleet_text


def watch(root, interval=2.0, *, env=None):
    revision = None
    while True:
        result = collect_fleet(root, env=env)
        if result['ledger_revision'] != revision:
            yield {'type': 'snapshot', 'metrics': result}
            revision = result['ledger_revision']
        time.sleep(interval)


def main(argv, root, *, env=None):
    parser = argparse.ArgumentParser(prog='hx metrics', description='Recorded packet and proof counts.')
    parser.add_argument('id', nargs='?')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--watch', nargs='?', type=float, const=2.0)
    parser.add_argument('--root', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.watch is not None:
        if args.watch <= 0 or args.id:
            parser.error('--watch needs a positive interval and no identity')
        try:
            for item in watch(root, args.watch, env=env):
                print(json.dumps(item), flush=True)
        except KeyboardInterrupt:
            return 0
    else:
        result = per_id(root, args.id) if args.id else collect_fleet(root, env=env)
        print(json.dumps(result, indent=2) if args.json else render_fleet_text(result))
    return 0
