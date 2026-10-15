"""Recorded packet, execution and proof metrics from the authoritative ledger."""
import argparse
from collections import Counter
import json
import time

from . import board


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
