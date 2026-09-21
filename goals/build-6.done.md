# build-6 done — M5, the Companion

The Companion is a Claude Code session in `<id>:companion`, not a headless call. The
`claude -p` runner is deleted; `tests/guard/test_no_headless.py` keeps it deleted.

## What works

- `hx companion <id>` composes `companion-system.md` (BASE + role + harness facts) and
  launches `start.sh <id> --companion` into its own home: guard hook, `companion-stop`
  hook, no product skills, `IS_SANDBOX=1`, bypass, `--model companion.model`. Idempotent.
- `hx wake companion <id> <stream>` writes `run/<id>/companion/<stream>.pass.md`, pastes
  `/clear`, then the fixed pointer. The Companion Reads the pass, state and log and Writes
  `out.json`.
- The `companion-stop` hook validates against 07.2, stamps `seq`/`prompt_version`/`ts`,
  moves it to `state/<id>/<stream>.json`, writes the closed-stream digest, and evaluates
  the seam policy on the main stream.
- `hx flush <id>` signals and blocks until every stream's `seq` equals its log head.
- `hx complete`'s final pass writes `## Digest` once, blocker or question first.
- FIFO truncation never drops at or ahead of `state.seq`; `stepstate.evict` is spec 10's
  order, a backstop to the prompt.

## One delivery mechanism

A wake whose pane is mid-turn leaves the pass file on disk and returns `pending`. The next
wake — a `log`/`stop`/`subagent-stop` hook, or `hx flush`, which re-signals each poll while
it blocks — delivers whatever pass file is there. A failed pass is the same case: the stop
hook rewrites the pass with `retry_reason` and returns. Second failure keeps the prior
state. No queue, no second path.

## Tests

`tools/milestone-check.sh build`: 5 guard, 474 core/fakeclaude. The fake `claude` plays
the Companion — reads the pass it is pointed at, writes a scripted `out.json`, fires the
stop hook. Covers the loop, the validator, the budget, FIFO, flush, the seam's four
conditions, a 500-record replay, a replayed `hx resume`, and both digests.

## Open

- The two-pass Companion live check against the real binary did not run. It needs a seed
  token and a killed-after run — do it in build-7.
- Step-state schema published for ui in `handoff/build-to-ui.md`.
