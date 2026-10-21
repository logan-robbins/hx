# Changelog

All notable changes to hx are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Until 1.0.0 the public surface is the `hx` command set of `spec/08-hx-cli.md`, the instance
layout of `spec/03-layout.md`, and the JSON shapes in `CONTRACTS.md`. A breaking change to any
of those is a minor bump and gets a **Changed** entry saying what an existing instance must do.

## [Unreleased]

### Added
- `hx.credential_mask` (issue #5, docs/fleet-throughput-plan.md P0.6a, D15): the exact
  credentials hx loads — `seed/token`, which `start.sh` exports as `ANTHROPIC_API_KEY` or
  `CLAUDE_CODE_OAUTH_TOKEN`; the `envfile.SEED_KEYS` providers from the environment, the
  instance env file and `seed/`; and the Jev key — are masked by equality, never by pattern
  (§4.3), before a ledger event is hashed and stored, before an observation or a captured
  check/tool output becomes an artifact (the observation before its identity, the capture as it
  leaves the pipe, and every installed file chunk by chunk with a carry across the 64 KiB
  boundary), before a hook stream record is written, and before any Jev request is serialized
  (`jev.encode`). Each value is also masked in its three base64 alignments. Masking keeps
  length (`*` runs), so sizes, offsets, chunk hashes and archives stay valid; values under 16
  characters are left alone. The set is loaded once per process from the named keys only; a
  rotation is seen by the next process, and a capture repeated after one stays idempotent.
  Limits: the worker's own transcript and pane log hold what it printed, and ledger rows other
  than events (task payloads, recipes, receipts, records) are written as given.
- `hx compose --max-tokens` defaults to 16000 (`compose.DEFAULT_MAX_TOKENS`), not 8000: a compiled
  codex/meta assignment is about 8.3 KB and is counted in bytes when no tokenizer is present, so
  the default must hold the system's own largest native delivery. The live native path still
  budgets with the model's real input limit.
- Per-agent chat metrics (docs/prompt-runtime.md): `hx metrics` adds `agents`, each worker's
  launch `flavors`, `chat_bytes`, `output_tokens` and `turns` (model responses) from captured
  main-session events — Claude transcripts, Codex rollouts, Pi `message_end` records and meta
  `model_response` hooks; a figure no captured record reports is `null`, never estimated. Claude
  transcript assistant events carry `message_id` so one response's repeated usage counts once.
- Test-run summaries (CONTRACTS.md § Scoped check execution artifacts): `checks.execute` parses
  captured output once with `hx.test_summary` (pytest, unittest, jest, vitest, go test, cargo
  test, dotnet test) into `summary` — `framework`, printed `passed`/`failed`/`skipped`/`errors`
  counts or `null`, at most 20 `failing` names and `truncated` — or `null`. `hx check`, the
  `check_result` and `tool_result` events and `hx tool-exec` carry it. When it reports a
  failure, output reduction keeps chunks naming a failing test first, then other diagnostics,
  then head chunks, within the same bounds.
- `hx memory warm` (operator): the one-time download of the embedding model into
  `~/.cache/chroma/onnx_models`. Every other chroma open now requires the model already on disk
  and otherwise raises `MemoryUnavailable` naming the command — an agent at a boundary never
  downloads. Before this, chroma's default embedder fetched the 80 MB archive from S3 under
  whatever `HOME` the process had: in the test suite that was every scratch HOME, which is why
  `hx resume` tests sat idle for minutes. `hx doctor` gained the `memory-model` row. The core
  tests' scratch HOMEs share the operator's `~/.cache/chroma` by symlink.
- The test suite runs on every core (`pytest-xdist`, `-n auto` in `pyproject.toml`; the `test`
  extra installs it). The core lane went from 32 min serial to about 6 min on eight workers.

- Search and retrieval (docs/search-and-retrieval.md): `hx code` indexes symbols, imports,
  and source text (Porter + trigram FTS5, reciprocal rank fusion, proximity rerank, fuzzy
  fallback) with `search`, `symbols`, `impact`, and candidate graph `edges`; `hx content`
  is a persistent exact-text store for docs and web (`index`, multi-query `search`, SSRF-
  hardened TTL-cached `fetch`, `stats`); `hx exec` runs Python/JavaScript/shell in isolated
  subprocesses where only stdout returns, with intent indexing over 5 KB and pointer-only
  output over 100 KB, plus `exec file` (`FILE_CONTENT`) and concurrent `exec batch`;
  `hx sift` applies Jev (`jev-1.13.0`) semantic sifting, failure triage, and check ordering
  over deterministic candidates — advisory only, degrading to `unsifted` on provider failure.
- Discipline and prompt caching: `hx discipline profile/orient/commit` derives module-size
  bounds and orientation load from the repo itself and judges whether a diff is one
  committable unit; `hx plan validate` now reports waves, level widths, max parallelism, and
  interface-first warnings; `hx cache assess/budget` makes prompt-cache TTL and the invariant
  budget explicit, with companion passes append-only while warm and restructuring when cold;
  `hx learn classify/repeat/invariant` classifies at the moment of learning, detects a
  repeated mistake against existing rules, and charges learnings against the invariant budget.

- `hx learn`: the central learnings — `LEARNINGS/<category>.md`, one file per fixed category
  of mistake (`hx learn categories`: orientation, ownership, planning, domain, build, testing,
  runtime, evidence, delivery, harness, environment) — and the only way into them
  (docs/learnings.md). An agent proposes a rule (`propose --category <c> --signal … --check …
  --evidence …`) the moment something trips it up; the proposal is filed next to the rules it
  most resembles, in any category, and never touches a rule file. A different session — the
  Partner on its next wake, a reviewer, the human — accepts it as a new numbered rule in that
  category (or refiles it with `--category`), or merges its evidence into the rule that already
  says it, or rejects it with a reason; the author's own session is refused. `check` lints the
  directory (the eleven files, shape, numbers unique across files, nothing outside the
  taxonomy, near-duplicate pairs across files), `pending` is the reviewer's worklist, `list`,
  `categories` and `path` read. The skeleton ships the eleven empty files; `config/hx.json`
  `learnings` lets a fleet point every instance at one shared directory.
- Every agent carries every learning in its system prompt: `hx compile` writes a `learnings`
  region into every persona — the Partner's included — with every rule whole under a heading
  per category, so the rules are present at every start and after every compaction with no
  Read, and cached with the invariant prompt. `hx heartbeat` reports `learnings_pending` and
  wakes the Partner once per new batch; `hx doctor` has a `learnings` line with the count per
  category, warns `learnings-size` above 200 KiB of region and `learnings-legacy` while a
  single `LEARNINGS.md` remains.
- `hx heartbeat --require-go`: the fleet go-gate in hx itself. Without a `GO <date>` line in
  `PARTNER.md` the heartbeat launches, restarts and wakes nothing (`HX-HEARTBEAT
  gated=no-GO-line`), so a fleet's cron can be installed before approval.
- `hx handoff check|capture|pushed|sums`: the handoff contract of `docs/multi-instance.md` §6
  as a command, with a `Delivery status:` line (`LOCAL_GATE_PASSED`, `CI_PENDING`, `CI_FAILED`,
  `CI_GREEN`) that must agree with the checks captured after the last push — a push starts CI,
  it does not pass it — and a refusal of any PR captured as a draft.
- `hx doctor` fails `bin-path` when an adapter's `config/<adapter>.json` `bin` names a
  versioned path: launchers auto-update and delete the old version, and every launch then fails.
  `hx install` no longer resolves the `claude` launcher's symlink when it records `bin`, for
  the same reason: the native installer's `~/.local/bin/claude` points into
  `~/.local/share/claude/versions/<v>`, which its auto-update deletes.

### Changed

- `hx content fetch` no longer redacts by pattern: `content_store.redact(text, root)` masks the
  loaded credentials exactly and the `authorization|token|secret|password` regex is deleted (it
  missed `ANTHROPIC_API_KEY=…`, since `\b` does not fire after `_`, and `{"token": "…"}`;
  issue #5). `hx models` masks with the same mechanism (`*` runs, not `[redacted]`).
  `jev.encode` and `jev.evaluate` take the instance `root`, `checks.execute` takes the
  instance mask, `observer.encode_observation` takes `root`, and `envfile.SEED_KEYS` is public.
  `jev` imports `httpx` only when it calls. Nothing to do for an existing instance.
- Claude's native Read is the whole-file delivery (docs/fleet-throughput-plan.md P0.5, D5). A
  live check with the real Claude Code 2.1.299 binary against a recording Messages API showed
  the native Read body stayed in the next request alongside hx's hook segments, so every
  read reached the model twice. Native Read caps at 25000 tokens (a 3000-line file came back
  as lines 1-1349 with `truncatedByTokenCap`), and `CLAUDE_CODE_FILE_READ_MAX_OUTPUT_TOKENS`
  raised to the limit returns the whole file. Hook `additionalContext` over about 10000
  characters collapses to a 2 KB `<persisted-output>` preview, and CRLF is shown as LF.
  `start.sh` now sets that variable to `HX_CONTEXT_THRESHOLD`, which bounds every admitted
  read. The worker hook verifies `tool_response.file.content` against the admitted bytes and
  adds only `Complete file: sha256=…; bytes=…` (`file_read_delivery.native_marker`); a capped,
  failed or different result fails the delivery and holds the barrier until a complete retry.
  Claude workers install one hook entry per event (a Read or Bash fires three, not about 22) and
  are refused the shell brokers (`hx file-read`, managed `hx repo file`), whose stdout is only a
  marker: they Read the file, materializing another revision first. Segments remain only for
  Grok, Meta and the Partner. Required material parts are each their
  own complete read: part 1's footer lists every part, each footer names
  `Read <next_path> next; part k of n`, and the chain counts as delivered only when every part
  has its own receipt (the gate names the next unread part, and `native_tools.pending` holds
  completion, reset and restart). Receipts hash on-disk bytes, never the native body. Removed:
  `prompt_compiler.material_bundle`, `context_packets.material_bundle` and whole-chain
  resolution of one part. An existing instance replaces its `adapters/claude/install.sh` and
  `start.sh` with the package skeleton's before its next launch (delete them and rerun
  `hx install --skeleton-only`, which never overwrites).

- A worker's Bash command is routed by its shape, not its first word
  (docs/fleet-throughput-plan.md P2.2, D7). The line is split with `shlex` on `&&`, `;`, `|`,
  `||`, `&` and parentheses, and each command's executable is taken past `NAME=value`, `env`,
  `npx`, `uv run` and `python -m`, and a git or go subcommand past global options, so
  `python3 -m pytest`, `uv run pytest`, `cd x && pytest` and `git -C x diff` are sent to
  `hx tool-exec` like a bare `pytest`. File readers are refused in any command of the line;
  `rg`/`grep` reading a pipe filters output and stays allowed.

- The seam and output reduction fire without Jev (docs/fleet-throughput-plan.md P0.4, D4, D8).
  `jev_decisions.decide` raises `jev.Unavailable` (no credential, timeout, invalid answer) and
  every caller degrades: Jev delta classification settles the affected events as `unclassified`
  (also past the four-request budget, which no longer raises), so the companion reviews them
  unrouted, cursors advance and the threshold reset fires with no `TYPESAFE_API_KEY`;
  `hx tool-exec` reduction keeps diagnostics, then the tail, then the head inside 4096 B and
  names the reason in `fallback`; `hx tools discover` returns no guess plus the `unranked`
  catalog page. A character split by a 768 B chunk boundary is carried into the next chunk
  instead of dropping both. Test summaries come only from a declared machine-readable report:
  check recipes and `hx tool-exec --report` name `junit-xml`, `trx`, `go-json`, `cargo-json` or
  `jest-json`, file reports bind to an hx-owned `{report}` path outside the checkout, and
  `hx.test_summary` parses them with `xml.etree`/`json`; `summary.framework` is now
  `summary.format` and every count is an integer. The text parsers for pytest, unittest, jest,
  vitest, go, cargo and dotnet output and the `DIAGNOSTIC`/`LEAF` regexes are deleted;
  diagnostics are a fixed word set or an `E ` line prefix on any line, not only a chunk's first.
  Recipes that relied on printed summaries add `report` and the runner's report flag.

- **Every `hx code` read answers for the caller's checkout** (CONTRACTS.md § `hx code`; fleet
  plan P0.1/D1). One namespace now holds N checkout roots: `repos` and `checkout_heads` are
  keyed by (namespace, root), and a read — search, symbols, `--expect absent`, `fresh`,
  `--at`, `callers`, `impact`, `edges`, `map`, `submaps`, `stats`, `contracts --register`,
  `import` — selects the snapshot of `--repo PATH` or the checkout containing the working
  directory, never the last indexer's. Before this, a worktree indexed at the same HEAD as a
  dirty sibling reused the sibling's snapshot, so every worktree saw one agent's uncommitted
  symbols and `--expect absent` and `fresh` answered for the wrong tree. Reads accept
  `--repo`; the shared-service client appends its checkout. Several indexed checkouts with
  different snapshots and no caller among them exit 2 naming `--repo`. Imports, contract
  registrations and resolved (SCIP) rows publish into one checkout's snapshot only. The
  `latest`, `HEAD` and branch snapshot refs are gone; only a clean complete index records
  `commit:<sha>`, and a snapshot ID or full SHA resolves without a checkout. Every snapshot
  records its dirty and stale paths, so a full index converges in one pass from its own
  snapshot or a same-HEAD sibling, including after a checkout reverts its edits. The FTS5
  mirrors of live rows (`code_search`, `code_trigram`) are dropped; search uses the
  content-addressed `source_search`/`source_trigram` only. tree-sitter is no longer gated to
  0.25.10: any `tree-sitter` CLI is used and its version is recorded in the extractor
  generation. Schema version 2: an existing index is refused; remove
  `state/code-index/index.db` and rebuild with `hx code index`.
