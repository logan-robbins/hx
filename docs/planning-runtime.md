# Dependency packet planning and verified execution

Every directive follows current code/application dependencies and the same accepted packet
lifecycle. Search for existing behavior, providers, interfaces, callers, tests, and precedents
before construction; reuse known capabilities. The Partner chooses decomposition and maximizes
useful parallel work within exact dependencies, source ownership, resources, and host capacity.
Request size does not determine packet count.

## Graph evidence and decomposition

The derived source graph records indexed declarations, reference/caller candidates, source
snapshots, and extraction provenance. The curated application map records anchored semantic
responsibilities and validated relationships. The executable packet DAG declares accepted
work and exact prerequisite/output versions. These graphs serve different purposes.
Freshness establishes current evidence, not complete semantic understanding. Unknown/dynamic
relationships remain discovery obligations. For conversion, index existing source first and
keep source and target namespaces distinct so shared providers are reused.

```sh
hx code index --repo WT
hx code map --budget 5000 --focus AREA
hx code contract NAME
hx code callers NAME --count
hx code tests-for PATH
hx map plan-context --repo WT --snapshot SNAP --goal goal.txt
hx plan draft --file request.json --repo WT --snapshot SNAP --out plan.json
```

The assignment brief returns responsibilities, anchors, contracts, consumers, owners, recipe
suggestions, and discovery gaps. It retrieves bounded exact/FTS selections, not an exhaustive
filesystem search. Resolve its unknowns and investigate omitted relationships as needed.
Graph candidates and Jev rankings do not validate semantic edges or prove absence.

## Current plan contract

Top-level fields are exactly:

```text
plan_id, expected_revision, repository, goal, constraints, tasks
```

A plan contains 1–256 observable behavior units, a matching repository UUID, up to 32 complete
constraints, and at most 1 MiB. Each unit contains exactly:

```text
id, expected_revision, behavior, acceptance, workdir, write_paths, map_inputs,
outputs, prerequisites, resources, check_policy
```

- `acceptance`: 1–32 observable obligations. The Partner defines what must hold, not scripts.
- `workdir`: an absolute Git worktree with matching portable-map repository identity.
- `write_paths`: up to 64 normalized repository-relative files/reserved prefixes. Symlink
  escapes and conflicting case aliases cannot obtain separate ownership.
- `map_inputs`: exact `{repository,snapshot,id,version}` references, revalidated at boundaries.
- `outputs`: 1–32 `{id,version,kind,obligation}` records. Source/contract outputs include exact
  `paths`; receipt outputs are bound to check coverage rather than an initial check recipe.
- `prerequisites`: exact `{task_id,output_id,version,need}` records. `need` is `hard`, `contract`,
  or `substitutable`. Early inputs add `provider_output`, `provider_version`, and boolean
  `stand_in`; substitution requires explicit true permission and retains real-provider proof.
- `resources`: up to 32 `{id,mode}` entries, `shared` or `exclusive`.
- `check_policy`: independent configured `reviewer`, 1–16 unique `required_checks` IDs, and the
  `role` of the seat that executes the unit (a configured worker role, never `partner`).

An assignment body is at most 16 KiB. Validation rejects cycles, mismatched versions,
missing/unknown contract fields, invalid source scope, and unordered overlapping write scopes.
It cannot prove the semantic quality of decomposition or acceptance checks.

```sh
hx plan validate --file plan.json
hx plan apply --file plan.json
hx plan unit TASK
hx plan ready PLAN
```

Validation reports dependency levels, possible parallel groups, widths, interface/size
warnings, and structural bounds. These aid judgment; actual readiness is checked independently.
Maximize justified concurrency without waiting for an entire static wave. Early immutable
interfaces can admit a consumer while provider implementation continues. Resource contention
and worktree/write ownership can serialize otherwise independent work.

## Source preparation and admission

Materialize sparse source before admission, then accept its workdir in the plan. Provisioning
never moves ownership or silently changes a packet's workdir:

```sh
hx repo assignment TASK --out /absolute/assignment-copy
hx repo expand --namespace REPO --at COMMIT --out /absolute/assignment-copy --paths PATH
hx plan replan PLAN --out revised-plan.json
# Review the correct workdir and revisions, validate, then apply.
hx plan assign TASK --revision REVISION --worker ID
hx launch ID --run RUN --request REQUEST
```

Admission requires current graph/map inputs, clean committed source, current prerequisite
proof, and free write/resource leases. Read-only/discovery packets with no writes still
require a current repository graph; an empty write set cannot bypass orientation evidence.
A sparse omission does not prove a capability absent. Unknown closures remain explicit.

Assignment reserves an authoritative run and leases. Explicit launch advances native startup
with one stable request identity. Reservation alone does not mean a worker received its packet.
Read returned controller state and reconcile uncertainty using that same request.

## Worker binding and proof

Before edits/proof, the worker proposes exact definition of done, check recipes, coverage
for every acceptance/output/deferred-integration obligation plus required policy checks, and
the receipts of its searches. The runtime receipts every search a run makes (native Grep/Glob
through the hooks, `hx code` queries from the worker's shell); the cited receipts must together
cover the packet's search areas, the directories of its write paths and map anchors, where a
prior implementation would live. `hx task` lists the areas and receipts:

```sh
hx plan bind RUN --file binding.json
hx plan binding RUN
hx plan approve-binding RUN --binding HASH
hx check CHECK --run RUN
hx plan audit RUN
```

Only the independently configured reviewer can approve that exact frozen binding. Source must
remain unchanged through approval. Revised recipes require stopped/revised work. Check receipt
reuse requires complete matching source, recipe, environment, toolchain, fixture, platform,
and external identities; unknown closures use broad source fingerprints rather than guessed
completeness. Native shell tools are not a filesystem sandbox.

Workers report current actions, blockers, findings, and evidence through `hx progress`.
A blocker does not release ownership. Audit pauses unexpected writes and retains leases;
restoring a file does not silently resume a paused run. The Partner stops/replans affected
assignments. Leases survive restart and do not expire on time alone.

`hx complete done` requests managed completion; `hx plan finish RUN --file receipts.json`
performs the direct verified boundary with exact receipts. Completion requires approved/current
passing checks, managed work/capture drain, clean source, unchanged prerequisites, and explicit
outputs covering all committed changed paths. Output proof names exact commit/tree/mode/blob
identities including verified deletions. Publication, coverage, release, and notification are
atomic; exact retries return existing completion. Revised completed units need newer outputs.

## Interfaces, integration, and delivery

`hx plan publish-contract` publishes immutable interface files with scoped passing proof.
`hx plan acquire-contract` exports only a declared exact input with its stand-in permission.
A contract can unlock permitted consumers early; local coverage does not discharge the real
provider obligation. An explicit completed integration unit installs and verifies exact hard
source inputs using `hx plan materialize`. `hx plan verify-integration` requires current
consumer/provider source and frozen integration check identity.

Covered packets release worker capacity while outstanding integration remains visible.
Independent intent review, short expected-head landing, fixed integration CI, and fixed
pre-deploy checks follow. Deployment eligibility names a tested immutable artifact and retains
all required real bindings. The configured product release system performs external deployment.
See [agent-scale runtime](agent-scale-runtime.md) and [contracts](../CONTRACTS.md).
