# Fleet throughput plan: ruflo, orca and headroom against hx as coded

**Method:** three inspections over `src/hx` (parallelism, graph/search, token economy) and three
over the external repositories' source (not their READMEs). Every "hx has X" below cites
`src/hx/<file>.py:<line>`; every external claim cites the inspected upstream file. Nothing
external was executed. hx docs were used only as a map of where to look; status comes from code.

## 0. Verdict

Nobody has a scheduler, a code graph or a reuse gate worth copying. hx is ahead of all three on
graph, admission proofs, contract-first dependencies and companion tiering. What the externals
contribute is narrow and concrete: from **headroom**, four stdlib-sized compression patterns and
one architecture rule about the provider prompt cache; from **orca**, five host-protection
mechanisms (pre-warmed checkouts, restore admission, process-growth breaker, stale-base refusal,
pointer injection); from **ruflo**, three disciplines (writer/reader split per level, a user-wide
launch ledger with a quota breaker, idempotent job keys). Everything else in those repos is
either marketing their own audits contradict, or things hx already does better.

Every item in the plan (§5) is split into **mechanism** (hx core, once), **profile** (data a
project sets at install), and **project script** (generators and queries hx calls but never
contains), per §4.1, so the next application tunes a profile instead of re-deriving a procedure.

Three principles shape every item (§4.2–4.4): **Claude Code is the default harness** and the
other flavors are adapters that must not shape core mechanisms; **no regex as a mechanism**,
structured sources and model classification instead; and **the companions are scripts, not
agents**, with a seam policy that clears everything when it must and otherwise exploits the
moments the prompt cache is already cold to install a targeted context for free.

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
searched, not present.

### 2A. Parallelism, claims, stubs

| Need at width 50–60 | Status | Evidence |
|---|---|---|
| Seat provisioning | **Absent** | The executor picks an idle configured seat by role from `config/*/harness.json` (`plan_dispatch.py:123-125`), but nothing generates seats: 60 agents = 60 hand-written harness files + 60 worktrees. |
| Contract-first early start | **Have, after publication** | Producer: bind → independent approve → commit → `hx check` → `publish-contract` (current receipts covering the output, drained producer; `dependency_packets.py:215`); auto-publish at `complete` (`unit_execution.py:363-367`). Consumer admission requires the publication (`contract_proof`, `dependency_packets.py:194`), then `acquire-contract` (`:280`), `packet_covered` → separate integration unit → `verify-integration` → `deployable`. **Nothing makes publication early**; no bulk stub command; no contract-wave validator. |
| Claims | **Internal only** | Write leases at `bind` (`unit_execution.py:226-234`); check claims (`check_artifacts.py:21-26,59-67`); equal-input check and .NET claims on the per-user host ledger join a live equal execution (`host_jobs.py:81-113`, `checks.py:238-256`, `dotnet_build.py:370-400`); outbox claims (`continuity_store.py:607-638`). The only external call is read-only `gh` in `handoff.py:60-64`. No pre-dispatch claim gate; workers cannot claim work at all. |
| `admit()` atomicity, resources | **Have** | `start_run` runs `admit`+`bind` inside one `BEGIN IMMEDIATE` (`continuity_store.py:182,314-324`); rechecks head, completion, workdir, leases (per repository id), resources, prerequisites, and that the worktree is unchanged since freshness was read before the writer (`unit_execution.py:166-223`). Resources are `shared`/`exclusive` only (`dependency_packets.py:149-156`); no counted capacity. |
| Isolated checkouts | **Have, no pool** | `repo_service.py:291-294` `git clone --shared --no-checkout` + `sparse-checkout set`; no `git worktree add`, no pre-warming. Service: `BoundedSemaphore(--workers)`, default 4, 1–64, acquired on the accept thread so a full pool stops accepting (`:366-384,403-406,480`); every request except `status` adds its elapsed time to its method's latency row in SQLite (`:153-161`). |
| Cross-instance view, pause, inbox | **Absent** | `board.snapshot(root)`, `metrics.collect_fleet(root)` read one root (`board.py:57-67`, `metrics.py:86-87`); no INBOX helper in code; `hx fleet pause|resume` toggles one root's `run/dispatch-paused` flag (`fleet.py:24-29`, `plan_dispatch.py:114-115`). |
| Shutdown census, retained leases | **Have, stronger than any external** | `native_shutdown.py:30-99` SIGSTOP-census-SIGKILL quanta; no auto-replay of uncertain paid work (`plan_dispatch.py:233-260`). |

