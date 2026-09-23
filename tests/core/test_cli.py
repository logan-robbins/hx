"""Subcommand dispatch for every command in spec 08 (goal build-1 item 1)."""

from __future__ import annotations

import pytest

from hx.cli import COMMANDS, IMPLEMENTED, NOT_IMPLEMENTED

#: Spec 08's table, plus install/up/doctor/ui/show from 17.2. The v1 cut (spec 14 D25)
#: removed `repo`, `push` and `upgrade`.
SPEC_08_COMMANDS = {
    "launch", "install", "doctor", "show", "ui", "up",
    "dispatch", "goal", "task", "compose", "seam", "restart", "complete", "resume",
    "read", "bench", "board", "flush", "companion", "wake", "heartbeat", "metrics",
    "goals", "archive",
    # Episode memory (docs/memory.md), not in spec 08's own table.
    "memory",
    # File memory (CONTRACTS.md `hx read` and `hx recall`), likewise post-spec-08.
    "recall",
    # Template compiler: distributes config/CLAUDE.md + role persona into
    # config/<id>/AGENTS.md on launch/restart; likewise post-spec-08.
    "compile",
}


def test_every_spec_08_command_is_dispatched():
    assert set(COMMANDS) == SPEC_08_COMMANDS, set(COMMANDS) ^ SPEC_08_COMMANDS


def test_no_command_is_both_implemented_and_not():
    assert not (set(IMPLEMENTED) & set(NOT_IMPLEMENTED))


@pytest.mark.parametrize("command", sorted(NOT_IMPLEMENTED))
def test_unimplemented_commands_exit_2_with_the_build_goal(run_hx, tmp_path, command):
    result = run_hx(command, env_extra={"HARNESS_ROOT": str(tmp_path / "instance")})
    assert result.returncode == 2
    assert result.stderr.strip() == f"hx: {command}: not implemented (build-{NOT_IMPLEMENTED[command]})"
    assert result.stdout == ""


def test_unknown_command_exits_2(run_hx, tmp_path):
    result = run_hx("frobnicate", env_extra={"HARNESS_ROOT": str(tmp_path / "instance")})
    assert result.returncode == 2
    assert "unknown command" in result.stderr


def test_bare_hx_prints_usage_and_exits_2(run_hx, tmp_path):
    result = run_hx(env_extra={"HARNESS_ROOT": str(tmp_path / "instance")})
    assert result.returncode == 2
    assert "usage: hx <command>" in result.stdout


def test_help_exits_0(run_hx, tmp_path):
    result = run_hx("--help", env_extra={"HARNESS_ROOT": str(tmp_path / "instance")})
    assert result.returncode == 0
    assert "usage: hx <command>" in result.stdout


def test_python_dash_m_hx_exists(run_hx, tmp_path):
    """tests/guard requires `python -m hx` to work; prove __main__.py is importable."""
    result = run_hx("--version", env_extra={"HARNESS_ROOT": str(tmp_path / "instance")})
    assert result.returncode == 0
    assert result.stdout.startswith("hx ")


def test_hook_entrypoint_knows_the_spec_09_events():
    from hx.hooks import EVENTS

    assert set(EVENTS) == {
        # No `guard`: there is no PreToolUse hook at all (spec 09.1, spec 14 D25).
        "context", "log", "subagent-start", "subagent-stop",
        "subagent-result", "stop", "precompact", "postcompact",
        # The Companion's own `stop`, in its own home — the other half of the pass protocol
        # (spec 10), not one of the agent's own.
        "companion-stop",
    }
