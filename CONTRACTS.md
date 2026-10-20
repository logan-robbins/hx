# HX contracts

HX is one unreleased product. Current schemas define authoritative state; obsolete schemas
are refused and replaced rather than migrated or retained as parallel workflows.

## Directive and planning authority

Every directive requires graph/dependency exploration and search for existing behavior,
interfaces, providers, tests, and precedents before building. Reuse existing capabilities.
The Partner chooses observable packet boundaries from actual dependencies and schedules
maximum useful parallelism within safe source scopes, ownership/resources, and capacity.
Request size does not select packet count. Freshness proves current evidence, not semantic
understanding. Derived source edges remain candidates until anchored semantic validation.
For conversion, index the existing source first and retain separate source/target namespaces.

All public worker assignment and native launch use an accepted schema-2 packet plan. Its
full schema, worker binding, early interfaces, and source/deployment proof are below.
The SQLite ledger owns task/run revisions, progress, events, records, check bindings,
receipts, ownership, packet coverage, integration, and delivery. Files are bounded context
projections or immutable evidence; they are not an independent task state machine.

## Admission and native execution

Source materialization and acceptance of the correct workdir precede admission. A sparse
checkout does not prove absence or complete source closure. `hx plan assign TASK --revision R
--worker ID` rechecks current graph/map evidence, clean committed source, exact prerequisite
proof, available leases/resources, and worktree ownership. Empty write scope requires a
current repository graph. Assignment reserves a run; it does not imply native startup.

`hx launch ID --run RUN --request REQUEST` explicitly advances the admitted worker controller.
The stable request names one logical startup; repeat it to reconcile uncertain transport.
The controller verifies installed prompts, source/context identity, capabilities, native
acknowledgment, and process ownership. `hx launch partner` starts the unassigned Partner.
`--drain` and `--shutdown` advance owned-process closure for a worker's original run/request.
Unknown process or remote outcomes retain their obligations rather than inventing success.

Workers bind their definition of done and exact check recipes before edits/proof. A configured
independent reviewer approves the frozen hash. Known mutating/proof tools enforce that gate;
arbitrary shell commands are not a filesystem sandbox. Worker commands act only on their run.

## Progress, context, and completion

`hx progress --file FILE` accepts exactly `task_id`, `run_id`, `expected_revision`, `phase`,
`step_updates`, `next`, `blocker`, `deliverables`, and `evidence_ids`. Step records contain
`step_id`, `status`, optional `summary`/`last`; status is `planned|active|blocked|done|cancelled`.
Deliverables contain relative `path`, `op=upsert|drop`, and upsert `description`.
Updates are bounded to 16 KiB and optimistic revisions. Claims never become execution receipts.

`hx task` reads the assigned packet. Native context is compiled from configured common,
audience, role, policy, and identity sources; current run records/evidence supply variable
context. Reset checkpoints freeze a source/input/event frontier and restore the current
packet. The Companion submits bounded, evidence-linked task/map changes; Jev judges bounded
observation/routing candidates without proving absence, dependency truth, or completion.
No personal or cross-task episodic state is retrieved into worker assignments.

Prompt manifests retain exact instruction source identities and `required_files` backing
material. Complete shared, audience, role, reviewed global/role learning, policy and identity
instructions are delivered automatically in the actual native input channel. Startup and
managed continuation hash their exact full submission and require native acknowledgment;
an oversized hook string that a host may truncate and model-selected file pagination are
not instruction transport. The Partner's synchronous hook segments obey the complete
boundary contract below.

Backing material parts are at most 40,000 UTF-8 bytes (10,000 estimated tokens using four
bytes per token, an explicit heuristic). Each part names `path`, `sha256`, `bytes`,
`payload_sha256`, `payload_bytes`, `part`, `parts`, `next_path`, `bundle_sha256`, and
`bundle_bytes`, plus its typed source/channel metadata. The physical NEXT/END markers and all
part/aggregate identities are verified before automatic full reconstruction. No mandatory
instruction, assignment obligation or selected required fact is sliced to satisfy a budget.
Complete native input that cannot fit its configured context/transport budget is refused.
When reviewed instruction sources change, the next managed boundary closes admission and
requests a drained native restart with the current compiled bundle. The run/checkpoint
continues only after acknowledgment of that fresh complete input. Active frozen prefixes
are never amended in place.

