"""Completion is ledger intent; native runtime checks establish the result."""
import pytest

from hx import complete
from hx.errors import Refused


def test_completion_requires_native_run_identity(tmp_path):
    with pytest.raises(Refused, match='HX_CONTINUITY_RUN'):
        complete.complete(tmp_path, env={'HARNESS_ID': 'eng-001'})
    assert not (tmp_path / 'state').exists()


@pytest.mark.parametrize('env,item,outcome,message', [
    ({'HARNESS_ID': 'partner'}, None, 'done', 'Partner'),
    ({'HARNESS_ID': 'eng-001'}, 'eng-002', 'done', 'assigned worker'),
    ({'HARNESS_ID': 'eng-001'}, None, 'blocked', 'hx progress'),
    ({'HARNESS_ID': 'eng-001'}, None, 'decision', 'hx progress'),
])
def test_completion_cannot_bypass_assignment_or_publish_alternate_outcomes(tmp_path, env, item, outcome, message):
    with pytest.raises(Refused, match=message):
        complete.complete(tmp_path, outcome, item_id=item, env=env)


def test_completion_only_requests_ledger_verification(tmp_path, monkeypatch, capsys):
    from hx import native_completion
    calls = []
    def request(store, run, worker):
        calls.append((run, worker))
        return {'status': 'requested', 'run_id': run}
    monkeypatch.setattr(native_completion, 'request', request)
    assert complete.main(['done'], tmp_path, env={'HARNESS_ID': 'eng-001', 'HX_CONTINUITY_RUN': 'R'}) == 0
    assert calls == [('R', 'eng-001')]
    output = capsys.readouterr().out
    assert 'End this turn' in output
    assert 'HX-COMPLETE' not in output
    assert not (tmp_path / 'tasks.json').exists()
    assert not (tmp_path / 'pods').exists()


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
    from hx import learn

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
    learnings = tmp_path / "LEARNINGS"
    env = {**git_env, "HX_LEARNINGS": str(learnings)}
    env.pop("HARNESS_ID", None)
    proposal = learn.propose(repo, signal=RULE_SIGNAL, check=RULE_CHECK, evidence="run 1",
                             category="testing", env={**env, "HARNESS_ID": "eng-001"})
    learn.accept(repo, proposal["id"], env=env)
    return repo, env



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
