#!/usr/bin/env python3
"""adapters/codex/toolbox_headers.py — the Foundry toolbox's `http_headers_helper`.

Usage: toolbox_headers.py (no arguments). Design: docs/codex-search-guard.md §4.5.

Codex runs `mcp_servers.foundry.http_headers_helper` and sends the JSON object it
prints as HTTP headers to the toolbox's MCP endpoint; it caches them and runs the
helper again when the endpoint rejects them, so a short-lived Entra token is fine.
The token is `FOUNDRY_TOOLBOX_TOKEN` from the process environment, else
`az account get-access-token --resource https://ai.azure.com` (the documented
scope for Foundry project endpoints), bounded at 8 s under Codex's 10 s. Any
failure is one stderr line, exit 1 and no stdout. The token is printed only as
the header value Codex asked for, never logged.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

RESOURCE = "https://ai.azure.com"


def token() -> str:
    explicit = os.environ.get("FOUNDRY_TOOLBOX_TOKEN")
    if explicit:
        return explicit
    done = subprocess.run(["az", "account", "get-access-token", "--resource", RESOURCE,
                           "--query", "accessToken", "-o", "tsv"],
                          capture_output=True, text=True, timeout=8, check=False)
    if done.returncode != 0 or not done.stdout.strip():
        reason = " ".join(done.stderr.split())[:300] or f"exit {done.returncode}, empty output"
        raise RuntimeError("az returned no token: " + reason)
    return done.stdout.strip()


def main() -> int:
    try:
        value = token()
    except (OSError, subprocess.TimeoutExpired, RuntimeError) as exc:
        sys.stderr.write(f"toolbox_headers: {' '.join(str(exc).split())}\n")
        return 1
    sys.stdout.write(json.dumps({"Authorization": "Bearer " + value}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