Explicit native source-file reads prohibit offsets/limits and resolve the whole verified
file or instruction chain in one logical call. Host-limited transports emit deterministic
segments without model pagination. Only all required complete segments settle a successful
read; incomplete delivery retains an action/completion barrier and permits full retry.
`hx file-read PATH` provides this same complete transport through one standalone native
shell call when the harness has no dedicated file-read tool. Managed calls return only the
broker marker on stdout, and the logical read settles only after post-hook complete delivery;
pipelines and lossy command wrappers are refused. Operator/protocol `hx repo file` returns
full exact UTF-8 source with `bytes`, `sha256`, `total_lines`, and `complete: true`; managed
native calls use the marker and complete broker transport. A partial range request or
unsupported complete response is refused.
Operator `hx evidence EVENT` also returns complete verified source/instruction evidence;
managed native shell callers must use the complete-file broker for source. Offset and
limit options apply only to non-file observations. Companion MCP source responses declare
the host's complete-result envelope and refuse anything exceeding their frozen context or
transport allowance before returning content. `hx exec` and `tool-exec` refuse
recognized source-dump commands before execution instead of reducing their output.
Search candidates and reduced non-file command output retain their separate bounded contracts.
Complete transport is evidence of emitted content, not proof of model attention or obedience.

`hx complete done` requests managed completion under the caller's `HX_CONTINUITY_RUN`.
It does not publish proof from agent prose. The runtime drains observed managed work and
capture, runs approved bound checks, verifies current source/environment/dependencies and
explicit changed output coverage, then atomically publishes outputs/coverage and releases
leases. `hx plan finish RUN --file RECEIPTS` is the direct boundary with exact run-bound
receipts. Blockers and decisions use typed progress; the Partner stops/replans affected work.
Lease release after stop requires reconciled owned-process quiescence.

## Partner boundary transport

The Partner's current persona, reviewed rules and orchestration context are supplied
completely at its native context boundary. Where a host caps one hook string, deterministic
synchronous transport segments preserve the exact complete boundary without model reads.

Its file-backed observation pass is requested with:

```text
Companion pass: read <path> and do what it says.
```

This observes Partner scheduling state; it does not assign, launch, or complete a worker.
Worker observations use the frozen ledger pass and bounded evidence/structured-patch tools.
Source and instruction evidence is a complete verified object or an explicit refusal;
the bounded evidence contract applies to non-file observations.
The Partner messaging endpoint record `run/partner/socket.json` has exactly `socket`, `token`,
`ts`, and `session_id`; token contents are never user-facing output.

## Read-only fleet and UI projections

`hx board --json` and `GET /api/board` return schema 2:

```text
schema_version, root_abs, ts, ledger_revision, plans, tasks, runs, leases,
progress, checkpoints, checks, milestones, delivery, truncated, proof_note
```

A task row carries `task_id`, `revision`, `parent_id`, `payload`, `created_at`, `status`,
`active_run_id`, `worker_id`, `completion`, and `binding`. Runs include recorded ledger
identity/phase plus `launch_id`, `native_status`, `native_error`, and the frozen `assignment` at that run's task revision. Progress includes task revision, run end state, steps, and deliverables. Delivery contains
`changes` and fixed `candidates`. Overview lists are bounded; truncation is explicit.

`hx show ID --json` and `GET /api/show/ID` select a task, run, worker, or plan directly and
add `identity`. `hx goals --json` and `GET /api/goals` project schema/root/time/revision,
plans, tasks, and truncation. Metrics aggregate packet, run, ownership, proof, and delivery
records. Views open the ledger read-only; they neither create nor mutate task state.
Recorded milestones and UI state are not live proof: `hx plan ready` and `hx change status`
revalidate current prerequisites and delivery evidence before action.

`POST /api/partner/wake` sends an explicitly requested human message to the Partner. It
creates no assignment or task transition. `hx wake partner TEXT` uses the same wake transport.

## Configuration and instructions

