"""Build a real instance with real stream records, by running the real hooks.

ui-6 asks for fixtures regenerated from real records rather than hand-written
ones. Nothing here invents a record: every line under `logs/<id>/` is written by
`python -m hx.hooks`, the same entry point `install.sh` bakes into an agent's
settings, driven with the payload shapes `handoff/build-to-ui.md` (build-5)
documents. What this module supplies is only the instance to write into and the
Claude-Code-shaped payloads to feed it.

It deliberately does not import from `tests/core/**`: those helpers are the build
lane's, and coupling the ui suite to them would make a rename there a failure
here. The subprocess contract (`--id <id> <event>`, JSON on stdin) is the part
that is public.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

#: Spec 09.1's events, in the order a real dispatch produces them.
MAIN_STREAM = "{id}-main"


def hook_env(root: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in ("HARNESS_ID", "HARNESS_ROOT")}
    env["HARNESS_ROOT"] = str(root)
    env["PYTHONPATH"] = str(REPO / "src")
    env.update(extra)
    return env


def run_hook(root: Path, item_id: str, event: str, payload: dict) -> subprocess.CompletedProcess:
    """One real hook invocation. Raises if hx rejected the payload."""
    result = subprocess.run(
        [sys.executable, "-m", "hx.hooks", "--id", item_id, event],
        input=json.dumps(payload),
        env=hook_env(root),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, f"{event} for {item_id}: {result.stdout}{result.stderr}"
    return result


def transcript(root: Path, name: str, *, input_tokens: int, cache_read: int) -> Path:
    """A Claude Code transcript whose latest assistant record has a `usage` block.

    `context_tokens` is read from this by the `log` hook: input plus cache reads,
    not output (build-5).
    """
    path = root / "run" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "go on"}}) + "\n"
        + json.dumps({
            "type": "assistant",
            "message": {
                "role": "assistant",
                "usage": {
                    "input_tokens": input_tokens,
                    "cache_read_input_tokens": cache_read,
                    "cache_creation_input_tokens": 0,
                    "output_tokens": 640,
                },
            },
        }) + "\n",
        encoding="utf-8",
    )
    return path


def post_tool(
    tool: str,
    tool_input: dict,
    tool_response: dict,
    *,
    transcript_path: Path | None = None,
    agent_id: str | None = None,
    tool_use_id: str = "toolu_01ABC",
) -> dict:
    payload = {
        "session_id": "sess-ui6",
        "hook_event_name": "PostToolUse",
        "cwd": "/work",
        "tool_name": tool,
        "tool_input": tool_input,
        "tool_response": tool_response,
        "tool_use_id": tool_use_id,
    }
    if transcript_path is not None:
        payload["transcript_path"] = str(transcript_path)
    if agent_id is not None:
        payload["agent_id"] = agent_id
        payload["agent_type"] = "general-purpose"
    return payload


def drive_main_thread(root: Path, item_id: str) -> None:
    """A session start, a few tool calls, and the turn marker."""
    run_hook(root, item_id, "context", {
        "session_id": "sess-ui6", "hook_event_name": "SessionStart", "source": "startup",
    })

    small = transcript(root, f"{item_id}/t-early.jsonl", input_tokens=9_000, cache_read=31_000)
    big = transcript(root, f"{item_id}/t-late.jsonl", input_tokens=12_400, cache_read=48_900)

    run_hook(root, item_id, "log", post_tool(
        "Read", {"file_path": "spec/08-hx-cli.md"},
        {"content": "## 8. `hx` CLI\nZero-dependency Python…"},
        transcript_path=small, tool_use_id="toolu_read_08",
    ))
    run_hook(root, item_id, "log", post_tool(
        "Bash", {"command": "python3 -m unittest discover -s tests -q"},
        {"stdout": "....\n", "stderr": "", "exit_code": 0},
        transcript_path=small, tool_use_id="toolu_bash_tests",
    ))
    run_hook(root, item_id, "log", post_tool(
        "Edit", {"file_path": "greet.py", "old_string": "def greet", "new_string": "def greet"},
        {"stdout": "", "exit_code": 1, "stderr": "no match"},
        transcript_path=big, tool_use_id="toolu_edit_greet",
    ))


def drive_subagent(root: Path, item_id: str, agent_id: str, *, close: bool) -> None:
    """Start a subagent, have it use a tool, and optionally stop it."""
    run_hook(root, item_id, "subagent-start", {
        "session_id": "sess-ui6", "hook_event_name": "SubagentStart",
        "agent_id": agent_id, "agent_type": "general-purpose",
        "prompt": f"survey the exit-code assertions for {agent_id}",
    })
    tape = transcript(root, f"{item_id}/t-{agent_id}.jsonl", input_tokens=4_100, cache_read=6_200)
    run_hook(root, item_id, "log", post_tool(
        "Grep", {"pattern": "returncode =="},
        {"stdout": "tests/test_board.py:41\n"},
        transcript_path=tape, agent_id=agent_id, tool_use_id=f"toolu_grep_{agent_id}",
    ))
    if not close:
        return
    run_hook(root, item_id, "subagent-stop", {
        "session_id": "sess-ui6", "hook_event_name": "SubagentStop",
        "agent_id": agent_id, "agent_type": "general-purpose",
        "last_assistant_message": f"{agent_id}: found two exit-code assertions; both already cover the flag.",
    })
    run_hook(root, item_id, "subagent-result", {
        "session_id": "sess-ui6", "hook_event_name": "PostToolUse",
        "tool_name": "Agent", "tool_input": {"prompt": "survey"},
        "tool_response": {"agentId": agent_id, "status": "completed"},
    })


def finish_turn(root: Path, item_id: str) -> None:
    run_hook(root, item_id, "stop", {
        "session_id": "sess-ui6", "hook_event_name": "Stop",
        "background_tasks": [],
    })


def drive(root: Path, item_id: str, *, subagents: int = 2, leave_open: int = 0) -> Path:
    """A whole M4-shaped run: main thread, `subagents` of them, then the turn.

    `leave_open` of them are started and never stopped, so the instance has both
    `-open` and `-closed` streams.
    """
    drive_main_thread(root, item_id)
    for n in range(subagents):
        drive_subagent(root, item_id, f"claude-agent-{item_id}-{n:02d}", close=n >= leave_open)
    finish_turn(root, item_id)
    return root


# -- the Companion (build-6) ---------------------------------------------

def companion_pass(root: Path, item_id: str, stream: str, state: dict) -> dict:
    """Install a step state the way the Companion's own `stop` hook does.

    `hx.companion.ingest` is the real acceptance path: it validates against the
    07.2 schema, evicts under `state_budget_tokens`, stamps `seq`,
    `prompt_version` and `ts`, and writes `state/<id>/<stream>.json`. So the
    resulting file is one hx accepted, not one this module composed — the only
    simulated part is the Companion's prose, which needs a live model.
    """
    from hx import companion

    # `seq` is required on the wire — a Companion that forgot it would make hx
    # re-feed the same records forever — but hx then stamps
    # `max(what was sent, the log head)`, so the value here does not matter.
    payload = {"seq": 0, **state}

    out = companion.out_path(root, item_id, stream)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload), encoding="utf-8")

    written = companion.ingest(root, item_id, stream)
    assert written is not None, (
        f"hx rejected the step state for {stream}; "
        f"see {root / 'run' / item_id / 'companion'} for the retry reason"
    )
    return written


def write_digest(root: Path, item_id: str, stream: str, text: str) -> Path:
    """A closed-stream digest, replacing the `_pending companion_` placeholder.

    `hook_subagent` leaves the placeholder at SubagentStop; the Companion's pass
    over the closed stream overwrites this file (spec 10).
    """
    from hx import companion

    path = companion.digest_path(root, item_id, stream)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path
