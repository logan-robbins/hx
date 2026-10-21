"""The Codex adapter: unattended launch, hook translation, and the shared hook payload."""

from __future__ import annotations

import importlib.util
import json
import os
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
            "--id", "partner",
            "--hook-bin", f"{sys.executable} -m hx.hooks",
            "--root", str(instance),
            "log",
        ],
        input=json.dumps(body),
        env=clean_env(HX_TMUX="tmux -L hx-adapter-test-isolated", PYTHONPATH=str(Path(__file__).resolve().parents[2] / "src")),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    line = (instance / "logs" / "partner" / "partner-main.jsonl").read_text().strip()
    record = json.loads(line)
    assert record["tool"] == "shell"
    assert record["ref"]["tool_use_id"] == "call_7"


def _codex_hook():
    return (
        Path(__file__).resolve().parents[2]
        / "src"
        / "hx"
        / "skeleton"
        / "adapters"
        / "codex"
        / "hook.py"
    )


def test_hook_translates_a_guard_denial_into_the_codex_deny_shape(tmp_path):
    """Exit 2 + stderr (the hx-hook guard contract) additionally goes out as
    Codex's `permissionDecision` deny JSON, which is what every version honors."""
    fake = tmp_path / "deny-bin"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stderr.write('hx guard: denied: nope\\n')\n"
        "sys.exit(2)\n"
    )
    fake.chmod(0o755)
    result = subprocess.run(
        [sys.executable, str(_codex_hook()),
         "--id", "partner", "--hook-bin", str(fake), "--root", "/tmp", "guard"],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "x"}}),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "hx guard: denied: nope" in result.stderr
    assert json.loads(result.stdout) == {"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": "hx guard: denied: nope",
    }}


def test_hook_passes_a_guard_allow_through_untouched(tmp_path):
    """An allowed call stays silent: no JSON, so nothing is consumed as a decision."""
    fake = tmp_path / "allow-bin"
    fake.write_text("#!/usr/bin/env python3\n")
    fake.chmod(0o755)
    result = subprocess.run(
        [sys.executable, str(_codex_hook()),
         "--id", "partner", "--hook-bin", str(fake), "--root", "/tmp", "guard"],
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "hx board"}}),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert result.stdout == "" and result.stderr == ""


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


def _no_login_home(tmp_path: Path) -> Path:
    """A HOME with no Codex login, so install takes the API-key path.

    The real HOME may hold a login, which would flip install.sh onto the login
    path and pollute the test with real credentials — never inherit it here.
    """
    home = tmp_path / "no-login-home"
    home.mkdir(parents=True, exist_ok=True)
    return home


def _login_home(tmp_path: Path) -> Path:
    """A HOME holding a fabricated ChatGPT login (no real credentials)."""
    home = tmp_path / "login-home"
    dot = home / ".codex"
    dot.mkdir(parents=True, exist_ok=True)
    auth = dot / "auth.json"
    auth.write_text('{"auth_mode": "chatgpt", "tokens": {"refresh_token": "test"}}\n')
    auth.chmod(0o600)
    return home