`config/ID/harness.json` names identity, pod, role, purpose, absolute workdir, supported flavor,
model/effort, independent `companion` and `learning` settings, and optional explorer model/effort. Roles have matching
`personas/ROLE.md` and `companion/roles/ROLE.md`. Workers use isolated native homes and
configured credentials; instance setup does not authorize changing personal harness state.
`config/hx.json` carries runtime, repository service, and optional capability settings.
`config/planning.json` has `schema_version: 1`, independent `reviewer`, and `required_checks`;
this policy document version is separate from the schema-2 packet plan.

Packaged skills are selected by the audience/role manifest. Worker subagents retain the
parent packet/run and scope. Learnings are proposed with `hx learn propose` and reviewed by
a different authorized session; learning text cannot replace current source or proof.

## `hx code`, `hx content`, `hx exec`, `hx sift` (search and retrieval)

All four print JSON to stdout (docs/search-and-retrieval.md). `hx code` query
success exits 0; a failed `--expect exists|absent|one|clean|fresh` exits 3; invalid
arguments/usage exit 2. Limits are 1–100 rows (20 by default); `map` also enforces
its JSON byte budget. `clean` certifies a complete, current conform result with no
diffs or uncertainty. `fresh` requires anchored, current results; use `code fresh
PATH...` for the complete write scope instead of inferring scope coverage from a
capped symbol result. `--namespace` selects a repository; omission searches shared
namespaces for named queries. `fresh` requires one unambiguous namespace.

`hx code index --repo PATH [--paths PATH...]` returns
`{repo, files, symbols, changed, skipped, head, resolved}`. The repository identity
is the portable map's `repo_id`, then a git remote, then the resolved path.
The store is `state/code-index/index.db` outside product checkouts. Bodies occur
once in `files`; Porter and optional trigram FTS use external content. Text files above
the maintained-file budget remain fully indexed and appear in the mandatory size audit.
The separate parser resource bound refuses indexing before publishing partial coverage.
Binary bodies are excluded and counted in `skipped`; streamed fingerprints remain in
`exclusions` for scope freshness. No file-count cap applies.
A derived index without current namespaces is rebuilt from source. Extractor-generation changes
invalidate old candidates before the next refresh.

