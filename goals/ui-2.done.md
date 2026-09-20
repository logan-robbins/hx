# ui-2 done — M9 part 2: Agent and Partner views, real instance source

## What was built

| File | What changed |
|---|---|
| `src/hx/ui/pane.py` | **new.** `capture()`, `session_alive()`, `log_fallback()`, `strip_ansi()`, `pane_targets()` — adapted from autodev's `fleet.py` per spec 16.4 |
| `src/hx/ui/data.py` | `InstanceSource` is real: `run_hx()` is the one seam, plus `CommandError`. `FixtureSource` and `scan()` unchanged |
| `src/hx/ui/server.py` | token transport is now an `HttpOnly` cookie; `serve(root, port=None)` is the `hx ui` entry |
| `src/hx/ui/static/app.js` | Agent and Partner views, a markdown renderer, board rows open an agent; no token anywhere |
| `src/hx/ui/static/index.html` | no token in any URL |
| `src/hx/ui/static/style.css` | styles for the two new views |
| `tests/ui/` | `test_pane.py` and `test_instance_source.py` new; `conftest.py` builds a real instance; auth and view tests updated |

### 1–2. Agent and Partner views (spec 16.2)

The Agent view renders, in order: the work item (frontmatter as a table, `## Order` with its
addenda, `## Tasks` live as checkboxes, deliverables, commands, open decision, digest); step
state per stream (open steps with their next action, closed steps with their commit sha,
working set, blockers, dead ends); the last context file with its seam timestamp and size;
every stream tail with the seam record marked and its context-file size shown (spec 7.4);
subagent handles against their `claude agent_id` and digests; `hx metrics`; and the pane.
Absent values render as "not yet", never an empty box. Every board row's id is a button that
opens that agent; with no id chosen the view is a picker.

The Partner view renders `partner_md`, the whole board, a chat box that POSTs to
`/api/partner/wake` and reports whether it was delivered, the Partner's pane as the reply
stream, and the line saying full control stays `tmux attach -t partner`.

Markdown is rendered by a small subset renderer (headings, fenced code, lists and task
checkboxes, blockquotes, inline code/bold/italic). Every node is built with `textContent`, so
nothing a HarnessAgent writes into a work item can inject markup into the page.

### 3. `InstanceSource`

`run_hx(root, args)` is the single seam: it sets `HARNESS_ROOT`, unsets `HARNESS_ID` (the UI is
not an agent, and spec 08 has Partner commands refuse a foreign id), and treats **exit 1 with a
JSON document on stdout as data** — `hx board` exits 1 whenever `errors` is non-empty and the
board still has to render. A "not implemented" message becomes `SourceUnavailable` (HTTP 503)
carrying hx's own words; anything else becomes `CommandError` (HTTP 502).

`show(id)` overlays a live `pane.capture()` on the document, because spec 16.2 refreshes the
pane on the SSE tick rather than showing whatever `hx show` happened to catch.

`wake_partner(text)` passes the text as one argv element — the one place text crosses a process
boundary, and it is hx's own fixed-form message, not an order.

### 4. `serve(root, port=None)`

`port` is positional so `hx ui` can pass an override straight through; omitted, `serve` reads
`config/ui.json` itself. The request for the subcommand is in `handoff/ui-to-build.md`.

### 6. Token transport is now a cookie

`GET /` sets `hx_ui_token=<token>; HttpOnly; SameSite=Strict; Path=/`. `Authorization: Bearer`
still works for API clients. **`?token=` is no longer accepted at all** — accepting it would
have kept the leak the change exists to close, so the tests now assert it is rejected. No
`Secure` flag: this server is http on 127.0.0.1 and a `Secure` cookie would never be sent.

Because the cookie is `HttpOnly`, the token is no longer written into the page: `index.html`
lost its bootstrap block, `app.js` cannot read the token, and no URL carries one.
`test_the_token_never_reaches_the_page` asserts that over `/`, `app.js` and `style.css`.

## Two real bugs this goal's tests caught

1. **`pane.py` used an invalid tmux pane target.** `=name` is tmux's exact-match *session*
   target and works for `has-session`, but a *pane* target is `session:window.pane` and tmux
   rejects a bare `=eng-001` with `can't find pane`. Every capture was silently falling through
   to the log fallback. Fixed to `=<session>:main`, falling back to `=<session>:`, which also
   made it correct in a way it had not been: `hx launch` runs the agent in window `main` and
   its Companion in `companion` (spec 08), and the Agent view wants the agent.
   `test_capture_reads_the_main_window_not_the_companion` pins it.
