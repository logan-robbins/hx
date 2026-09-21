# build-8: Milestone M6 (spec 13): seams for real, `hx seam`, the live suite, `hx upgrade` complete

Read `goals/build-7.done.md` (yours), `handoff/orchestrator-to-build.md`, any other
`handoff/*-to-build.md`, spec 02 (Seams, Compaction), 06 (pointer), 07.4, 09.2–9.3 (the
handshake), 10 (seam policy), 11 (Compaction rows), 13 M6, 17.6.

## Build

1. `hx seam <id>`: refuse while `run/<id>/turn` shows non-empty `background_tasks` (leave the
   marker; the next `stop` retries); otherwise `hx flush`, remove `run/<id>/seam`, and paste
   `/clear` into the pane. The `context` hook on `source=clear` composes and, for a `working`
   item, sends the goal with `--now` (exists since build-3). Order in the transcript must be
   `Stop` → `SessionStart(clear)` → `/goal` → one Read of the context file.
2. Seam triggers: the Companion marker (spec 10 policy: step closed on main, above
   `seam_min_context_tokens`, after `seam_min_interval_s`, no open subagents) and the `log`
   hook's threshold marker (build-5) both land in `run/<id>/seam`; `stop` consumes it via `hx
   seam`. Never for subagent streams. Write a `seam` record (07.4) with `prompt_version`,
   `context_tokens` before, and the context file size.
3. Compaction is disabled by construction: `PreCompact` hook blocks auto-compaction on the main
   thread when a seam is pending (spec 02 Compaction, 01.1 E4) and logs otherwise; verify that
   no `compact_boundary` appears in the main transcript across a 10-seam live run.
4. `hx restart <id>` and `hx launch <id>` of a `working` item deliver the goal after the idle
   prompt appears (readiness detector from build-3); a `goal-pending` left by a mid-turn
   `hx dispatch partner` is pasted by `stop` and runs as the next input (live, on the Partner).
5. **The live suite** (`tests/live/`, marked and skipped unless `HX_LIVE=1` and `seed/token`
   exist): the M6 criteria above against the real pinned binary, reaping everything it launches.
   `hx upgrade` completes spec 17.6: re-render every home's settings and skills from the package,
   run the live suite against the candidate binary, and pin only on green; record the result in
   `config/claude.json` (`tested_at`, `suite_sha`).
6. Tests offline for the policy and the handshake with the fake; live for the transcript order,
   the 10-seam run, restart and launch delivery, and `goal-pending` on the Partner. Paste the
   transcript excerpts. Kill everything you launched.

7. `hx show --json` gains `turn: {ts, background_tasks}` from `run/<id>/turn` (CONTRACTS.md);
   publish it to the ui lane.

Keep the done file short (ORCHESTRATION.md step 4, revised): the tests and transcript excerpts
are the evidence; do not narrate.

8. `hx install`'s last step and `hx up` start `hx ui` in tmux session `ui` (idempotent) and print
   the URL (spec 16.1, 17.2). `hx heartbeat` restarts it when dead.

## Done when

- `tools/milestone-check.sh build` passes; `HX_LIVE=1 .venv/bin/python -m pytest tests/live`
  passes and its last lines are in the done file.
- Committed path-scoped. `goals/build-8.done.md` written, with handoffs.
