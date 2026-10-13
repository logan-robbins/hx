"""Live compaction scenario: three lanes, 5k seams, one forced seam per lane.

TOOL_SKILL_PLAN step 6; derived from `test_small_app.py` with two changes: goals large enough
to cross a 5,000-token seam threshold, and a forced `hx seam <id>` mid-task per lane, so a
post-seam measurement exists even when no threshold seam fires (the first small-app run fired
none: its packets were 3.8 KB).

Afterwards the run is measured from the session streams through `hx metrics <id>` and one
results block is appended to docs/compaction-scenario-results.md, before any assertion, so a
failing run is recorded too. Then the pass criteria of docs/compaction-scenario.md:

- every lane through a checked receipt: `hx complete done` ran the goal's `### Checks` and
  `tasks.json` records `outcome: done`; the test re-runs each lane's checks itself as well;
- waste re-reads (`reads_of_working_set`) under 10% of post-seam tool calls, all lanes;
- zero searches before the first edit after any seam.

Skipped like the rest of tests/live (conftest.py: `HX_LIVE=1`, `claude` on PATH, a seed
token). `HX_COMPACTION_RESULTS=<path>` sends the results block elsewhere (a scratch run).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from .conftest import REPO, SRC, wait_for

RESULTS = Path(os.environ.get("HX_COMPACTION_RESULTS")
               or REPO / "docs" / "compaction-scenario-results.md")
THRESHOLD = 5000
WASTE_LIMIT = 0.10
DISPATCHED = "2026-10-12T12:00:00Z"
COMPANION_MODEL = "claude-haiku-5-5"

CONTRACT = """# Task board API contract (published interface, stub first)

- `GET /tasks` -> 200 `[{"id": 1, "title": "Write tests", "done": false, "column": "todo",
  "tags": ["docs"]}]`; `?column=todo|doing|done` and `?tag=T` filter the list
- `POST /tasks` with `{"title": str, "column"?: str, "tags"?: [str]}` -> 201 the created task
- `POST /tasks/{id}/move` with `{"column": str}` -> 200 the updated task
- `POST /tasks/{id}/done` -> 200 the updated task (column becomes `done`)
- `DELETE /tasks/{id}` -> 204
Errors are `{"error": str, "field"?: str}` with a 4xx status: 400 for a bad body, 404 for an
unknown id, 409 for moving a done task. Storage is in-memory behind the API.
"""

#: Each goal is several behaviour units, one commit each, so a lane runs for many turns and
#: its context crosses the threshold; the checks are standard-library only.
GOALS = {
    "fe-001": """## Goal
Build the frontend lane of the task-board app in this workdir, against the stub contract in
CONTRACT.md (do not wait for the real API). One behaviour unit per commit:
1. `api.js`: a small client for every contract route, with the error shape surfaced.
2. `board.js`: render tasks into todo / doing / done columns; move and done buttons.
3. `filters.js`: column and tag filters that rebuild the query string.
4. `index.html` + `app.js`: wire the three modules together; a form that creates a task.
5. `check.sh`: greps the modules for every contract path, the move and done handlers, the
   filter query parameters, and the error rendering; exits non-zero on any miss.
Run `bash check.sh` after every unit and commit it. When all five are committed and the
check passes, run `hx complete done` as your last action.

## Definition of done
- [ ] all five units committed, `bash check.sh` passes, the workdir is clean

### Checks

```bash
bash check.sh
```
""",
    "api-001": """## Goal
Build the API lane of the task-board app in this workdir, implementing CONTRACT.md with the
Python standard library only (`http.server`, `json`, `unittest`). One behaviour unit per
commit, each with its tests:
1. `errors.py`: the error shape and the 400 / 404 / 409 cases as exceptions.
2. `validation.py`: request-body validation for create and move (title, column, tags).
3. `api.py`: the in-memory store and every contract route, including filters and delete.
4. `test_api.py`: at least eight `unittest` tests through a real server on a free port,
   covering each route, each filter and each error status.
Run `python3 -m unittest discover -q` after every unit and commit it. When all four are
committed and the tests pass, run `hx complete done` as your last action.

## Definition of done
- [ ] all four units committed, the unittest suite passes, the workdir is clean

### Checks

```bash
python3 -m unittest discover -q
```
""",
    "be-001": """## Goal
Build the backend/storage lane of the task-board app in this workdir, standard library only.
One behaviour unit per commit, each with its tests:
1. `domain.py`: Task and the rules (non-empty title, known column, a done task cannot move,
   tags are lower-case and unique).
