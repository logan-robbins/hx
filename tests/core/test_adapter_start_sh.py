"""`adapters/claude/start.sh` — the entire launch (spec 11, 17.4).

M0 pass criteria: "`start.sh` argv is exactly `--dangerously-skip-permissions --effort …
--model … --append-system-prompt-file run/<id>/persona.md` with no prompt argument;
`persona.md` equals `AGENTS.md` above the header".

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


def test_refuses_a_missing_worktree(ready):
    import shutil

    shutil.rmtree(ready / "wt" / "eng-001")
    result = start(ready, "eng-001")
    assert result.returncode != 0
    assert "refuse" in result.stderr and "worktree" in result.stderr


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
        and (index == 0 or record["argv"][index - 1] not in ("--effort", "--model", "--append-system-prompt-file"))
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
    for forbidden in ("CLAUDE_CODE_AUTO_COMPACT_WINDOW", "CLAUDE_AUTOCOMPACT_PCT_OVERRIDE",
                      "CLAUDE_CODE_DISABLE_1M_CONTEXT"):
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
    assert [e for e in board["errors"] if "eng-001" in e] == []
