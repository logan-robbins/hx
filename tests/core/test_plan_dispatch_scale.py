"""The executor at the operating point: 62 ready packets, two roots, one box, real tmux.

Every launch spawns tests/fakeclaude in its own native session on a private tmux server.
The measured numbers print with `-s`; the assertions hold the plan's targets.
"""
from __future__ import annotations

import json
import math
import subprocess
import sys
import time

import pytest

from hx import appmap, host_pool, plan_dispatch, planning
from hx.continuity_store import ContinuityStore
from hx.install import install_skeleton
from hx.loop_jobs import Jobs
from .conftest import FAKE_CLAUDE, clean_env
from .test_appmap import git
from .test_planning import unit

UNITS = 31
SETTINGS = {"auto": True, "sessions": 60, "quota": 32, "writers": 32, "per_tick": 16,
            "restore": {"concurrency": 16, "per_second": 1000}}


def root(tmp_path, name):
    repo = tmp_path / name / "repo"
    repo.mkdir(parents=True)
    git(repo, "init", "-q")
    (repo / "README.md").write_text("A fleet root.\n")
    info = appmap.initialize(repo)
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "Initial fleet root")
    store = ContinuityStore(tmp_path / name / "ledger")
    appmap.import_baseline(store, repo)
    install_skeleton(store.root)
    config = store.root / "config"
    (config / "claude.json").write_text(json.dumps({"bin": str(FAKE_CLAUDE)}))
    (config / "hx.json").write_text(json.dumps({"dispatch": SETTINGS}))
    token = store.root / "seed" / "token"
    token.parent.mkdir(parents=True, exist_ok=True)
    token.write_text("fixture-key")
    token.chmod(0o600)
    tasks = []
    for index in range(1, UNITS + 1):
        worker, tree = f"eng-{index:03d}", tmp_path / name / "trees" / f"u{index:03d}"
        git(repo, "worktree", "add", "-qb", f"u{index:03d}", str(tree))
        (config / worker).mkdir()
        (config / worker / "harness.json").write_text(json.dumps({"id": worker, "pod": "eng", "role": "backend-engineer",
                                                                  "model": "claude-opus-5-5", "effort": "high", "workdir": str(tree)}))
        tasks.append(unit(tree, f"{name}-u{index:03d}"))
    planning.apply(store, {"plan_id": name, "expected_revision": 0, "repository": info["repo_id"],
                           "goal": "Run every packet of the fleet at once.", "constraints": ["Each packet stays in its worktree."],
                           "tasks": tasks})
    return store


def active(store):
    return store.db.execute("SELECT count(*) FROM runs WHERE ended_at IS NULL").fetchone()[0]


def sessions(tmux):
    listed = subprocess.run([*tmux, "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True, check=False)
    return [name for name in listed.stdout.split() if name.startswith("hx-")]


def p95(values):
    ordered = sorted(values)
    return ordered[math.ceil(0.95 * len(ordered)) - 1]


def test_sixty_two_packets_on_two_roots_start_within_a_tick_and_never_exceed_a_budget(tmp_path, tmux_server, monkeypatch):
    monkeypatch.setattr(host_pool, "directory", lambda: tmp_path / "host-pool")
    env = clean_env(HX_TMUX=" ".join(tmux_server), HOME=str(tmp_path / "home"), HX_PYTHON=sys.executable)
    roots = {}
    try:
        for name in ("alpha", "beta"):
            roots[name] = root(tmp_path, name)
        measure(roots, tmux_server, env)
    finally:
        for store in roots.values():
            store.close()


def measure(roots, tmux_server, env):
    jobs = {name: Jobs.inline() for name in roots}
    passes, launched, wall, held = {name: 0 for name in roots}, {}, [], []

    def tick(name):
        store = roots[name]
        started, begun = time.perf_counter(), time.time()
        report = plan_dispatch.tick(store, env=env, jobs=jobs[name]) or {"launched": [], "waiting": []}
        wall.append(time.perf_counter() - started)
        # Every unit is ready before the first pass: a unit's wait is the passes before its launch.
        # Its spawn is timed from the start of that pass, a span short enough for file mtimes.
        for item in report["launched"]:
            launched[name, item["task_id"]] = (passes[name], begun)
        passes[name] += 1
        held.extend(item["task_id"] for item in report["waiting"] if item["reason"] == "box sessions budget is spent")
        # Budgets hold after every pass: box, root quota, in-flight restore launches.
        assert sum(active(other) for other in roots.values()) <= plan_dispatch.settings(store.root)["sessions"]
        assert active(store) <= SETTINGS["quota"]
        assert host_pool.status()["launching"] <= SETTINGS["restore"]["concurrency"]
        return report

    for _ in range(2):
        for name in roots:
            tick(name)
    assert len(launched) == SETTINGS["sessions"] and len(held) == 2 * UNITS - SETTINGS["sessions"], held
    # Two packets wait on the box; the operator raises it and the next pass starts them.
    for store in roots.values():
        (store.root / "config" / "hx.json").write_text(json.dumps({"dispatch": {**SETTINGS, "sessions": 2 * UNITS}}))
    for name in roots:
        tick(name)
    assert len(launched) == 2 * UNITS
    for _ in range(2):  # Idle passes launch nothing twice.
        for name in roots:
            assert not tick(name)["launched"]
    markers = {(name, row["task_id"]): store.root / "run" / row["worker_id"] / "fake-ready"
               for name, store in roots.items() for row in store.db.execute("SELECT task_id,worker_id FROM dispatch_launches")}
    spawned, deadline = {}, time.time() + 120
    while len(spawned) < len(markers) and time.time() < deadline:
        spawned.update({unit: path.stat().st_mtime - launched[unit][1] for unit, path in markers.items() if unit not in spawned and path.is_file()})
        time.sleep(0.05)
    keys = [row[0] for store in roots.values() for row in store.db.execute("SELECT launch_key FROM dispatch_launches")]
    native = [row[0] for store in roots.values() for row in store.db.execute("SELECT count(*) FROM native_launches GROUP BY run_id")]
    live = sessions(tmux_server)
    numbers = {"units": len(launched), "roots": len(roots), "passes": len(wall), "box_held": len(held),
               "pass_seconds_p50": round(sorted(wall)[len(wall) // 2], 2), "pass_seconds_max": round(max(wall), 2),
               "ready_to_running_passes_p95": p95([index for index, _ in launched.values()]),
               "pass_to_spawned_seconds_p50": round(sorted(spawned.values())[len(spawned) // 2], 2),
               "pass_to_spawned_seconds_p95": round(p95(list(spawned.values())), 2),
               "live_panes": len(live), "double_launches": len(keys) - len(set(keys)) + sum(count - 1 for count in native)}
    print("\nplan executor scale:", json.dumps(numbers))
    assert numbers["ready_to_running_passes_p95"] < 2 and numbers["double_launches"] == 0
    assert len(set(keys)) == len(native) == len(live) == len(spawned) == 2 * UNITS
