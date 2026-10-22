"""Adapter home and credential diagnostics, independent of live agent launch."""
from __future__ import annotations

import json
import os
import tomllib
from pathlib import Path

OK, WARN, FAIL = "ok", "warn", "fail"


def _pi_home(root: Path, home: Path, item_id: str) -> list[tuple[str, str, str]]:
    """A Pi home is settings, the hx extension, and an auth file. No `.claude.json`."""
    label = f"home:{item_id}"
    found: list[tuple[str, str, str]] = []
    settings_path = home / "settings.json"
    trust = None
    reserve = None
    if settings_path.is_file():
        try:
            settings = json.loads(settings_path.read_text())
        except json.JSONDecodeError:
            settings = None
        if isinstance(settings, dict):
            trust = settings.get("defaultProjectTrust")
            compaction = settings.get("compaction")
            if isinstance(compaction, dict):
                reserve = compaction.get("reserveTokens")
    if trust == "never":
        found.append((OK, label, "defaultProjectTrust never"))
    else:
        found.append((FAIL, label, "settings.json `defaultProjectTrust` is not `never`"))

    extension = home / "extensions" / "hx" / "index.ts"
    found.append(
        (OK, label, "extensions/hx/index.ts")
        if extension.is_file()
        else (FAIL, label, "extensions/hx/index.ts missing; adapters/pi/install.sh copies it")
    )

    auth = home / "auth.json"
    if not auth.is_file():
        found.append((FAIL, label, "auth.json missing; adapters/pi/install.sh writes it from seed/pi-token"))
    elif auth.stat().st_mode & 0o77:
        found.append((FAIL, label, "auth.json is readable by group or other; it must be mode 0600"))
    else:
        found.append((OK, label, "auth.json mode 0600"))

    if isinstance(reserve, int):
        found.append((OK, label, f"compaction reserveTokens={reserve}"))
    return found


def _pi_instance(root: Path) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    token = root / "seed" / "pi-token"
    if not token.is_file():
        found.append((
            FAIL, "pi-token",
            "seed/pi-token absent; a Pi worker authenticates from that file, mode 0600",
        ))
    elif token.stat().st_mode & 0o77:
        found.append((FAIL, "pi-token", "seed/pi-token is readable by group or other; it must be mode 0600"))
    elif not token.read_text().strip():
        found.append((FAIL, "pi-token", "seed/pi-token is empty"))
    else:
        found.append((OK, "pi-token", "seed/pi-token present, mode 0600"))

    recorded = root / "config" / "pi.json"
    if not recorded.is_file():
        found.append((WARN, "pi", "config/pi.json absent; record the pi binary's path as `bin`"))
    else:
        try:
            body = json.loads(recorded.read_text())
        except json.JSONDecodeError as exc:
            found.append((FAIL, "pi", f"config/pi.json is not valid JSON: {exc}"))
        else:
            binary = body.get("bin") if isinstance(body, dict) else None
            if binary and os.access(binary, os.X_OK):
                found.append((OK, "pi", binary))
            else:
                found.append((FAIL, "pi", f"config/pi.json bin {binary!r} is missing or not executable"))
    return found


def _grok_home(root: Path, home: Path, item_id: str) -> list[tuple[str, str, str]]:
    """A Grok home is config.toml: minimal screen, always-approve, goal mode, hx hooks."""
    label = f"home:{item_id}"
    found: list[tuple[str, str, str]] = []
    config_path = home / "config.toml"
    try:
        config = tomllib.loads(config_path.read_text())
    except (OSError, ValueError):
        config = None
    if not isinstance(config, dict):
        found.append((FAIL, label, "config.toml is missing or not valid TOML"))
        return found
    ui = config.get("ui") if isinstance(config.get("ui"), dict) else {}
    if ui.get("screen_mode") == "minimal":
        found.append((OK, label, "screen_mode minimal"))
    else:
        found.append((FAIL, label, "config.toml `[ui] screen_mode` is not `minimal`"))
    if ui.get("permission_mode") == "always-approve":
        found.append((OK, label, "permission_mode always-approve"))
    else:
        found.append((FAIL, label, "config.toml `[ui] permission_mode` is not `always-approve`"))
    goal = config.get("goal") if isinstance(config.get("goal"), dict) else {}
    if goal.get("enabled") is True:
        found.append((OK, label, "goal mode enabled"))
    else:
        found.append((FAIL, label, "config.toml `[goal] enabled` is not true"))
    hooks = config.get("hooks") if isinstance(config.get("hooks"), dict) else {}
    if hooks and all(
        isinstance(hooks.get(event), list) and hooks[event] for event in ("SessionStart", "PostToolUse", "Stop")
    ):
        found.append((OK, label, "hx hooks wired"))
    else:
        found.append((FAIL, label, "config.toml hooks are missing SessionStart/PostToolUse/Stop"))
    return found


