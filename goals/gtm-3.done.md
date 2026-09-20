# gtm-3 done: the M8 scenario pack, skills reconciled with the real CLI, the M7 eval plan

Lane `gtm`, goal 3. `tools/milestone-check.sh` exits 0. `tests/guard` (5), `tests/packaging`
(74) and `tests/scenario` (42) all pass. Everything is committed path-scoped.

## 1. The M8 scenario pack — `tests/scenario/m8/`

The concrete data spec 13's M8 runs on, so the end-to-end test is written against files that
were checked rather than invented at the last minute by whoever reaches M8.

| File | What it is |
|---|---|
| `README.md` | the sequence, command by command, with the spec 06 transition each causes |
| `chat.md` | the four things the human types, and what a correct Partner reply must and must not contain |
| `orders/partner.md` | the Partner's own order; checks are `hx board --require-done eng-001 eng-002` |
| `orders/eng-001.md` | `--upper` on the fixture CLI |
| `orders/eng-002.md` | `--lang fr`, `after: [eng-001]`, and the decision it must stop on |
| `orders/eng-002.addendum.md` | the human's answer, as the Partner should write it |
| `config/eng-00{1,2}/AGENTS.md` | both personas, header and all |
| `expected/01…08-*.txt` | the `hx board` text at eight observation points |
| `repo/` | the product repo: a stdlib CLI, plus two tripwires |

**The two orders are a real dependency, not a decorative one.** `eng-001` adds `--upper`;
`eng-002` adds `--lang fr` and must satisfy
`python3 greet.py --upper --lang fr World | grep -qx 'BONJOUR, WORLD !'`, which it cannot
unless `eng-001`'s work is on the branch. `eng-001`'s order tells it to apply the uppercasing
to the assembled greeting *because* `eng-002` will need to compose with it — so the `after`
edge shows up in the prose of both orders, not just in frontmatter.

**The `decision` is scripted, and the pack says so.** `orders/eng-002.md` tells the worker in
as many words to stop and ask about the unknown-language behaviour, and why: it is a
public-contract choice that is expensive to undo. A test pack needs one deterministic path
through the `decision` outcome, and an order that genuinely withholds a decision is the honest
way to get one. Its `### Checks` deliberately never mention `--lang xx` or the warning text, so
the worker cannot read the answer off its own definition of done —
`test_the_orders_checks_dont_pre_decide_the_open_question` asserts that.

**The addendum grows the order, not the checks.** The human's answer adds a behavioural
requirement and tells the worker to test it; `python3 -m unittest discover -s tests -q` is
already in the `### Checks`, so the worker's own new test is what proves the answer. Checks
stay the Partner's, the order grows, the agent supplies the evidence.

**Two tripwires in the fixture repo**, both of which fail loudly rather than quietly:
`repo/CLAUDE.md` contradicts the work item, forbids `hx complete`, and demands a
`TRIPWIRE-CLAUDE-MD-LOADED` banner on every reply; `repo/.claude/settings.json` registers a
`PreToolUse` hook that denies every tool call, so an agent that loaded it would stop dead on
its first action rather than run with the wrong rules. M8's assertion is that the run completes
and neither string appears anywhere.

### The expected boards are real output, not hand-drawn

`test_m8_pack.py::test_expected_board_is_what_hx_board_actually_prints` builds a scratch
instance in each of the eight states and diffs the **real `hx board`** against the checked-in
file, normalising timestamps to `<ts>`. All eight agree with the build lane's current
`board.py`.

Writing that comparison is what caught a mistake of mine: the fixture wrote `outcome: done`
into an `-idle` work item, and `hx.workitems` correctly refuses it — only a `complete` item
carries an outcome in its frontmatter. A benched item's `done` still shows on the board because
it comes from `tasks.json`, which `hx bench` does not touch. My `expected/08` had been right;
my fixture had not. That is exactly the class of error a hand-written expected file ships
silently.

