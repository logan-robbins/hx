"""Jev where it is appropriate: classification second opinion, search sifting."""
from __future__ import annotations

from hx import code_index, jev, learn


def _fake_choice(monkeypatch, choice="testing"):
    def evaluate(state, questions, *, key, root, endpoint=None, timeout=None):
        answers = {}
        for name, question in questions.items():
            if question["type"] == "choice":
                answers[name] = {"type": "choice", "choice": choice, "confidence": 0.88,
                                 "probabilities": {k: (0.88 if k == choice else 0.12 / (len(question["criteria"]) - 1))
                                                   for k in question["criteria"]}}
            else:
                answers[name] = {"type": "noul", "noul": 0.9}
        return {"model": jev.MODEL, "answers": answers,
                "usage": {"input_tokens": 5, "output_tokens": 5}}
    monkeypatch.setattr(jev, "evaluate", evaluate)
    monkeypatch.setattr(jev, "credential", lambda root, env=None: "k")


def test_classify_decision_uses_jev_choice(tmp_path, monkeypatch):
    _fake_choice(monkeypatch)
    result = learn.classify_decision(tmp_path, "the suite passed without exercising the code",
                                     "tests must exercise the behaviour", env={})
    assert result["source"] == "jev"
    assert result["category"] == "testing"
    assert result["confidence"] == 0.88


def test_classify_decision_falls_back_deterministically(tmp_path, monkeypatch):
    def unavailable(state, questions, *, key, **kw):
        raise jev.Unavailable("timeout")
    monkeypatch.setattr(jev, "evaluate", unavailable)
    monkeypatch.setattr(jev, "credential", lambda root, env=None: "k")
    result = learn.classify_decision(tmp_path, "test passed against wrong fixture",
                                     "tests must use real fixtures", env={})
    assert result["source"] == "deterministic"
    assert result["category"] in learn.CATEGORY_NAMES


def test_code_search_sifted_merges_scores(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    (repo / "src").mkdir(parents=True)
    (repo / "src" / "auth.py").write_text("def login(user):\n    return verify(user)\n")
    (repo / "src" / "db.py").write_text("def connect():\n    return open_db()\n")
    root = tmp_path / "root"
    root.mkdir()
    code_index.index_repo(root, repo)
    monkeypatch.setattr("hx.search_sift.sift",
                        lambda root_, q, candidates, env=None: {
                            "status": "complete", "sifted": True,
                            "results": [{**c, "jev_score": 0.9 if "auth" in c["id"] else 0.1}
                                        for c in candidates]})
    results = code_index.search_sifted(root, "return", "which file handles user login?")
    assert results[0]["path"].endswith("auth.py")
    assert results[0]["jev_score"] == 0.9
    assert {r["path"] for r in results} == {"src/auth.py", "src/db.py"}
