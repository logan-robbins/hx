## Goal

A decision-complete inventory of the tools and skills the hx fleet has, builds,
or orchestrates, with every capability assigned to the one audience that sees
it, so that capability growth is deliberate and no agent is handed more
surface than its job needs. "Tool" means a deterministic capability exposed
as an `hx` CLI command (bounded JSON, same on every adapter). "Skill" means
packaged judgment: a procedure an agent loads on demand, never a new code
path. This document is the implementation guide for that work; it stands
alone, carries its verdicts on the state of the art inline, and inherits the
code-graph decisions of GRAPH_PLAN.md by reference to its sections. Verified
against primary sources, the hx tree and the host on 2026-10-12.

The one rule that organises everything below: **visibility is budget**. Every
command name, skill description and persona line an agent carries costs
context on every turn, and the small-app live run showed a two-file task
sitting at 33,674 context tokens at its first companion pass, almost all of
it invariant overhead. So each capability is exposed to exactly the audience
that needs it, the worker's surface is the smallest, and anything shown to a
worker must displace something or justify its line.

## Verdicts on the state of the art

| Technique | Primary source and what it says | Verdict for hx |
|---|---|---|
| Agent Skills open standard | agentskills.io/specification: a skill is a directory with `SKILL.md`; frontmatter `name` (≤64 chars, lowercase/digits/hyphens, must match the directory), `description` (≤1,024 chars, what and when), optional `license`, `compatibility` (≤500), `metadata` (string map), `allowed-tools` (experimental); optional `scripts/`, `references/`, `assets/`; progressive disclosure (metadata ≈100 tokens at startup, body <5,000 tokens on activation, resources on demand); keep `SKILL.md` under 500 lines; `skills-ref validate` checks the frontmatter. | **Adopt as the only skill format.** It is already what `src/hx/skills/` ships. hx skills use the six portable fields only, plus `metadata` keys for the hx loader (§Skills). The open question in the previous draft (skeleton prompt files versus skill files) is closed: always-on rules stay in persona/learnings (system prompt, cached); procedures are skills. |
| Claude Code skills | code.claude.com/docs/en/skills: loads from the managed, `~/.claude/skills`, project `.claude/skills`, nested, `--add-dir`, plugin and synced locations; precedence enterprise > personal > project; extra fields `disable-model-invocation`, `user-invocable`, `paths`, `context: fork`, `hooks`, `!cmd` injection; skill listing truncated at 1,536 chars of `description`+`when_to_use`; on compaction the most recent invocation of each skill is re-attached, first 5,000 tokens per skill within a 25,000-token budget; restriction by `Skill(name)` permission rules, `skillOverrides`, `disableBundledSkills`; `allowManagedPermissionRulesOnly` (2.1.282+) ignores `allowed-tools` in personal and project skills. | hx installs skills into `run/<id>/home/skills/` (the personal location of that `CLAUDE_CONFIG_DIR`), which is right. Claude-only fields are **not used**: they do not port to Codex or Pi and `allowed-tools` is ignored under managed rules on the pinned 2.1.296 line anyway. The 1,536-char listing cap and the 5,000-token re-attach bound set the size rules in §Skills. Whether `--setting-sources user` also suppresses a product checkout's `.claude/skills/` is **unverified** on 2.1.296: step 7 adds the doctor check. |
| Codex skills | learn.chatgpt.com/docs/build-skills: loads `$CWD/.agents/skills` up to `$REPO_ROOT/.agents/skills` (repo, needs project trust), `$HOME/.agents/skills` (user), `/etc/codex/skills` (admin), bundled system skills; invoked implicitly by description or explicitly with `$name` / `/skills`; one skill disabled via `[[skills.config]] path=… enabled=false` in `~/.codex/config.toml`; `agents/openai.yaml` `policy.allow_implicit_invocation`. | The adapter copies hx skills into `$CODEX_HOME/skills`, a location the current page does not list. **Verify on the pinned binary** (step 7) that it is scanned; if not, install under a `$HOME`-independent path the binary does scan or deliver through the facade. `$HOME/.agents/skills` is scanned regardless of `CODEX_HOME`, which breaks two-worlds isolation: §Risks. |
| Pi skills | pi-coding-agent docs/skills.md: global `~/.pi/agent/skills/` and `~/.agents/skills/`; project `.pi/skills/`, `.agents/skills/` after trust; settings `skills` array; `--skill <path>` repeatable and additive even with `--no-skills`; `enableSkillCommands`; honours `disable-model-invocation`; names need not match the directory. | The adapter copies into `$PI_CODING_AGENT_DIR/skills`. Launch with `--no-skills` plus one `--skill` per hx skill (step 7): discovery off, hx skills on, `~/.agents/skills` never read. |
| Grok and Muse skills | docs.x.ai/build/cli/reference: `grok inspect [--json]` reports the rules, skills, plugins, hooks and MCP servers discovered for a directory. Muse: `dispatch.py` records that Muse Code keeps its skill index under `home/data/`; no public page reached. | Adapter-tested only. `grok inspect --json` is the verification command for Grok; Muse stays "installed, unverified" until a live listing is recorded. Both adapters remain advisory for native tool schemas (docs/continuity-runtime.md). |
| MCP | modelcontextprotocol.io/specification/versioning: current revision **2026-07-28**; requests carry the version in `_meta`; `server/discover` is a mandatory RPC; servers keep backward compatibility with the handshake revisions 2025-11-25 and earlier. hx's client (`capability_catalog.MCP`) sends `initialize` with `2024-11-05`. | **No MCP server is built** (GRAPH_PLAN §Key Decisions). The handshake client stays valid by the spec's own compatibility clause; `hx tools index` records the version each server answers with and uses `server/discover` only when `initialize` is refused (P2). |
| Code execution with MCP | anthropic.com/engineering/code-execution-with-mcp: tool definitions and intermediate results consume context; present servers as a code API, disclose progressively, process in the sandbox; one example drops 150,000 tokens to 2,000 (98.7%); requires a secure execution environment. | hx already has this shape: `hx exec` for in-environment processing and the `hx tools` shell facade for discovery. The 98.7% is one example, not a benchmark; the local measurement is `hx cache report` and the compaction scenario. |
| Advanced tool use (beta `advanced-tool-use-2025-11-20`) | anthropic.com/engineering/advanced-tool-use: Tool Search Tool with `defer_loading` (upfront context 77K→8.7K tokens, 85%; MCP eval accuracy Opus 4 49→74%, Opus 4.5 79.5→88.1%); Programmatic Tool Calling via `code_execution_20250825` and `allowed_callers` (43,588→27,297 tokens, 37%); `input_examples` on tool definitions (72→90% on complex parameters). | API-level features; CLI harnesses expose only Claude's `--tools` and Pi's extension API, which hx already drives through `hx tools discover/load`. **Adopted:** the examples lesson. Every worker-facing `hx` command gets one example invocation in `--help` and one in its skill (P2). **Not adopted:** native deferral for Codex, Grok, Muse; they stay advisory and no schema saving is claimed. |
| Provider model listings | Anthropic `GET /v1/models` (`lifecycle` filter active/deprecated/retired, `limit` 1–1000, `X-Api-Key` + `anthropic-version`; fields `id`, `display_name`, `lifecycle`, `deprecated_at`, `retires_at`, `line`, `max_input_tokens`, `max_tokens`, `capabilities.effort.{low,medium,high,xhigh,max}`). OpenAI `GET /v1/models` (Bearer; `{object:"list", data:[{id, object, created, owned_by, shutdown_date?}]}`). xAI `GET /v1/language-models` (Bearer; `aliases[]`, `context_length` per the June 2026 changelog, pricing, modalities; official page unreachable, mirrors only). Meta `GET https://api.meta.ai/v1/models` (Bearer `MODEL_API_KEY`, OpenAI-compatible; `muse-wonder-1.0` on the API, `muse-wonder-full-1.0` subscription-only). TypeSafe: no listing endpoint found. | **Build `hx models`** over these four endpoints with stdlib `urllib`, 5 s per provider, no retry. Anthropic's `max_input_tokens` and effort capabilities also validate `config/models.json` windows and `harness.json` efforts. A Claude OAuth seed token does not authenticate the Models API, so that provider reports `unverified` unless `ANTHROPIC_API_KEY` is present. TypeSafe stays hand-pinned and prints `pinned`. |
| `gh` for the sign-off loop | cli.github.com/manual/gh_pr_checks: `--json` fields `bucket,name,state,link,workflow,startedAt,completedAt,description,event`; `bucket` ∈ pass/fail/pending/skipping/cancel; `--required`, `--watch`, `--fail-fast`; exit 8 when checks are pending. | **Orchestrate**, already done by `hx handoff capture`. The sign-off skill uses `gh pr checks --json bucket,name,link --required` and treats exit 8 as pending. `jq` is referenced nowhere in hx (zero matches in `src/` and `docs/`); the previous draft's "already assumed by adapters" is withdrawn. |
| Codebase-memory / RIG / SCIP / tree-sitter | See GRAPH_PLAN.md §Verdicts. | Inherited unchanged. This plan only schedules them (P0 graph completion). |

