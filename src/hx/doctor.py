"""`hx doctor` — what is here, what is missing, what is broken (spec 08).

Every line is `<status>  <check>  <detail>`. It checks exactly what spec 08 lists: tmux, git,
the Python floor, the pinned `claude` binary and version, the paths in `config/hx.json`, the
seed token, each `run/<id>/home/settings.json`, and the sandbox flags of every running agent.

It does not inspect work items (spec 14 D25): the board is a listing and the doctor is not a
policeman. `warn` is for what a milestone has not delivered yet, so `hx doctor` on a freshly
created skeleton exits 0.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from . import tmux
from .config_models import load_models
from .errors import ValidationError
from .ids import ID_RE
from .install import EXPECTED_SKELETON_FILES
from .root import resolve_root

#: Spec 17.2 step 1 and pyproject `requires-python`.
PYTHON_FLOOR = (3, 14)

OK, WARN, FAIL = "ok", "warn", "fail"


#: `claude --version` prints `2.1.278 (Claude Code)`; both config/claude.json and
#: packaging/tested-claude-versions.json hold the bare version (CONTRACTS.md).
CLAUDE_VERSION_SUFFIX = " (Claude Code)"


def bare_claude_version(output: str) -> str:
    return output.strip().removesuffix(CLAUDE_VERSION_SUFFIX).strip()


def _binary_version(binary: str, *args: str) -> str | None:
    path = shutil.which(binary)
    if not path:
        return None
    result = subprocess.run([path, *args], capture_output=True, text=True, check=False)
    return (result.stdout or result.stderr).strip().splitlines()[0] if (result.stdout or result.stderr) else path


#: Spec 11: not negotiable, and checked on every live agent.
REQUIRED_ENV = ("IS_SANDBOX=1",)
REQUIRED_FLAG = "--dangerously-skip-permissions"


def session_environment(item_id: str, env=None) -> dict[str, str]:
    """What `tmux show-environment -t <id>` reports for the agent's session."""
    result = subprocess.run(
        [*tmux.tmux_command(env), "show-environment", "-t", f"={item_id}"],
        capture_output=True, text=True, check=False,
    )
    found: dict[str, str] = {}
    if result.returncode != 0:
        return found
    for line in result.stdout.splitlines():
        if "=" in line and not line.startswith("-"):
            key, _, value = line.partition("=")
            found[key] = value
    return found


def pane_command(item_id: str, env=None) -> str:
    """The full command line of the process in `<id>:main`."""
    result = subprocess.run(
        [*tmux.tmux_command(env), "list-panes", "-t", f"={item_id}:main", "-F", "#{pane_pid}"],
        capture_output=True, text=True, check=False,
    )
    pid = result.stdout.strip().splitlines()[0] if result.returncode == 0 and result.stdout.strip() else ""
    if not pid:
        return ""
    listing = subprocess.run(
        ["ps", "-o", "command=", "-p", pid], capture_output=True, text=True, check=False
    )
    if listing.returncode == 0 and listing.stdout.strip():
        return listing.stdout.strip()
    # The pane's own process is a shell or the launcher; look at its children too.
    children = subprocess.run(
        ["ps", "-o", "command=", "-ax"], capture_output=True, text=True, check=False
    )
    return children.stdout if children.returncode == 0 else ""


def live_agent_checks(item_id: str, env=None) -> list[tuple[str, str]]:
    """(status, detail) for one live agent's sandbox and permission flags."""
    found: list[tuple[str, str]] = []
    environment = session_environment(item_id, env)
    for pair in REQUIRED_ENV:
        key, _, value = pair.partition("=")
        if environment.get(key) == value:
            found.append((OK, f"{pair} on the tmux session"))
        else:
            found.append((
                FAIL,
                f"{key} is {environment.get(key, 'unset')!r} on session {item_id}, not {value!r}; "
                f"every agent runs sandboxed (spec 11). Relaunch it: `hx restart {item_id}`",
            ))

    command = pane_command(item_id, env)
    if not command:
        found.append((WARN, f"could not read the pane's command line for {item_id}"))
    elif REQUIRED_FLAG in command:
        found.append((OK, f"{REQUIRED_FLAG} in the pane's argv"))
    elif "start.sh" in command:
        # The launcher has not exec'd the binary yet: it is still running its refusals and
        # deriving persona.md. Reading its argv here says nothing about the agent, and a
        # failure would be simply untrue (handoff/gtm-to-build.md gtm-8).
        found.append((
            WARN,
            f"{item_id} is still launching (start.sh has not exec'd yet); "
            f"the flag cannot be read until it has",
        ))
    else:
        found.append((
            FAIL,
            f"{item_id} is running without {REQUIRED_FLAG}; every agent bypasses permissions "
            f"(spec 11). Relaunch it: `hx restart {item_id}`",
        ))
    return found


