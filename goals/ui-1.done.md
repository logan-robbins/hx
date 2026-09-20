# ui-1 done — M9 part 1 (spec 16): server, board, orders, archive from fixtures

## What was built

`src/hx/ui/` (new package, stdlib only, no build step, no CDN, no framework):

| File | What |
|---|---|
| `data.py` | `Source` — the one interface every view reads through: `board()`, `show(id)`, `orders()`, `archive()`, `wake_partner(text)`, plus `scan()` for the SSE sweep. `FixtureSource` (ships now) and `InstanceSource` (readers land in ui-2; its `scan()` is complete because it is pure filesystem) |
| `server.py` | `http.server` on `127.0.0.1`, bearer token, every route, SSE watcher, `serve(root)` / `serve_fixtures(dir)` / `main()` |
| `__main__.py` | `python -m hx.ui --root <root>` and `python -m hx.ui --fixtures <dir>` |
| `static/index.html`, `static/app.js`, `static/style.css` | Board, Orders, Archive, and the shell navigation for Agent and Partner (those two views are ui-2) |

`tests/ui/`: 151 tests, 1 skipped (`test_auth.py`, `test_endpoints.py`, `test_events.py`,
`test_wake.py`, `test_instance.py`, `test_fixtures_contract.py`, `test_views_js.py`), plus
`fixtures/` (5 files) and `js/` (the headless DOM harness).

### Endpoints

`GET /` (the one unauthenticated response; it carries the token to the page), `GET /static/*`,
`GET /api/board`, `GET /api/show/<id>`, `GET /api/orders`, `GET /api/archive`,
`GET /api/events` (SSE), `POST /api/partner/wake`. Nothing else is routed; everything else is
404. `POST` is routed to the wake and nowhere else.

**Token.** Read from `run/ui-token`, created with mode 0600 if missing (`instance_token`);
port from `config/ui.json`, default 8765 (`instance_port`). Accepted as
`Authorization: Bearer <t>` **or** `?token=<t>`, because `<link>`, `<script>` and
`EventSource` cannot set a request header and spec 16.1 requires the token on every request
but the static index. In `--fixtures` mode the token is ephemeral and printed to stdout: the
fixture tree is never written to, which is what lets the "no endpoint mutates the instance"
assertion be exact.

**SSE.** One `Watcher` thread per server for all browsers: scan, diff, push
`{"changed": [scopes]}`. The interval is `SCAN_INTERVAL = 1.0`, spec 16.1's "once a second";
the tests drive it at 0.05 s so "within 1 s" is a real assertion and not a sleep. No timeouts
anywhere on the wait: the heartbeat is a comment frame the watcher publishes every 15 scans,
so the SSE thread does a blocking `get()` and never polls with a deadline. A scan that raises
(a half-written instance) is swallowed and the sweep continues.

**The only write path** in the whole server is `POST /api/partner/wake` → `Source.wake_partner`.
Asserted directly: a spy source records every call, and the test asserts the call list is
exactly `[("wake_partner", (text,))]` — one call, with the text verbatim, and no read
alongside it — and that the fixture tree manifest is unchanged across it.

### Two defects found and fixed while building

1. A `404`/`401` `POST` answered without draining the request body. On an HTTP/1.1 keep-alive
   connection the undrained JSON body was then parsed as the next request line
   (`Bad HTTP/0.9 request type ('{"text":')`). `do_POST` now drains first, refusal included.
   Regression test: `test_a_refused_post_leaves_the_connection_usable`.
2. A browser closing a tab raised `ConnectionResetError` out of `handle_one_request` and
   `ThreadingHTTPServer` dumped a traceback — a long-running local server would spew these.
   `UIServer.handle_error` now ignores peer-gone errors and `_send` guards its writes.

## How it was verified

All commands run from the repo root with `.venv/bin/python` (3.14.7).

```
$ ./tools/milestone-check.sh
........................................................................ [ 41%]
........................................................................ [ 61%]
..s..................................................................... [ 82%]
.............................................................            [100%]

$ .venv/bin/python -m pytest tests/guard
5 passed in 0.22s

$ .venv/bin/python -m pytest tests/ui
151 passed, 1 skipped in 3.44s

$ .venv/bin/python -m pytest
443 passed, 1 skipped in 13.77s
```

The full-suite count moves between runs because the build and gtm lanes are committing to the
same tree throughout; `tools/milestone-check.sh` exits 0 and `tests/ui` is 151 passed,
1 skipped, which is the part this lane owns.

The one skip is `test_query_token_is_accepted[/api/events]`, skipped by design: a query-token
SSE request would block that test: the SSE tests cover it.

### A guard test failed twice during the goal; both were other lanes', both are fixed

Neither was caused by the ui lane, and neither is outstanding. Recorded because the orchestrator
reads this file to decide the next goal.

1. **At the start of the goal**, both guard tests failed: `test_harness_root_refusal` because
   `python -m hx` did not exist yet, and `test_user_home_untouched` because
   `~/.claude/plugins/known_marketplaces.json` had drifted from the baseline. Fixed mid-goal by
   other lanes — the build lane landed `src/hx/__main__.py`, and the orchestrator fixed the
   manifest in `b934676`. The build lane had already filed the marketplace drift, so the ui lane
   did not duplicate it.
