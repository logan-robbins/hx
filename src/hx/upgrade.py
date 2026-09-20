"""`hx upgrade` — move the pin, but only to a version the live suite has passed on (spec 17.6).

A failed check leaves the pinned version in place and says why. Restarting sessions onto the
new binary is `hx restart <id>`, at a boundary, one id at a time — this command does not do it
for you, because a restart mid-turn would throw away a turn.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import claude_bin
from .errors import NotFound


def upgrade(root: Path, *, binary: str | None = None, env=None) -> dict:
    pin = claude_bin.load_pin(root)
    if pin is None:
        raise NotFound(
            f"{claude_bin.CONFIG}: nothing pinned yet; `hx install` records it (spec 17.2 step 1)"
        )
    target = binary or pin.get("bin") or claude_bin.find_binary()
    version = claude_bin.probe(target)
    # Refuses before writing anything: the old pin survives a rejected upgrade.
    claude_bin.require_tested(version)

    previous = pin.get("version")
    claude_bin.write_pin(root, target, version)
    return {"bin": target, "version": version, "previous": previous, "changed": version != previous}


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx upgrade", add_help=True)
    parser.add_argument("--claude", dest="binary", default=None, help="the binary to pin")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    result = upgrade(root, binary=args.binary, env=env)
    if result["changed"]:
        print(f"HX-UPGRADE {result['previous'] or 'none'} -> {result['version']}")
        print("restart each session at a boundary, one at a time: `hx restart <id>` (spec 17.6)")
    else:
        print(f"HX-UPGRADE unchanged {result['version']}")
    return 0
