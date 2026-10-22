# Fleet throughput plan: ruflo, orca and headroom against hx as coded

**Date:** 2026-10-19 · **Baseline:** `main` @ `d076a7d` · **Method:** six Opus 5.5 inspections run
by the author's session: three over `src/hx` (parallelism, graph/search, token economy) and three
over the external repositories' source (not their READMEs). Every "hx has X" below cites
`src/hx/<file>.py:<line>` at `d076a7d`; every external claim cites the inspected upstream file.
Nothing external was executed. The one experiment run locally used hx's own `index_repo`,
`code_freshness.scope` and `code_index.execute` on a throwaway two-worktree repository in the
session scratchpad. hx docs were used only as a map of where to look; status comes from code.

GitHub trending on 2026-10-19 (fetched twice, once as HTML): ruvnet/ruflo #5 (76,988 stars,
v3.57.0), stablyai/orca #6 (97,888 stars, v1.4.214), headroomlabs-ai/headroom #7 (76,104 stars,
v0.41.0). The premise holds.

This document supersedes the two untracked drafts of the same day
(`competitive-audit-ruflo-orca-headroom.md`, `fleet-systematization-audit.md`), which were
synthesised from reports rather than from code and carried three claims the code refutes
(§2, marked ✗).

## 0. Verdict

Nobody has a scheduler, a code graph or a reuse gate worth copying. hx is ahead of all three on
graph, admission proofs, contract-first dependencies and companion tiering. What the externals
contribute is narrow and concrete: from **headroom**, four stdlib-sized compression patterns and
one architecture rule about the provider prompt cache; from **orca**, five host-protection
mechanisms (pre-warmed checkouts, restore admission, process-growth breaker, stale-base refusal,
pointer injection); from **ruflo**, three disciplines (writer/reader split per level, a user-wide
launch ledger with a quota breaker, idempotent job keys). Everything else in those repos is
either marketing their own audits contradict, or things hx already does better.

The real finding is internal. Read against a sustained **50–60 concurrent engineering agents**,
the code has fourteen defects and gaps (§3) that no external repo would fix, and the worst are
correctness, not throughput: all worktrees of one repository share one graph namespace that
follows whichever worktree indexed last, so at width ≥ 2 `hx code fresh`, `--expect absent` and
`--at HEAD` answer for the wrong tree; every hook invocation opens the ledger with a writer lock,
so 60 agents' tool calls contend on one SQLite file and a lock timeout *denies the tool*; the
context seam cannot fire without the remote Jev service; the loop runs acceptance checks (up to
3600 s) and companion passes (up to 60 s) inline, one per tick.

The plan (§5) therefore fixes correctness at width first (P0), then adds the dispatch fabric and
the graph-side lane machinery (P1), then the token cascade (P2). Every item is split into
**mechanism** (hx core, once), **profile** (data a project sets at install), and **project script**
(generators and queries hx calls but never contains), per §4.1, so the next application tunes a
profile instead of re-deriving a procedure.

Three principles the author restated on 2026-10-19 shape every item (§4.2–4.4): **Claude Code
is the default harness** and the other flavors are adapters that must not shape core
mechanisms; **no regex as a mechanism**, structured sources and model classification instead;
and **the companions are scripts, not agents**, with a seam policy that clears everything when
it must and otherwise exploits the moments the prompt cache is already cold to install a
targeted context for free.

## 1. The three externals, as their source has them

### 1A. Parallelization and dispatch

| | ruflo | orca | headroom |
|---|---|---|---|
| What dispatches | MCP `swarm_init`/`agent_spawn` write JSON records only (`cli/src/mcp-tools/swarm-tools.ts:324`, `agent-tools.ts:363`); fan-out is Claude Code's own Task tool; headless pool `maxConcurrent ?? 2` (`headless-worker-executor.ts:674`) under a user-wide budget `maxConcurrentGlobal: 1, maxLaunchesPerHour: 2` (`global-ai-budget.ts:42-47`); AI workers off by default. The one DAG scheduler is a separate bin (`v3/@claude-flow/codex/.../orchestrator.ts:402-483`): Kahn levels with level barriers, `maxConcurrent` 4, `maxWriters` 2. | Coordinator loop, poll 2 s, `MAX_CONCURRENT_DEFAULT = 4`, one terminal per tick, all workers in **one** worktree, no decomposition, no merge phase ever assigned (`coordinator.ts:35-36,146,252`). Prepared-checkout pool `LIMIT = 3`, TTL 5 min, refilled only in bursts (`worktree-create-preparation-pool.ts:46-47`). Host ledger 256 PTYs per uid (`host-terminal-ledger.ts:22-27`); spawn admission token bucket for *restores* (`host-spawn-admission.ts:133-141`); process-growth breaker 1500 descendants / 40 per s (`process-growth-breaker.ts:22-27`); stale-base refusal at 20 commits (`coordinator-stale-base-flag.ts:4`). | Intra-request only: `ThreadPoolExecutor` of `HEADROOM_COMPRESS_WORKERS=4` over cache-miss messages (`content_router.py:243-247`). |
| Claims / conflicts | `claims_steal` etc. are unlocked JSON read-modify-write; `expiresAt` never enforced (`claims-tools.ts:14`). | None. `worker_done --files-modified` is stored, never checked (`lifecycle-reconciliation.ts:270-284`). | n/a |
| Consensus / topology | Raft/Byzantine live in `v3/@claude-flow/swarm`, not a dependency of the CLI; CLI "consensus" is a vote count in JSON (`coordination-tools.ts:486-493`). | n/a | n/a |
| Refuted marketing | "workflow_execute real dependency graph" walks steps in order (`workflow-tools.ts:409`); "Redis worker queue" has no Redis import. | README "fan one prompt across five agents, merge the winner": no code found under `src/`. | n/a |
| **Take** | writer/reader split with readers exempt; user-wide launch ledger with 429/quota breaker and receipts; job key `sha256(repo, HEAD, spec)`; process-group kill. **Not** level barriers (a ready-queue beats them). | pre-warmed sparse checkouts via `worktree move -f -f` + `reset --hard`; restore token bucket; growth breaker; stale-base refusal that does not consume retries; one-line pointer injection instead of pasting bodies; shell precheck gate per node. **Not** hibernation/resume (re-bills the context). | nothing |

### 1B. Code graph, search, reuse before build

| | ruflo | orca | headroom |
|---|---|---|---|
| Graph | File-level regex import graph, JS/TS only, with cycle/MinCut/Louvain, reachable only from `ruflo analyze` (`ruvector/graph-analyzer.ts:224,349`); "AST analyzer" is regex plus brace counting (`ast-analyzer.ts:53-58`); `kg-extract` is an LLM skill; `memory-graph.ts` PageRank is over memory entries. | None: no tree-sitter, LSP, symbol index or vectors (greps empty); bundled ripgrep (`bundled-ripgrep-text-search.ts:33`); FTS5 over past transcripts with `tokenchars '_.-/+'` (`session-search-schema.ts:21,89-91`), exposed to agents as `orca search`. | `CodeGraphWatcher`: watchdog, 2 s debounce, shells to external `codebase-memory-mcp` (`graph/watcher.py:112,222-232`); off by default. |
| Reuse gate | None. `hooks_pre-edit` returns hard-coded `fileExists: true, relatedFiles: []` (`hooks-tools.ts:1040-1042`). Only dedupe: background job key skip. | None enforced; `orca reference find <issue>` maps an issue to existing agents/worktrees, not required before work (`cli/specs/reference.ts:44-45`). | `read_lifecycle.py` replaces a stale Read with a pointer after an edit (`:504-512`). |
| Retrieval ranking | `smart-retrieval.ts`: expansion → RRF k=60 → recency half-life 30 d → MMR λ=0.7 (token-Jaccard) → session round-robin; **opt-in** (`memory-tools.ts:657`). Its own audit: HNSW "150×–12,500×" measured 1.48× (`docs/reviews/intelligence-system-audit-2026-05-29.md:13`). | bm25 over transcripts. | n/a |
| **Take** | nothing for the graph. RRF+MMR recipe only if a retrieval ceiling is ever measured. | the `_.-/+` tokenizer rule for identifier search; an issue-key → active lane lookup before dispatch. | stale-read pointers at seam re-issue. |

