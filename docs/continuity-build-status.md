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
| Goal assignment | `hx plan draft --out` compiles current map paths, invariants, check recipes and outputs into reviewable assignments with specific unknowns. `hx plan replan --out` refreshes affected bindings and downstream output versions while preserving acceptance and ownership. Validated behavior-unit DAGs, exact prerequisite outputs, write leases, declared checks and bounded map briefs. Partner prompts keep current owners, blockers and next assignable work. `hx loop --status` reads compact headers; ordinary success does not require reopening logs or receipts. |
| Jev | Required real TypeSafe API, pinned `jev-1.13.0`. Direct judgments on new-event deltas, repeated tool observations, optional tools and surplus output. No fact-survival ranking, generation, model switching or inference fallback. Requests ≤4,000 bytes, ≤16 questions, 500 ms, no automatic transport retry; current-task decisions are cached and bounded. |
| Companion | Up to four new observed map identities per pass; cached runtime discovery anchors; bounded optional neighbors with unchanged outside relationships preserved. Configured native Claude, one root model slot, no hooks or independent log scans. Frozen current-task pass; only scoped evidence reads and structured patch submission. Map/fact/cursor changes share one transaction. |
| Context and memory | Current goal/constraints/cursor, selected current facts, exact commands and scoped map inputs. Planned boundaries enforce file size and read/search bounds. Compression erases obsolete record payloads; explicit drops leave version tombstones. Completed tasks erase worker facts, progress detail and semantic/read caches. |
| Application learning | Completion publishes current findings and bounded retrieval terms. Integration transfers matching findings with collision checks; scoped Git publication carries source-bound vocabulary into fresh clones. Older record versions lose their retrieval hints. |
| Inspection efficiency | ≤160 lines per source read; ≤80 scoped search results. An unchanged successful range cannot be reread in the same checkpoint. Changed files, errors and fresh contexts remain readable. Exact bounded searches reuse small unchanged results across contexts/workers in the same worktree; partial output never establishes absence. The root search cache is capped at 256 entries. Common noisy shell inspections route through bounded output capture. These gates do not sandbox arbitrary programs. |
| Output | `hx tool-exec` executes a request once, captures ≤1 MiB and returns ≤4 KiB of selected verbatim output with status, diagnostics and recovery addresses. Jev only judges surplus chunks. A failed reduction can be retried without repeating the command. |
| Tools | Small per-adapter catalog, Jev discovery and exact-ID selection. Required tools survive. Claude can select its launch tools before dispatch; live expansions checkpoint and restart the same assignment, then confirm `loaded`. Pi updates real active schemas through its public API. A paged MCP/skill catalog exposes one selected schema or skill body through a task-scoped shell facade. Grok TUI, Codex and Muse remain advisory; no native schema savings are claimed. |
| Compaction | Planned hooks and incremental capture enforce the model threshold; the root service waits for a classified, idle boundary, then submits one reset and waits for its startup acknowledgement. Model output caps reach Claude, Grok and Pi; native autocompaction settings reach Claude, Codex and Pi. The companion maintains current state for clear and restoration without a second conversation summary. Partner restoration omits worker investigation and personal-memory lookup; engineering/QA retain task-specific continuation. Bounded Claude reset state is attached through `SessionStart.additionalContext`. Forced continuation preserves unresolved corrections/gaps without acknowledging them. Codex receives `compact_prompt`; Claude receives compact instructions; Pi can use the checkpoint pointer as its summary. No synchronous inference in compaction hooks. |
| Completion | Worker commits changes and requests `hx complete done`. Runtime waits for turn/tool/child/capture/companion drain, runs declared checks, stops managed native execution, publishes exact outputs and releases leases. Failure resumes the same worker once. Publication replay recovers after a crash. |

## Verification

Tests use isolated roots and serial execution. Local provider fixtures exercise installed
native CLI transport; they do not establish model extraction accuracy. The installed Pi
SDK check verifies actual active tool definitions after deferred loading without invoking
a model. The native completion integration covers turn end, pending tool refusal, checks,
shutdown, publication, release and publication replay.

Latest verification (October 5; overlapping counts are not additive):

- Token controls/adapter recovery regression: 470 passed, 7 optional checks skipped.
- Final catalog/export/retention/schema regression: 59 passed; final retention/token checks: 8 passed.
- Packaging text contracts: 71 passed. This fixes the previous PR head's sole Linux/macOS
  CI failure: the stale assertion for retired personal-memory/sub-task instructions.
- Installed Pi SDK: one passing check of actual deferred tool selection and provider-payload
  output caps, without a model request. The restart test uses real tmux and generated hooks
  with an isolated CLI fixture; it preserves assignment ownership and rejects retired hooks.

- Application-learning and generated-goal regression: 386 passed, 2 optional checks skipped.
  Includes discovery, completion publication, fresh-clone vocabulary reuse, scoped write
  ownership, replanning, current-source search reuse and active-owner map repair.
- Final integration/planning follow-up: 55 passed, including refusal to publish a
  source-materialization receipt when application-map transfer encounters a collision.
- Companion-reset CI at `32f96e8` passed on Linux and macOS before the application-learning
  changes in this update.

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
- Companion reset follow-up: 95 passed across compaction, Partner seams, composition,
  prompt budgets and readiness; 24 passed for revised prompt contracts and Partner reset
  behavior; 74 passed, 6 optional checks skipped for native controller/tools/companion
  integration and prompt contracts. Counts overlap.
- Baseline CI at `8f725cc`: Linux 1,820 passed / 25 skipped; macOS 1,822 passed /
  23 skipped; packaging and Claude-home isolation checks passed on both platforms.
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

- Coordinated legacy-authority migration and projection consumers, and retention for
  cancelled assignments that may still be resumed.
- Optional QA triage, test-selection and review extensions after core operation.

Provider-cost accounting and parent dollar reserves are not part of the design: the operator
requires hook limits, context thresholds/autocompaction and model output caps. Schema 19
adds restart generations without resetting task state or releasing write leases. A supervisor
records its process-instance identity before spawning the native agent. Full map export has
a recoverable journal, and closed-task raw events/pass inputs retire in bounded batches while
current application citations and check artifacts remain usable.

Known boundaries remain explicit: managed completion covers admitted operations and observed
process descendants; it cannot certify arbitrary untracked remote jobs. Vendor-provided
system context and generated summaries are distinct from hx's bounded files/checkpoints.
