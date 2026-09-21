# ui-7: step state from the real Companion, `turn.background_tasks`, and one error-state fix

Sent now, in parallel with build-7 (the build lane is deleting the cut code). Build to
CONTRACTS.md as rewritten; if `goals/build-7.done.md` exists before you close, re-verify
against the real `hx board --json`, `hx show`, `hx orders` on a scratch instance; otherwise say
that verification is against the contract only. Read `goals/build-6.done.md`, `handoff/build-to-ui.md` (the
step-state schema the Companion output is validated against), CONTRACTS.md (`turn`), spec 07.2,
16.2.

## Build

0. **The v1 cut first** (ORCHESTRATION.md constraints; CONTRACTS.md rewritten): the Board has no
   `after`/`ready`/`goal_pending` columns and no invariant-errors box; `partner` is not a board
   row (the Partner view stays); work-item files stay `pods/<pod>/<id>-<state>.md`; the Orders view lists `tasks.json` orders and addenda per id with no graph and no
   file-match badge; `hx show partner` has the reduced shape. Regenerate fixtures from the
   build lane's cut commands; delete the tests of removed things.

1. Step state rendered from the real schema: open steps with intent and next action, closed
   steps with outcome, commit, evidence seqs, `verified`; working set with per-file notes;
   decisions; blockers; dead ends; the budget used versus `state_budget_tokens` as a bar. Fixture
   regenerated from a real Companion write (ask the build lane for one in
   `handoff/ui-to-build.md` if none is committed).
2. Closed-stream digests from `state/<id>/<stream>.digest.md` replace "pending companion".
3. `turn.background_tasks` (CONTRACTS.md) in the Agent header: "stopped with work still running"
   with the task ids when non-empty.
4. Orchestrator's browser finding: the Agent view's error state (unknown id, or a fixture/instance
   read failure) must keep the id switcher header so the human can recover without the Board.
5. Tests for 1–4; fixtures conform to the published schema.

## Done when

- `tools/milestone-check.sh ui` passes.
- Committed path-scoped. `goals/ui-7.done.md` written, with handoffs.
