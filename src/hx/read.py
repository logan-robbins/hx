"""`hx read <id>` — the Partner's view of a finished item (spec 08, 12 step 5).

Prints the `## Digest` the Companion wrote and the `## Open decision` the agent left, which
is what the Partner acts on. `--full` prints the whole body.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .caller import require_partner_caller
from .errors import Refused
from .workitems import (
    SECTION_DIGEST,
    SECTION_OPEN_DECISION,
    require_work_item,
    section_text,
    split_frontmatter_text,
)


def read(root: Path, item_id: str, *, full: bool = False, env=None) -> str:
    require_partner_caller("read", env)
    path = require_work_item(root, item_id)
    state = path.name.rsplit("-", 1)[1].removesuffix(".md")
    _, body = split_frontmatter_text(path.read_text())

    if full:
        return body.strip("\n")
    if state != "complete":
        raise Refused(
            f"refuse: {item_id} is `{state}`, not `complete`; the Digest is written when the "
            f"item completes (spec 06). `hx read {item_id} --full` shows the body as it stands"
        )

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
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    print(read(root, args.id, full=args.full, env=env))
    return 0