### 1C. Token compression and companion models

| | ruflo | orca | headroom |
|---|---|---|---|
| Mechanism | Cheaper model per token, not fewer tokens: explicit > bandit router > per-type defaults (`agent-tools.ts:169-278`); Tier-1 "skip LLM" only *returns a note* (`:460`). `TokenOptimizer` reports `tokensSaved: null` without a baseline (`integration/src/token-optimizer.ts:31-38,113-123`). One `cache_control: ephemeral` on the system prompt in the API path only. No tool-output compression. ~378 distinct MCP tool names advertised by default (`commands/mcp.ts:211`). | None. `agent-token-usage.ts` reshapes provider usage per worktree (58 lines); cost from a hard-coded price table (`claude-model-pricing.ts:50-55`); `acp-compaction-turn.ts` forwards `/compact`. Per-agent `CLAUDE_CONFIG_DIR` for attribution and account choice (`claude-accounts/environment.ts:64-65`). | Real layer: tool-result interceptors → `CacheAligner` (detect-only, no-op on messages) → `ContentRouter` (`transforms/pipeline.py:152-187`). Frozen prefix = provider-reported cached tokens; **replays previously forwarded bytes** and compresses only the appended delta (`cache/prefix_tracker.py:564-587,1189-1201`). Content detector with byte gates 512/2048/5120 (`live_zone.rs:170-197`). `compact_lossless` folds with exact inverses and round-trip check (`lossless_compaction.py:113-472`). `SearchCompressor` 5/file, 15 files, 30 total, first+last kept (`search_compressor.rs:185-197`). Rust `SmartCrusher` row keep (§5 P2.1). `CodeAwareCompressor` **off by default** (`content_router.py:1908`) and really "first N statements". Kompress = 261 MB ONNX ModernBERT. CCR retrieval tool is hash-only; the advertised "BM25 within cached content" does not exist. Telemetry on by default (`telemetry/beacon.py:59`). |
| Measured vs claimed | README "30–50% token reduction", "352× edits" contradicted by the null-returning optimizer and the 2026-05-29 audit. Real number: `claude -p` from project dir ≈ $1.56 vs empty cwd + `--append-system-prompt` ≈ $0.34, batched ≈ $0.02/item (`services/fable-harness.ts:10-28`). | n/a | Seeded proof 21/57/42/30 % on **synthetic** inputs, tool messages only, CCR-dropped rows count as saved, no accuracy measured (`benchmarks/real_world_agent_benchmark.py`, `index_proof_table.py:55-75`). |
| **Take** | null-when-unmeasured; judge/verifier nodes from an empty cwd; schema-overhead budget; identical-failing-command loop breaker. **Not** the bandit, **not** the tool surface. | cache-token normalisation rule; price table as optional config. hx already has per-agent homes (`run/<id>/home`), so attribution is already exact. | lossless folds; content-detector routing; the prefix-replay rule as a stated invariant; stale-read pointers; search caps; SmartCrusher's must-keep set only if JSON-array tool output becomes common. **Not** Kompress, CodeAware, CCR tool, cross-turn dedup markers, `learn` over `~/.claude`. |

## 2. hx as the code has it

Status: **Have** = body read · **Partial** = mechanism present, fleet behaviour missing · **Absent** =
searched, not present · **✗** = a draft claim the code refutes.

### 2A. Parallelism, claims, stubs, unblocking

| Need at width 50–60 | Status | Evidence |
|---|---|---|
| Autonomous dispatch of ready units | **Absent** | `application_loop.py:123-187` `tick` drains, retires, reconciles, advances one completion, runs companions, restarts, resets; never calls `ready`/`assign`/`launch`. `native_controller.py:255`: "A reserved run without a native launch is deliberately absent." `plan ready/assign` and `launch` are Partner-only (`planning.py:365-367`, `lifecycle.py:117`). The loop sleeps 5 s only when idle (`:256-260`); no interval flag. |
| Topological levels, parallel groups | **Have, report-only** | `planning.py:185-232` computes `dependency_levels`, `hard_dependency_levels`, `potential_parallel_groups` (greedy: distinct workdir, disjoint write scope, no exclusive clash), `max_parallelism`; consumed only by tests. |
| Plan scale | **Blocker** | 1–32 units per plan (`planning.py:65`); prerequisites resolve only inside the same plan (`:152`); ≤ 64 write paths, 32 resources, 32 outputs, 64 prerequisites, 256 KiB per plan. 500 units need ≥ 16 plans with no dependencies between them. |
| Worker selection, seat provisioning | **Absent** | `assign(store, task_id, revision, worker_id)` needs the id (`unit_execution.py:237`); one active run per worker (`continuity_schema.py:21`) and per workdir (`unit_execution.py:174-177`). `config/*/harness.json` scans exist (`goal_templates.py:32`, `learning_companion.py:55`, `doctor_context.py:13`) but nothing picks an idle worker by role; 60 agents = 60 hand-written harness files + 60 worktrees. |
| Contract-first early start | **Have, after publication** | Producer: bind → independent approve → commit → `hx check` → `publish-contract` (needs approved binding, current receipts, drained producer; `dependency_packets.py:213-263`); auto-publish at `complete` (`unit_execution.py:324-328`). Consumer admission requires the row (`contract_proof`, `:194-210`), then Partner `ready/assign/launch`, then `acquire-contract` (own run only, `planning.py:372-374`), `packet_covered` → separate integration unit → `verify-integration` → `deployable` (`:389-454`). **Nothing makes publication early**; no bulk stub command; no contract-wave validator. |
| Self-unblock | **Absent** | A blocked consumer is never admitted and never notified; `contract_published` wakes only the Partner; `hx progress` blockers queue kind `projection`, which is not in `NOTIFY` (`progress.py:148`, `runtime_notifications.py:10-11,37`); `packet_binding_proposed` wakes nobody, so the single reviewer (`goal_templates.py:40` `reviewers[0]`) is a silent funnel before every first edit (`unit_execution.py:286-287`). |
| Claims | **Internal only** | Write leases at `bind` (`unit_execution.py:206-209`); check claims (`check_artifacts.py:24-26,71`); .NET equal-input claims **refuse** a duplicate rather than join it (`dotnet_build.py:240-256`); outbox claims (`continuity_store.py:529-547`). The only external call is read-only `gh` in `handoff.py:58-61`. No pre-dispatch claim gate; workers cannot claim work at all. |
| `admit()` atomicity, resources | **Have** | `start_run` wraps `admit`+`bind` in one `BEGIN IMMEDIATE` (`continuity_store.py:120,236-246`); rechecks head, completion, workdir, leases (per repository id), freshness, resources, prerequisites (`unit_execution.py:164-198`). Resources are `shared`/`exclusive` only (`dependency_packets.py:149-156`); no counted capacity. |
| Host pools | **Partial, .NET only** | `dotnet_slots.py:20-24,39-44`: per-user per-boot SQLite in the temp dir, shared by every root; limit = last `--jobs` any root passed (1–64), else 2; a full pool raises "retry later", nothing waits; crashed jobs keep their slot (`:37`). No `HX_BUILD_SLOTS`/`HX_TEST_SLOTS`, no named pools, no per-box session cap. |
| Companion concurrency | **1 per root, both kinds** | `CREATE UNIQUE INDEX companion_active_slot … WHERE status='running'` and `learning_active_slot` (`continuity_schema.py:312,322`); each kind checks the other (`native_companion.py:168-169`, `learning_companion.py:201`); the pass runs **inside the tick** for up to 60 s (`native_companion.py:143-145`). Provider: `claude-cli` only; `anthropic`/`openai-api` are validation-only names (`config_harness.py:87-89`, `native_companion.py:81-82`). |
| Isolated checkouts | **Have, no pool** | `repo_service.py:279-283` `git clone --shared --no-checkout` + `sparse-checkout set`; no `git worktree add`, no pre-warming. Service: `BoundedSemaphore(--workers)`, default 4, 1–64, acquired on the accept thread so a full pool stops accepting (`:352-361,393,457`); every request writes a latency row to SQLite (`:157-160`). |
| Cross-instance view, pause, inbox | **Absent** | `board.snapshot(root)`, `metrics.collect_fleet(root)` read one root (`board.py:57-67`, `metrics.py:83-84`); no INBOX helper in code; the only pause is per-run scope pausing (`unit_execution.py:292`). |
| Shutdown census, retained leases | **Have, stronger than any external** | `native_shutdown.py` SIGSTOP-census-SIGKILL quanta; no auto-replay of uncertain paid work. |

