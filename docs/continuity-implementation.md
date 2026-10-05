# hx: incremental continuity and map-guided tasks

Status, 2026-09-25: implementation underway on `codex/continuity-runtime`; see [build status](/Users/loganrobbins/workspace/hx/docs/continuity-build-status.md) for verified units and outstanding integration. This document is the authoritative plan; the prompt audit and September research report supply supporting evidence.

**Scope:** engineer current-task context and bounded stored state; improve source/command retrieval, task-scoped tool loading, tool-output delivery, and compaction; add QA assistance as a subsequent extension. Build directly in dependency order with correctness tests; no A/B rollout, comparative benchmark, or demonstrated-savings prerequisite. Executor models remain explicitly configured. Model selection/switching is excluded from the roadmap. Workers have no personal or cross-task episodic memory.

Implementation target: hx 0.2. Baseline: working tree at `356d9c819b61bf70f846fc873e022cb52d50ca4c`, inspected 2026-09-25. Repository: `/Users/loganrobbins/workspace/hx`.

Build on hx’s event → companion → context → seam loop. Replace whole-state regeneration with validated record changes. Add a small application map that supplies task boundaries, interface dependencies, write ownership, and checks. Use Jev for bounded observation deltas, optional tool routing and surplus-output judgments. Keep planning, fact extraction and factual retention decisions in the native LLM sessions. Workers receive freshly compiled context for each assignment; they do not accumulate personal or cross-task memories.

Optimize **verified continuation facts per delivered token** and **time/cost to an accepted task**. Preserve exact identifiers, constraints, commands, reasons, and next actions; remove repeated narrative.

**Planner efficiency, clarified 2026-10-01:** the Partner consumes compact goal progress,
active ownership, blockers, dependencies, and next assignable work. Workers and QA run
the assigned checks; deterministic code validates completion and dependency applicability.
The Partner uses those results without reopening successful checks or loading their raw
output. Retrieve one targeted detail only when a failure, conflict, changed input, or
unresolved decision affects planning. Reuse the current map brief; missing information
creates discovery work only when it prevents a useful assignment. Keep provenance behind
references and reuse existing receipts; do not add model review or extra reporting to
routine success. Evidence supports correct continuation and scheduling with minimal work.

## 1. Changes against the implementation

| Current implementation | Required change |
|---|---|
| Pass names a mutable log and `from_seq`; ingestion advances to the current log head, including events potentially unseen by the companion. [companion.py:307](/Users/loganrobbins/workspace/hx/src/hx/companion.py:307) | Freeze each pass’s event range; acknowledge only that range after an atomic validated update. |
| Companion rereads previous state and emits a replacement; validation permits omitted fields and checks evidence only as a list. [stepstate.py:69](/Users/loganrobbins/workspace/hx/src/hx/stepstate.py:69) | Stable record IDs, typed evidence, explicit supersession, revision checks; unchanged records survive byte-for-byte. |
| Tool events retain excerpts and vendor transcript pointers. [hook_log.py:60](/Users/loganrobbins/workspace/hx/src/hx/hook_log.py:60) | Capture corrections, progress, source fingerprints, and check evidence into a bounded spool; retain only what current work requires. |
| Every tool hook checks companion readiness; appending rescans the stream, and usage lookup can rescan the transcript. Pi launches synchronous hook processes. [streams.py:135](/Users/loganrobbins/workspace/hx/src/hx/streams.py:135), [transcripts.py:18](/Users/loganrobbins/workspace/hx/src/hx/transcripts.py:18), [index.ts:43](/Users/loganrobbins/workspace/hx/src/hx/skeleton/adapters/pi/extension/index.ts:43) | A resident observer ingests appended records/native events, maintains transactional heads, and batches extraction. Retain thin lifecycle/control hooks. |
| Context concatenates invariants, memory, task, tasks, state, episodes, and handles; no aggregate budget. [compose.py:438](/Users/loganrobbins/workspace/hx/src/hx/compose.py:438) | Compile a bounded task slice with one authoritative representation of each fact. |
| Startup/resume composes immediately; planned seams check companion readiness. [hook_context.py:58](/Users/loganrobbins/workspace/hx/src/hx/hook_context.py:58), [seam.py:126](/Users/loganrobbins/workspace/hx/src/hx/seam.py:126) | Every boundary names a consistent checkpoint and includes unprocessed evidence needed for continuation. |
| Worker IDs key unlocked `tasks.json`; sequencing lives in Partner prose. [tasks.py:1](/Users/loganrobbins/workspace/hx/src/hx/tasks.py:1), [README.md:101](/Users/loganrobbins/workspace/hx/README.md:101) | Separate persistent task IDs from worker assignments; persist dependencies, input versions, and write leases. |
| Completion executes goal checks; state’s `verified` flag has no code/environment binding. [complete.py:96](/Users/loganrobbins/workspace/hx/src/hx/complete.py:96) | Checks produce receipts tied to exact inputs; completion consumes valid receipts and records integration proof. |
| Memory injects similarity/recency-ranked episodes, widening roles when sparse. [memory.py:662](/Users/loganrobbins/workspace/hx/src/hx/memory.py:662) | Replace episode retrieval with a bounded current-task store and current application map. Rank both context inclusion and continued storage; delete obsolete records. |

Retain native harnesses, tmux, goal pointers, deterministic completion, and one context file per stream. Supersede the existing no-sequencing, unbounded-context, and whole-state-write decisions. Add one narrowly scoped HTTP decision client for Jev.

## 2. Durable authority and record contracts

Use standard-library SQLite at `state/continuity.sqlite`, WAL mode, foreign keys, and durable commits. Task state and application map are two logical stores in one transaction boundary. Store large payloads in `state/artifacts/<sha256>`; write, fsync, and atomically install each artifact before referencing it. Garbage-collect unreferenced artifacts after interrupted writes.

`tasks.json`, `state/<worker>/<stream>.json`, work-item status/Tasks/Digest sections, and JSONL streams become compatibility projections. The ledger is authoritative for an active fleet; `hx repair` regenerates projections. Import the repository's committed application map as a versioned baseline; keep runtime observations in ledger overlays until integration exports them. Keep persona and operator-authored configuration files as inputs. Route task updates and deliverables through `hx progress`; admit other worker notes only when they serve an identified remaining goal step. Projections carry their ledger revision and update only after commit.

Use these tables; JSON payloads have explicit versioned schemas:

| Table | Required identity and contents |
|---|---|
| `events` | `(run_id, stream_id, seq)`; unique capture key, immutable payload hash; kind, task version, tool/action, artifact hash, provenance, source version, disposition. |
| `passes` | `pass_id`; immutable `(cursor, to_seq]`, event digest, task version, selected record versions, prompt version, status. |
| `records` | `(record_id, version)`; task/repository scope, kind, payload, evidence IDs, input fingerprints, `supersedes`, validity, retention reason, consuming step, expiry condition, storage cost. |
| `cursors` | `(run_id, stream_id)`; last atomically classified event, state revision. |
| `capture_sources` | Native session/branch, source generation, file identity, committed byte offset or event ID, decoder version, capture watermark, gap status. Independent of extraction cursors. |
| `tasks` | `(task_id, revision)`; immutable parent, goal/acceptance references, dependencies with required revisions/outputs, component IDs, expanded read/write sets, check IDs, map inputs, budget. |
| `runs` | `run_id`; task revision, worker ID, pinned map revision, phase, checkpoint, outcome. Events, passes, receipts and leases reference this assignment. |
| `map_records` | Stable node/edge ID, version, repository, kind, endpoints/payload, evidence, source fingerprints, applicability. |
| `record_entities` | Versioned current-task fact → map entity links, validated through explicit IDs/source anchors. Retrieval joins task state and application structure without copying either graph. |
| `receipts` | Check ID/version, task, exact command, cwd, input/environment fingerprints, start/end fingerprints, exit, result artifact, duration. |
| `checkpoints` | Immutable ID; ledger/map revision, task version, stream cursor vector, selected record versions, pending event IDs, source fingerprints, packet artifact hash. |
| `retrieval_runs` | Current-task query/policy/model versions, source snapshot, selected record versions, bounded trace, stop reason, budgets. Delete with the task; retain only content-free aggregate performance counters. |
| `leases`, `outbox` | Active assignment ownership; durable idempotent projection, index, and wake operations. |

