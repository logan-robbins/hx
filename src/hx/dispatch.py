"""`hx dispatch <id> <order-file> [...]` — the only way a task starts (spec 06, 08).

The order is a file, copied verbatim into the work item and recorded in `tasks.json`; no task
text ever crosses a command line. Everything happens under `run/tasks.lock`, in the write
order spec 08 gives, so re-running an interrupted dispatch completes it:

    tasks, then per-id archive, reset, render, rename, goal.

The Partner is dispatched the same way with three differences (spec 06, 08): no archive, no
wipe, no reset — its session, streams and state are continuous — and its pointer lands in
`run/partner/goal-pending`, because its own pane is mid-turn when it dispatches itself.
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import archive, goal, store, tasks as tasks_mod, timestamps, tmux
from .caller import require_partner_caller
from .config_harness import load_harness
from .errors import NotFound, Refused, ValidationError
from .ids import PARTNER
from .orders import parse_order
from .workitems import (
    find_work_item,
    load_template,
    render,
    rename_state,
    state_of,
    work_item_path,
)

#: Cleared from `run/<id>/home/` at every dispatch, and nothing else (spec 08).
HOME_WIPE = ("projects", "file-history", "history.jsonl")
#: Kept in `run/<id>/` across a dispatch; everything else there is cleared (spec 08).
#: The Companion's home and system prompt are kept for the same reason the agent's home is:
#: its session is continuous and serves the same agent across dispatches, and deleting its
#: `CLAUDE_CONFIG_DIR` out from under a running process breaks it quietly (spec 10).
RUN_KEEP = ("home", "persona.md", "companion-home", "companion-system.md")


@dataclass
class Plan:
    """One id's dispatch, validated and ready to apply."""

    id: str
    pod: str
    order_path: Path
    order_text: str
    after: list[str]
    work_item: Path
    already_applied: bool = False


def _pod_of(root: Path, item_id: str, work_item: Path) -> str:
    harness = root / "config" / item_id / "harness.json"
    if harness.is_file():
        return load_harness(harness, check_cross_file=False).pod
    return work_item.parent.name


def _validate(root: Path, pairs: list[tuple[str, str]], existing: dict[str, dict], env) -> list[Plan]:
    """Every order is validated before anything is written (spec 08 pseudo-code)."""
    plans: list[Plan] = []
    seen: set[str] = set()
    for item_id, order_file in pairs:
        if item_id in seen:
            raise Refused(f"refuse: {item_id} named twice in one dispatch")
        seen.add(item_id)

        order = parse_order(Path(order_file))
        work_item = find_work_item(root, item_id)
        if work_item is None:
            raise NotFound(
                f"{item_id}: no work item; `hx launch {item_id}` creates the `-idle` one first (spec 08)"
            )
        if not tmux.has_session(item_id, env):
            raise Refused(
                f"refuse: {item_id} has no live tmux session; `hx launch {item_id}` starts it "
                f"before a dispatch can send the goal (spec 08)"
            )

        _refuse_dirty_worktree(root, item_id)

        state = state_of(work_item)
        already = False
        if state != "idle":
            # An interrupted dispatch re-run: this id was already applied with this very
            # order, so completing the run means skipping it, not refusing it (spec 08,
            # "Re-running the same `hx dispatch` completes an interrupted one").
            record = existing.get(item_id) or {}
            same_order = record.get("order") == order.text
            same_after = list(record.get("after") or []) == order.after
            if state in ("working", "queued") and same_order and same_after:
                already = True
            else:
                raise Refused(
                    f"refuse: {item_id} is `{state}`, not `idle`; only an idle item is "
                    f"dispatched (spec 06). `hx bench {item_id}` frees a completed one"
                )

        plans.append(
            Plan(
                id=item_id,
                pod=_pod_of(root, item_id, work_item),
                order_path=Path(order_file),
                order_text=order.text,
                after=order.after,
                work_item=work_item,
                already_applied=already,
            )
        )
    return plans


def _reset_run_dir(root: Path, item_id: str) -> None:
    """Clear `run/<id>/` except `home/` and `persona.md`, and wipe the home's own memory."""
    run = root / "run" / item_id
    run.mkdir(parents=True, exist_ok=True)
    for entry in run.iterdir():
        if entry.name in RUN_KEEP:
            continue
        if entry.is_dir() and not entry.is_symlink():
            shutil.rmtree(entry)
        else:
            entry.unlink()

    home = run / "home"
    if home.is_dir():
        # Exactly these three: transcripts and per-project auto memory, pre-edit snapshots,
        # and typed prompts. settings.json, .credentials.json, skills/ and agents/ survive.
        for name in HOME_WIPE:
            target = home / name
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()

    store.atomic_write_json(run / "subagents.json", {})


