"""`hx ui` — the read-only web view of one instance (spec 08, 16).

The ui lane owns `src/hx/ui/**`; this is only the CLI entry that hands it the root. `serve`
blocks until the server is shut down, reads the port from `config/ui.json` itself, and writes
nothing under `HARNESS_ROOT` except `run/ui-token` (handoff/ui-to-build.md).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .errors import HxError


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx ui", add_help=True)
    parser.add_argument("--port", type=int, default=None, help="override config/ui.json")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if not root.is_dir():
        raise HxError(f"{root}: HARNESS_ROOT does not exist; run `hx install --root {root}` first")

    from .ui.server import serve

    serve(root, args.port)
    return 0
