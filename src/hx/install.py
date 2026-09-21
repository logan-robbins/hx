"""`hx install` — the one manual command (spec 17.2).

Four steps, in order:

  1. refuse root; check tmux, git, Python; find `claude` and pin a tested version
  2. create the instance from the package skeleton
  3. the seed token — the one thing only a human can do
  4. `hx launch partner` and `hx ui`, then print `tmux attach -t partner` and the URL

That is the whole of deployment (spec 14 D25): no mirror, no worktrees, no unit files. After
this the human types nothing but chat.

hx reads nothing from the user's `~/.claude` at any point, on any platform: auth is the
instance token at `seed/token` (spec 11 Auth). `--skeleton-only` stops after step 2, which is
what the test suites use.
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import sys
from pathlib import Path

from . import store
from .errors import HxError
from .ids import PARTNER
from .root import resolve_root

#: Directories of spec 03 that exist in every instance from the moment it is created.
LAYOUT_DIRS = (
    "adapters",
    "archive",
    "bin",
    "config",
    "logs",
    "pods",
    "run",
    "seed",
    "state",
)

#: Spec 03: everything but `config/` is runtime state, so the instance ignores it in git.
GITIGNORE = """# Written by `hx install` (spec 03). Only config/ is worth committing.
pods/
logs/
state/
run/
tasks.json
"""

#: Exactly what spec 17.2 step 2 says a fresh instance is created with, plus the adapters.
#: No worker: the example worker configuration ships as `templates/worker/` for the Partner to
#: copy into `config/<id>/` when it creates an agent (CONTRACTS.md "Fresh instance contents").
EXPECTED_SKELETON_FILES = (
    "PARTNER.md",
    "adapters/claude/install.sh",
    "adapters/claude/start.sh",
    "adapters/claude/seam-command",
    "adapters/pi/install.sh",
    "adapters/pi/start.sh",
    "adapters/pi/seam-command",
    "adapters/pi/extension/index.ts",
    "companion/BASE.md",
    "companion/roles/partner.md",
    "config/CLAUDE.md",
    "config/models.json",
    "config/partner/AGENTS.md",
    "config/partner/SUBAGENTS.md",
    "config/partner/harness.json",
    "personas/partner/AGENTS.md",
    "templates/work-item.md",
)


def entry_points() -> dict[str, str]:
    """Absolute paths of `hx`, `hx-hook` and the interpreter (CONTRACTS.md `config/hx.json`).

    The entry-point scripts sit next to the running interpreter — that is where `uv tool
    install` and a venv both put them — so resolve from `sys.executable` first and fall back
    to PATH. Hook commands in every `run/<id>/home/settings.json` reference these, so a
    package upgrade that moves them is followed by another `hx install`, which re-records
    them (spec 17.1).
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
    link_bin(root, recorded)
    return recorded


#: `bin/hx` and `bin/hx-hook` of spec 03, as symlinks to whatever `config/hx.json` records.
BIN_LINKS = (("hx", "hx_bin"), ("hx-hook", "hook_bin"))


def link_bin(root: Path, recorded: dict[str, str]) -> list[Path]:
    """Point `$HARNESS_ROOT/bin/{hx,hx-hook}` at the package's entry points (spec 03).

    `start.sh` prepends this directory to every agent's `PATH`, so an agent runs `hx board`
    rather than hunting for the binary through `config/hx.json` — which is what the Partner
    had to do in the live rehearsal of 2026-09-20 21:20 (build-8 item 11). Symlinks, not
    copies, so a package upgrade that moves the entry point is followed by one `hx install`.
    """
    bindir = root / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, key in BIN_LINKS:
        target = recorded.get(key)
        link = bindir / name
        if not target:
            continue
        if link.is_symlink() or link.exists():
            if link.is_symlink() and os.readlink(link) == target:
                written.append(link)
                continue
            link.unlink()
        link.symlink_to(target)
        written.append(link)
    return written


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


#: Spec 17.2 step 3 stops here until the human has pasted the token. A distinct exit code, so
#: a script can tell "waiting for the human" from "something is wrong".
TOKEN_WAIT_EXIT = 4