Keep `task_id` across reassignment and resume; create a new `run_id` for each execution attempt. Amendments append task revisions; lifecycle transitions update runs and append events. Worker slots such as `eng-001` remain reusable. Allocate IDs and per-stream sequence numbers transactionally. Unknown subagents get provisional streams, then reconcile by native identity.

Task record kinds:

| Kind | Preserve |
|---|---|
| `goal`, `constraint` | Source request/correction ID; exact binding wording; explicit supersession. |
| `decision` | Choice, reason, affected interfaces. |
| `finding` | Fact, exact path/symbol, source hash, evidence. Include facts from edited files. |
| `search` | Query, cwd, path scope/globs, tool flags, source snapshot, relevant hits or negative result. |
| `command` | Exact executable/arguments or script, cwd, named environment requirements, check ID, latest result. |
| `cursor` | One active step; phase, last completed action, next executable action, blocker, open child tasks. |
| `dead_end` | Rejected approach, observed failure, conditions under which retry becomes valid. |

Keep the active goal and applicable corrections for the lifetime of that goal. Fold superseded wording into the current requirements and retire obsolete versions under the lifecycle below. The compiler includes every active binding constraint; neither Jev nor the companion can silently remove one. Only explicit task amendments supersede them. Render full hashes and verbose provenance through short stable IDs; retain exact values in the ledger.

**Current-state lifecycle.** Optimize for completing the current goal with the smallest sufficient state. There is no personal worker memory or cross-task episode store. A new independent assignment starts with its goal, current repository/map/configuration, and explicitly declared prerequisite outputs. A restart or reassignment of the same unfinished goal receives only that goal's surviving task state. Source/configuration changes invalidate affected facts before another packet can select them. Historical application states stay outside automatic retrieval; reconstruct history from Git only when the current goal explicitly needs comparison, migration, or rollback.

| Decision | Admission and lifetime |
|---|---|
| `context` | Current, required/useful for the next action; include within the packet budget and retain only while that need exists. |
| `store` | Current, needed for a named remaining goal step but not the next action; retain off-context with a consuming step, expiry condition, and byte/token charge. |
| `compress` | Some content still matters; replace it with the smallest sufficient typed fact or summary. Release the larger record/output after validating the replacement and retiring its live references. |
| `drop` | Irrelevant, obsolete, resolved, superseded, duplicated, or outside the goal; remove from context, task storage, retrieval index, and unreferenced artifacts. No permanent archive by default. |

Reevaluate at source/task changes, phase transitions, checkpoint construction, and completion. A fixed bug normally needs the current validation receipt; delete its investigation transcript and obsolete failures unless a remaining regression task needs them. A stack replacement removes old framework commands/anchors from the active map. Preserve a decision's rationale only while it explains a current constraint or choice. An unresolved external operation remains relevant until reconciled; then compact to the current result required by its consumers.

Set initial per-goal limits of 32,000 tokens for retained task records and 64 MiB for retained evidence; packet limits remain §5's 8,000 tokens. Count referenced artifacts and pending evidence, not just summaries. Enforce a separate 64 MiB host capture spool with streaming reduction/backpressure rather than unbounded buffering. Delete invalid/unneeded material first, then compress useful optional records. If genuinely required state still cannot fit, checkpoint and split/pause admission; do not disguise missing required evidence as successful compaction. Live source files and required product deliverables are application artifacts, not a hidden memory tier.

Keep the current issued checkpoint; retain its predecessor only until successful rehydration acknowledgement. Retire old pass/checkpoint/retrieval references; otherwise they would pin the entire history. Commit replacement/drop decisions and reference updates transactionally, then garbage-collect unreferenced payloads and index entries idempotently. Readers filter by current validity immediately; delayed deletion must not leak stale facts. Closed tasks purge generated notes, records, traces, and unneeded evidence after handoff acknowledgement. Move only named prerequisite outputs/receipts into their active consumers' lifetimes; remove them when the last consumer ends. Durable map records describe current source, while obsolete map versions survive only live concurrency/recovery references. Immutable means unchanged while retained, not retained forever.

## 3. Event processing and continuation

**Observation architecture.** Run one deterministic observer per hx root per host; the LLM companion consumes its bounded deltas. Register hx-managed session sources at launch/start. Tail structured native session logs for messages, tool results, and usage. Where a native extension already receives those events, enqueue them directly into a durable spool. Pi uses this path, including its child-process JSON stdout: children currently run with `--no-session`, so a session-file watcher would miss them. [index.ts:274](/Users/loganrobbins/workspace/hx/src/hx/skeleton/adapters/pi/extension/index.ts:274) Remote observers submit idempotent batches to the fleet authority.

**Host RAM.** Share the observer across workers and process capture and extraction serially within its resource budget. Use a 256 KiB capture scheduling quantum, at most 128 records per drain, and at most 32 event headers per companion pass; read a body only if it can fit the packet. A single complete record may exceed the scheduling quantum. Keep artifact verification in 64 KiB chunks, configure each SQLite connection for a 2 MiB page-cache target and disk-backed temporary storage, and disable database memory mapping. These are component bounds, not a total RSS guarantee. The 64 MiB capture spool is disk-backed. Do not load embedding models or launch per-worker watcher processes. Verify resource use serially with small fixtures before live fleet admission.

Use filesystem notifications to trigger reads of appended bytes; reconcile registered file identities/sizes every five seconds to recover missed notifications. Persist complete-record offsets atomically with normalized events. Buffer partial UTF-8/JSON lines; do not acknowledge them. Track replacement/truncation generations and native branch ancestry; never treat inactive branches as current execution. Restart from committed offsets; report unrecoverable gaps explicitly. Pin decoder versions and replay fixtures for each supported adapter. Copy needed payloads into hx artifacts before advancing capture; vendor log retention must not invalidate accepted evidence.

Keep hooks for context injection, pre-action guards, turn/background-work status, subagent registration, and compaction/reset boundaries. Their capture work is a compact durable enqueue; move stream scans, model scheduling, and tmux delivery into the resident controller. Retain capture hooks for evidence absent from a verified log/native feed, including source/check fingerprints recorded at execution. Hook and transcript observations share native IDs and merge complementary fields. Remove redundant per-tool hooks only after adapter replay proves equivalent capture, including failures and corrections. Never infer a safe reset from a quiet file.

At boundaries, persist the hook's final-message/background-work payload and request an observer drain. Claude explicitly does not guarantee that the final message is in the transcript when Stop fires. [Claude Stop contract](https://code.claude.com/docs/en/hooks#stop) Record a capture watermark and unresolved gaps; EOF alone does not establish turn completeness. A delayed log record remains eligible for ingestion after the boundary and reconciles with its hook evidence. Forced continuation packets include capture lag as well as pending extraction.

Maintain stream heads and latest usage during ingestion. Trigger readiness from changed-stream counters, then batch novel evidence into bounded passes; deterministic receipts and no-change events never wake the LLM. Replace `flush`'s 100 ms rescan/re-signal loop with checkpoint-completion notifications and a deadline. Current `wake_due` already batches at 20 records by default; its per-tool invocation is orchestration overhead, not necessarily a model call. [companion.py:492](/Users/loganrobbins/workspace/hx/src/hx/companion.py:492), [flush.py:39](/Users/loganrobbins/workspace/hx/src/hx/flush.py:39)

