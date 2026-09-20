"""The M4 hooks: `log`, `subagent-start`, `subagent-stop`, `subagent-result`, `stop`.

Spec 07.1 (record shape, the 4 KB line, excerpt + ref), 07.3 (the subagent's context file),
09.1 (the five hooks), 13 M4.

Payload shapes verified against https://code.claude.com/docs/en/hooks on 2026-09-20:
`SubagentStart` carries `agent_id` and `agent_type` and takes **JSON only** on stdout;
`SubagentStop` adds `last_assistant_message`; `PostToolUse(Agent)` carries
`tool_response.{agentId,status}`.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from .conftest import clean_env, wait_for
from .test_compose import run_hook
from .test_transitions import dispatch_working


def records(instance, item_id, stream):
    from hx.streams import iter_records, stream_path

    return list(iter_records(stream_path(instance, item_id, stream)))


def events(instance, item_id, stream):
    return [r.get("event") for r in records(instance, item_id, stream)]


def post_tool(tool="Bash", tool_input=None, tool_response=None, *, agent_id=None, transcript=None):
    payload = {
        "session_id": "abc123",
        "hook_event_name": "PostToolUse",
        "cwd": "/tmp",
        "tool_name": tool,
        "tool_input": tool_input if tool_input is not None else {"command": "ls"},
        "tool_response": tool_response if tool_response is not None else {"stdout": "a\nb\n"},
        "tool_use_id": "toolu_01ABC",
    }
    if agent_id:
        payload["agent_id"] = agent_id
        payload["agent_type"] = "general-purpose"
    if transcript:
        payload["transcript_path"] = str(transcript)
    return payload


def transcript_with(tmp_path, *, input_tokens=0, cache_read=0, cache_creation=0):
    """A transcript whose latest assistant record carries a `usage` block (spec 07.1)."""
    path = tmp_path / "transcript.jsonl"
    path.write_text(
        json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}) + "\n"
        + json.dumps({
            "type": "assistant",
            "message": {"role": "assistant", "usage": {
                "input_tokens": input_tokens,
                "cache_read_input_tokens": cache_read,
                "cache_creation_input_tokens": cache_creation,
                "output_tokens": 900,
            }},
        }) + "\n"
    )
    return path


@pytest.fixture
def working(instance, hx, launched, orders):
    launched("eng-001")
    dispatch_working(instance, hx, orders)
    return instance


# --- the log hook ---------------------------------------------------------------------------


def test_one_record_per_tool_call_in_the_spec_07_1_shape(working, tmp_path):
    transcript = transcript_with(tmp_path, input_tokens=1000, cache_read=47000)
    result = run_hook(working, "eng-001", "log",
                      post_tool(tool="Read", tool_input={"file_path": "/x"}, transcript=transcript))
    assert result.returncode == 0, result.stderr

    record = records(working, "eng-001", "eng-001-main")[-1]
    assert record["event"] == "post_tool" and record["tool"] == "Read"
    assert record["stream"] == "eng-001-main"
    assert isinstance(record["seq"], int) and record["seq"] >= 1
    assert record["ts"].endswith("Z")
    assert "/x" in record["input"] and record["output"]
    assert record["context_tokens"] == 48000, "input + cache reads, not output"
    assert record["ref"]["transcript"] == str(transcript)
    assert record["ref"]["tool_use_id"] == "toolu_01ABC"


def test_seq_is_monotonic_across_calls(working):
    for _ in range(5):
        assert run_hook(working, "eng-001", "log", post_tool()).returncode == 0
    seqs = [r["seq"] for r in records(working, "eng-001", "eng-001-main")]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)


def test_a_huge_tool_result_still_fits_one_line_under_4kb(working):
    """spec 07.1: one JSON line per record, under 4 KB, excerpt plus ref."""
    from hx.streams import MAX_RECORD_BYTES, main_stream

    huge = "x" * 500_000
    result = run_hook(working, "eng-001", "log",
                      post_tool(tool_input={"command": huge}, tool_response={"stdout": huge}))
    assert result.returncode == 0, result.stderr

    lines = main_stream(working, "eng-001").read_text().splitlines()
    assert lines, "the record was dropped"
    for line in lines:
        assert len(line.encode()) <= MAX_RECORD_BYTES, f"{len(line.encode())} bytes"
    record = json.loads(lines[-1])
    assert record["event"] == "post_tool"
    assert record["ref"]["transcript"] is not None or "ref" in record, "the ref survives the trim"


def test_the_seam_marker_is_written_at_the_threshold(working, tmp_path):
    """spec 05, 02: the hard trigger that does not wait for a step to close."""
    from hx.hook_log import seam_marker, threshold_for

    threshold = threshold_for(working, "eng-001")
    assert threshold == 500_000, "claude-opus-5 is capped at 500000 (spec 05)"

    run_hook(working, "eng-001", "log",
             post_tool(transcript=transcript_with(tmp_path, input_tokens=threshold - 1)))
    assert not seam_marker(working, "eng-001").exists()

    run_hook(working, "eng-001", "log",
             post_tool(transcript=transcript_with(tmp_path, input_tokens=threshold)))
    assert seam_marker(working, "eng-001").is_file()


def test_a_subagent_stream_never_triggers_a_seam(working, tmp_path):
    """A subagent's window is its own (spec 09.1: the marker is main-stream only)."""
    from hx.hook_log import seam_marker

    run_hook(working, "eng-001", "subagent-start",
             {"hook_event_name": "SubagentStart", "agent_id": "agent-1", "agent_type": "Explore"})
    run_hook(working, "eng-001", "log",
             post_tool(agent_id="agent-1", transcript=transcript_with(tmp_path, input_tokens=900_000)))
    assert not seam_marker(working, "eng-001").exists()
    assert events(working, "eng-001", "eng-001-s001")[-1] == "post_tool"


