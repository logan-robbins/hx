"""`hx recall` — last-resort file-memory search over completed Work Items.

Bounds are the feature: a query or filter is required, hits and excerpts are capped,
and only completed bodies are ever scanned.
"""

from __future__ import annotations


def complete_one(instance, hx, launched, goals, item_id="eng-001", **kwargs):
    launched(item_id)
    goals(item_id, **kwargs)
    assert hx("dispatch", item_id, f"run/goal-{item_id}.md", cwd=instance).returncode == 0
    assert hx("complete", "done", harness_id=item_id).returncode == 0
    return instance


def test_recall_finds_a_completed_goal_by_query(instance, hx, launched, goals):
    complete_one(instance, hx, launched, goals, goal="Stream the importer for speed.")
    result = hx("recall", "importer")
    assert result.returncode == 0, result.stderr
    assert "HX-RECALL" in result.stdout
    assert "eng-001-complete.md" in result.stdout


def test_recall_reports_none_when_nothing_matches(instance, hx, launched, goals):
    complete_one(instance, hx, launched, goals)
    result = hx("recall", "something no work item ever said")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "HX-RECALL none"


def test_recall_never_scans_working_items(instance, hx, launched, goals):
    launched("eng-001")
    goals("eng-001", goal="A working goal about wombats.")
    assert hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance).returncode == 0
    result = hx("recall", "wombats")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "HX-RECALL none"


def test_recall_searches_benched_bodies(instance, hx, launched, goals):
    complete_one(instance, hx, launched, goals, goal="Bench me about badgers.")
    assert hx("bench", "eng-001").returncode == 0
    result = hx("recall", "badgers")
    assert result.returncode == 0, result.stderr
    assert "archive/eng-001-" in result.stdout


def test_recall_refuses_an_unbounded_search(instance, hx):
    result = hx("recall")
    assert result.returncode != 0
    assert "needs a query or a filter" in result.stderr


def test_recall_caps_limit_and_excerpts(instance, hx, launched, goals):
    complete_one(instance, hx, launched, goals)
    result = hx("recall", "Tasks", "--limit", "99")
    assert result.returncode != 0
    assert "1..20" in result.stderr
