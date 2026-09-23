"""`hx flush <id>` — wait until the Companion has caught up (spec 08, 10).

Signal, then block until every stream's state `seq` equals its log head. No timeout: the
remaining blocking callers — `hx complete` and `hx flush` itself, both deliberate acts at a
known-quiet point — need the step state to be current before they compose or write a digest,
and a flush that gave up early would hand the agent a context file missing its most recent
work. The automatic paths never block: the `stop`-hook seam signals, checks readiness, and
defers with a watcher record when the take is not ready, and `precompact` signals and
returns.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from . import timestamps

POLL_SECONDS = 0.1


def signal(root: Path, item_id: str, *, env=None) -> list[str]:
    """Wake the Companion for every stream that still has new records (spec 10)."""
    from . import companion as companion_mod

    return companion_mod.wake_due(root, item_id, force=True, env=env)


def flush(root: Path, item_id: str, *, env=None, wait: bool = True) -> bool:
    """True once every stream is caught up. Blocks until it is (spec 08).

    With no Companion running there is nothing to wait for: `wait_for` gives up on a running
    loop only when there is none, which is the case in every test that does not start one and
    in an instance whose Companion has not been launched yet.
    """
    from .companion import is_caught_up

    signal(root, item_id, env=env)
    if not wait or is_caught_up(root, item_id):
        return True
    if not _companion_running(root, item_id, env):
        # Nothing will ever move the cursor, so blocking would block forever.
        return False
    while not is_caught_up(root, item_id):
        time.sleep(POLL_SECONDS)
        # Re-signal each poll. A wake whose pane was mid-turn leaves the pass file on disk;
        # this is the next wake that delivers it. Same mechanism, no queue.
        signal(root, item_id, env=env)
    return True


def _companion_running(root: Path, item_id: str, env=None) -> bool:
    """Is there a `<id>:companion` window to wake? `hx launch` starts one (spec 08, 10)."""
    from .companion import is_running

    return is_running(item_id, env)


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx flush", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--no-wait", action="store_true", help="signal without blocking")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    caught_up = flush(root, args.id, env=env, wait=not args.no_wait)
    print(f"HX-FLUSH {args.id} {'ok' if caught_up else 'no-companion'}")
    return 0