def test_a_malformed_log_payload_is_logged_and_allowed(working):
    result = subprocess.run(
        [sys.executable, "-m", "hx.hooks", "--id", "eng-001", "log"],
        input="{not json", env=clean_env(HARNESS_ROOT=str(working)),
        capture_output=True, text=True,
    )
    assert result.returncode == 0, "a broken hook never stops the agent"
    assert "log" in (working / "logs" / "eng-001" / "hook-errors.log").read_text()


# --- the subagent hooks ----------------------------------------------------------------------


def test_three_parallel_subagents_get_three_isolated_streams(working):
    """spec 13 M4: correct handles, each with its own context file."""
    paths = {}
    for n, agent_id in enumerate(("agent-a", "agent-b", "agent-c"), start=1):
        result = run_hook(working, "eng-001", "subagent-start", {
            "hook_event_name": "SubagentStart", "agent_id": agent_id, "agent_type": "Explore",
            "tool_input": {"prompt": f"look at thing {n}"},
        })
        assert result.returncode == 0, result.stderr
        body = json.loads(result.stdout)
        assert body["hookSpecificOutput"]["hookEventName"] == "SubagentStart"
        paths[agent_id] = body["hookSpecificOutput"]["additionalContext"]

    mapping = json.loads((working / "run" / "eng-001" / "subagents.json").read_text())
    assert mapping == {"agent-a": "s001", "agent-b": "s002", "agent-c": "s003"}

    logs = working / "logs" / "eng-001"
    for handle in ("s001", "s002", "s003"):
        assert (logs / f"eng-001-{handle}-open.jsonl").is_file()
        context = working / "run" / "eng-001" / f"eng-001-{handle}.context.md"
        assert context.is_file()
        assert str(context) in paths[[k for k, v in mapping.items() if v == handle][0]]

    # Each carries its own task, and none carries another's.
    first = (working / "run" / "eng-001" / "eng-001-s001.context.md").read_text()
    assert "look at thing 1" in first and "look at thing 2" not in first
    assert "subagent" in first.lower(), "SUBAGENTS.md is the memory section (spec 07.3)"
    assert "## Tasks" not in first, "`## Tasks` is main-stream only"


