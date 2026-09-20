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

from . import store
from .errors import NotFound, ValidationError
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


# --- transitions (spec 06) -------------------------------------------------------------------
#
# hx owns the state suffix and the `## Order` addenda; the body between them is the
# HarnessAgent's (spec 04). Renames are same-directory renames (spec 08).

SECTION_ORDER = "## Order"
SECTION_TASKS = "## Tasks"
SECTION_DIGEST = "## Digest"
SECTION_OPEN_DECISION = "## Open decision"

#: `templates/work-item.md` placeholders, rendered by literal replacement, never `str.format`
#: (CONTRACTS.md "`templates/work-item.md` placeholders", handoff/gtm-to-build.md).
TEMPLATE_TOKENS = ("{{id}}", "{{pod}}", "{{after}}", "{{dispatched}}", "{{order}}")


def pods_dir(root: Path, pod: str) -> Path:
    return root / "pods" / pod


def work_item_path(root: Path, pod: str, item_id: str, state: str) -> Path:
    return pods_dir(root, pod) / f"{item_id}-{state}.md"


def find_work_item(root: Path, item_id: str) -> Path | None:
    """The one work item for an id, or None. Raises when there is more than one (spec 06)."""
    by_id, _ = find_work_items(root)
    found = by_id.get(item_id, [])
    if len(found) > 1:
        listed = ", ".join(str(p.relative_to(root)) for p in found)
        raise ValidationError(f"{item_id}: {len(found)} work items ({listed}); one work item per id (spec 06)")
    return found[0] if found else None


def require_work_item(root: Path, item_id: str) -> Path:
    path = find_work_item(root, item_id)
    if path is None:
        raise NotFound(f"{item_id}: no work item under pods/; `hx launch {item_id}` creates one")
    return path


def state_of(path: Path) -> str:
    return parse_work_item_filename(path.name, path=path).state


def rename_state(path: Path, state: str) -> Path:
    """Move a work item to a new state. Same-directory rename, so it is atomic (spec 08)."""
    if state not in STATES:
        raise ValidationError(f"{path}: `{state}` is not a work item state ({', '.join(STATES)})")
    name = parse_work_item_filename(path.name, path=path)
    target = path.with_name(f"{name.id}-{state}.md")
    if target != path:
        path.rename(target)
    return target


def render(template: str, *, item_id: str, pod: str, after: list[str], dispatched: str, order: str) -> str:
    """Render `templates/work-item.md`. Literal replacement of the five tokens, nothing else.

    `{{after}}` renders *inside* the `[...]` the template already has, so `after: [{{after}}]`
    becomes `after: [eng-000, eng-002]` and `after: []` when empty.
    """
    rendered = template
    for token, value in (
        ("{{id}}", item_id),
        ("{{pod}}", pod),
        ("{{after}}", ", ".join(after)),
        ("{{dispatched}}", dispatched),
        ("{{order}}", order),
    ):
        rendered = rendered.replace(token, value)
    return rendered


def load_template(root: Path) -> str:
    path = root / "templates" / "work-item.md"
    if not path.is_file():
        raise NotFound(f"{path}: no work item template; `hx install` copies it from the package (spec 17.2)")
    return path.read_text()


def split_frontmatter_text(text: str) -> tuple[str, str]:
    """Return (frontmatter block including both fences, body). Raises when there is none."""
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        raise ValidationError("work item has no frontmatter; it opens with `---` (spec 06)")
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join(lines[: index + 1]) + "\n", "\n".join(lines[index + 1 :])
    raise ValidationError("work item frontmatter was opened with `---` and never closed")


def set_frontmatter(path: Path, **fields: str | None) -> None:
    """Rewrite frontmatter keys in place, leaving the body and key order untouched."""
    front, body = split_frontmatter_text(path.read_text())
    lines = front.split("\n")
    for key, value in fields.items():
        rendered = "" if value is None else str(value)
        for index, line in enumerate(lines):
            if line.split(":", 1)[0].strip() == key and not line.startswith("---"):
                lines[index] = f"{key}: {rendered}".rstrip()
                break
        else:
            lines.insert(len(lines) - 2, f"{key}: {rendered}".rstrip())
    store.atomic_write_text(path, "\n".join(lines) + body)


def section_bounds(body: str, heading: str) -> tuple[int, int] | None:
    """(start, end) line indexes of a `## Heading` section's content, fences respected."""
    lines = body.split("\n")
    level = len(heading) - len(heading.lstrip("#"))
    start = None
    in_fence: str | None = None
    for index, line in enumerate(lines):
        fence = re.match(r"^\s*(`{3,}|~{3,})", line)
        if fence:
            marker = fence.group(1)[0]
            in_fence = marker if in_fence is None else (None if in_fence == marker else in_fence)
            continue
        if in_fence is not None:
            continue
        stripped = line.rstrip()
        if start is None:
            if stripped == heading:
                start = index
            continue
        hashes = len(stripped) - len(stripped.lstrip("#"))
        if stripped.startswith("#") and 0 < hashes <= level:
            return start, index
    return (start, len(lines)) if start is not None else None


def section_text(body: str, heading: str) -> str | None:
    bounds = section_bounds(body, heading)
    if bounds is None:
        return None
    start, end = bounds
    return "\n".join(body.split("\n")[start + 1 : end]).strip("\n")


def append_to_section(path: Path, heading: str, text: str) -> None:
    """Insert `text` at the end of a section, before the next heading of the same level."""
    front, body = split_frontmatter_text(path.read_text())
    bounds = section_bounds(body, heading)
    if bounds is None:
        raise ValidationError(f"{path}: no `{heading}` section to append to (spec 06)")
    _, end = bounds
    lines = body.split("\n")
    block = text.rstrip("\n").split("\n")
    while end > 0 and lines[end - 1].strip() == "":
        end -= 1
    updated = lines[:end] + ["", *block, ""] + lines[end:]
    store.atomic_write_text(path, front + "\n".join(updated))


def replace_section(path: Path, heading: str, text: str) -> None:
    """Replace a section's content, keeping the heading and everything around it."""
    front, body = split_frontmatter_text(path.read_text())
    bounds = section_bounds(body, heading)
    if bounds is None:
        raise ValidationError(f"{path}: no `{heading}` section (spec 06)")
    start, end = bounds
    lines = body.split("\n")
    block = text.rstrip("\n").split("\n")
    updated = lines[: start + 1] + ["", *block, ""] + lines[end:]
    store.atomic_write_text(path, front + "\n".join(updated))
