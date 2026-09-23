"""Regenerate `tests/ui/fixtures/` for the v1 cut (ui-7 item 0).

    .venv/bin/python -m tests.ui.regen_fixtures

Every fixture is built to `CONTRACTS.md` as rewritten. Where a real hx path can
produce or check the data, it does:

* **stream records** are the ones `python -m hx.hooks` wrote in ui-6 — the cut
  did not touch them, so they are carried over from the existing fixture rather
  than re-run;
* **step state** goes through `hx.stepstate.validate` and `evict`, the same
  functions the Companion's `stop` hook accepts a pass with, so a fixture hx
  would reject cannot be committed;
* **board, goals and `show partner`** are written to the contract by hand,
  because build-7 is still cutting those commands and they do not yet emit the
  v1 shape. `goals/ui-7.done.md` says so plainly.

Run it again once build-7 lands to replace the last of that with real output.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))

from hx import stepstate  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"
ROOT_ABS = "/srv/hx"
TS = "2026-09-20T13:10:00Z"

#: `companion.state_budget_tokens` from `templates/worker/harness.json`. The UI
#: has no way to read the real one — `hx show --json` does not carry it — so the
#: bar is drawn against this and labelled as the default. Asked for in
#: handoff/ui-to-build.md.
DEFAULT_STATE_BUDGET = 10_000


# -- the board (v1 cut: no errors, no after/ready/goal_pending, no partner) ----

BOARD_ITEMS = [
    {
        "id": "eng-000", "pod": "engineers", "role": "engineer", "state": "complete",
        "file": "pods/engineers/eng-000-complete.md", "outcome": "done",
        "dispatched": "2026-09-20T10:00:00Z", "completed": "2026-09-20T11:58:12Z",
        "open_subagents": 0, "goal_ts": "2026-09-20T10:00:03Z", "session_alive": True,
        "context_tokens": 132880, "seams": 2, "turn_ts": "2026-09-20T11:58:12Z",
        "scope": "Ship `hx board --json`.",
    },
    {
        "id": "eng-001", "pod": "engineers", "role": "engineer", "state": "working",
        "file": "pods/engineers/eng-001-working.md", "outcome": None,
        "dispatched": "2026-09-20T12:00:00Z", "completed": None,
        "open_subagents": 1, "goal_ts": "2026-09-20T12:00:03Z", "session_alive": True,
        "context_tokens": 48211, "seams": 2, "turn_ts": "2026-09-20T13:09:40Z",
        "scope": "Add `--require-done` to `hx board` so the Partner's own `### Checks` block can assert that a",
    },
    {
        # Never dispatched: no goal, no stream, so `seams` and `context_tokens`
        # are null rather than zero (the distinction ui-3 asked build to keep).
        "id": "eng-002", "pod": "engineers", "role": "engineer", "state": "idle",
        "file": "pods/engineers/eng-002-idle.md", "outcome": None,
        "dispatched": None, "completed": None,
        "open_subagents": 0, "goal_ts": None, "session_alive": True,
        "context_tokens": None, "seams": None, "turn_ts": None, "scope": None,
    },
    {
        "id": "eng-003", "pod": "engineers", "role": "engineer", "state": "complete",
        "file": "pods/engineers/eng-003-complete.md", "outcome": "decision",
        "dispatched": "2026-09-20T10:30:00Z", "completed": "2026-09-20T12:47:31Z",
        "open_subagents": 0, "goal_ts": "2026-09-20T10:30:04Z", "session_alive": True,
        "context_tokens": 74902, "seams": 1, "turn_ts": "2026-09-20T12:47:31Z",
        "scope": "Decide where the seam threshold lives: per model in `config/models.json`, or per agent in",
    },
    {
        # A working item whose session is gone. With no invariants in v1 this is
        # no longer an error the board reports — the column is the only sign.
        "id": "res-001", "pod": "research", "role": "researcher", "state": "working",
        "file": "pods/research/res-001-working.md", "outcome": None,
        "dispatched": "2026-09-20T08:40:00Z", "completed": None,
        "open_subagents": 0, "goal_ts": "2026-09-20T08:40:02Z", "session_alive": False,
        "context_tokens": 20118, "seams": 0, "turn_ts": "2026-09-20T09:02:11Z", "scope": None,
    },
]

for _item in BOARD_ITEMS:
    # Companion activity (CONTRACTS.md): eng-001's Companion is mid-pass in the fixture.
    _item.setdefault("companion_pass", _item["id"] == "eng-001")
    _item.setdefault("companion_ts", "2026-09-20T13:09:35Z" if _item.get("turn_ts") else None)
BOARD = {
    "root_abs": ROOT_ABS, "ts": TS, "items": BOARD_ITEMS,
    "memory": {"episodes": 12, "queued": 1, "indexed_ts": "2026-09-20T13:05:00Z"},
}


# -- goals (v1 cut: one entry per tasks.json id, no graph, no file compare) ---

GOAL_001 = """## Goal
Add `--require-done` to `hx board` so the Partner's own `### Checks` block can assert that a
set of ids finished with outcome `done`.

