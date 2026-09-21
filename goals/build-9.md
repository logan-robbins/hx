# build-9: Milestone M7 (spec 13): `hx metrics` and the Companion replay eval

Read `goals/build-8.done.md`, `handoff/orchestrator-to-build.md`, spec 07.4, 13 M7,
`docs/companion-eval.md` (gtm), CONTRACTS.md (`hx metrics`).

## Build

1. `hx metrics <id> [--json]` exactly per CONTRACTS.md, from the seam records and the tool
   records that follow each seam in the main stream.
2. The replay eval: from logs recorded in build-8's live runs (or recorded now: two workers on
   the m8 repo with `--setting-sources user`), seam at five points per task; for each, compose
   the context file, launch a fresh worker against the real binary pointed at it, and record
   the first ten turns: Reads of the context file (must be 1), Reads of `working_set` files
   (waste), other tool calls. Report per seam and in total; the pass bar is the one
   `docs/companion-eval.md` states.
3. Feed the result to gtm: what the Companion prompt should keep or drop, as observed, in
   `handoff/build-to-gtm.md`.

## Done when

- `tools/milestone-check.sh build` passes; the eval's table is in the done file.
- Committed path-scoped. `goals/build-9.done.md` written, short.
