"""The fake `claude` used by the M0-M5 suites (ORCHESTRATION.md).

It records argv, env and cwd; accepts pasted input on a real tmux pane; and emits scripted
hook payloads. M6 onward runs the real pinned binary.
"""

from __future__ import annotations

import json
import os
import stat
import subprocess

from .conftest import FAKE_CLAUDE, clean_env, wait_for


def test_the_fake_is_executable_and_stdlib_only():
    assert os.stat(FAKE_CLAUDE).st_mode & stat.S_IXUSR
    source = FAKE_CLAUDE.read_text()
    assert source.startswith("#!/usr/bin/env python3")
    for third_party in ("import pytest", "import yaml", "import requests"):
        assert third_party not in source


def test_it_records_argv_env_and_cwd(tmp_path):
    root = tmp_path / "instance"
    (root / "run").mkdir(parents=True)
    workdir = tmp_path / "work"
    workdir.mkdir()
    result = subprocess.run(
        [str(FAKE_CLAUDE), "--dangerously-skip-permissions", "--model", "claude-opus-5"],
        cwd=str(workdir),
        input="",
        capture_output=True,
        text=True,
        env=clean_env(HARNESS_ROOT=str(root), HARNESS_ID="eng-001", DISABLE_AUTOUPDATER="1"),
    )
    assert result.returncode == 0, result.stderr
    record = json.loads((root / "run" / "eng-001" / "fake-argv.json").read_text())
    assert record["argv"] == ["--dangerously-skip-permissions", "--model", "claude-opus-5"]
    assert record["cwd"] == str(workdir.resolve())
    assert record["env"]["HARNESS_ID"] == "eng-001"
    assert record["env"]["DISABLE_AUTOUPDATER"] == "1"


def test_it_appends_every_pasted_line(tmp_path):
    root = tmp_path / "instance"
    (root / "run").mkdir(parents=True)
    subprocess.run(
        [str(FAKE_CLAUDE)],
        input="/goal read the work item\nsecond line\n",
        capture_output=True,
        text=True,
        env=clean_env(HARNESS_ROOT=str(root), HARNESS_ID="eng-001"),
    )
    log = (root / "run" / "eng-001" / "fake-input.log").read_text()
    assert log == "/goal read the work item\nsecond line\n"


def test_it_takes_pasted_input_on_a_real_tmux_pane(tmp_path, tmux_server):
    """`hx goal` pastes into window `main` via a tmux buffer (spec 08)."""
    root = tmp_path / "instance"
    (root / "run").mkdir(parents=True)
    env = clean_env(HARNESS_ROOT=str(root), HARNESS_ID="eng-001")
    subprocess.run(
        [*tmux_server, "new-session", "-d", "-s", "eng-001", "-n", "main",
         "-e", f"HARNESS_ROOT={root}", "-e", "HARNESS_ID=eng-001", str(FAKE_CLAUDE)],
        check=True, env=env,
    )
    ready = root / "run" / "eng-001" / "fake-ready"
    wait_for(ready.is_file, what="the fake pane to come up")

    pointer = "/goal The order for eng-001 is in pods/engineers/eng-001-working.md"
    subprocess.run([*tmux_server, "set-buffer", "-b", "hx", pointer], check=True, env=env)
    subprocess.run([*tmux_server, "paste-buffer", "-b", "hx", "-t", "=eng-001:main"], check=True, env=env)
    subprocess.run([*tmux_server, "send-keys", "-t", "=eng-001:main", "Enter"], check=True, env=env)

    log = root / "run" / "eng-001" / "fake-input.log"
    wait_for(lambda: pointer in log.read_text(), what="the pasted pointer to reach the pane")


def test_it_emits_scripted_hook_payloads(tmp_path):
    """Used from M1 to drive `hx-hook` the way a real session would."""
    root = tmp_path / "instance"
    (root / "run").mkdir(parents=True)
    recorder = tmp_path / "recorder.py"
    recorder.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, pathlib\n"
        "payload = json.load(sys.stdin)\n"
        "pathlib.Path(sys.argv[0] + '.seen').write_text(json.dumps({'argv': sys.argv[1:], 'payload': payload}))\n"
        "print('recorded')\n"
    )
    script = tmp_path / "script.json"
    script.write_text(json.dumps([
        {"on": "start", "event": "context", "payload": {"source": "startup"}},
        {"on": "/clear", "event": "context", "payload": {"source": "clear"}},
    ]))

    import sys

    result = subprocess.run(
        [str(FAKE_CLAUDE)],
        input="/clear\n",
        capture_output=True,
        text=True,
        env=clean_env(
            HARNESS_ROOT=str(root),
            HARNESS_ID="eng-001",
            HX_HOOK_BIN=f"{sys.executable} {recorder}",
            HX_FAKE_SCRIPT=str(script),
        ),
    )
    assert result.returncode == 0, result.stderr
    emitted = [
        json.loads(line)
        for line in (root / "run" / "eng-001" / "fake-hooks.log").read_text().splitlines()
    ]
    assert [e["payload"]["source"] for e in emitted] == ["startup", "clear"]
    assert all(e["exit"] == 0 for e in emitted)
    seen = json.loads((tmp_path / "recorder.py.seen").read_text())
    assert seen["argv"] == ["--id", "eng-001", "context"]
    assert seen["payload"]["source"] == "clear"


def test_the_fake_exits_on_its_sentinel(tmp_path):
    root = tmp_path / "instance"
    (root / "run").mkdir(parents=True)
    result = subprocess.run(
        [str(FAKE_CLAUDE)],
        input="/fake-exit\nnever read\n",
        capture_output=True,
        text=True,
        timeout=30,
        env=clean_env(HARNESS_ROOT=str(root), HARNESS_ID="eng-001"),
    )
    assert result.returncode == 0
    assert "never read" not in (root / "run" / "eng-001" / "fake-input.log").read_text()
