# ui-1: Milestone M9 part 1 (spec 16): server, board, orders, archive views from fixtures

Read `ORCHESTRATION.md`, `CONTRACTS.md`, then spec sections 03, 06, 07, 08 (`hx board`,
`hx show`, `hx wake`), 16 (`spec/NN-*.md`). You own `src/hx/ui/**` and `tests/ui/**`. The
harness commands do not exist yet; build against fixtures that conform to `CONTRACTS.md` and
keep the data access behind one small interface so the build lane's real functions can be
plugged in later (`hx.ui.data.Source` with `board()`, `show(id)`, `orders()`, `archive()`,
`wake_partner(text)`; `FixtureSource` now, `InstanceSource` in ui-2).

The autodev UI at `/Users/loganrobbins/workspace/autodev/src/autodev/` (`service.py`,
`fleet.py`, `web/`) is the prior art; spec 16.4 says what to keep and what to drop. Never read
`/Users/loganrobbins/workspace/autodev/website/`. Take code from it only under your own paths.

## Build

1. `src/hx/ui/server.py`: stdlib `http.server` on `127.0.0.1:<port>` (default 8765; `port`
   from `config/ui.json` when an instance is given), bearer token read from `run/ui-token`
   (create with mode 0600 if missing), every request except the static index requires it.
   `python -m hx.ui --root <root>` and `python -m hx.ui --fixtures <dir>` both start it; the
   `hx ui` entry in `cli.py` is the build lane's and will call `hx.ui.server.serve(root)`.
2. Endpoints: `GET /` static index; `GET /static/*`; `GET /api/board`; `GET /api/show/<id>`;
   `GET /api/orders`; `GET /api/archive`; `POST /api/partner/wake` (body `{"text": …}`);
   `GET /api/events` server-sent events. Nothing else writes.
3. SSE: once a second stat the mtimes of `tasks.json`, `pods/`, `orders/`, `state/`, `logs/`,
   `run/*/turn`, `run/*/goal` (recursively where directories) and push `{"changed": [ids]}`;
   the browser re-fetches those views. Fixture mode watches the fixture directory.
4. Views in vanilla JS and CSS, no build step, no CDN, no framework: Board, Orders (with the
   `after` graph and which queued items wait on which ids), Archive, and the shell navigation
   for Agent and Partner (those two views are ui-2). Render exactly the fields in
   `CONTRACTS.md`; `partner` first on the board; invariant errors shown prominently.
5. `tests/ui/fixtures/`: at least `board.json`, `show-partner.json`, `show-eng-001.json`,
   `orders.json`, `archive.json`, hand-written to `CONTRACTS.md`, including a queued item with an
   unmet `after`, a `complete` item with outcome `decision`, and a board with one invariant error.
6. Tests: every endpoint with and without the token; SSE emits within 1 s of touching a fixture
   file; `POST /api/partner/wake` calls the source's `wake_partner` exactly once with the text
   and never writes anything else; no endpoint mutates the instance (assert the fixture tree's
   manifest is unchanged after the whole test run).

## Done when

- `tools/milestone-check.sh` passes: `tests/guard` and `tests/ui` (other lanes' tests may not
  exist yet; report, do not fix).
- `python -m hx.ui --fixtures tests/ui/fixtures` serves a board you have looked at in a browser
  or with `curl` (paste the curl output of `/api/board` in the done file).
- Everything committed with explicit paths; `goals/ui-1.done.md` written per ORCHESTRATION.md,
  including any `handoff/ui-to-*.md` entries (for example fields you needed that
  `CONTRACTS.md` lacks, in `handoff/to-orchestrator.md`).
