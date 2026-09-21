"""Parser and validator for order files (spec 06, CONTRACTS.md).

The order is a file at any path the Partner likes, never command-line text, and `hx dispatch`
deletes it once it has read it: the text then lives in `tasks.json` and the work item and
nowhere else. There is no `orders/` directory (spec 14 D25).

It holds exactly two sections, `## Order` and `## Definition of done`, and no frontmatter.
The definition of done must carry a `### Checks` heading with a fenced ```bash block that is
not empty: `hx dispatch` refuses an order that lacks either section or the checks block, and
`hx complete done` runs that block with `bash -e`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .errors import ValidationError
from .frontmatter import parse_frontmatter

ORDER_HEADING = "## Order"
DOD_HEADING = "## Definition of done"
CHECKS_HEADING = "### Checks"

_H2_RE = re.compile(r"^##(?!#)\s*(.+?)\s*$")
_H3_RE = re.compile(r"^###(?!#)\s*(.+?)\s*$")
_FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([A-Za-z0-9_+-]*)\s*$")


@dataclass(frozen=True)
class Order:
    """One parsed order file."""

    path: Path
    order: str
    definition_of_done: str
    checks: str
    text: str

    @property
    def body(self) -> str:
        """Everything below the frontmatter, copied verbatim into the work item (spec 06)."""
        return self.text


def _sections(body: str) -> dict[str, tuple[int, int]]:
    """Map each `## Heading` to the (start, end) line range of its content."""
    lines = body.split("\n")
    found: list[tuple[str, int]] = []
    in_fence: str | None = None
    for i, line in enumerate(lines):
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
        match = _H2_RE.match(line)
        if match:
            found.append((match.group(1), i))
    result: dict[str, tuple[int, int]] = {}
    for index, (heading, start) in enumerate(found):
        end = found[index + 1][1] if index + 1 < len(found) else len(lines)
        if heading in result:
            raise ValidationError("DUPLICATE:" + heading)
        result[heading] = (start, end)
    return result


def _fenced_bash(lines: list[str], path: str) -> str:
    """The first fenced ```bash block in `lines`, or an error naming the rule."""
    block: list[str] | None = None
    fence_marker: str | None = None
    fence_indent = ""
    for line in lines:
        match = _FENCE_RE.match(line)
        if fence_marker is None:
            if match and match.group(3).lower() in ("bash", "sh", "shell"):
                fence_marker = match.group(2)
                fence_indent = match.group(1)
                block = []
            continue
        if match and match.group(2)[0] == fence_marker[0] and match.group(3) == "":
            break
        assert block is not None
        block.append(line[len(fence_indent) :] if line.startswith(fence_indent) else line)
    if block is None:
        raise ValidationError(
            f"{path}: `{CHECKS_HEADING}` has no fenced ```bash block; `hx complete done` runs "
            f"that block with `bash -e` in the worktree (spec 06)"
        )
    checks = "\n".join(block).strip("\n")
    if checks.strip() == "" or all(
        line.strip() == "" or line.strip().startswith("#") for line in checks.split("\n")
    ):
        raise ValidationError(
            f"{path}: the ```bash block under `{CHECKS_HEADING}` is empty; an empty block is "
            f"refused at dispatch (spec 06). When nothing is executable, check the deliverable "
            f"exists, for example `test -s report.md`"
        )
    return checks


def parse_order_text(text: str, path: str | Path) -> Order:
    """Parse and validate an order. Every message names the file and the rule it broke."""
    path_str = str(path)
    data, body = parse_frontmatter(text, path_str)
    if data:
        raise ValidationError(
            f"{path_str}: frontmatter key(s) {', '.join(sorted(data))}; an order has no "
            f"frontmatter, only `{ORDER_HEADING}` and `{DOD_HEADING}` (spec 06, CONTRACTS.md). "
            f"There are no dependency fields: sequencing is the Partner's own judgement"
        )

    try:
        sections = _sections(body)
    except ValidationError as exc:
        heading = str(exc).split("DUPLICATE:", 1)[1]
        raise ValidationError(
            f"{path_str}: `## {heading}` appears more than once; an order has exactly one "
            f"`{ORDER_HEADING}` and one `{DOD_HEADING}` (spec 06)"
        ) from None

    lines = body.split("\n")
    for heading in (ORDER_HEADING, DOD_HEADING):
        if heading.removeprefix("## ") not in sections:
            raise ValidationError(
                f"{path_str}: no `{heading}` section; an order has exactly two sections, "
                f"`{ORDER_HEADING}` and `{DOD_HEADING}` (spec 06). `hx dispatch` refuses it"
            )

    order_start, order_end = sections[ORDER_HEADING.removeprefix("## ")]
    order_text = "\n".join(lines[order_start + 1 : order_end]).strip("\n")
    if order_text.strip() == "":
        raise ValidationError(f"{path_str}: `{ORDER_HEADING}` is empty; the order is the whole task (spec 06)")

    dod_start, dod_end = sections[DOD_HEADING.removeprefix("## ")]
    dod_lines = lines[dod_start + 1 : dod_end]
    dod_text = "\n".join(dod_lines).strip("\n")

    checks_at = None
    in_fence: str | None = None
    for i, line in enumerate(dod_lines):
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(2)[0]
            in_fence = marker if in_fence is None else (None if in_fence == marker else in_fence)
            continue
        if in_fence is not None:
            continue
        match = _H3_RE.match(line)
        if match and match.group(1) == CHECKS_HEADING.removeprefix("### "):
            if checks_at is not None:
                raise ValidationError(
                    f"{path_str}: `{CHECKS_HEADING}` appears more than once under `{DOD_HEADING}` (spec 06)"
                )
            checks_at = i
    if checks_at is None:
        raise ValidationError(
            f"{path_str}: `{DOD_HEADING}` has no `{CHECKS_HEADING}` heading; the definition of "
            f"done has two parts, an acceptance checklist and a `{CHECKS_HEADING}` fenced bash "
            f"block (spec 06). `hx dispatch` refuses it"
        )
    checks = _fenced_bash(dod_lines[checks_at + 1 :], path_str)

    return Order(
        path=Path(path),
        order=order_text,
        definition_of_done=dod_text,
        checks=checks,
        text=body.strip("\n") + "\n",
    )


def parse_order(path: str | Path) -> Order:
    path = Path(path)
    if not path.is_file():
        raise ValidationError(f"{path}: order file not found; the order is always a file (spec 06)")
    return parse_order_text(path.read_text(), path)


# --- `hx orders [--json]` (CONTRACTS.md) -------------------------------------------------------
#
# Read-only over `tasks.json`, and nothing else. The order file is consumed and deleted at
# dispatch, so there is no file to compare against and no graph to draw (spec 14 D25).


def collect(root: Path) -> dict:
    """The `hx orders --json` document: one entry per id in `tasks.json`."""
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
                "order": record.get("order"),
                "addenda": list(record.get("addenda") or []),
                "dispatched": record.get("dispatched"),
                "completed": record.get("completed"),
            }
        )

    return {"root_abs": str(root), "ts": timestamps.now(), "orders": entries}


def main(argv: list[str], root: Path, *, env=None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="hx orders", add_help=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    view = collect(root)
    if args.json:
        print(json.dumps(view, indent=2))
    else:
        for entry in view["orders"]:
            print(
                f"{entry['id']}  {entry['state'] or '-'}  {entry['outcome'] or '-'}  "
                f"addenda={len(entry['addenda'])}  dispatched={entry['dispatched'] or '-'}  "
                f"completed={entry['completed'] or '-'}"
            )
    return 0
