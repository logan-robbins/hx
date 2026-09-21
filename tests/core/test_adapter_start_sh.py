"""`adapters/claude/start.sh` — the entire launch (spec 11, 17.4).

M0 pass criteria: "`start.sh` argv is exactly `--dangerously-skip-permissions --effort …
--model … --append-system-prompt-file run/<id>/persona.md` with no prompt argument;
`persona.md` equals `AGENTS.md` above the header", plus `--setting-sources user` (build-8
item 0: the workdir is a checkout hx does not own, and its `.claude/settings.json` must
never load).

The tmux tests run a real tmux server on a private socket and a fake `claude` that records
its argv, env and cwd (ORCHESTRATION.md "Fake claude for M0-M5").
"""

from __future__ import annotations

import json
import subprocess

import pytest

from .conftest import AGENTS_MD, FAKE_CLAUDE, PERSONA, clean_env, wait_for

EXPECTED_FLAGS = [
    "--dangerously-skip-permissions",
    # Only the home's settings load: not the workdir's `.claude/settings.json`, not its
    # `.claude/settings.local.json` (build-8 item 0, live 2026-09-20 21:20).
    "--setting-sources",
    "user",
    "--effort",
    "xhigh",
    "--model",
    "claude-opus-5",
    "--append-system-prompt-file",
]

#: Flags spec 11 and 17.4 say are never passed.
FORBIDDEN_FLAGS = {
    "--resume", "--continue", "-p", "--print", "--permission-mode",
    "--append-subagent-system-prompt-file", "--settings", "--add-dir",
}


def start(instance, item_id, *args, tmux=None, **env_extra):
    script = instance / "adapters" / "claude" / "start.sh"
    env = clean_env(HARNESS_ROOT=str(instance), **env_extra)
    if tmux:
        env["HX_TMUX"] = " ".join(tmux)
    return subprocess.run(
        ["bash", str(script), *args, item_id], env=env, capture_output=True, text=True
    )


