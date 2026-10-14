"""`hx compose` and the `context` hook — milestone M2 (spec 02, 07.3, 09.1, 13).

The main stream has no context file: `hx compose <id>` rewrites the work item's `## Status`
(`PARTNER.md`'s for the Partner) and the `context` hook hands the agent its chain — persona,
identity line, rules — ending in one pointer to that file (`hx.persona.boundary`, whose order
and channels tests/core/test_persona.py covers). A subagent stream still gets one context file.
"""

from __future__ import annotations

import json
import subprocess
import sys

import pytest

from hx.hook_context import CONTEXT_LINE
from hx.persona import IDLE_LINE

from .conftest import PERSONA, clean_env, wait_for

STATUS_PARTS = ["### Step state", "### Memory episodes", "### Open subagent handles"]

SUBAGENT_SECTIONS = [
    "## Who your subagents are",
    "## Task",
    "## Step state",
    "## Memory episodes",
    "## Open subagent handles",
]

SOURCES = ["startup", "resume", "clear", "compact"]


def _set_flavor(instance, item_id, flavor):
    harness = instance / "config" / item_id / "harness.json"
    body = json.loads(harness.read_text())
    body["flavor"] = flavor
    harness.write_text(json.dumps(body))


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


def work_item(instance, item_id="eng-001"):
    return instance / "pods" / "engineers" / f"{item_id}-working.md"


def old_context_file(instance, item_id):
    return instance / "run" / item_id / f"{item_id}-main.context.md"


def subagent_file(instance, stream, item_id="eng-001"):
    return instance / "run" / item_id / f"{stream}.context.md"


def status_of(path):
    """The `## Status` section of a work item or PARTNER.md, up to the next `##`."""
    body = path.read_text()
    assert "\n## Status\n" in body, f"no ## Status in {path.name}"
    section = body.split("\n## Status\n", 1)[1]
    following = section.find("\n## ")
    return section if following < 0 else section[:following]


def part(status, title):
    """One `###` part of a status section."""
    start = status.index(f"### {title}\n")
    following = status.find("\n### ", start + 1)
    return status[start:] if following < 0 else status[start:following]


def section_index(text, heading):
    assert heading in text, f"{heading} missing"
    return text.index(heading)


# --- hx compose: the main stream -----------------------------------------------------------------


def test_compose_writes_the_status_into_the_working_item_and_names_it(instance, hx, launched, goals):
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, goals, goal="Stream the importer.")
    result = hx("compose", "eng-001")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(work_item(instance))
    assert not old_context_file(instance, "eng-001").exists(), "the main stream has no context file"
    status = status_of(work_item(instance))
    positions = [section_index(status, heading + "\n") for heading in STATUS_PARTS]
    assert positions == sorted(positions), f"status parts out of order: {positions}"


def test_the_main_stream_has_no_context_text(instance, launched):
    from hx import compose
    from hx.errors import ValidationError

    launched("eng-001")
    with pytest.raises(ValidationError, match="main stream has no context file"):
        compose.compose_text(instance, "eng-001", "eng-001-main")


def test_status_is_written_into_the_working_item_after_tasks(instance, hx, launched, goals):
    """Session memory is the work item: the agent keeps `## Tasks`, hx rewrites `## Status`
    at every boundary."""
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, goals)
    item = work_item(instance)
    item.write_text(item.read_text().replace("- [ ] …", "- [x] read it\n- [ ] rewrite it"))
    state = instance / "state" / "eng-001"
    state.mkdir(parents=True, exist_ok=True)
    (state / "eng-001-main.json").write_text(json.dumps({"seq": 7, "goal": "stream the importer"}))
    logs = instance / "logs" / "eng-001"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "eng-001-s001-open.jsonl").write_text("")

    assert hx("compose", "eng-001").returncode == 0
    body = item.read_text()
    assert body.count("\n## Status\n") == 1
    assert body.index("## Tasks") < body.index("## Status") < body.index("## Deliverables")
    status = body[body.index("## Status"):body.index("## Deliverables")]
    assert "<rewritten by hx" not in status, "the template's placeholder is replaced"
    assert "### Step state\n\ngoal: stream the importer" in status
    assert "### Memory episodes" in status and "`eng-001-s001`" in status
    assert "- [x] read it\n- [ ] rewrite it" in body, "the agent's half is untouched"

    before = item.stat().st_mtime_ns
    assert hx("compose", "eng-001").returncode == 0
    assert item.read_text() == body and item.stat().st_mtime_ns == before, \
        "an unchanged status is not rewritten"
    (state / "eng-001-main.json").write_text(json.dumps({"seq": 8, "goal": "stream it in chunks"}))
    assert hx("compose", "eng-001").returncode == 0
    body = item.read_text()
    assert body.count("\n## Status\n") == 1 and "stream it in chunks" in body
    assert "stream the importer" not in body.split("## Status", 1)[1]