2. **At the end of the goal**, `test_user_home_untouched` failed again on three
   `~/.claude/file-history/0bd6d45e…/…@v1` files — Claude Code's own pre-edit snapshot cache,
   holding copies of `config/eng-001/AGENTS.md`, `SUBAGENTS.md` and `harness.json` written by a
   skeleton-building lane's session, not the ui session (`cd595057…`). The ui lane contains no
   reference to `~/.claude`, `Path.home()` or `expanduser` anywhere in `src/hx/ui/**` or
   `tests/ui/**`. Since `tools/**` is yours and the guard test's docstring forbids editing the
   exclusion list to make it pass, it was filed in `handoff/to-orchestrator.md` with the
   suggested one-line fix rather than fixed here. You then fixed it in `4a30e69` ("guard
   manifest: prune file-history") while the entry was being written; that entry is marked
   `DONE 2026-09-20` in place.

One further transient: `tests/core/test_adapter_start_sh.py::test_refuses_without_a_pinned_binary`
failed once with a `FileNotFoundError` from `subprocess` while the build lane was mid-write, and
passed on the next run without anything changing in this lane. Not filed: it did not reproduce.

Final state: `tools/milestone-check.sh` exits 0 — `tests/guard` 5 passed, `tests/ui` 151 passed
1 skipped, full suite 443 passed 1 skipped. Nothing is outstanding against another lane.

### Served for real

```
$ .venv/bin/python -m hx.ui --fixtures tests/ui/fixtures
hx ui (fixtures tests/ui/fixtures): http://127.0.0.1:8765/
hx ui token: Ml_1g3znPFAsJEgaKeGAe48S4h1RHcwvBWsYbG8E5So

$ curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/api/board
401
$ curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8765/
200
$ curl -s -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8765/api/board
{
  "errors": [
    "pods/research/res-001-working.md: no live tmux session res-001"
  ],
  "items": [
    {
      "after": [],
      "completed": null,
      "context_tokens": 91044,
      "dispatched": "2026-09-20T09:15:00Z",
      "file": "pods/partner/partner-working.md",
      "goal_pending": false,
      "goal_ts": "2026-09-20T09:15:02Z",
      "id": "partner",
      "open_subagents": 0,
      "outcome": null,
      "pod": "partner",
      "ready": true,
      "role": "partner",
      "seams": 4,
      "session_alive": true,
      "state": "working",
      "turn_ts": "2026-09-20T13:09:58Z"
    },
    {
      "after": [],
      "completed": "2026-09-20T11:58:12Z",
      "context_tokens": 132880,
      "dispatched": "2026-09-20T10:00:00Z",
      "file": "pods/engineers/eng-000-complete.md",
      "goal_pending": false,
      "goal_ts": "2026-09-20T10:00:03Z",
      "id": "eng-000",
      "open_subagents": 0,
      "outcome": "done",
      "pod": "engineers",
      "ready": true,
      "role": "engineer",
      "seams": 2,
      "session_alive": true,
      "state": "complete",
      "turn_ts": "2026-09-20T11:58:12Z"
    },
    {
      "after": [
        "eng-000"
      ... (eng-002 queued/unmet, eng-003 complete/decision, res-001 working/session dead)
```

Every other route, live on the same server:

```
/api/orders                200      /static/app.js             200
/api/archive               200      /static/style.css          200
/api/show/partner          200      /static/../data.py         404
/api/show/eng-001          200      /static/style.css?token=…  200 text/css; charset=utf-8
/api/show/nope             404

$ curl -s -X POST -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
       -d '{"text":"eng-003 complete: decision; hx read eng-003"}' \
       http://127.0.0.1:8765/api/partner/wake
{ "delivered": true }
```

SSE, live against a scratch copy of the fixture tree (never the repo's, so the committed
fixtures keep their mtimes) — `touch board.json`, then `touch show-eng-001.json`:

```
$ curl -sN -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8766/api/events
: hx ui events

data: {"changed": ["board"]}

data: {"changed": ["eng-001"]}
```

### The views

`app.js` is rendered headlessly in `tests/ui/test_views_js.py`: `tests/ui/js/render.js` runs
the real `static/app.js` against a minimal DOM shim under node and prints what each view
produced, which the Python tests assert on. This is what checks that the page renders exactly
the `CONTRACTS.md` fields, that `partner` is first, and that invariant errors are prominent.
The board as rendered:

```
invariant errors (1)
  pods/research/res-001-working.md: no live tmux session res-001
board · 6 items
id            pod / role            state    outcome   after              subagents goal      session context  seams turn
partner       partner / partner     working  —         —                  0         09:15:02Z ● live  91,044   4     13:09:58Z
eng-000       engineers / engineer  complete done      —                  0         10:00:03Z ● live  132,880  2     11:58:12Z
eng-001       engineers / engineer  working  —         eng-000 ✓          1         12:00:03Z ● live  48,211   2     13:09:40Z
eng-002       engineers / engineer  queued   —         waits on eng-003   0         —         ● live  —        0     —
eng-003       engineers / engineer  complete decision  —                  0         10:30:04Z ● live  74,902   1     12:47:31Z
res-001       research / researcher working  —         —                  0         08:40:02Z ● dead  20,118   0     09:02:11Z
```

Orders renders the `after` graph (`eng-001 ── after ──▶ eng-000  met`,
`eng-002 ── waits on ──▶ eng-003  unmet`), every order and addendum verbatim in a `<pre>`, and
badges for the two cases the board cannot show (`file edited since dispatch`, `not dispatched`).
Archive renders benched bodies and archived dispatches per id, including the empty case. Agent
and Partner are navigable shells naming the endpoints their data already comes from, and the
Partner shell says full control stays `tmux attach -t partner` (spec 16.2).

## Live against Claude Code vs. against the fake

**Nothing in this goal ran against Claude Code, the fake `claude`, or tmux, and nothing in it
needs to.** The UI is a read-only HTTP server over JSON documents; it launches no agent, sends
no keys to a pane, and reads no transcript. Everything above was verified against:

- the hand-written fixtures in `tests/ui/fixtures/` (conforming to `CONTRACTS.md`),
- a real `http.server` over a real TCP socket on `127.0.0.1` (the tests are HTTP clients, not
  handler unit tests),
- a real filesystem for the mtime sweep (`InstanceSource.scan()` over a built `HARNESS_ROOT`
  tree in `tmp_path`, asserting each watched path moves its scope and each unwatched path
  moves nothing),
- the real `static/app.js` under node 26.5.0.

The pane capture that spec 16.2 puts in the Agent view is ui-2, and that is where `tmux
capture-pane` first appears in this lane.

## Open questions

Three of the four were raised and answered inside this goal. `handoff/orchestrator-to-ui.md`
(2026-09-20) accepted both ui handoffs as written and is marked `DONE 2026-09-20` in place;
`CONTRACTS.md` now carries `hx orders --json`, `hx archive --json` and the SSE scope section.

1. ~~`CONTRACTS.md` has no Orders or Archive shape.~~ **Resolved.** Adopted verbatim into
   `CONTRACTS.md`, `file_matches_record` included, and added to spec 08 and to build-2, so
   `InstanceSource` will have real commands to call in ui-2. No code or fixture change was
   needed: `tests/ui/fixtures/orders.json` and `archive.json` were written to these shapes and
   `tests/ui/test_fixtures_contract.py` already validates them against exactly these key sets.
2. ~~The SSE `changed` list carries one non-id scope.~~ **Resolved.** `tasks` is accepted and
   pinned in `CONTRACTS.md`; do not fan out. That is what the server already emits. `CONTRACTS.md`
   adds that the browser treats `tasks` as "re-fetch the board and the orders view" — `app.js`
   re-fetches whichever view is open on any change, which covers that and every id case; there
   is now a comment at that line saying so.
3. ~~`file_matches_record` is the ui lane's own idea.~~ **Resolved.** Keep the key and the badge.
4. **`InstanceSource` readers are still stubs** — the one item left open, and it is ui-2's by
   this goal's own scoping ("`FixtureSource` now, `InstanceSource` in ui-2"). `--root` currently
   serves 503 with a message naming ui-2. What ui-2 will bind to:
   - `board()` → `hx.board.collect(root)`, which exists today and already returns the
     `CONTRACTS.md` document (verified by reading `src/hx/board.py`).
   - `orders()`, `archive()` → `hx orders --json` / `hx archive --json`, landing in build-2.
   - `show(id)`, `wake_partner(text)` → `src/hx/show.py` and `src/hx/wake.py`, neither of which
     exists yet. `CONTRACTS.md` already fixes `hx.wake.wake_partner(root, text) -> bool`.

   `board()` could have been wired today, since its function exists; it was left alone because
   wiring one of five readers makes `--root` half-work in a way that is worse than a clear 503.

## Handoff entries written

- `handoff/to-orchestrator.md` — three entries, all this date: the Orders and Archive JSON
  shapes `CONTRACTS.md` lacked (items 1 and 3 above), the SSE `tasks` scope (item 2), and
  `tests/guard/test_user_home_untouched.py` failing on `~/.claude/file-history/` (above).
  All three are answered and applied; none is left open.
- `handoff/ui-to-build.md` — new file, two entries: `hx ui` should call
  `hx.ui.server.serve(root)` (signature given, so it is not guessed at build-10), and the table
  of functions `InstanceSource` intends to call in ui-2 (`hx.board.collect` confirmed present;
  `hx.show` and `hx.wake` not yet written), with the note that the ui lane is not blocked
  because it runs on fixtures.

`handoff/orchestrator-to-ui.md` was written by the orchestrator during this goal, answering
both entries above. It was read, applied (nothing needed changing; the reply records why per
item) and marked `DONE 2026-09-20` in place, per ORCHESTRATION.md. No other
`handoff/*-to-ui.md` exists.
