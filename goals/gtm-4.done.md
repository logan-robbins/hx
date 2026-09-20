# gtm-4 done: the deploy proof, docs verified against the adapters, an unscripted scenario

Lane `gtm`, goal 4. `tests/guard` (5), `tests/packaging` (77 + 1 skipped) and `tests/scenario`
(70) all pass. Everything is committed path-scoped.

`packaging/e2e-deploy.sh` runs and reaches its gate: **`SKIPPED (waiting on build-4)`**,
exit 0. That is the expected outcome today, not a failure — the full `hx install` is build-4
and had not landed when this goal closed.

## 1. `packaging/e2e-deploy.sh` — the M10 proof for one machine

Sixteen steps on top of `e2e-install.sh`, each naming itself on failure. In a `HOME` that did
not exist a moment ago, with its own `UV_TOOL_DIR`, `UV_TOOL_BIN_DIR` and `UV_CACHE_DIR`:

1–3. record the real `~/.claude` manifest, make the fresh HOME, `uv build`, `uv tool install`.
4. **the gate** — `hx install --root … ` with no `--skeleton-only`; on exit 2 with "not
   implemented", print the gate and exit 0 with `SKIPPED (waiting on build-4)`.
5. build a **fake user Claude home**: credentials, a `settings.json` carrying a
   deny-everything `PreToolUse` hook, a `CLAUDE.md` with a banner, and a skill.
6. build a bare **product repo** from `tests/scenario/m8/repo` with a `.claude/settings.json`
   and `.claude/notes.md` committed into it.
7. `hx install --root <scratch>/hx --from-user-config <fake>`.
8. assert only `.credentials.json` was taken — byte-identical, mode 0600 — and that no
   `CLAUDE.md`, no skills, and none of the user's hooks reached `seed/home`.
9. re-hash the fake user home: **byte-identical**, so it was only read.
10. `config/claude.json` has `{bin, version}` with the version **bare** and in the tested list.
11. `hx repo add`; `config/repo.json` written; `repos/<name>.git` is bare with an `upstream`
    remote.
12. `hx launch eng-001` with `HX_CLAUDE_BIN` = the fake `claude` and `HX_TMUX` on a private
    tmux server; the session is live.
13. `wt/eng-001` holds the product, is on `agent/eng-001`, has **no `.claude/`**, and does not
    contain the planted `REPO-CLAUDE-DIR-LEAKED` string anywhere.
14. the agent home carries none of the planted user strings, and its `settings.json` has the
    bypass acceptance, `instructionFiles: claude-md`, a non-empty `claudeMdExcludes`, **no**
    `crossSessionInbound`, `skills/hx-worker` and not `skills/hx-partner`.
15. the units are rendered into this HOME's `Library/LaunchAgents` or `.config/systemd/user`,
    each naming the instance root and the installed `hx`, with **no** surviving
    `{HARNESS_ROOT}` or `{HX_BIN}`.
16. the real `~/.claude` manifest is unchanged.

**The fake user home is the design point.** Asserting "we did not copy the user's settings" by
checking that a file is absent proves very little — the file might have been absent anyway.
Planting a deny-everything hook, a banner `CLAUDE.md` and a skill, and then grepping the seed
home and the agent home for those exact strings, is the difference between *isolated* and *we
did not look*. And pointing `--from-user-config` at a path rather than at the user's own home
is what makes the whole assertion possible without reading real credentials.

Two `set -e` hazards were fixed while writing it: `grep -q X && die "…"` aborts the script when
grep finds nothing, which is the **passing** case. Every one of those is now an explicit `if`.

### Last 15 lines of `packaging/e2e-deploy.sh <scratch>`

```
   ok  4 entries from /Users/loganrobbins/.claude

== 2. fresh HOME and uv directories under the scratch dir
   ok  HOME=…/scratchpad/deploy-final/home

== 3. uv build and uv tool install
   ok  hx_harness-0.1.0-py3-none-any.whl -> …/deploy-final/uv/bin/hx

== 4. is the full hx install built yet?
   gate  hx: install: not implemented (build-4); `hx install --skeleton-only --root <path>`
   creates the instance layout and skeleton (spec 17.2 step 2). The preflight checks, seed
   login, repo mirror, boot units and `hx launch partner` land with their milestones

== SKIPPED (waiting on build-4)
   `hx install` without --skeleton-only is not built yet, so the deploy proof
   cannot run. Everything before this point passed: the wheel builds, installs as
   a uv tool, and its `hx` runs. Re-run this script when build-4 lands.
```

`DEPLOY_EXIT=0`. (The scratch prefix is elided as `…`; the gate line is wrapped for width.
Nothing else is changed.) `tests/packaging/test_e2e_deploy.py` skips with that gate as its
reason — a green test for a proof that did not run would be worse than no test.

## 2. Docs verified against the adapters

I read `src/hx/skeleton/adapters/claude/install.sh` and `start.sh` and checked every claim in
`docs/two-worlds.md` and `docs/deploy.md` about what is written, copied, excluded or launched.

