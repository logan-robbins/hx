"""Every transition in spec 06, and every M1 pass criterion in spec 13.

These run against a real tmux server on a private socket, with the fake `claude` in the pane,
so the goal pointer really is pasted through a tmux buffer and really does land in the pane's
stdin. Nothing here touches the user's tmux, home, or `~/.claude`.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import pytest

from .conftest import manifest, wait_for

#: `run/` and `logs/` move constantly (markers, pane log); a "changes nothing" assertion is
#: about the control plane: tasks.json, the work items, the archives, the orders.
CONTROL_PLANE_ONLY = ("run", "logs", "state", "archive/.keep")


def control_manifest(root):
    return manifest(root, skip=CONTROL_PLANE_ONLY)


def tasks_of(root):
    path = root / "tasks.json"
    return json.loads(path.read_text()) if path.is_file() else {}


def item_state(root, item_id):
    from hx.workitems import find_work_item

    path = find_work_item(root, item_id)
    return None if path is None else path.name.rsplit("-", 1)[1].removesuffix(".md")


def pasted(root, item_id):
    log = root / "run" / item_id / "fake-input.log"
    return log.read_text() if log.is_file() else ""


def hold_pane(tmux_server, item_id):
    """Leave the fake pane mid-turn — no idle prompt — the way a real turn looks."""
    subprocess.run(
        [*tmux_server, "send-keys", "-t", f"={item_id}:main", "/fake-hold", "Enter"], check=True
    )
    from hx.goal import capture_pane, pane_is_idle

    env = {"HX_TMUX": " ".join(tmux_server)}
    wait_for(
        lambda: not pane_is_idle(capture_pane(item_id, env) or ""),
        what=f"{item_id}'s pane to be mid-turn",
    )


def wait_for_goal(root, item_id):
    wait_for(
        lambda: "/goal The order for" in pasted(root, item_id),
        what=f"the goal pointer to reach {item_id}'s pane",
    )
    return pasted(root, item_id)


# --- launch ---------------------------------------------------------------------------------


def test_launch_creates_the_idle_work_item_and_the_session(instance, hx, launched):
    assert item_state(instance, "eng-001") is None
    launched("eng-001")
    assert item_state(instance, "eng-001") == "idle"
    assert (instance / "run" / "eng-001" / "home" / "settings.json").is_file()


def test_launch_is_idempotent(instance, hx, launched):
    launched("eng-001")
    first = json.loads((instance / "run" / "eng-001" / "fake-argv.json").read_text())
    result = hx("launch", "eng-001")
    assert result.returncode == 0
    assert "existing" in result.stdout
    second = json.loads((instance / "run" / "eng-001" / "fake-argv.json").read_text())
    assert second["pid"] == first["pid"], "an idempotent launch does not restart the agent"


def test_launch_arms_the_pane_log(instance, launched):
    """spec 03, 11: the UI's fallback for a dead session."""
    launched("eng-001")
    log = instance / "logs" / "eng-001" / "eng-001-pane.log"
    wait_for(lambda: log.is_file() and log.stat().st_size > 0, what="the pane log to fill")
    assert "hx-fake-idle>" in log.read_text()


def test_launch_of_a_working_item_sends_the_goal(instance, hx, launched, orders):
    launched("eng-001")
    orders("eng-001")
    assert hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance).returncode == 0
    wait_for_goal(instance, "eng-001")
    (instance / "run" / "eng-001" / "fake-input.log").write_text("")
    assert hx("launch", "eng-001").returncode == 0
    assert "/goal The order for" in wait_for_goal(instance, "eng-001")


# --- dispatch -------------------------------------------------------------------------------


