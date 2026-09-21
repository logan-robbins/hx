"""`hx bench <id>` — free an id for the next order (spec 06, 08).

The completed body is archived to `pods/<pod>/archive/<id>-<ts>.md` before the work item is
reset from `templates/work-item.md` and renamed `complete → idle`. `tasks.json` is not
touched: the record of what that id did stays until the id is dispatched again.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import archive as archive_mod, store, timestamps
from .caller import require_partner_caller
from .errors import Refused
from .workitems import (
    parse_work_item,
    render,
    rename_state,
    load_template,
    require_work_item,
    work_item_path,
)


def _save_worktree_patch(root: Path, item_id: str, pod: str, ts: str) -> Path | None:
    """Save uncommitted work, tracked and untracked, next to the benched body (spec 08).

    Benching frees the id, and the next dispatch resets the worktree — so anything not
    committed is about to be gone. `git diff HEAD` covers tracked changes; untracked files
    are added to the index first with `--intent-to-add` so they appear in the same patch, and
    the index is restored afterwards.
    """
    from .dispatch import _worktree_of
    from .repo import git

    workdir = _worktree_of(root, item_id)
    if workdir is None or not (workdir / ".git").exists():
        return None

    git("add", "-AN", ".", cwd=workdir, check=False)
    diff = git("diff", "HEAD", "--binary", cwd=workdir, check=False)
    git("reset", "-q", cwd=workdir, check=False)
    if diff.returncode != 0 or not diff.stdout.strip():
        return None

    patch = archive_mod.bench_archive_path(root, pod, item_id, ts).with_suffix(".patch")
    store.atomic_write_text(patch, diff.stdout)
    return patch


def bench(root: Path, item_id: str, *, env=None) -> dict:
    require_partner_caller("bench", env)

    path = require_work_item(root, item_id)
    item = parse_work_item(path)
    if item.state != "complete":
        raise Refused(
            f"refuse: {item_id} is `{item.state}`, not `complete`; a benched item is one that "
            f"finished and whose digest has been read (spec 06)"
        )

    ts = timestamps.now()
    # Archive the body before the reset, never after (spec 13 M1).
    target = archive_mod.bench_archive_path(root, item.pod, item_id, ts)
    store.atomic_write_text(target, path.read_text())
    patch = _save_worktree_patch(root, item_id, item.pod, ts)

    blank = render(
        load_template(root), item_id=item_id, pod=item.pod, after=[], dispatched="", order=""
    )
    store.atomic_write_text(path, blank)
    final = rename_state(path, "idle")

    return {
        "id": item_id,
        "archived": str(target.relative_to(root)),
        "patch": str(patch.relative_to(root)) if patch else None,
        "file": str(final.relative_to(root)),
    }


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx bench", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    result = bench(root, args.id, env=env)
    print(f"HX-BENCH {result['id']} idle archived={result['archived']}")
    if result["patch"]:
        print(f"  patch={result['patch']} (content, not staging: `git apply` it to restore)")
    return 0
