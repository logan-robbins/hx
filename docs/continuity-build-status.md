# Continuity runtime implementation status

Branch: `codex/continuity-runtime`, created from local `main` at `6c7b94c`.
Specification: [continuity-implementation.md](continuity-implementation.md).
The full specification remains the delivery scope. Existing spec-file deletions predate this work.

| Units | State | Evidence / remaining integration |
|---|---|---|
| P01 durable authority | Implemented; not yet cut over | `src/hx/continuity_store.py`; 19 tests cover transaction rollback, abrupt exit, concurrent writers/CAS, late events, references/GC, and outbox leases. |
| P02a–P02b incremental capture | Pending | Observer, native decoders, thin hooks, controller. |
| P03 bounded companion passes | Pending | Frozen ranges, typed patches, atomic cursor commit. |
| P04 typed progress/facts | In progress | `src/hx/facts.py` validates versioned payloads; compact rendering tested. Progress CLI and companion patches remain. |
| P05 check receipts | Pending | Input/environment identity and completion integration. |
| P06a–P06c shared application map | Pending | Portable records, validated edges, CAS overlays, export/check. |
| P07 context compiler | In progress | Whole-fact budget selection and required-fact overflow implemented; checkpoint/map/pending-tail integration remains. |
| P08 task planning | Pending | Stable task identity, ownership, prerequisites, acceptance. |
| P09 budgets | Pending | Entire request accounting, capability gates, shared parent reserve. |
| P10 Jev | Pending | Bounded client, validated decisions, deterministic fallback. |
| P10b tools | Pending | Catalog, discover/load, verified adapter tool visibility. |
| P11a–P11b selection/prompts | Pending | Record selection, plan review, all roles/companions, QA role. |
| P11c traversal | Pending | Bounded graph traversal and current-task traces. |
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

Verification: `.venv/bin/python -m pytest tests/core/test_continuity_store.py tests/core/test_facts.py -q` — 46 passed. No live adapter integration has been changed or certified yet.
