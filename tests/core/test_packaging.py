"""`hx install` end to end (spec 17.2, all four steps; 17.1, 08).

The v1 cut (spec 14 D25) left install with no mirror, no sparse worktrees, no unit files, no
`hx push` and no `hx upgrade`. Everything runs against a fake `claude` and a HOME under
`tmp_path`: the user's real `~/.claude` is never read, which `tests/guard` also proves from
the outside and `test_no_install_path_reads_the_users_claude_home` proves from the inside.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from .conftest import SRC, clean_env

TESTED = json.loads((SRC / "hx" / "packaging" / "tested-claude-versions.json").read_text())["versions"]


def git(*args, cwd=None, check=True):
    return subprocess.run(
        ["git", *args], cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=check
    )


def run_install(tmp_path, root, fake_claude_on_path, *args, home=None, tmux=None):
    """Run `hx install`.

    Step 4 is `hx launch partner`, so a full install starts a real tmux session. `tmux` points
    it at the test's private server; without it the session would land on the machine's default
    server, where the lanes themselves run — which is exactly how one leaked in build-3.
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


def test_step_1_pins_a_bare_tested_version(tmp_path, fake_claude_on_path, tmux_server):
    root = tmp_path / "instance"
    result = run_install(tmp_path, root, fake_claude_on_path, "--skeleton-only")
    assert result.returncode == 0, result.stdout + result.stderr
    # --skeleton-only stops before step 1's pin is needed, so pin it the normal way:
    (root / "seed").mkdir(parents=True, exist_ok=True)
    (root / "seed" / "token").write_text("sk-ant-oat-x\n")
    (root / "seed" / "token").chmod(0o600)
    result = run_install(tmp_path, root, fake_claude_on_path, tmux=tmux_server)
    pin = json.loads((root / "config" / "claude.json").read_text())
    assert pin["version"] == TESTED[0]
    assert "(Claude Code)" not in pin["version"], "the version is bare (CONTRACTS.md)"
    assert Path(pin["bin"]).is_file()


def test_step_1_records_an_untested_version_and_says_so(tmp_path):
    """Versions are recorded, never refused: a release newer than the live-verified list
    installs, is named `untested` beside the newest verified one, and the install goes on
    to the token step exactly as a tested one does."""
    from hx import claude_bin
    from hx.install import TOKEN_WAIT_EXIT

    root = tmp_path / "instance"
    bindir = tmp_path / "newbin"
    bindir.mkdir()
    binary = bindir / "claude"
    binary.write_text('#!/usr/bin/env bash\necho "1.0.0 (Claude Code)"\n')
    binary.chmod(0o755)
    home = tmp_path / "h"
    home.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "hx", "install", "--root", str(root)],
        env=clean_env(HOME=str(home), PATH=f"{bindir}:{os.environ.get('PATH', '')}"),
        capture_output=True, text=True,
    )
    assert result.returncode == TOKEN_WAIT_EXIT, result.stdout + result.stderr
    assert "1. claude 1.0.0 at" in result.stdout and "untested" in result.stdout
    assert TESTED[0] in result.stdout, "it names the newest live-verified version beside it"
    assert json.loads((root / "config" / "claude.json").read_text())["version"] == "1.0.0"
    assert claude_bin.tested_status("1.0.0") == {"tested": False, "newest_tested": TESTED[0]}
    assert claude_bin.tested_status(TESTED[0])["tested"] is True