def install_home(instance, item_id):
    script = instance / "adapters" / "claude" / "install.sh"
    result = subprocess.run(
        ["bash", str(script), item_id],
        env=clean_env(HARNESS_ROOT=str(instance)),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return instance


@pytest.fixture
def ready(instance):
    install_home(instance, "eng-001")
    install_home(instance, "partner")
    return instance


# --- refusals, no tmux needed ----------------------------------------------------------------


def test_refuses_a_home_without_settings(instance):
    result = start(instance, "eng-001")
    assert result.returncode != 0
    assert "refuse" in result.stderr and "settings.json" in result.stderr


def test_refuses_without_the_instance_token(ready):
    (ready / "seed" / "token").unlink()
    result = start(ready, "eng-001")
    assert result.returncode != 0
    assert "refuse" in result.stderr and "seed/token" in result.stderr


def test_refuses_a_token_readable_by_anyone_else(ready):
    (ready / "seed" / "token").chmod(0o604)
    result = start(ready, "eng-001")
    assert result.returncode != 0
    assert "refuse" in result.stderr and "0600" in result.stderr


def test_refuses_an_agents_md_without_the_mutable_header(ready):
    """Without the header hx cannot tell the persona from the agent's own memory (spec 03)."""
    (ready / "config" / "eng-001" / "AGENTS.md").write_text("no header here\n")
    result = start(ready, "eng-001")
    assert result.returncode != 0
    assert "refuse" in result.stderr and "UPDATES BELOW ONLY" in result.stderr


def test_refuses_a_missing_agents_md(ready):
    (ready / "config" / "eng-001" / "AGENTS.md").unlink()
    result = start(ready, "eng-001")
    assert result.returncode != 0 and "refuse" in result.stderr


def test_refuses_a_missing_workdir(ready):
    """17.2: the workdir is whatever `harness.json` names, and it has to be there."""
    import shutil

    shutil.rmtree(ready / "wt" / "eng-001")
    result = start(ready, "eng-001")
    assert result.returncode != 0
    assert "refuse" in result.stderr and "workdir" in result.stderr


def test_refuses_a_non_id(ready):
    assert "refuse" in start(ready, "Engineering").stderr


def test_refuses_without_a_pinned_binary(ready):
    (ready / "config" / "claude.json").write_text(json.dumps({"version": "fake-0"}))
    # A PATH with bash and python but no `claude`, so the fallback lookup finds nothing.
    result = start(ready, "eng-001", PATH="/usr/bin:/bin")
    assert result.returncode != 0, result.stdout
    assert "refuse" in result.stderr and "claude" in result.stderr


# --- the launch, in a real tmux session with the fake claude ------------------------------------


def launch_and_record(instance, item_id, tmux_server):
    result = start(instance, item_id, tmux=tmux_server)
    assert result.returncode == 0, result.stderr
    record_path = instance / "run" / item_id / "fake-argv.json"
    wait_for(record_path.is_file, what=f"{item_id} to record its argv")
    return json.loads(record_path.read_text())


def test_argv_is_exactly_spec_17_4(ready, tmux_server):
    record = launch_and_record(ready, "eng-001", tmux_server)
    persona = str(ready / "run" / "eng-001" / "persona.md")
    assert record["argv"] == EXPECTED_FLAGS + [persona]


def test_no_prompt_argument_ever(ready, tmux_server):
    record = launch_and_record(ready, "eng-001", tmux_server)
    positional = [
        arg
        for index, arg in enumerate(record["argv"])
        if not arg.startswith("--")
        and (
            index == 0
            or record["argv"][index - 1]
            not in ("--effort", "--model", "--append-system-prompt-file", "--setting-sources")
        )
    ]
    assert positional == [], f"a prompt argument reached the binary: {positional}"
    assert not FORBIDDEN_FLAGS & set(record["argv"])


def test_the_token_reaches_the_agent_and_never_its_argv(ready, tmux_server):
    """spec 11 Auth, CONTRACTS.md: exported, never an argument to anything."""
    record = launch_and_record(ready, "eng-001", tmux_server)
    secret = (ready / "seed" / "token").read_text().strip()
    assert record["env"].get("CLAUDE_CODE_OAUTH_TOKEN") == secret
    assert all(secret not in arg for arg in record["argv"]), "the token is in the argv"

    shown = subprocess.run(
        [*tmux_server, "show-environment", "-t", "=eng-001"], capture_output=True, text=True, check=True
    ).stdout
    assert secret not in shown, "the token is in the tmux session environment, where `ps` sees it"


def test_the_session_env_is_spec_11(ready, tmux_server):
    record = launch_and_record(ready, "eng-001", tmux_server)
    env = record["env"]
    assert env["HARNESS_ID"] == "eng-001"
    assert env["HARNESS_ROOT"] == str(ready)
    assert env["CLAUDE_CONFIG_DIR"] == str(ready / "run" / "eng-001" / "home")
    assert env["DISABLE_AUTOUPDATER"] == "1"
    for forbidden in ("CLAUDE_AUTOCOMPACT_PCT_OVERRIDE", "CLAUDE_CODE_DISABLE_1M_CONTEXT"):
        assert forbidden not in env, f"{forbidden} must stay unset (spec 11 Compaction)"


def test_the_env_is_set_on_the_tmux_session(ready, tmux_server):
    launch_and_record(ready, "eng-001", tmux_server)
    shown = subprocess.run(
        [*tmux_server, "show-environment", "-t", "=eng-001"], capture_output=True, text=True, check=True
    ).stdout
    assert f"HARNESS_ROOT={ready}" in shown
    assert "HARNESS_ID=eng-001" in shown
    assert f"CLAUDE_CONFIG_DIR={ready / 'run' / 'eng-001' / 'home'}" in shown
    assert "DISABLE_AUTOUPDATER=1" in shown


def test_the_worker_runs_in_its_worktree_and_the_partner_in_the_root(ready, tmux_server):
    worker = launch_and_record(ready, "eng-001", tmux_server)
    partner = launch_and_record(ready, "partner", tmux_server)
    assert worker["cwd"] == str((ready / "wt" / "eng-001").resolve())
    assert partner["cwd"] == str(ready.resolve())


def test_the_session_is_named_by_the_id_with_a_main_window(ready, tmux_server):
    launch_and_record(ready, "eng-001", tmux_server)
    sessions = subprocess.run(
        [*tmux_server, "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True, check=True
    ).stdout.split()
    assert "eng-001" in sessions
    windows = subprocess.run(
        [*tmux_server, "list-windows", "-t", "=eng-001", "-F", "#{window_name}"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert "main" in windows


def test_persona_equals_agents_md_above_the_header(ready, tmux_server):
    launch_and_record(ready, "eng-001", tmux_server)
    persona = (ready / "run" / "eng-001" / "persona.md").read_text()
    assert persona == AGENTS_MD.split("## UPDATES BELOW ONLY")[0]
    assert persona == PERSONA + "\n"
    assert "UPDATES BELOW ONLY" not in persona
    assert "Things I learned" not in persona, "the agent's own memory is not system prompt"


def test_persona_is_regenerated_at_every_launch(ready, tmux_server):
    launch_and_record(ready, "eng-001", tmux_server)
    (ready / "config" / "eng-001" / "AGENTS.md").write_text(
        "You are eng-001, now a reviewer.\n\n## UPDATES BELOW ONLY\n\nold memory\n"
    )
    (ready / "run" / "eng-001" / "fake-argv.json").unlink()
    launch_and_record(ready, "eng-001", tmux_server)
    assert (ready / "run" / "eng-001" / "persona.md").read_text() == "You are eng-001, now a reviewer.\n\n"


def test_relaunching_respawns_the_main_window(ready, tmux_server):
    first = launch_and_record(ready, "eng-001", tmux_server)
    (ready / "run" / "eng-001" / "fake-argv.json").unlink()
    second = launch_and_record(ready, "eng-001", tmux_server)
    assert second["pid"] != first["pid"]
    sessions = subprocess.run(
        [*tmux_server, "list-sessions", "-F", "#{session_name}"], capture_output=True, text=True, check=True
    ).stdout.split()
    assert sessions.count("eng-001") == 1


def test_the_model_and_effort_come_from_harness_json(ready, tmux_server):
    path = ready / "config" / "eng-001" / "harness.json"
    body = json.loads(path.read_text())
    body["model"], body["effort"] = "claude-sonnet-5", "max"
    path.write_text(json.dumps(body))
    record = launch_and_record(ready, "eng-001", tmux_server)
    assert "claude-sonnet-5" in record["argv"] and "max" in record["argv"]


def test_the_board_sees_the_launched_session(ready, tmux_server, work_item):
    from hx.board import collect

    work_item("eng-001", "working")
    (ready / "run" / "eng-001").mkdir(parents=True, exist_ok=True)
    (ready / "run" / "eng-001" / "goal").write_text("2026-09-20T12:00:00Z\n")
    launch_and_record(ready, "eng-001", tmux_server)
    board = collect(ready, env={"HX_TMUX": " ".join(tmux_server)})
    item = {i["id"]: i for i in board["items"]}["eng-001"]
    assert item["session_alive"] is True
    assert item["state"] == "working"


def test_the_agent_is_sandboxed_and_bypasses_permissions(ready, tmux_server):
    """spec 11, not negotiable: `--dangerously-skip-permissions` and `IS_SANDBOX=1`, always."""
    record = launch_and_record(ready, "eng-001", tmux_server)
    assert "--dangerously-skip-permissions" in record["argv"]
    assert record["env"].get("IS_SANDBOX") == "1", "in the process the agent runs as"

    shown = subprocess.run(
        [*tmux_server, "show-environment", "-t", "=eng-001"], capture_output=True, text=True, check=True
    ).stdout
    assert "IS_SANDBOX=1" in shown, "and on the tmux session, which hx doctor reads"


def test_doctor_fails_a_live_agent_without_the_sandbox(ready, tmux_server):
    from hx.doctor import FAIL, live_agent_checks

    launch_and_record(ready, "eng-001", tmux_server)
    env = {"HX_TMUX": " ".join(tmux_server)}
    assert not [c for c in live_agent_checks("eng-001", env) if c[0] == FAIL]

    subprocess.run([*tmux_server, "set-environment", "-t", "=eng-001", "IS_SANDBOX", "0"], check=True)
    failures = live_agent_checks("eng-001", env)
    assert any(status == FAIL and "IS_SANDBOX" in detail for status, detail in failures), failures


# --- build-8 item 0: the workdir's own `.claude/` never reaches an agent -----------------------


def test_the_companion_also_gets_setting_sources_user(ready, tmux_server):
    """spec 10, 17.4: the Companion runs in HARNESS_ROOT but is launched by the same script."""
    from hx import companion as companion_mod

    (ready / "run" / "eng-001").mkdir(parents=True, exist_ok=True)
    (ready / "run" / "eng-001" / "companion-system.md").write_text("You are a Companion.\n")
    result = start(ready, "eng-001", "--companion", tmux=tmux_server)
    assert result.returncode == 0, result.stderr
    record_path = ready / "run" / "eng-001" / "fake-argv-companion.json"
    wait_for(record_path.is_file, what="the Companion to record its argv")
    argv = json.loads(record_path.read_text())["argv"]
    assert "--setting-sources" in argv
    assert argv[argv.index("--setting-sources") + 1] == "user"
    assert companion_mod  # the module this launch belongs to


def test_the_workdirs_own_claude_settings_are_never_loaded(ready, tmux_server):
    """Live 2026-09-20 21:20: a worker loaded its product repo's deny-all PreToolUse hook and
    could never run `hx complete`. Only `user` is loaded, so `project` and `local` cannot be.
    """
    workdir = ready / "wt" / "eng-001"
    tripwire = workdir / ".claude"
    tripwire.mkdir(parents=True, exist_ok=True)
    (tripwire / "settings.json").write_text(
        '{"hooks": {"PreToolUse": [{"matcher": "*", "hooks": [{"type": "command", '
        '"command": "echo TRIPWIRE >&2; exit 2"}]}]}, "permissions": {"defaultMode": "plan"}}\n'
    )
    (tripwire / "settings.local.json").write_text('{"permissions": {"defaultMode": "plan"}}\n')

    record = launch_and_record(ready, "eng-001", tmux_server)
    argv = record["argv"]
    assert argv[argv.index("--setting-sources") + 1] == "user", (
        "neither `project` nor `local` may be in the list; the workdir is a checkout hx does "
        "not own (build-8 item 0)"
    )
    assert str(workdir) == record["cwd"], "the agent really is running in that directory"


def test_harness_root_bin_is_on_the_agents_path(ready, tmux_server):
    """build-8 item 11: an agent runs `hx board`, not `$(cat config/hx.json | …)`."""
    record = launch_and_record(ready, "eng-001", tmux_server)
    path = record["env"].get("PATH", "")
    assert path.split(":")[0] == str(ready / "bin"), f"PATH starts with {path.split(':')[0]!r}"


def test_the_companion_also_gets_bin_on_its_path(ready, tmux_server):
    (ready / "run" / "eng-001").mkdir(parents=True, exist_ok=True)
    (ready / "run" / "eng-001" / "companion-system.md").write_text("You are a Companion.\n")
    assert start(ready, "eng-001", "--companion", tmux=tmux_server).returncode == 0
    record_path = ready / "run" / "eng-001" / "fake-argv-companion.json"
    wait_for(record_path.is_file, what="the Companion to record its env")
    path = json.loads(record_path.read_text())["env"].get("PATH", "")
    assert path.split(":")[0] == str(ready / "bin")


# --- D26: the `/goal` stop-hook block cap ------------------------------------------------------


def test_the_stop_hook_block_cap_is_raised_for_the_agent(ready, tmux_server):
    """D26. Claude Code caps a blocked turn-ending at 9 by default, then pauses the goal
    (live 2026-09-20, 01.1 E10). Nine turns is nothing for a real task."""
    record = launch_and_record(ready, "eng-001", tmux_server)
    assert record["env"].get("CLAUDE_CODE_STOP_HOOK_BLOCK_CAP") == "100000"


def test_the_companion_gets_the_block_cap_too(ready, tmux_server):
    (ready / "run" / "eng-001").mkdir(parents=True, exist_ok=True)
    (ready / "run" / "eng-001" / "companion-system.md").write_text("You are a Companion.\n")
    assert start(ready, "eng-001", "--companion", tmux=tmux_server).returncode == 0
    record_path = ready / "run" / "eng-001" / "fake-argv-companion.json"
    wait_for(record_path.is_file, what="the Companion to record its env")
    env = json.loads(record_path.read_text())["env"]
    assert env.get("CLAUDE_CODE_STOP_HOOK_BLOCK_CAP") == "100000"


# --- the operator's autocompact window ---------------------------------------------------------


def test_the_autocompact_window_reaches_the_agents_process(ready, tmux_server):
    """`config/models.json` carries `autocompact_window` for this model, so the session runs
    with native compaction pulled down to it. hx's own seam threshold is below it (validated
    in config_models.py), so the seam still happens first and this number is never reached."""
    record = launch_and_record(ready, "eng-001", tmux_server)
    assert record["env"].get("CLAUDE_CODE_AUTO_COMPACT_WINDOW") == "250000"


def test_the_autocompact_window_is_on_the_tmux_session_too(ready, tmux_server):
    launch_and_record(ready, "eng-001", tmux_server)
    shown = subprocess.run(
        [*tmux_server, "show-environment", "-t", "=eng-001", "CLAUDE_CODE_AUTO_COMPACT_WINDOW"],
        capture_output=True, text=True, check=False,
    )
    assert shown.stdout.strip() == "CLAUDE_CODE_AUTO_COMPACT_WINDOW=250000", shown.stdout


def test_a_model_row_without_an_autocompact_window_exports_nothing(ready, tmux_server):
    """Optional: without the field the session keeps Claude Code's native window, which is
    what spec 11 Compaction describes."""
    models = ready / "config" / "models.json"
    rows = json.loads(models.read_text())
    for row in rows.values():
        row.pop("autocompact_window", None)
        row["threshold"] = 500000
    models.write_text(json.dumps(rows))
    record = launch_and_record(ready, "eng-001", tmux_server)
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in record["env"]


def test_an_unlisted_model_does_not_stop_the_launch(ready, tmux_server):
    """The Companion's model need not be in models.json, and a missing row is not an error."""
    path = ready / "config" / "eng-001" / "harness.json"
    body = json.loads(path.read_text())
    body["model"] = "claude-haiku-5"
    path.write_text(json.dumps(body))
    record = launch_and_record(ready, "eng-001", tmux_server)
    assert "CLAUDE_CODE_AUTO_COMPACT_WINDOW" not in record["env"]
    assert "claude-haiku-5" in record["argv"]


def test_the_block_cap_is_on_the_tmux_session_too(ready, tmux_server):
    """`start.sh` sets it both on the session and in the exec'd env, like IS_SANDBOX."""
    launch_and_record(ready, "eng-001", tmux_server)
    shown = subprocess.run(
        [*tmux_server, "show-environment", "-t", "=eng-001", "CLAUDE_CODE_STOP_HOOK_BLOCK_CAP"],
        capture_output=True, text=True, check=False,
    )
    assert shown.stdout.strip() == "CLAUDE_CODE_STOP_HOOK_BLOCK_CAP=100000", shown.stdout
