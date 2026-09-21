# ui-8: the autodev UI is the UI. Restore it and point it at hx.

Spec 16.4 said keep autodev's `web/style.css` and layout and restyle them to the board, agent,
and Partner navigation. ui-1 rebuilt instead (559 lines of CSS sharing 2 of autodev's 148
selectors). That was wrong and it is reversed here. Read spec 16 (cut version), CONTRACTS.md,
`/Users/loganrobbins/workspace/autodev/src/autodev/web/{index.html,app.js,style.css}` in full
(never `website/`), and `goals/ui-7.done.md`.

## Do

1. Copy autodev's `index.html`, `app.js`, `style.css` into `src/hx/ui/static/`, replacing the
   rebuilt files. Keep the shell exactly: sidebar navigation, topbar, breadcrumb, details
   drawer, toasts, avatars and initials, badges, the board's cards, the agent table, the graph
   page, the chat page, the activity page, the typography and colours. Delete only: `demo.js`
   and every demo control, the settings/config editing page and `loadConfig`/`saveConfig`, pillar
   editing, the mailbox chat mechanics (`refreshChat` against a mailbox), and the 2 s poll.
2. Re-point the data layer at hx: `/api/board` (agents, from `hx board --json`),
   `/api/show/<id>` (work item, step state, streams, pane, digest), `/api/orders`,
   `/api/archive`, `/api/events` (SSE replaces the poll; `route()` re-renders on the ids in
   `changed`), `POST /api/partner/wake` for the chat page (send), Partner pane capture for the
   replies. Map autodev's model onto hx's: pillars → pods; agents → HarnessAgents with their
   Companion; ledger tasks → the work item's `## Tasks` and the step state's open and closed
   steps; `currentTask`/`currentText` → the open step's next action; `phase` → state and
   outcome; the graph page → the fleet: Partner at the root, pods, workers with state colours,
   each worker's Companion beside it; activity → stream tails with seam markers.
3. Keep hx's `server.py` (stdlib, cookie token, SSE) and `data.py`; delete hx-only views that
   have no autodev counterpart only if nothing in the spec needs them (the Orders and Archive
   lists stay, as drawer contents or a page in the autodev shell, your call, written down).
4. Every screen must explain what each HarnessAgent is doing in one line without opening it:
   the board card shows the open step's next action; the agent row shows it too.
5. Tests: the DOM harness runs the restored `app.js`; the earlier assertions (fields rendered,
   partner first, live update, cookie) are re-pointed, not deleted; `tests/ui` green.

## Done when

- `tools/milestone-check.sh ui` passes.
- `python -m hx.ui --fixtures tests/ui/fixtures` serves the autodev shell with hx data; the
  orchestrator walks every screen in the browser before this goal is accepted.
- Committed path-scoped. `goals/ui-8.done.md` written, short.
