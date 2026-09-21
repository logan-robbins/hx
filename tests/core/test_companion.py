"""The Companion as a tmux session — milestone M5 (spec 10, 07.2, 07.4, 13 M5).

There is no headless path: the Companion is a Claude Code session in window `<id>:companion`,
woken by pasting `/clear` and a fixed pointer to a pass file. Here the fake `claude` plays it —
it reads the pass it is pointed at, writes a scripted `out.json`, and fires its own `stop`
hook, which is what the real one does.
"""

from __future__ import annotations

import json
import subprocess

import pytest

from .conftest import wait_for
from .test_compose import run_hook
from .test_streams import post_tool, transcript_with
from .test_transitions import dispatch_working


def state_of(instance, item_id, stream):
    from hx.companion import state_path
    from hx.stepstate import load

    return load(state_path(instance, item_id, stream))


def append(instance, item_id, stream, count, **extra):
    """Append records and wake the Companion, which is what the `log` hook does (spec 10)."""
    from hx.companion import wake_due
    from hx.streams import append_record

    for _ in range(count):
        append_record(instance, item_id, stream, {"event": "post_tool", "tool": "Bash", **extra})
    # `force` is the turn-end trigger: the `stop` and `subagent-stop` hooks and `hx flush` all
    # wake regardless of `batch_records`, which is the threshold the `log` hook uses.
    wake_due(instance, item_id, force=True)


def wake_until(instance, item_id, predicate, *, what):
    """Keep waking until `predicate` holds.

    A pass whose wake found the pane mid-turn stays on disk, and the next wake — any trigger,
    or `hx flush` — delivers it (spec 10). There is no queue and no second mechanism, so a test
    that needs a second pass with no new records plays the part of that next trigger.
    """
    from hx.flush import signal

    def step():
        if predicate():
            return True
        signal(instance, item_id)
        return False

    return wait_for(step, what=what)


def wait_for_state(instance, item_id, stream, seq):
    wait_for(lambda: (state_of(instance, item_id, stream) or {}).get("seq") == seq,
             what=f"{stream} state at seq {seq}")
    return state_of(instance, item_id, stream)


@pytest.fixture
def companion(instance, hx, launched, orders, companion_script, tmux_server, monkeypatch):
    """A worker with a live Companion session, dispatched and working.

    `HX_TMUX` is set in this process too, so the helpers that talk to tmux with no explicit
    env — `wake_due`, `flush` — reach the test's private server rather than the machine's.
    """
    monkeypatch.setenv("HX_TMUX", " ".join(tmux_server))
    launched("eng-001", companion=True)
    dispatch_working(instance, hx, orders, order="Stream the importer.")
    wait_for((instance / "run" / "eng-001" / "companion-home" / "settings.json").is_file,
             what="the Companion home")
    return instance


# --- the session ------------------------------------------------------------------------------