def test_status_is_only_written_into_a_working_item(instance, hx, launched, goals):
    """An idle item has no task to remember, and a completed item is a record."""
    launched("eng-001")
    idle = instance / "pods" / "engineers" / "eng-001-idle.md"
    before = idle.read_text() if idle.exists() else None
    result = hx("compose", "eng-001")
    assert result.returncode == 0, result.stderr
    assert "no task in progress" in result.stdout
    assert (idle.read_text() if idle.exists() else None) == before
    assert not old_context_file(instance, "eng-001").exists()


def test_step_state_is_rendered_not_dumped(instance, hx, launched, goals):
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, goals)
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
    rendered = part(status_of(work_item(instance)), "Step state")
    # One tagged line per fact: the section is read by a model at every boundary and every
    # heading, bullet and blank line in it is paid for again (tests/core/test_density.py).
    assert "goal: stream the importer" in rendered
    assert "dec: use a generator <- memory [401]" in rendered
    assert "open st7: rewrite read_all [398]" in rendered
    assert "next st7: delete the buffer" in rendered
    assert "done st6 verified abc1234: read the importer" in rendered
    assert "dead: mmap: the file is a stream" in rendered
    assert "file src/importer.py: read_all buffers" in rendered
    assert "commit abc1234: read the importer" in rendered
    assert "seq 412" in rendered
    assert '"open_steps"' not in rendered, "rendered as lines, not dumped as JSON"


def test_step_state_says_none_yet_before_the_companion_lands(instance, hx, launched, goals):
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, goals)
    assert hx("compose", "eng-001").returncode == 0
    assert "_none yet_" in part(status_of(work_item(instance)), "Step state")


def test_open_handles_list_the_running_subagents(instance, hx, launched, goals):
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, goals)
    logs = instance / "logs" / "eng-001"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "eng-001-s001-open.jsonl").write_text("")
    (logs / "eng-001-s002-closed.jsonl").write_text("")
    (instance / "run" / "eng-001" / "subagents.json").write_text(json.dumps({"agent-abc": "eng-001-s001"}))

    assert hx("compose", "eng-001").returncode == 0
    handles = part(status_of(work_item(instance)), "Open subagent handles")
    assert "`eng-001-s001`" in handles and "agent-abc" in handles
    assert "s002" not in handles, "a closed stream is not an open handle"


def test_the_memory_episodes_part_is_there_before_a_single_episode_exists(
    instance, hx, launched, goals
):
    """It is composed on an instance with no chroma store at all, and says so like any other
    empty part — the agent must never have to wonder whether hx failed to compose it."""
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, goals, goal="Stream the importer.")
    assert hx("compose", "eng-001").returncode == 0
    assert "_none yet_" in part(status_of(work_item(instance)), "Memory episodes")
    assert not (instance / "state" / "memory" / "chroma").exists(), (
        "an empty store costs no chroma open at all"
    )


def test_the_goal_memory_asks_about_carries_the_order_and_its_addenda(instance, hx, launched, goals):
    from hx import compose

    from .test_transitions import dispatch_working, write_addendum

    launched("eng-001")
    dispatch_working(instance, hx, goals, goal="The original order.")
    assert hx("complete", "decision", harness_id="eng-001").returncode == 0
    write_addendum(instance, "eng-001", "And also handle the empty case.")
    assert hx("resume", "eng-001", "run/addendum-eng-001.md", cwd=instance).returncode == 0
    _source, text = compose.task(instance, "eng-001")
    assert "The original order." in text and "And also handle the empty case." in text


