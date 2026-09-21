"""`hx compose` and the `context` hook — milestone M2 (spec 02, 07.3, 09.1, 13).

M2 pass criteria: on `startup`, `resume`, `clear` and `compact` the hook prints one path line;
the file holds memory, task, `## Tasks`, step state and open handles, in that order, and no
persona; the Partner's file also holds `PARTNER.md` and board output.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from .conftest import PERSONA, clean_env, wait_for

SECTION_ORDER = [
    "## Memory",
    "## Task",
    "## Tasks",
    "## Step state",
    "## Open subagent handles",
]

SOURCES = ["startup", "resume", "clear", "compact"]


def run_hook(instance, item_id, event, payload=None, *, tmux=None, harness_id=None, **env_extra):
    env = clean_env(HARNESS_ROOT=str(instance), **env_extra)
    if tmux:
        env["HX_TMUX"] = " ".join(tmux)
    if harness_id:
        env["HARNESS_ID"] = harness_id
    return subprocess.run(
        [sys.executable, "-m", "hx.hooks", "--id", item_id, event],
        input=json.dumps(payload or {}),
        env=env,
        capture_output=True,
        text=True,
    )


def context_file(instance, item_id):
    return instance / "run" / item_id / f"{item_id}-main.context.md"


def section_index(text, heading):
    assert heading in text, f"{heading} missing from the context file"
    return text.index(heading)


# --- hx compose --------------------------------------------------------------------------------


def test_the_sections_are_in_the_order_spec_07_3_fixes(instance, hx, launched, orders):
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, orders, order="Stream the importer.")
    result = hx("compose", "eng-001")
    assert result.returncode == 0, result.stderr
    path = context_file(instance, "eng-001")
    assert result.stdout.strip() == str(path)

    text = path.read_text()
    positions = [section_index(text, heading) for heading in SECTION_ORDER]
    assert positions == sorted(positions), f"sections out of order: {positions}"


def test_the_persona_is_never_in_the_context_file(instance, hx, launched, orders):
    """spec 02 Identity: the persona is system prompt, not context."""
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, orders)
    assert hx("compose", "eng-001").returncode == 0
    text = context_file(instance, "eng-001").read_text()

    persona_first_line = PERSONA.strip().split("\n")[0]
    assert persona_first_line not in text, "the persona leaked into the context file"
    assert "## UPDATES BELOW ONLY" not in text
    assert "Things I learned: nothing yet." in text, "the memory below the header is carried"


def test_each_section_names_the_file_it_came_from(instance, hx, launched, orders):
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, orders)
    assert hx("compose", "eng-001").returncode == 0
    text = context_file(instance, "eng-001").read_text()
    assert "_source: `config/eng-001/AGENTS.md`_" in text
    assert "_source: `pods/engineers/eng-001-working.md`_" in text


def test_the_task_section_carries_the_order_and_its_addenda(instance, hx, launched, orders):
    from .test_transitions import dispatch_working, write_addendum

    launched("eng-001")
    dispatch_working(instance, hx, orders, order="The original order.")
    assert hx("complete", "decision", harness_id="eng-001").returncode == 0
    write_addendum(instance, "eng-001", "And also handle the empty case.")
    assert hx("resume", "eng-001", "run/addendum-eng-001.md", cwd=instance).returncode == 0

    assert hx("compose", "eng-001").returncode == 0
    text = context_file(instance, "eng-001").read_text()
    task = text[section_index(text, "## Task"):section_index(text, "## Tasks")]
    assert "The original order." in task
    assert "And also handle the empty case." in task


def test_the_tasks_section_is_the_agents_own_list(instance, hx, launched, orders):
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, orders)
    work_item = instance / "pods" / "engineers" / "eng-001-working.md"
    work_item.write_text(work_item.read_text().replace("- [ ] …", "- [x] read it\n- [ ] rewrite it"))

    assert hx("compose", "eng-001").returncode == 0
    text = context_file(instance, "eng-001").read_text()
    tasks = text[section_index(text, "## Tasks"):section_index(text, "## Step state")]
    assert "- [x] read it" in tasks and "- [ ] rewrite it" in tasks


def test_step_state_is_rendered_not_dumped(instance, hx, launched, orders):
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, orders)
    state = instance / "state" / "eng-001"
    state.mkdir(parents=True, exist_ok=True)
    (state / "eng-001-main.json").write_text(json.dumps({
        "seq": 412,
        "goal": "stream the importer",
        "decisions": [{"d": "use a generator", "why": "memory", "ev": [401]}],
        "open_steps": [{"id": "st7", "intent": "rewrite read_all", "next": "delete the buffer", "ev": [398]}],
        "closed_steps": [{"id": "st6", "outcome": "read the importer", "verified": True, "commit": "abc1234"}],
        "dead_ends": ["mmap: the file is a stream"],
        "working_set": {"files": [{"path": "src/importer.py", "note": "read_all buffers"}],
                        "commits": [{"sha": "abc1234", "msg": "read the importer"}]},
    }))

    assert hx("compose", "eng-001").returncode == 0
    text = context_file(instance, "eng-001").read_text()
    rendered = text[section_index(text, "## Step state"):section_index(text, "## Open subagent handles")]
    assert "**Goal:** stream the importer" in rendered
    assert "use a generator — memory" in rendered
    assert "`st7` rewrite read_all" in rendered and "next: delete the buffer" in rendered
    assert "`st6` read the importer (verified) `abc1234`" in rendered
    assert "mmap: the file is a stream" in rendered
    assert "`src/importer.py` — read_all buffers" in rendered
    assert "_step state at seq 412_" in rendered
    assert '"open_steps"' not in rendered, "rendered as markdown, not dumped as JSON"


def test_step_state_says_none_yet_before_the_companion_lands(instance, hx, launched, orders):
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, orders)
    assert hx("compose", "eng-001").returncode == 0
    text = context_file(instance, "eng-001").read_text()
    rendered = text[section_index(text, "## Step state"):section_index(text, "## Open subagent handles")]
    assert "_none yet_" in rendered


def test_open_handles_list_the_running_subagents(instance, hx, launched, orders):
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, orders)
    logs = instance / "logs" / "eng-001"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "eng-001-s001-open.jsonl").write_text("")
    (logs / "eng-001-s002-closed.jsonl").write_text("")
    (instance / "run" / "eng-001" / "subagents.json").write_text(json.dumps({"agent-abc": "eng-001-s001"}))

    assert hx("compose", "eng-001").returncode == 0
    text = context_file(instance, "eng-001").read_text()
    handles = text[section_index(text, "## Open subagent handles"):]
    assert "`eng-001-s001`" in handles and "agent-abc" in handles
    assert "s002" not in handles, "a closed stream is not an open handle"


def test_a_subagent_stream_gets_subagents_md_and_no_tasks(instance, hx, launched, orders):
    """spec 07.3: SUBAGENTS.md whole for a subagent stream; `## Tasks` is main-stream only."""
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, orders)
    (instance / "config" / "eng-001" / "SUBAGENTS.md").write_text("You are a subagent of eng-001.\n")

    result = hx("compose", "eng-001", "eng-001-s001")
    assert result.returncode == 0, result.stderr
    text = (instance / "run" / "eng-001" / "eng-001-s001.context.md").read_text()
    assert "You are a subagent of eng-001." in text
    assert "## Tasks" not in text
    assert "## Who your subagents are" in text


def test_the_partner_file_holds_partner_md_and_the_board(instance, hx, launched, orders):
    launched("partner")
    (instance / "PARTNER.md").write_text("# Partner state\n\nTwo workers idle.\n")
    result = hx("compose", "partner")
    assert result.returncode == 0, result.stderr
    text = (instance / "run" / "partner" / "partner-main.context.md").read_text()
    assert "Two workers idle." in text
    assert "## Board" in text
    assert "eng-001" in text, "the board is a listing of the workers (spec 14 D25)"
    assert "partner" not in text.split("## Board", 1)[1], "the Partner is not an item"


def test_compose_of_an_unknown_id_is_not_found(instance, hx):
    result = hx("compose", "eng-404")
    assert result.returncode == 2 and "unknown id" in result.stderr


# --- the context hook ----------------------------------------------------------------------------


@pytest.mark.parametrize("source", SOURCES)
def test_the_hook_prints_exactly_one_path_line(instance, hx, launched, orders, tmux_server, source):
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, orders)
    result = run_hook(
        instance, "eng-001", "context",
        {"hook_event_name": "SessionStart", "source": source, "session_id": "s1",
         "cwd": str(instance / "wt" / "eng-001")},
        tmux=tmux_server,
    )
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.split("\n") if line.strip()]
    assert len(lines) == 1, f"expected one line, got {lines}"
    assert lines[0] == (
        f"Use the Read tool once on {context_file(instance, 'eng-001')} before anything else; "
        f"do not cat it and do not read it twice."
    )
    assert not lines[0].lstrip().startswith("{"), "never JSON, never a leading brace (spec 09.1)"
    assert context_file(instance, "eng-001").is_file()


@pytest.mark.parametrize("source", ["startup", "resume", "compact"])
def test_only_clear_sends_the_goal(instance, hx, launched, orders, tmux_server, source):
    """spec 09.1: `startup` and `resume` send none; `hx launch`/`hx restart` do that."""
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, orders)
    (instance / "run" / "eng-001" / "fake-input.log").write_text("")
    run_hook(instance, "eng-001", "context",
             {"source": source, "cwd": str(instance)}, tmux=tmux_server)
    assert "/goal" not in (instance / "run" / "eng-001" / "fake-input.log").read_text()


def test_clear_on_a_working_item_sends_the_goal(instance, hx, launched, orders, tmux_server):
    """spec 09.3 step 4: the `context` hook finishes the seam."""
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, orders)
    (instance / "run" / "eng-001" / "fake-input.log").write_text("")
    result = run_hook(instance, "eng-001", "context",
                      {"source": "clear", "cwd": str(instance)}, tmux=tmux_server)
    assert result.returncode == 0, result.stderr
    wait_for(
        lambda: "/goal The order for eng-001" in
        (instance / "run" / "eng-001" / "fake-input.log").read_text(),
        what="the pointer the clear hook sent",
    )


def test_clear_on_an_idle_item_sends_no_goal(instance, hx, launched, tmux_server):
    launched("eng-001")
    (instance / "run" / "eng-001" / "fake-input.log").write_text("")
    run_hook(instance, "eng-001", "context", {"source": "clear", "cwd": str(instance)}, tmux=tmux_server)
    assert "/goal" not in (instance / "run" / "eng-001" / "fake-input.log").read_text()


def test_the_hook_records_a_boundary_on_the_main_stream(instance, hx, launched, orders, tmux_server):
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, orders)
    run_hook(instance, "eng-001", "context",
             {"source": "compact", "transcript_path": "/tmp/t.jsonl", "cwd": str(instance)},
             tmux=tmux_server)

    from hx.streams import iter_records, main_stream

    records = list(iter_records(main_stream(instance, "eng-001")))
    assert records, "no boundary record"
    last = records[-1]
    assert last["event"] == "boundary" and last["source"] == "compact"
    assert last["stream"] == "eng-001-main" and last["seq"] >= 1
    assert last["ref"]["transcript"] == "/tmp/t.jsonl"


def test_the_partner_socket_file_is_written_from_the_environment(instance, hx, launched, tmux_server):
    """CONTRACTS.md `run/partner/socket.json`: exactly {socket, token, ts, session_id}."""
    launched("partner")
    result = run_hook(
        instance, "partner", "context",
        {"source": "startup", "session_id": "sess-42", "cwd": str(instance)},
        tmux=tmux_server,
        CLAUDE_CODE_MESSAGING_SOCKET="/tmp/cc-socks-501/partner.sock",
        CLAUDE_CODE_MESSAGING_TOKEN="tok-xyz",
    )
    assert result.returncode == 0, result.stderr
    recorded = json.loads((instance / "run" / "partner" / "socket.json").read_text())
    assert set(recorded) == {"socket", "token", "ts", "session_id"}
    assert recorded["socket"] == "/tmp/cc-socks-501/partner.sock"
    assert recorded["token"] == "tok-xyz"
    assert recorded["session_id"] == "sess-42"
    assert recorded["ts"].endswith("Z")

    from hx.wake import read_socket

    assert read_socket(instance) == ("/tmp/cc-socks-501/partner.sock", "tok-xyz")


def test_no_socket_file_for_a_worker(instance, hx, launched, tmux_server):
    launched("eng-001")
    run_hook(instance, "eng-001", "context", {"source": "startup", "cwd": str(instance)},
             tmux=tmux_server, CLAUDE_CODE_MESSAGING_SOCKET="/tmp/x.sock")
    assert not (instance / "run" / "eng-001" / "socket.json").exists()
    assert not (instance / "run" / "partner" / "socket.json").exists()


# --- hx-hook plumbing (goal build-3 item 4) ----------------------------------------------------


def test_a_malformed_payload_is_logged_and_allowed(instance, hx, launched, tmux_server):
    launched("eng-001")
    result = subprocess.run(
        [sys.executable, "-m", "hx.hooks", "--id", "eng-001", "context"],
        input="{not json",
        env=clean_env(HARNESS_ROOT=str(instance), HX_TMUX=" ".join(tmux_server)),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, "a broken hook must never stop the agent"
    log = instance / "logs" / "eng-001" / "hook-errors.log"
    assert log.is_file() and "context" in log.read_text()


def test_a_malformed_payload_denies_for_guard(instance, hx, launched):
    """The one exception: hx could not prove the call was safe (spec 09.2)."""
    launched("eng-001")
    result = subprocess.run(
        [sys.executable, "-m", "hx.hooks", "--id", "eng-001", "guard"],
        input="{not json",
        env=clean_env(HARNESS_ROOT=str(instance)),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert result.stdout == ""


def test_the_id_must_match_harness_id(instance, hx, launched, tmux_server):
    launched("eng-001")
    result = run_hook(instance, "eng-001", "context", {"source": "startup"},
                      tmux=tmux_server, harness_id="eng-002")
    assert result.returncode == 0, "still allows: a hook never stops the agent"
    log = instance / "logs" / "eng-001" / "hook-errors.log"
    assert "does not match HARNESS_ID eng-002" in log.read_text()
    assert result.stdout == "", "and it composed nothing"


def test_a_matching_harness_id_is_fine(instance, hx, launched, tmux_server):
    launched("eng-001")
    result = run_hook(instance, "eng-001", "context", {"source": "startup", "cwd": str(instance)},
                      tmux=tmux_server, harness_id="eng-001")
    assert result.returncode == 0 and result.stdout.startswith("Use the Read tool once on ")


def test_every_spec_09_event_is_implemented(instance, hx, launched):
    """build-8 item 3 closed the last two: `precompact` and `postcompact` are log-only."""
    from hx.hooks import EVENTS, IMPLEMENTED

    assert set(EVENTS) == set(IMPLEMENTED), set(EVENTS) ^ set(IMPLEMENTED)

    launched("eng-001")
    result = run_hook(instance, "eng-001", "precompact", {})
    assert result.returncode == 0
    assert "not implemented" not in result.stderr
