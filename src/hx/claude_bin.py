"""The pinned Claude Code binary and its tested-version list (spec 17.1, 17.2 step 1, 17.6).

`config/claude.json` is `{"bin": "<abs path>", "version": "2.1.278"}`, the version bare — the
output of `claude --version` with the ` (Claude Code)` suffix stripped (CONTRACTS.md). The
package ships the list of versions its live suite has passed on, and neither `hx install` nor
`hx upgrade` will pin a version that is not in it.
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


def probe(binary: str) -> str:
    result = subprocess.run([binary, "--version"], capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise HxError(f"{binary} --version failed: {result.stdout}{result.stderr}".rstrip())
    version = bare_version(result.stdout or result.stderr)
    if not version:
        raise HxError(f"{binary} --version printed nothing recognisable")
    return version


def find_binary(explicit: str | None = None) -> str:
    """The `claude` binary: `--claude` when given, else the one on PATH."""
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_file():
            raise NotFound(f"{explicit}: no such file")
        return str(path.resolve())
    found = shutil.which("claude")
    if not found:
        raise NotFound(
            "claude is not on PATH; install Claude Code, or pass `--claude <path>` (spec 17.2 step 1)"
        )
    return str(Path(found).resolve())


def require_tested(version: str) -> None:
    versions = tested_versions()
    if version not in versions:
        newest = versions[0] if versions else "(none)"
        raise HxError(
            f"claude {version} is not in this package's tested list ({', '.join(versions) or 'empty'}). "
            f"Install {newest} and run this again, or upgrade hx to a package that has been "
            f"tested on {version} (spec 17.2 step 1, 17.6)",
            exit_code=5,
        )


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
