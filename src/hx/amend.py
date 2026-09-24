"""`hx amend <id> <addendum-file>` — correct an item's goal in place (spec 08).

The addendum lands exactly where `hx resume` puts one: `### Goal addendum <ts>` beneath the
Work Item's `## Goal`, and `{ts, text}` in `tasks.json[<id>].addenda`. When the addendum
carries its own `### Checks` heading with a fenced ```bash block, that block also replaces the
Checks block in `tasks.json[<id>].goal` and in the Work Item's `## Definition of done`, which
is what `hx complete done` runs and what the worker reads. Before that replacement is written,
the amended goal must pass `parse_goal_text` and must carry the new block; otherwise nothing is
written at all.

Unlike a resume, an amend changes no state, pastes nothing and restarts nothing: the item may
be `working`, and the worker's next `hx task` or `hx complete done` reads the amended record.
The addendum file is consumed once the amend succeeds, like every goal and addendum file.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from . import store, tasks as tasks_mod, timestamps
from .caller import require_partner_caller
from .errors import NotFound, Refused, ValidationError
from .goals import _fenced_bash, addendum_checks, checks_fence_span, parse_goal_text, replace_checks
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

#: A `working` item is the point: its Checks can be corrected without a pause. A `complete`
#: one is amended before a resume or for the record. An `idle` item has no goal to amend.
AMENDABLE = ("working", "complete")


@dataclass
class Amendment:
    """Everything an amend or a resume writes, computed before any of it is written."""

    work_item: Path
    front: str
    body: str
    goal: str | None
    checks: str | None
    addendum: str
    ts: str

    @property
    def replaced(self) -> bool:
        return self.goal is not None


def read_addendum(addendum_file: str | Path, command: str) -> tuple[Path, str]:
    path = Path(addendum_file)
    if not path.is_file():
        raise NotFound(f"{path}: no addendum file; the addendum is always a file (spec 06)")
    text = path.read_text().strip("\n")
    if not text.strip():
        raise Refused(f"refuse: {path} is empty; an addendum says what changed (spec 08 `hx {command}`)")
    return path, text


def _checks_in(body: str, path: str) -> str:
    lines = body.split("\n")
    opened, closed = checks_fence_span(lines, path)
    return "\n".join(lines[opened + 1 : closed]).strip("\n")


def plan(root: Path, item_id: str, addendum: str, addendum_path: Path, recorded_goal: str | None,
         *, ts: str | None = None) -> Amendment:
    """Compute the amended Work Item body and goal. Raises, and writes nothing, on any refusal."""
    ts = ts or timestamps.now()
    work_item = require_work_item(root, item_id)
    front, body = split_frontmatter_text(work_item.read_text())
    body = append_to_section_text(body, SECTION_GOAL, f"{ADDENDUM_HEADING} {ts}\n\n{addendum}", path=work_item)

    try:
        fence = addendum_checks(addendum, addendum_path)
    except ValidationError as exc:
        raise Refused(f"refuse: {exc.message}; nothing was written") from None
    if fence is None:
        return Amendment(work_item, front, body, None, None, addendum, ts)

    if not recorded_goal:
        raise Refused(
            f"refuse: tasks.json has no goal for {item_id}, so there is no `### Checks` block to "
            f"replace; nothing was written"
        )
    label = f"tasks.json[{item_id}].goal"
    try:
        goal = replace_checks(recorded_goal, fence, label)
        wanted = _fenced_bash(fence, str(addendum_path))
        parsed = parse_goal_text(goal, f"{label} as amended by {addendum_path}")
        body = replace_checks(body, fence, work_item)
        in_item = _checks_in(body, str(work_item))
    except ValidationError as exc:
        raise Refused(f"refuse: {exc.message}; nothing was written") from None
    # The substitution must have taken in both places, or the amend would report `replaced`
    # over a goal that still runs the old block.
    if parsed.checks != wanted or in_item != wanted:
        raise Refused(
            f"refuse: the `### Checks` replacement did not take in "
            f"{label if parsed.checks != wanted else work_item}; nothing was written"
        )
    return Amendment(work_item, front, body, goal, parsed.checks, addendum, ts)


def write(root: Path, item_id: str, amendment: Amendment, entries: dict[str, dict], entry: dict) -> None:
    """The Work Item first, then `tasks.json`, the same order as `hx resume`."""
    store.atomic_write_text(amendment.work_item, amendment.front + amendment.body)
    entry.setdefault("addenda", []).append({"ts": amendment.ts, "text": amendment.addendum})
    if amendment.goal is not None:
        entry["goal"] = amendment.goal
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

    amendment = plan(root, item_id, addendum, addendum_path, entry.get("goal"))
    write(root, item_id, amendment, entries, entry)
    addendum_path.unlink(missing_ok=True)
    return {
        "id": item_id,
        "ts": amendment.ts,
        "checks": "replaced" if amendment.replaced else "kept",
        "addenda": len(entry["addenda"]),
    }


def line(result: dict) -> str:
    return f"{AMEND} {result['id']} checks={result['checks']} addenda={result['addenda']}"


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx amend", add_help=True)
    parser.add_argument("id")
    parser.add_argument("addendum_file", metavar="ADDENDUM-FILE")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    print(line(amend(root, args.id, args.addendum_file, env=env)))
    return 0
