# Repository, packet, and delivery runtime

The Partner uses the application/code graph to find work that can overlap. It frames
one paragraph naming the result, responsibility, dependencies, and any permitted stand-in.
The harness worker owns implementation, definition of done, and exact execution checks.
Review, landing, integration, and release validation have separate authorities and proof.

This flow applies to general development, maintenance, new features, and integrations.
For codebase conversions, graph the existing codebase first, before planning conversion
packets. Index supported source exports and import external analyzer facts when needed.
Keep the existing source graph and the target application graph in separate repository
namespaces. The Partner's emphasis is then understanding the source application's behavior,
shared callees, dependencies, contract boundaries and target counterparts. Establish which
capabilities should be built once and which consumers can proceed against published
interfaces. Source candidates and unresolved dynamic calls remain explicit; the harness
workers establish implementation/parity evidence within their assigned packets.

Every directive follows current graph exploration and search/reuse before construction.
Choose packet boundaries from behavior/dependencies and maximize useful parallel work within
ownership, source safety, resources, and capacity. Request size does not select packet count.
The only assignment contract is the dependency packet with approved worker bindings.
Configure source workdirs at project setup; provision an additional directory only when needed and accept it before admission. `hx plan assign` reserves
a run; then explicitly `hx launch ID --run RUN --request REQUEST` advances native startup.
A reservation alone does not prove packet delivery or execution.

## Immutable code and shared repository queries

```sh
hx code index --repo /absolute/product
hx code signature IOrderRepository --at COMMIT
hx code callers OrderRepository --at COMMIT --count
hx code journal --namespace REPOSITORY
hx code journal --namespace REPOSITORY --replay CHANGESET
```

Snapshots bind repository, commit, working-copy overlay, and extractor generation.
Source bodies, extraction results, and graph objects are compressed immutable objects;
persistent path maps share unchanged bindings. Named queries select indexed postings and
check snapshot membership before applying result limits. An explicit historical query can
capture Git objects lazily without checking out the revision. Source/metadata bounds fail
explicitly rather than reporting a partial graph as complete. Dirty source receives an
overlay identity; it cannot overwrite the immutable clean revision.

Exact-path refresh inspects changed paths. Committed updates use parent deltas, including
modes and deletions. Prepared changesets and replay cursors survive interruption. Full
enumeration is bootstrap/reconciliation work. Syntax facts are candidates; resolved facts
retain build context. Software AG Natural supports exported object declarations, parameter
interfaces, and static call targets; dynamic calls remain unresolved reference sites.

One optional host service shares those facts across instances:

```sh
hx repo serve --socket /tmp/hx-repository.sock
hx repo register --repo /absolute/product
hx repo file --namespace REPOSITORY --at COMMIT --path src/Orders.cs
hx repo diff --namespace REPOSITORY --base BASE --at COMMIT
hx repo history --namespace REPOSITORY --at COMMIT --path src/Orders.cs
hx repo materialize --namespace REPOSITORY --at COMMIT --paths src/Orders.cs --out /tmp/orders-copy --working-copy
hx repo assignment TASK --at COMMIT --out /tmp/assignment-copy
hx repo expand --namespace REPOSITORY --at COMMIT --out /tmp/assignment-copy --paths src/Dependency.cs
```

Configure `repository_service` in `config/hx.json` as the absolute socket path, or set
`HX_REPO_SERVICE_SOCKET`. Run the service with its own harness root outside product
checkouts; register repositories with that service. Code commands and freshness gates
then use the configured service. `--owners` joins the requesting instance's live leases,
so shared derived facts do not become another ownership authority. Requests, responses,
complete source responses, and concurrent workers have explicit transport/resource limits. An unavailable configured service
returns an explicit error.

Metadata queries materialize no client checkout. `materialize` requires a fresh directory
outside the registered source and exact regular paths. `--working-copy` shares local Git
objects and creates a clean sparse copy at the named commit. Choose paths from assignment
scope and known build/test dependencies; incomplete dependency knowledge is not permission
to claim a complete sparse build. Source is never written into an existing task checkout.

