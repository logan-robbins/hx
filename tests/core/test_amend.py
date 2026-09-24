"""`hx amend <id> <addendum-file>`, and `hx resume` carrying a `### Checks` block (spec 08).

The failure this removes: an in-flight Checks block could only be corrected by hand-editing
`tasks.json[<id>].goal`, because `hx resume` refuses a `working` item and its addendum never
reached the block `hx complete done` runs. Every test here runs the direction that must fail
as well as the one that must pass.
"""

from __future__ import annotations

import json

import pytest

from hx.goals import addendum_checks, parse_goal_text, replace_checks

from .conftest import GOAL
from .test_transitions import control_manifest, item_state, pasted, tasks_of, wait_for_goal

FENCE = chr(96) * 3

#: The goal's own gate cannot pass in the scratch worktree; the amended one can.
OLD_CHECKS = "test -f DELIVERABLE.md"
NEW_CHECKS = "test -f README.md"


def checks_addendum(checks=NEW_CHECKS, prose="The gate named the wrong file.", fence=FENCE):
    return f"{prose}\n\n### Checks\n\n{fence}bash\n{checks}\n{fence}\n"


def write_addendum(instance, text, item_id="eng-001"):
    path = instance / "run" / f"amend-{item_id}.md"
    path.write_text(text)
    return path


def work_item(instance, item_id="eng-001", state="working"):
    return instance / "pods" / "engineers" / f"{item_id}-{state}.md"


def dod_of(text):
    return text.split("## Definition of done", 1)[1].split("\n## ", 1)[0]


@pytest.fixture
def working(instance, hx, launched, goals):
    launched("eng-001")
    goals("eng-001", checks=OLD_CHECKS)
    result = hx("dispatch", "eng-001", "run/goal-eng-001.md", cwd=instance)
    assert result.returncode == 0, result.stdout + result.stderr
    wait_for_goal(instance, "eng-001")
    return instance


def test_amend_replaces_the_checks_of_a_working_item(working, hx):
    instance = working
    # The fail direction first: the dispatched gate names a file the work never makes.
    before = hx("complete", "done", harness_id="eng-001")
    assert before.returncode == 1 and "HX-CHECK-FAILED eng-001" in before.stdout
    typed = pasted(instance, "eng-001")

    path = write_addendum(instance, checks_addendum())
    result = hx("amend", "eng-001", str(path), cwd=instance)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "HX-AMEND eng-001 checks=replaced addenda=1"

    # No state change, no paste, and the addendum file is consumed.
    assert item_state(instance, "eng-001") == "working"
    assert pasted(instance, "eng-001") == typed, "amend pastes nothing into the pane"
    assert not path.exists()

    entry = tasks_of(instance)["eng-001"]
    assert parse_goal_text(entry["goal"], "tasks.json").checks == NEW_CHECKS
    assert OLD_CHECKS not in entry["goal"]
    assert entry["outcome"] is None and len(entry["addenda"]) == 1
    assert entry["addenda"][0]["text"] == checks_addendum().strip("\n")

    body = work_item(instance).read_text()
    dod = dod_of(body)
    assert NEW_CHECKS in dod and OLD_CHECKS not in dod
    goal_part = body.split("## Definition of done", 1)[0]
    assert "### Goal addendum" in goal_part and "The gate named the wrong file." in goal_part

    task = hx("task", harness_id="eng-001")
    assert task.returncode == 0 and NEW_CHECKS in task.stdout.split("## Goal addendum")[0]

    # And the worker's next completion runs the amended gate.
    after = hx("complete", "done", harness_id="eng-001")
    assert after.returncode == 0, after.stdout + after.stderr
    assert after.stdout.strip().split("\n")[-1] == "HX-COMPLETE eng-001 done"


def test_amend_without_checks_keeps_the_gate(working, hx):
    instance = working
    goal_before = tasks_of(instance)["eng-001"]["goal"]
    dod_before = dod_of(work_item(instance).read_text())

    path = write_addendum(instance, "Prose only: prefer the flat list.\n")
    result = hx("amend", "eng-001", str(path), cwd=instance)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "HX-AMEND eng-001 checks=kept addenda=1"

    entry = tasks_of(instance)["eng-001"]
    assert entry["goal"] == goal_before
    assert entry["addenda"][0]["text"] == "Prose only: prefer the flat list."
    body = work_item(instance).read_text()
    assert dod_of(body) == dod_before
    assert "Prose only: prefer the flat list." in body.split("## Definition of done", 1)[0]

    second = write_addendum(instance, "And a second note.\n")
    assert hx("amend", "eng-001", str(second), cwd=instance).stdout.strip() == (
        "HX-AMEND eng-001 checks=kept addenda=2"
    )


