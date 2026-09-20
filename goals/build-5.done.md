# build-5 done: Milestone M4 — `log`, the three subagent hooks, and `stop`

## What was built

### 1. `log` (PostToolUse, every tool, every agent)

One raw record per tool call, on the stream of the thread that made it: the main stream, or the
subagent's `sNNN` looked up from `run/<id>/subagents.json` by the payload's `agent_id`. The
record is spec 07.1's shape — `seq`, `ts`, `stream`, `event`, `tool`, head excerpts of `input`
and `output`, `exit`, `context_tokens`, and `ref` pointing at the full payload in Claude Code's
own transcript, so nothing is lost.

`context_tokens` comes from the `usage` block of the latest assistant record in that transcript
(`transcripts.py`): **input plus cache reads, not output**, because what matters is the size of
the context the next turn has to fit. At or above the model's `threshold` from
`config/models.json` it touches `run/<id>/seam` — the hard trigger that does not wait for a
step to close (spec 02 Seams) — and **only on the main stream**, because a subagent's window is
its own.

### 2–4. `subagent-start`, `subagent-stop`, `subagent-result`

`subagents.py` owns the `agent_id → sNNN` map, assigned **under a lock**: several subagents can
start at once, and two of them taking the same handle would merge two agents into one stream.

`SubagentStart` opens `logs/<id>/<id>-sNNN-open.jsonl` with an `open` record, appends `spawned`
to the main stream, composes the subagent's own context file, and returns the path as JSON
`hookSpecificOutput.additionalContext` — **JSON is the only accepted form for that event**;
plain stdout is not injected (verified against the docs and live).

`SubagentStop` writes a `close` record, renames the stream to `-closed`, leaves a digest file,
and appends `closed` to the main stream. `PostToolUse(Agent)` maps `tool_response.agentId` back
to the handle and hands the digest path to the parent as `additionalContext` — the only path by
which anything reaches the parent (spec 09.1).

### 5. `stop` (Stop, main thread)

Writes `run/<id>/turn` with `ts`, `session_id` and `background_tasks`; wakes the Companion (a
no-op until build-6, but the call site is in place); then, **if a goal is owed, delivers it** —
this is what makes the Partner's self-dispatch work, since `hx dispatch partner` runs from
inside the Partner's own Bash tool and leaves `run/partner/goal-pending`. Otherwise, if a seam
marker is pending, it logs that `hx seam` is build-7 and **leaves the marker**, which is what
spec 09.3 step 2 prescribes when a seam cannot be taken yet. No decision output: `/goal` owns
continuation.

### 8. The three answers from build-4's open questions

- `hx dispatch` **refuses a dirty worktree**, exit 1, listing the files and naming `hx bench`
  as the way out. A dispatch resets the worktree, so this is the other end of the rule
  `hx complete done` already enforces.
- `hx bench` **saves the diff as a patch** at `pods/<pod>/archive/<id>-<ts>.patch` before the
  reset — tracked changes and untracked files both, via `add -AN` then `diff HEAD --binary`,
  with the index restored afterwards.
- `hx doctor` **fails when `base_branch` is not a ref in the mirror**, because that is what
  worktrees are cut from.

## How it was verified

```
$ ./tools/milestone-check.sh build
== required: tests/guard
== required:  tests/core tests/fakeclaude
MILESTONE-CHECK PASSED for build (own paths; add --all for the advisory run)

$ .venv/bin/python -m pytest tests/core
452 passed
```

Every M4 criterion in spec 13 has a test in `tests/core/test_streams.py`: three parallel
subagents producing three isolated streams with the right handles and each its own context
file; the main stream recording every spawn and close; the closed-stream digest reaching the
parent through `PostToolUse(Agent)`; `hx complete` refusing while a stream is `-open`; a
`goal-pending` left by `hx dispatch partner` pasted by `stop` and arriving in the pane; and the
4 KB line cap holding on a 500,000-character tool result.

Payload shapes verified at **https://code.claude.com/docs/en/hooks** on 2026-09-20:
`SubagentStart` (`agent_id`, `agent_type`, JSON-only output), `SubagentStop`
(`last_assistant_message`), `PostToolUse` (`tool_response.{agentId,status}`).

## The live check (item 7), and what it found

Run twice against the real pinned binary 2.1.278, each on a private tmux socket killed in an
EXIT trap. Nothing was left running and `~/.claude` is unchanged.

### First run, on the Partner — which cannot work, and proved why

`goals/build-5.md` item 7 asks for "a Partner turn that spawns two subagents". Spec 09.1 marks
the three subagent hooks **Non-Partner**, and `install.sh` implements that. The live Partner's
settings wired exactly:

```
['PostCompact', 'PostToolUse', 'PreCompact', 'PreToolUse', 'SessionStart', 'Stop']
```

So the turn spawned two real subagents and produced no subagent streams — correctly. It was
still worth running: it live-proved the Non-Partner rule, and it proved the documented
fallback, because both subagents' `Bash` calls landed on `partner-main` carrying their real
`agent_id`s:

