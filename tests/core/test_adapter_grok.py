"""The Grok adapter: minimal-screen launch, hook translation, and the shared hook payload."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tomllib
from pathlib import Path

from .conftest import clean_env


def _hook_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "hx"
        / "skeleton"
        / "adapters"
        / "grok"
        / "hook.py"
    )
    spec = importlib.util.spec_from_file_location("grok_hook", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hook_translates_camel_case_into_hx_shape():
    hook = _hook_module()
    out = hook.translate(
        {
            "hookEventName": "post_tool_use",
            "hook_event_name": "PostToolUse",
            "sessionId": "abc-123",
            "toolName": "run_terminal_command",
            "toolUseId": "call_9",
            "toolInput": {"command": "true"},
            "toolResult": {"type": "Bash", "exit_code": 0},
        }
    )
    assert out["tool_name"] == "run_terminal_command"
    assert out["tool_use_id"] == "call_9"
    assert out["tool_input"] == {"command": "true"}
    assert out["tool_response"] == {"type": "Bash", "exit_code": 0}
    assert out["session_id"] == "abc-123"
    # Unknown keys pass through; hx-hook ignores what it does not read.
    assert out["hookEventName"] == "post_tool_use"


def test_hook_end_to_end_writes_the_log_stream(instance):
    from hx.streams import main_stream

    script = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "hx"
        / "skeleton"
        / "adapters"
        / "grok"
        / "hook.py"
    )
    payload = json.dumps(
        {
            "hookEventName": "post_tool_use",
            "sessionId": "abc-123",
            "toolName": "read_file",
            "toolUseId": "call_1",
            "toolInput": {"path": "/x"},
            "toolResult": {"text": "hello"},
        }
    )
    result = subprocess.run(
        [
            sys.executable, str(script),
            "--id", "eng-001",
            "--hook-bin", f"{sys.executable} -m hx.hooks",
            "log",
        ],
        input=payload,
        env=clean_env(HARNESS_ROOT=str(instance)),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(main_stream(instance, "eng-001").read_text().splitlines()[-1])
    assert record["tool"] == "read_file"
    assert record["ref"]["tool_use_id"] == "call_1"


def test_seam_slash_follows_the_grok_adapter_file(instance):
    from hx.seam import seam_slash

    harness = instance / "config" / "eng-001" / "harness.json"
    body = json.loads(harness.read_text())
    body["flavor"] = "grok"
    harness.write_text(json.dumps(body))
    assert seam_slash(instance, "eng-001") == "/new"


def _grok_token(instance: Path) -> None:
    token = instance / "seed" / "grok-token"
    token.write_text("xai-test-key\n")
    token.chmod(0o600)


def test_grok_install_writes_a_minimal_home_and_the_companion(instance):
    _grok_token(instance)
    script = instance / "adapters" / "grok" / "install.sh"
    result = subprocess.run(
        ["bash", str(script), "eng-001"],
        env=clean_env(HARNESS_ROOT=str(instance), HX_SKILLS_DIR=str(Path(__file__).resolve().parents[2] / "src" / "hx" / "skills")),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    home = instance / "run" / "eng-001" / "home"
    config = tomllib.loads((home / "config.toml").read_text())
    assert config["ui"]["screen_mode"] == "minimal"
    assert config["ui"]["permission_mode"] == "always-approve"
    assert config["goal"]["enabled"] is True
    for event in ("SessionStart", "PostToolUse", "Stop"):
        assert config["hooks"][event], event
    assert "--id eng-001" in (home / "config.toml").read_text()
    assert (home / "skills" / "hx-worker" / "SKILL.md").is_file()
    companion = instance / "run" / "eng-001" / "companion-home" / "settings.json"
    assert companion.is_file()
    assert "companion-stop" in companion.read_text()
    assert not (home / "auth.json").exists()


def test_grok_install_refuses_the_partner(instance):
    _grok_token(instance)
    script = instance / "adapters" / "grok" / "install.sh"
    result = subprocess.run(
        ["bash", str(script), "partner"],
        env=clean_env(HARNESS_ROOT=str(instance)),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Partner" in result.stderr


def test_grok_start_exec_is_minimal_bare_and_isolated(instance, tmp_path):
    _grok_token(instance)
    install = subprocess.run(
        ["bash", str(instance / "adapters" / "grok" / "install.sh"), "eng-001"],
        env=clean_env(HARNESS_ROOT=str(instance)),
        capture_output=True,
        text=True,
    )
    assert install.returncode == 0, install.stderr

    recorded = tmp_path / "argv.json"
    fake = tmp_path / "grok"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd(),\n"
        "           'env': {k: os.environ[k] for k in os.environ if k.startswith(('HARNESS', 'GROK_', 'XAI_'))}},\n"
        "          open(os.environ['HX_ARGV_FILE'], 'w'))\n"
    )
    fake.chmod(0o755)
    result = subprocess.run(
        ["bash", str(instance / "adapters" / "grok" / "start.sh"), "--exec", "eng-001"],
        env=clean_env(
            HARNESS_ROOT=str(instance),
            HX_GROK_BIN=str(fake),
            HX_ARGV_FILE=str(recorded),
        ),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(recorded.read_text())
    argv = body["argv"]
    assert "--minimal" in argv
    assert "--fullscreen" not in argv
    assert argv[argv.index("--permission-mode") + 1] == "bypassPermissions"
    assert "-p" not in argv and "--print" not in argv
    assert not any(arg.startswith("The goal") for arg in argv)
    assert "--rules" in argv
    assert "You are eng-001" in argv[argv.index("--rules") + 1]
    assert "Things I learned" not in argv[argv.index("--rules") + 1]
    assert body["env"]["GROK_HOME"] == str(instance / "run" / "eng-001" / "home")
    assert body["env"]["XAI_API_KEY"] == "xai-test-key"
    assert body["env"]["HARNESS_ID"] == "eng-001"
    assert body["cwd"] == str(instance / "wt" / "eng-001")


def test_grok_start_refuses_fullscreen_override(instance):
    assert "--fullscreen" not in (instance / "adapters" / "grok" / "start.sh").read_text()


def test_dispatch_wipes_grok_sessions(instance):
    from hx.config_harness import flavor_of
    from hx.dispatch import _reset_run_dir

    harness = instance / "config" / "eng-001" / "harness.json"
    body = json.loads(harness.read_text())
    body["flavor"] = "grok"
    harness.write_text(json.dumps(body))
    assert flavor_of(instance, "eng-001") == "grok"
    sessions = instance / "run" / "eng-001" / "home" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "old.jsonl").write_text("{}\n")
    kept = instance / "run" / "eng-001" / "home" / "config.toml"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("{}\n")
    _reset_run_dir(instance, "eng-001")
    assert not sessions.exists()
    assert kept.is_file()
