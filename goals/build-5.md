# build-5: Milestone M4 (spec 13): `log`, `subagent-start`, `subagent-stop`, `subagent-result`, `stop`

Read `goals/build-4.done.md` (yours), `handoff/orchestrator-to-build.md`, any other
`handoff/*-to-build.md`, spec 07.1 (raw record shape, 4 KB line cap, excerpt + ref), 07.3
(subagent context file), 09.1 (the five hooks, payload sources), 09.3 (handshake), 09.4, 13 M4,
`hx.streams.append_record` (yours, from build-3).

## Build

1. `hx-hook --id <id> log` (PostToolUse `*`): one raw record per tool call appended to the
   stream of the calling thread (main, or `sNNN` looked up from `run/<id>/subagents.json` by the
   payload's agent id), with excerpt and ref per 07.1; write `run/<id>/seam` when
   `context_tokens ≥` the model's threshold from `config/models.json` on the main stream only
   (spec 06/07; M6 consumes it). Never raise; a malformed payload goes to `hook-errors.log`.
2. `subagent-start` (SubagentStart, non-Partner): assign the next `sNNN`, record it in
   `run/<id>/subagents.json`, create `logs/<id>/<id>-sNNN-open.jsonl`, compose the subagent's
   context file (`SUBAGENTS.md` as memory section, the subagent prompt as task), and return JSON
   `additionalContext` pointing at it with the same Read-tool line as 09.1 (JSON on stdout is
   the only accepted form for this hook; verify against `docs/en/hooks#subagentstart`).
3. `subagent-stop`: rename `-open` → `-closed`, write a boundary record, and leave the digest to
   the Companion (M5); until then write `state/<id>/<id>-sNNN.digest.md` with a placeholder line.
4. `subagent-result` (PostToolUse `Agent`): map `tool_response.agentId` to `sNNN`, append the
   closed-stream digest path to the parent's main stream record so the parent sees where it is.
5. `stop` (Stop): write `run/<id>/turn` with `ts` and the payload's background task list; wake
   the Companion (no-op until M5); if `run/<id>/goal-pending` exists, run `hx goal <id> --now`
   and remove it; else if `run/<id>/seam` exists, run `hx seam <id>` (not implemented until
   build-7: leave the marker and log). This is what delivers the Partner's self-dispatch
   pointer.
6. Tests (fake `claude` emitting scripted hook payloads, real tmux): three parallel subagents →
   three isolated streams with correct handles and each gets its own context file path; the
   main stream records every spawn and close; the closed-stream digest path reaches the parent
   via `PostToolUse(Agent)`; `hx complete` refuses while any stream is `-open`; a `goal-pending`
   left by `hx dispatch partner` is pasted by `stop` and appears in the pane input log as the
   next line; the 4 KB cap holds on a huge tool result.
7. Live check (real binary, scratch root, seed token as in build-3): a Partner turn that spawns
   two subagents; confirm the three stream files, the `SubagentStart` context line in a
   subagent transcript, and that `stop` fires after the turn. Kill everything you launched.

## Done when

- `tools/milestone-check.sh` passes for `tests/guard` and `tests/core`.
- Committed path-scoped. `goals/build-5.done.md` written, with handoffs (the ui lane renders
  stream tails and subagent handles: tell it in `handoff/build-to-ui.md` what the records look
  like now).
