"""Quiet protocol: `hx complete` wakes the Partner only for outcomes needing action."""

from __future__ import annotations


def _dispatched(instance, hx, launched, goals):
    launched("partner", "eng-001")
    goals("eng-001")
    assert hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance).returncode == 0
    from .conftest import write_evidence

    write_evidence(instance, "eng-001")


def test_complete_done_does_not_wake_the_partner(instance, hx, launched, goals):
    _dispatched(instance, hx, launched, goals)
    assert hx("complete", "done", harness_id="eng-001").returncode == 0
    log = instance / "run" / "partner" / "fake-input.log"
    pasted = log.read_text() if log.is_file() else ""
    assert "eng-001 done" not in pasted, "a done completion must be silent; the board poll discovers it"


def test_complete_blocked_still_wakes_the_partner(instance, hx, launched, goals, monkeypatch):
    import hx.complete as complete_mod

    _dispatched(instance, hx, launched, goals)
    calls = []
    monkeypatch.setattr(
        complete_mod.wake, "wake_partner_status", lambda root, text: calls.append(text) or "accepted"
    )
    result = complete_mod.complete(instance, "blocked", item_id="eng-001", env=None)
    assert result["outcome"] == "blocked"
    assert result["woke_partner"] == "accepted"
    assert calls == ["eng-001 blocked"]


# --- `done` requires the worker's `## Evidence`; repeats are the planned runtime's ------------

import json  # noqa: E402
import os  # noqa: E402
import subprocess  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402

RULE_SIGNAL = "ModuleNotFoundError: No module named requests in the test run"
RULE_CHECK = "the test env installs the project dependencies before pytest"


@pytest.fixture
def learned(tmp_path, monkeypatch):
    """A clean git workdir as the instance root, and a learnings directory with one rule."""
    from hx import complete, learn

    repo = tmp_path / "repo"
    repo.mkdir()
    git_env = {**os.environ, "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull}
    subprocess.run(["git", "init", "-q", str(repo)], env=git_env, check=True)
    (repo / "source.py").write_text("value = 1\n")
    (repo / ".gitignore").write_text("state/\n")
    subprocess.run(["git", "add", "."], cwd=repo, env=git_env, check=True)
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.com",
                    "-c", "core.hooksPath=/dev/null", "commit", "-qm", "fixture"],
                   cwd=repo, env=git_env, check=True)
    monkeypatch.setattr(complete.streams, "subagent_streams", lambda *_a, **_k: [])
    monkeypatch.setattr(complete, "workdir_for", lambda *_: repo)
    monkeypatch.setattr(complete.tasks_mod, "load_tasks", lambda *_: {"eng-001": {"goal": "g"}})
    learnings = tmp_path / "LEARNINGS"
    env = {**git_env, "HX_LEARNINGS": str(learnings)}
    env.pop("HARNESS_ID", None)
    proposal = learn.propose(repo, signal=RULE_SIGNAL, check=RULE_CHECK, evidence="run 1",
                             category="testing", env={**env, "HARNESS_ID": "eng-001"})
    learn.accept(repo, proposal["id"], env=env)
    return repo, env


def _item(repo, evidence):
    item = repo.parent / "eng-001-working.md"
    item.write_text("---\nid: eng-001\n---\n## Goal\n\nDo it.\n\n## Evidence\n" + evidence + "\n")
    return item


@pytest.mark.parametrize("evidence", ["", "<one line per line of your definition of done>"],
                         ids=["empty", "template-note"])
def test_done_refuses_an_unwritten_evidence_section(learned, monkeypatch, evidence):
    from hx import complete

    repo, env = learned
    item = _item(repo, evidence)
    monkeypatch.setattr(complete, "require_work_item", lambda *_: item)
    with pytest.raises(complete.CheckFailed) as failed:
        complete.preflight(repo, "eng-001", "done", env=env)
    assert failed.value.message.startswith("HX-CHECK-FAILED eng-001\n`## Evidence` in your work item is empty")


def test_done_with_evidence_passes_and_never_consults_the_learnings(learned, monkeypatch):
    """No acceptance block runs at `hx complete done`, so there is no output to compare."""
    from hx import complete, learn

    repo, env = learned
    item = _item(repo, "- pytest -q tests/importer: 12 passed (receipt r-1)")
    monkeypatch.setattr(complete, "require_work_item", lambda *_: item)
    monkeypatch.setattr(learn, "completion_repeat", lambda *a, **k: pytest.fail("consulted"))
    proof = complete.preflight(repo, "eng-001", "done", env=env)
    assert proof["source"] is not None and proof["acceptance"]


def test_only_the_first_2000_bytes_are_the_signal(learned, monkeypatch):
    from hx import learn

    repo, env = learned
    seen = []
    monkeypatch.setattr(learn, "repeat", lambda root, signal, env=None: seen.append(signal) or
                        {"verdict": "new", "rule": None, "score": 0.0, "action": ""})
    assert learn.completion_repeat(repo, "eng-001", "x" * 5000, check="c", env=env) is None
    assert len(seen[0].encode()) == 2000


def test_a_failed_native_acceptance_check_carries_the_repeat_note(learned, monkeypatch):
    """`native_completion.advance_one`'s half: the output comes from the check's evidence."""
    from hx import evidence, native_completion

    repo, env = learned
    monkeypatch.setattr(evidence, "read", lambda store, event_id, limit=8000: {
        "encoding": "utf-8", "content": f"{RULE_SIGNAL} {RULE_CHECK}"[:limit]})
    store = SimpleNamespace(root=repo)
    row = {"payload": {"worker_id": "eng-001"}}
    note = native_completion._repeat_note(store, row, "unit", {"event_id": "e1"}, env)
    assert note == "learnings: repeat of L1 (1.00)"
    assert json.loads((repo / "state" / "learn" / "repeats.jsonl").read_text())["check"] == "unit"
    monkeypatch.setattr(evidence, "read", lambda *a, **k: (_ for _ in ()).throw(ValueError("gone")))
    assert native_completion._repeat_note(store, row, "unit", {"event_id": "e2"}, env) is None