The other 41 tests are structural and always run: every order parses with
`hx.orders.parse_order` (the function `hx dispatch` validates with), the `after` graph is
acyclic by depth-first search and names only ids the pack ships, both personas carry exactly
one `## UPDATES BELOW ONLY` and name their own id, the fixture repo runs *and does not already
do the work*, the tripwires are intact, and `expected/` covers exactly the steps the README's
sequence table names — no more, no fewer.

## 2. Skills and docs reconciled with the real CLI

`goals/build-2.done.md` does not exist, so by the letter this step was build-1's three commands
only. But `dispatch`, `complete`, `resume`, `bench`, `read`, `show`, `launch`, `restart`,
`wake`, `orders`, `archive` and `heartbeat` all have real surfaces in the shared tree today. I
reconciled against what is actually there, since the point of the step is that the skills match
reality; the orchestrator has since confirmed that was right.

**Real output, from a scratch instance at `/private/tmp/claude-501/demo3/hx`:**

```
$ hx board                                    # fresh skeleton, before `hx launch partner`
(partner: no work item)  -  -  0  -
config/partner/: no work item (spec 08 board invariants)
exit=1

$ hx board --require-done eng-001
(partner: no work item)  -  -  0  -
config/partner/: no work item (spec 08 board invariants)
require-done eng-001: no such id
exit=1

$ hx install --root /private/tmp/claude-501/demo3/x
hx: install: not implemented (build-11); `hx install --skeleton-only --root <path>` creates the
instance layout and skeleton (spec 17.2 step 2). The preflight checks, seed login, repo mirror,
boot units and `hx launch partner` land with their milestones
exit=2

$ hx doctor                                   # on a complete skeleton
ok    python        3.14.7 (…)
ok    tmux          tmux 3.7c
ok    git           git version 2.50.1 (Apple Git-155)
ok    root          /private/tmp/claude-501/demo3/hx
warn  claude        config/claude.json absent; `hx install` records {bin, version} (spec 17.1)
ok    skeleton      … (11 lines, all ok)
ok    models        2 model(s): claude-opus-5, claude-sonnet-5
warn  seed          seed/home/.credentials.json absent; `hx install` runs the seed login (spec 17.2 step 3)
warn  home:partner  run/<id>/home absent; `hx launch` runs adapters/claude/install.sh
warn  repo          config/repo.json absent; `hx repo add <url|path>` mirrors the product repo (spec 17.2 step 4)
exit=0
```

**Discrepancies found and fixed, all on my side:**

| What | Fix |
|---|---|
| `hx orders` and `hx archive` exist; `hx-partner/SKILL.md` did not mention them | added to "Watching", with when to reach for each |
| `hx install` without `--skeleton-only` exits 2 with a message naming the flag | `docs/deploy.md` quotes it verbatim as a pre-release note |
| `hx board` on a fresh skeleton exits 1 with `config/partner/: no work item` | `docs/deploy.md` says that is expected between install steps 2 and 6, with the real output |

**Checked and correct, no change needed:** the `/goal` pointer text in `src/hx/goal.py` matches
spec 06 and the `hx-worker` skill word for word; `HX-COMPLETE` and `HX-CHECK-FAILED` match;
`hx complete` and `hx task` take an optional `--id` that defaults to `HARNESS_ID`, which is
what the skills describe; `hx board --require-done` behaves as spec 08 says; `hx wake` takes
`target text` positionally and neither skill claims otherwise.

**What remains for gtm-4:** a full `dispatch → complete → resume → bench` cycle against a live
instance. `hx dispatch` requires a live tmux session per id, which requires `hx launch`, which
runs `install.sh` and `start.sh` and needs the pinned `claude` binary and seeded credentials —
M6+ territory. Also still `not implemented`: `metrics` (build-8), `ui` (build-10), and
`install`, `push`, `repo`, `upgrade` (build-11). The skills describe them because they ship
with the finished package; the gap is recorded here rather than hidden.

## 3. The ui static files are pinned

`goals/ui-2.done.md` exists, so `packaging/e2e-install.sh` step 4 now requires
`hx/ui/static/{index.html,app.js,style.css}` in the built wheel. 26 required files, `E2E_EXIT=0`.