@pytest.mark.parametrize(
    "addendum",
    [
        checks_addendum(checks="# only a comment, nothing runs"),
        "### Checks\n\nno fenced block under the heading at all\n",
        checks_addendum() + "\n" + checks_addendum(checks="true"),
        f"### Checks\n\n{FENCE}bash\ntest -f README.md\n",
    ],
    ids=["comment-only-block", "heading-without-block", "two-checks-headings", "unclosed-fence"],
)
def test_amend_refuses_a_block_that_breaks_the_goal_and_changes_nothing(working, hx, addendum):
    instance = working
    path = write_addendum(instance, addendum)
    before = control_manifest(instance)

    result = hx("amend", "eng-001", str(path), cwd=instance)
    assert result.returncode == 1, result.stdout + result.stderr
    assert "refuse" in result.stderr and "nothing was written" in result.stderr
    assert control_manifest(instance) == before, "a refused amend writes nothing"
    assert path.exists(), "a refused addendum is not consumed"


def test_amend_is_the_partners(working, hx):
    instance = working
    path = write_addendum(instance, checks_addendum())
    before = control_manifest(instance)
    result = hx("amend", "eng-001", str(path), cwd=instance, harness_id="eng-001")
    assert result.returncode == 1 and "is the Partner's" in result.stderr
    assert control_manifest(instance) == before


def test_amend_refuses_an_idle_item_and_the_partner(instance, hx, launched):
    launched("eng-001")
    path = write_addendum(instance, checks_addendum())
    idle = hx("amend", "eng-001", str(path), cwd=instance)
    assert idle.returncode == 1 and "`idle`" in idle.stderr
    partner = hx("amend", "partner", str(path), cwd=instance)
    assert partner.returncode == 1 and "Partner has no work item" in partner.stderr
    assert path.exists()


def test_amend_reaches_a_complete_item(working, hx):
    """A paused item can be corrected before its resume; the state stays `complete`."""
    instance = working
    assert hx("complete", "decision", harness_id="eng-001").returncode == 0
    path = write_addendum(instance, checks_addendum())
    result = hx("amend", "eng-001", str(path), cwd=instance)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "HX-AMEND eng-001 checks=replaced addenda=1"
    assert item_state(instance, "eng-001") == "complete"
    assert tasks_of(instance)["eng-001"]["outcome"] == "decision"
    assert NEW_CHECKS in dod_of(work_item(instance, state="complete").read_text())


def test_resume_replaces_the_checks_when_its_addendum_carries_them(working, hx):
    instance = working
    assert hx("complete", "blocked", harness_id="eng-001").returncode == 0
    path = write_addendum(instance, checks_addendum())
    result = hx("resume", "eng-001", str(path), cwd=instance)
    assert result.returncode == 0, result.stderr
    lines = result.stdout.strip().split("\n")
    assert lines[0] == "HX-AMEND eng-001 checks=replaced addenda=1"
    assert lines[-1].startswith("HX-RESUME eng-001 working")

    entry = tasks_of(instance)["eng-001"]
    assert parse_goal_text(entry["goal"], "tasks.json").checks == NEW_CHECKS
    assert NEW_CHECKS in dod_of(work_item(instance).read_text())
    after = hx("complete", "done", harness_id="eng-001")
    assert after.returncode == 0, after.stdout + after.stderr


def test_resume_refuses_a_broken_block_before_it_writes_anything(working, hx):
    instance = working
    assert hx("complete", "blocked", harness_id="eng-001").returncode == 0
    path = write_addendum(instance, checks_addendum(checks="# nothing"))
    before = control_manifest(instance)
    result = hx("resume", "eng-001", str(path), cwd=instance)
    assert result.returncode == 1 and "nothing was written" in result.stderr
    assert control_manifest(instance) == before
    assert item_state(instance, "eng-001") == "complete"


def test_amend_keeps_a_longer_fence_intact():
    """A block that itself holds a shorter fence travels with its own markers."""
    goal = GOAL.format(goal="Do it.", checks=OLD_CHECKS)
    inner = f"cat <<'MD' > x.md\n{FENCE}\nMD\n{NEW_CHECKS}"
    fence = addendum_checks(checks_addendum(checks=inner, fence="````"), "addendum")
    amended = replace_checks(goal, fence, "goal")
    assert parse_goal_text(amended, "goal").checks == inner
    assert json.dumps(amended).count(OLD_CHECKS) == 0
