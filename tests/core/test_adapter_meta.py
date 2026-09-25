"""The Meta adapter: XDG-isolated home, hook translation, and the shared hook payload."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

from .conftest import clean_env


def _hook_module():
    path = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "hx"
        / "skeleton"
        / "adapters"
        / "meta"
        / "hook.py"
    )
    spec = importlib.util.spec_from_file_location("meta_hook", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hook_translates_camel_case_into_hx_shape():
    hook = _hook_module()
    out = hook.translate(
        {
            "hookEventName": "postToolUse",
            "sessionId": "abc-123",
            "toolName": "run_terminal_command",
            "toolUseId": "call_9",
            "toolInput": {"command": "true"},
            "toolResult": {"exit_code": 0},
        }
    )
    assert out["tool_name"] == "run_terminal_command"
    assert out["tool_use_id"] == "call_9"
    assert out["tool_input"] == {"command": "true"}
    assert out["tool_response"] == {"exit_code": 0}
    assert out["session_id"] == "abc-123"


def test_hook_passes_claude_shape_through():
    hook = _hook_module()
    out = hook.translate(
        {
            "hook_event_name": "SessionStart",
            "source": "startup",
            "session_id": "abc-123",
            "cwd": "/work",
        }
    )
    assert out["session_id"] == "abc-123"
    assert out["source"] == "startup"


def test_hook_end_to_end_writes_the_log_stream(instance):
    from hx.streams import main_stream

    script = (
        Path(__file__).resolve().parents[2]
        / "src"
        / "hx"
        / "skeleton"
        / "adapters"
        / "meta"
        / "hook.py"
    )
    payload = json.dumps(
        {
            "hook_event_name": "PostToolUse",
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
            "--root", str(instance),
            "log",
        ],
        input=payload,
        env=clean_env(),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(main_stream(instance, "eng-001").read_text().splitlines()[-1])
    assert record["tool"] == "read_file"
    assert record["ref"]["tool_use_id"] == "call_1"


def test_seam_slash_follows_the_meta_adapter_file(instance):
    from hx.seam import seam_slash

    harness = instance / "config" / "eng-001" / "harness.json"
    body = json.loads(harness.read_text())
    body["flavor"] = "meta"
    harness.write_text(json.dumps(body))
    assert seam_slash(instance, "eng-001") == "/new"


def _meta_token(instance: Path) -> None:
    token = instance / "seed" / "meta-token"
    token.write_text("meta-test-key\n")
    token.chmod(0o600)


def test_meta_install_writes_an_xdg_home_and_the_companion(instance):
    _meta_token(instance)
    script = instance / "adapters" / "meta" / "install.sh"
    result = subprocess.run(
        ["bash", str(script), "eng-001"],
        env=clean_env(HARNESS_ROOT=str(instance), HX_SKILLS_DIR=str(Path(__file__).resolve().parents[2] / "src" / "hx" / "skills")),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    home = instance / "run" / "eng-001" / "home"
    settings = json.loads((home / "muse" / "settings.json").read_text())
    assert settings["schema_version"] == 1
    assert settings["permissions"]["default_profile"] == ":unrestricted"
    for event in ("SessionStart", "PostToolUse", "Stop"):
        assert settings["hooks"][event], event
    assert "--id eng-001" in (home / "muse" / "settings.json").read_text()
    assert "--root" in (home / "muse" / "settings.json").read_text()
    assert (home / "muse" / "skills" / "hx-worker" / "SKILL.md").is_file()
    companion = instance / "run" / "eng-001" / "companion-home" / "settings.json"
    assert companion.is_file()
    assert "companion-stop" in companion.read_text()


def test_meta_install_no_companion_needs_no_claude_token(instance):
    """`--no-companion` is the `companion.disabled` install path: Muse home only."""
    (instance / "seed" / "token").unlink()
    _meta_token(instance)
    script = instance / "adapters" / "meta" / "install.sh"
    result = subprocess.run(
        ["bash", str(script), "--no-companion", "eng-001"],
        env=clean_env(HARNESS_ROOT=str(instance)),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert (instance / "run" / "eng-001" / "home" / "muse" / "settings.json").is_file()
    assert not (instance / "run" / "eng-001" / "companion-home").exists()


def test_meta_install_refuses_the_partner(instance):
    _meta_token(instance)
    script = instance / "adapters" / "meta" / "install.sh"
    result = subprocess.run(
        ["bash", str(script), "partner"],
        env=clean_env(HARNESS_ROOT=str(instance)),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Partner" in result.stderr


def test_meta_start_exec_is_yolo_bare_and_isolated(instance, tmp_path):
    _meta_token(instance)
    install = subprocess.run(
        ["bash", str(instance / "adapters" / "meta" / "install.sh"), "eng-001"],
        env=clean_env(HARNESS_ROOT=str(instance)),
        capture_output=True,
        text=True,
    )
    assert install.returncode == 0, install.stderr

    recorded = tmp_path / "argv.json"
    fake = tmp_path / "muse"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd(),\n"
        "           'env': {k: os.environ[k] for k in os.environ if k.startswith(('HARNESS', 'XDG_', 'META_'))}},\n"
        "          open(os.environ['HX_ARGV_FILE'], 'w'))\n"
    )
    fake.chmod(0o755)
    result = subprocess.run(
        ["bash", str(instance / "adapters" / "meta" / "start.sh"), "--exec", "eng-001"],
        env=clean_env(
            HARNESS_ROOT=str(instance),
            HX_META_BIN=str(fake),
            HX_ARGV_FILE=str(recorded),
        ),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(recorded.read_text())
    argv = body["argv"]
    assert "--yolo" in argv
    assert "-p" not in argv and "exec" not in argv
    assert not any(arg.startswith("The goal") for arg in argv)
    assert body["env"]["XDG_CONFIG_HOME"] == str(instance / "run" / "eng-001" / "home")
    assert body["env"]["XDG_DATA_HOME"] == str(instance / "run" / "eng-001" / "home" / "data")
    assert body["env"]["META_API_KEY"] == "meta-test-key"
    assert body["env"]["HARNESS_ID"] == "eng-001"
    assert body["cwd"] == str(instance / "wt" / "eng-001")
    persona = instance / "run" / "eng-001" / "persona.md"
    assert "You are eng-001" in persona.read_text()
    assert "Things I learned" not in persona.read_text()


def test_dispatch_wipes_meta_data(instance):
    from hx.config_harness import flavor_of
    from hx.dispatch import _reset_run_dir

    harness = instance / "config" / "eng-001" / "harness.json"
    body = json.loads(harness.read_text())
    body["flavor"] = "meta"
    harness.write_text(json.dumps(body))
    assert flavor_of(instance, "eng-001") == "meta"
    sessions = instance / "run" / "eng-001" / "home" / "data" / "muse" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "old.jsonl").write_text("{}\n")
    kept = instance / "run" / "eng-001" / "home" / "muse" / "settings.json"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("{}\n")
    _reset_run_dir(instance, "eng-001")
    assert not (instance / "run" / "eng-001" / "home" / "data").exists()
    assert kept.is_file()
