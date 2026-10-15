"""Read recorded packet status and, explicitly, its assignment and proof headers."""
import argparse
import json
from . import board, show


def status(root, item_id, *, env=None):
    return board.render_text(show.collect(root, item_id, env=env))


def read(root, item_id, *, full=False, detail=False, env=None):
    result = show.collect(root, item_id, env=env)
    if full:
        return json.dumps(result, indent=2)
    if detail:
        return show.render_text(result)
    return board.render_text(result)


def main(argv, root, *, env=None):
    parser = argparse.ArgumentParser(prog='hx read')
    parser.add_argument('id')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--detail', action='store_true')
    mode.add_argument('--full', action='store_true')
    parser.add_argument('--root', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    print(read(root, args.id, full=args.full, detail=args.detail, env=env))
    return 0