### 2B. Code graph, search, reuse

| Need | Status | Evidence |
|---|---|---|
| Structural graph, immutable snapshots | **Have** | `code_store.py:27-61` repos/files/symbols/refs/edges/projects/project_edges/contracts/exclusions/metadata, WAL (`:78`); content-addressed `source_objects`/`extraction_objects`/`graph_objects` (`code_snapshot.py:21-24`); persistent-treap `snapshot_nodes` (`:52,272`); `snapshot_refs` (`commit:<sha>`) (`:56`) and `checkout_heads(root)` (`:55`). DB at `state/code-index/index.db` (`code_store.py:65-66`), refused inside the product repo (`code_build.py:624-625`). |
| Extraction lanes | **Have** | Python stdlib `ast` (calls, references, imports; **no inherits**); `braces()` regex for .cs .ts .tsx .js .jsx .go .rs (calls, references, registers, implements from class headers, imports); tree-sitter CLI, version recorded and never gated (`code_adapters.py:67`), one `tags` subprocess per file per grammar tried until one yields rows (`:215-223`); SCIP lane=resolved needing a clean checkout at the same HEAD (`code_scip.py:244-262`); Natural (`.nat .nsp .nsn …`: objects/routines/data, CALLNAT/FETCH/PERFORM as refs); JSONL import. Call edges are built at query time by name match and every edge is `status="candidate"` (`code_queries.py:353,362-400`). |
| Ranking | **Have** | Exact-symbol hits score `10 - LANES[lane]` and always beat FTS (`code_queries.py:11,82,94`); RRF `1/(60+i)`; proximity ×1.5 ≤ 100 chars (`:154`); Levenshtein fallback only on zero hits over the **first 5000 names alphabetically** (`code_snapshot.py:1003-1015`); CAP 100; `sift` is a remote Jev re-sort that never drops. No embeddings anywhere. |
| Incremental index, invalidation, freshness | **Have, serial** | Candidates = git delta + the base snapshot's dirty and stale paths, skipped on sha256+generation+mode (`code_build.py:682-706,775-783`); mtime only detects mid-read change (`:728-737`). `invalidate()` reverse closure (`code_invalidation.py:39-56`); `apply_changeset` journaled, `BEGIN IMMEDIATE`, optimistic check of this checkout's `checkout_heads` binding, 4 retries (`code_build.py:265,354,368-379,966-977`). Indexing is `for rel in sorted(names)` under a per-namespace lock stripe (`:819,953-957`); one file over `HX_CODE_MAX_FILE_BYTES` (512 KiB) aborts the index (`:16,629,747-752`). Freshness is pull-based (`code_freshness.scope/require`); no watcher. |
| Reuse queries | **Have, unique** | `code_reuse.py` precedent/conform/tests_for/register; `code_index --expect exists|absent|one|clean|fresh` exit 3; `hx code contract NAME [--task]` joins ledger publications with the manual contracts registry. |
| Search-before-build enforcement | **Have** | Every search a run makes is receipted (`search_receipts.py`): a native Grep/Glob at settlement or when the box cache serves it (`native_tools.py`), an `hx code` query from the worker's shell (`cli.py`). A focused `hx code map` enumerates every declaring file under its focus and reports `complete` (`code_analysis._map`). The binding cites a complete area map of every packet search area, the directories of its write paths and map anchors, and `hx plan bind` refuses anything else (`dependency_packets.bind_checks`); `runtime_policy.inspect_call` refuses the first edit before approval. Ownership is the guard against duplicate work: overlapping write scopes refused at validate and admit (`planning.py`, `unit_execution.py`). |
| Transcript corpus, clone detection | **Absent** | `content_store` (porter+trigram+title, RRF) is fed by `search_exec` output; native transcripts are decoded into ledger `events` (`observer.py:291-332`) and tail-read for usage (`transcripts.py:18-47`), never indexed for search. `span_sha` used only for staleness; the only near-duplicate test is learnings Jaccard/sequence-ratio 0.8 (`learn.py:85,336-341`). |