Host observation (macOS, 2026-10-12): `gh` present; `jq` unused by hx; `tree-sitter`, `universal-ctags`, `scip-*` absent (GRAPH_PLAN). The pinned Claude Code line is 2.1.296.

## Lessons carried from GRAPH_PLAN.md and the fleet runs

1. **Over-exposure is the failure mode, not missing capability.** Lane reads
   consumed 1,312 s of a 1,759 s wall (GRAPH_PLAN §Lessons 2), and the small-app
   run spent most of a worker's first-pass context on invariant overhead
   (docs/compaction-scenario-results.md). → §Inventory assigns one audience per
   command; the worker column is the shortest; `hx help` shows only the caller's
   column.
2. **Agents do not read prose rules.** → Every rule a tool can decide is an
   `--expect` lint with a distinct exit code usable inside `### Checks`
   (GRAPH_PLAN §Success Criteria), and never only a sentence in a skill.
3. **One authority per fact.** Ownership lives in ledger leases; model IDs
   live in `config/models.json`; rules live in `LEARNINGS/`. → `hx models`
   proposes, never rewrites; `hx learn repeat` records, never judges.
4. **Agent-appended layers go unused; derived facts are the product.** →
   Promotion is measured: `hx metrics --commands` counts which `hx` commands
   agents actually run, and the removal audit (§Validation) uses that count,
   not opinion.
