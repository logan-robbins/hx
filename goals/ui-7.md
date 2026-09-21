# ui-7: step state from the real Companion, `turn.background_tasks`, and one error-state fix

Sent after build-6 (M5) lands. Read `goals/build-6.done.md`, `handoff/build-to-ui.md` (the
step-state schema the Companion output is validated against), CONTRACTS.md (`turn`), spec 07.2,
16.2.

## Build

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
