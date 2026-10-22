"""The Partner's Companion as one Messages API exchange per pass (spec 10, 07.2, 07.4, 13 M5).

There is no Companion session and no pane: a pass sends the cached system block, the stream's
prior state and its new records, and installs the state the model submits through
`submit_state`. The responses here are recorded Messages API bodies replayed in-process.
"""

from __future__ import annotations

import json
import os
import time

import pytest

from .companion_http import api_key, replay, silent
from .test_compose import run_hook
from .test_streams import post_tool, transcript_with


@pytest.fixture(autouse=True)
def private_companion_transport(monkeypatch, tmux_server, instance):
    """Even state-only cases must never probe an operator's default Partner."""
    monkeypatch.setenv('HX_TMUX', ' '.join(tmux_server))
    api_key(instance)


def state_of(instance, item_id, stream):
    from hx.companion import state_path
    from hx.stepstate import load

    return load(state_path(instance, item_id, stream))


def append(instance, item_id, stream, count, **extra):
    """Append records, which is what the `log` hook does (spec 10)."""
    from hx.streams import append_record

    for _ in range(count):
        append_record(instance, item_id, stream, {"event": "post_tool", "tool": "Bash", **extra})


def full(**fields):
    """A complete submitted state: every key the tool's schema requires."""
    return {"goal": "", "constraints": [], "decisions": [], "open_steps": [], "closed_steps": [],
            "dead_ends": [], "working_set": {}, "blockers": [], "subagents_open": [], **fields}


def run(instance, *steps, stream="partner-main", **inputs):
    from hx.companion import run_pass

    with replay(*steps, **inputs) as sent:
        result = run_pass(instance, "partner", stream)
    return result, sent


def errors(instance):
    path = instance / "logs" / "partner" / "hook-errors.log"
    return path.read_text() if path.is_file() else ""


def _disable_companion(instance, item_id="partner"):
    harness = instance / "config" / item_id / "harness.json"
    body = json.loads(harness.read_text())
    body["companion"] = {**body.get("companion", {}), "disabled": True}
    harness.write_text(json.dumps(body))


def _companion(instance, **fields):
    harness = instance / "config" / "partner" / "harness.json"
    config = json.loads(harness.read_text())
    config["companion"] = {**config.get("companion", {}), **fields}
    harness.write_text(json.dumps(config))


# --- the exchange -------------------------------------------------------------------------------


def test_disabled_companion_takes_no_pass_and_stays_caught_up(instance):
    """`companion.disabled`: no stream is due, no request is sent, seams stay takeable."""
    from hx.companion import is_caught_up, is_disabled, run_due

    _disable_companion(instance)
    assert is_disabled(instance, "partner") is True
    append(instance, "partner", "partner-main", 1)
    with silent():
        assert run_due(instance, "partner", force=True) == []
    assert is_caught_up(instance, "partner") is True


def test_the_system_block_is_composed_and_byte_stable(instance):
    """spec 10: BASE.md, the role file and the harness facts — the cached prefix, and only it."""
    from hx.companion import system_prompt, config_for

    append(instance, "partner", "partner-main", 3)
    result, sent = run(instance, "submit-state", state={"state": full(goal="first")})
    assert result["status"] == "committed"
    append(instance, "partner", "partner-main", 2)
    _, again = run(instance, "submit-state", state={"state": full(goal="second")})
    text = system_prompt(instance, config_for(instance, "partner"))
    assert "# Companion" in text, "BASE.md"
    assert "- id: `partner`" in text and "- role: `partner`" in text and "state budget:" in text
    assert "seam policy" not in text
    for request in (*sent, *again):
        assert request["body"]["system"] == [{"type": "text", "text": text, "cache_control": {"type": "ephemeral"}}]
        assert [tool["name"] for tool in request["body"]["tools"]] == ["submit_state"]


def test_there_is_no_companion_session_left(instance):
    """No pane, no pass file, no wake: a pass is a request."""
    from hx import companion as companion_mod

    for name in ("launch", "window", "write_pass", "wake", "ingest", "wake_due", "POINTER", "companion_home"):
        assert not hasattr(companion_mod, name), name
    append(instance, "partner", "partner-main", 1)
    run(instance, "submit-state", state={"state": full()})
    assert not (instance / "run" / "partner" / "companion").exists()
    assert not (instance / "run" / "partner" / "companion-home").exists()


