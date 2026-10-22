"""`adapters/claude/install.sh` — the per-agent home.

`run/<id>/home/settings.json` validates: hooks present with the right `--id`, instruction-files
mode `claude-md`, `claudeMdExcludes` set.

Settings keys per code.claude.com/docs/en/settings-reference,
/docs/en/memory#choose-which-instruction-files-load, /docs/en/cross-session-messaging and
/docs/en/hooks.
"""

from __future__ import annotations

import json
import shlex
import subprocess

import pytest

from .conftest import SRC, clean_env

#: A worker's hook events, each run as `<hx-hook> --id <id> <event>`: no `guard`, the Partner's.
HX_EVENTS = {
    "context", "log", "subagent-start", "subagent-stop",
    "stop", "precompact", "postcompact",
}
PARTNER_EVENTS = HX_EVENTS | {"guard", "subagent-result", "log-failure"}


def hook_event(command):
    return next(part for part in shlex.split(command) if part in PARTNER_EVENTS)


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
    """Auth is one long-lived token per instance, at `seed/token`."""
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


def test_every_hook_runs_bin_hx_hook(instance):
    """Every hook command is `<root>/bin/hx-hook --id <id> <event>`, whatever
    `config/hx.json` records; `hx install` links `bin/hx-hook` to the entry point."""
    (instance / "config" / "hx.json").write_text(
        json.dumps({"hx_bin": "/opt/hx/bin/hx", "hook_bin": "/opt/hx/bin/hx-hook"})
    )
    assert run_install(instance, "eng-001").returncode == 0
    for command in hook_commands(settings_for(instance, "eng-001")):
        assert command.startswith(str(instance / "bin" / "hx-hook") + " ")


def test_the_hook_events_map_to_the_claude_code_events_of_spec_09(installed):
    settings = settings_for(installed, "eng-001")
    by_event = {}
    for claude_event, entries in settings["hooks"].items():
        for entry in entries:
            for hook in entry["hooks"]:
                by_event[hook["command"].rsplit(" ", 1)[1]] = (claude_event, entry.get("matcher"))

    assert by_event["context"] == ("SessionStart", "startup|resume|clear|compact")
    assert "PreToolUse" not in settings["hooks"], "a worker is never guarded (spec 09.1)"
    assert by_event["log"] == ("PostToolUse", "*")
    assert "subagent-result" not in by_event
    assert by_event["subagent-start"][0] == "SubagentStart"
    assert by_event["subagent-stop"][0] == "SubagentStop"
    assert by_event["stop"] == ("Stop", None), "Stop takes no matcher (docs/en/hooks)"
    assert by_event["precompact"][0] == "PreCompact"
    assert by_event["postcompact"][0] == "PostCompact"


def test_guard_is_installed_for_the_partner_alone(instance):
    """`guard` is a PreToolUse entry in the Partner's settings.json, and in no worker's."""
    from hx.hook_guard import MATCHER

    assert run_install(instance, "partner").returncode == 0
    assert run_install(instance, "eng-001").returncode == 0
    partner = settings_for(instance, "partner")["hooks"]
    assert partner["PreToolUse"] == [
        {"matcher": MATCHER, "hooks": [{"type": "command", "command": partner["PreToolUse"][0]["hooks"][0]["command"]}]}
    ]
    assert partner["PreToolUse"][0]["hooks"][0]["command"].endswith(" --id partner guard")

    worker = settings_for(instance, "eng-001")["hooks"]
    assert "PreToolUse" not in worker
    assert not any(c.endswith(" guard") for c in hook_commands({"hooks": worker}))


def test_the_partner_observes_and_rehydrates_its_bounded_research_children(instance):
    assert run_install(instance, "partner").returncode == 0
    commands = hook_commands(settings_for(instance, "partner"))
    assert {hook_event(c) for c in commands} == PARTNER_EVENTS
    hooks = settings_for(instance, "partner")['hooks']
    assert hook_event(hooks['SubagentStart'][0]['hooks'][0]['command']) == 'subagent-start'
    assert hook_event(hooks['SubagentStop'][0]['hooks'][0]['command']) == 'subagent-stop'
    assert any(block.get('matcher') == 'Agent' and hook_event(block['hooks'][0]['command']) == 'subagent-result'
               for block in hooks['PostToolUse'])


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
    """Every agent runs bypass permissions; no launch is ever interactive."""
    assert settings_for(installed, "eng-001")["skipDangerousModePermissionPrompt"] is True


