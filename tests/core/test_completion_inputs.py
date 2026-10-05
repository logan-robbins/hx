"""Completion input guards without launching native agents or tmux."""

from __future__ import annotations

import os
import subprocess
from types import SimpleNamespace

import pytest

from hx import complete


@pytest.fixture
def repository(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    subprocess.run(["git", "init", "-q", str(repo)], env=env, check=True)
    (repo / "source.py").write_text("value = 1\n")
    subprocess.run(["git", "add", "."], cwd=repo, env=env, check=True)
    subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                    "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture"], cwd=repo, env=env, check=True)
    monkeypatch.setattr(complete.streams, "subagent_streams", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(complete, "workdir_for", lambda *_: repo)
    monkeypatch.setattr(complete.tasks_mod, "load_tasks", lambda *_: {"eng-001": {"goal": "check source"}})
    return repo, env


def test_passing_check_that_dirties_source_cannot_complete(repository, monkeypatch):
    repo, env = repository
    monkeypatch.setattr(complete, "parse_goal_text", lambda *_: SimpleNamespace(checks="printf changed > source.py"))
    with pytest.raises(complete.CheckFailed, match="became dirty"):
        complete.preflight(repo, "eng-001", "done", env=env)


def test_write_and_restore_during_check_cannot_complete(repository, monkeypatch):
    repo, env = repository
    monkeypatch.setattr(complete, "parse_goal_text", lambda *_: SimpleNamespace(checks="original=$(cat source.py); printf changed > source.py; printf '%s\n' \"$original\" > source.py"))
    with pytest.raises(complete.CheckFailed, match="source inputs changed"):
        complete.preflight(repo, "eng-001", "done", env=env)


def test_unchanged_passing_check_can_complete_preflight(repository, monkeypatch):
    repo, env = repository
    monkeypatch.setattr(complete, "parse_goal_text", lambda *_: SimpleNamespace(checks="test -f source.py"))
    complete.preflight(repo, "eng-001", "done", env=env)


def test_nested_workdir_is_checked_and_git_errors_are_not_clean(repository, monkeypatch):
    repo, _ = repository
    nested = repo / "nested"
    nested.mkdir()
    (nested / "dirty.txt").write_text("untracked")
    assert complete.workdir_is_dirty(nested)[0]
    monkeypatch.setattr(complete.subprocess, "run", lambda *_args, **_kwargs:
                        subprocess.CompletedProcess([], 128, "", "permission denied"))
    dirty, message = complete.workdir_is_dirty(repo)
    assert dirty and "Could not inspect" in message


@pytest.mark.parametrize("commit_change", [False, True])
def test_late_write_during_flush_prevents_completion_metadata(repository, monkeypatch, commit_change):
    repo, env = repository
    monkeypatch.setattr(complete, "parse_goal_text", lambda *_: SimpleNamespace(checks="test -f source.py"))
    monkeypatch.setattr(complete, "require_work_item", lambda *_: repo / "item.md")
    monkeypatch.setattr(complete, "parse_work_item", lambda *_: SimpleNamespace(state="working", dispatched="today"))
    def flush(*_args, **_kwargs):
        (repo / "source.py").write_text("value = 2\n")
        if commit_change:
            subprocess.run(["git", "add", "source.py"], cwd=repo, env=env, check=True)
            subprocess.run(["git", "-c", "user.name=Test", "-c", "user.email=test@example.com",
                            "-c", "core.hooksPath=/dev/null", "commit", "-qm", "late change"], cwd=repo, env=env, check=True)
    monkeypatch.setattr(complete.flush_mod, "flush", flush)
    monkeypatch.setattr(complete, "final_digest", lambda *_args, **_kwargs: "summary")
    monkeypatch.setattr(complete, "write_digest", lambda *_: pytest.fail("completion metadata changed despite stale verification"))
    with pytest.raises(complete.CheckFailed, match="after verification"):
        complete.complete(repo, "done", item_id="eng-001", env=env)


def test_late_acceptance_amendment_cannot_reuse_old_preflight(repository, monkeypatch):
    repo, env = repository
    monkeypatch.setattr(complete, "parse_goal_text", lambda *_: SimpleNamespace(checks="test -f source.py"))
    proof = complete.preflight(repo, "eng-001", "done", env=env)
    monkeypatch.setattr(complete.tasks_mod, "load_tasks", lambda *_: {"eng-001": {"goal": "Changed acceptance checks."}})
    with pytest.raises(complete.CheckFailed, match="acceptance changed"):
        complete.final_workspace_guard(repo, "eng-001", proof, env=env)