- Fleet throughput P0.2 and P0.3 (docs/fleet-throughput-plan.md). Ledger schema 22; rebuild
  instances with `hx install`.
  - **Ledger openers.** `ContinuityStore.reader` is query-only and runs DDL-free snapshots.
    `ContinuityStore.attach` opens an installed ledger with no DDL and no writer lock.
    `budget=` bounds each writer-lock wait and raises `LedgerBusy`. `hx evidence`, file
    reads, runtime policy and `hx loop --status` use the reader. Hooks and loop jobs attach.
  - **Long work happens before the writer lock.** Every such site re-verifies its inputs
    inside the transaction:
    - `scan_repo` hashing is done by `unit_execution.prepare_admission`. `start_run(...,
      prepared=)` refuses a planned admission without it, and refuses one whose worktree
      changed after it was scanned.
    - Bundle compilation and configuration hashing run in `native_launch.prepare`, and
      launch-input verification in `native_launch.verify`; `confirm` re-checks them inside
      the writer.
    - Check identity and blob reads run in `dependency_packets.publish_contract`.
    - Compaction runs its context compilation in `continue_context`.
    - `observer.register` and `native_capture.bind` return an existing source or binding
      from a snapshot.
  - **A contended planned hook never denies.** If the writer is not free within
    `hooks.LEDGER_BUDGET_S` (5 s per commit), the hook lets the operation run and writes its
    whole delivery under `state/hook-spool/`. Later hooks of that run queue behind it.
    - Each tick replays the spool in order. A tool start is recorded as admitted
      (`native_tools.record_executed`). A deferred complete-file read is recorded as
      undelivered, so the agent re-reads it.
    - A delivery that cannot be applied becomes the run's capture gap.
    - Until replayed, deferred hooks are capture lag in `native_producer.status` (new
      `deferred_hooks`), so drain, restart, reset and completion wait for them.
    - The guard and intentional refusals still exit 2.
  - **Checks and Companion passes leave the tick.**
    - `hx loop` runs acceptance checks and both Companion kinds on `loop_jobs.Jobs`, a
      thread pool of `loop.workers` threads (config/hx.json, default 4).
    - The tick submits a job and reaps its result on a later tick. A check in flight shows
      as `checking`, and the tick reports `waiting` while jobs run, listing them under
      `companions`.
    - Completion is no longer gated by another run's Companion.
    - `--once` and tests run jobs inline.
  - **Counted Companion slots.** The two unique single-slot indexes are gone.
    `companion.max_concurrent` (default 1) bounds running context and learning jobs together.
    A pass with no free slot raises `SlotBusy` and stays prepared.
  - **Round-robin cursors.** Native restart, token reset, context Companion selection and
    learning selection each rotate on their own cursor (`restart-round`, `reset-round`,
    `companion-round`, `learning-round`), so one stuck pane cannot hold the others.

