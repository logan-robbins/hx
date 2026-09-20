"""`hx board` — the whole instance in one screen, and every invariant (spec 08).

Text form: one line per id, `<work-item-file>  <after>  <outcome>  <open subagents>  <goal ts>`,
then the invariant errors. `--json` is the object in CONTRACTS.md. Exit 0 when `errors` is
empty, else 1. With `--require-done <id>…`, exit 0 iff every listed item is `complete` with
outcome `done` — the shape the Partner's own `### Checks` block uses.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import streams, timestamps, tmux
from .config_harness import load_harness
from .errors import HxError, ValidationError
from .ids import ID_RE, PARTNER, sort_key
from .tasks import is_ready, load_tasks, outcome_of
from .workitems import find_work_items, parse_work_item

HOME_SETTINGS = "settings.json"
HOME_CREDENTIALS = ".credentials.json"


def _config_ids(root: Path) -> list[str]:
    config = root / "config"
    if not config.is_dir():
        return []
    return sorted(
        entry.name for entry in config.iterdir() if entry.is_dir() and ID_RE.match(entry.name)
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
    """Build the `hx board --json` object, errors included (CONTRACTS.md)."""
    errors: list[str] = []

    by_id, name_errors = find_work_items(root)
    errors.extend(name_errors)

    try:
        tasks = load_tasks(root)
    except ValidationError as exc:
        errors.append(str(exc))
        tasks = {}

    config_ids = _config_ids(root)
    sessions = tmux.live_sessions(env)
    if sessions is None:
        errors.append("tmux: not found on PATH; hx cannot tell which sessions are live (spec 08 `hx doctor`)")

    ids = sorted(set(by_id) | set(config_ids) | set(tasks), key=sort_key)

    items: list[dict] = []
    for item_id in ids:
        files = by_id.get(item_id, [])
        if len(files) > 1:
            listed = ", ".join(str(f.relative_to(root)) for f in files)
            errors.append(f"{item_id}: {len(files)} work items ({listed}); one work item per id (spec 06)")
        work_item = None
        rel_file = None
        if files:
            rel_file = str(files[0].relative_to(root))
            try:
                work_item = parse_work_item(files[0])
            except ValidationError as exc:
                errors.append(str(exc))

        config_dir = root / "config" / item_id
        has_config = config_dir.is_dir()
        if files and not has_config:
            errors.append(f"{rel_file}: no config/{item_id}/ (spec 08 board invariants)")
        if has_config and not files:
            errors.append(f"config/{item_id}/: no work item (spec 08 board invariants)")
        if item_id in tasks and not has_config:
            errors.append(f"tasks.json: `{item_id}` has no config/{item_id}/ (spec 08 board invariants)")

        role = None
        pod = work_item.pod if work_item else None
        if has_config:
            try:
                config = load_harness(config_dir / "harness.json", root=root, check_cross_file=False)
                role = config.role
                pod = pod or config.pod
            except ValidationError as exc:
                errors.append(str(exc))

        task = tasks.get(item_id) or {}
        after = task.get("after") if isinstance(task.get("after"), list) else None
        if after is None:
            after = list(work_item.after) if work_item else []
        outcome = task.get("outcome") if item_id in tasks else None
        if outcome is None and work_item is not None:
            outcome = work_item.outcome
        dispatched = task.get("dispatched") or (work_item.dispatched if work_item else None)
        completed = task.get("completed")

        state = work_item.state if work_item else None
        goal_ts = _marker_ts(root / "run" / item_id / "goal")
        goal_pending = (root / "run" / item_id / "goal-pending").exists()
        turn_ts = _marker_ts(root / "run" / item_id / "turn")
        open_subagents = streams.open_subagents(root, item_id)
        session_alive = bool(sessions and item_id in sessions)

        if state == "working":
            if sessions is not None and not session_alive:
                errors.append(f"{rel_file}: no live tmux session {item_id}")
            if goal_ts is None:
                errors.append(
                    f"{rel_file}: working with no run/{item_id}/goal marker; a live pane on a "
                    f"working item with no goal marker is a violation (spec 06)"
                )
        if state == "queued":
            if not after:
                errors.append(
                    f"{rel_file}: queued with an empty `after`; a queued item waits on at least "
                    f"one entry that is not yet done (spec 06)"
                )
            elif is_ready(tasks, after):
                errors.append(
                    f"{rel_file}: queued but every `after` entry is done; it should have been "
                    f"promoted to working (spec 06)"
                )
            if goal_ts is not None:
                errors.append(f"{rel_file}: queued with a run/{item_id}/goal marker (spec 06)")
        if state == "complete" and open_subagents:
            errors.append(
                f"{rel_file}: complete with {open_subagents} open subagent stream(s) "
                f"(spec 08 board invariants)"
            )

        home = root / "run" / item_id / "home"
        if home.is_dir():
            for required in (HOME_SETTINGS, HOME_CREDENTIALS):
                if not (home / required).exists():
                    errors.append(f"run/{item_id}/home/: no {required} (spec 08 board invariants)")

        items.append(
            {
                "id": item_id,
                "pod": pod,
                "role": role,
                "state": state,
                "file": rel_file,
                "after": list(after),
                "ready": is_ready(tasks, after),
                "outcome": outcome,
                "dispatched": dispatched,
                "completed": completed,
                "open_subagents": open_subagents,
                "goal_ts": goal_ts,
                "goal_pending": goal_pending,
                "session_alive": session_alive,
                "context_tokens": streams.last_context_tokens(root, item_id),
                "seams": streams.count_seams(root, item_id, dispatched),
                "turn_ts": turn_ts,
            }
        )

    return {
        "root_abs": str(root),
        "ts": timestamps.now(),
        "items": items,
        "errors": errors,
    }


def render_text(board: dict) -> str:
    """One line per id, then the invariant errors (spec 08)."""
    lines = []
    for item in board["items"]:
        lines.append(
            "  ".join(
                (
                    item["file"] or f"({item['id']}: no work item)",
                    ",".join(item["after"]) or "-",
                    item["outcome"] or "-",
                    str(item["open_subagents"]),
                    item["goal_ts"] or "-",
                )
            )
        )
    lines.extend(board["errors"])
    return "\n".join(lines)


def require_done(board: dict, ids: list[str]) -> list[str]:
    """Spec 08: exit 0 iff every listed item is `complete` with outcome `done`."""
    index = {item["id"]: item for item in board["items"]}
    failures = []
    for item_id in ids:
        item = index.get(item_id)
        if item is None:
            failures.append(f"require-done {item_id}: no such id")
        elif item["state"] != "complete":
            failures.append(f"require-done {item_id}: state is {item['state'] or 'unknown'}, not complete")
        elif item["outcome"] != "done":
            failures.append(f"require-done {item_id}: outcome is {item['outcome'] or 'none'}, not done")
    return failures


def main(argv: list[str], root: Path, *, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hx board", add_help=True)
    parser.add_argument("--json", action="store_true", help="emit the CONTRACTS.md object")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    parser.add_argument(
        "--require-done",
        nargs="+",
        metavar="ID",
        default=None,
        help="exit 0 iff every listed id is complete with outcome done",
    )
    args = parser.parse_args(argv)

    if not root.is_dir():
        raise HxError(f"{root}: HARNESS_ROOT does not exist; run `hx install --root {root}` first")

    board = collect(root, env=env)

    if args.require_done is not None:
        failures = require_done(board, args.require_done)
        if args.json:
            print(json.dumps({**board, "require_done": args.require_done, "require_done_failures": failures}, indent=2))
        else:
            text = render_text(board)
            if text:
                print(text)
            for failure in failures:
                print(failure)
            for item_id in args.require_done:
                if not any(f.startswith(f"require-done {item_id}:") for f in failures):
                    print(f"require-done {item_id}: ok")
        return 1 if failures else 0

    if args.json:
        print(json.dumps(board, indent=2))
    else:
        text = render_text(board)
        if text:
            print(text)
    return 1 if board["errors"] else 0
