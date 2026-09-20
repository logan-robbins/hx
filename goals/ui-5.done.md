# ui-5 done — the browser-pass fixes, and both scenario packs as fixtures

All seven items done. The three the orchestrator's live pass caught were real bugs, not polish:
one of them was the view stating something false.

## The fixes

### 1. "order file missing" is not "file edited since dispatch"

`hx orders` reports `file_matches_record: false` for two different situations — the file
differs from what was dispatched, and there is no file at all — and the view called both
"edited". For a deleted `orders/<id>.md` it was **stating something untrue about a file that
does not exist**. The distinguishing fact is `order`, which is `null` only in the second case.
Now: `order === null` → "order file missing"; both present and differing → "file edited since
dispatch"; no `tasks.json` record → neither badge, since there is nothing to compare against.
Four tests: match, edited, missing, never dispatched.

### 2. Markdown tables

`PARTNER.md`'s fleet table rendered as raw pipes. The renderer now does GFM tables — header
row, `|---|:--:|---:|` rule with alignment, body rows padded to the header's width so a short
row cannot shift the columns. A line with a pipe in it and no rule under it stays a paragraph,
so `Use \`a | b\` to pipe.` is unharmed. Tested against the **real skeleton `PARTNER.md`**: its
four tables render, and no `|---|` survives as text.

Adding tables surfaced a second gap. The context file build-3 describes uses `_source: \`path\`_`
and `_none yet_`, and the renderer had no underscore emphasis — so those lines showed their
underscores. It has it now, deliberately intraword-safe: `working_set` and
`file_matches_record` stay literal, which matters because step state is full of them.

### 3. "from the none" → "no session, no log"

One helper, three answers: "from the session", "from the log", "no session, no log".

### 4. Narrow width

The board's id column is sticky (`position: sticky; left: 0`) with an opaque background and a
right border, so the id stays readable while the rest scrolls; markdown tables are explicitly
*not* pinned, since they are narrow and a pinned first column there would be wrong. Below
640 px the nav takes its own row (`flex-basis: 100%; order: 3`) and `LIVE` stays on the title
row (`order: 2`). Asserted as CSS rules rather than pixels — see the note on the browser below.

### 5. Agent id switcher

The Agent view now carries the board's ids in its header: every other id is a button, the
current one is inert, and a single-id fleet gets no switcher at all.

### 6. Both packs, every step

`tests/ui/test_m8_instance.py` is parametrised over **all 14 observation points** in
`tests/scenario/test_m8_pack.py::STEPS` and `test_m8b_pack.py::STEPS`. Each is built with the
gtm lane's own `packlib.build_instance`, given the pack's real orders and personas, and then:
the board is compared **column by column against the checked-in `expected/NN-*.txt`**, the
`after` graph is checked, and every view is rendered by the real `static/app.js` — board,
orders, archive, Partner, and each worker's Agent view in turn.

### 7. Nothing here can reach another lane's session

Audited, and it was not hypothetical: **there is a live tmux session named `partner` on this
machine right now**, the build lane's. Two consequences, both handled:

- **Wake.** `hx wake` reaches the Partner only through `run/partner/socket.json` inside
  `HARNESS_ROOT`. Every wake test uses a scratch root that has no such file, or one it created
  pointing at its own socket. There is no path from a test to a real session, and a test now
  asserts that explicitly.
- **The board.** `hx board` matches a live session by the bare id, so a scratch instance was
  reporting the build lane's real `partner` as its own `session_alive: true`. Every UI test
  that touches liveness now reads through a private tmux server via `HX_TMUX`
  (`conftest.isolated_source`), which has no sessions at all. `InstanceSource` gained an `env`
  passthrough for this — the build lane's documented hatch. A server still leaves it unset: it
  wants the real tmux.

That second one was a latent flake, not a theoretical one: three tests were asserting
`session_alive is False` for ids another lane could start at any moment.

## How it was verified