def test_idle_to_working_when_after_is_empty(instance, hx, launched, orders):
    launched("eng-001")
    orders("eng-001", order="Rewrite the importer.")
    result = hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance)
    assert result.returncode == 0, result.stderr
    assert item_state(instance, "eng-001") == "working"

    entry = tasks_of(instance)["eng-001"]
    assert entry["after"] == [] and entry["outcome"] is None and entry["completed"] is None
    assert "Rewrite the importer." in entry["order"]

    body = (instance / "pods" / "engineers" / "eng-001-working.md").read_text()
    assert "Rewrite the importer." in body, "the order is copied verbatim (spec 06)"
    assert "### Checks" in body
    assert "{{" not in body, "every template token is rendered"
    assert (instance / "run" / "eng-001" / "goal").is_file()

    pointer = wait_for_goal(instance, "eng-001")
    assert str((instance / "pods" / "engineers" / "eng-001-working.md").resolve()) in pointer
    assert "HX-COMPLETE eng-001 <outcome>" in pointer


def test_idle_to_queued_when_an_after_entry_is_unmet(instance, hx, launched, orders, agent):
    agent("eng-002")
    launched("eng-001", "eng-002")
    orders("eng-001")
    orders("eng-002", after=["eng-001"])
    result = hx("dispatch", "eng-002", "orders/eng-002.md", cwd=instance)
    assert result.returncode == 0, result.stderr
    assert item_state(instance, "eng-002") == "queued"
    assert not (instance / "run" / "eng-002" / "goal").exists(), "a queued item has no goal marker"
    assert "/goal" not in pasted(instance, "eng-002")


def test_dispatching_2_of_20_ids_changes_exactly_2(instance, hx, launched, orders, tmux_server):
    """spec 13 M1: exactly 2 tasks, 2 work items, and 2 archived log/state dirs."""
    from hx.lifecycle import ensure_work_item

    ids = [f"eng-{n:03d}" for n in range(1, 21)]
    for item_id in ids:
        config = instance / "config" / item_id
        config.mkdir(parents=True, exist_ok=True)
        (config / "harness.json").write_text(
            json.dumps({"id": item_id, "pod": "engineers", "role": "engineer",
                        "model": "claude-opus-5", "effort": "high",
                        "workdir": f"wt/{item_id}", "branch": f"agent/{item_id}"})
        )
        (config / "AGENTS.md").write_text("persona\n\n## UPDATES BELOW ONLY\n")
        (instance / "wt" / item_id).mkdir(parents=True, exist_ok=True)
        ensure_work_item(instance, item_id)
        # Prior-dispatch logs and state, so the archive step has something to move.
        for name in ("logs", "state"):
            directory = instance / name / item_id
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "old.txt").write_text("previous dispatch\n")

    launched("eng-003", "eng-007")
    orders("eng-003")
    orders("eng-007")

    before = control_manifest(instance)
    result = hx("dispatch", "eng-003", "orders/eng-003.md", "eng-007", "orders/eng-007.md", cwd=instance)
    assert result.returncode == 0, result.stderr

    assert sorted(tasks_of(instance)) == ["eng-003", "eng-007"]
    assert sorted(
        p.name for p in (instance / "pods" / "engineers").glob("*-working.md")
    ) == ["eng-003-working.md", "eng-007-working.md"]
    assert sorted(p.name for p in (instance / "archive").iterdir()) == ["eng-003", "eng-007"]
    for item_id in ("eng-003", "eng-007"):
        archived = list((instance / "archive" / item_id).iterdir())
        assert len(archived) == 1
        assert (archived[0] / "logs" / "old.txt").is_file()
        assert (archived[0] / "state" / "old.txt").is_file()

    after = control_manifest(instance)
    touched = {path for path in set(before) | set(after) if before.get(path) != after.get(path)}
    untouched_ids = set(ids) - {"eng-003", "eng-007"}
    for path in touched:
        assert not any(other in path for other in untouched_ids), f"{path} changed"


def test_dispatch_refuses_a_non_idle_item(instance, hx, launched, orders):
    launched("eng-001")
    orders("eng-001")
    assert hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance).returncode == 0
    orders("eng-001", order="A different order entirely.")
    result = hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance)
    assert result.returncode == 1
    assert "refuse" in result.stderr and "working" in result.stderr


