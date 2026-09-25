"""`hx distill <id> <distilled-file> [--memory]` (spec 08).

The failure this removes: resumes and amends append `### Goal addendum` sections
and `tasks.json` records without bound, and every one rides the agent's context
file after every boundary. Distilling swaps the accumulation for the Partner's
distillation: promoted invariants above the header (where `hx compile` keeps
them), superseded addenda gone, the goal and its current gate untouched.
"""

from __future__ import annotations

import pytest

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


def agents_of(instance, item_id="eng-001"):
    return instance / "config" / item_id / "AGENTS.md"


def test_distill_shrinks_addenda_into_directives(accumulated, hx):
    instance = accumulated
    distilled = instance / "run" / "distilled-eng-001.md"
    distilled.write_text("The gate names README.md, not DELIVERABLE.md.\n")
    result = hx("distill", "eng-001", str(distilled), cwd=instance)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().startswith("HX-DISTILL eng-001 directives=created")
    assert not distilled.exists(), "the distilled file is consumed like an addendum"

    # The accumulation is gone from both places hx reads.
    body = work_item(instance).read_text()
    assert "### Goal addendum" not in body
    entry = tasks_of(instance)["eng-001"]
    assert entry["addenda"] == []

    # The gate the last addendum installed survives the prose that carried it.
    assert NEW_CHECKS in body and OLD_CHECKS not in body
    assert parse_goal_text(entry["goal"], "tasks.json").checks == NEW_CHECKS

    # The promotion sits above the header, fenced so the next distill replaces it.
    upper, _, lower = agents_of(instance).read_text().partition("## UPDATES BELOW ONLY")
    assert "## Distilled directives" in upper
    assert "The gate names README.md" in upper
    assert "hx:distilled begin" in upper
    assert "Things I learned: nothing yet." in lower, "below-header memory is kept"


def test_second_distill_replaces_instead_of_appending(accumulated, hx):
    instance = accumulated
    first = instance / "run" / "d1.md"
    first.write_text("First truth.\n")
    assert hx("distill", "eng-001", str(first), cwd=instance).returncode == 0
    second = instance / "run" / "d2.md"
    second.write_text("Second truth.\n")
    result = hx("distill", "eng-001", str(second), cwd=instance)
    assert result.returncode == 0, result.stderr
    assert "directives=replaced" in result.stdout
    upper = agents_of(instance).read_text().split("## UPDATES BELOW ONLY")[0]
    assert upper.count("## Distilled directives") == 1
    assert "Second truth." in upper and "First truth." not in upper


def test_memory_flag_replaces_below_header_memory(accumulated, hx):
    instance = accumulated
    distilled = instance / "run" / "d3.md"
    distilled.write_text("Still true.\n\n## Memory\n\nKept fact.\n")
    result = hx("distill", "eng-001", str(distilled), "--memory", cwd=instance)
    assert result.returncode == 0, result.stderr
    assert "memory=replaced" in result.stdout
    _, _, lower = agents_of(instance).read_text().partition("## UPDATES BELOW ONLY")
    assert lower.strip() == "Kept fact."


def test_memory_flag_refuses_without_a_memory_section(accumulated, hx):
    instance = accumulated
    distilled = instance / "run" / "d4.md"
    distilled.write_text("Still true.\n")
    before = agents_of(instance).read_text()
    result = hx("distill", "eng-001", str(distilled), "--memory", cwd=instance)
    assert result.returncode != 0 and "no `## Memory` section" in result.stderr
    assert agents_of(instance).read_text() == before, "a refusal writes nothing"
    assert distilled.exists(), "a refusal consumes nothing"


def test_empty_distillation_is_a_refusal(accumulated, hx):
    instance = accumulated
    distilled = instance / "run" / "d5.md"
    distilled.write_text("  \n")
    before = agents_of(instance).read_text()
    result = hx("distill", "eng-001", str(distilled), cwd=instance)
    assert result.returncode != 0
    assert agents_of(instance).read_text() == before


def test_a_worker_cannot_distill(accumulated, hx):
    instance = accumulated
    distilled = instance / "run" / "d6.md"
    distilled.write_text("Still true.\n")
    result = hx("distill", "eng-001", str(distilled), cwd=instance, harness_id="eng-001")
    assert result.returncode != 0 and "Partner" in result.stderr
