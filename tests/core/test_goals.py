"""Goal-file parser and validator.

A goal is one `## Goal` paragraph, optionally followed by one to three sentences on what it
depends on, and nothing else: the worker writes its own `## Definition of done` in the work
item on top of its role's standard set, and `hx complete done` requires its `## Evidence`.
"""

from __future__ import annotations

import pytest

from hx.errors import ValidationError
from hx.goals import parse_goal, parse_goal_text

GOOD = """## Goal

Rewrite the importer so it streams instead of buffering: memory stays flat on a 10 GB export
and the CLI's output is unchanged. It depends on `eng-002`'s reader interface, already merged.
"""

WITH_FRONTMATTER = "---\nafter: [eng-000]\n---\n" + GOOD

EXTRA_SECTIONS = GOOD + """
## Definition of done

- [ ] the importer streams

### Checks

```bash
pytest -q tests/importer
```
"""


def malformed():
    return {
        "no ## Goal": GOOD.replace("## Goal", "## Task"),
        "empty ## Goal": "## Goal\n\n",
        "two ## Goal sections": GOOD + "\n## Goal\n\nAnd another thing.\n",
        "a ## Definition of done": EXTRA_SECTIONS,
        "a ### Checks": GOOD + "\n### Checks\n\n```bash\ntrue\n```\n",
        "any other heading": GOOD + "\n## Notes\n\nmore\n",
        "text above ## Goal": "Context first.\n\n" + GOOD,
        "frontmatter `after`": WITH_FRONTMATTER,
        "any other frontmatter key": "---\npriority: high\n---\n" + GOOD,
    }


def test_good_goal():
    goal = parse_goal_text(GOOD, "goal.md")
    assert goal.goal.startswith("Rewrite the importer")
    assert "depends on `eng-002`" in goal.goal


def test_a_goal_has_no_frontmatter(tmp_path):
    path = tmp_path / "eng-001.md"
    path.write_text(WITH_FRONTMATTER)
    with pytest.raises(ValidationError) as exc:
        parse_goal(path)
    assert "a goal has no frontmatter" in str(exc.value)


def test_the_goal_text_is_kept_verbatim_for_the_work_item():
    goal = parse_goal_text(GOOD, "goal.md")
    assert goal.body == GOOD


@pytest.mark.parametrize("name", sorted(malformed()))
def test_every_malformed_goal_is_rejected_by_name(name, tmp_path):
    path = tmp_path / "eng-001.md"
    path.write_text(malformed()[name])
    with pytest.raises(ValidationError) as exc:
        parse_goal(path)
    message = str(exc.value)
    assert str(path) in message, f"{name}: message does not name the file: {message}"
    assert "goal is" in message or "frontmatter" in message, (
        f"{name}: message does not name the rule: {message}"
    )


def test_a_missing_goal_file_is_rejected(tmp_path):
    with pytest.raises(ValidationError) as exc:
        parse_goal(tmp_path / "nope.md")
    assert "planning goal input not found" in str(exc.value)


def test_a_heading_inside_a_fence_is_not_a_section():
    text = GOOD + "\n```bash\n## not a heading\n```\n"
    assert "## not a heading" in parse_goal_text(text, "goal.md").goal


def test_planning_goal_input_is_one_paragraph(tmp_path):
    path = tmp_path / "goal.md"
    path.write_text("## Goal\nReturn the requested behavior through the published API.\n")
    goal = parse_goal(path)
    assert goal.goal == "Return the requested behavior through the published API."