def test_dispatch_refuses_an_order_without_checks(instance, hx, launched):
    launched("eng-001")
    path = instance / "orders" / "eng-001.md"
    path.parent.mkdir(exist_ok=True)
    path.write_text("## Order\n\nDo it.\n\n## Definition of done\n\n- [ ] done\n")
    result = hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance)
    assert result.returncode == 2
    assert "### Checks" in result.stderr
    assert not (instance / "tasks.json").exists(), "a refused dispatch writes nothing"


def test_dispatch_refuses_without_a_live_session(instance, hx, orders):
    from hx.lifecycle import ensure_work_item

    ensure_work_item(instance, "eng-001")
    orders("eng-001")
    result = hx("dispatch", "eng-001", "orders/eng-001.md", cwd=instance)
    assert result.returncode == 1
    assert "no live tmux session" in result.stderr


def test_an_interrupted_dispatch_recovers_on_re_run(instance, hx, launched, orders, agent, tmux_server):
    """spec 08: "Re-running the same `hx dispatch` completes an interrupted one"."""
    from hx.dispatch import dispatch

    agent("eng-002")
    launched("eng-001", "eng-002")
    orders("eng-001")
    orders("eng-002")

    env = {"HARNESS_ROOT": str(instance), "HARNESS_ID": "partner"}
    import hx.dispatch as dispatch_mod

    original = dispatch_mod.apply_plan
    applied = []

    def stop_after_first(root, plan, entries, ts, env_):
        if applied:
            raise KeyboardInterrupt("interrupted between ids")
        applied.append(plan.id)
        return original(root, plan, entries, ts, env_)

    dispatch_mod.apply_plan = stop_after_first
    try:
        with pytest.raises(KeyboardInterrupt):
            dispatch(
                instance,
                [("eng-001", str(instance / "orders/eng-001.md")),
                 ("eng-002", str(instance / "orders/eng-002.md"))],
                env={**env, "HX_TMUX": " ".join(tmux_server)},
            )
    finally:
        dispatch_mod.apply_plan = original

    assert item_state(instance, "eng-001") == "working"
    assert item_state(instance, "eng-002") == "idle", "the second id never got applied"

    result = hx("dispatch", "eng-001", "orders/eng-001.md", "eng-002", "orders/eng-002.md", cwd=instance)
    assert result.returncode == 0, result.stderr
    assert item_state(instance, "eng-001") == "working"
    assert item_state(instance, "eng-002") == "working"


# --- complete -------------------------------------------------------------------------------


def dispatch_working(instance, hx, orders, item_id="eng-001", **order_kwargs):
    orders(item_id, **order_kwargs)
    result = hx("dispatch", item_id, f"orders/{item_id}.md", cwd=instance)
    assert result.returncode == 0, result.stderr
    wait_for_goal(instance, item_id)
    return instance


def test_working_to_complete_done(instance, hx, launched, orders):
    launched("eng-001")
    dispatch_working(instance, hx, orders, checks="test -f README.md")

    result = hx("complete", "done", harness_id="eng-001")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().split("\n")[-1] == "HX-COMPLETE eng-001 done"

    assert item_state(instance, "eng-001") == "complete"
    entry = tasks_of(instance)["eng-001"]
    assert entry["outcome"] == "done" and entry["completed"]
    body = (instance / "pods" / "engineers" / "eng-001-complete.md").read_text()
    assert "outcome: done" in body
    assert "pending companion" in body, "the Digest placeholder until the Companion lands (M5)"
    assert not (instance / "run" / "eng-001" / "goal").exists()


@pytest.mark.parametrize("outcome", ["blocked", "decision", "exhausted"])
def test_the_other_outcomes_run_no_checks(instance, hx, launched, orders, outcome):
    """spec 02: `blocked`, `decision` and `exhausted` run no checks."""
    launched("eng-001")
    dispatch_working(instance, hx, orders, checks="exit 7")
    result = hx("complete", outcome, harness_id="eng-001")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().split("\n")[-1] == f"HX-COMPLETE eng-001 {outcome}"
    assert tasks_of(instance)["eng-001"]["outcome"] == outcome


