# ui-4 done — bound to the published functions; `hx ui` confirmed; M8 end to end

## What was built

### 1. `InstanceSource` binds to the published functions

`handoff/build-to-ui.md` (build-2) published a plain function per reader. All five are bound
and called **in process**; there is no `hx` subprocess left in any read path.

| `Source` method | bound to | state |
|---|---|---|
| `board()` | `hx.board.collect(root)` | bound |
| `show(id)` | `hx.show.collect(root, id)` | bound |
| `orders()` | `hx.orders.collect(root)` | bound |
| `archive()` | `hx.archive.collect(root)` | bound |
| `wake_partner_status(text)` | `hx.wake.wake_partner_status(root, text)` | bound |
| `metrics` | `hx.metrics.collect` | **M7; nothing to bind yet** |

The binding is a lazy import per reader, so one missing module cannot take the others down.
`metrics` is in the table pointing at `hx.metrics` so it binds itself the moment M7 lands, and
a test asserts it is `None` today so the entry cannot rot unnoticed. No UI change will be
needed when it arrives: the Agent view reads `metrics` out of `hx.show.collect`'s document.

**The fallback is a live path, not dead code.** `run_hx` remains for any reader whose import
fails, and `InstanceSource(root, prefer_subprocess=True)` forces it. Three readers are asserted
to return byte-identical documents down both routes, so the fallback is a different road to the
same answer rather than a second implementation.

**The raise contract**, implemented exactly as published: `hx.errors.NotFound` → **404** with
its message shown; `ValidationError` and `OSError` → **502**; `NotFound` caught first because
it subclasses `HxError`. Both paths tested over HTTP, the 502 against a deliberately broken
instance. `board`, `orders` and `archive` never raise for a bad file — their `errors` list is
rendered, which is the same rule `run_hx` applied to exit 1.

**`wake_partner_status` earned its keep.** The chat box no longer says "not delivered: no
Partner socket, or the connection was refused" — it says which, in terms of what to do:

- `no-socket` → "the Partner has no messaging socket yet — it has not started a session. Check
  `tmux attach -t partner`."
