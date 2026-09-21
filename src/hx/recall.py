"""`hx recall` — last-resort file-memory search over completed Work Items (spec 06, 08).

Completed Work Item bodies (live `*-complete.md` items plus `pods/<pod>/archive/` bodies
from `hx bench`) are file memory: the how that the Partner never absorbs in the normal
flow. This command searches them with plain substring matching — no embeddings, no vector
store — and is deliberately bounded:

- a query or an id/pod filter is required; bare `hx recall` is refused;
- at most 50 files are scanned, newest first;
- at most `--limit` hits (default 5, max 20);
- excerpts are capped (default 400 chars, `--full` raises to 2000).

Reach for this only after `hx board`, `hx read <id>`, and `hx memory search` came up
empty. The Partner tracks what/why/state; the how lives here.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from .errors import ValidationError
from .workitems import find_work_items, split_frontmatter_text

#: Hard bounds for the last-resort search.
MAX_FILES_SCANNED = 50
DEFAULT_LIMIT = 5
MAX_LIMIT = 20
EXCERPT_CHARS = 400
FULL_EXCERPT_CHARS = 2000

#: A benched body is `<id>-<ts>.md`; the id is the leading segment.
ARCHIVED_ID_RE = re.compile(r"^(partner|[a-z]+-[0-9]{3})(?=[-.]|$)")


def _candidates(root: Path, item_id: str | None, pod: str | None) -> list[Path]:
    """Completed Work Item bodies: live `*-complete.md` plus benched archive bodies."""
    found: list[Path] = []
    for _id, paths in find_work_items(root).items():
        if item_id is not None and _id != item_id:
            continue
        for path in paths:
            if pod is not None and path.parent.name != pod:
                continue
            if path.name.endswith("-complete.md"):
                found.append(path)
    pods = root / "pods"
    if pods.is_dir():
        for pod_dir in sorted(p for p in pods.iterdir() if p.is_dir()):
            if pod is not None and pod_dir.name != pod:
                continue
            archive = pod_dir / "archive"
            if not archive.is_dir():
                continue
            for path in sorted(archive.glob("*.md")):
                match = ARCHIVED_ID_RE.match(path.name)
                if match is None:
                    continue
                if item_id is not None and match.group(1) != item_id:
                    continue
                found.append(path)
    # Newest first, so the scan budget covers the freshest memory.
    found.sort(key=lambda p: p.stat().st_mtime if p.exists() else 0, reverse=True)
    return found[:MAX_FILES_SCANNED]


def _excerpt(text: str, at: int, cap: int) -> str:
    start = max(0, at - cap // 4)
    snippet = " ".join(text[start : start + cap].split())
    return snippet


def recall(
    root: Path,
    query: str | None,
    *,
    item_id: str | None = None,
    pod: str | None = None,
    limit: int = DEFAULT_LIMIT,
    full: bool = False,
) -> list[dict]:
    """Bounded substring search over completed Work Item bodies."""
    if not query and item_id is None and pod is None:
        raise ValidationError(
            "refuse: `hx recall` needs a query or a filter (`--id`, `--pod`); "
            "unbounded file-memory scans are not a first read (spec 06)"
        )
    if limit < 1 or limit > MAX_LIMIT:
        raise ValidationError(
            f"refuse: `--limit` is 1..{MAX_LIMIT}; file memory is read in sips, not gulps (spec 06)"
        )
    cap = FULL_EXCERPT_CHARS if full else EXCERPT_CHARS
    hits: list[dict] = []
    lowered = query.lower() if query else None
    for path in _candidates(root, item_id, pod):
        if len(hits) >= limit:
            break
        try:
            text = path.read_text()
        except OSError:
            continue
        try:
            _, body = split_frontmatter_text(text)
        except ValidationError:
            body = text
        if lowered is None:
            at = 0
        else:
            at = body.lower().find(lowered)
            if at < 0:
                continue
        try:
            rel = str(path.relative_to(root))
        except ValueError:
            rel = str(path)
        hits.append({"path": rel, "excerpt": _excerpt(body, at, cap)})
    return hits


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx recall", add_help=True)
    parser.add_argument("query", nargs="?", help="substring to find in completed Work Items")
    parser.add_argument("--id", dest="item_id", help="one id's completed bodies only")
    parser.add_argument("--pod", help="one pod's completed bodies only")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT)
    parser.add_argument("--full", action="store_true", help="longer excerpts (still capped)")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    hits = recall(
        root, args.query, item_id=args.item_id, pod=args.pod, limit=args.limit, full=args.full
    )
    if not hits:
        print("HX-RECALL none")
        return 0
    for hit in hits:
        print(f"HX-RECALL {hit['path']}\n  {hit['excerpt']}")
    return 0
