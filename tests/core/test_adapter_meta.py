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


def test_model_response_projects_before_the_hook_pipe(tmp_path):
    script = Path(_hook_module().__file__)
    received = tmp_path / "received.json"
    receiver = tmp_path / "receive.py"
    receiver.write_text("import sys,pathlib\npathlib.Path(" + repr(str(received)) + ").write_bytes(sys.stdin.buffer.read())\n")
    payload = {"hook_event_name": "PostLLMCall", "session_id": "S", "turn_id": "T", "request_id": "T:0:1",
        "status": "failed", "error": "Provider unavailable", "usage": {"input_tokens": 12, "extra": "PRIVATE"},
        "messages": [{"text": "PRIVATE" * 20000}], "tools": [{"description": "PRIVATE"}],
        "options": {"private": "PRIVATE"}, "output_text_preview": "PRIVATE"}
    import shlex
    result = subprocess.run([sys.executable, str(script), "--id", "eng-001", "--root", str(tmp_path),
        "--hook-bin", shlex.join([sys.executable, str(receiver)]), "model-response"],
        input=json.dumps(payload), capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    wire = received.read_bytes()
    assert b"PRIVATE" not in wire and len(wire) < 1000
    public = json.loads(wire)
    assert public["request_id"] == "T:0:1" and public["error"] == "Provider unavailable"
    assert public["usage"] == {"input_tokens": 12}


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
            "--id", "partner",
            "--hook-bin", f"{sys.executable} -m hx.hooks",
            "--root", str(instance),
            "log",
        ],
        input=payload,
        env=clean_env(HX_TMUX="tmux -L hx-adapter-test-isolated", ),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    record = json.loads(main_stream(instance, "partner").read_text().splitlines()[-1])
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
    from .conftest import installed_skills, manifest_skills
    assert installed_skills(home / "muse" / "skills") == manifest_skills(instance, "eng-001")
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
