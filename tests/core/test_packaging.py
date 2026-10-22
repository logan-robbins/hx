"""`hx install` end to end, all four steps.

Everything runs against a fake `claude` and a HOME under `tmp_path`: the user's real
`~/.claude` is never read, which `test_no_install_path_reads_the_users_claude_home` proves.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import FAKE_CLAUDE, clean_env


def git(*args, cwd=None, check=True):
    return subprocess.run(
        ["git", *args], cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=check
    )


def run_install(tmp_path, root, fake_claude_on_path, *args, home=None, tmux=None):
    """Run `hx install`.

    Step 4 is `hx launch partner`, so a full install starts a real tmux session. `tmux` points
    it at the test's private server; without it the session would land on the machine's default
    server and outlive the test.
    """
    home = home or (tmp_path / "install-home")
    home.mkdir(parents=True, exist_ok=True)
    env = clean_env(HOME=str(home), **fake_claude_on_path)
    if tmux:
        env["HX_TMUX"] = " ".join(tmux)
    return subprocess.run(
        [sys.executable, "-m", "hx", "install", "--root", str(root), *args],
        env=env, capture_output=True, text=True,
    )


# --- step 1: preflight ---------------------------------------------------------------------


def test_step_1_records_the_binary_path_and_nothing_else(tmp_path, fake_claude_on_path, tmux_server):
    root = tmp_path / "instance"
    (root / "seed").mkdir(parents=True)
    (root / "seed" / "token").write_text("sk-ant-oat-x\n")
    (root / "seed" / "token").chmod(0o600)
    result = run_install(tmp_path, root, fake_claude_on_path, tmux=tmux_server)
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"1. claude at {FAKE_CLAUDE}" in result.stdout
    assert json.loads((root / "config" / "claude.json").read_text()) == {"bin": str(FAKE_CLAUDE)}


def test_step_1_records_the_binary_without_running_it(tmp_path):
    """Step 1 finds `claude` and records where it is. It never runs it: a binary that fails
    every invocation is recorded all the same, and the install goes on to the token step."""
    from hx.install import TOKEN_WAIT_EXIT

    root = tmp_path / "instance"
    bindir = tmp_path / "newbin"
    bindir.mkdir()
    binary = bindir / "claude"
    binary.write_text("#!/usr/bin/env bash\nexit 1\n")
    binary.chmod(0o755)
    home = tmp_path / "h"
    home.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "hx", "install", "--root", str(root)],
        env=clean_env(HOME=str(home), PATH=f"{bindir}:{os.environ.get('PATH', '')}"),
        capture_output=True, text=True,
    )
    assert result.returncode == TOKEN_WAIT_EXIT, result.stdout + result.stderr
    assert f"1. claude at {binary}" in result.stdout
    assert json.loads((root / "config" / "claude.json").read_text()) == {"bin": str(binary)}


def test_step_1_takes_an_explicit_binary(tmp_path, fake_claude_on_path):
    root = tmp_path / "instance"
    binary = Path(fake_claude_on_path["PATH"].split(":")[0]) / "claude"
    home = tmp_path / "h2"
    home.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "hx", "install", "--root", str(root), "--claude", str(binary),
         "--skeleton-only"],
        env=clean_env(HOME=str(home)), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- step 3: the seed token ------------------------------------------------------------------


def test_step_3_stops_until_the_human_pastes_the_token(tmp_path, fake_claude_on_path):
    from hx.install import TOKEN_WAIT_EXIT

    root = tmp_path / "instance"
    result = run_install(tmp_path, root, fake_claude_on_path)
    assert result.returncode == TOKEN_WAIT_EXIT
    assert "claude setup-token" in result.stdout
    assert "~/.claude" in result.stdout, "it says hx reads nothing of the user's"


def test_step_3_tightens_the_tokens_mode(tmp_path, fake_claude_on_path, tmux_server):
    root = tmp_path / "instance"
    run_install(tmp_path, root, fake_claude_on_path)
    token = root / "seed" / "token"
    token.write_text("sk-ant-oat-x\n")
    token.chmod(0o644)
    run_install(tmp_path, root, fake_claude_on_path, tmux=tmux_server)
    assert token.stat().st_mode & 0o777 == 0o600


# --- step 4: no units ---------------------------------------------------------------------------


def test_install_ships_no_unit_files(tmp_path, fake_claude_on_path, tmux_server):
    """17.2: hx ships no launchd plist and no systemd unit; cron is the human's own."""
    root = tmp_path / "instance"
    home = tmp_path / "install-home"
    (root / "seed").mkdir(parents=True)
    (root / "seed" / "token").write_text("sk-ant-oat-fake\n")
    result = run_install(tmp_path, root, fake_claude_on_path, home=home, tmux=tmux_server)
    assert result.returncode == 0, result.stdout + result.stderr
    assert list(home.rglob("*.plist")) == []
    assert list(home.rglob("*.service")) == [] and list(home.rglob("*.timer")) == []
    assert "systemd" not in result.stdout and "launchd" not in result.stdout


def test_a_fresh_instance_has_the_partner_persona_and_no_assignment(
    tmp_path, fake_claude_on_path, tmux_server
):
    """17.2 step 2 and CONTRACTS.md "Fresh instance contents"."""
    root = tmp_path / "instance"
    (root / "seed").mkdir(parents=True)
    (root / "seed" / "token").write_text("sk-ant-oat-fake\n")
    assert run_install(
        tmp_path, root, fake_claude_on_path, tmux=tmux_server
    ).returncode == 0
    assert (root / "personas" / "partner.md").is_file()

    from hx.board import collect

    assert collect(root)["tasks"] == [], "Partner bootstrap creates no packet assignment"


def test_there_is_no_implicit_repository_creation_push_or_upgrade_command(run_hx, tmp_path):
    for command in ("push", "upgrade"):
        result = run_hx(command, env_extra={"HARNESS_ROOT": str(tmp_path / "instance")})
        assert result.returncode == 2
        assert "unknown command" in result.stderr
    for action in ('init', 'create'):
        result = run_hx('repo', action, env_extra={'HARNESS_ROOT': str(tmp_path / 'instance')})
        assert result.returncode == 2 and 'invalid choice' in result.stderr
    assert not (tmp_path / 'instance' / '.git').exists()


# --- the standing rule ---------------------------------------------------------------------------


def test_no_install_path_reads_the_users_claude_home(tmp_path, fake_claude_on_path, monkeypatch):
    """hx reads nothing from ~/.claude on any platform."""
    import builtins

    real_home_claude = Path.home() / ".claude"
    opened: list[str] = []
    real_open = builtins.open

    def watching_open(file, *args, **kwargs):
        try:
            resolved = str(Path(file).resolve())
        except (TypeError, ValueError, OSError):
            resolved = str(file)
        if resolved.startswith(str(real_home_claude)):
            opened.append(resolved)
        return real_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", watching_open)

    from hx.install import install_skeleton, preflight

    root = tmp_path / "instance"
    monkeypatch.setenv("PATH", fake_claude_on_path["PATH"])
    preflight(root, None)
    install_skeleton(root)
    assert opened == [], f"hx opened the user's Claude home: {opened}"