The ui lane then confirmed in `handoff/ui-to-gtm.md` that those three are the whole list and
that it is exhaustive *by construction* — the static handler refuses any other name — which is
what makes pinning them a decision rather than a guess. They also asked for one more check, and
it was a good one: that the packaged `index.html` and `app.js` contain no `http://` or
`https://`. Spec 16.1 is "no build step, no CDN"; the repo-side test asserts it, but the wheel
is where it would matter, since a page that pulls a script from a CDN works on the machine that
built it and fails on an air-gapped one. Step 4 now scans both files and fails naming the file
and the URL.

I checked the scanner against a deliberately bad wheel before trusting it:

```
$ python3 -c '<the scanner>' bad.whl
hx/ui/static/index.html: https://cdn.example.com/x.js
hx/ui/static/app.js: http://evil.test/api
```

A check that cannot fail proves nothing.

## 4. `docs/companion-eval.md`

The M7 plan in concrete terms, no code. What it fixes:

- **The corpus**: recorded raw streams from M6 runs, because that is the first milestone
  against a live Claude Code and a corpus of fixtures would measure the fixtures. Capture is
  copying an `archive/<id>/<ts>/` directory out of a scratch instance — `hx dispatch` already
  archives logs and state at the start of every dispatch, so this needs no new command and no
  instrumentation in the hot path.
- **What makes an entry worth having**: long enough to seam five times, with at least one dead
  end and one fact that had to be read from a file. A task that never re-reads anything cannot
  distinguish a good Companion from a broken one.
- **Five seam points per task**, spread by progress through the closed steps rather than by
  turn — an early seam has little state to carry and a late one has a lot.
- **The metric** (spec 13 M7, decision D8): `working_set` re-Reads as waste, other tool calls
  as work, and context-file Reads which **must be exactly 1** — zero means the seam failed
  outright, two means the file could not be held.
- **The pass bar**: context-file Reads == 1 at *every* seam, not a mean; re-Reads near zero
  with every non-zero one actually looked at rather than treated as a background rate; no dead
  end repeated; step state inside `state_budget_tokens` on the longest task.
- **How a prompt change is judged**: same corpus, same seam points, before and after, compared
  per seam rather than in aggregate, with `prompt_version` as the label — and a change that
  does not move the metric is not an improvement however much better the prose reads.
