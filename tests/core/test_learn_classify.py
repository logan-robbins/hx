"""Learnings: classify at the moment of learning, detect repeats, guard invariants."""
from __future__ import annotations

from hx import learn


def test_classify_suggests_category_from_definitions():
    ranked = learn.classify("ran the test suite against the wrong fixture and it passed vacuously",
                            "a test must prove the behaviour with a real fixture")
    assert ranked[0]["category"] in {"testing", "evidence"}
    assert ranked[0]["score"] > 0


def test_classify_ranks_all_categories():
    ranked = learn.classify("anything", "anything")
    assert {row["category"] for row in ranked} == set(learn.CATEGORY_NAMES)


def test_repeat_detects_existing_rule(instance):
    learn.propose(instance, signal="status claimed from memory instead of a tool run",
                  check="status comes from a tool run", evidence="run 1", category="evidence",
                  env={"HARNESS_ID": "eng-001"})
    pending = learn.list_proposals(learn.learnings_path(instance, {}))
    learn.accept(instance, pending[0]["id"], env={"HARNESS_ID": "partner"})
    result = learn.repeat(instance, "status claimed from memory instead of a tool run")
    assert result["verdict"] == "repeat"
    assert result["rule"] is not None


def test_repeat_new_signal_is_new(instance):
    result = learn.repeat(instance, "quantum flux capacitor inverted the polarity")
    assert result["verdict"] == "new"


def test_invariant_report_fits_budget(instance):
    report = learn.invariant_report(instance)
    assert report["rules"] == 0
    assert report["within_budget"] is True
    assert report["ceiling_bytes"] > 0
