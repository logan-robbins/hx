"""Parser and validator for goal files (spec 06, CONTRACTS.md).

The Partner dispatches a goal as a file at any path it likes, never command-line text, and
`hx dispatch` deletes it once it has read it: the text then lives in `tasks.json` and the
Work Item and nowhere else. The persona treats the dispatched goal as its Work Item.

A goal file holds exactly two sections, `## Goal` and `## Definition of done`, and no
frontmatter. The definition of done must carry a `### Checks` heading with a fenced ```bash
block that is not empty: `hx dispatch` refuses a goal that lacks either section or the
checks block, and `hx complete done` runs that block with `bash -e`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .errors import ValidationError
from .frontmatter import parse_frontmatter

GOAL_HEADING = "## Goal"
DOD_HEADING = "## Definition of done"
CHECKS_HEADING = "### Checks"

_H2_RE = re.compile(r"^##(?!#)\s*(.+?)\s*$")
_H3_RE = re.compile(r"^###(?!#)\s*(.+?)\s*$")
_FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([A-Za-z0-9_+-]*)\s*$")


@dataclass(frozen=True)
class Goal:
    """One parsed goal file."""

    path: Path
    goal: str
    definition_of_done: str
    checks: str
    text: str

    @property
    def body(self) -> str:
        """Everything below the frontmatter, copied verbatim into the Work Item (spec 06)."""
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
        # A closing fence is at least as long as the one it closes (CommonMark), so a
        # ````bash block may hold a ``` line without being cut short there.
        if (match and match.group(2)[0] == fence_marker[0]
                and len(match.group(2)) >= len(fence_marker) and match.group(3) == ""):
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


def parse_goal_text(text: str, path: str | Path) -> Goal:
    """Parse and validate a goal. Every message names the file and the rule it broke."""
    path_str = str(path)
    data, body = parse_frontmatter(text, path_str)
    if data:
        raise ValidationError(
            f"{path_str}: frontmatter key(s) {', '.join(sorted(data))}; a goal has no "
            f"frontmatter, only `{GOAL_HEADING}` and `{DOD_HEADING}` (spec 06, CONTRACTS.md). "
            f"There are no dependency fields: sequencing is the Partner's own judgement"
        )

    try:
        sections = _sections(body)
    except ValidationError as exc:
        heading = str(exc).split("DUPLICATE:", 1)[1]
        raise ValidationError(
            f"{path_str}: `## {heading}` appears more than once; a goal has exactly one "
            f"`{GOAL_HEADING}` and one `{DOD_HEADING}` (spec 06)"
        ) from None

    lines = body.split("\n")
    goal_key = GOAL_HEADING.removeprefix("## ")
    if goal_key not in sections:
        raise ValidationError(
            f"{path_str}: no `{GOAL_HEADING}` section; a goal has exactly two sections, "
            f"`{GOAL_HEADING}` and `{DOD_HEADING}` (spec 06). `hx dispatch` refuses it"
        )
    if DOD_HEADING.removeprefix("## ") not in sections:
        raise ValidationError(
            f"{path_str}: no `{DOD_HEADING}` section; a goal has exactly two sections, "
            f"`{GOAL_HEADING}` and `{DOD_HEADING}` (spec 06). `hx dispatch` refuses it"
        )

    goal_start, goal_end = sections[goal_key]
    goal_text = "\n".join(lines[goal_start + 1 : goal_end]).strip("\n")
    if goal_text.strip() == "":
        raise ValidationError(f"{path_str}: `{GOAL_HEADING}` is empty; the goal is the whole task (spec 06)")

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

    return Goal(
        path=Path(path),
        goal=goal_text,
        definition_of_done=dod_text,
        checks=checks,
        text=body.strip("\n") + "\n",
    )


def checks_fence_span(lines: list[str], path: str) -> tuple[int, int]:
    """(open, close) line indexes of the ```bash fence under `### Checks` in `## Definition of done`.

    `lines` is a whole goal or Work Item body. `close` is the closing fence line itself.
    """
    try:
        sections = _sections("\n".join(lines))
    except ValidationError as exc:
        heading = str(exc).split("DUPLICATE:", 1)[1]
        raise ValidationError(f"{path}: `## {heading}` appears more than once (spec 06)") from None
    bounds = sections.get(DOD_HEADING.removeprefix("## "))
    if bounds is None:
        raise ValidationError(f"{path}: no `{DOD_HEADING}` section to carry `{CHECKS_HEADING}` (spec 06)")
    start, end = bounds
    heading_at = None
    in_fence: str | None = None
    for i in range(start + 1, end):
        fence = _FENCE_RE.match(lines[i])
        if fence:
            marker = fence.group(2)[0]
            in_fence = marker if in_fence is None else (None if in_fence == marker else in_fence)
            continue
        if in_fence is None:
            match = _H3_RE.match(lines[i])
            if match and match.group(1) == CHECKS_HEADING.removeprefix("### "):
                heading_at = i
                break
    if heading_at is None:
        raise ValidationError(f"{path}: `{DOD_HEADING}` has no `{CHECKS_HEADING}` heading (spec 06)")
    return _bash_fence_after(lines, heading_at + 1, end, path)


def _bash_fence_after(lines: list[str], start: int, end: int, path: str) -> tuple[int, int]:
    """The first ```bash fence in `lines[start:end]`, as (open, close) line indexes."""
    opened = None
    marker = ""
    for i in range(start, end):
        match = _FENCE_RE.match(lines[i])
        if opened is None:
            if match and match.group(3).lower() in ("bash", "sh", "shell"):
                opened, marker = i, match.group(2)
            continue
        if match and match.group(2)[0] == marker[0] and len(match.group(2)) >= len(marker) \
                and match.group(3) == "":
            return opened, i
    if opened is None:
        raise ValidationError(f"{path}: `{CHECKS_HEADING}` has no fenced ```bash block (spec 06)")
    raise ValidationError(f"{path}: the ```bash block under `{CHECKS_HEADING}` is never closed (spec 06)")


