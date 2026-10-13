"""`hx distill <id> <distilled-file>` (spec 08).

The failure this removes: resumes and amends append `### Goal addendum` sections
and `tasks.json` records without bound, and every one rides the agent's context
file after every boundary. Distilling swaps the accumulation for the Partner's
distillation: the directives become a learnings text proposal (a lesson is owed to
every agent of the role, not to one per-id file), superseded addenda gone, the goal
and its current gate untouched.
"""

from __future__ import annotations

import pytest

from hx import learn
from hx.goals import parse_goal_text

from .test_amend import OLD_CHECKS, checks_addendum, work_item, write_addendum
from .test_transitions import tasks_of, wait_for_goal

NEW_CHECKS = "test -f README.md"


@pytest.fixture
def accumulated(instance, hx, launched, goals):
    launched("eng-001")
    goals("eng-001", checks=OLD_CHECKS)
    result = hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance)
    assert result.returncode == 0, result.stdout + result.stderr
    wait_for_goal(instance, "eng-001")
    for prose in ("First correction.", checks_addendum()):
        path = write_addendum(instance, prose)
        result = hx("amend", "eng-001", str(path), cwd=instance)
        assert result.returncode == 0, result.stderr
    assert len(tasks_of(instance)["eng-001"]["addenda"]) == 2
    return instance


def pending(instance):
    return learn.list_proposals(instance / "LEARNINGS")


def test_distill_shrinks_addenda_into_a_learnings_proposal(accumulated, hx):
    instance = accumulated
    distilled = instance / "run" / "distilled-eng-001.md"
    distilled.write_text("The gate names README.md, not DELIVERABLE.md.\n")
    result = hx("distill", "eng-001", str(distilled), cwd=instance)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("HX-DISTILL eng-001 proposal=")
    assert not distilled.exists(), "the distilled file is consumed like an addendum"

    # The accumulation is gone from both places hx reads.
    body = work_item(instance).read_text()
    assert "### Goal addendum" not in body
    entry = tasks_of(instance)["eng-001"]
    assert entry["addenda"] == []

    # The gate the last addendum installed survives the prose that carried it.
    assert NEW_CHECKS in body and OLD_CHECKS not in body
    assert parse_goal_text(entry["goal"], "tasks.json").checks == NEW_CHECKS

    # The directives wait for review as prose, authored as the agent they came from, so the
    # Partner can review them; no per-id file is written.
    [proposal] = pending(instance)
    assert proposal["kind"] == "text" and proposal["author"] == "eng-001"
    assert "The gate names README.md" in proposal["text"]
    assert not (instance / "config" / "eng-001" / "AGENTS.md").exists()
    review = hx("learn", "pending", cwd=instance)
    assert "The gate names README.md" in review.stdout and "HX-LEARN-PENDING 1" in review.stdout
    accepted = hx("learn", "accept", proposal["id"], "--signal", "the gate names a file the goal does not",
                  "--check", "read ### Checks before the prose", "--category", "delivery", cwd=instance)
    assert accepted.returncode == 0, accepted.stderr


def test_each_distill_is_its_own_proposal(accumulated, hx):
    instance = accumulated
    for name, text in (("d1.md", "First truth.\n"), ("d2.md", "Second truth.\n")):
        path = instance / "run" / name
        path.write_text(text)
        assert hx("distill", "eng-001", str(path), cwd=instance).returncode == 0
    assert sorted(p["text"] for p in pending(instance)) == ["First truth.", "Second truth."]


def test_the_memory_flag_is_gone(accumulated, hx):
    instance = accumulated
    distilled = instance / "run" / "d3.md"
    distilled.write_text("Still true.\n")
    result = hx("distill", "eng-001", str(distilled), "--memory", cwd=instance)
    assert result.returncode != 0
    assert distilled.exists() and pending(instance) == []


def test_empty_distillation_is_a_refusal(accumulated, hx):
    instance = accumulated
    distilled = instance / "run" / "d5.md"
    distilled.write_text("  \n")
    before = work_item(instance).read_text()
    result = hx("distill", "eng-001", str(distilled), cwd=instance)
    assert result.returncode != 0
    assert work_item(instance).read_text() == before and pending(instance) == []


def test_a_worker_cannot_distill(accumulated, hx):
    instance = accumulated
    distilled = instance / "run" / "d6.md"
    distilled.write_text("Still true.\n")
    result = hx("distill", "eng-001", str(distilled), cwd=instance, harness_id="eng-001")
    assert result.returncode != 0 and "Partner" in result.stderr
