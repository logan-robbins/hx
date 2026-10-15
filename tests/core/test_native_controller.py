from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from hx import lifecycle, native_controller as controller, native_launch, unit_execution
from hx.continuity_store import Conflict
from .conftest import wait_for
from .test_appmap import mapped
from .test_context_packets import assignment
from .test_map_updates import active
from .test_native_launch import configured
from .test_unit_execution import fleet


CLI_STAND_IN = r'''#!/usr/bin/env python3
import json, os, pathlib, shlex, subprocess, sys, tomllib, tty, termios
root = pathlib.Path(os.environ['HARNESS_ROOT'])
worker = os.environ['HARNESS_ID']
flavor = os.environ['HX_CONTINUITY_ADAPTER']
home = pathlib.Path(os.environ.get('CLAUDE_CONFIG_DIR') or os.environ.get('CODEX_HOME') or
    os.environ.get('GROK_HOME') or os.environ.get('PI_CODING_AGENT_DIR') or os.environ['XDG_CONFIG_HOME'])
log = root / 'run' / worker / 'native-test.jsonl'
log.parent.mkdir(parents=True, exist_ok=True)
def record(value):
    with log.open('a') as output:
        output.write(json.dumps(value) + '\n')
if flavor == 'pi':
    contract = json.loads((home / 'extensions/hx/hook-contract.json').read_text())
    def command(event):
        return [contract['command'], *contract['args'], event]
else:
    if flavor in ('grok', 'codex'):
        settings = tomllib.loads((home / 'config.toml').read_text())
    else:
        settings = json.loads((home / ('muse/settings.json' if flavor == 'meta' else 'settings.json')).read_text())
    def command(event):
        name = {'context': 'SessionStart', 'request': 'UserPromptSubmit'}[event]
        return shlex.split(settings['hooks'][name][0]['hooks'][0]['command'])
def fire(event, **fields):
    result = subprocess.run(command(event), input=json.dumps({'session_id':'test-native-session', **fields}),
                            text=True, capture_output=True)
    record({'event': event, 'code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
record({'argv': sys.argv[1:], 'cwd': os.getcwd(), 'home': str(home)})
fire('context', source='startup')
# Emulate a native multiline editor: advertise bracketed paste and submit the
# complete buffered input only when the controller sends Enter after paste.
print('\x1b[?2004hhx-fake-idle>', flush=True)
tty.setcbreak(sys.stdin.fileno())
buffer = bytearray()
escape = bytearray()
pasting = False
while True:
    char = os.read(sys.stdin.fileno(), 1)
    if not char:
        break
    if escape or char == b'\x1b':
        escape.extend(char)
        if bytes(escape) == b'\x1b[200~':
            pasting = True
            escape.clear()
        elif bytes(escape) == b'\x1b[201~':
            pasting = False
            escape.clear()
        elif not any(candidate.startswith(bytes(escape)) for candidate in (b'\x1b[200~', b'\x1b[201~')):
            buffer.extend(escape)
            escape.clear()
        continue
    if char in (b'\n', b'\r') and not pasting:
        line = buffer.decode()
        buffer.clear()
        if line:
            record({'prompt': line})
            if not os.environ.get('HX_TEST_SKIP_ACK'):
                fire('request', prompt=line)
        print('hx-fake-idle>', flush=True)
    else:
        buffer.extend(char)
'''


@pytest.fixture
def runtime(configured, tmux_server, child_env, tmp_path, monkeypatch):
    from hx import application_loop
    # Transport fixtures do not start an unrelated background inference service.
    monkeypatch.setattr(application_loop, 'ensure', lambda *a, **kw: None)
    store, repo, run = configured
    executable = tmp_path / "cli-stand-in"
    executable.write_text(CLI_STAND_IN)
    executable.chmod(0o700)
    login = tmp_path / "user" / ".codex" / "auth.json"
    login.parent.mkdir(parents=True)
    login.write_text('{"fixture":true}')
    env = child_env(HX_TMUX=" ".join(tmux_server), HOME=str(login.parents[1]), HX_PYTHON=sys.executable)
    for flavor in ("claude", "codex", "meta", "grok", "pi"):
        (store.root / "config" / (flavor + ".json")).write_text(json.dumps({"bin": str(executable), "version": "test-only"}))
        token = store.root / "seed" / ("token" if flavor == "claude" else flavor + "-token")
        token.write_text("fixture-key")
        token.chmod(0o600)
    return store, repo, run, env


