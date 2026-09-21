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
        "eng-001",
        instance,
    )
    assert code == 0 and line == ""
    record = json.loads(main_stream(instance, "eng-001").read_text().splitlines()[-1])
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


def test_pi_start_exec_is_bare_and_isolated(instance, tmp_path):
    _pi_token(instance)
    install = subprocess.run(
        ["bash", str(instance / "adapters" / "pi" / "install.sh"), "eng-001"],
        env=clean_env(HARNESS_ROOT=str(instance)),
        capture_output=True,
        text=True,
    )
    assert install.returncode == 0, install.stderr

    recorded = tmp_path / "argv.json"
    fake = tmp_path / "pi"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, sys\n"
        "json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd(),\n"
        "           'env': {k: os.environ[k] for k in os.environ if k.startswith(('HARNESS', 'PI_'))}},\n"
        "          open(os.environ['HX_ARGV_FILE'], 'w'))\n"
    )
    fake.chmod(0o755)
    result = subprocess.run(
        ["bash", str(instance / "adapters" / "pi" / "start.sh"), "--exec", "eng-001"],
        env=clean_env(
            HARNESS_ROOT=str(instance),
            HX_PI_BIN=str(fake),
            HX_ARGV_FILE=str(recorded),
        ),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(recorded.read_text())
    argv = body["argv"]
    assert "--no-approve" in argv
    assert "--no-extensions" in argv
    assert "--no-context-files" in argv
    assert "-p" not in argv and "--print" not in argv
    assert not any(arg.startswith("The goal") for arg in argv)
    persona = str(instance / "run" / "eng-001" / "persona.md")
    assert argv[argv.index("--append-system-prompt") + 1] == persona
    assert "You are eng-001" in Path(persona).read_text()
    assert "Things I learned" not in Path(persona).read_text()
    assert body["env"]["PI_CODING_AGENT_DIR"] == str(instance / "run" / "eng-001" / "home")
    assert body["env"]["HARNESS_ID"] == "eng-001"
    assert body["cwd"] == str(instance / "wt" / "eng-001")


def test_dispatch_wipes_pi_sessions(instance):
    from hx.config_harness import flavor_of
    from hx.dispatch import _reset_run_dir

    harness = instance / "config" / "eng-001" / "harness.json"
    body = json.loads(harness.read_text())
    body["flavor"] = "pi"
    harness.write_text(json.dumps(body))
    assert flavor_of(instance, "eng-001") == "pi"
    sessions = instance / "run" / "eng-001" / "home" / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "old.jsonl").write_text("{}\n")
    kept = instance / "run" / "eng-001" / "home" / "settings.json"
    kept.parent.mkdir(parents=True, exist_ok=True)
    kept.write_text("{}\n")
    _reset_run_dir(instance, "eng-001")
    assert not sessions.exists()
    assert kept.is_file()
