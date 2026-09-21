"""Archived dispatches and benched bodies (spec 03, 08), and the `hx archive` view.

Two different archives, both read by `hx show` and `hx archive`:

- `archive/<id>/<ts>/` — the `logs/<id>/` and `state/<id>/` of a previous dispatch, moved
  there by `hx dispatch`. Not written by `hx resume`, which keeps everything.
- `pods/<pod>/archive/<id>-<ts>.md` — the body of a completed work item, moved there by
  `hx bench` before the item is reset from the template.
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from . import timestamps
from .config_harness import load_harness
from .errors import ValidationError
from .ids import ID_RE, sort_key
from .workitems import SECTION_DIGEST, find_work_items, section_text, split_frontmatter_text

ARCHIVED_DIRS = ("logs", "state")


def dispatch_archive_dir(root: Path, item_id: str, ts: str) -> Path:
    return root / "archive" / item_id / ts


def archive_dispatch(root: Path, item_id: str, ts: str) -> Path | None:
    """Move `logs/<id>/` and `state/<id>/` into `archive/<id>/<ts>/`, then recreate them.

    Returns the archive directory, or None when there was nothing to archive.
    """
    target = dispatch_archive_dir(root, item_id, ts)
    moved = False
    for name in ARCHIVED_DIRS:
        source = root / name / item_id
        if source.is_dir() and any(source.iterdir()):
            target.mkdir(parents=True, exist_ok=True)
            destination = target / name
            if destination.exists():
                shutil.rmtree(destination)
            shutil.move(str(source), str(destination))
            moved = True
        if source.exists() and not source.is_dir():
            raise ValidationError(f"{source}: expected a directory")
        source.mkdir(parents=True, exist_ok=True)
    return target if moved else None


def bench_archive_path(root: Path, pod: str, item_id: str, ts: str) -> Path:
    return root / "pods" / pod / "archive" / f"{item_id}-{ts}.md"


def _digest_of_body(text: str) -> str | None:
    try:
        _, body = split_frontmatter_text(text)
    except ValidationError:
        body = text
    digest = section_text(body, SECTION_DIGEST)
    return digest or None


def bench_entries(root: Path, pod: str | None, item_id: str) -> list[dict]:
    """`hx show`'s `bench` list: benched bodies for an id, oldest first (CONTRACTS.md)."""
    if pod is None:
        return []
    directory = root / "pods" / pod / "archive"
    if not directory.is_dir():
        return []
    entries = []
    for path in sorted(directory.glob(f"{item_id}-*.md")):
        ts = path.stem[len(item_id) + 1 :]
        entries.append(
            {"ts": ts, "path": str(path.relative_to(root)), "digest": _digest_of_body(path.read_text())}
        )
    return entries


def archive_entries(root: Path, item_id: str) -> list[dict]:
    """`hx show`'s `archive` list: archived dispatches for an id, oldest first."""
    directory = root / "archive" / item_id
    if not directory.is_dir():
        return []
    entries = []
    for path in sorted(p for p in directory.iterdir() if p.is_dir()):
        entries.append({"ts": path.name, "path": str(path.relative_to(root)), "digest": None})
    return entries


def collect(root: Path) -> dict:
    """`hx archive --json` (CONTRACTS.md): benched bodies and archived dispatches per id."""
    by_id = find_work_items(root)
    ids = set(by_id)
    for base in ("archive", "pods"):
        directory = root / base
        if not directory.is_dir():
            continue
        if base == "archive":
            ids.update(e.name for e in directory.iterdir() if e.is_dir() and ID_RE.match(e.name))
        else:
            for pod_dir in directory.iterdir():
                bench = pod_dir / "archive"
                if bench.is_dir():
                    for path in bench.glob("*.md"):
                        head = path.name.rsplit("-", 1)[0].rsplit("-", 1)[0]
                        if ID_RE.match(head):
                            ids.add(head)
    config = root / "config"
    if config.is_dir():
        ids.update(e.name for e in config.iterdir() if e.is_dir() and ID_RE.match(e.name))

    items = []
    for item_id in sorted(ids, key=sort_key):
        pod = None
        files = by_id.get(item_id, [])
        if files:
            pod = files[0].parent.name
        else:
            harness = root / "config" / item_id / "harness.json"
            if harness.is_file():
                try:
                    pod = load_harness(harness, check_cross_file=False).pod
                except ValidationError:
                    pod = None
        items.append(
            {
                "id": item_id,
                "pod": pod,
                "bench": bench_entries(root, pod, item_id),
                "archive": archive_entries(root, item_id),
            }
        )

    return {"root_abs": str(root), "ts": timestamps.now(), "items": items}


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx archive", add_help=True)
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    view = collect(root)
    if args.json:
        print(json.dumps(view, indent=2))
    else:
        for item in view["items"]:
            for entry in item["bench"]:
                print(f"bench    {item['id']}  {entry['ts']}  {entry['path']}")
            for entry in item["archive"]:
                print(f"dispatch {item['id']}  {entry['ts']}  {entry['path']}")
    return 0
