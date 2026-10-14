# hx — Game of Harnesses

hx runs a coordinated fleet of native coding-agent harnesses. A Partner turns a goal
into bounded assignments; workers execute in their own sessions; a companion maintains
the small current state needed to continue; and hx checks, publishes, and reports
completed work.

Use hx for new features, application maintenance, integrations, or codebase conversions.
For a conversion, graph the existing codebase first. The Partner uses that source graph
to understand behavior and dependencies before dispatching target work; source and target
graphs remain separately addressable so shared capabilities can be built once and reused.

hx is designed to make each assignment start with the right context, use only the
tools it needs, avoid repeated repository searches, and finish with machine-checked
outputs. The current implementation includes application mapping, task planning,
required Jev calls for bounded observation and routing judgments, token controls,
incremental capture, context reset, tool-output reduction, and managed completion.

**→ [Getting started](docs/getting-started.md)** sets up a Partner. [Operating hx](docs/operating.md)
covers day-to-day use. [System design](docs/system-design.md) describes the runtime as
implemented and its remaining integration work.

Inside Claude Code, the plugin provides setup and Partner commands:

```text
/plugin marketplace add logan-robbins/hx
/plugin install hx
/hx:setup
```

For a local checkout, add its absolute path as the marketplace source and install
`hx@hx-marketplace`. Plugin installation and hx instance creation are separate; each
instance uses isolated Claude homes.

## How the runtime works

```mermaid
flowchart TB
    U[User goal and corrections] --> P[Partner]
    M[Versioned semantic application map] --> P
    P --> A[Dependency packets<br/>behavior · scope · contracts · outputs]
    A --> C[Current task context]
    C --> W[Native worker harness]
    W --> B[Worker DoD and checks<br/>independent approval]
    W --> T[Tools · repository · child agents]
    T --> E[Incremental event capture]
    E --> J[Jev: bounded delta and routing judgments]
    J --> K[Companion: task and map patches]
    K --> S[Validated current task state]
    K --> M
    S --> C
    W --> X[Early contracts · scoped proof · packet coverage]
    X --> P
    X --> D[Intent review · short landing]
    D --> I[Fixed integration candidate]
    I --> R[Validated release artifact]
```

The application map records semantic responsibilities, behaviors, interfaces, source
anchors, and checks. Stable IDs survive stack changes when responsibilities remain the
same. Planning uses a bounded slice of that map to define assignments and avoid asking
workers to rediscover known paths and contracts. The Partner focuses on dependency
understanding and parallel scheduling; the harness worker owns its definition of done,
implementation, and exact checks. An independent configured reviewer approves the
binding before edits. Published interfaces can unlock consumers using permitted
stand-ins, with real-provider verification retained as a deployment obligation.

A shared derived code graph provides bounded symbol, signature, caller, test, parity,
and precedent queries across repository namespaces. It supports Python, C#,
TypeScript/JavaScript, Go, Rust, and Software AG Natural without building the repository, with optional
external extractors and imported graphs. `hx code fresh PATH --expect fresh` and the
planning gate enforce current scope; `--owners` reads the existing ledger leases.
See [search and retrieval](docs/search-and-retrieval.md) for commands, provenance,
external formats, and measured coverage.

Immutable source snapshots support explicit `hx code … --at COMMIT` queries and share
content and extraction objects across checkouts. An optional host service behind the
CLI shares those caches across instances and supplies checkout-free metadata queries.
Scoped check execution artifacts can be reused across runs when complete input and
environment identities match. `hx build` adds evaluated .NET dependencies, deduplicated
builds, immutable artifacts, and test shards that avoid repeated compilation.

The companion is a Claude Code session of its own with Read and Write — two tools and nothing else;
it processes new captured events and submits structured changes to current
task state and the application map. Jev is required for its bounded judgments about
observation deltas, repeated tool observations, optional tool discovery, and surplus
output. Jev does not choose which facts survive, write facts, generate task plans, or
switch models. The companion handles factual changes; deterministic code validates
scope, versions, evidence references, and source applicability.

Workers receive a bounded packet for the active assignment: goal and corrections,
acceptance, next action, relevant findings and commands, required map records, and
unresolved events. Obsolete state can be compressed or deleted. Workers do not carry
personal episodic memory across independent assignments. Planned context boundaries
use incremental capture and hooks; the runtime restores the current packet after a
controlled reset. Token thresholds, native autocompaction settings, and model output
caps provide the enforcement layers.

Completion waits for managed work and capture to drain, runs the declared checks,
publishes the exact accepted outputs, and releases assignment ownership. A worker's
claim alone does not complete a task.

Intent review, landing, integration, and deployment eligibility are separate states.
Local landing publishes with an expected-head check; broad validation runs against
fixed post-merge and pre-deploy candidates while main advances. Deployment evidence
names the tested artifact. See [agent-scale runtime](docs/agent-scale-runtime.md) for
configuration, commands, and the boundaries of reusable proof.

## Requirements and current limits

hx requires Python 3.14, `tmux`, `git`, and a supported Claude installation for the
Partner. Other native harnesses can be assigned as workers when configured. TypeSafe
API access is required for Jev; the runtime has no inference fallback for Jev decisions.
See [getting started](docs/getting-started.md) for credentials and installation.

The merged runtime is covered by Linux and macOS CI, focused integration tests, and
installed adapter checks. A live native engineering-to-QA fleet run has not yet been
completed. Coordinated migration of remaining legacy state consumers and retention for
cancelled assignments that may be resumed remain open. Provider-specific tool schema
reduction is available only where the native public interface supports it; advisory
adapters do not claim native schema savings. See [current implementation status](docs/continuity-build-status.md)
for verified behavior and boundaries.

## Documentation

- [Getting started](docs/getting-started.md) — setup and first Partner
- [Operating hx](docs/operating.md) — daily use
- [Several instances on one host](docs/multi-instance.md) — a fleet of Partners: private tmux servers, credentials, go-gate, clean product repository, bound evidence, concurrent preparation and short landing
- [System design](docs/system-design.md) — current architecture and remaining integration
- [Search and retrieval](docs/search-and-retrieval.md) — code/graph/content search, isolated execution, Jev sifting
- [Provider model registry](docs/models.md) — latest model IDs per provider, pinned defaults
- [Application map](docs/application-map.md) — semantic records, validation, and updates
- [Continuity runtime](docs/continuity-runtime.md) — capture, state, checks, and reset contracts
- [Planning runtime](docs/planning-runtime.md) — assignments, ownership, and completion
- [Agent-scale runtime](docs/agent-scale-runtime.md) — immutable snapshots, shared computation, dependency packets, review, and fixed CI candidates
- [Current implementation status](docs/continuity-build-status.md) — merged scope and verification
- [Native interface verification](docs/native-interface-verification.md) — adapter support boundaries
- [docs/learnings.md](docs/learnings.md) — the central LEARNINGS file: propose, other-session review, numbered append; the signal index every agent carries
- [CONTRACTS.md](CONTRACTS.md) — shared machine-readable shapes
- [CHANGELOG.md](CHANGELOG.md)

## License

MIT. See [LICENSE](LICENSE).
