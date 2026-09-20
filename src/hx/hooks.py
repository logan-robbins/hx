"""`hx-hook` — the hook entrypoint every agent home points at (spec 09).

`adapters/claude/install.sh` writes `<hook_bin> --id <id> <event>` into
`run/<id>/home/settings.json` for each of the nine hx events. The event handlers land with
their milestones (spec 13: `context` at M2, `guard` at M3, the stream and subagent hooks at
M4, `stop`'s seam handshake at M6); until then the entrypoint exists, validates its
arguments, and says which build goal delivers the event.
"""

from __future__ import annotations

import argparse
import sys

#: The hx event vocabulary of spec 09.1, and the build goal that delivers each
#: (`handoff/orchestrator-to-build.md`, 2026-09-20 renumbering).
EVENTS = {
    "context": 3,
    "guard": 3,
    "log": 5,
    "subagent-start": 5,
    "subagent-stop": 5,
    "subagent-result": 5,
    "precompact": 5,
    "postcompact": 5,
    "stop": 7,
}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    parser = argparse.ArgumentParser(prog="hx-hook", add_help=True)
    parser.add_argument("--id", required=True, help="the HarnessAgent id, baked in by install.sh")
    parser.add_argument("event", choices=sorted(EVENTS), help="the hx hook event (spec 09.1)")
    args = parser.parse_args(argv)

    # A hook must never take a tool call down with it: `guard` denies with exit 2 and every
    # other event is advisory, so an unimplemented event exits 0 and says so on stderr.
    print(
        f"hx-hook: {args.event}: not implemented (build-{EVENTS[args.event]}) for --id {args.id}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
