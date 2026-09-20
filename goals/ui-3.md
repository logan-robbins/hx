# ui-3: metrics and seams rendered, then the switch to the real harness

Read `goals/ui-2.done.md` (yours), `CONTRACTS.md` (new section `hx metrics`), spec 07.4, 16.2,
`handoff/*-to-ui.md` (especially `handoff/build-to-ui.md` if it exists: build-2 names the
Python functions).

## Build

1. **Metrics as a table**, from the `hx metrics --json` shape in `CONTRACTS.md`: one row per
   seam (source, context tokens before, context file size, tool calls in the next 10 turns split
   into context-file Reads, working-set Reads, other), totals row, and a red mark on any seam
   whose `reads_of_context_file` is not 1 or whose `reads_of_working_set` is above 0. Fixture
   `tests/ui/fixtures/metrics-eng-001.json`.
2. **Seams in the stream tail** (spec 16.2): a `seam` record renders as a marker with the
   context file size and, from the metrics document, the tool calls of the ten turns that
   followed. Records of other types keep their current rendering.
3. **Settle the static file names** and write them to `handoff/ui-to-gtm.md` so the gtm lane can
   pin them in the wheel check.
4. **Switch to the real harness**, in two steps, each behind the same `Source` interface:
   (a) if `goals/build-2.done.md` exists, `InstanceSource` calls the real `hx show`, `hx orders`,
   `hx archive`, `hx wake partner` via the existing subprocess runner and the 503s disappear;
   run the M8-shaped scratch instance the gtm lane is building under `tests/scenario/m8/` (if it
   exists) through `hx install --skeleton-only` plus hand-placed work items and `tasks.json`, and
   paste the real `/api/show/partner` and `/api/orders` in the done file; (b) if
   `handoff/build-to-ui.md` names the Python functions, bind them directly and keep the
   subprocess runner only as the fallback when import fails. If build-2 has not landed when you
   reach this step, do 1–3, record it, and stop: the swap is ui-4.
5. Tests for 1, 2, and whichever of 4 applied; `tests/ui` leaves the scratch root's manifest
   unchanged except `run/ui-token`.

## Done when

- `tools/milestone-check.sh` passes for `tests/guard` and `tests/ui`.
- Committed path-scoped. `goals/ui-3.done.md` written, with handoffs.
