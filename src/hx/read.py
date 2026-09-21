"""`hx read <id>` — the Partner's trust-model view of a finished item (spec 08, 12 step 5).

Personas are trusted: the default output is status only — id, state, outcome, timestamps —
so the Partner tracks what/why/state without absorbing the how. Prose (`## Digest`,
`## Open decision`) is available via `--detail` for blocked/decision follow-ups, and the
whole body via `--full`. Completed Work Item bodies live on in file memory; `hx recall`
searches them as a last resort under strict bounds.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .caller import require_partner_caller
from .errors import Refused
from .tasks import load_tasks
from .workitems import (
    SECTION_DIGEST,
    SECTION_OPEN_DECISION,
    require_work_item,
    section_text,
    split_frontmatter_text,
)


def status(root: Path, item_id: str, *, env=None) -> str:
    """One trust-model status block: state and outcome, no prose."""
    require_partner_caller("read", env)
    path = require_work_item(root, item_id)
    state = path.name.rsplit("-", 1)[1].removesuffix(".md")
    entry = load_tasks(root).get(item_id) or {}
    lines = [
        f"{item_id} {state} {entry.get('outcome') or '-'}",
        f"dispatched: {entry.get('dispatched') or '-'}",
        f"completed: {entry.get('completed') or '-'}",
        f"file: {path.relative_to(root)}",
        "detail archived; `hx read <id> --detail` sparingly, `hx recall` only as a last resort.",
    ]
    return "\n".join(lines)


def read(root: Path, item_id: str, *, full: bool = False, detail: bool = False, env=None) -> str:
    require_partner_caller("read", env)
    path = require_work_item(root, item_id)
    state = path.name.rsplit("-", 1)[1].removesuffix(".md")
    _, body = split_frontmatter_text(path.read_text())

    if full:
        return body.strip("\n")
    if state != "complete":
        raise Refused(
            f"refuse: {item_id} is `{state}`, not `complete`; status is written when the "
            f"item completes (spec 06). `hx read {item_id} --full` shows the body as it stands"
        )
    if not detail:
        return status(root, item_id, env=env)

    digest = section_text(body, SECTION_DIGEST)
    decision = section_text(body, SECTION_OPEN_DECISION)
    parts = [f"{SECTION_DIGEST}\n{digest or '(empty)'}"]
    if decision:
        parts.append(f"{SECTION_OPEN_DECISION}\n{decision}")
    return "\n\n".join(parts)


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx read", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--full", action="store_true", help="print the whole body")
    parser.add_argument(
        "--detail",
        action="store_true",
        help="print the Digest and open decision (blocked/decision follow-ups only)",
    )
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    print(read(root, args.id, full=args.full, detail=args.detail, env=env))
    return 0