2. **`el()` flattened children only one level**, so a view that nests a heading with a mapped
   list appended the inner `Array` object itself. Now `flat(Infinity)`, and the DOM shim throws
   on anything that is not a node or a string rather than rendering nothing.

## How it was verified

```
$ ./tools/milestone-check.sh
milestone-check: PASS (exit 0)

$ .venv/bin/python -m pytest tests/guard
5 passed in 0.20s

$ .venv/bin/python -m pytest tests/ui
213 passed, 1 skipped in 7.53s

$ .venv/bin/python -m pytest
530 passed, 1 skipped in 20.64s
```

- **Views** — `tests/ui/test_views_js.py` runs the real `static/app.js` under node against a
  DOM shim and asserts on what it produced: every section spec 16.2 names, the four `## Tasks`
  checkboxes with two done and two open, the open step's next action, the closed step's commit
  sha, the working-set note, the dead end, the context file with its seam, each stream tail
  with the seam's context-file size, subagent ids and digests, the pane lines, "not yet" for
  absent values, and for the Partner every line of `partner_md`, the whole board, the chat POST
  body and the `tmux attach -t partner` line.
- **Pane** — `tests/ui/test_pane.py` runs a **real tmux on a private `-L` socket** (never the
  user's server or any lane's session), waits by polling rather than sleeping, and covers ANSI
  stripping including OSC and hyperlink sequences, the 120-line limit, `main` vs `companion`,
  the session/log/none sources, a live pane beating a stale log, exact session matching
  (`eng-001` never matches `eng-0011`), and that capture creates and disturbs nothing.
- **`InstanceSource`** — `tests/ui/test_instance_source.py` runs against a real `HARNESS_ROOT`
  built by `.venv/bin/hx install --skeleton-only` plus hand-made work items and `tasks.json`,
  and against a stub `hx` for the parse path of every reader. The session-scoped
  `instance_root` fixture diffs a manifest of that instance — contents, mode and mtime of every
  file — across the whole test run and **allows only `run/ui-token`**.

### Served against a scratch instance

```
$ .venv/bin/python -m hx.ui --root <scratch> --port 8792
hx ui (…/scratchpad/inst): http://127.0.0.1:8792/
hx ui token: Carhz4tr1vp4mDuwdRjV5f6elq6dynBAPC_b0IKyoXY

$ ls -l <scratch>/run/ui-token
-rw-------  1 loganrobbins  wheel  44 …/run/ui-token

$ curl -s -D - -o /dev/null http://127.0.0.1:8792/ | grep -i set-cookie
Set-Cookie: hx_ui_token=Carhz…; HttpOnly; SameSite=Strict; Path=/

# the browser's path: GET / for the cookie, then nothing else carries a token
$ curl -c jar -o /dev/null http://127.0.0.1:8792/ && curl -b jar -w '%{http_code}\n' …/api/board
200
$ curl -b jar -w '%{http_code}\n' …/static/app.js
200
$ curl -w '%{http_code}\n' …/api/board        # no cookie, no header
401
```

`/api/board` against that scratch instance, in full:

```json
{
  "errors": [
    "pods/partner/partner-working.md: no live tmux session partner",
    "pods/partner/partner-working.md: working with no run/partner/goal marker; a live pane on a working item with no goal marker is a violation (spec 06)",
    "pods/engineers/eng-001-working.md: no live tmux session eng-001",
    "pods/engineers/eng-001-working.md: working with no run/eng-001/goal marker; a live pane on a working item with no goal marker is a violation (spec 06)"
  ],
  "items": [
    {
      "after": [],
      "completed": null,
      "context_tokens": null,
      "dispatched": "2026-09-20T12:00:00Z",
      "file": "pods/partner/partner-working.md",
      "goal_pending": false,
      "goal_ts": null,
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
      "completed": null,
      "context_tokens": null,
      "dispatched": "2026-09-20T12:00:00Z",
      "file": "pods/engineers/eng-001-working.md",
      "goal_pending": false,
      "goal_ts": null,
      "id": "eng-001",
      "open_subagents": 0,
      "outcome": null,
      "pod": "engineers",
      "ready": true,
      "role": "engineer",
      "seams": null,
      "session_alive": false,
      "state": "working",
      "turn_ts": null
    }
  ],
  "root_abs": "/private/tmp/claude-501/-Users-loganrobbins-workspace-hx/cd595057-fbf9-4c6e-ac74-30de1b461e27/scratchpad/inst",
  "ts": "2026-09-20T20:49:31Z"
}```

