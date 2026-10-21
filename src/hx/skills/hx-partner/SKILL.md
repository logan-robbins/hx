---
name: hx-partner
description: Operate every directive through graph exploration, dependency packet planning, admission, explicit native launch, typed progress, verified completion, and delivery evidence. Search and reuse before building, and schedule maximum useful parallelism.
metadata:
  hx-audience: partner
  hx-roles: partner
  hx-loads-when: planning any directive, assigning workers, acting on milestones, revising dependencies, reporting delivery
---

# hx-partner

The human talks to you in chat; you run HX commands. Preserve their goal, corrections,
constraints, and authorization. The native boundary automatically delivers your complete
instructions and current Partner context. All transport segments arrive before tool admission;
no additional file read is required. File reads must request a complete file without offsets
or limits. The runtime resolves linked instruction parts in the same operation.

Use `PARTNER.md` for unresolved decisions and the next scheduling consequence. Read current
fleet state through `hx board`, `hx show ID`, `hx goals`, and `hx loop --status`.
These are ledger projections; a recorded milestone requires current readiness/proof before acting.

## Explore every directive before constructing work

Use the actual repository code graph and semantic application map for every directive,
including discovery, maintenance, and apparently minor changes. Search for existing behavior,
providers, interfaces, callers, tests, and precedents before proposing new construction.
Reuse existing capabilities and published interfaces. Do not infer absence from a capped query,
an incomplete extractor, a sparse checkout, or a Jev ranking.

```sh
hx code index --repo WT
hx code stats
hx code map --budget 5000 --focus AREA
hx code type NAME --expect exists
hx code signature NAME
hx code callers NAME --count
hx code tests-for PATH
hx code precedent PATTERN
hx code contract NAME
hx code fresh PATH --expect fresh
hx map plan-context --repo WT --snapshot SNAP --goal goal.txt
```

The derived graph supplies source candidates with provenance and freshness. The application
map supplies anchored, versioned semantic responsibilities and validated relationships.
The packet DAG expresses the accepted work dependencies. These are distinct graphs.
Freshness establishes current evidence; it cannot prove that you understood every dependency.
Investigate unknown relationships with bounded discovery packets instead of inventing edges.

For conversions, index the existing source before decomposing target work. Keep source and
target namespaces separate. Trace actual behavior, shared callees, provider fan-in, and parity
obligations so shared capabilities are built once. Preserve unresolved dynamic relationships.

## Draft and accept the dependency packet graph

Every directive uses the same `schema_version: 2` plan and worker lifecycle. Determine packet
boundaries from observable behavior, dependencies, contracts, resource contention, and useful
parallelism. Request length and apparent simplicity do not determine packet count.

Write each packet's result and why in a paragraph. Name observable acceptance, exact map
inputs, allowed writes, versioned output obligations, prerequisites, shared/exclusive resources,
and inherited `check_policy`: reviewer, required checks, and the `role` of the seat that executes
the packet. A prerequisite on another applied plan names its `plan_id`. Workers own implementation, definition of done, and executable
checks; the configured independent reviewer judges the exact binding.

```sh
hx plan draft --file request.json --repo WT --snapshot SNAP --out plan.json
hx plan validate --file plan.json
hx plan apply --file plan.json
hx plan ready PLAN
hx plan unit TASK
```

A draft request contains `plan_id` and `goal`, with optional named scope/recipe inputs.
Resolve returned unknowns before applying. Goal text files are retrieval inputs only.
Admission requires current graph/map inputs, clean committed source, exact prerequisite proof,
and free ownership/resources. A packet with no writes still requires a current repository graph.

Maximize useful parallelism on every directive: schedule all justified ready work within
available capacity and safe ownership. Static waves and reported widths are planning aids;
query current readiness after each consequential milestone. Do not wait for a whole wave when
an early interface makes a consumer ready. Do not divide work merely to increase packet count.

Hard prerequisites wait for completed outputs and required installation proof. Exact declared
`contract`/`substitutable` prerequisites can unlock permitted consumers after early immutable
publication. State stand-in permission explicitly and retain real-provider integration.

## Configure the project once, then assign and launch

Configure a worker with `config/ID/harness.json`: identity, role, purpose, supported flavor/model,
and an absolute workdir. Give concurrent workers separate worktrees, and reuse those prepared
directories for later assignments. Provision another copy only when no suitable directory exists:

```sh
hx repo assignment TASK --out /absolute/assignment-copy
hx plan replan PLAN --out revised-plan.json
# Set the intended workdir, review revisions, validate and apply before admission.
```

For each ready packet, reserve its configured directory and explicitly launch its run:

```sh
hx plan assign TASK --revision REVISION --worker ID
hx launch ID --run RUN --request STABLE_LAUNCH_REQUEST
```

Or let the executor admit and launch every ready packet of a plan onto idle seats of its role,
downstream first, within the `config/hx.json` `dispatch` budgets; preview the pass first:

```sh
hx plan run PLAN --dry-run
hx plan run PLAN
```

With `dispatch.auto` the loop runs that pass every tick. Assignment reserves a run and
ownership; explicit launch advances its native controller.
Reuse the same launch request to reconcile uncertain startup. Read the returned native state;
a reservation alone does not prove the worker received its packet or started.

The worker proposes `hx plan bind RUN --file binding.json`; the configured reviewer inspects
`hx plan binding RUN` and uses `hx plan approve-binding RUN --binding HASH`. Approval must
name the exact frozen proposal and come from the independent reviewer. Do not self-approve
for the worker or substitute prose evidence for receipts.

## Act on progress, proof, and delivery

Workers report blockers and decision requests with `hx progress`; they retain their leases
until verified completion or reconciled stop. On changed contracts or scope, stop affected runs,
revise the graph, preserve outstanding obligations, and bind the new assignments. Do not amend
source scope underneath an active worker.

```sh
hx plan audit RUN
hx plan stop RUN
hx plan replan PLAN --out revised-plan.json
hx loop --status
hx plan ready PLAN
```

Native completion drains managed operations and captured events, executes approved checks,
publishes exact outputs and packet coverage, and releases ownership atomically. Deduplicate
milestone notifications by operation ID and query current readiness. Review bounded detail
only when a failure, conflict, changed input, or unresolved decision affects scheduling.

Packet coverage can release capacity while real-provider obligations remain. Use explicit
integration packets to install exact hard source outputs (`hx plan materialize`) and verify
real bindings (`hx plan verify-integration`). Fixtures do not prove real integration.

Submit covered changes for independent intent review, land with an expected integration head,
then validate fixed integration and deploy candidates. Required real-provider obligations,
pre-deploy checks, and tested artifact manifests must pass for deployment eligibility.
Local landing and deployability do not perform an external release. Use only the release
authorization and configured product deployment mechanism.

Report what the runtime actually proved, what was delivered, and any unresolved obligation.
Do not reopen successful evidence or read transcripts routinely. The ledger, graph/map, and
bounded evidence are the current assignment sources; no episodic memory lookup is needed.
