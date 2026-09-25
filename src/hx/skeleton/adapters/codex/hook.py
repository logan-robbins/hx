#!/usr/bin/env python3
"""adapters/codex/hook.py — Codex hook events into `hx-hook` calls.

Usage: hook.py --id <id> --hook-bin <command> --root <root> <event>
(stdin: the hook JSON).

Codex's wire is already Claude-shaped (verified against the official hooks
reference: `hook_event_name`, `session_id`, `source`, `cwd`, plus Codex
extensions like `model`, `permission_mode`, and turn-scoped `turn_id`), so
this is mostly a pass-through with a normalizer for the keys hx-hook reads:
camelCase variants map to snake_case, and `tool_response` is filled from
`toolResult` when only the latter is present. Unknown keys pass through;
hx-hook ignores what it does not read. Two deliberate gaps, not oversights:

- context tokens: hook payloads carry no usage counts, so `context_tokens`
  stays absent and the seam threshold does not fire for codex rows.
- persona: the CLI surface has no verifiable system-prompt injection, so
  the persona file is derived for inspection and identity arrives through the
  context file and the pasted goal pointer.

A `guard` denial (exit 2, reason on stderr) is additionally translated into
Codex's `permissionDecision` deny JSON on stdout: exit 2 alone is a documented
deny, but the JSON form is what every version honors, and stdout JSON is
consumed rather than shown.

Hook commands run with the session cwd as their working directory, so
everything the hook needs (--hook-bin, --root) is baked into the command line
at install time; nothing is read from the environment.
"""

from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys

RENAMES = {
    "toolName": "tool_name",
    "toolUseId": "tool_use_id",
    "toolInput": "tool_input",
    "toolResult": "tool_result",
    "sessionId": "session_id",
}


def translate(body: dict) -> dict:
    out = dict(body)
    for src, dst in RENAMES.items():
        if src in body and dst not in out:
            out[dst] = body[src]
    if "tool_response" not in out and "tool_result" in out:
        out["tool_response"] = out["tool_result"]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True)
    parser.add_argument("--hook-bin", required=True)
    parser.add_argument("--root", required=True)
    parser.add_argument("event")
    args = parser.parse_args(argv)

    try:
        body = json.load(sys.stdin)
    except json.JSONDecodeError:
        body = {}
    if not isinstance(body, dict):
        body = {}

    payload = json.dumps(translate(body)).encode()
    # --hook-bin can be a bare path or a command with arguments.
    try:
        proc = subprocess.run(
            [*shlex.split(args.hook_bin), "--id", args.id, "--root", args.root, args.event],
            input=payload,
            capture_output=True,
        )
    except OSError as exc:
        print(f"hook.py: cannot run {args.hook_bin}: {exc}", file=sys.stderr)
        return 1
    if proc.stdout:
        sys.stdout.buffer.write(proc.stdout)
    if proc.stderr:
        sys.stderr.buffer.write(proc.stderr)
    if args.event == "guard" and proc.returncode == 2:
        reason = (proc.stderr or b"").decode("utf-8", "replace").strip()
        sys.stdout.write(json.dumps({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason or "denied by hx guard",
        }}) + "\n")
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
