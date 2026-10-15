"""The Pi adapter: flavor selection, install, launch argv, and the shared hook payload."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

from .conftest import clean_env


def test_log_hook_uses_context_tokens_from_the_payload(instance):
    from hx.hook_log import handle
    from hx.streams import main_stream

    code, line = handle(
        {
            "tool_name": "read",
            "tool_use_id": "call_1",
            "tool_input": {"path": "/x"},
            "tool_response": {"text": "hello"},
            "context_tokens": 12345,
        },
        "partner",
        instance,
        env=clean_env(HX_TMUX="tmux -L hx-adapter-test-isolated"),
    )
    assert code == 0 and line == ""
    record = json.loads(main_stream(instance, "partner").read_text().splitlines()[-1])
    assert record["context_tokens"] == 12345
    assert record["tool"] == "read"


def test_seam_slash_follows_the_adapter_file(instance):
    from hx.seam import seam_slash

    assert seam_slash(instance, "eng-001") == "/clear"
    harness = instance / "config" / "eng-001" / "harness.json"
    body = json.loads(harness.read_text())
    body["flavor"] = "pi"
    harness.write_text(json.dumps(body))
    assert seam_slash(instance, "eng-001") == "/new"


def _pi_token(instance: Path) -> None:
    token = instance / "seed" / "pi-token"
    token.write_text("sk-pi-test\n")
    token.chmod(0o600)


def test_pi_install_writes_a_home_and_the_companion(instance):
    _pi_token(instance)
    script = instance / "adapters" / "pi" / "install.sh"
    result = subprocess.run(
        ["bash", str(script), "eng-001"],
        env=clean_env(HARNESS_ROOT=str(instance), HX_SKILLS_DIR=str(Path(__file__).resolve().parents[2] / "src" / "hx" / "skills")),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    home = instance / "run" / "eng-001" / "home"
    settings = json.loads((home / "settings.json").read_text())
    assert settings["defaultProjectTrust"] == "never"
    assert settings["compaction"]["reserveTokens"] == 750000
    auth = json.loads((home / "auth.json").read_text())
    assert auth["anthropic"]["type"] == "api_key"
    assert auth["anthropic"]["key"] == "sk-pi-test"
    assert stat.S_IMODE((home / "auth.json").stat().st_mode) == 0o600
    assert (home / "extensions" / "hx" / "index.ts").is_file()
    assert (home / "skills" / "hx-worker" / "SKILL.md").is_file()
    from .conftest import installed_skills, manifest_skills
    assert installed_skills(home / "skills") == manifest_skills(instance, "eng-001")
    companion = instance / "run" / "eng-001" / "companion-home" / "settings.json"
    assert companion.is_file()
    assert "companion-stop" in companion.read_text()
    assert not (home / ".claude.json").exists()


def test_pi_install_refuses_the_partner(instance):
    _pi_token(instance)
    script = instance / "adapters" / "pi" / "install.sh"
    result = subprocess.run(
        ["bash", str(script), "partner"],
        env=clean_env(HARNESS_ROOT=str(instance)),
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Partner" in result.stderr
