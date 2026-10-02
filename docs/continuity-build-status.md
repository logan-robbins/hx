# Continuity runtime implementation status

Branch: `codex/continuity-runtime`, created from local `main` at `6c7b94c`.
Specification: [continuity-implementation.md](continuity-implementation.md).
The full specification remains the delivery scope. Existing spec-file deletions predate this work.

| Units | State | Evidence / remaining integration |
|---|---|---|
| P01 durable authority | Implemented; not yet cut over | `src/hx/continuity_store.py`; Schema 15; tests cover transaction rollback, abrupt exit, concurrent writers/CAS, late events, references/GC, outbox leases, checkpoint request replay, migrations, and verified artifact slices. |
| P02a incremental capture | In progress | Registered-source observer, durable offsets/provenance, native macOS notifications plus reconciliation, bounded reads/spool, branch filtering, and versioned decoders implemented. Remaining: complete adapter source contracts, gap reconciliation, controller scheduling. |
| P02b adapter capture/control | In progress | `hx capture bind/enqueue` provides session/run ownership, durable delivery acknowledgements, retry deduplication, public native decoding, spool admission, and controller notifications. Planned-hook routing binds sessions/children, stores bounded public deliveries before sending, and retries through the root observer. All five installers persist immutable launch contracts and bake callback identity into arguments; Pi snapshots its contract at extension load. Late evidence remains tied to the original run; queues/gaps gate context, completion, and downstream prerequisites. Fresh planned launch now creates private sessions, observes startup, and confirms exact submission through installed request hooks. Full native event/source coverage, late/gap reconciliation, shutdown/resume barriers, and model execution verification remain. Installed CLI versions/options and the real Pi extension loader were checked separately. |
| P03 bounded companion passes | In progress | Frozen immutable inputs, selected records, evidence-bound patches, CAS, pending-queue resolution, atomic cursor commit, and idempotent replay implemented. Remaining: route the native companion through this API, bounded retry, map-patch integration. |
| P04 typed progress/facts | In progress | Typed facts and `hx progress --file/--run` implemented: bounded sparse updates, field provenance, task/run ownership, CAS, idempotency, deterministic cursor reduction, and projection outbox. Native prompt deployment and projection consumers remain. |
| P05 check receipts | In progress | `checks.py`/`fingerprints.py`: exact recipes, streamed identities/output, receipt reuse/revalidation, uncertain-request deduplication, timeout/output/background-process failure handling. Planned-unit ledger completion rechecks all assigned receipts and native delivery/gap readiness, then publishes exact outputs atomically. Interrupted-execution recovery, watcher generation guards, native completion wiring, and quota integration remain. |
| P06a portable application map | In progress | `appmap.py`: semantic schema, stable responsibility IDs, portable Git baseline import/export, typed relationships, source anchors, selected reads/cache, and disk-backed batch validation. Fresh-clone reconstruction, stack replacement, stale anchors, branch provenance, and declared config-dependent fact invalidation tested. Automatic source/config watcher wiring remains. |
| P06b shared map updates | In progress | `map_updates.py`, `map_refresh.py`, `map_dependencies.py`: proposal CAS/coalescing, atomic graph changes, isolated overlays, durable bounded source refresh, exact task/fact input bindings, incident-edge/fact/receipt invalidation, and direct-consumer replan queue. Planned source leases are implemented. Remaining: watcher registration, automatic downstream replan scheduling, file-only consumer indexing, native companion transaction integration. |
| P06c map integration | In progress | `hx map check` and deterministic shard export implemented. Concurrent publication protection, merged-source reconciliation, and crash recovery remain. |
| P07 context compiler | In progress | `context_packets.py` and explicit `hx compose --run`: immutable assignment packets, selected current facts, required map inputs, exact recipes, pending evidence/capture lag, source-aware replay, acknowledgements and predecessor retirement. Native turn-boundary barriers, persona placement, adapter/controller composition and full-request budget wiring remain. |
| P08 task planning | In progress | Indexed assignment brief; validated behavior-unit DAG; atomic plan revisions; ready/assign/audit/finish/materialize/stop; repository-wide leases; exact source/receipt outputs; verified integration before consumer admission. Serial ledger/CLI end-to-end coverage. Private launch preparation freezes current instructions and context, excludes old homes/memories, and binds hooks to the shared authority. Explicit native dispatch starts one unique session and never resends an uncertain submission. Owned-process shutdown now records kernel identities and terminates observed descendants through bounded, replayable steps. Background/source reconciliation, safe lease release and resume, automatic scheduling, resource-aware admission, mutation-boundary audits, and semantic plan review remain. |
| P09 budgets | Pending | Entire request accounting, capability gates, shared parent reserve. |
| P10 Jev | Pending | Bounded client, validated decisions, deterministic fallback. |
| P10b tools | Pending | Catalog, discover/load, verified adapter tool visibility. |
| P11a selection | Pending | Semantic record selection and plan review. |
| P11b prompts | In progress | Audience-aware canonical prompts for Partner/workers/companions/subagents; backend/frontend/release/QA policies; resolved identity; preserved operator policy; no personal-memory import; delivery manifests; verified context-channel packet composition. Shared legacy compiler also fixes unresolved placeholders and copied-role duplication. Native installation acknowledgement, companion tool enforcement, instruction-skill migration, and complete request-budget accounting remain. |
| P11c traversal | In progress | Exact/lexical seeds, bounded adjacent records and interface consumers, required input overflow, current owners and explicit gaps. Full beam/Jev traversal, persisted selection, and current-task traces remain. |
| P11d–P11e lifecycle/preservation | Pending | Quotas, compression/drop, evidence references, GC. |
| P11f compaction | Pending | Advisory timing inside deterministic readiness gates. |
| P11g output reduction | Pending | Pre-delivery adapters, diagnostic preservation, bounded recovery. |
| P11h–P11j QA | Pending | Optional extensions after core; triage, test selection, review. |
| P12 migration/release | Pending | Atomic authority cutover, projections, adapter and recovery verification. |

