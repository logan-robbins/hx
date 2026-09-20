"""Work-item filenames and work-item frontmatter (spec 06).

The filename carries the identity and the state:

    ^(?<id>partner|[a-z]+-[0-9]{3})-(?<state>idle|queued|working|complete)\\.md$

and the frontmatter carries the control-plane fields `hx dispatch` renders from the template.
The body below it belongs to the HarnessAgent (spec 04) and is not validated here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .errors import ValidationError
from .frontmatter import frontmatter_list, parse_frontmatter
from .ids import ID_PATTERN, ID_RE, OUTCOMES, STATES

WORK_ITEM_RE = re.compile(
    rf"^(?P<id>{ID_PATTERN})-(?P<state>{'|'.join(STATES)})\.md$"
)

_FRONTMATTER_KEYS = ("id", "pod", "after", "outcome", "dispatched")


@dataclass(frozen=True)
class WorkItemName:
    id: str
    state: str
    filename: str


@dataclass(frozen=True)
class WorkItem:
    path: Path
    id: str
    state: str
    pod: str
    after: list[str]
    outcome: str | None
    dispatched: str | None
    body: str


def parse_work_item_filename(name: str, *, path: str | Path | None = None) -> WorkItemName:
    """Parse `<id>-<state>.md`. Raises naming the file and the regex it failed."""
    subject = str(path) if path is not None else name
    match = WORK_ITEM_RE.match(name)
    if not match:
        raise ValidationError(
            f"{subject}: work item filename does not match "
            f"`^({ID_PATTERN})-({'|'.join(STATES)})\\.md$` (spec 06)"
        )
    return WorkItemName(id=match.group("id"), state=match.group("state"), filename=name)


def parse_work_item_text(text: str, path: str | Path, *, name: WorkItemName | None = None) -> WorkItem:
    """Validate a work item's frontmatter against its filename."""
    path = Path(path)
    path_str = str(path)
    if name is None:
        name = parse_work_item_filename(path.name, path=path)

    data, body = parse_frontmatter(text, path_str)
    if data is None:
        raise ValidationError(
            f"{path_str}: no frontmatter; a work item opens with `---` and the fields "
            f"{', '.join(_FRONTMATTER_KEYS)} (spec 06)"
        )
    unknown = sorted(set(data) - set(_FRONTMATTER_KEYS))
    if unknown:
        raise ValidationError(
            f"{path_str}: frontmatter key(s) {', '.join(unknown)} are not work item fields; "
            f"allowed: {', '.join(_FRONTMATTER_KEYS)} (spec 06)"
        )
    for required in ("id", "pod"):
        if required not in data:
            raise ValidationError(f"{path_str}: frontmatter is missing `{required}` (spec 06)")

    item_id = data["id"]
    if not isinstance(item_id, str) or not ID_RE.match(item_id):
        raise ValidationError(
            f"{path_str}: frontmatter `id` `{item_id!r}` is not an id "
            f"(`partner` or `<pod>-NNN`, spec 06)"
        )
    if item_id != name.id:
        raise ValidationError(
            f"{path_str}: frontmatter `id` is `{item_id}` but the filename says `{name.id}`; "
            f"one work item per id (spec 06)"
        )

    pod = data["pod"]
    if not isinstance(pod, str) or pod.strip() == "":
        raise ValidationError(f"{path_str}: frontmatter `pod` must be a non-empty string, got `{pod!r}`")
    if path.parent.name not in ("", ".") and pod != path.parent.name:
        raise ValidationError(
            f"{path_str}: frontmatter `pod` is `{pod}` but the file is in "
            f"`pods/{path.parent.name}/` (spec 03)"
        )

    after = frontmatter_list(data, "after", path_str)
    for dep in after:
        if not ID_RE.match(dep):
            raise ValidationError(
                f"{path_str}: frontmatter `after` entry `{dep}` is not an id (spec 06)"
            )

    outcome = data.get("outcome")
    if outcome is not None:
        if not isinstance(outcome, str) or outcome not in OUTCOMES:
            raise ValidationError(
                f"{path_str}: frontmatter `outcome` must be one of {', '.join(OUTCOMES)} or "
                f"empty, got `{outcome!r}` (spec 06)"
            )
    if outcome is not None and name.state != "complete":
        raise ValidationError(
            f"{path_str}: frontmatter `outcome` is `{outcome}` but the state suffix is "
            f"`{name.state}`; only a `complete` item carries an outcome (spec 06)"
        )

    dispatched = data.get("dispatched")
    if dispatched is not None and (not isinstance(dispatched, str) or dispatched.strip() == ""):
        raise ValidationError(
            f"{path_str}: frontmatter `dispatched` must be an ISO 8601 UTC timestamp or empty, "
            f"got `{dispatched!r}`"
        )

    return WorkItem(
        path=path,
        id=item_id,
        state=name.state,
        pod=pod,
        after=after,
        outcome=outcome,
        dispatched=dispatched,
        body=body,
    )


def parse_work_item(path: str | Path) -> WorkItem:
    path = Path(path)
    if not path.is_file():
        raise ValidationError(f"{path}: work item not found")
    return parse_work_item_text(path.read_text(), path)


def find_work_items(root: Path) -> tuple[dict[str, list[Path]], list[str]]:
    """Every work item under `pods/`, grouped by id, plus one error per stray filename.

    `pods/<pod>/archive/` holds benched bodies (spec 03) and is not scanned.
    """
    by_id: dict[str, list[Path]] = {}
    errors: list[str] = []
    pods = root / "pods"
    if not pods.is_dir():
        return by_id, errors
    for pod_dir in sorted(p for p in pods.iterdir() if p.is_dir()):
        for entry in sorted(pod_dir.iterdir()):
            if entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue
            rel = entry.relative_to(root)
            if not entry.name.endswith(".md"):
                errors.append(f"{rel}: not a work item; `pods/<pod>/` holds only `<id>-<state>.md` (spec 03)")
                continue
            try:
                name = parse_work_item_filename(entry.name, path=rel)
            except ValidationError as exc:
                errors.append(str(exc))
                continue
            by_id.setdefault(name.id, []).append(entry)
    return by_id, errors
