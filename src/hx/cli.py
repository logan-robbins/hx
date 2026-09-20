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

from . import board as board_cmd
from . import doctor as doctor_cmd
from . import install as install_cmd
from .errors import HxError

#: Every command of spec 08, plus `install`, `up`, `doctor`, `ui`, `show`, `repo`, `push`
#: and `upgrade`. The value is the build-lane goal that delivers it (spec 13 milestone + 1).
NOT_IMPLEMENTED = {
    "launch": 2,
    "up": 2,
    "dispatch": 2,
    "goal": 2,
    "task": 2,
    "complete": 2,
    "resume": 2,
    "read": 2,
    "bench": 2,
    "wake": 2,
    "orders": 2,
    "archive": 2,
    "compose": 3,
    "flush": 6,
    "companion": 6,
    "seam": 7,
    "restart": 7,
    "metrics": 8,
    "heartbeat": 9,
    "show": 10,
    "ui": 10,
    "repo": 11,
    "push": 11,
    "upgrade": 11,
}

IMPLEMENTED = {
    "board": board_cmd.main,
    "doctor": doctor_cmd.main,
    "install": install_cmd.main,
}

COMMANDS = sorted(set(IMPLEMENTED) | set(NOT_IMPLEMENTED))

USAGE = f"""usage: hx <command> [options]

hx is the control plane for a HarnessAgent fleet (spec/HARNESS_SPEC.md). HARNESS_ROOT
selects the instance; it defaults to ~/hx and may never be inside the user's ~/.claude.

implemented now:
  board [--json] [--require-done ID...]   the whole instance, and every invariant
  doctor [--json]                         what is here, what is missing, what is broken
  install --skeleton-only --root PATH     create the instance layout and skeleton

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
