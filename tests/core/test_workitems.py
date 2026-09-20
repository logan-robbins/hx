"""Work-item filename regex and work-item frontmatter (spec 06)."""

from __future__ import annotations

import pytest

from hx.errors import ValidationError
from hx.workitems import find_work_items, parse_work_item, parse_work_item_filename, parse_work_item_text

GOOD = """---
id: eng-001
pod: engineers
after: [eng-000]
outcome:
dispatched: 2026-09-20T12:00:00Z
---

## Order

Do it.

## Tasks
- [ ] one
"""

VALID_NAMES = [
    ("partner-idle.md", "partner", "idle"),
    ("partner-working.md", "partner", "working"),
    ("eng-001-working.md", "eng-001", "working"),
    ("eng-000-queued.md", "eng-000", "queued"),
    ("rev-999-complete.md", "rev-999", "complete"),
    ("qa-042-idle.md", "qa-042", "idle"),
]

INVALID_NAMES = [
    "eng-1-working.md",       # three digits required
    "eng-0001-working.md",    # exactly three
    "ENG-001-idle.md",        # lower case only
    "eng-001-done.md",        # `done` is an outcome, not a state
    "eng-001.md",             # the state suffix is mandatory
    "eng-001-working.txt",    # work items are markdown
    "partner.md",
    "partner-idle.markdown",
    "eng001-idle.md",
    "-001-idle.md",
    "eng-001-working.md.bak",
]


@pytest.mark.parametrize("name,item_id,state", VALID_NAMES)
def test_valid_filenames(name, item_id, state):
    parsed = parse_work_item_filename(name)
    assert (parsed.id, parsed.state) == (item_id, state)


@pytest.mark.parametrize("name", INVALID_NAMES)
def test_invalid_filenames_are_rejected_by_the_regex(name):
    with pytest.raises(ValidationError) as exc:
        parse_work_item_filename(name)
    assert name in str(exc.value) and "spec 06" in str(exc.value)


def test_good_frontmatter():
    item = parse_work_item_text(GOOD, "pods/engineers/eng-001-working.md")
    assert (item.id, item.pod, item.state) == ("eng-001", "engineers", "working")
    assert item.after == ["eng-000"] and item.outcome is None
    assert item.body.lstrip().startswith("## Order")


def malformed():
    return {
        "no frontmatter": GOOD.split("---\n", 2)[2],
        "id does not match the filename": GOOD.replace("id: eng-001", "id: eng-002"),
        "id is not an id": GOOD.replace("id: eng-001", "id: engineer"),
        "pod does not match the directory": GOOD.replace("pod: engineers", "pod: reviewers"),
        "missing pod": GOOD.replace("pod: engineers\n", ""),
        "unknown frontmatter key": GOOD.replace("pod: engineers", "pod: engineers\nowner: me"),
        "outcome on a working item": GOOD.replace("outcome:", "outcome: done"),
        "outcome is not an outcome": GOOD.replace("outcome:", "outcome: finished"),
        "after entry is not an id": GOOD.replace("eng-000", "Engineering"),
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


def test_find_work_items_skips_the_bench_archive_and_reports_strays(instance, work_item):
    work_item("eng-001", "working")
    archive = instance / "pods" / "engineers" / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "eng-001-2026-09-19T10:00:00Z.md").write_text("benched body\n")
    (instance / "pods" / "engineers" / "notes.md").write_text("stray\n")

    by_id, errors = find_work_items(instance)
    assert set(by_id) == {"eng-001"}
    assert len(errors) == 1 and "notes.md" in errors[0]