```
  seq 5 agent_id= aef9324901c339c76 stream= partner-main
  seq 6 agent_id= a63853b441336393f stream= partner-main
```

It also live-confirmed `transcripts.context_tokens` against a real transcript: `ctx=29985`,
then `ctx=31076` as the conversation grew.

No `run/partner/turn` was written, because I killed that session while its background agents
were still running, so the turn never ended and `Stop` never fired. That is my teardown, not a
defect.

### Second run, on `eng-001` — where the hooks apply

```
--- subagents.json ---
{"a32fb1869e77890cc": "s001", "a54462861917a6a12": "s002"}

--- main stream ---
  1 boundary startup
  2 post_tool Read
  3 post_tool Agent
  4 spawned  s001
  5 post_tool Agent
  6 spawned  s002
  7 closed   s001
  8 closed   s002

--- s001 stream (eng-001-s001-closed.jsonl) ---
  1 open
  2 close  ALPHA

--- run/eng-001/turn ---
{"background_tasks": [], "session_id": "d23cd0bc-…", "ts": "2026-09-20T23:21:21Z"}

--- context files ---  eng-001-main.context.md, eng-001-s001.context.md, eng-001-s002.context.md
--- digests ---        eng-001-s001.digest.md, eng-001-s002.digest.md
--- hook errors ---    (none)
```

Two real `agent_id`s mapped to `s001` and `s002`, three context files, both streams opened and
closed, the digests written, the turn marker written by `Stop` after the background agents
finished, and not one hook error. The agent answered "ALPHA and BETA."

### The bug the live check found

The subagent's context file had an **empty Task section** (`_none yet_`). Spec 07.3 says
section 2 for a subagent stream is "the subagent prompt" — and there is no prompt to be had.
`SubagentStart`'s documented input is `session_id`, `hook_event_name`, `agent_id`,
`agent_type`, `cwd`, `permission_mode`. Nothing else arrives, which the live run confirmed.

The prompt *does* exist in the parent's `PreToolUse(Agent)` payload, which the `guard` hook
already sees, so hx could stash it there and pair it with the next `SubagentStart`. But the
live run spawned two `Agent` calls in parallel and there is nothing in either payload to pair
them by — and a subagent handed another subagent's task is worse than one handed none. So I did
not build that correlation. The section now says the task is the message the subagent was
spawned with and is already in its conversation, which is true: Claude Code delivers the
parent's prompt as the subagent's first message. Raised for a spec decision in
`handoff/to-orchestrator.md`.

## Open questions

1. **Spec 07.3 section 2 for subagent streams** — above. Either reword it, or tell me to build
   the `PreToolUse(Agent)` stash with an explicit "best effort, only when one Agent call is in
   flight" rule.
2. **`subagent-result` returns `additionalContext` on `PostToolUse`.** The docs list
   `additionalContext` for `PostToolUse`, and spec 09.1 says to return the digest that way, but
   I have not seen it injected in a live run — the second run's digests were placeholders, so
   there was nothing worth injecting to observe. Worth confirming in build-6, when real digests
   exist, that the parent actually receives it.
3. **The `exit` field on `post_tool` records is best-effort.** `tool_response` shapes differ per
   tool; hx reads `exit_code`, `exitCode` or `status`, whichever is present, and omits the field
   otherwise. If the Companion needs a reliable failure signal, that is a place to tighten.
4. **`hx bench`'s patch is `git diff HEAD --binary`.** It restores the index with `git reset`
   afterwards, which discards a *staged-but-uncommitted* index the agent had arranged. The
   content is in the patch either way; the staging arrangement is not.

## Handoff entries written

- `handoff/to-orchestrator.md` — the two live findings: `SubagentStart` carries no prompt (needs
  a 07.3 decision), and build-5 item 7's "Partner turn that spawns two subagents" contradicts
  spec 09.1's Non-Partner rule.
- `handoff/build-to-ui.md` — every `event` value the UI will render, which fields each carries,
  that `input`/`output` are 2000-character head excerpts with a `ref` to the full payload, the
  `agent_id → sNNN` map, and the one thing that looks odd and is correct (a subagent's tool
  calls on the main stream when hx never saw it start, which is always the case for the
  Partner).

## Handoff entries read and applied (marked `DONE` in place)

- `handoff/orchestrator-to-build.md` — answers 3 and 4 to build-4's open questions implemented
  as item 8, with five tests.

## Notes for whoever writes build-6

- `hx.streams.append_record` is the only writer; `hx.subagents.assign` the only handle
  allocator. Both take their own locks, so the Companion can read while hooks write.
- `state/<id>/<stream>.digest.md` already exists for every closed stream, holding
  `_pending companion_`. Overwrite it; `subagent-result` already hands its path to the parent.
- `run/<id>/seam` is written by the `log` hook and is currently never consumed. `hx seam` is
  build-7; until then every `stop` logs that it left the marker in place.
- `hx.hook_stop.handle` already calls `flush`, so the Companion wake has a call site waiting.
