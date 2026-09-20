"""The boot and heartbeat units (spec 17.2 step 5).

The templates are the gtm lane's, shipped in the package at `hx/packaging/`. Two tokens, and
only two, are substituted, by **literal replacement, never `str.format`**: the files are
plists and INI with shell in their comments, and a brace in a future comment would make
`str.format` raise, while a typo'd token would raise `KeyError` — a visible unsubstituted
token is the better failure (handoff/gtm-to-build.md, gtm-2 entry 2).

hx writes the files and prints the commands. It never enables or loads them: starting a boot
unit is the human's decision, and it is the one step that outlives the install.
"""

from __future__ import annotations

import platform
from pathlib import Path

from . import store
from .claude_bin import packaging_dir
from .errors import NotFound

TOKEN_ROOT = "{HARNESS_ROOT}"
TOKEN_HX_BIN = "{HX_BIN}"

LAUNCHD_UNITS = ("com.hx.up.plist", "com.hx.heartbeat.plist")
SYSTEMD_UNITS = ("hx-up.service", "hx-heartbeat.service", "hx-heartbeat.timer")


def is_macos() -> bool:
    return platform.system() == "Darwin"


def target_dir(home: Path) -> Path:
    return home / "Library" / "LaunchAgents" if is_macos() else home / ".config" / "systemd" / "user"


def source_dir() -> Path:
    return packaging_dir() / ("launchd" if is_macos() else "systemd")


def unit_names() -> tuple[str, ...]:
    return LAUNCHD_UNITS if is_macos() else SYSTEMD_UNITS


def render(template: str, *, root: Path, hx_bin: str) -> str:
    return template.replace(TOKEN_ROOT, str(root)).replace(TOKEN_HX_BIN, hx_bin)


def enable_commands(home: Path) -> list[str]:
    """What the human runs to start them. hx prints these and does nothing else."""
    directory = target_dir(home)
    if is_macos():
        return [f'launchctl bootstrap gui/$(id -u) "{directory / name}"' for name in LAUNCHD_UNITS]
    return [
        "systemctl --user daemon-reload",
        "systemctl --user enable --now hx-up.service hx-heartbeat.timer",
        'loginctl enable-linger "$USER"',
    ]


def install_units(root: Path, hx_bin: str, home: Path) -> list[Path]:
    """Render every unit for this platform into the current HOME. Never enables them."""
    source = source_dir()
    if not source.is_dir():
        raise NotFound(f"{source}: the package ships no unit templates; the install is broken")

    directory = target_dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    written = []
    for name in unit_names():
        template = source / name
        if not template.is_file():
            raise NotFound(f"{template}: missing from the package")
        rendered = render(template.read_text(), root=root, hx_bin=hx_bin)
        left = [token for token in (TOKEN_ROOT, TOKEN_HX_BIN) if token in rendered]
        if left:
            raise NotFound(f"{template}: {', '.join(left)} survived substitution")
        target = directory / name
        store.atomic_write_text(target, rendered)
        written.append(target)
    return written
