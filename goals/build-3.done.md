# build-3 done: Milestones M2 and M3 — `hx compose`, the `context` hook, the `guard`

## What was built

### 1. `hx compose <id> [<stream>]`

`src/hx/compose.py` writes `run/<id>/<stream>.context.md` with exactly the spec 07.3 sections,
in order, each carrying the path it came from:

| # | Section | Source |
|---|---|---|
| 1 | `## Memory` (`## Who your subagents are` on a subagent stream) | `config/<id>/AGENTS.md` below `## UPDATES BELOW ONLY`, or `config/<id>/SUBAGENTS.md` whole |
| 2 | `## Task` | the work item's `## Order`, addenda included; `tasks.json` before the first render |
| 3 | `## Tasks` (main stream only) | the work item |
| 4 | `## Step state` | `state/<id>/<stream>.json`, rendered as markdown |
| 5 | `## Open subagent handles` | `run/<id>/subagents.json` and the `-open` streams |

For `partner`, `## PARTNER.md` and `## Board` follow. An empty section says `_none yet_` rather
than disappearing, so the shape is stable. No caps: nothing is injected, so nothing needs one.

**The persona is never in the file** (spec 02 Identity). When `AGENTS.md` has no header, memory
composes as empty rather than falling back to the whole file — nothing is safer than leaking the
persona into context it is already in the system prompt for.

Step state is **rendered, not dumped**: `render_step_state` turns spec 07.2's schema into
headed bullet lists, with a `**Other**` JSON block for any key the Companion grows, so a schema
that outgrows the renderer is visible instead of silently dropped.

### 2. `hx-hook --id <id> context`

`src/hx/hook_context.py`, on `SessionStart` for all four sources. It composes, prints **exactly
one line** — `Read <abs path> before doing anything else.`, spec 09.1's wording verbatim, never
JSON and never a leading brace — appends a `boundary` record to the main stream, and for the
Partner writes `run/partner/socket.json` in the CONTRACTS.md four-key form from
`CLAUDE_CODE_MESSAGING_SOCKET`/`_TOKEN`. On `source=clear` with a `working` item it runs
`hx goal <id> --now`, which is what finishes a seam; `startup` and `resume` send none.

### 3. `hx-hook --id <id> guard`

`src/hx/hook_guard.py`: the eight rules of spec 09.2, first match wins, deny = exit 2 with the
reason on stderr and nothing on stdout, never exit 1. Every string in `.tool_input` is
evaluated; Bash commands are tokenised and globs cut back to their literal prefix
(`../../config/*/AGENTS.md` resolves through `../../config/`); every candidate is resolved with
symlinks followed and compared against **`HARNESS_ROOT` subtrees**, so a `config/` directory
inside the product worktree stays the agent's own business.

Rule 1 applies the edit to a copy and compares the bytes above the header; an edit it cannot
simulate (a non-unique `old_string`) is denied rather than guessed at. Rule 7 strips leading
env assignments and a leading `cd <root> &&` before asking whether the command starts with
`hx `.

