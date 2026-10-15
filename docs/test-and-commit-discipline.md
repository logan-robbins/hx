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
  tier-appropriate obligations. The harness worker binds exact
  checks and coverage before editing; an independent configured reviewer approves
  adequacy. The Partner schedules dependencies and does not author test procedures.
- Full-suite green (`tools/milestone-check.sh` with no lane, the CI form) is
  required at the configured integration/release boundary for anything touching shared infrastructure
  (store, CLI dispatch, hooks, runtime policy). Lane-scoped green is enough
  for local packet coverage of a change contained in one lane. Broad required checks
  remain pending until that boundary passes; local coverage can release worker capacity.
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

## Preserve admitted source identity

Prepare current source and resolve integration changes before admission. After admission,
retain the exact branch/worktree and source boundary used by the approved worker binding.
Do not pull or rebase newer source underneath active work. Changed dependencies require a
stopped/revised assignment and current proof. Commit bounded changes within frozen ownership.

Fixed post-merge/pre-deploy validation retains its named candidate while main advances.
Complete scoped input identities preserve unrelated proof; unknown closures use the broad
repository fingerprint. The Partner schedules revised work when inputs change.

## Push, test, sign-off — more frequently

The loop per behaviour unit is: implement slice → focused tests → commit →
packet coverage → reviewed landing → fixed integration/pre-deploy CI → receipt.
When remote PR publication is explicitly requested, push and its required forge checks
also apply at their configured boundary. Sign-off consumes receipts (check results bound to
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

## Dependency exploration and current evidence

Every directive requires current graph/dependency exploration and search/reuse before
construction. `hx discipline orient --goal FILE` can estimate candidate orientation load;
it does not choose packet count or prove semantic understanding. Boundaries follow actual
behavior, dependencies, shared contracts, ownership/resources, and useful parallelism.
Resolve missing starting facts through targeted graph/map retrieval or discovery packets.

## Inspecting recorded proof

`hx metrics [IDENTITY]` aggregates ledger packet/run, lease, receipt, checkpoint, milestone,
and delivery state. `hx show ID --json` reads current task/run/worker/plan detail; immutable
checkpoint/event references retain their exact scope. These views do not manufacture live
readiness or prove that a search established a semantic relationship.

Use `hx plan ready` to revalidate assignment prerequisites and `hx change status` for current
delivery proof. Evidence of source identity, approved check binding, receipts, source/output
coverage, and managed drain supports the named boundary only. Pending or skipped broader
checks remain explicit. A repeated supported mistake can become a reviewed learning rule;
`hx learn propose` is not a replacement for repairing the implementation or proof.

Partner observation can inspect its own context reset activity. Worker continuations use
immutable ledger checkpoints and events, with no per-seam file-stream metric authority.
