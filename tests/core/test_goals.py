"""Goal-file parser and validator (spec 06, CONTRACTS.md).

M0 pass criterion names this one explicitly: "goal without `### Checks` included".
"""

from __future__ import annotations

import pytest

from hx.errors import ValidationError
from hx.goals import parse_goal, parse_goal_text

GOOD = """## Goal

Rewrite the importer so it streams instead of buffering.

## Definition of done

- [ ] the importer streams
- [ ] the suite passes

### Checks

```bash
pytest -q tests/importer
test -s docs/importer.md
```
"""

WITH_FRONTMATTER = "---\nafter: [eng-000]\n---\n" + GOOD


def malformed():
    return {
        "no ## Goal": GOOD.replace("## Goal", "## Task"),
        "no ## Definition of done": GOOD.replace("## Definition of done", "## Done when"),
        "no ### Checks heading": GOOD.replace("### Checks\n", ""),
        "### Checks with no bash block": GOOD.replace("```bash", "```python"),
        "### Checks with an empty bash block": GOOD.replace(
            "pytest -q tests/importer\ntest -s docs/importer.md", ""
        ),
        "### Checks with only comments": GOOD.replace(
            "pytest -q tests/importer\ntest -s docs/importer.md", "# nothing to run"
        ),
        "empty ## Goal": GOOD.replace("Rewrite the importer so it streams instead of buffering.", ""),
        "two ## Goal sections": GOOD + "\n## Goal\n\nAnd another thing.\n",
        "two ### Checks": GOOD + "\n### Checks\n\n```bash\ntrue\n```\n",
        # The v1 cut (spec 14 D25) left a goal with no frontmatter at all: no `after`, no
        # dependency fields, nothing.
        "frontmatter `after`": WITH_FRONTMATTER,
        "any other frontmatter key": "---\npriority: high\n---\n" + GOOD,
    }


def test_good_goal():
    goal = parse_goal_text(GOOD, "goal.md")
    assert goal.goal.startswith("Rewrite the importer")
    assert "pytest -q tests/importer" in goal.checks
    assert "the suite passes" in goal.definition_of_done


def test_a_goal_has_no_frontmatter(tmp_path):
    """spec 14 D25: `after` is gone, and with it every goal frontmatter key."""
    path = tmp_path / "eng-001.md"
    path.write_text(WITH_FRONTMATTER)
    with pytest.raises(ValidationError) as exc:
        parse_goal(path)
    assert "a goal has no frontmatter" in str(exc.value)
    assert not hasattr(parse_goal_text(GOOD, "goal.md"), "after")


def test_the_goal_text_is_kept_verbatim_for_the_work_item():
    goal = parse_goal_text(GOOD, "goal.md")
    assert "## Goal" in goal.body and "### Checks" in goal.body


def test_checks_survive_indentation_and_tildes():
    text = GOOD.replace("```bash", "~~~bash").replace("```", "~~~")
    assert "pytest" in parse_goal_text(text, "goal.md").checks


@pytest.mark.parametrize("name", sorted(malformed()))
def test_every_malformed_goal_is_rejected_by_name(name, tmp_path):
    path = tmp_path / "eng-001.md"
    path.write_text(malformed()[name])
    with pytest.raises(ValidationError) as exc:
        parse_goal(path)
    message = str(exc.value)
    assert str(path) in message, f"{name}: message does not name the file: {message}"
    # Section rules cite spec 06 by name; frontmatter rules say which frontmatter rule broke.
    assert "spec 06" in message or "frontmatter" in message, (
        f"{name}: message does not name the rule: {message}"
    )


@pytest.mark.parametrize(
    "name",
    [
        "frontmatter `after`",
        "no ## Goal",
        "no ## Definition of done",
        "no ### Checks heading",
        "### Checks with no bash block",
        "### Checks with an empty bash block",
    ],
)
def test_the_section_rules_cite_spec_06(name, tmp_path):
    path = tmp_path / "eng-001.md"
    path.write_text(malformed()[name])
    with pytest.raises(ValidationError) as exc:
        parse_goal(path)
    assert "spec 06" in str(exc.value)


def test_a_missing_goal_file_is_rejected(tmp_path):
    with pytest.raises(ValidationError) as exc:
        parse_goal(tmp_path / "nope.md")
    assert "goal file not found" in str(exc.value)


def test_a_heading_inside_a_fence_is_not_a_section(tmp_path):
    text = GOOD.replace(
        "pytest -q tests/importer", "grep -q '## Goal' docs/importer.md"
    )
    goal = parse_goal_text(text, "goal.md")
    assert "## Goal" in goal.checks


def test_the_skeleton_goal_template_passes_its_own_rules():
    """`templates/goal.md` is the gtm lane's; a break is reported, not fixed."""
    from hx.install import skeleton_dir

    path = skeleton_dir() / "templates" / "goal.md"
    if path.exists():
        assert parse_goal(path).checks.strip()
