"""The Companion loop — milestone M5 (spec 10, 07.2, 07.4, 13 M5).

Offline: a fake `claude -p` returns scripted step states, so the loop, the validator, the
budget, FIFO retention, flush, the seam policy and the digests are all exercised without a
model call. The live call is recorded in `goals/build-6.done.md`.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from .conftest import clean_env, wait_for
from .test_compose import run_hook
from .test_streams import post_tool, transcript_with
from .test_transitions import dispatch_working


def config_of(instance, item_id="eng-001"):
    from hx.companion import config_for

    return config_for(instance, item_id)


def state_of(instance, item_id, stream):
    from hx.companion import state_path
    from hx.stepstate import load

    return load(state_path(instance, item_id, stream))


def append(instance, item_id, stream, count, **extra):
    from hx.streams import append_record

    for _ in range(count):
        append_record(instance, item_id, stream, {"event": "post_tool", "tool": "Bash", **extra})


@pytest.fixture
def agent_working(instance, hx, launched, orders):
    launched("eng-001")
    dispatch_working(instance, hx, orders, order="Stream the importer.")
    return instance


def run_pass(instance, fake_companion, item_id="eng-001"):
    from hx.companion import compose_system_prompt, config_for, pass_once

    config = config_for(instance, item_id)
    compose_system_prompt(instance, config)
    return pass_once(instance, config, env={**clean_env(), **fake_companion.env})


# --- the loop --------------------------------------------------------------------------------


def test_a_pass_writes_step_state_stamped_with_prompt_version(agent_working, fake_companion):
    """spec 10 step 3, 07.4: `prompt_version` is the shas of BASE.md and the role file."""
    append(agent_working, "eng-001", "eng-001-main", 3)
    fake_companion.responses({"state": {"seq": 3, "goal": "stream the importer",
                                        "open_steps": [{"id": "st1", "intent": "read it"}]}})

    result = run_pass(agent_working, fake_companion)
    assert "eng-001-main" in result["streams"]

    state = state_of(agent_working, "eng-001", "eng-001-main")
    assert state["goal"] == "stream the importer"
    assert state["seq"] == 3
    assert set(state["prompt_version"]) == {"base", "role"}
    assert all(state["prompt_version"].values()), "both shas are present"
    assert state["ts"].endswith("Z")


def test_the_payload_is_the_layered_prompt_of_spec_10(agent_working, fake_companion):
    append(agent_working, "eng-001", "eng-001-main", 2)
    fake_companion.responses({"state": {"seq": 2}})
    run_pass(agent_working, fake_companion)

    payload = fake_companion.calls()[0]["payload"]
    order = [payload.index(h) for h in
             ("# Identity of this stream", "# Task", "# Current step state", "# New records")]
    assert order == sorted(order), "identity, task, state, records — the cacheable prefix first"
    assert "Stream the importer." in payload, "the order is in the task block"
    assert "Things I learned" in payload, "AGENTS.md is the identity of a main stream"


def test_the_argv_denies_every_tool_and_persists_nothing(agent_working, fake_companion):
    append(agent_working, "eng-001", "eng-001-main", 1)
    fake_companion.responses({"state": {"seq": 1}})
    run_pass(agent_working, fake_companion)

    from hx.companion import DENIED_TOOLS

    argv = fake_companion.calls()[0]["argv"]
    assert "-p" in argv
    denied = set(argv[argv.index("--disallowedTools") + 1].split(","))
    assert {"Bash", "Edit", "Write", "Read", "Agent", "WebFetch"} <= denied, "it cannot act"
    assert denied == set(DENIED_TOOLS)
    # Not `*`: --json-schema is a StructuredOutput *tool*, and `*` denies that too.
    assert "*" not in denied
    assert "StructuredOutput" not in denied, "the one tool the Companion needs"
    assert "--no-session-persistence" in argv, "every call is stateless (spec 10)"
    assert argv[argv.index("--output-format") + 1] == "json"
    assert "--json-schema" in argv
    assert "--append-system-prompt-file" in argv
    assert "--bare" not in argv, "bare mode never reads OAuth credentials (docs/en/headless)"
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert schema["required"] == ["seq"]


def test_only_records_past_the_cursor_are_sent(agent_working, fake_companion):
    append(agent_working, "eng-001", "eng-001-main", 2)
    fake_companion.responses({"state": {"seq": 2}}, {"state": {"seq": 4}})
    run_pass(agent_working, fake_companion)
    append(agent_working, "eng-001", "eng-001-main", 2)
    run_pass(agent_working, fake_companion)

    second = fake_companion.calls()[1]["payload"]
    assert "seq > 2" in second
    body = second.split("```jsonl", 1)[1]
    assert body.count('"seq": 3') + body.count('"seq":3') == 1
    assert '"seq": 1' not in body and '"seq":1' not in body


def test_a_pass_with_no_new_records_calls_nothing(agent_working, fake_companion):
    fake_companion.responses({"state": {"seq": 1}})
    result = run_pass(agent_working, fake_companion)
    assert result["streams"] == []
    assert fake_companion.calls() == []


# --- the validator ---------------------------------------------------------------------------


def test_invalid_output_keeps_the_prior_state(agent_working, fake_companion):
    """spec 13 M5: "invalid output keeps prior state"."""
    append(agent_working, "eng-001", "eng-001-main", 2)
    fake_companion.responses({"state": {"seq": 2, "goal": "the good one"}})
    run_pass(agent_working, fake_companion)
    good = state_of(agent_working, "eng-001", "eng-001-main")

    append(agent_working, "eng-001", "eng-001-main", 2)
    # Both the call and its one retry come back malformed.
    fake_companion.responses({"state": {"seq": "not a number"}}, {"state": {"seq": "still bad"}})
    run_pass(agent_working, fake_companion)

    assert state_of(agent_working, "eng-001", "eng-001-main") == good
    errors = (agent_working / "logs" / "eng-001" / "hook-errors.log").read_text()
    assert "keeping the prior state" in errors and "retried once" in errors


def test_a_bad_answer_is_retried_once_with_the_failure_named(agent_working, fake_companion):
    append(agent_working, "eng-001", "eng-001-main", 2)
    fake_companion.responses(
        {"raw_result": "Here you go:\n```json\n{\"seq\": 2}\n```"},
        {"state": {"seq": 2, "goal": "second time"}},
    )
    run_pass(agent_working, fake_companion)

    calls = fake_companion.calls()
    assert len(calls) == 2, "one retry, not more"
    assert "Your previous answer was rejected" in calls[1]["payload"]
    assert "not a bare JSON object" in calls[1]["payload"], "the failure is named"
    assert state_of(agent_working, "eng-001", "eng-001-main")["goal"] == "second time"


def test_fences_are_not_stripped(agent_working, fake_companion):
    """Strict JSON: a parser that strips fences would teach the model that fences are fine."""
    append(agent_working, "eng-001", "eng-001-main", 1)
    fake_companion.responses({"raw_result": "```json\n{\"seq\": 1}\n```"},
                             {"raw_result": "```json\n{\"seq\": 1}\n```"})
    run_pass(agent_working, fake_companion)
    assert state_of(agent_working, "eng-001", "eng-001-main") is None


def test_the_cursor_never_goes_backwards(agent_working, fake_companion):
    append(agent_working, "eng-001", "eng-001-main", 4)
    fake_companion.responses({"state": {"seq": 4}})
    run_pass(agent_working, fake_companion)
    append(agent_working, "eng-001", "eng-001-main", 2)
    fake_companion.responses({"state": {"seq": 1}}, {"state": {"seq": 1}})
    run_pass(agent_working, fake_companion)
    assert state_of(agent_working, "eng-001", "eng-001-main")["seq"] == 4


def test_hx_owns_the_cursor_even_when_the_model_lags(agent_working, fake_companion):
    append(agent_working, "eng-001", "eng-001-main", 5)
    fake_companion.responses({"state": {"seq": 2}})
    run_pass(agent_working, fake_companion)
    assert state_of(agent_working, "eng-001", "eng-001-main")["seq"] == 5, (
        "otherwise hx re-feeds the same records forever"
    )


# --- the budget ------------------------------------------------------------------------------


def test_the_budget_is_enforced_deterministically(agent_working, fake_companion):
    """spec 13 M5: state stays within budget. The prompt asks; this is the backstop."""
    from hx.stepstate import estimate_tokens

    harness = agent_working / "config" / "eng-001" / "harness.json"
    config = json.loads(harness.read_text())
    config["companion"] = {"state_budget_tokens": 200}
    harness.write_text(json.dumps(config))

    append(agent_working, "eng-001", "eng-001-main", 1)
    fake_companion.responses({"state": {
        "seq": 1,
        "goal": "g",
        "dead_ends": ["d" * 600, "e" * 600],
        "closed_steps": [{"id": "st1", "outcome": "x", "chatter": "y" * 600}],
        "working_set": {"dirty": ["z" * 600], "files": [{"path": "a.py", "note": "n" * 600}]},
    }})
    run_pass(agent_working, fake_companion)

    state = state_of(agent_working, "eng-001", "eng-001-main")
    assert estimate_tokens(state) <= 200 + 50, estimate_tokens(state)
    assert "chatter" not in json.dumps(state), "closed steps collapse first (spec 10)"
    errors = (agent_working / "logs" / "eng-001" / "hook-errors.log").read_text()
    assert "over budget, evicted" in errors


def test_eviction_order_is_spec_10s(agent_working):
    from hx.stepstate import evict

    state = {
        "seq": 1,
        "dead_ends": ["old" * 300, "new" * 300],
        "closed_steps": [{"id": "s", "outcome": "o", "detail": "d" * 900}],
        "working_set": {"dirty": ["f" * 900], "files": [{"path": "x.py", "note": "n" * 900}]},
    }
    _, applied = evict(state, 5)
    assert applied[:2] == ["collapse_closed_steps", "oldest_dead_ends"]
    assert applied.index("closed_step_working_set") < applied.index("untouched_file_notes")


# --- FIFO retention ---------------------------------------------------------------------------


def test_truncation_never_drops_records_at_or_ahead_of_the_cursor(agent_working):
    """spec 07.1: never below `state.seq − keep_behind`."""
    from hx.streams import iter_records, main_stream, truncate

    append(agent_working, "eng-001", "eng-001-main", 400)
    path = main_stream(agent_working, "eng-001")
    truncate(path, state_seq=350, max_records=100, keep_behind=20)

    kept = [r["seq"] for r in iter_records(path)]
    floor = 350 - 20
    assert max(kept) == 400, "everything ahead of the cursor survives"
    assert set(range(floor + 1, 401)) <= set(kept), (
        "nothing at or ahead of `state.seq − keep_behind` is ever dropped (spec 07.1)"
    )
    assert len(kept) < 400, "and the stream did shrink"


def test_truncation_keeps_everything_when_the_cursor_is_behind(agent_working):
    from hx.streams import iter_records, main_stream, truncate

    append(agent_working, "eng-001", "eng-001-main", 200)
    path = main_stream(agent_working, "eng-001")
    truncate(path, state_seq=5, max_records=50, keep_behind=100)
    kept = [r["seq"] for r in iter_records(path)]
    assert len(kept) == 200, "nothing ahead of an un-caught-up Companion is dropped"


# --- the 500-record replay ---------------------------------------------------------------------


def test_a_500_record_replay_stays_within_budget(agent_working, fake_companion):
    """spec 13 M5: state stays within budget across a 500-record replay."""
    from hx.stepstate import estimate_tokens

    harness = agent_working / "config" / "eng-001" / "harness.json"
    config = json.loads(harness.read_text())
    config["companion"] = {"batch_records": 20, "state_budget_tokens": 2000}
    harness.write_text(json.dumps(config))

    responses = []
    for batch in range(1, 26):
        responses.append({"state": {
            "seq": batch * 20,
            "goal": "stream the importer",
            "closed_steps": [{"id": f"st{n}", "outcome": f"did thing {n}" * 6, "verified": True}
                             for n in range(1, batch + 1)],
            "dead_ends": [f"dead end {n}" * 8 for n in range(1, batch + 1)],
            "working_set": {"files": [{"path": f"f{n}.py", "note": "note" * 20}
                                      for n in range(1, batch + 1)]},
        }})
    fake_companion.responses(*responses)

    sizes = []
    for batch in range(25):
        append(agent_working, "eng-001", "eng-001-main", 20)
        run_pass(agent_working, fake_companion)
        state = state_of(agent_working, "eng-001", "eng-001-main")
        sizes.append(estimate_tokens(state))

    assert state_of(agent_working, "eng-001", "eng-001-main")["seq"] == 500
    assert max(sizes) <= 2000 + 50, f"peak {max(sizes)} tokens"
    assert all(s["prompt_version"] for s in [state_of(agent_working, "eng-001", "eng-001-main")])


# --- the seam policy ----------------------------------------------------------------------------


def test_the_seam_marker_needs_all_four_conditions(agent_working, fake_companion, tmp_path):
    """spec 10: a closed step, enough context, enough time, and no open subagents."""
    from hx.hook_log import seam_marker

    harness = agent_working / "config" / "eng-001" / "harness.json"
    config = json.loads(harness.read_text())
    config["companion"] = {"seam_min_context_tokens": 1000, "seam_min_interval_s": 600}
    harness.write_text(json.dumps(config))

    # No closed step yet: no seam, however much context there is.
    run_hook(agent_working, "eng-001", "log",
             post_tool(transcript=transcript_with(tmp_path, input_tokens=5000)))
    fake_companion.responses({"state": {"seq": 1, "open_steps": [{"id": "st1", "intent": "x"}]}})
    assert run_pass(agent_working, fake_companion)["seam"] is False
    assert not seam_marker(agent_working, "eng-001").exists()

    # A closed step, and the context is there.
    run_hook(agent_working, "eng-001", "log",
             post_tool(transcript=transcript_with(tmp_path, input_tokens=5000)))
    fake_companion.responses({"state": {"seq": 2, "closed_steps": [{"id": "st1", "outcome": "done"}]}})
    assert run_pass(agent_working, fake_companion)["seam"] is True
    assert seam_marker(agent_working, "eng-001").is_file()


def test_an_open_subagent_holds_the_seam_off(agent_working, fake_companion, tmp_path):
    from hx.hook_log import seam_marker

    harness = agent_working / "config" / "eng-001" / "harness.json"
    config = json.loads(harness.read_text())
    config["companion"] = {"seam_min_context_tokens": 1000, "seam_min_interval_s": 0}
    harness.write_text(json.dumps(config))

    run_hook(agent_working, "eng-001", "log",
             post_tool(transcript=transcript_with(tmp_path, input_tokens=5000)))
    fake_companion.responses({"state": {"seq": 1, "closed_steps": [{"id": "st1", "outcome": "d"}],
                                        "subagents_open": ["s001"]}})
    assert run_pass(agent_working, fake_companion)["seam"] is False
    assert not seam_marker(agent_working, "eng-001").exists()


def test_not_enough_context_holds_the_seam_off(agent_working, fake_companion, tmp_path):
    from hx.hook_log import seam_marker

    harness = agent_working / "config" / "eng-001" / "harness.json"
    config = json.loads(harness.read_text())
    config["companion"] = {"seam_min_context_tokens": 100_000}
    harness.write_text(json.dumps(config))

    run_hook(agent_working, "eng-001", "log",
             post_tool(transcript=transcript_with(tmp_path, input_tokens=5000)))
    fake_companion.responses({"state": {"seq": 1, "closed_steps": [{"id": "st1", "outcome": "d"}]}})
    assert run_pass(agent_working, fake_companion)["seam"] is False
    assert not seam_marker(agent_working, "eng-001").exists()


# --- digests, flush, and resume -------------------------------------------------------------------


def test_a_closed_subagent_stream_gets_a_digest(agent_working, fake_companion):
    run_hook(agent_working, "eng-001", "subagent-start",
             {"hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "Explore"})
    run_hook(agent_working, "eng-001", "subagent-stop",
             {"hook_event_name": "SubagentStop", "agent_id": "a1",
              "last_assistant_message": "found the buffer in read_all"})

    fake_companion.responses(
        {"state": {"seq": 2}},
        {"raw_result": "s001 read the importer and found the buffer in read_all. Nothing committed."},
    )
    result = run_pass(agent_working, fake_companion)
    assert "eng-001-s001" in result["digests"]

    digest = (agent_working / "state" / "eng-001" / "eng-001-s001.digest.md").read_text()
    assert "read_all" in digest and "_pending companion_" not in digest


def test_flush_returns_when_the_state_is_at_the_head(agent_working, fake_companion):
    from hx.flush import flush

    append(agent_working, "eng-001", "eng-001-main", 3)
    assert flush(agent_working, "eng-001") is False, "no Companion window: nothing will move it"

    fake_companion.responses({"state": {"seq": 3}})
    run_pass(agent_working, fake_companion)
    assert flush(agent_working, "eng-001") is True


def test_is_caught_up_tracks_every_stream(agent_working, fake_companion):
    from hx.companion import is_caught_up

    run_hook(agent_working, "eng-001", "subagent-start",
             {"hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "Explore"})
    append(agent_working, "eng-001", "eng-001-main", 2)
    assert is_caught_up(agent_working, "eng-001") is False

    fake_companion.responses({"state": {"seq": 99}}, {"state": {"seq": 99}})
    run_pass(agent_working, fake_companion)
    assert is_caught_up(agent_working, "eng-001") is True


def test_a_replayed_resume_keeps_closed_steps_and_absorbs_the_addendum(
    agent_working, hx, fake_companion
):
    """spec 10 "Across `hx resume`", spec 13 M5."""
    from .test_transitions import write_addendum

    append(agent_working, "eng-001", "eng-001-main", 4)
    fake_companion.responses({"state": {
        "seq": 4, "goal": "stream the importer",
        "closed_steps": [{"id": "st1", "outcome": "read the importer", "verified": True}],
    }})
    run_pass(agent_working, fake_companion)

    assert hx("complete", "decision", harness_id="eng-001").returncode == 0
    write_addendum(agent_working, "eng-001", "Use a generator, not a list.")
    assert hx("resume", "eng-001", "orders/eng-001.addendum.md", cwd=agent_working).returncode == 0

    # The state survived the resume: only `hx dispatch` archives it (spec 07.2).
    state = state_of(agent_working, "eng-001", "eng-001-main")
    assert state["closed_steps"][0]["outcome"] == "read the importer"
    assert state["seq"] == 4

    append(agent_working, "eng-001", "eng-001-main", 2)
    fake_companion.responses({"state": {
        "seq": 6, "goal": "stream the importer with a generator",
        "closed_steps": [{"id": "st1", "outcome": "read the importer", "verified": True}],
    }})
    run_pass(agent_working, fake_companion)

    payload = fake_companion.calls()[-1]["payload"]
    assert "Use a generator, not a list." in payload, "the addendum is in the task block"
    after = state_of(agent_working, "eng-001", "eng-001-main")
    assert after["closed_steps"][0]["outcome"] == "read the importer", "old steps kept"
    assert "generator" in after["goal"], "and the new instruction absorbed"


# --- the final pass -------------------------------------------------------------------------------


def test_the_final_pass_writes_the_digest_from_the_step_state(agent_working, hx, fake_companion):
    append(agent_working, "eng-001", "eng-001-main", 2)
    fake_companion.responses({"state": {
        "seq": 2, "goal": "stream the importer",
        "closed_steps": [{"id": "st1", "outcome": "rewrote read_all", "verified": True,
                          "commit": "abc1234"}],
        "open_steps": [{"id": "st2", "intent": "benchmark it", "next": "run the suite"}],
    }})
    run_pass(agent_working, fake_companion)

    assert hx("complete", "done", harness_id="eng-001").returncode == 0
    body = (agent_working / "pods" / "engineers" / "eng-001-complete.md").read_text()
    digest = body.split("## Digest", 1)[1]
    assert "rewrote read_all" in digest and "abc1234" in digest
    assert "benchmark it" in digest


def test_a_blocked_digest_leads_with_the_blocker(agent_working, hx, fake_companion):
    """spec 10: the blocker or question first, so the Partner's addendum can answer it."""
    append(agent_working, "eng-001", "eng-001-main", 1)
    fake_companion.responses({"state": {
        "seq": 1, "goal": "stream the importer",
        "blockers": ["the fixture file is not in the repo"],
        "closed_steps": [{"id": "st1", "outcome": "read it"}],
    }})
    run_pass(agent_working, fake_companion)

    assert hx("complete", "blocked", harness_id="eng-001").returncode == 0
    body = (agent_working / "pods" / "engineers" / "eng-001-complete.md").read_text()
    digest = body.split("## Digest", 1)[1].strip()
    assert digest.startswith("**Blocked on:** the fixture file is not in the repo")


