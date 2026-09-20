"""`adapters/claude/install.sh` — the per-agent home (spec 09, 11, 17.3).

M0 pass criterion: "`run/<id>/home/settings.json` validates: hooks present with the right
`--id`, instruction-files mode `claude-md`, `claudeMdExcludes` set".

Settings keys verified against code.claude.com/docs/en/settings-reference,
/docs/en/memory#choose-which-instruction-files-load, /docs/en/cross-session-messaging and
/docs/en/hooks on 2026-09-20; the URLs are recorded in goals/build-1.done.md.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from .conftest import clean_env

#: spec 09.1, `<hx-hook> --id <id> <event>`.
HX_EVENTS = {
    "context", "guard", "log", "subagent-start", "subagent-stop",
    "subagent-result", "stop", "precompact", "postcompact",
}
PARTNER_EVENTS = HX_EVENTS - {"subagent-start", "subagent-stop", "subagent-result"}


def run_install(instance, item_id, **env_extra):
    script = instance / "adapters" / "claude" / "install.sh"
    return subprocess.run(
        ["bash", str(script), item_id],
        env=clean_env(HARNESS_ROOT=str(instance), **env_extra),
        capture_output=True,
        text=True,
    )


def settings_for(instance, item_id):
    return json.loads((instance / "run" / item_id / "home" / "settings.json").read_text())


def hook_commands(settings):
    commands = []
    for entries in settings["hooks"].values():
        for entry in entries:
            for hook in entry["hooks"]:
                commands.append(hook["command"])
    return commands


@pytest.fixture
def installed(instance):
    result = run_install(instance, "eng-001")
    assert result.returncode == 0, result.stderr
    return instance


# --- refusals ------------------------------------------------------------------------------


def test_refuses_without_seed_credentials(instance):
    (instance / "seed" / "home" / ".credentials.json").unlink()
    result = run_install(instance, "eng-001")
    assert result.returncode != 0
    assert "refuse" in result.stderr
    assert "seed" in result.stderr
    assert not (instance / "run" / "eng-001" / "home" / "settings.json").exists()


def test_refuses_a_non_id(instance):
    result = run_install(instance, "Engineering")
    assert result.returncode != 0 and "refuse" in result.stderr


# --- hooks ----------------------------------------------------------------------------------


def test_every_spec_09_event_is_wired_with_the_right_id(installed):
    settings = settings_for(installed, "eng-001")
    commands = hook_commands(settings)
    events = {command.rsplit(" ", 1)[1] for command in commands}
    assert events == HX_EVENTS
    for command in commands:
        assert "--id eng-001" in command, command


def test_the_hook_binary_defaults_to_bin_hx_hook(instance):
    """Spec 09: every hook command is `<root>/bin/hx-hook --id <id> <event>`.

    The fallback, used when `hx install` has not recorded `config/hx.json` (CONTRACTS.md).
    """
    (instance / "config" / "hx.json").unlink()
    assert run_install(instance, "eng-001").returncode == 0
    for command in hook_commands(settings_for(instance, "eng-001")):
        assert command.startswith(str(instance / "bin" / "hx-hook"))


def test_the_hook_binary_comes_from_config_hx_json_when_recorded(installed):
    """`hx install --skeleton-only` records it, so a real instance uses the real path."""
    import json as _json

    recorded = _json.loads((installed / "config" / "hx.json").read_text())["hook_bin"]
    assert recorded
    for command in hook_commands(settings_for(installed, "eng-001")):
        assert command.startswith(recorded)


def test_config_hx_json_overrides_the_hook_binary(instance):
    """CONTRACTS.md `config/hx.json`: `{"hx_bin", "hook_bin"}`."""
    (instance / "config" / "hx.json").write_text(
        json.dumps({"hx_bin": "/opt/hx/bin/hx", "hook_bin": "/opt/hx/bin/hx-hook"})
    )
    assert run_install(instance, "eng-001").returncode == 0
    for command in hook_commands(settings_for(instance, "eng-001")):
        assert command.startswith("/opt/hx/bin/hx-hook ")


def test_the_hook_events_map_to_the_claude_code_events_of_spec_09(installed):
    settings = settings_for(installed, "eng-001")
    by_event = {}
    for claude_event, entries in settings["hooks"].items():
        for entry in entries:
            for hook in entry["hooks"]:
                by_event[hook["command"].rsplit(" ", 1)[1]] = (claude_event, entry.get("matcher"))

    assert by_event["context"] == ("SessionStart", "startup|resume|clear|compact")
    assert by_event["guard"] == ("PreToolUse", "*")
    assert by_event["log"] == ("PostToolUse", "*")
    assert by_event["subagent-result"] == ("PostToolUse", "Agent")
    assert by_event["subagent-start"][0] == "SubagentStart"
    assert by_event["subagent-stop"][0] == "SubagentStop"
    assert by_event["stop"] == ("Stop", None), "Stop takes no matcher (docs/en/hooks)"
    assert by_event["precompact"][0] == "PreCompact"
    assert by_event["postcompact"][0] == "PostCompact"


def test_the_partner_gets_no_subagent_hooks(instance):
    """Spec 09.1 marks the three subagent events "Non-Partner"."""
    assert run_install(instance, "partner").returncode == 0
    commands = hook_commands(settings_for(instance, "partner"))
    assert {c.rsplit(" ", 1)[1] for c in commands} == PARTNER_EVENTS


# --- instruction files, excludes, permissions, messaging --------------------------------------


def test_instruction_files_mode_is_claude_md(installed):
    """docs/en/memory: the value lives under the built-in `agents-md` plugin's pluginConfigs."""
    settings = settings_for(installed, "eng-001")
    assert settings["pluginConfigs"]["agents-md@builtin"]["options"]["instructionFiles"] == "claude-md"