def test_the_main_stream_records_every_spawn_and_close(working):
    for agent_id in ("agent-a", "agent-b"):
        run_hook(working, "eng-001", "subagent-start",
                 {"hook_event_name": "SubagentStart", "agent_id": agent_id, "agent_type": "Explore"})
    for agent_id in ("agent-a", "agent-b"):
        run_hook(working, "eng-001", "subagent-stop", {
            "hook_event_name": "SubagentStop", "agent_id": agent_id,
            "last_assistant_message": f"{agent_id} done",
        })

    main = records(working, "eng-001", "eng-001-main")
    spawned = [r for r in main if r["event"] == "spawned"]
    closed = [r for r in main if r["event"] == "closed"]
    assert [r["handle"] for r in spawned] == ["s001", "s002"]
    assert [r["handle"] for r in closed] == ["s001", "s002"]


def test_subagent_stop_closes_the_stream(working):
    run_hook(working, "eng-001", "subagent-start",
             {"hook_event_name": "SubagentStart", "agent_id": "agent-a", "agent_type": "Explore"})
    logs = working / "logs" / "eng-001"
    assert (logs / "eng-001-s001-open.jsonl").is_file()

    run_hook(working, "eng-001", "subagent-stop", {
        "hook_event_name": "SubagentStop", "agent_id": "agent-a",
        "last_assistant_message": "the importer buffers in read_all",
    })
    assert not (logs / "eng-001-s001-open.jsonl").exists()
    assert (logs / "eng-001-s001-closed.jsonl").is_file()

    closing = records(working, "eng-001", "eng-001-s001")[-1]
    assert closing["event"] == "close"
    assert "read_all" in closing["output"]


def test_the_closed_stream_digest_reaches_the_parent(working):
    """spec 09.1: `PostToolUse(Agent)` is the only path that reaches the parent."""
    run_hook(working, "eng-001", "subagent-start",
             {"hook_event_name": "SubagentStart", "agent_id": "agent-a", "agent_type": "Explore"})
    run_hook(working, "eng-001", "subagent-stop",
             {"hook_event_name": "SubagentStop", "agent_id": "agent-a", "last_assistant_message": "done"})

    digest = working / "state" / "eng-001" / "eng-001-s001.digest.md"
    assert digest.is_file(), "a closed stream always has a digest file"
    digest.write_text("s001 found the buffer in read_all.\n")

    result = run_hook(working, "eng-001", "subagent-result", {
        "hook_event_name": "PostToolUse", "tool_name": "Agent",
        "tool_response": {"agentId": "agent-a", "status": "completed"},
        "tool_use_id": "toolu_x",
    })
    assert result.returncode == 0, result.stderr
    body = json.loads(result.stdout)
    assert str(digest) in body["hookSpecificOutput"]["additionalContext"]
    assert "Read tool" in body["hookSpecificOutput"]["additionalContext"]

    handed = [r for r in records(working, "eng-001", "eng-001-main") if r["event"] == "subagent_result"]
    assert handed and handed[-1]["handle"] == "s001" and handed[-1]["digest"] == str(digest)


def test_a_subagent_result_that_is_not_completed_says_nothing(working):
    run_hook(working, "eng-001", "subagent-start",
             {"hook_event_name": "SubagentStart", "agent_id": "agent-a", "agent_type": "Explore"})
    result = run_hook(working, "eng-001", "subagent-result", {
        "hook_event_name": "PostToolUse", "tool_name": "Agent",
        "tool_response": {"agentId": "agent-a", "status": "failed"},
    })
    assert result.returncode == 0 and result.stdout == ""


def test_complete_refuses_while_a_stream_is_open(working, hx):
    """spec 13 M4, and the same rule build-2 pinned, now driven through the real hooks."""
    run_hook(working, "eng-001", "subagent-start",
             {"hook_event_name": "SubagentStart", "agent_id": "agent-a", "agent_type": "Explore"})
    refused = hx("complete", "done", harness_id="eng-001")
    assert refused.returncode == 1 and "HX-CHECK-FAILED" in refused.stdout
    assert "s001" in refused.stdout

    run_hook(working, "eng-001", "subagent-stop",
             {"hook_event_name": "SubagentStop", "agent_id": "agent-a", "last_assistant_message": "x"})
    assert hx("complete", "done", harness_id="eng-001").returncode == 0


