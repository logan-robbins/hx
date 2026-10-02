from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from io import StringIO
from pathlib import Path

import pytest

from hx import hook_contract, hooks, native_launch, native_sources, observer
from hx.continuity_store import Conflict, ContinuityStore, digest
from hx.errors import ValidationError
from .test_appmap import mapped
from .test_context_packets import assignment
from .test_map_updates import active
from .test_native_launch import configured, prepare
from .test_observer import capture, source_state
from .test_unit_execution import fleet


@pytest.fixture
def launched(configured):
    store, _, run = configured
    launch = prepare(configured)
    capsule = Path(launch["payload"]["capsule"])
    home = capsule / "run/eng-001/home"
    home.mkdir(parents=True)
    env = native_launch.environment(store.root, launch, env={})
    args = hook_contract.installation_args(capsule, "eng-001", "claude", env)
    return store, run, launch, home, args


def report(launched, path, *, event="stop", **extra):
    store, run, launch, _, _ = launched
    return native_sources.observe(store, run_id=run, launch_id=launch["launch_id"], worker_id="eng-001",
        adapter="claude", event=event, payload={"session_id": "S", "transcript_path": str(path), **extra})


def hook(launched, path, *, event="stop", **extra):
    store, _, _, _, args = launched
    return hooks.main(["--root", str(store.root), "--id", "eng-001", *args, event], env={},
        stdin=StringIO(json.dumps({"session_id": "S", "transcript_path": str(path), **extra})))


def record(native="A", parent=None, *, text="Use the current receipt.", **extra):
    return {"type": "assistant", "uuid": native, "parentUuid": parent, "sessionId": "S",
        "message": {"role": "assistant", "content": [{"type": "thinking", "thinking": "PRIVATE"},
            {"type": "text", "text": text}], "usage": {"input_tokens": 7}}, **extra}


def append(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write((json.dumps(body) + "\n").encode())


def test_hook_registers_without_reading_and_observer_captures_delayed_partial_output(launched, monkeypatch):
    store, run, _, home, _ = launched
    path = home / "projects/project/S.jsonl"
    real_open = os.open
    def reject_source_open(candidate, *args, **kwargs):
        assert str(candidate) != str(path), "hook must not open the transcript"
        return real_open(candidate, *args, **kwargs)
    with monkeypatch.context() as m:
        m.setattr(os, "open", reject_source_open)
        m.setattr(observer, "drain", lambda *a, **kw: pytest.fail("hook must not drain transcript"))
        assert hook(launched, path, last_assistant_message="Hook final text.") == 0
        assert hook(launched, path, last_assistant_message="Hook final text.") == 0
    rows = store.db.execute("SELECT source_id FROM capture_sources WHERE run_id=?", (run,)).fetchall()
    assert len(rows) == 1
    source = rows[0][0]
    assert observer.source_snapshot(store, run)["sources"][0]["lag"]
    assert observer.drain(store, source)["pending_tail"]
    assert source_state(store, source)["payload"]["gaps"] == []
    path.parent.mkdir(parents=True)
    body = json.dumps(record()).encode()
    path.write_bytes(body[:-1])
    assert observer.drain(store, source)["offset"] == 0
    with path.open("ab") as handle:
        handle.write(body[-1:] + b"\n")
    with ContinuityStore(store.root) as reopened:
        result = observer.drain(reopened, source)
        assert result["events"] == 1 and not result["pending_tail"] and not result["gaps"]
    assert not observer.source_snapshot(store, run)["sources"][0]["lag"]
    assert "PRIVATE" not in "\n".join(store.db.iterdump())
    assert source_state(store, source)["payload"]["latest_usage"] == {"input_tokens": 7}


def test_child_source_has_own_stream_and_tool_identity(launched):
    store, run, launch, home, _ = launched
    main = home / "projects/project/S.jsonl"
    child = home / "projects/project/S/subagents/agent-child.jsonl"
    sources = report(launched, main, event="subagent-stop", agent_id="child", agent_transcript_path=str(child))
    assert len(sources) == 2
    body = {"type": "user", "uuid": "U", "parentUuid": None, "sessionId": "S", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "C", "content": "failed", "is_error": True}]}}
    append(main, body)
    append(child, {**body, "agentId": "child", "isSidechain": True})
    for source in sources:
        assert observer.drain(store, source)["events"] == 1
    results = [dict(row) for row in store.db.execute("""SELECT e.stream_id,e.payload FROM events e
        JOIN event_origins o USING(event_id) WHERE e.run_id=? AND e.kind='tool_result'""", (run,))]
    assert len(results) == 2
    child_result = next(item for item in results if item["stream_id"] == "child:" + digest([launch["launch_id"], "child"]))
    captured = json.loads(child_result["payload"])
    assert captured["observation"]["data"]["agent_id"] == "child"
    assert captured["logical_id"] == digest(("native", "S", "tool_result", "tool:" + digest(["child", "C"])))
    assert len({json.loads(item["payload"])["logical_id"] for item in results}) == 2


