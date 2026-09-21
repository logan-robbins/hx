"""`hx resume <id> <addendum-file>` — continue a paused item with everything it had (spec 06).

Only the order grows. `## Tasks`, step state, memory, workdir and logs are all kept: that is
the whole difference between a resume and `hx bench` + `hx dispatch`, which is a fresh start.
The item is `complete` while this runs, so the append never races the agent (spec 04). The
addendum file is deleted once the resume succeeds: the text lives in `tasks.json` and the
work item, and nowhere else (spec 06, spec 14 D25).
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import compose, goal, tasks as tasks_mod, timestamps
from .caller import require_partner_caller
from .errors import NotFound, Refused
from .ids import PARTNER
from .workitems import (
    SECTION_ORDER,
    append_to_section,
    parse_work_item,
    rename_state,
    require_work_item,
    set_frontmatter,
)

#: Spec 06: `hx resume` appends `## Order addendum <ts>` beneath `## Order`.
ADDENDUM_HEADING = "### Order addendum"

#: Only a paused item resumes; `done` and `exhausted` are ended, not paused (spec 06).
RESUMABLE = ("blocked", "decision")


def resume(root: Path, item_id: str, addendum_file: str | Path, *, env=None) -> dict:
    require_partner_caller("resume", env)
    if item_id == PARTNER:
        raise Refused(
            "refuse: the Partner has no work item and is never resumed; the human talks to "
            "it in chat (spec 12, spec 14 D25)"
        )

    addendum_path = Path(addendum_file)
    if not addendum_path.is_file():
        raise NotFound(f"{addendum_path}: no addendum file; the addendum is always a file (spec 06)")
    addendum = addendum_path.read_text().strip("\n")
    if not addendum.strip():
        raise Refused(f"refuse: {addendum_path} is empty; an addendum says what changed (spec 06)")

    path = require_work_item(root, item_id)
    item = parse_work_item(path)
    if item.state != "complete":
        raise Refused(
            f"refuse: {item_id} is `{item.state}`, not `complete`; only a paused item resumes (spec 06)"
        )
    if item.outcome not in RESUMABLE:
        raise Refused(
            f"refuse: {item_id} completed `{item.outcome or 'with no outcome'}`; "
            f"only `{'` or `'.join(RESUMABLE)}` resumes. `hx bench {item_id}` starts it over (spec 06)"
        )

    ts = timestamps.now()
    append_to_section(path, SECTION_ORDER, f"{ADDENDUM_HEADING} {ts}\n\n{addendum}")
    set_frontmatter(path, outcome=None)

    entries = tasks_mod.load_tasks(root)
    entry = entries.setdefault(item_id, tasks_mod.new_entry("", item.dispatched or ts))
    entry.setdefault("addenda", []).append({"ts": ts, "text": addendum})
    entry["outcome"] = None
    entry["completed"] = None
    tasks_mod.write_tasks(root, entries)

    final = rename_state(path, "working")
    compose.compose(root, item_id, f"{item_id}-main", env=env)
    sent = goal.send_goal(root, item_id, env=env)

    # Consumed, like the order file at dispatch (spec 06).
    addendum_path.unlink(missing_ok=True)

    return {"id": item_id, "ts": ts, "file": str(final.relative_to(root)), "goal": sent}


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx resume", add_help=True)
    parser.add_argument("id")
    parser.add_argument("addendum_file", metavar="ADDENDUM-FILE")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    result = resume(root, args.id, args.addendum_file, env=env)
    print(f"HX-RESUME {result['id']} working goal={result['goal']}")
    return 0