def addendum_checks(text: str, path: str | Path) -> list[str] | None:
    """The `### Checks` fence an addendum carries, as its lines (fences included), or None.

    An addendum that names `### Checks` outside a fence means to replace the block, so a
    heading with no usable ```bash block under it is an error, not a quiet `kept`.
    """
    path_str = str(path)
    lines = text.split("\n")
    in_fence: str | None = None
    heading_at = None
    for i, line in enumerate(lines):
        fence = _FENCE_RE.match(line)
        if fence:
            marker = fence.group(2)[0]
            in_fence = marker if in_fence is None else (None if in_fence == marker else in_fence)
            continue
        if in_fence is None:
            match = _H3_RE.match(line)
            if match and match.group(1) == CHECKS_HEADING.removeprefix("### "):
                if heading_at is not None:
                    raise ValidationError(f"{path_str}: `{CHECKS_HEADING}` appears more than once (spec 08 `hx amend`)")
                heading_at = i
    if heading_at is None:
        return None
    # The same emptiness rule dispatch applies, before anything is spliced anywhere.
    _fenced_bash(lines[heading_at + 1 :], path_str)
    opened, closed = _bash_fence_after(lines, heading_at + 1, len(lines), path_str)
    return lines[opened : closed + 1]


def replace_checks(body: str, fence_lines: list[str], path: str | Path) -> str:
    """`body` with its `### Checks` fence replaced by `fence_lines`, fences and all.

    The addendum's own fence markers travel with the block, so a block that itself contains
    a shorter fence cannot close early inside the target.
    """
    lines = body.split("\n")
    opened, closed = checks_fence_span(lines, str(path))
    return "\n".join(lines[:opened] + list(fence_lines) + lines[closed + 1 :])


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
