"""`hx wake partner "<text>"` — the one way anything reaches the Partner (spec 08, 12).

Cross-session messaging is on by default and the Partner's home sets
`crossSessionInbound: accept`, so an inbound message is delivered with no approval hold in any
permission mode: an idle Partner starts a turn, a busy one reads it between tool calls
(spec 01.1, verified against code.claude.com/docs/en/cross-session-messaging).

The socket and token are per process and are recorded to `run/partner/socket.json` by the
`context` hook at every `SessionStart`, so they survive `/clear` (spec 09, 12). That hook is
M2; until then the file is written by hand in tests.

The wire format is an auth line then the message, each newline-terminated; the socket answers
nothing (spec 08, live-verified E6).
"""

from __future__ import annotations

import argparse
import json
import socket
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


def wake_partner_status(root: Path, text: str) -> str:
    """`accepted`, `no-socket`, or `refused`. Never blocks and never retries.

    `no-socket` means `run/partner/socket.json` is absent or unusable — the Partner has not
    started a session yet, so nothing has recorded a socket. `refused` means the file named a
    socket and the connection or the write failed: a stale file from a dead session, or a
    Partner that is not listening.
    """
    found = read_socket(Path(root))
    if found is None:
        return NO_SOCKET
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
        return REFUSED


def wake_partner(root: Path, text: str) -> bool:
    """True when the socket accepted the message, False otherwise (CONTRACTS.md).

    The UI calls this one; the CLI calls `wake_partner_status`, which says which failure it
    was. The text is a fixed short form composed by hx, never an order: orders are files.
    """
    return wake_partner_status(root, text) == ACCEPTED


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx wake", add_help=True)
    parser.add_argument("target", help="only `partner` is a wake target")
    parser.add_argument("text", help="the fixed short form hx composed; never an order")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.target != PARTNER:
        raise Refused(f"refuse: `{args.target}` is not a wake target; only the Partner is woken (spec 08)")
    status = wake_partner_status(root, args.text)
    # Last line, exactly; exit 0 only when the Partner actually took it (CONTRACTS.md).
    print(f"HX-WAKE partner {status}")
    return 0 if status == ACCEPTED else NOT_REACHED_EXIT
