"""`hx bench <id>` — free an id for the next order (spec 06, 08).

The completed body is archived to `pods/<pod>/archive/<id>-<ts>.md` before the work item is
reset from `templates/work-item.md` and renamed `complete → idle`. `tasks.json` is not
touched: the record of what that id did stays until the id is dispatched again.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import archive as archive_mod, store, timestamps
from .caller import require_partner_caller
from .errors import Refused
from .workitems import (
    parse_work_item,
    render,
    rename_state,
    load_template,
    require_work_item,
    work_item_path,
)


def bench(root: Path, item_id: str, *, env=None) -> dict:
    require_partner_caller("bench", env)

    path = require_work_item(root, item_id)
    item = parse_work_item(path)
    if item.state != "complete":
        raise Refused(
            f"refuse: {item_id} is `{item.state}`, not `complete`; a benched item is one that "
            f"finished and whose digest has been read (spec 06)"
        )

    ts = timestamps.now()
    # Archive the body before the reset, never after (spec 13 M1).
    target = archive_mod.bench_archive_path(root, item.pod, item_id, ts)
    store.atomic_write_text(target, path.read_text())

    blank = render(
        load_template(root), item_id=item_id, pod=item.pod, after=[], dispatched="", order=""
    )
    store.atomic_write_text(path, blank)
    final = rename_state(path, "idle")

    return {
        "id": item_id,
        "archived": str(target.relative_to(root)),
        "file": str(final.relative_to(root)),
    }


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx bench", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    result = bench(root, args.id, env=env)
    print(f"HX-BENCH {result['id']} idle archived={result['archived']}")
    return 0
