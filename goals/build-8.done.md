# build-8 done — M6: seams for real, and four live findings

## The blocking item, and the two "first" ones

0. **`--setting-sources user` on every launch** (`start.sh`, agent and Companion share the
   `exec`). A workdir is a checkout hx does not own and its `.claude/settings.json` is not
   hx's to trust. Flag verified against `claude --help` on the pinned 2.1.278 and against
   `code.claude.com/docs/en/cli-reference` ("Comma-separated list of setting sources to load
   (`user`, `project`, `local`)"). Managed settings are a separate level and still apply.
10. **The paste transport.** `goal.paste` waits for the text to reach the input box, presses
    Enter, waits for the box to let go, and presses again if it has not — up to four times.
    Two things eat an Enter, both found live: the paste still being ingested, and the
    slash-command autocomplete menu (01.1 E9). `goal.input_box()` is the detector: everything
    from the last prompt glyph on, which is the box; the transcript is above it.
11. **`hx` on the agents' PATH.** `hx install` writes `bin/{hx,hx-hook}` as symlinks to the
    recorded entry points; `start.sh` prepends `$HARNESS_ROOT/bin` for agent and Companion;
    `hx doctor` fails on a missing or dangling link.

## The rest

1, 2. **`hx seam <id>`** (`src/hx/seam.py`): defers while `run/<id>/turn` shows background
   work and leaves the marker for the next boundary; otherwise flush, compose, `/clear`, the
   spec 07.4 `seam` record (`source`, `prompt_version`, `context_tokens_before`,
   `context_file_bytes`, `working_set_size`), marker gone. The `stop` hook consumes the
   marker. Both triggers were already in place from build-5 and build-6.
3. **`precompact`/`postcompact`**, which had never been implemented — they printed "not
   implemented (build-5)". Log-only, per spec 02 and 09.1; `precompact` also flushes so the
   Companion is at the head before anything is summarised away. Both fire for subagents and
   land on the compacting thread's own stream. **Not** blocking — see Open 1.
7. `turn: {ts, background_tasks}` in `hx show --json`.
8. `hx ui` in tmux session `ui`, started by `hx install` and `hx up` (which print the URL)
   and restarted by `hx heartbeat`. Idempotent.
9. `hx heartbeat` launches a dead Partner; `hx doctor` checks each home's pre-seeded
   `.claude.json` as well as its `settings.json`.
12. `hx heartbeat` re-pastes the pointer to a `working` agent that is alive, idle, and has no
    `HX-COMPLETE` in its stream. This is the recovery for 01.1 E10 below.
D26. `start.sh` exports `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP=100000` for every agent and
    Companion, on the session and in the exec'd env.

## Live findings (recorded by the orchestrator as 01.1 E9 and E10)

- **E9.** A slash command's autocomplete menu eats the first Enter. `hx seam` and every
  Companion wake paste `/clear`, so this stranded every seam until `submit` kept pressing.
- **E10.** A `/goal` pauses itself: "A hook blocked the turn from ending 9 consecutive times
  — overriding and ending turn", then "Goal paused · send a message to continue". Nine turns,
  not forever. D26 raises the cap; item 12 is the fallback.

## Tests

```
$ tools/milestone-check.sh build
== required: tests/guard
.....                                                                    [100%]
== required:  tests/core tests/fakeclaude
..                                                                       [100%]
MILESTONE-CHECK PASSED for build (own paths; add --all for the advisory run)

$ HX_LIVE=1 HX_SEED_TOKEN=<the setup-token file> .venv/bin/python -m pytest tests/live
..........                                                               [100%]
EXIT 0
```

5 guard, 434 core/fakeclaude, 10 live. The live suite skips with a one-line reason when
`HX_LIVE=1` is unset or no seed token is reachable, so it never runs by accident.

## Live vs. fake

- **Live**, pinned 2.1.278 with the seed token, each test on its own tmux server, all reaped:
  item 0 in a copy of the m8 tripwire repo (a Bash tool call writes its file; the tripwire
  line never appears; the checkout's `.claude/` is never touched), the exec'd argv carrying
  `--setting-sources user`, `hx board` run off the agent's PATH, the first paste into a
  brand-new pane and a second into a warm one, `/clear` cutting a conversation, the seam
  handshake in the order spec 09.2 fixes, ten seams with no `compact_pending`, and `hx
  restart`/`hx launch` delivering the pointer.
- **Fake:** everything else — `tests/core`, including `hx seam`'s defer/take, the seam
  record's shape, the compaction hooks, the `bin/` links, the PATH, and D26's env var.

## Open

1. **Item 3 says `PreCompact` should block; spec 02 and 09.1 say it never may.** A block
   suppresses compaction for the whole turn (E4) and also disables subagent compaction (E8).
   I built the log-only behaviour the spec specifies. The criterion item 3 names — no
   `compact_boundary` across ten seams — holds either way and is tested live.
2. **Item 5 asks `hx upgrade` to be completed; build-7 deleted it** under D25, and spec 17 no
   longer mentions it. I built nothing rather than undo the cut.
3. Item 4's "`goal-pending` left by a mid-turn `hx dispatch partner`" is gone with the cut:
   the Partner has no work item and is never dispatched. The rest of item 4 is tested live.
4. `hx metrics` is still the one unimplemented command (`hx: metrics: not implemented`).

## Handoffs written

- `handoff/to-orchestrator.md` — the two spec contradictions above, and E9/E10 (answered:
  recorded in 01.1, and D26).
- `handoff/build-to-ui.md` — `turn` in `hx show --json`, the `ui` session and its URL,
  `goal.input_box()`, and D26's env var.
