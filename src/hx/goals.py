"""Parser and validator for goal files (spec 06, CONTRACTS.md).

The Partner dispatches a goal as a file at any path it likes, never command-line text, and
`hx dispatch` deletes it once it has read it: the text then lives in `tasks.json` and the
Work Item and nowhere else. The persona treats the dispatched goal as its Work Item.

A goal file is one `## Goal` section and nothing else: a paragraph saying what the result is
and why, optionally followed by one to three sentences on what it depends on. No frontmatter,
no other heading. The worker writes its own `## Definition of done` in the work item, on top
of its role's standard set (its persona), and `hx complete done` checks the evidence it left,
not a block the Partner wrote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .errors import ValidationError
from .frontmatter import parse_frontmatter

GOAL_HEADING = "## Goal"

_HEADING_RE = re.compile(r"^(#{1,6})(?!#)\s*(.+?)\s*$")
_FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([A-Za-z0-9_+-]*)\s*$")

#: Where a refused heading belongs now, for the two the old two-section contract carried.
_MOVED = {
    "Definition of done": "the worker writes its own `## Definition of done` in the work item, "
                          "on top of its role's standard set",
    "Checks": "there is no Checks block: `hx complete done` requires the worker's `## Evidence`",
}


@dataclass(frozen=True)
class Goal:
    """One parsed goal file."""

    path: Path
    goal: str
    text: str

    @property
    def body(self) -> str:
        """Everything below the frontmatter, copied verbatim into the Work Item (spec 06)."""
        return self.text


def _headings(body: str) -> list[tuple[int, int, str]]:
    """(line index, level, text) of every Markdown heading outside a fence."""
    found: list[tuple[int, int, str]] = []
    in_fence: str | None = None
    for i, line in enumerate(body.split("\n")):
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(2)[0]
            if in_fence is None:
                in_fence = marker
            elif in_fence == marker:
                in_fence = None
            continue
        if in_fence is not None:
            continue
        match = _HEADING_RE.match(line)
        if match:
            found.append((i, len(match.group(1)), match.group(2)))
    return found


def parse_goal_text(text: str, path: str | Path) -> Goal:
    """Parse and validate a goal. Every message names the file and the rule it broke."""
    path_str = str(path)
    data, body = parse_frontmatter(text, path_str)
    if data:
        raise ValidationError(
            f"{path_str}: frontmatter key(s) {', '.join(sorted(data))}; a goal has no "
            f"frontmatter, only one `{GOAL_HEADING}` paragraph (spec 06, CONTRACTS.md). "
            f"There are no dependency fields: say what it depends on in a sentence"
        )

    headings = _headings(body)
    goals = [h for h in headings if h[1] == 2 and h[2] == GOAL_HEADING.removeprefix("## ")]
    if not goals:
        raise ValidationError(
            f"{path_str}: no `{GOAL_HEADING}` section; a goal is one `{GOAL_HEADING}` paragraph "
            f"(spec 06). `hx dispatch` refuses it"
        )
    if len(goals) > 1:
        raise ValidationError(
            f"{path_str}: `{GOAL_HEADING}` appears more than once; a goal is one "
            f"`{GOAL_HEADING}` paragraph (spec 06)"
        )
    others = [h for h in headings if h is not goals[0]]
    if others:
        _, level, heading = others[0]
        moved = _MOVED.get(heading)
        raise ValidationError(
            f"{path_str}: `{'#' * level} {heading}`; a goal is one `{GOAL_HEADING}` paragraph and "
            f"no other heading (spec 06)" + (f": {moved}" if moved else "")
        )

    lines = body.split("\n")
    start = goals[0][0]
    before = "\n".join(lines[:start]).strip()
    if before:
        raise ValidationError(
            f"{path_str}: text above `{GOAL_HEADING}`; a goal is one `{GOAL_HEADING}` paragraph "
            f"and nothing else (spec 06)"
        )
    goal_text = "\n".join(lines[start + 1 :]).strip("\n")
    if goal_text.strip() == "":
        raise ValidationError(f"{path_str}: `{GOAL_HEADING}` is empty; the goal is the whole task (spec 06)")

    return Goal(path=Path(path), goal=goal_text, text=body.strip("\n") + "\n")


def parse_goal(path: str | Path) -> Goal:
    path = Path(path)
    if not path.is_file():
        raise ValidationError(f"{path}: goal file not found; the goal is always a file (spec 06)")
    return parse_goal_text(path.read_text(), path)


# --- `hx goals [--json]` (CONTRACTS.md) -------------------------------------------------------
#
# Read-only over `tasks.json`, and nothing else. The goal file is consumed and deleted at
# dispatch, so there is no file to compare against and no graph to draw (spec 14 D25).


def collect(root: Path) -> dict:
    """The `hx goals --json` document: one entry per id in `tasks.json`."""
    from . import timestamps
    from .ids import sort_key
    from .tasks import load_tasks
    from .workitems import find_work_item, parse_work_item_filename

    root = Path(root)
    tasks = load_tasks(root)

    entries = []
    for item_id in sorted(tasks, key=sort_key):
        record = tasks[item_id]
        work_item = find_work_item(root, item_id)
        state = None
        if work_item is not None:
            state = parse_work_item_filename(work_item.name, path=work_item).state
        entries.append(
            {
                "id": item_id,
                "pod": work_item.parent.name if work_item else None,
                "state": state,
                "outcome": record.get("outcome"),
                "goal": record.get("goal"),
                "addenda": list(record.get("addenda") or []),
                "dispatched": record.get("dispatched"),
                "completed": record.get("completed"),
            }
        )

    return {"root_abs": str(root), "ts": timestamps.now(), "goals": entries}


def main(argv: list[str], root: Path, *, env=None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="hx goals", add_help=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    view = collect(root)
    if args.json:
        print(json.dumps(view, indent=2))
    else:
        for entry in view["goals"]:
            print(
                f"{entry['id']}  {entry['state'] or '-'}  {entry['outcome'] or '-'}  "
                f"addenda={len(entry['addenda'])}  dispatched={entry['dispatched'] or '-'}  "
                f"completed={entry['completed'] or '-'}"
            )
    return 0