def test_complete_refuses_a_failing_check_and_changes_nothing(instance, hx, launched, orders):
    launched("eng-001")
    dispatch_working(instance, hx, orders, checks="echo 'the report is missing' >&2\nexit 3")
    before = control_manifest(instance)

    result = hx("complete", "done", harness_id="eng-001")
    assert result.returncode == 1
    assert result.stdout.startswith("HX-CHECK-FAILED eng-001")
    assert "the report is missing" in result.stdout
    assert "HX-COMPLETE" not in result.stdout
    assert control_manifest(instance) == before, "a refused completion changes nothing"
    assert item_state(instance, "eng-001") == "working"
    assert (instance / "run" / "eng-001" / "goal").is_file(), "the goal stays active"


def test_complete_refuses_a_dirty_worktree_and_changes_nothing(instance, hx, launched, orders):
    launched("eng-001")
    dispatch_working(instance, hx, orders)
    (instance / "wt" / "eng-001" / "uncommitted.py").write_text("half a change\n")
    before = control_manifest(instance)

    result = hx("complete", "done", harness_id="eng-001")
    assert result.returncode == 1
    assert result.stdout.startswith("HX-CHECK-FAILED eng-001")
    assert "dirty" in result.stdout and "uncommitted.py" in result.stdout
    assert control_manifest(instance) == before
    assert item_state(instance, "eng-001") == "working"


def test_complete_refuses_an_open_subagent_stream_and_changes_nothing(instance, hx, launched, orders):
    launched("eng-001")
    dispatch_working(instance, hx, orders)
    logs = instance / "logs" / "eng-001"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "eng-001-s001-open.jsonl").write_text("")
    before = control_manifest(instance)

    result = hx("complete", "done", harness_id="eng-001")
    assert result.returncode == 1
    assert result.stdout.startswith("HX-CHECK-FAILED eng-001")
    assert "s001" in result.stdout
    assert control_manifest(instance) == before
    assert item_state(instance, "eng-001") == "working"


def test_an_open_stream_refuses_every_outcome(instance, hx, launched, orders):
    """spec 08: `hx complete` requires zero `-open` subagent streams, whatever the outcome."""
    launched("eng-001")
    dispatch_working(instance, hx, orders)
    logs = instance / "logs" / "eng-001"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "eng-001-s002-open.jsonl").write_text("")
    result = hx("complete", "blocked", harness_id="eng-001")
    assert result.returncode == 1 and "HX-CHECK-FAILED" in result.stdout


def test_complete_is_the_callers_own_item(instance, hx, launched, orders):
    launched("eng-001")
    dispatch_working(instance, hx, orders)
    result = hx("complete", "done", harness_id=None)
    assert result.returncode == 2
    assert "HARNESS_ID" in result.stderr


def test_complete_wakes_the_partner(instance, hx, launched, orders, tmp_path):
    """spec 08, 12 step 4: `<id> complete: <outcome>; hx read <id>`."""
    import socket
    import threading

    launched("eng-001")
    dispatch_working(instance, hx, orders)

    # AF_UNIX paths are capped near 104 bytes, and pytest's tmp_path is long.
    address = str(Path(tempfile.mkdtemp(prefix="hxw")) / "p.sock")
    received: list[bytes] = []
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(address)
    server.listen(1)

    def accept_once():
        connection, _ = server.accept()
        with connection:
            received.append(connection.recv(65536))

    thread = threading.Thread(target=accept_once, daemon=True)
    thread.start()

    socket_file = instance / "run" / "partner" / "socket.json"
    socket_file.parent.mkdir(parents=True, exist_ok=True)
    socket_file.write_text(json.dumps({"socket": address, "token": "tok-123"}))

    assert hx("complete", "done", harness_id="eng-001").returncode == 0
    thread.join(timeout=10)
    server.close()

    lines = [json.loads(line) for line in received[0].decode().strip().split("\n")]
    assert lines[0] == {"type": "auth", "token": "tok-123"}
    assert lines[1]["type"] == "user"
    assert lines[1]["message"] == {
        "role": "user",
        "content": "eng-001 complete: done; hx read eng-001",
    }


# --- queued → working, inside `hx complete done` of the last dependency ------------------------


