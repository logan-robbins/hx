# build-6: Milestone M5 (spec 13): the Companion loop, `hx companion`, `hx flush`, digests

Read `goals/build-5.done.md` (yours), `handoff/orchestrator-to-build.md`, any other
`handoff/*-to-build.md`, spec 10 (whole), 05 (`companion.*`, the new `provider: claude-cli`
paragraph), 07.2 (step-state schema), 07.4, 08 (`hx companion`, `hx flush`, `hx complete`'s
final pass), 13 M5, `src/hx/skeleton/companion/BASE.md` and `roles/*.md` (gtm's prompts).

## Decision already made: model access

There is no Anthropic API key in a subscription-only deployment. The Companion calls the pinned
`claude -p` with the same `seed/token` (`CLAUDE_CODE_OAUTH_TOKEN`), `--model companion.model`
(full id), a Companion-only `CLAUDE_CONFIG_DIR=run/<id>/companion-home` written by `install.sh`
(no hooks, no skills, no CLAUDE.md, onboarding and trust pre-seeded, bypass acceptance),
`--append-system-prompt-file run/<id>/companion-system.md` (BASE.md + role + harness facts,
composed once at start), `--output-format json`, and the current state plus new records on
**stdin**. Verify against `code.claude.com/docs/en/cli-reference` and `docs/en/headless` (or the
current name of that page) which flags exist for: JSON output, a JSON schema for the output
(`--json-schema` or equivalent), disabling session persistence, and no tools (the Companion
must not be able to call tools; if a flag exists to disallow all tools, use it, else
`--disallowedTools` with every tool name). Record URLs and the exact argv in the done file.
`prompt_version` = shas of BASE.md and the role file. Cache: the identical prefix is cached by
the binary; report `cache_read_input_tokens` from the result's `usage`.

## Build

1. `hx companion <id>`: the loop of spec 10 in window `<id>:companion` (`hx launch` starts it):
   wake on `batch_records` new records in any stream, `run/<id>/turn` touched, subagent stop, or
   `hx flush`; one call per stream with new records; validate the returned step state against
   the 07.2 schema (invalid output keeps the prior state and logs to `hook-errors.log`); write
   `state/<id>/<stream>.json` with `seq`, `prompt_version`, `ts`; seam policy on the main
   stream → `run/<id>/seam`; closed-stream digest → `state/<id>/<stream>.digest.md`.
2. `hx flush <id>`: signal the Companion and block (no timeout) until `state.seq` equals the
   log head for every stream.
3. `hx complete`'s final pass: the Digest from the main state and every closed-stream digest,
   blocker or question first for `blocked`/`decision`, written once into `## Digest`.
4. FIFO retention and truncation: stream files never drop records at or ahead of `state.seq`;
   the budget (`state_budget_tokens`, estimated at 4 chars per token) is enforced by the prompt
   first and by a deterministic eviction (spec 10 order) if the model overshoots.
5. Tests, offline: a fake Companion model (a script standing in for `claude -p` that returns
   scripted step states) for the loop, validator, budget, FIFO, flush, and a 500-record replay
   from a recorded log (record one from a build-5 live run if you have it; otherwise synthesise
   and say so); `prompt_version` stamped on every write; after a replayed `hx resume` the state
   keeps closed steps and absorbs the addendum.
6. Live: one real Companion call against the real binary with the seed token on a small recorded
   stream; paste the returned state and the `usage` block (cache reads non-zero on the second
   call). Kill everything you launched.

## Done when

- `tools/milestone-check.sh build` passes; every M5 criterion in spec 13 has a test.
- Committed path-scoped. `goals/build-6.done.md` written, with handoffs (ui renders step state:
  publish the exact schema you validate against in `handoff/build-to-ui.md`).