def flavor(runtime, name):
    store = runtime[0]
    path = store.root / "config" / "eng-001" / "harness.json"
    body = json.loads(path.read_text())
    body["flavor"] = name
    path.write_text(json.dumps(body))


def ready(runtime):
    store, _, run, env = runtime
    controller.advance(store, "eng-001", run, "launch", env=env, submit=False)
    try:
        wait_for(lambda: native_launch._row(store, run)["status"] == "ready", what="native startup callback", limit=10)
    except AssertionError:
        row = native_launch._row(store, run)
        pane = controller.goal.capture_pane(row["payload"]["session"], env)
        pytest.fail(f"startup did not complete: {row['status']}; pane: {pane}")
    # The startup hook commits before the CLI returns to its input loop.
    # Tests that submit immediately need the independently observable prompt too.
    session = native_launch._row(store, run)["payload"]["session"]
    wait_for(lambda: "hx-fake-idle>" in controller.goal.capture_pane(session, env),
             what="native fixture input prompt", limit=10)
    return native_launch._row(store, run)


@pytest.mark.parametrize("adapter", ["claude", "codex", "meta", "grok", "pi"])
def test_real_tmux_installed_hooks_and_single_submission(runtime, adapter):
    store, repo, run, env = runtime
    flavor(runtime, adapter)
    row = ready(runtime)
    assert row["payload"]["session"].startswith("hx-")
    assert row["payload"]["instruction_delivery"] == "prefix_verified_waiting_for_complete_input"
    result = controller.advance(store, "eng-001", run, "launch", env=env)
    wait_for(lambda: native_launch._row(store, run)["status"] == "submitted", what="exact native request acknowledgement", limit=10)
    assert controller.advance(store, "eng-001", run, "launch", env=env)["status"] == "submitted"
    lines = [json.loads(line) for line in (store.root / "run/eng-001/native-test.jsonl").read_text().splitlines()]
    assert len([line for line in lines if "prompt" in line]) == 1
    assert lines[0]["cwd"] == str(repo)
    assert lines[0]["home"].startswith(row["payload"]["capsule"])
    assert all(not line.get("stderr") for line in lines)
    assert Path(row["payload"]["packet_path"]).read_text().count("Session identity:") == (1 if adapter in {"codex", "meta"} else 0)
    # A worker must not release ownership while its native process can still act.
    with pytest.raises(Conflict, match="session ownership"):
        unit_execution.stop(store, run)
    assert store.db.execute("SELECT count(*) FROM leases WHERE run_id=?", (run,)).fetchone()[0] > 0


def test_missing_acknowledgement_never_repastes(runtime):
    store, _, run, env = runtime
    env["HX_TEST_SKIP_ACK"] = "1"
    ready(runtime)
    result = controller.advance(store, "eng-001", run, "launch", env=env)
    assert result["status"] == "submission_unconfirmed"
    assert controller.advance(store, "eng-001", run, "launch", env=env)["status"] == "submission_unconfirmed"
    lines = (store.root / "run/eng-001/native-test.jsonl").read_text().splitlines()
    assert sum('"prompt"' in line for line in lines) == 1


def test_transport_exception_stays_uncertain_without_releasing_ownership(runtime, monkeypatch):
    store, _, run, env = runtime
    ready(runtime)
    def uncertain(*args, **kwargs):
        raise OSError("transport failed after possible delivery")
    monkeypatch.setattr(controller.goal, "paste", uncertain)
    with pytest.raises(OSError):
        controller.advance(store, "eng-001", run, "launch", env=env)
    assert controller.advance(store, "eng-001", run, "launch", env=env)["status"] == "submission_uncertain"
    with pytest.raises(Conflict):
        unit_execution.stop(store, run)


