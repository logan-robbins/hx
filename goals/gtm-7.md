# gtm-7: docs and skills after M4, the doctor assertion, and the Companion prompt for `claude-cli`

Sent after build-5 (M4) lands. Read `goals/build-5.done.md`, `handoff/orchestrator-to-gtm.md`,
any other `handoff/*-to-gtm.md`, spec 05 (`provider: claude-cli`), 10.

## Build

1. `tests/packaging`: assert `docs/deploy.md`'s `hx doctor` block equals the real output on the
   deploy-proof instance (paths normalised), so the doc cannot go stale silently.
2. `docs/deploy.md`, `docs/two-worlds.md`, `README.md`: reflect M4 (streams, subagent streams,
   the pane log, `run/<id>/turn`) and the branch name `agent/<id>` everywhere; the Companion
   paragraph says it runs through `claude -p` with the same token and no tools.
3. `src/hx/skeleton/companion/BASE.md`: it will be delivered as a `--append-system-prompt-file`
   to `claude -p` with the state and records on stdin and the answer required as one JSON object
   matching spec 07.2; make the output contract explicit at the top (JSON only, no prose, the
   schema by field), and keep every rule from gtm-1/gtm-5. `roles/*.md` unchanged unless the
   build lane's `handoff/build-to-gtm.md` asks.
4. `hx-worker` and `hx-partner` skills: the M4 behaviours the agent sees (subagent streams,
   what `stop` does with `goal-pending`), checked against the real hooks in the tree.
5. Extend `tests/scenario/m8/README.md` with the exact sequence the build lane's M8 test will
   drive, now that dispatch, launch, worktrees, and the token exist; update `expected/` if the
   board text changed (the pack test tells you).

## Done when

- `tools/milestone-check.sh gtm` passes.
- Committed path-scoped. `goals/gtm-7.done.md` written, with handoffs.