def _worktree_of(root: Path, item_id: str) -> Path | None:
    from .config_harness import load_harness, resolve_workdir

    if item_id == PARTNER:
        return None
    harness = root / "config" / item_id / "harness.json"
    if not harness.is_file():
        return None
    config = load_harness(harness, check_cross_file=False)
    return resolve_workdir(config.workdir, root) if config.workdir else root / "wt" / item_id


def _refuse_dirty_worktree(root: Path, item_id: str) -> None:
    """A dispatch resets the worktree, so it refuses to throw away uncommitted work.

    `hx complete done` already refuses a dirty worktree; this is the other end of the same
    rule (spec 08, `handoff/orchestrator-to-build.md` answer 3). `hx bench` is the way out:
    it saves the diff as a patch first.
    """
    from .complete import worktree_is_dirty

    workdir = _worktree_of(root, item_id)
    if workdir is None or not (workdir / ".git").exists():
        return
    dirty, listing = worktree_is_dirty(workdir)
    if dirty:
        raise Refused(
            f"refuse: {item_id} has uncommitted work in {workdir}, and a dispatch resets the "
            f"worktree to the base branch:\n{listing}"
            f"Commit it, or `hx bench {item_id}`, which saves the diff as a patch first (spec 08)"
        )


def _reset_worktree(root: Path, item_id: str, env) -> None:
    """A dispatch is a fresh start, so the worktree goes back to `base_branch` (spec 17.3).

    Only with a mirror: an instance that has no product repo yet has nothing to reset to, and
    a dispatch there is still valid work.
    """
    from . import repo as repo_mod
    from .config_harness import load_harness, resolve_workdir

    if repo_mod.load_repo(root) is None:
        return
    harness = root / "config" / item_id / "harness.json"
    if not harness.is_file():
        return
    config = load_harness(harness, check_cross_file=False)
    workdir = resolve_workdir(config.workdir, root) if config.workdir else root / "wt" / item_id
    if (workdir / ".git").exists():
        repo_mod.reset_worktree(root, item_id, workdir, env=env)


def apply_plan(root: Path, plan: Plan, entries: dict[str, dict], ts: str, env) -> dict:
    """Archive, reset, render, rename, goal — for one id."""
    result = {"id": plan.id, "archived": None, "state": None, "goal": None}
    if plan.already_applied:
        result["state"] = state_of(plan.work_item)
        result["goal"] = "already"
        return result

    if plan.id != PARTNER:
        archived = archive.archive_dispatch(root, plan.id, ts)
        result["archived"] = str(archived.relative_to(root)) if archived else None
        _reset_run_dir(root, plan.id)
        _reset_worktree(root, plan.id, env)
        # A pipe started before the archive keeps writing into the moved file (spec 03, 11).
        tmux.arm_pane_log(root, plan.id, env)

    body = render(
        load_template(root),
        item_id=plan.id,
        pod=plan.pod,
        after=plan.after,
        dispatched=ts,
        order=plan.order_text.rstrip("\n"),
    )
    idle_path = work_item_path(root, plan.pod, plan.id, "idle")
    store.atomic_write_text(idle_path, body)
    if plan.work_item != idle_path and plan.work_item.exists():
        plan.work_item.unlink()

    ready = tasks_mod.is_ready(entries, plan.after)
    target_state = "working" if ready else "queued"
    final = rename_state(idle_path, target_state)
    result["state"] = target_state
    result["file"] = str(final.relative_to(root))

    if ready:
        result["goal"] = goal.send_goal(root, plan.id, env=env)
    else:
        # A queued item has no goal marker until it is promoted (spec 06).
        goal.clear_marker(root, plan.id)
    return result


def dispatch(root: Path, pairs: list[tuple[str, str]], *, env=None) -> list[dict]:
    require_partner_caller("dispatch", env)
    ts = timestamps.now()
    with store.locked(root):
        entries = tasks_mod.load_tasks(root)
        plans = _validate(root, pairs, entries, env)

        for plan in plans:
            if plan.already_applied:
                continue
            # A re-dispatch of a dependency resets its outcome, so a stale completion never
            # satisfies a newer dependent (spec 08).
            entries[plan.id] = tasks_mod.new_entry(plan.order_text, plan.after, ts)
        tasks_mod.write_tasks(root, entries)

        return [apply_plan(root, plan, entries, ts, env) for plan in plans]


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx dispatch", add_help=True)
    parser.add_argument("pairs", nargs="+", metavar="ID ORDER-FILE")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if len(args.pairs) % 2 != 0:
        raise ValidationError(
            "dispatch takes `<id> <order-file>` pairs: "
            "`hx dispatch eng-001 orders/eng-001.md eng-002 orders/eng-002.md`"
        )
    pairs = list(zip(args.pairs[0::2], args.pairs[1::2]))
    results = dispatch(root, pairs, env=env)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for result in results:
            print(f"HX-DISPATCH {result['id']} {result['state']} goal={result['goal'] or 'none'}")
    return 0
