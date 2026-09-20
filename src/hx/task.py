"""`hx task` — print the caller's own full order and its addenda (spec 08).

The agent never needs this to start work: the `/goal` pointer names the work item and the
order is copied into it verbatim. This is for re-reading the order without re-reading the
whole item, and it is the only agent-facing command besides `hx complete`.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .caller import require_agent_caller
from .errors import NotFound
from .tasks import load_tasks


def task(root: Path, item_id: str) -> str:
    entry = load_tasks(root).get(item_id)
    if entry is None:
        raise NotFound(
            f"{item_id}: nothing in tasks.json; you have not been dispatched an order yet (spec 08)"
        )
    parts = [entry.get("order") or "(no order recorded)"]
    for addendum in entry.get("addenda") or []:
        parts.append(f"## Order addendum {addendum.get('ts', '')}\n\n{addendum.get('text', '')}")
    return "\n\n".join(part.strip("\n") for part in parts)


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx task", add_help=True)
    parser.add_argument("--id", help="the id to print (default: HARNESS_ID)")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    item_id = args.id or require_agent_caller("task", env)
    print(task(root, item_id))
    return 0