Server-sent events against the same real instance — `touch pods/engineers/eng-001-working.md`,
then `touch tasks.json`:

```
$ curl -sN -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8792/api/events
: hx ui events

data: {"changed": ["eng-001"]}

data: {"changed": ["tasks"]}
```

## `/api/show/partner` is a 503, as you directed

`handoff/orchestrator-to-ui.md` (2026-09-20) says to close ui-2 without `hx show`, record the
503 and paste `/api/board` instead. That entry is applied and marked `DONE 2026-09-20`. What
the endpoint actually returns today:

```
$ curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8792/api/show/partner
{
  "error": "hx: show: not implemented (build-10)"
}
```

Every reader that build-2 still owes, as served right now:

```
/api/show/partner      503  hx: show: not implemented (build-10)
/api/show/eng-001      503  hx: show: not implemented (build-10)
/api/orders            503  hx: orders: not implemented (build-2)
/api/archive           503  hx: archive: not implemented (build-2)
POST /api/partner/wake 503  hx: wake: not implemented (build-2)
```

`show` says **build-10** because `cli.py`'s `NOT_IMPLEMENTED` map still carries the old number;
`goals/build-2.md` item 9 delivers it. The UI prints hx's string verbatim, so a human reading
the Agent view today is told to wait for build-10 when the answer is build-2. Filed to the
build lane; nothing breaks either way.

**None of this is guesswork waiting to be rewritten.** The parse path of all five readers is
tested against a stub `hx` that emits the ui-1 fixtures, and the server is tested serving every
view off that stub. When build-2 lands they go live with no change here.

## Live against Claude Code vs. against the fake

Nothing in this goal ran against Claude Code or the fake `claude`, and nothing in it needs to:
the UI launches no agent and reads no transcript. What it *did* run against, rather than a
mock:

- **real tmux** (3.7c) on a private socket, for every pane test;
- **a real `HARNESS_ROOT`**, built by the build lane's own `hx install --skeleton-only`;
- **the real `hx` binary** as a subprocess, which is how `board()` is live today and how the
  503s above are produced;
- **a real `http.server` over a real TCP socket**, with `curl`'s own cookie jar for the browser
  flow;
- **the real `static/app.js`** under node 26.5.0.

The pane log fallback (`logs/<id>/<id>-pane.log`) is tested against hand-written log files.
Nothing writes that file yet — `tmux pipe-pane` is build-2 item 12.

## Open questions

None outstanding. Both ui-2 handoffs were raised and answered inside this goal
(`handoff/orchestrator-to-ui.md`, marked `DONE 2026-09-20`):

1. **Sequencing** — close ui-2 without `hx show`; switching `InstanceSource` to the real
   commands and then to the Python functions is **ui-3**, which you send after build-2 lands.
   Done as directed.
2. **Pane log** — `start.sh` will `tmux pipe-pane -o` into `logs/<id>/<id>-pane.log`, now in
   spec 03 and 11 and build-2 item 12. `log_fallback` already read exactly that path; kept as
   written, and its docstring now cites the spec instead of the open question.

One thing for ui-3 beyond the swap: the Agent view's `metrics` section renders the object as
pretty-printed JSON, because `hx metrics` has no shape in `CONTRACTS.md` and spec 07.4
describes it only in prose. If you want it rendered as a table, it needs a contract first.

## Handoff entries written

- `handoff/to-orchestrator.md` — two entries, both answered and applied: the `hx show`
  sequencing conflict, and the missing pane-log definition.
- `handoff/ui-to-build.md` — one new entry: the `hx ui` subcommand request (exact `serve`
  signature, that it blocks, that it writes only `run/ui-token`), the stale `"show": 10` entry
  in `cli.py`'s `NOT_IMPLEMENTED` map, the five commands `InstanceSource` shells out to today,
  and what `handoff/build-to-ui.md` needs to name for ui-3 — module path, signature, return
  value, and what each raises for an unknown id, so the UI can tell 404 from 502.
- `handoff/orchestrator-to-ui.md` — read, applied, and marked `DONE 2026-09-20` in place, with
  a note recording what was done for each item.