One reading worth stating, because the M3 table settles it: **the Partner may edit any
`config/<id>/AGENTS.md`**, which is how row 11 passes, and follows spec 04 ("`AGENTS.md` above
the header | Partner, rarely, only on direct human instruction").

### 4. `hx-hook` plumbing

`src/hx/hooks.py`. The root resolves first, so the standing `~/.claude` refusal applies before
anything else and every later failure has somewhere to log. A malformed payload, a missing
file, any exception: appended to `logs/<id>/hook-errors.log` and **allowed** — a broken hook
must never be why an agent stops. The single exception is `guard`, which **denies**, because an
error there means hx could not prove the call was safe. `--id` must match `HARNESS_ID` when the
session sets one.

### 5. Auth, rebuilt mid-goal

The live check found that macOS keeps **no credentials file at all** — logins live in the
Keychain, and a fresh `CLAUDE_CONFIG_DIR` is not logged in. The orchestrator's decision:
one long-lived OAuth token per instance at `seed/token`, `claude setup-token`, no Keychain, no
`~/.claude` read, `--from-user-config` removed. Implemented: `install.sh` and `start.sh` refuse
a missing token or one readable by group or other; no home holds credentials; `start.sh` reads
the token from the file and **exports** it, so it is never an argument to `env`, to `tmux -e`,
or to anything else, and cannot appear in `ps`. `hx board` and `hx doctor` check the token and
its mode.

### 6. Two interactive launch gates, found by running the real binary

Both forbidden by "Nothing about launch is interactive" (spec 05, 11), and neither visible
against the fake:

| Gate | What blocks | Key `install.sh` now writes into `run/<id>/home/.claude.json` |
|---|---|---|
| First-run onboarding | the theme picker | `hasCompletedOnboarding: true` |
| Workspace trust | "Is this a project you created or one you trust?" | `projects["<cwd>"].hasTrustDialogAccepted: true` |

`<cwd>` is `wt/<id>`, or `HARNESS_ROOT` for the Partner. Key names were confirmed **read-only**
against a real human-accepted `~/.claude.json` and then proved by the live run. Existing keys
are merged, never reset, so a home that has been running keeps what Claude Code wrote there.
The dialog was never answered by sending keys. I also wrote a third key,
`hasCompletedProjectOnboarding`, then removed it: it does not exist in a real config, and
writing invented keys is not something to leave in.

### 7. `hx ui`

`src/hx/ui_cmd.py` calls `hx.ui.server.serve(root, port)` exactly as the ui lane published it,
and nothing else. `NOT_IMPLEMENTED` is renumbered: `repo`/`push`/`upgrade` 4, `companion` 6,
`seam` 7, `metrics` 8. `hx.hooks.EVENTS` follows, with `stop` at 7.

## How it was verified

```
$ .venv/bin/python -m pytest tests/guard
5 passed in 0.22s

$ .venv/bin/python -m pytest tests/core
394 passed in 102.11s (0:01:42)
```

The M3 table has one test per row (`tests/core/test_guard.py`, rows 01–22 by name) and the M2
criteria each have one (`tests/core/test_compose.py`, 28 tests). `PreToolUse` payload fields
were verified against **https://code.claude.com/docs/en/hooks** on 2026-09-20:
`session_id`, `transcript_path`, `cwd`, `hook_event_name`, `permission_mode`, `tool_name`,
`tool_input`, `tool_use_id`, plus `agent_id`/`agent_type` inside a subagent. The same page
confirms deny = exit 2 with the reason on stderr.

One correction worth recording, because it nearly went the other way: a first doc summary
claimed `SessionStart` has **no** `source` field and that the start reason is only a matcher.
Re-fetching the section verbatim showed `"source": "startup"` in the input schema with values
`startup|resume|clear|compact|fork`. Spec 09 is right; the summary was wrong.

`CLAUDE_CODE_OAUTH_TOKEN` and `claude setup-token` were verified at
**https://code.claude.com/docs/en/authentication** (the token is long-lived, printed by
`claude setup-token`, and takes precedence over any credentials file) and against the pinned
binary's own `claude setup-token --help`.

## The live check (item 6 and item 8) — run, against Claude Code 2.1.278

A scratch instance under my scratchpad, the token the human placed at
`/private/tmp/claude-501/hx-seed/token` copied to `seed/token` mode 0600, a home written by
`install.sh`, launched by `start.sh` on a private tmux socket. The token's contents were never
printed or logged.

**Launch is non-interactive and authenticated.** After the two gate fixes above, the pane
reached its prompt with no dialog:

```
 ▐▛███▛█   Claude Code v2.1.278
▝▜██████▀  Opus 5 with xhigh effort · Claude API
────────────────────────────────────────────────────────────────────────────────
❯
────────────────────────────────────────────────────────────────────────────────
  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents
```

**The `context` hook fired for real.** `run/partner/socket.json` was written from the real hook
environment in the CONTRACTS.md form — `{"socket": "/tmp/cc-socks/94705.sock", "token":
"<32 chars>", "ts": "2026-09-20T22:12:10Z", "session_id": "ece51cb5-…"}` — the context file was
composed, `logs/partner/hook-errors.log` was never created, and the main stream carried:

```json
{"seq": 1, "ts": "2026-09-20T22:12:10Z", "stream": "partner-main", "event": "boundary",
 "source": "startup", "context_file": "…/run/partner/partner-main.context.md",
 "ref": {"transcript": "…/home/projects/…/ece51cb5-….jsonl"}}
```

**The hook's line reached the model.** In the transcript, as an injected `attachment` record:
`Read …/run/partner/partner-main.context.md before doing anything else.`

**The persona works, from the system prompt.** `config/partner/AGENTS.md` above the header gave
the agent the callsign `BLUEHERON` and nothing else did — the context file provably does not
carry the persona. Asked "who are you?":

```
❯ who are you?
  Read 2 files
⏺ BLUEHERON — the Partner of this scratch hx instance (hx-live-probe), running
  one verification run.
  Context file read: nothing dispatched yet, one open task (wait), board shows
  only pods/partner/partner-idle.md. Standing by.
```

**`hx wake partner` reaches a live session.** `HX-WAKE partner accepted`, exit 0, and in the
pane the message arrived and the idle Partner started a turn on it, running `hx read eng-001`:

```
  eng-001 complete: done; hx read eng-001
⏺ Reading completed work item eng-001
  ⎿  $ cd "…/live2" && hx read eng-001
```

### Item 8: `_REAL_PROMPT` was wrong, and the live check is why it is right now

Two things the real TUI does that the docs do not say, both observed directly:

1. **The prompt glyph is `❯` (U+276F), not `>`.**
2. **The input box is drawn while the session is working.** The prompt is present mid-turn, so
   its presence says nothing about readiness.

The signal is the status bar, which carries `esc to interrupt` exactly while a turn is in
flight. Observed, verbatim:

```
  ⏵⏵ bypass permissions on (shift+tab to cycle) · ← for agents                       <- idle
  ⏵⏵ bypass permissions on (shift+tab to cycle) · esc to interrupt · ← for ag…       <- busy
```