Natural UTF-8 source exports use `lane=syntax`, `extractor=stdlib-natural`.
Extension matching and symbol lookup are case insensitive. `.nat` is a generic
Natural object; `.nsp/.nsn/.nss/.ns7/.nsc/.nsh/.nsg/.nsl/.nsa/.nsm/.nsd`
provide exported object kinds. Functions and subroutines retain their declared
names even when the filename differs. Routine signatures include adjacent return
and `DEFINE DATA` declarations; external `.nss` routines include preceding data
declarations. Listing numbers and comments are excluded from signatures while
anchor hashes retain the original physical source lines. Fields expose
`parameter|variable|view` kinds with level-qualified names. Maps and DDMs expose
object anchors without interpreting their editor layouts. `type` includes
`data_area|ddm`; callable objects use `symbols` or `signature`.
Literal `CALLNAT`/`FETCH`, `PERFORM`, and symbolic function calls produce candidate
call sites. Dynamic operands and `DEFINE PROTOTYPE VARIABLE` function calls carry
`kind=dynamic_calls` and do not produce static call edges. `INCLUDE`, data-area
`USING`, `VIEW OF`, and literal `USING MAP` dependencies produce import candidates.
No library/steplib, dynamic assignment, database schema, or parameter parity is
certified. See [Natural source support](docs/search-and-retrieval.md#natural-source-support).

`symbols`, `type`, `signature`, `tests-for` return declaration rows:
`{repo, name, qualified, kind, path, line, end_line, signature, lane, extractor,
anchor_sha, attributes, stale, unanchored}`. `lane` is
`resolved|syntax|text|imported`; declaration signatures come from source headers
or an external parser's supplied signature. `callers` returns reference sites with
`reference: resolved|syntactic|imported`; `--count` returns
`{count, fan_in: "files", stale}`. `register` returns registration references.
`search` retains `{path,title,snippet,score,layer}` with provenance added; `layer`
is `symbol|fts|fuzzy`. `precedent` preserves source whitespace in its bounded
snippet. Optional sifting adds `jev_score` and `sift_status`; all deterministic
hits remain, including unsifted hits beyond Jev's candidate cap.

`impact` returns defined symbols, referencing file paths and candidate test paths.
`edges [PATH]` returns `{repo, from_path, to_path, kind, status: "candidate", lane,
extractor, anchor_sha, stale, ...}` for imports, calls, implements, tests and
custom imported kinds. These never validate map relations. `conform` returns
`{interface, interfaces, implementations, tests, diffs, uncertain, truncated,
stale, clean}`; overloads, generic constraints and default values cannot certify
clean parity. `projects [NAME]` returns manifests and project dependencies;
`map --budget BYTES [--focus PATH...]` returns ranked files with scope headers,
definitions and fan-in. `stats` reports namespaces, heads, coverage, tools,
tokenizers and database bytes. `submaps` returns modularity, directory baseline
and candidate components with bounded member/boundary lists, `read_versions`
and `put` operations; the caller supplies its current run/snapshot patch envelope.

`hx code import --namespace NS [--repo PATH] [--extractor TOOL] --symbols FILE
--edges FILE` accepts JSONL symbols
`{name,qualified,kind,path,line,end_line,signature}` (optional `attributes`) and
edges `{from,to,kind,evidence}`. `from` names a symbol or relative source path;
`to` names a symbol or path. Imports replace the same namespace/tool batch
atomically; free-form kinds and evidence survive. Missing source spans remain
`unanchored: true` and cannot satisfy freshness.

`hx code contracts --register FILE` accepts a JSON object or array of
`{name,path,version,producer,repo,sha}` records; `repo` must already be indexed,
`sha` is the published Git commit. `contract NAME` returns matching published
records across namespaces. `--owners` adds `{worker,task,run}` entries from the
continuity ledger's current leases; the graph has no ownership columns.

Resolved indexing is explicit: `index --resolved --indexer-checkout PATH` requires
a separate clean checkout at the same committed HEAD. The SCIP CLI and applicable
indexer must be installed; failures report degraded syntax coverage. Optional
`HX_SCIP_COMMANDS` overrides each detected indexer's argv; no tool is vendored.
The disposable indexer's `index.scip` is removed after ingestion.

Completion results include `changed_paths`; publication replay uses the original
assignment revision when retrying its derived graph refresh. Planning/admission
bootstraps missing caches and rebinds a cache from another checkout; stale scope
in the indexed checkout refuses admission with `hx code index --repo PATH`.
The installed `bin/code-index-post-merge`, run with `HARNESS_ROOT`, performs the
post-merge rebuild; `HX_CODE_INDEXER_CHECKOUT` opts into the resolved lane.

`hx content index` returns `{"label", "chunks", "code_chunks"}`; `content search` returns a
map of query to `{"title", "content", "source", "content_type", "rank", "layer", "stale"}`
lists. `hx exec` returns `{"exit_code", "stdout_returned", "bytes_produced", "indexed", ...}`;
output over 100 KB returns an index pointer with no raw text. `hx sift candidates` returns
`{"status", "sifted", "results": [{"id", "snippet", "jev_score", "candidate"}]}`; on provider
failure `status` is `unsifted` and every `jev_score` is null. Sift and triage output is
advisory: it never validates edges, receipts, or completion.


## Immutable graph snapshots and changesets

Graph snapshots bind repository, commit, working-copy overlay and extractor generation.
`hx code … --at COMMIT` selects immutable source at that revision. Historical Git capture
does not check out files. Dirty overlays never replace clean-commit facts. Content-addressed
compressed source/extraction/graph objects and persistent path bindings share unchanged
data. Named lookup limits apply after snapshot membership, not to unrelated historical rows.

`hx code journal [--namespace REPOSITORY] [--replay CHANGESET]` lists or replays durable
prepared changesets. They retain parent/new identity, exact changed paths, modes, blobs,
and deletions. Exact-path refresh does not enumerate the whole checkout. Extraction occurs
outside the publication transaction; applied/superseded state is durable. Explicit historical
capture is bounded by `HX_CODE_REVISION_MAX_BYTES` and `HX_CODE_REVISION_MAX_PATHS`.

## Host repository protocol (`hx repo`)

Configure `config/hx.json` `repository_service` or `HX_REPO_SERVICE_SOCKET` as a Unix socket
path. `hx repo serve [--socket PATH] [--workers N]` owns one shared derived store outside
product checkouts. Workers are bounded to 1–64; requests to 64 KiB and responses to 256 KiB.
Metadata limits are 1–100 rows. Source-file reads return complete regular Git blobs with exact
UTF-8 bytes and content identity; range parameters are refused. A complete response exceeding
the explicit transport limit fails instead of returning a prefix. Register with `hx repo register --repo PATH`.

The JSON protocol uses `method: register|status|code|scope|diff|merge-base|history|file|materialize|expand`.
Source operations name `namespace` and `at`; answers carry exact resolved commit and
`complete`. Code uses an `argv` array and returns `{exit_code,result}`. Client roots cannot
override service state. Configured `hx code` and freshness calls route through this protocol.
Ownership and authoritative early publication are read-through joins on the client ledger.

`materialize` names exact `paths` and a fresh `out` outside the source checkout. Optional
`working_copy: true` shares local Git objects and writes a clean sparse copy at `at`.
Metadata jobs need no client checkout. Query failure is explicit; incomplete answers cannot
establish absence or prove complete build inputs.

`hx repo assignment TASK --out DIR [--at COMMIT]` derives sparse prefixes from a frozen
planned task, map anchors, hard output files and bound check closure. The materialization
answer includes task/revision, `closure_complete`, `gaps`, `early_contracts`, and
`anticipated_paths`. Unknown closures remain explicit. Provisioning does not move task
ownership or rewrite its workdir; replan to the new checkout before admission.
`repo expand --namespace REPOSITORY --at COMMIT --out DIR --paths PATH…` extends the read
set of an existing clean sparse copy at that exact source, preserving earlier paths.
Dirty or different-source copies are refused before modification.

## Dependency packet plan (`schema_version: 2`)

Top-level plan fields remain `schema_version`, `plan_id`, `expected_revision`, `repository`,
`goal`, `constraints`, `tasks`. Each version-2 task has exactly:

```text
id, expected_revision, behavior, acceptance, workdir, write_paths, map_inputs,
outputs, prerequisites, resources, check_policy
```

`check_policy` is `{reviewer,required_checks}` with an independent configured worker and
1–16 unique check IDs. `resources` has up to 32 unique `{id,mode}` entries (`shared` or
`exclusive`). Outputs have `{id,version,kind,obligation}` plus `paths` for `source` and
`contract`; `receipt` outputs have no `check_id`. A prerequisite is `{task_id,output_id,
version,need}`. `need` is `hard`, `contract`, or `substitutable`. Early dependencies add
`provider_output`, `provider_version`, and boolean `stand_in`; substitution requires true.
Only the current schema-2 dependency packet plan is accepted.

Before edits, the worker submits `hx plan bind RUN --file FILE` with exactly:

```json
{"definition_of_done":["Observable result"],"checks":{"CHECK":"exact check recipe"},
 "coverage":{"acceptance.0":["CHECK"],"output.OUTPUT":["CHECK"],
 "integration.PRODUCER/CONTRACT":["INTEGRATION_CHECK"]}}
```

Every acceptance/output/deferred-integration obligation must be covered. Inherited check
IDs remain required. Binding occurs at the unchanged admission source and freezes recipes.
`hx plan binding RUN` exposes the exact proposal; `approve-binding RUN --binding HASH`
requires the independently named reviewer. Known mutating native tools and proof commands
refuse unapproved bindings; this does not sandbox arbitrary shell programs.

`publish-contract RUN --output ID --file RECEIPTS` publishes exact regular interface files
and passing scoped proof while the provider is active. Published versions are immutable.
`acquire-contract TASK --producer ID --output ID --out DIR` exports only a declared
prerequisite and reports stand-in permission plus the pending real provider/version.
`verify-integration TASK --integration-run RUN --producer ID --output ID` requires completed
integration, exact installed consumer/provider source and the frozen integration recipes.

Typed milestones are `contract_published`, `packet_covered`, `integration_verified`,
and `deployable`. Packet coverage releases leases; deferred binding remains explicit.
Current readiness revalidates proof rather than trusting historical milestone rows.
The root loop delivers bounded at-least-once `HX-EVENT OPERATION KIND task=… identity=…`
notifications. Deduplicate by operation ID and query readiness; a wake is not evidence.

## Scoped check execution artifacts

Recipes optionally add `source: {complete,inputs,dependencies}`. Complete declared closure
uses scoped source identity; omitted/false closure retains repository-wide hashing. Recipe
`inputs` remain included. Unknown external/environment dependencies disallow reusable proof.
Graph candidates cannot assert completeness. Execution artifacts are keyed by complete
recipe/source/environment identities, including tools, platform, cwd and external versions.
Equal in-flight requests share an execution. Reuse creates a new requesting-run receipt.
Corruption rejects reuse. Interrupted claims remain held until owned process/group death
is proven; absent spawn identity remains unresolved.

A recipe may declare `report`, the runner's machine-readable format: `junit-xml` (pytest
`--junit-xml`), `trx` (`dotnet test --logger trx`), `go-json` (`go test -json`), `cargo-json`
(cargo `--message-format=json` with libtest `--format json`) or `jest-json` (`jest --json`,
vitest `--reporter=json`); `hx tool-exec --report FORMAT` declares the same. A file format
names its path with the argument placeholder `{report}`, which hx binds to a file beside the
captured output under the root (never the checkout) and removes after parsing; the JSON
formats read `{report}` when named, else the captured output, skipping non-JSON lines. The
execution parses that report once (`hx.test_summary`, `xml.etree`/`json`) into `summary`:
`{format, passed, failed, skipped, errors, failing, truncated}` counted per test, else
`null`; text output is never parsed. `failing` lists at most 20 unique failed or errored names
of at most 512 characters in report order; `truncated` marks an omitted name. `hx check`
prints `receipt_id`, `check_id`, `check_version`, `exit_code`, `valid`, `reused`,
`execution_id`, `reusable`, `reasons`, `artifact_hash`, `summary` and `event_id`; the
`check_result` event carries the same result. The `tool_result` event carries the execution
observation with `summary`, `artifact_hash` and `command`; `hx tool-exec` prints `exit_code`,
`reason`, `output_bytes`, `summary`, `event_id` and the reduction. A chunk is a diagnostic
when it names error, fail, exception, traceback, panic or assert, or holds a line starting
`E `. When `summary` records a failure, reduction keeps chunks naming a failing test, then
other diagnostics, then head chunks, in that order within the unchanged byte bounds. Jev
judges surplus chunks in at most four requests; without Jev or past that budget the
reduction keeps diagnostics, then the tail, then the head, and names the reason in
`fallback` (`null` when Jev judged). A character split by a chunk boundary starts the next
chunk. A summary is a parse of the runner's report, not an execution receipt.

