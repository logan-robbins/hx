"""The `log` hook: `PostToolUse`, every tool, every agent (spec 09.1, 07.1).

One raw record per tool call, appended to the stream of the thread that made it: the main
stream, or the subagent's `sNNN` looked up from `run/<id>/subagents.json` by the payload's
`agent_id`. The raw stream exists for one reader, the Companion; the HarnessAgent never sees
it (spec 07.1).

It is also where the hard seam trigger lives: when `context_tokens` on the **main** stream
reaches the model's threshold from `config/models.json`, the hook touches `run/<id>/seam` and
the next `stop` takes the seam (spec 02 Seams, 09.3). Subagent streams never trigger one —
a subagent's window is its own.
"""

from __future__ import annotations

from pathlib import Path

from . import streams, transcripts
from .config_harness import load_harness
from .config_models import load_models
from .subagents import handle_for

SEAM_MARKER = "seam"


def seam_marker(root: Path, item_id: str) -> Path:
    return root / "run" / item_id / SEAM_MARKER


def threshold_for(root: Path, item_id: str) -> int | None:
    """The seam threshold of this agent's model, or None when it cannot be worked out."""
    harness = root / "config" / item_id / "harness.json"
    models_path = root / "config" / "models.json"
    if not harness.is_file() or not models_path.is_file():
        return None
    try:
        model = load_harness(harness, check_cross_file=False).model
        models = load_models(models_path)
    except Exception:
        return None
    row = models.get(model)
    return row.threshold if row else None


def handle(payload: dict, item_id: str, root: Path, *, env=None) -> tuple[int, str]:
    tool_input = payload.get("tool_input")
    tool_response = payload.get("tool_response")
    agent_id = payload.get("agent_id")
    transcript = payload.get("transcript_path")

    stream, is_main = handle_for(root, item_id, agent_id)
    # Pi's extension passes the count it already summed. Claude leaves it out and the
    # transcript parse below is how that session's usage is read.
    supplied = payload.get("context_tokens")
    if isinstance(supplied, int) and not isinstance(supplied, bool):
        tokens = supplied
    else:
        tokens = transcripts.context_tokens(transcript)

    record = {
        "event": "post_tool",
        "tool": payload.get("tool_name"),
        # Head excerpts, with `ref` pointing at the full payload in the transcript: nothing is
        # lost, and the Companion follows the ref when an excerpt is not enough (spec 07.1).
        "input": streams.excerpt(tool_input),
        "output": streams.excerpt(tool_response),
        "context_tokens": tokens,
        "ref": {"transcript": transcript, "tool_use_id": payload.get("tool_use_id")},
    }
    if isinstance(tool_response, dict):
        for key in ("exit_code", "exitCode", "status"):
            if key in tool_response:
                record["exit"] = tool_response[key]
                break
    if agent_id:
        record["agent_id"] = agent_id

    streams.append_record(root, item_id, stream, record)

    # Spec 10 wake trigger: `batch_records` new records in any stream.
    from . import companion as companion_mod

    companion_mod.wake_due(root, item_id, env=env)

    # The hard trigger: it does not wait for a step to close (spec 05, 02 Seams). The
    # Partner takes it like any agent; its seam rehydrates from the context file and no
    # goal pointer is involved.
    if is_main and tokens is not None:
        threshold = threshold_for(root, item_id)
        if threshold is not None and tokens >= threshold:
            marker = seam_marker(root, item_id)
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
    return 0, ""