**The scripts were right everywhere. No discrepancy to report to the build lane, and nothing
for them to change.** What was wrong was the docs: they described the behaviour correctly but
vaguely, which is its own kind of wrong — a reader who wanted to *verify* the isolation story
could not, because the docs named no keys.

Each claim checked, and where it is true:

| Claim | Verified in |
|---|---|
| hooks carry `--id <id>` and the absolute `hook_bin` from `config/hx.json` | `install.sh`, `hook()` |
| six hook events for the Partner, nine for a worker | `install.sh`, `if not is_partner` |
| bypass acceptance | `skipDangerousModePermissionPrompt: True` |
| instruction-files mode `claude-md` | `pluginConfigs["agents-md@builtin"]["options"]["instructionFiles"]` — a **plugin** key; the docs named no key at all before |
| `claudeMdExcludes` for the product repo | seven globs under `wt/**` and `repos/**`, covering `CLAUDE.md`, `CLAUDE.local.md`, `AGENTS.md` and the `.claude/` copies of each |
| `crossSessionInbound: accept` is the Partner's alone | `install.sh`, `if is_partner` |
| credentials copied per home, 0600, from `seed/home` | `cp` + `chmod 600` |
| `config/CLAUDE.md` becomes the home's `CLAUDE.md` | `install.sh` |
| one skill per role, copied not symlinked | `cp -R` after `rm -rf` |
| install refuses a home with no seed credentials | `install.sh`, the `seed_credentials` check |
| launch refuses a home with no settings or credentials | `start.sh` refusals |
| launch refuses an `AGENTS.md` with no header line | `start.sh`, `grep -qxF "$HEADER"` |
| launch refuses a worker with no worktree | `start.sh` |
| persona derived immediately before `exec`, above the header only | `start.sh`, the `awk` in `--exec` mode |
| argv exactly spec 17.4, no prompt argument, no `--resume` | `start.sh`, `exec env …` |
| cwd `wt/<id>`, `HARNESS_ROOT` for partner | `start.sh` |
| `DISABLE_AUTOUPDATER=1`, pinned `bin` from `config/claude.json` | `start.sh` |
| the dispatch home wipe is exactly `projects/`, `file-history/`, `history.jsonl` | `dispatch.py` `HOME_WIPE` |

**One thing the docs did not mention and now do:** `start.sh` pipes each pane to
`logs/<id>/<id>-pane.log`. It is instance state like the rest of `logs/`, archived by the next
dispatch. Worth documenting because a reader will find the file and wonder what it is.

**Two claims remain spec rather than implementation**, and `two-worlds.md` now has a "What is
built today" section saying so: the sparse checkout
(`git sparse-checkout set --no-cone '/*' '!/.claude/'`) and `--from-user-config`. Both are
build-4, and both are asserted by `e2e-deploy.sh` the moment it stops skipping. A page whose
whole purpose is to be trusted about isolation should not quietly describe unbuilt behaviour
in the present tense.

`docs/deploy.md`: `--from-user-config` takes a **path**, and the doc now says what it copies
out of it — `.credentials.json` — and what it deliberately does not.

## 3. `tests/scenario/m8b/` — a `decision` nobody scripted

m8 tells its worker to stop and ask, in as many words, because a test pack needs one
deterministic path through that outcome. m8b never mentions a decision anywhere the worker can
see. One order, one worker, no `after` chain, six observation points.

**The ambiguity, exactly.** `orders/eng-001.md` asks for a `--json` mode and then says two
things that cannot both be true:

- `## Order`: "must print a JSON object **and nothing else on stdout**", with a worked pipeline
  example;
- `## Definition of done` criterion 3: "**still prints the human-readable greeting line
  first**, so the existing scripts that grep for `Hello,` keep working".

Neither is marked as the one that wins, and neither is obviously the afterthought — the
pipeline has a worked example, the grep has a named group of existing users. It is not a trick:
it is the most ordinary way an order goes wrong, written twenty minutes apart and never read
side by side.

**The checks are deliberately neutral.** All four pass under both readings. That is what makes
the question unavoidable: if a check resolved the ambiguity, a worker could satisfy the block,
call the prose an inconsistency, and finish — and the run would look like a success while
shipping a public-contract decision nobody made. `test_the_checks_do_not_resolve_the_ambiguity`
asserts they stay neutral, so a later tidy-up cannot quietly defuse the scenario.

**Two ways to pass, one way to fail**, all documented in the README. The worker reaches
`decision`; *or* the Partner re-reads its own draft order, spots the contradiction, and asks
before dispatching — which is better, and the board then goes `02 → 05` with no `decision` at
all. The failure is silent: nobody notices, the neutral checks pass, the item completes `done`.
That is exactly the failure mode `hx complete decision` exists for, and a scenario that only
ever exercises it when instructed never tests whether it would be reached.

The addendum **withdraws criterion 3 explicitly** rather than just answering. The
`## Definition of done` is what the goal evaluator judges, so an addendum that settles the
question without retracting the losing half would leave the worker unable to satisfy its own
item.

