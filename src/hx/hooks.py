"""`hx-hook` — the hook entrypoint every agent home points at (spec 09).

`adapters/claude/install.sh` writes `<hook_bin> --id <id> <event>` into
`run/<id>/home/settings.json` for each of the nine hx events. This module is the plumbing they
share: read the payload from stdin, resolve the root, dispatch, and — above all — never take a
tool call down with it.

**Failure policy.** A hook that crashes must not break the agent. A malformed payload, a
missing file, an unexpected exception: logged to `logs/<id>/hook-errors.log` and **allowed**
(exit 0). The one exception is `guard`, where an error means hx could not prove the call was
safe, so it **denies** (exit 2). No timeouts, no network.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

from . import hook_context, hook_guard, hook_log, hook_stop, hook_subagent
from .errors import HxError
from .ids import is_id
from .root import resolve_root

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
    # The turn marker and the `goal-pending` consumption ship with the M4 batch (spec 13).
    "stop": 5,
}

IMPLEMENTED = (
    "context", "guard", "log", "subagent-start", "subagent-stop", "subagent-result", "stop",
)

#: The handlers that produce output on stdout, and what form it takes. `context` prints one
#: plain line (SessionStart injects stdout); the two subagent hooks must return JSON, because
#: plain stdout is not injected for them (spec 09.1, docs/en/hooks#subagentstart).
_HANDLERS = {
    "context": hook_context.handle,
    "log": hook_log.handle,
    "subagent-start": hook_subagent.start,
    "subagent-stop": hook_subagent.stop,
    "subagent-result": hook_subagent.result,
    "stop": hook_stop.handle,
}

#: `guard` denies on error; every other event allows, because a broken hook must never be the
#: reason an agent stops working (spec 09.1: "Deny = exit 2 ... Never exit 1").
DENY_ON_ERROR = ("guard",)

ERROR_LOG = "hook-errors.log"


def error_log_path(root: Path, item_id: str) -> Path:
    return root / "logs" / item_id / ERROR_LOG


def log_error(root: Path, item_id: str, event: str, message: str) -> None:
    """Record why a hook could not do its job. Never raises: this is the last line of defence."""
    try:
        from . import timestamps

        path = error_log_path(root, item_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as handle:
            handle.write(f"{timestamps.now()} {event}: {message}\n")
            handle.flush()
    except Exception:
        pass


def read_payload(stream=None) -> dict:
    """The hook payload from stdin. An empty or malformed body is an error the caller logs."""
    text = (stream or sys.stdin).read()
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError(f"hook payload is {type(data).__name__}, not an object")
    return data


def check_id(item_id: str, env) -> None:
    """The baked-in `--id` must match `HARNESS_ID` when the session sets one (goal item 4).

    A mismatch means the settings file of one agent's home is being used by another, which is
    exactly what rule 2 of the guard exists to prevent.
    """
    env = os.environ if env is None else env
    running = env.get("HARNESS_ID")
    if running and running != item_id:
        raise HxError(f"--id {item_id} does not match HARNESS_ID {running}")


def main(argv: list[str] | None = None, *, stdin=None, env=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    env = os.environ if env is None else env

    parser = argparse.ArgumentParser(prog="hx-hook", add_help=True)
    parser.add_argument("--id", required=True, dest="item_id", help="the id, baked in by install.sh")
    parser.add_argument("event", choices=sorted(EVENTS), help="the hx hook event (spec 09.1)")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    event, item_id = args.event, args.item_id
    denies_on_error = event in DENY_ON_ERROR
    root = None

    try:
        # The root first, so everything after it has somewhere to log to, and so the standing
        # `~/.claude` refusal applies before the hook touches anything.
        root = resolve_root(args.root, env)
        if not is_id(item_id):
            raise HxError(f"--id {item_id} is not an id (`partner` or `<pod>-NNN`)")
        check_id(item_id, env)
        payload = read_payload(stdin)

        if event in _HANDLERS:
            code, line = _HANDLERS[event](payload, item_id, root, env=env)
            if line:
                print(line)
            return code

        if event == "guard":
            decision = hook_guard.decide(payload, item_id, root)
            if decision.allow:
                return 0
            print(f"{decision.reason} (guard rule {decision.rule}, spec 09.2)", file=sys.stderr)
            return 2

        print(
            f"hx-hook: {event}: not implemented (build-{EVENTS[event]}) for --id {item_id}",
            file=sys.stderr,
        )
        return 0

    except SystemExit:
        raise
    except BaseException as exc:
        detail = f"{type(exc).__name__}: {exc}"
        if root is not None:
            log_error(root, item_id, event, detail + "\n" + traceback.format_exc())
        print(f"hx-hook: {event}: {detail}", file=sys.stderr)
        if denies_on_error:
            # hx could not prove the call was safe, so it is not allowed (spec 09.2).
            return 2
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
