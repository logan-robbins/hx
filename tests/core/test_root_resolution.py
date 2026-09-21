"""HARNESS_ROOT resolution and the standing refusal (ORCHESTRATION.md rule 2, spec 17.3).

tests/guard/test_harness_root_refusal.py owns the guard-level version of this; these tests
cover the resolution rules around it and prove that every command, implemented or not,
applies the refusal before doing anything else.
"""

from __future__ import annotations

import pytest

from hx.errors import HxRefusal
from hx.root import resolve_root


def env_for(tmp_path, **extra):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    return {"HOME": str(home), **extra}


def test_defaults_to_hx_under_home(tmp_path):
    env = env_for(tmp_path)
    assert resolve_root(None, env) == (tmp_path / "home" / "hx").absolute()


def test_harness_root_env_wins(tmp_path):
    env = env_for(tmp_path, HARNESS_ROOT=str(tmp_path / "srv" / "hx"))
    assert resolve_root(None, env) == (tmp_path / "srv" / "hx").absolute()


def test_explicit_override_wins_over_env(tmp_path):
    env = env_for(tmp_path, HARNESS_ROOT=str(tmp_path / "from-env"))
    assert resolve_root(str(tmp_path / "explicit"), env) == (tmp_path / "explicit").absolute()


def test_tilde_is_expanded(tmp_path):
    env = env_for(tmp_path)
    assert resolve_root("~/elsewhere", env) == (tmp_path / "home" / "elsewhere").absolute()


@pytest.mark.parametrize("suffix", ["", "/hx", "/nested/deeper"])
def test_refuses_the_user_claude_home_and_anything_under_it(tmp_path, suffix):
    env = env_for(tmp_path)
    target = str(tmp_path / "home" / ".claude") + suffix
    with pytest.raises(HxRefusal) as exc:
        resolve_root(target, env)
    assert "refuse" in str(exc.value).lower()


def test_refuses_a_symlink_that_resolves_into_the_user_claude_home(tmp_path):
    env = env_for(tmp_path)
    inside = tmp_path / "home" / ".claude" / "hx"
    inside.mkdir(parents=True)
    link = tmp_path / "innocent-looking"
    link.symlink_to(inside)
    with pytest.raises(HxRefusal) as exc:
        resolve_root(str(link), env)
    assert "refuse" in str(exc.value).lower()


def test_refuses_when_the_user_claude_home_is_itself_a_symlink(tmp_path):
    """`~/.claude` pointing elsewhere must not become a way in."""
    home = tmp_path / "home"
    home.mkdir()
    real = tmp_path / "real-claude"
    (real / "hx").mkdir(parents=True)
    (home / ".claude").symlink_to(real)
    with pytest.raises(HxRefusal) as exc:
        resolve_root(str(real / "hx"), {"HOME": str(home)})
    assert "refuse" in str(exc.value).lower()


def test_a_sibling_directory_named_like_it_is_allowed(tmp_path):
    env = env_for(tmp_path)
    target = tmp_path / "home" / ".claude-hx"
    assert resolve_root(str(target), env) == target.absolute()


def test_every_command_refuses_before_doing_anything(run_hx, tmp_path):
    """Implemented and not-yet-implemented commands alike (spec 08, goal build-1 item 1)."""
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    for command in ("doctor", "board", "install", "dispatch", "complete", "seam", "ui", "orders"):
        result = run_hx(
            command,
            env_extra={"HOME": str(home), "HARNESS_ROOT": str(home / ".claude" / "instance")},
        )
        combined = (result.stdout + result.stderr).lower()
        assert result.returncode == 1, f"{command}: exit {result.returncode}\n{combined}"
        assert "refuse" in combined, f"{command}: {combined}"
        assert "not implemented" not in combined, f"{command} answered before refusing"
