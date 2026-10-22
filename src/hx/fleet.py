"""`hx fleet pause|resume`: the operator's switch for the plan executor.

The flag is `run/dispatch-paused` in the root. While it exists no executor pass
admits or launches a unit (`plan_dispatch.paused`); runs already admitted keep
running and the loop keeps reconciling them.
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from .continuity_store import canonical
from .store import atomic_write_text


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx fleet", epilog="example: hx fleet pause")
    parser.add_argument("--root")
    actions = parser.add_subparsers(dest="action", required=True)
    for action in ("pause", "resume"):
        actions.add_parser(action).add_argument("--root", default=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    flag = Path(root) / "run" / "dispatch-paused"
    if args.action == "pause":
        flag.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(flag, canonical({"paused_at": time.time()}) + "\n")
    else:
        flag.unlink(missing_ok=True)
    print(canonical({"dispatch": "paused" if flag.exists() else "running", "flag": str(flag)}))
    return 0
