"""The three subagent hooks (spec 09.1, 07.3, 09.4).

`SubagentStart` is the only boundary a subagent gets: hx assigns its handle, opens its stream,
composes its context file, and returns the path as JSON `additionalContext` — **JSON is the
only accepted form for this event**; plain stdout is not injected (verified against
code.claude.com/docs/en/hooks#subagentstart, 2026-09-20).

`SubagentStop` closes the stream. `PostToolUse(Agent)` is the only path by which anything
reaches the parent, so the closed-stream digest is handed back there.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import compose as compose_mod, store, streams, subagents
from .hook_context import CONTEXT_LINE

#: Until the Companion writes real digests (M5), a closed stream still gets a file, so the
#: path the parent is handed always exists.
DIGEST_PLACEHOLDER = "_pending companion_\n"


def digest_path(root: Path, item_id: str, stream: str) -> Path:
    return root / "state" / item_id / f"{stream}.digest.md"


def prompts_path(root: Path, item_id: str) -> Path:
    return root / "run" / item_id / "subagent-prompts.json"


def record_prompt(root: Path, item_id: str, agent_id: str, prompt: str) -> None:
    """Keep the spawn prompt by `agentId`, so a recompose can use it (build-6 live finding)."""
    path = prompts_path(root, item_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = {}
    if path.is_file():
        try:
            loaded = json.loads(path.read_text())
            existing = loaded if isinstance(loaded, dict) else {}
        except (json.JSONDecodeError, OSError):
            existing = {}
    existing[agent_id] = prompt
    store.atomic_write_json(path, existing)


def known_prompt(root: Path, item_id: str, agent_id: str) -> str:
    path = prompts_path(root, item_id)
    if not path.is_file():
        return ""
    try:
        loaded = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return ""
    return str(loaded.get(agent_id, "")) if isinstance(loaded, dict) else ""


def _spawn_prompt(payload: dict) -> str:
    """What the parent asked for, if the payload carries it.

    It usually does not: `SubagentStart`'s documented fields are `session_id`,
    `hook_event_name`, `agent_id`, `agent_type`, `cwd` and `permission_mode`, and a live run
    confirmed nothing else arrives. The fields below are checked anyway, because a future
    version that adds the prompt should start using it without a change here.
    """
    tool_input = payload.get("tool_input")
    if isinstance(tool_input, dict):
        for key in ("prompt", "description", "task"):
            value = tool_input.get(key)
            if isinstance(value, str) and value.strip():
                return value
    for key in ("prompt", "description"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def start(payload: dict, item_id: str, root: Path, *, env=None) -> tuple[int, str]:
    agent_id = str(payload.get("agent_id") or "")
    if not agent_id:
        raise ValueError("SubagentStart payload has no agent_id")

    handle = subagents.assign(root, item_id, agent_id)
    stream = subagents.stream_for(item_id, handle)

    open_path = streams.log_dir(root, item_id) / f"{stream}-open.jsonl"
    open_path.parent.mkdir(parents=True, exist_ok=True)
    open_path.touch()

    prompt = _spawn_prompt(payload) or known_prompt(root, item_id, agent_id)
    streams.append_record(root, item_id, stream, {
        "event": "open",
        "agent_id": agent_id,
        "agent_type": payload.get("agent_type"),
        "input": streams.excerpt(prompt),
        "ref": {"transcript": payload.get("transcript_path")},
    })
    streams.append_record(root, item_id, f"{item_id}-main", {
        "event": "spawned",
        "handle": handle,
        "agent_id": agent_id,
        "agent_type": payload.get("agent_type"),
    })

    # The subagent's own context file: SUBAGENTS.md as memory, its prompt as the task (07.3).
    path = compose_mod.compose(root, item_id, stream, subagent_prompt=prompt, env=env)

    # JSON, because plain stdout is not injected for this event.
    return 0, json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "SubagentStart",
            "additionalContext": CONTEXT_LINE.format(path=path),
        }
    })


def stop(payload: dict, item_id: str, root: Path, *, env=None) -> tuple[int, str]:
    agent_id = str(payload.get("agent_id") or "")
    handle = subagents.load(root, item_id).get(agent_id)
    if not handle:
        # Nothing to close: a subagent hx never saw start. Recorded, not raised.
        streams.append_record(root, item_id, f"{item_id}-main", {
            "event": "closed",
            "handle": None,
            "agent_id": agent_id,
            "note": "no handle for this agent_id; it started before hx was watching",
        })
        return 0, ""

    stream = subagents.stream_for(item_id, handle)
    streams.append_record(root, item_id, stream, {
        "event": "close",
        "agent_id": agent_id,
        "output": streams.excerpt(payload.get("last_assistant_message") or ""),
        "ref": {"transcript": payload.get("transcript_path")},
    })

    open_path = streams.log_dir(root, item_id) / f"{stream}-open.jsonl"
    closed_path = streams.log_dir(root, item_id) / f"{stream}-closed.jsonl"
    if open_path.is_file():
        open_path.rename(closed_path)

    digest = digest_path(root, item_id, stream)
    if not digest.is_file():
        digest.parent.mkdir(parents=True, exist_ok=True)
        digest.write_text(DIGEST_PLACEHOLDER)

    streams.append_record(root, item_id, f"{item_id}-main", {
        "event": "closed",
        "handle": handle,
        "agent_id": agent_id,
        "digest": str(digest),
    })

    # Spec 10 wake trigger: a subagent stopped, so its stream is ready for a final pass.
    from . import companion as companion_mod

    companion_mod.wake_due(root, item_id, force=True, env=env)
    return 0, ""


def result(payload: dict, item_id: str, root: Path, *, env=None) -> tuple[int, str]:
    """`PostToolUse(Agent)` (spec 09.1).

    Two live findings shape this (build-6). The Agent tool is **asynchronous**: this hook
    fires when the subagent is *launched*, with `tool_response.status == "async_launched"`,
    not when it finishes — so there is no digest to hand back yet, and spec 09.1's
    "the only path that reaches the parent" does not hold as written.

    What the payload does carry is the spawn prompt, keyed by the same `agentId` that
    `SubagentStart` reports. That is the pairing build-5 could not find, so hx records it
    here and the subagent's context file uses it.
    """
    response = payload.get("tool_response")
    response = response if isinstance(response, dict) else {}
    status = response.get("status")
    agent_id = str(response.get("agentId") or payload.get("agent_id") or "")

    prompt = response.get("prompt")
    if agent_id and isinstance(prompt, str) and prompt.strip():
        record_prompt(root, item_id, agent_id, prompt)
        # The context file was composed at SubagentStart, before the prompt was knowable.
        handle = subagents.load(root, item_id).get(agent_id)
        if handle:
            compose_mod.compose(
                root, item_id, subagents.stream_for(item_id, handle),
                subagent_prompt=prompt, env=env,
            )

    if status not in (None, "completed"):
        return 0, ""

    handle = subagents.load(root, item_id).get(agent_id)
    if not handle:
        return 0, ""

    stream = subagents.stream_for(item_id, handle)
    digest = digest_path(root, item_id, stream)
    streams.append_record(root, item_id, f"{item_id}-main", {
        "event": "subagent_result",
        "handle": handle,
        "agent_id": agent_id,
        "digest": str(digest) if digest.is_file() else None,
        "ref": {"tool_use_id": payload.get("tool_use_id")},
    })
    if not digest.is_file():
        return 0, ""

    return 0, json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolUse",
            "additionalContext": (
                f"{handle} finished. Its digest is at {digest}; "
                f"use the Read tool once on it if you need what it found."
            ),
        }
    })
