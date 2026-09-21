# ui-8: the autodev UI is the UI. Restore it and point it at hx.

Spec 16.4 said keep autodev's `web/style.css` and layout and restyle them to the board, agent,
and Partner navigation. ui-1 rebuilt instead (559 lines of CSS sharing 2 of autodev's 148
selectors). That was wrong and it is reversed here. Read spec 16 (cut version), CONTRACTS.md,
`/Users/loganrobbins/workspace/autodev/src/autodev/web/{index.html,app.js,style.css}` in full
(never `website/`), and `goals/ui-7.done.md`.

## The reference

`https://autodev-team.com/demo/index.html?demo=1#overview` is the look, screen for screen; open it
in the in-app browser and keep it open while you work. Observed there, and the hx mapping:

| Demo screen | hx |
|---|---|
| Sidebar: workspace, "All Pillars", the pillars with a live dot, agents listed under each | Sidebar: instance name, "All Pods", the pods (`backend`, `frontend`, `release`, …) with a live dot, the HarnessAgents under each |
| Overview: one card per pillar: description, counters, a current line, avatars | One card per pod: the pod's agents as avatars; the current line is each working agent's open step; **no "open tasks / in progress / blocked" counters**: show the agents' states (idle, working, complete with outcome) instead |
| Pillar page → Agent graph: manager at the root, agents below with role, current line, and status; GM chat, Task board, Agent list, Contract tabs | Pod page → fleet graph: the Partner at the root, this pod's agents below with role, current line (open step's next action), and state; each agent's Companion drawn beside it; tabs: Partner chat, Task board, Agent list. No Contract tab |
| Task board: columns Queued / Working / Validating / Blocked, cards with assignee avatar and age | Task board: columns are the work-item state and outcome (`idle`, `working`, `complete: done`, `complete: decision`, `complete: blocked`, `complete: exhausted`); one card per work item with its agent's avatar, the order's first line, and **that agent's own `## Tasks` checklist from its work item** rendered inside the card; age from `dispatched` |
| Harness Agents table: agent, status, current work, counts | Agent table: agent, pod, role, state and outcome, current work (open step's next action), session alive, seams; drawer opens the full Agent page (work item, step state, streams, pane) |
| GM chat | Partner chat: `POST /api/partner/wake`; replies from the Partner's pane capture; the note that full control is `tmux attach -t partner` |
| Demo banner, speed controls | none |

The graph page (`#pillar/frontend/graph` in the demo) is the primary view of a pod and the one the
spec author cares about most: the Partner node at the root, this pod's HarnessAgents below it as
cards (avatar, name, role, current line, state pill), each with its Companion drawn as a small
attached node, edges from the Partner to every agent it has dispatched (from `tasks.json`) and
from each agent to its Companion; zoom and fit controls as in the demo; live state via SSE. Keep
the demo's card style and spacing exactly.

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
   the pod card, the graph node, the board card, and the agent row all carry the open step's
   next action from the step state (or the first unchecked `## Tasks` line when there is no
   state yet). Task counters are gone; the agent's own `## Tasks` list is what is shown.
5. Tests: the DOM harness runs the restored `app.js`; the earlier assertions (fields rendered,
   partner first, live update, cookie) are re-pointed, not deleted; `tests/ui` green.

## The reference

`https://autodev-team.com/demo/index.html?demo=1#overview` is the look, screen for screen; open it
in the in-app browser and keep it open while you work. Observed there, and the hx mapping:

| Demo screen | hx |
|---|---|
| Sidebar: workspace, "All Pillars", the pillars with a live dot, agents listed under each | Sidebar: instance name, "All Pods", the pods (`backend`, `frontend`, `release`, …) with a live dot, the HarnessAgents under each |
| Overview: one card per pillar: description, counters, a current line, avatars | One card per pod: the pod's agents as avatars; the current line is each working agent's open step; **no "open tasks / in progress / blocked" counters**: show the agents' states (idle, working, complete with outcome) instead |
| Pillar page → Agent graph: manager at the root, agents below with role, current line, and status; GM chat, Task board, Agent list, Contract tabs | Pod page → fleet graph: the Partner at the root, this pod's agents below with role, current line (open step's next action), and state; each agent's Companion drawn beside it; tabs: Partner chat, Task board, Agent list. No Contract tab |
| Task board: columns Queued / Working / Validating / Blocked, cards with assignee avatar and age | Task board: columns are the work-item state and outcome (`idle`, `working`, `complete: done`, `complete: decision`, `complete: blocked`, `complete: exhausted`); one card per work item with its agent's avatar, the order's first line, and **that agent's own `## Tasks` checklist from its work item** rendered inside the card; age from `dispatched` |
| Harness Agents table: agent, status, current work, counts | Agent table: agent, pod, role, state and outcome, current work (open step's next action), session alive, seams; drawer opens the full Agent page (work item, step state, streams, pane) |
| GM chat | Partner chat: `POST /api/partner/wake`; replies from the Partner's pane capture; the note that full control is `tmux attach -t partner` |
| Demo banner, speed controls | none |

The graph page (`#pillar/frontend/graph` in the demo) is the primary view of a pod and the one the
spec author cares about most: the Partner node at the root, this pod's HarnessAgents below it as
cards (avatar, name, role, current line, state pill), each with its Companion drawn as a small
attached node, edges from the Partner to every agent it has dispatched (from `tasks.json`) and
from each agent to its Companion; zoom and fit controls as in the demo; live state via SSE. Keep
the demo's card style and spacing exactly.

## Done when

- `tools/milestone-check.sh ui` passes.
- `python -m hx.ui --fixtures tests/ui/fixtures` serves the autodev shell with hx data; the
  orchestrator walks every screen in the browser before this goal is accepted.
- Committed path-scoped. `goals/ui-8.done.md` written, short.