Implementation uses correctness tests, not A/B experiments or comparative rollout gates. CLI stand-ins exercise repeatable failure and transport cases; they are test fixtures, never production runtimes or evidence of live model behavior.
No model switching or personal/cross-task episodic memory is introduced.

Compact continuation uses typed facts, rendered once by code. Preserve exact conditions,
negation, commands, identifiers, and unresolved obligations; put repeated provenance behind
stable IDs. The examples are non-exhaustive. The [language guide](continuity-language.md) covers
complete propositions, conditions, causes, dependencies, verification, uncertainty, and
remaining obligations; findings retain ordinary concise prose for other relationships. Required text must never be blindly truncated.

Verification: the launch-contract, producer/capture, ledger, packet, unit execution,
prompt compiler, and CLI suites passed 156 tests; the optional installed-Pi test was
skipped in that run and passed separately against Pi 0.84.3. A separate 99-test run
covered generated hook commands and installation for all five adapters, including
callbacks with cleared environments and callbacks after worker reuse. Actual
installed CLI version/help and launch-option parsing checks also passed for all five.
No model request or worker fleet was started. Native process launch/resume, full
source coverage, prompt installation acknowledgement, and coordinated cutover
remain unverified.

The [native interface report](native-interface-verification.md) records actual installed
CLI versions, option parsing, immutable hook binding, and the installed Pi loader check.
These results are separate from model execution or full fleet certification.

Private native preparation adds a Schema 13 reservation and selected configuration
snapshot. Its launch/prompt/hook/producer/ledger/packet checks passed 123 tests
(one optional installed-Pi loader test skipped). The expanded preparation and
five-adapter install/start suites passed 114 tests, including concurrent preparation,
changed packet/configuration refusal, late correction refusal, and shared-authority
callbacks from private homes. No model request was made. Fresh dispatch and startup/submission observation are
now connected through `native_controller.py`; controlled recovery remains pending.

