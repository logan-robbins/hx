"""The Codex adapter: unattended launch, hook translation, and the shared hook payload."""

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
        / "codex"
        / "hook.py"
    )
    spec = importlib.util.spec_from_file_location("codex_hook", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hook_translates_camel_case_into_hx_shape():
    module = _hook_module()
    body = {
        "sessionId": "abc123",
        "toolName": "shell",
        "toolUseId": "call_7",
        "toolInput": {"command": "ls"},
        "toolResult": "ok",
    }
    out = module.translate(body)
    assert out["session_id"] == "abc123"
    assert out["tool_name"] == "shell"
    assert out["tool_use_id"] == "call_7"
    assert out["tool_input"] == {"command": "ls"}
    assert out["tool_response"] == "ok"


def test_hook_end_to_end_writes_the_log_stream(instance):
    hook = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "hx"
        / "skeleton"
        / "adapters"
        / "codex"
        / "hook.py"
    )
    body = {
        "session_id": "abc123",
        "hook_event_name": "PostToolUse",
        "tool_name": "shell",
        "tool_use_id": "call_7",
        "tool_input": {"command": "ls"},
        "tool_response": "ok",
        "cwd": str(instance),
    }
    result = subprocess.run(
        [
            sys.executable, str(hook),
            "--id", "eng-001",
            "--hook-bin", f"{sys.executable} -m hx.hooks",
            "--root", str(instance),
            "log",
        ],
        input=json.dumps(body),
        env=clean_env(PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src")),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    line = (instance / "logs" / "eng-001" / "eng-001-main.jsonl").read_text().strip()
    record = json.loads(line)
    assert record["tool"] == "shell"
    assert record["ref"]["tool_use_id"] == "call_7"


def test_seam_slash_follows_the_codex_adapter_file(instance):
    from hx.seam import seam_slash

    harness = instance / "config" / "eng-001" / "harness.json"
    body = json.loads(harness.read_text())
    body["flavor"] = "codex"
    harness.write_text(json.dumps(body))
    assert seam_slash(instance, "eng-001") == "/new"


def _codex_token(instance: Path) -> None:
    token = instance / "seed" / "codex-token"
    token.write_text("sk-test-key\n")
    token.chmod(0o600)


def _fake_codex(tmp_path: Path, mode: str = "record") -> Path:
    """A fake `codex`: `login --with-api-key` provisions auth, anything else records argv."""
    fake = tmp_path / "codex"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "if len(sys.argv) > 2 and sys.argv[1] == 'login' and sys.argv[2] == '--with-api-key':\n"
        "    home = os.environ['CODEX_HOME']\n"
        "    open(os.path.join(home, 'auth.json'), 'w').write('{\"api_key\": \"test\"}')\n"
        "    sys.exit(0)\n"
        "json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd(),\n"
        "           'env': {k: os.environ[k] for k in os.environ if k.startswith(('HARNESS', 'CODEX_', 'OPENAI_'))}},\n"
        "          open(os.environ['HX_ARGV_FILE'], 'w'))\n"
    )
    fake.chmod(0o755)
    return fake


def test_codex_install_writes_home_and_provisions_auth(instance, tmp_path):
    _codex_token(instance)
    script = instance / "adapters" / "codex" / "install.sh"
    result = subprocess.run(
        ["bash", str(script), "eng-001"],
        env=clean_env(
            HARNESS_ROOT=str(instance),
            HX_CODEX_BIN=str(_fake_codex(tmp_path)),
            HX_SKILLS_DIR=str(Path(__file__).resolve().parents[2] / "src" / "hx" / "skills"),
        ),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    home = instance / "run" / "eng-001" / "home"
    config = tomllib.loads((home / "config.toml").read_text())
    assert config["approval_policy"] == "never"
    assert config["sandbox_mode"] == "danger-full-access"
    for event in (
        "SessionStart", "PostToolUse", "Stop",
        "PreCompact", "PostCompact", "SubagentStart", "SubagentStop",
    ):
        assert config["hooks"][event], event
    assert "--id eng-001" in (home / "config.toml").read_text()
    assert (home / "auth.json").is_file(), "login --with-api-key provisions the home auth"
    assert (home / "skills" / "hx-worker" / "SKILL.md").is_file()
    companion = instance / "run" / "eng-001" / "companion-home" / "settings.json"
    assert companion.is_file()
    assert "companion-stop" in companion.read_text()


def test_codex_install_refuses_the_partner(instance):
    _codex_token(instance)
    script = instance / "adapters" / "codex" / "install.sh"
    result = subprocess.run(
        ["bash", str(script), "partner"],
        env=clean_env(HARNESS_ROOT=str(instance)),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Partner" in result.stderr


def test_codex_start_exec_is_bare_unattended_and_isolated(instance, tmp_path):
    _codex_token(instance)
    harness = instance / "config" / "eng-001" / "harness.json"
    body = json.loads(harness.read_text())
    body["model"] = "gpt-6-sol"
    body["effort"] = "xhigh"
    harness.write_text(json.dumps(body))
    install = subprocess.run(
        ["bash", str(instance / "adapters" / "codex" / "install.sh"), "eng-001"],
        env=clean_env(HARNESS_ROOT=str(instance), HX_CODEX_BIN=str(_fake_codex(tmp_path))),
        capture_output=True,
        text=True,
    )
    assert install.returncode == 0, install.stderr

    recorded = tmp_path / "argv.json"
    result = subprocess.run(
        ["bash", str(instance / "adapters" / "codex" / "start.sh"), "--exec", "eng-001"],
        env=clean_env(
            HARNESS_ROOT=str(instance),
            HX_CODEX_BIN=str(_fake_codex(tmp_path)),
            HX_ARGV_FILE=str(recorded),
        ),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(recorded.read_text())
    argv = body["argv"]
    assert "--dangerously-bypass-approvals-and-sandbox" in argv
    assert "--dangerously-bypass-hook-trust" in argv
    assert argv[argv.index("-m") + 1] == "gpt-6-sol"
    assert argv[argv.index("-c") + 1] == 'model_reasoning_effort="xhigh"'
    assert argv == [
        "--dangerously-bypass-approvals-and-sandbox",
        "--dangerously-bypass-hook-trust",
        "-a", "never",
        "-s", "danger-full-access",
        "-m", "gpt-6-sol",
        "-c", 'model_reasoning_effort="xhigh"',
    ], "bare: no prompt argument, flags only"
    assert body["env"]["CODEX_HOME"] == str(instance / "run" / "eng-001" / "home")
    assert body["env"]["HARNESS_ID"] == "eng-001"
    assert body["cwd"] == str(instance / "wt" / "eng-001")


def test_dispatch_wipes_codex_sessions(instance):
    from hx.config_harness import flavor_of
    from hx.dispatch import _reset_run_dir

    harness = instance / "config" / "eng-001" / "harness.json"
    body = json.loads(harness.read_text())
    body["flavor"] = "codex"
    harness.write_text(json.dumps(body))
    assert flavor_of(instance, "eng-001") == "codex"

    sessions = instance / "run" / "eng-001" / "home" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "stale.json").write_text("{}\n")
    kept = instance / "run" / "eng-001" / "home" / "config.toml"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("{}\n")
    _reset_run_dir(instance, "eng-001")
    assert not sessions.exists()
    assert kept.is_file()
