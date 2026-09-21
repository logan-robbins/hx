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

from .conftest import SRC, clean_env

#: spec 09.1, `<hx-hook> --id <id> <event>`.
#: No `guard`: there is no PreToolUse hook at all (spec 09.1, spec 14 D25).
HX_EVENTS = {
    "context", "log", "subagent-start", "subagent-stop",
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


def test_refuses_without_the_instance_token(instance):
    """spec 11 Auth: auth is one long-lived token per instance, at `seed/token`."""
    (instance / "seed" / "token").unlink()
    result = run_install(instance, "eng-001")
    assert result.returncode != 0
    assert "refuse" in result.stderr
    assert "seed/token" in result.stderr and "setup-token" in result.stderr
    assert not (instance / "run" / "eng-001" / "home" / "settings.json").exists()


def test_refuses_a_token_readable_by_anyone_else(instance):
    """CONTRACTS.md `seed/token`: mode 0600, because it is a year-long credential."""
    (instance / "seed" / "token").chmod(0o644)
    result = run_install(instance, "eng-001")
    assert result.returncode != 0
    assert "refuse" in result.stderr and "0600" in result.stderr


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
    assert "PreToolUse" not in settings["hooks"], "no guard hook (spec 09.1, spec 14 D25)"
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


def test_claude_md_excludes_cover_the_agents_own_workdir(installed):
    """The workdir is whatever `harness.json` names; nothing in it is ever discovered."""
    excludes = settings_for(installed, "eng-001")["claudeMdExcludes"]
    workdir = str(installed / "wt" / "eng-001")
    assert f"{workdir}/**/CLAUDE.md" in excludes
    assert f"{workdir}/**/AGENTS.md" in excludes
    assert f"{workdir}/**/.claude/CLAUDE.md" in excludes
    # The home's own CLAUDE.md, which is the one that must load, is never excluded.
    assert all(not pattern.startswith(str(installed / "run")) for pattern in excludes)
    assert all(pattern.startswith("/") for pattern in excludes), "patterns are absolute globs"


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


def test_no_credentials_file_is_written_into_the_home(installed):
    """spec 11 Auth: agent homes hold no credentials; the token is the whole of auth."""
    home = installed / "run" / "eng-001" / "home"
    assert not (home / ".credentials.json").exists()
    assert list(home.glob("*credential*")) == []


def test_the_token_is_never_copied_into_the_instance(installed):
    """It stays in `seed/token`; nothing under `run/` ever holds it (CONTRACTS.md)."""
    secret = (installed / "seed" / "token").read_text().strip()
    for path in (installed / "run").rglob("*"):
        if path.is_file():
            assert secret not in path.read_text(errors="replace"), f"the token leaked into {path}"


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


SKILLS_DIR = str(SRC / "hx" / "skills")


def test_the_companion_home_gets_only_its_stop_hook_and_hx_companion(instance):
    """spec 10, handoff/orchestrator-to-build.md 2026-09-20: one hook, one skill."""
    assert run_install(instance, "eng-001", HX_SKILLS_DIR=SKILLS_DIR).returncode == 0
    home = instance / "run" / "eng-001" / "companion-home"
    settings = json.loads((home / "settings.json").read_text())
    assert set(settings["hooks"]) == {"Stop"}
    assert (home / "skills" / "hx-companion" / "SKILL.md").is_file()
    assert not (home / "CLAUDE.md").exists()


def test_the_partner_home_gets_hx_partner_and_hx_fleet(instance):
    """handoff/orchestrator-to-build.md 2026-09-20."""
    assert run_install(instance, "partner", HX_SKILLS_DIR=SKILLS_DIR).returncode == 0
    skills = instance / "run" / "partner" / "home" / "skills"
    assert (skills / "hx-partner" / "SKILL.md").is_file()
    assert (skills / "hx-fleet" / "SKILL.md").is_file()
    assert not (skills / "hx-worker").exists()


def test_a_worker_home_gets_hx_worker_only(instance):
    assert run_install(instance, "eng-001", HX_SKILLS_DIR=SKILLS_DIR).returncode == 0
    skills = instance / "run" / "eng-001" / "home" / "skills"
    assert (skills / "hx-worker" / "SKILL.md").is_file()
    assert not (skills / "hx-partner").exists() and not (skills / "hx-fleet").exists()


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


# --- nothing about launch is interactive (spec 05, 11) -------------------------------------
#
# Both of these were found by the build-3 live check: a brand-new CLAUDE_CONFIG_DIR stops at
# the first-run theme picker, and then at the folder-trust dialog, and an agent has no one to
# answer either.


def config_json(instance, item_id):
    return json.loads((instance / "run" / item_id / "home" / ".claude.json").read_text())


def test_onboarding_is_pre_completed(installed):
    assert config_json(installed, "eng-001")["hasCompletedOnboarding"] is True


def test_the_folder_trust_dialog_is_pre_accepted_for_the_agents_own_cwd(installed):
    """The cwd is `wt/<id>` for a worker (spec 17.4), and hx created it."""
    projects = config_json(installed, "eng-001")["projects"]
    workdir = str(installed / "wt" / "eng-001")
    assert projects[workdir]["hasTrustDialogAccepted"] is True
    # A CLAUDE.md with `@path` imports would otherwise prompt for approval (CONTRACTS.md).
    assert projects[workdir]["hasClaudeMdExternalIncludesApproved"] is True
    # Only keys that exist in a real config are written; nothing invented.
    assert set(projects[workdir]) == {"hasTrustDialogAccepted", "hasClaudeMdExternalIncludesApproved"}


def test_the_partners_trusted_directory_is_the_root(instance):
    assert run_install(instance, "partner").returncode == 0
    projects = config_json(instance, "partner")["projects"]
    assert projects[str(instance)]["hasTrustDialogAccepted"] is True


def test_rerunning_keeps_what_claude_code_wrote_into_config_json(installed):
    """A home that has been running is not reset by a re-install."""
    path = installed / "run" / "eng-001" / "home" / ".claude.json"
    body = json.loads(path.read_text())
    body["numStartups"] = 7
    body["projects"][str(installed / "wt" / "eng-001")]["history"] = ["something"]
    path.write_text(json.dumps(body))

    assert run_install(installed, "eng-001").returncode == 0
    after = json.loads(path.read_text())
    assert after["numStartups"] == 7
    assert after["projects"][str(installed / "wt" / "eng-001")]["history"] == ["something"]
    assert after["hasCompletedOnboarding"] is True
