"""`hx show <id> [--json]` — everything hx knows about one id (spec 08, CONTRACTS.md).

Read-only. Fields a later milestone produces — step state (M5), the context file (M2),
metrics (M7) — are `null` or empty until then, never omitted: "absent values are `null`,
never omitted" (CONTRACTS.md).
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from . import archive, companion as companion_mod, compose, streams, timestamps, tmux
from .config_harness import load_harness
from .errors import NotFound, ValidationError
from .ids import PARTNER
from .tasks import load_tasks
from .workitems import find_work_item, parse_work_item, split_frontmatter_text

#: CONTRACTS.md: the last 50 records of each stream, as parsed JSON objects.
TAIL_RECORDS = 50
#: CONTRACTS.md: the last 120 lines of the pane, ANSI stripped.
PANE_LINES = 120

_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b[@-_]|[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _stream_entry(root: Path, path: Path, handle: str, is_open: bool, item_id: str) -> dict:
    records = list(streams.iter_records(path))
    entry = {
        "handle": handle,
        "path": str(path.relative_to(root)),
        "open": is_open,
        "records": len(records),
        "tail": records[-TAIL_RECORDS:],
    }
    if not is_open:
        digest = root / "state" / item_id / f"{handle}.digest.md"
        entry["digest"] = digest.read_text() if digest.is_file() else None
    return entry


def _streams(root: Path, item_id: str) -> list[dict]:
    found = []
    main = streams.main_stream(root, item_id)
    if main.is_file():
        found.append(_stream_entry(root, main, f"{item_id}-main", True, item_id))
    for path in streams.subagent_streams(root, item_id):
        handle = path.name.rsplit("-", 1)[0]
        found.append(_stream_entry(root, path, handle, path.name.endswith("-open.jsonl"), item_id))
    return found


def _step_state(root: Path, item_id: str) -> dict:
    """The Companion's step state per stream (spec 07.2); empty until M5."""
    directory = root / "state" / item_id
    state: dict[str, object] = {}
    if not directory.is_dir():
        return state
    for path in sorted(directory.glob("*.json")):
        try:
            state[path.stem] = json.loads(path.read_text())
        except json.JSONDecodeError:
            state[path.stem] = None
    return state


def _context_file(root: Path, item_id: str) -> dict:
    path = compose.context_path(root, item_id, f"{item_id}-main")
    if not path.is_file():
        return {"path": str(path.relative_to(root)), "text": None, "seam_ts": None}
    return {
        "path": str(path.relative_to(root)),
        "text": path.read_text(),
        "seam_ts": timestamps.from_mtime(path),
    }


def _pane(root: Path, item_id: str, env) -> dict:
    alive = tmux.has_session(item_id, env)
    lines: list[str] = []
    if alive:
        from .goal import capture_pane

        captured = capture_pane(item_id, env, lines=PANE_LINES)
        if captured:
            lines = strip_ansi(captured).split("\n")[-PANE_LINES:]
    if not lines:
        # The UI's fallback for a dead session (spec 03, 11): the raw pane capture.
        log = tmux.pane_log(root, item_id)
        if log.is_file():
            lines = strip_ansi(log.read_text(errors="replace")).split("\n")[-PANE_LINES:]
    return {"session": item_id, "alive": alive, "lines": lines}


def _turn(root: Path, item_id: str) -> dict | None:
    """`run/<id>/turn` as CONTRACTS.md renders it: `{ts, background_tasks}`, or null.

    Written by the `stop` hook at every turn boundary. A non-empty `background_tasks` is the
    UI's "stopped with work still running", and is what `hx seam` defers on (spec 09.2).
    """
    from .hook_stop import turn_marker

    path = turn_marker(root, item_id)
    if not path.is_file():
        return None
    try:
        marker = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(marker, dict):
        return None
    tasks = marker.get("background_tasks")
    return {"ts": marker.get("ts"), "background_tasks": tasks if isinstance(tasks, list) else []}


