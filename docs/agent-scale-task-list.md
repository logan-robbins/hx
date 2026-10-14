# Agent-scale implementation task list

Implement the agreed repository, dependency, review, and delivery designs on main.
The Partner owns application/dependency understanding and parallel scheduling;
workers own their work packet, implementation, definition of done, and evidence.
This is implementation tracking, not an A/B evaluation or research deliverable.

## Ordered upgrades

- [x] Confirm merged graph work, current main, and redundant branch/worktree cleanup.
- [x] 1. Immutable repository snapshots, reusable source objects/extractions, explicit revision queries, concurrent branch isolation.
- [x] 2. Durable change journal, path-only refresh, dependency invalidation, restart replay, short publication transactions.
- [x] 3. Shared host repository service behind the CLI, bounded metadata/content APIs, shared caches, bounded upstream work.
- [x] 4. Complete scoped check identities, cross-run execution-artifact reuse, new run-bound receipts, unknown-closure fallback.
- [x] 5. Evaluated .NET project/build dependencies, immutable restore/build artifacts, deduplicated bounded build jobs, test shards without repeated compilation.
- [x] 6. Partner behavior/dependency packets, harness-bound execution checks, hard/substitutable dependencies and shared resource constraints.
- [x] 7. Early immutable contract publication and typed coverage/integration/deployment obligations; milestone-driven scheduling.
- [x] 8. First-class intent/change/review records, semantic approval policies, independent configured approval authority, authoritative landing status.
- [x] 9. Optimistic short landing publication, post-merge batch validation, regression attribution/repair/revert, immutable pre-deploy candidates.
- [x] 10. Sparse assignment materialization and checkout-free metadata jobs; isolated writable build/test state.
- [x] 11. Exact scoped handoffs, continuously finishing CI candidates, lifecycle telemetry and recovery/concurrency simulations.
- [x] 12. Update system-design, README, CONTRACTS, packaged personas/skills, CLI exposure and CI; run focused, full parallel, and installed-package verification.

## Completion audit

Each upgrade requires a reachable product path, tests of behavior and failure/replay
boundaries, and current documentation. Add implementation and validation evidence
below as stages complete. Test subsets prove their named scope only. No pending
review, test, binding, or release obligation is reported as passing. Full completion
requires all ordered upgrades and their integrated verification.

## Evidence

- Implemented on `main`, starting at `deb261e`; the graph PR was already merged and
  redundant branch/worktree cleanup was verified. Only the primary checkout is active.
- Final `tools/milestone-check.sh`: **5 guard tests passed; 2461 full-suite tests passed,
  27 skipped**, exit 0. The configured parallel runner remained in use. The 417 source/test
  inputs matched their start-of-run hashes through completion. Skips name optional
  extractors/native providers, opt-in live model calls, and explicit test exclusions.
- Graph coverage includes actual 13-client cold barriers, immutable revision/overlay
  isolation, cached extraction, restart/replay, named lookup membership and bounded work.
  Final graph audit selection: 121 passed, 3 optional extractor skips. Natural CALLNAT
  regressions prove full refresh rebinds affected dependents from cached extraction outside
  writer transactions. Partial/stale indexes and zero-count aggregates cannot prove absence.
- .NET broker/check/CLI selection: 168 passed, 1 skipped. All three installed-SDK
  project/solution fixtures passed, including `.sln`/`.slnx` repeated reuse and Release-to-Debug
  project mapping. VSTest/MTP shards use exact immutable outputs. Preparation ownership,
  cancellation/replay, host slot limits and conservative process recovery are covered.
- Packet/delivery/recovery selections cover immutable worker bindings and independent
  review, early publication/acquisition, retained real-provider obligations, optimistic
  landing, fixed candidates, sparse validation closures, tested artifact manifests,
  checkpoint frontiers, failure attribution, repair/revert and interrupted controllers.
  Parent integration selections cover scoped handoffs, native completion, notifications,
  store migration, sparse preflight/expansion and exact pinned deletions.
- Fresh wheel installation and seeded fake-worker deployment both passed. A clean-source
  wheel contains 227 shipped files with exact current-source byte equality and no stale
  build-directory files. Installed CLI/module/schema-20/Natural/shared-service smoke passed.
  The installed CLI indexed hx itself: 470 files, 4184 symbols, one ContinuityStore signature
  and 53 caller files with current graph evidence. These are correctness checks, not A/B
  performance comparisons.
- Full verification caught obsolete CLI/Partner assertions, a package download timeout and
  a test's pre-exec Darwin identity capture. Assertions and child-readiness fixtures were
  corrected; a real exec regression preserves strict old-token rejection. The final full
  command succeeded after those corrections.
- System design, README, CONTRACTS, CLI exposure, CI, personas and shipped skills now describe
  general development with source-first graph exploration specifically required for
  conversions. The Partner owns dependency understanding and scheduling; workers own the
  how, definition of done and checks. Contract availability/local coverage retain parity,
  real-provider integration and tested release obligations. No research deliverable or A/B
  evaluation was added.
