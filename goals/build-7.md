# build-7: the v1 cut (spec 14 D25). Delete, do not deprecate.

Read `spec/14-open-items.md` D25, `ORCHESTRATION.md` (constraints, the v1 cut bullet), then the
rewritten spec 03, 04, 06, 08, 09, 12, 13, 16, 17, and CONTRACTS.md. Nothing here adds
behaviour; everything removes it. Tests for removed behaviour are deleted with it. The done file
is a list of what was deleted and the line count before and after.

## Delete

1. Dependency chains: `after` everywhere (order parsing, work-item frontmatter, `tasks.json`,
   board), the `queued` state, promotion in `hx complete`, `hx board --require-done`.
2. The Partner as a work item: `pods/partner`, `orders/partner.md`, `hx dispatch partner`,
   `hx resume partner`, `hx complete` for partner, `run/<id>/goal-pending` and its delivery in
   the `stop` hook, `hx goal --now` paths that existed only for it (keep `--now` for the
   context-on-clear hook). The Partner is launched by `hx up`/`hx launch partner` and gets its
   goal from the human in chat. `hx wake partner` stays.
3. The `guard` hook: `hx-hook guard`, its rules, its tests, its entry in `install.sh`'s hooks.
4. Locks and atomic writes on `tasks.json`: plain `json.dump` to the file.
5. State in filenames: work items are `pods/<pod>/<id>.md` with `state:` in frontmatter, set by
   dispatch/complete/resume/bench. Delete the filename regex, the transition machinery, all
   `hx board` invariants and `errors`, and the doctor checks of items. `hx board` is the plain
   listing in CONTRACTS.md; `hx doctor` checks only what spec 08 now lists.
6. Deployment beyond install: `hx repo add`, mirror, sparse worktrees, `hx push`, `hx upgrade`,
   unit rendering and `src/hx/packaging/` use in install (the gtm lane deletes the templates),
   `config/repo.json`, `repos/`, `wt/` management, the `branch` field, dispatch's git reset and
   dirty-worktree refusal, bench's patch. `harness.json.workdir` is any absolute directory;
   `hx complete done` still requires a clean `git status` there when it is a git repo.
7. Task text in two places: `hx dispatch` and `hx resume` delete their input file after
   success; `orders/` is not a directory hx knows; `hx orders --json` reads `tasks.json` only.
8. Everything that referenced the above in `cli.py`, `board.py`, `show.py`, `orders.py`,
   `doctor.py`, `install.py`, `dispatch.py`, `complete.py`, `resume.py`, `bench.py`,
   `install.sh`, `start.sh`, the fake, and `tests/core/`.

## Keep unchanged

Launch and adapters (token, `IS_SANDBOX=1`, pre-seeded state file, persona file), `hx goal`,
`hx complete` (checks, `HX-COMPLETE`, `HX-CHECK-FAILED`, wake partner, digest), `hx resume` for
blocked/decision, `hx bench` (body archive only), the `context`, `log`, `subagent-*`, `stop`
(turn marker, seam, Companion wake) hooks, the Companion session, `hx flush`, `hx show`,
`hx archive`, `hx read`, `hx task`, `hx up`, `hx heartbeat`, `hx install` with the token gate.

## Done when

- `tools/milestone-check.sh build` passes; `grep -rn` of `src/` for `after`, `queued`, `guard`,
  `flock`, `goal-pending`, `sparse`, `mirror`, `push`, `upgrade`, `require-done` returns only
  prose.
- Committed path-scoped. `goals/build-7.done.md` written, short, with the line counts and a
  `handoff/build-to-ui.md` entry naming the changed shapes.