## .NET build broker (`hx build`)

`graph` and `build` name a project/solution plus cwd, configuration, optional framework,
runtime and source-contract JSON. MSBuild evaluated properties/items/imports, SDK, packages
and source dependencies determine completeness and build identity. Unknown custom build
closures do not qualify for cache reuse. `build` accepts run/request identity and a bounded
job count; equal requests deduplicate across processes. Host slots limit simultaneous builds.
Immutable restore/build artifacts coexist with private writable intermediate/test state.
Optional `--platform` participates in evaluated identity. Ordinary SDK `.sln`/`.slnx`
solutions obtain exact mapped project/global/reference contexts from the installed
MSBuild ProjectGraph API. The offline reader bootstrap uses the same managed broker
and immutable artifacts; known mappings qualify for solution reuse.

`test BUILD` runs a selected test shard against materialized build outputs without another
compilation; optional project/framework/filter/runner args enter identity. `status JOB`,
`reconcile JOB`, and `materialize BUILD DESTINATION` expose lifecycle and artifacts.
Managed queued/running build jobs block completion. Current proof verifies graph/toolchain
and artifact provenance again at the final boundary.

## Intent, landing and fixed CI (`hx change`)

`submit --file FILE` takes exactly `{id,run_id,base,head,behavior}`. Intent preserves the
assigned goal and completed packet source scope. Changes, review decisions and landing
status are authoritative ledger records. Review names revision and intent hash; the author
cannot self-approve. Approval policies classify actual interface/migration/sensitive changes.
Reviewer roles/identities come from configuration, not caller claims.