def test_a_queued_item_is_promoted_by_its_last_dependency(instance, hx, launched, orders, agent):
    agent("eng-002")
    launched("eng-001", "eng-002")
    orders("eng-001")
    orders("eng-002", after=["eng-001"])
    assert hx("dispatch", "eng-001", "orders/eng-001.md", "eng-002", "orders/eng-002.md",
              cwd=instance).returncode == 0
    assert item_state(instance, "eng-001") == "working"
    assert item_state(instance, "eng-002") == "queued"
    wait_for_goal(instance, "eng-001")

    result = hx("complete", "done", harness_id="eng-001")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HX-PROMOTED eng-002 working" in result.stdout
    assert result.stdout.strip().split("\n")[-1] == "HX-COMPLETE eng-001 done"

    assert item_state(instance, "eng-002") == "working"
    assert (instance / "run" / "eng-002" / "goal").is_file()
    assert "/goal The order for eng-002" in wait_for_goal(instance, "eng-002")


def test_a_queued_item_with_two_dependencies_waits_for_both(instance, hx, launched, orders, agent):
    agent("eng-002", git=True)
    agent("eng-003")
    launched("eng-001", "eng-002", "eng-003")
    orders("eng-001")
    orders("eng-002")
    orders("eng-003", after=["eng-001", "eng-002"])
    assert hx("dispatch", "eng-001", "orders/eng-001.md", "eng-002", "orders/eng-002.md",
              "eng-003", "orders/eng-003.md", cwd=instance).returncode == 0
    assert item_state(instance, "eng-003") == "queued"

    assert hx("complete", "done", harness_id="eng-001").returncode == 0
    assert item_state(instance, "eng-003") == "queued", "one dependency is still open"

    result = hx("complete", "done", harness_id="eng-002")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "HX-PROMOTED eng-003 working" in result.stdout
    assert item_state(instance, "eng-003") == "working"


def test_a_blocked_dependency_does_not_promote(instance, hx, launched, orders, agent):
    agent("eng-002")
    launched("eng-001", "eng-002")
    orders("eng-001")
    orders("eng-002", after=["eng-001"])
    assert hx("dispatch", "eng-001", "orders/eng-001.md", "eng-002", "orders/eng-002.md",
              cwd=instance).returncode == 0
    result = hx("complete", "blocked", harness_id="eng-001")
    assert result.returncode == 0
    assert "HX-PROMOTED" not in result.stdout
    assert item_state(instance, "eng-002") == "queued"


# --- resume ----------------------------------------------------------------------------------


def write_addendum(instance, item_id, text="Use a flat list, not a map."):
    path = instance / "orders" / f"{item_id}.addendum.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text + "\n")
    return path


def test_resume_keeps_logs_state_and_tasks_and_appends_the_addendum(instance, hx, launched, orders):
    """spec 13 M1: keeps logs, state and `## Tasks`, appends the addendum, sends the goal."""
    launched("eng-001")
    dispatch_working(instance, hx, orders)

    # What the agent and the Companion built up while it worked.
    work_item = instance / "pods" / "engineers" / "eng-001-working.md"
    body = work_item.read_text().replace("- [ ] …", "- [x] read the importer\n- [ ] rewrite it")
    work_item.write_text(body)
    logs = instance / "logs" / "eng-001"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "eng-001-main.jsonl").write_text('{"seq": 1, "event": "post_tool"}\n')
    state = instance / "state" / "eng-001"
    state.mkdir(parents=True, exist_ok=True)
    (state / "eng-001-main.json").write_text('{"seq": 1}\n')

    assert hx("complete", "decision", harness_id="eng-001").returncode == 0
    archives_before = sorted(p.name for p in (instance / "archive" / "eng-001").glob("*"))
    write_addendum(instance, "eng-001")
    (instance / "run" / "eng-001" / "fake-input.log").write_text("")

    result = hx("resume", "eng-001", "orders/eng-001.addendum.md", cwd=instance)
    assert result.returncode == 0, result.stderr
    assert item_state(instance, "eng-001") == "working"

    resumed = (instance / "pods" / "engineers" / "eng-001-working.md").read_text()
    assert "### Order addendum" in resumed
    assert "Use a flat list, not a map." in resumed
    assert "- [x] read the importer" in resumed, "## Tasks survives a resume"
    assert "- [ ] rewrite it" in resumed
    assert "outcome:" in resumed and "outcome: decision" not in resumed

    assert (logs / "eng-001-main.jsonl").read_text() == '{"seq": 1, "event": "post_tool"}\n'
    assert (state / "eng-001-main.json").read_text() == '{"seq": 1}\n'
    assert sorted(p.name for p in (instance / "archive" / "eng-001").glob("*")) == archives_before, (
        "resume archives nothing; only dispatch does (spec 07.2)"
    )

    entry = tasks_of(instance)["eng-001"]
    assert entry["outcome"] is None and entry["completed"] is None
    assert len(entry["addenda"]) == 1
    assert entry["addenda"][0]["text"] == "Use a flat list, not a map."

    assert "/goal The order for eng-001" in wait_for_goal(instance, "eng-001")