### 2C. Token economy

| Need | Status | Evidence |
|---|---|---|
| Tokenizer, units | **Absent, mixed** | `context_packets.py:22` `len(text.encode())`, none of 7 production `issue()` callers pass `count_tokens`; 4 B/token at `file_limits.py:19`, 4 chars/token at `stepstate.py:58`, and `optional_tokens * 4` inside the same function (`context_packets.py:261`). The seam uses provider-reported tokens (`token_controls.py:103-118,219-245`) and then subtracts byte reservations (`file_reads.py:200-209`). |
| Tool-output reduction | **Have, `hx tool-exec` only** | `output_reduction.py:13-16`: 4096 B out, 768 B raw chunks, Jev sees ≤ 8192 B of non-diagnostic chunks, > 4 batches falls back to diagnostics, then the tail, then the head (`:141,160-170`); a UTF-8 character split by a chunk boundary is carried into the next chunk (`:67-69,86`); a chunk is a diagnostic when it names error/fail/exception/traceback/panic/assert or holds a line starting `E ` (`:18-25`). `test_summary.py` parses 5 declared report formats (`:29-30`), never text output; nothing strips ANSI. Native Bash/Grep/Read results reach the model raw; routing splits the command on `&&`, `;`, `|` and parentheses and looks past `NAME=value`, `env`, `npx`, `uv run` and `python -m` (`runtime_policy.py:60-103,215-218`): `bash -c "pytest"` and `timeout 60 pytest` pass. `tool-exec` kills at 60 s / 1 MiB (`tool_execution.py:14,56`), unrecoverable. |
| Seam before native compaction | **Have** | Threshold `min(model.threshold, window − output − min(4096, window//10))` (`token_controls.py:42`); `guard` refuses every request at the threshold (`:121-126`). The compaction prompt (2,047 B) is installed as every worker's `config/CLAUDE.md`, resident every turn (`native_launch.py:43-45`). |
| Metrics | **Have, partial** | `metrics.py:27-83`: per-agent `chat_bytes`, output tokens (Claude max per `message_id`; Codex, pi, meta per record), turns, from ledger `events` with `stream_id LIKE 'native:%'`; `None` when unrecorded. Companion and learning passes record per-response input/output/cache tokens in their job rows and, for the Partner, `run/<id>/companion-usage.jsonl` (`companion_api.py:34,101-106`, `native_companion.py:207-214`, `learning_companion.py:233-240`, `companion.py:79-80,280-286`); Jev token usage is kept in `retrieval_runs` (`jev.py:100`, `jev_decisions.py:43,49`). `metrics.py` reports none of these: no input/cache tokens, cost, companion/learning/Jev spend; no price table, no per-lane spend budget, no spend throttle. |

## 3. Open defects

Levenshtein over the first 5000 names (`code_snapshot.py:1003-1015`); one file over
`HX_CODE_MAX_FILE_BYTES` aborts an index (`code_build.py:747-752`); `facts.select_facts` and
`runtime_policy.CONTEXT_BYTES` are dead outside tests; `hx loop --status` pages 16 runs
(`application_loop.py:433-435`).

## 4. Principles

### 4.1 The systematization rule

Every item in §5 is split three ways, and the split is the deliverable as much as the code:

- **Mechanism** lives in `src/hx` once, with the same shape for every application: executors,
  pools, allocators, gates, registries, reports. It never contains a project's language, forge,
  tracker, naming scheme or numbers.
- **Profile** is data a project sets at install and tunes from measurement: the instance's
  `config/hx.json` (`loop_jobs.profile`), in sections such as `dispatch`, `pools`, `loop`,
  `companion`, `seam` and `partition`. It holds box budgets, pool sizes, partition rules, and the
  *commands* hx calls for project-specific steps.
- **Project script** is what those commands point at: stub generators, external claim queries
  (forge PRs, tracker items, team branches), prechecks, database provisioning. hx runs them at
  the right time, receipts their output, and refuses on failure; it never contains them.

The Natural → C# tweaks in `EXAMPLE.md` map onto this as follows. Each row is a profile field or
a project command once §5 lands; none stays a convention.