def test_the_final_pass_folds_in_closed_stream_digests(agent_working, hx, fake_companion):
    run_hook(agent_working, "eng-001", "subagent-start",
             {"hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "Explore"})
    run_hook(agent_working, "eng-001", "subagent-stop",
             {"hook_event_name": "SubagentStop", "agent_id": "a1", "last_assistant_message": "done"})
    (agent_working / "state" / "eng-001" / "eng-001-s001.digest.md").write_text(
        "s001 measured the buffer: 400 MB peak.\n"
    )
    append(agent_working, "eng-001", "eng-001-main", 1)
    fake_companion.responses({"state": {"seq": 9, "goal": "stream the importer"}},
                             {"state": {"seq": 9}})
    run_pass(agent_working, fake_companion)

    assert hx("complete", "done", harness_id="eng-001").returncode == 0
    digest = (agent_working / "pods" / "engineers" / "eng-001-complete.md").read_text()
    assert "400 MB peak" in digest


# --- usage ----------------------------------------------------------------------------------------


def test_cache_reads_are_recorded_for_hx_metrics(agent_working, fake_companion):
    """spec 05: `usage.cache_read_input_tokens` is what `hx metrics` reports."""
    append(agent_working, "eng-001", "eng-001-main", 2)
    fake_companion.responses({"state": {"seq": 2}}, {"state": {"seq": 4}})
    run_pass(agent_working, fake_companion)
    append(agent_working, "eng-001", "eng-001-main", 2)
    run_pass(agent_working, fake_companion)

    recorded = [json.loads(line) for line in
                (agent_working / "state" / "eng-001" / "companion-usage.jsonl").read_text().splitlines()]
    assert len(recorded) == 2
    assert recorded[0]["usage"]["cache_read_input_tokens"] == 0
    assert recorded[1]["usage"]["cache_read_input_tokens"] > 0, "the identical prefix is cached"


# --- the Companion window ------------------------------------------------------------------------


def test_launch_starts_the_companion_window(instance, hx, tmux_server):
    """spec 08: `hx launch` runs `start.sh` in window `main` and `hx companion` in `companion`."""
    result = hx("launch", "eng-001")
    assert result.returncode == 0, result.stdout + result.stderr
    windows = subprocess.run(
        [*tmux_server, "list-windows", "-t", "=eng-001", "-F", "#{window_name}"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert "main" in windows and "companion" in windows


def test_no_companion_leaves_the_window_out(instance, hx, tmux_server):
    result = hx("launch", "--no-companion", "eng-001")
    assert result.returncode == 0, result.stdout + result.stderr
    windows = subprocess.run(
        [*tmux_server, "list-windows", "-t", "=eng-001", "-F", "#{window_name}"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert windows == ["main"]