### 2B. Code graph, search, reuse

| Need | Status | Evidence |
|---|---|---|
| Structural graph, immutable snapshots | **Have** | `code_store.py:27-54` repos/files/symbols/refs/edges/projects/project_edges/contracts/exclusions/metadata, WAL; content-addressed `source_objects`/`extraction_objects`/`graph_objects`; persistent-treap `snapshot_nodes`; `snapshot_refs` (`latest`, `commit:<sha>`, branch) and `checkout_heads(root)`. DB at `state/code-index/index.db`, refused inside the product repo (`code_build.py:613`). |
| FTS | **Have, half unused** | Four FTS5 tables: `code_search` (porter) and `code_trigram` over `files` (`code_store.py:93-108`) are maintained on every write but never read, because every CLI read activates a snapshot (`code_index.py:31`) after which `fts_tables()` returns `[]` (`code_store.py:196`); queries use `source_search`/`source_trigram` (`code_snapshot.py:18`, `code_store.py:126`). |
| Extraction lanes | **Have** | Python stdlib `ast` (calls, references, imports; **no inherits**); `braces()` regex for .cs .ts .tsx .js .jsx .go .rs (calls, references, registers, implements from class headers, imports); tree-sitter CLI, version recorded and never gated (`code_adapters.py:67`), one subprocess per file; SCIP lane=resolved needing a clean checkout at the same HEAD (`code_scip.py:244-255`); Natural (`.nat .nsp .nsn …`: objects/routines/data, CALLNAT/FETCH/PERFORM as refs); JSONL import. Call edges are built at query time by name match and every edge is `status="candidate"` (`code_queries.py:359,410`). |
| Ranking | **Have** | Exact-symbol hits score `10 - LANES[lane]` and always beat FTS (`code_queries.py:74,88,110`); RRF `1/(60+i)`; proximity ×1.5 ≤ 100 chars (`:170`); Levenshtein fallback only on zero hits over the **first 5000 names alphabetically** (`code_snapshot.py:819`); CAP 100; `sift` is a remote Jev re-sort that never drops. No embeddings anywhere. |
| Incremental index, invalidation, freshness | **Have, serial** | Candidates = git delta + stale paths, skipped on sha256+generation+mode (`code_build.py:727,820`); mtime only detects mid-read change (`:765`) ✗ (draft said mtime skip). `invalidate()` reverse closure; `apply_changeset` journaled, `BEGIN IMMEDIATE`, optimistic `latest` check, 4 retries (`:369-375,999`). Indexing is `for rel in sorted(names)` under a per-namespace lock stripe (`:861,983`); one file over `HX_CODE_MAX_FILE_BYTES` (512 KiB) aborts the index. Freshness is pull-based (`code_freshness.scope/require`); no watcher. |
| **Multi-worktree correctness** | **Defect (D1)** | Namespace = `.hx/map/manifest.json` `repo_id`, else origin URL, else path (`code_store.py:167-181`): all worktrees of one repo share one namespace. Live rows, `snapshot_refs['latest']` and `repos.root` (`code_build.py:570` `ON CONFLICT(repo) DO UPDATE SET root=…`) are last-writer-wins. Verified in the scratch experiment: a clean worktree's `search` returned the dirty sibling's uncommitted symbol; `hx code fresh` checked `repos[0]["root"]` (`code_index.py:336`), reporting fresh while the caller was stale; `--expect absent` (`:488`) and `--at HEAD` resolve the same way; re-indexing the clean tree reused the sibling's snapshot (`changed=0`) and did not converge until a scoped then full refresh; `code import` refuses a second worktree (`code_import.py:60`). Only `checkout_heads` is per worktree, so **admission** freshness is right while **queries** are wrong. |
| Reuse queries | **Have, unique** | `code_reuse.py` precedent/conform/tests_for/register; `code_index --expect exists|absent|one|clean|fresh` exit 3; `hx code contract NAME [--task]` joins ledger publications with the manual contracts registry. |
| Search-before-build enforcement | **Absent** | `runtime_policy.inspect_call` blocks edits before binding approval, runtime-owned paths, oversize arguments, unscoped Grep/Glob, noisy Bash (`:59,134-162`); `native_tools._admit` adds read capacity, repeat-search denial, capture gaps, 256 pending calls. Nothing requires an `hx code` query before the first Write. Prompted in `hx-worker/SKILL.md:18-35`, `audiences/worker.md:22`; retro-counted by Partner-only `discipline.post_seam` (`discipline.py:368`). What *is* enforced against duplicate work is ownership: overlapping write scopes refused at validate and admit. |
| Cross-lane sharing | **Absent** | `search_coverage.py:23,52,58`: fingerprint = sorted `(abs_path, dev, ino, size, mtime_ns, ctime_ns)`, query hash embeds the absolute path, lookup keyed on the absolute worktree: three independent reasons N worktrees never share; results > 2048 B not cached. Check identities "intentionally retain absolute workspace identity" (`fingerprints.py:126,134`). .NET seed = digest of a recipe containing `'cwd': str(cwd)` and the absolute project path (`dotnet_build.py:320,335`), claims and cache per root ✗ (draft said host-wide). |
| Lane partition from the graph | **Absent** | `code_analysis.py:171-189` map gives per-file `fan_in` and PageRank; `submaps` Louvain emits ≤ 16 `claim="hypothesis"` candidates, members `[:64]`, only when modularity beats the directory baseline (`:230`); both load the whole graph through `edges(limit=1_000_000)` (`:153`). `hx plan draft` always yields one unit (`goal_templates.py:44-114`); `hx plan validate` takes hand-authored prerequisites. The graph never produces unit dependencies, providers, width or depth. `plan_context` reads the **appmap ledger**, not the code graph ✗ (`retrieval.py:30-53`, ≤ 3 seeds, 24 records, 32 edges, 1.5 s). |
| Stubs in the graph | **Absent** | `publish-contract` freezes files and receipts into `unit_contracts`; published symbols are **not** inserted into the graph, so a consumer cannot `type`/`signature` them until the stub is merged and indexed in its own worktree. |
| Transcript corpus, clone detection | **Absent** | `content_store` (porter+trigram+title, RRF) is fed by `search_exec` output; transcripts only tail-read for usage. `span_sha` used only for staleness; the only near-duplicate test is learnings Jaccard 0.8. |

### 2C. Token economy

