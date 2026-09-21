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
* **board, orders and `show partner`** are written to the contract by hand,
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
    },
    {
        "id": "eng-001", "pod": "engineers", "role": "engineer", "state": "working",
        "file": "pods/engineers/eng-001-working.md", "outcome": None,
        "dispatched": "2026-09-20T12:00:00Z", "completed": None,
        "open_subagents": 1, "goal_ts": "2026-09-20T12:00:03Z", "session_alive": True,
        "context_tokens": 48211, "seams": 2, "turn_ts": "2026-09-20T13:09:40Z",
    },
    {
        # Never dispatched: no goal, no stream, so `seams` and `context_tokens`
        # are null rather than zero (the distinction ui-3 asked build to keep).
        "id": "eng-002", "pod": "engineers", "role": "engineer", "state": "idle",
        "file": "pods/engineers/eng-002-idle.md", "outcome": None,
        "dispatched": None, "completed": None,
        "open_subagents": 0, "goal_ts": None, "session_alive": True,
        "context_tokens": None, "seams": None, "turn_ts": None,
    },
    {
        "id": "eng-003", "pod": "engineers", "role": "engineer", "state": "complete",
        "file": "pods/engineers/eng-003-complete.md", "outcome": "decision",
        "dispatched": "2026-09-20T10:30:00Z", "completed": "2026-09-20T12:47:31Z",
        "open_subagents": 0, "goal_ts": "2026-09-20T10:30:04Z", "session_alive": True,
        "context_tokens": 74902, "seams": 1, "turn_ts": "2026-09-20T12:47:31Z",
    },
    {
        # A working item whose session is gone. With no invariants in v1 this is
        # no longer an error the board reports — the column is the only sign.
        "id": "res-001", "pod": "research", "role": "researcher", "state": "working",
        "file": "pods/research/res-001-working.md", "outcome": None,
        "dispatched": "2026-09-20T08:40:00Z", "completed": None,
        "open_subagents": 0, "goal_ts": "2026-09-20T08:40:02Z", "session_alive": False,
        "context_tokens": 20118, "seams": 0, "turn_ts": "2026-09-20T09:02:11Z",
    },
]

BOARD = {"root_abs": ROOT_ABS, "ts": TS, "items": BOARD_ITEMS}


# -- orders (v1 cut: one entry per tasks.json id, no graph, no file compare) ---

ORDER_001 = """## Order
Add `--require-done` to `hx board` so the Partner's own `### Checks` block can assert that a
set of ids finished with outcome `done`.

## Definition of done
- `hx board --require-done eng-000` exits 0 and prints nothing extra.

### Checks
```bash
python -m pytest tests/test_board.py -q
```
"""

ORDER_003 = """## Order
Decide where the seam threshold lives: per model in `config/models.json`, or per agent in
`config/<id>/harness.json`. Write the recommendation and the losing option's cost.

## Definition of done
- A recommendation with the cost of the option not taken.

### Checks
```bash
test -s /srv/hx/pods/engineers/eng-003-complete.md
```
"""

ORDERS = {
    "root_abs": ROOT_ABS,
    "ts": TS,
    "orders": [
        {
            "id": "eng-000", "pod": "engineers", "state": "complete", "outcome": "done",
            "order": "## Order\nShip `hx board --json`.\n\n## Definition of done\n- It validates.\n\n### Checks\n```bash\ntrue\n```\n",
            "addenda": [],
            "dispatched": "2026-09-20T10:00:00Z", "completed": "2026-09-20T11:58:12Z",
        },
        {
            "id": "eng-001", "pod": "engineers", "state": "working", "outcome": None,
            "order": ORDER_001,
            "addenda": [{
                "ts": "2026-09-20T12:30:00Z",
                "text": "Also refuse an id with no `config/<id>/` directory; exit 1 and name it.",
            }],
            "dispatched": "2026-09-20T12:00:00Z", "completed": None,
        },
        {
            "id": "eng-003", "pod": "engineers", "state": "complete", "outcome": "decision",
            "order": ORDER_003,
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
            "order": ORDER_001,
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

    # v1 cut: `hx show partner --json` returns only these four keys.
    partner_existing = json.loads((FIXTURES / "show-partner.json").read_text(encoding="utf-8"))
    partner = {
        "id": "partner",
        "partner_md": partner_existing["partner_md"],
        "pane": partner_existing["pane"],
        "streams": partner_existing["streams"],
    }

    for name, document in (
        ("board.json", BOARD),
        ("orders.json", ORDERS),
        ("show-eng-001.json", show),
        ("show-partner.json", partner),
    ):
        (FIXTURES / name).write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {name}")

    print(f"  board items   : {[item['id'] for item in BOARD['items']]}")
    print(f"  orders        : {[entry['id'] for entry in ORDERS['orders']]}")
    print(f"  step state    : {sorted(show['step_state'])}")
    print(f"  turn          : {show['turn']}")
    print(f"  partner keys  : {sorted(partner)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
