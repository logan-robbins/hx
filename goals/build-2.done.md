# build-2 done: Milestone M1 (spec 13), the control plane, plus `hx show --json`

## What was built

Every item in `goals/build-2.md`. Sixteen new modules under `src/hx/`, one per command group
(spec 08), and 77 new tests.

### 1. `tasks.json` under `run/tasks.lock`

`store.py` is the two primitives every control-plane write uses: `locked(root)` holds
`run/tasks.lock` with `flock` and no timeout, and `atomic_write_text`/`atomic_write_json` are
tmp-file + `fsync` + same-directory rename. `tasks.py` gained the writers. A re-dispatch
replaces the whole entry, so a dependency's outcome resets to `null` and a stale completion
never satisfies a newer dependent.

### 2. `hx dispatch`

Validates every order and every precondition before anything is written, then applies under
the lock in the write order spec 08 gives: tasks, then per-id archive, reset, render, rename,
goal. Per id: `logs/<id>/` and `state/<id>/` move to `archive/<id>/<ts>/` and are recreated;
`run/<id>/` is cleared except `home/` and `persona.md`; the home loses exactly `projects/`,
`file-history/` and `history.jsonl`, so `settings.json`, `.credentials.json`, `skills/` and
`agents/` survive; `run/<id>/subagents.json` becomes `{}`; the work item is **rendered** from
`templates/work-item.md` with the gtm lane's five `{{…}}` tokens, by literal replacement, never
reconstructed in Python; and the item lands `working` with the pointer sent, or `queued` with
no goal marker.

For `partner`: no archive, no wipe, no reset — its session, streams and state are continuous —
and its pointer lands in `run/partner/goal-pending`, because its own pane is mid-turn when it
dispatches itself.

**Interrupted dispatch recovers on re-run.** An id whose work item is no longer `idle` but
whose `tasks.json` record is byte-identical to the order being dispatched is treated as
already applied and skipped; anything else is refused. So the second run of an interrupted
`hx dispatch` finishes the ids that never got applied and leaves the rest alone, while a
genuine re-dispatch of a working item is still refused.

### 3. `hx goal`

The spec 06 pointer, verbatim, pasted through a tmux buffer: `load-buffer` **from a file**
(never `set-buffer` with an argument, so the pointer never crosses a command line),
`paste-buffer`, then `Enter`. At the idle prompt it pastes; mid-turn it leaves
`run/<id>/goal-pending` for the `stop` hook. `--now` skips the check, for the `context` hook on
`clear` and the `stop` hook; `--wait` blocks for the prompt with no timeout, which is what
`hx launch` and `hx restart` use.

`run/<id>/goal` is written in **both** cases with the timestamp of the call, per the spec 08
update mid-goal, so the `working` invariant holds during the turn in which a busy pane is owed
its goal.

Readiness detection is one function, `hx.goal.pane_is_idle`, over `hx.goal.IDLE_PROMPTS`. It
carries two patterns: the fake's `hx-fake-idle>` line, which is authoritative today, and
`_REAL_PROMPT` for Claude Code's `>` input box, **to be confirmed against the pinned binary at
M6** — that is the single place to correct it.

### 4. `hx complete`

Refuses with `HX-CHECK-FAILED <id>` and the failing output, exit 1, changing nothing, on any
`-open` subagent stream (for every outcome), and for `done` additionally on a dirty worktree
(not for `partner`) or a `### Checks` block that does not exit 0 under `bash -e` in the
worktree (`HARNESS_ROOT` for `partner`). The checks come from the order recorded in
`tasks.json`, not from the file on disk, so an order edited after dispatch cannot change what
is checked.

On success: `hx flush` (a no-op until M5), a `## Digest` placeholder, the outcome into the work
item frontmatter and `tasks.json`, `working → complete`, the goal marker removed, every
`queued` item whose `after` are all `done` promoted to `working` with its pointer sent, and
`HX-COMPLETE <id> <outcome>` as the **last line of stdout**. Unless the caller is `partner`, it
wakes the Partner.

### 5–6. `hx resume`, `hx bench`

