"""`hx distill <id> <distilled-file> [--memory]` — shrink what the agent carries.

Addenda accumulate without bound: every `hx resume` and `hx amend` appends a
`### Goal addendum <ts>` to the Work Item's `## Goal` plus `{ts, text}` in
`tasks.json[<id>].addenda`, and both ride every context file the agent reads
after a boundary. Below-header memory in `config/<id>/AGENTS.md` grows the same
way. Distilling is the Partner's periodic correction: it reads the accumulation,
writes what is still true into `<distilled-file>`, and hx swaps the accumulation
for the distillation.

What one invocation does, in order, writing nothing until everything validates:

1. The distilled file must exist and be non-empty; an empty distillation says
   nothing is still true, which is `hx bench`, not this.
2. Above the header of `config/<id>/AGENTS.md`, a fenced `## Distilled
   directives` section is created or replaced whole. The fence markers are
   `hx:distilled`, which `compile`'s region regex does not match, so the next
   `hx compile` keeps them: promoted invariants are per-agent persona now.
3. Every `### Goal addendum <ts>` subsection is removed from the Work Item's
   `## Goal`, and the matching `tasks.json` entries are dropped. The goal and
   its current `### Checks` are never touched: a checks replacement an addendum
   once carried already lives in both, so removing the prose removes nothing
   the gate runs.
4. With `--memory`, the below-header memory is replaced whole with a second
   heading in the distilled file (`## Memory`, split off before step 2).
   Without it, below-header memory is left alone.

Only the Partner calls this (spec 08): it rewrites what the agent remembers.
A refusal names the first thing wrong and writes nothing.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from . import amend as amend_mod, compose as compose_mod, store, tasks as tasks_mod
from .caller import require_partner_caller
from .errors import NotFound, Refused, ValidationError
from .workitems import (
    SECTION_GOAL,
    find_work_item,
    section_bounds,
    split_frontmatter_text,
)

DISTILL = "HX-DISTILL"

#: The promoted section above the header. The fence kind is deliberately not
#: `global` or `role`: `compile` replaces those regions whole and would eat
#: what was promoted.
SECTION_DISTILLED = "## Distilled directives"
FENCE_BEGIN = "<!-- hx:distilled begin (promoted by `hx distill` — edit via distill, not by hand) -->"
FENCE_END = "<!-- hx:distilled end -->"
MEMORY_HEADING = "## Memory"


def read_distilled(distilled_file: str | Path) -> tuple[Path, str]:
    path = Path(distilled_file)
    if not path.is_file():
        raise NotFound(f"{path}: no distilled file; the distillation is always a file (spec 06)")
    text = path.read_text().strip("\n")
    if not text.strip():
        raise Refused(f"refuse: {path} is empty; nothing still true is `hx bench`, not distill")
    return path, text


def split_memory(text: str) -> tuple[str, str | None]:
    """Split off a trailing `## Memory` section for `--memory`; otherwise (directives, None)."""
    bounds = section_bounds(text, MEMORY_HEADING)
    if bounds is None:
        return text, None
    lines = text.split("\n")
    start, end = bounds
    memory = "\n".join(lines[start + 1 : end]).strip("\n")
    rest = "\n".join(lines[:start] + lines[end:]).strip("\n")
    return rest, (memory or None)


def distill_upper(upper: str, directives: str) -> tuple[str, bool]:
    """`upper` with the fenced distilled section created or replaced. Returns (text, replaced)."""
    block = f"{SECTION_DISTILLED}\n\n{FENCE_BEGIN}\n{directives.strip(chr(10))}\n{FENCE_END}"
    pattern = re.compile(
        rf"^{re.escape(SECTION_DISTILLED)}\s*\n\n{re.escape(FENCE_BEGIN)}\n.*?\n{re.escape(FENCE_END)}",
        re.S | re.M,
    )
    if pattern.search(upper):
        return pattern.sub(block, upper), True
    stripped = upper.strip("\n")
    return (f"{stripped}\n\n{block}\n" if stripped else f"{block}\n"), False


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


def distill(root: Path, item_id: str, distilled_file: str | Path, *, memory: bool = False,
            env=None) -> dict:
    require_partner_caller("distill", env)
    agents = root / "config" / item_id / "AGENTS.md"
    if not agents.is_file():
        raise NotFound(f"{agents}: no AGENTS.md to distill into")
    text = agents.read_text()
    if compose_mod.HEADER not in text:
        raise ValidationError(
            f"{agents}: no `{compose_mod.HEADER}` line; hx cannot tell persona from memory"
        )
    upper, _, lower = text.partition(compose_mod.HEADER)

    distilled_path, distilled = read_distilled(distilled_file)
    directives, new_memory = split_memory(distilled)
    if memory and new_memory is None:
        raise Refused(
            f"refuse: {distilled_path} has no `{MEMORY_HEADING}` section; "
            "`--memory` replaces below-header memory whole and needs one"
        )
    if not directives.strip():
        raise Refused(
            f"refuse: {distilled_path} has nothing outside `{MEMORY_HEADING}`; "
            "the distilled directives are the point of this command"
        )

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

    # Everything validated: write. AGENTS.md first, then the Work Item, then tasks.json.
    before = len(text.encode())
    new_upper, replaced = distill_upper(upper, directives)
    if memory:
        assert new_memory is not None
        new_text = new_upper + compose_mod.HEADER + "\n" + new_memory.strip("\n") + "\n"
    else:
        new_text = new_upper + compose_mod.HEADER + lower if lower else new_upper + compose_mod.HEADER + "\n"
    store.atomic_write_text(agents, new_text)
    if work_item is not None and new_body is not None:
        store.atomic_write_text(work_item, front + new_body)
    if recorded:
        entry["addenda"] = []
        entries[item_id] = entry
        tasks_mod.write_tasks(root, entries)

    distilled_path.unlink(missing_ok=True)
    return {
        "id": item_id,
        "directives_replaced": replaced,
        "goal_addenda_removed": goal_addenda,
        "recorded_addenda_dropped": recorded,
        "memory_replaced": memory,
        "bytes_before": before,
        "bytes_after": len(new_text.encode()),
    }


def line(result: dict) -> str:
    return (
        f"{DISTILL} {result['id']} directives={'replaced' if result['directives_replaced'] else 'created'} "
        f"goal_addenda={result['goal_addenda_removed']} recorded={result['recorded_addenda_dropped']} "
        f"memory={'replaced' if result['memory_replaced'] else 'kept'} "
        f"bytes={result['bytes_before']}->{result['bytes_after']}"
    )


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx distill", add_help=True)
    parser.add_argument("id")
    parser.add_argument("distilled_file", metavar="DISTILLED-FILE")
    parser.add_argument("--memory", action="store_true",
                        help="also replace below-header memory with the file's ## Memory section")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    print(line(distill(root, args.id, args.distilled_file, memory=args.memory, env=env)))
    return 0