def test_existing_session_collision_is_not_replaced(runtime):
    store, _, run, env = runtime
    row = native_launch.prepare(store, "eng-001", run, "launch", env=env)
    session = "hx-" + row["launch_id"]
    command = env["HX_TMUX"].split()
    subprocess.run([*command, "new-session", "-d", "-s", session, "sleep", "60"], check=True)
    before = subprocess.check_output([*command, "display-message", "-p", "-t", "="+session, "#{pane_id}"])
    with pytest.raises(Exception, match="creation failed"):
        controller.advance(store, "eng-001", run, "launch", env=env)
    after = subprocess.check_output([*command, "display-message", "-p", "-t", "="+session, "#{pane_id}"])
    assert before == after
    assert controller.advance(store, "eng-001", run, "launch", env=env)["status"] == "start_uncertain"


def test_worker_launch_without_identity_is_always_refused(runtime):
    store, _, _, env = runtime
    with pytest.raises(Conflict, match="worker launch requires"):
        lifecycle.launch(store.root, "eng-001", env=env)


def test_wrong_native_session_cannot_acknowledge_submission(runtime):
    store, _, run, env = runtime
    env["HX_TEST_SKIP_ACK"] = "1"
    ready(runtime)
    controller.advance(store, "eng-001", run, "launch", env=env)
    row = native_launch._row(store, run)
    lines = [json.loads(line) for line in (store.root / "run/eng-001/native-test.jsonl").read_text().splitlines()]
    prompt = next(line["prompt"] for line in lines if "prompt" in line)
    with pytest.raises(Conflict, match="another session"):
        controller.observe(store, run, row["launch_id"], "request", {"prompt": prompt, "session_id": "wrong-session"})
    assert native_launch._row(store, run)["status"] == "submission_unconfirmed"


def test_request_without_verified_startup_is_rejected(configured):
    from io import StringIO
    from hx import hooks

    store, _, run = configured
    row = native_launch.prepare(store, "eng-001", run, "launch", env={})
    code = hooks.main(["--root", str(store.root), "--id", "eng-001", "--continuity-run", run,
        "--continuity-launch", row["launch_id"], "--continuity-adapter", "claude", "request"],
        stdin=StringIO(json.dumps({"prompt": "Begin without startup.", "session_id": "S"})), env={})
    assert code == 2
    assert native_launch._row(store, run)["status"] == "submission_rejected"


def test_planned_launch_cli_returns_durable_status(runtime, capsys):
    store, _, run, env = runtime
    code = lifecycle.main_launch(["eng-001", "--run", run, "--request", "cli"], store.root, env=env)
    assert code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["run_id"] == run and output["session"].startswith("hx-")


def test_missing_original_pane_does_not_start_a_replacement(runtime):
    store, _, run, env = runtime
    row = ready(runtime)
    subprocess.run([*env["HX_TMUX"].split(), "kill-session", "-t", "=" + row["payload"]["session"]], check=True)
    assert controller.advance(store, "eng-001", run, "launch", env=env)["status"] == "session_uncertain"
    assert controller.advance(store, "eng-001", run, "launch", env=env)["status"] == "session_uncertain"


def test_uncontrolled_clear_invalidates_startup_delivery(runtime):
    store, _, run, _ = runtime
    row = ready(runtime)
    with pytest.raises(Conflict, match="checkpoint boundary"):
        controller.observe(store, run, row["launch_id"], "context", {"source": "clear", "session_id": "test-native-session"})
    assert not native_launch._row(store, run)["payload"]["startup_observed"]
    with pytest.raises(Conflict, match="not verified"):
        controller.observe(store, run, row["launch_id"], "request", {"prompt": "Continue.", "session_id": "test-native-session"})