def collect(root: Path, item_id: str, *, env=None) -> dict:
    """The `hx show <id> --json` document. Raises `NotFound` when the id is unknown."""
    root = Path(root)
    path = find_work_item(root, item_id)
    config_dir = root / "config" / item_id
    if path is None and not config_dir.is_dir():
        raise NotFound(f"{item_id}: no work item and no config/{item_id}/; unknown id")

    pod = role = state = None
    work_item = None
    if path is not None:
        state = path.name.rsplit("-", 1)[1].removesuffix(".md")
        pod = path.parent.name
        try:
            parsed = parse_work_item(path)
            pod = parsed.pod
            _, body = split_frontmatter_text(path.read_text())
            work_item = {
                "frontmatter": {
                    "id": parsed.id,
                    "pod": parsed.pod,
                    "outcome": parsed.outcome,
                    "dispatched": parsed.dispatched,
                },
                "body": body,
            }
        except ValidationError:
            work_item = {"frontmatter": None, "body": path.read_text()}

    harness = config_dir / "harness.json"
    if harness.is_file():
        try:
            config = load_harness(harness, check_cross_file=False)
            role = config.role
            pod = pod or config.pod
        except ValidationError:
            role = None

    entry = load_tasks(root).get(item_id)
    task = None
    if entry is not None:
        task = {
            "order": entry.get("order"),
            "addenda": list(entry.get("addenda") or []),
            "outcome": entry.get("outcome"),
            "dispatched": entry.get("dispatched"),
            "completed": entry.get("completed"),
        }

    subagents_file = root / "run" / item_id / "subagents.json"
    subagents: dict = {}
    if subagents_file.is_file():
        try:
            loaded = json.loads(subagents_file.read_text())
            subagents = loaded if isinstance(loaded, dict) else {}
        except json.JSONDecodeError:
            subagents = {}

    persona = root / "run" / item_id / "persona.md"

    document = {
        "id": item_id,
        "pod": pod,
        "role": role,
        "state": state,
        "file": str(path.relative_to(root)) if path else None,
        "work_item": work_item,
        "task": task,
        "persona_path": str(persona.relative_to(root)) if persona.is_file() else None,
        "step_state": _step_state(root, item_id),
        "context_file": _context_file(root, item_id),
        "streams": _streams(root, item_id),
        "subagents": subagents,
        # `hx metrics` is M7; the key is present and null until then.
        "metrics": None,
        "pane": _pane(root, item_id, env),
        "turn": _turn(root, item_id),
        "companion": companion_mod.activity(root, item_id),
        "archive": archive.archive_entries(root, item_id),
        "bench": archive.bench_entries(root, pod, item_id),
    }
    if item_id == PARTNER:
        # The Partner has no work item, task, or step state (CONTRACTS.md, spec 14 D25).
        partner_md = root / "PARTNER.md"
        return {
            "id": PARTNER,
            "partner_md": partner_md.read_text() if partner_md.is_file() else None,
            "pane": document["pane"],
            "streams": document["streams"],
            "companion": document["companion"],
        }
    return document


def render_text(document: dict) -> str:
    if document["id"] == PARTNER:
        lines = [f"{PARTNER}  pane={'alive' if document['pane']['alive'] else 'dead'}"]
        for stream in document["streams"]:
            lines.append(
                f"stream  {stream['handle']}  {'open' if stream['open'] else 'closed'}  "
                f"{stream['records']} records"
            )
        if document["partner_md"]:
            lines += ["", document["partner_md"].rstrip("\n")]
        return "\n".join(lines)

    lines = [
        f"{document['id']}  {document['state'] or '-'}  pod={document['pod'] or '-'}  "
        f"role={document['role'] or '-'}",
        f"file    {document['file'] or '-'}",
    ]
    task = document["task"]
    if task:
        lines.append(
            f"task    outcome={task['outcome'] or '-'}  "
            f"dispatched={task['dispatched'] or '-'}  completed={task['completed'] or '-'}  "
            f"addenda={len(task['addenda'])}"
        )
    for stream in document["streams"]:
        lines.append(
            f"stream  {stream['handle']}  {'open' if stream['open'] else 'closed'}  "
            f"{stream['records']} records"
        )
    lines.append(f"pane    {'alive' if document['pane']['alive'] else 'dead'}")
    if document["work_item"] and document["work_item"]["body"]:
        lines.append("")
        lines.append(document["work_item"]["body"].rstrip("\n"))
    return "\n".join(lines)


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx show", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    document = collect(root, args.id, env=env)
    print(json.dumps(document, indent=2) if args.json else render_text(document))
    return 0