- Chat is a machine channel (docs/prompt-runtime.md): `continuity/prompts/common.md` states it
  once, so every compiled bundle carries it exactly once. No narration between tool calls,
  explanation, recap, greeting, closing offer or chat question; a needed decision is a typed
  `hx progress` blocker; unavoidable text answers first with payload verbatim; typed progress,
  commits, learning proposals, map findings, Companion records and compaction state stay complete
  statements. `personas/partner.md` makes the Partner's replies to the human the one place a
  sentence is the deliverable; `compaction_policy.PROMPT` says a model reads the summary.
  Rebuild instances with `hx install`.

- Prompt layers (docs/prompt-runtime.md § Prompt layers): every agent's prompt is four layers,
  each from one source — the invariants (`config/CLAUDE.md`), the persona (`personas/<role>.md`,
  one file per role), the learnings (`LEARNINGS/<category>.md`, and `personas/<role>/<category>.md`
  for a role's own rules) and session memory (the work item) — and nothing per id is copied
  from a shared source. The only per-id text is `harness.json` `purpose` and the agent's own
  sections of its work item (`## Tasks`, and with the goal contract below its definition of
  done, orientation and evidence).
  - **Delivery is the `context` hook** (SessionStart, matcher `startup|resume|clear|compact`):
    at every boundary it prints the persona, the identity line `You are <id> in pod <pod>.
    <purpose>`, every rule file that holds a rule, whole, and one pointer line naming the work
    item (`PARTNER.md` for the Partner; `hx.persona.IDLE_LINE` with no task in progress).
    `hx persona ID [--summary]` prints exactly that, writing nothing. The output is bounded —
    10,000 bytes on Claude, whose SessionStart output is capped at 10,000 characters, 32 KiB
    elsewhere — and past the bound the rule files, then the persona, are listed as paths to
    read once instead (`hx doctor` warns `hook-size`). The invariants and the persona each have
    exactly one channel per flavor (`config_harness.INVARIANTS_CHANNEL`, `PERSONA_CHANNEL`):
    Claude reads its home `CLAUDE.md` (refreshed by `start.sh --exec`) and the persona from the
    hook; Codex reads both from `$CODEX_HOME/AGENTS.md`, written at launch; Pi and Grok take
    the persona file through their launch flag and the invariants from the hook; Meta takes
    both from the hook. No Claude worker or Partner launch passes `--append-system-prompt-file`
    any more, so a persona or `purpose` edit reaches a Claude agent at its next boundary.
  - **Session memory is the work item.** `hx compose <id>` rewrites a `## Status` section in
    the `-working` item (and at the end of `PARTNER.md`) at every boundary — step state, memory
    episodes, open subagent handles as `###` parts — and the main stream has no context file
    any more: the boundary's one Read is the work item. Subagent streams keep their own
    context file (`run/<id>/<stream>.context.md`, named by the `SubagentStart` hook), which
    carries the invariants for the hook-channel flavors. The boundary record's
    `context_file` names the work item, and `hx metrics` keeps the field names
    `reads_of_context_file` and `context_file_bytes` for the file the boundary named.
  - **Learnings are global or a role's.** `hx learn accept ID --role R` writes the rule to
    `personas/<role>/<category>.md`; `--global` (or no scope) to `LEARNINGS/<category>.md`.
    Each agent is handed the global files and its own role's, never another role's.
  - **Gone:** `src/hx/compile.py` and `hx compile` (the planned runtime calls
    `hx.prompt_compiler` directly), `config/<id>/AGENTS.md`, `run/<id>/persona.md`,
    `templates/*/AGENTS.md`, `personas/<role>/AGENTS.md`, the persona's `hx:global` and
    learnings regions, the context file's `## Invariants`, and the `## UPDATES BELOW ONLY`
    memory channel (a lesson is a learnings proposal; what an agent did is its step state).
  - A worker is its `config/<id>/harness.json`. `templates/worker/` and
    `templates/contributor/` ship `harness.json` and `SUBAGENTS.md` only. `SUBAGENTS.md` is not
    copied per id: the `SubagentStart` hook renders the shared one with the worker's identity
    into each subagent's context file; a `config/<id>/SUBAGENTS.md` still replaces it for that
    id (`hx doctor` warns `subagents:<id>`).
  - Planned runs read the live sources (docs/prompt-runtime.md § Planned prompt sources): the
    audience files, the `## Domain judgment` section of `personas/<role>.md`, a worker's
    `purpose`, the Companion's `companion/roles/<role>.md` and operator policy, nothing else;
    `continuity/prompts/roles/` and `continuity/prompts/companion-roles/` are gone, their text
    moved into those sections.
  - **Existing instances:** `hx install`, `hx up`, `hx launch` and `hx restart` migrate. Each
    `personas/<role>/AGENTS.md` (and `config/partner/AGENTS.md`) becomes `personas/<role>.md`,
    an edited one leaving the paragraphs the new persona does not say as one text proposal
    scoped to the role; memory below the old header becomes a proposal too. A worker's
    `config/<id>/AGENTS.md` loses every paragraph a shared source already says, its per-id
    paragraph becomes `purpose`, anything else becomes one text proposal, and the file is
    removed; `run/<id>/persona.md` is removed. Rule lines an older hx scoped in place with
    `Role: <role>` move to `personas/<role>/<category>.md`. A `config/<id>/SUBAGENTS.md` that
    is a copy of a template is removed. Decisions: Partner reads `PARTNER.md` whole at every
    boundary (no bounding); the Partner runs `hx board` once after a boundary rather than
    carrying a board in context; resume re-injection is left to Claude Code's own dedup; a
    persona is printed whole with no `{{…}}` expansion.
  - **Measured** (docs/prompt-runtime.md § Measured; the measuring test was throwaway and is not committed):

    | Variant | | Persona | Home `CLAUDE.md` | Boundary Read | `hx cache report` prefix | At a boundary |
    |---|---|---|---|---|---|---|
    | conftest | before | 8,295 (`run/<id>/persona.md`, system prompt) | 2,946 | 3,497 (context file) | 12,200 | 14,738 |
    | conftest | after | 6,558 (`context` hook output) | 3,353 | 1,954 (work item) | 10,719 | 11,865 |
    | template | before | 12,583 | 2,946 | 7,699 | 16,488 | 23,228 |
    | template | after | 6,617 | 3,353 | 1,954 | 10,778 | 11,924 |

    One launched `backend-engineer` worker on a fresh instance with no rules, in bytes. "Before"
    is the tree this work started from; "after" is this one. The conftest variant carries the
    test fixture's `purpose`; the template variant carries a one-line `purpose` plus, before, the
    template's per-id `AGENTS.md` and, after, the template's `explorer` block. "At a boundary" is
    persona + home `CLAUDE.md` + the boundary Read. The hook output is the persona (6,225 bytes for
    `backend-engineer`, up from 4,360 with the definition-of-done standard set), the identity line
    and the pointer; an idle agent's is 120 bytes shorter and it has no boundary Read. The before
    Read was an idle agent's context file, which repeated `config/CLAUDE.md`; the after Read is a
    dispatched work item with a one-line goal. `config/CLAUDE.md` grew by the goal-contract
    paragraphs.
- Goals (CONTRACTS.md "Goals"): a goal is one `## Goal` paragraph — what the result is and why
  — plus, when it depends on other work, one to three sentences saying on what. No
  frontmatter, no text above it, no other heading: `## Definition of done` and `### Checks` in
  a goal file are refused by name, each message saying where it went.
  - **The worker writes its own definition of done.** The work-item template gains
    `## Definition of done` (written before the first edit, on top of the role's standard set),
    `## Orientation` (the explorer's cited starting points) and `## Evidence`. The backend,
    frontend, release and QA personas carry a `## Your definition of done` standard set whose
    first rule is "Orient before you write": one explorer subagent, graph-first and read-only,
    drafts those sections before the first edit.
  - **`hx complete done` runs no checks.** It requires a clean workdir, no open subagent
    stream and a written `## Evidence` (not empty, not the template's note); otherwise
    `HX-CHECK-FAILED` and nothing changes. `blocked`, `decision` and `exhausted` require only
    that no subagent stream is open. Completion repeats (`learn_repeats`) now come from planned
    acceptance checks only; the planned runtime's checks are unchanged.
  - **`hx dispatch` runs nothing in the workdir:** the gate preflight and `HX-GATE-EMPTY` are
    gone.
  - **Addenda are prose.** `hx amend` and `hx resume` append the addendum and nothing else; an
    addendum with a `Checks` heading (outside a fence) is refused, and `hx amend` prints
    `HX-AMEND <id> addenda=<n>` where it printed `checks=replaced|unchanged`.
  - **The explorer.** `harness.json` takes an optional `explorer` block, `{"model", "effort"}`,
    the model validated against `config/models.json`; `templates/worker/harness.json` ships
    `claude-sonnet-5-5` at `high` (the Meta contributor template has none). With one, the
    identity line ends `Your explorer runs on <model> at <effort>.`, and `SUBAGENTS.md` tells a
    subagent spawned as the explorer what it may write.
  - `hx metrics <id>` reports `edits_before_definition_of_done`: workdir edits, main and
    subagent streams, before the work item's definition of done was written; null when there
    is no stream or the write cannot be placed.
  - The scenario packs (`tests/scenario/m8`, `m8b`) and the UI fixtures carry paragraph goals;
    m8b's contradiction now sits inside its one goal paragraph.
  - **Existing instances:** a goal recorded under the old contract still shows and resumes, but
    dispatching its text again is refused until it is one paragraph. An in-flight item has no
    `## Evidence` section; `hx complete done` asks for one and the worker adds it.
  - Decisions: the parser refuses other headings and text above `## Goal` but does not count
    paragraphs or sentences (the Partner's skill and the scenario packs hold that line); a
    `Checks` heading in an addendum is refused rather than silently ignored; `done` requires
    `## Evidence` but not `## Definition of done`, whose timing `edits_before_definition_of_done`
    measures instead; "ready for QA" is `## Deliverables` plus `hx complete done`, with no new
    status.
- The `hx-partner` skill is organised around the four things the Partner reads before it
  decides: **status** (`hx board`, `hx show`, `hx read`, `hx goals`, `hx metrics`, fleet
  memory), the **application map** (`hx map get|plan-context|check`: which responsibility a
  change touches, its interfaces and consumers, who holds a lease), the **feature map** (the
  code graph's `hx code submaps|map|contract|callers|tests-for`, for cutting units one worker
  can finish) and the **assignment ledger** (`hx plan`, `hx loop --status`, leases, receipts).
  Writing a goal is the paragraph rule and nothing else; the sections on writing `### Checks`
  and lint lines are gone (a lint is now a line of the worker's definition of done). It also
  covers the `explorer` block when creating a worker, `HX-AMEND` on resume, and the `done`
  row as clean workdir, no open subagent and `## Evidence` written.

### Fixed

- `tests/ui/test_binding.py` bound `metrics` as "not shipped yet" after `hx metrics` had landed
  (red on `main` since the eng-008 merge); the UI now binds `hx.metrics.collect_fleet` by its
  own name. `tests/core/test_adapter_start_sh.py::test_refuses_without_a_pinned_binary` built
  its claude-less `PATH` from `/usr/bin:/bin`, which on a developer box holds a real `claude`;
  it now builds a `PATH` with everything but `claude`.

- `hx ui` on a host that serves several instances: the token cookie is now named after the
  server's port (`hx_ui_token_<port>`). Browsers scope cookies by host, never by port
  (RFC 6265 §8.5), so with one fixed name the last instance opened overwrote every other
  tab's cookie and they answered 401 until reloaded. An API client that carried the cookie
  by its old name must use `hx_ui_token_<port>` or `Authorization: Bearer`.
- The UI's Agent and Partner pane views now observe the tmux server named by `HX_TMUX`, as
  every other hx command does, instead of a bare `tmux`: with one private tmux server per
  instance the pane was always reported dead. `hx.ui.pane.capture` and `session_alive` take
  an `env` like the rest of the readers.

### Added

- `docs/multi-instance.md` §6: what a handoff carries before another instance or the human acts
  on it — an authority line and capture time, PR heads and merge states from one query made
  after the last push (earlier ones labelled historical, a blocked state with its cause),
  evidence carried from one tree to another only with a recorded empty product diff, skipped
  rows never promoted, open findings closed only by a cited line, and a checksum list over the
  directory. The release-engineer and qa-engineer Companion roles keep the same facts.
- `docs/multi-instance.md`: running a fleet of Partners on one host (private tmux servers via `HX_TMUX`, per-instance credentials and usage limits, go-gate, clean product repository, bound evidence, serialized merges, the four channels between instances and the human session, stable launcher paths instead of versioned binary pins); pointers in the `hx-setup` and `hx-partner` skills, the operating table and the README.

Pre-release. The build is tracked by milestone in `spec/13-build-order.md`; entries land here
as milestones are accepted.

### Added

- `hx amend <id> <addendum-file>` (Partner, `working` or `complete`): appends the addendum as
  `hx resume` does, and when it carries a `### Checks` block, replaces the Checks fence in
  `tasks.json[<id>].goal` and the work item once the amended goal parses; otherwise nothing is
  written. No state change, no paste. Prints `HX-AMEND <id> checks=replaced|kept addenda=<n>`.
  `hx resume` applies the same replacement and prints the same line before `HX-RESUME`.
- Gate preflight in `hx dispatch`: each goal's Checks run in its workdir (120 s budget) before
  anything is written. Exit 0 refuses with `HX-GATE-EMPTY <id>`; a failure prints its exit code
  and first `FAIL` line; a timeout prints `gate preflight: not judged after 120s` and proceeds.
- The Partner's `guard` hook (`hx-hook --id partner guard`, `PreToolUse`), installed by the
  Claude adapter for the Partner only, over `config/partner/guard.json`: denies with exit 2 a
  Bash simple command matching `deny_commands` and no `allow_commands`, and any Bash, Read,
  Edit, Write, Grep or Glob call that names a path under `deny_paths`. Absent or invalid file:
  inactive, logged in `logs/partner/guard.log`.
- Template compiler (`src/hx/compile.py`, `hx compile`): base rules live in two
  Partner-managed sources — `config/CLAUDE.md` (global invariants) and
  `personas/<role>/AGENTS.md` (role) — and are distributed into each worker's final
  `config/<id>/AGENTS.md` as fenced regions on every `hx launch` and `hx restart`,
  preserving the per-id text and the agent's below-header memory. Roles without a
  persona file compile globals only; the Partner is exempt. The Partner may edit
  above-header persona text and `config/CLAUDE.md` freely for efficiency.
- `## Invariants` context-file section (spec 07.3 §0): `config/CLAUDE.md` whole, on
  every stream including subagents — the hooks' live read, so a Partner edit lands at
  the next boundary on every harness flavor. Skills and personas point here instead of
  restating the rules.
- Board scopes: `hx board --json` items carry `scope` (the goal's first content line
  from `tasks.json`, else the work item, capped at 160 chars, `null` when goalless)
  and the text form appends `scope <id>:` lines, so the Partner sees what each stream
  is building without a read per worker.
- Paragraph-goal contract: the Partner writes `## Goal` as one paragraph of what/why
  plus deliverables and stops; the harness decomposes. Goals must be self-sufficient
  (simulate missing data/services in-goal — no blockers by construction) and
  independent work goes out in one `hx dispatch` call, with `waits on` tracked in
  `PARTNER.md`.
- `hx bench` clears `idle` items with outcome `done` and no live session (killed
  sessions left finished work uncleared); live sessions and unfinished items are
  still refused.
- Board `needs_input`: `hx board --json` items carry true while a live pane tail
  matches a known awaiting-human marker, with a needs-input column in the text
  form — the stall nothing else reports.
- Codex adapter (`flavor: codex`): unattended Codex sessions with the Claude hook
  set (SessionStart, PostToolUse, Stop, Pre/PostCompact, SubagentStart/Stop),
  auth provisioned from `seed/codex-token` via codex's own login, home isolated
  through `CODEX_HOME`. The Partner stays on claude.

### Changed

- **`hx code fresh` is in the worker column**, so `hx help` under a worker lists the command
  the goal template's `### Checks` already run; the gate had allowed it as an unlisted
  subcommand. The four role personas carry their granularity and principal-branch rules above
  `## UPDATES BELOW ONLY`, where `hx compile` reads the role persona from; below the header they
  were memory the Partner is told to leave empty, and never reached a system prompt.
- **The packaged skills match the surface.** `hx-worker` gains "Search before you build"
  (the graph queries, `--expect` lints, provenance), the context-economy tools by mode, and the
  planned-run rules (`hx progress`, leases, no memory); `hx-partner` gains the lookups before a
  goal, the three graph lint lines for `### Checks`, the assignment ledger (`hx plan`) and the
  never-twice record; `hx-fleet`, `hx-setup`, `hx-memory` and `hx-companion` describe the
  manifest-driven skill install, the recorded binary, the post-merge graph hook, the operator
  column and the planned Companion pass. `hx restart` re-records the Claude version as
  `hx launch` does.
- **Claude Code versions are recorded, not gated.** `hx install` no longer refuses a
  `claude` missing from `packaging/tested-claude-versions.json`; it records `{bin, version}`
  for whatever it finds and prints `tested` or `untested` beside it. Every launch boundary
  (`hx launch`, `hx restart`, `hx up`) re-records the version the binary reports now
  (`hx.claude_bin.refresh_pin`), so a fleet runs the latest release from its next launch;
  `DISABLE_AUTOUPDATER=1` still keeps a running session on one binary. `hx doctor` warns,
  never fails, when the binary has moved past the record. `--ignore-claude-version` is a
  no-op kept for scripts. The tested list is the record of what the live suite last passed
  on (docs/github-plan.md §4, docs/two-worlds.md, docs/deploy.md, docs/getting-started.md,
  the `hx-setup` skill, CONTRACTS.md).
- **`hx dispatch` refuses a goal whose `### Checks` already exit 0 in the workdir.** An
  existing goal whose gate is `true`, or passes on the untouched tree, must be rewritten to fail
  before the work exists. The 120 s budget is the one timeout in hx.
- A closing fence under `### Checks` must be at least as long as the one it closes, so a
  four-backtick bash block may hold a three-backtick line; before, the block was cut short there.
- `pytest tests` runs the suite: `tests/live/conftest.py` had marked every collected item
  skipped when `HX_LIVE` was unset, and `addopts = "-q"` hid the count.
- The turn-end `stop` hook wakes the Companion batch-gated instead of forced; forced
  wakes belong to `hx flush`, `hx seam`, and the Companion's own `stop` hook.
- `test_the_partner_is_never_seamed` replaced by `test_a_pending_partner_seam_is_acted_on`
  (spec 14 D14: the Partner takes seams; the old test contradicted the shipped behavior
  and failed).

- Episode memory (`src/hx/memory.py`, `docs/memory.md`): every Companion compaction (`pass`),
  seam, native compaction and Digest is kept as a time-stamped episode in an instance-global
  ChromaDB store under `state/memory/`, written by hooks as a queued JSON file and indexed under
  `state/memory/index.lock`. `hx memory search|index|list|stats` searches it recency-weighted,
  filtered to the caller's role by default; `hx compose` adds a **Memory episodes** section after
  **Step state** with the closest same-role episodes of other agents. New `companion` fields
  `memory_inject_k`, `memory_episode_chars`, `memory_half_life_h`; new dependency `chromadb`.
- `hx-memory` skill: read the context file's Memory episodes first, search your own role next,
  widen with `--all-roles` only when that is empty or off-topic.
- Companion activity in the contracts: `hx board --json` items carry `companion_pass` (a pass is
  in flight: `run/<id>/companion/*.pass.md` exists) and `companion_ts` (newest `state/<id>/*.json`
  write), and the board carries a top-level `memory` summary (`episodes`, `queued`, `indexed_ts`)
  read from `state/memory/stats.json` without opening ChromaDB. `hx show <id> --json` (and the
  Partner's reduced shape) carry a `companion` block with the same facts per stream. The SSE
  watcher adds the `memory` scope and watches `run/<id>/companion/`.
- `hx-setup` skill (`src/hx/skills/hx-setup/SKILL.md`): how a coding agent or another harness
  installs hx and brings up an instance — prerequisites, the wheel, `hx install` and its exit-4
  stop for the seed token (the human's step), `hx doctor` verification, Sonnet/medium for a
  trial instance, hand-over to the Partner, `hx up`, upgrading, and what never to do. Shipped in
  the package, never copied into an agent home. `hx-partner` gains "What the human sees" (the
  UI's pages and words) and the `hx show` fields that answer "is the Companion working" and
  "what did it last write"; `docs/operating.md` and `docs/deploy.md` describe the current UI.
- UI: a **Session** page (`#session?agent=<id>`) that the agent drawer opens in another window
  with the pane, step state, context file, stream tails, subagents and metrics.
- `hx show <id> --json` carries `compactions`: per stream, the Companion's last written
  compaction (`state/<id>/<stream>.json`) with its `ts`, `seq` and the text `hx compose` puts in
  front of the master. The UI's **Compaction** page (`#compaction?agent=<id>&stream=<handle>`)
  opens it in another window from the Companion node, the drawer and the Session page, rendered
  for a person and then verbatim. The Partner's reduced shape carries it too.
- `models.json` rows accept `autocompact_window`; `start.sh` exports it as
  `CLAUDE_CODE_AUTO_COMPACT_WINDOW`, validation requires `threshold < autocompact_window <=
  window`, and `hx doctor` reports it. Shipped defaults: 1M window, 250k autocompact, 200k seam.

### Changed

- UI: the agent drawer is now the agent's work item file, rendered section by section in the
  file's order with `## Order` labelled **Goal** and the `hx task` addenda under it, plus a
  Companion status line (pass running on which stream since when, or idle since its last
  write) and the open steps' next actions. The step-state dump, context file, stream tails,
  subagents, metrics and pane moved to the Session page; the composed context's **Memory
  episodes** section is shown there as a count only. The **Orders** view is named **Goals**
  (`/api/orders` and the `order` JSON keys are unchanged); the fleet graph's Companion node
  pulses during a pass and the page header shows the memory store's episode count.

### Fixed

- The Partner is never seamed (spec 12): `seam_is_due` returns false for the Partner, the hard
  threshold in the log hook skips it, and its stop hook drops a stale `run/partner/seam` marker
  instead of running `hx seam partner`, which raised `NotFound` on every stop once the Partner's
  context passed the threshold (18 tracebacks in `logs/partner/hook-errors.log` on a live
  instance).

- Companion output is written for a model, not a person: `companion/BASE.md` and the role files
  ask for telegraphic strings (exact `path:line`, sha7, verbatim commands and error lines, no
  filler) and carry a "pre-answer the master's next tool calls" table; `render_step_state` emits
  one tagged line per fact. The step-state schema and every contract are unchanged.

- Instance skeleton texts (`src/hx/skeleton/`):
  - `companion/BASE.md` and `companion/roles/{partner,backend-engineer,frontend-engineer,release-engineer}.md`
    — the Companion's step-state schema, retention rules, seam policy, digest rules, and
    addendum absorption (spec 10).
  - `personas/{partner,backend-engineer,frontend-engineer,release-engineer}/AGENTS.md` — the
    four personas a Partner copies over `config/<id>/AGENTS.md` when it creates a worker. A
    role is a pair: a persona and the Companion role file of the same name, and `hx launch`
    refuses a `role` with no role file.
  - `templates/work-item.md` (spec 06), plus `templates/order.md` and `templates/addendum.md`
    as worked examples of the two Partner-written file formats.
  - `PARTNER.md`, the Partner's initial memory document.
  - `config/CLAUDE.md`, the one globally loaded instruction file (spec 17.5).
  - `config/partner/{AGENTS.md,SUBAGENTS.md,harness.json}`, the only agent a fresh instance
    installs, plus `templates/worker/{AGENTS.md,SUBAGENTS.md,harness.json}` — the worker
    identity the Partner copies into `config/<id>/` to create an agent (spec 05, 17.2).
- Agent skills, installed into agent homes only and never into `~/.claude/skills` (spec 17.5):
  `hx-partner` and `hx-fleet` into the Partner's home, `hx-worker` into a worker's, and
  `hx-companion` into the Companion's. `hx-fleet` is the Partner's manual for creating,
  changing and retiring HarnessAgents — the default personas, what `hx launch` wires for a
  worker, how to add a role, and how to verify one is really wired rather than assume it.
- `src/hx/packaging/tested-claude-versions.json`, the list `hx install` checks before pinning a
  Claude Code version (spec 17.2 step 1). First entry: `2.1.278`.
- `packaging/e2e-install.sh`: the release check. Builds the wheel, asserts it carries every
  package-data file `hx install` needs, installs it with `uv tool install` into a `HOME` that
  did not exist a moment ago, runs the installed `hx doctor` and `hx install --skeleton-only`,
  asserts every skeleton file landed byte-identical and that a fresh instance holds only
  `partner`, and asserts that neither the fresh Claude home nor the real `~/.claude` was
  touched. Run as a test by `tests/packaging/test_e2e_install.py`.
- `packaging/e2e-deploy.sh`: the deploy proof. Fourteen steps in a `HOME` that did not exist a
  moment ago, with a planted `~/.claude` full of tripwires — the exit-4 seed stop, the token at
  mode 0600 and in no argv, no credentials file in any agent home, no planted string reaching
  the instance, no repository or worker or unit file created by hx, a real `hx launch` into the
  workdir its `harness.json` names, and the operator's own `~/.claude` byte-identical before
  and after. Run as a test by `tests/packaging/test_e2e_deploy.py`.
- `tests/packaging/test_docs_match_reality.py`: builds a real instance and compares
  `docs/deploy.md`'s `$ hx doctor` block — 25 rows — against what `hx doctor` prints, so the
  doc cannot go stale silently. It compares `(status, name)` pairs rather than whole lines,
  because the detail column is paths and versions that differ per machine. It also holds
  `docs/operating.md` to quoting spec 06's `/goal` pointer verbatim, and
  `docs/getting-started.md` to the flags, exit code, versions and wheel name the CLI really
  has.
- `docs/getting-started.md`: a fresh machine to a working Partner, and `docs/operating.md`:
  the day-to-day operator view, every command and every block of output copied from a real
  instance rather than described.
- `tests/scenario/m8/` and `tests/scenario/m8b/`: the two scenario packs M8 runs on, with every
  `hx board` in them regenerated from a real `hx board` rather than hand-written. m8 is the
  scripted `decision`; m8b is the one nobody scripted — a single self-contradicting order with
  deliberately neutral checks, so there is no path that satisfies the checks while dodging the
  question.
- `tests/packaging/test_ci_workflow.py`: validates `.github/workflows/ci.yml` with `actionlint`
  when present, otherwise a minimal structural YAML parser with its own negative tests, then
  asserts what CI must do.
- `docs/deploy.md`, `docs/two-worlds.md`, `docs/github-plan.md`, `docs/companion-eval.md`,
  `README.md`, `LICENSE` (MIT), and this file.
- `tests/packaging/test_skeleton_texts.py`, which checks the shipped texts against the spec
  constraints: order-shaped examples carry `## Order`, `## Definition of done`, and a non-empty
  `### Checks` bash block; the work-item template keeps spec 06's sections, its standing
  instructions, and the placeholders `CONTRACTS.md` pins; every `AGENTS.md` has exactly one
  `## UPDATES BELOW ONLY`; every `SKILL.md` has valid frontmatter; and every shipped role has
  both halves, a persona and a Companion role file of the same name.

### Changed

- **The Companion is a tmux Claude Code session** (M5), not an API client and no longer a
  `claude -p` call: window `<id>:companion`, its own config home at `run/<id>/companion-home`,
  the same pinned binary and the same `seed/token`. It has one hook — its own `Stop`, which
  validates what it wrote and installs it — the `hx-companion` skill, and no CLAUDE.md. hx
  drives it the way it drives every other agent: `/clear`, then one line pointing at a pass
  file. A subscription-only instance gets a Companion with no API key. `companion/BASE.md`
  opens with its output contract — one JSON object, the eleven keys in a table, empty forms
  spelled out, and what happens to a malformed answer — and is explicit that its two tools are
  a rule it keeps rather than a fence it cannot cross: it runs with permissions bypassed like
  everything else here.
- **Auth is one long-lived token per instance**, not a copy of the user's credentials.
  `claude setup-token` once, pasted into `$HARNESS_ROOT/seed/token` at mode 0600, exported as
  `CLAUDE_CODE_OAUTH_TOKEN` by `start.sh`. `--from-user-config` is gone. hx reads nothing from
  `~/.claude` on any platform — not the credentials file, not the macOS Keychain — and agent
  homes hold no credentials file at all.
- `docs/two-worlds.md` is checked against `adapters/claude/install.sh`, `start.sh` and
  `dispatch.py` rather than against the spec they implement, and now carries a table naming,
  claim by claim, the file each one is true in, so a reader who wants to verify the isolation
  story can. The "what is built today" hedge is gone: the install path is built and the deploy
  proof runs it.
- `docs/deploy.md` is the real `hx install` transcript: four numbered steps, the exit-4 stop
  for the seed token and the exact two commands the human runs, an idempotent second run, and
  a healthy `hx doctor`. Keeping hx running is the operator's cron, and the page says so.
- `docs/two-worlds.md` and `README.md` reflect M4: the raw stream written one line per tool
  call, a stream per subagent opened and renamed when it stops, the `agent_id → sNNN` map
  assigned under a lock, `run/<id>/turn` after every turn, and the pane log. The claim table
  gains six rows, including that `context_tokens` is input plus cache reads rather than output
  and that the seam marker is touched only on the main stream.
- The `hx-worker` and `hx-partner` skills carry M4's behaviours as the agent sees them — a
  subagent's task is the message it was spawned with, the digest is all that crosses back, and
  the turn has to end before the harness can deliver a goal or take a seam — each checked
  against the hooks in the tree rather than against the spec.
- `.github/workflows/ci.yml`: the `package` job runs `packaging/e2e-deploy.sh` as well as
  `e2e-install.sh`. The `test` job runs the full suite directly rather than
  `tools/milestone-check.sh`, which now takes a lane name and would otherwise run the guard
  tests and nothing else.

### Removed

Pre-release scope cut (`spec/14-open-items.md` D25, 2026-09-20). The rule behind it: a human
already runs one Partner managing three tmux sessions with nothing, so hx adds only what that
cannot do. None of this shipped in a release; it is recorded because the spec, the skills and
the scenario packs all referred to it.

- **`after` dependency chains, the `queued` state, and promotion.** A Partner that must
  sequence two items dispatches the second when the first's completion wakes it. Work items
  are `pods/<pod>/<id>-<state>.md` with `state ∈ {idle, working, complete}` and no fourth.
- **The Partner as a work item.** No `pods/partner/`, no order file of its own, no
  self-dispatch, no self-completion, no `goal-pending`. The human's message in its tmux session
  is its goal, and it is not a row on `hx board`.
- **Git management.** No `hx repo add`, no `config/repo.json`, no bare mirror, no sparse
  worktrees, no `keep_claude_dir`, no `base_branch`, no `harness.json.branch`, no `hx push`. A
  worker's `workdir` is an absolute directory somebody chose. The only git hx runs is
  `git status --porcelain` inside `hx complete done`, and only when that directory is a
  repository.
- **Shipped launchd plists and systemd units**, and the CI step that verified them. Spec 17.2:
  "hx ships no launchd plist and no systemd unit." `hx up` and `hx heartbeat` are ordinary
  commands for the operator's own cron.
- **`hx upgrade` and its tested-list gate.** Upgrading the package is `uv tool install` again.
- **The `guard` `PreToolUse` hook and its rule list.** Nothing refuses an agent a tool call.
  What an agent may not touch is a rule in its persona, its `CLAUDE.md` and its skill — stated
  as a rule, not as a fence, because a fence that is not there teaches the wrong thing.
- **The enforcement machinery around work items**: the filename regex as a validation gate, the
  transitions table, the nine `hx board` invariants and their error lines, `hx board
  --require-done`, and `hx doctor` policing work items. `hx board` is a listing and exits 0.
- **`run/tasks.lock`**, the flock around `tasks.json`; **`hx bench` patch files and workdir
  resets**; and **`hx dispatch` refusing or resetting a dirty workdir**.