The [runtime contracts](continuity-runtime.md) document native binding/enqueue, typed
progress, and check receipts, including retry identities, bounded input/output, sparse
updates, source/environment applicability, and remaining wiring.
The [application-map contract](application-map.md) documents portable records, source
validation, selected lookup, import/export, and remaining shared-write requirements.
The [planning runtime](planning-runtime.md) documents focused assignments, dependency
proofs, lease collisions, successful integration, and the remaining native launch boundary.
The [planned context contract](continuity-runtime.md#planned-assignment-context) documents
the explicit ledger compose path, immutable replay, pending tails, and current-task isolation.
The [prompt runtime](prompt-runtime.md) documents role/audience compilation, operator-policy
migration, source/identity validation, channel placement, and remaining native activation.

## Current capture and evidence commands

`hx observe register --run RUN --stream STREAM --path PATH --decoder DECODER --session SESSION`
registers a source for an existing active ledger run. `hx observe serve` owns one resident
observer per root; `hx observe drain` performs a bounded drain; `hx observe status` exposes
committed offsets, pending tails, gaps, and observed usage. The observer has native vnode
notifications on macOS/BSD and a five-second registered-source reconciliation fallback.
Tree transcripts require explicit `--branch-ids` ancestry. Unknown formats stop with a gap.

`hx evidence EVENT --offset N --limit N` returns a bounded page with its identity, encoding,
source hash, and next offset. Artifact chunk hashes verify only the requested chunks;
ordinary reads do not scan the full artifact. Schema upgrades index older artifacts in
one explicit bounded-chunk migration pass.

Normal companion inputs contain new/pending events plus selected current facts. Broader
state reads belong to explicit batch compaction/compression. Source-content verification
streams declared files through a hash without loading their text into the model; a
watcher-backed source-version cache remains part of the source/map integration.

## Host RAM

One observer serves the root. Capture yields after a 256 KiB scheduling quantum or 128
records; a complete record can exceed that quantum, up to the existing 8 MiB record
bound. Oversized records stop with explicit backpressure and require reduction. JSON
decoding still allocates the current record; this is not an 8 MiB process-memory limit.
Decoded records are released before the next read. Pass preparation fetches at most 32
event headers and loads only bodies small enough for the remaining packet budget.
Artifact verification streams 64 KiB chunks; chunk indexing uses views instead of
copying the full artifact. SQLite uses a 2 MiB page-cache target per connection,
disk-backed temporary storage, and no database memory mapping. The 64 MiB capture spool and
additional 64 MiB/1,024-record producer retry queue are disk storage. The observer retries
at most 16 binding heads per quantum; blocked bindings do not monopolize another worker's capture. Queue bodies are loaded one at a time.

An isolated macOS Python process captured 64 records from a 1,058,934-byte fixture in
four batches and prepared a 5,910-byte context packet. Its measured peak RSS was
25.77 MiB (`resource.getrusage`), including interpreter/imports and fixture creation.
This checks the small fixture only; fleet totals and worst-case native records are not
certified. Tests run serially; no live worker fleet or embedding model was started.
Collision tests use two short-lived SQLite writer threads inside a single test process.
Map proposal inputs are limited to 256 KiB, 16 operations, and 64 read dependencies;
incident-edge validation streams indexed metadata without loading endpoint bodies.

A separate isolated check captured 262,144 output bytes on disk. Its parent-process peak
RSS was 27.05 MiB; the largest child-process peak was 15.00 MiB. These are separate
`getrusage` measurements, not a combined fleet-memory bound. Streamed artifact installation
and source fingerprinting have tests that reject reads larger than 64 KiB.

Priority: functional completion of precise behavior units before additional NFR
hardening. Connect these plan/ownership/proof gates and immutable task packets to
native dispatch and resume, align persona placement, and route the controller/companions through existing
capture/pass APIs. Schedule independent work only when prerequisites and available
host capacity permit it; do not turn every dependency edge into more searching.
Follow with export recovery, watcher coverage, and remaining retention/budget work.
Preserve
the legacy authority until the coordinated P12 migration; library tests alone do not prove
end-to-end adapter compatibility.

Fresh controller integration: actual installed Muse 1.4.2 startup/request hooks
passed with its echo provider; Pi 0.84.3 loaded the extension and delivered request
and tool-result callbacks. The combined controller/contract suite passed 22 tests
including those installed-runtime checks. This exposed and fixed Muse’s unsupported
`-m` flag and deferred-startup ordering. Process shutdown/background-work barriers
and native unit completion remain unfinished; active sessions retain assignment
leases. All model-backed fleet verification remains pending.

The preparation/controller, adapter install/start, and legacy lifecycle regression
suites passed 178 tests with the optional installed-Muse check skipped in that run.
Installed-runtime checks are run separately with explicit executable/loader paths.
The controller, hook-contract, producer, unit execution, context packet, and ledger
suites subsequently passed 106 tests with both installed-runtime checks enabled.

Tool admission/drain integration: planned tool callbacks now register call ownership
before execution and settle matching calls atomically with captured results. The
Partner can close admission with `hx launch ... --drain`; concurrent admissions
serialize with that transition, and retries preserve the same session and leases.
Stable child/session bindings prevent one child from settling another child's call.
Planned Pi children explicitly load the frozen hook extension from their private
capsule. Active-call and child counts remain separate from process-quiescence proof.
Controlled process shutdown, background-work reconciliation, resume, and native unit
completion are still unfinished. The full delivery scope above remains active.

Validation for tool admission/drain: 240 tests passed across native control,
preparation, capture/producer/contracts, ledger/unit execution, and five-adapter
install/start regressions, with the installed Muse and Pi checks enabled. After
moving external pane queries outside SQLite's writer transaction, the final
admission/drain suite passed 22 tests, including installed Pi parent/child callback
checks. Tests ran serially without model calls or a live fleet. Pi extension syntax
and `git diff --check` also passed.

Owned-process shutdown integration: `hx launch ... --shutdown` records identities
before signalling, stops discovered descendants, and ends at
`stopped_unreconciled`. macOS uses audit-token signals; a PID or session-name match
cannot substitute for the original process instance. A small waiting shell keeps
tmux from automatically resuming the stopped native executable. Freeze batches
exclude ledger writers, and conditional state updates preserve concurrent/restarted
controller progress. Termination still retains leases: historical detached work,
remote operations, source/capture coverage, completion handoff, and resume remain
unfinished. The full specification remains the delivery scope.

Validation for owned-process shutdown: 161 tests passed across shutdown/process
identity, native controller/tools/preparation/contracts, ledger/unit execution, and
legacy lifecycle behavior. Installed Muse echo-provider shutdown and Pi loader
checks were enabled. The tests include interrupted signals, concurrent controller
advances, stale kernel identities, unrelated processes, a child in a separate OS
session, and a replacement tmux session. Tests ran serially without model calls.
An isolated waiting-shell measurement reported 1,920 KiB RSS on this host; that is
one shell's observed RSS, not a fleet or worst-case bound. No new watcher or model
process is introduced by the shell supervisor.

Capture readiness now shares a bounded metadata snapshot across context packets,
unit completion, and downstream prerequisite admission. Late transcript appends
block dependent work; same-size rewrites outside the fixed tail fingerprint leave
a persistent reconciliation gap. Planned Pi parent/child extensions relay public
completed messages and usage through the durable producer. Thinking/signatures are
excluded before the hook pipe; messages do not bypass tool admission or settlement.
Automatic source registration, complete adapter coverage, reconciliation, native
completion, and the remaining specification units above remain unfinished.

Validation for shared source readiness and Pi completed-message capture: 153 tests
passed across observer, context packets, unit execution, producer/capture, hook
contracts, native tools, and Pi adapter regressions. Installed Pi 0.84.3 loader
checks exercised frozen callback identities and parent/child messages; a pipe
assertion verified that private fields were removed before transport. Tests ran
serially without model calls. Extension syntax and `git diff --check` also passed.

Claude source registration now connects reported main/child transcripts to the
shared observer from planned lifecycle hooks. Sources retain original launch,
private-home, session, and actor identity; registration reads no transcript body.
Incremental capture excludes main-session sidechains and detects ancestry changes;
fork/resume reconciliation remains explicit and unfinished. Files outside the
original home, replacement symlinks/homes, non-regular files, and conflicting
registrations cannot silently substitute evidence. Schema 15 adds a run/source
index while preserving existing offsets and generations. Other adapters retain
their existing direct-event paths pending verified native source contracts.

Validation for Claude source registration: 187 tests passed across scoped sources,
observer, ledger migrations, context packets, unit execution, native capture and
producer, hook contracts, preparation, and controller behavior. Installed Muse
echo-provider and Pi loader checks were enabled. Claude source cases use controlled
JSONL replay and the shared hook entrypoint; they do not prove native emission
or model execution. Tests ran serially, with two-connection collision cases inside
the source suite. `git diff --check` passed. Full adapter coverage, branch/gap
reconciliation, and native completion remain unfinished.

Codex main-thread source registration now uses the version-pinned
`codex-rollout-0.156.1` decoder. It preserves public message/tool/usage evidence,
including complementary command exit status and namespaced tool definitions,
without copying private reasoning or repeated native instruction frames into
task memory. Source metadata must identify the original session and verified CLI
version; unknown records and continuation changes remain gaps. The installed CLI
was exercised with a local provider fixture, real hooks, one fixed command, and
its actual rollout. Child ancestry, further native versions, complete Meta/Grok
source contracts, reconciliation, and the remaining specification stay open.

Validation for Codex source registration: 194 tests passed across rollout/source
capture, observer, context packets, unit execution, hook contracts, producer,
preparation, controller, and Codex adapter behavior. After tightening discovered
tool and web-action validation, all 15 Codex rollout tests passed, including the
installed CLI check. Installed Muse echo-provider and Pi loader checks were also
enabled in the broader suite. These overlapping counts are not additive. No paid
model or model fleet was run. `git diff --check` passed.
