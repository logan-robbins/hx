"""Goal paragraph parsing and current ledger plan projection."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .errors import ValidationError
from .frontmatter import parse_frontmatter

GOAL_HEADING = "## Goal"

_HEADING_RE = re.compile(r"^(#{1,6})(?!#)\s*(.+?)\s*$")
_FENCE_RE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([A-Za-z0-9_+-]*)\s*$")


@dataclass(frozen=True)
class Goal:
    """One parsed goal file."""

    path: Path
    goal: str
    text: str

    @property
    def body(self) -> str:
        """Everything below the frontmatter, used as the planning input."""
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
            f"frontmatter, only one `{GOAL_HEADING}` paragraph (CONTRACTS.md)"
        )

    headings = _headings(body)
    goals = [h for h in headings if h[1] == 2 and h[2] == GOAL_HEADING.removeprefix("## ")]
    if not goals:
        raise ValidationError(
            f"{path_str}: no `{GOAL_HEADING}` section; a goal is one `{GOAL_HEADING}` paragraph"
            f". provide it as a planning input"
        )
    if len(goals) > 1:
        raise ValidationError(
            f"{path_str}: `{GOAL_HEADING}` appears more than once; a goal is one "
            f"`{GOAL_HEADING}` paragraph"
        )
    others = [h for h in headings if h is not goals[0]]
    if others:
        _, level, heading = others[0]
        raise ValidationError(
            f"{path_str}: `{'#' * level} {heading}`; a goal is one `{GOAL_HEADING}` paragraph and "
            f"no other heading"
        )

    lines = body.split("\n")
    start = goals[0][0]
    before = "\n".join(lines[:start]).strip()
    if before:
        raise ValidationError(
            f"{path_str}: text above `{GOAL_HEADING}`; a goal is one `{GOAL_HEADING}` paragraph "
            f"and nothing else"
        )
    goal_text = "\n".join(lines[start + 1 :]).strip("\n")
    if goal_text.strip() == "":
        raise ValidationError(f"{path_str}: `{GOAL_HEADING}` is empty; the goal is the whole task")

    return Goal(path=Path(path), goal=goal_text, text=body.strip("\n") + "\n")


def parse_goal(path: str | Path) -> Goal:
    path = Path(path)
    if not path.is_file():
        raise ValidationError(f"{path}: planning goal input not found")
    return parse_goal_text(path.read_text(), path)


def collect(root):
    from .board import collect as board
    view = board(root)
    return {key: view[key] for key in ('root_abs', 'ts', 'ledger_revision', 'plans', 'tasks', 'truncated')}


def main(argv, root, *, env=None):
    import argparse
    import json
    parser = argparse.ArgumentParser(prog='hx goals', description='Current plans and their dependency packets.')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--root', help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    view = collect(root)
    if args.json:
        print(json.dumps(view, indent=2))
    else:
        for plan in view['plans']:
            print(f"{plan['plan_id']}@{plan['revision']}: {plan['payload']['definition']['goal']}")
        for task in view['tasks']:
            print(f"  {task['task_id']}@{task['revision']} {task['status']}: {task['payload']['goal']}")
    return 0