5. **Figures need a primary source or a local measurement.** The 98.7% and
   85% figures above are their authors' examples. → Local numbers come from
   `hx cache report` and the compaction scenario before any claim is repeated.
6. **Keep the product repository clean.** Skills, indexes and config never
   enter a product checkout (docs/multi-instance.md §5). → Skills install into
   the agent home only; discovery of checkout-level skill directories is
   disabled or warned about (§Risks).
7. **Mixed adapters mean a shell facade.** → Nothing is proposed as an MCP
   server or a native plugin; skills that must reach an advisory adapter also
   load through `hx tools load skill:NAME`, which bounds them to 8 KiB.
8. **Names collide; check before naming.** `hx bench <id>` already means "free
   an id" (`bench.py`). → The compaction harness is not `hx bench compaction`;
   it is measurement fields in `hx metrics` plus a live runner (§Tools To Build).

## Inventory by audience

Every capability appears once, with the audience that sees it and its
status: **have** (keep, do not rebuild), **build**, **orchestrate** (external
binary, detected at use), or **runtime** (run by hooks, the controller or the
Partner's commands, never typed by an agent). "Sees" means: the command is in
that audience's skill or persona and in its filtered `hx help`; the refusal
layer (§Key Decisions) backs the boundary.

**Operator** (human, or the `hx-setup` agent; no `HARNESS_ID`):
`install`, `up`, `doctor`, `ui`, `heartbeat`, `archive`, `learn check|path|categories`,
`cache assess|budget` (have); `models` (build); `metrics --commands`, `cache report`
(build, also Partner); `tmux`, `git`, `uv`/Python 3.14, SQLite, `gh` (orchestrate).

**Partner** (`HARNESS_ID=partner`):
`launch`, `dispatch`, `goal`, `resume`, `amend`, `bench`, `distill`, `restart`, `read`,
`board`, `show`, `metrics`, `goals`, `recall`, `plan draft|replan|changes|validate|apply|unit|ready|assign|audit|finish|materialize|stop`,
`map init|import|export|overlay|propose|refresh|publish|plan-context|get|check|anchor`,
`learn pending|accept|reject|list`, `tools index`, `compile`, `handoff check|capture|pushed|sums`,
`discipline profile|orient`, `memory search --all-roles|stats|list` (have);
`code contracts --register`, `code submaps`, `code stats`, `code import`, freshness
refusal in `plan validate` (build, GRAPH_PLAN steps 2–8); `models`, `hx help` filtering,
skill manifest (build).

**Worker** (`HARNESS_ID=<pod>-NNN`, `HX_ROLE=agent`); the list a worker is shown, whole:
`task`, `complete`, `progress`, `learn propose|classify|repeat|list KEY`, `memory search`,
`recall` (last resort), `code search|symbols|impact|edges`, `content search|fetch`,
`exec (exec/file/batch)`, `sift`, `tool-exec`, `tools discover|load|call|status`,
`discipline commit`, `evidence` (have); `code type|signature|callers|register|tests-for|conform|precedent|contract|map`
with `--expect`/`--owners` (build, GRAPH_PLAN steps 2–6); `discipline pull` (build).
Fourteen command families, thirty-four subcommands; anything added here must
displace one or carry its own `hx metrics --commands` justification after one release.

**Subagent** (worker's child; delivered by hook, reads only):
`code search|type|signature|callers`, `content search|fetch`, `exec`, `memory search`,
`evidence` (have/build as above). No `complete`, no `learn propose` (the parent
proposes), no `progress`.

**Companion** (Claude session, Read + Write only; planned mode adds the two MCP
tools `read_evidence` and `submit_patch`): no `hx` command at all (have).

**Runtime only** (hooks, the root service, `hx launch`): `capture`, `observe`,
`flush`, `seam`, `compose`, `check`, `companion`, `loop`, `wake` (run by `hx complete`,
`hx heartbeat` and the UI), `compile` on every launch (have); completion-time
`learn repeat` record and `code index --paths` refresh (build).

**Skills** (packaged judgment; Agent Skills format; installed per role by each
adapter's `install.sh` from the package `src/hx/skills/`):

| Skill | Audience | Status | Size today |
|---|---|---|---|
| `hx-partner` | Partner | have | 15.6 KB |
| `hx-fleet` | Partner | have; gains §Model refresh | 8.6 KB |
| `hx-memory` | Partner, worker | have | 5.3 KB |
| `hx-worker` | worker (all roles) | have; gains "search before build" block | 16.4 KB |
| `hx-companion` | Companion | have | 7.1 KB |
| `hx-setup` | operator agent only, never installed into a home | have | 11.9 KB |
| `hx-signoff` | release-engineer, qa-engineer workers | build, ≤8 KiB | — |
| `hx-conversion` | Partner + workers, opt-in per instance | build, ≤8 KiB | — |
| Graph-first | not a skill: two persona lines + `### Checks` lint lines | build (GRAPH_PLAN step 10) | — |
| Model refresh | not a skill: a section of `hx-fleet` | build | — |

Prompt audiences (`continuity/prompts/audiences/{worker,partner,companion,subagent}.md`),
the five role personas with their granularity and dev-pull rules, the learnings
region compiled whole into every persona, `config/CLAUDE.md`, and the base addendum
are **have** and are the always-on layer; nothing in this plan moves them into skills.

Adapters orchestrated today: `claude`, `codex`, `grok`, `meta`, `pi`. To add:
`tree-sitter` CLI + grammars, `scip-*`, `universal-ctags` (GRAPH_PLAN §Approach);
`gh` (already called by `hx handoff`). Each is detected at use time, reported by
`hx doctor`, never vendored.

## Success Criteria

- Every capability an agent is expected to use appears exactly once in
  §Inventory with an audience and a status; a worker's visible command list
  is printed by `hx help` under a worker `HARNESS_ID` and contains no
  Partner or operator command.
- Each build item names its surface (`hx` subcommand or skill), its owner
  (Partner, worker, companion, runtime, operator), its bounds and its
  validation.
- Partner-only commands refuse a worker caller with the existing `refuse:`
  exit code; the refusal set equals the Partner column, verified by one test
  that iterates it.
- No capability is proposed as an MCP server or a native plugin
  (GRAPH_PLAN §Key Decisions); skills reach advisory adapters through
  `hx tools load skill:NAME` and are therefore ≤8 KiB when new.
- Every skill validates against the Agent Skills frontmatter rules (name
  matches directory, ≤64 chars; description ≤1,024 chars; body ≤32 KiB);
  `hx doctor` prints a `skills` row; a product checkout's skill directories
  never load into a worker, and a host `~/.agents/skills` never loads into a
  Codex or Pi worker (§Risks names the one unverified case).
- `hx models` prints a per-provider diff against `config/models.json` and
  `docs/models.md`, reports `unverified` per provider on a missing credential
  or endpoint error, never rewrites config, and supports `--expect current`
  (exit 3 when a configured ID is absent from a provider's active list).
- The compaction scenario yields numbers, not claims: per-seam packet bytes,
  waste re-reads, searches before first edit, recorded in
  docs/compaction-scenario-results.md by a rerunnable live test that forces
  seams with `hx seam`.
- The list stays short by measurement: `hx metrics --commands` counts `hx`
  invocations per command from the capture streams; anything unused by a
  named workflow in two releases is proposed for removal at review.

## Tools To Build

**P0 — exposure enforcement** (owner: runtime; audience: all).
`hx help` prints the caller's column of §Inventory, derived from one table in
`cli.py` (`AUDIENCES: dict[str, set[str]]`), with `hx help --all` for the whole
set; `hx --help` with no `HARNESS_ID` is the operator view. `require_partner_caller`
is applied to the entire Partner column (today it covers `amend`, `bench`,
`dispatch`, `distill`, `goal`, `launch`, `restart`, `read`, `resume`, `tools index`
and the planning/controller paths; missing: `map publish|propose|refresh|import|export`,
`learn accept|reject`, `compile`, `handoff`, `code contracts --register`, `code submaps`,
`models`). A skill manifest `src/hx/skills/manifest.json` (`{name: {audiences: [...],
roles: [...], optional: bool}}`) replaces the hard-coded skill lists in the five
`install.sh` files; `config/hx.json` gains an optional `skills` list to enable
optional skills per instance. `hx doctor` gains a `skills` row: frontmatter
validation per the standard, size bounds, and a grep lint that no worker-audience
skill names a Partner-only command. Validation: `tests/core/test_cli.py` iterates
`AUDIENCES` with `HARNESS_ID=eng-001` and asserts refusal for every Partner and
operator command and acceptance for every worker command; `tests/core/test_doctor.py`
fixture with one malformed skill.

**P0 — graph completion** (owner: worker queries, Partner registry, runtime gate).
GRAPH_PLAN.md steps 1–7 as written there: schema and extractors, the bounded
queries `type`, `signature`, `callers [--count]`, `register`, `tests-for`,
`conform`, `precedent`, `projects`, `map`, `stats` with `--expect` and `--owners`;
the SCIP resolved lane; non-Python map anchors; `hx code import`; the contract
registry; the freshness gate in `hx plan validate` and the completion-time
`hx code index --paths`. Steps 8–10 there (submaps, benchmark gate, rollout)
follow in the same branch. Nothing is restated here; the audiences are in
§Inventory.

**P0 — `hx models`** (owner: operator/Partner; surface: `hx models [--provider P]
[--expect current] [--json]`).
Queries, in order, Anthropic `GET /v1/models?lifecycle=active&limit=1000`
(`X-Api-Key` from `ANTHROPIC_API_KEY` in the env file, never the OAuth seed
token), OpenAI `GET /v1/models` (`OPENAI_API_KEY`, the Codex seed), xAI
`GET /v1/language-models` (`XAI_API_KEY`, the Grok seed), Meta
`GET https://api.meta.ai/v1/models` (`META_API_KEY`, the Meta seed). TypeSafe is
printed from `hx.jev` as `pinned`. Credentials are read through the existing
`envfile`/`seed/` mapping and never printed. Output per provider:
`{status: ok|unverified|error, source, ids, added, removed, deprecated, window: {id: max_input_tokens}, effort: {id: [...]}}`
diffed against `config/models.json` and the ID lists in `docs/models.md`;
`unverified` on a missing credential, `error` with the HTTP status on anything
else, 5 s per provider, no retry, no fallback. It writes nothing. `--expect current`
exits 3 when a configured ID is missing from its provider's active list or a
configured `window` exceeds the provider's `max_input_tokens`; usage errors exit
2 (the convention of `hx wake` and GRAPH_PLAN). Validation: `tests/core/test_models.py`
with a fixture HTTP handler per provider shape, one deprecated ID, one missing
credential, one window mismatch; the live run in step 3 is recorded as a diff
commit, not a test.

**P1 — `hx learn repeat` at completion** (owner: runtime).
When `complete.preflight` or `native_completion.advance_one` sees a failed
check, the first 2,000 bytes of the failing output become the signal for
`learn.repeat`; a `repeat` or `related` verdict appends one line to the
`HX-CHECK-FAILED` output (`learnings: repeat of <Rule> (score)`) and one JSON
record to `state/learn/repeats.jsonl` (`{id, rule, score, ts, check}`). Advisory:
pass/fail stays with the check. `hx metrics` summary gains `learn_repeats` (count
per rule since the last accepted rule). Validation: a `tests/core/test_complete.py`
fixture whose failing check output matches an existing rule asserts the record
and the unchanged verdict; a non-matching failure writes nothing.

**P1 — `hx discipline pull` and post-seam orient** (owner: worker/Partner; both
surface in `hx metrics <id>`).
`hx discipline pull --repo DIR [--expect fresh]`: base chosen as `commit_range`
does (`dev`, `origin/dev`, `main`, `master`, their origins), then `behind` from
`git rev-list --count HEAD..BASE`, `diverged_files` from
`git diff --name-only HEAD...BASE`, `base_age_s` from the merge-base commit date;
read-only git, 30 s timeout as today. `--expect fresh` exits 3 when `behind > 0`,
so a goal's `### Checks` can carry the frequent-pull rule. `hx discipline orient
--post-seam ID` reads the main stream after the last seam record and reports
`searches_before_first_edit`, `modules_read_before_first_write`, `task_reprints`
(`hx task` calls); these join the per-seam block that `hx metrics <id>` already
prints (`reads_of_context_file`, `reads_of_working_set`). Validation:
`tests/core/test_discipline.py` with a two-branch fixture repo, and a stream fixture
with one seam.

**P1 — compaction scenario as numbers** (owner: runtime; no new command).
The runner is `tests/live/test_compaction_scenario.py`, derived from
`tests/live/test_small_app.py` (three lanes, 5k threshold, companions on Haiku),
with two changes: goals large enough to cross 5k, and a forced `hx seam <id>`
mid-task per lane so the measurement exists even when no threshold seam fires
(the first run fired none: packets were 3.8 KB). `hx metrics <id>` gains per seam
`packet_bytes` (from the seam record) alongside the orient fields above; the
runner asserts the pass criteria of docs/compaction-scenario.md (waste re-reads
under 10% of post-seam tool calls, zero searches before first edit for packet-carried
facts, all lanes through checked receipts) and appends the run to
docs/compaction-scenario-results.md. Compaction quality is the system's own
engineering responsibility: companion passes, token controls and packet
construction are owned by hx and measured here; it is deliberately not a fleet
skill, because agents must never have to be good at evaluating compaction for
compaction to be good.

**P1 — `hx metrics --commands`** (owner: operator/Partner).
Counts `hx <command> [<sub>]` invocations per agent and per command from the
Bash/exec tool inputs in the capture streams since `--since TS` (default: the
last dispatch per id), printed as `{command: {calls, agents}}`. This is the
removal audit's evidence and the promotion measure for every build item above.
Validation: a stream fixture with five commands across two ids.

**P2 — `hx cache report`** (owner: operator/Partner; surfaces in `hx metrics`).
Per agent: invariant prefix bytes = `run/<id>/persona.md` + `config/CLAUDE.md`
+ the learnings region + the installed skill descriptions (the listing cost;
bodies are not invariant), against `prompt_cache.invariant_budget` for the
agent's model row; last seam age against the harness `cache_ttl` via
`prompt_cache.assess`. Native tool schemas and host-injected context are
adapter-specific and reported as `unmeasured`, not estimated. Validation:
`tests/core/test_prompt_cache.py` with a fixture instance.

**P2 — examples in `--help`** (owner: runtime).
Every worker-column command's argparse `epilog` carries one example invocation;
`tests/core/test_cli.py` asserts the word `example` in `--help` for each. The
same examples appear in `hx-worker`.

**P2 — MCP version record** (owner: runtime).
`hx tools index` stores the `protocolVersion` each server returns; when a server
refuses `initialize`, the client calls `server/discover` once and reports the
supported versions in the refusal. No other MCP change.

## Skills To Add

Format rules for every hx skill, new or existing: Agent Skills frontmatter with
the portable fields only (`name`, `description`, `license`, `compatibility`,
`metadata`, `allowed-tools`); `metadata` carries `hx-audience`, `hx-roles` and
`hx-loads-when` for the manifest and for a reader; `description` ≤1,024 chars,
front-loaded with the trigger words, because Claude truncates the listing at
1,536 chars and Codex and Pi match on the description alone; body under 500
lines and under 5,000 tokens so Claude's compaction re-attach keeps it whole;
new skills ≤8 KiB so `hx tools load skill:NAME` can carry them to advisory
adapters; no `!cmd` injection, no `context: fork`, no `paths`, no
`disable-model-invocation` (not portable); one worked example per command the
skill names; no Partner-only command inside a worker-audience skill.

- **`hx-signoff`** (release-engineer and qa-engineer roles; loads when a goal
  names push, PR, CI, release or sign-off). The per-unit loop: focused tests →
  `hx discipline commit` → commit → push → `gh pr checks --json bucket,name,link --required`
  (exit 8 is pending; `bucket` fail is a stop) → receipt via `hx handoff capture`
  where a handoff directory exists. The granularity gate's `split` instruction is
  its escalation path. Most of this exists as persona lines today; the skill
  carries the full procedure with commands.
- **`hx-conversion`** (Partner and worker audiences; `optional: true`, enabled
  by `config/hx.json` `skills: ["hx-conversion"]` on conversion instances only).
  How to run a conversion against the dual-purpose graph: index the source repo
  under its own namespace (`hx code index --repo SRC`), publish contracts first
  (`hx code contracts --register`), trace a source capability to its target
  counterpart with `hx code contract NAME`, keep `hx code contract NAME --expect absent`
  in every `### Checks` as the no-second-interface lint, land per-behaviour commits.
  Depends on GRAPH_PLAN steps 5–6.
- **Model refresh** becomes §7 of `hx-fleet`, not a skill: run `hx models --expect current`,
  reconcile `docs/models.md` and the skeleton `config/models.json`, bump tested SDK
  versions only with a live smoke per changed ID. One place, Partner-only.
- **Graph-first** is not a skill: two persona lines (search the graph before
  proposing new code; redirect, do not block) in the worker personas, a
  "search before build" block in `hx-worker` §5 naming `hx code type|signature|callers`
  with one example each, and the lint lines in `templates/goal.md`. Ships with
  GRAPH_PLAN step 10.

Delivery per adapter (verified in the adapters' `install.sh` and the sources
above): Claude → `run/<id>/home/skills/<name>` under `--setting-sources user`;
Codex → `$CODEX_HOME/skills/<name>` (verify on the pinned binary, step 7);
Pi → `$PI_CODING_AGENT_DIR/skills/<name>` with `--no-skills --skill <path>…`;
Grok → `$GROK_HOME/skills/<name>`, checked with `grok inspect --json`;
Muse → `run/<id>/home/muse/skills/<name>`; Companion → `hx-companion` only;
any adapter → `hx tools load skill:NAME` through `config/capabilities.json`.

## External Tools To Orchestrate (never vendor)

- `tree-sitter` CLI + grammars, `universal-ctags`, `scip-*` and
  `rust-analyzer scip` per repo language, exactly as GRAPH_PLAN §Approach
  tiers them; detected per prerequisite, reported by `hx code stats` and
  `hx doctor`.
- `gh` for PR and CI status in `hx-signoff` and `hx handoff capture`;
  absent `gh` makes the skill's CI step report `unverified`, never pass.
- No `jq`: hx reads JSON with its own Python, and no adapter calls it.
- The provider model endpoints above, called only by `hx models`.

## Key Decisions

- **One audience per capability, enforced twice.** Visibility (skill
  manifest, persona, filtered `hx help`) keeps a worker's surface small;
  authority (`require_partner_caller` on the whole Partner column, the
  Companion's two tools, `tool_catalog.CATALOG` per adapter, the guard hook's
  `deny_commands`) makes the boundary hold when visibility fails. A worker that
  needs a Partner action asks through `hx complete blocked|decision`, as today.
- **Always-on versus on-demand.** Rules every turn needs (invariants, persona,
  learnings) stay in the system prompt where the provider caches them;
  procedures with commands are skills, loaded by description match. A skill is
  never the only place a rule lives that a lint could decide (`--expect`).
- **CLI, never MCP or plugin.** GRAPH_PLAN §Key Decisions stands; the facade
  (`hx tools`, `hx tool-exec`, `config/capabilities.json`) is the only path to
  external MCP tools and operator skills, and it is task-scoped.
- **Portable skills only.** The six standard fields; Claude, Codex and Pi
  extensions are not used, so one `SKILL.md` serves five adapters.
- **Measured promotion, measured removal.** `hx metrics --commands` is the
  evidence for both; two releases unused means a removal proposal.
- **`hx models` proposes, never rewrites.** The diff is the output; the
  Partner or operator commits the registry change; a credential or endpoint
  failure is `unverified` per provider, loudly, with no silent omission.
- **Advisory stays advisory.** Codex, Grok and Muse get skills and the facade;
  no native schema reduction is claimed for them (docs/continuity-runtime.md).
- **No index, skill or config in the product checkout.** Skills install into
  the agent home; checkout-level skill directories are disabled where the
  adapter allows and warned about by `hx doctor` where it does not.
- **Exit codes follow one convention.** `--expect` failures exit 3, usage 2,
  success 0, matching `hx wake` and GRAPH_PLAN.

## Steps

1. **Ratify and enforce the inventory.** Add `AUDIENCES` to `cli.py`, the
   filtered `hx help`, the extended `require_partner_caller` coverage, the
   skill manifest consumed by the five `install.sh` files, `config/hx.json`
   `skills`, and the `hx doctor skills` row. Mark anything unwanted as rejected
   here rather than leaving an implicit backlog. Validation: the audience
   iteration test in `tests/core/test_cli.py`; `tests/core/test_adapter_*.py`
   assert the per-role skill set from the manifest; `tests/core/test_doctor.py`
   malformed-skill fixture.
2. **Build the graph P0** with GRAPH_PLAN.md steps 1–7 on branch
   `feat/code-graph-sota`, per-step commits, focused tests green first.
   Validation: GRAPH_PLAN §Validation Plan.
3. **Add `hx models` and `hx-fleet` §Model refresh.** Run it once against the
   live providers with the instance's env file; commit the resulting
   `docs/models.md` and skeleton `config/models.json` diff as the validation,
   with each provider's status line in the commit message.
4. **Wire `hx learn repeat` into completion.** Validation: the matching and
   non-matching fixtures in `tests/core/test_complete.py`; `hx metrics`
   `learn_repeats` present.
5. **Add `hx discipline pull`, post-seam orient, and `hx metrics --commands`.**
   Validation: the fixture repo and stream tests; the worker persona's dev-pull
   line gains `hx discipline pull --expect fresh` as the lint form in
   `templates/goal.md`.
6. **Make the compaction scenario rerunnable.** `packet_bytes` in `hx metrics`;
   `tests/live/test_compaction_scenario.py` with forced `hx seam`; first recorded
   run appended to docs/compaction-scenario-results.md as the baseline. Runs
   after step 2 so the graph and cache work give it something stable to measure.
7. **Land the skills and the isolation fixes.** `hx-signoff`, `hx-conversion`
   (optional), the `hx-worker` search-before-build block, the two persona lines,
   the goal-template lint lines; Pi launches with `--no-skills` plus one `--skill`
   per installed skill; `hx doctor` warns when a worker's workdir contains
   `.claude/skills`, `.agents/skills` or `.pi/skills`, or when the host has a
   non-empty `~/.agents/skills` while a Codex or Pi worker is configured; record
   on the pinned Codex binary whether `$CODEX_HOME/skills` is scanned and on
   Claude 2.1.296 whether `--setting-sources user` suppresses project skills,
   in docs/two-worlds.md. Validation: each skill passes the doctor lint and a
   prompt-compiler or live-fixture test proves it is installed for its roles and
   absent for others.
8. **P2 and review.** `hx cache report`, `--help` examples, the MCP version
   record; then the first removal audit using `hx metrics --commands` after two
   release cycles, in the same review that accepts this plan's next revision.

## Validation Plan

- Each tool: focused tests in the existing per-module files
  (`test_code_index`, `test_learn`, `test_discipline`, `test_prompt_cache`,
  `test_complete`, `test_cli`, `test_doctor`, `test_metrics`), watched failing
  first where behaviour is new; new `test_models.py` with fixture HTTP handlers.
- Exposure: one test iterates `AUDIENCES` under a worker `HARNESS_ID` and
  asserts every Partner and operator command refuses and every worker command
  does not; `hx help` output under each identity equals its column.
- Each skill: frontmatter validation (name/dir match, ≤64; description ≤1,024;
  body ≤32 KiB; new skills ≤8 KiB), installation per role from the manifest
  (adapter tests), loading through the facade for new skills
  (`test_capability_catalog`), and the no-Partner-command grep lint.
- Live, recorded not asserted: `hx models` first run (step 3),
  `grok inspect --json` and the Codex skills listing on the pinned binaries
  (step 7), the compaction baseline (step 6).
- The list itself: after two release cycles, `hx metrics --commands` per
  command and per audience; propose removal for anything unused by a named
  workflow.
- Full suite stays green via `tools/milestone-check.sh`.

## Risks / Open Questions

- **Tool sprawl** is the failure mode this document exists to prevent; the
  worker column is capped at the fourteen families listed and the removal
  audit is not optional.
- **Two-worlds leak through skill discovery.** Codex and Pi scan
  `$HOME/.agents/skills` regardless of `CODEX_HOME`/`PI_CODING_AGENT_DIR`. Pi
  is closed by `--no-skills` + `--skill`. Codex has no blanket disable
  documented (only per-path `[[skills.config]]`); until the pinned binary is
  checked, `hx doctor` warns on a non-empty `~/.agents/skills`, and overriding
  `HOME` for the Codex process is **not** adopted without a live test, because
  auth and sessions resolve through `CODEX_HOME` today and the side effects of
  a foreign `HOME` are unmeasured.
- **Checkout-level skills.** Codex and Pi need project trust, which hx never
  grants; Claude under `--setting-sources user` is unverified for project
  skills on 2.1.296. The doctor warning covers all three until verified.
- **Provider API drift.** `hx models` must fail loudly per provider and diff
  nothing automatically; the xAI endpoint and fields were verified from mirrors
  only, and Meta's listing schema was not read in full, so the first live run
  decides both and the fixture shapes are corrected from it.
- **Credentials.** The Claude OAuth seed cannot call the Models API; instances
  without `ANTHROPIC_API_KEY` see `unverified` for Anthropic, which is the
  honest answer, not a failure.
- **Repeat detection quality.** `learn.repeat` is a similarity score over
  signals; a check's raw output is noisier than a proposed signal, so the record
  is advisory and the threshold is reviewed against `state/learn/repeats.jsonl`
  after the first live wave before anything is counted as a cascade failure.
- **Compaction scenario cost.** It needs live credentials and Haiku
  companions; it runs on demand, never in CI (docs/github-plan.md §3).
- **MCP evolution.** The 2026-07-28 revision keeps handshake compatibility;
  if a registered server drops it, `server/discover` reports the mismatch and
  the server is unregistered rather than the client rewritten mid-release.