| EXAMPLE.md tweak | Today | Systematized as |
|---|---|---|
| Inventory from the system-map graph ∩ handlers ∩ tracker claims (711 → 500 WPs + 21 providers) | `hx plan partition` over the graph and `hx code import` facts; tracker claims by project script | profile `claim_command` filtering candidates (P1.6) |
| Minute-0 contract commit per wave (`I<X>Service.cs`, `<P>Contract.cs`, `<P>Seed.cs` per program) | project generator, unenforced | contract-wave template unit (deferred, §5): profile `stub_command`; hx refuses the wave until `--expect exists` holds for every declared contract |
| Pre-allocated ISN blocks, DB names, port arithmetic | convention + in-goal checks | `hx alloc <namespace> --lane L` from profile ranges (P1.2, deferred) |
| 13 instances, tmux server per instance, UI ports 8800+idx, per-lane worktrees | documented manual procedure | `hx fleet provision --profile` (P1.2, deferred) |
| 4-source claim pre-check + `check-claims.py` inside each goal | inside each goal, or an unreceipted `dispatch.precheck` | profile `claim_command`; executor runs it before `assign` and requires a receipt (P1.6) |
| 88 eng / 33 QA / 13 rel, 4.2 core-h/WP, ≤ 45 engineers per box | asserted | `hx metrics --sizing` from recorded durations (P1.7, deferred); values go back into the profile |

### 4.2 Claude Code is the default harness

Every core mechanism is designed for Claude Code first: its native Read is the whole-file
delivery path, its transcript usage fields (`input_tokens`, `cache_read_input_tokens`,
`cache_creation_input_tokens`, `output_tokens`) are the ledger's primary token source
(`token_controls.py:234` sums them), its prompt cache is the cost model the seam policy
reasons about (§4.4), and the companions run on Claude models through the Messages API.
Codex, Pi, Grok and Meta are adapters under `skeleton/adapters/`; a mechanism that only exists
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

Existing regex debt is replaced, not patched: the `braces()` lane in `code_extract.py` is
demoted to last resort (a C# profile runs SCIP + MSBuild, and `hx code stats` reports the lane
per file). Headroom's folds are regex-based; hx's deferred folds (P2.1) are equality- and
prefix-based.

### 4.4 Companions are scripts; seams are cache-aware

The companion is a deterministic transform, not an agent: a frozen, bounded ledger slice in,
a validated operations patch out (`passes.py` create/supersede/compress/invalidate/drop,
committed by `passes.commit`), no session, no pane, no tools beyond reading the evidence it is
handed.

