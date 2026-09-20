# build-2: Milestone M1 (spec 13): the control-plane commands, plus `hx show --json`

Read `goals/build-1.done.md` (yours), any `handoff/*-to-build.md`, then spec 06 (transitions),
08 (every command, `tasks.json`, dispatch pseudo-code, board invariants), 09 (`stop` hook's
`goal-pending` consumption; implement only what `hx goal` needs now), 12 (how the Partner uses
these), CONTRACTS.md (`hx show --json`).

## Build

1. `tasks.json` store under `run/tasks.lock` (flock) with tmp+fsync+rename writes; the shape in
   spec 08. Re-dispatch of a dependency resets its outcome to `null`.
2. `hx dispatch <id> <order-file> [...]`: validate every order first, then apply under the lock
   exactly as the pseudo-code in 08: archive `logs/<id>/`, `state/<id>/` to `archive/<id>/<ts>/`;
   clear `run/<id>/` except `home/` and `persona.md`; wipe exactly `home/projects/`,
   `home/file-history/`, `home/history.jsonl`; render the work item from `templates/work-item.md`
   with the order verbatim; `idle → working` + `hx goal <id>` when every `after` is `done`, else
   `idle → queued` and no goal marker. For `partner`: no archive, no wipe, no reset; render,
   record, rename, and `hx goal partner` lands in `run/partner/goal-pending`. Interrupted
   dispatch (kill between steps in a test) recovers on re-run.
3. `hx goal <id> [--now]`: the fixed pointer text of spec 06, pasted through a tmux buffer
   (`load-buffer` from a file, `paste-buffer`, then Enter) when the pane shows the idle prompt;
   when the pane is mid-turn, write `run/<id>/goal-pending` instead. `--now` skips the readiness
   wait (used by hooks). Readiness detection: the fake `claude` prints a recognizable idle prompt
   line; make the detector a single function with the real Claude Code prompt pattern noted for
   M6. Write `run/<id>/goal` with the timestamp when pasted. No timeout: wait for the prompt.
4. `hx complete <outcome>` (caller identified by `HARNESS_ID`): refuse with `HX-CHECK-FAILED <id>`
   and exit 1, changing nothing, on any `-open` stream, on a dirty worktree (for `done`, not
   partner), or on a failing `### Checks` block run with `bash -e` in the worktree
   (`HARNESS_ROOT` for partner). Otherwise: `hx flush` (stub until M5: no-op), Digest (stub:
   write `## Digest` placeholder line "pending companion"), outcome into the work item
   frontmatter and `tasks.json`, `working → complete`, remove `run/<id>/goal`, for `done` promote
   every `queued` item whose `after` are all `done` (`queued → working` + `hx goal`), print
   `HX-COMPLETE <id> <outcome>` as the last stdout line, and unless partner call
   `hx wake partner "<id> complete: <outcome>; hx read <id>"`.
5. `hx resume <id> <addendum-file>`: only from `complete` with outcome `blocked|decision`;
   append `## Order addendum <ts>` + file verbatim beneath `## Order`; record in `tasks.json`,
   clear outcome; keep logs, state, `## Tasks`, memory, worktree; `complete → working`;
   `hx compose` (stub until M2); `hx goal` (partner → `goal-pending`).
6. `hx bench <id>`: `complete → idle`, archive the body to `pods/<pod>/archive/<id>-<ts>.md`,
   reset from the template; does not touch `tasks.json`.
7. `hx launch <id>` (idempotent: no-op if the tmux session exists) via `start.sh`;
   `hx restart <id>` (kill session, bare `start.sh`, then `hx goal` when the pane is ready if
   the item is `working`); `hx up` (launch partner and every `working` item); `hx heartbeat`
   (board; restart dead sessions; `hx wake partner` with the board diff when it changed).
8. `hx wake partner "<text>"` per spec 08 and CONTRACTS.md: `hx.wake.wake_partner(root, text)`
   reading `run/partner/socket.json`; returns False when absent or refused; never blocks.
   `run/partner/socket.json` is written by the `context` hook at SessionStart (M2); for now
   tests write it by hand and a fake socket server records what arrived.
9. `hx show <id> [--json]` exactly per CONTRACTS.md, `null` for anything not yet produced
   (step state, metrics, context file arrive in later milestones).
10. `hx read <id>`: print the work item's `## Digest` and `## Open decision` sections.
11. Partner-only commands refuse when `HARNESS_ID` is set and is not `partner`.

## Done when

- Every M1 pass criterion in spec 13 has a test and passes, with the fake `claude` in a real
  tmux (private socket), including: dispatch of 2 of 20 ids changes exactly 2 tasks, 2 work
  items, 2 archives; queued/promotion; the three `HX-CHECK-FAILED` cases change nothing (compare
  a manifest of the root before and after); resume keeps logs, state, `## Tasks`; partner
  dispatch/resume archive nothing and leave `goal-pending`; interrupted dispatch recovers;
  bench archives before reset; `hx show --json` validates against CONTRACTS.md on a dispatched
  item.
- `tools/milestone-check.sh` passes. Committed. `goals/build-2.done.md` written, with a handoff
  to ui (`handoff/build-to-ui.md`) naming the Python functions the UI should call for
  board, show, and wake (module paths and signatures).