- `refused` → "the Partner's socket refused the connection — it may be stale. `hx restart
  partner` rewrites it at the next SessionStart."

`POST /api/partner/wake` now answers `{"delivered": bool, "status": "accepted|no-socket|refused"}`.
`delivered` is unchanged for anything that was reading it.

### 2. `hx ui` — confirmed working

It exists and calls `serve(root, args.port)` exactly as the ui-2 handoff specified. Rather than
take that on inspection, there is a test that runs `hx ui --port N` as a subprocess against a
real instance, waits for `run/ui-token` to appear, and fetches `/api/board` over the port it
was given — plus one that it refuses a `HARNESS_ROOT` that does not exist. So
`python -m hx.ui --root` is no longer the documented entry; `hx ui` is, and both work.

### 3. A real instance, end to end

`hx install --skeleton-only`, then the M8 pack placed as its README says — the pack's real
`orders/*.md` and both personas — at **observation point 4**: the Partner working, `eng-001`
`complete/done`, `eng-002` `complete/decision` behind it. `tasks.json` records what
`hx dispatch` records (`parse_order(...).text`, not the raw file), so `file_matches_record`
means something. No agent is launched, so pane capture finds no session; `eng-002` has a pane
log placed at `logs/eng-002/eng-002-pane.log` and falls back to it, `partner` has none and says
so.

## How it was verified

```
$ ./tools/milestone-check.sh
milestone-check PASS (exit 0)

$ .venv/bin/python -m pytest tests/guard
5 passed in 0.43s

$ .venv/bin/python -m pytest tests/ui
280 passed, 1 skipped in 10.30s
```

`tests/ui` is 280 passed / 1 skipped, up from 249 at the close of ui-3. Two new files:
`test_binding.py` (the binding, both paths, the raise contract, `hx ui`) and
`test_m8_instance.py` (the pack placed, every view against it, the pane-log fallback).

**On the browser walk.** The goal asks for every view walked in a browser. No browser tool is
available in this session — the only fetch tool refuses localhost, and there is no in-app
Browser here — so I did the closest equivalent that actually catches broken rendering rather
than claiming a walk I did not do: every view was rendered through the node DOM harness
against **documents pulled from the live M8 instance**, and inspected. That is weaker than a
human eye for layout and stronger than one for content. It found three things, all fixed:

1. **"after graph · 1 edges"** — counts were string-concatenated. There is now a `count()`
   helper and the headings read "1 edge", "3 items", "10 lines".
2. **"orders · 3 orders"** — redundant after the first fix; the heading says "orders · 3".
3. **A context file hx has named but not written rendered as "0 chars"** with an empty `<pre>`.
   On a real instance `context_file.text` is `null` until the first compose, so it now reads
   `run/eng-001/eng-001-main.context.md · not composed yet` and shows no empty box. This is
   exactly the class of thing only real data surfaces — the hand-written fixtures all have a
   context file.

The session-scoped manifest checks still hold: both the plain scratch instance and the M8
instance are byte-identical — contents, mode and mtime — after the whole run, except
`run/ui-token`.

### Served: `hx ui` against the M8 instance

```
$ .venv/bin/python -m hx.ui --root <scratch>/m8 --port 8802
hx ui (…/scratchpad/m8): http://127.0.0.1:8802/
hx ui token: VlBhmzhj1NEfO5RnGndsgkSnOyzZ7wSJ76QpKZqN4Ws

$ for p in board orders archive show/partner show/eng-001 show/eng-002; do …; done
/api/board               200      /api/show/partner        200
/api/orders              200      /api/show/eng-001        200
/api/archive             200      /api/show/eng-002        200
/api/show/eng-404        404      / (no token)             200

$ curl -X POST … -d '{"text":"eng-002 needs a decision"}' …/api/partner/wake
{ "delivered": false, "status": "no-socket" }
```

`/api/board`:

```json
{
  "errors": [
    "pods/partner/partner-working.md: no live tmux session partner"
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
      "session_alive": false,
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
  "ts": "2026-09-20T21:33:03Z"
}```

`/api/show/eng-002` — the item stopped on a decision, its pane served from the log
(order and body clipped; both render in full in the view):

```json
{
  "archive": [],
  "bench": [],
  "context_file": {
    "path": "run/eng-002/eng-002-main.context.md",
    "seam_ts": null,
    "text": null
  },
  "file": "pods/engineers/eng-002-complete.md",
  "id": "eng-002",
  "metrics": null,
  "pane": {
    "alive": false,
    "error": "can't find session: eng-002",
    "lines": [
      "> hx task",
      "## Order",
      "Add a --lang flag to the greeter.",
      "The unknown-language behaviour is a public-contract choice.",
      "> python3 -m unittest discover -s tests -q",
      "OK",
      "I have built everything that does not depend on the open question and committed it.",
      "Writing the question into ## Open decision and stopping.",
      "> hx complete decision",
      "HX-COMPLETE eng-002 decision"
    ],
    "session": "eng-002",
    "source": "log"
  },
  "persona_path": null,
  "pod": "engineers",
  "role": "engineer",
  "state": "complete",
  "step_state": {},
  "streams": [],
  "subagents": {},
  "task": {
    "addenda": [],
    "after": [
      "eng-001"
    ],
    "completed": "2026-09-20T12:00:00Z",
    "dispatched": "2026-09-20T12:00:00Z",
    "order": "## Order\n\nAdd a `--lang` flag to the `greet` CLI in `greet.py`, on top of the `--upper` flag `eng-001`\nhas already landed.\n\n`--lang fr World\n\u2026(3126 chars; rendered in full in the view)\u2026",
    "outcome": "decision"
  },
  "work_item": {
    "body": "## Order\n\nscenario fixture\n\n## Definition of done\n\n1. n/a\n\n## Standing instructions\n- Keep `## Tasks` current: mark a task done the moment i\n\u2026(947 chars; rendered in full in the view)\u2026",
    "frontmatter": {
      "after": [
        "eng-001"
      ],
      "dispatched": "2026-09-20T12:00:00Z",
      "id": "eng-002",
      "outcome": "decision",
      "pod": "engineers"
    }
  }
}
```

`/api/orders` — the `after` chain, met (order text clipped):

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
      "order": "## Order\n\nShip `--upper` and `--lang` on the `greet` CLI, as the human asked for in chat.\n\nThe human wants two things in the `greet` repo, a\n\u2026(2349 chars; rendered in full in the view)\u2026",
      "path": "orders/partner.md",
      "pod": "partner",
      "ready": true,
      "record": {
        "addenda": [],
        "after": [],
        "completed": null,
        "dispatched": "2026-09-20T12:00:00Z",
        "order": "## Order\n\nShip `--upper` and `--lang` on the `greet` CLI, as the human asked for in chat.\n\nThe human wants two things in the `greet` repo, a\n\u2026(2349 chars; rendered in full in the view)\u2026",
        "outcome": null
      },
      "state": "working",
      "waiting_on": []
    },
    {
      "addenda": [],
      "after": [],
      "file_matches_record": true,
      "id": "eng-001",
      "order": "## Order\n\nAdd an `--upper` flag to the `greet` CLI in `greet.py`.\n\n`greet.py World` prints `Hello, World!` today. With `--upper` it must pri\n\u2026(2122 chars; rendered in full in the view)\u2026",
      "path": "orders/eng-001.md",
      "pod": "engineers",
      "ready": true,
      "record": {
        "addenda": [],
        "after": [],
        "completed": "2026-09-20T12:00:00Z",
        "dispatched": "2026-09-20T12:00:00Z",
        "order": "## Order\n\nAdd an `--upper` flag to the `greet` CLI in `greet.py`.\n\n`greet.py World` prints `Hello, World!` today. With `--upper` it must pri\n\u2026(2122 chars; rendered in full in the view)\u2026",
        "outcome": "done"
      },
      "state": "complete",
      "waiting_on": []
    },
    {
      "addenda": [
        {
          "path": "orders/eng-002.addendum.md",
          "text": "Answering the open decision in your Digest: **fall back to English, and warn on stderr.**\n\n`greet.py --lang xx World` prints `Hello, World!`\n\u2026(1333 chars; rendered in full in the view)\u2026",
          "ts": null
        }
      ],
      "after": [
        "eng-001"
      ],
      "file_matches_record": false,
      "id": "eng-002",
      "order": "## Order\n\nAdd a `--lang` flag to the `greet` CLI in `greet.py`, on top of the `--upper` flag `eng-001`\nhas already landed.\n\n`--lang fr World\n\u2026(3183 chars; rendered in full in the view)\u2026",
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
        "order": "## Order\n\nAdd a `--lang` flag to the `greet` CLI in `greet.py`, on top of the `--upper` flag `eng-001`\nhas already landed.\n\n`--lang fr World\n\u2026(3126 chars; rendered in full in the view)\u2026",
        "outcome": "decision"
      },
      "state": "complete",
      "waiting_on": []
    }
  ],
  "root_abs": "/private/tmp/claude-501/-Users-loganrobbins-workspace-hx/cd595057-fbf9-4c6e-ac74-30de1b461e27/scratchpad/m8",
  "ts": "2026-09-20T21:33:03Z"
}
```

Server-sent events, touching one work item, then `tasks.json`, then an order file:

```
$ curl -sN -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8802/api/events
: hx ui events

data: {"changed": ["eng-002"]}

data: {"changed": ["tasks"]}

data: {"changed": ["partner"]}
```

Each maps as CONTRACTS.md says: a work item and an order file to their id, `tasks.json` to the
reserved `tasks` scope, with no fan-out.

## Live against Claude Code vs. against the fake

Nothing ran against Claude Code or the fake `claude`, and nothing here needs to — the UI
launches no agent and reads no transcript. What it ran against rather than a mock: the build
lane's **real functions in process**, a **real `HARNESS_ROOT`** in the M8 shape from the gtm
lane's own pack, the **real `hx ui` subcommand** as a subprocess, a **real `http.server`** over
TCP, and the **real `static/app.js`** under node 26.5.0. Pane capture against real tmux on a
private socket is unchanged from ui-2 and still passing; the pane-log fallback is exercised
here with a real log file.

## Open questions

1. **No browser tool in this session.** Item 3 asked for a walk in a browser; I substituted the
   DOM harness against live data and said so above. If a visual pass matters for M9 sign-off —
   layout, contrast, the metrics table at narrow widths — it needs either a session with a
   browser or a human looking once. Everything I *can* check automatically is checked.
2. **`metrics` is bound to nothing until M7.** The Agent view therefore shows "not yet" for
   every real instance today. Nothing to do; flagging so it is not read as a UI gap.
3. **The M8 instance is built at one observation point.** I used step 4 because it is the
   richest single state (a `decision`, a met `after`, a mix of `working` and `complete`). The
   other seven are in `tests/scenario/test_m8_pack.py`'s `STEPS`; if you want the UI asserted
   at each, say so and it is a parametrised fixture rather than new machinery.

## Handoff entries written

- `handoff/build-to-ui.md` — the build-2 entry read, applied and marked `DONE 2026-09-20`, with
  a reply recording what was bound, that no `hx` subprocess remains in the read path, that the
  raise contract is implemented as written, that `wake_partner_status` is what let the page say
  *which* of the three happened, that `hx ui` works and is tested, and that `metrics` is the one
  entry with nothing behind it yet.
- No new entry to `handoff/to-orchestrator.md`: the three open items above are questions for you
  in this file, not contract changes. `handoff/ui-to-gtm.md` and `handoff/ui-to-build.md` need
  nothing new — the static file list is unchanged, as ui-3 promised it would be.
