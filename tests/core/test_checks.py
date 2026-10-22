from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hx import checks, fingerprints
from hx.continuity_store import Conflict, ContinuityStore
from hx.errors import ValidationError
from hx.evidence import read


def definition(script="print('checked')"):
    return {"id": "unit", "argv": [sys.executable, "-c", script], "cwd": ".",
            "inputs": [], "environment": {"complete": True, "executables": [], "inputs": [], "external_versions": {}},
            "timeout_s": 3, "max_output_bytes": 100000}


@pytest.fixture
def setup(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "source.py").write_text("value = 1\n")
    (repo / ".gitignore").write_text("ignored/\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    with ContinuityStore(tmp_path / "ledger") as store:
        with store.transaction() as tx:
            tx.put_task("T", {"goal": "Verify exact inputs.", "workdir": str(repo)}, expected_revision=0)
            run = tx.start_run("T", 1, "eng-001")
        yield store, run, repo, {"PATH": os.defpath, "LANG": "C", "PYTHONDONTWRITEBYTECODE": "1"}


def test_receipt_binds_command_source_environment_and_output(setup):
    store, run, repo, env = setup
    recipe = definition()
    result = checks.run_check(store, run, "unit", recipe=recipe, env=env)
    assert result["valid"] and result["reusable"] and not result["reused"]
    assert read(store, result["event_id"])["content"] == "checked\n"
    row = store.db.execute("SELECT * FROM receipts").fetchone()
    payload = json.loads(row["payload"])
    assert payload["argv"] == recipe["argv"] and payload["cwd"] == str(repo)
    assert payload["source_before"]["files"] == 2
    assert row["start_hash"] == row["end_hash"]
    assert store.db.execute("SELECT disposition FROM events").fetchone()[0] == "reduced"
    assert not list((store.root / "state" / "check-output").iterdir())


def test_identical_inputs_reuse_without_running_the_command(setup, monkeypatch):
    store, run, _, env = setup
    recipe = definition()
    first = checks.run_check(store, run, "unit", recipe=recipe, env=env)
    monkeypatch.setattr(checks, "execute", lambda *_: pytest.fail("identical check ran again"))
    second = checks.run_check(store, run, "unit", recipe=recipe, env=env)
    assert second["receipt_id"] != first["receipt_id"] and second["reused"]
    assert second["execution_id"] == first["execution_id"]
    assert second["event_id"] != first["event_id"]


@pytest.mark.parametrize("change", ["source", "untracked", "environment", "recipe", "external", "ignored", "executable"])
def test_changed_inputs_prevent_reuse(setup, change):
    store, run, repo, env = setup
    recipe = definition()
    (repo / "ignored").mkdir()
    fixture = repo / "ignored" / "fixture"
    fixture.write_text("old")
    recipe["inputs"] = ["ignored/fixture"]
    recipe["environment"]["external_versions"] = {"database": "ignored/fixture"}
    shim = repo / "ignored" / "shim"
    shim.write_text("#!/bin/sh\nexit 0\n")
    shim.chmod(0o755)
    recipe["environment"]["executables"] = [str(shim)]
    first = checks.run_check(store, run, "unit", recipe=recipe, env=env)
    if change == "source":
        (repo / "source.py").write_text("value = 2\n")
    elif change == "untracked":
        (repo / "new.py").write_text("new input\n")
    elif change == "environment":
        env["FEATURE_FLAG"] = "on"
    elif change == "recipe":
        recipe["argv"][-1] = "print('different check')"
    elif change == "executable":
        shim.write_text("#!/bin/sh\nexit 1\n")
    else:
        fixture.write_text("changed")
    second = checks.run_check(store, run, "unit", recipe=recipe, env=env)
    assert not second["reused"] and second["receipt_id"] != first["receipt_id"]


@pytest.mark.parametrize("restore", [False, True])
def test_writes_during_a_passing_check_invalidate_receipt_even_when_restored(setup, restore):
    store, run, _, env = setup
    script = "from pathlib import Path; p=Path('source.py'); old=p.read_bytes(); p.write_text('changed')"
    if restore:
        script += "; p.write_bytes(old)"
    result = checks.run_check(store, run, "unit", recipe=definition(script), env=env)
    assert result["exit_code"] == 0 and not result["valid"]
    assert "source_changed_during_check" in result["reasons"]


def test_unknown_external_state_keeps_execution_but_disables_reuse(setup):
    store, run, _, env = setup
    recipe = definition()
    recipe["environment"]["external_versions"] = {"service": None}
    first = checks.run_check(store, run, "unit", recipe=recipe, env=env)
    second = checks.run_check(store, run, "unit", recipe=recipe, env=env)
    assert first["valid"] and not first["reusable"]
    assert second["receipt_id"] != first["receipt_id"] and not second["reused"]


def test_failed_output_is_retained_but_cannot_be_reused(setup):
    store, run, _, env = setup
    recipe = definition("import sys; print('specific diagnostic', file=sys.stderr); sys.exit(3)")
    result = checks.run_check(store, run, "unit", recipe=recipe, env=env)
    assert result["exit_code"] == 3 and not result["valid"]
    assert "specific diagnostic" in read(store, result["event_id"])["content"]


@pytest.mark.parametrize("reason,script,timeout,limit", [
    ("output_limit", "print('x' * 100000)", 3, 1234),
    ("timeout", "import time; print('starting', flush=True); time.sleep(3)", 0.2, 100000),
])
def test_resource_bounds_stop_check_without_claiming_success(setup, reason, script, timeout, limit):
    store, run, _, env = setup
    recipe = definition(script)
    recipe.update(timeout_s=timeout, max_output_bytes=limit)
    result = checks.run_check(store, run, "unit", recipe=recipe, env=env)
    assert not result["valid"] and reason in result["reasons"]
    assert (store.artifacts / result["artifact_hash"]).stat().st_size <= limit


def test_environment_values_are_not_stored_in_receipt(setup):
    store, run, _, env = setup
    env["EXAMPLE_SECRET"] = "do-not-store-this-value"
    checks.run_check(store, run, "unit", recipe=definition(), env=env)
    assert "do-not-store-this-value" not in "\n".join(store.db.iterdump())


def test_uncertain_request_retries_do_not_execute_twice(setup, monkeypatch):
    store, run, _, env = setup
    recipe = definition()
    first = checks.run_check(store, run, "unit", recipe=recipe, request_id="one", env=env)
    monkeypatch.setattr(checks, "execute", lambda *_: pytest.fail("retry reexecuted"))
    assert checks.run_check(store, run, "unit", recipe=recipe, request_id="one", env=env) == first
    with pytest.raises(Conflict, match="different command"):
        checks.run_check(store, run, "unit", recipe=definition("print('changed')"), request_id="one", env=env)
    with store.transaction() as tx:
        tx.finish_run(run, "finished")
    assert checks.run_check(store, run, "unit", request_id="one", env=env) == first
    with pytest.raises(Conflict, match="active run"):
        checks.run_check(store, run, "unit", recipe=recipe, request_id="new", env=env)


def test_unresolved_execution_is_not_automatically_restarted(setup, monkeypatch):
    store, run, _, env = setup
    recipe = definition()
    def interrupted(*args, **kwargs):
        raise RuntimeError("interrupted before receipt commit")
    monkeypatch.setattr(checks, "execute", interrupted)
    with pytest.raises(RuntimeError):
        checks.run_check(store, run, "unit", recipe=recipe, request_id="one", env=env)
    with pytest.raises(Conflict, match="unresolved execution"):
        checks.run_check(store, run, "unit", recipe=recipe, request_id="one", env=env)
    assert store.db.execute("SELECT count(*) FROM receipts").fetchone()[0] == 0


def test_assigned_acceptance_recipe_cannot_be_replaced(setup):
    store, run, repo, env = setup
    with store.transaction() as tx:
        tx.finish_run(run, "rebind")
        tx.put_task("T", {"goal": "Acceptance", "workdir": str(repo), "checks": {"unit": definition()}}, expected_revision=1)
        run = tx.start_run("T", 2, "eng-001")
    with pytest.raises(Conflict, match="assigned check version"):
        checks.run_check(store, run, "unit", recipe=definition("print('weaker')"), env=env)
    assert checks.run_check(store, run, "unit", env=env)["valid"]


def test_executable_symlink_and_relative_path_keep_invocation_identity(setup):
    _, _, repo, env = setup
    binary = repo / "bin"
    binary.mkdir()
    link = binary / "python"
    link.symlink_to(sys.executable)
    env["PATH"] = "bin"
    snapshot, argv = fingerprints.environment(repo, ["python", "-c", "pass"], definition()["environment"], env)
    assert argv[0] == str(link) and snapshot["complete"]


def test_reopen_keeps_receipts(setup):
    store, run, _, env = setup
    receipt = checks.run_check(store, run, "unit", recipe=definition(), env=env)
    with ContinuityStore(store.root) as reopened:
        assert reopened.db.execute("SELECT count(*) FROM check_executions").fetchone()[0] == 1
        assert reopened.db.execute("SELECT receipt_id FROM receipts").fetchone()[0] == receipt["receipt_id"]
        assert read(reopened, receipt["event_id"])["content"] == "checked\n"


def test_check_cli_uses_assigned_recipe(setup, run_hx):
    store, run, repo, _ = setup
    with store.transaction() as tx:
        tx.finish_run(run, "rebind")
        tx.put_task("T", {"goal": "Acceptance", "workdir": str(repo), "checks": {"unit": definition()}}, expected_revision=1)
        run = tx.start_run("T", 2, "eng-001")
    result = run_hx("check", "unit", "--root", str(store.root), env_extra={"HARNESS_ID": "eng-001"})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["valid"]
    receipt = json.loads(result.stdout)["receipt_id"]
    checked = run_hx("check", "unit", "--receipt", receipt, "--root", str(store.root), env_extra={"HARNESS_ID": "eng-001"})
    assert checked.returncode == 0, checked.stderr
    assert json.loads(checked.stdout)["current"]


def test_source_change_after_check_makes_boundary_revalidation_fail(setup):
    store, run, repo, env = setup
    result = checks.run_check(store, run, "unit", recipe=definition(), env=env)
    assert checks.current(store, run, result["receipt_id"], env=env)["current"]
    (repo / "source.py").write_text("changed after verification")
    checked = checks.current(store, run, result["receipt_id"], env=env)
    assert not checked["current"] and "source_changed_or_unknown" in checked["reasons"]


def test_missing_output_cannot_be_reused_and_is_recreated_by_a_new_check(setup):
    store, run, _, env = setup
    recipe = definition()
    first = checks.run_check(store, run, "unit", recipe=recipe, env=env)
    (store.artifacts / first["artifact_hash"]).unlink()
    assert "output_missing_or_corrupt" in checks.current(store, run, first["receipt_id"], env=env)["reasons"]
    second = checks.run_check(store, run, "unit", recipe=recipe, env=env)
    assert second["valid"] and not second["reused"]


def test_environment_input_changed_during_check_invalidates_receipt(setup):
    store, run, repo, env = setup
    (repo / "ignored").mkdir()
    (repo / "ignored" / "environment-version").write_text("old")
    recipe = definition("from pathlib import Path; Path('ignored/environment-version').write_text('new')")
    recipe["environment"]["inputs"] = ["ignored/environment-version"]
    result = checks.run_check(store, run, "unit", recipe=recipe, env=env)
    assert not result["valid"] and "environment_changed_during_check" in result["reasons"]


def test_task_amended_while_check_runs_keeps_evidence_but_invalidates_result(setup, monkeypatch):
    store, run, repo, env = setup
    original = checks.execute
    def execute(*args, **kwargs):
        result = original(*args, **kwargs)
        with store.transaction() as tx:
            tx.put_task("T", {"goal": "Changed acceptance.", "workdir": str(repo)}, expected_revision=1)
        return result
    monkeypatch.setattr(checks, "execute", execute)
    result = checks.run_check(store, run, "unit", recipe=definition(), env=env)
    assert not result["valid"] and "assignment_changed_during_check" in result["reasons"]
    assert read(store, result["event_id"])["content"] == "checked\n"


def test_check_with_unsettled_background_process_cannot_pass(setup):
    store, run, _, env = setup
    recipe = definition("import subprocess, sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(5)'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)")
    result = checks.run_check(store, run, "unit", recipe=recipe, env=env)
    assert result["exit_code"] == 0 and not result["valid"]
    assert "background_processes" in result["reasons"]


def test_tracked_deletion_is_known_but_missing_declared_fixture_is_not(setup):
    _, _, repo, env = setup
    original = fingerprints.source(repo, [], env)
    (repo / "source.py").unlink()
    deleted = fingerprints.source(repo, [], env)
    assert deleted["complete"] and deleted["hash"] != original["hash"]
    missing = fingerprints.source(repo, ["required-fixture"], env)
    assert not missing["complete"]


def test_fingerprinting_reads_only_bounded_chunks(setup, monkeypatch):
    _, _, repo, _ = setup
    target = repo / "input.dat"
    target.write_bytes(b"x" * (3 * fingerprints.CHUNK_BYTES + 7))
    original = os.fdopen
    reads = []
    class Reader:
        def __init__(self, handle):
            self.handle = handle
        def __enter__(self):
            return self
        def __exit__(self, *_):
            self.handle.close()
        def __getattr__(self, name):
            return getattr(self.handle, name)
        def read(self, size=-1):
            assert 0 < size <= fingerprints.CHUNK_BYTES
            chunk = self.handle.read(size)
            reads.append(len(chunk))
            return chunk
    monkeypatch.setattr(os, "fdopen", lambda *args, **kwargs: Reader(original(*args, **kwargs)))
    fingerprint = fingerprints.Fingerprint()
    fingerprint.path(target, "input")
    assert fingerprint.result()["complete"]
    assert sum(reads) == target.stat().st_size and len(reads) == 4


def test_receipt_without_current_provenance_cannot_pass_any_gate(setup):
    store, run, _, env = setup
    receipt = checks.run_check(store, run, "unit", recipe=definition(), env=env, request_id="proof")
    row = store.db.execute("SELECT * FROM receipts WHERE receipt_id=?", (receipt["receipt_id"],)).fetchone()
    payload = json.loads(row["payload"])
    payload.pop("provenance")
    store.db.execute("UPDATE receipts SET payload=? WHERE receipt_id=?", (json.dumps(payload), receipt["receipt_id"]))
    row = store.db.execute("SELECT * FROM receipts WHERE receipt_id=?", (receipt["receipt_id"],)).fetchone()
    assert not checks.receipt_intact(store, row)
    assert not checks.current(store, run, receipt["receipt_id"], env=env)["current"]
    assert not checks.run_check(store, run, "unit", recipe=definition(), env=env, request_id="proof")["valid"]


def test_check_result_carries_the_declared_report_summary(setup, monkeypatch):
    store, run, repo, env = setup
    junit = ('<testsuites><testsuite><testcase classname="t" name="a"/><testcase classname="t" name="b"/>'
             '<testcase classname="t" name="c"><skipped/></testcase></testsuite></testsuites>')
    recipe = {**definition(f"import sys; open(sys.argv[1], 'w').write({junit!r}); print('3 tests')"), "report": "junit-xml"}
    recipe["argv"] = [*recipe["argv"], "{report}"]
    result = checks.run_check(store, run, "unit", recipe=recipe, env=env)
    summary = {"format": "junit-xml", "passed": 2, "failed": 0, "skipped": 1, "errors": 0,
               "failing": [], "truncated": False}
    assert result["valid"] and result["summary"] == summary
    event = json.loads(store.db.execute("SELECT payload FROM events WHERE event_id=?", (result["event_id"],)).fetchone()[0])
    assert event["summary"] == summary
    # The report lives beside the captured output under the root, never in the checkout, and is removed.
    assert not list((store.root / "state" / "check-output").iterdir())
    assert sorted(path.name for path in repo.iterdir()) == [".git", ".gitignore", "source.py"]
    payload = json.loads(store.db.execute("SELECT payload FROM receipts").fetchone()["payload"])
    assert payload["resolved_argv"][-1] == "{report}"
    monkeypatch.setattr(checks, "execute", lambda *_: pytest.fail("identical check ran again"))
    assert checks.run_check(store, run, "unit", recipe=recipe, env=env)["summary"] == summary


def test_a_check_without_a_declared_report_has_no_summary_and_a_file_report_needs_its_path(setup):
    store, run, _, env = setup
    recipe = definition("print('==== 1 failed, 3 passed in 0.50s ====')")
    assert checks.run_check(store, run, "unit", recipe=recipe, env=env)["summary"] is None
    with pytest.raises(ValidationError, match="placeholder"):
        checks.validate({**definition(), "report": "junit-xml"})
    with pytest.raises(ValidationError, match="report format"):
        checks.validate({**definition(), "report": "text"})


def test_spawn_failure_has_no_summary(tmp_path):
    observed = checks.execute([str(tmp_path / "missing")], tmp_path, {}, tmp_path / "output", 1, 1000)
    assert observed["reason"] == "spawn_failed" and observed["summary"] is None


JOINED = """import os, pathlib, time
with open(os.environ["COUNTER"], "a") as counter:
    counter.write("ran\\n")
release = pathlib.Path(os.environ["RELEASE"])
while not release.exists():
    time.sleep(0.02)
print("joined proof")
"""


def test_a_check_with_the_same_inputs_in_two_worktrees_runs_once(tmp_path):
    """Two roots run one check from two worktrees at one commit while the first execution is live."""
    import time
    from concurrent.futures import ThreadPoolExecutor
    repo = tmp_path / "repo"
    repo.mkdir()
    git = ["git", "-C", str(repo), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
           "-c", "commit.gpgSign=false", "-c", "core.hooksPath=/dev/null"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "source.py").write_text("value = 1\n")
    subprocess.run([*git, "add", "."], check=True)
    subprocess.run([*git, "commit", "-qm", "Fixture"], check=True)
    worktree = tmp_path / "second-worktree"
    subprocess.run([*git, "worktree", "add", "-q", "--detach", str(worktree)], check=True)
    counter, release = tmp_path / "executions", tmp_path / "release"
    env = {"PATH": os.defpath, "LANG": "C", "PYTHONDONTWRITEBYTECODE": "1", "COUNTER": str(counter), "RELEASE": str(release)}
    recipe = {**definition(JOINED), "timeout_s": 30}

    def started(store, task, checkout, worker):
        with store.transaction() as tx:
            tx.put_task(task, {"goal": "Verify exact inputs.", "workdir": str(checkout)}, expected_revision=0)
            return tx.start_run(task, 1, worker)

    def requested(root, checkout, worker):
        with ContinuityStore(root) as store:
            run = started(store, "T", checkout, worker)
            began = time.perf_counter()
            result = checks.run_check(store, run, "unit", recipe=recipe, env=env)
            return run, result, time.perf_counter() - began

    with ThreadPoolExecutor(2) as pool:
        first = pool.submit(requested, tmp_path / "first-root", repo, "eng-001")
        end = time.monotonic() + 10
        while not counter.exists() and time.monotonic() < end:
            time.sleep(0.02)
        try:
            assert counter.exists()
            second = pool.submit(requested, tmp_path / "second-root", worktree, "eng-002")
            time.sleep(0.5)
            assert not second.done()
        finally:
            release.write_text("done")
        (_, owner, owned), (run, joiner, waited) = first.result(timeout=30), second.result(timeout=30)
    assert counter.read_text() == "ran\n"
    assert owner["valid"] and "joined" not in owner, owner
    assert joiner["valid"] and joiner["reusable"] and not joiner["reused"], joiner
    assert joiner["joined"] == {"root": str((tmp_path / "first-root").resolve()), "execution_id": owner["execution_id"]}
    assert joiner["artifact_hash"] == owner["artifact_hash"] and joiner["receipt_id"] != owner["receipt_id"]
    with ContinuityStore(tmp_path / "second-root") as store:
        assert read(store, joiner["event_id"])["content"] == "joined proof\n"
        assert checks.current(store, run, joiner["receipt_id"], env=env)["current"]
        # The identity is the checkout's content at its commit, so the main checkout reuses it here.
        reused = checks.run_check(store, started(store, "U", repo, "eng-003"), "unit", recipe=recipe, env=env)
        assert reused["valid"] and reused["reused"] and reused["execution_id"] == joiner["execution_id"]
        (worktree / "source.py").write_text("value = 2\n")
        assert not checks.current(store, run, joiner["receipt_id"], env=env)["current"]
    assert counter.read_text() == "ran\n"
    print(f"\njoined check: owner {owned:.2f}s, joiner {waited:.2f}s from another root and worktree; 1 execution, 3 receipts")