Normal capture and companion passes read only appended event ranges, selected current records, and bounded evidence slices. Do not load whole transcripts or whole prior memory files for routine updates. Reserve broader current-task state loads for explicit batch compression/compaction; keep those batches within retention and request budgets. Artifact slices use a chunk-integrity index so verifying an excerpt does not require reading the entire artifact.

For a growing log, full rescans per event produce quadratic cumulative decoding; incremental capture decodes newly appended bytes once, apart from bounded crash replay. Transport changes reduce process/I/O latency. Token savings come separately from deterministic reduction and delta extraction; measure both rather than attributing model savings to watching files.

1. **Capture.** Adapters normalize `request`, `correction`, `assistant_message`, `tool_result`, `progress`, `source_change`, `check_result`, `spawn`, `finish`, and `boundary`. Capture available output into a bounded processing spool before reduction; exclude private reasoning. Keep only evidence selected by the retention lifecycle after classification. Missing payloads are incomplete evidence. Execution-side capture records source bytes/hashes; a later observer read proves only that later snapshot. Deduplicate by `(session, native_event_id, kind)`; use a durable transcript generation/offset when native IDs are absent. Otherwise assign a spool UUID, retain ambiguous redeliveries, and mark identity uncertain. Never deduplicate by output text. An assistant statement is a claim, not a receipt.
2. **Reduce deterministically.** Record commands, exit codes, file mutations, child status, and receipts directly. An `rg` search exit of 1 means no matches for that scope. Hash canonical UTF-8 event JSON with sorted keys; compute the pass digest over the ordered `(event_id, payload_hash)` list.
3. **Prepare a bounded pass.** Freeze `(cursor, to_seq]`; include active constraints/cursor and only related record versions. Supply the delta inline in one pass file, plus exact artifact retrieval commands. Do not ask the companion to open the whole log or previous summary.
4. **Extract novel facts.** The native companion returns typed `create`, `supersede`, `invalidate`, `compress`, and `drop` operations, evidence references, and an acknowledgement of the frozen range. It generates wording only for changed records. Deterministically understood events require no LLM pass.
5. **Validate and commit.** Check pass identity, range digest, task revision, expected record versions, evidence existence/type, and source applicability. In one transaction, install accepted records/map changes, invalidate affected receipts, advance the cursor exactly to `to_seq`, and enqueue projections. Reject stale/conflicting proposals without advancing the cursor. Reprocessing a committed pass is a no-op.

Every event receives a disposition: `reduced`, `extracted`, `no_change`, `dropped`, or `pending`. Committing dispositions advances the contiguous cursor even when extraction is pending. An indexed pending queue independently selects those IDs for extraction; resolving one never rewinds or skips the new-event cursor. Retry extraction once; unresolved items remain in the queue and mandatory handoff evidence. Fully caught up means cursor at the frozen head **and** no pending items through it.

Reuse complete source statements and existing typed records instead of paraphrasing them unnecessarily. Code copies structured tool/progress fields; Jev classifies bounded observation deltas; the native companion proposes factual changes. Cross-event synthesis, relevance and uncertain interpretation stay with that companion. Irrelevant observations may be explicitly dropped. Uninterpreted user corrections and evidence of unresolved current work stay pending until their obligations are resolved.

Pin only evidence needed by live records, receipts, operations, and the current recovery checkpoint. Retire obsolete references and garbage-collect their payloads; no durable task archive is required. Keep compact capture watermarks for replay safety while the run remains active. Checkpoints preserve the current continuation state, not an ever-growing revision history.

**Boundary protocol:** reduce deterministic events, then freeze a checkpoint in a short transaction; release the transaction before any model call. Planned resets wait for extraction readiness. Forced compaction/restart uses the latest committed checkpoint plus ordered pending/tail event IDs, decisive excerpts, artifact commands, and explicit lag. Never block native compaction. If mandatory evidence exceeds budget, hold the next execution turn and request splitting. Issue one immutable packet; late extraction creates the next checkpoint and cannot edit the issued packet. Reuse it across clear/start. Boundary acknowledgements alone never trigger extraction.

`hx progress --file <json>` accepts `task_id`, `run_id`, `expected_revision`, `phase`, `step_updates`, `next`, `blocker`, `deliverables`, and `evidence_ids`. Emit on next-action changes, unobservable conclusions, and before yielding. Source identity validates a finding’s provenance; semantic conclusions remain assertions until independently checked. Tool execution and acceptance proof remain distinct.

## 4. Application map and task decomposition

**Implement the map.** Its useful contribution is identifying independently testable boundaries and shared contracts before dispatch. Start with observed structure; expand only the portion needed for the current goal.

Nodes: `behavior`, `component`, `interface`, `file`, `check`, `resource`. Edges: `implements`, `contains`, `depends_on`, `provides`, `consumes`, `checked_by`. A component stores one responsibility sentence; an interface stores its signature/schema and invariants; a check stores its exact invocation, cwd, declared inputs, and environment recipe. Resources cover databases, queues, services, and deployment targets. Use file + qualified symbol + content hash as a source anchor; line numbers are display hints.

**Semantic structure.** Make observable behaviors and invariants first-class records: what happens, under which preconditions, inputs/outputs, side effects, and failure modes. Connect each behavior to implementing components, required contracts/resources, and checks. Distinguish `required` claims from user goals, `observed` claims backed by source/execution evidence, and `hypothesis` claims awaiting validation. A declared check link does not prove the behavior; receipts prove only their recorded assertions and inputs. Example: “events arriving during a companion pass remain pending for the next pass” links the pass boundary, cursor writer, and late-arrival regression check.

Use exact/lexical and semantic retrieval to find starting records from goal language, then expand typed graph edges and validate source versions. Jev prioritizes optional expansion through the bounded traversal in §6. Required task dependencies, constraints, owned interfaces, and checks bypass probabilistic selection. Exact IDs, paths, ownership, dependencies, and receipts remain deterministic. Embedding similarity never establishes a scheduling dependency, correctness, or write permission. Index changed semantic records only; the vectors are a rebuildable lookup index, not the application map's authority.

Seed component boundaries from package manifests, directory ownership, entrypoints, test configuration, and observed reads. Validate membership and symbol locations against source. Persist uncertain relationships as `candidate`; planning uses `validated` edges. A passing test proves the recorded check, not inferred coverage of every component. `checked_by` requires an explicit check declaration or measured coverage evidence.

Scope every map record by repository identity and source fingerprints. Separate dirty worktree overlays from committed snapshots. File changes invalidate affected anchors and incident edges; interface changes invalidate consuming task inputs. Queue replanning for only those tasks and downstream dependants. A moved line with unchanged symbol content refreshes its location without replanning. Added knowledge alone does not restart running work.

**Stack changes and repository format.** Keep responsibility and contract IDs independent of framework, language, directory, and worker. Replacing a Python HTTP service with Go retains its component ID when its responsibility remains the same; update implementation anchors, runtime attributes, and check recipes. Changed contracts get new versions; component splits/merges record `replaces` lineage and require explicit task rebinding. Store stack-specific facts in namespaced attributes such as `python.runtime` and `node.package_manager`; adapters extract them without changing the graph's core schema. Preserve unfamiliar attributes; require a schema migration for unfamiliar structural types.

Commit the portable baseline under the product repository:

```text
.hx/map/manifest.json             schema version, persistent repo_id
.hx/map/behaviors/<id>.json       outcomes, invariants, failure modes, claim provenance
.hx/map/components/<id>.json      responsibility, source anchors, owned edges
.hx/map/interfaces/<id>.json      contracts and versions
.hx/map/checks/<id>.json          commands, inputs, environment recipes
.hx/map/resources/<id>.json       external dependencies and deployment boundaries
```

Use repository-relative paths, stable IDs, deterministic serialization, and source-content fingerprints. Keep event logs, leases, worker status, absolute paths, credentials, and SQLite outside Git. Portable evidence contains source anchors and check definitions; live execution receipts remain in the ledger. Source fingerprints exclude map files to avoid self-referential hashes. Record the importing Git commit in runtime metadata.