def _first_launch_state(home: Path, item_id: str) -> tuple[str, str, str]:
    """`run/<id>/home/.claude.json`: onboarding done and the cwd's trust dialog accepted."""
    path = home / ".claude.json"
    label = f"home:{item_id}"
    if not path.is_file():
        return (FAIL, label, ".claude.json missing; `hx launch` pre-seeds it, and without it "
                             "the pane stops at the onboarding wizard (CONTRACTS.md)")
    try:
        state = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        return (FAIL, label, f".claude.json is not valid JSON: {exc}")
    if not isinstance(state, dict) or state.get("hasCompletedOnboarding") is not True:
        return (FAIL, label, ".claude.json has no `hasCompletedOnboarding: true`; the first "
                             "launch would stop at the onboarding wizard")
    projects = state.get("projects")
    trusted = [
        cwd for cwd, entry in (projects or {}).items()
        if isinstance(entry, dict) and entry.get("hasTrustDialogAccepted") is True
    ] if isinstance(projects, dict) else []
    if not trusted:
        return (FAIL, label, ".claude.json accepts no workspace trust dialog; the first launch "
                             'would stop at "Quick safety check … Yes, I trust this folder"')
    return (OK, label, f".claude.json (onboarding done, {len(trusted)} trusted cwd)")