def test_the_gate_flag_is_a_harmless_no_op(tmp_path, fake_claude_on_path):
    root = tmp_path / "instance"
    home = tmp_path / "h3"
    home.mkdir()
    result = subprocess.run(
        [sys.executable, "-m", "hx", "install", "--root", str(root), "--ignore-claude-version",
         "--skeleton-only"],
        env=clean_env(HOME=str(home), **fake_claude_on_path), capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


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


# --- step 4: no units, no mirror (spec 14 D25) -------------------------------------------------


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


def test_a_fresh_instance_has_no_mirror_worktrees_or_orders_dir(
    tmp_path, fake_claude_on_path, tmux_server
):
    """17.2 step 2 and CONTRACTS.md "Fresh instance contents"."""
    root = tmp_path / "instance"
    (root / "seed").mkdir(parents=True)
    (root / "seed" / "token").write_text("sk-ant-oat-fake\n")
    assert run_install(
        tmp_path, root, fake_claude_on_path, tmux=tmux_server
    ).returncode == 0
    for gone in ("repos", "wt", "goals", "config/repo.json"):
        assert not (root / gone).exists(), gone
    assert (root / "personas" / "partner.md").is_file()
    assert not (root / "personas" / "partner").exists(), "one file per role, no directory of AGENTS.md"

    from hx.board import collect

    assert collect(root)["items"] == [], "the Partner is not an item (spec 14 D25)"


def test_there_is_no_repo_push_or_upgrade_command(run_hx, tmp_path):
    for command in ("repo", "push", "upgrade"):
        result = run_hx(command, env_extra={"HARNESS_ROOT": str(tmp_path / "instance")})
        assert result.returncode == 2
        assert "unknown command" in result.stderr


def test_doctor_warns_when_the_binary_moved_past_its_record(instance, hx, tmp_path):
    """The record lags the binary only between an upstream release and the next launch
    boundary; the doctor says so as a warning that names both versions, never a failure."""
    from hx.doctor import FAIL, WARN, run_checks

    bindir = tmp_path / "driftbin"
    bindir.mkdir()
    binary = bindir / "claude"
    binary.write_text('#!/usr/bin/env bash\necho "9.9.9 (Claude Code)"\n')
    binary.chmod(0o755)
    pin = instance / "config" / "claude.json"
    pin.write_text(json.dumps({"bin": str(binary), "version": TESTED[0]}))

    rows = [c for c in run_checks(instance) if c[1] == "claude"]
    assert rows and rows[0][0] == WARN, rows
    assert "9.9.9" in rows[0][2] and TESTED[0] in rows[0][2] and "re-records" in rows[0][2], rows
    assert not [c for c in rows if c[0] == FAIL]


def test_a_launch_boundary_re_records_the_version(instance, tmp_path):
    """`refresh_pin` is what `hx launch`, `hx restart` and `hx up` run first: the pin follows
    the binary, so a fleet runs the latest release from its next launch."""
    from hx import claude_bin

    pin = json.loads((instance / "config" / "claude.json").read_text())
    moved = claude_bin.refresh_pin(instance, env=clean_env(HX_FAKE_CLAUDE_VERSION="9.9.9"))
    assert moved == {"bin": pin["bin"], "version": "9.9.9", "previous": pin["version"]}
    assert json.loads((instance / "config" / "claude.json").read_text())["version"] == "9.9.9"
    back = claude_bin.refresh_pin(instance, env=clean_env())
    assert back["version"] == pin["version"] and back["previous"] == "9.9.9"
    assert "previous" not in claude_bin.refresh_pin(instance, env=clean_env())
    (instance / "config" / "claude.json").write_text(json.dumps({"bin": "/nope/claude", "version": "1"}))
    assert claude_bin.refresh_pin(instance, env=clean_env()) is None, "a missing binary is the launcher's refusal, not this one's"


# --- the standing rule ---------------------------------------------------------------------------


def test_no_install_path_reads_the_users_claude_home(tmp_path, fake_claude_on_path, monkeypatch):
    """hx reads nothing from ~/.claude on any platform (spec 11 Auth, 17.3)."""
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
    preflight(root, None, os.environ)
    install_skeleton(root)
    assert opened == [], f"hx opened the user's Claude home: {opened}"


def test_dispatch_leaves_a_dirty_workdir_untouched(instance, hx, goals, launched):
    """spec 13 M1, spec 14 D25: a dirty workdir is the agent's business, not hx's."""
    launched("eng-001")
    workdir = instance / "wt" / "eng-001"
    (workdir / "half-done.py").write_text("work in progress\n")
    goals("eng-001")

    result = hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance)
    assert result.returncode == 0, result.stderr
    assert (workdir / "half-done.py").read_text() == "work in progress\n"
    assert git("-C", str(workdir), "status", "--porcelain").stdout.strip() != ""


def test_bench_touches_neither_git_nor_the_workdir(instance, hx, goals, launched):
    """spec 08, spec 14 D25: the body archive is the whole of `hx bench`."""
    launched("eng-001")
    goals("eng-001")
    assert hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance).returncode == 0
    assert hx("complete", "blocked", harness_id="eng-001").returncode == 0

    workdir = instance / "wt" / "eng-001"
    (workdir / "never-committed.py").write_text("untracked work\n")
    head = git("-C", str(workdir), "rev-parse", "HEAD").stdout

    result = hx("bench", "eng-001")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "patch" not in result.stdout
    assert list((instance / "pods" / "engineers" / "archive").glob("*.patch")) == []
    assert (workdir / "never-committed.py").is_file()
    assert git("-C", str(workdir), "rev-parse", "HEAD").stdout == head
