"""The plan executor: idempotent dispatch onto role-matched seats under every budget."""
from __future__ import annotations

import json
import sys

import pytest

from hx import application_loop, host_pool, native_controller, native_launch, plan_dispatch, planning, unit_execution as units
from hx.errors import ValidationError
from .test_appmap import git, mapped  # noqa: F401 — fixture chain
from .test_map_updates import active  # noqa: F401 — fixture chain
from .test_planning import bind, dependency, plan, unit
from .test_unit_execution import commit, finish, fleet  # noqa: F401 — fixture


@pytest.fixture(autouse=True)
def host(tmp_path, monkeypatch):
    """Each test gets its own box ledger; the operator's real one is never touched."""
    monkeypatch.setattr(host_pool, "directory", lambda: tmp_path / "host-pool")


@pytest.fixture
def launches(monkeypatch):
    """Native spawn is recorded, not run; the scale test runs it for real."""
    calls = []

    def advance(ledger, worker, run_id, request_id, *, env=None, submit=True, ensure_service=True):
        calls.append((worker, run_id, request_id, submit))
        return {"status": "starting"}

    monkeypatch.setattr(native_controller, "advance", advance)
    monkeypatch.setattr(application_loop, "ensure", lambda *a, **k: None)
    return calls


def seats(store, **roles):
    for worker, role in roles.items():
        folder = store.root / "config" / worker.replace("_", "-")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "harness.json").write_text(json.dumps({"id": worker.replace("_", "-"), "pod": worker.split("_")[0], "role": role,
                                                         "model": "claude-opus-5-5", "effort": "high", "workdir": str(store.root)}))


def configure(store, delivery=None, **dispatch):
    (store.root / "config").mkdir(parents=True, exist_ok=True)
    (store.root / "config" / "hx.json").write_text(json.dumps({"dispatch": dispatch}))
    if delivery is not None:
        (store.root / "config" / "delivery.json").write_text(json.dumps(delivery))


def trees(repo, tmp_path, *names):
    paths = []
    for name in names:
        path = tmp_path / "trees" / name
        git(repo, "worktree", "add", "-qb", "tree-" + name, str(path))
        paths.append(path)
    return paths


def role(task, name):
    task["check_policy"]["role"] = name
    return task


def rows(store):
    return {row["task_id"]: dict(row) for row in store.db.execute("SELECT * FROM dispatch_launches")}


def test_a_pass_admits_downstream_first_onto_role_matched_seats_and_never_launches_a_key_twice(fleet, tmp_path, launches):
    store, repo = fleet[:2]
    seats(store, eng_001="backend-engineer", eng_002="backend-engineer", qa_001="qa-engineer", qa_002="qa-engineer")
    configure(store, per_tick=2, downstream_roles=["qa-engineer"])
    first, second, third, fourth = trees(repo, tmp_path, "schema", "report", "extra", "verify")
    planning.apply(store, plan(fleet, [unit(first, "schema"), unit(second, "report", prerequisites=[dependency("schema")]),
                                       unit(third, "extra"), role(unit(fourth, "verify"), "qa-engineer")]))
    producer = units.assign(store, "schema", 1, "eng-001")["run_id"]
    bind(store, producer)
    finish(store, producer)
    preview = plan_dispatch.run(store, "continuation", dry_run=True)
    # The QA unit ranks first; then the deeper consumer; its reviewer qa-001 is never its seat.
    assert [(item["task_id"], item["worker_id"]) for item in preview["selected"]] == [("verify", "qa-002"), ("report", "eng-001")]
    assert preview["waiting"] == [{"task_id": "extra", "plan_id": "continuation", "reason": "per-tick dispatch budget is spent"}]
    assert launches == [] and rows(store) == {}
    result = plan_dispatch.run(store, "continuation")
    assert [item["task_id"] for item in result["launched"]] == ["verify", "report"]
    assert [call[0] for call in launches] == ["qa-002", "eng-001"] and not any(call[3] for call in launches)
    assert {row["status"] for row in rows(store).values()} == {"launched"}
    assert [item["task_id"] for item in plan_dispatch.run(store, "continuation")["launched"]] == ["extra"]
    assert plan_dispatch.run(store, "continuation")["launched"] == []
    assert len(launches) == len({call[2] for call in launches}) == 3
    # A stopped run at the same head is never redispatched; a new head is a new key.
    extra = rows(store)["extra"]
    units.stop(store, extra["run_id"])
    again = plan_dispatch.run(store, "continuation")
    assert again["launched"] == [] and again["waiting"][0]["reason"] == "launch key already launched at this head"
    (third / "notes.md").write_text("Moved the head.\n")
    commit(third)
    assert [item["task_id"] for item in plan_dispatch.run(store, "continuation")["launched"]] == ["extra"]
    assert len(launches) == 4 and len({call[2] for call in launches}) == 4