```
$ ./tools/milestone-check.sh
one failure, outside this lane — see below

$ .venv/bin/python -m pytest tests/guard
5 passed in 2.01s

$ .venv/bin/python -m pytest tests/ui
338 passed, 1 skipped in 13.04s
```

**`tools/milestone-check.sh` fails on one test that is not this lane's**, and did not block
ui-5: `tests/packaging/test_e2e_deploy.py::test_end_to_end_deploy`, at
`step 7. hx install --from-user-config`. That flag does not exist — `hx install` accepts only
`--root`, `--claude`, `--repo` and `--skeleton-only`, and nothing in `src/hx/` mentions it. So
`packaging/e2e-deploy.sh` is calling something the build lane has not shipped: a gtm↔build
sequencing gap, reported to gtm with the cause and touched by me not at all. `tests/guard` and
`tests/ui` both pass, which is what ui-5's done condition names.

**One transient, chased and explained.** All 14 `test_expected_board_is_what_hx_board_actually_prints`
cases across both packs failed in one run and passed on the next with nothing changed here. All
of them failing together and recovering together is the signature of `python -m hx board`
failing to import while `src/hx/**` was mid-write. I chased the obvious suspect first — that
the live `partner` session changes the board text — and **disproved it** on a private tmux
server: for these states `hx board` prints the same text either way. Recorded because a
disproved hypothesis is worth as much as a confirmed one to whoever reads this next.

`tests/ui` is up from 280 at the close of ui-4. The new coverage is the seven items above plus
the 14-step parametrisation (three assertions each).

**On the browser.** ui-4 flagged that no browser tool exists in this session, and that is still
true — the only fetch tool refuses localhost. So item 4 is the one I cannot check the way it
should be checked: the sticky column and the nav/LIVE ordering are asserted as CSS rules, which
proves the rules are present and not that they look right at 375 px. Everything else in this
goal was verified against rendered output. If the next browser pass finds item 4 wrong, the
rules are in one place (`style.css`, the `@media (max-width: 640px)` block and the
`.scroll table` rules) and the fix will not touch the views.

### Live, against an M8 step-4 instance

Built with `packlib`, the pack's real orders and personas, the real skeleton `PARTNER.md`, a
pane log for `eng-002`, and `orders/eng-001.md` deleted so the new badge has something to say.

```
$ .venv/bin/python -m hx.ui --root <scratch>/m8 --port 8811
/api/board               200      /api/show/partner        200
/api/orders              200      /api/show/eng-001        200
/api/archive             200      /api/show/eng-002        200

$ curl …/api/orders
  partner   order=present  path=orders/partner.md   file_matches_record=True
  eng-001   order=null     path=None                file_matches_record=False   → "order file missing"
  eng-002   order=present  path=orders/eng-002.md   file_matches_record=True
```

Rendered, all five views, no error banner:

```
orders badges     : complete, decision, done, met, order file missing, working, —
PARTNER.md tables : pod, role, what it is for, notes | date, question, asked in chat |
                    date, decision, why | date, id, outcome, what landed
agent eng-001     : switcher = [partner, eng-002]   pane = "● dead · 0 lines · no session, no log"
agent eng-002     : switcher = [partner, eng-001]   pane = "● dead · 2 lines · from the log"
```

Server-sent events — appending a `## Tasks` line to a work item, then touching an order:

```
$ curl -sN -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8811/api/events
: hx ui events

data: {"changed": ["eng-002"]}

data: {"changed": ["partner"]}
```

`/api/board`:

