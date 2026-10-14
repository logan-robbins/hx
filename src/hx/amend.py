"""`hx amend <id> <addendum-file>` — correct an item's goal in place (spec 08).

The addendum lands exactly where `hx resume` puts one: `### Goal addendum <ts>` beneath the
Work Item's `## Goal`, and `{ts, text}` in `tasks.json[<id>].addenda`. It is prose the worker
reads: there is no acceptance block to replace, because the worker owns its
`## Definition of done` and `hx complete done` checks the `## Evidence` it left. An addendum
that carries a `### Checks` heading is refused, so a Partner writing to the old contract learns
it before anything is written.

Unlike a resume, an amend changes no state, pastes nothing and restarts nothing: the item may
be `working`, and the worker's next `hx task` or boundary reads the amended record. The
addendum file is consumed once the amend succeeds, like every goal and addendum file.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from . import store, tasks as tasks_mod, timestamps
from .caller import require_partner_caller
from .errors import NotFound, Refused
from .goals import _headings
from .ids import PARTNER
from .workitems import (
    SECTION_GOAL,
    append_to_section_text,
    parse_work_item,
    require_work_item,
    split_frontmatter_text,
)

AMEND = "HX-AMEND"

#: Spec 06: the addendum heading, shared with `hx resume`.
ADDENDUM_HEADING = "### Goal addendum"

#: A `working` item is the point: its goal can be corrected without a pause. A `complete`
#: one is amended before a resume or for the record. An `idle` item has no goal to amend.
AMENDABLE = ("working", "complete")


@dataclass
class Amendment:
    """Everything an amend or a resume writes, computed before any of it is written."""

    work_item: Path
    front: str
    body: str
    addendum: str
    ts: str


def read_addendum(addendum_file: str | Path, command: str) -> tuple[Path, str]:
    path = Path(addendum_file)
    if not path.is_file():
        raise NotFound(f"{path}: no addendum file; the addendum is always a file (spec 06)")
    text = path.read_text().strip("\n")
    if not text.strip():
        raise Refused(f"refuse: {path} is empty; an addendum says what changed (spec 08 `hx {command}`)")
    for _, level, heading in _headings(text):
        if heading == "Checks":
            raise Refused(
                f"refuse: {path}: `{'#' * level} Checks`; there is no Checks block to replace — the "
                f"worker owns `## Definition of done` and `hx complete done` requires its "
                f"`## Evidence`. Say what changed in prose; nothing was written"
            )
    return path, text


def plan(root: Path, item_id: str, addendum: str, *, ts: str | None = None) -> Amendment:
    """Compute the amended Work Item body. Raises, and writes nothing, on any refusal."""
    ts = ts or timestamps.now()
    work_item = require_work_item(root, item_id)
    front, body = split_frontmatter_text(work_item.read_text())
    body = append_to_section_text(body, SECTION_GOAL, f"{ADDENDUM_HEADING} {ts}\n\n{addendum}", path=work_item)
    return Amendment(work_item, front, body, addendum, ts)


def write(root: Path, item_id: str, amendment: Amendment, entries: dict[str, dict], entry: dict) -> None:
    """The Work Item first, then `tasks.json`, the same order as `hx resume`."""
    store.atomic_write_text(amendment.work_item, amendment.front + amendment.body)
    entry.setdefault("addenda", []).append({"ts": amendment.ts, "text": amendment.addendum})
    tasks_mod.write_tasks(root, entries)


def amend(root: Path, item_id: str, addendum_file: str | Path, *, env=None) -> dict:
    require_partner_caller("amend", env)
    if item_id == PARTNER:
        raise Refused(
            "refuse: the Partner has no work item and no goal to amend; the human talks to it "
            "in chat (spec 12, spec 14 D25)"
        )
    addendum_path, addendum = read_addendum(addendum_file, "amend")

    path = require_work_item(root, item_id)
    item = parse_work_item(path)
    if item.state not in AMENDABLE:
        raise Refused(
            f"refuse: {item_id} is `{item.state}`; only a `working` or `complete` item is amended "
            f"(spec 08). An idle item takes a goal with `hx dispatch`"
        )

    entries = tasks_mod.load_tasks(root)
    entry = entries.get(item_id)
    if entry is None:
        raise Refused(f"refuse: {item_id} has no tasks.json record; it was never dispatched (spec 08)")

    amendment = plan(root, item_id, addendum)
    write(root, item_id, amendment, entries, entry)
    addendum_path.unlink(missing_ok=True)
    return {"id": item_id, "ts": amendment.ts, "addenda": len(entry["addenda"])}


def line(result: dict) -> str:
    return f"{AMEND} {result['id']} addenda={result['addenda']}"


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx amend", add_help=True)
    parser.add_argument("id")
    parser.add_argument("addendum_file", metavar="ADDENDUM-FILE")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    print(line(amend(root, args.id, args.addendum_file, env=env)))
    return 0
