"""`hx flush <id>` — wait until every stream's state `seq` equals its log head (spec 08).

The Companion is build-6 (M5), so there is nothing to wait for yet and this is a no-op that
returns cleanly. `hx complete`, `hx seam` and the hooks already call it, so the call sites are
in place before the loop behind them exists.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def flush(root: Path, item_id: str, *, env=None) -> bool:
    """True when every stream is flushed. Always true until the Companion lands (M5)."""
    return True


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx flush", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    flush(root, args.id, env=env)
    print(f"HX-FLUSH {args.id} ok")
    return 0