def test_claude_md_excludes_cover_the_product_repo(installed):
    excludes = settings_for(installed, "eng-001")["claudeMdExcludes"]
    root = str(installed)
    assert f"{root}/wt/**/CLAUDE.md" in excludes
    assert f"{root}/wt/**/AGENTS.md" in excludes
    assert f"{root}/repos/**/CLAUDE.md" in excludes
    # The home's own CLAUDE.md, which is the one that must load, is never excluded.
    assert all(not pattern.startswith(f"{root}/run/") for pattern in excludes)
    assert all(pattern.startswith(root) for pattern in excludes), "patterns are absolute globs"


def test_the_bypass_acceptance_is_written(installed):
    """Every agent runs bypass permissions; no launch is ever interactive (spec 05, 11)."""
    assert settings_for(installed, "eng-001")["skipDangerousModePermissionPrompt"] is True


def test_cross_session_inbound_is_the_partners_alone(instance):
    """Spec 05, 11, 17.3 scope `crossSessionInbound: accept` to the Partner."""
    assert run_install(instance, "partner").returncode == 0
    assert run_install(instance, "eng-001").returncode == 0
    assert settings_for(instance, "partner")["crossSessionInbound"] == "accept"
    assert "crossSessionInbound" not in settings_for(instance, "eng-001")


def test_permissions_default_mode_is_not_used(installed):
    """Spec 11: it is ignored in project/local scope and would silently degrade to manual."""
    assert "permissions" not in settings_for(installed, "eng-001")


# --- credentials, CLAUDE.md, skills -------------------------------------------------------------


def test_credentials_are_seeded_from_seed_home(installed):
    home = installed / "run" / "eng-001" / "home"
    assert json.loads((home / ".credentials.json").read_text()) == {"fake": "credentials"}
    assert (home / ".credentials.json").stat().st_mode & 0o777 == 0o600


def test_the_one_claude_md_is_installed_into_the_home(instance):
    (instance / "config" / "CLAUDE.md").write_text("# the one CLAUDE.md\n")
    assert run_install(instance, "eng-001").returncode == 0
    assert (instance / "run" / "eng-001" / "home" / "CLAUDE.md").read_text() == "# the one CLAUDE.md\n"


def test_skills_are_copied_per_role(instance, tmp_path):
    """Spec 17.5: `hx-partner` or `hx-worker` only, copied, never symlinked."""
    skills = tmp_path / "skills"
    for name in ("hx-partner", "hx-worker"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")

    assert run_install(instance, "eng-001", HX_SKILLS_DIR=str(skills)).returncode == 0
    assert run_install(instance, "partner", HX_SKILLS_DIR=str(skills)).returncode == 0

    worker_skills = instance / "run" / "eng-001" / "home" / "skills"
    partner_skills = instance / "run" / "partner" / "home" / "skills"
    assert (worker_skills / "hx-worker" / "SKILL.md").is_file()
    assert not (worker_skills / "hx-partner").exists()
    assert (partner_skills / "hx-partner" / "SKILL.md").is_file()
    assert not (partner_skills / "hx-worker").exists()
    assert not (worker_skills / "hx-worker").is_symlink()


def test_rerunning_is_idempotent(instance):
    assert run_install(instance, "eng-001").returncode == 0
    first = (instance / "run" / "eng-001" / "home" / "settings.json").read_text()
    assert run_install(instance, "eng-001").returncode == 0
    assert (instance / "run" / "eng-001" / "home" / "settings.json").read_text() == first


def test_the_board_accepts_a_home_install_sh_wrote(instance, work_item):
    """The board invariant "every run/<id>/home/ has its settings file and credentials"."""
    from hx.board import collect

    work_item("eng-001", "idle")
    work_item("partner", "idle")
    assert run_install(instance, "eng-001").returncode == 0
    assert [e for e in collect(instance)["errors"] if "home" in e] == []


def test_python_bin_from_config_hx_json_is_preferred(instance):
    """CONTRACTS.md `config/hx.json` third key; the adapters read JSON with hx's own Python."""
    import sys

    (instance / "config" / "hx.json").write_text(
        json.dumps({"hx_bin": "/opt/hx/bin/hx", "hook_bin": "/opt/hx/bin/hx-hook",
                    "python_bin": sys.executable})
    )
    assert run_install(instance, "eng-001").returncode == 0
    assert settings_for(instance, "eng-001")["skipDangerousModePermissionPrompt"] is True


def test_a_broken_python_bin_is_refused(instance):
    (instance / "config" / "hx.json").write_text(json.dumps({"python_bin": "/nope/python"}))
    result = run_install(instance, "eng-001")
    assert result.returncode != 0
    assert "/nope/python not found" in result.stderr
