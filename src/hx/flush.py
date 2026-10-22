"""Run the Partner's owed Companion passes now.

The application loop runs them in its Companion slots; `hx flush partner` runs
every stream with new records inline, one Messages API exchange each. Workers
drain durable observations through their native run controller.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def flush(root: Path, item_id: str, *, env=None) -> bool:
    """True once every stream is caught up. A failed pass leaves its stream behind."""
    from . import companion as companion_mod
    companion_mod.require_partner(item_id)
    companion_mod.run_due(root, item_id, force=True, env=env)
    return companion_mod.is_caught_up(root, item_id)


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx flush", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.id != "partner":
        parser.error("worker capture drains through its native run")
    caught_up = flush(root, args.id, env=env)
    print(f"HX-FLUSH {args.id} {'ok' if caught_up else 'behind'}")
    return 0