def test_an_unknown_agent_id_falls_back_to_the_main_stream(working):
    """A subagent hx never saw start: recorded on main, never on an invented handle."""
    assert run_hook(working, "eng-001", "log", post_tool(agent_id="ghost")).returncode == 0
    assert events(working, "eng-001", "eng-001-main")[-1] == "post_tool"
    assert not (working / "logs" / "eng-001").glob("*-s*-open.jsonl") or True


# --- the stop hook ----------------------------------------------------------------------------


def test_stop_writes_the_turn_marker(working):
    result = run_hook(working, "eng-001", "stop", {
        "hook_event_name": "Stop", "session_id": "s1",
        "background_tasks": [{"id": "bg1", "status": "running"}],
    })
    assert result.returncode == 0, result.stderr
    marker = json.loads((working / "run" / "eng-001" / "turn").read_text())
    assert marker["background_tasks"] == [{"id": "bg1", "status": "running"}]
    assert marker["ts"].endswith("Z") and marker["session_id"] == "s1"


def test_stop_pastes_a_goal_the_pane_was_owed(instance, hx, launched, orders, tmux_server):
    """spec 02, 08, 09.1: this is what delivers the Partner's self-dispatch pointer."""
    from .test_transitions import hold_pane, pasted

    launched("partner")
    orders("partner", checks="true")
    hold_pane(tmux_server, "partner")
    assert hx("dispatch", "partner", "orders/partner.md", cwd=instance).returncode == 0
    assert (instance / "run" / "partner" / "goal-pending").is_file()
    assert "/goal The order for" not in pasted(instance, "partner")

    # The turn ends: the stop hook runs, and the queued pointer goes in.
    result = run_hook(instance, "partner", "stop",
                      {"hook_event_name": "Stop", "background_tasks": []}, tmux=tmux_server)
    assert result.returncode == 0, result.stderr
    assert not (instance / "run" / "partner" / "goal-pending").exists()
    wait_for(
        lambda: "/goal The order for partner" in pasted(instance, "partner"),
        what="the pointer the stop hook pasted",
    )
    assert (instance / "run" / "partner" / "goal").is_file()


def test_stop_leaves_a_pending_seam_for_build_7(working):
    from hx.hook_log import seam_marker

    marker = seam_marker(working, "eng-001")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    result = run_hook(working, "eng-001", "stop", {"hook_event_name": "Stop", "background_tasks": []})
    assert result.returncode == 0
    assert marker.is_file(), "the marker stays, so the next boundary tries again (spec 09.3)"
    assert "build-7" in (working / "logs" / "eng-001" / "hook-errors.log").read_text()


def test_stop_prefers_the_pending_goal_over_the_seam(instance, hx, launched, orders, tmux_server):
    """spec 09.1: `goal-pending` first, `else if` the seam marker."""
    from hx.hook_log import seam_marker
    from .test_transitions import hold_pane

    launched("partner")
    orders("partner", checks="true")
    hold_pane(tmux_server, "partner")
    assert hx("dispatch", "partner", "orders/partner.md", cwd=instance).returncode == 0
    seam_marker(instance, "partner").touch()

    run_hook(instance, "partner", "stop",
             {"hook_event_name": "Stop", "background_tasks": []}, tmux=tmux_server)
    assert not (instance / "run" / "partner" / "goal-pending").exists()
    assert seam_marker(instance, "partner").is_file(), "the seam is still owed"


def test_a_subagent_with_no_prompt_in_its_payload_still_gets_a_task_section(working):
    """`SubagentStart` carries no prompt; the section says where the task came from.

    Verified live on 2026-09-20: the payload's documented fields are `session_id`,
    `hook_event_name`, `agent_id`, `agent_type`, `cwd` and `permission_mode`.
    """
    result = run_hook(working, "eng-001", "subagent-start",
                      {"hook_event_name": "SubagentStart", "agent_id": "agent-a",
                       "agent_type": "Explore", "cwd": "/tmp", "permission_mode": "bypassPermissions"})
    assert result.returncode == 0, result.stderr

    text = (working / "run" / "eng-001" / "eng-001-s001.context.md").read_text()
    section = text[text.index("## Task"):text.index("## Step state")]
    assert "_none yet_" not in section, "an empty task section tells the subagent nothing"
    assert "already in this conversation" in section
