# ui-6: real streams, subagents, and seams from an M4 run

Sent after build-5 (M4) lands. Read `goals/build-5.done.md`, `handoff/build-to-ui.md` (the
record shapes the `log`, `subagent-*` and `stop` hooks write), spec 07.1, 07.4, 16.2.

## Build

1. Render the real raw-record shape in the stream tail (tool name, excerpt, ref, seq, ts;
   boundary records as markers; the `turn` marker from `run/<id>/turn` as "last turn" in the
   Agent header).
2. Subagent handles: from `run/<id>/subagents.json` and the `-open`/`-closed` streams, with each
   closed stream's digest.
3. The Board's `open_subagents`, `turn_ts`, `context_tokens` and `seams` columns from real data;
   assert against an instance produced by the build lane's M4 test fixtures (ask for a recorded
   scratch root in `handoff/ui-to-build.md` if none is committed).
4. Fixtures regenerated from real records, not hand-written, and the contract test updated.
5. Live pass in the in-app browser by you is not possible; make the DOM-harness tests cover the
   new sections and paste `/api/show/<id>` for an id with two closed subagent streams.

## Done when

- `tools/milestone-check.sh ui` passes.
- Committed path-scoped. `goals/ui-6.done.md` written, with handoffs.