def test_the_addendum_lands_beneath_the_order_not_at_the_end(instance, hx, launched, orders):
    launched("eng-001")
    dispatch_working(instance, hx, orders)
    assert hx("complete", "blocked", harness_id="eng-001").returncode == 0
    write_addendum(instance, "eng-001")
    assert hx("resume", "eng-001", "orders/eng-001.addendum.md", cwd=instance).returncode == 0

    body = (instance / "pods" / "engineers" / "eng-001-working.md").read_text()
    addendum_at = body.index("### Order addendum")
    assert body.index("## Order") < addendum_at < body.index("## Definition of done")
    assert addendum_at < body.index("## Tasks")


@pytest.mark.parametrize("outcome", ["done", "exhausted"])
def test_resume_refuses_an_outcome_that_is_not_paused(instance, hx, launched, orders, outcome):
    launched("eng-001")
    dispatch_working(instance, hx, orders)
    assert hx("complete", outcome, harness_id="eng-001").returncode == 0
    write_addendum(instance, "eng-001")
    result = hx("resume", "eng-001", "orders/eng-001.addendum.md", cwd=instance)
    assert result.returncode == 1
    assert "refuse" in result.stderr and outcome in result.stderr


def test_resume_refuses_a_working_item(instance, hx, launched, orders):
    launched("eng-001")
    dispatch_working(instance, hx, orders)
    write_addendum(instance, "eng-001")
    result = hx("resume", "eng-001", "orders/eng-001.addendum.md", cwd=instance)
    assert result.returncode == 1 and "not `complete`" in result.stderr


# --- bench -----------------------------------------------------------------------------------


def test_bench_archives_the_body_before_it_resets(instance, hx, launched, orders):
    launched("eng-001")
    dispatch_working(instance, hx, orders, order="A very specific order.")
    assert hx("complete", "done", harness_id="eng-001").returncode == 0
    before = (instance / "pods" / "engineers" / "eng-001-complete.md").read_text()
    tasks_before = tasks_of(instance)

    result = hx("bench", "eng-001")
    assert result.returncode == 0, result.stderr
    assert item_state(instance, "eng-001") == "idle"

    archived = list((instance / "pods" / "engineers" / "archive").glob("eng-001-*.md"))
    assert len(archived) == 1
    assert archived[0].read_text() == before, "the body is archived exactly as it stood"
    assert "A very specific order." in archived[0].read_text()

    reset = (instance / "pods" / "engineers" / "eng-001-idle.md").read_text()
    assert "A very specific order." not in reset
    assert "## Tasks" in reset and "{{" not in reset
    assert tasks_of(instance) == tasks_before, "bench does not touch tasks.json (spec 08)"
    assert tasks_of(instance)["eng-001"]["outcome"] == "done"


