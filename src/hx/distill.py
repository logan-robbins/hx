"""`hx distill <id> <distilled-file>` — shrink what the agent carries.

Addenda accumulate without bound: every `hx resume` and `hx amend` appends a
`### Goal addendum <ts>` to the Work Item's `## Goal` plus `{ts, text}` in
`tasks.json[<id>].addenda`, and both ride every context file the agent reads
after a boundary. Distilling is the Partner's periodic correction: it reads the
accumulation, writes what is still true into `<distilled-file>`, and hx swaps the
accumulation for the distillation.

What one invocation does, in order, writing nothing until everything validates:

1. The distilled file must exist and be non-empty; an empty distillation says
   nothing is still true, which is `hx bench`, not this.
2. The distilled directives become one pending learnings text proposal
   (`hx.learn.propose_text`), authored as the agent they came from. A directive
   that outlives a goal is a lesson, and a lesson is owed to every agent of the
   role, not to one per-id file: a reviewer turns it into rules
   (`hx learn pending`, `hx learn accept ID --signal … --check …`), and the next
   launch carries them in every persona.
3. Every `### Goal addendum <ts>` subsection is removed from the Work Item's
   `## Goal`, and the matching `tasks.json` entries are dropped. The goal
   paragraph itself is never touched.

Only the Partner calls this (spec 08): it rewrites what the agent carries.
A refusal names the first thing wrong and writes nothing.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from . import amend as amend_mod, learn, store, tasks as tasks_mod
from .caller import require_partner_caller
from .errors import NotFound, Refused
from .workitems import (
    SECTION_GOAL,
    find_work_item,
    section_bounds,
    split_frontmatter_text,
)

DISTILL = "HX-DISTILL"


def read_distilled(distilled_file: str | Path) -> tuple[Path, str]:
    path = Path(distilled_file)
    if not path.is_file():
        raise NotFound(f"{path}: no distilled file; the distillation is always a file (spec 06)")
    text = path.read_text().strip("\n")
    if not text.strip():
        raise Refused(f"refuse: {path} is empty; nothing still true is `hx bench`, not distill")
    return path, text


def strip_addenda(body: str) -> tuple[str, int]:
    """`body` with every `### Goal addendum` subsection removed. Returns (body, count).

    Fence-aware: a `### Goal addendum` line inside a fenced block is content, not a heading.
    """
    heading = amend_mod.ADDENDUM_HEADING
    lines = body.split("\n")
    out: list[str] = []
    removed = 0
    skipping = False
    in_fence: str | None = None
    for line in lines:
        fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence:
            marker = fence.group(1)[0]
            in_fence = marker if in_fence is None else (None if in_fence == marker else in_fence)
            if not skipping:
                out.append(line)
            continue
        if in_fence is None and line.startswith("### "):
            if line == heading or line.startswith(heading + " "):
                skipping = True
                removed += 1
                continue
            skipping = False
        elif in_fence is None and line.startswith("## "):
            skipping = False
        if not skipping:
            out.append(line)
    return "\n".join(out), removed


def distill(root: Path, item_id: str, distilled_file: str | Path, *, env=None) -> dict:
    require_partner_caller("distill", env)
    harness = root / "config" / item_id / "harness.json"
    if not harness.is_file():
        raise NotFound(f"{harness}: no config for {item_id}")
    distilled_path, distilled = read_distilled(distilled_file)

    work_item = find_work_item(root, item_id)
    goal_addenda = 0
    new_body = None
    if work_item is not None:
        front, body = split_frontmatter_text(work_item.read_text())
        if section_bounds(body, SECTION_GOAL) is not None:
            new_body, goal_addenda = strip_addenda(body)

    entries = tasks_mod.load_tasks(root)
    entry = entries.get(item_id) or {}
    recorded = len(entry.get("addenda") or [])

    # Everything validated: write. The proposal first, then the Work Item, then tasks.json.
    try:
        role = json.loads(harness.read_text()).get("role")
    except (OSError, ValueError, AttributeError):
        role = None
    proposal = learn.propose_text(
        root, text=distilled, author=item_id,
        role=role if isinstance(role, str) and learn.ROLE_NAME_RE.match(role) else None,
        source=f"distilled from {item_id}'s goal addenda by hx distill", env=env,
    )
    if work_item is not None and new_body is not None:
        store.atomic_write_text(work_item, front + new_body)
    if recorded:
        entry["addenda"] = []
        entries[item_id] = entry
        tasks_mod.write_tasks(root, entries)

    distilled_path.unlink(missing_ok=True)
    return {
        "id": item_id,
        "proposal": proposal["id"],
        "goal_addenda_removed": goal_addenda,
        "recorded_addenda_dropped": recorded,
    }


def line(result: dict) -> str:
    return (
        f"{DISTILL} {result['id']} proposal={result['proposal']} "
        f"goal_addenda={result['goal_addenda_removed']} recorded={result['recorded_addenda_dropped']}"
    )


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx distill", add_help=True)
    parser.add_argument("id")
    parser.add_argument("distilled_file", metavar="DISTILLED-FILE")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    print(line(distill(root, args.id, args.distilled_file, env=env)))
    return 0
