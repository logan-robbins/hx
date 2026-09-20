# ui-2: Milestone M9 part 2: Agent and Partner views, real instance source

Read `goals/ui-1.done.md` (yours), `goals/build-1.done.md` if it exists yet (the build lane is
still on M0; its code is in the shared tree and `hx.board.collect(root)` already returns the
CONTRACTS.md board), `handoff/orchestrator-to-ui.md`, any other `handoff/*-to-ui.md`, spec 16
again, spec 07 (step-state fields you render), 10 (Digest), 12 (what the human does in chat).

## Build

1. Agent view per spec 16.2: work item rendered (frontmatter, `## Order` with addenda,
   definition of done with its checks, `## Tasks` live, deliverables, open decision, digest);
   step state rendered (open steps with next action, closed steps with commits, working set,
   blockers, dead ends) from the `step_state` object; the last composed context file with its
   seam timestamp; main and subagent stream tails; metrics; pane capture (last 120 lines, ANSI
   stripped, refreshed on the SSE tick); subagent handles with digests. Every field comes from
   `hx show --json` (CONTRACTS.md); render `null` as "not yet".
2. Partner view: `partner_md` rendered; the board; chat box that POSTs to `/api/partner/wake`
   and shows the Partner's pane capture as the reply stream; a visible line saying full control
   is `tmux attach -t partner`.
3. `hx.ui.data.InstanceSource(root)`: board and show through the build lane's Python functions
   (their names are in `handoff/build-to-ui.md` when build-2 lands; until then call
   `.venv/bin/hx board --json` and `hx show <id> --json` via subprocess behind the same
   interface, and switch when the handoff arrives). Pane capture via `tmux capture-pane -p -e`
   with ANSI stripping, adapted from autodev's `fleet.py` into `src/hx/ui/pane.py`; log-file
   fallback when the session is dead. `wake_partner` calls `hx.wake.wake_partner` when it
   exists, else `.venv/bin/hx wake partner <text>` via subprocess with the text as one argv
   element (this is the one place text crosses a process boundary; it is hx's own fixed-form
   message, not an order).
4. `hx.ui.server.serve(root, port=None)` as the entry the build lane's `hx ui` subcommand calls;
   write the request for that subcommand in `handoff/ui-to-build.md`.
5. Tests: Agent and Partner views render every fixture field (assert on the served HTML/JSON,
   not on pixels); `InstanceSource` against a scratch root created with
   `.venv/bin/hx install --root <tmp> --skeleton-only` plus hand-made work items and
   `tasks.json`; pane capture against a real tmux session with a private socket; the whole
   suite leaves the scratch root's manifest unchanged except for `run/ui-token`.

6. Token transport: replace `?token=` in URLs with a cookie set by `GET /` (`HttpOnly`,
   `SameSite=Strict`, path `/`); `Authorization: Bearer` stays for API clients. A token in a
   query string ends up in browser history and server logs. `EventSource` and `<link>` send
   cookies on their own. Update the tests that asserted the query form.

## Done when

- `tools/milestone-check.sh` passes for `tests/guard` and `tests/ui`.
- `.venv/bin/python -m hx.ui --root <scratch>` serves board, agent, partner, orders, archive
  against a scratch instance; paste the `curl` output of `/api/show/partner` in the done file.
- Committed. `goals/ui-2.done.md` written, with handoffs.
