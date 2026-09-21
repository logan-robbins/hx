"""`hx board` — a plain listing of what is on disk, one line per worker id (spec 08).

It judges nothing and exits 0. The v1 cut (spec 14 D25) removed the nine invariants, the
`errors` list, and `--require-done`: a board that refuses to agree with the filesystem is a
policy engine, and hx has none. `partner` is not an item: the Partner has no work item.

Text form: `id  pod  state  outcome  dispatched  alive|dead  subagents=N  context=N  seams=N`.
`--json` is the object in CONTRACTS.md.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import streams, timestamps, tmux
from .config_harness import load_harness
from .errors import HxError, ValidationError
from .ids import ID_RE, PARTNER, sort_key
from .tasks import load_tasks
from .workitems import find_work_items, parse_work_item


def _config_ids(root: Path) -> list[str]:
    config = root / "config"
    if not config.is_dir():
        return []
    return sorted(
        entry.name
        for entry in config.iterdir()
        if entry.is_dir() and ID_RE.match(entry.name) and entry.name != PARTNER
    )


def _marker_ts(path: Path) -> str | None:
    """A marker file's timestamp: its content when that is one, else its mtime."""
    if not path.exists():
        return None
    try:
        text = path.read_text().strip()
    except OSError:
        text = ""
    if text and timestamps.looks_like(text.splitlines()[0]):
        return text.splitlines()[0]
    return timestamps.from_mtime(path)


def collect(root: Path, *, env: dict[str, str] | None = None) -> dict:
    """Build the `hx board --json` object (CONTRACTS.md). Nothing here can fail a board."""
    by_id = find_work_items(root)
    try:
        tasks = load_tasks(root)
    except ValidationError:
        # An unreadable control plane is a doctor problem, not a board one.
        tasks = {}

    sessions = tmux.live_sessions(env) or set()
    ids = sorted(
        (set(by_id) | set(_config_ids(root)) | set(tasks)) - {PARTNER}, key=sort_key
    )

    items: list[dict] = []
    for item_id in ids:
        files = by_id.get(item_id, [])
        work_item = None
        rel_file = None
        state = None
        if files:
            rel_file = str(files[0].relative_to(root))
            state = files[0].name.removesuffix(".md").rpartition("-")[2]
            try:
                work_item = parse_work_item(files[0])
            except ValidationError:
                pass

        role = None
        pod = work_item.pod if work_item else (files[0].parent.name if files else None)
        harness = root / "config" / item_id / "harness.json"
        if harness.is_file():
            try:
                config = load_harness(harness, root=root, check_cross_file=False)
                role = config.role
                pod = pod or config.pod
            except ValidationError:
                pass

        task = tasks.get(item_id) or {}
        outcome = task.get("outcome") if item_id in tasks else None
        if outcome is None and work_item is not None:
            outcome = work_item.outcome
        dispatched = task.get("dispatched") or (work_item.dispatched if work_item else None)

        items.append(
            {
                "id": item_id,
                "pod": pod,
                "role": role,
                "state": state,
                "file": rel_file,
                "outcome": outcome,
                "dispatched": dispatched,
                "completed": task.get("completed"),
                "open_subagents": streams.open_subagents(root, item_id),
                "goal_ts": _marker_ts(root / "run" / item_id / "goal"),
                "session_alive": item_id in sessions,
                "context_tokens": streams.last_context_tokens(root, item_id),
                "seams": streams.count_seams(root, item_id, dispatched),
                "turn_ts": _marker_ts(root / "run" / item_id / "turn"),
            }
        )

    return {"root_abs": str(root), "ts": timestamps.now(), "items": items}


def render_text(board: dict) -> str:
    """One line per id, in the order of spec 08. No verdicts, no trailing error block."""
    lines = []
    for item in board["items"]:
        lines.append(
            "  ".join(
                (
                    item["id"],
                    item["pod"] or "-",
                    item["state"] or "-",
                    item["outcome"] or "-",
                    item["dispatched"] or "-",
                    "alive" if item["session_alive"] else "dead",
                    f"subagents={item['open_subagents']}",
                    f"context={item['context_tokens'] if item['context_tokens'] is not None else '-'}",
                    f"seams={item['seams'] if item['seams'] is not None else '-'}",
                )
            )
        )
    return "\n".join(lines)


def main(argv: list[str], root: Path, *, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hx board", add_help=True)
    parser.add_argument("--json", action="store_true", help="emit the CONTRACTS.md object")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if not root.is_dir():
        raise HxError(f"{root}: HARNESS_ROOT does not exist; run `hx install --root {root}` first")

    board = collect(root, env=env)
    if args.json:
        print(json.dumps(board, indent=2))
    else:
        text = render_text(board)
        if text:
            print(text)
    return 0