`assignment` derives the sparse read set from the current frozen task's source scope,
map anchors, hard output files and bound check inputs/dependencies. It reports unknown
closures and early contract acquisition obligations instead of certifying a build. Future
output paths remain anticipated; existing dependencies are materialized. Replan the task
to use its new checkout before admission. Provision additional files before the tool that
needs them; never use a sparse omission as evidence that a capability is absent.
`expand` adds exact regular paths to an existing clean sparse copy at the named source;
it retains prior paths and refuses dirty or different-source copies.

## Worker bindings and early interfaces

Dependency packet plans declare `check_policy` and `resources`; workers bind executable checks.
Behavior/acceptance and output obligations remain fixed. Policy names an independent
reviewer and required check IDs. Worker bindings supply exact recipes and a coverage map
for every `acceptance.N`, `output.ID`, and deferred `integration.PRODUCER/CONTRACT`.

```sh
hx plan bind RUN --file binding.json
hx plan binding RUN
hx plan approve-binding RUN --binding HASH
hx plan publish-contract RUN --output API --file receipts.json
hx plan acquire-contract CONSUMER --producer PROVIDER --output API --out /tmp/published-api
hx plan verify-integration CONSUMER --integration-run RUN --producer PROVIDER --output API
```

Binding and approval require the unchanged admission source. Binding recipes freeze before
edits; the implementing worker cannot approve its own binding. Native known mutating tools
and proof paths reject an unapproved binding. This is deterministic lifecycle enforcement,
not a filesystem sandbox for arbitrary shell programs.

A hard prerequisite requires completed exact output and installation proof as appropriate.
A `contract` or `substitutable` prerequisite names the immutable contract output/version,
real `provider_output`/`provider_version`, and explicit `stand_in` permission. Contracts
publish while their provider run remains active. Exported bytes retain source modes/blob
identities and passing contract evidence. Consumers acquire only their declared contracts.

Packet completion proves local obligations, publishes outputs, and releases source/resource
leases. Deferred real-provider checks do not hold that worker until the provider finishes.
They remain pending and block deployment until a completed integration run installs exact
consumer/provider versions and runs the consumer's frozen integration recipes. Shared and
exclusive resource constraints model fixture/service contention independently of paths.

Typed contract/coverage/integration/deployment milestones are durable. The root loop sends
bounded, at-least-once operation notifications; use operation identity to deduplicate wakes
and query current readiness. Notification text is a scheduling signal, never proof itself.

## Scoped check artifacts and .NET builds

Check recipes accept optional `source: {complete, inputs, dependencies}`. A complete declared
closure scopes the source identity; omitted/incomplete closures retain repository-wide
fingerprinting. Graph candidates alone cannot establish completeness. All recipe inputs,
tools, environment, fixtures, platform and external version files still participate.
Matching executions can be reused across runs with a new run-bound receipt. Identities are
relative to the checkout plus its repository namespace and commit, so an equal check already
running in another worktree or root on the box is joined instead of run again; the joiner gets
its own receipt naming the original execution. Cache corruption invalidates reuse. A process
claim survives interruption until reconciliation proves its owned process group is gone;
missing process identity stays unresolved.

```sh
hx build graph src/App/App.csproj --cwd /absolute/product --configuration Release
hx build graph All.sln --cwd /absolute/product --configuration Release --platform 'Any CPU'
hx build build src/App/App.csproj --cwd /absolute/product --configuration Release --run RUN --request compile-1
hx build test BUILD --run RUN --request unit-shard-1 --filter 'Category=Unit'
hx build status JOB
hx build reconcile JOB
hx build materialize BUILD /tmp/private-build-output
```