| Need | Status | Evidence |
|---|---|---|
| Tokenizer, units | **Absent, mixed** | `context_packets.py:22` `len(text.encode())`, none of 7 production `issue()` callers pass `count_tokens`; 4 B/token at `file_limits.py:19`, `stepstate.py:58`, and `optional_tokens * 4` inside the same function (`context_packets.py:260`). The seam uses provider-reported tokens (`token_controls.py:116`) and then subtracts byte reservations (`file_reads.py:169-178`). |
| Tool-output reduction | **Have, `hx tool-exec` only** | `output_reduction.py:9-12`: 4096 B out, 768 B raw chunks, Jev sees ≤ 8192 B of non-diagnostic chunks, > 4 batches **raises**; chunks with a split UTF-8 character are dropped (`:62-67`); `DIAGNOSTIC` has `^E\s|^\s*[+-]\s` without `re.M`, so the anchors match only chunk starts. `test_summary.py` 7 runners, ANSI stripped there only. Native Bash/Grep/Read results reach the model raw; routing is enforced by the **first word** of the command (`runtime_policy.py:155-162`): `python -m pytest`, `uv run …`, `cd x && pytest` pass. `tool-exec` kills at 60 s / 1 MiB (`tool_execution.py:14,54`), unrecoverable. |
| Seam before native compaction | **Have, Jev-dependent** | Threshold `min(model.threshold, window − output − min(4096, window//10))` (`token_controls.py:15`); `reset_one` will not fire while events are pending (`:69-71`), every non-bookkeeping event needs Jev with no fallback (`jev_deltas.py:58-60`, `jev_decisions.py:32-34`); `guard` then refuses every request at the threshold (`:44-45`). Jev is a remote `httpx` call, 4000 B in, 0.5 s deadline (`jev.py:13-23`). The compaction prompt (2,047 B) is installed as every worker's `config/CLAUDE.md`, resident every turn (`native_launch.py:45`). |
| Whole-file delivery | **Have, probably paid twice on Claude** | Slices refused (`file_reads.py:115`); PostToolUse re-emits the complete file as `ceil(threshold/9000)` `additionalContext` segments (`file_read_delivery.py:79-84`, `hooks.py:196-202`) while the native Read result stays in context; nothing removes the native copy (inferred, not verified live). Each Read/Bash call registers ~22 extra hook entries at the shipped threshold (`skeleton/adapters/claude/install.sh:158-166`). Same-file re-read is refused per launch/checkpoint (`runtime_policy.py:215-221`). |
| Companions | **Have, serialised, unmetered** | Context companion: frozen ≤ 32 KiB input, `--tools ''`, two MCP tools, `--max-turns 6`, Jev-settled `_reduce` bypass (`native_companion.py:21,43,109-132,192`); triggered per tick when `head_seq > classified_seq`, no batching; the Jev routes are appended to the **system prompt** per pass (`:95`), which defeats the provider cache (inferred). Partner companion is a long-lived tmux pane driven by `/clear` + pointer (`companion.py:294-295`), not the sidecar. Learning companion via sidecar, batch 32. Companion output reaches the worker only at packet re-issue (startup refresh, threshold reset, `/clear`, compact, resume, restart). The CLI's JSON usage is discarded; **companion spend is never recorded** (`checks.py:184-186`). Models: companion/learning `claude-haiku-5-5` low, worker/Partner `claude-opus-5-5` xhigh, explorer `claude-sonnet-5-5` (`skeleton/templates/worker/harness.json`). |
| Prompt cache | **Accounting only** | `prompt_cache.py:41-46` bytes as tokens; no `cache_control`; `cache_ttl: "1h"` read at `:193` and wired to nothing. Stable prefix by body-hash dedupe (`prompt_compiler.py:112,173`); packets frozen per checkpoint and re-issued only at seams, which is already the headroom prefix rule in spirit. |
| Metrics | **Have, partial** | `metrics.py:48-79`: per-agent `chat_bytes`, Claude output tokens (max per `message_id`), turns, from ledger `events` with `stream_id LIKE 'native:%'`; `None` when unrecorded. Not measured: input/cache tokens, cost, companion/learning/Jev spend; no price table, no per-lane budget, no throttle. |

## 3. Defects and gaps, ordered by blast radius at 50–60 agents

| # | Defect | Kind | Where |
|---|---|---|---|
| D1 | One graph namespace per repository across worktrees; queries, `fresh`, `--expect absent`, `--at` answer for the last indexer; full re-index does not converge from a dirty sibling | correctness | `code_store.py:167-181`, `code_build.py:570`, `code_index.py:31,336,488` |
| D2 | Every hook opens the ledger with `BEGIN IMMEDIATE` + DDL + `synchronous=FULL`, 10 s busy timeout; a hook failure on request/tool-start returns 2 and **blocks the tool**; `admit` hashes the whole repo and `ready()` does so for every unit under the writer lock; prompt compilation and `publish_contract` fingerprinting also under the lock | contention → denial | `continuity_store.py:66-100`, `hooks.py:177,319`, `native_tools.py:108,120`, `unit_execution.py:194-196,215-229`, `native_launch.py:101-106` |
| D3 | The loop runs acceptance checks (≤ 3600 s) and companion passes (≤ 60 s) inline, one per tick; completion waits on the companion; any running companion blocks all restarts and resets; one slot for both companion kinds | serial | `native_completion.py:88-125`, `native_companion.py:143-145`, `application_loop.py:157-159,165-185`, `checks.py:36` |
| D4 | The seam and `tool-exec` reduction cannot proceed without the remote Jev service; without `TYPESAFE_API_KEY` a worker stops at the threshold and is never reset | availability | `jev_deltas.py:58-60`, `jev_decisions.py:32-34`, `token_controls.py:44-45,69-71` |
| D5 | Claude workers probably receive each Read twice (native result + hook segments); ~22 hook entries per Read/Bash | cost | `file_read_delivery.py:79-84`, `hooks.py:196-202`, `claude/install.sh:158-166` |
| D6 | Plans cap at 32 units; prerequisites cannot cross plans | scale | `planning.py:65,152` |
| D7 | Noisy-command routing checks the first word only | leak | `runtime_policy.py:155-162` |
| D8 | `DIAGNOSTIC` regex anchors match only chunk starts (no `re.M`); split-UTF-8 chunks dropped; > 4 Jev batches raises. Replaced by structured runner output, not patched (§4.3) | reduction quality | `output_reduction.py:12,62-67,114-116` |
| D9 | Restart and token reset take the first row with no cursor: one busy pane blocks every restart | head-of-line | `native_restart.py:25,40-41`, `token_controls.py:50-62` |
| D10 | Progress blockers, binding proposals and check failures wake nobody | latency | `runtime_notifications.py:10-11,37`, `progress.py:148` |
| D11 | Equal-input builds and checks are refused, not joined; identities embed absolute paths | duplicate work | `dotnet_build.py:242,320`, `check_artifacts.py`, `fingerprints.py:126` |
| D12 | Two FTS tables maintained on every write and never read | write cost | `code_store.py:93-108,196` |
| D13 | tree-sitter was gated to exactly 0.25.10; resolved 2026-10-22, the version is recorded and never gated | policy | `code_adapters.py:67` |
| D14 | Companion spend discarded; Jev routes in the system prompt per pass | metering, cache | `checks.py:184-186`, `native_companion.py:95` |

Smaller: Levenshtein over the first 5000 names (`code_snapshot.py:819`); one oversize file aborts an index; `facts.select_facts` and `runtime_policy.CONTEXT_BYTES` are dead; `hx loop --status` pages 16 runs; no test covers two worktrees at the same HEAD (`test_code_snapshots.py:202` covers different HEADs).

## 4. Principles

### 4.1 The systematization rule

