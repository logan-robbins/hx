---
name: hx-fleet
description: Configure worker identities and native harnesses, reserve admitted packet runs, explicitly launch and reconcile them, and manage owned process drain/shutdown.
metadata:
  hx-audience: partner
  hx-roles: partner
  hx-loads-when: creating a worker, configuring a role or harness, launching a run, reconciling owned native processes
---

# hx-fleet

Worker configuration is `config/ID/harness.json`. Copy `templates/worker/harness.json`, replace
identity/pod/workdir/purpose placeholders, choose a supported flavor/model/effort, and select a
role with both `personas/ROLE.md` and `companion/roles/ROLE.md`. Shipped roles are
`backend-engineer`, `frontend-engineer`, `release-engineer`, and `qa-engineer`.
Use an existing absolute Git worktree matching the packet repository. Concurrent workers need
separate worktrees; write/resource leases still prevent conflicts across branches and plans.
Configure the independently named binding/review worker as required by policy.

The role persona supplies domain judgment; `purpose` supplies this identity's scope and intent.
Shared worker subagent instructions live in `templates/worker/SUBAGENTS.md`. An `explorer`
configuration selects a supported model/effort for bounded read-only orientation. Install and
authentication use private configured native homes; do not alter personal harness settings.
Use `hx persona ID`, `hx doctor`, and `hx show ID` to inspect configured/recorded state.

Every directive uses graph exploration, search/reuse, and dependency planning. Maximize useful
parallelism from dependencies and resource constraints, without any request-size shortcut.
See `hx-partner` for plan design and `hx-worker` for approved bindings and execution.

```sh
hx plan validate --file plan.json
hx plan apply --file plan.json
hx plan ready PLAN
hx plan assign TASK --revision REVISION --worker ID
hx launch ID --run RUN --request REQUEST
```

Materialize sparse source and accept its workdir before admission. Assignment reserves run
ownership; explicit launch advances the native controller. Worker launch always names that
run and a stable request identity. Repeat the same request to reconcile uncertain transport;
read returned controller state rather than assuming a pane means successful packet delivery.
The Partner itself starts with `hx launch partner` and does not own a worker assignment.

```sh
hx launch ID --run RUN --request REQUEST --drain
hx launch ID --run RUN --request REQUEST --shutdown
hx plan stop RUN
hx up
hx heartbeat
```

Drain closes new request/tool admission. Shutdown reconciles owned native process termination.
Stop retains ownership when process identity or quiescence is uncertain; only reconciliation
or verified completion releases leases. Recovery advances durable intents and existing requests,
not unlaunched reservations. Do not paste into panes, kill an unverified foreign process, or
create a second native request to bypass uncertainty.

`hx board`, `hx goals`, `hx show ID`, and `hx loop --status` read ledger state. Identity details
may name task, run, worker, or plan. Milestones are at-least-once notifications: deduplicate
operation IDs and query current readiness. A recorded view does not establish current proof.