def test_the_user_turn_names_the_cursor_and_carries_whole_records(instance, tmp_path):
    run_hook(instance, "partner", "subagent-start",
             {"hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "Explore"})
    append(instance, "partner", "partner-main", 3, input="first")
    _, sent = run(instance, "submit-state", state={"state": full(goal="g")})
    user = sent[0]["body"]["messages"][0]["content"][0]["text"]
    assert "stream: partner-main (main)" in user and "from_seq: 0" in user
    assert "open_subagents: s001" in user
    assert "none: this is the first pass for this stream" in user
    head = state_of(instance, "partner", "partner-main")["seq"]
    assert f"through_seq: {head}" in user and user.count('"input":"first"') == 3

    append(instance, "partner", "partner-main", 2, input="second")
    _, sent = run(instance, "submit-state", state={"state": full(goal="g")})
    user = sent[0]["body"]["messages"][0]["content"][0]["text"]
    assert f"from_seq: {head}" in user and '"input":"first"' not in user and user.count('"input":"second"') == 2
    assert '"goal":"g"' in user and '"seq"' not in user.split("## New records")[0]


def test_the_submitted_state_is_installed_and_stamped(instance):
    append(instance, "partner", "partner-main", 3)
    result, sent = run(instance, "submit-state", state={"state": full(
        goal="stream the importer",
        closed_steps=[{"id": "st1", "outcome": "read it", "verified": True, "commit": "", "ev": [1]}])})
    assert result["status"] == "committed" and len(sent) == 1
    state = state_of(instance, "partner", "partner-main")
    assert state["seq"] == 3 and state["goal"] == "stream the importer"
    assert set(state["prompt_version"]) == {"base", "role"} and all(state["prompt_version"].values())
    assert state["ts"].endswith("Z")
    (line,) = (instance / "run" / "partner" / "companion-usage.jsonl").read_text().splitlines()
    usage = json.loads(line)
    assert (usage["from_seq"], usage["through_seq"], usage["outcome"]) == (0, 3, "submitted")
    assert usage["usage"][0]["cache_read_input_tokens"] == 9216


def test_hx_owns_the_cursor_even_when_the_model_names_one(instance):
    append(instance, "partner", "partner-main", 5)
    run(instance, "submit-state", state={"state": full(goal="g")})
    assert state_of(instance, "partner", "partner-main")["seq"] == 5


def test_a_closed_subagent_stream_submits_its_digest(instance):
    from hx.companion import digest_path

    run_hook(instance, "partner", "subagent-start",
             {"hook_event_name": "SubagentStart", "agent_id": "a1", "agent_type": "Explore"})
    run_hook(instance, "partner", "subagent-stop",
             {"hook_event_name": "SubagentStop", "agent_id": "a1", "agent_type": "Explore"})
    from hx.companion import due_streams
    (stream,) = [s for s in due_streams(instance, "partner") if s != "partner-main"]
    result, sent = run(instance, "submit-state", stream=stream,
                       state={"state": full(goal="explore"), "digest": "Read the parser; committed nothing; left the importer unproven."})
    assert result["status"] == "committed"
    assert "subagent, closed: also submit its digest" in sent[0]["body"]["messages"][0]["content"][0]["text"]
    assert "importer unproven" in digest_path(instance, "partner", stream).read_text()


# --- the validator -------------------------------------------------------------------------------


def test_invalid_state_is_retried_once_then_the_prior_state_stands(instance):
    """spec 10, 13 M5: "invalid output keeps prior state"."""
    append(instance, "partner", "partner-main", 3)
    run(instance, "submit-state", state={"state": full(goal="the good one")})
    good = state_of(instance, "partner", "partner-main")

    append(instance, "partner", "partner-main", 3)
    result, sent = run(instance, "submit-state", "resubmit-state",
                       state={"state": {"goal": "x", "zz": 1}}, retry={"state": {"decisions": [{"d": 1}]}})
    assert result["status"] == "unresolved" and len(sent) == 2
    assert state_of(instance, "partner", "partner-main") == good
    assert "not installed" in errors(instance) and "the prior state stands" in errors(instance)


def test_the_retry_carries_the_reason_in_the_same_exchange(instance):
    append(instance, "partner", "partner-main", 3)
    result, sent = run(instance, "submit-state", "resubmit-state",
                       state={"state": {"decisions": [{"d": 1}]}}, retry={"state": full(goal="fixed")})
    assert result["status"] == "committed" and state_of(instance, "partner", "partner-main")["goal"] == "fixed"
    (rejection,) = sent[1]["body"]["messages"][2]["content"]
    assert rejection["is_error"] and rejection["tool_use_id"] == "toolu_01SubmitState"
    assert rejection["content"]


def test_a_failed_request_keeps_the_prior_state(instance):
    append(instance, "partner", "partner-main", 3)
    result, _ = run(instance, "overloaded")
    assert result["status"] == "unresolved" and "529" in result["error"]
    assert state_of(instance, "partner", "partner-main") is None


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


def test_the_budget_is_enforced_deterministically(instance):
    from hx.stepstate import estimate_tokens

    _companion(instance, state_budget_tokens=200)
    append(instance, "partner", "partner-main", 3)
    run(instance, "submit-state", state={"state": full(
        goal="g",
        dead_ends=["d" * 600, "e" * 600],
        closed_steps=[{"id": "st1", "outcome": "x" * 600, "verified": False, "commit": "", "ev": []}],
        working_set={"dirty": ["z" * 600], "files": [{"path": "a.py", "note": "n" * 600}]})})
    state = state_of(instance, "partner", "partner-main")
    assert estimate_tokens(state) <= 250, estimate_tokens(state)
    assert "over budget, evicted" in errors(instance)


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


def test_a_500_record_replay_stays_within_budget(instance):
    """spec 13 M5. The log is synthesised: the build-5 live run recorded only a few records."""
    from hx.companion import due_streams
    from hx.stepstate import estimate_tokens

    _companion(instance, batch_records=20, state_budget_tokens=2000)
    sizes = []
    for batch in range(1, 26):
        append(instance, "partner", "partner-main", 20)
        assert due_streams(instance, "partner") == ["partner-main"]
        run(instance, "submit-state", state={"state": full(
            goal="stream the importer",
            closed_steps=[{"id": f"st{n}", "outcome": f"did thing {n}" * 6, "verified": True, "commit": "", "ev": []}
                          for n in range(1, batch + 1)],
            dead_ends=[f"dead end {n}" * 8 for n in range(1, batch + 1)],
            working_set={"files": [{"path": f"f{n}.py", "note": "note" * 20} for n in range(1, batch + 1)]})})
        sizes.append(estimate_tokens(state_of(instance, "partner", "partner-main")))
    assert state_of(instance, "partner", "partner-main")["seq"] == 500
    assert max(sizes) <= 2050, f"peak {max(sizes)} tokens"


# --- FIFO retention ------------------------------------------------------------------------------------


def test_truncation_never_drops_records_at_or_ahead_of_the_cursor(instance):
    """spec 07.1: never below `state.seq − keep_behind`."""
    from hx.streams import iter_records, main_stream, truncate

    append(instance, "partner", "partner-main", 400)
    from hx import learning_companion
    from hx.continuity_store import ContinuityStore
    with ContinuityStore(instance) as ledger:
        learning_companion.register(ledger)
        with ledger.transaction() as tx:
            tx.db.execute("UPDATE learning_cursors SET assessed_seq=350 WHERE agent_id='partner' AND stream_id='partner-main'")
    path = main_stream(instance, "partner")
    truncate(path, state_seq=350, max_records=100, keep_behind=20)

    kept = [r["seq"] for r in iter_records(path)]
    assert max(kept) == 400
    assert set(range(331, 401)) <= set(kept), "nothing at or ahead of the floor is dropped"
    assert len(kept) < 400, "and the stream did shrink"


def test_truncation_keeps_everything_when_the_cursor_is_behind(instance):
    from hx.streams import iter_records, main_stream, truncate

    append(instance, "partner", "partner-main", 200)
    path = main_stream(instance, "partner")
    truncate(path, state_seq=5, max_records=50, keep_behind=100)
    assert len(list(iter_records(path))) == 200


# --- flush and the loop -------------------------------------------------------------------------------


def test_flush_runs_the_owed_passes_inline(instance):
    from hx.companion import is_caught_up
    from hx.flush import flush

    append(instance, "partner", "partner-main", 3)
    with replay("submit-state", state={"state": full(goal="g")}):
        assert flush(instance, "partner") is True
    assert is_caught_up(instance, "partner") is True
    assert state_of(instance, "partner", "partner-main")["seq"] == 3


def test_the_loop_selects_a_due_stream_and_a_failed_window_waits(instance):
    from hx import companion as companion_mod
    from hx.continuity_store import ContinuityStore

    _companion(instance, batch_records=2)
    append(instance, "partner", "partner-main", 2)
    with ContinuityStore(instance) as ledger:
        candidate = companion_mod.select(ledger)
        assert candidate["stream_id"] == "partner-main"
        with replay("overloaded"):
            assert companion_mod.assess(ledger, candidate)["status"] == "unresolved"
        assert companion_mod.select(ledger) is None, "the same window is not retried"
        append(instance, "partner", "partner-main", 1)
        retried = companion_mod.select(ledger)
        assert retried["stream_id"] == "partner-main"
        with replay("submit-state", state={"state": full()}):
            assert companion_mod.assess(ledger, retried)["status"] == "committed"


# --- the seam policy (spec 10, fleet-throughput §4.4) ---------------------------------------------------


def _seam_profile(instance, **values):
    path = instance / "config" / "hx.json"
    data = json.loads(path.read_text()) if path.is_file() else {}
    data["seam"] = {"soft_fraction": 0.5, "cache_ttl_s": 300, "min_benefit_bytes": 0, **values}
    path.write_text(json.dumps(data))


def _stop(instance, monkeypatch, tmp_path, **usage):
    from hx import hook_stop, seam

    taken = []
    monkeypatch.setattr(seam, "pane_ready", lambda *a, **kw: True)
    monkeypatch.setattr(seam.goal_mod, "paste", lambda *a, **kw: taken.append(a))
    transcript = transcript_with(tmp_path, **usage)
    assert hook_stop.handle({"session_id": "s", "transcript_path": str(transcript)}, "partner", instance) == (0, "")
    return taken


def _seams(instance):
    from hx.streams import iter_records, main_stream

    return [r for r in iter_records(main_stream(instance, "partner")) if r.get("event") == "seam"]


def test_a_warm_partner_turn_holds_the_seam(instance, monkeypatch, tmp_path):
    _seam_profile(instance)
    run_hook(instance, "partner", "log", post_tool(transcript=transcript_with(tmp_path, input_tokens=1000)))
    taken = _stop(instance, monkeypatch, tmp_path, input_tokens=200, cache_read=119800, cache_creation=500)
    assert not taken and not _seams(instance)
    from hx.streams import iter_records, main_stream

    # Stop records the turn's last response: the log hook fires per tool call and never sees it.
    last = list(iter_records(main_stream(instance, "partner")))[-1]
    assert {key: last[key] for key in ("event", "context_tokens", "cache_read", "cache_creation")} == {
        "event": "turn_end", "context_tokens": 120500, "cache_read": 119800, "cache_creation": 500}


def test_a_cold_miss_partner_turn_takes_the_opportunistic_seam(instance, monkeypatch, tmp_path):
    _seam_profile(instance)
    run_hook(instance, "partner", "log", post_tool(transcript=transcript_with(tmp_path, input_tokens=1000)))
    taken = _stop(instance, monkeypatch, tmp_path, input_tokens=200, cache_read=0, cache_creation=120000)
    assert taken and taken[0][1] == "/clear"
    (record,) = _seams(instance)
    assert record["reason"] == "cold_miss"


def test_a_cold_miss_below_the_soft_fraction_holds(instance, monkeypatch, tmp_path):
    _seam_profile(instance)
    run_hook(instance, "partner", "log", post_tool(transcript=transcript_with(tmp_path, input_tokens=1000)))
    taken = _stop(instance, monkeypatch, tmp_path, input_tokens=200, cache_read=0, cache_creation=50000)
    assert not taken and not _seams(instance)


def test_the_hard_threshold_seams_whatever_the_cache(instance, monkeypatch, tmp_path):
    from hx.hook_log import seam_marker

    _seam_profile(instance)
    run_hook(instance, "partner", "log", post_tool(transcript=transcript_with(tmp_path, cache_read=210000)))
    assert seam_marker(instance, "partner").exists()
    taken = _stop(instance, monkeypatch, tmp_path, cache_read=210000)
    assert taken and _seams(instance)[0]["reason"] == "hard"


def test_an_idle_cold_partner_seams_only_on_an_empty_prompt(instance, monkeypatch, tmp_path):
    from hx import goal, seam, streams, token_controls

    _seam_profile(instance, cache_ttl_s=60)
    run_hook(instance, "partner", "log", post_tool(transcript=transcript_with(tmp_path, input_tokens=1000)))
    run_hook(instance, "partner", "log", post_tool(transcript=transcript_with(tmp_path, cache_read=150000)))
    path = streams.main_stream(instance, "partner")
    pasted = []
    monkeypatch.setattr(seam, "pane_ready", lambda *a, **kw: True)
    monkeypatch.setattr(seam.goal_mod, "paste", lambda *a, **kw: pasted.append(a))
    screens = {"typing": "❯ half a sentence\n", "empty": "❯ \n"}
    monkeypatch.setattr(goal, "capture_pane", lambda *a, **kw: screens[shown[0]])

    shown = ["empty"]
    assert token_controls.partner_reset(instance) is None, "within the cache lifetime: warm"
    stale = time.time() - 120
    os.utime(path, (stale, stale))
    record = json.loads(path.read_text().splitlines()[-1])
    record["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(stale))
    path.write_text("\n".join([*path.read_text().splitlines()[:-1], json.dumps(record)]) + "\n")
    os.utime(path, (stale, stale))

    shown = ["typing"]
    assert token_controls.partner_reset(instance) is None, "the human is typing"
    shown[0] = "empty"
    result = token_controls.partner_reset(instance)
    assert result["status"] == "partner_seam" and result["seam"] == token_controls.COLD_IDLE
    assert pasted and _seams(instance)[0]["reason"] == "cold_idle"


def test_a_pending_seam_never_waits_on_the_companion(instance, monkeypatch):
    """spec 14 D14, fleet-throughput §4.4: the Partner seams like any agent — `/clear` plus its
    context file — and a Companion behind its stream head does not hold it."""
    from hx import hook_stop, streams, seam
    from hx.companion import is_caught_up
    from hx.hook_log import seam_marker

    monkeypatch.setattr(seam, 'pane_ready', lambda *a, **kw: True)
    pasted: list[tuple] = []
    monkeypatch.setattr(seam.goal_mod, 'paste', lambda *a, **kw: pasted.append(a))
    streams.append_record(instance, "partner", "partner-main",
                          {"event": "post_tool", "tool": "Bash", "input": "ls", "output": "a", "context_tokens": 1000})
    assert not is_caught_up(instance, "partner")
    marker = seam_marker(instance, "partner")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    with silent():
        assert hook_stop.handle({"session_id": "s"}, "partner", instance, env=None) == (0, "")
    assert not marker.exists(), "the seam was taken"
    assert pasted and pasted[0][1] == "/clear"
    assert _seams(instance)[-1]["reason"] == "hard"


def test_a_menu_still_defers_the_seam(instance, monkeypatch):
    from hx import hook_stop, streams, seam
    from hx.hook_log import seam_marker
    from hx.streams import iter_records, stream_path

    monkeypatch.setattr(seam, 'pane_ready', lambda *a, **kw: False)
    marker = seam_marker(instance, "partner")
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
    assert hook_stop.handle({"session_id": "s"}, "partner", instance, env=None) == (0, "")
    assert marker.exists(), "not ready: the marker stays for the next boundary"
    records = list(iter_records(stream_path(instance, "partner", "partner-main")))
    assert records[-1]["event"] == "seam_waiting" and records[-1]["ready"] == "menu"


# --- the sandbox and doctor ------------------------------------------------------------------------------


def test_doctor_warns_rather_than_failing_while_start_sh_has_not_execd(instance):
    """handoff/gtm-to-build.md gtm-8: the check races the launch, and a failure would be untrue."""
    from hx.doctor import FAIL, WARN, live_agent_checks

    import hx.doctor as doctor_mod

    original = doctor_mod.pane_command
    doctor_mod.pane_command = lambda item_id, env=None: "bash /srv/hx/adapters/claude/start.sh --exec partner"
    doctor_mod.session_environment = lambda item_id, env=None: {"IS_SANDBOX": "1"}
    try:
        statuses = [status for status, _ in live_agent_checks("partner")]
        assert FAIL not in statuses and WARN in statuses
    finally:
        doctor_mod.pane_command = original


@pytest.mark.parametrize('operation', ['run_pass', 'due_streams', 'run_due'])
def test_worker_file_companion_execution_is_refused_before_mutation(tmp_path, operation):
    from hx import companion
    from hx.errors import Refused
    root = tmp_path / 'worker-root'
    root.mkdir()
    args = (root, 'eng-001')
    if operation == 'run_pass':
        args += ('eng-001-main',)
    with pytest.raises(Refused, match='ledger run'):
        getattr(companion, operation)(*args)
    assert list(root.iterdir()) == []


@pytest.mark.parametrize('module,operation', [
    ('flush', 'flush'), ('seam', 'seam'), ('hook_stop', 'handle'),
    ('hook_compact', 'pre'), ('hook_compact', 'post'), ('hook_context', 'handle'),
])
def test_worker_cannot_enter_partner_orchestration_handlers(tmp_path, module, operation):
    import importlib
    from hx.errors import Refused
    handler = getattr(importlib.import_module('hx.' + module), operation)
    root = tmp_path / 'worker-root'
    root.mkdir()
    args = ({'background_tasks': ['still-running']}, 'eng-001', root) if module.startswith('hook_') else (root, 'eng-001')
    with pytest.raises(Refused):
        handler(*args, env={})
    assert list(root.iterdir()) == []