```json
{
  "errors": [
    "seed/token: missing; the human runs `claude setup-token` once and pastes the token there, mode 0600 (spec 11 Auth)"
  ],
  "items": [
    {
      "after": [],
      "completed": null,
      "context_tokens": null,
      "dispatched": "2026-09-20T12:00:00Z",
      "file": "pods/partner/partner-working.md",
      "goal_pending": false,
      "goal_ts": "2026-09-20T12:00:00Z",
      "id": "partner",
      "open_subagents": 0,
      "outcome": null,
      "pod": "partner",
      "ready": true,
      "role": "partner",
      "seams": null,
      "session_alive": true,
      "state": "working",
      "turn_ts": null
    },
    {
      "after": [],
      "completed": "2026-09-20T12:00:00Z",
      "context_tokens": null,
      "dispatched": "2026-09-20T12:00:00Z",
      "file": "pods/engineers/eng-001-complete.md",
      "goal_pending": false,
      "goal_ts": null,
      "id": "eng-001",
      "open_subagents": 0,
      "outcome": "done",
      "pod": "engineers",
      "ready": true,
      "role": "engineer",
      "seams": null,
      "session_alive": false,
      "state": "complete",
      "turn_ts": null
    },
    {
      "after": [
        "eng-001"
      ],
      "completed": "2026-09-20T12:00:00Z",
      "context_tokens": null,
      "dispatched": "2026-09-20T12:00:00Z",
      "file": "pods/engineers/eng-002-complete.md",
      "goal_pending": false,
      "goal_ts": null,
      "id": "eng-002",
      "open_subagents": 0,
      "outcome": "decision",
      "pod": "engineers",
      "ready": true,
      "role": "engineer",
      "seams": null,
      "session_alive": false,
      "state": "complete",
      "turn_ts": null
    }
  ],
  "root_abs": "/private/tmp/claude-501/-Users-loganrobbins-workspace-hx/cd595057-fbf9-4c6e-ac74-30de1b461e27/scratchpad/m8",
  "ts": "2026-09-20T22:25:46Z"
}```

`/api/orders` — `eng-001`'s file deleted, the other two intact (order text clipped):

```json
{
  "errors": [],
  "graph": {
    "edges": [
      {
        "from": "eng-001",
        "met": true,
        "to": "eng-002"
      }
    ],
    "nodes": [
      {
        "id": "partner",
        "outcome": null,
        "ready": true,
        "state": "working"
      },
      {
        "id": "eng-001",
        "outcome": "done",
        "ready": true,
        "state": "complete"
      },
      {
        "id": "eng-002",
        "outcome": "decision",
        "ready": true,
        "state": "complete"
      }
    ]
  },
  "orders": [
    {
      "addenda": [],
      "after": [],
      "file_matches_record": true,
      "id": "partner",
      "order": "## Order\n\nShip `--upper` and `--lang` on the `greet` CLI, as the human asked for in chat.\n\nThe human wants two things in\n\u2026(2349 chars; rendered in full)\u2026",
      "path": "orders/partner.md",
      "pod": "partner",
      "ready": true,
      "record": {
        "addenda": [],
        "after": [],
        "completed": null,
        "dispatched": "2026-09-20T12:00:00Z",
        "order": "## Order\n\nShip `--upper` and `--lang` on the `greet` CLI, as the human asked for in chat.\n\nThe human wants two things in\n\u2026(2349 chars; rendered in full)\u2026",
        "outcome": null
      },
      "state": "working",
      "waiting_on": []
    },
    {
      "addenda": [],
      "after": [],
      "file_matches_record": false,
      "id": "eng-001",
      "order": null,
      "path": null,
      "pod": "engineers",
      "ready": true,
      "record": {
        "addenda": [],
        "after": [],
        "completed": "2026-09-20T12:00:00Z",
        "dispatched": "2026-09-20T12:00:00Z",
        "order": "## Order\n\nAdd an `--upper` flag to the `greet` CLI in `greet.py`.\n\n`greet.py World` prints `Hello, World!` today. With `\n\u2026(2122 chars; rendered in full)\u2026",
        "outcome": "done"
      },
      "state": "complete",
      "waiting_on": []
    },
    {
      "addenda": [
        {
          "path": "orders/eng-002.addendum.md",
          "text": "Answering the open decision in your Digest: **fall back to English, and warn on stderr.**\n\n`greet.py --lang xx World` pr\n\u2026(1333 chars; rendered in full)\u2026",
          "ts": null
        }
      ],
      "after": [
        "eng-001"
      ],
      "file_matches_record": true,
      "id": "eng-002",
      "order": "## Order\n\nAdd a `--lang` flag to the `greet` CLI in `greet.py`, on top of the `--upper` flag `eng-001`\nhas already lande\n\u2026(3126 chars; rendered in full)\u2026",
      "path": "orders/eng-002.md",
      "pod": "engineers",
      "ready": true,
      "record": {
        "addenda": [],
        "after": [
          "eng-001"
        ],
        "completed": "2026-09-20T12:00:00Z",
        "dispatched": "2026-09-20T12:00:00Z",
        "order": "## Order\n\nAdd a `--lang` flag to the `greet` CLI in `greet.py`, on top of the `--upper` flag `eng-001`\nhas already lande\n\u2026(3126 chars; rendered in full)\u2026",
        "outcome": "decision"
      },
      "state": "complete",
      "waiting_on": []
    }
  ],
  "root_abs": "/private/tmp/claude-501/-Users-loganrobbins-workspace-hx/cd595057-fbf9-4c6e-ac74-30de1b461e27/scratchpad/m8",
  "ts": "2026-09-20T22:25:46Z"
}
```

## Live against Claude Code vs. against the fake

Nothing ran against Claude Code or the fake `claude`, and nothing here needs to. What it ran
against rather than a mock: the build lane's **real functions in process**, **14 real
`HARNESS_ROOT`s** built by the gtm lane's own `packlib` in both pack shapes, the **real
skeleton `PARTNER.md`**, the real `http.server` over TCP, and the real `static/app.js` under
node 26.5.0. Pane capture against real tmux on a private socket is unchanged and still passing.

The one deliberate *avoidance* of the real thing is item 7: this lane must not touch the live
`partner` session the build lane is running, so every instance is read through a tmux server
that does not exist.

## The build lane's build-3 handoff

`handoff/build-to-ui.md` (build-3, the context file and step state) was unmarked when this goal
started. Read, applied, marked `DONE 2026-09-20`, with three decisions recorded there:

- **The context file is rendered as markdown now**, not dumped into a `<pre>`. They are right
  that it is meant to be read, and its `_source: \`path\`_` lines were unreadable preformatted —
  which is what turned up the missing underscore emphasis above.