## Definition of done
- `hx board --require-done eng-000` exits 0 and prints nothing extra.

### Checks
```bash
python -m pytest tests/test_board.py -q
```
"""

GOAL_003 = """## Goal
Decide where the seam threshold lives: per model in `config/models.json`, or per agent in
`config/<id>/harness.json`. Write the recommendation and the losing option's cost.

## Definition of done
- A recommendation with the cost of the option not taken.

### Checks
```bash
test -s /srv/hx/pods/engineers/eng-003-complete.md
```
"""

GOALS = {
    "root_abs": ROOT_ABS,
    "ts": TS,
    "goals": [
        {
            "id": "eng-000", "pod": "engineers", "state": "complete", "outcome": "done",
            "goal": "## Goal\nShip `hx board --json`.\n\n## Definition of done\n- It validates.\n\n### Checks\n```bash\ntrue\n```\n",
            "addenda": [],
            "dispatched": "2026-09-20T10:00:00Z", "completed": "2026-09-20T11:58:12Z",
        },
        {
            "id": "eng-001", "pod": "engineers", "state": "working", "outcome": None,
            "goal": GOAL_001,
            "addenda": [{
                "ts": "2026-09-20T12:30:00Z",
                "text": "Also refuse an id with no `config/<id>/` directory; exit 1 and name it.",
            }],
            "dispatched": "2026-09-20T12:00:00Z", "completed": None,
        },
        {
            "id": "eng-003", "pod": "engineers", "state": "complete", "outcome": "decision",
            "goal": GOAL_003,
            "addenda": [],
            "dispatched": "2026-09-20T10:30:00Z", "completed": "2026-09-20T12:47:31Z",
        },
    ],
}


# -- step state (validated by the real hx.stepstate) --------------------------

MAIN_STATE = {
    "goal": "add `--require-done` to hx board, with the spec 08 exit rule",
    "constraints": ["stdlib only", "no timeouts anywhere"],
    "decisions": [
        {
            "d": "`--require-done` takes ids, not a file",
            "why": "spec 08 shows it inline in the Partner's own `### Checks` block",
            "ev": [401],
        },
        {
            "d": "read tasks.json under the flock",
            "why": "two dispatches raced the read and one saw a half-written file",
            "ev": [372, 380],
        },
    ],
    "open_steps": [
        {
            "id": "st7",
            "intent": "refuse an id that has no `config/<id>/`",
            "next": "raise in `_require_done` before the outcome lookup",
            "ev": [398, 410],
        },
        {
            "id": "st8",
            "intent": "cover the three exit paths in tests/test_board.py",
            "next": "write the unknown-id case first; the other two already pass",
            "ev": [410],
        },
    ],
    "closed_steps": [
        {
            "id": "st6",
            "outcome": "`--require-done` parsed and threaded to the board reader",
            "verified": True,
            "commit": "1b90c3d",
            "ev": [390],
        },
        {
            "id": "st5",
            "outcome": "read spec 08 end to end; the exit rule is in the `hx board` row",
            "verified": False,
            "commit": "",
            "ev": [340],
        },
    ],
    "dead_ends": [
        "parsed the flag with a manual argv scan before finding argparse already had the group",
    ],
    "blockers": [],
    "subagents_open": ["eng-001-s001"],
    "working_set": {
        "commits": ["1b90c3d board: parse --require-done"],
        "dirty": ["src/hx/board.py", "tests/test_board.py"],
        "files": [
            {"path": "spec/08-hx-cli.md", "note": "exit 0 iff every listed id is complete/done"},
            {"path": "CONTRACTS.md", "note": "board items are keyed by id, partner is not one"},
        ],
        "last_failure": "test_unknown_id_exits_1: expected 1, got 0",
        "hypothesis": "the lookup treats a missing id as not-done rather than refusing",
    },
}

SUBAGENT_STATE = {
    "goal": "survey every exit-code assertion already in tests/",
    "constraints": [],
    "decisions": [],
    "open_steps": [
        {"id": "st1", "intent": "grep the suite", "next": "report the list to the parent", "ev": [2]},
    ],
    "closed_steps": [],
    "dead_ends": [],
    "blockers": ["cannot run the suite: no worktree on this stream"],
    "subagents_open": [],
    "working_set": {
        "commits": [], "dirty": [], "files": [], "last_failure": "", "hypothesis": "",
    },
}

#: What `hx.companion.ingest` stamps after validation (build-6).
PROMPT_VERSION = {"base": "9c1f2ab", "role": "4d80e17"}


def accept(state: dict, *, seq: int) -> dict:
    """Put a candidate through the real validator, evictor and stamping.

    `hx.stepstate.validate` is the function the Companion's `stop` hook accepts a
    pass with; anything it rejects never reaches `state/<id>/<stream>.json`. So a
    fixture that survives this is one hx would have written.
    """
    accepted = stepstate.validate({**state, "seq": seq})
    accepted, evicted = stepstate.evict(accepted, DEFAULT_STATE_BUDGET)
    assert not evicted, f"the fixture is over budget and would be evicted: {evicted}"
    accepted["prompt_version"] = PROMPT_VERSION
    accepted["ts"] = "2026-09-20T13:09:41Z"
    return accepted


# -- digests (build-6 replaces the `_pending companion_` placeholder) ---------

DIGESTS = {
    "eng-001-s002": (
        "Surveyed the exit-code assertions in `tests/`.\n\n"
        "- `tests/test_board.py:41` and `tests/test_complete.py:88` are the only two.\n"
        "- Both assert on `returncode` directly, so a new exit path needs its own case.\n"
        "- Left open: nothing.\n"
    ),
    "eng-001-s003": (
        "Read `spec/08-hx-cli.md` and `spec/06-work-items.md` end to end.\n\n"
        "- The `--require-done` exit rule is in the `hx board` row of 08's table.\n"
        "- Nothing in 06 constrains it.\n"
        "- Committed nothing; this was a read-only pass.\n"
    ),
}


# -- the rest of the fleet (ui-8) ---------------------------------------------
#
# ui-8 made the whole instance one page: the fleet graph, the task board and the
# agent table all say what every HarnessAgent is doing, from its own work item
# and its Companion's step state. With a `show` document for only one id, four
# of the five agents had nothing to say. These are the other four, to the same
# contract, so every screen of `python -m hx.ui --fixtures tests/ui/fixtures`
# has something real in it.


def work_item_body(goal: str, tasks: list[tuple[bool, str]], *, deliverables: str = "",
                   commands: str = "", decision: str = "", digest: str = "") -> str:
    """The rendered `templates/work-item.md` body: the goal, then the sections."""
    checklist = "\n".join(f"- [{'x' if done else ' '}] {text}" for done, text in tasks)
    return (
        f"{goal}\n## Tasks\n{checklist or '_none yet_'}\n\n"
        f"## Deliverables\n{deliverables}\n\n## Commands\n{commands}\n\n"
        f"## Open decision\n{decision}\n\n## Digest\n{digest}\n"
    )


def record(seq: int, ts: str, stream: str, **fields) -> dict:
    return {"seq": seq, "ts": ts, "stream": stream, **fields}


def show_document(
    *, id: str, pod: str, role: str, state: str, outcome, dispatched, completed,
    goal: str, body: str, step_state: dict, streams: list, pane: dict,
    metrics: dict | None = None, context_file=None, addenda=(), turn=None,
) -> dict:
    return {
        "id": id,
        "pod": pod,
        "role": role,
        "state": state,
        "file": f"pods/{pod}/{id}-{state}.md",
        "work_item": {
            "frontmatter": {"id": id, "pod": pod, "outcome": outcome, "dispatched": dispatched},
            "body": body,
        },
        "task": {
            "goal": goal,
            "addenda": list(addenda),
            "outcome": outcome,
            "dispatched": dispatched,
            "completed": completed,
        },
        "persona_path": f"run/{id}/persona.md",
        "step_state": step_state,
        "context_file": context_file,
        "streams": streams,
        "subagents": {},
        "metrics": metrics or {"id": id, "stream": f"{id}-main", "dispatched": dispatched,
                               "seams": [], "totals": {"seams": 0, "tool_calls": 0,
                                                       "reads_of_context_file": 0,
                                                       "reads_of_working_set": 0, "other": 0}},
        "pane": pane,
        "turn": turn,
        "companion": {
            "pass_in_flight": id == "eng-001",
            "pass_stream": f"{id}-main" if id == "eng-001" else None,
            "pass_since": "2026-09-20T13:09:41Z" if id == "eng-001" else None,
            "last_state_ts": "2026-09-20T13:09:35Z" if id != "eng-000" else None,
            "streams": len(step_state) if isinstance(step_state, dict) else 0,
        },
        "archive": [],
        "bench": [],
        "compactions": compactions(id, step_state),
    }


def compactions(id: str, step_state) -> dict:
    """CONTRACTS.md: the installed step state per stream, rendered as the master reads it."""
    from hx.compose import render_step_state

    return {
        handle: {"path": f"state/{id}/{handle}.json", "ts": state.get("ts"),
                 "seq": state.get("seq"), "text": render_step_state(state)}
        for handle, state in (step_state or {}).items() if isinstance(state, dict)
    }


GOAL_000 = """## Goal
Ship `hx board --json` to the shape `CONTRACTS.md` gives: one entry per worker id, `partner`
not among them.