Watch manifest, lockfile, build, deployment, and test-config fingerprints. Invalidate their dependent map facts, command recipes, and receipts; rescan that scope. Unsupported languages still use explicit paths, contracts, and executable checks; inferred symbol relationships remain candidates until validated. The map describes capabilities and interfaces even when extraction support changes.

**Shared updates.** Every worker and companion reads slices from the same repository authority. Workers submit `hx map propose --file <patch.json>`; companions emit the same patch envelope in their pass output. Each patch carries `patch_id`, task/run identity, branch/worktree snapshot, operations, evidence, and expected versions for every record read or changed. The authority validates and commits it transactionally. Contexts contain the task's slice plus relevant changes since its checkpoint.

One authority owns the ledger for each active repository fleet. Same-host workers reach it through hx; remote workers use its authenticated endpoint, never a network-mounted SQLite file. Separate fleets exchange committed map records through Git. They import each branch's baseline into a separate snapshot; unmerged observations never become another branch's validated truth.

| Collision | Required handling |
|---|---|
| Different records; unchanged read dependencies | Accept both patches; unrelated map revisions do not cause retries. |
| Same record; identical resulting content | Coalesce idempotently and retain both evidence references. |
| Same record; different content or changed read dependency | Reject the stale patch with base/current/proposed values. Originating worker reconciles from evidence; never last-writer-wins. |
| Different branches describe different implementations | Retain branch-scoped versions. Reconcile only when integrating those branches. |
| Two tasks write overlapping source paths | Existing write leases serialize them; map update rights grant no source write rights. |
| Git conflict in map records | Integration worker reconstructs affected records from the merged source and reruns validation; never merge JSON blindly. |

Validate the resulting graph, including referenced endpoints and contracts, inside the patch transaction. Patches requiring several records commit all-or-nothing. A persistent contradiction becomes a scoped reconciliation task; independent records continue updating. Preserve existing validated facts until contradictory evidence marks them stale; exclude disputed facts from new task readiness.

The integration task exports only accepted records applicable to its combined source snapshot and commits source/map changes together. `hx map check` verifies schema, references, source fingerprints, and check recipes in CI. Export deterministic ordering and one record per file so unrelated edits produce independent Git diffs. A fresh clone reconstructs the planning baseline from Git; runtime continuity resumes from the ledger.

**Partner assignment contract.** Before drafting goals, the Partner calls `hx map plan-context --goal <file>`. Return a bounded assignment brief: candidate components and responsibilities, exact implementation anchors, shared contracts/consumers, check recipes, active write owners, and missing/stale evidence. Include source versions and record IDs; omit unrelated topology. The Partner selects boundaries, writes behavior-focused goals, and names the brief's records as task inputs. Map facts supply localization, sequencing constraints, and proof commands; the Partner supplies the intended behavior and acceptance criteria.

Join current map records to active task inputs/outputs by exact IDs. Retrieval can surface current work and explicitly required prerequisite outputs; it cannot reuse old task narratives or invent subgoals. For “make uploaded documents available to the KB analyst,” return verified ingestion → index → store → consumer relationships, concrete files/checks, and missing links. The Partner turns those facts into a goal and acceptance criteria. Missing links produce discovery work, never fabricated paths.

For an hx goal such as “add a harness adapter,” the brief identifies launch/install/event/reset contracts, flavor registration, shared configuration paths, and adapter conformance tests. The Partner assigns shared contract/registration changes first, then independent adapter work against the pinned contract, then integration checks. Existing compliant contracts become inputs rather than new work. Missing capability evidence becomes a small discovery task. This avoids broad goals that require each worker to rediscover the same harness rules.

Test assignment briefs against known responsibility boundaries, prerequisites, checks, and overlapping write scopes. Missing or stale facts must become explicit discovery work. Keep the map focused on facts used by these decisions; do not populate an exhaustive symbol catalog.

The Partner produces the task graph; deterministic `hx plan validate` enforces:

1. One observable behavior/invariant per leaf task, with a local acceptance command and explicit artifact/contract outputs. Keep implementation, fixtures, and tests of that behavior together.
2. Every dependency names required output IDs/versions. Establish shared interfaces before consumers; integrate and verify after consumers finish. Map edges suggest impact; only declared task prerequisites control scheduling.
3. Freeze expanded write paths with the task revision. Lease keys are `(repo_id, canonical_path_or_reserved_prefix)`; equal paths and ancestor/descendant reservations conflict across worktrees and map versions. Normalize symlinks and filesystem case. Include fixtures, lockfiles, generated outputs, and reserved prefixes for new files. Hold leases until the assignment stops. Parallel writers use separate worktrees; native subagents subdivide inherited leases and serialize shared-worktree mutations.
4. Include relevant map nodes and one-hop interfaces/tests. Missing boundaries create a scoped discovery task whose output is validated map evidence and a proposed decomposition.
5. Reject cycles, absent checks, unresolved inputs, overlapping leases, and oversized packets. Readiness requires dependency receipts whose output artifacts are present in the consumer worktree at the pinned versions.

`hx plan apply` records the validated graph. The Partner assigns ready tasks; the companion only proposes knowledge. Source outputs include a commit/tree fingerprint; other outputs use artifact hashes. An explicit integration task installs prerequisite outputs into the consumer input snapshot and emits a materialization receipt before consumer dispatch. Conflicts return to that integration task. Changed interfaces pause affected assignments at a safe boundary and wake the Partner with invalidated inputs; active leases retain their original paths.

After mutating tools and before completion, compare changed paths with leased scope. Unexpected writes pause the assignment for scope reconciliation.

Example for hx: define the event/pass contract → build independent adapters against it → implement the transactional reducer → integrate boundary delivery → run restart/concurrency scenarios. Split by behavior and acceptance proof. Do not create separate tasks merely for writing a fixture, adding one helper, or updating its assertion.

## 5. Context compiler and admission budgets

Extend `hx compose`; keep `hx compile` responsible for personas. Each context packet contains, in this order:

1. Task/version, repository/workdir, checkpoint, goal, acceptance criteria, and active constraints.
2. Cursor: completed action, exact next action, blocker, child status.
3. Relevant decisions/findings/dead ends; changed and unchanged source facts needed for that action.
4. Map slice: interfaces, allowed writes, dependencies and check commands.
5. Valid receipts, unresolved current failures/evidence, then optional current-task facts.

Use concise complete propositions with explicit subjects and concrete verbs. Apply the [compact continuation language](/Users/loganrobbins/workspace/hx/docs/continuity-language.md): conditions, exceptions, dependencies, ownership, input/output, rationale, expected versus observed behavior, source-bound verification, changes, uncertainty, and remaining obligations are common relationships, not an exhaustive template set. Render structured commands/cursors/searches deterministically; preserve other findings as concise complete sentences. Keep reasons and retry conditions where they affect the next decision. Quote exact commands and whitespace-sensitive text losslessly; reject unrecognized structured fields rather than silently omitting them. Display one short record/version ID and resolve verbose provenance through the ledger. Never shorten by deleting negation, applicability, command arguments, uncertainty, or unresolved obligations.

Deduplicate by record ID and content. Fold superseded addenda into current directives; delete obsolete versions when recovery references retire. Delete completed unrelated work from both the packet and task store. Include recovery commands only for deliberately retained artifacts. Never truncate a still-required command, identifier, constraint, or failing assertion to meet a character limit.

Illustrative handoff; `<…>` values are populated by the compiler:

```text
task T17@4 | checkpoint C81 | repo hx | cwd /Users/loganrobbins/workspace/hx
goal Preserve every event across companion passes.
must Keep native harness launch and one-read rehydration.
done P02: immutable pass range; receipt R12 valid for inputs <fingerprint>.
next Edit src/hx/companion.py::ingest; apply only P03.to_seq using its expected revision.
fact F28: ingest currently stamps the arrival-time log head; late events can be skipped.
write src/hx/companion.py; tests/core/test_companion.py
check .venv/bin/python -m pytest tests/core/test_companion.py -q
fail R13: late-arrival scenario expected cursor 40, observed 41.
artifact R13: hx evidence R13 --view failure
```

