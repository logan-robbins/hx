"""The id vocabulary, in one place.

An id is `partner` or `<pod-ish word>-NNN`, from the work-item filename regex in spec 06.
Every other module imports `ID_RE` rather than restating it.
"""

from __future__ import annotations

import re

ID_PATTERN = r"partner|[a-z]+-[0-9]{3}"
ID_RE = re.compile(rf"^(?:{ID_PATTERN})$")

PARTNER = "partner"

#: Work item states; the filename suffix is the state (spec 06). Nothing gates on this
#: list — the v1 cut (spec 14 D25) removed the filename regex as a validation gate and the
#: transition table with it — but hx itself only ever writes one of these three.
STATES = ("idle", "working", "complete")

#: Outcomes `hx complete` may record (spec 06 "Outcome mapping").
OUTCOMES = ("done", "blocked", "decision", "exhausted")


def is_id(value: object) -> bool:
    return isinstance(value, str) and ID_RE.match(value) is not None


def sort_key(item_id: str) -> tuple[int, str]:
    """`partner` first, then by id (CONTRACTS.md `hx board --json`)."""
    return (0 if item_id == PARTNER else 1, item_id)