`config/delivery.json` accepts reviewer/sensitive lists, `post_merge_checks`,
`pre_deploy_checks`, `check_definitions`, `build_checks`, `artifact_paths`, and
`validation_resources`. Exact check definitions and complete fixed-source closures are
required for candidate execution. Pre-deploy requires build checks and explicit tested
artifact inputs. Required forge checks remain required until forge policy changes explicitly.

`land CHANGE --repo BARE --expected-head COMMIT` prepares the merge before a brief local
ref compare-and-swap. Main advancement refuses stale publication. `reconcile ID` checks
actual publication after interruption. No remote push/deploy is implied.

`candidate --repo BARE --expected-head COMMIT --boundary integration|deploy` freezes source,
policy and coalesced intent membership. `run-validation BATCH --snapshot FRESH_DIR` executes
those checks on immutable source while main advances. Candidate status does not chase main.
Failure evidence creates scoped repair work. `fail BATCH --receipt ID [--baseline ID]`
retains attribution; `revert CHANGE --batch BATCH --repo BARE --expected-head COMMIT`
requires confirmed regression and publishes a surgical inverse, retaining later unrelated
changes. Pre-deploy readiness requires exact-source integration, real-provider proof and
an immutable manifest naming the tested artifacts/build identity.
Verified checkpoint references retain earlier proof while coalescing only later pending
intents. `candidate --historical` permits an authoritative ancestor; `--retry BATCH` creates
a distinct attempt only after safe reconciliation. Readiness preserves checkpoint intent,
approval, receipt and real-provider obligations. Sparse archives report `source_coverage`
as `declared_closure` or `whole_tree`, plus external environment resources. Multi-change
regressions retain bounded attribution; they do not authorize automatic batch rollback.