def test_a_legacy_memory_file_never_leaks(instance, hx, launched, goals):
    """No agent keeps a memory file: a lesson is a learnings proposal. A `config/<id>/AGENTS.md`
    an older hx left (before `hx launch` migrates it) reaches nothing."""
    from hx import persona

    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, goals)
    (instance / "config" / "eng-001" / "AGENTS.md").write_text(
        "persona\n\n## UPDATES BELOW ONLY\n\nOLD PRIVATE NOTE\n")
    assert hx("compose", "eng-001").returncode == 0
    assert "OLD PRIVATE NOTE" not in work_item(instance).read_text()
    assert "OLD PRIVATE NOTE" not in persona.boundary(instance, "eng-001")["text"]


def test_the_partner_status_goes_into_partner_md(instance, hx, launched):
    launched("partner")
    partner_md = instance / "PARTNER.md"
    partner_md.write_text("# Partner state\n\n## Notes\n\n- two workers idle\n")
    state = instance / "state" / "partner"
    state.mkdir(parents=True, exist_ok=True)
    (state / "partner-main.json").write_text(json.dumps({"seq": 3, "goal": "ship the importer"}))
    result = hx("compose", "partner")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(partner_md)
    body = partner_md.read_text()
    assert body.index("## Notes") < body.index("## Status")
    assert "### Step state\n\ngoal: ship the importer" in body
    assert "### Memory episodes" not in body, "the Partner gets no episodes"
    assert not old_context_file(instance, "partner").exists()


def test_compose_of_an_unknown_id_is_not_found(instance, hx):
    result = hx("compose", "eng-404")
    assert result.returncode == 2 and "unknown id" in result.stderr


def test_with_status_places_replaces_and_keeps_everything_else():
    from hx.compose import with_status

    item = "---\nid: eng-001\n---\nGoal.\n\n## Tasks\n- [ ] a\n```\n## Status\n```\n\n## Commands\n"
    once = with_status(item, "first")
    assert once.startswith("---\nid: eng-001\n---\n")
    assert once.index("- [ ] a") < once.index("\n## Status\n\nfirst\n") < once.index("## Commands")
    assert "```\n## Status\n```" in once, "a heading inside a fence is not the section"
    assert with_status(once, "first") == once
    twice = with_status(once, "second")
    assert "first" not in twice and twice.count("\n## Status\n\n") == 1
    assert with_status("No sections.\n", "s") == "No sections.\n\n## Status\n\ns\n"


# --- hx compose: a subagent stream -----------------------------------------------------------------


def test_a_subagent_stream_gets_its_sections_in_order(instance, hx, launched, goals):
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, goals)
    result = hx("compose", "eng-001", "eng-001-s001")
    assert result.returncode == 0, result.stderr
    path = subagent_file(instance, "eng-001-s001")
    assert result.stdout.strip() == str(path)
    text = path.read_text()
    # Headings match with their newline: `## Task` is a prefix of `## Tasks`.
    positions = [section_index(text, heading + "\n") for heading in SUBAGENT_SECTIONS]
    assert positions == sorted(positions), f"sections out of order: {positions}"


@pytest.mark.parametrize("flavor,carried", [
    ("claude", False), ("codex", False), ("pi", True), ("grok", True), ("meta", True),
])
def test_a_subagent_carries_the_invariants_only_where_its_parent_gets_them_from_the_hook(
    instance, hx, launched, goals, flavor, carried
):
    """A subagent runs no `context` hook. Claude and Codex subagents load the same CLAUDE.md /
    AGENTS.md their parent does; on Pi, Grok and Meta the hook is the invariants' channel, so
    the subagent's file carries them, whole, first."""
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, goals)
    _set_flavor(instance, "eng-001", flavor)
    assert hx("compose", "eng-001", "eng-001-s001").returncode == 0
    text = subagent_file(instance, "eng-001-s001").read_text()
    source = (instance / "config" / "CLAUDE.md").read_text().strip("\n")
    assert ("\n## Invariants\n" in text) is carried
    assert (source in text) is carried
    if carried:
        assert "_source: `config/CLAUDE.md`_" in text
        assert text.index("## Invariants\n") < text.index("## Who your subagents are\n")


def test_no_persona_section_on_a_subagent_stream(instance, hx, launched, goals):
    """Subagent streams carry SUBAGENTS.md whole as their identity."""
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, goals)
    _set_flavor(instance, "eng-001", "codex")
    (instance / "config" / "eng-001" / "SUBAGENTS.md").write_text("You are a subagent of eng-001.\n")
    assert hx("compose", "eng-001", "eng-001-s001").returncode == 0
    text = subagent_file(instance, "eng-001-s001").read_text()
    assert "\n## Persona\n" not in text
    assert PERSONA.strip().split("\n")[0] not in text
    assert "You are a subagent of eng-001." in text


