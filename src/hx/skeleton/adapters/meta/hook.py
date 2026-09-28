#!/usr/bin/env python3
"""adapters/meta/hook.py — Muse Code hook events into `hx-hook` calls.

Usage: hook.py --id <id> --hook-bin <command> --root <root> <event>
(stdin: the hook JSON).

Muse Code's user hooks are Claude-shaped (verified live against 1.3.0 for
SessionStart: `hook_event_name`, `session_id`, `source`, `cwd`), so this is
mostly a pass-through with a normalizer for the keys hx-hook reads: camelCase
variants map to snake_case, and `tool_response` is filled from `toolResult`
when only the latter is present. Unknown keys pass through; hx-hook ignores
what it does not read. Two deliberate gaps, not oversights:

- context tokens: hook payloads carry no usage counts, so `context_tokens`
  stays absent and the seam threshold does not fire for meta rows.
- persona: the 1.3.0 CLI surface has no verifiable system-prompt injection, so
  the persona file is derived for inspection and identity arrives through the
  context file and the pasted goal pointer.

Hook commands run through the shell with a cleared environment, so everything
the hook needs (--hook-bin, --root) is baked into the command line at install
time; nothing is read from the environment.
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

    payload = json.dumps(translate(body)).encode()
    # --hook-bin can be a bare path or a command with arguments.
    try:
        proc = subprocess.run(
            [*shlex.split(args.hook_bin), "--id", args.id, "--root", args.root, *extra, args.event],
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
    return proc.returncode


if __name__ == "__main__":
    sys.exit(main())
