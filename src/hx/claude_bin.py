"""The recorded Claude Code binary and the versions the live suite has passed on (spec 17.1,
17.2 step 1, 17.6).

`config/claude.json` is `{"bin": "<abs path>", "version": "2.1.278"}`, the version bare — the
output of `claude --version` with the ` (Claude Code)` suffix stripped (CONTRACTS.md). The
version is a record, not a gate: `hx install` pins whatever `claude` it finds, and every launch
boundary (`hx launch`, `hx restart`, `hx up`) re-records the version the binary reports now, so
a fleet runs the latest release from its next launch and never changes mid-session
(`DISABLE_AUTOUPDATER=1` in `start.sh`). The package still ships the list of versions its live
suite last passed on; `hx doctor` says `tested` or `untested` against it, and nothing refuses.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from . import store
from .errors import HxError, NotFound

CONFIG = "config/claude.json"
VERSION_SUFFIX = " (Claude Code)"
TESTED_FILE = "tested-claude-versions.json"


def packaging_dir() -> Path:
    """The package's own `packaging/`, shipped as package data (handoff/gtm-to-build.md)."""
    return Path(__file__).resolve().parent / "packaging"


def tested_versions() -> list[str]:
    path = packaging_dir() / TESTED_FILE
    if not path.is_file():
        raise NotFound(f"{path}: the package ships no tested-version list; the install is broken")
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise HxError(f"{path}: not valid JSON: {exc}") from exc
    versions = data.get("versions") if isinstance(data, dict) else None
    if not isinstance(versions, list) or not all(isinstance(v, str) for v in versions):
        raise HxError(f"{path}: must be {{\"versions\": [\"2.1.278\", …]}}")
    return versions


def bare_version(output: str) -> str:
    """`2.1.278 (Claude Code)` → `2.1.278` (CONTRACTS.md "Claude Code version strings")."""
    return output.strip().splitlines()[0].removesuffix(VERSION_SUFFIX).strip() if output.strip() else ""


def probe(binary: str, env=None) -> str:
    result = subprocess.run([binary, "--version"], capture_output=True, text=True, check=False, env=env)
    if result.returncode != 0:
        raise HxError(f"{binary} --version failed: {result.stdout}{result.stderr}".rstrip())
    version = bare_version(result.stdout or result.stderr)
    if not version:
        raise HxError(f"{binary} --version printed nothing recognisable")
    return version


def find_binary(explicit: str | None = None) -> str:
    """The `claude` binary: `--claude` when given, else the one on PATH.

    The path is made absolute but symlinks are *not* resolved. The native installer's
    `~/.local/bin/claude` is a symlink into `~/.local/share/claude/versions/<v>`, and its
    auto-update repoints the link and deletes old versions: a resolved path pins a file that
    will not be there next week, and every launch then fails. The launcher is the stable
    name; the version is recorded beside it.
    """
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise NotFound(f"{explicit}: no such file")
        return str(path.absolute())
    found = shutil.which("claude")
    if not found:
        raise NotFound(
            "claude is not on PATH; install Claude Code, or pass `--claude <path>` (spec 17.2 step 1)"
        )
    return str(Path(found).absolute())


def tested_status(version: str) -> dict:
    """`{"tested": bool, "newest_tested": str | None}` against the shipped live-verified list.

    Informational only. The gate that refused an unlisted version is gone: a fleet runs the
    latest release, picked up at launch boundaries, and the live suite is what gets run
    against a new one. `hx doctor` and `hx install` print this; nothing acts on it.
    """
    versions = tested_versions()
    return {"tested": version in versions, "newest_tested": versions[0] if versions else None}


def status_text(version: str) -> str:
    """`tested`, or `untested; the live suite last passed on 2.1.296`."""
    status = tested_status(version)
    if status["tested"]:
        return "tested"
    newest = status["newest_tested"]
    return f"untested; the live suite last passed on {newest}" if newest else "untested"


def describe(version: str) -> str:
    """`2.1.296 (tested)`, or `2.1.300 (untested; the live suite last passed on 2.1.296)`."""
    return f"{version} ({status_text(version)})"


def refresh_pin(root: Path, env=None) -> dict | None:
    """Re-record the version the pinned binary reports now; run at every launch boundary.

    Returns the pin, with `previous` added when the version moved, or `None` when there is no
    pin or the binary cannot be probed — the launcher's own refusal then says why, with the
    path, and a launch never fails over a version record.
    """
    pin = load_pin(root)
    if not pin or not isinstance(pin.get("bin"), str) or not Path(pin["bin"]).is_file():
        return None
    try:
        version = probe(pin["bin"], env=env)
    except HxError:
        return None
    if version == pin.get("version"):
        return pin
    previous = pin.get("version")
    return write_pin(root, pin["bin"], version) | {"previous": previous}


def load_pin(root: Path) -> dict | None:
    path = root / CONFIG
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as exc:
        raise HxError(f"{CONFIG}: not valid JSON: {exc}") from exc
    return data if isinstance(data, dict) else None


def write_pin(root: Path, binary: str, version: str) -> dict:
    pin = {"bin": binary, "version": version}
    store.atomic_write_json(root / CONFIG, pin)
    return pin
