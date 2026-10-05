# Continuity runtime status

Branch: `codex/continuity-runtime`. Delivery: [draft PR #1](https://github.com/logan-robbins/hx/pull/1), never merged to `main`.
Specification: [continuity-implementation.md](continuity-implementation.md).
Existing deletions under `spec/` predate this work and are excluded from the PR commits.

## Functional application loop

Planned assignments now run through one connected path:

`goal/plan → map slice + current checkpoint → native worker → bounded tools/capture → Jev deltas → companion patches → refreshed context → acceptance checks → output publication`

The root service starts at dispatch and acknowledges readiness. It serializes companion
execution, fairly schedules streams/completions, watches indexed map sources and follows
ordinary commits in the same worktree. Branch changes and resets require explicit rebinding.
Late events remain eligible; failed unchanged windows do not cause repeated inference.
An explicit retry creates a fresh attempt. Kernel identity and accepted patch receipts govern
interruption recovery; ambiguous ownership remains unresolved.

| Area | Implemented behavior |
|---|---|
| Application map | Portable `.hx/map` records describe responsibilities, behaviors, interfaces, dependencies and checks. Git baselines and worktree overlays remain distinct. Shared writes use version CAS; identical updates coalesce and conflicting updates roll back. Source changes invalidate only indexed consumers. Fresh source anchors reach the companion without another repository search. |
| Goal assignment | Validated behavior-unit DAGs, exact prerequisite outputs, write leases, declared checks and bounded map briefs. Partner prompts keep current owners, blockers and next assignable work. `hx loop --status` reads compact headers; ordinary success does not require reopening logs or receipts. |
| Jev | Required real TypeSafe API, pinned `jev-1.13.0`. Direct judgments on new-event deltas, repeated tool observations, optional tools and surplus output. No fact-survival ranking, generation, model switching or inference fallback. Requests ≤4,000 bytes, ≤16 questions, 500 ms, no automatic transport retry; current-task decisions are cached and bounded. |
| Companion | Configured native Claude, one root model slot, no hooks or independent log scans. Frozen current-task pass; only scoped evidence reads and structured patch submission. Map/fact/cursor changes share one transaction. |
| Context and memory | Current goal/constraints/cursor, selected current facts, exact commands and scoped map inputs. Planned boundaries enforce file size and read/search bounds. Compression erases obsolete record payloads; explicit drops leave version tombstones. Completed tasks erase worker facts, progress detail and semantic/read caches. |
| Inspection efficiency | ≤160 lines per source read; ≤80 scoped search results. An unchanged successful range cannot be reread in the same checkpoint. Changed files, errors and fresh contexts remain readable. Common noisy shell inspections route through bounded output capture. These gates do not sandbox arbitrary programs. |
| Output | `hx tool-exec` executes a request once, captures ≤1 MiB and returns ≤4 KiB of selected verbatim output with status, diagnostics and recovery addresses. Jev only judges surplus chunks. A failed reduction can be retried without repeating the command. |
| Tools | Small per-adapter catalog, Jev discovery and exact-ID selection. Required tools survive. Claude can select its launch tools before dispatch; live expansions report `pending_restart`. Pi updates real active schemas through its public API. Grok TUI, Codex and Muse remain advisory; no native schema savings are claimed. |
| Compaction | Current checkpoint before reset; forced continuation preserves unresolved corrections/gaps without acknowledging them. Codex receives `compact_prompt`; Claude receives compact instructions; Pi can use the checkpoint pointer as its summary. No synchronous inference in compaction hooks. |
| Completion | Worker commits changes and requests `hx complete done`. Runtime waits for turn/tool/child/capture/companion drain, runs declared checks, stops managed native execution, publishes exact outputs and releases leases. Failure resumes the same worker once. Publication replay recovers after a crash. |

## Verification

Tests use isolated roots and serial execution. Local provider fixtures exercise installed
native CLI transport; they do not establish model extraction accuracy. The installed Pi
SDK check verifies actual active tool definitions after deferred loading without invoking
a model. The native completion integration covers turn end, pending tool refusal, checks,
shutdown, publication, release and publication replay.

Latest verification (October 4; overlapping counts are not additive):

- Broad regression: 390 passed, 2 optional checks skipped. The historical user-home
  guard failed because the current Claude configuration differs from the September 21
  pre-build manifest. That baseline and the user's configuration were left untouched.
- A separate before/after manifest check around six isolated native/API tests passed
  and confirmed that those tests did not change the current Claude home.
- Final focused regression after the publication barrier and startup-fixture fix:
  81 passed, 1 optional installed-runtime check skipped.
- Portability/fixture regression: 261 passed after reconciling QA packaging and
  board snapshots, pinning the native-install fixture, and preserving full launch
  argv and session identity in diagnostics. The local installed-Claude deploy-doc
  test requires an allowlisted version; this host now has 2.1.289, outside that list.
- Python compilation and `git diff --check` passed.

Live Jev checks used the operator's named dotenv key without printing or persisting it:

- Event delta: 405 input / 40 output tokens, 360.43 ms; identical replay reused the cache.
- Tool discovery: 366 input / 77 output tokens, 481.24 ms; identical replay reused the cache.

No paid native worker fleet was started. No default production root exists on this host.
The [interface report](native-interface-verification.md) distinguishes native transport,
public API support and unverified provider behavior.

## Scope beyond the functional loop

The functional loop above is implemented. These broader specification items remain
outside its current capabilities and must not be presented as completed release work:

- Complete provider-request/cost accounting and parent reserves; full source coverage for
  every vendor/child/remote operation; recovery of ownership that lacks a process identity.
- Claude live tool expansion/restart, MCP/skill catalog discovery, and adapters whose native
  schema filtering or custom compaction surfaces have not been verified.
- Coordinated legacy-authority migration, projection consumers, map export crash recovery,
  automatic downstream replanning, and cancellation/raw-event/pass artifact retention.
- Optional QA triage, test-selection and review extensions after core operation.

Known boundaries remain explicit: managed completion covers admitted operations and observed
process descendants; it cannot certify arbitrary untracked remote jobs. Vendor-provided
system context and generated summaries are distinct from hx's bounded files/checkpoints.
