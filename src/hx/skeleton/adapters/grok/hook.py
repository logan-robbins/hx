#!/usr/bin/env python3
"""adapters/grok/hook.py — grok hook events into `hx-hook` calls.

Usage: hook.py --id <id> --hook-bin <abs path> <event> (stdin: grok's JSON).

Grok's envelope is camelCase where Claude's is snake_case, so the keys hx-hook
reads are mapped here; everything else passes through untouched (hx-hook
ignores what it does not read). Two deliberate gaps, not oversights:

- context tokens: grok hook payloads carry no usage counts, so `context_tokens`
  stays absent and the seam threshold does not fire for grok rows. Grok
  auto-compacts natively at 85%; `hx seam` still reframes explicitly.
- subagent identity: grok names the child type (`subagentType`), not a stable
  id, so child records fall back to the main stream rather than a wrong one.
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
    "subagentType": "agent_id",
}


def translate(body: dict) -> dict:
    out = dict(body)
    for src, dst in RENAMES.items():
        if src in body and dst not in out:
            out[dst] = body[src]
    # grok also emits this alias itself; hx-hook reads it, so make sure it exists.
    if "tool_response" not in out and "tool_result" in out:
        out["tool_response"] = out["tool_result"]
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", required=True)
    parser.add_argument("--hook-bin", required=True)
    parser.add_argument("--root")
    parser.add_argument("event")
    for field in ("run", "launch", "adapter"):
        parser.add_argument("--continuity-" + field)
    args = parser.parse_args(argv)
    continuity = [getattr(args, "continuity_" + field) for field in ("run", "launch", "adapter")]
    if any(continuity) and not all(continuity):
        parser.error("planned hook identity must be complete")
    extra = [part for field, value in zip(("run", "launch", "adapter"), continuity)
             if value is not None for part in ("--continuity-" + field, value)]

    try:
        raw = sys.stdin.buffer.read(8 * 1024 * 1024 + 1)
        if len(raw) > 8 * 1024 * 1024:
            raise ValueError("oversized hook input")
        body = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        body = {"_hx_capture_error": True}
    if not isinstance(body, dict):
        body = {"_hx_capture_error": True}

    translated = translate(body)
    if any(continuity) and "subagentType" in body and "agent_id" not in body and "agentId" not in body:
        # A type such as 'explore' is shared by concurrent children, not an ID.
        translated.pop("agent_id", None)
        translated["agent_type"] = body["subagentType"]
    payload = json.dumps(translated).encode()
    # --hook-bin can be a bare path or a command with arguments.
    try:
        proc = subprocess.run(
            [*shlex.split(args.hook_bin), "--id", args.id,
             *(["--root", args.root] if args.root else []), *extra, args.event],
            input=payload,
            capture_output=True,
        )
    except OSError as exc:
        print(f"hook.py: cannot run {args.hook_bin}: {exc}", file=sys.stderr)
        return 2 if any(continuity) and args.event in {"request", "tool-start"} else 1
    if proc.stdout:
        sys.stdout.buffer.write(proc.stdout)
    if proc.stderr:
        sys.stderr.buffer.write(proc.stderr)
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