`resume` takes only `complete` with outcome `blocked` or `decision`; it appends
`### Order addendum <ts>` plus the file verbatim inside the `## Order` section (above
`## Definition of done`, not at the end of the body), records the addendum in `tasks.json`,
clears the outcome, and keeps logs, state, `## Tasks`, memory and worktree. Only the order
grows. `bench` archives the completed body to `pods/<pod>/archive/<id>-<ts>.md` **before** the
reset, then resets from the template and renames `complete → idle`, and does not touch
`tasks.json`.

### 7. `hx launch`, `hx restart`, `hx up`, `hx heartbeat`

`launch` is idempotent — a live session is left alone, but a `working` item still gets its
pointer — and it creates the `-idle` work item when missing, creates the worktree directory,
and passes `HX_SKILLS_DIR` to `install.sh`. `restart` flushes, composes, relaunches bare
through `start.sh` (whose `respawn-window -k` is spec 08's "kill window `main`") and sends the
pointer once the pane is ready. `up` launches every `config/<id>/`, `partner` first.
`heartbeat` runs the board, restarts every `working` item whose session is dead, and wakes the
Partner with the board diff only when an item is `working` or `queued` **and** the board
changed since the last heartbeat.

### 8. `hx wake partner`

`hx.wake.wake_partner(root, text) -> bool` is the function `CONTRACTS.md` names and the UI
calls. `wake_partner_status` returns `accepted` / `no-socket` / `refused`. The CLI prints
`HX-WAKE partner <status>` as its last line and exits 0 only for `accepted`, 3 otherwise, per
the contract added mid-goal. `hx complete` and `hx heartbeat` treat a failed wake as a warning
on **stderr** and still succeed, so `HX-COMPLETE` stays the last line of stdout.

### 9–11. `hx show`, `hx orders`, `hx archive`, `hx read`, `hx task`

`hx show <id> --json` is the `CONTRACTS.md` document field for field, with `null` for what
later milestones produce: `step_state` is `{}` until M5, `context_file.text` is `null` until
M2, `metrics` is `null` until M7. `partner` additionally carries `partner_md`. The `pane`
block falls back to `logs/<id>/<id>-pane.log` when the session is dead. `hx orders --json` and
`hx archive --json` are the two views adopted from the ui lane, including
`file_matches_record`, `waiting_on`, and the `after` graph.

### 12–13. The pane log and `config/hx.json`

`start.sh` arms `tmux pipe-pane -o` at `logs/<id>/<id>-pane.log` right after the session is
created. It is not a Companion stream: `hx.streams._SUBAGENT_RE` ignores it, and because it
lives in `logs/<id>/` `hx dispatch` archives it with the rest — then **re-arms the pipe**,
because a pipe started before the archive keeps writing into the moved file.

`hx install --skeleton-only` now writes `config/hx.json` with `hx_bin`, `hook_bin` and
`python_bin`, resolved from `sys.executable`'s own `bin/` (the venv's, not the base
installation's) and falling back to `PATH`. `hx doctor` **fails** when any is missing or not
executable. `install.sh` and `start.sh` prefer `python_bin`.

### 14. Who may call what

`hx dispatch`, `resume`, `bench`, `launch`, `restart` and `read` refuse when `HARNESS_ID` is
set and is not `partner`; they run for a system caller with no `HARNESS_ID` (`hx up`,
`hx heartbeat`). `hx complete` and `hx task` act on the caller's own id and require it.

## How it was verified

```
$ .venv/bin/python -m pytest tests/guard
5 passed in 1.57s

$ .venv/bin/python -m pytest tests/core
312 passed in 78.76s (0:01:18)
```

77 of those 312 are new in this goal. The transition, view and lifecycle suites all drive a
**real tmux server on a private socket** (`tmux -L hx-test-<pid>`, killed at teardown) with the
fake `claude` in the pane, so the pointer really is pasted through a tmux buffer and really
does arrive on the agent's stdin. Nothing points `HOME`, `CLAUDE_CONFIG_DIR` or `HARNESS_ROOT`
at the user's `~/.claude`, and no test reaches the user's tmux server.

Every M1 pass criterion in spec 13, and where it is pinned:

| Criterion | Test |
|---|---|
| every spec 06 transition passes | `test_transitions.py`, 37 tests |
| all others exit non-zero | `test_dispatch_refuses_a_non_idle_item`, `test_resume_refuses_an_outcome_that_is_not_paused`, `test_bench_refuses_an_item_that_is_not_complete`, `test_read_refuses_an_item_that_has_not_completed` |
| dispatching 2 of 20 ids changes exactly 2 tasks, 2 work items, 2 archives | `test_dispatching_2_of_20_ids_changes_exactly_2` — builds 20 ids with prior logs and state, then diffs a manifest of the whole root |
| unmet `after` lands `queued` with no goal marker | `test_idle_to_queued_when_an_after_entry_is_unmet` |
| promoted by `hx complete done` of the last dependency | `test_a_queued_item_is_promoted_by_its_last_dependency`, `test_a_queued_item_with_two_dependencies_waits_for_both`, `test_a_blocked_dependency_does_not_promote` |
| the three `HX-CHECK-FAILED` cases change nothing | `test_complete_refuses_a_failing_check_and_changes_nothing`, `..._a_dirty_worktree_...`, `..._an_open_subagent_stream_...` — each compares a manifest of the root before and after |
| resume keeps logs, state and `## Tasks` | `test_resume_keeps_logs_state_and_tasks_and_appends_the_addendum` |
| partner dispatch and resume archive nothing, leave `goal-pending` | `test_partner_dispatch_archives_nothing_and_leaves_goal_pending`, `test_partner_resume_...` |
| interrupted dispatch recovers on re-run | `test_an_interrupted_dispatch_recovers_on_re_run` — interrupts between the two ids, then re-runs the same command |
| bench archives the body before reset | `test_bench_archives_the_body_before_it_resets` |
| `hx show --json` validates against CONTRACTS.md on a dispatched item | `test_show_json_matches_contracts_on_a_dispatched_item` and four more |

### End to end on a scratch instance

`hx up` → the Partner dispatches a two-item plan with an `after` chain in one call → the
dependent queues → the first completes → hx promotes the second and sends its pointer, with no
Partner wake in between:

```
### hx up (partner first)
HX-LAUNCH partner started goal=none
HX-LAUNCH eng-001 started goal=none
HX-LAUNCH eng-002 started goal=none

### the Partner dispatches the whole plan in one call
HX-DISPATCH eng-001 working goal=pasted
HX-DISPATCH eng-002 queued goal=none

### hx board  (eng-002 is queued behind eng-001, no goal marker)
pods/partner/partner-idle.md  -  -  0  -
pods/engineers/eng-001-working.md  -  -  0  2026-09-20T21:19:16Z
pods/engineers/eng-002-queued.md  eng-001  -  0  -
board exit: 0

### the pointer that reached eng-001's pane
/goal The order for eng-001 is in /…/m1/pods/engineers/eng-001-working.md; read it first.
Done when `hx complete <outcome>` has been run and its output line
`HX-COMPLETE eng-001 <outcome>` appears.

### eng-001 finishes: hx complete done promotes eng-002
hx: complete: the Partner was not woken (no-socket); the completion is recorded and `hx heartbeat` will report it
HX-PROMOTED eng-002 working goal=pasted
HX-COMPLETE eng-001 done

### hx board after the promotion
pods/partner/partner-idle.md  -  -  0  -
pods/engineers/eng-001-complete.md  -  done  0  -
pods/engineers/eng-002-working.md  eng-001  -  0  2026-09-20T21:19:16Z
board exit: 0

### hx orders --json
graph edges: [{'from': 'eng-001', 'to': 'eng-002', 'met': True}]
errors: []
```

## Verified live against Claude Code vs. against the fake

**Against the fake `claude` only.** Everything in this goal: the pointer arriving in the pane,
the idle-prompt detection, `goal-pending`, the launch argv, the pane log. The fake now prints
`hx-fake-idle>` when it waits for input and understands `/fake-hold` and `/fake-release`, which
is how a test drives `hx goal`'s two paths deterministically. M6 is where the real binary takes
over (spec 13).

**Not verified against Claude Code, and the one place it matters.** `hx.goal._REAL_PROMPT` is
my reading of the TUI's `>` input box from the docs, not from the binary. If it is wrong,
`hx goal` without `--now` will see a live idle pane as busy and defer to `goal-pending`, which
the `stop` hook then delivers — degraded, not broken, and the board invariant still holds
because the marker is written either way. **M6 must confirm it against the pinned binary.**

**Carried forward from build-1, unchanged.** The settings keys in `install.sh` were verified
against `code.claude.com/docs/en/settings-reference`, `/docs/en/memory`,
`/docs/en/cross-session-messaging` and `/docs/en/hooks` on 2026-09-20; the wake wire format
(auth line, then the user message, newline-terminated) is from the cross-session-messaging
page and spec 01.1's live test E6.

## Open questions

1. **`hx launch` does not start the Companion window.** Spec 08 says launch runs
   `hx companion <id>` in window `companion`; the Companion loop is M5, so only window `main`
   exists today. Nothing tracks that but this note.
2. **The worktree is a plain directory.** `hx launch` creates `wt/<id>` if missing so a launch
   is possible; cutting it sparsely from the bare mirror is `hx repo add`, now build-4. An
   instance built today therefore has no git worktree unless a test makes one, and
   `hx complete done`'s clean-worktree check is skipped when `git status` fails (no repo).
   That skip is deliberate for now but should become a refusal once build-4 lands.
3. **Relative order paths resolve against the caller's cwd**, not against `HARNESS_ROOT`. That
   is right for the Partner, whose cwd *is* `HARNESS_ROOT` (spec 17.4), and it is what the
   acceptance run above exercises. Anything invoking `hx dispatch` from elsewhere must pass an
   absolute path. Say so if you would rather relative paths resolved against the root.
4. **`NOT_IMPLEMENTED` numbers after build-4.** I set `repo`, `push` and `upgrade` to `4` per
   the plan change. `companion` (6), `seam` (7), `metrics` (8) and `ui` (10) are still the old
   milestone+1 numbers and are now probably wrong, since build-3 covers two milestones. They
   are one dict in `cli.py`; tell me the numbers and I will set them.
5. **`run/partner/socket.json` keys are not in CONTRACTS.md.** `hx.wake.read_socket` accepts
   both `{"socket", "token"}` and `{"CLAUDE_CODE_MESSAGING_SOCKET",
   "CLAUDE_CODE_MESSAGING_TOKEN"}`, because the `context` hook (M2) writes what Claude Code
   exported to it. Worth pinning in `CONTRACTS.md` before build-3 writes that hook.

## Handoff entries written

- `handoff/build-to-ui.md` — the functions the UI binds to in ui-4: `hx.board.collect`,
  `hx.show.collect`, `hx.orders.collect`, `hx.archive.collect`, `hx.wake.wake_partner` and
  `wake_partner_status`, each with its signature and return value; the 404-versus-502 rule
  (only `show.collect` raises `hx.errors.NotFound` for an unknown id; the view functions never
  raise for a bad id and put the problem in `errors`); the `hx wake` exit-code contract; and
  two behaviours to render rather than treat as bugs — `hx board --json` exits 1 on a fresh
  instance, and a benched id is `idle` with its last outcome.

## Handoff entries read and applied (marked `DONE` in place)

- `handoff/orchestrator-to-build.md`, "two items for build-2's close" — both applied: the
  `HX-WAKE` contract with its exit codes and the warning-not-failure behaviour in
  `hx complete` and `hx heartbeat`, and the function list published to the ui lane.
- `handoff/ui-to-build.md` — `hx ui` will call `hx.ui.server.serve(root, port)` exactly as
  specified (build-10, not this goal); the stale `NOT_IMPLEMENTED` numbers the ui lane flagged
  for `show`, `orders`, `archive` and `wake` are gone, because all four are implemented now.
- Mid-goal spec updates applied: `hx goal` writes `run/<id>/goal` in both cases; `hx bench`
  leaves `tasks.json` alone and a benched id shows as `idle` with its last outcome, which has
  its own test; packaging moved to build-4 in `NOT_IMPLEMENTED`.

## Notes for whoever writes build-3

- `hx compose` and `hx flush` exist as call sites with the right signatures
  (`hx.compose.compose(root, id, stream)`, `hx.flush.flush(root, id)`); M2 and M5 fill them in.
  `hx resume` and `hx restart` already call `compose`, and `hx complete` already calls `flush`.
- The `stop` hook's half of `goal-pending` is the piece M2/M3 needs from `hx goal`: read
  `run/<id>/goal-pending`, run `hx goal <id> --now`, remove the marker. `send_goal(now=True)`
  is that call.
- `hx.goal.pointer_text(root, id)` gives the exact pointer without sending it, for the hook.
- The `context` hook writes `run/partner/socket.json`; `hx.wake.read_socket` already reads both
  key spellings, so write whichever is convenient.
