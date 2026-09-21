# build-7 done — the v1 cut (spec 14 D25)

Deletion only, plus the Companion live check build-6 left open, and one live bug it found.

## Deleted

1. **Dependency chains.** `after` (order frontmatter, work-item frontmatter, `tasks.json`,
   board, `hx show`, `hx orders`), the `queued` state, `complete`'s promotion and
   `HX-PROMOTED`, `hx board --require-done`, `tasks.is_ready/unmet/promotable`,
   `frontmatter.frontmatter_list`. An order now has **no frontmatter at all**.
2. **The Partner as a work item.** `hx launch partner` creates none; `hx dispatch`,
   `hx resume`, `hx complete` and `hx goal` refuse the id; `run/<id>/goal-pending` and its
   delivery in the `stop` hook are gone (they existed only for the Partner dispatching
   itself), so `hx goal` waits for the idle prompt and pastes, full stop. `hx board` does not
   list `partner`; `hx show partner --json` is CONTRACTS.md's reduced document.
   `hx wake partner` unchanged.
3. **The `guard` hook.** `hook_guard.py`, the `guard` event, `DENY_ON_ERROR`, the `PreToolUse`
   entry in both homes `install.sh` writes, `tests/core/test_guard.py`. No hook enforces
   anything now, and no hook exits 2.
4. **`tasks.json` locks and atomic writes.** `store.locked`/`lock_path`/`fcntl` gone;
   `tasks.write_tasks` is `json.dump` to the file. The per-stream `seq` locks in `streams.py`
   and `subagents.py` stay — spec 09 and 17.6 keep those.
5. **Enforcement around work items.** `WORK_ITEM_RE` as a gate (the filename splits on its
   last `-` and neither half is checked), the outcome-vs-suffix cross-check, `find_work_items`'
   error list, the nine board invariants and `errors` (board exits 0 always), `hx archive`'s
   `errors`, and the doctor's repo checks. `hx doctor` inspects no work item.
6. **Deployment beyond install.** `repo.py`, `push.py`, `upgrade.py`, `units.py`,
   `src/hx/packaging/{launchd,systemd}/`, `config/repo.json`, `repos/`, `wt/`, `orders/`, the
   `branch` field, dispatch's dirty refusal and git reset, bench's `.patch`. `hx install` is
   four steps. `workdir` is whatever `harness.json` names; `install.sh`/`start.sh` read it
   instead of assuming `wt/<id>`, and `claudeMdExcludes` covers that directory.
7. **Task text in two places.** `hx dispatch` and `hx resume` delete their input file on
   success; a re-run whose order file is already consumed re-renders from `tasks.json`.
   `hx orders --json` reads `tasks.json` only — no graph, no `file_matches_record`.

## Added (item 9, the skills handoff, and the bug the live check found)

`install.sh` copies `hx-partner` + `hx-fleet` into the Partner home, `hx-worker` into a
worker's, `hx-companion` into the Companion home; `hx install` ships `skeleton/personas/`
(`handoff/orchestrator-to-build.md`, 2026-09-20).

**Live bug, fixed:** a freshly launched pane draws `❯ Try "create a util logging.py that..."`,
so the idle detector — which demanded an *empty* prompt line — read every fresh pane as busy
and `hx launch` hung forever (no timeouts anywhere). `goal._PLACEHOLDER_PROMPT`; the captured
chrome is pinned as `tests/core/test_lifecycle.py::REAL_IDLE_PLACEHOLDER`.

## Line counts

| | before (`1db83fa`) | after |
|---|---|---|
| `src/hx/**.py` (lane; not `ui/`) | 7460 | 6339 |
| `src/hx/skeleton/adapters/claude/*.sh` | 453 | 484 |
| `tests/core/**` + `tests/fakeclaude/claude` | 6142 | 5281 |

Five modules and one test module deleted outright.

## Tests

`tools/milestone-check.sh build` → `MILESTONE-CHECK PASSED for build`: 5 guard, 395
core/fakeclaude, both `[100%]`. 474 before the cut took the tests for removed behaviour with it.

## Live vs. fake

- **Live, pinned 2.1.278 with the seed token, scratch root under the session scratchpad:**
  `hx launch eng-001` started *two* real Claude Code sessions (`:main` and `:companion`; the
  Companion home carried only its `Stop` hook and `hx-companion`), then two passes over a
  5- then 7-record stream. The Companion wrote `state/eng-001/eng-001-main.json` at `seq 6`
  then `seq 7` — a closed step with commit `9f2c1ab`, an open step whose `next` names the
  real fix, a two-file `working_set` with notes, `prompt_version`, no hook errors. **Pass 1
  needed one manual Enter (open question 2); everything else was hx.** Everything launched
  was killed.
- **Fake:** everything else — `tests/core` drives the fake `claude` in a real tmux pane on a
  private socket: the paste transport, every hook, the Companion pass/ingest loop.

## Open

1. Answered while this goal ran (`handoff/to-orchestrator.md`): the wake spelling is
   `hx companion <id> --wake <stream>` and the spec now agrees; CONTRACTS.md's `wt/<id>` line
   is fixed; `hx heartbeat`'s missing `hx launch partner` and `hx doctor`'s missing
   `.claude.json` check are build-8 item 9. Nothing left for me in any of them.
2. **The first paste into a brand-new pane loses its Enter, and I did not fix it.** Live,
   pass 1's `/clear` and pointer both sat unsubmitted in the input box until I pressed Enter
   by hand; pass 2, into the same now-warm pane, submitted on its own. `send-keys` text plus
   a *later* Enter does submit, so the Enter `goal.paste` sends microseconds after
   `paste-buffer` is swallowed while the fresh TUI ingests the paste. Candidates:
   `paste-buffer -p` (bracketed — it would also change what the fake reads), or wait for the
   pasted text to appear before pressing Enter. I proved neither, and it is a behaviour
   change to the transport at the end of a deletion goal, so I handed it to build-8, which
   owns the live suite; the orchestrator has made it `goals/build-8.md` item 10, first.
   **Untouched, this hangs the first Companion pass of every real launch.**
3. `876cf12` swept the gtm lane's uncommitted `src/hx/skeleton/**` and `src/hx/skills/**` into
   a build-lane commit (`git add src/hx`). No content changed; recorded to gtm and to you.

## Handoffs written

- `handoff/build-to-ui.md` — every changed shape: board (no `errors`/`after`/`ready`/
  `goal_pending`, exits 0, no `partner`), orders (tasks.json only), archive (no `errors`),
  show (no `after`; `partner` is the reduced document), the commands that are gone.
- `handoff/build-to-gtm.md` — `{{after}}` must leave `templates/work-item.md`;
  `personas/partner/AGENTS.md` is in `EXPECTED_SKELETON_FILES`; what is gone from the docs;
  the index apology.
- `handoff/to-orchestrator.md` — two entries: the four questions (answered in place, DONE),
  and the first-paste Enter bug, now `goals/build-8.md` item 10.
