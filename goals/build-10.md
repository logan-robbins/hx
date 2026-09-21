# build-10: Milestone M8 (spec 13): end to end, as the human does it

Read `goals/build-9.done.md`, spec 12, 13 M8, `tests/scenario/m8/README.md` (the gtm lane's
drive sequence and expected boards), `docs/getting-started.md`.

## Run

On a fresh scratch instance installed with the seed token, exactly the getting-started path: the
human types the goal into the Partner's pane (you paste it, as the human would); the Partner
creates and launches a backend engineer, writes the order, dispatches; the worker (with
subagents, in a copy of the m8 repo with its `.claude/` tripwire in place) finishes with
`HX-COMPLETE be-001 done`; the Partner is woken, reads, dispatches the second order; the second
worker ends `decision`; the human answers in the Partner's pane; the Partner writes the addendum
and resumes; both complete; the Partner reports in chat; the human ran no hx command. Seams
occur (lower `seam_min_context_tokens` in the scratch `harness.json` if needed to force at least
one per worker). Record `hx metrics` per seam, `hx board` at each step against the pack's
expected boards, and the real `~/.claude` manifest before and after (byte-identical).

## Done when

- The sequence above completed live, with the transcript excerpts and the boards in the done
  file; every discrepancy with the pack's `expected/` explained or fixed on your side.
- `tools/milestone-check.sh build` passes; `tests/live` includes this as a marked test.
- Kill everything you launched. Committed path-scoped. `goals/build-10.done.md` written.