The seam policy: **when necessary, clear everything**, including what is lost from the Claude
prompt cache, and re-issue the packet; **otherwise, as soon as the cache is cold anyway, there
is no reason not to install an extremely efficient targeted context whenever that is beneficial
as the session nears its limit.** A turn that runs on a cold cache re-writes the whole prefix
regardless, so replacing the prefix with a smaller targeted packet at that moment costs nothing
extra and buys back the headroom; rewriting inside a warm cache pays the cache-write multiplier
to lose the read discount (headroom's prefix rule, `prefix_tracker.py:6-9`). Three regimes, all
decided from data the ledger already captures:

| Regime | Condition | Action |
|---|---|---|
| **Hard seam** | context ≥ threshold (`token_controls.py:42`) | `/clear`, re-issue the packet; cache loss accepted |
| **Opportunistic seam** | context ≥ `seam.soft_fraction × threshold` **and** the cache is cold: the last turn's usage shows `cache_read ≈ 0` with `cache_creation` ≈ prefix, or idle time since the last assistant message ≥ the cache TTL recorded in the profile | `/clear`, install the targeted packet; no extra cost because the prefix would be rewritten anyway |
| **Hold** | cache warm, below hard threshold | never rewrite; the companion keeps the next targeted packet ready in the ledger so either seam is instant |

## 5. Plan

Effort: **S** ≤ days · **M** 1–2 weeks · **L** > 2 weeks. Every item names its owner module, its
surface, its bounds and its validation, and adds efficiency, speed or width. Breaking changes
only: remove the old path, add no migration, compatibility or fallback code.

### P1 — throughput mechanisms

**P1.6 Claim gate before `assign`.** Mechanism + project script. The executor runs the profile
`claim_command` (project script: forge PRs, tracker items, team branches) before `assign` with
the unit's write scope and outputs on stdin, receipts its output, and refuses on non-zero, so
duplicate effort is caught before a seat or tokens are spent. It sits beside the role's
`dispatch.precheck` (`plan_dispatch.py:276-286`), which gets the unit without its outputs and
keeps only the exit code and stderr tail as a deferral reason. Owner: `plan_dispatch.py`.
Validation: claim refusal leaves the unit ready, not failed. Effort S.

### P2 — token economy

**P2.3 Fleet token ledger.** Mechanism. Companion and learning usage comes from their job rows
(§2C); worker usage from the Claude transcript fields (§4.2): input, cache read, cache creation
and output tokens per agent in `metrics.agent_figures`, with the cache-token normalisation for
the adapter flavors, and companion, learning and Jev spend beside them; optional price table in
`models.json`; every figure `None` when unmeasured. Owner: `metrics.py`. Effort S.

**Deferred, with the reason:** fleet provisioning, allocator, inbox and fleet status (P1.2:
operator ergonomics, the 13-instance procedure works by hand); structural folds (P2.1: only `hx tool-exec`
output is reduced and it is already capped; folds also cost output tokens when a reasoning model
rebuilds the folded shape); stale-read pointers at re-issue (P2.4); the sizing report (P1.7) and
per-lane token budgets until there is data; Louvain lanes, the contract-wave template,
extractor lanes as a profile choice (`graph.lanes`) with the per-file lane in `hx code stats`,
project-named pools and waiter queues (none adds efficiency, speed or width); MMR/recency rerank
and embeddings (no measured lexical ceiling; ruflo's own HNSW audit is 1.48×); a source watcher
(pull freshness at admission suffices: published stubs are in the graph and every query answers
for the caller's checkout); Kompress/CodeAware (model-based, non-stdlib, and the companion
already rewrites context); hibernation/resume (re-bills context); dynamic model routing (static
role tiers in the profile until P2.3 produces net numbers).

## 6. Measure before tuning

`tests/core/test_plan_dispatch_scale.py` runs the executor at the operating point: 62 ready
packets across 2 roots on one host, each launch a `tests/fakeclaude` session on a private tmux
server, asserting ready→running p95 < 2 ticks, zero double launches and no budget exceeded.
Acceptance for "sustained 50–60": ready→running p95 < 2 ticks, completion lag p95 < 30 s, hook
denials 0, companion lag bounded and non-growing, build wait ≤ build time, zero duplicate
equal-input jobs, search dedupe hit-rate reported per commit. Profile numbers for a real box
come from recorded durations after a calibration wave, never from assertion.

## 7. Sequencing

P1.6 and P2.3 are independent S items in disjoint modules (`plan_dispatch.py`, `metrics.py`):
parallel sessions, one commit each.

## 8. Decisions

- No external scheduler, graph or compressor is imported; patterns are re-implemented in stdlib
  Python at the seams named above. `httpx` serves Jev and the companion API; Jev is never on
  the critical path.
- Claude Code is the default harness (§4.2); other flavors are adapters and never shape a core
  mechanism.
- No regex as a mechanism (§4.3); structured sources first, model classification second.
- Companions are scripts: one bounded API exchange, no pane, no session (§4.4). Seams are
  decided from cache state and context size, never from a fixed interval.
- Caps are box budgets from the profile, derived by measurement, not caution numbers like 3.
- Mechanism/profile/project split (§4.1) is mandatory for every item; a PR that hard-codes a
  project value in `src/hx` is wrong by definition.
- The worker surface does not grow: `hx plan run|partition` are Partner audience and
  `hx fleet pause|resume` operator audience in `cli.AUDIENCES`.
- Not copied, by name: ruflo's tool surface, consensus, bandit router and level barriers; orca's
  single-worktree coordinator, racing, hibernation and Electron shell; headroom's proxy/wrap,
  ML models, CCR tool, telemetry and `learn`.

## 9. Unverified

- No external repo was executed; orca was read as a sparse `src/` checkout, headroom and ruflo
  as shallow clones at their 2026-10-17/19 heads.
- Deployed instances' companion model configuration and the Natural → C# profile values are
  outside this repository.
- hx has not run the packet admission flow, the executor path or either companion end to end
  with a live model; the scale test (§6) runs on fake sessions.