Every item in §5 is split three ways, and the split is the deliverable as much as the code:

- **Mechanism** lives in `src/hx` once, with the same shape for every application: executors,
  pools, allocators, gates, registries, reports. It never contains a project's language, forge,
  tracker, naming scheme or numbers.
- **Profile** is data a project sets at install and tunes from measurement: one shared
  `fleet.json` in a fleet directory that every instance's `config/hx.json` points at (`"fleet":
  "/path"`, beside the existing `learnings`, `skills` and `repository_service` keys,
  `install.py:131-146`), plus per-instance overrides. It holds box budgets, pool sizes, lane
  roots, ranges, role model tiers, and the *commands* hx calls for project-specific steps.
- **Project script** is what those commands point at: stub generators, external claim queries
  (forge PRs, tracker items, team branches), prechecks, database provisioning. hx runs them at
  the right time, receipts their output, and refuses on failure; it never contains them.

The Natural → C# tweaks in `EXAMPLE.md` map onto this as follows. Each row is a profile field or
a project command once §5 lands; none stays a convention.

| EXAMPLE.md tweak | Today | Systematized as |
|---|---|---|
| Inventory from the system-map graph ∩ handlers ∩ tracker claims (711 → 500 WPs + 21 providers) | Partner analysis + project scripts | `hx code import` facts + `hx plan partition` (P1.4) with `claim_command` filtering candidates |
| `tools/wave-order.py` (family heads, FETCH pairs as one WP; waves 376/115/9) | project script | `hx plan partition --waves` output; pairing rules are a profile `partition.merge` list |
| Minute-0 contract commit per wave (`I<X>Service.cs`, `<P>Contract.cs`, `<P>Seed.cs` per program) | project generator, unenforced | contract-wave template unit (P1.4): profile `stub_command`; hx registers the stub symbols and refuses the wave until `--expect exists` holds for every declared contract |
| Pre-allocated ISN blocks, DB names, port arithmetic | convention + in-goal checks | `hx alloc <namespace> --lane L` from profile ranges (P1.2) |
| 13 instances, tmux server per instance, UI ports 8800+idx, per-lane worktrees | documented manual procedure | `hx fleet provision --profile` (P1.2) |
| 4-source claim pre-check + `check-claims.py` inside each goal | after seat and tokens are spent | profile `claim_command`; executor runs it before `assign` and requires a receipt (P1.6) |
| `HX_BUILD_SLOTS` / `HX_TEST_SLOTS`, golden bin/obj seeding | do not exist / `dotnet_artifacts` | named host pools in the profile (P1.3); `dotnet_artifacts` unchanged |
| ≤ 10 QA-PASS WPs per batch, `merge-lock.sh`, strict up-to-date ruleset | shell + forge config | `delivery.json` `max_batch` + executor backpressure (P1.1); remote push stays an authorised external op |
| 88 eng / 33 QA / 13 rel, 4.2 core-h/WP, ≤ 45 engineers per box | asserted | `hx metrics --sizing` from recorded durations (P1.7); values go back into the profile |

### 4.2 Claude Code is the default harness

Every core mechanism is designed for Claude Code first: its native Read is the whole-file
delivery path (P0.5), its transcript usage fields (`input_tokens`, `cache_read_input_tokens`,
`cache_creation_input_tokens`, `output_tokens`) are the ledger's primary token source
(`token_controls.py:116` already sums them), its prompt cache is the cost model the seam policy
reasons about (§4.4), and the companions run on Claude models through the Messages API (P0.7).
Codex, Pi, Grok and Meta stay adapters under `skeleton/adapters/`; a mechanism that only exists
to serve a non-Claude flavor (hook-emitted file segments, `hx file-read` markers) lives in the
adapter, never in the core path, and nothing in the core waits on the least capable flavor.
`tests/fakeclaude` is the scale substrate (§6).

### 4.3 No regex as a mechanism

Regular expressions are not how hx classifies, extracts or gates. The order of preference is:

1. **Structured sources.** `ast` for Python; SCIP (`code_scip.py`, lane `resolved`) and the
   MSBuild graph (`dotnet_graph.py`) for C#; machine-readable runner output for checks (pytest
   `--junit-xml`, `dotnet test --logger trx`, `go test -json`, `cargo --message-format=json`,
   jest `--json`) parsed with `xml.etree`/`json`; `shlex` for command lines; `json.loads` for
   payloads; `startswith`/`partition` on line prefixes for diffs and `path:line:` output.
2. **Model classification** where text is genuinely free-form: Jev for bounded routing, the
   companion for rewriting.
3. Never a regex as the primary mechanism, and never one that a project would need to tune.

Existing regex debt is replaced, not patched: `DIAGNOSTIC`/`LEAF` in `output_reduction.py:12-13`
and the seven text parsers in `test_summary.py` (replaced by structured runner output declared
per check recipe), the `braces()` lane in `code_extract.py` (demoted to last resort; a C# profile
runs SCIP + MSBuild, and `hx code stats` reports the lane per file), and the first-word command
check in `runtime_policy.py:155-162` (replaced by `shlex` segmentation, P2.2). Headroom's folds
are regex-based; the hx versions (P2.1) are equality- and prefix-based.

### 4.4 Companions are scripts; seams are cache-aware

The companion is a deterministic transform, not an agent: a frozen, bounded ledger slice in,
a validated operations patch out (`passes.py` create/supersede/compress/invalidate/drop,
committed by `passes.commit`), no session, no pane, no tools beyond reading the evidence it is
handed. The code is most of the way there for the worker context companion (`--tools ''`, two
MCP tools, `--max-turns 6`, Jev-settled `_reduce` bypass; `native_companion.py:21,43,109-132,192`)
but it still spawns the CLI per pass in a temp HOME and the Partner companion is a long-lived
tmux pane driven by `/clear` plus a pointer (`companion.py:294-295`). P0.7 makes both one
bounded Messages API exchange.

The seam policy, restated by the author: **when necessary, clear everything**, including what is
lost from the Claude prompt cache, and re-issue the packet; **otherwise, as soon as the cache is
cold anyway, there is no reason not to install an extremely efficient targeted context
whenever that is beneficial as the session nears its limit.** A turn that runs on a cold cache
re-writes the whole prefix regardless, so replacing the prefix with a smaller targeted packet at
that moment costs nothing extra and buys back the headroom; rewriting inside a warm cache pays
the cache-write multiplier to lose the read discount (headroom's prefix rule, `prefix_tracker.py:6-9`).
Three regimes, all decided from data the ledger already captures:

| Regime | Condition | Action |
|---|---|---|
| **Hard seam** | context ≥ threshold (`token_controls.py:15`) | `/clear`, re-issue the packet; cache loss accepted |
| **Opportunistic seam** | context ≥ `seam.soft_fraction × threshold` **and** the cache is cold: the last turn's usage shows `cache_read ≈ 0` with `cache_creation` ≈ prefix, or idle time since the last assistant message ≥ the cache TTL recorded in the profile | `/clear`, install the targeted packet; no extra cost because the prefix would be rewritten anyway |
| **Hold** | cache warm, below hard threshold | never rewrite; the companion keeps the next targeted packet ready in the ledger so either seam is instant |

Today's soft seam is a fixed rule (≥ 60k tokens and ≥ 600 s since the last seam,
`companion.py:445-464`, `seam_min_interval_s`) that ignores cache state; `cache_ttl: "1h"` is read
by `prompt_cache.py:193` and wired to nothing; the seam cannot fire without Jev (D4). P0.7
replaces the rule with the table above and P0.4 removes the Jev dependency.

## 5. Plan

Effort: **S** ≤ days · **M** 1–2 weeks · **L** > 2 weeks. Every item names its owner module, its
surface, its bounds and its validation. Breaking changes only: remove the old path, add no
migration, compatibility or fallback code.

### P0 — correctness and availability at width (do first, in this order)

**P0.1 Graph answers for the caller's checkout (D1).** Mechanism. `code_index` resolves every
read against the caller's `checkout_heads` entry (the worktree the CLI runs in, or `--repo`),
activating `commit:<sha>` of that head and overlaying that checkout's live rows, never
`latest`. `repos` becomes `(repo, root)`-keyed so a namespace holds N roots; `fresh`,
`--expect`, `--at` and `code import` take the caller's root. Re-index of worktree B at the same
HEAD as a dirty A must converge in one full pass. Owner: `code_store.py`, `code_snapshot.py`,
`code_index.py`, `code_build.py`, `code_import.py`. Bounds: unchanged query caps. Validation:
a two-worktree test at the same HEAD (dirty A, clean B) asserting search, `fresh`, `--expect
absent` and `import` per worktree; `tools/code-graph-benchmark.py` gains a multi-worktree
case. Effort M.

**P0.2 Hook path never takes the writer lock (D2).** Mechanism. Split `ContinuityStore` into a
read-only opener (no `BEGIN IMMEDIATE`, no DDL, `synchronous=NORMAL` under WAL) used by every
hook and query, and a mutating opener used by the loop and CLI mutations. Move `scan_repo`
hashing out of `admit`/`ready` transactions (hash first, then transact on the hashes), and
prompt compilation and publish fingerprinting out from under the lock. A hook that cannot read
within its budget returns a **pass-through with a logged event**, never 2, except the guard.
Owner: `continuity_store.py`, `hooks.py`, `native_tools.py`, `unit_execution.py`,
`native_launch.py`, `dependency_packets.py`. Validation: a contention test with 64 concurrent
hook invocations against one mutating loop asserting zero denials and p95 hook latency; the
guard tests unchanged. Effort M.

**P0.3 Checks and companion passes leave the tick (D3, D9).** Mechanism. A per-root job runner
(threads, bounded by profile `loop.workers`) executes acceptance checks and companion/learning
passes; the tick only enqueues and reaps. Replace the two unique single-slot indexes with
counted slots sized by profile `companion.max_concurrent` (default 1 keeps today's memory
footprint; the Natural → C# box sets it to its session budget). Restart and reset iterate with a
round-robin cursor like `advance_one`. Completion no longer waits behind another run's
companion. Owner: `application_loop.py`, `native_completion.py`, `native_companion.py`,
`learning_companion.py`, `native_restart.py`, `token_controls.py`, `continuity_schema.py`.
Validation: tick-time test under 16 runs with one 300 s check queued (tick stays < 1 s);
companion concurrency test at `max_concurrent=4`. Effort M.

**P0.4 The seam and reduction always fire (D4, D8).** Mechanism. Deterministic fallback when
Jev is absent, times out or errors: events settle as `unclassified` and the seam proceeds on the
frozen packet; `output_reduction` falls back to a structured summary (P2.1) plus head and tail
inside the 4096 B budget. The `DIAGNOSTIC` and `LEAF` regexes are deleted, not fixed: check
recipes declare a machine-readable output format and `test_summary` parses that with
`xml.etree`/`json`; free text goes to Jev or the fallback. Chunking carries the UTF-8 remainder
into the next chunk; over-budget Jev batches degrade to the fallback instead of raising. Jev
stays the better path when present. Owner: `jev_deltas.py`, `jev_decisions.py`,
`token_controls.py`, `output_reduction.py`, `test_summary.py`, `checks.py`. Validation: existing
`test_jev_failure_*` flipped to assert the fallback output; a seam test with no
`TYPESAFE_API_KEY`; `test_test_summary` fixtures re-recorded as junit/trx/json. Effort S–M.

**P0.5 Claude's native Read is the delivery (D5).** Mechanism, Claude default. Verify live first
(one Claude run with a transcript diff: does the native Read body remain alongside the
segments?). If it does, Claude workers get the complete-file guarantee from the native result
and the hook emits only the completeness marker; the segment path moves into the Codex/Pi
adapters. Collapse the ~22 per-segment hook entries into one entry whose command loops
segments internally where an adapter still needs them. Owner: `file_read_delivery.py`,
`hooks.py`, `skeleton/adapters/claude/install.sh`. Validation: `test_native_file_host` asserts
one body per read on Claude; hook count in the generated `settings.json` ≤ 8. Effort S.

**P0.6 Plans at fleet scale (D6).** Mechanism. Prerequisites may name an output of another
*applied* plan in the same root by exact identity `(plan_id, task_id, output_id, version)`;
`contract_proof` and `materialize` already key on exact outputs. Unit cap rises to 256 per plan
and 1 MiB per plan body; levels/groups are computed over the union of applied plans. Owner:
`planning.py`, `dependency_packets.py`, `unit_execution.py`. Validation: cross-plan early-start
test; validate/apply at 256 units stays < 2 s. Effort S–M.

**P0.7 Script companions on the Messages API; cache-aware seams (§4.4).** Mechanism + profile,
Claude default. (a) Build the `anthropic` transport that `config_harness.py:87-89` already
names: one bounded HTTP exchange per pass over the existing preflight/frozen-body/protocol seams
(`httpx` is already the dependency; the two protocol tools become API tool-use with a JSON
schema for the patch; `read_evidence` stays a bounded second round, max 2). The pass is a pure
function of the frozen slice; `passes.commit` validates as today. The Partner companion becomes
the same script (step-state eviction in `stepstate.evict` is already deterministic): the tmux
companion pane, `start.sh --companion` and the `/clear` + pointer protocol are deleted. Usage
from every response is recorded on the job (feeds P2.3). The system block is byte-stable across
passes (Jev routes move to the user turn) so the companion's own calls cache. (b) Seam policy
per the §4.4 table: `token_controls` computes cache state per turn from the transcript usage
fields and idle time; profile `seam.soft_fraction` (default 0.6), `seam.cache_ttl_s` (default
300, 3600 when the instance runs with the 1-hour cache) and `seam.min_benefit_bytes` (do not
seam when the targeted packet is not materially smaller than the live prefix). The companion
keeps the next targeted packet materialised in the ledger after every pass so a seam is a
`/clear` plus one paste, never a wait on a model. `seam_min_interval_s` and the fixed 60k rule
are removed. Owner: `sidecar_process.py` (replaced by `companion_api.py`), `native_companion.py`,
`learning_companion.py`, `companion.py`, `stepstate.py`, `token_controls.py`,
`compaction_policy.py`, `config_harness.py`, `skeleton/adapters/claude/start.sh`. Validation:
companion pass as a recorded HTTP fixture (no network in tests); seam tests for the three
regimes driven by synthetic usage rows (warm/hot, cold-by-miss, cold-by-idle); one live Claude
run confirming the opportunistic seam fires on a cold cache and not on a warm one. Effort M.

### P1 — throughput mechanisms

**P1.1 Executor (DAG dispatch over existing admission).** Mechanism + profile. In `tick`, after
reconciliation: for every applied plan, `ready()`; order ready units downstream-first (release,
QA, integration before new engineering); for each, pick an idle seat whose role matches the
unit's `check_policy` role and whose workdir is free; `assign()` + `lifecycle.launch()`
in-process (the CLI caller gate stays for humans). Budgets from the profile, all enforced through
the host pool of P1.3: `sessions` per box (one number across instances), per-instance quota,
`writers` vs read-only units (ruflo), restore token bucket after a box restart (orca,
`restore.concurrency`, `restore.per_second`), process-growth breaker (orca, sampled `ps`), a
`precheck_command` per unit kind (orca) whose non-zero exit skips without consuming a failure,
stale-base refusal at `dispatch.max_behind` commits that re-queues instead of failing, an
idempotent launch key `sha256(plan, task, revision, head)` (ruflo) so a reconciled double tick
never double-launches, and merge backpressure: no new engineering unit for a lane whose
landed-but-unvalidated count exceeds `2 × delivery.max_batch`. Dispatch never auto-replays
`*_uncertain`. Surfaces: `hx plan run PLAN [--dry-run] [--max N]` (Partner) for one plan;
`fleet.json` `dispatch.auto: true` makes the loop do it continuously; `hx fleet pause|resume`
is a flag file the executor checks. Owner: new `plan_dispatch.py`, `application_loop.py`,
`cli.py`. Validation: `tests/scale/` lane on `tests/fakeclaude` at 60 concurrent runs asserting
ready→running p95 < 2 ticks, zero double-launch, budget never exceeded. Effort M.

**P1.2 Fleet provisioning and allocation.** Mechanism + profile + project script. `hx fleet
provision --profile fleet.json` loops `install_skeleton` per instance, writes the three
templates that do not ship today (`ui.json` with `base_port + index`, `planning.json`,
`delivery.json`), generates `config/<id>/harness.json` seats per role from counts in the profile,
materialises each seat's sparse checkout through `repo_service` (`lanes.root`,
`lanes.sparse_paths`), and keeps a pre-warmed pool of `lanes.prewarm` checkouts refilled after
each claim (orca's `worktree move -f -f` + `reset --hard` idea, sized to the dispatch rate, not
3). `hx alloc <namespace> --lane L` hands out ranges (ISN blocks, DB suffixes, port blocks)
from `ranges` in the profile through the flocked-numbering pattern of `learn.propose/accept`;
`hx fleet status` is the multi-root rollup over `board.snapshot`/`metrics.collect_fleet`; the
documented one-line INBOX becomes `hx fleet inbox "<line>"` on the same flock. Provisioning
hooks (`provision_command` per lane: database, seed data) are project scripts hx runs and
receipts. Owner: new `fleet.py`, `install.py`, `repo_service.py`, `board.py`, `metrics.py`.
Validation: provision 13 instances × 8 seats in a temp root in < 60 s with no `~/.claude`
access (guard lane), allocation uniqueness under 64 concurrent `hx alloc`. Effort M.

**P1.3 Named host pools; join duplicates (D11).** Mechanism + profile. Generalise
`dotnet_slots` into `host_pools.py`: per-user per-boot SQLite, named pools (`sessions`,
`build`, `test`, project-named), limits read from the profile instead of the last `--jobs`,
waiters with a bounded queue instead of "retry later", slot reclamation on process death
(process identity already exists in `native_processes.py`). Equal-input builds and checks
**join** the running job and share its receipt instead of raising `Conflict`; identities key on
`(namespace, commit, relative path, content digest)` and the recipe, not the absolute workdir;
`search_coverage` keys the same way so one lane's Grep serves every lane at that commit.
`dotnet_slots` becomes the `build` pool client. Owner: new `host_pools.py`, `dotnet_slots.py`,
`dotnet_build.py`, `check_artifacts.py`, `fingerprints.py`, `search_coverage.py`. Validation:
two roots requesting the same build get one process and two receipts; 60 lanes, one Grep
each, one execution. Effort M.

**P1.4 Lanes, providers and stubs from the graph.** Mechanism + profile + project script.
`hx plan partition --repo WT [--scope …]` reads the graph (fan-in per symbol, Louvain
communities, imported facts) and emits what the Natural → C# project computed by hand: lane
candidates, the shared-provider set (fan-in across ≥ 2 lanes, ranked), family heads (a callee
built once by its smallest caller), and waves with width and depth, as a report and as a draft
multi-unit plan (`--out plan.json`) that `hx plan validate` accepts. Pairing rules (the FETCH
pair) are `partition.merge` patterns in the profile. The **contract wave** is a plan template:
one generator unit per lane whose bound checks are the profile `stub_command`, then `hx code
contract NAME --expect exists` for every declared contract, then a compile of the stub tree; its
outputs publish through `publish-contract`, consumers take `need: contract` prerequisites on
them. **Stub registration:** `publish-contract` inserts the published symbols into the graph
under the namespace with lane `contract` (provenance = ledger row), so `hx code type/signature
--expect exists` holds for every consumer before the stub is merged; the row is superseded by
the real symbol at the next index. `hx code map`/`submaps` stop loading the whole graph through
`edges(limit=1_000_000)` and use a bounded fan-in query. Extractor lanes become a profile choice
(`graph.lanes`, e.g. `[scip, msbuild, python-ast, natural]` for a C# target); the regex
`braces()` lane is the last resort and `hx code stats` reports the lane per file so a project
can see when it is running on it (§4.3). Drop the two unused FTS tables (D12); the
tree-sitter version gate (D13) is gone. Owner: `code_analysis.py`, `code_queries.py`,
`goal_templates.py`, `dependency_packets.py`, `code_store.py`, `code_adapters.py`, new
`plan_partition.py`. Validation: partition of the `tests/core` synthetic repos reproduces
hand-written expected lanes and providers; stub test asserts `--expect exists` before merge.
Effort L.

**P1.5 Wake the right party (D10).** Mechanism. `progress` blockers and decision requests
wake the Partner; `packet_binding_proposed` wakes the configured reviewer; `unit_check_failed`
wakes the Partner; `contract_published` wakes the Partner *and* re-runs `ready()` for consumers
in the executor. Delivery is one pointer line over the existing socket/tmux path (orca), never a
body. `NOTIFY` and `runtime_notifications.drain` take their per-tick budget from the profile.
Owner: `runtime_notifications.py`, `progress.py`, `dependency_packets.py`. Validation:
notification tests per kind. Effort S.

**P1.6 Reuse-before-build and claim gates at admission.** Mechanism + project script. The
binding gains a required `reuse` section: for every name the goal lists under *must not mint*
and *must reuse*, a receipted `hx code contract|type NAME --expect absent|exists` run at the
admitted head; `admit` refuses the first edit without it (the hook point already blocks edits
before binding approval). Separately, the executor runs the profile `claim_command` (project
script: forge PRs, tracker items, team branches) with the unit's write scope and outputs on
stdin before `assign`, receipts its output, and refuses on non-zero, so duplicate effort is
caught before a seat or tokens are spent. Owner: `dependency_packets.py`, `unit_execution.py`,
`plan_dispatch.py`. Validation: admission refused without reuse receipts; claim refusal leaves
the unit ready, not failed. Effort S–M.

**P1.7 Sizing from recorded durations.** Mechanism. `hx metrics --sizing` aggregates run wall
time by role, check durations, pool waits, companion queue depth and lag into per-band
medians and the implied role ratio, pool sizes and sessions per box at a target utilisation.
Read-only. Owner: `metrics.py`. Effort S.

### P2 — token economy

**P2.1 Structural folds before any model (headroom's idea, without its regexes or inverses).**
Mechanism + profile. hx keeps the full artifact and hands out `hx evidence` pointers, so a fold
needs no inverse and no round-trip check: it is a deterministic, self-announcing lossy view
("14 identical lines folded; full output: `hx evidence ID --offset …`"). In
`output_reduction.reduce`, before chunking: strip ANSI escapes by scanning for the escape byte;
collapse runs of equal lines (line equality); fold repeated blocks (window equality over the
last 8 line positions, ≥ 3 lines); group `path:line:content` rows under one path line
(`str.partition(':')` and `isdigit()`); drop diff `index` lines (`startswith`). Then content
routing by structure, with the byte gates 512/2048/5120: `json.loads` succeeds → JSON; a
majority of lines `startswith(('+', '-', '@@', 'diff --git'))` → diff; a majority of lines
partition as `path:digits:` → search; a declared runner format → structured summary (P0.4);
otherwise text. Routes: search output gets the 5-per-file / 15-files / 30-total cap with first
and last kept and error lines (lowercased `in` test on a fixed word set) boosted; JSON arrays
over the gate get the `[N]{cols}` table when the rows share keys, else the must-keep set (error
rows, rare fields, rare status values, values > 2σ, first 3, last 2) with a pointer; everything
else proceeds to Jev/companion as today. Kill switch `reduction.folds: false` in the profile.
Owner: `output_reduction.py`, new `structural_folds.py`. Validation: fold determinism tests; a
ratio table on the structured fixtures recorded in the test, not the docs. Effort S–M.

**P2.2 Route by command shape, not first word (D7).** Mechanism. `shlex.split` the Bash line,
segment on the `&&`, `;`, `|`, `||` tokens, and take each segment's executable after the known
wrappers (`uv run`, `python -m`, `npx`, `cd …`, `env`); apply the same noisy set. No regex.
Owner: `runtime_policy.py`. Effort S.

**P2.3 Fleet token ledger with soft lane budgets (D14).** Mechanism + profile. Companion usage
comes from the API response (P0.7); worker usage from the Claude transcript fields (§4.2):
input, cache read, cache creation and output tokens per agent in `metrics.agent_figures`, with
the cache-token normalisation for the adapter flavors; optional price table in `models.json`;
every figure `None` when unmeasured. Profile `budgets.<role>.per_unit_tokens` throttles **new
dispatch** in a lane, never running work. Owner: `native_companion.py`, `metrics.py`,
`plan_dispatch.py`. Effort S.

**P2.4 Stale-read pointers at re-issue (headroom).** Mechanism. When a packet is re-issued
after a seam, files that were read and then edited in the same launch are listed as pointers to
their current digest rather than repeated; whole-file delivery is unchanged on request. Owner:
`compaction_policy.py`, `context_packets.py`. Effort S.

**Deferred, with the reason:** MMR/recency rerank and embeddings (no measured lexical ceiling;
ruflo's own HNSW audit is 1.48×); a source watcher (pull freshness at admission is already
green once P1.4 registers stubs and P0.1 fixes roots); Kompress/CodeAware (model-based,
non-stdlib, and the companion already rewrites context); hibernation/resume (re-bills context);
dynamic model routing (static role tiers in the profile until P2.3 produces net numbers).

## 6. Measure before tuning

A `tests/scale/` lane on `tests/fakeclaude`: 60 concurrent runs across 2 roots on one host,
driven by the executor, with checks of 5–60 s and companion passes stubbed at 2 s. Record
ready→running latency, completion lag, companion queue depth, hook p95 latency and denial
count, pool wait vs job time, repo-service queueing, duplicate executions. Acceptance for
"sustained 50–60": ready→running p95 < 2 ticks, completion lag p95 < 30 s, hook denials 0,
companion lag bounded and non-growing, build wait ≤ build time, zero duplicate equal-input
jobs, search dedupe hit-rate reported per commit. Profile numbers for a real box come out of
this lane plus `hx metrics --sizing` after a calibration wave, never from assertion.

## 7. Sequencing and sessions

1. **P0.4, P0.5, P2.2** (S each): one session, one commit each; no design risk.
2. **P0.1** (M): own worktree session; touches the graph store and all readers.
3. **P0.2 + P0.3** (M): own worktree session; the loop and the ledger openers.
4. **P0.7** (M): own worktree session after 1 and 3; script companions and the cache-aware seam.
5. **P0.6 + P1.1 + P1.5** (M): own session after 3; the executor needs the de-serialised loop.
6. **P1.3** (M) and **P1.2** (M): parallel sessions after 5; pools and provisioning are disjoint.
7. **P1.4** (L): after 2; the graph-side lane machinery and stub registration.
8. **P1.6, P1.7, P2.1, P2.3, P2.4**: parallel S items once 5 and 7 land.
9. The scale lane (§6) is written alongside 5 and gates 6–8.

### Scope cut, 2026-10-21

With P0.1–P0.7, P1.1, P1.5 and P2.2 merged (main 6cc21fa), the author asked whether the rest
adds efficiency, speed or width specifically. Judged on that alone:

- **Kept:** stub registration in the graph and the graph-derived provider/wave report (P1.4,
  without Louvain lanes or the contract-wave template); joining equal-input builds and checks,
  build slots from config, and the commit-keyed search cache (P1.3, without project-named pools
  or waiter queues); the token ledger with input/cache tokens per agent and companion spend
  (P2.3, without lane budgets until there is data); the pre-dispatch `claim_command` in the
  executor (the P1.6 half that saves whole units).
- **Cut or deferred:** fleet provisioning, allocator, inbox and fleet status (P1.2: operator
  ergonomics, the 13-instance procedure works by hand); the receipted reuse section in every
  binding (P1.6: a step on every unit's critical path, and the conversion skill already puts
  the `--expect absent` lines in the definition of done); structural folds (P2.1: only
  `hx tool-exec` output is reduced and it is already capped); stale-read pointers (P2.4);
  the sizing report (P1.7) until there is data; a separate scale lane (the P1.1 session
  shipped a 60-run scale test).

## 8. Decisions

- No external scheduler, graph or compressor is imported; patterns are re-implemented in stdlib
  Python at the seams named above. `httpx` stays for Jev and the companion API; Jev is never
  on the critical path.
- Claude Code is the default harness (§4.2); other flavors are adapters and never shape a core
  mechanism.
- No regex as a mechanism (§4.3); structured sources first, model classification second.
- Companions are scripts: one bounded API exchange, no pane, no session (§4.4). Seams are
  decided from cache state and context size, never from a fixed interval.
- Caps are box budgets from the profile, derived by measurement, not caution numbers like 3.
- Mechanism/profile/project split (§4.1) is mandatory for every item; a PR that hard-codes a
  project value in `src/hx` is wrong by definition.
- Worker surface is unchanged: every new command (`hx plan run|partition`, `hx fleet
  provision|status|pause|resume|inbox`, `hx alloc`, `hx metrics --sizing`) is operator or Partner
  audience in `cli.AUDIENCES`.
- Breaking changes only: the two FTS tables, the tree-sitter gate, the single-slot indexes, the
  per-plan prerequisite scope and the first-word gate are removed, not shimmed.
- Not copied, by name: ruflo's tool surface, consensus, bandit router and level barriers; orca's
  single-worktree coordinator, racing, hibernation and Electron shell; headroom's proxy/wrap,
  ML models, CCR tool, telemetry and `learn`.

## 9. Risks

- P0.1 changes the meaning of every `hx code` read; the two-worktree test is the contract and
  the benchmark must not regress warm query latency.
- P0.2 widens the window between hashing and transacting; admission must re-verify digests
  inside the transaction (cheap) rather than trust the pre-hash.
- P1.1 can double-dispatch across a reconciled tick without the idempotent key; the key is not
  optional.
- P1.3 joining a running job shares its failure too; receipts must record the joiner.
- P2.1 folds cost output tokens when a reasoning model rebuilds the folded shape; the kill
  switch exists for that reason.

## 10. Unverified

- D5 (double Read delivery) and the companion cache miss (D14) are inferred from code; verify
  with one live Claude run each before P0.5 and P2.3.
- No external repo was executed; orca was read as a sparse `src/` checkout, headroom and ruflo
  as shallow clones at their 2026-10-17/19 heads.
- Deployed instances' companion model configuration and the Natural → C# profile values are
  outside this repository.
- hx has not run the packet admission flow, the executor path or either companion end to end
  with a live model since the 2026-10-15 redesign; the scale lane (§6) is on fake sessions.