- **Step state stays rendered from the raw JSON**, not from their `render_step_state`. Not a
  rejection: the web view already does things a markdown string cannot — next actions in
  colour, commit shas as styled code, `verified` as a pill, blockers in red, one card per
  stream — and collapsing that to markdown would lose it. They keep `step_state` raw in
  `hx show --json`, which is exactly what makes that possible.
- **Showing "who this agent is" from `config/<id>/AGENTS.md` is a good idea and I did not do
  it.** It is outside ui-5, and it wants a `CONTRACTS.md` line first: `hx show --json` gives
  `persona_path` but not the text, and the UI should not start reading instance files directly
  when everything else comes through their functions.

## Open questions

1. **Item 4 is asserted as CSS, not as pixels** — no browser tool in this session. The one item
   in this goal that a human eye should confirm once.
2. **The persona text** (above) — wants a contract line before the UI reads it.
3. **`hx metrics` is still M7**, so the Agent view's metrics table shows "not yet" on every real
   instance. Unchanged from ui-4; flagging so it is not read as a regression from the table
   work in ui-3.

## Handoff entries written

- `handoff/build-to-ui.md` — build-3 read, applied and marked `DONE 2026-09-20`, with the three
  decisions above, and one thing back to them: `hx board` matching sessions by bare id means a
  scratch instance reports this machine's sessions as its own, which is a test-flake shape
  rather than an hx bug, and `HX_TMUX` is the fix.
- `handoff/ui-to-gtm.md` — the UI now runs both their packs at every step; nothing written to
  `tests/scenario/**`, which is theirs; plus the board-`after`-versus-orders-graph distinction
  that my first assertion got wrong, and the same tmux warning.
- Nothing new to `handoff/to-orchestrator.md`: the open items above are questions in this file,
  not contract changes.