## Definition of done
- `hx board --json` prints the contract document and exits 0.

### Checks
```bash
python -m pytest tests/core/test_board.py -q
```
"""

STATE_000 = {
    "goal": "ship `hx board --json` to the CONTRACTS.md shape",
    "constraints": ["stdlib only", "exit 0 always in v1"],
    "decisions": [
        {"d": "read the state from the filename suffix, not the frontmatter",
         "why": "the v1 cut made the filename the state and nothing polices it", "ev": [120]},
    ],
    "open_steps": [],
    "closed_steps": [
        {"id": "st1", "outcome": "collect() returns the contract document",
         "verified": True, "commit": "a41f0b2", "ev": [88]},
        {"id": "st2", "outcome": "`partner` is not an item; the Partner has no work item",
         "verified": True, "commit": "a41f0b2", "ev": [104]},
        {"id": "st3", "outcome": "tests cover a fresh root, one item and five",
         "verified": True, "commit": "c70d18e", "ev": [131]},
    ],
    "dead_ends": [],
    "blockers": [],
    "subagents_open": [],
    "working_set": {
        "commits": ["a41f0b2 board: the CONTRACTS.md document", "c70d18e board: tests"],
        "dirty": [],
        "files": [{"path": "CONTRACTS.md", "note": "the board document, field by field"}],
        "last_failure": "",
        "hypothesis": "",
    },
}

STATE_003 = {
    "goal": "decide where the seam threshold lives and write the recommendation",
    "constraints": ["decide, do not implement"],
    "decisions": [
        {"d": "per model in `config/models.json`",
         "why": "the threshold is a property of the context window, which is the model's",
         "ev": [61, 77]},
    ],
    "open_steps": [],
    "closed_steps": [
        {"id": "st1", "outcome": "read spec 05 and 10; both leave the location open",
         "verified": True, "commit": "", "ev": [44]},
        {"id": "st2", "outcome": "wrote the recommendation and the losing option's cost into ## Open decision",
         "verified": True, "commit": "", "ev": [77]},
    ],
    "dead_ends": ["tried to infer the threshold from the last seam's context_tokens; too few seams to be worth it"],
    "blockers": [],
    "subagents_open": [],
    "working_set": {
        "commits": [],
        "dirty": [],
        "files": [
            {"path": "spec/05-configuration.md", "note": "models.json is per model, already read at launch"},
            {"path": "spec/10-companion.md", "note": "the budget is per agent; the threshold is not"},
        ],
        "last_failure": "",
        "hypothesis": "",
    },
}


def other_shows() -> dict:
    """`show-<id>.json` for the four ids the board lists and ui-7 had no document for."""
    eng000 = show_document(
        id="eng-000", pod="engineers", role="engineer", state="complete", outcome="done",
        dispatched="2026-09-20T10:00:00Z", completed="2026-09-20T11:58:12Z",
        goal=GOAL_000,
        body=work_item_body(
            GOAL_000,
            [(True, "Read CONTRACTS.md for the board document."),
             (True, "Write `collect()` in `src/hx/board.py`."),
             (True, "Leave `partner` out of the items."),
             (True, "Cover a fresh root, one item and five in `tests/core/test_board.py`.")],
            deliverables="- `src/hx/board.py`\n- `tests/core/test_board.py`",
            commands="```bash\npython -m pytest tests/core/test_board.py -q\n```",
            digest="`hx board --json` ships the contract document. `partner` is not an item.",
        ),
        step_state={"eng-000-main": accept(STATE_000, seq=131)},
        streams=[{
            "handle": "eng-000-main",
            "path": "logs/eng-000/eng-000-main.jsonl",
            "open": False,
            "records": 131,
            "tail": [
                record(128, "2026-09-20T11:57:40Z", "eng-000-main", event="post_tool", tool="Bash",
                       input='{"command": "python -m pytest tests/core/test_board.py -q"}',
                       output='{"stdout": "12 passed in 0.31s\\n", "exit_code": 0}', exit=0,
                       context_tokens=131002, ref={"transcript": "/srv/hx/run/eng-000/t.jsonl",
                                                   "tool_use_id": "toolu_bash_pytest"}),
                record(129, "2026-09-20T11:57:58Z", "eng-000-main", event="post_tool", tool="Bash",
                       input='{"command": "git commit -m \'board: tests\' -- src/hx/board.py tests/core/test_board.py"}',
                       output='{"stdout": "[main c70d18e] board: tests\\n", "exit_code": 0}', exit=0,
                       context_tokens=131880, ref={"transcript": "/srv/hx/run/eng-000/t.jsonl",
                                                   "tool_use_id": "toolu_bash_commit"}),
                record(130, "2026-09-20T11:58:10Z", "eng-000-main", event="seam", source="clear",
                       context_file="/srv/hx/run/eng-000/eng-000-main.context.md",
                       context_file_bytes=2410, ref={"transcript": None}),
                record(131, "2026-09-20T11:58:12Z", "eng-000-main", event="close",
                       ref={"transcript": None}),
            ],
            "digest": None,
        }],
        metrics={
            "id": "eng-000", "stream": "eng-000-main", "dispatched": "2026-09-20T10:00:00Z",
            "seams": [
                {"seq": 62, "ts": "2026-09-20T10:44:02Z", "source": "startup",
                 "prompt_version": "base-3/engineer-2", "context_tokens_before": 0,
                 "context_file_bytes": 1710, "working_set_size": 0,
                 "next_10_turns": {"turns": 10, "tool_calls": 11, "reads_of_context_file": 1,
                                   "reads_of_working_set": 0, "other": 10}},
                {"seq": 130, "ts": "2026-09-20T11:58:10Z", "source": "clear",
                 "prompt_version": "base-3/engineer-2", "context_tokens_before": 131880,
                 "context_file_bytes": 2410, "working_set_size": 2,
                 "next_10_turns": {"turns": 2, "tool_calls": 2, "reads_of_context_file": 1,
                                   "reads_of_working_set": 0, "other": 1}},
            ],
            "totals": {"seams": 2, "tool_calls": 13, "reads_of_context_file": 2,
                       "reads_of_working_set": 0, "other": 11},
        },
        context_file={
            "path": "run/eng-000/eng-000-main.context.md",
            "text": ("# Context\n\n_source: state/eng-000/eng-000-main.json_\n\n"
                     "## Goal\nship `hx board --json` to the CONTRACTS.md shape\n\n"
                     "## Closed\n- st1 collect() returns the contract document (a41f0b2)\n"
                     "- st2 `partner` is not an item (a41f0b2)\n"),
            "seam_ts": "2026-09-20T11:58:10Z",
        },
        pane={"session": "eng-000", "alive": True,
              "lines": ["> python -m pytest tests/core/test_board.py -q", "12 passed in 0.31s",
                        "Complete: done. Work item renamed to eng-000-complete.md."],
              "source": "session"},
        turn={"ts": "2026-09-20T11:58:12Z", "background_tasks": []},
    )

    eng002 = show_document(
        id="eng-002", pod="engineers", role="engineer", state="idle", outcome=None,
        dispatched=None, completed=None,
        goal="",
        body=work_item_body("", [], digest=""),
        step_state={},
        streams=[],
        pane={"session": "eng-002", "alive": True,
              "lines": ["Waiting. No goal dispatched."], "source": "session"},
        context_file={"path": "run/eng-002/eng-002-main.context.md", "text": None, "seam_ts": None},
        turn=None,
    )

    eng003 = show_document(
        id="eng-003", pod="engineers", role="engineer", state="complete", outcome="decision",
        dispatched="2026-09-20T10:30:00Z", completed="2026-09-20T12:47:31Z",
        goal=GOAL_003,
        body=work_item_body(
            GOAL_003,
            [(True, "Read spec 05 and spec 10 for where configuration lives."),
             (True, "Write the recommendation and the cost of the option not taken.")],
            deliverables="- The recommendation, in `## Open decision` below.",
            decision=("**Per model, in `config/models.json`.** The seam threshold is a share of the "
                      "context window, and the window is a property of the model, not of the agent.\n\n"
                      "Cost of the option not taken: per agent in `config/<id>/harness.json` would let "
                      "one agent seam earlier than its podmates, which is occasionally wanted — but it "
                      "puts a number that has to track the model in a file nobody rewrites when the "
                      "model changes."),
            digest="Recommended per model in `config/models.json`; the per-agent cost is written above.",
        ),
        step_state={"eng-003-main": accept(STATE_003, seq=77)},
        streams=[{
            "handle": "eng-003-main",
            "path": "logs/eng-003/eng-003-main.jsonl",
            "open": False,
            "records": 77,
            "tail": [
                record(74, "2026-09-20T12:44:10Z", "eng-003-main", event="post_tool", tool="Read",
                       input='{"file_path": "spec/10-companion.md"}',
                       output='{"content": "## 10. The Companion\\nThe budget is per agent\\u2026"}',
                       context_tokens=71240, ref={"transcript": "/srv/hx/run/eng-003/t.jsonl",
                                                  "tool_use_id": "toolu_read_10"}),
                record(75, "2026-09-20T12:46:02Z", "eng-003-main", event="seam", source="compact",
                       context_file="/srv/hx/run/eng-003/eng-003-main.context.md",
                       context_file_bytes=1980, ref={"transcript": None}),
                record(76, "2026-09-20T12:47:20Z", "eng-003-main", event="post_tool", tool="Edit",
                       input='{"file_path": "pods/engineers/eng-003-working.md"}',
                       output='{"stdout": "", "exit_code": 0}', exit=0, context_tokens=74902,
                       ref={"transcript": "/srv/hx/run/eng-003/t.jsonl", "tool_use_id": "toolu_edit_wi"}),
                record(77, "2026-09-20T12:47:31Z", "eng-003-main", event="close", ref={"transcript": None}),
            ],
            "digest": None,
        }],
        metrics={
            "id": "eng-003", "stream": "eng-003-main", "dispatched": "2026-09-20T10:30:00Z",
            "seams": [
                {"seq": 75, "ts": "2026-09-20T12:46:02Z", "source": "compact",
                 "prompt_version": "base-3/engineer-2", "context_tokens_before": 71240,
                 "context_file_bytes": 1980, "working_set_size": 2,
                 # Deliberately dirty: this seam re-read two working-set files, which
                 # is the waste metric spec 07.4 exists to show.
                 "next_10_turns": {"turns": 3, "tool_calls": 4, "reads_of_context_file": 1,
                                   "reads_of_working_set": 2, "other": 1}},
            ],
            "totals": {"seams": 1, "tool_calls": 4, "reads_of_context_file": 1,
                       "reads_of_working_set": 2, "other": 1},
        },
        context_file={
            "path": "run/eng-003/eng-003-main.context.md",
            "text": ("# Context\n\n_source: state/eng-003/eng-003-main.json_\n\n"
                     "## Goal\ndecide where the seam threshold lives\n\n"
                     "## Decisions\n- per model in `config/models.json` — the threshold is a share of "
                     "the context window\n"),
            "seam_ts": "2026-09-20T12:46:02Z",
        },
        pane={"session": "eng-003", "alive": True,
              "lines": ["Wrote the recommendation into ## Open decision.",
                        "Complete: decision. The Partner has the call."],
              "source": "session"},
        turn={"ts": "2026-09-20T12:47:31Z", "background_tasks": []},
    )

    res001 = show_document(
        id="res-001", pod="research", role="researcher", state="working", outcome=None,
        dispatched="2026-09-20T08:40:00Z", completed=None,
        goal=("## Goal\nSurvey how the pinned Claude Code writes `SessionStart` hook payloads, and "
               "record what you verified.\n\n## Definition of done\n- A note naming the fields "
               "observed live.\n\n### Checks\n```bash\ntest -s notes/sessionstart.md\n```\n"),
        body=work_item_body(
            "## Goal\nSurvey how the pinned Claude Code writes `SessionStart` hook payloads.\n",
            [(True, "Read the hook documentation for SessionStart."),
             (False, "Run one live session and capture the payload."),
             (False, "Write `notes/sessionstart.md` with the fields observed.")],
            commands="```bash\ntest -s notes/sessionstart.md\n```",
        ),
        # No Companion pass yet: the board's `seams: 0` and this empty step state are
        # the same fact, and the UI falls back to the first unchecked `## Tasks` line.
        step_state={},
        streams=[{
            "handle": "res-001-main",
            "path": "logs/res-001/res-001-main.jsonl",
            "open": True,
            "records": 9,
            "tail": [
                record(8, "2026-09-20T09:02:04Z", "res-001-main", event="post_tool", tool="WebFetch",
                       input='{"url": "https://code.claude.com/docs/hooks"}',
                       output='{"content": "SessionStart fires on startup, resume and clear\\u2026"}',
                       context_tokens=20118, ref={"transcript": "/srv/hx/run/res-001/t.jsonl",
                                                  "tool_use_id": "toolu_fetch_hooks"}),
                record(9, "2026-09-20T09:02:11Z", "res-001-main", event="boundary", source="startup",
                       context_file=None, ref={"transcript": None}),
            ],
            "digest": None,
        }],
        pane={"session": "res-001", "alive": False,
              "lines": ["(no session; the last 120 lines are from the log)",
                        "Read the hooks documentation. Next: capture one live payload."],
              "source": "log"},
        context_file={"path": "run/res-001/res-001-main.context.md", "text": None, "seam_ts": None},
        turn={"ts": "2026-09-20T09:02:11Z", "background_tasks": []},
    )

    return {
        "show-eng-000.json": eng000,
        "show-eng-002.json": eng002,
        "show-eng-003.json": eng003,
        "show-res-001.json": res001,
    }


def main() -> int:
    existing = json.loads((FIXTURES / "show-eng-001.json").read_text(encoding="utf-8"))

    # The streams and subagents are the real records from ui-6; the cut did not
    # touch them. Only the digests change: the Companion has written them now.
    streams = existing["streams"]
    for stream in streams:
        handle = stream["handle"]
        if handle in DIGESTS:
            stream["digest"] = DIGESTS[handle]

    show = {
        "id": "eng-001",
        "pod": "engineers",
        "role": "engineer",
        "state": "working",
        "file": "pods/engineers/eng-001-working.md",
        "work_item": {
            # v1 cut: no `after` in the frontmatter.
            "frontmatter": {
                "id": "eng-001", "pod": "engineers", "outcome": None,
                "dispatched": "2026-09-20T12:00:00Z",
            },
            "body": existing["work_item"]["body"],
        },
        "task": {
            # v1 cut: no `after` in the task block.
            "goal": GOAL_001,
            "addenda": [{
                "ts": "2026-09-20T12:30:00Z",
                "text": "Also refuse an id with no `config/<id>/` directory; exit 1 and name it.",
            }],
            "outcome": None,
            "dispatched": "2026-09-20T12:00:00Z",
            "completed": None,
        },
        "persona_path": "run/eng-001/persona.md",
        "step_state": {
            "eng-001-main": accept(MAIN_STATE, seq=412),
            "eng-001-s001": accept(SUBAGENT_STATE, seq=40),
        },
        "context_file": existing["context_file"],
        "streams": streams,
        "subagents": existing["subagents"],
        "metrics": existing["metrics"],
        "pane": existing["pane"],
        # New in CONTRACTS.md: the stop hook's marker, with background work.
        "turn": {
            "ts": "2026-09-20T13:09:40Z",
            "background_tasks": ["bash_12", "bash_19"],
        },
        "archive": existing["archive"],
        "bench": existing["bench"],
    }
    show["compactions"] = compactions("eng-001", show["step_state"])

    # v1 cut: `hx show partner --json` has no work item or task.
    partner_existing = json.loads((FIXTURES / "show-partner.json").read_text(encoding="utf-8"))
    partner = {
        "id": "partner",
        "partner_md": partner_existing["partner_md"],
        "pane": partner_existing["pane"],
        "streams": partner_existing["streams"],
        "companion": partner_existing["companion"],
        "compactions": partner_existing.get("compactions", {}),
    }

    for name, document in (
        ("board.json", BOARD),
        ("goals.json", GOALS),
        ("show-eng-001.json", show),
        ("show-partner.json", partner),
        *other_shows().items(),
    ):
        (FIXTURES / name).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {name}")

    print(f"  board items   : {[item['id'] for item in BOARD['items']]}")
    print(f"  goals        : {[entry['id'] for entry in GOALS['goals']]}")
    print(f"  step state    : {sorted(show['step_state'])}")
    print(f"  turn          : {show['turn']}")
    print(f"  partner keys  : {sorted(partner)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
