# build-6: Milestone M5 (spec 13): the Companion loop, `hx companion`, `hx flush`, digests

Read `goals/build-5.done.md` (yours), `handoff/orchestrator-to-build.md`, any other
`handoff/*-to-build.md`, spec 10 (whole), 05 (`companion.*`, the new `provider: claude-cli`
paragraph), 07.2 (step-state schema), 07.4, 08 (`hx companion`, `hx flush`, `hx complete`'s
final pass), 13 M5, `src/hx/skeleton/companion/BASE.md` and `roles/*.md` (gtm's prompts).

## Decision (spec author, supersedes the earlier `claude -p` note): the Companion is a tmux session

No `claude -p`, no headless call, no API client anywhere in hx (spec 02 "Model calls";
`tests/guard/test_no_headless.py` enforces it). The Companion is a Claude Code session in window
`<id>:companion`, launched by `start.sh <id> --companion` with its own home
`run/<id>/companion-home` (guard hook plus a Companion `stop` hook; the `hx-companion` skill the
gtm lane is writing now; no product skills), `--dangerously-skip-permissions`, `IS_SANDBOX=1`,
`--model companion.model`, `--append-system-prompt-file run/<id>/companion-system.md`. hx
drives the loop by `hx wake companion <id> <stream>`: write the pass file, paste `/clear`, paste
the fixed pointer (CONTRACTS.md "The Companion is a tmux session"). The Companion reads the
pass, state and log with its Read tool and writes `out.json` with Write; its `stop` hook
validates, stamps, and moves it to `state/`. Retry once with `retry_reason`, then keep prior
state. Whatever `claude -p` code exists in your tree is removed, not kept as a fallback.

## Build

1. `hx companion <id>` launches the Companion session (idempotent) after composing
   `run/<id>/companion-system.md`; `hx wake companion <id> <stream>` per CONTRACTS.md; the wake
   triggers of spec 10 fire from the agent's `stop`, `subagent-stop` and `log` hooks and from
   `hx flush`; the Companion's own `stop` hook validates `out.json` against the 07.2 schema,
   stamps `seq`, `prompt_version`, `ts`, moves it to `state/<id>/<stream>.json`, evaluates the
   seam policy on the main stream → `run/<id>/seam`, and writes the closed-stream digest →
   `state/<id>/<stream>.digest.md` when the pass was for a closed stream.
2. `hx flush <id>`: signal the Companion and block (no timeout) until `state.seq` equals the
   log head for every stream.
3. `hx complete`'s final pass: the Digest from the main state and every closed-stream digest,
   blocker or question first for `blocked`/`decision`, written once into `## Digest`.
4. FIFO retention and truncation: stream files never drop records at or ahead of `state.seq`;
   the budget (`state_budget_tokens`, estimated at 4 chars per token) is enforced by the prompt
   first and by a deterministic eviction (spec 10 order) if the model overshoots.
5. Tests, offline: the fake `claude` playing the Companion (it reads the pass file it is pointed
   at and writes a scripted `out.json`, then fires the Companion `stop` hook) for the loop, validator, budget, FIFO, flush, and a 500-record replay
   from a recorded log (record one from a build-5 live run if you have it; otherwise synthesise
   and say so); `prompt_version` stamped on every write; after a replayed `hx resume` the state
   keeps closed steps and absorbs the addendum.
6. Live: a real Companion session against the real binary with the seed token; two passes on a
   small recorded stream; paste the written state and the pane's two turns. Kill everything you launched.

7. From build-5's open questions: prove live that the parent receives `subagent-result`'s
   `additionalContext` (a real digest); `hx bench` prints that its patch preserves content, not
   staging.

## Done when

- `tools/milestone-check.sh build` passes; every M5 criterion in spec 13 has a test.
- Committed path-scoped. `goals/build-6.done.md` written, with handoffs (ui renders step state:
  publish the exact schema you validate against in `handoff/build-to-ui.md`).