def preflight(root: Path, claude: str | None, env=None) -> dict:
    """Step 1: refuse root, check the tools, pin a tested `claude` (spec 17.2 step 1)."""
    import shutil
    import subprocess
    import sys

    from . import claude_bin

    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise HxError(
            "refuse: hx does not run as root. Every agent launches Claude Code with "
            "--dangerously-skip-permissions, which Claude Code itself refuses under root or "
            "sudo (spec 17.2 step 1, 11 Permissions)",
            exit_code=1,
        )

    version = sys.version_info
    if (version.major, version.minor) < (3, 14):
        raise HxError(f"hx needs Python 3.14 or newer; this is {platform.python_version()}")

    for tool in ("tmux", "git"):
        if not shutil.which(tool):
            raise HxError(f"{tool} is not on PATH; hx needs it (spec 17.2 step 1)")

    binary = claude_bin.find_binary(claude)
    pinned = claude_bin.probe(binary)
    claude_bin.require_tested(pinned)
    return {"bin": binary, "version": pinned}


def seed_token_path(root: Path) -> Path:
    return root / "seed" / "token"


def check_seed_token(root: Path) -> bool:
    """Step 3: the token is the one thing hx cannot do for the human (spec 11 Auth).

    Returns True when it is there; tightens its mode to 0600 on the way past.
    """
    token = seed_token_path(root)
    token.parent.mkdir(parents=True, exist_ok=True)
    if not token.is_file() or not token.read_text().strip():
        return False
    token.chmod(0o600)
    return True


def token_instructions(root: Path) -> str:
    return (
        "hx needs one long-lived token for this instance. Two steps, both yours:\n"
        "\n"
        "  1. claude setup-token\n"
        f"  2. paste the token it prints into {seed_token_path(root)}\n"
        "\n"
        "Then run this command again. hx reads nothing from your own ~/.claude, on any\n"
        "platform: this token is the whole of an agent's auth (spec 11 Auth)."
    )


def main(argv: list[str], root: Path | None = None, *, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hx install", add_help=True)
    parser.add_argument("--root", help="HARNESS_ROOT to create (default: $HARNESS_ROOT, else ~/hx)")
    parser.add_argument("--claude", default=None, help="the claude binary to pin (default: PATH)")
    parser.add_argument(
        "--skeleton-only",
        action="store_true",
        help="stop after step 2: the layout and the package skeleton (spec 17.2)",
    )
    args = parser.parse_args(argv)

    env = os.environ if env is None else env
    root = resolve_root(args.root, env)

    # --- step 1 ------------------------------------------------------------------------
    pin = None
    if not args.skeleton_only:
        pin = preflight(root, args.claude, env)
        print(f"1. claude {pin['version']} at {pin['bin']}")

    # --- step 2 ------------------------------------------------------------------------
    result = install_skeleton(root)
    print(f"2. instance at {result['root']}")
    for name in result["created"]:
        print(f"   created  {name}")
    for name in result["skipped"]:
        print(f"   kept     {name}")
    for name in result["missing"]:
        print(f"   missing  {name}")

    if pin is not None:
        from . import claude_bin

        claude_bin.write_pin(root, pin["bin"], pin["version"])
        print(f"   created  {claude_bin.CONFIG}")

    if args.skeleton_only:
        return 0

    # --- step 3 ------------------------------------------------------------------------
    if not check_seed_token(root):
        print()
        print(token_instructions(root))
        return TOKEN_WAIT_EXIT
    print(f"3. seed token at {seed_token_path(root)}, mode 0600")

    # --- step 4 ------------------------------------------------------------------------
    from . import lifecycle

    launched = lifecycle.launch(root, PARTNER, env=env)
    print(f"4. partner {launched['session']}")
    lifecycle.start_ui(root, env=env)
    print(f"   ui {lifecycle.ui_url(root)}")
    print()
    print("tmux attach -t partner")
    print(lifecycle.ui_url(root))
    return 0
