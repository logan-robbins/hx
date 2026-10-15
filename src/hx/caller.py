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


def require_partner_caller(command: str, env=None, *, owner: str | None = None) -> None:
    """The whole Partner column of `hx.cli.AUDIENCES` is the Partner's (and the operator's,
    who runs with no `HARNESS_ID`); `owner` words the refusal for an operator-only command."""
    who = caller(env)
    if who is not None and who != PARTNER:
        raise Refused(
            f"refuse: `hx {command}` is {owner or 'the Partner' + chr(39) + 's'}, and HARNESS_ID "
            f"is `{who}`. A worker records blockers and decisions through `hx progress` "
            f"instead of acting for itself (spec 08; `hx help` lists your commands)"
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