def test_cross_session_inbound_is_the_partners_alone(instance):
    """`crossSessionInbound: accept` is scoped to the Partner."""
    assert run_install(instance, "partner").returncode == 0
    assert run_install(instance, "eng-001").returncode == 0
    assert settings_for(instance, "partner")["crossSessionInbound"] == "accept"
    assert "crossSessionInbound" not in settings_for(instance, "eng-001")


def test_permissions_default_mode_is_not_used(installed):
    """It is ignored in project/local scope and would silently degrade to manual."""
    assert "permissions" not in settings_for(installed, "eng-001")


# --- credentials, CLAUDE.md, skills -------------------------------------------------------------


def test_no_credentials_file_is_written_into_the_home(installed):
    """Agent homes hold no credentials; the token is the whole of auth."""
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
    """`hx-partner` or `hx-worker` only, copied, never symlinked."""
    skills = tmp_path / "skills"
    for name in ("hx-partner", "hx-worker"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(f"---\nname: {name}\n---\n")
    # Which home gets which skill is the manifest's decision.
    (skills / "manifest.json").write_text(json.dumps({
        "hx-partner": {"audiences": ["partner"], "roles": ["partner"], "optional": False},
        "hx-worker": {"audiences": ["worker"], "roles": ["*"], "optional": False},
    }))

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


def test_the_partner_home_gets_hx_partner_and_hx_fleet(instance):
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


def _expected_skills(instance, item_id):
    """The per-role skill set, read from the shipped manifest rather than retyped."""
    from hx import skill_manifest

    return set(skill_manifest.for_agent(SRC / "hx" / "skills", instance, item_id, env={}))


def _installed(path):
    return {p.name for p in path.iterdir() if p.is_dir()} if path.is_dir() else set()


@pytest.mark.parametrize("item_id", ["partner", "eng-001"])
def test_each_home_gets_exactly_the_manifests_skills(instance, item_id):
    assert run_install(instance, item_id, HX_SKILLS_DIR=SKILLS_DIR).returncode == 0
    home = instance / "run" / item_id
    assert _installed(home / "home" / "skills") == _expected_skills(instance, item_id)


def test_a_role_skill_installs_for_its_role_only(instance, tmp_path):
    """A manifest row with `roles` reaches only workers of those roles."""
    import shutil

    skills = tmp_path / "skills"
    shutil.copytree(SKILLS_DIR, skills)
    (skills / "hx-qa-only").mkdir()
    (skills / "hx-qa-only" / "SKILL.md").write_text("---\nname: hx-qa-only\ndescription: x\n---\nbody\n")
    manifest = json.loads((skills / "manifest.json").read_text())
    manifest["hx-qa-only"] = {"audiences": ["worker"], "roles": ["qa-engineer"], "optional": False}
    (skills / "manifest.json").write_text(json.dumps(manifest))

    assert run_install(instance, "eng-001", HX_SKILLS_DIR=str(skills)).returncode == 0
    assert not (instance / "run" / "eng-001" / "home" / "skills" / "hx-qa-only").exists()
    harness = instance / "config" / "eng-001" / "harness.json"
    harness.write_text(json.dumps({**json.loads(harness.read_text()), "role": "qa-engineer"}))
    assert run_install(instance, "eng-001", HX_SKILLS_DIR=str(skills)).returncode == 0
    assert (instance / "run" / "eng-001" / "home" / "skills" / "hx-qa-only" / "SKILL.md").is_file()


def test_an_optional_skill_installs_only_when_config_hx_json_names_it(instance, tmp_path):
    import shutil

    skills = tmp_path / "skills"
    shutil.copytree(SKILLS_DIR, skills)
    (skills / "hx-extra").mkdir()
    (skills / "hx-extra" / "SKILL.md").write_text("---\nname: hx-extra\ndescription: x\n---\nbody\n")
    manifest = json.loads((skills / "manifest.json").read_text())
    manifest["hx-extra"] = {"audiences": ["partner", "worker"], "roles": ["*"], "optional": True}
    (skills / "manifest.json").write_text(json.dumps(manifest))

    assert run_install(instance, "eng-001", HX_SKILLS_DIR=str(skills)).returncode == 0
    assert not (instance / "run" / "eng-001" / "home" / "skills" / "hx-extra").exists()
    hx_json = instance / "config" / "hx.json"
    hx_json.write_text(json.dumps({**json.loads(hx_json.read_text()), "skills": ["hx-extra"]}))
    for item_id in ("eng-001", "partner"):
        assert run_install(instance, item_id, HX_SKILLS_DIR=str(skills)).returncode == 0
        assert (instance / "run" / item_id / "home" / "skills" / "hx-extra" / "SKILL.md").is_file()


def test_skill_selection_has_one_packet_runtime_for_every_worker(instance):
    selected = _expected_skills_env(instance, "eng-001", HX_CONTINUITY_RUN="r1")
    assert "hx-worker" in selected
    assert "hx-memory" not in selected
    assert selected == _expected_skills(instance, "eng-001")


def _expected_skills_env(instance, item_id, **env):
    from hx import skill_manifest

    return set(skill_manifest.for_agent(SRC / "hx" / "skills", instance, item_id, env=env))


def test_a_missing_manifest_stops_the_install(instance, tmp_path):
    skills = tmp_path / "skills"
    (skills / "hx-worker").mkdir(parents=True)
    (skills / "hx-worker" / "SKILL.md").write_text("---\nname: hx-worker\n---\n")
    result = run_install(instance, "eng-001", HX_SKILLS_DIR=str(skills))
    assert result.returncode != 0 and "manifest" in result.stderr


def test_the_adapter_refuses_without_hx_python(instance):
    """hx runs every adapter with HX_PYTHON, the interpreter hx itself runs on."""
    result = run_install(instance, "eng-001", HX_PYTHON="")
    assert result.returncode != 0
    assert "HX_PYTHON is unset" in result.stderr


def test_a_broken_hx_python_is_refused(instance):
    result = run_install(instance, "eng-001", HX_PYTHON="/nope/python")
    assert result.returncode != 0
    assert "/nope/python not found" in result.stderr


# --- nothing about launch is interactive ---------------------------------------------------
#
# A brand-new CLAUDE_CONFIG_DIR stops at the first-run theme picker, and then at the
# folder-trust dialog, and an agent has no one to answer either.


def config_json(instance, item_id):
    return json.loads((instance / "run" / item_id / "home" / ".claude.json").read_text())


def test_onboarding_is_pre_completed(installed):
    assert config_json(installed, "eng-001")["hasCompletedOnboarding"] is True


def test_the_folder_trust_dialog_is_pre_accepted_for_the_agents_own_cwd(installed):
    """A worker's cwd is its harness `workdir`, here `wt/<id>`."""
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


# --- the shipped role and optional skills ---------------------------------------------------------


def _set_role(instance, item_id, role):
    harness = instance / "config" / item_id / "harness.json"
    harness.write_text(json.dumps({**json.loads(harness.read_text()), "role": role}))


@pytest.mark.parametrize("role,expected", [
    ("release-engineer", True), ("qa-engineer", True),
    ("backend-engineer", False), ("frontend-engineer", False), ("engineer", False),
])
def test_hx_signoff_installs_for_release_and_qa_engineers_only(instance, role, expected):
    _set_role(instance, "eng-001", role)
    assert run_install(instance, "eng-001", HX_SKILLS_DIR=SKILLS_DIR).returncode == 0
    home = instance / "run" / "eng-001"
    assert (home / "home" / "skills" / "hx-signoff" / "SKILL.md").is_file() is expected
    assert _installed(home / "home" / "skills") == _expected_skills(instance, "eng-001")


def test_the_partner_never_gets_hx_signoff(instance):
    assert run_install(instance, "partner", HX_SKILLS_DIR=SKILLS_DIR).returncode == 0
    assert "hx-signoff" not in _installed(instance / "run" / "partner" / "home" / "skills")


def test_hx_conversion_is_off_until_config_hx_json_turns_it_on(instance):
    for item_id in ("eng-001", "partner"):
        assert run_install(instance, item_id, HX_SKILLS_DIR=SKILLS_DIR).returncode == 0
        assert "hx-conversion" not in _installed(instance / "run" / item_id / "home" / "skills")
    hx_json = instance / "config" / "hx.json"
    recorded = json.loads(hx_json.read_text()) if hx_json.is_file() else {}
    hx_json.write_text(json.dumps({**recorded, "skills": ["hx-conversion"]}))
    for item_id in ("eng-001", "partner"):
        assert run_install(instance, item_id, HX_SKILLS_DIR=SKILLS_DIR).returncode == 0
        home = instance / "run" / item_id
        assert (home / "home" / "skills" / "hx-conversion" / "SKILL.md").is_file(), item_id
        assert _installed(home / "home" / "skills") == _expected_skills(instance, item_id)