def run_checks(root: Path, *, env: dict[str, str] | None = None) -> list[tuple[str, str, str]]:
    checks: list[tuple[str, str, str]] = []

    version = sys.version_info
    python_ok = (version.major, version.minor) >= PYTHON_FLOOR
    checks.append(
        (
            OK if python_ok else FAIL,
            "python",
            f"{platform.python_version()} ({sys.executable})"
            + ("" if python_ok else f"; hx needs >= {'.'.join(map(str, PYTHON_FLOOR))} (spec 17.2)"),
        )
    )

    tmux_version = tmux.version(env)
    checks.append(
        (OK, "tmux", tmux_version) if tmux_version else (FAIL, "tmux", "not found on PATH; hx runs every agent in tmux")
    )

    git_version = _binary_version("git", "--version")
    checks.append(
        (OK, "git", git_version) if git_version else (FAIL, "git", "not found on PATH")
    )

    checks.append((OK, "root", str(root)) if root.is_dir() else (WARN, "root", f"{root} does not exist; run `hx install --skeleton-only --root {root}`"))
    if root.exists() and not root.is_dir():
        checks.append((FAIL, "root", f"{root} exists and is not a directory"))

    claude_json = root / "config" / "claude.json"
    if claude_json.is_file():
        try:
            pinned = json.loads(claude_json.read_text())
        except json.JSONDecodeError as exc:
            checks.append((FAIL, "claude", f"config/claude.json is not valid JSON: {exc}"))
        else:
            binary = pinned.get("bin")
            pinned_version = pinned.get("version")
            if not binary:
                checks.append((FAIL, "claude", "config/claude.json has no `bin` (spec 17.1)"))
            elif not Path(binary).is_file():
                checks.append((FAIL, "claude", f"pinned binary {binary} is not there (spec 17.1)"))
            else:
                installed = subprocess.run(
                    [binary, "--version"], capture_output=True, text=True, check=False
                )
                installed_version = bare_claude_version(installed.stdout or "")
                if pinned_version and installed_version and installed_version != pinned_version:
                    checks.append((
                        FAIL,
                        "claude",
                        f"{binary} reports {installed_version} but config/claude.json pins "
                        f"{pinned_version}; install the pinned version or re-run "
                        f"`hx install --claude <bin>` (spec 17.1)",
                    ))
                else:
                    checks.append(
                        (OK, "claude", f"{binary} pinned at {pinned_version or 'an unrecorded version'}")
                    )
    else:
        checks.append(
            (WARN, "claude", "config/claude.json absent; `hx install` records {bin, version} (spec 17.1)")
        )

    if not root.is_dir():
        return checks

    for relative in EXPECTED_SKELETON_FILES:
        target = root / relative
        checks.append(
            (OK, "skeleton", relative) if target.exists() else (WARN, "skeleton", f"{relative} missing")
        )

    models_path = root / "config" / "models.json"
    if models_path.is_file():
        try:
            models = load_models(models_path)
        except ValidationError as exc:
            checks.append((FAIL, "models", str(exc)))
        else:
            checks.append((OK, "models", f"{len(models)} model(s): {', '.join(sorted(models))}"))

    hx_json = root / "config" / "hx.json"
    if hx_json.is_file():
        try:
            recorded = json.loads(hx_json.read_text())
        except json.JSONDecodeError as exc:
            checks.append((FAIL, "hx.json", f"config/hx.json is not valid JSON: {exc}"))
            recorded = {}
        # Every `run/<id>/home/settings.json` points its hooks at `hook_bin`, and the adapters
        # read JSON with `python_bin`; a missing one is silently broken hooks (spec 17.1).
        for key in ("hx_bin", "hook_bin", "python_bin"):
            value = recorded.get(key)
            if not value:
                checks.append((FAIL, "hx.json", f"config/hx.json has no `{key}` (CONTRACTS.md)"))
            elif not os.access(value, os.X_OK):
                checks.append((FAIL, "hx.json", f"`{key}` {value} is missing or not executable"))
            else:
                checks.append((OK, "hx.json", f"{key} {value}"))
    else:
        checks.append(
            (WARN, "hx.json", "config/hx.json absent; `hx install --skeleton-only` records it (CONTRACTS.md)")
        )

    # `bin/hx` and `bin/hx-hook` are what puts hx on every agent's PATH (spec 03, 17.1).
    for name in ("hx", "hx-hook"):
        link = root / "bin" / name
        if not link.exists():
            checks.append((
                FAIL, "bin",
                f"bin/{name} is missing; agents get hx on their PATH from $HARNESS_ROOT/bin "
                f"(spec 03). `hx install` writes it",
            ))
        elif not os.access(link, os.X_OK):
            checks.append((
                FAIL, "bin",
                f"bin/{name} does not resolve to an executable "
                f"(-> {os.readlink(link) if link.is_symlink() else link}); re-run `hx install`",
            ))
        else:
            checks.append((OK, "bin", f"bin/{name} -> {os.path.realpath(link)}"))

    token = root / "seed" / "token"
    if not token.is_file():
        checks.append((
            FAIL, "token",
            "seed/token absent; the human runs `claude setup-token` once and pastes the token "
            "there, mode 0600 (spec 11 Auth, 17.2 step 3)",
        ))
    elif token.stat().st_mode & 0o77:
        checks.append((FAIL, "token", "seed/token is readable by group or other; it must be mode 0600"))
    elif not token.read_text().strip():
        checks.append((FAIL, "token", "seed/token is empty"))
    else:
        checks.append((OK, "token", "seed/token present, mode 0600"))

    config_dir = root / "config"
    ids = sorted(e.name for e in config_dir.iterdir() if e.is_dir() and ID_RE.match(e.name)) if config_dir.is_dir() else []
    if not ids:
        checks.append((WARN, "agents", "no config/<id>/ yet"))
    for item_id in ids:
        home = root / "run" / item_id / "home"
        if not home.is_dir():
            checks.append((WARN, f"home:{item_id}", "run/<id>/home absent; `hx launch` runs adapters/claude/install.sh"))
            continue
        # Homes hold no credentials: auth is the instance token (spec 11 Auth).
        target = home / "settings.json"
        checks.append(
            (OK, f"home:{item_id}", "settings.json")
            if target.is_file()
            else (FAIL, f"home:{item_id}", "settings.json missing; `hx launch` writes it")
        )
        # The pre-seeded first-launch state (spec 08): without it the pane stops at the
        # onboarding wizard or the workspace-trust dialog and nothing about launch is
        # non-interactive any more (CONTRACTS.md `run/<id>/home/.claude.json`).
        checks.append(_first_launch_state(home, item_id))

    # A live agent must be sandboxed and bypassing permissions, always (spec 11, the
    # 2026-09-20 directive). Checked from the session environment tmux reports and from the
    # argv of the process in the pane, because either one alone can be stale.
    for item_id in ids:
        if not tmux.has_session(item_id, env):
            continue
        for status, detail in live_agent_checks(item_id, env):
            checks.append((status, f"sandbox:{item_id}", detail))

    return checks


def main(argv: list[str], root: Path | None = None, *, env: dict[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hx doctor", add_help=True)
    parser.add_argument("--root", help=argparse.SUPPRESS)
    parser.add_argument("--json", action="store_true", help="emit the checks as JSON")
    args = parser.parse_args(argv)

    root = resolve_root(args.root, env) if root is None or args.root else root
    checks = run_checks(root, env=env)
    failed = [c for c in checks if c[0] == FAIL]

    if args.json:
        print(json.dumps({"root_abs": str(root), "checks": [
            {"status": s, "check": c, "detail": d} for s, c, d in checks
        ]}, indent=2))
    else:
        width = max(len(check) for _, check, _ in checks)
        for status, check, detail in checks:
            print(f"{status:<5} {check:<{width}}  {detail}")
        if failed:
            print(f"\n{len(failed)} check(s) failed")
    return 1 if failed else 0