MSBuild evaluation supplies project references, imports, compiler properties, target
frameworks, SDK and package inputs. Complete supported closures qualify for immutable
restore/build reuse. Custom targets, generators or unsupported solution configuration
can require an explicit complete source contract; incomplete results never certify reuse.
Ordinary SDK `.sln`/`.slnx` entrypoints use the installed MSBuild ProjectGraph API
to obtain actual per-project configuration/platform/reference contexts, then execute
the original solution. Its reader bootstraps offline through the same managed broker
and is cached as an immutable artifact. Matching solution builds are reusable; unknown
custom solution behavior stays explicitly incomplete.

The broker joins an equal build or test shard already running in any root or worktree on the
box, adopting a successful build into the joiner's own checkout place; it bounds concurrency
across host processes and isolates writable intermediate/output directories. Test shards
materialize the immutable build in private writable state and run without rebuilding it.
Receipts retain the build identity and test recipe. Managed jobs must drain before assignment
completion.

Runner-specific arguments remain distinct: the VSTest `--filter` selects its shard;
MTP uses its native runner options through `--runner-arg=VALUE`. A fresh build needs a
new request identity and joins a live equal build rather than starting a second one.

## Review, landing, and fixed validation

```sh
hx change submit --file change.json
hx change review CHANGE --revision N --decision approve --reason 'Intent and boundary verified'
hx change land CHANGE --repo /absolute/integration.git --expected-head COMMIT
hx change candidate --repo /absolute/integration.git --expected-head COMMIT --boundary integration
hx change run-validation BATCH --snapshot /tmp/fixed-validation
hx change status --change CHANGE
hx handoff delivery /tmp/handoff --change CHANGE --batch BATCH
hx handoff check /tmp/handoff --live
```

A change submission contains exactly `id`, `run_id`, `base`, `head`, and `behavior`.
Behavior must preserve the packet's assigned intent and the accepted source scope.
Review decisions name its revision and intent hash. Actual public interfaces, migrations,
and sensitive paths require the configured consequential-change approval. The author cannot
self-approve. Configured worker roles come from their harness configuration.

`config/delivery.json` configures reviewer identities, sensitive paths/reviewers,
`post_merge_checks`, `pre_deploy_checks`, exact `check_definitions`, `build_checks`,
`artifact_paths`, and optional shared/exclusive `validation_resources`. Fixed archive
checks require complete declared source closures. Pre-deploy policy names both build
checks and artifact paths; release checks must fingerprint the tested artifacts.

Landing prepares a candidate outside the publication transaction and uses a brief
expected-head ref update in a bare local integration repository. A moved head refuses
publication; preparation can be repeated against the new head. Interrupted publication
is reconciled against the actual ref before retry. Local landing does not push remotely.

Post-merge validation freezes source, covered intents, policy and recipes and runs on a
private fixed archive. It may coalesce landed changes. Main can advance while the candidate
finishes. Failed candidates retain diagnostic receipts and create scoped repair work;
reverts are inverse changes applied to the current head with expected-head publication,
preserving later unrelated changes. Pre-deploy validation requires verified integration
for that exact source, resolved real-provider obligations, and an immutable tested build
manifest. It proves the named candidate/artifact, not whatever happens to be newest.

Verified checkpoints let later candidates include only pending intents while preserving
the earlier proof chain. `candidate --historical` can freeze a named authoritative ancestor
to drain a backlog. Sparse validation archives materialize declared complete check and
environment inputs, and report that coverage separately from the full source commit/tree.
`change reconcile BATCH` requires controller/check process quiescence before releasing an
interrupted attempt. `candidate … --retry BATCH` then creates a distinct attempt; it never
rewrites the failed or unresolved evidence. Multi-change attribution names a bounded set
of implicated intents and does not claim a uniquely identified culprit without proof.

Delivery handoffs capture packet coverage, review, landing, candidate source/tree, check
identities, proof, tested build, and outstanding obligations separately. The capture is a
ledger artifact and the directory is sealed. A historical capture remains evidence for its
recorded boundaries; `--live` requires current identities/readiness to match before acting.
Pending, failed, skipped, and unverified work cannot be relabelled as deployment approval.
