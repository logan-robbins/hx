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
    # The work item lives outside the workdir, with the `## Evidence` `done` requires.
    item = tmp_path / "eng-001-working.md"
    item.write_text("---\nid: eng-001\n---\n## Goal\n\ncheck source\n\n## Evidence\n- `test -f source.py`\n")
    monkeypatch.setattr(complete, "require_work_item", lambda *_: item)
    return repo, env


def test_a_clean_workdir_with_evidence_passes_preflight(repository):
    repo, env = repository
    proof = complete.preflight(repo, "eng-001", "done", env=env)
    assert proof["source"]["repository"]


def test_a_dirty_workdir_is_refused_before_evidence_is_read(repository):
    repo, env = repository
    (repo / "source.py").write_text("value = 3\n")
    with pytest.raises(complete.CheckFailed, match="is dirty"):
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
    proof = complete.preflight(repo, "eng-001", "done", env=env)
    monkeypatch.setattr(complete.tasks_mod, "load_tasks", lambda *_: {"eng-001": {"goal": "Changed acceptance checks."}})
    with pytest.raises(complete.CheckFailed, match="acceptance changed"):
        complete.final_workspace_guard(repo, "eng-001", proof, env=env)