2. `store.py`: TaskStore (add, get, list with column/tag filters, move, mark_done, delete).
3. `persistence.py`: JSON file save/load with an atomic write and a schema version.
4. `test_domain.py`, `test_store.py`, `test_persistence.py`: at least ten `unittest` tests in
   all, including a save/load round trip and every rule above.
Run `python3 -m unittest discover -q` after every unit and commit it. When all four are
committed and the tests pass, run `hx complete done` as your last action.

## Definition of done
- [ ] all four units committed, the unittest suite passes, the workdir is clean

### Checks

```bash
python3 -m unittest discover -q
```
""",
}

CHECKS = {
    "fe-001": ["bash", "check.sh"],
    "api-001": [sys.executable, "-m", "unittest", "discover", "-q"],
    "be-001": [sys.executable, "-m", "unittest", "discover", "-q"],
}

#: The file whose appearance means the lane is mid-task: the forced seam goes in then.
FIRST_FILE = {"fe-001": "api.js", "api-001": "errors.py", "be-001": "domain.py"}


def _git(workdir: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(workdir), *args], check=True,
                          capture_output=True, text=True).stdout


def _lane_repo(live_root, item_id: str) -> Path:
    workdir = live_root / "work" / item_id
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "CONTRACT.md").write_text(CONTRACT)
    # `hx complete done` needs a clean tree after the checks run; bytecode would dirty it.
    (workdir / ".gitignore").write_text("__pycache__/\n*.pyc\n")
    _git(workdir, "init", "-q")
    _git(workdir, "add", "-A")
    _git(workdir, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm",
         "contract: published interface (stub first)")
    return workdir


def _pod(item_id: str) -> str:
    return "frontend" if item_id.startswith("fe") else "engineers"


def _companion_on_haiku(live_root, item_id: str) -> None:
    path = live_root / "config" / item_id / "harness.json"
    harness = json.loads(path.read_text())
    harness["companion"].update({"model": COMPANION_MODEL, "effort": "low", "cache_ttl": "1h"})
    path.write_text(json.dumps(harness, indent=2) + "\n")


def force_seam(live_env, item_id: str) -> str:
    """`hx seam <id>` until it is taken. `waiting` (a menu, or the Companion behind) and
    `deferred` (background tasks) are what the next boundary would retry, so this retries."""
    env = {**live_env, "PYTHONPATH": str(SRC)}

    def taken():
        result = subprocess.run([sys.executable, "-m", "hx", "seam", item_id], env=env,
                                capture_output=True, text=True, timeout=120)
        return result.stdout.strip() if f"HX-SEAM {item_id} taken" in result.stdout else None

    return wait_for(taken, what=f"`hx seam {item_id}` to be taken", limit=900.0, interval=5.0)


def outcome_of(live_root, item_id: str):
    from hx.tasks import load_tasks

    return (load_tasks(live_root).get(item_id) or {}).get("outcome")


def measure(live_root, item_id: str) -> dict:
    """One lane's numbers, from `hx metrics <id>` (null fields stay null: never estimated)."""
    from hx.metrics import per_id

    seams = per_id(live_root, item_id)["seams"]
    windows = [seam["next_10_turns"] or {} for seam in seams]
    measured = [w for w in windows if w.get("tool_calls") is not None]
    return {
        "seams": len(seams),
        "packet_bytes": [seam.get("packet_bytes") for seam in seams],
        "tool_calls": sum(w["tool_calls"] for w in measured),
        "waste": sum(w["reads_of_working_set"] for w in measured),
        "unmeasured_windows": len(windows) - len(measured),
        "searches_before_first_edit": sum(seam["searches_before_first_edit"] for seam in seams),
        "modules_read_before_first_write": sum(seam["modules_read_before_first_write"]
                                               for seam in seams),
        "task_reprints": sum(seam["task_reprints"] for seam in seams),
        "receipt": outcome_of(live_root, item_id),
    }


