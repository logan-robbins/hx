"""`tasks.json`: the control-plane record per id (spec 08).

    {"eng-002": {"order": "…", "addenda": [{"ts","text"}],
                 "outcome": null, "dispatched": "…", "completed": null}}

Written only by hx (spec 04), with an ordinary write: the v1 cut (spec 14 D25) removed the
lock and the atomic-rename ceremony. Together with the work item this is the only place task
text lives.
"""

from __future__ import annotations

import json
from pathlib import Path

from .errors import ValidationError
from .ids import ID_RE, OUTCOMES

FILENAME = "tasks.json"
_FIELDS = ("order", "addenda", "outcome", "dispatched", "completed")


def path_for(root: Path) -> Path:
    return root / FILENAME


def load_tasks(root: Path) -> dict[str, dict]:
    """Read `tasks.json`; an absent file is an empty control plane, not an error."""
    path = path_for(root)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise ValidationError(f"{FILENAME}: not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise ValidationError(f"{FILENAME}: must be a JSON object keyed by id, got {type(data).__name__}")
    for key, entry in data.items():
        if not ID_RE.match(str(key)):
            raise ValidationError(f"{FILENAME}: key `{key}` is not an id (`partner` or `<pod>-NNN`, spec 06)")
        if not isinstance(entry, dict):
            raise ValidationError(f"{FILENAME}: `{key}`: entry must be an object, got {type(entry).__name__}")
        unknown = sorted(set(entry) - set(_FIELDS))
        if unknown:
            raise ValidationError(
                f"{FILENAME}: `{key}`: unknown field(s) {', '.join(unknown)}; "
                f"allowed: {', '.join(_FIELDS)} (spec 08)"
            )
        outcome = entry.get("outcome")
        if outcome is not None and outcome not in OUTCOMES:
            raise ValidationError(
                f"{FILENAME}: `{key}`: `outcome` must be one of {', '.join(OUTCOMES)} or null, "
                f"got `{outcome!r}` (spec 08)"
            )
    return data


def outcome_of(tasks: dict[str, dict], item_id: str) -> str | None:
    entry = tasks.get(item_id)
    return entry.get("outcome") if isinstance(entry, dict) else None


def new_entry(order: str, dispatched: str) -> dict:
    """A fresh `tasks.json` record for a dispatch (spec 08)."""
    return {
        "order": order,
        "addenda": [],
        "outcome": None,
        "dispatched": dispatched,
        "completed": None,
    }


def write_tasks(root: Path, tasks: dict[str, dict]) -> None:
    """Write `tasks.json`. An ordinary write, no lock (spec 08, spec 14 D25)."""
    path = path_for(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as handle:
        json.dump(tasks, handle, indent=2, sort_keys=True)
        handle.write("\n")