def test_a_benched_id_shows_as_idle_with_its_last_outcome(
    instance, hx, launched, orders, tmux_server
):
    """spec 08, by design: the outcome is history, the state is the board."""
    from hx.board import collect

    launched("eng-001")
    dispatch_working(instance, hx, orders)
    assert hx("complete", "done", harness_id="eng-001").returncode == 0
    assert hx("bench", "eng-001").returncode == 0

    board = collect(instance, env={"HX_TMUX": " ".join(tmux_server)})
    item = {i["id"]: i for i in board["items"]}["eng-001"]
    assert item["state"] == "idle"
    assert item["outcome"] == "done"
    assert [e for e in board["errors"] if "eng-001" in e] == []


def test_bench_refuses_an_item_that_is_not_complete(instance, hx, launched, orders):
    launched("eng-001")
    dispatch_working(instance, hx, orders)
    result = hx("bench", "eng-001")
    assert result.returncode == 1 and "refuse" in result.stderr


def test_a_benched_id_can_be_dispatched_again(instance, hx, launched, orders):
    launched("eng-001")
    dispatch_working(instance, hx, orders)
    assert hx("complete", "done", harness_id="eng-001").returncode == 0
    assert hx("bench", "eng-001").returncode == 0
    dispatch_working(instance, hx, orders, order="The next order.")
    assert item_state(instance, "eng-001") == "working"
    assert "The next order." in tasks_of(instance)["eng-001"]["order"]


# --- the Partner ------------------------------------------------------------------------------


def test_partner_dispatch_archives_nothing_and_leaves_goal_pending(
    instance, hx, launched, orders, tmux_server
):
    """spec 06, 08: the Partner's session is continuous and its own pane is mid-turn."""
    launched("partner")
    logs = instance / "logs" / "partner"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "partner-main.jsonl").write_text('{"seq": 1}\n')
    orders("partner", order="Decompose what the human asked for.",
           checks="hx board --require-done eng-001")

    # The Partner dispatches itself from its own Bash tool, so its pane is mid-turn.
    hold_pane(tmux_server, "partner")

    result = hx("dispatch", "partner", "orders/partner.md", cwd=instance)
    assert result.returncode == 0, result.stderr
    assert item_state(instance, "partner") == "working"
    assert (instance / "run" / "partner" / "goal-pending").is_file()
    assert (instance / "run" / "partner" / "goal").is_file(), (
        "the marker is written in both cases, so the `working` invariant holds mid-turn (spec 08)"
    )
    assert not (instance / "archive" / "partner").exists(), "no archive for the Partner"
    assert (instance / "logs" / "partner").is_dir()
    assert (logs / "partner-main.jsonl").read_text() == '{"seq": 1}\n', "no wipe for the Partner"


def test_partner_resume_archives_nothing_and_leaves_goal_pending(
    instance, hx, launched, orders, tmux_server
):
    launched("partner")
    orders("partner", checks="true")
    assert hx("dispatch", "partner", "orders/partner.md", cwd=instance).returncode == 0
    wait_for_goal(instance, "partner")
    assert hx("complete", "decision", harness_id="partner").returncode == 0

    write_addendum(instance, "partner", "The human chose the flat list.")
    hold_pane(tmux_server, "partner")
    result = hx("resume", "partner", "orders/partner.addendum.md", cwd=instance)
    assert result.returncode == 0, result.stderr
    assert item_state(instance, "partner") == "working"
    assert (instance / "run" / "partner" / "goal-pending").is_file()
    assert not (instance / "archive" / "partner").exists()


def test_the_partner_is_not_woken_by_its_own_completion(instance, hx, launched, orders):
    launched("partner")
    orders("partner", checks="true")
    assert hx("dispatch", "partner", "orders/partner.md", cwd=instance).returncode == 0
    wait_for_goal(instance, "partner")
    result = hx("complete", "done", harness_id="partner")
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip().split("\n")[-1] == "HX-COMPLETE partner done"


def test_partner_checks_run_in_harness_root(instance, hx, launched, orders):
    """spec 06: the Partner has no worktree, so its checks run in HARNESS_ROOT."""
    launched("partner")
    orders("partner", checks="test -f config/models.json")
    assert hx("dispatch", "partner", "orders/partner.md", cwd=instance).returncode == 0
    wait_for_goal(instance, "partner")
    assert hx("complete", "done", harness_id="partner").returncode == 0


