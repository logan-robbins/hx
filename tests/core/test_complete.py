"""Quiet protocol: `hx complete` wakes the Partner only for outcomes needing action."""

from __future__ import annotations


def _dispatched(instance, hx, launched, goals):
    launched("partner", "eng-001")
    goals("eng-001")
    assert hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance).returncode == 0


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


# --- the never-twice question at completion (TOOL_SKILL_PLAN step 4) ---------------------------

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


def _failing(monkeypatch, output):
    from hx import complete

    script = f"printf '%s\\n' {json.dumps(output)}; exit 1"
    monkeypatch.setattr(complete, "parse_goal_text", lambda *_: SimpleNamespace(checks=script))


def test_a_failure_that_repeats_a_rule_is_recorded_and_still_fails(learned, monkeypatch):
    from hx import complete

    repo, env = learned
    _failing(monkeypatch, f"{RULE_SIGNAL} {RULE_CHECK}")
    with pytest.raises(complete.CheckFailed) as failed:
        complete.preflight(repo, "eng-001", "done", env=env)
    message = failed.value.message
    assert message.startswith("HX-CHECK-FAILED eng-001\n`### Checks` failed with exit 1")
    assert message.rstrip().endswith("learnings: repeat of L1 (1.00)")
    records = [json.loads(line) for line in
               (repo / "state" / "learn" / "repeats.jsonl").read_text().splitlines()]
    assert len(records) == 1
    assert {k: records[0][k] for k in ("id", "rule", "score", "check")} == {
        "id": "eng-001", "rule": "L1", "score": 1.0, "check": "### Checks"}
    assert records[0]["ts"].endswith("Z")


def test_a_related_failure_is_recorded_with_its_score(learned, monkeypatch):
    from hx import complete

    repo, env = learned
    _failing(monkeypatch, RULE_SIGNAL)
    with pytest.raises(complete.CheckFailed, match=r"learnings: repeat of L1 \(0\.\d\d\)"):
        complete.preflight(repo, "eng-001", "done", env=env)
    record = json.loads((repo / "state" / "learn" / "repeats.jsonl").read_text())
    assert record["verdict"] == "related" and 0.25 <= record["score"] < 0.8


def test_a_failure_matching_no_rule_writes_nothing(learned, monkeypatch):
    from hx import complete

    repo, env = learned
    _failing(monkeypatch, "make: *** [all] Error 2")
    with pytest.raises(complete.CheckFailed) as failed:
        complete.preflight(repo, "eng-001", "done", env=env)
    assert "learnings:" not in failed.value.message
    assert not (repo / "state" / "learn" / "repeats.jsonl").exists()


def test_only_the_first_2000_bytes_are_the_signal(learned, monkeypatch):
    from hx import learn

    repo, env = learned
    seen = []
    monkeypatch.setattr(learn, "repeat", lambda root, signal, env=None: seen.append(signal) or
                        {"verdict": "new", "rule": None, "score": 0.0, "action": ""})
    assert learn.completion_repeat(repo, "eng-001", "x" * 5000, check="c", env=env) is None
    assert len(seen[0].encode()) == 2000


def test_a_passing_check_never_consults_the_learnings(learned, monkeypatch):
    from hx import complete, learn

    repo, env = learned
    monkeypatch.setattr(complete, "parse_goal_text", lambda *_: SimpleNamespace(checks="true"))
    monkeypatch.setattr(learn, "completion_repeat", lambda *a, **k: pytest.fail("consulted on a pass"))
    complete.preflight(repo, "eng-001", "done", env=env)


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