`hx.goal.pane_is_idle` now reads busy from that marker, idle from a drawn prompt with no
marker, and falls back to the fake's line. Checked against live captures of both states:
busy → `False`, idle → `True`. Both strings are pinned in
`tests/core/test_lifecycle.py` as the record of what 2.1.278 draws.

**This was a real bug, not a tidy-up.** The previous pattern matched only `>` and only on the
last non-empty line, which is the status bar — so against the real binary it would have read
**every** pane as busy, forever. `hx goal` would have deferred every pointer to `goal-pending`,
and `hx launch`'s readiness wait, which by spec has no timeout, would have hung.

### What the live check did *not* establish

**"The agent's first tool call after a boundary is one Read of that path"** is not what
happened. From the transcript, in order:

```
1. Bash  cat "…/run/partner/partner-main.context.md"
2. Read  "…/run/partner/partner-main.context.md"
```

The agent read the context file **twice**, first with `Bash cat` and then with `Read`. So the
M2 criterion is met in substance — the file was read, first, and its contents demonstrably
shaped the answer — but not in form. It matters beyond tidiness: M7's metric is "Reads of the
context file per seam (must be 1)", and a `Bash cat` does not count as a Read while still
spending the tokens. The wording that produces the behaviour is split between spec 09.1's hook
line (mine) and `config/CLAUDE.md` (the gtm lane's), so I have raised it rather than changing
either unilaterally: `handoff/build-to-gtm.md`.

**"Asked who it is in its first turn, the agent answers from the persona with zero Reads"** is
also not literally met: it answered from the persona, but after the two boundary reads above.
The two criteria pull against each other in a first turn that happens to be a question — one
says read the context file first, the other says answer with zero reads. Flagged in
`handoff/to-orchestrator.md`.

## Open questions

1. **The token is not in the tmux session environment.** CONTRACTS.md says `start.sh` "exports
   it as `CLAUDE_CODE_OAUTH_TOKEN` on the tmux session (never on the command line…)". `tmux -e
   VAR=value` **is** a command line, and `tmux show-environment` prints it back, so I honoured
   the stronger half: the launcher reads `seed/token` itself and exports it in its own process
   before `exec`. The agent gets it; `ps` and `tmux show-environment` do not. Tell me if you
   want the session-env form instead and I will change it.
2. **`config/CLAUDE.md` with `@imports` would add a third launch gate.** A real config has
   `hasClaudeMdExternalIncludesApproved` per project, which implies a prompt when a CLAUDE.md
   imports files outside the project. The gtm lane's `config/CLAUDE.md` uses no imports today,
   so nothing blocks; if it ever does, `install.sh` needs that key too.
3. **`hx compose` takes the task from the work item, not `tasks.json`.** Spec 07.3 says
   "verbatim order and every addendum from `tasks.json`"; goal build-3 item 1 says "the
   `## Order` with its addenda from the work item". I used the work item — it is the live copy
   the agent also sees, and `hx resume` appends addenda there — and fall back to `tasks.json`
   before the first render. Same content, different source of truth; say which you want.
4. **`stop` is numbered build-7.** Spec 13 puts the `goal-pending` consumption and the seam
   handshake at M6, so the Partner's self-dispatch pointer is not delivered until then. If you
   want `goal-pending` consumed earlier, it is a small hook and could land with the M4 batch.

## Handoff entries written

- `handoff/to-orchestrator.md` — the macOS credentials gap (now decided and implemented), and
  the "zero Reads" criterion that the live run contradicts.
- `handoff/build-to-ui.md` — the context file's path, its fixed section list, the
  `_source:` lines, and `hx.compose.render_step_state` for the Agent view.
- `handoff/build-to-gtm.md` — the `Bash cat` versus `Read` finding, which is a wording problem
  in `config/CLAUDE.md` and the standing instructions.

## Handoff entries read and applied (marked `DONE` in place)

- `handoff/orchestrator-to-build.md` — the build-2 answers and renumbering, and the macOS auth
  decision: token auth implemented, `seed/token` mode-checked, `CLAUDE_CODE_OAUTH_TOKEN`
  exported, `--from-user-config` gone, nothing reading `~/.claude`.
- `handoff/ui-to-build.md` — `hx ui` wired to `serve(root, port)`; the stale `show`/`orders`/
  `archive`/`wake` numbers are gone because all four are implemented.

## Notes for whoever writes build-4

- `install.sh` is idempotent over `.claude.json`: it merges, so re-running after an upgrade
  will not reset a running home. `hx upgrade` can call it directly.
- `hx.streams.append_record(root, id, stream, record)` assigns `seq` under a per-stream lock
  and keeps the line under 4 KB (spec 07.1). The M4 `log` hook should use it as is.
- The live scratch instance is reproducible: `hx install --skeleton-only`, copy a token to
  `seed/token` at 0600, `install.sh <id>`, `start.sh <id>`. `hx doctor` reports `token` and
  `home:<id>` as `ok` when it is right.
