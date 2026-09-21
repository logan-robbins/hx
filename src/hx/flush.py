"""`hx flush <id>` — wait until the Companion has caught up (spec 08, 10).

Signal, then block until every stream's state `seq` equals its log head. No timeout: the
callers — `hx complete`, `hx seam`, the `stop` hook — need the step state to be current before
they compose or write a digest, and a flush that gave up early would hand the agent a context
file missing its most recent work.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

from . import timestamps

POLL_SECONDS = 0.1


def signal(root: Path, item_id: str) -> Path:
    """Touch the marker the Companion loop watches (spec 10 step 1)."""
    from .companion import flush_marker

    marker = flush_marker(root, item_id)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(timestamps.now() + "\n")
    return marker


def flush(root: Path, item_id: str, *, env=None, wait: bool = True) -> bool:
    """True once every stream is caught up. Blocks until it is (spec 08).

    With no Companion running there is nothing to wait for: `wait_for` gives up on a running
    loop only when there is none, which is the case in every test that does not start one and
    in an instance whose Companion has not been launched yet.
    """
    from .companion import is_caught_up

    signal(root, item_id)
    if not wait or is_caught_up(root, item_id):
        return True
    if not _companion_running(root, item_id, env):
        # Nothing will ever move the cursor, so blocking would block forever.
        return False
    while not is_caught_up(root, item_id):
        time.sleep(POLL_SECONDS)
    return True


def _companion_running(root: Path, item_id: str, env=None) -> bool:
    """Is there a `<id>:companion` window to wake? `hx launch` starts one (spec 08)."""
    import subprocess

    from . import tmux

    result = subprocess.run(
        [*tmux.tmux_command(env), "list-windows", "-t", f"={item_id}", "-F", "#{window_name}"],
        capture_output=True, text=True, check=False,
    )
    return result.returncode == 0 and "companion" in result.stdout.split()


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx flush", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--no-wait", action="store_true", help="signal without blocking")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    caught_up = flush(root, args.id, env=env, wait=not args.no_wait)
    print(f"HX-FLUSH {args.id} {'ok' if caught_up else 'no-companion'}")
    return 0
