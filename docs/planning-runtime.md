# Focused plans and verified unit execution

The functional boundary is a complete behavior unit: relevant source context,
implementation and fixtures, acceptance checks, and explicit outputs. The Partner
chooses the decomposition. Deterministic gates enforce ownership, dependency order,
and output evidence. These commands operate on the continuity ledger; native
dispatch/resume and automatic tool-boundary auditing are not connected yet.

## Assignment brief

```sh
hx map plan-context --repo /absolute/worktree --snapshot SNAPSHOT --goal goal.md
hx map plan-context --repo /absolute/worktree --snapshot SNAPSHOT --goal goal.md --require contract-id
```

The brief returns current responsibilities, anchors, contracts, check recipes,
active write owners, and explicit discovery gaps. Lookup uses exact IDs/paths and
SQLite FTS over current semantic records. It selects up to three seeds, their
adjacent relationships, and consumers of selected interfaces. It does not recursively
explore consumers' implementations or search the filesystem. Missing/stale facts
never become invented dependency links.

Optional expansion stops at 24 considered records, 32 inspected edges, or 1.5
seconds. Each relation/direction group contributes at most four candidates; the
groups are interleaved so a common relation does not consume every slot. The
default response budget is 16,000 UTF-8 bytes (`--max-bytes` permits 2,000–64,000).
Up to 16 required IDs bypass ranking; missing or oversized mandatory context fails
explicitly. Coverage reports available, inspected, and omitted relationships.
These are bounded deterministic retrieval foundations; Jev ranking, full beam
traversal, persisted selections, and model token accounting remain separate work.

Use selected IDs/versions as `map_inputs` in the plan. The plan revalidates them
before persistence, assignment, and completion. Exact commands, conditions, and
contracts retain their original text.

## Plan contract

`hx plan validate --file plan.json` validates without persisting.
`hx plan apply --file plan.json` atomically records a validated revision.
`hx plan unit TASK` retrieves the current unit assignment.

The top-level JSON object has exactly these fields:

| Field | Contract |
|---|---|
| `schema_version` | `1` |
| `plan_id` | Stable plan ID |
| `expected_revision` | `0` for creation; current revision for an amendment |
| `repository` | UUID from `.hx/map/manifest.json` |
| `goal` | Parent goal, a nonempty statement |
| `constraints` | Up to 32 complete statements |
| `tasks` | 1–32 behavior units |

Each unit has exactly `id`, `expected_revision`, `behavior`, `acceptance`, `workdir`,
`write_paths`, `map_inputs`, `checks`, `outputs`, and `prerequisites`.

- `acceptance` has 1–32 statements describing observable results.
- `workdir` is an absolute Git worktree root with the matching repository UUID.
- `write_paths` names up to 64 repository-relative files or reserved prefixes.
  Symlink resolution must stay inside the worktree. Scopes are frozen and
  conservatively case-folded, which can serialize some distinct Linux paths.
- `map_inputs` contains exact `{repository,snapshot,id,version}` references.
- `checks` maps 1–16 check IDs to exact [check recipes](continuity-runtime.md#check-receipts).
  A recipe key must equal its `id`.
- `outputs` contains 1–32 versioned outputs. A source output is
  `{id,version,kind:"source",paths:[...]}`; paths name exact files, not directories.
  A receipt output is `{id,version,kind:"receipt",check_id}`.
- `prerequisites` contains up to 64 `{task_id,output_id,version}` references to
  outputs declared by other units in the same plan.

The plan is at most 256 KiB, and an individual assignment body at most 16,000 bytes.
These are structural bounds, not a complete native prompt/token budget. Validation
rejects cycles, missing checks/outputs, mismatched versions, invalid source scope,
and unordered units with equal or ancestor/descendant write paths. Ordered units
may reuse a scope after the prior assignment stops. Static dependency levels and
possible parallel groups are planning aids; actual readiness is checked separately.
The validator does not claim to prove the semantic quality of a behavior or test.

## Readiness, ownership, and completion

```sh
hx plan ready PLAN
hx plan assign TASK --revision REVISION --worker eng-001
hx plan audit RUN
hx check CHECK --run RUN
hx plan finish RUN --file receipts.json
hx plan stop RUN
```

`receipts.json` maps every assigned check ID to one receipt ID. Assignment returns
the exact unit packet and a run ID. It starts a ledger assignment, not a native
model process. `ready` gives a bounded plan snapshot with a reason for each blocked
unit; `assign` rechecks admission in the same transaction that acquires leases.

Admission requires current map inputs, a clean committed worktree, proven
prerequisites, and free write ownership. A worktree can have only one active
assignment. Leases use repository UUID plus canonical relative path/prefix, so
overlaps conflict across branches, worktrees, and plans. Independent units can run
in separate worktrees. This does not automatically launch every ready unit or
override future controller RAM/concurrency budgets.

Leases survive restart and have no time-based expiry. Completion or explicit stop
releases them. Native controller integration must stop the actual worker before
using the stop transition. A revision cannot replace an active assignment or a
contract used by an active downstream assignment.

Audit compares Git-visible changes against the frozen scope and pauses unexpected
writes while retaining leases. A paused run requires stop/reconciliation; deleting
the offending file does not silently resume it. Git-visible auditing does not
detect arbitrary ignored writes or changes made and reverted within one tool call.
Automatic post-tool audits and registered source watcher integration remain pending.

Completion requires a clean committed worktree, current passing receipts for all
assigned checks, unchanged prerequisite proofs, and explicit source outputs covering
all committed changed paths. It rechecks source/environment identities and declared
ignored inputs at the final boundary. Source proofs contain the commit/tree and
exact per-path Git mode/object identity, including verified deletions. Receipt
outputs retain their content-addressed evidence artifact. Completion, output
publication, lease release, and the dependent-work notification commit atomically.
An exact completion retry returns its existing result. Revised completed units must
publish greater output versions and update dependent contracts.

Partner/operator commands own planning, assignment, and stop. Workers may audit,
finish, or materialize only their own run. Run acceptance checks and completion in
the same environment: changing environment values invalidates reusable receipts.

## Installing prerequisites

A producer's completed source output does not mean the consumer has its files.
An explicit integration unit installs and commits those exact files in the consumer
worktree, then records a materialization receipt:

```sh
hx plan materialize CONSUMER --integration-run RUN --producer PRODUCER --output OUTPUT
```

The command checks installed object identities; it does not merge branches or
resolve conflicts. It requires a separate active integration assignment in the
consumer worktree and an explicit consumer prerequisite on that integration unit's
acceptance output. The consumer remains blocked until integration completes
successfully at the attested commit, its checks remain valid, and its own current
tree contains the exact prerequisite file versions. Failed/stopped integration does
not unlock consumers. Receipt artifacts are already present in the shared ledger
and need no file installation.

A useful graph is: producer emits `source` plus `proof`; integration depends on the
producer's `proof`, installs `source`, verifies it, and emits its own proof; consumer
depends on the producer's `source` and the integration proof. Its own implementation,
fixtures, and tests remain together in that consumer unit. After admission, the
consumer may intentionally change its input files within its owned scope; the
entry snapshot and acceptance checks preserve what it actually started from.

The serial tests cover this entire ledger/CLI path, stale and failed proof, failed
integration, worker ownership, task version changes, worktree isolation, scoped
writes, restart, and two concurrent prefix-reservation writers. Actual native
adapter launches and automatic controller dispatch are not certified by these tests.