## Scoped delivery handoff

`hx handoff delivery DIR --change CHANGE [--batch BATCH]` writes `HANDOFF.md`,
`delivery-boundaries.json`, and `EVIDENCE-SHA256SUMS.txt`. The capture is also an immutable
ledger artifact. Fields distinguish coverage, review, landing, candidate source/tree,
required recipe hashes, captured receipts, tested build/manifest and remaining obligations.
`handoff check DIR` verifies captured evidence and seals; `--live` also requires current
identities/readiness to match. Historical evidence retains its original scope. Missing,
pending, failed or skipped validation cannot become deployment approval.

## Independent context and learning Companions

Each agent has a Context Companion and a separate Learning Companion. Context jobs own their
frozen task/map patches and context event cursor. Learning jobs have separate `learning_jobs`
and `learning_cursors`, frozen captured evidence, model/effort, request and proposal budgets.
Neither sidecar can acknowledge the other's cursor. Context failure does not disable learning;
learning lag does not block packet completion or release. Both respect shared host capacity.

Learning assessment tools are exactly `read_evidence` (one complete frozen evidence object,
without offsets) and `submit_assessment`. Responses require `schema_version: 1`,
`event_digest`, `proposals`, and `pending_event_ids`, with optional `ignored_event_reasons`
mapping explicitly ignored event IDs to reasons. Each proposal contains exactly
`category`, `scope` (global or paired role), `signal`, `check`, and nonempty `evidence_ids`.
Foreign, unread, ignored, pending or changed evidence cannot support a proposal. Artifact
references require a complete verified artifact read; a captured excerpt never proves the
original source or command output was complete. Relevant unavailable evidence remains pending.
Assessment commits its
own cursor and durable proposal outbox atomically, preserving exact cited evidence artifacts.
Replayed accepted output is idempotent. Failed/uncertain paid work is not silently repeated.

The outbox creates pending `hx learn` proposals only. An independently authorized different
session accepts, merges or rejects them. Reviewed global and matching role rule files compile
into every agent audience/flavor, including both Companions and worker subagents; proposal
text cannot override current policy or become an execution receipt. Accept and merge refuse
a rule file above 40,000 UTF-8 bytes before changing it; the proposal remains pending.

## Whole-file size discovery

`hx code audit --repo PATH` scans tracked and nonignored text files completely, reporting every
outlier's path, UTF-8 bytes, SHA-256 and estimated tokens. Indexing and admission force this
scan; stored admission and public readiness/assignment retain the report. Existing outliers
remain visible and fully indexed within the separately declared parser resource bound; a
resource limit refuses indexing without publishing partial absence. Changed oversized text
files pause scope audit and cannot publish completion.
