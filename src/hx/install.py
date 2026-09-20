"""`hx install` — create the instance under a given HARNESS_ROOT (spec 17.2).

M0 builds step 2 only: the directory layout of spec 03 and a copy of the package skeleton
(`src/hx/skeleton/**`) into it. Steps 1 and 3-6 — the preflight checks and
`config/claude.json`, the seed login, the repo mirror, the boot and heartbeat units, and
`hx launch partner` — land with their milestones, so `--skeleton-only` is required until then.

The gtm lane owns `templates/`, `companion/`, `PARTNER.md` and `config/CLAUDE.md` inside the
skeleton. Until those land, install copies what exists and `hx doctor` reports the rest.
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

from . import store
from .errors import HxError
from .root import resolve_root

#: Directories of spec 03 that exist in every instance from the moment it is created.
LAYOUT_DIRS = (
    "adapters",
    "archive",
    "config",
    "logs",
    "orders",
    "pods",
    "pods/partner",
    "repos",
    "run",
    "seed",
    "seed/home",
    "state",
    "wt",
)

#: Spec 03: everything but `config/` is runtime state, so the instance ignores it in git.
GITIGNORE = """# Written by `hx install` (spec 03). Only config/ is worth committing.
pods/
logs/
state/
run/
orders/
tasks.json
"""

#: Exactly what spec 17.2 step 2 says a fresh instance is created with, plus the adapters.
#: No worker: the example worker configuration ships as `templates/worker/` for the Partner to
#: copy into `config/<id>/` when it creates an agent (CONTRACTS.md "Fresh instance contents").
EXPECTED_SKELETON_FILES = (
    "PARTNER.md",
    "adapters/claude/install.sh",
    "adapters/claude/start.sh",
    "companion/BASE.md",
    "companion/roles/partner.md",
    "config/CLAUDE.md",
    "config/models.json",
    "config/partner/AGENTS.md",
    "config/partner/SUBAGENTS.md",
    "config/partner/harness.json",
    "templates/work-item.md",
)


def entry_points() -> dict[str, str]:
    """Absolute paths of `hx`, `hx-hook` and the interpreter (CONTRACTS.md `config/hx.json`).

    The entry-point scripts sit next to the running interpreter — that is where `uv tool
    install` and a venv both put them — so resolve from `sys.executable` first and fall back
    to PATH. Hook commands in every `run/<id>/home/settings.json` reference these, so a
    package upgrade that moves them is followed by `hx upgrade`, not by broken hooks
    (spec 17.1).
    """
    # `sys.executable` unresolved: inside a venv that is the venv's own python, which is the
    # interpreter hx runs on and the one whose bin/ holds the entry-point scripts. Resolving
    # it would jump to the base installation, where they are not.
    found: dict[str, str] = {"python_bin": str(Path(sys.executable).absolute())}
    bindirs = [Path(sys.executable).absolute().parent, Path(sys.prefix) / "bin"]
    for key, name in (("hx_bin", "hx"), ("hook_bin", "hx-hook")):
        for bindir in bindirs:
            candidate = bindir / name
            if candidate.is_file():
                found[key] = str(candidate)
                break
        else:
            found[key] = shutil.which(name) or ""
    return found


def write_hx_json(root: Path) -> dict[str, str]:
    """Record the entry points the adapters and hooks read (CONTRACTS.md, spec 17.1)."""
    recorded = entry_points()
    store.atomic_write_json(root / "config" / "hx.json", recorded)
    return recorded


def skeleton_dir() -> Path:
    """The package's instance skeleton, shipped as package data (pyproject `package-data`)."""
    return Path(__file__).resolve().parent / "skeleton"


def _copy_tree(source: Path, destination: Path) -> tuple[list[str], list[str]]:
    """Copy `source` into `destination` without overwriting anything already there.

    Modes are preserved, so `adapters/claude/*.sh` stay executable.
    """
    created: list[str] = []
    skipped: list[str] = []
    for entry in sorted(source.rglob("*")):
        relative = entry.relative_to(source)
        target = destination / relative
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            skipped.append(str(relative))
            continue
        shutil.copy2(entry, target)
        created.append(str(relative))
    return created, skipped


def install_skeleton(root: Path) -> dict:
    """Create the layout and copy the package skeleton. Idempotent; never overwrites."""
    source = skeleton_dir()
    if not source.is_dir():
        raise HxError(f"{source}: the package skeleton is missing; the hx install is broken")

    root.mkdir(parents=True, exist_ok=True)
    for name in LAYOUT_DIRS:
        (root / name).mkdir(parents=True, exist_ok=True)

    created, skipped = _copy_tree(source, root)

    gitignore = root / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text(GITIGNORE)
        created.append(".gitignore")
    else:
        skipped.append(".gitignore")

    hx_json = root / "config" / "hx.json"
    before = hx_json.read_text() if hx_json.is_file() else None
    recorded = write_hx_json(root)
    # Always refreshed, because it records where this package's entry points actually are;
    # reported as kept when nothing moved, so a re-run is visibly a no-op.
    if hx_json.read_text() == before:
        skipped.append("config/hx.json")
    else:
        created.append("config/hx.json")

    missing = [name for name in EXPECTED_SKELETON_FILES if not (root / name).exists()]
    return {
        "root": str(root),
        "created": created,
        "skipped": skipped,
        "missing": missing,
        "hx_json": recorded,
    }


def main(argv: list[str], root: Path | None = None, *, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hx install", add_help=True)
    parser.add_argument("--root", help="HARNESS_ROOT to create (default: $HARNESS_ROOT, else ~/hx)")
    parser.add_argument(
        "--skeleton-only",
        action="store_true",
        help="create the layout and copy the package skeleton, and nothing else (spec 17.2 step 2)",
    )
    args = parser.parse_args(argv)

    root = resolve_root(args.root, env)
    if not args.skeleton_only:
        raise HxError(
            "install: not implemented (build-4); `hx install --skeleton-only --root <path>` "
            "creates the instance layout and skeleton (spec 17.2 step 2). The preflight "
            "checks, seed login, repo mirror, boot units and `hx launch partner` land with "
            "their milestones"
        )

    result = install_skeleton(root)
    print(f"root {result['root']}")
    for name in result["created"]:
        print(f"created  {name}")
    for name in result["skipped"]:
        print(f"kept     {name}")
    for name in result["missing"]:
        print(f"missing  {name}")
    return 0
