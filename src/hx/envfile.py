"""Instance auth configuration and safe loading of a supplied dotenv file."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

from .errors import HxError
from .store import atomic_write_json

AUTH_CONFIG = "config/auth.json"
_KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_SEED_KEYS = {
    "codex-token": ("OPENAI_API_KEY",),
    "grok-token": ("XAI_API_KEY", "GROK_API_KEY"),
    "meta-token": ("META_API_KEY",),
    "pi-token": ("PI_API_KEY",),
}


def parse(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise HxError(f"env file {path} does not exist")
    values: dict[str, str] = {}
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise HxError(f"env file {path}:{number}: expected KEY=VALUE")
        key, value = line.split("=", 1)
        key, value = key.strip(), value.strip()
        if not _KEY.fullmatch(key):
            raise HxError(f"env file {path}:{number}: invalid key name")
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key] = value
    return values


def configure(root: Path, env_file: str) -> None:
    path = Path(env_file).expanduser().resolve()
    parse(path)
    atomic_write_json(root / AUTH_CONFIG, {"mode": "auto", "env_file": str(path)})


def settings(root: Path) -> dict:
    path = root / AUTH_CONFIG
    if not path.is_file():
        return {"mode": "auto"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HxError(f"{path}: invalid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("mode") != "auto":
        raise HxError(f"{path}: mode must be auto")
    return data


def sync_seed(root: Path) -> None:
    config = settings(root)
    path = config.get("env_file")
    if not path:
        return
    values = parse(Path(path))
    mapping = dict(_SEED_KEYS)
    mapping["token"] = ("ANTHROPIC_API_KEY", "ANTRHOPIC_API_KEY")
    seed = root / "seed"
    seed.mkdir(parents=True, exist_ok=True)
    for filename, names in mapping.items():
        target = seed / filename
        if target.is_file() and target.read_text(encoding="utf-8").strip():
            continue  # a local credential always wins over the env fallback
        value = next((values[name] for name in names if values.get(name)), None)
        if not value:
            if filename == "token":
                raise HxError(f"env file {path} needs ANTHROPIC_API_KEY when no OAuth token is present")
            continue
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(value + "\n")
        target.chmod(0o600)


def main() -> None:
    """Emit NUL-delimited assignments for the private adapter shell loader."""
    values = parse(Path(sys.argv[1]))
    values.setdefault("ANTHROPIC_API_KEY", values.get("ANTRHOPIC_API_KEY", ""))
    values.setdefault("XAI_API_KEY", values.get("GROK_API_KEY", ""))
    for key, value in values.items():
        if value:
            sys.stdout.buffer.write(f"{key}={value}".encode() + b"\0")


if __name__ == "__main__":
    main()
