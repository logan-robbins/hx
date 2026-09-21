"""build-8 items 0 and 11, live: what a launched agent does and does not load.

Item 0 is the blocking finding of 2026-09-20 21:20 — a worker loaded its product repo's own
`.claude/settings.json`, whose deny-all `PreToolUse` hook denied every tool, so it could never
reach `hx complete`. Item 11 is `hx` on the agent's `PATH`.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from .conftest import REPO, pane_text, wait_for, work_item

TRIPWIRE = "TRIPWIRE-REPO-CLAUDE-DIR-LOADED"


def tripwired_workdir(live_root) -> Path:
    """A copy of `tests/scenario/m8/repo`, whose `.claude/` is a deny-all tripwire.

    A copy: the checkout's own `.claude/` is never edited, which is the point of the fixture.
    """
    workdir = live_root / "work" / "be-001"
    workdir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(REPO / "tests" / "scenario" / "m8" / "repo", workdir, dirs_exist_ok=True)
    for cache in workdir.rglob("__pycache__"):
        shutil.rmtree(cache, ignore_errors=True)
    settings = json.loads((workdir / ".claude" / "settings.json").read_text())
    assert TRIPWIRE in json.dumps(settings), "the fixture is supposed to be a tripwire"
    assert settings["permissions"]["defaultMode"] == "plan"
    return workdir


def test_the_workdirs_own_claude_settings_never_reach_the_agent(live_root, live_env, worker):
    """spec 11, 17.4: `--setting-sources user`, so only the home's settings load."""
    from hx.lifecycle import launch

    workdir = tripwired_workdir(live_root)
    item_id, _ = worker("be-001", workdir=workdir)
    work_item(live_root, item_id)
    launch(live_root, item_id, companion=False, env=live_env)

    from hx import goal

    goal.wait_for_prompt(item_id, live_env)
    goal.paste(item_id, "Use the Bash tool to run exactly: echo ok > PROOF.txt", live_env)

    proof = workdir / "PROOF.txt"
    wait_for(proof.is_file, what="the agent's Bash tool call to create PROOF.txt")
    assert proof.read_text().strip() == "ok"
    assert TRIPWIRE not in pane_text(item_id, live_env), "the repo's PreToolUse hook loaded"


def test_the_pinned_binary_takes_setting_sources_user(live_root, live_env, worker):
    """The flag is in the argv the binary actually got, not only in start.sh."""
    from hx.lifecycle import launch

    item_id, _ = worker("be-001")
    work_item(live_root, item_id)
    launch(live_root, item_id, companion=False, env=live_env)

    pane_pid = subprocess.run(
        [*live_env["HX_TMUX"].split(), "list-panes", "-t", f"={item_id}:main", "-F", "#{pane_pid}"],
        capture_output=True, text=True, check=True,
    ).stdout.strip().splitlines()[0]

    def argv_of_the_binary():
        children = subprocess.run(["pgrep", "-P", pane_pid], capture_output=True, text=True).stdout.split()
        for pid in [pane_pid, *children]:
            command = subprocess.run(
                ["ps", "-o", "command=", "-p", pid], capture_output=True, text=True
            ).stdout
            if "--setting-sources" in command:
                return command
        return None

    command = wait_for(argv_of_the_binary, what="the exec'd claude process")
    assert "--setting-sources user" in " ".join(command.split())
    assert "--dangerously-skip-permissions" in command


def test_hx_is_on_the_agents_path(live_root, live_env, worker):
    """build-8 item 11: `$HARNESS_ROOT/bin` first, so an agent runs `hx board`."""
    from hx.lifecycle import launch

    item_id, workdir = worker("be-001")
    work_item(live_root, item_id)
    launch(live_root, item_id, companion=False, env=live_env)

    assert (live_root / "bin" / "hx").is_symlink()

    from hx import goal

    goal.wait_for_prompt(item_id, live_env)
    goal.paste(item_id, "Use the Bash tool to run exactly: hx board > BOARD.txt 2>&1", live_env)

    board = workdir / "BOARD.txt"
    wait_for(board.is_file, what="the agent to run `hx board` off its PATH")
    assert item_id in board.read_text(), board.read_text()
