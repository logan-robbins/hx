"""`hx dispatch <id> <goal-file> [...]` — the only way a task starts (spec 06, 08).

The goal is a file at any path the Partner likes, copied verbatim into the Work Item and
recorded in `tasks.json`, and deleted once the dispatch succeeds: no goal text ever crosses a
command line, and the text ends up in exactly two places. The write order is spec 08's, so
re-running an interrupted dispatch completes it:

    tasks, then per-id archive, reset, render, rename, goal, delete the goal file.

The Partner is never dispatched: it has no work item and its goal comes from the human in
chat (spec 12, spec 14 D25). hx does not inspect or reset git — a dirty workdir is the
agent's business.
"""

from __future__ import annotations

import argparse
import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from . import archive, goal, store, tasks as tasks_mod, timestamps, tmux
from .caller import require_partner_caller
from .config_harness import flavor_of, load_harness
from .errors import NotFound, Refused, ValidationError
from .ids import PARTNER
from .goals import parse_goal, parse_goal_text
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
#: Pi keeps the conversation in `home/sessions`. A new goal is a new session.
PI_HOME_WIPE = ("sessions",)
#: Grok keeps the conversation in `home/sessions/`. A new goal is a new session.
GROK_HOME_WIPE = ("sessions",)
#: Muse Code keeps sessions, traces, and the skill index under `home/data/`.
#: A new goal is a new session; the config in `home/muse/` survives.
META_HOME_WIPE = ("data",)
#: Codex keeps the conversation in `home/sessions/`. A new goal is a new session.
CODEX_HOME_WIPE = ("sessions",)
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
    goal_path: Path
    goal_text: str
    work_item: Path
    already_applied: bool = False


def _pod_of(root: Path, item_id: str, work_item: Path) -> str:
    harness = root / "config" / item_id / "harness.json"
    if harness.is_file():
        return load_harness(harness, check_cross_file=False).pod
    return work_item.parent.name


def _validate(root: Path, pairs: list[tuple[str, str]], existing: dict[str, dict], env) -> list[Plan]:
    """Every goal is validated before anything is written (spec 08 pseudo-code)."""
    plans: list[Plan] = []
    seen: set[str] = set()
    for item_id, goal_file in pairs:
        if item_id == PARTNER:
            raise Refused(
                "refuse: the Partner has no work item and is never dispatched; the human "
                "gives it its goal in chat (spec 12, spec 14 D25)"
            )
        if item_id in seen:
            raise Refused(f"refuse: {item_id} named twice in one dispatch")
        seen.add(item_id)

        recorded = (existing.get(item_id) or {}).get("goal")
        if not Path(goal_file).exists() and recorded:
            # The goal file was consumed by the dispatch this one is completing; the text is
            # in `tasks.json` and hx re-renders from there (spec 08).
            goal = parse_goal_text(recorded, f"tasks.json[{item_id}].goal")
        else:
            goal = parse_goal(Path(goal_file))
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

        state = state_of(work_item)
        already = False
        if state != "idle":
            # An interrupted dispatch re-run: this id was already applied with this very
            # goal, so completing the run means skipping it, not refusing it (spec 08,
            # "Re-running the same `hx dispatch` completes an interrupted one").
            record = existing.get(item_id) or {}
            if state == "working" and record.get("goal") == goal.text:
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
                goal_path=Path(goal_file),
                goal_text=goal.text,
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
        flavor = flavor_of(root, item_id)
        wipe = HOME_WIPE + (PI_HOME_WIPE if flavor == "pi" else ())
        wipe = wipe + (GROK_HOME_WIPE if flavor == "grok" else ())
        wipe = wipe + (META_HOME_WIPE if flavor == "meta" else ())
        wipe = wipe + (CODEX_HOME_WIPE if flavor == "codex" else ())
        for name in wipe:
            target = home / name
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            elif target.exists():
                target.unlink()

    store.atomic_write_json(run / "subagents.json", {})


def apply_plan(root: Path, plan: Plan, entries: dict[str, dict], ts: str, env) -> dict:
    """Archive, reset, render, rename, goal — for one id."""
    result = {"id": plan.id, "archived": None, "state": None, "goal": None}
    if plan.already_applied:
        result["state"] = state_of(plan.work_item)
        result["goal"] = "already"
        return result

    archived = archive.archive_dispatch(root, plan.id, ts)
    result["archived"] = str(archived.relative_to(root)) if archived else None
    _reset_run_dir(root, plan.id)
    # A pipe started before the archive keeps writing into the moved file (spec 03, 11).
    tmux.arm_pane_log(root, plan.id, env)

    body = render(
        load_template(root),
        item_id=plan.id,
        pod=plan.pod,
        dispatched=ts,
        goal=plan.goal_text.rstrip("\n"),
    )
    idle_path = work_item_path(root, plan.pod, plan.id, "idle")
    store.atomic_write_text(idle_path, body)
    if plan.work_item != idle_path and plan.work_item.exists():
        plan.work_item.unlink()

    final = rename_state(idle_path, "working")
    result["state"] = "working"
    result["file"] = str(final.relative_to(root))
    result["goal"] = goal.send_goal(root, plan.id, env=env)

    # The goal file is consumed: the text now lives in `tasks.json` and the Work Item, and
    # nowhere else (spec 06, 08).
    plan.goal_path.unlink(missing_ok=True)
    result["consumed"] = str(plan.goal_path)
    return result


def dispatch(root: Path, pairs: list[tuple[str, str]], *, env=None) -> list[dict]:
    require_partner_caller("dispatch", env)
    ts = timestamps.now()
    entries = tasks_mod.load_tasks(root)
    plans = _validate(root, pairs, entries, env)

    for plan in plans:
        if plan.already_applied:
            continue
        entries[plan.id] = tasks_mod.new_entry(plan.goal_text, ts)
    tasks_mod.write_tasks(root, entries)

    return [apply_plan(root, plan, entries, ts, env) for plan in plans]


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx dispatch", add_help=True)
    parser.add_argument("pairs", nargs="+", metavar="ID GOAL-FILE")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if len(args.pairs) % 2 != 0:
        raise ValidationError(
            "dispatch takes `<id> <goal-file>` pairs: "
            "`hx dispatch eng-001 /tmp/eng-001.md eng-002 /tmp/eng-002.md`"
        )
    pairs = list(zip(args.pairs[0::2], args.pairs[1::2]))
    results = dispatch(root, pairs, env=env)

    if args.json:
        print(json.dumps(results, indent=2))
    else:
        for result in results:
            print(f"HX-DISPATCH {result['id']} {result['state']} goal={result['goal'] or 'none'}")
    return 0