def _grok_instance(root: Path) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    token = root / "seed" / "grok-token"
    if not token.is_file():
        found.append((
            FAIL, "grok-token",
            "seed/grok-token absent; a Grok worker authenticates from that file, mode 0600",
        ))
    elif token.stat().st_mode & 0o77:
        found.append((FAIL, "grok-token", "seed/grok-token is readable by group or other; it must be mode 0600"))
    elif not token.read_text().strip():
        found.append((FAIL, "grok-token", "seed/grok-token is empty"))
    else:
        found.append((OK, "grok-token", "seed/grok-token present, mode 0600"))

    recorded = root / "config" / "grok.json"
    if not recorded.is_file():
        found.append((WARN, "grok", "config/grok.json absent; record the grok binary's path as `bin`"))
    else:
        try:
            body = json.loads(recorded.read_text())
        except json.JSONDecodeError as exc:
            found.append((FAIL, "grok", f"config/grok.json is not valid JSON: {exc}"))
        else:
            binary = body.get("bin") if isinstance(body, dict) else None
            if binary and os.access(binary, os.X_OK):
                found.append((OK, "grok", binary))
            else:
                found.append((FAIL, "grok", f"config/grok.json bin {binary!r} is missing or not executable"))
    return found


def _meta_home(root: Path, home: Path, item_id: str) -> list[tuple[str, str, str]]:
    """A meta home is muse/settings.json: Unrestricted posture and hx hooks."""
    label = f"home:{item_id}"
    found: list[tuple[str, str, str]] = []
    settings_path = home / "muse" / "settings.json"
    try:
        settings = json.loads(settings_path.read_text())
    except (OSError, json.JSONDecodeError):
        settings = None
    if not isinstance(settings, dict):
        found.append((FAIL, label, "muse/settings.json is missing or not valid JSON"))
        return found
    permissions = settings.get("permissions") if isinstance(settings.get("permissions"), dict) else {}
    if permissions.get("default_profile") == ":unrestricted":
        found.append((OK, label, "default_profile :unrestricted"))
    else:
        found.append((FAIL, label, "muse/settings.json `permissions.default_profile` is not `:unrestricted`"))
    hooks = settings.get("hooks") if isinstance(settings.get("hooks"), dict) else {}
    if hooks and all(
        isinstance(hooks.get(event), list) and hooks[event]
        for event in ("SessionStart", "PostToolUse", "Stop")
    ):
        found.append((OK, label, "hx hooks wired"))
    else:
        found.append((FAIL, label, "muse/settings.json hooks are missing SessionStart/PostToolUse/Stop"))
    return found


def _meta_instance(root: Path) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    token = root / "seed" / "meta-token"
    if not token.is_file():
        found.append((
            FAIL, "meta-token",
            "seed/meta-token absent; a meta worker authenticates from that file, mode 0600",
        ))
    elif token.stat().st_mode & 0o77:
        found.append((FAIL, "meta-token", "seed/meta-token is readable by group or other; it must be mode 0600"))
    elif not token.read_text().strip():
        found.append((FAIL, "meta-token", "seed/meta-token is empty"))
    else:
        found.append((OK, "meta-token", "seed/meta-token present, mode 0600"))

    recorded = root / "config" / "meta.json"
    if not recorded.is_file():
        found.append((WARN, "meta", "config/meta.json absent; record the muse binary's path as `bin`"))
    else:
        try:
            body = json.loads(recorded.read_text())
        except json.JSONDecodeError as exc:
            found.append((FAIL, "meta", f"config/meta.json is not valid JSON: {exc}"))
        else:
            binary = body.get("bin") if isinstance(body, dict) else None
            if binary and os.access(binary, os.X_OK):
                found.append((OK, "meta", binary))
            else:
                found.append((FAIL, "meta", f"config/meta.json bin {binary!r} is missing or not executable"))
    return found


