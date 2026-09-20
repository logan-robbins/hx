# ui-4: bind to the published harness functions; the `hx ui` entry; a real instance end to end

Read `goals/ui-3.done.md` (yours), `handoff/build-to-ui.md` (the published functions: module
path, signature, return, what each raises), `handoff/orchestrator-to-ui.md`, CONTRACTS.md
(`HX-WAKE`, `hx metrics`).

## Build

1. `InstanceSource` binds directly to the published Python functions for board, show, orders,
   archive, metrics and wake; the subprocess runner stays only as the fallback when an import
   fails, and a test proves the fallback path still works. Map "unknown id" to 404 and any other
   raise to 502 with the exception's message, per the published raise contract.
2. Confirm `hx ui` (the build lane's subcommand) starts your `serve(root)`; if it does not exist
   yet, keep `python -m hx.ui --root` as the documented entry and say so.
3. **A real instance end to end**, on a scratch root under your scratchpad: `hx install
   --skeleton-only`, then the M8 pack (`tests/scenario/m8/`) placed as the gtm README says
   (orders, config, work items, `tasks.json`), then `python -m hx.ui --root <root>`. Walk every
   view in a browser (the in-app Browser is fine) and fix what is wrong. Paste `/api/board`,
   `/api/show/eng-002`, `/api/orders` and the SSE frames from touching one work item in the done
   file. No agent is launched in this goal; pane capture shows "no live tmux session" and the
   pane-log fallback shows the file you place there.
4. Tests for 1–3; `tests/ui` leaves the scratch root's manifest unchanged except `run/ui-token`.

## Done when

- `tools/milestone-check.sh` passes for `tests/guard` and `tests/ui`.
- Committed path-scoped. `goals/ui-4.done.md` written, with handoffs.
