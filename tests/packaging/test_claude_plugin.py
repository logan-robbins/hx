"""The Claude Code plugin (`/.claude-plugin`, `/commands`) — validated, then pinned.

The plugin is the install path for strangers: marketplace entry, manifest, and slash
commands. Two things go stale here without anyone noticing: a command file quoting an
`hx` subcommand that does not exist, and a manifest whose version drifts from the
package. So this module checks both — every `hx <word>` in every command against
`hx.cli.COMMANDS`, and every version string against `pyproject.toml`.
"""

from __future__ import annotations

import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]
PLUGIN = REPO / ".claude-plugin" / "plugin.json"
MARKETPLACE = REPO / ".claude-plugin" / "marketplace.json"
COMMANDS_DIR = REPO / "commands"

sys.path.insert(0, str(REPO / "src"))

from hx import cli  # noqa: E402


def _version():
    text = (REPO / "pyproject.toml").read_text()
    m = re.search(r'^version\s*=\s*"([^"]+)"', text, re.M)
    assert m, "no version in pyproject.toml"
    return m.group(1)


def test_manifest_is_valid():
    manifest = json.loads(PLUGIN.read_text())
    assert manifest["name"] == "hx"
    assert manifest["description"].strip()
    assert manifest["version"] == _version()


def test_marketplace_lists_the_plugin():
    market = json.loads(MARKETPLACE.read_text())
    assert market["name"].strip()
    entries = [p for p in market["plugins"] if p["name"] == "hx"]
    assert len(entries) == 1, "marketplace must list exactly one hx plugin"
    assert entries[0]["source"] == "./"
    assert entries[0]["version"] == _version()


def test_commands_have_frontmatter_and_real_subcommands():
    files = sorted(COMMANDS_DIR.glob("*.md"))
    assert files, "no plugin commands"
    mentioned = set()
    for path in files:
        text = path.read_text()
        m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        assert m, f"{path.name}: no frontmatter block"
        assert re.search(r"^description:\s*\S", m.group(1), re.M), (
            f"{path.name}: frontmatter needs a description"
        )
        # Backticked `hx <sub>` in prose, plus bare `hx <sub>` at a line start
        # (shell blocks). Prose like "hx reads nothing" matches neither.
        mentioned |= set(re.findall(r"(?:`|^)hx\s+([a-z][a-z-]*)", text, re.M))
    unknown = mentioned - set(cli.COMMANDS)
    assert not unknown, f"commands quote unknown hx subcommands: {sorted(unknown)}"
    assert mentioned, "commands never invoke hx"
