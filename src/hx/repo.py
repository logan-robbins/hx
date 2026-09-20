"""`hx repo add`, the bare mirror, and the sparse worktrees cut from it (spec 08, 17.2, 17.3).

This is the "without messing with the project upstream" guarantee. hx clones the product repo
into a bare mirror at `repos/<name>.git`; every agent works in a worktree cut from the mirror,
on its own branch, which exists only in the mirror. The user's checkout is never touched and
the upstream sees nothing until the Partner is told to `hx push`.

The worktree is sparse so the product's own `.claude/` never lands in it: those settings and
hooks belong to the user's interactive work and could fight hx (spec 17.3).
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from . import store
from .errors import HxError, NotFound, Refused

CONFIG = "config/repo.json"
#: Spec 17.3: keep everything, drop the repo's own Claude directory.
SPARSE_RULES = ("/*", "!/.claude/")
UPSTREAM_REMOTE = "upstream"


def config_path(root: Path) -> Path:
    return root / CONFIG


def load_repo(root: Path) -> dict | None:
    """`config/repo.json`, or None when no repo has been added yet."""
    path = config_path(root)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise HxError(f"{CONFIG}: not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or "name" not in data:
        raise HxError(f"{CONFIG}: must be an object with at least `name` (spec 17.2 step 4)")
    return data


def mirror_path(root: Path, name: str) -> Path:
    return root / "repos" / f"{name}.git"


def require_mirror(root: Path) -> tuple[dict, Path]:
    config = load_repo(root)
    if config is None:
        raise NotFound(
            f"{CONFIG}: no product repo; `hx repo add <url|path>` mirrors one (spec 17.2 step 4)"
        )
    mirror = mirror_path(root, config["name"])
    if not mirror.is_dir():
        raise NotFound(f"{mirror}: the mirror in {CONFIG} is not there")
    return config, mirror


def git(*args: str, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd) if cwd else None, capture_output=True, text=True, check=False
    )
    if check and result.returncode != 0:
        raise HxError(f"git {' '.join(args)} failed:\n{result.stdout}{result.stderr}".rstrip())
    return result


def name_for(source: str) -> str:
    """The mirror's name: the last path component, without `.git`."""
    text = source.rstrip("/")
    if text.endswith(".git"):
        text = text[: -len(".git")]
    name = text.rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    if not name:
        raise HxError(f"cannot work out a repository name from `{source}`")
    return name


def detect_base_branch(mirror: Path) -> str:
    """The mirror's default branch, from its own HEAD."""
    result = git("symbolic-ref", "--short", "HEAD", cwd=mirror, check=False)
    if result.returncode == 0 and result.stdout.strip():
        return result.stdout.strip()
    for candidate in ("main", "master"):
        if git("rev-parse", "--verify", candidate, cwd=mirror, check=False).returncode == 0:
            return candidate
    raise HxError(f"{mirror}: cannot work out the base branch; no HEAD, no main, no master")


def add(root: Path, source: str, *, env=None) -> dict:
    """Mirror the product repo. One repo per instance (spec 17.2 step 4)."""
    existing = load_repo(root)
    if existing is not None:
        raise Refused(
            f"refuse: this instance already mirrors `{existing['name']}` from "
            f"{existing.get('upstream')}; one product repo per instance (spec 17.2 step 4)"
        )

    upstream = source
    local = Path(source).expanduser()
    if local.exists():
        upstream = str(local.resolve())

    name = name_for(upstream)
    mirror = mirror_path(root, name)
    mirror.parent.mkdir(parents=True, exist_ok=True)
    if mirror.exists():
        raise Refused(f"refuse: {mirror} already exists")

    git("clone", "--mirror", upstream, str(mirror))
    # `--mirror` names the source `origin`; hx calls it `upstream`, because that is the one
    # thing `hx push` is allowed to touch (spec 08).
    if git("remote", "get-url", UPSTREAM_REMOTE, cwd=mirror, check=False).returncode != 0:
        git("remote", "rename", "origin", UPSTREAM_REMOTE, cwd=mirror, check=False)
    # A mirror clone sets `remote.<name>.mirror`, which makes *any* push a force-push of every
    # ref, deleting upstream refs the mirror does not have. `hx push` sends one branch, on
    # request, and nothing else ever contacts upstream (spec 08, 17.2 step 4) — so this is
    # unset explicitly rather than relied upon not to fire.
    git("config", "--unset", f"remote.{UPSTREAM_REMOTE}.mirror", cwd=mirror, check=False)

    config = {
        "name": name,
        "upstream": upstream,
        "base_branch": detect_base_branch(mirror),
        "keep_claude_dir": False,
    }
    store.atomic_write_json(config_path(root), config)
    return config


#: Spec 17.2 and 17.3: agent branches are `agent/<id>`, and they exist only in the mirror
#: until `hx push`. `config/<id>/harness.json` `branch` overrides it (spec 05), which is what
#: the skeleton's example worker uses.
BRANCH_PREFIX = "agent"


def branch_for(root: Path, item_id: str) -> str:
    """The agent's branch. `config/<id>/harness.json` `branch` wins; else `agent/<id>`."""
    from .config_harness import load_harness

    harness = root / "config" / item_id / "harness.json"
    if harness.is_file():
        try:
            configured = load_harness(harness, check_cross_file=False).branch
        except Exception:
            configured = None
        if configured:
            return configured
    return f"{BRANCH_PREFIX}/{item_id}"


def create_worktree(root: Path, item_id: str, workdir: Path, *, env=None) -> Path:
    """Cut `wt/<id>` from the mirror, sparse, on the agent's own branch (spec 17.3).

    `--no-checkout` first, then the sparse rules, then the checkout: the repo's `.claude/`
    is never written to disk at all, rather than written and deleted.
    """
    config, mirror = require_mirror(root)
    branch = branch_for(root, item_id)
    base = config.get("base_branch") or detect_base_branch(mirror)

    workdir.parent.mkdir(parents=True, exist_ok=True)
    git("worktree", "add", "--no-checkout", "-B", branch, str(workdir), base, cwd=mirror)

    if not config.get("keep_claude_dir"):
        git("sparse-checkout", "init", "--no-cone", cwd=workdir, check=False)
        git("sparse-checkout", "set", "--no-cone", *SPARSE_RULES, cwd=workdir)
    git("checkout", cwd=workdir)
    return workdir


def reset_worktree(root: Path, item_id: str, workdir: Path, *, env=None) -> None:
    """Put the worktree back on `base_branch` for a fresh dispatch (goal build-4 item 2)."""
    config, mirror = require_mirror(root)
    base = config.get("base_branch") or detect_base_branch(mirror)
    branch = branch_for(root, item_id)
    git("fetch", UPSTREAM_REMOTE, base, cwd=mirror, check=False)
    git("checkout", "-B", branch, base, cwd=workdir)
    git("reset", "--hard", base, cwd=workdir)
    git("clean", "-fd", cwd=workdir, check=False)


def main(argv: list[str], root: Path, *, env=None) -> int:
    parser = argparse.ArgumentParser(prog="hx repo", add_help=True)
    parser.add_argument("action", choices=["add", "show"])
    parser.add_argument("source", nargs="?", help="the product repo's URL or path")
    parser.add_argument("--root", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    if args.action == "show":
        config = load_repo(root)
        print(json.dumps(config, indent=2) if config else "(no product repo)")
        return 0

    if not args.source:
        raise HxError("repo add takes the product repo's URL or path")
    config = add(root, args.source, env=env)
    print(f"HX-REPO {config['name']} mirrored from {config['upstream']} ({config['base_branch']})")
    return 0
