"""Order-file parser and validator (spec 06, CONTRACTS.md).

M0 pass criterion names this one explicitly: "order without `### Checks` included".
"""

from __future__ import annotations

import pytest

from hx.errors import ValidationError
from hx.orders import parse_order, parse_order_text

GOOD = """---
after: [eng-000, eng-002]
---
## Order

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

NO_FRONTMATTER = GOOD.split("---\n", 2)[2]


def malformed():
    return {
        "no ## Order": GOOD.replace("## Order", "## Task"),
        "no ## Definition of done": GOOD.replace("## Definition of done", "## Done when"),
        "no ### Checks heading": GOOD.replace("### Checks\n", ""),
        "### Checks with no bash block": GOOD.replace("```bash", "```python"),
        "### Checks with an empty bash block": GOOD.replace(
            "pytest -q tests/importer\ntest -s docs/importer.md", ""
        ),
        "### Checks with only comments": GOOD.replace(
            "pytest -q tests/importer\ntest -s docs/importer.md", "# nothing to run"
        ),
        "empty ## Order": GOOD.replace("Rewrite the importer so it streams instead of buffering.", ""),
        "two ## Order sections": GOOD + "\n## Order\n\nAnd another thing.\n",
        "two ### Checks": GOOD + "\n### Checks\n\n```bash\ntrue\n```\n",
        "after entry is not an id": GOOD.replace("eng-000", "Engineering-1"),
        "after is not a list": GOOD.replace("after: [eng-000, eng-002]", "after: eng-000"),
        "unknown frontmatter key": GOOD.replace("after: [eng-000, eng-002]", "priority: high"),
        "unclosed frontmatter": GOOD.replace("---\n## Order", "## Order"),
        "indented frontmatter": GOOD.replace("after: [eng-000, eng-002]", "  after: [eng-000]"),
    }


def test_good_order():
    order = parse_order_text(GOOD, "orders/eng-001.md")
    assert order.after == ["eng-000", "eng-002"]
    assert order.order.startswith("Rewrite the importer")
    assert "pytest -q tests/importer" in order.checks
    assert "the suite passes" in order.definition_of_done


def test_frontmatter_is_optional():
    order = parse_order_text(NO_FRONTMATTER, "orders/eng-001.md")
    assert order.after == []


def test_the_order_text_is_kept_verbatim_for_the_work_item():
    order = parse_order_text(GOOD, "orders/eng-001.md")
    assert "## Order" in order.body and "### Checks" in order.body
    assert "after: [eng-000" not in order.body


def test_checks_survive_indentation_and_tildes():
    text = NO_FRONTMATTER.replace("```bash", "~~~bash").replace("```", "~~~")
    assert "pytest" in parse_order_text(text, "orders/eng-001.md").checks


@pytest.mark.parametrize("name", sorted(malformed()))
def test_every_malformed_order_is_rejected_by_name(name, tmp_path):
    path = tmp_path / "eng-001.md"
    path.write_text(malformed()[name])
    with pytest.raises(ValidationError) as exc:
        parse_order(path)
    message = str(exc.value)
    assert str(path) in message, f"{name}: message does not name the file: {message}"
    # Section rules cite spec 06 by name; frontmatter rules say which frontmatter rule broke.
    assert "spec 06" in message or "frontmatter" in message, (
        f"{name}: message does not name the rule: {message}"
    )


@pytest.mark.parametrize(
    "name",
    [
        "no ## Order",
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
        parse_order(path)
    assert "spec 06" in str(exc.value)


def test_a_missing_order_file_is_rejected(tmp_path):
    with pytest.raises(ValidationError) as exc:
        parse_order(tmp_path / "nope.md")
    assert "order file not found" in str(exc.value)


def test_a_heading_inside_a_fence_is_not_a_section(tmp_path):
    text = NO_FRONTMATTER.replace(
        "pytest -q tests/importer", "grep -q '## Order' docs/importer.md"
    )
    order = parse_order_text(text, "orders/eng-001.md")
    assert "## Order" in order.checks


def test_the_skeleton_order_template_passes_its_own_rules():
    """`templates/order.md` is the gtm lane's; a break is reported, not fixed."""
    from hx.install import skeleton_dir

    path = skeleton_dir() / "templates" / "order.md"
    if path.exists():
        assert parse_order(path).checks.strip()
