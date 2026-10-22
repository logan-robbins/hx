"""`hx doctor` — what is here, what is missing, what is broken (spec 08).

Every line is `<status>  <check>  <detail>`. It checks exactly what spec 08 lists: tmux, git,
the Python floor, the pinned `claude` binary and version, the paths in `config/hx.json`, the
seed token, each `run/<id>/home/settings.json`, and the sandbox flags of every running agent.

Worker homes come from admitted native launch capsules. Checks never infer assignment
state from worker files or boot a process.
"""

from __future__ import annotations

import argparse
import json
import re
import os
import platform
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from . import tmux
from .config_harness import PARTNER_FLAVORS, flavor_of
from .config_models import load_models
from .errors import ValidationError
from .ids import ID_RE
from .install import EXPECTED_SKELETON_FILES
from .root import resolve_root

#: Spec 17.2 step 1 and pyproject `requires-python`.
PYTHON_FLOOR = (3, 14)

from .doctor_homes import (OK, WARN, FAIL, _pi_home, _pi_instance, _grok_home, _grok_instance,
                           _meta_home, _meta_instance, _codex_home, _codex_instance)
from .doctor_context import checks as _complete_context_size

#: A path component that is a version (`muse-bin-1.4.3-R5018.1`, `claude/2.1.278/bin/claude`).
VERSIONED_PATH_RE = re.compile(r"(?:^|[-_/])v?[0-9]+\.[0-9]+(?:\.[0-9]+)?(?:[-_.][A-Za-z0-9]+)*(?:/|$)")


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
        ["ps", "-ww", "-o", "command=", "-p", pid], capture_output=True, text=True, check=False
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
                f"every agent runs sandboxed. Inspect `hx show {item_id}` and reconcile its native launch.",
            ))

    command = pane_command(item_id, env)
    if not command:
        found.append((WARN, f"could not read the pane's command line for {item_id}"))
    elif REQUIRED_FLAG in command:
        found.append((OK, f"{REQUIRED_FLAG} in the pane's argv"))
    elif "start.sh" in command:
        # The launcher has not exec'd the binary yet: it is still running its refusals.
        # Reading its argv here says nothing about the agent, and a
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
            f"Inspect `hx show {item_id}` and reconcile its native launch.",
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


















def live_meta_checks(root: Path, item_id: str, env=None) -> list[tuple[str, str]]:
    """A live meta session uses its own XDG home and runs unattended."""
    found: list[tuple[str, str]] = []
    environment = session_environment(item_id, env)
    expected = str(root / "run" / item_id / "home")
    actual = environment.get("XDG_CONFIG_HOME")
    if actual == expected:
        found.append((OK, f"XDG_CONFIG_HOME {expected}"))
    else:
        found.append((
            FAIL,
            f"XDG_CONFIG_HOME is {actual!r} on session {item_id}, not {expected!r}",
        ))
    command = pane_command(item_id, env)
    if not command:
        found.append((WARN, f"could not read the pane's command line for {item_id}"))
    elif "--yolo" in command:
        found.append((OK, "--yolo in the pane's argv"))
    elif "start.sh" in command:
        found.append((WARN, f"{item_id} is still launching (start.sh has not exec'd yet)"))
    else:
        found.append((FAIL, f"{item_id} is running without --yolo"))
    return found


def live_codex_checks(root: Path, item_id: str, env=None) -> list[tuple[str, str]]:
    """A live Codex session uses its own CODEX_HOME and runs unattended."""
    found: list[tuple[str, str]] = []
    environment = session_environment(item_id, env)
    expected = str(root / "run" / item_id / "home")
    actual = environment.get("CODEX_HOME")
    if actual == expected:
        found.append((OK, f"CODEX_HOME {expected}"))
    else:
        found.append((
            FAIL,
            f"CODEX_HOME is {actual!r} on session {item_id}, not {expected!r}",
        ))
    command = pane_command(item_id, env)
    if not command:
        found.append((WARN, f"could not read the pane's command line for {item_id}"))
    elif "--dangerously-bypass-approvals-and-sandbox" in command:
        found.append((OK, "--dangerously-bypass-approvals-and-sandbox in the pane's argv"))
    elif "start.sh" in command:
        found.append((WARN, f"{item_id} is still launching (start.sh has not exec'd yet)"))
    else:
        found.append((FAIL, f"{item_id} is running without --dangerously-bypass-approvals-and-sandbox"))
    return found


