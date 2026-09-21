"""Work-item filenames and work-item frontmatter (spec 06).

The v1 cut (spec 14 D25) removed the filename regex as a validation gate: the suffix is read,
never policed, and the HarnessAgent may rename its own item to whatever it likes.
"""

from __future__ import annotations

import pytest

from hx.errors import ValidationError
from hx.workitems import find_work_items, parse_work_item, parse_work_item_filename, parse_work_item_text

GOOD = """---
id: eng-001
pod: engineers
outcome:
dispatched: 2026-09-20T12:00:00Z
---

## Order

Do it.

## Tasks
- [ ] one
"""

NAMES = [
    ("eng-001-working.md", "eng-001", "working"),
    ("rev-999-complete.md", "rev-999", "complete"),
    ("qa-042-idle.md", "qa-042", "idle"),
    # Nothing is checked against a vocabulary: the suffix is whatever is after the last `-`,
    # and the agent may rename its own item (spec 06, spec 14 D25).
    ("eng-001-halfway.md", "eng-001", "halfway"),
    ("eng-0001-working.md", "eng-0001", "working"),
    ("ENG-001-idle.md", "ENG-001", "idle"),
    # Split on the last `-`, and that is the whole rule: a name with no state suffix splits
    # somewhere odd rather than being refused. Nothing downstream cares (spec 14 D25).
    ("eng-001.md", "eng", "001"),
]

#: Not `<something>-<something>.md` at all, which is the one thing that is still not a
#: work item: `pods/<pod>/` may hold other files and nothing complains about them.
NOT_WORK_ITEMS = [
    "eng-001-working.txt",    # work items are markdown
    "partner.md",
    "partner-idle.markdown",
    "notes.md",
]


@pytest.mark.parametrize("name,item_id,state", NAMES)
def test_the_suffix_is_read_not_policed(name, item_id, state):
    parsed = parse_work_item_filename(name)
    assert (parsed.id, parsed.state) == (item_id, state)


@pytest.mark.parametrize("name", NOT_WORK_ITEMS)
def test_a_name_that_is_not_id_dash_state_md_is_not_a_work_item(name):
    with pytest.raises(ValidationError) as exc:
        parse_work_item_filename(name)
    assert name in str(exc.value) and "spec 06" in str(exc.value)


def test_good_frontmatter():
    item = parse_work_item_text(GOOD, "pods/engineers/eng-001-working.md")
    assert (item.id, item.pod, item.state) == ("eng-001", "engineers", "working")
    assert item.outcome is None and not hasattr(item, "after")
    assert item.body.lstrip().startswith("## Order")


def malformed():
    return {
        "no frontmatter": GOOD.split("---\n", 2)[2],
        "id does not match the filename": GOOD.replace("id: eng-001", "id: eng-002"),
        "id is not an id": GOOD.replace("id: eng-001", "id: engineer"),
        "pod does not match the directory": GOOD.replace("pod: engineers", "pod: reviewers"),
        "missing pod": GOOD.replace("pod: engineers\n", ""),
        "unknown frontmatter key": GOOD.replace("pod: engineers", "pod: engineers\nowner: me"),
        "outcome is not an outcome": GOOD.replace("outcome:", "outcome: finished"),
        "frontmatter `after`": GOOD.replace("pod: engineers", "pod: engineers\nafter: [eng-000]"),
        "dispatched is empty text": GOOD.replace("dispatched: 2026-09-20T12:00:00Z", "dispatched: '   '"),
    }


@pytest.mark.parametrize("name", sorted(malformed()))
def test_every_malformed_work_item_is_rejected_by_name(name, tmp_path):
    directory = tmp_path / "pods" / "engineers"
    directory.mkdir(parents=True)
    path = directory / "eng-001-working.md"
    path.write_text(malformed()[name])
    with pytest.raises(ValidationError) as exc:
        parse_work_item(path)
    assert str(path) in str(exc.value), f"{name}: {exc.value}"


def test_a_complete_item_may_carry_an_outcome(tmp_path):
    directory = tmp_path / "pods" / "engineers"
    directory.mkdir(parents=True)
    path = directory / "eng-001-complete.md"
    path.write_text(GOOD.replace("outcome:", "outcome: blocked"))
    assert parse_work_item(path).outcome == "blocked"


def test_an_outcome_on_a_working_item_is_not_refused(tmp_path):
    """No transition table: the frontmatter and the suffix are not cross-checked (D25)."""
    directory = tmp_path / "pods" / "engineers"
    directory.mkdir(parents=True)
    path = directory / "eng-001-working.md"
    path.write_text(GOOD.replace("outcome:", "outcome: done"))
    assert parse_work_item(path).outcome == "done"


def test_find_work_items_skips_the_bench_archive_and_strays(instance, work_item):
    work_item("eng-001", "working")
    archive = instance / "pods" / "engineers" / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "eng-001-2026-09-19T10:00:00Z.md").write_text("benched body\n")
    (instance / "pods" / "engineers" / "notes.txt").write_text("stray\n")

    assert set(find_work_items(instance)) == {"eng-001"}
