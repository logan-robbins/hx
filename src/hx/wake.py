"""`hx wake partner "<text>"` — the one way anything reaches the Partner (spec 08, 12).

A Claude Partner is reached over cross-session messaging, which is on by default and
delivered with no approval hold in any permission mode: an idle Partner starts a turn, a
busy one reads it between tool calls (spec 01.1, verified against
code.claude.com/docs/en/cross-session-messaging).

The socket and token are per process and are recorded to `run/partner/socket.json` by the
`context` hook at every `SessionStart`, so they survive `/clear` (spec 09, 12). That hook is
M2; until then the file is written by hand in tests.

The wire format is an auth line then the message, each newline-terminated; the socket answers
nothing (spec 08, live-verified E6).

A Codex Partner has no messaging socket, so the wake is pasted into its tmux pane instead —
the same paste `hx goal` uses, which never blocks: the polls that watch the input box are
bounded and a missing pane reports `no-socket`.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
from pathlib import Path

from .errors import Refused
from .ids import PARTNER

SOCKET_FILE = "run/partner/socket.json"

#: CONTRACTS.md: "It never blocks and never retries." This bounds the connect and the two
#: writes so a stale socket file cannot hang a caller; it is not a wait for a condition, which
#: is what spec 08's "no timeouts anywhere" rules out.
_SOCKET_TIMEOUT_S = 5.0


def socket_path(root: Path) -> Path:
    return root / SOCKET_FILE


def read_socket(root: Path) -> tuple[str, str] | None:
    """(socket path, token) from `run/partner/socket.json`, or None when it is unusable.

    Both the hook's own environment-variable names and short keys are accepted, because the
    `context` hook writes what Claude Code exported to it (spec 09).
    """
    path = socket_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    address = data.get("socket") or data.get("CLAUDE_CODE_MESSAGING_SOCKET")
    token = data.get("token") or data.get("CLAUDE_CODE_MESSAGING_TOKEN") or ""
    if not address:
        return None
    return str(address), str(token)


#: The three outcomes the CLI reports (CONTRACTS.md `hx wake partner`).
ACCEPTED, NO_SOCKET, REFUSED = "accepted", "no-socket", "refused"

#: Exit code when the Partner was not reached. Callers treat a failed wake as a warning: the
#: work is done and recorded either way, and `hx heartbeat` will try again.
NOT_REACHED_EXIT = 3


def _partner_flavor(root: Path) -> str:
    """This instance's Partner flavor; `claude` when nothing says otherwise."""
    from .config_harness import flavor_of

    try:
        return flavor_of(root, PARTNER)
    except Exception:
        return "claude"


def _paste_to_partner(text: str) -> str:
    """The non-Claude wake path: paste the text into the Partner's pane (spec 12).

    `no-socket` when there is no pane to paste into, `refused` when the paste
    itself failed, `accepted` once the text left this process for the pane.
    """
    from . import goal as goal_mod

    try:
        if goal_mod.capture_pane(PARTNER) is None:
            return NO_SOCKET
        goal_mod.paste(PARTNER, text)
    except FileNotFoundError:
        return NO_SOCKET  # no tmux at all, so no Partner session either
    except subprocess.CalledProcessError:
        return NO_SOCKET  # the pane went away between the capture and the paste
    except Exception:
        return REFUSED
    return ACCEPTED


def wake_partner_status(root: Path, text: str) -> str:
    """`accepted`, `no-socket`, or `refused`. Never blocks and never retries.

    `no-socket` means `run/partner/socket.json` is absent or unusable — the Partner has not
    started a session yet, so nothing has recorded a socket. `refused` means the file named a
    socket and the connection or the write failed: a stale file from a dead session, or a
    Partner that is not listening.

    A Partner that does not run on Claude has no socket to record, so a wake that the
    socket path does not accept falls back to pasting into its pane.
    """
    found = read_socket(Path(root))
    if found is None:
        if _partner_flavor(Path(root)) == "claude":
            return NO_SOCKET
        return _paste_to_partner(text)
    address, token = found
    payloads = [
        json.dumps({"type": "auth", "token": token}),
        json.dumps({"type": "user", "message": {"role": "user", "content": text}}),
    ]
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(_SOCKET_TIMEOUT_S)
            client.connect(address)
            client.sendall(("\n".join(payloads) + "\n").encode())
        return ACCEPTED
    except (OSError, ValueError):
        if _partner_flavor(Path(root)) == "claude":
            return REFUSED
        return _paste_to_partner(text)


def wake_partner(root: Path, text: str) -> bool:
    """True when the socket accepted the message, False otherwise (CONTRACTS.md).

    The UI calls this one; the CLI calls `wake_partner_status`, which says which failure it
    was. The text is a fixed short form composed by hx, never a goal: goals are files.
    """
    return wake_partner_status(root, text) == ACCEPTED


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx wake", add_help=True)
    parser.add_argument("target", help="only `partner` is a wake target")
    parser.add_argument("text", help="the fixed short form hx composed; never a goal")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.target != PARTNER:
        raise Refused(f"refuse: `{args.target}` is not a wake target; only the Partner is woken (spec 08)")
    status = wake_partner_status(root, args.text)
    # Last line, exactly; exit 0 only when the Partner actually took it (CONTRACTS.md).
    print(f"HX-WAKE partner {status}")
    return 0 if status == ACCEPTED else NOT_REACHED_EXIT