def test_quota_and_writer_budgets_exempt_read_only_units(fleet, tmp_path, launches):
    store, repo = fleet[:2]
    seats(store, eng_001="backend-engineer", eng_002="backend-engineer", eng_003="backend-engineer", eng_004="backend-engineer")
    configure(store, per_tick=8, quota=2, writers=1)
    a, b, c, d = trees(repo, tmp_path, "a", "b", "c", "d")
    planning.apply(store, plan(fleet, [unit(a, "a"), unit(b, "b"), unit(c, "c", paths=[]), unit(d, "d", paths=[])]))
    result = plan_dispatch.run(store, "continuation")
    assert [item["task_id"] for item in result["launched"]] == ["a", "c"]
    assert {item["task_id"]: item["reason"] for item in result["waiting"]} == {
        "b": "writer budget is spent", "d": "root session quota is spent"}


def test_box_sessions_restore_tokens_and_the_growth_breaker_bound_every_root(fleet, tmp_path, launches, monkeypatch):
    store, repo = fleet[:2]
    seats(store, eng_001="backend-engineer", eng_002="backend-engineer", eng_003="backend-engineer")
    a, b, c = trees(repo, tmp_path, "a", "b", "c")
    planning.apply(store, plan(fleet, [unit(a, "a"), unit(b, "b"), unit(c, "c")]))
    configure(store, sessions=3, restore={"concurrency": 8, "per_second": 1000})
    # Another root on the box already holds two sessions.
    host_pool.admit(tmp_path / "other-root", [], census=2, descendants=0, limits=plan_dispatch.settings(store.root))
    result = plan_dispatch.run(store, "continuation")
    assert [item["task_id"] for item in result["launched"]] == ["a"]
    assert result["budget"]["box"] == "box sessions budget is spent"
    host_pool.admit(tmp_path / "other-root", [], census=0, descendants=0, limits=plan_dispatch.settings(store.root))
    configure(store, restore={"concurrency": 1, "per_second": 0.001})
    result = plan_dispatch.run(store, "continuation")
    assert [item["task_id"] for item in result["launched"]] == ["b"]
    assert result["budget"]["box"] == "restore concurrency budget is spent"
    result = plan_dispatch.run(store, "continuation")
    assert result["launched"] == [] and result["budget"]["box"] == "restore rate budget is spent"
    configure(store, restore={"concurrency": 8, "per_second": 1000}, growth={"max_descendants": 100})
    monkeypatch.setattr(plan_dispatch, "_descendants", lambda identities: 100)
    result = plan_dispatch.run(store, "continuation")
    assert result["launched"] == [] and result["budget"]["box"] == "process growth breaker is open"
    assert host_pool.status()["launching"] == 0


def test_precheck_and_stale_base_defer_without_failure_and_backpressure_holds_engineering(fleet, tmp_path, launches):
    store, repo = fleet[:2]
    seats(store, eng_001="backend-engineer", eng_002="backend-engineer", eng_003="backend-engineer", qa_002="qa-engineer")
    a, b, c, d = trees(repo, tmp_path, "a", "b", "c", "d")
    precheck = [sys.executable, "-c", "import json,sys; sys.exit(3 if json.load(sys.stdin)['task_id'] == 'b' else 0)"]
    configure(store, per_tick=8, retry_seconds=3600, precheck={"backend-engineer": precheck})
    planning.apply(store, plan(fleet, [unit(a, "a"), unit(b, "b")]))
    result = plan_dispatch.run(store, "continuation")
    assert [item["task_id"] for item in result["launched"]] == ["a"]
    assert result["deferred"][0]["task_id"] == "b" and result["deferred"][0]["reason"].startswith("precheck exited 3")
    assert store.db.execute("SELECT count(*) FROM runs WHERE task_id='b'").fetchone()[0] == 0
    assert plan_dispatch.run(store, "continuation")["waiting"][0]["reason"].startswith("deferred: precheck exited 3")
    # The base branch moves three commits past the worktrees.
    base = git(repo, "symbolic-ref", "--short", "HEAD")
    for index in range(3):
        (repo / f"base-{index}.md").write_text("Base moved.\n")
        commit(repo)
    configure(store, per_tick=8, base_ref=base, max_behind=2)
    body = plan(fleet, [unit(a, "a"), unit(b, "b"), unit(c, "c")])
    body.update(plan_id="later", tasks=[unit(c, "c")])
    planning.apply(store, body)
    stale = plan_dispatch.run(store, "later")
    assert stale["launched"] == [] and stale["deferred"][0]["reason"] == f"stale base: 3 commits behind {base} (max 2)"
    configure(store, per_tick=8, downstream_roles=["qa-engineer"], delivery={"max_batch": 1})
    with store.transaction() as tx:
        for index in range(3):
            tx.db.execute("INSERT INTO delivery_batches VALUES(?,?,?,'pending','{}')", (f"batch-{index}", fleet[2]["repo_id"], "head"))
    body.update(plan_id="lane", tasks=[unit(d, "engineering"), role(unit(d, "qa", paths=[]), "qa-engineer")])
    body["tasks"][0]["workdir"] = str(c)
    planning.apply(store, body)
    held = plan_dispatch.run(store, "lane")
    assert [item["task_id"] for item in held["launched"]] == ["qa"]
    assert held["waiting"] == [{"task_id": "engineering", "plan_id": "lane",
                                "reason": "merge backpressure: lane has more than 2 landed batches awaiting validation"}]