def test_main_source_excludes_sidechains_and_stops_before_changed_branch(launched):
    store, _, _, home, _ = launched
    path = home / "S.jsonl"
    source = report(launched, path)[0]
    append(path, record("root"))
    append(path, record("inactive", "root", isSidechain=True, text="Inactive branch."))
    append(path, {"type": "progress", "uuid": "progress", "parentUuid": "root", "sessionId": "S"})
    append(path, {"type": "attachment", "uuid": "attachment", "parentUuid": "progress", "sessionId": "S",
                  "attachment": {"type": "file", "content": "PRIVATE ATTACHMENT"}})
    append(path, record("meta", "attachment", isMeta=True, text="Internal metadata."))
    append(path, record("current", "meta"))
    result = observer.drain(store, source)
    assert result["events"] == 3 and result["gaps"] == []
    attachment = json.loads(store.db.execute("SELECT payload FROM events WHERE kind='request'").fetchone()[0])
    assert attachment["observation"]["data"] == {"attachment_type": "file", "content_available": False}
    append(path, record("fork", "root", text="Unselected fork."))
    rejected = observer.drain(store, source)
    assert rejected["events"] == 0 and rejected["offset"] == result["offset"] and rejected["gaps"]
    assert source_state(store, source)["payload"]["last_native_id"] == "current"
    assert all(text not in "\n".join(store.db.iterdump()) for text in ["Inactive branch.", "Internal metadata.", "Unselected fork.", "PRIVATE ATTACHMENT"])


@pytest.mark.parametrize("change", ["outside", "file-symlink", "directory-symlink", "home-replaced", "fifo"])
def test_registered_source_cannot_read_replacement_outside_private_scope(launched, tmp_path, change):
    store, run, _, home, _ = launched
    path = home / "projects/S.jsonl"
    if change == "outside":
        with pytest.raises(Conflict, match="outside"):
            report(launched, tmp_path / "personal.jsonl")
        return
    source = report(launched, path)[0]
    external = tmp_path / "external"
    external.mkdir()
    append(external / "S.jsonl", record(text="OUTSIDE PRIVATE SCOPE"))
    path.parent.mkdir()
    if change == "file-symlink":
        path.symlink_to(external / "S.jsonl")
    elif change == "directory-symlink":
        path.parent.rmdir()
        path.parent.symlink_to(external, target_is_directory=True)
    elif change == "home-replaced":
        home.rename(home.with_name("old-home"))
        append(path, record(text="OUTSIDE PRIVATE SCOPE"))
    else:
        os.mkfifo(path)
    assert observer.source_snapshot(store, run)["sources"][0]["lag"]
    result = observer.drain(store, source)
    assert result["bytes"] == 0 and result["gaps"]
    assert "OUTSIDE PRIVATE SCOPE" not in "\n".join(store.db.iterdump())


def test_source_rebinding_session_changes_and_redirected_home_refuse(launched, tmp_path):
    store, run, launch, home, _ = launched
    first = home / "S.jsonl"
    source = report(launched, first)[0]
    assert report(launched, first) == [source]
    with pytest.raises(Conflict, match="rebound"):
        report(launched, home / "different.jsonl")
    with pytest.raises(Conflict, match="rebound"):
        report(launched, first, session_id="other-session")
    with pytest.raises(ValidationError):
        report(launched, "relative.jsonl")
    with pytest.raises(ValidationError):
        report(launched, first, agent_transcript_path=str(home / "child.jsonl"))
    external = tmp_path / "another-home"
    home.rename(external)
    home.symlink_to(external, target_is_directory=True)
    with pytest.raises(Conflict, match="redirected"):
        report(launched, first)


def test_late_source_cannot_rebind_after_worker_reuse(launched):
    store, run, _, home, _ = launched
    source = report(launched, home / "S.jsonl")[0]
    with store.transaction() as tx:
        tx.finish_run(run, "stopped")
        tx.put_task("new", {"goal": "A different assignment."}, expected_revision=0)
        newer = tx.start_run("new", 1, "eng-001")
    with pytest.raises(Conflict, match="original active run"):
        report(launched, home / "late.jsonl")
    assert not store.db.execute("SELECT 1 FROM capture_sources WHERE run_id=?", (newer,)).fetchone()
    assert source_state(store, source)["run_id"] == run


@pytest.mark.parametrize("same_path", [False, True])
def test_concurrent_source_reports_keep_one_binding(launched, same_path):
    store, run, launch, home, args = launched
    def attempt(index):
        with ContinuityStore(store.root) as other:
            scoped = (other, run, launch, home, args)
            try:
                return report(scoped, home / ("same.jsonl" if same_path else f"{index}.jsonl"))[0]
            except Conflict:
                return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, range(2)))
    assert store.db.execute("SELECT count(*) FROM capture_sources WHERE run_id=?", (run,)).fetchone()[0] == 1
    if same_path:
        assert results[0] == results[1] and results[0]
    else:
        assert results.count(None) == 1


def test_scoped_transcript_stops_ingesting_after_task_amendment(capture):
    # Exercise the observer's revision gate independently of planning, whose
    # active-unit guard already prevents ordinary amendments during execution.
    store, run, _, _ = capture
    home = store.root.resolve()
    info = home.stat()
    path = home / "owned.jsonl"
    registration = dict(run_id=run, stream_id="native", path=path, decoder="claude-v1", session_id="S",
        native_scope={"root": str(home), "root_identity": [info.st_dev, info.st_ino],
            "actor_id": None, "launch_id": "original", "branch_policy": "claude-linear-v1"})
    source = observer.register(store, **registration)
    with store.transaction() as tx:
        task_id = tx.db.execute("SELECT task_id FROM runs WHERE run_id=?", (run,)).fetchone()[0]
        task = tx.task(task_id)
        tx.put_task(task_id, {**task["payload"], "goal": "Changed task."}, expected_revision=task["revision"])
    append(path, record(text="LATE OLD TASK"))
    assert observer.drain(store, source)["status"] == "stale"
    assert "LATE OLD TASK" not in "\n".join(store.db.iterdump())
    with pytest.raises(Conflict, match="task changed"):
        observer.register(store, **registration)
