"""`hx push <id>` — the only command that ever contacts the user's upstream (spec 08, 17.2).

Everything else stays inside the instance: agents work in worktrees cut from the bare mirror,
on branches that exist only in the mirror. This pushes one of those branches, and only when
the Partner is told to.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from .caller import require_partner_caller
from .errors import NotFound, Refused
from .repo import UPSTREAM_REMOTE, branch_for, git, require_mirror


def push(root: Path, item_id: str, *, env=None) -> dict:
    require_partner_caller("push", env)
    config, mirror = require_mirror(root)
    branch = branch_for(root, item_id)
    if git("rev-parse", "--verify", branch, cwd=mirror, check=False).returncode != 0:
        raise NotFound(
            f"{branch}: no such branch in {mirror.name}; `hx launch {item_id}` cuts the worktree "
            f"and creates it"
        )
    if git("config", "--get", f"remote.{UPSTREAM_REMOTE}.mirror", cwd=mirror,
           check=False).stdout.strip() == "true":
        raise Refused(
            f"refuse: {mirror.name} has remote.{UPSTREAM_REMOTE}.mirror set, which would make "
            f"this a force-push of every ref and delete upstream refs the mirror does not "
            f"have. `hx repo add` unsets it; unset it before pushing"
        )
    # One explicit refspec, so what reaches upstream is exactly this branch.
    git("push", UPSTREAM_REMOTE, f"refs/heads/{branch}:refs/heads/{branch}", cwd=mirror)
    return {"id": item_id, "branch": branch, "upstream": config.get("upstream")}


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx push", add_help=True)
    parser.add_argument("id")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    result = push(root, args.id, env=env)
    print(f"HX-PUSH {result['id']} {result['branch']} -> {result['upstream']}")
    return 0
