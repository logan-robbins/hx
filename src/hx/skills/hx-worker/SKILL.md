---
name: hx-worker
description: Execute an admitted dependency packet using current graph evidence, reuse before construction, approved worker check bindings, typed progress, exact source outputs, and managed verified completion.
metadata:
  hx-audience: worker
  hx-roles: "*"
  hx-loads-when: starting an admitted run, restoring packet context, binding checks, editing source, reporting progress, completing proof
---

# hx-worker

You are a native harness worker in an admitted run. Your current packet carries the parent
goal, constraints, behavior, acceptance, allowed source scope, exact prerequisite/output
versions, map inputs, check policy, findings, next actions, and unresolved events. Read the
supplied boundary context once and continue from it. `hx task` reads your current assignment.
The ledger and immutable artifacts retain current facts across context resets.

## Search and bind before building

For every assignment, explore actual code/application dependencies and search for existing
behavior, providers, interfaces, declarations, tests, and precedents before construction.
Reuse known capabilities. A bounded graph hit is candidate evidence with provenance; a
ranking does not prove dependency truth or absence. Read exact relevant source as necessary.
A sparse omission is not evidence that a capability is missing.

```sh
hx code map --budget 5000 --focus AREA
hx code type NAME --expect exists
hx code signature NAME
hx code callers NAME --count
hx code tests-for PATH --expect exists
hx code conform NAME
hx code precedent PATTERN
hx code contract NAME
hx code fresh PATH --expect fresh
hx content search QUERY
```

Use a bounded read-only explorer when useful. It returns anchored source findings,
known dependencies, and suggested checks; you own the implementation and definition of done.
Missing knowledge or an incomplete packet is a progress blocker requiring discovery or replan.

Before editing or executing proof commands, submit your exact binding:

```sh
hx plan bind RUN --file binding.json
hx plan binding RUN
```

```json
{"definition_of_done":["Observable behavior holds."],
 "checks":{"unit":{"id":"unit","argv":["python3","-m","pytest","tests/test_feature.py"],"cwd":".","inputs":[],"environment":{"complete":true,"executables":[],"inputs":[],"external_versions":{}},"timeout_s":120,"max_output_bytes":100000}},
 "coverage":{"acceptance.0":["unit"],"output.proof":["unit"]}}
```

Cover every actual acceptance, output, and deferred integration obligation exactly, including
inherited required checks. The example's coverage keys must be replaced by your packet's
obligations. The configured independent reviewer approves the exact frozen hash using
`hx plan approve-binding RUN --binding HASH`. You cannot approve yourself. A changed binding
requires stopped/revised work. Keep the admitted source unchanged until approval.

## Work and progress

Keep source changes inside frozen write scope and retain the admitted branch/worktree identity.
Do not pull newer source underneath a binding. Request changed inputs/scope through the Partner,
who stops/replans affected assignments. Record concrete next actions, findings, blockers,
deliverables, and evidence through typed progress:

```sh
hx progress --run RUN
hx progress --file progress.json
hx plan audit RUN
```

Progress JSON has `task_id`, `run_id`, `expected_revision`, `phase`, `step_updates`, `next`,
`blocker`, `deliverables`, and `evidence_ids`. Step updates carry `step_id`, `status`, and optional
`summary`/`last`; statuses are `planned`, `active`, `blocked`, `done`, or `cancelled`.
Deliverables have relative `path`, `op` (`upsert`/`drop`), and an upsert `description`.
Use the revision returned by the last progress operation. Claims do not become check receipts.
Blockers retain ownership until completion or reconciled stop; there is no blocked completion.

Exploit maximum useful parallelism in independent exploration, implementation, builds, and
checks within scope, capacity, and resource limits. Subagents inherit your run and cannot
expand ownership. Give them disjoint scopes and exact deliverables. Wait for managed work to
drain before completion. Commit bounded changes with meaningful tests and exact source evidence.
Do not choose packet count or runtime behavior from apparent request size.

For declared interfaces, publish immutable files and passing contract evidence early:

```sh
hx plan publish-contract RUN --output API --file receipts.json
hx plan acquire-contract TASK --producer PRODUCER --output API --out /absolute/interfaces
```

Use only your declared exact input and permitted stand-in. Provider implementation and
real-provider integration remain obligations. Hard source inputs require completed output
and explicit installation proof; the integration assignment can use `hx plan materialize`.
`hx plan verify-integration` discharges a real binding only with exact completed integration proof.

## Checks and completion

```sh
hx check CHECK --run RUN
hx tool-exec --request REQUEST -- COMMAND ARGUMENTS
hx tool-exec --request REQUEST --report junit-xml -- python3 -m pytest -q --junit-xml={report}
hx evidence EVENT --offset 0 --limit 8000
hx complete done
```

A test run's `summary` comes only from a declared report (`junit-xml`, `trx`, `go-json`,
`cargo-json`, `jest-json`); `{report}` names the file hx reads outside your checkout.

`hx complete done` requests managed completion: drain captured/managed work, execute approved
checks, verify current source/environment/dependencies, publish exact declared outputs,
record packet coverage, and release ownership atomically. Keep evidence tied to exact source,
commands, environment, fixtures, and artifacts. A selected subset cannot prove broader coverage.
Fix a failed gate and retry with current proof. Source changes invalidate relevant old receipts.
`hx plan finish RUN --file receipts.json` is the direct verified completion boundary when
explicit run-bound receipts are available. Only your own run is writable by worker commands.

For .NET, `hx build graph` evaluates real MSBuild dependencies; `hx build build` shares an
immutable matching build; `hx build test BUILD` creates private writable test shards without
recompiling. Use exact configuration/framework/platform/source identities. Reconcile uncertain
managed jobs through the Partner; unknown process outcomes retain ownership.

Packet coverage is separate from independent intent review, landing, fixed integration CI,
real-provider verification, deploy validation, and external release. Report only your proven
boundary. The Partner schedules downstream work and owns plan revisions.

## Context and tools

Use bounded graph/content/evidence retrieval. Program large-data filtering in `hx exec` and
retrieve exact snippets; `hx sift` ranks bounded candidates without proving their truth.
`hx tools discover`, `load`, `call`, and `status` expose configured optional capabilities.
The native harness supplies read/search/edit/write/shell tools; arbitrary shell commands are
not a filesystem sandbox. Respect packet ownership even when a tool could reach farther.

The Companion processes captured deltas and proposes validated current task/map changes.
It does not execute your commands. Context reset restores the current packet and retained
source facts, not personal episodic memories. Propose map findings and reusable learning rules
through runtime commands; do not edit HX state, logs, or generated context projections.
