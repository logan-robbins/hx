"""Request verified completion of the caller's ledger-owned native assignment."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from .caller import require_agent_caller
from .continuity_store import ContinuityStore, canonical
from .errors import Refused
from .ids import PARTNER


def complete(root: Path, outcome: str = "done", *, item_id: str | None = None, env=None) -> dict:
    """Record intent only. Runtime checks and publication establish completion."""
    child = os.environ if env is None else env
    worker = require_agent_caller("complete", child)
    if worker == PARTNER:
        raise Refused("the Partner orchestrates plans and cannot complete a worker run")
    if item_id is not None and item_id != worker:
        raise Refused("completion can only be requested by the assigned worker")
    if outcome != "done":
        raise Refused("record blockers and decisions with hx progress; the Partner controls unit admission")
    run_id = child.get("HX_CONTINUITY_RUN")
    if not run_id:
        raise Refused("completion requires the active native assignment's HX_CONTINUITY_RUN")
    from .native_completion import request
    with ContinuityStore(root) as ledger:
        return request(ledger, run_id, worker)


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx complete", epilog="Example: hx complete done")
    parser.add_argument("outcome", choices=["done"])
    parser.add_argument("--id", help="assigned worker ID (default: HARNESS_ID)")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    print(canonical(complete(root, args.outcome, item_id=args.id, env=env)))
    print("Completion requested. End this turn; the runtime verifies checks and publishes the unit.")
    return 0