@pytest.mark.skipif(not os.environ.get("HX_MUSE_TEST_BIN"), reason="requires explicitly selected installed Muse; uses its echo provider without model calls")
def test_installed_muse_startup_and_request_hooks(runtime, tmp_path):
    store, _, run, env = runtime
    flavor(runtime, "meta")
    executable = Path(os.environ["HX_MUSE_TEST_BIN"]).resolve()
    wrapper = tmp_path / "installed-muse-echo"
    diagnostics = tmp_path / "muse-stderr.txt"
    wrapper.write_text(f"#!{sys.executable}\nimport os,sys\nos.environ['MUSE_NO_AUTO_UPDATE']='1'\n"
        f"os.dup2(os.open({str(diagnostics)!r}, os.O_WRONLY | os.O_CREAT, 0o600), 2)\n"
        "args=sys.argv[1:]\nassert '--model' in args\n"
        "for option in ('--model', '--reasoning-effort'):\n    index=args.index(option)\n    del args[index:index+2]\n"
        f"os.execv({str(executable)!r}, [{str(executable)!r}, '--provider', 'echo', *args])\n")
    wrapper.chmod(0o700)
    (store.root / "config/meta.json").write_text(json.dumps({"bin": str(wrapper), "version": "installed-echo-check"}))
    controller.advance(store, "eng-001", run, "launch", env=env, submit=False)
    row = native_launch._row(store, run)
    wait_for(lambda: controller.goal.pane_is_idle(controller.goal.capture_pane(row["payload"]["session"], env) or ""),
             what="installed Muse input prompt", limit=10)
    controller.advance(store, "eng-001", run, "launch", env=env)
    wait_for(lambda: native_launch._row(store, run)["status"] == "submitted", what="installed Muse request hook", limit=15)
    current = native_launch._row(store, run)
    assert current["payload"]["instruction_delivery"] == "complete_native_input_acknowledged"
    # Muse trims external trailing whitespace. The non-whitespace closing
    # envelope preserves the packet's newline as an interior exact byte.
    packet = Path(current['payload']['packet_path']).read_text()
    assert packet.endswith('\n')
    actual_input = controller._submission_prompt(current)
    assert packet in actual_input and actual_input.endswith(controller._input_end(current['launch_id']))
    assert actual_input.strip() == actual_input
    assert controller.advance(store, "eng-001", run, "launch", env=env)["status"] == "submitted"


def test_recovery_never_starts_a_run_without_explicit_launch(runtime, monkeypatch):
    store, _, _, env = runtime
    monkeypatch.setattr(controller, 'advance', lambda *a, **kw: pytest.fail('reservation is not launch authorization'))
    assert controller.reconcile_launches(store, env=env) == []
    assert store.db.execute('SELECT count(*) FROM native_launches').fetchone()[0] == 0


def test_recovery_advances_existing_ready_launch_once(runtime):
    store, _, run, env = runtime
    ready(runtime)
    results = controller.reconcile_launches(store, env=env)
    assert len(results) == 1
    wait_for(lambda: native_launch._row(store, run)['status'] == 'submitted', what='recovered submission')
    controller.reconcile_launches(store, env=env)
    lines = (store.root / 'run/eng-001/native-test.jsonl').read_text().splitlines()
    assert sum('"prompt"' in line for line in lines) == 1


def test_recovery_records_missing_submitted_pane_without_replacement(runtime):
    store, _, run, env = runtime
    row = ready(runtime)
    controller.advance(store, 'eng-001', run, 'launch', env=env)
    wait_for(lambda: native_launch._row(store, run)['status'] == 'submitted', what='submission')
    subprocess.run([*env['HX_TMUX'].split(), 'kill-session', '-t', '=' + row['payload']['session']], check=True)
    result = controller.reconcile_launches(store, env=env)[0]
    assert result['status'] == 'session_uncertain'
    assert controller.reconcile_launches(store, env=env) == []
    assert store.db.execute('SELECT count(*) FROM leases WHERE run_id=?', (run,)).fetchone()[0]
    assert store.db.execute("SELECT count(*) FROM outbox WHERE kind='native_launch_unresolved'").fetchone()[0] == 1