def test_a_subagent_stream_gets_subagents_md_and_no_tasks(instance, hx, launched, goals):
    """spec 07.3: SUBAGENTS.md whole for a subagent stream; `## Tasks` is main-stream only."""
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, goals)
    (instance / "config" / "eng-001" / "SUBAGENTS.md").write_text("You are a subagent of eng-001.\n")

    result = hx("compose", "eng-001", "eng-001-s001")
    assert result.returncode == 0, result.stderr
    text = subagent_file(instance, "eng-001-s001").read_text()
    assert "You are a subagent of eng-001." in text
    assert "\n## Tasks\n" not in text, "`## Tasks` is main-stream only"
    assert "## Who your subagents are" in text


def test_a_subagent_gets_the_shared_template_rendered_with_its_parents_id(instance, hx, launched, goals):
    """Nothing is copied per id: the template is rendered at delivery, comments dropped; Meta
    workers get the contributor template; an id's own SUBAGENTS.md replaces it."""
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, goals)
    assert not (instance / "config" / "eng-001" / "SUBAGENTS.md").exists()
    assert hx("compose", "eng-001", "eng-001-s001").returncode == 0
    text = subagent_file(instance, "eng-001-s001").read_text()
    assert "You are a subagent of `eng-001`, an engineer in the `engineers` pod." in text
    assert "{{" not in text and "<!--" not in text
    assert "_source: `templates/worker/SUBAGENTS.md`_" in text
    template = instance / "templates" / "worker" / "SUBAGENTS.md"
    template.write_text(template.read_text() + "\nSHARED EDIT for {{id}}.\n")
    assert hx("compose", "eng-001", "eng-001-s002").returncode == 0
    assert "SHARED EDIT for eng-001." in subagent_file(instance, "eng-001-s002").read_text()
    _set_flavor(instance, "eng-001", "meta")
    assert hx("compose", "eng-001", "eng-001-s003").returncode == 0
    assert "_source: `templates/contributor/SUBAGENTS.md`_" in subagent_file(
        instance, "eng-001-s003").read_text()


# --- the context hook ----------------------------------------------------------------------------


@pytest.mark.parametrize("source", SOURCES)
def test_the_hook_hands_the_chain_and_ends_with_one_pointer(instance, hx, launched, goals, tmux_server, source):
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, goals)
    result = run_hook(
        instance, "eng-001", "context",
        {"hook_event_name": "SessionStart", "source": source, "session_id": "s1",
         "cwd": str(instance / "wt" / "eng-001")},
        tmux=tmux_server,
    )
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.split("\n") if line.strip()]
    pointer = CONTEXT_LINE.format(path=work_item(instance).resolve())
    assert lines[-1] == pointer
    assert sum("Use the Read tool once on" in line for line in lines) == 1, "one pointer, one Read"
    assert "You are eng-001 in pod engineers." in result.stdout
    assert not result.stdout.lstrip().startswith("{"), "never JSON, never a leading brace (spec 09.1)"
    assert not old_context_file(instance, "eng-001").exists()
    assert "\n## Status\n" in work_item(instance).read_text(), "the status is written first"


def test_the_hook_prints_what_hx_persona_prints(instance, hx, launched, goals, tmux_server):
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, goals)
    result = run_hook(instance, "eng-001", "context", {"source": "startup", "cwd": str(instance)},
                      tmux=tmux_server)
    assert result.returncode == 0, result.stderr
    printed = hx("persona", "eng-001")
    assert printed.returncode == 0, printed.stderr
    assert result.stdout.strip("\n") == printed.stdout.strip("\n")


@pytest.mark.parametrize("source", ["startup", "resume", "compact"])
def test_only_clear_sends_the_goal(instance, hx, launched, goals, tmux_server, source):
    """spec 09.1: `startup` and `resume` send none; `hx launch`/`hx restart` do that."""
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, goals)
    (instance / "run" / "eng-001" / "fake-input.log").write_text("")
    run_hook(instance, "eng-001", "context",
             {"source": source, "cwd": str(instance)}, tmux=tmux_server)
    assert "/goal" not in (instance / "run" / "eng-001" / "fake-input.log").read_text()