def test_a_stopped_pass_resumes_admitted_launches_and_never_replays_an_uncertain_one(fleet, tmp_path, launches, monkeypatch):
    store, repo = fleet[:2]
    seats(store, eng_001="backend-engineer", eng_002="backend-engineer")
    a, b = trees(repo, tmp_path, "a", "b")
    planning.apply(store, plan(fleet, [unit(a, "a"), unit(b, "b")]))
    gone = {"platform": sys.platform, "boot": "a-previous-boot", "pid": 2}
    for task, worker in (("a", "eng-001"), ("b", "eng-002")):
        run = units.assign(store, task, 1, worker)["run_id"]
        with store.transaction() as tx:
            tx.db.execute("INSERT INTO dispatch_launches VALUES(?,?,?,?,?,?,?,'assigned',?)",
                          (task + "-key", "continuation", task, 1, "head", worker, run, json.dumps({"owner": gone})))
    real = native_launch._row
    monkeypatch.setattr(native_launch, "_row", lambda ledger, run_id: {"status": "start_uncertain"}
                        if run_id == rows(store)["b"]["run_id"] else real(ledger, run_id))
    from hx.loop_jobs import Jobs
    assert plan_dispatch.tick(store, jobs=Jobs.inline()) == {"status": "resumed", "resumed": ["a"]}
    assert [call[2] for call in launches] == ["a-key"]
    assert rows(store)["a"]["status"] == "launched"
    assert rows(store)["b"]["status"] == "launch_failed"
    assert json.loads(rows(store)["b"]["payload"])["reason"] == "native launch is start_uncertain"
    assert plan_dispatch.tick(store, jobs=Jobs.inline()) is None and len(launches) == 1


def test_the_loop_dispatches_when_auto_and_the_operator_pauses_it(fleet, tmp_path, launches, run_hx):
    store, repo = fleet[:2]
    seats(store, eng_001="backend-engineer")
    configure(store, auto=True)
    (a,) = trees(repo, tmp_path, "a")
    planning.apply(store, plan(fleet, [unit(a, "a")]))
    root = str(store.root)
    paused = run_hx("fleet", "pause", "--root", root)
    assert paused.returncode == 0 and json.loads(paused.stdout)["dispatch"] == "paused"
    assert "dispatch" not in application_loop.tick(store)
    assert json.loads(run_hx("plan", "run", "continuation", "--root", root).stdout)["status"] == "paused"
    preview = run_hx("plan", "run", "continuation", "--dry-run", "--root", root)
    assert preview.returncode == 0 and json.loads(preview.stdout)["selected"][0]["worker_id"] == "eng-001"
    assert json.loads(run_hx("fleet", "resume", "--root", root).stdout)["dispatch"] == "running"
    result = application_loop.tick(store)
    assert result["dispatch"]["launched"][0]["task_id"] == "a" and len(launches) == 1


@pytest.mark.parametrize("dispatch,message", [({"unknown": 1}, "unknown fields"), ({"per_tick": 0}, "per_tick"),
                                              ({"restore": {"rate": 1}}, "restore has unknown"), ({"precheck": {"qa": "x"}}, "argv")])
def test_dispatch_settings_are_validated(fleet, dispatch, message):
    configure(fleet[0], **dispatch)
    with pytest.raises(ValidationError, match=message):
        plan_dispatch.settings(fleet[0].root)


def test_a_concurrent_pass_that_reserved_a_key_first_keeps_it(fleet, tmp_path, launches, monkeypatch):
    store, repo = fleet[:2]
    seats(store, eng_001="backend-engineer", eng_002="backend-engineer")
    (a,) = trees(repo, tmp_path, "a")
    planning.apply(store, plan(fleet, [unit(a, "a")]))
    admit = host_pool.admit

    def raced(root, keys, **kwargs):
        # Another process reserves the same key between this pass's read and its reservation.
        with store.transaction() as tx:
            tx.db.execute("INSERT INTO dispatch_launches VALUES(?,'continuation','a',1,'head','eng-002',NULL,'reserved','{}')", (keys[0],))
        return admit(root, keys, **kwargs)

    monkeypatch.setattr(host_pool, "admit", raced)
    result = plan_dispatch.run(store, "continuation")
    assert result["launched"] == [] and result["waiting"][-1]["reason"] == "launch key already reserved at this head"
    assert launches == [] and host_pool.status()["launching"] == 0
    assert store.db.execute("SELECT count(*) FROM runs WHERE task_id='a'").fetchone()[0] == 0