Add `context.max_tokens=8000`, `context.optional_tokens=1000`, `companion.pass_max_tokens=6000`, and `budget.max_forecast_usd` to configuration. These are initial operating limits. Use the adapter tokenizer; without one, conservatively charge UTF-8 bytes as tokens. Count persona/instructions, tool schemas, packet, conversation already retained, and reserved next output against the model window; the packet cap alone is insufficient.

Before dispatch and at controlled turn boundaries, require:

```text
known_context + packet + next_tool_output_reserve + next_model_output_reserve <= window_headroom
packet <= context.max_tokens
spent_usd + reserved_forecast_usd <= budget.max_forecast_usd
```

Set `window_headroom = min(model.threshold, floor(0.8 × model.window))`. Count retained conversation once; `packet` means newly delivered content. Forecast from observed task/check/tool classes; price model input, cache use, output, companion, Jev, and replanning separately. Version prices and accounting. Until calibrated, use configured upper output limits and uncached input prices. Store forecast and measured spend separately.

On overflow, checkpoint and wake the Partner with the violating budget and task boundaries. The Partner splits at the next independently verifiable contract; `hx plan validate` confirms acceptance coverage before replacing the parent with child tasks plus integration. Children share the remaining parent budget, including integration and parent acceptance; splitting never resets spend. If an indivisible task cannot fit, retain its checkpoint and mark `budget_blocked`; never discard a constraint to admit it.

Adapters declare usage-reporting, request-gating, output-limiting, and reset capabilities. Enforce a configured hard spend limit only through adapters with request-level accounting and admission; refuse such dispatch when capabilities are absent. Current Codex hook normalization explicitly lacks usage counts. [hook.py:15](/Users/loganrobbins/workspace/hx/src/hx/skeleton/adapters/codex/hook.py:15)

Large results are reduced to the evidence required for current work; retain a full artifact only when a live record or check needs it, within the storage budget. A bounded error/hit excerpt enters context. Apply limits through wrapped commands or supported pre-delivery adapters. Existing `PostToolUse` capture occurs after execution and cannot retroactively save the current tool result’s context.

## 6. Jev: bounded selection that removes expensive work

