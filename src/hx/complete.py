"""`hx complete <outcome>` — the HarnessAgent's last action (spec 06, 08).

Completion is machine-checked, never prose: `done` runs the order's `### Checks` block with
`bash -e` in the worktree, requires a clean worktree, and requires no subagent stream to be
open. A refusal prints `HX-CHECK-FAILED <id>` with the failing output, changes nothing, and
leaves the item `working` with its goal active, so the agent fixes it and runs it again.

Only success prints `HX-COMPLETE <id> <outcome>` as the last line of stdout. That line is the
goal evaluator's proof, because it reads tool output in the transcript and calls no tools
itself (spec 06).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

from . import flush as flush_mod, goal, store, streams, tasks as tasks_mod, timestamps, wake
from .caller import require_agent_caller
from .config_harness import load_harness, resolve_workdir
from .errors import Refused
from .ids import OUTCOMES, PARTNER
from .orders import parse_order_text
from .workitems import (
    SECTION_DIGEST,
    replace_section,
    parse_work_item,
    rename_state,
    require_work_item,
    section_bounds,
    section_text,
    set_frontmatter,
    split_frontmatter_text,
)

CHECK_FAILED = "HX-CHECK-FAILED"
COMPLETE = "HX-COMPLETE"

#: Written under `## Digest` until the Companion writes the real one at M5 (spec 10).
DIGEST_PLACEHOLDER = "pending companion"

#: `templates/work-item.md` ships `## Digest` with an angle-bracketed note saying the
#: Companion fills it in. That note is not content, so it is replaced, not appended to.
_TEMPLATE_NOTE = re.compile(r"^<[^>]*>$")


class CheckFailed(Refused):
    """A refused completion. The item stays `working` and nothing on disk changed."""


def workdir_for(root: Path, item_id: str) -> Path:
    """The worktree checks run in; `HARNESS_ROOT` for the Partner, which has none (spec 06)."""
    if item_id == PARTNER:
        return root
    harness = root / "config" / item_id / "harness.json"
    if harness.is_file():
        config = load_harness(harness, check_cross_file=False)
        if config.workdir:
            return resolve_workdir(config.workdir, root)
    return root / "wt" / item_id


def run_checks(checks: str, workdir: Path, env=None) -> subprocess.CompletedProcess:
    """Run the `### Checks` block with `bash -e`; every command must exit 0 (spec 06)."""
    import os

    child = dict(os.environ if env is None else env)
    child.setdefault("HARNESS_ROOT", str(workdir))
    return subprocess.run(
        ["bash", "-e", "-c", checks],
        cwd=str(workdir),
        capture_output=True,
        text=True,
        check=False,
        env=child,
    )


def worktree_is_dirty(workdir: Path) -> tuple[bool, str]:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=str(workdir), capture_output=True, text=True, check=False
    )
    if result.returncode != 0:
        return False, ""
    return bool(result.stdout.strip()), result.stdout


def preflight(root: Path, item_id: str, outcome: str, *, env=None) -> None:
    """Every refusal, before anything is written. Raises `CheckFailed`."""
    open_streams = streams.subagent_streams(root, item_id, state="open")
    if open_streams:
        listed = ", ".join(str(p.relative_to(root)) for p in open_streams)
        raise CheckFailed(
            f"{CHECK_FAILED} {item_id}\n{len(open_streams)} subagent stream(s) still open: "
            f"{listed}\nWait for your subagents to finish, then run `hx complete {outcome}` again."
        )

    if outcome != "done":
        # `blocked`, `decision` and `exhausted` run no checks (spec 02 Completion).
        return

    workdir = workdir_for(root, item_id)
    if item_id != PARTNER:
        dirty, listing = worktree_is_dirty(workdir)
        if dirty:
            raise CheckFailed(
                f"{CHECK_FAILED} {item_id}\nthe worktree {workdir} is dirty:\n{listing}"
                f"Commit your work, then run `hx complete done` again."
            )

    entry = tasks_mod.load_tasks(root).get(item_id) or {}
    order_text = entry.get("order")
    if not order_text:
        raise CheckFailed(
            f"{CHECK_FAILED} {item_id}\nno order recorded in tasks.json; `hx complete done` runs "
            f"the `### Checks` block of the order it was dispatched with (spec 06)."
        )
    checks = parse_order_text(order_text, f"tasks.json[{item_id}].order").checks
    result = run_checks(checks, workdir, env)
    if result.returncode != 0:
        raise CheckFailed(
            f"{CHECK_FAILED} {item_id}\n`### Checks` failed with exit {result.returncode} in {workdir}:\n"
            f"{result.stdout}{result.stderr}"
        )


def final_digest(root: Path, item_id: str, outcome: str, *, env=None) -> str:
    """The Companion's final pass: the Partner-facing summary (spec 10, 06).

    Composed from the main step state and every closed-stream digest. For `blocked` and
    `decision` the blocker or the question comes **first**, because that is what the Partner's
    addendum has to answer (spec 10 "Final pass").
    """
    from .companion import digest_path, open_streams, state_path
    from .stepstate import load as load_state

    state = load_state(state_path(root, item_id, f"{item_id}-main")) or {}
    lines: list[str] = []

    if outcome in ("blocked", "decision"):
        heading = "**Blocked on:**" if outcome == "blocked" else "**Decision needed:**"
        first = [str(b) for b in (state.get("blockers") or []) if str(b).strip()]
        if not first:
            first = [str(d.get("d", "")) for d in (state.get("decisions") or []) if isinstance(d, dict)][-1:]
        lines.append(f"{heading} {first[0] if first else 'see `## Open decision` in this item.'}")
        lines.append("")

    if state.get("goal"):
        lines += [f"**Goal:** {state['goal']}", ""]

    closed = [c for c in (state.get("closed_steps") or []) if isinstance(c, dict)]
    if closed:
        lines.append("**Done**")
        for step in closed:
            commit = f" `{step['commit']}`" if step.get("commit") else ""
            mark = "" if step.get("verified") else " (unverified)"
            lines.append(f"- {step.get('outcome', step.get('id', '?'))}{commit}{mark}")
        lines.append("")

    open_steps = [o for o in (state.get("open_steps") or []) if isinstance(o, dict)]
    if open_steps:
        lines.append("**Left open**")
        for step in open_steps:
            lines.append(f"- {step.get('intent', step.get('id', '?'))}"
                         + (f" — next: {step['next']}" if step.get("next") else ""))
        lines.append("")

    for stream in open_streams(root, item_id):
        if stream.endswith("-main"):
            continue
        path = digest_path(root, item_id, stream)
        if path.is_file():
            text = path.read_text().strip()
            if text and text != "_pending companion_":
                lines += [f"**{stream}**", text, ""]

    return "\n".join(lines).strip() or DIGEST_PLACEHOLDER


def write_digest(path: Path, text: str) -> None:
    """Write `## Digest` once. Never over something already written there (spec 06)."""
    _, body = split_frontmatter_text(path.read_text())
    if section_bounds(body, SECTION_DIGEST) is None:
        return
    existing = (section_text(body, SECTION_DIGEST) or "").strip()
    if existing and not _TEMPLATE_NOTE.match(existing) and existing != DIGEST_PLACEHOLDER:
        return
    replace_section(path, SECTION_DIGEST, text)


def complete(root: Path, outcome: str, *, item_id: str | None = None, env=None) -> dict:
    if outcome not in OUTCOMES:
        raise Refused(f"refuse: `{outcome}` is not an outcome ({', '.join(OUTCOMES)}, spec 06)")
    item_id = item_id or require_agent_caller("complete", env)

    path = require_work_item(root, item_id)
    item = parse_work_item(path)
    if item.state != "working":
        raise Refused(
            f"refuse: {item_id} is `{item.state}`, not `working`; only a working item completes (spec 06)"
        )

    preflight(root, item_id, outcome, env=env)

    ts = timestamps.now()
    # The Companion has to be at the head of the stream before its final pass (spec 08).
    flush_mod.flush(root, item_id, env=env)
    write_digest(path, final_digest(root, item_id, outcome, env=env))
    set_frontmatter(path, outcome=outcome)

    promoted: list[str] = []
    with store.locked(root):
        entries = tasks_mod.load_tasks(root)
        entry = entries.setdefault(item_id, tasks_mod.new_entry("", list(item.after), item.dispatched or ts))
        entry["outcome"] = outcome
        entry["completed"] = ts

        if outcome == "done":
            from .workitems import find_work_items

            by_id, _ = find_work_items(root)
            waiting = {}
            for other_id, files in by_id.items():
                if other_id == item_id or not files:
                    continue
                other = files[0]
                if other.name.endswith("-queued.md"):
                    waiting[other_id] = list((entries.get(other_id) or {}).get("after") or [])
            promoted = tasks_mod.promotable(entries, waiting)
        tasks_mod.write_tasks(root, entries)

    final = rename_state(path, "complete")
    goal.clear_marker(root, item_id)

    promotions = []
    for other_id in promoted:
        promotions.append(promote(root, other_id, env=env))

    woke = None
    if item_id != PARTNER:
        woke = wake.wake_partner_status(root, f"{item_id} complete: {outcome}; hx read {item_id}")

    return {
        "id": item_id,
        "outcome": outcome,
        "file": str(final.relative_to(root)),
        "completed": ts,
        "promoted": promotions,
        "woke_partner": woke,
    }


def promote(root: Path, item_id: str, *, env=None) -> dict:
    """`queued → working` + the goal, inside `hx complete done` of the last dependency (spec 06)."""
    path = require_work_item(root, item_id)
    final = rename_state(path, "working")
    sent = goal.send_goal(root, item_id, env=env)
    return {"id": item_id, "file": str(final.relative_to(root)), "goal": sent}


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx complete", add_help=True)
    parser.add_argument("outcome", choices=list(OUTCOMES))
    parser.add_argument("--id", help="the id to complete (default: HARNESS_ID)")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    try:
        result = complete(root, args.outcome, item_id=args.id, env=env)
    except CheckFailed as exc:
        # The refusal itself is the output the agent reads; it changed nothing.
        print(exc.message)
        return 1

    if result["woke_partner"] not in (None, wake.ACCEPTED):
        # A warning, never a failure: the work is done and recorded, and `hx heartbeat`
        # wakes the Partner again when the board changed (spec 08, 12).
        print(
            f"hx: complete: the Partner was not woken ({result['woke_partner']}); "
            f"the completion is recorded and `hx heartbeat` will report it",
            file=sys.stderr,
        )
    for promotion in result["promoted"]:
        print(f"HX-PROMOTED {promotion['id']} working goal={promotion['goal']}")
    # Last line of stdout, always: the goal evaluator's proof (spec 06).
    print(f"{COMPLETE} {result['id']} {result['outcome']}")
    return 0