- **What it does not measure**: subagent continuity (no hook fires on their compaction), digest
  quality (M8's business), and whether the agent did good work at all — an agent that continues
  smoothly in the wrong direction scores perfectly.

## 5. `docs/github-plan.md`

Already carried `notes/` and `AUTODEV-COMPARISON.md` under "what stays private" and `spec/` as
only the spec — applied at the end of gtm-2 when the orchestrator's answer arrived. This goal
added `tests/scenario/` and `docs/companion-eval.md` to the published layout, and a line on why
the scenario ships: a reader should be able to read what hx claims to do end to end rather than
take the README's word for it.

## How it was verified

```
$ tools/milestone-check.sh
MC_EXIT=0

$ .venv/bin/python -m pytest tests/guard
5 passed in 1.35s

$ .venv/bin/python -m pytest tests/packaging
74 passed in 2.34s

$ .venv/bin/python -m pytest tests/scenario
42 passed in 1.14s

$ packaging/e2e-install.sh <scratch>
   ok  74 entries, all 26 required files present
   ok  the packaged UI references no external URL (spec 16.1: no CDN)
== PASS  wheel built, installed, instance created, no Claude home touched
E2E_EXIT=0
```

## Live vs. asserted

**Verified against real behaviour:** all eight expected boards, by running the real `hx board`
against a built instance; every order, by `hx.orders.parse_order`; the fixture repo, by running
its tests and its CLI; the CLI surfaces above, by running them; the CDN scanner, against a bad
wheel; the wheel, end to end.

**Not verified, and cannot be yet:** the pack has never been *run* as M8. No `hx dispatch` has
touched it, no agent has read one of its orders, and no seam has been taken. It is checked
data, not a passing test of the thing it describes. The same applies to `docs/companion-eval.md`
— it is a plan for a measurement nobody has taken, and the first real corpus may well show the
five-seam spread or the ten-turn window is wrong.

## Open questions

1. **Does the `decision` scripting make M8 weaker?** `orders/eng-002.md` instructs the
   `decision` outcome rather than letting it emerge. I think determinism is right for a test
   pack and have said so in the README, but a reviewer could reasonably want a second, unscripted
   scenario where the Partner has to notice an ambiguity nobody flagged.
2. **`hx metrics` is build-8**, so `docs/companion-eval.md` describes a command that does not
   exist. The plan is written against spec 08's description of it; if the implementation ends
   up recording something different, the doc needs a pass.
3. **The pack assumes one pod.** Nothing in M8 needs two, but every `expected/` line encodes
   `pods/engineers/`, so a future scenario with two pods is a new pack rather than an edit.

## Handoff entries

**Read and applied before finishing:**

- `handoff/orchestrator-to-gtm.md` — answers to all three of this goal's questions, marked
  `DONE 2026-09-20`. All three went the way the pack had assumed, so no expected board moved:
  the goal marker **is** written when delivery defers to `goal-pending` (A1); a benched item
  **does** keep its outcome, and that is intended (A2); and spec 12 is reworded so the Partner
  completes its own item before benching the plan's workers (option 1 of the three I offered) —
  which is the order the pack already used. The README now records them as settled, with the
  reasoning, rather than as assumptions someone might relitigate.
- `handoff/build-to-gtm.md` — the collection-time crash my gtm-2 `packaging/` move caused,
  marked `DONE 2026-09-20`. The path was fixed in gtm-2; both of the build lane's robustness
  suggestions are now in as of this goal. `units()` resolves its directory at call time and
  returns `[]` when it is missing, so a move fails one test instead of interrupting collection
  and taking every lane's tests down with it; and a non-parametrized test asserts both
  directories exist, because a parametrize over an empty list would otherwise pass silently —
  which would have been the same bug wearing a disguise.
- `handoff/ui-to-gtm.md` — the settled static file list, marked `DONE 2026-09-20`. Their three
  files were already the three I had pinned; their extra no-CDN check is now in.

**Written by me:**

- `handoff/gtm-to-build.md` — the pack exists and what M8 should run on it: the file-by-file
  contents, that the eight expected boards are diffed against their real `hx board` so a change
  to the text form fails that test and the expected files are then mine to update, the three
  questions above, and the reconciliation findings.
- `handoff/to-orchestrator.md` — the spec 12 bench-before-complete ordering bug with three ways
  to settle it and a recommendation; the two smaller spec gaps (the goal marker on deferral,
  and a benched item keeping its outcome); and a note that `goals/build-2.done.md` does not
  exist even though M1 has landed, so step 2 was reconciled against the tree rather than the
  marker.
- `handoff/gtm-to-ui.md` — their static files are pinned, what that costs them (a rename breaks
  the check until I update the list), and the maintenance-free alternative if they would rather
  not be coupled to it.

## Other lanes

`tools/milestone-check.sh` exits 0 with every lane's suite green. The build lane's tree was
busy throughout — `dispatch.py`, `complete.py`, `resume.py`, `bench.py`, `goal.py`, `show.py`
and a dozen more arrived during this goal — and nothing in their paths was touched by me.

## Commits

```
319514a gtm: no-CDN check on the packaged UI; the pack's three assumptions are settled
d4c31be gtm: the M7 companion eval plan, and docs reconciled with the real CLI
be622bf gtm: the M8 scenario pack, and a test that keeps it true
7461ba3 gtm: make the packaging tests collection-safe, per build's handoff
c4bdf7f gtm: pin the ui static files in the wheel check
9691ff2 gtm: to orchestrator — spec 12 benches before the Partner completes itself
e664ed9 gtm: handoff to build — the M8 scenario pack, and three things it needs
```
