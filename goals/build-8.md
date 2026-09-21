# build-8: Milestone M6 (spec 13): seams for real, `hx seam`, the live suite

Read `goals/build-7.done.md` (yours), `handoff/orchestrator-to-build.md`, any other
`handoff/*-to-build.md`, spec 02 (Seams, Compaction), 06 (pointer), 07.4, 09.2–9.3 (the
handshake), 10 (seam policy), 11 (Compaction rows), 13 M6, 17.6.

## Blocking, before items 10 and 11

0. **A repo's own `.claude/settings.json` applies to agents.** Live rehearsal 2026-09-20 21:20: the
   worker `be-001`, working in a copy of `tests/scenario/m8/repo`, loaded that repo's
   `.claude/settings.json` tripwire (deny-all `PreToolUse`, `defaultMode: plan`), had every tool
   denied, could never run `hx complete`, and its goal evaluator gave up. Fix: `start.sh` passes
   `--setting-sources user` on every launch (agent and Companion) so only the home's settings
   load. Verify the flag's exact name and semantics against `code.claude.com/docs/en/cli-reference`
   and the pinned binary (`claude --help`), record the URL, then prove it live: launch a worker in
   a copy of the m8 repo and show a Bash tool call succeed and the tripwire line never appear.
   Never edit the checkout's `.claude/`. Spec 11 and 17.4, CONTRACTS.md updated.
12. `hx heartbeat`: a `working` item whose session is alive, whose pane is idle, and whose stream
    has no `HX-COMPLETE` gets its goal pointer pasted again (`hx goal <id>`); this is what
    recovers a worker whose `/goal` evaluator cleared itself (seen live).

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
3. Compaction never happens on the main thread by construction: the seam threshold sits far
   below the model's window, so the `log` hook marks a seam first. `precompact`/`postcompact`
   are log-only (spec 02, 09.1; `precompact` runs `hx flush` first). Verify no `compact_boundary`
   appears in the main transcript across a 10-seam live run.
4. `hx restart <id>` and `hx launch <id>` of a `working` item deliver the goal after the idle
   prompt appears (readiness detector from build-3); a `goal-pending` left by a mid-turn
   `hx dispatch partner` is pasted by `stop` and runs as the next input (live, on the Partner).
5. **The live suite** (`tests/live/`, marked and skipped unless `HX_LIVE=1` and `seed/token`
   exist): the M6 criteria above against the real pinned binary, reaping everything it launches.
6. Tests offline for the policy and the handshake with the fake; live for the transcript order,
   the 10-seam run, restart and launch delivery, and `goal-pending` on the Partner. Paste the
   transcript excerpts. Kill everything you launched.

7. `hx show --json` gains `turn: {ts, background_tasks}` from `run/<id>/turn` (CONTRACTS.md);
   publish it to the ui lane.

Keep the done file short (ORCHESTRATION.md step 4, revised): the tests and transcript excerpts
are the evidence; do not narrate.

8. `hx install`'s last step and `hx up` start `hx ui` in tmux session `ui` (idempotent) and print
   the URL (spec 16.1, 17.2). `hx heartbeat` restarts it when dead.

9. From build-7's findings: `hx heartbeat` launches the Partner when its session is dead
   (spec 08); `hx doctor` checks each home's pre-seeded `.claude.json` as well as its
   `settings.json`.

10. **First, before anything else in this goal**: the paste transport bug from build-7's handoff.
    The first paste into a brand-new Claude Code pane loses its Enter (the TUI is still
    ingesting the paste when the Enter arrives). Every paste in hx goes through one function:
    after `paste-buffer`, poll `capture-pane` until the pasted text is visible in the input box,
    then send Enter, then poll until the input box is empty (submitted); if still unsubmitted
    after the second poll, send Enter once more. No fixed sleeps, no timeouts. Prove it live on
    a fresh pane (first paste) and a warm one. The orchestrator hit the same bug in the live
    rehearsal on 2026-09-20 21:17.

11. **Also first**: `hx` is not on the agents' `PATH`. In the live rehearsal (2026-09-20 21:20)
    the Partner had to discover the binary through `config/hx.json`. `hx install` writes
    `$HARNESS_ROOT/bin/hx` and `bin/hx-hook` as symlinks to the recorded entry points (spec 03),
    and `start.sh` prepends `$HARNESS_ROOT/bin` to `PATH` in the agent's and the Companion's
    environment. `hx doctor` fails when `bin/hx` is missing or does not resolve. Test with the
    fake and prove live.

## Done when

- `tools/milestone-check.sh build` passes; `HX_LIVE=1 .venv/bin/python -m pytest tests/live`
  passes and its last lines are in the done file.
- Committed path-scoped. `goals/build-8.done.md` written, with handoffs.
