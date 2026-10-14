# Test kinds, commit size, and sign-off cadence

Meta-rules for how agents work, independent of what they build. hx already
enforces pieces of this (declared checks at completion, bounded inspection,
lane gates in `tools/milestone-check.sh`); this document standardises the
rest so the Partner, workers, and subagents all follow the same discipline
and the harness can measure whether they did.

## Test kinds and when each is required

| Tier | Scope | Required when | Gate |
|---|---|---|---|
| Unit | One function or class | Every new behaviour | Before commit |
| Module contract | One module's public API | New or changed module boundary | Before commit |
| Integration | Modules + store/process | Cross-module or ledger change | Before push |
| Scenario | Fleet/native behaviour | Harness, hook, or adapter change | Before sign-off |
| Live | Real provider/native call | API client or adapter change | Before sign-off, key permitting |

Rules:

- A task is not done when its unit tests pass if its tier table row requires
  more. `hx complete` runs the declared checks; the plan must declare the
  tier-appropriate checks, and the Partner rejects a plan whose checks stop
  below the required tier.
- Full-suite green (`tools/milestone-check.sh` with no lane, the CI form) is
  required before sign-off of anything touching shared infrastructure
  (store, CLI dispatch, hooks, runtime policy). Lane-scoped green is enough
  for a change contained in one lane.
- A skipped live test is reported as skipped, never as passed.

## Search before build

- The first repository action of an assignment is targeted retrieval: the
  plan's map brief, `hx code search`/`symbols` for code questions, and
  `hx content search` for indexed docs. Reading a file to edit it is fine;
  reading to explore is routed through the indexes.
- Full-repository scans (recursive grep/find over the tree, whole-file reads
  of large files) are a rule violation, not a style issue. The runtime
  bounds (160 lines, 80 results) stay; the code index exists so the bound
  rarely bites.
- Context files and packets must carry the targeted facts an assignment
  needs. If a worker has to search for something the packet should have
  contained, that is a packet defect to record, not worker diligence.

## Write small, test early

The failure mode this prevents: hundreds of lines written before the first
test runs, so the first failure implicates everything.

- First test or runnable probe lands with the first function, not the first
  module. As a working bound: no more than roughly one function or ~150
  lines of new code before something executes it.
- Each subsequent slice is red-green at the same granularity: one behaviour,
  one test, run it, then the next.
- Subagents inherit this: a delegated module is delivered as tested slices
  or not delegated whole. Delegating an entire untested module to one
  subagent concentrates exactly the risk this section exists to spread.

## What is committable

A commit is one behaviour unit: the smallest change that is independently
understandable, testable, and revertable. Granularity follows the unit the
project already uses to reason about itself:

| Project type | Commit unit | Tests that must be green |
|---|---|---|
| Library / harness (hx) | One module or one contract | That module's tests + CLI contract |
| Service feature | One endpoint or behaviour | Endpoint + integration tier |
| Frontend | One component or journey step | Component + journey check |
| Data / migration | One schema step | Migration + replay test |
| Docs / config | One topic | Link/render check if any |

- Committable means: the tier-required tests pass, no unrelated changes are
  mixed in, and the message names the behaviour, not the files.
- Size is a smell, not a quota: a change spanning many modules is usually
  several behaviour units planned as one. The Partner splits it; workers
  do not land it whole.
- Feature submaps follow the same boundary: one feature is a map subgraph
  (its behaviours, interfaces, and checks) sized so one assignment can own
  it and one commit can change it.

## Stay current with dev

Workers pull from `dev` (or the repo's principal branch) frequently —
at assignment start, before each new behaviour unit, and before sign-off.
A long-lived branch that drifts from dev converts small reviews into
archaeology and makes the granularity gate measure other people's work.
The gate's base is the merge-base with `dev` first, then `main`: pulling
often keeps that base close, so what sign-off measures is the unit's own
change. Conflicts are resolved in small pulls by the worker, never in one
large merge at the end.

## Push, test, sign-off — more frequently

The loop per behaviour unit is: implement slice → focused tests → commit →
push → CI → receipt. Sign-off consumes receipts (check results bound to
current inputs), not claims. Shortening this loop is the point of the
granularity rules above: small units make frequent sign-off cheap, and
frequent sign-off is what makes small units safe.

## Module size: measured, not esoteric

Every codebase already has a size distribution, so bounds come from the
repo itself via `hx discipline profile`: median module, the p75 soft bound,
and a robust hard bound (median + 3 × the median-to-p75 spread, which tracks
p90 without letting the largest file set the norm). For hx today that is
median 178 lines, soft 311, hard 577, function p90 of 46 lines. A new module
should land near the median and below soft; above hard is a split candidate.
This is normal practice made explicit — the same distribution a reviewer
already feels — not a universal quota imported from outside.

## The discovery-and-orient phase

How hard it is for a model to get started is a goal-quality signal. Before
dispatch, `hx discipline orient` estimates orientation load from the files
the goal names, the candidate files the code index finds for its terms, and
goal size. During work, the observable symptoms of too broad a goal are:
many searches before the first edit, reads across many modules before any
write, and clarifying questions that a brief should have answered. Broad
orientation is the Partner's decomposition failure, not worker slowness:
split the goal or attach a brief naming starting files and symbols.

## Measuring compliance

The harness can observe, per assignment: whether an index/map search
preceded the first edit, lines written before the first test run, which
test tiers ran before completion, scan-bound violations, and commit sizes.
These belong in `hx metrics` as discipline signals beside the existing seam
waste metrics. A repeated violation is a learnings proposal
(`hx learn propose`), not a prompt reminder.

Three of these signals are now commands. `hx discipline pull --repo . --expect fresh` is the
check form of "stay current with dev": it reads local refs (fetch first), reports how many
commits the principal branch has that `HEAD` lacks and which files they touched, and exits 3
while that count is above zero, so a worker's definition of done (every engineer persona's
standard set carries it) or a worker before sign-off can carry the rule as a lint. `hx discipline orient --post-seam ID` reports, after the last seam,
the searches before the first edit, the distinct modules read before the first write and the
`hx task` reprints; the same three numbers sit on every seam of `hx metrics <id>`, beside
`reads_of_context_file` and `reads_of_working_set`. `hx metrics --commands` counts which `hx`
commands each agent actually ran since its dispatch; it is the evidence for adding a command
to a worker's list and for removing one.
