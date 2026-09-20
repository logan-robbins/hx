"""`hx compose <id> <stream>` — the single context file handed over at every boundary.

Spec 07.3 defines the sections and M2 builds it. Until then this writes nothing and reports
the path it will write, so `hx resume` and `hx restart` can call it in the right place now.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def context_path(root: Path, item_id: str, stream: str) -> Path:
    return root / "run" / item_id / f"{stream}.context.md"


def compose(root: Path, item_id: str, stream: str, *, env=None) -> Path:
    """Return the context file's path. Composing it is M2 (spec 07.3)."""
    return context_path(root, item_id, stream)


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx compose", add_help=True)
    parser.add_argument("id")
    parser.add_argument("stream")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    print(compose(root, args.id, args.stream, env=env))
    return 0