def test_codex_install_writes_home_and_provisions_auth(instance, tmp_path):
    _codex_token(instance)
    script = instance / "adapters" / "codex" / "install.sh"
    result = subprocess.run(
        ["bash", str(script), "eng-001"],
        env=clean_env(
            HARNESS_ROOT=str(instance),
            HOME=str(_no_login_home(tmp_path)),
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
    workdir = json.loads((instance / "config" / "eng-001" / "harness.json").read_text())["workdir"]
    assert config["projects"][workdir]["trust_level"] == "trusted"
    assert (home / "auth.json").is_file(), "login --with-api-key provisions the home auth"
    assert (home / "skills" / "hx-worker" / "SKILL.md").is_file()
    from .conftest import installed_skills, manifest_skills
    assert installed_skills(home / "skills") == manifest_skills(instance, "eng-001")


def test_codex_install_accepts_the_partner_with_partner_skills(instance, tmp_path):
    """The Partner may run on Codex (spec 12): partner skills."""
    _codex_token(instance)
    script = instance / "adapters" / "codex" / "install.sh"
    result = subprocess.run(
        ["bash", str(script), "partner"],
        env=clean_env(
            HARNESS_ROOT=str(instance),
            HOME=str(_no_login_home(tmp_path)),
            HX_CODEX_BIN=str(_fake_codex(tmp_path)),
            HX_SKILLS_DIR=str(Path(__file__).resolve().parents[2] / "src" / "hx" / "skills"),
        ),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    home = instance / "run" / "partner" / "home"
    assert (home / "config.toml").is_file()
    assert (home / "auth.json").is_file()
    assert (home / "skills" / "hx-partner" / "SKILL.md").is_file()
    assert (home / "skills" / "hx-fleet" / "SKILL.md").is_file()
    assert not (home / "skills" / "hx-worker").exists(), "workers get hx-worker, the Partner does not"
    from .conftest import installed_skills, manifest_skills
    assert installed_skills(home / "skills") == manifest_skills(instance, "partner")
    config = tomllib.loads((home / "config.toml").read_text())
    guards = config["hooks"].get("PreToolUse", [])
    assert len(guards) == 1 and 'matcher' not in guards[0]
    assert "guard" in guards[0]["hooks"][0]["command"]
    assert config['hooks']['SubagentStart'] and config['hooks']['SubagentStop']
    result_hooks = [entry for entry in config['hooks']['PostToolUse']
                    if any('subagent-result' in hook['command'] for hook in entry['hooks'])]
    from hx.file_read_delivery import segment_slots
    assert len(result_hooks) == segment_slots(instance, 'partner')
    assert all(hook['matcher'] == 'Agent|spawn_agent' for hook in result_hooks)
    assert config["projects"][str(instance)]["trust_level"] == "trusted"


def test_codex_worker_has_no_pre_tool_use_hook(instance, tmp_path):
    """The guard is the Partner's alone: no worker is ever guarded (spec 09.1)."""
    _codex_token(instance)
    result = subprocess.run(
        ["bash", str(instance / "adapters" / "codex" / "install.sh"), "eng-001"],
        env=clean_env(
            HARNESS_ROOT=str(instance),
            HOME=str(_no_login_home(tmp_path)),
            HX_CODEX_BIN=str(_fake_codex(tmp_path)),
            HX_SKILLS_DIR=str(Path(__file__).resolve().parents[2] / "src" / "hx" / "skills"),
        ),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    config = tomllib.loads((instance / "run" / "eng-001" / "home" / "config.toml").read_text())
    assert "PreToolUse" not in config["hooks"]


def test_codex_start_exec_runs_the_partner_in_the_root(instance, tmp_path):
    """No workdir on the Partner harness: the session runs in HARNESS_ROOT (spec 12)."""
    _codex_token(instance)
    install = subprocess.run(
        ["bash", str(instance / "adapters" / "codex" / "install.sh"), "partner"],
        env=clean_env(
            HARNESS_ROOT=str(instance),
            HOME=str(_no_login_home(tmp_path)),
            HX_CODEX_BIN=str(_fake_codex(tmp_path)),
        ),
        capture_output=True,
        text=True,
    )
    assert install.returncode == 0, install.stderr

    recorded = tmp_path / "argv.json"
    result = subprocess.run(
        ["bash", str(instance / "adapters" / "codex" / "start.sh"), "--exec", "partner"],
        env=clean_env(
            HARNESS_ROOT=str(instance),
            HOME=str(_no_login_home(tmp_path)),
            HX_CODEX_BIN=str(_fake_codex(tmp_path)),
            HX_ARGV_FILE=str(recorded),
        ),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    body = json.loads(recorded.read_text())
    assert "-a" not in body["argv"], "the CLI rejects -a with the bypass flag (0.157)"
    assert body["env"]["CODEX_HOME"] == str(instance / "run" / "partner" / "home")
    assert body["env"]["HARNESS_ID"] == "partner"
    assert body["cwd"] == str(instance)




def test_codex_install_prefers_the_chatgpt_login_over_the_api_key(instance, tmp_path):
    """The invoker's login wins: copied into the home, `login` never invoked."""
    login_home = _login_home(tmp_path)
    never = tmp_path / "must-not-run"
    never.write_text("#!/usr/bin/env python3\nimport sys; sys.exit(1)\n")
    never.chmod(0o755)
    script = instance / "adapters" / "codex" / "install.sh"
    result = subprocess.run(
        ["bash", str(script), "eng-001"],
        env=clean_env(
            HARNESS_ROOT=str(instance),
            HOME=str(login_home),
            HX_CODEX_BIN=str(never),
            HX_SKILLS_DIR=str(Path(__file__).resolve().parents[2] / "src" / "hx" / "skills"),
        ),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ChatGPT login" in result.stdout
    copied = instance / "run" / "eng-001" / "home" / "auth.json"
    assert copied.read_text() == (login_home / ".codex" / "auth.json").read_text()
    assert os.stat(copied).st_mode & 0o77 == 0
