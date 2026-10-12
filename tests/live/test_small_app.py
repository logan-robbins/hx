"""Live small-app run: three lanes stubbed off one contract, 5k seams.

A task-board app split frontend / API / backend. The contract is published
first (interface-first); each lane builds against the stub in parallel.
Afterwards the run itself is evaluated: files landed, checks pass in each
workdir, no lane waited on another, and post-seam behaviour is measured
from the session streams (see docs/compaction-scenario.md).
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .conftest import pane_text, wait_for, work_item

CONTRACT = """# Task board API contract (published interface, stub first)

- `GET /tasks` -> 200 `[{"id": 1, "title": "Write tests", "done": false}]`
- `POST /tasks` with `{"title": str}` -> 201 the created task
- `POST /tasks/{id}/done` -> 200 the updated task
Errors are `{"error": str}` with a 4xx status. Storage is in-memory.
"""

GOALS = {
    "fe-001": """## Goal
Build the frontend lane of the task-board app in this workdir, against the
stub contract in CONTRACT.md (do not wait for the real API):
- `index.html` + `app.js` that list tasks and mark one done, fetching the contract paths
- `check.sh` that greps app.js for `/tasks` and the done handler, exits 0 on success
Run `bash check.sh`, then git add and commit. Then stop; do not run hx complete.

## Definition of done
- [ ] index.html, app.js, check.sh exist and `bash check.sh` passes
""",
    "api-001": """## Goal
Build the API lane of the task-board app in this workdir, implementing CONTRACT.md
with the Python standard library only:
- `api.py` with an in-memory task store and the three contract routes
- `test_api.py` with at least three tests covering list, create, and done
Run `python3 -m pytest test_api.py -q`, then git add and commit. Then stop; do not run hx complete.

## Definition of done
- [ ] api.py and test_api.py exist and the tests pass
""",
    "be-001": """## Goal
Build the backend/storage lane of the task-board app in this workdir:
- `store.py` with a TaskStore class (add, list, mark_done) and JSON file persistence
- `test_store.py` with at least three tests including a save/load round trip
Run `python3 -m pytest test_store.py -q`, then git add and commit. Then stop; do not run hx complete.

## Definition of done
- [ ] store.py and test_store.py exist and the tests pass
""",
}


def _git(workdir: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(workdir), *args], check=True, capture_output=True)


def _lane_repo(live_root, item_id: str) -> Path:
    workdir = live_root / "work" / item_id
    workdir.mkdir(parents=True, exist_ok=True)
    (workdir / "CONTRACT.md").write_text(CONTRACT)
    _git(workdir, "init", "-q")
    _git(workdir, "add", "-A")
    _git(workdir, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "contract: published interface (stub first)")
    return workdir


def test_three_lane_task_board(live_root, live_env, worker):
    from hx.lifecycle import launch

    # 5k seams for this run: rewrite the instance model thresholds low.
    models = {
        "claude-sonnet-5-5": {"window": 1000000, "seam_threshold": 5000},
        "claude-opus-5-5": {"window": 1000000, "seam_threshold": 5000},
        "claude-haiku-5-5": {"window": 200000, "seam_threshold": 5000},
    }
    (live_root / "config" / "models.json").write_text(json.dumps(models, indent=2) + "\n")

    launched = {}
    for item_id, goal in GOALS.items():
        workdir = _lane_repo(live_root, item_id)
        _, _ = worker(item_id, workdir=workdir,
                      pod="frontend" if item_id.startswith("fe") else "engineers",
                      role="frontend-engineer" if item_id.startswith("fe") else "backend-engineer")
        # conftest.work_item has a latent NameError; write the item directly.
        pod = "frontend" if item_id.startswith("fe") else "engineers"
        path = live_root / "pods" / pod / f"{item_id}-working.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            f"---\nid: {item_id}\npod: {'frontend' if item_id.startswith('fe') else 'engineers'}\n"
            f"outcome:\ndispatched: 2026-10-12T12:00:00Z\n---\n\n{goal}\n## Tasks\n- [ ] build the lane\n")
        launched[item_id] = workdir

    for item_id in GOALS:
        assert launch(live_root, item_id, companion=True, env=live_env)["goal"] == "pasted"

    expected = {
        "fe-001": ["index.html", "app.js", "check.sh"],
        "api-001": ["api.py", "test_api.py"],
        "be-001": ["store.py", "test_store.py"],
    }
    for item_id, files in expected.items():
        workdir = launched[item_id]
        for name in files:
            wait_for(lambda p=workdir / name: p.is_file(),
                     what=f"{item_id} to create {name}", limit=600.0)

    # The test, not the agent, verifies each lane's checks.
    import sys
    for item_id, check in (("fe-001", ["bash", "check.sh"]),
                           ("api-001", [sys.executable, "-m", "pytest", "test_api.py", "-q"]),
                           ("be-001", [sys.executable, "-m", "pytest", "test_store.py", "-q"])):
        result = subprocess.run(check, cwd=launched[item_id], capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, f"{item_id} checks failed:\n{result.stdout}\n{result.stderr}"

    # Each lane committed its own work (frequent sign-off at lane granularity).
    for item_id, workdir in launched.items():
        log = subprocess.run(["git", "-C", str(workdir), "log", "--oneline"],
                             capture_output=True, text=True, check=True).stdout
        assert len(log.strip().splitlines()) >= 2, f"{item_id} did not commit: {log}"
        assert "contract: published interface" in log