def results_block(live_root, rows: dict, *, checks_ok: dict) -> tuple[str, dict]:
    from hx import timestamps

    calls = sum(row["tool_calls"] for row in rows.values())
    waste = sum(row["waste"] for row in rows.values())
    searches = sum(row["searches_before_first_edit"] for row in rows.values())
    receipts = sum(1 for row in rows.values() if row["receipt"] == "done")
    unmeasured = sum(row["unmeasured_windows"] for row in rows.values())
    ratio = waste / calls if calls else None
    verdict = {
        "receipts": receipts == len(rows) and all(checks_ok.values()),
        "waste": ratio is not None and ratio < WASTE_LIMIT and unmeasured == 0,
        "searches": searches == 0,
        "seams": all(row["seams"] >= 1 for row in rows.values()),
    }
    lines = [
        "",
        f"## Compaction scenario run {timestamps.now()}",
        "",
        f"Runner: `tests/live/test_compaction_scenario.py`; three lanes, {THRESHOLD:,}-token seam",
        f"threshold, companions on {COMPANION_MODEL} (low), one forced `hx seam` per lane mid-task.",
        f"Instance: `{live_root}`.",
        "",
        "| lane | seams | packet bytes | post-seam tool calls | waste re-reads | "
        "searches before first edit | modules read before first write | task reprints | "
        "receipt | checks |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for item_id, row in rows.items():
        packets = ", ".join("null" if b is None else f"{b:,}" for b in row["packet_bytes"]) or "none"
        lines.append(
            f"| {item_id} | {row['seams']} | {packets} | {row['tool_calls']} | "
            f"{row['waste']} | {row['searches_before_first_edit']} | "
            f"{row['modules_read_before_first_write']} | {row['task_reprints']} | "
            f"{row['receipt'] or 'none'} | {'pass' if checks_ok[item_id] else 'FAIL'} |")
    shown = "n/a" if ratio is None else f"{ratio:.1%}"
    lines += [
        "",
        f"- Waste re-reads: {waste}/{calls} post-seam tool calls = {shown} (criterion under "
        f"{WASTE_LIMIT:.0%}); windows without a parseable transcript: {unmeasured}.",
        f"- Searches before first edit after a seam: {searches} (criterion 0).",
        f"- Lanes through checked receipts: {receipts}/{len(rows)}; checks re-run by the test: "
        f"{sum(checks_ok.values())}/{len(rows)}.",
        "- Verdict: " + ("pass" if all(verdict.values()) else
                         "fail (" + ", ".join(k for k, ok in verdict.items() if not ok) + ")") + ".",
        "",
    ]
    return "\n".join(lines), verdict


def test_compaction_scenario(live_root, live_env, worker):
    from hx import tasks as tasks_mod
    from hx.lifecycle import launch

    models = {name: {"window": window, "seam_threshold": THRESHOLD} for name, window in (
        ("claude-sonnet-5-5", 1000000), ("claude-opus-5-5", 1000000), ("claude-haiku-5-5", 200000))}
    (live_root / "config" / "models.json").write_text(json.dumps(models, indent=2) + "\n")

    workdirs, entries = {}, {}
    for item_id, goal in GOALS.items():
        workdir = _lane_repo(live_root, item_id)
        worker(item_id, workdir=workdir, pod=_pod(item_id),
               role="frontend-engineer" if item_id.startswith("fe") else "backend-engineer")
        _companion_on_haiku(live_root, item_id)
        path = live_root / "pods" / _pod(item_id) / f"{item_id}-working.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\nid: {item_id}\npod: {_pod(item_id)}\noutcome:\n"
                        f"dispatched: {DISPATCHED}\n---\n\n{goal}\n## Tasks\n- [ ] build the lane\n")
        # `hx complete done` runs the `### Checks` of the goal recorded at dispatch.
        entries[item_id] = tasks_mod.new_entry(goal, DISPATCHED)
        workdirs[item_id] = workdir
    tasks_mod.write_tasks(live_root, entries)

    for item_id in GOALS:
        assert launch(live_root, item_id, companion=True, env=live_env)["goal"] == "pasted"

    # Mid-task: once a lane's first unit has a file, force its seam.
    for item_id, name in FIRST_FILE.items():
        wait_for(lambda p=workdirs[item_id] / name: p.is_file(),
                 what=f"{item_id} to start ({name})", limit=900.0)
        force_seam(live_env, item_id)

    for item_id in GOALS:
        wait_for(lambda i=item_id: outcome_of(live_root, i) is not None,
                 what=f"{item_id} to complete through `hx complete`", limit=2400.0, interval=5.0)

    checks_ok = {}
    for item_id, command in CHECKS.items():
        result = subprocess.run(command, cwd=workdirs[item_id], capture_output=True, text=True,
                                timeout=300)
        checks_ok[item_id] = result.returncode == 0

    rows = {item_id: measure(live_root, item_id) for item_id in GOALS}
    block, verdict = results_block(live_root, rows, checks_ok=checks_ok)
    with RESULTS.open("a") as handle:
        handle.write(block)

    assert verdict["seams"], f"a lane has no seam record: {rows}"
    assert verdict["receipts"], f"not every lane is through a checked receipt: {rows} {checks_ok}"
    assert verdict["waste"], f"waste re-reads at or over {WASTE_LIMIT:.0%}, or unmeasured: {rows}"
    assert verdict["searches"], f"searches before the first edit after a seam: {rows}"