Jev accepts state plus typed questions and returns choices/probabilities or scores; use it for selecting existing candidates. The native LLM continues writing code, extracting new facts, and generating plans. [TypeSafe coding-agent integration](https://docs.typesafe.ai/introduction/coding-agents) Add a semantic traversal policy to the existing SQLite map; no graph database or generated query language is required. Neo4j's experiment demonstrates ranking fetched edges with `Choice`; TypeSafe also documents hierarchical beam search. These establish a usable pattern, not measured hx savings. [Neo4j traversal experiment](https://neo4j.com/blog/genai/navigating-a-neo4j-knowledge-graph-with-jev/), [TypeSafe hierarchical classification](https://docs.typesafe.ai/cookbooks/hierarchical_classification)

**Required integration, clarified 2026-10-01:** Jev participates in the live decision loop. There is no opt-in flag, substitute model, or heuristic fallback for a required Jev decision. Deterministic bookkeeping and exact required inputs do not need a semantic call.

Implement `jev.py` against `POST /v1/systemone`; pin `jev-1.13.0`. The documented price is $0.042 per million input tokens, with free output; request limits are 64k total and 32k for state plus the longest question. Batch independent questions over the same state. A 4,000-input-token request costs $0.000168 at that price. [TypeSafe model reference](https://docs.typesafe.ai/models)

| Integration | Decision and savings mechanism |
|---|---|
| Event deltas — P10 | Compare new bounded observations with supplied current state; identify additions, repetitions, uncertainty and map-related changes. Avoid unnecessary extraction calls without choosing which factual obligations survive. |
| Tool/skill loading — P10b | Select optional tools and skill bodies for the current task, phase, and map slice before their definitions enter context. Keep required capabilities and discovery available; load missing capabilities on demand. |
| Companion inputs — P11a | Code includes active constraints, cursor, exact references and a bounded current-state slice. Jev routes new observations; it does not rank facts out of the checkpoint. |
| Plan review — P11a | Assess each leaf for one behavior, independent acceptance, and coherent ownership. Return failed criteria to the Partner before dispatch. Target: fewer decomposition retries; code validates dependencies, leases, budgets, and checks. |
| Map traversal — P11c | Before Partner assignment and when preparing a changed task's context, prioritize real behavior/component/interface/check edges. Return useful neighborhoods, source anchors, current decisions, and coverage gaps. Target: fewer repository searches and assignment revisions. |
| Task-store lifecycle — P11d | The companion compresses/drops current-goal facts; code enforces validity, quotas and closed-task purge. This requires no Jev fact selection. |
| Extractive preservation — P11e | Preserve complete source statements and exact structured fields. Code renders typed facts once; the companion writes new relationships. Jev does not generate or choose the preserved fact set. |
| Planned compaction timing — P11f | Within a deterministic pressure/readiness envelope, assess phase completion and context change. Target: reset at useful work boundaries with less rediscovery and lower total context cost. |
| Tool-result reduction — P11g | Select relevant surplus output chunks before delivery to the coding model. Protect deterministic diagnostics; reduce context cost before it accumulates. |
| Failure triage — P11h | Classify bounded failure signatures from real check receipts; give QA/engineering a focused next investigation. |
| Preliminary check selection — P11i | Rank additional test candidates against the current diff/contracts; find useful failures earlier without reducing required acceptance coverage. |
| Semantic review — P11j | Screen current source units and assertions for named contract/test gaps; route located suspicions to verification. |

Cap each request at 16 candidates and 4,000 tokens including state and all questions; conservatively count UTF-8 bytes when exact tokenization is unavailable. Split independent `Noul` batches before sending. Never split one `Choice` and compare probabilities from different option sets. Cache by model/question/task/policy versions, source snapshot, event digest, ordered candidate IDs/versions, and relevant current-task path inputs. Use a 500 ms request deadline without synchronous retry. A required Jev decision that fails stops its dependent operation with an explicit error; do not substitute deterministic ranking or another model. Validate answer types, finite probabilities, exact option IDs, and Choice normalization before use. Keep scores and full traces outside the agent packet.

Use independent `Noul` questions for each direct judgment. Policy thresholds differ: optional tool suggestions start at 0.65; delta/map routing uses 0.90; repetition can bypass extraction only with complete coverage and both novelty/map scores at most 0.05, excluding failures. Output retention preserves uncertain surplus. Scores are not evidence correctness. Validate these policies with fixed correctness cases; no comparative rollout gate. `Choice` probabilities belong only to the offered option set, and hx's smaller request cap governs. [Choice contract](https://docs.typesafe.ai/primitives/choice), [Noul contract](https://docs.typesafe.ai/primitives/noul)

**Bounded traversal contract.** Implement `retrieval.py` behind `hx map plan-context` and the context compiler:

1. Pin repository/branch, task, and graph versions. Include binding constraints, cursor, explicit references, task prerequisite closure, owned interfaces, and declared checks deterministically. Seed optional search with up to three exact/lexical/vector matches. Skip Jev when the required records and deterministic neighborhood already fit and satisfy the requested context fields.
2. Fetch valid incoming/outgoing edges with direction, relationship type, endpoint responsibility, source anchors, and compact local schema. Include only current-goal task records and declared prerequisite outputs through exact entity references. Use no invented graph edges. At high fanout, page candidates by relationship type/component with round-robin diversity and stable IDs; expose considered/available counts. Preserve cross-component links and multiple memberships. Grouping is a retrieval view, never new architecture.
3. At each depth, offer one `Choice` over at most 15 real path extensions `(parent_path_id, edge_id, endpoint_id)` plus `none_useful`; include each parent's path context. This allocates effort across the combined frontier. Ask at most 15 independent usefulness Nouls for deduplicated endpoints/seeds; reduce edge candidates when needed to fit the request. Keep up to three distinct next paths: two by Choice rank and one remaining path by deterministic diversity rotation; use fewer when exhausted. Break ties by stable ID. A winning `none_useful` ends this optional search attempt, retaining admitted evidence and reporting all unexplored coverage. Otherwise do not prune traversal solely on endpoint usefulness: a useful route can pass through a generic node. Preserve useful records from every explored depth, not just the winning leaf.
4. Track visited IDs per path. Memoization includes ordered frontier/path contexts, visited IDs, seeds, requirements, policy, and graph/source snapshot. Compare Choice ranks only within the same request. Use breadth-level beam selection, not cumulative probability products. `-log(P)` is not an admissible remaining-cost estimate for A*; summing log probabilities still favors shorter paths. Claim neither optimal paths nor exhaustive coverage.
5. Set query limits: depth 4, beam 3, four API requests, 16,000 total input tokens, and 1,500 ms elapsed. Pack all frontier questions for a depth together; count every question against these shared limits. There is no model retry. On timeout/invalid response stop the requested semantic selection and preserve the last committed context; do not substitute deterministic expansion or another model. Record `exhausted`, `none_useful`, `budget`, `timeout`, or `invalid_response`; unvisited/omitted edges remain a coverage gap. Stop optional search on these conditions, never on a model's claim that the engineering goal is complete.
6. Deduplicate admitted records, union deterministic requirements, and compile within §5's packet budget. Return exact paths/commands, relationship evidence, record versions, and a compact gap summary. Missing requested fields become discovery tasks; a budget stop never means “no dependency exists.” Atomically commit a successful selection and its bounded trace under the checkpoint's packet-input hash before construction. Crash/replay reuses that decision without another Jev call; failed decisions cannot produce a replacement selection.

Dependency impact analysis traverses all applicable declared dependency edges in code. Jev can order optional investigation, but cannot remove consumers from invalidation. Task routing remains Partner selection among deterministically ready assignments. Keep completion, release authorization, event capture, and reset eligibility deterministic; Jev may recommend planned reset timing inside the envelope below. Do not use `Noul(goal_reached)` to certify context completeness; exact target lookup needs no model.

**Extractive preservation.** Preserve exact structured progress/tool fields and complete source statements. Keep negation, conditions, attribution and exact commands. The companion proposes fact changes using known record IDs and versions; code renders their typed payloads once. Jev supplies bounded delta/repetition routes, never new prose or a factual retention authority. Missing coverage and uncertain interpretation remain explicit.

**Joint context and storage decisions.** The companion chooses `context`, `store`, `compress`, or `drop` according to the next action and named remaining steps of the current goal. Code enforces binding obligations, source validity, quotas and references. A vague possibility of future usefulness is not a retention reason. Delete obsolete application states and irrelevant facts; keep only useful current state with an expiry and size charge. Neither factual inclusion nor deletion depends on Jev scores.

The companion can produce a shorter replacement from several still-useful records and check that its meaning and qualifiers survive. Code validates protected fields, source applicability, and live references before installation. Keep minimal supporting spans or structured receipts where the current fact needs evidence; remap live references to those compact artifacts before deleting originals. A deleted payload must not leave a promised retrieval command that no longer works. A retention manifest covers current mandatory records and decisions to retain, compress, or drop; previous text is not permanently pinned. Unresolved current obligations stay explicit until resolved or superseded by the goal. Forced compaction carries that live tail through §3.

**Planned compaction timing.** Keep the current safe-boundary gates: no in-flight background work, capture/extraction caught up for the frozen boundary, pane ready, and a validated packet within budget. [seam.py:106](/Users/loganrobbins/workspace/hx/src/hx/seam.py:106) Start the advisory band at `0.75 × window_headroom`; below it, make no timing call. At an eligible idle boundary, batch Nouls for “the current phase has finished” and “the next action needs a different working set,” using the cursor, recent delta, and proposed packet. Recommend an early reset only when both are `>=0.90` and estimated retained-context reduction is at least 25%, using comparable token accounting. Log the policy version and estimates; test these thresholds with fixed phase/headroom fixtures.

Evaluate at most once per changed phase or eight new semantic events, with the existing 500 ms deadline and no retry. Timeout or invalid response stops that timing assessment with an explicit error; no substitute recommendation is generated. A valid uncertain answer does not recommend an early reset. Deduplicate recommendations by checkpoint and clear the advisory state after reset. At the hard admission limit, deterministic policy requires a checkpoint/reset or task split; Jev cannot defer it, waive readiness, or drop mandatory evidence. Native forced compaction proceeds immediately through §3's tail-preserving protocol and never waits for Jev. Measure saved input cost against reset/rehydration cost and lost prompt-cache reuse; an earlier reset is not automatically cheaper.

**No episodic carryover.** Remove historical-route and task-outcome retrieval from the design. Keep decision traces only within the active task/recovery budget; after closure, discard their text and paths. Content-free aggregate latency/cost/error counters support evaluation without becoming worker knowledge. The shared map improves by replacing observations with validated current application facts, not by accumulating accounts of past work.

**Engineering/QA integrations.** The [September 25 use-case review](/Users/loganrobbins/workspace/hx/docs/jev-engineering-usecases-2026-09.md) verifies implementations for output pruning, failure triage, preliminary test selection, and staged code review. Adopt those bounded patterns through the shared client. Model routing/switching is excluded from this design. Continuous semantic supervision and browser control are not required implementation units.

Build tool-output reduction with its capture, budget, and retention dependencies. Integrate deterministic diagnostic preservation and Jev selection directly; verify the result with fixed correctness fixtures. No comparison implementation or experimental rollout is required.

**Task-scoped tool loading — P10b.** The application map identifies needed capabilities; a separate tool catalog maps those capabilities to each adapter's real built-ins, MCP tools, and skills. Store stable tool IDs, concise purpose, input-schema hash, provider/version, capability tags, and required dependencies. Keep full schemas and optional skill bodies outside the executor packet until selected. Catalog metadata describes capabilities; it cannot authorize an action or override operator policy.

At dispatch, compile `required ∪ selected_optional` from the current goal, next action, role, map slice, and available catalog. Code includes tools needed for context reads, progress/completion, applicable checks, and discovery/loading. Jev ranks bounded optional candidates only; it cannot omit required tools or invent names. Include only the selected command recipes and relevant skill bodies. Exact requested tool names bypass semantic ranking. Provider failure stops semantic tool selection with an explicit error. Already available required capabilities remain available; do not substitute heuristic matching for a failed Jev decision.

Expose `hx tools discover --query <text>` and `hx tools load <id>...` through the retained shell/discovery capability. Discovery returns a bounded page of IDs, purpose, and availability, with a continuation cursor and an exact-name lookup; the full catalog never enters context merely to select tools. Loading validates catalog version, task scope, dependencies, and existing permissions, then installs the real schema through an adapter-supported mechanism. A loaded tool is not invoked automatically. Recompute at task/phase boundaries or an explicit missing-capability request, not every tool call; preserve schemas for in-flight calls and outstanding results. Independent assignments start with a fresh selection.

Adapters declare `tool_visibility = dynamic | launch | advisory` separately from permissions. Use native deferred loading where verified; otherwise use a launch allowlist and apply expansions at a checkpointed session restart. A launch-only loader returns `pending_restart` until that restart succeeds; never report an unavailable tool as loaded. Advisory adapters receive focused recipes/discovery hints and must not claim reduced native schema tokens. Keep actual tool availability, selected definitions, and host-preloaded definitions distinct. Do not use permission allow rules as a substitute for controlling schema exposure.

Local CLI inspection on 2026-09-25 confirms Grok Build 1.0.41 exposes `--tools` and `--disallowed-tools` for built-ins; the current [Grok launcher](/Users/loganrobbins/workspace/hx/src/hx/skeleton/adapters/grok/start.sh) passes neither. Claude exposes `--tools` for built-ins; Pi exposes `--tools` for built-in, extension, and custom tools. These establish launch selection, not verified dynamic reloading. Grok's MCP configuration commands are separate from its built-in allowlist. Register MCP and skill loading only through verified per-adapter contracts; treat other adapters as advisory until their capability is established. Pin capability checks to the configured executable/version, and test emitted launch arguments plus the tool definitions actually exposed.

| Unit | Required contract |
|---|---|
| P11g: output reduction | Run only when a result threatens the reserved tool-output budget and the adapter supports interception before delivery. Parse status, failing assertions, diagnostic locations, and required output fields deterministically. Jev ranks surplus chunks using the current goal/cursor; preserve selected text verbatim and call/result pairing. Never treat source documents or structured data as disposable log chatter. Timeout/unscored content remains explicitly available through bounded task-local evidence; never silently drop the only error. Without interception, reduce future context only and claim no savings on initial delivery. |
| P11h: failure triage | Deduplicate exact failure signatures within the current run; ask `Choice` for connection, timeout, auth, dependency/configuration, assertion, build/script, or unknown. Preserve command, exit, failed assertion, input revision, and diagnostic text. Labels are hypotheses; they cannot change pass/fail, authorize retries, or rewrite an observed failure as a flaky test. Expire on repair/source change. |
| P11i: preliminary checks | Derive mandatory checks from the task and current map, then add/order semantically relevant test candidates against the actual diff. Always include deterministic impact matches. Selection affects the development loop; completion still requires the declared acceptance suite, including its full-suite command when specified. Record omitted tests as `not_run`; a selected subset's success never establishes broader coverage. Incomplete maps remain explicit. Provider failure stops semantic check selection; existing declared acceptance remains required. |
| P11j: review | Parse changed units and attach relevant current contracts, callers, and test assertions. Ask atomic questions about missing cases, self-confirming assertions, or a specific invariant. Return source/version, rule ID, candidate location, and uncertainty. A reviewer confirms or dismisses each hypothesis with evidence; missing/truncated units produce `incomplete`, not `clear`. No model score certifies correctness, suppresses a failing receipt, or replaces deterministic analysis/tests. |

Share §6's request bounds and §5's task cost reserve across these uses; do not multiply budgets per hook. Cache only identical goal/source/evidence/question versions, expiring under §2. Count actual invocations, incomplete cases, and recovery reads. The optional semantic review can suggest new checks, but only the Partner's validated task amendment changes acceptance requirements. Existing required checks remain mandatory.

Enable integrations when their dependencies and correctness tests pass. Test cross-component changes, sparse/stale maps, high fanout, long routes, relevant siblings, absent targets, deletion correctness, and fresh-task isolation. Mandatory records must survive selection; bounded traversal must report incomplete coverage. Record actual tokens, latency, storage, and failures as operational diagnostics. No baseline experiment, paired trial, or savings threshold gates implementation or activation.

## 7. Verification and sensible implementation units

`hx check <check-id>` records exact command/cwd, source fingerprint, executable/environment/lockfile fingerprint, exit, output artifact, and before/after input hashes. Compute source identity from tracked contents plus nonignored untracked inputs; include declared ignored fixtures and external data versions. A mutation during execution invalidates the receipt. Unknown environment or external state makes a receipt non-reusable.

Reuse receipts only for identical declared inputs and check versions. A task is complete only when its acceptance checks are valid, required artifacts exist, no child remains open, and the final workspace satisfies the clean-tree contract. Check cleanliness again after verification. Integration checks run against the combined tree. Emit `HX-COMPLETE` only after completion commits to the ledger; deliver the wake through the outbox.

Deliver these units in dependency order. Each owns one behavior and its tests; default to one active implementation unit per executor.

Begin with P01 and implement the core in dependency order, including task-scoped tool loading P10b and tool-output reduction P11g. P01–P11g, including lettered subunits, form the core context/state/tool implementation; P12 cuts it over. P11h–P11j are optional QA extensions after the core is working and do not block its migration. General tool-use improvements include scoped source discovery, current command recipes, source-aware reuse of results, and fewer redundant searches/checks; the executor still constructs and executes tool calls.

| Unit / dependencies | Code boundary | Acceptance evidence |
|---|---|---|
| P01 | New `continuity_store.py`: schema, transactions, artifacts, outbox | Crash/reopen preserves committed state; rollback exposes neither cursor nor partial records; concurrent updates do not disappear. |
| P02a / P01 | `events.py`, resident observer, source cursors | Crash/restart, partial records, missed notifications, rotation, and branch changes preserve evidence; repeated native event deduplicates; identical distinct calls survive; artifacts survive transcript deletion. |
| P02b / P02a | Native adapters, thin hooks, controller notifications | Corrections, failures, assistant-only turns, sessionless children, and late Stop messages survive; source observations keep execution fingerprints; no hot-path full scan; lifecycle control remains effective. |
| P03 / P02b | `companion.py`, `stepstate.py`: bounded passes and patches | Freeze through 40, append 41 during extraction: commit cursor 40; next pass contains 41. Reject future cursors, stale revisions, invented evidence; replay committed pass is idempotent. |
| P04 / P03 | New `progress.py`, task records and companion prompts | Amendment preserves unrelated records; edited-file facts survive; decisions/constraints cannot vanish through sparse output. |
| P05 / P02b | New `checks.py`, `complete.py` | Changing code, environment, check, or external input invalidates reuse; writes during/after checks prevent false completion. |
| P06a / P03 | New `appmap.py`: schema, portable import/export, stack attributes | Fresh clone reconstructs baseline; stack replacement retains responsibility IDs; config changes invalidate dependent facts; candidate edges cannot establish readiness. |
| P06b / P06a | Map proposal transactions and branch overlays | Concurrent independent patches both survive; identical changes coalesce; conflicting stale patches reject; multi-record updates roll back together; branch facts stay isolated. |
| P06c / P06b | Map export/integration and `hx map check` | Source/map commit validates together; unrelated record files merge independently; conflicting records rebuild against merged source; stale anchors fail CI. |
| P07 / P04, P05, P06c | `compose.py`, boundary hooks, `seam.py`, `flush.py` | Restart/clear yields identical checkpoint content; unresolved tail survives; exact commands remain intact; mandatory overflow blocks admission. |
| P08 / P05–P07 | New `planning.py`, map assignment brief, tasks/dispatch/resume | Brief supplies exact owners/contracts/checks and flags missing evidence; reject cycles/overlap/missing inputs; stable task survives worker reuse; consumer requires materialized prerequisites. |
| P09 / P07–P08 | New `budgets.py`, model config/adapters | Reserve all prompt/output/cost components; automatic split preserves parent acceptance; incapable adapter refuses hard-limit dispatch. |
| P10 / P07 | New `jev.py`, `memory.py` | Fixed fake responses cover batching, bounds, cache keys, empty results, invalid IDs, timeout; failed decisions stop the dependent operation without replacing Jev; mandatory records remain intact. |
| P10b / P08–P10 | New `tool_catalog.py`, adapter capability/launch integration, `hx tools discover/load` | Mandatory tools survive selection; exact requests load without scoring; unknown/stale IDs reject; unavailable tools are never reported loaded; missing capabilities remain discoverable; phase changes preserve in-flight calls; new tasks inherit no old selection; launch-only adapters expand at a checkpointed restart. |
| P11a / P08–P10 | Jev record selection and plan review | Selection shrinks prompts without changing accepted patches; failed semantic criteria return to Partner; Jev cannot bypass deterministic gates. |
| P11b / P04, P06c, P08, P09 | Prompt compiler, all personas/companions, QA role, instruction skills | Every role/adapter receives resolved identity and complete task inputs once; preserve operator policy, exclude generated personal memory; fixtures cover fresh assignment isolation, source rereads, real prerequisites, stale QA proof, and unknown release outcomes. |
| P11c / P06c, P08–P10 | New `retrieval.py`, map/context integration, trace persistence | Fixed responses preserve jointly relevant siblings and mandatory low-ranked dependencies; handle cycles, incoming edges, rare high-fanout relations, stale sources, invalid IDs, and all budget stops; identical saved selection yields identical packet. |
| P11d / P04, P07, P10 | Task-store lifecycle, quotas, index cleanup, garbage collection | Source changes evict obsolete facts; compressed replacements permit old payload deletion; closed goals purge state; long runs stay within quotas; crash/retry and shared live references prevent premature deletion. |
| P11e / P04, P07, P10, P11d | Extractive task facts and retention manifest | Negated/conditional current constraints retain meaning; irrelevant candidates can be dropped; compression preserves required fields then deletes originals; unresolved current obligations survive; expired evidence cannot enter new packets. |
| P11f / P07, P09, P10 | Advisory seam timing inside deterministic gates | A model recommendation cannot bypass running work/readiness or delay hard limits; forced compaction never waits; timeout falls back; repeated advice cannot cause reset loops; continuation fixtures retain current required state. |
| P11g / P02b, P09, P10, P11d | Adapter interception and tool-result reduction | Hidden-middle errors, multiline assertions, structured results, and call/result identity survive; timeout/unscored chunks have valid recovery; bounded task storage; measure tokens actually delivered. |
| P11h / P05, P10, P11d | Receipt-based failure triage | Fixed failures receive tentative classes/unknown; status and exact diagnostics remain unchanged; no automatic retry; duplicates coalesce only within matching run/input identity. |
| P11i / P05, P06c, P10 | Preliminary test ranking | Mandatory/impact checks cannot be omitted; fixtures include broad indirect tests; incomplete inputs fall back; selected-subset success cannot complete a task with outstanding acceptance. |
| P11j / P06c, P11a, P11d | Located semantic review and QA handoff | Seeded contract/test gaps exercise review precision/recall; missing context stays incomplete; suspected issues require verification; changed inputs expire old findings. |
| P12 / core units through P11g | Migration, projections, CLI/UI contracts, recovery scenarios | Existing instance imports once; interruption resumes import; adapters/UI use projected versions; completion/wake recovery does not redispatch completed work. Optional QA units cannot block core cutover. |

For each unit, run its focused suite; run the combined boundary/concurrency suite when integrating. Implement bug fixes with reproducing tests, features with invariant/behavior tests. Preserve check modes `new_behavior`, `regression`, and `artifact`: replace dispatch’s blanket “already passes” rejection with mode-specific validation, so refactors and documentation tasks have meaningful gates.

## 8. Persona and companion alignment

The shipped prompts need revision alongside the runtime. Findings, source locations, and exact replacement rules are in the [prompt alignment audit](/Users/loganrobbins/workspace/hx/docs/prompt-alignment-audit.md). Compile one shared rule set plus audience-specific mechanics and a short role policy; place each rule in one delivery channel. Supply the task, active constraints, map slice, and source versions in every companion pass. Test generated prompts, not only the source templates.

| Role | Current map slice | State retained while needed for the active goal |
|---|---|---|
| Partner | Behaviors, boundaries, dependencies, current ownership, acceptance checks | Binding user corrections, task graph changes, output versions, next assignment, unresolved decisions. |
| Backend | Domain invariants, data ownership, interfaces, migrations, integration checks | Source facts, interface changes, exact failures, decisions, test/environment receipts. |
| Frontend | User journeys, UI states, API contracts, design tokens, accessibility checks | Verified behavior with build/viewport/method, open accessibility defects, commands, contract assumptions. |
| QA — add shipped role | Required behaviors, changed dependencies, failure modes, test coverage claims | Reproduction, expected/actual result, dataset/seed, exact revision/environment, failures, coverage gaps, unresolved flakes. |
| Release | Build inputs, artifact/deployment dependencies, compatibility, rollback contracts | Ordered operations, artifact digests, target/version, authorization scope, operation IDs, observed results, next unrepeated action. |

Give QA and release engineers both map slices and companions. QA evaluates claims against requirements independently; prior success does not replace verification of changed inputs. Release distinguishes observed past success from current target state and records uncertain operations before any retry. Keep deterministic capture active for every role; invoke companion extraction only for new semantic information. Routine check output and receipt updates need no model call.

Add `qa-engineer` persona and companion retention files to the skeleton, role catalog, fleet instructions, and installation tests. Replace mandatory microtask fan-out with one coherent, independently testable behavior per assignment. The Partner owns cross-worker decomposition; workers own implementation steps within assigned scope. Preserve genuine dependencies; simulations prove local behavior only and require separate integration evidence.

Land prompt changes with their supporting APIs at cutover. P11b validates rendered prompts and behavior; the companion's corrected-constraint fixture also proves the required input survives a new pass.

## 9. Migration and release gate

Build the core units through P11g behind a schema-version gate; legacy files remain the sole live authority until P12. QA extensions P11h–P11j have separate activation gates. At cutover, pause assignments and take a temporary recovery backup of worker-written sections and task/state/log files, outside automatic retrieval. Import only active goals, applicable corrections, current map facts, and state admitted by §2; derive task IDs from dispatch identity. Mark old verification claims unbound. Exclude personal memories and historical episodes. Install `hx progress` and updated worker instructions before restarting sessions; switch every reader/writer together, regenerate projections, then resume. Interrupted migration resumes idempotently. Test progress → completion → restart, verify current required state, then delete the migration backup. Intentional exclusions follow recorded retention decisions; current required edits cannot disappear accidentally.

Update `CONTRACTS.md`, configuration validators, CLI help, shipped personas/skills, and the active specification. Keep existing board/show fields as projections and add task/checkpoint IDs. Index only current map facts and retained active-task records through an outbox. Remove deleted/invalidated entries and enforce validity filters immediately, even while index cleanup lags. Composition reads the existing index without waiting for embedding work. Goal and source applicability precede semantic similarity.

Use deterministic event fixtures and fresh-session integration tests: late event arrival, crash during commit/garbage collection, correction after resume, stale test receipt, stack replacement, closed-task reuse, long-run storage pressure, renamed source, shared-file contention, oversized mandatory context, and interrupted task splitting.

Release requires zero unintended loss of current required state, zero lost active binding constraints, zero accepted stale receipts, zero obsolete/cross-task facts in new packets, bounded task storage, deterministic packet hashes for identical live checkpoints, and correct parent acceptance after splitting. Intentional classified deletion is required behavior. Tool selections must preserve required capabilities and provide a working expansion path. Release follows correctness and integration checks; no comparative evaluation is required.

Measure bytes decoded per new event, process launches, observer CPU, p95 tool-hook delay, capture/checkpoint lag, packet tokens, companion input/output tokens, extraction calls avoided, time to first useful action, and accepted-task time/cost. Count repeated searches and rerun checks as rediscovery only when their inputs and required fact were unchanged and already supplied. Reads needed to edit current source are useful work. This replaces the current blanket classification of working-set reads as waste with an outcome-based continuation metric.

### October 4 implementation direction (supersedes fact-ranking proposals)

Jev is required for bounded judgments on event deltas, repeated tool observations,
optional tool/skill routing and surplus tool-output chunks. Do not use it to decide
which binding facts survive a checkpoint. Keep arithmetic, quotas, version checks,
mandatory obligations and tool permissions in code. Use the configured generative
companion to write and reconcile structured facts and application-map updates.
Apply the October 2 vendor limitations and the engineering examples verified through
October 3; no inference fallback, no model switching and no comparative rollout gate.
