# Continuity runtime implementation status

Branch: `codex/continuity-runtime`, created from local `main` at `6c7b94c`.
Specification: [continuity-implementation.md](continuity-implementation.md).
The full specification remains the delivery scope. Existing spec-file deletions predate this work.

| Units | State | Evidence / remaining integration |
|---|---|---|
| P01 durable authority | Implemented; not yet cut over | `src/hx/continuity_store.py`; Schema 9; tests cover transaction rollback, abrupt exit, concurrent writers/CAS, late events, references/GC, outbox leases, migrations, and verified artifact slices. |
| P02a incremental capture | In progress | Registered-source observer, durable offsets/provenance, native macOS notifications plus reconciliation, bounded reads/spool, branch filtering, and versioned decoders implemented. Remaining: complete adapter source contracts, gap reconciliation, controller scheduling. |
| P02b adapter capture/control | In progress | `hx capture bind/enqueue` provides session/run ownership, durable delivery acknowledgements, retry deduplication, public native decoding, spool admission, and controller notifications. Shared hook normalization covers all five adapters; automatic registration, producer retry storage, lifecycle wiring, and live-version verification remain. |
| P03 bounded companion passes | In progress | Frozen immutable inputs, selected records, evidence-bound patches, CAS, pending-queue resolution, atomic cursor commit, and idempotent replay implemented. Remaining: route the native companion through this API, bounded retry, map-patch integration. |
| P04 typed progress/facts | In progress | Typed facts and `hx progress --file/--run` implemented: bounded sparse updates, field provenance, task/run ownership, CAS, idempotency, deterministic cursor reduction, and projection outbox. Native prompt deployment and projection consumers remain. |
| P05 check receipts | In progress | `checks.py`/`fingerprints.py`: exact recipes, streamed identities/output, receipt reuse/revalidation, uncertain-request deduplication, timeout/output/background-process failure handling. Planned-unit ledger completion rechecks all assigned receipts and publishes exact outputs atomically. Interrupted-execution recovery, watcher generation guards, native completion wiring, and quota integration remain. |
| P06a portable application map | In progress | `appmap.py`: semantic schema, stable responsibility IDs, portable Git baseline import/export, typed relationships, source anchors, selected reads/cache, and disk-backed batch validation. Fresh-clone reconstruction, stack replacement, stale anchors, branch provenance, and declared config-dependent fact invalidation tested. Automatic source/config watcher wiring remains. |
| P06b shared map updates | In progress | `map_updates.py`, `map_refresh.py`, `map_dependencies.py`: proposal CAS/coalescing, atomic graph changes, isolated overlays, durable bounded source refresh, exact task/fact input bindings, incident-edge/fact/receipt invalidation, and direct-consumer replan queue. Planned source leases are implemented. Remaining: watcher registration, automatic downstream replan scheduling, file-only consumer indexing, native companion transaction integration. |
| P06c map integration | In progress | `hx map check` and deterministic shard export implemented. Concurrent publication protection, merged-source reconciliation, and crash recovery remain. |
| P07 context compiler | In progress | Whole-fact budget selection and required-fact overflow implemented; checkpoint/map/pending-tail integration remains. |
| P08 task planning | In progress | Indexed assignment brief; validated behavior-unit DAG; atomic plan revisions; ready/assign/audit/finish/materialize/stop; repository-wide leases; exact source/receipt outputs; verified integration before consumer admission. Serial ledger/CLI end-to-end coverage. Native dispatch/resume, resource-aware scheduling, automatic mutation-boundary audits, and semantic plan review remain. |
| P09 budgets | Pending | Entire request accounting, capability gates, shared parent reserve. |
| P10 Jev | Pending | Bounded client, validated decisions, deterministic fallback. |
| P10b tools | Pending | Catalog, discover/load, verified adapter tool visibility. |
| P11a–P11b selection/prompts | Pending | Record selection, plan review, all roles/companions, QA role. |
| P11c traversal | In progress | Exact/lexical seeds, bounded adjacent records and interface consumers, required input overflow, current owners and explicit gaps. Full beam/Jev traversal, persisted selection, and current-task traces remain. |
| P11d–P11e lifecycle/preservation | Pending | Quotas, compression/drop, evidence references, GC. |
| P11f compaction | Pending | Advisory timing inside deterministic readiness gates. |
| P11g output reduction | Pending | Pre-delivery adapters, diagnostic preservation, bounded recovery. |
| P11h–P11j QA | Pending | Optional extensions after core; triage, test selection, review. |
| P12 migration/release | Pending | Atomic authority cutover, projections, adapter and recovery verification. |

Implementation uses correctness tests, not A/B experiments or comparative rollout gates.
No model switching or personal/cross-task episodic memory is introduced.

Compact continuation uses typed facts, rendered once by code. Preserve exact conditions,
negation, commands, identifiers, and unresolved obligations; put repeated provenance behind
stable IDs. The examples are non-exhaustive. The [language guide](continuity-language.md) covers
complete propositions, conditions, causes, dependencies, verification, uncertainty, and
remaining obligations; findings retain ordinary concise prose for other relationships. Required text must never be blindly truncated.

Verification: `.venv/bin/python -m pytest tests/core/test_unit_execution.py tests/core/test_planning.py tests/core/test_map_refresh.py tests/core/test_map_updates.py tests/core/test_appmap.py tests/core/test_completion_inputs.py tests/core/test_checks.py tests/core/test_evidence.py tests/core/test_native_capture.py tests/core/test_progress.py tests/core/test_observer.py tests/core/test_passes.py tests/core/test_continuity_store.py tests/core/test_facts.py tests/core/test_cli.py -q` — 285 passed. Final ownership/materialization adjustments are also covered by the targeted planning/execution rerun. No live adapter capture/companion path has been cut over or certified yet.

The [runtime contracts](continuity-runtime.md) document native binding/enqueue, typed
progress, and check receipts, including retry identities, bounded input/output, sparse
updates, source/environment applicability, and remaining wiring.
The [application-map contract](application-map.md) documents portable records, source
validation, selected lookup, import/export, and remaining shared-write requirements.
The [planning runtime](planning-runtime.md) documents focused assignments, dependency
proofs, lease collisions, successful integration, and the remaining native launch boundary.

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
disk-backed temporary storage, and no database memory mapping. The 64 MiB spool is disk storage.

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
hardening. Connect these plan/ownership/proof gates to native dispatch and resume,
compose focused task packets, and route the controller/companions through existing
capture/pass APIs. Schedule independent work only when prerequisites and available
host capacity permit it; do not turn every dependency edge into more searching.
Follow with export recovery, watcher coverage, and remaining retention/budget work.
Preserve
the legacy authority until the coordinated P12 migration; library tests alone do not prove
end-to-end adapter compatibility.