`tests/scenario/test_m8b_pack.py` (28 tests) asserts the contradiction is still present in both
halves, that the checks resolve neither side, that nothing the worker can see names
`hx complete decision`, that the persona teaches the general behaviour without knowing about
this particular order, that the addendum withdraws the losing criterion, and that the six
expected boards are what the real `hx board` prints.

**`tests/scenario/packlib.py`** now holds what both packs need — building an instance in a
given observation state, and reading the real `hx board` from it. It was duplicated across the
two modules for about an hour, which was long enough to see it would drift.

## 4. `CHANGELOG.md`

Updated with what exists as of today: the m8b pack, `packlib`, `e2e-deploy.sh` and its gate,
and a **Changed** section for the two docs, since a reader tracking the isolation story needs
to know it was re-verified against the scripts rather than the spec.

## How it was verified

```
$ .venv/bin/python -m pytest tests/guard
5 passed in 1.22s

$ .venv/bin/python -m pytest tests/packaging
77 passed, 1 skipped in 5.21s

$ .venv/bin/python -m pytest tests/scenario
70 passed in 1.64s

$ packaging/e2e-deploy.sh <scratch>
DEPLOY_EXIT=0    # SKIPPED (waiting on build-4)

$ packaging/e2e-install.sh <scratch>
E2E_EXIT=0       # 26 required files, no external URL in the packaged UI
```

The one skip in `tests/packaging` is `test_e2e_deploy.py::test_end_to_end_deploy`, with the
gate text as its reason.

## Live vs. asserted

**Verified for real:** every claim in the table above, by reading the two scripts; the six m8b
expected boards and the eight m8 ones, by running the real `hx board` against instances built
in each state; every order in both packs, by `hx.orders.parse_order`; the deploy script's
preamble — wheel build, tool install, entry points — end to end; the gate and the skip.

**Written but never run:** steps 5–16 of `e2e-deploy.sh`. They are the whole point of the
script and not one of them has executed, because the commands they exercise do not exist yet.
The assertions are written against the contracts in `handoff/gtm-to-build.md`, which is a
weaker thing than running them. Expect the first real run after build-4 to find something —
most likely in the unit-rendering destination or the exact shape of `config/repo.json`.

**Also not run:** m8b as a scenario. Like m8, it is checked data. Whether the ambiguity is
actually noticed is the experiment, and it cannot be unit-tested — a live agent has to meet it.

## Open questions

1. **Does m8b's ambiguity survive contact with a good agent?** It is possible that a capable
   worker resolves it "correctly" by reasoning about intent — the pipeline example is more
   specific than the grep claim — and finishes without asking. That would not be wrong, exactly,
   but it would mean the pack tests less than intended. The first live run tells us, and the
   fix would be to make the two halves more evenly weighted rather than to make the order more
   obviously broken.
2. **`--from-user-config` merging the bypass acceptance.** The contract says the acceptance
   entry is merged into `seed/home/settings.json` while nothing else of the user's settings is
   copied. `e2e-deploy.sh` asserts the user's *hooks* did not come across, but it cannot assert
   the merge happened correctly until there is a settings file to look at. Worth tightening
   once build-4 lands.
3. **The units on Linux have still never been loaded.** No container runtime here, per the
   orchestrator. `e2e-deploy.sh` asserts the rendered text; `systemctl --user daemon-reload`
   accepting it is still M10-on-a-Linux-box or CI.

## Handoff entries

**Read before starting and before finishing.** No new `handoff/*-to-gtm.md` entries arrived
during this goal; the three that exist were all marked `DONE` in gtm-3
(`orchestrator-to-gtm.md`, `build-to-gtm.md`, `ui-to-gtm.md`). The orchestrator's plan change —
`hx install` full, `hx repo add`, sparse worktrees, `hx push`, `hx upgrade` and unit rendering
moving forward to build-4 — is what this goal's gate is written against.

**Written by me:** `handoff/gtm-to-build.md`, gtm-4 entry. The deploy proof exists and is
gated; the two contracts it depends on (`--from-user-config <path>`, and where the rendered
units go on each platform); the sixteen things it will assert, so none of them is a surprise
when build-4 runs it; and the adapter review in full, including the explicit statement that
nothing in their scripts needs to change.

## Other lanes

`tools/milestone-check.sh` exits 0 at the close of this goal. It was red at two points during
it, both times on `tests/core/` — the build lane landing build-2's close and the
`NOT_IMPLEMENTED` renumbering while the run went past; green either side. `tests/guard`, `tests/packaging` and
`tests/scenario` — the three suites this goal names — pass throughout. Nothing in another
lane's path was touched, and every commit was `git add <paths> && git commit -- <the same
paths>`.

## Commits

```
95bb3a7 gtm: docs checked against the adapters, not against the spec they implement
18dc358 gtm: tests/scenario/m8b — a decision nobody scripted, and shared pack machinery
5f0efeb gtm: packaging/e2e-deploy.sh — the M10 deploy proof, gated on build-4
288fa21 gtm: handoff to build — the deploy proof, its two contracts, and the adapter check
```
