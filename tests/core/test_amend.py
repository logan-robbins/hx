"""`hx amend <id> <addendum-file>`, and the addendum line `hx resume` prints (spec 08).

An addendum is prose the worker reads beneath `## Goal`: the worker owns its
`## Definition of done`, so there is no acceptance block for an addendum to replace, and one
that carries a `### Checks` heading is refused before anything is written. Every test here
runs the direction that must fail as well as the one that must pass.
"""

from __future__ import annotations

import pytest

from .conftest import write_evidence
from .test_transitions import control_manifest, item_state, pasted, tasks_of, wait_for_goal

FENCE = chr(96) * 3


def checks_addendum(checks="test -f README.md", prose="The gate named the wrong file."):
    return f"{prose}\n\n### Checks\n\n{FENCE}bash\n{checks}\n{FENCE}\n"


def write_addendum(instance, text, item_id="eng-001"):
    path = instance / "run" / f"amend-{item_id}.md"
    path.write_text(text)
    return path


def work_item(instance, item_id="eng-001", state="working"):
    return instance / "pods" / "engineers" / f"{item_id}-{state}.md"


def goal_part(text):
    """Everything above the worker's own `## Definition of done`: the goal and its addenda."""
    return text.split("## Definition of done", 1)[0]


@pytest.fixture
def working(instance, hx, launched, goals):
    launched("eng-001")
    goals("eng-001")
    result = hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance)
    assert result.returncode == 0, result.stdout + result.stderr
    wait_for_goal(instance, "eng-001")
    return instance


def test_amend_appends_to_a_working_item_beneath_the_goal(working, hx):
    instance = working
    goal_before = tasks_of(instance)["eng-001"]["goal"]
    typed = pasted(instance, "eng-001")

    path = write_addendum(instance, "Prose only: prefer the flat list.\n")
    result = hx("amend", "eng-001", str(path), cwd=instance)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "HX-AMEND eng-001 addenda=1"

    # No state change, no paste, and the addendum file is consumed.
    assert item_state(instance, "eng-001") == "working"
    assert pasted(instance, "eng-001") == typed, "amend pastes nothing into the pane"
    assert not path.exists()

    entry = tasks_of(instance)["eng-001"]
    assert entry["goal"] == goal_before, "the goal paragraph itself is never rewritten"
    assert entry["outcome"] is None
    assert entry["addenda"][0]["text"] == "Prose only: prefer the flat list."
    above = goal_part(work_item(instance).read_text())
    assert "### Goal addendum" in above and "Prose only: prefer the flat list." in above

    task = hx("task", harness_id="eng-001")
    assert task.returncode == 0 and "Prose only: prefer the flat list." in task.stdout

    second = write_addendum(instance, "And a second note.\n")
    assert hx("amend", "eng-001", str(second), cwd=instance).stdout.strip() == (
        "HX-AMEND eng-001 addenda=2"
    )


@pytest.mark.parametrize(
    "addendum",
    [checks_addendum(), "### Checks\n\nno fenced block under the heading at all\n",
     "Prose first.\n\n## Checks\n\nanother level\n"],
    ids=["checks-block", "heading-without-block", "level-two-heading"],
)
def test_amend_refuses_a_checks_heading_and_changes_nothing(working, hx, addendum):
    instance = working
    path = write_addendum(instance, addendum)
    before = control_manifest(instance)

    result = hx("amend", "eng-001", str(path), cwd=instance)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "refuse" in result.stderr and "nothing was written" in result.stderr
    assert "no Checks block to replace" in result.stderr
    assert control_manifest(instance) == before, "a refused amend writes nothing"
    assert path.exists(), "a refused addendum is not consumed"


def test_a_checks_fence_inside_prose_is_only_prose(working, hx):
    """A heading inside a fence is not a heading: the worker reads it as text."""
    instance = working
    path = write_addendum(instance, f"Run this:\n\n{FENCE}\n### Checks\n{FENCE}\n")
    assert hx("amend", "eng-001", str(path), cwd=instance).returncode == 0


def test_amend_is_the_partners(working, hx):
    instance = working
    path = write_addendum(instance, "A note.\n")
    before = control_manifest(instance)
    result = hx("amend", "eng-001", str(path), cwd=instance, harness_id="eng-001")
    assert result.returncode == 1 and "is the Partner's" in result.stderr
    assert control_manifest(instance) == before


def test_amend_refuses_an_idle_item_and_the_partner(instance, hx, launched):
    launched("eng-001")
    path = write_addendum(instance, "A note.\n")
    idle = hx("amend", "eng-001", str(path), cwd=instance)
    assert idle.returncode == 1 and "`idle`" in idle.stderr
    partner = hx("amend", "partner", str(path), cwd=instance)
    assert partner.returncode == 1 and "Partner has no work item" in partner.stderr
    assert path.exists()


def test_amend_reaches_a_complete_item(working, hx):
    """A paused item can be corrected before its resume; the state stays `complete`."""
    instance = working
    assert hx("complete", "decision", harness_id="eng-001").returncode == 0
    path = write_addendum(instance, "Answer: the flat list.\n")
    result = hx("amend", "eng-001", str(path), cwd=instance)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "HX-AMEND eng-001 addenda=1"
    assert item_state(instance, "eng-001") == "complete"
    assert tasks_of(instance)["eng-001"]["outcome"] == "decision"
    assert "Answer: the flat list." in goal_part(work_item(instance, state="complete").read_text())


def test_resume_prints_the_amend_line_and_the_worker_finishes_on_evidence(working, hx):
    instance = working
    assert hx("complete", "blocked", harness_id="eng-001").returncode == 0
    path = write_addendum(instance, "The service is up now; continue.\n")
    result = hx("resume", "eng-001", str(path), cwd=instance)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().split("\n")
    assert lines[0] == "HX-AMEND eng-001 addenda=1"
    assert lines[-1].startswith("HX-RESUME eng-001 working")

    refused = hx("complete", "done", harness_id="eng-001")
    assert refused.returncode == 1 and "`## Evidence`" in refused.stdout
    write_evidence(instance, "eng-001")
    after = hx("complete", "done", harness_id="eng-001")
    assert after.returncode == 0, after.stdout + after.stderr


def test_resume_refuses_a_checks_heading_before_it_writes_anything(working, hx):
    instance = working
    assert hx("complete", "blocked", harness_id="eng-001").returncode == 0
    path = write_addendum(instance, checks_addendum())
    before = control_manifest(instance)
    result = hx("resume", "eng-001", str(path), cwd=instance)
    assert result.returncode == 1 and "nothing was written" in result.stderr
    assert control_manifest(instance) == before
    assert item_state(instance, "eng-001") == "complete"