def test_clear_on_a_working_item_sends_the_goal(instance, hx, launched, goals, tmux_server):
    """spec 09.3 step 4: the `context` hook finishes the seam."""
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, goals)
    (instance / "run" / "eng-001" / "fake-input.log").write_text("")
    result = run_hook(instance, "eng-001", "context",
                      {"source": "clear", "cwd": str(instance)}, tmux=tmux_server)
    assert result.returncode == 0, result.stderr
    wait_for(
        lambda: "/goal The goal for eng-001" in
        (instance / "run" / "eng-001" / "fake-input.log").read_text(),
        what="the pointer the clear hook sent",
    )


def test_clear_on_an_idle_item_sends_no_goal(instance, hx, launched, tmux_server):
    launched("eng-001")
    (instance / "run" / "eng-001" / "fake-input.log").write_text("")
    run_hook(instance, "eng-001", "context", {"source": "clear", "cwd": str(instance)}, tmux=tmux_server)
    assert "/goal" not in (instance / "run" / "eng-001" / "fake-input.log").read_text()


def test_the_hook_records_a_boundary_on_the_main_stream(instance, hx, launched, goals, tmux_server):
    from .test_transitions import dispatch_working

    launched("eng-001")
    dispatch_working(instance, hx, goals)
    result = run_hook(instance, "eng-001", "context",
                      {"source": "compact", "transcript_path": "/tmp/t.jsonl", "cwd": str(instance)},
                      tmux=tmux_server)

    from hx import persona
    from hx.streams import iter_records, main_stream

    records = list(iter_records(main_stream(instance, "eng-001")))
    assert records, "no boundary record"
    last = records[-1]
    assert last["event"] == "boundary" and last["source"] == "compact"
    assert last["stream"] == "eng-001-main" and last["seq"] >= 1
    assert last["ref"]["transcript"] == "/tmp/t.jsonl"
    assert last["context_file"] == str(work_item(instance)), "the file the pointer names"
    assert last["hook_bytes"] == persona.boundary(instance, "eng-001")["bytes"]
    assert result.stdout.startswith(persona.boundary(instance, "eng-001")["text"])


def test_the_partner_is_pointed_at_partner_md(instance, hx, launched, tmux_server):
    launched("partner")
    (instance / "PARTNER.md").write_text("# Partner state\n\nTwo workers idle.\n")
    result = run_hook(instance, "partner", "context", {"source": "startup", "cwd": str(instance)},
                      tmux=tmux_server)
    assert result.returncode == 0, result.stderr
    lines = [line for line in result.stdout.split("\n") if line.strip()]
    assert lines[0] == "You are the Partner.", "the persona reads first"
    assert lines[-1] == CONTEXT_LINE.format(path=(instance / "PARTNER.md").resolve())
    assert "Two workers idle." not in result.stdout, "PARTNER.md is read once, not printed"


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


def test_a_malformed_payload_allows_for_guard(instance, hx, launched):
    """The guard keeps the module's failure policy for its own crashes: log and allow
    (spec 09.1). Until `guard` was an event this test asserted exit 2, which only ever came
    from argparse rejecting the unknown event name."""
    launched("eng-001")
    for item_id in ("eng-001", "partner"):
        result = subprocess.run(
            [sys.executable, "-m", "hx.hooks", "--id", item_id, "guard"],
            input="{not json",
            env=clean_env(HARNESS_ROOT=str(instance)),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == ""
        log = instance / "logs" / item_id / "hook-errors.log"
        assert log.is_file() and "guard: JSONDecodeError" in log.read_text()


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
    assert result.returncode == 0, result.stderr
    assert "You are eng-001 in pod engineers." in result.stdout
    assert result.stdout.strip("\n").endswith(IDLE_LINE), "no task in progress: no file to read"


def test_every_spec_09_event_is_implemented(instance, hx, launched):
    """build-8 item 3 closed the last two: `precompact` and `postcompact` are log-only."""
    from hx.hooks import EVENTS, IMPLEMENTED

    assert set(EVENTS) == set(IMPLEMENTED), set(EVENTS) ^ set(IMPLEMENTED)

    launched("eng-001")
    result = run_hook(instance, "eng-001", "precompact", {})
    assert result.returncode == 0
    assert "not implemented" not in result.stderr