def _codex_home(root: Path, home: Path, item_id: str) -> list[tuple[str, str, str]]:
    """A Codex home is config.toml: unattended posture and hx hooks."""
    label = f"home:{item_id}"
    found: list[tuple[str, str, str]] = []
    config_path = home / "config.toml"
    try:
        config = tomllib.loads(config_path.read_text())
    except (OSError, ValueError):
        config = None
    if not isinstance(config, dict):
        found.append((FAIL, label, "config.toml is missing or not valid TOML"))
        return found
    if config.get("approval_policy") == "never":
        found.append((OK, label, "approval_policy never"))
    else:
        found.append((FAIL, label, "config.toml `approval_policy` is not `never`"))
    if config.get("sandbox_mode") == "danger-full-access":
        found.append((OK, label, "sandbox_mode danger-full-access"))
    else:
        found.append((FAIL, label, "config.toml `sandbox_mode` is not `danger-full-access`"))
    hooks = config.get("hooks") if isinstance(config.get("hooks"), dict) else {}
    if hooks and all(
        isinstance(hooks.get(event), list) and hooks[event] for event in ("SessionStart", "PostToolUse", "Stop")
    ):
        found.append((OK, label, "hx hooks wired"))
    else:
        found.append((FAIL, label, "config.toml hooks are missing SessionStart/PostToolUse/Stop"))
    try:  # The web search route (docs/codex-search-guard.md §4.6); _codex_instance judges the JSON.
        guard = json.loads((root / "config" / "codex.json").read_text()).get("search_guard")
    except (OSError, ValueError, AttributeError):
        guard = None
    if isinstance(guard, dict):
        servers = config.get("mcp_servers") if isinstance(config.get("mcp_servers"), dict) else {}
        foundry = servers.get("foundry") if isinstance(servers.get("foundry"), dict) else {}
        matched = all(any(isinstance(group, dict) and group.get("matcher") == "mcp__foundry__web_search"
                          for group in hooks.get(event) or []) for event in ("PreToolUse", "PostToolUse"))
        if config.get("web_search") == "disabled" and foundry.get("url") == guard.get("toolbox_url") and matched:
            found.append((OK, label, "web search guarded (foundry toolbox)"))
        else:
            found.append((FAIL, label, "config.toml does not route web search through the guarded foundry "
                          "toolbox that config/codex.json `search_guard` names; `hx launch` rewrites it"))
    elif config.get("web_search") == "cached":
        found.append((OK, label, "web search cached (hosted search, unguarded)"))
    else:
        found.append((FAIL, label, "config.toml `web_search` is not `cached`; `hx launch` rewrites it"))
    return found


def _codex_instance(root: Path, ids: list[str]) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    token = root / "seed" / "codex-token"
    if not token.is_file():
        homes = [root / "run" / item_id / "home" / "auth.json" for item_id in ids]
        if ids and all(path.is_file() for path in homes):
            found.append((
                OK, "codex-token",
                "seed/codex-token absent; every Codex home carries the ChatGPT login",
            ))
        else:
            found.append((
                FAIL, "codex-token",
                "seed/codex-token absent; a Codex worker authenticates from that file, mode 0600",
            ))
    elif token.stat().st_mode & 0o77:
        found.append((FAIL, "codex-token", "seed/codex-token is readable by group or other; it must be mode 0600"))
    elif not token.read_text().strip():
        found.append((FAIL, "codex-token", "seed/codex-token is empty"))
    else:
        found.append((OK, "codex-token", "seed/codex-token present, mode 0600"))

    recorded = root / "config" / "codex.json"
    if not recorded.is_file():
        found.append((WARN, "codex", "config/codex.json absent; record the codex binary's path as `bin`"))
    else:
        try:
            body = json.loads(recorded.read_text())
        except json.JSONDecodeError as exc:
            found.append((FAIL, "codex", f"config/codex.json is not valid JSON: {exc}"))
        else:
            binary = body.get("bin") if isinstance(body, dict) else None
            if binary and os.access(binary, os.X_OK):
                found.append((OK, "codex", binary))
            else:
                found.append((FAIL, "codex", f"config/codex.json bin {binary!r} is missing or not executable"))
            guard = body.get("search_guard") if isinstance(body, dict) else None
            missing = [key for key in ("toolbox_url", "content_safety_endpoint") if guard is not None
                       and not (isinstance(guard, dict) and isinstance(guard.get(key), str) and guard[key].strip())]
            if missing:
                found.append((FAIL, "codex", f"config/codex.json `search_guard` lacks {' and '.join(missing)}; "
                              "install.sh refuses it"))
    return found
