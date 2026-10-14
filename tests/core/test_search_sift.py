"""Jev sifting: deterministic candidates in, labelled candidates out, degrade open."""
from __future__ import annotations

import pytest

from hx import jev, search_sift


@pytest.fixture
def fake_jev(monkeypatch):
    def evaluate(state, questions, *, key, endpoint=None, timeout=None):
        answers = {}
        for name, question in questions.items():
            if question["type"] == "choice":
                answers[name] = {"type": "choice", "choice": "assertion",
                                 "confidence": 0.9,
                                 "probabilities": {k: (0.9 if k == "assertion" else 0.1 / 7)
                                                   for k in question["criteria"]}}
            else:
                answers[name] = {"type": "noul", "noul": 0.9 if "auth" in name else 0.2}
        return {"model": jev.MODEL, "answers": answers,
                "usage": {"input_tokens": 10, "output_tokens": 5}}
    monkeypatch.setattr(jev, "evaluate", evaluate)
    monkeypatch.setattr(jev, "credential", lambda root, env=None: "test-key")
    return evaluate


def test_sift_scores_without_dropping(tmp_path, fake_jev):
    result = search_sift.sift(tmp_path, "where is auth handled?",
                              [{"id": "auth.py", "snippet": "login()"},
                               {"id": "db.py", "snippet": "connect()"}])
    assert result["sifted"] is True
    assert {r["id"] for r in result["results"]} == {"auth.py", "db.py"}
    assert result["results"][0]["id"] == "auth.py"


def test_sift_degrades_when_jev_unavailable(tmp_path, monkeypatch):
    def unavailable(state, questions, *, key, **kw):
        raise jev.Unavailable("timeout")
    monkeypatch.setattr(jev, "evaluate", unavailable)
    monkeypatch.setattr(jev, "credential", lambda root, env=None: "k")
    result = search_sift.sift(tmp_path, "q", [{"id": "a", "snippet": "x"}])
    assert result["status"] == "unsifted"
    assert result["results"][0]["jev_score"] is None


def test_sift_rejects_too_many_candidates(tmp_path):
    with pytest.raises(Exception):
        search_sift.sift(tmp_path, "q", [{"id": str(i), "snippet": ""} for i in range(17)])


def test_triage_labels_tentative(tmp_path, fake_jev):
    result = search_sift.triage_failure(tmp_path, "AssertionError: expected 3 got 4\nFAILED test_x")
    assert result["category"] == "assertion"
    assert result["tentative"] is True
    assert "AssertionError" in result["excerpt"]


def test_select_checks_unions_deterministic_and_jev(tmp_path, fake_jev):
    result = search_sift.select_checks(
        tmp_path, ["auth.py"],
        [{"id": "auth-tests", "description": "covers auth.py login"},
         {"id": "db-tests", "description": "covers db"}])
    bases = {c["id"]: c["basis"] for c in result["ordered"]}
    assert bases["auth-tests"] == "deterministic"
    assert result["ordered"][-1]["basis"] in {"jev-candidate", "not_run"}
    assert "not_run" in result["note"]


def test_sift_cache_avoids_second_call(tmp_path, monkeypatch):
    calls = []
    def evaluate(state, questions, *, key, **kw):
        calls.append(1)
        return {"model": jev.MODEL,
                "answers": {n: {"type": "noul", "noul": 0.5} for n in questions},
                "usage": {"input_tokens": 1, "output_tokens": 1}}
    monkeypatch.setattr(jev, "evaluate", evaluate)
    monkeypatch.setattr(jev, "credential", lambda root, env=None: "k")
    candidates = [{"id": "a", "snippet": "x"}]
    search_sift.sift(tmp_path, "q", candidates)
    search_sift.sift(tmp_path, "q", candidates)
    assert len(calls) == 1
