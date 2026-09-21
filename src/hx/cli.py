"""`hx` — subcommand dispatch for the whole command set of spec 08.

Every command resolves HARNESS_ROOT through `hx.root.resolve_root` before doing anything
else, so the standing refusal (a root that is, or resolves inside, the user's `~/.claude`)
applies to all of them, implemented or not.

Commands that a later milestone delivers exit 2 with
`hx: <cmd>: not implemented (build-N)`, where N is the build-lane goal that delivers it —
spec 13's milestone plus one, since goal build-1 is M0.
"""

from __future__ import annotations

import sys
from pathlib import Path

from . import archive as archive_cmd
from . import bench as bench_cmd
from . import board as board_cmd
from . import complete as complete_cmd
from . import companion as companion_cmd
from . import compose as compose_cmd
from . import dispatch as dispatch_cmd
from . import doctor as doctor_cmd
from . import flush as flush_cmd
from . import goal as goal_cmd
from . import install as install_cmd
from . import lifecycle
from . import orders as orders_cmd
from . import read as read_cmd
from . import resume as resume_cmd
from . import seam as seam_cmd
from . import show as show_cmd
from . import task as task_cmd
from . import ui_cmd
from . import wake as wake_cmd
from .errors import HxError

#: Every command of spec 08, plus `install`, `up`, `doctor`, `ui` and `show`. The v1 cut
#: (spec 14 D25) removed `repo`, `push` and `upgrade`: hx does not manage git or its own
#: version. The value is the build-lane goal that delivers each remaining command.
NOT_IMPLEMENTED = {
    "metrics": 8,
}

IMPLEMENTED = {
    "archive": archive_cmd.main,
    "bench": bench_cmd.main,
    "board": board_cmd.main,
    "companion": companion_cmd.main,
    "complete": complete_cmd.main,
    # `hx compose` and `hx flush` are call sites the hooks and `hx complete` already use;
    # what they do arrives at M2 and M5 (spec 13).
    "compose": compose_cmd.main,
    "dispatch": dispatch_cmd.main,
    "doctor": doctor_cmd.main,
    "flush": flush_cmd.main,
    "goal": goal_cmd.main,
    "heartbeat": lifecycle.main_heartbeat,
    "install": install_cmd.main,
    "launch": lifecycle.main_launch,
    "orders": orders_cmd.main,
    "read": read_cmd.main,
    "restart": lifecycle.main_restart,
    "resume": resume_cmd.main,
    "seam": seam_cmd.main,
    "show": show_cmd.main,
    "task": task_cmd.main,
    "ui": ui_cmd.main,
    "up": lifecycle.main_up,
    "wake": wake_cmd.main,
}

COMMANDS = sorted(set(IMPLEMENTED) | set(NOT_IMPLEMENTED))

USAGE = f"""usage: hx <command> [options]

hx is the control plane for a HarnessAgent fleet (spec/HARNESS_SPEC.md). HARNESS_ROOT
selects the instance; it defaults to ~/hx and may never be inside the user's ~/.claude.

the control plane:
  launch ID                    idle work item, home, tmux session, goal if working
  dispatch ID ORDER [ID ORDER] validate the orders, then working; the files are consumed
  goal ID [--now]              paste the pointer into a worker's pane
  task                         print your own order and its addenda
  complete OUTCOME             the agent's last action; checks run here
  resume ID ADDENDUM           continue a blocked or decision item
  bench ID                     archive the body and free the id
  seam ID                      flush, recompose, and cut the conversation
  read ID [--full]             the Digest and the open decision
  companion ID [--once]        the Companion loop, one per agent
  flush ID                     wait for the Companion to reach the log head
  restart ID / up / heartbeat  relaunch, boot, and the human's own cron
  wake partner TEXT            the one way anything reaches the Partner

read-only views:
  board [--json]                          a plain listing of what is on disk
  show ID [--json]                        everything hx knows about one id
  orders [--json] / archive [--json]      the task records, and what has been archived
  ui [--port N]                           the read-only web view on 127.0.0.1
  doctor [--json]                         what is here, what is missing, what is broken
  install --root PATH [--claude B]        create the instance (spec 17.2)

every command of spec 08:
  {chr(10) + '  '}{'  '.join(COMMANDS)}
"""


def _root_for(argv: list[str], env) -> Path:
    """Resolve the root, honouring a `--root` anywhere in the command's own arguments."""
    from .root import resolve_root

    override = None
    for index, arg in enumerate(argv):
        if arg == "--root" and index + 1 < len(argv):
            override = argv[index + 1]
        elif arg.startswith("--root="):
            override = arg.split("=", 1)[1]
    return resolve_root(override, env)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    import os

    env = os.environ

    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0 if argv else 2
    if argv[0] in ("-V", "--version"):
        from . import __version__

        print(f"hx {__version__}")
        return 0

    command, rest = argv[0], argv[1:]
    if command not in IMPLEMENTED and command not in NOT_IMPLEMENTED:
        print(f"hx: {command}: unknown command; try `hx --help`", file=sys.stderr)
        return 2

    try:
        # The standing refusal runs before anything else, for every command (ORCHESTRATION.md).
        root = _root_for(rest, env)
        if command in NOT_IMPLEMENTED:
            print(
                f"hx: {command}: not implemented (build-{NOT_IMPLEMENTED[command]})",
                file=sys.stderr,
            )
            return 2
        return IMPLEMENTED[command](rest, root, env=env)
    except HxError as exc:
        print(f"hx: {exc.message}", file=sys.stderr)
        return exc.exit_code
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
