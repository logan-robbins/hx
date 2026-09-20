# build-3: Milestones M2 and M3 (spec 13): `context` hook, `hx compose`, Claude adapter, `guard`

Read `goals/build-2.done.md` (yours), `handoff/orchestrator-to-build.md`, any other
`handoff/*-to-build.md`, then spec 02 (Single-file context, Identity), 07.3 (context file
sections, in order), 09 (`context`, `guard`, 9.3 handshake, 9.4), 11 (Adapters), 13 M2 and the
M3 table, 04 (ownership, which the guard enforces).

## Build

1. `hx compose <id> [<stream>]`: writes `run/<id>/<stream>.context.md` with exactly the spec 07.3
   sections in order: 1 memory (`config/<id>/AGENTS.md` below `## UPDATES BELOW ONLY`; for a
   subagent stream `config/<id>/SUBAGENTS.md`), 2 task (the `## Order` with its addenda from
   the work item), 3 `## Tasks` from the work item, 4 step state (`state/<id>/<stream>.json`,
   rendered; "none yet" when absent), 5 open handles (`run/<id>/subagents.json` and `-open`
   streams). Never the persona. For `partner`: also `PARTNER.md` and the `hx board` text.
   Each section carries the source path it came from. No caps.
2. `hx-hook --id <id> context` (SessionStart, all four sources, JSON on stdin): write
   `run/partner/socket.json` from `CLAUDE_CODE_MESSAGING_SOCKET`/`_TOKEN` when id is partner;
   `hx compose`; print exactly one line to stdout: the absolute path of the context file with
   the one-sentence instruction the spec gives ("Read this file first: <path>"); on
   `source=clear` and a `working` item, run `hx goal <id> --now` (spec 09.2). `startup` and
   `resume` send no goal. Record the event in the main stream (a boundary record, spec 07.1).
3. `hx-hook --id <id> guard` (PreToolUse, all tools): the eight rules of spec 09.2 in order,
   deny = exit 2 with the reason on stderr and nothing on stdout; allow = exit 0, no output.
   Path resolution: every path argument (Read/Edit/Write/MultiEdit `file_path`, Bash command
   text tokens that look like paths, notebook paths) is resolved with symlinks followed and
   compared against `HARNESS_ROOT` subtrees; rule 1 (AGENTS.md above the header byte-identical)
   compares the proposed content against the file for Edit/Write. Rule 7: a Bash command that
   mentions `$HARNESS_ROOT` or the root path is allowed only when it starts with `hx ` (after
   leading env assignments and `cd <root> &&` are stripped). Rule 5: `orders/` writable only by
   partner. Rule 3: `PARTNER.md` partner-only. No timeouts; no network.
4. `hx-hook` plumbing shared by all hooks: read JSON from stdin, never crash (a malformed
   payload logs to `logs/<id>/hook-errors.log` and allows, except `guard`, which denies), and
   the `--id` must match `HARNESS_ID` when set.
5. Tests, M2: for `startup|resume|clear|compact` payloads the hook prints one path line; the file
   holds the five sections in order and no persona text (assert the persona's first line is
   absent); Partner's file holds `PARTNER.md` and board output; on `clear` with a `working`
   item `hx goal --now` is invoked (fake `claude` records the pasted pointer); `socket.json` is
   written for partner from the env. M3: the whole spec 13 M3 table, row by row, each as one
   test with the real payload shape from `code.claude.com/docs/en/hooks` (verify the
   PreToolUse payload fields and record the URL), run under `HARNESS_ID` of the acting id.
6. Live check (real Claude Code, one session, scratch root under your scratchpad): launch
   `partner` with `start.sh` against the real pinned binary and a home installed by
   `install.sh` from a seed you create with `hx install --from-user-config`-equivalent copying
   of your own session's credentials **read-only** (never write to `~/.claude`); confirm in the
   pane that SessionStart printed the context path, that the first tool call is one Read of it,
   and that asked "who are you" it answers from the persona with zero Reads. Kill the session
   after. Record the transcript lines in the done file. If credentials cannot be copied without
   touching `~/.claude`, say so and stop at the fake.

## Done when

- `tools/milestone-check.sh` passes for `tests/guard` and `tests/core`.
- The M3 table has one passing test per row; the M2 criteria each have a test.
- The live check is recorded, or its blocker is named.
- Committed path-scoped. `goals/build-3.done.md` written, with handoffs (the ui lane needs the
  context-file path and the step-state rendering you chose; write `handoff/build-to-ui.md`).
