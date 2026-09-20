"""`tasks.json`: the control-plane record per id (spec 08).

    {"eng-002": {"order": "…", "after": ["eng-001"], "addenda": [{"ts","text"}],
                 "outcome": null, "dispatched": "…", "completed": null}}

Written only by hx under `run/tasks.lock` (spec 04). M0 only reads it; the writers arrive
with `hx dispatch`, `hx complete` and `hx resume`.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import store
from .errors import ValidationError
from .ids import ID_RE, OUTCOMES

FILENAME = "tasks.json"
_FIELDS = ("order", "after", "addenda", "outcome", "dispatched", "completed")


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
        after = entry.get("after")
        if after is not None and not (isinstance(after, list) and all(isinstance(a, str) for a in after)):
            raise ValidationError(f"{FILENAME}: `{key}`: `after` must be a list of ids")
    return data


def outcome_of(tasks: dict[str, dict], item_id: str) -> str | None:
    entry = tasks.get(item_id)
    return entry.get("outcome") if isinstance(entry, dict) else None


def is_ready(tasks: dict[str, dict], after: list[str]) -> bool:
    """Spec 08: readiness of an `after` entry is `tasks.json[<dep>].outcome == "done"`."""
    return all(outcome_of(tasks, dep) == "done" for dep in after)


def new_entry(order: str, after: list[str], dispatched: str) -> dict:
    """A fresh `tasks.json` record for a dispatch (spec 08)."""
    return {
        "order": order,
        "after": list(after),
        "addenda": [],
        "outcome": None,
        "dispatched": dispatched,
        "completed": None,
    }


def write_tasks(root: Path, tasks: dict[str, dict]) -> None:
    """Write `tasks.json` atomically. The caller holds `run/tasks.lock` (spec 04)."""
    store.atomic_write_json(path_for(root), tasks)


def unmet(tasks: dict[str, dict], after: list[str]) -> list[str]:
    """The `after` entries whose outcome is not `done` — CONTRACTS.md `waiting_on`."""
    return [dep for dep in after if outcome_of(tasks, dep) != "done"]


def promotable(tasks: dict[str, dict], queued: dict[str, list[str]]) -> list[str]:
    """Ids among `queued` (id → after) whose every dependency is now `done` (spec 06)."""
    return sorted(item_id for item_id, after in queued.items() if is_ready(tasks, after))