def live_pi_checks(root: Path, item_id: str, env=None) -> list[tuple[str, str]]:
    """A live Pi session uses its own config dir and ignores the workdir's `.pi/`."""
    found: list[tuple[str, str]] = []
    environment = session_environment(item_id, env)
    expected = str(root / "run" / item_id / "home")
    actual = environment.get("PI_CODING_AGENT_DIR")
    if actual == expected:
        found.append((OK, f"PI_CODING_AGENT_DIR {expected}"))
    else:
        found.append((
            FAIL,
            f"PI_CODING_AGENT_DIR is {actual!r} on session {item_id}, not {expected!r}",
        ))
    command = pane_command(item_id, env)
    if not command:
        found.append((WARN, f"could not read the pane's command line for {item_id}"))
    elif "--no-approve" in command or " -na" in f" {command}":
        found.append((OK, "--no-approve in the pane's argv"))
    elif "start.sh" in command:
        found.append((WARN, f"{item_id} is still launching (start.sh has not exec'd yet)"))
    else:
        found.append((FAIL, f"{item_id} is running without --no-approve; the workdir's .pi/ must not load"))
    return found


def live_grok_checks(root: Path, item_id: str, env=None) -> list[tuple[str, str]]:
    """A live Grok session uses its own GROK_HOME and runs minimal and unattended."""
    found: list[tuple[str, str]] = []
    environment = session_environment(item_id, env)
    expected = str(root / "run" / item_id / "home")
    actual = environment.get("GROK_HOME")
    if actual == expected:
        found.append((OK, f"GROK_HOME {expected}"))
    else:
        found.append((
            FAIL,
            f"GROK_HOME is {actual!r} on session {item_id}, not {expected!r}",
        ))
    command = pane_command(item_id, env)
    if not command:
        found.append((WARN, f"could not read the pane's command line for {item_id}"))
    elif "--minimal" in command and "bypassPermissions" in command:
        found.append((OK, "--minimal with bypassPermissions in the pane's argv"))
    elif "--fullscreen" in command:
        found.append((FAIL, f"{item_id} is running the fullscreen TUI; the default is --minimal"))
    elif "start.sh" in command:
        found.append((WARN, f"{item_id} is still launching (start.sh has not exec'd yet)"))
    else:
        found.append((FAIL, f"{item_id} is running without --minimal bypassPermissions"))
    return found


#: Skill directories an agent CLI may discover inside its working directory: Claude's project
#: skills, the Agent Skills location Codex and Pi scan, and Pi's own (TOOL_SKILL_PLAN step 7).
WORKDIR_SKILL_DIRS = (".claude/skills", ".agents/skills", ".pi/skills")
#: Flavors that scan `$HOME/.agents/skills` whatever their own config directory says.
HOST_SKILL_FLAVORS = ("codex", "pi")


def _workdir_of(root: Path, item_id: str) -> Path | None:
    """`harness.json` `workdir`, resolved against the root; None when unreadable or unset."""
    try:
        data = json.loads((root / "config" / item_id / "harness.json").read_text())
    except (OSError, json.JSONDecodeError):
        return None
    value = data.get("workdir") if isinstance(data, dict) else None
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    return path if path.is_absolute() else root / path


