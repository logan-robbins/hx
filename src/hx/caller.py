"""Who is allowed to run what (spec 08).

Agent-side commands identify the caller by `HARNESS_ID`, set on the tmux session by
`start.sh`. Partner commands refuse when `HARNESS_ID` is set and is not `partner`; system
commands (`hx up`, `hx heartbeat`) run from systemd and cron with no `HARNESS_ID` at all.
"""

from __future__ import annotations

import os

from .errors import HxError, Refused
from .ids import PARTNER, is_id


def caller(env=None) -> str | None:
    env = os.environ if env is None else env
    value = env.get("HARNESS_ID")
    return value or None


def require_partner_caller(command: str, env=None) -> None:
    """`hx dispatch`, `resume`, `bench`, `launch`, `restart` and `read` are the Partner's."""
    who = caller(env)
    if who is not None and who != PARTNER:
        raise Refused(
            f"refuse: `hx {command}` is the Partner's, and HARNESS_ID is `{who}`. "
            f"A worker asks the Partner instead of dispatching for itself (spec 08)"
        )


def require_agent_caller(command: str, env=None) -> str:
    """`hx complete` and `hx task` act on the caller's own item, so the id must be set."""
    who = caller(env)
    if who is None:
        raise HxError(
            f"{command}: HARNESS_ID is not set; this command is run by a HarnessAgent inside "
            f"its own tmux session, where `start.sh` sets it (spec 08, 17.4)"
        )
    if not is_id(who):
        raise HxError(f"{command}: HARNESS_ID `{who}` is not an id (`partner` or `<pod>-NNN`)")
    return who
