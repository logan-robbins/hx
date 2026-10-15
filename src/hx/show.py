"""Inspect a ledger task, run, worker or plan identity."""
import argparse
import json
from . import board
from .errors import NotFound


def collect(root, item_id, *, env=None):
    result = board.snapshot(root, item_id)
    if not result['tasks']:
        raise NotFound(f'{item_id}: no packet, run, worker or plan in the ledger')
    return {'identity': item_id, **result}


def render_text(document):
    return board.render_text(document) + '\n' + json.dumps({key: document[key] for key in ('progress', 'checks', 'milestones', 'delivery')}, indent=2)


def main(argv, root, *, env=None):
    parser = argparse.ArgumentParser(prog='hx show')
    parser.add_argument('id')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--root', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    result = collect(root, args.id, env=env)
    print(json.dumps(result, indent=2) if args.json else render_text(result))
    return 0