def skill_isolation(root: Path, ids: list[str], env=None) -> list[tuple[str, str, str]]:
    """`skills-isolation` warnings: skills that could reach a worker from outside its home.

    A worker's workdir is a product checkout, and its `.claude/skills`, `.agents/skills` or
    `.pi/skills` must never load into the worker. Claude under `--setting-sources user` is
    unverified for project skills on the pinned version; Codex and Pi need project trust, which
    hx never grants. Codex and Pi also scan `$HOME/.agents/skills` regardless of `CODEX_HOME`
    or `PI_CODING_AGENT_DIR`, so a non-empty one is reported while such a worker is configured
    (Pi launches with `--no-skills`). Warnings until docs/two-worlds.md records both checks.
    """
    env = os.environ if env is None else env
    rows: list[tuple[str, str, str]] = []
    workers = [item_id for item_id in ids if item_id != "partner"]
    for item_id in workers:
        workdir = _workdir_of(root, item_id)
        if workdir is None:
            continue
        found = [rel for rel in WORKDIR_SKILL_DIRS if (workdir / rel).is_dir()]
        if found:
            rows.append((WARN, "skills-isolation",
                         f"{item_id}: workdir {workdir} contains {', '.join(found)}; a checkout's "
                         "skills must never load into a worker (docs/two-worlds.md, Skills)"))
    host_workers = [i for i in workers if flavor_of(root, i) in HOST_SKILL_FLAVORS]
    home = env.get("HOME")
    if host_workers and home:
        host = Path(home) / ".agents" / "skills"
        try:
            entries = sorted(p.name for p in host.iterdir()) if host.is_dir() else []
        except OSError:
            entries = []
        if entries:
            rows.append((WARN, "skills-isolation",
                         f"~/.agents/skills holds {len(entries)} entr{'y' if len(entries) == 1 else 'ies'} "
                         f"while Codex or Pi worker(s) {', '.join(host_workers)} are configured; "
                         "Codex scans it whatever CODEX_HOME says (docs/two-worlds.md, Skills)"))
    return rows




def _persona_sources(root: Path, item_id: str) -> list[tuple[str, str, str]]:
    """What the `context` hook hands a worker (docs/prompt-runtime.md § Prompt layers): a
    `purpose` the Partner actually wrote and a role persona."""
    from .config_harness import purpose_text

    if item_id == "partner":
        return []
    try:
        data = json.loads((root / "config" / item_id / "harness.json").read_text())
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(data, dict):
        return []
    rows = []
    if purpose_text(data.get("purpose")) is None:
        rows.append((WARN, f"purpose:{item_id}",
                     "harness.json `purpose` is absent or still the template's placeholder, so the "
                     "identity line says nothing about what this agent is for; write one paragraph there"))
    role = data.get("role")
    if isinstance(role, str) and not (root / "personas" / f"{role}.md").is_file():
        rows.append((WARN, f"persona:{item_id}",
                     f"personas/{role}.md is missing; the agent gets its identity line, the "
                     "learnings and the pointer alone"))
    if (root / "config" / item_id / "SUBAGENTS.md").is_file():
        rows.append((OK, f"subagents:{item_id}",
                     f"config/{item_id}/SUBAGENTS.md supplies this worker's custom subagent instructions"))
    return rows