def test_launch_starts_the_companion_session(instance, hx, tmux_server):
    """spec 08, 10: `start.sh` in window `main`, the Companion in window `companion`."""
    assert hx("launch", "eng-001").returncode == 0
    windows = subprocess.run(
        [*tmux_server, "list-windows", "-t", "=eng-001", "-F", "#{window_name}"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert sorted(windows) == ["companion", "main"]


def test_no_companion_leaves_the_window_out(instance, hx, tmux_server):
    assert hx("launch", "--no-companion", "eng-001").returncode == 0
    windows = subprocess.run(
        [*tmux_server, "list-windows", "-t", "=eng-001", "-F", "#{window_name}"],
        capture_output=True, text=True, check=True,
    ).stdout.split()
    assert windows == ["main"]


def test_the_system_prompt_is_composed_before_launch(instance, hx):
    """spec 10: BASE.md, the role file, and the harness facts — the cached prefix."""
    assert hx("launch", "eng-001").returncode == 0
    text = (instance / "run" / "eng-001" / "companion-system.md").read_text()
    assert "# Companion" in text, "BASE.md"
    assert "- id: `eng-001`" in text and "- role: `engineer`" in text
    assert "state budget:" in text and "seam policy:" in text


def test_the_companion_home_has_only_its_two_hooks(instance, hx):
    """spec 10: the guard, and its own stop. No product skills, no CLAUDE.md."""
    assert hx("launch", "eng-001").returncode == 0
    home = instance / "run" / "eng-001" / "companion-home"
    settings = json.loads((home / "settings.json").read_text())
    assert sorted(settings["hooks"]) == ["PreToolUse", "Stop"]
    commands = [h["command"] for entries in settings["hooks"].values()
                for entry in entries for h in entry["hooks"]]
    assert any(c.endswith("guard") for c in commands)
    assert any(c.endswith("companion-stop") for c in commands)
    assert not (home / "CLAUDE.md").exists() and not (home / "skills").exists()


def test_there_is_no_headless_runner_left(instance):
    """spec 02 "Model calls"; `tests/guard/test_no_headless.py` is the enforcing test."""
    from hx import companion as companion_mod

    source = (instance.parent, )
    del source
    assert not hasattr(companion_mod, "call_model")
    assert not hasattr(companion_mod, "DENIED_TOOLS")


# --- the pass protocol -------------------------------------------------------------------------


def test_the_pass_file_carries_every_key(companion, companion_script):
    """CONTRACTS.md plus handoff/gtm-to-build.md gtm-8: nine keys, none omitted."""
    from hx.companion import pass_path, write_pass

    path = write_pass(companion, "eng-001", "eng-001-main")
    keys = [line.split(":", 1)[0] for line in path.read_text().splitlines() if ":" in line]
    assert keys == ["stream", "state", "log", "from_seq", "write", "retry_reason",
                    "context_tokens", "last_seam_ts", "open_subagents"]
    body = path.read_text()
    assert "stream: eng-001-main" in body and "from_seq: 0" in body
    # Empty values are written, never omitted: the Companion must not have to guess.
    assert "retry_reason: \n" in body or body.rstrip().endswith("open_subagents:")
    assert "last_seam_ts: \n" in body, "no seam yet, which satisfies the interval condition"


def test_the_pass_reports_the_seam_inputs(companion, tmp_path):
    from hx.companion import write_pass

    run_hook(companion, "eng-001", "log",
             post_tool(transcript=transcript_with(tmp_path, input_tokens=4200)))
    run_hook(companion, "eng-001", "subagent-start",
             {"hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "Explore"})

    body = write_pass(companion, "eng-001", "eng-001-main").read_text()
    assert "context_tokens: 4200" in body
    assert "open_subagents: s001" in body


def test_a_subagent_pass_leaves_the_seam_keys_empty(companion):
    """The seam policy is evaluated on the main stream only (gtm-8)."""
    from hx.companion import write_pass

    run_hook(companion, "eng-001", "subagent-start",
             {"hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "Explore"})
    body = write_pass(companion, "eng-001", "eng-001-s001").read_text()
    assert "context_tokens: \n" in body and "open_subagents: \n" in body


def test_the_companion_writes_state_and_its_stop_hook_installs_it(companion, companion_script):
    companion_script.responses({"state": {
        "seq": 3, "goal": "stream the importer",
        "closed_steps": [{"id": "st1", "outcome": "read it", "verified": True}],
    }})
    append(companion, "eng-001", "eng-001-main", 3)

    state = wait_for_state(companion, "eng-001", "eng-001-main", 3)
    assert state["goal"] == "stream the importer"
    assert set(state["prompt_version"]) == {"base", "role"}
    assert all(state["prompt_version"].values())
    assert state["ts"].endswith("Z")

    from hx.companion import out_path, pass_path

    assert not out_path(companion, "eng-001", "eng-001-main").exists(), "consumed"
    assert not pass_path(companion, "eng-001", "eng-001-main").exists(), "and cleared"


def test_the_pass_names_the_cursor_so_only_new_records_are_read(companion, companion_script):
    companion_script.responses({"state": {"seq": 3}}, {"state": {"seq": 6}})
    append(companion, "eng-001", "eng-001-main", 3)
    wait_for_state(companion, "eng-001", "eng-001-main", 3)
    append(companion, "eng-001", "eng-001-main", 3)
    wait_for_state(companion, "eng-001", "eng-001-main", 6)

    assert [p["from_seq"] for p in companion_script.passes()][:2] == ["0", "3"]


def test_nothing_is_pasted_but_clear_and_the_pointer(companion, companion_script):
    """spec 10: "Nothing is passed as prompt text but the pointer"."""
    companion_script.responses({"state": {"seq": 3}})
    append(companion, "eng-001", "eng-001-main", 3)
    wait_for_state(companion, "eng-001", "eng-001-main", 3)

    log = companion / "run" / "eng-001" / "companion" / "fake-input.log"
    if not log.is_file():
        log = companion / "run" / "eng-001" / "fake-input.log"
    for line in [l for l in log.read_text().splitlines() if l.strip()]:
        assert line == "/clear" or line.startswith(("Companion pass: read ", "/goal "))


# --- the validator -------------------------------------------------------------------------------


def test_invalid_output_is_retried_once_then_the_prior_state_stands(companion, companion_script):
    """spec 10, 13 M5: "invalid output keeps prior state"."""
    companion_script.responses({"state": {"seq": 3, "goal": "the good one"}})
    append(companion, "eng-001", "eng-001-main", 3)
    good = wait_for_state(companion, "eng-001", "eng-001-main", 3)

    companion_script.responses({"text": "```json\n{\"seq\": 6}\n```"}, {"text": "still not json"})
    append(companion, "eng-001", "eng-001-main", 3)

    errors = companion / "logs" / "eng-001" / "hook-errors.log"
    wake_until(companion, "eng-001",
               lambda: errors.is_file() and "keeping the prior state" in errors.read_text(),
               what="the second failure")
    assert state_of(companion, "eng-001", "eng-001-main") == good
    assert "pass rewritten with the reason" in errors.read_text()


def test_a_fenced_answer_is_rejected_not_stripped(companion, companion_script):
    """No fence-tolerant parser (spec 10, the orchestrator's answer to gtm-7)."""
    companion_script.responses({"text": "```json\n{\"seq\": 3}\n```"},
                               {"text": "```json\n{\"seq\": 3}\n```"})
    append(companion, "eng-001", "eng-001-main", 3)
    errors = companion / "logs" / "eng-001" / "hook-errors.log"
    wake_until(companion, "eng-001",
               lambda: errors.is_file() and "keeping the prior state" in errors.read_text(),
               what="the fenced answer to be rejected twice")
    assert state_of(companion, "eng-001", "eng-001-main") is None


def test_the_retry_pass_carries_the_reason(companion, companion_script):
    companion_script.responses({"text": "not json at all"}, {"state": {"seq": 3}})
    append(companion, "eng-001", "eng-001-main", 3)
    wake_until(companion, "eng-001",
               lambda: (state_of(companion, "eng-001", "eng-001-main") or {}).get("seq") == 3,
               what="the retry to land")

    passes = companion_script.passes()
    assert len(passes) >= 2
    assert passes[0]["retry_reason"] == ""
    assert "not valid JSON" in passes[1]["retry_reason"], passes[1]


def test_hx_owns_the_cursor_even_when_the_model_lags(companion, companion_script):
    companion_script.responses({"state": {"seq": 2}})
    append(companion, "eng-001", "eng-001-main", 5)
    assert wait_for_state(companion, "eng-001", "eng-001-main", 5)["seq"] == 5


def test_the_validator_rejects_what_the_schema_forbids(instance):
    from hx.stepstate import InvalidState, validate

    assert validate({"seq": 1, "goal": "x"})["seq"] == 1
    for bad in ([], {"seq": "x"}, {"seq": 1, "zz": 1}, {"goal": "x"},
                {"seq": 1, "decisions": [{"d": 1}]}, {"seq": -1}):
        with pytest.raises(InvalidState):
            validate(bad)
    with pytest.raises(InvalidState):
        validate({"seq": 2}, previous_seq=5)


# --- the budget -------------------------------------------------------------------------------------


def test_the_budget_is_enforced_deterministically(companion, companion_script):
    from hx.stepstate import estimate_tokens

    harness = companion / "config" / "eng-001" / "harness.json"
    config = json.loads(harness.read_text())
    config["companion"] = {"state_budget_tokens": 200}
    harness.write_text(json.dumps(config))

    companion_script.responses({"state": {
        "seq": 3, "goal": "g",
        "dead_ends": ["d" * 600, "e" * 600],
        "closed_steps": [{"id": "st1", "outcome": "x", "chatter": "y" * 600}],
        "working_set": {"dirty": ["z" * 600], "files": [{"path": "a.py", "note": "n" * 600}]},
    }})
    append(companion, "eng-001", "eng-001-main", 3)
    state = wait_for_state(companion, "eng-001", "eng-001-main", 3)

    assert estimate_tokens(state) <= 250, estimate_tokens(state)
    assert "chatter" not in json.dumps(state), "closed steps collapse first (spec 10)"
    assert "over budget, evicted" in (companion / "logs" / "eng-001" / "hook-errors.log").read_text()


def test_eviction_order_is_spec_10s(instance):
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


# --- FIFO retention ------------------------------------------------------------------------------------


def test_truncation_never_drops_records_at_or_ahead_of_the_cursor(instance):
    """spec 07.1: never below `state.seq − keep_behind`."""
    from hx.streams import iter_records, main_stream, truncate

    append(instance, "eng-001", "eng-001-main", 400)
    path = main_stream(instance, "eng-001")
    truncate(path, state_seq=350, max_records=100, keep_behind=20)

    kept = [r["seq"] for r in iter_records(path)]
    assert max(kept) == 400
    assert set(range(331, 401)) <= set(kept), "nothing at or ahead of the floor is dropped"
    assert len(kept) < 400, "and the stream did shrink"


def test_truncation_keeps_everything_when_the_cursor_is_behind(instance):
    from hx.streams import iter_records, main_stream, truncate

    append(instance, "eng-001", "eng-001-main", 200)
    path = main_stream(instance, "eng-001")
    truncate(path, state_seq=5, max_records=50, keep_behind=100)
    assert len(list(iter_records(path))) == 200


# --- the 500-record replay -------------------------------------------------------------------------------


def test_a_500_record_replay_stays_within_budget(companion, companion_script):
    """spec 13 M5. The log is synthesised: the build-5 live run recorded only a few records."""
    from hx.stepstate import estimate_tokens

    harness = companion / "config" / "eng-001" / "harness.json"
    config = json.loads(harness.read_text())
    config["companion"] = {"batch_records": 20, "state_budget_tokens": 2000}
    harness.write_text(json.dumps(config))

    companion_script.responses(*[
        {"state": {
            "seq": batch * 20,
            "goal": "stream the importer",
            "closed_steps": [{"id": f"st{n}", "outcome": f"did thing {n}" * 6, "verified": True}
                             for n in range(1, batch + 1)],
            "dead_ends": [f"dead end {n}" * 8 for n in range(1, batch + 1)],
            "working_set": {"files": [{"path": f"f{n}.py", "note": "note" * 20}
                                      for n in range(1, batch + 1)]},
        }} for batch in range(1, 26)
    ])

    sizes = []
    for batch in range(1, 26):
        append(companion, "eng-001", "eng-001-main", 20)
        sizes.append(estimate_tokens(wait_for_state(companion, "eng-001", "eng-001-main", batch * 20)))

    assert state_of(companion, "eng-001", "eng-001-main")["seq"] == 500
    assert max(sizes) <= 2050, f"peak {max(sizes)} tokens"


# --- the seam policy --------------------------------------------------------------------------------------


def test_the_seam_marker_needs_all_four_conditions(companion, companion_script, tmp_path):
    """spec 10: a closed step, enough context, enough time, and no open subagents."""
    from hx.hook_log import seam_marker

    harness = companion / "config" / "eng-001" / "harness.json"
    config = json.loads(harness.read_text())
    config["companion"] = {"seam_min_context_tokens": 1000, "seam_min_interval_s": 600}
    harness.write_text(json.dumps(config))

    run_hook(companion, "eng-001", "log",
             post_tool(transcript=transcript_with(tmp_path, input_tokens=5000)))
    companion_script.responses({"state": {"seq": 3, "open_steps": [{"id": "st1", "intent": "x"}]}})
    append(companion, "eng-001", "eng-001-main", 2)
    wait_for_state(companion, "eng-001", "eng-001-main", 3)
    assert not seam_marker(companion, "eng-001").exists(), "no closed step, no seam"

    companion_script.responses({"state": {"seq": 5,
                                          "closed_steps": [{"id": "st1", "outcome": "done"}]}})
    append(companion, "eng-001", "eng-001-main", 2)
    wait_for_state(companion, "eng-001", "eng-001-main", 5)
    wait_for(seam_marker(companion, "eng-001").is_file, what="the seam marker")


def test_an_open_subagent_holds_the_seam_off(companion, companion_script, tmp_path):
    from hx.hook_log import seam_marker

    harness = companion / "config" / "eng-001" / "harness.json"
    config = json.loads(harness.read_text())
    config["companion"] = {"seam_min_context_tokens": 1000, "seam_min_interval_s": 0}
    harness.write_text(json.dumps(config))

    run_hook(companion, "eng-001", "log",
             post_tool(transcript=transcript_with(tmp_path, input_tokens=5000)))
    companion_script.responses({"state": {"seq": 99,
                                          "closed_steps": [{"id": "st1", "outcome": "d"}],
                                          "subagents_open": ["s001"]}})
    append(companion, "eng-001", "eng-001-main", 2)
    wait_for_state(companion, "eng-001", "eng-001-main", 99)
    assert not seam_marker(companion, "eng-001").exists()


# --- flush, resume, and the final pass -----------------------------------------------------------------------


def test_flush_waits_for_the_head(companion, companion_script):
    from hx.companion import is_caught_up
    from hx.flush import flush

    companion_script.responses({"state": {"seq": 3}})
    append(companion, "eng-001", "eng-001-main", 3)
    assert flush(companion, "eng-001") is True
    assert is_caught_up(companion, "eng-001") is True
    assert state_of(companion, "eng-001", "eng-001-main")["seq"] == 3


def test_a_replayed_resume_keeps_closed_steps(companion, hx, companion_script):
    """spec 10 "Across `hx resume`", spec 13 M5: only `hx dispatch` archives state."""
    from .test_transitions import write_addendum

    companion_script.responses({"state": {
        "seq": 4, "goal": "stream the importer",
        "closed_steps": [{"id": "st1", "outcome": "read the importer", "verified": True}],
    }})
    append(companion, "eng-001", "eng-001-main", 4)
    wait_for_state(companion, "eng-001", "eng-001-main", 4)

    assert hx("complete", "decision", harness_id="eng-001").returncode == 0
    write_addendum(companion, "eng-001", "Use a generator, not a list.")
    assert hx("resume", "eng-001", "orders/eng-001.addendum.md", cwd=companion).returncode == 0

    state = state_of(companion, "eng-001", "eng-001-main")
    assert state["closed_steps"][0]["outcome"] == "read the importer"
    assert state["seq"] == 4


def test_the_final_pass_writes_the_digest_from_the_step_state(companion, hx, companion_script):
    companion_script.responses({"state": {
        "seq": 2, "goal": "stream the importer",
        "closed_steps": [{"id": "st1", "outcome": "rewrote read_all", "verified": True,
                          "commit": "abc1234"}],
        "open_steps": [{"id": "st2", "intent": "benchmark it", "next": "run the suite"}],
    }})
    append(companion, "eng-001", "eng-001-main", 2)
    wait_for_state(companion, "eng-001", "eng-001-main", 2)

    assert hx("complete", "done", harness_id="eng-001").returncode == 0
    digest = (companion / "pods" / "engineers" / "eng-001-complete.md").read_text().split("## Digest", 1)[1]
    assert "rewrote read_all" in digest and "abc1234" in digest and "benchmark it" in digest


def test_a_blocked_digest_leads_with_the_blocker(companion, hx, companion_script):
    """spec 10: the blocker first, so the Partner's addendum can answer it."""
    companion_script.responses({"state": {
        "seq": 1, "goal": "stream the importer",
        "blockers": ["the fixture file is not in the repo"],
        "closed_steps": [{"id": "st1", "outcome": "read it"}],
    }})
    append(companion, "eng-001", "eng-001-main", 1)
    wait_for_state(companion, "eng-001", "eng-001-main", 1)

    assert hx("complete", "blocked", harness_id="eng-001").returncode == 0
    body = (companion / "pods" / "engineers" / "eng-001-complete.md").read_text()
    assert body.split("## Digest", 1)[1].strip().startswith(
        "**Blocked on:** the fixture file is not in the repo")


# --- the sandbox directive (spec 11) -----------------------------------------------------------------------------


def test_the_companion_session_is_sandboxed(instance, hx, tmux_server):
    """spec 11, not negotiable: every claude process hx starts, the Companion included."""
    assert hx("launch", "eng-001").returncode == 0
    shown = subprocess.run(
        [*tmux_server, "show-environment", "-t", "=eng-001"], capture_output=True, text=True, check=True
    ).stdout
    assert "IS_SANDBOX=1" in shown


def test_doctor_warns_rather_than_failing_while_start_sh_has_not_execd(instance):
    """handoff/gtm-to-build.md gtm-8: the check races the launch, and a failure would be untrue."""
    from hx.doctor import FAIL, WARN, live_agent_checks

    import hx.doctor as doctor_mod

    original = doctor_mod.pane_command
    doctor_mod.pane_command = lambda item_id, env=None: "bash /srv/hx/adapters/claude/start.sh --exec eng-001"
    doctor_mod.session_environment = lambda item_id, env=None: {"IS_SANDBOX": "1"}
    try:
        statuses = [status for status, _ in live_agent_checks("eng-001")]
        assert FAIL not in statuses and WARN in statuses
    finally:
        doctor_mod.pane_command = original