def test_public_controller_rejects_raw_persistence_runs(tmp_path):
    from hx.continuity_store import ContinuityStore
    with ContinuityStore(tmp_path) as store:
        with store.transaction() as tx:
            tx.put_task('raw', {'goal': 'A storage fixture is not an executable packet.'}, expected_revision=0)
            run = tx.start_run('raw', 1, 'eng-001')
        with pytest.raises(Conflict, match='active planned run'):
            controller.advance(store, 'eng-001', run, 'raw-launch', env={})
        assert store.db.execute('SELECT count(*) FROM native_launches').fetchone()[0] == 0
        assert not (tmp_path / 'run').exists()


def test_startup_never_recomposes_or_references_required_context(runtime, monkeypatch):
    store, _, run, env = runtime
    row = ready(runtime)
    with store.transaction() as tx:
        payload = {**row['payload'], 'startup_observed': False}
        tx.db.execute("UPDATE native_launches SET status='starting',payload=? WHERE launch_id=?", (json.dumps(payload), row['launch_id']))
    def unexpected(*a, **kw):
        pytest.fail('startup must deliver the already frozen complete input, with no new model read')
    monkeypatch.setattr(controller.context_packets, 'issue', unexpected)
    result = controller.observe(store, run, row['launch_id'], 'context', {'source': 'startup', 'session_id': row['payload']['native_session']})
    current = native_launch._row(store, run)
    assert result is None and current['status'] == 'ready'
    assert current['payload']['checkpoint_id'] == row['payload']['checkpoint_id']
    assert current['payload']['startup_observed']
    assert Path(current['payload']['packet_path']).read_text() in controller._submission_prompt(current)


@pytest.mark.parametrize("adapter", ["claude", "codex", "meta", "grok", "pi"])
def test_every_adapter_receives_exact_full_checkpoint_once_without_required_reads(runtime, adapter):
    from hx import context_packets
    from hx.continuity_store import digest
    store, _, run, env = runtime
    flavor(runtime, adapter)
    row = ready(runtime)
    controller.advance(store, "eng-001", run, "launch", env=env)
    wait_for(lambda: native_launch._row(store, run)["status"] == "submitted", what="complete input acknowledgment")
    current = native_launch._row(store, run)
    packet = context_packets.read(store, current["payload"]["checkpoint_id"], run)
    rows = [json.loads(line) for line in (store.root / "run/eng-001/native-test.jsonl").read_text().splitlines()]
    inputs = [row["prompt"] for row in rows if "prompt" in row]
    assert inputs == [controller._prompt(current["launch_id"]) + "\n\n" + packet["text"] + controller._input_end(current["launch_id"])]
    assert digest(inputs[0]) == current["payload"]["submission_hash"]
    assert all(not entry.get("stdout") for entry in rows if entry.get("event") == "context")
    assert "Read the task context at" not in inputs[0]
    assert inputs[0].count("Finish one observable behavior end to end.") == (1 if adapter in {"codex", "meta"} else 0)


def test_truncated_initial_native_input_is_rejected_before_model_execution(runtime):
    store, _, run, env = runtime
    env["HX_TEST_SKIP_ACK"] = "1"
    ready(runtime)
    controller.advance(store, "eng-001", run, "launch", env=env)
    current = native_launch._row(store, run)
    assert current["status"] == "submission_unconfirmed"
    partial = controller._submission_prompt(current)[:-100]
    with pytest.raises(Conflict, match="complete frozen checkpoint"):
        controller.observe(store, run, current["launch_id"], "request",
            {"prompt": partial, "session_id": "test-native-session"})
    assert native_launch._row(store, run)["status"] == "submission_unconfirmed"