def _agent_home(root: Path, item_id: str) -> Path | None:
    """Only a Partner bootstrap or an active ledger launch owns an agent home."""
    if item_id == "partner":
        return root / "run" / item_id / "home"
    from .board import ledger_reader
    with ledger_reader(root) as db:
        if db is None:
            return None
        row = db.execute("""SELECT l.payload FROM native_launches l JOIN runs r USING(run_id)
            JOIN unit_runs u USING(run_id) WHERE r.worker_id=? AND r.ended_at IS NULL
            AND l.status<>'archived' ORDER BY l.rowid DESC LIMIT 1""", (item_id,)).fetchone()
        if row is None:
            return None
        capsule = Path(json.loads(row[0])["capsule"])
        return capsule / "run" / item_id / "home"


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
                from . import claude_bin
                if pinned_version and installed_version and installed_version != pinned_version:
                    # A record, not a gate: the binary moved on and the next launch boundary
                    # re-records it (`hx.claude_bin.refresh_pin`). Worth knowing, never fatal.
                    checks.append((
                        WARN,
                        "claude",
                        f"{binary} now reports {installed_version}; config/claude.json recorded "
                        f"{pinned_version}. The next `hx launch` or `hx up` "
                        f"re-records it ({claude_bin.describe(installed_version)})",
                    ))
                else:
                    shown = installed_version or pinned_version
                    checks.append((
                        OK, "claude",
                        f"{binary} at {claude_bin.describe(shown) if shown else 'an unrecorded version'}",
                    ))
    else:
        checks.append(
            (WARN, "claude", "config/claude.json absent; `hx install` records {bin, version} (spec 17.1)")
        )

    if not root.is_dir():
        return checks

    # Central learnings (docs/learnings.md): where the directory is, how many rules, what waits.
    from . import learn as learn_mod

    # An instance that points `learnings` elsewhere (a fleet's shared directory) does not use
    # its own LEARNINGS/ skeleton files; the `learnings` row below reports the directory it uses.
    own_learnings = learn_mod.learnings_path(root, env) == root / learn_mod.DIRECTORY_NAME
    for relative in EXPECTED_SKELETON_FILES:
        if relative.startswith(learn_mod.DIRECTORY_NAME + "/") and not own_learnings:
            continue
        target = root / relative
        checks.append(
            (OK, "skeleton", relative) if target.exists() else (WARN, "skeleton", f"{relative} missing")
        )


    try:
        learnings = learn_mod.summary(root, env)
    except ValidationError as exc:
        checks.append((FAIL, "learnings", str(exc)))
    else:
        lint_failed = [detail for status, detail in learn_mod.check(root, env) if status == FAIL]
        if lint_failed:
            checks.append((FAIL, "learnings", f"{learnings['path']}: {lint_failed[0]} (hx learn check)"))
        elif not learnings["exists"]:
            checks.append((WARN, "learnings", f"{learnings['path']} does not exist yet; `hx install` ships it"))
        else:
            by_category = ", ".join(f"{c} {n}" for c, n in learnings["categories"].items()) or "none"
            checks.append((
                OK if not learnings["pending"] else WARN, "learnings",
                f"{learnings['path']}: {learnings['rules']} rule(s) ({by_category}), "
                f"{learnings['pending']} pending proposal(s)"
                + (" (hx learn pending)" if learnings["pending"] else ""),
            ))
            # Every agent is handed the global rules and its role's at every boundary
            # (docs/learnings.md); complete delivery must fit the configured native context.
            checks.extend(_complete_context_size(root, env))
    # Every adapter records its binary as `bin`; a versioned path dies with the next auto-update
    # (the launcher deletes the old version), so the path must be the stable launcher.
    for adapter_json in sorted((root / "config").glob("*.json")):
        if adapter_json.name in ("hx.json", "models.json", "ui.json", "partner-guard.json"):
            continue
        try:
            recorded = json.loads(adapter_json.read_text())
        except json.JSONDecodeError:
            continue
        binary = recorded.get("bin") if isinstance(recorded, dict) else None
        if binary and VERSIONED_PATH_RE.search(str(binary)):
            checks.append((FAIL, "bin-path", f"config/{adapter_json.name} bin {binary} names a version; "
                           "record the stable launcher path instead (an auto-update deletes old versions)"))

    models_path = root / "config" / "models.json"
    if models_path.is_file():
        try:
            models = load_models(models_path)
        except ValidationError as exc:
            checks.append((FAIL, "models", str(exc)))
        else:
            # One line, whatever the row count: docs/deploy.md's `hx doctor` block is compared
            # line for line against this output. The numbers are on it because
            # `autocompact_window` is exported into the agent's session as
            # `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, and "which window is this fleet actually
            # running at" is otherwise three files away.
            described = ", ".join(
                f"{model.id} window={model.window} seam={model.threshold}"
                + (
                    f" autocompact={model.autocompact_window}"
                    if model.autocompact_window
                    else " autocompact=native"
                )
                for model in sorted(models.values(), key=lambda m: m.id)
            )
            checks.append((OK, "models", f"{len(models)} model(s): {described}"))

    # The skills the adapters install (TOOL_SKILL_PLAN step 1): the manifest, the Agent Skills
    # frontmatter rules, the size bounds, and no worker skill naming a command a worker is
    # refused. `HX_SKILLS_DIR` points it at another directory, as it does the adapters.
    from . import skill_manifest

    for status, detail in skill_manifest.check(skill_manifest.skills_dir(env),
                                               root if root.is_dir() else None):
        checks.append((status, "skills", detail))

    hx_json = root / "config" / "hx.json"
    if hx_json.is_file():
        try:
            recorded = json.loads(hx_json.read_text())
        except json.JSONDecodeError as exc:
            checks.append((FAIL, "hx.json", f"config/hx.json is not valid JSON: {exc}"))
            recorded = {}
        # `bin/hx` and `bin/hx-hook` link to these, and every agent's hooks run `bin/hx-hook`;
        # a missing one is silently broken hooks (spec 17.1).
        for key in ("hx_bin", "hook_bin"):
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
        checks.extend(_persona_sources(root, item_id))
        home = _agent_home(root, item_id)
        if home is None:
            continue
        flavor = flavor_of(root, item_id)
        if item_id == "partner" and flavor not in PARTNER_FLAVORS:
            checks.append((
                FAIL, f"flavor:{item_id}",
                "the Partner runs on claude or codex (spec 12)",
            ))
        if not home.is_dir():
            checks.append((
                WARN, f"home:{item_id}",
                f"{home} absent; inspect the native launch preparation",
            ))
            continue
        # Homes hold no credentials: auth is the instance token (spec 11 Auth).
        # Grok and codex homes carry config.toml and a meta home muse/settings.json
        # instead of settings.json.
        target = home / {
            "grok": "config.toml",
            "codex": "config.toml",
            "meta": "muse/settings.json",
        }.get(flavor, "settings.json")
        checks.append(
            (OK, f"home:{item_id}", target.name)
            if target.is_file()
            else (FAIL, f"home:{item_id}", f"{target.name} missing; `hx launch` writes it")
        )
        if flavor == "pi":
            checks.extend(_pi_home(root, home, item_id))
        elif flavor == "grok":
            checks.extend(_grok_home(root, home, item_id))
        elif flavor == "meta":
            checks.extend(_meta_home(root, home, item_id))
        elif flavor == "codex":
            checks.extend(_codex_home(root, home, item_id))
        else:
            # The pre-seeded first-launch state (spec 08): without it the pane stops at the
            # onboarding wizard or the workspace-trust dialog and nothing about launch is
            # non-interactive any more (CONTRACTS.md `run/<id>/home/.claude.json`).
            checks.append(_first_launch_state(home, item_id))

    pi_ids = [item_id for item_id in ids if flavor_of(root, item_id) == "pi"]
    if pi_ids:
        checks.extend(_pi_instance(root))
    grok_ids = [item_id for item_id in ids if flavor_of(root, item_id) == "grok"]
    if grok_ids:
        checks.extend(_grok_instance(root))
    meta_ids = [item_id for item_id in ids if flavor_of(root, item_id) == "meta"]
    if meta_ids:
        checks.extend(_meta_instance(root))
    codex_ids = [item_id for item_id in ids if flavor_of(root, item_id) == "codex"]
    if codex_ids:
        checks.extend(_codex_instance(root, codex_ids))
    # Two-worlds isolation for skill discovery (TOOL_SKILL_PLAN step 7).
    checks.extend(skill_isolation(root, ids, env))

    # A live agent must be sandboxed and bypassing permissions, always (spec 11, the
    # 2026-09-20 directive). Checked from the session environment tmux reports and from the
    # argv of the process in the pane, because either one alone can be stale.
    # A Pi session has neither flag; it is checked for its own config dir and --no-approve.
    for item_id in ids:
        if item_id != "partner" or not tmux.has_session(item_id, env):
            continue
        if flavor_of(root, item_id) == "pi":
            for status, detail in live_pi_checks(root, item_id, env):
                checks.append((status, f"pi:{item_id}", detail))
        elif flavor_of(root, item_id) == "grok":
            for status, detail in live_grok_checks(root, item_id, env):
                checks.append((status, f"grok:{item_id}", detail))
        elif flavor_of(root, item_id) == "meta":
            for status, detail in live_meta_checks(root, item_id, env):
                checks.append((status, f"meta:{item_id}", detail))
        elif flavor_of(root, item_id) == "codex":
            for status, detail in live_codex_checks(root, item_id, env):
                checks.append((status, f"codex:{item_id}", detail))
        else:
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
