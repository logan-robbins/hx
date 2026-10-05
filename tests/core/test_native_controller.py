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
import json, os, pathlib, shlex, subprocess, sys, tomllib
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
print('hx-fake-idle>', flush=True)
for line in sys.stdin:
    line = line.rstrip('\n')
    if not line:
        print('hx-fake-idle>', flush=True)
        continue
    record({'prompt': line})
    if not os.environ.get('HX_TEST_SKIP_ACK'):
        fire('request', prompt=line)
    print('hx-fake-idle>', flush=True)
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
    assert row["payload"]["instruction_delivery"] == "launcher_prefix_verified_startup_observed"
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


def test_legacy_launch_and_restart_cannot_bypass_planned_ownership(runtime):
    store, _, _, env = runtime
    for action in (lifecycle.launch, lifecycle.restart):
        with pytest.raises(Conflict, match="planned worker"):
            action(store.root, "eng-001", env=env)


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
    assert native_launch._row(store, run)["payload"]["instruction_delivery"] == "launcher_prefix_verified_startup_observed"
    assert controller.advance(store, "eng-001", run, "launch", env=env)["status"] == "submitted"
