# Changelog

All notable changes to hx are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Until 1.0.0 the public surface is the `hx` command set of `spec/08-hx-cli.md`, the instance
layout of `spec/03-layout.md`, and the JSON shapes in `CONTRACTS.md`. A breaking change to any
of those is a minor bump and gets a **Changed** entry saying what an existing instance must do.

## [Unreleased]

### Added

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

- Prompt layers (docs/prompt-runtime.md § Prompt layers): every agent's prompt is four layers,
  each from one source — the invariants (`config/CLAUDE.md`), the persona, the learnings and
  session memory — and nothing per id is copied from a shared source.
  - The invariants reach each agent through exactly one channel
    (`config_harness.INVARIANTS_CHANNEL`): Claude's home `CLAUDE.md`, refreshed by
    `start.sh --exec` at every launch; the persona's `hx:global` region on Pi and Grok; the
    context file's `## Invariants` on Codex and Meta.
  - The persona is rendered, not compiled into a per-id file: every `start.sh --exec` runs
    `python -m hx.persona` (`hx persona ID [--print]`) immediately before it execs, from
    `personas/<role>/AGENTS.md`, the new `harness.json` `purpose` (the one per-id paragraph;
    the templates ship `"{{purpose}}"` and `hx doctor` warns `purpose:<id>` until it is
    written) and the learnings. `config/<id>/AGENTS.md` is no longer read, written or
    required for a worker. **Existing instances:** `hx install`, `hx up`, `hx launch` and
    `hx restart` migrate each worker's file — its per-id paragraph becomes `purpose`, anything
    else no shared source already says becomes one pending learnings text proposal
    (`hx learn pending`), and the file is removed. Plain `hx compile ID` is gone (exit 2,
    pointing at `hx persona`); `hx compile ID --planned` stays. `hx distill` turns promoted
    directives into a text proposal instead of writing them into the agent's file, and its
    `--memory` option is gone. `hx learn accept --keep` turns a text proposal holding several
    lessons into several rules.
  - Learnings are global or scoped to one role: a scoped rule carries `Role: <role>` after its
    key, `hx learn propose --role R` proposes one, `hx learn accept ID --role R | --global`
    decides it, `hx learn list` prints the scope (`--role R|global` filters), and the output
    lines and `decisions.jsonl` carry `scope`. Each persona's learnings region is the global
    rules, then its role's (`### <role> · <category>`); other roles' rules are not in it. A
    migrated or distilled text proposal is scoped to its agent's role, so what one worker
    learned reaches every worker of its role. `hx doctor` bounds `learnings-size` by the
    largest region any configured role renders. Existing rules have no `Role:` and stay global.
  - The `## UPDATES BELOW ONLY` memory channel is gone. No agent keeps a memory file: a lesson
    is a learnings proposal (scoped to the agent's role), and what an agent did is its step
    state and the episode store. `hx compose` has no `## Memory` section (subagent streams
    keep `## Who your subagents are`); the shipped personas, `config/CLAUDE.md`, the skills and
    the docs no longer tell anyone to write below a header. **Existing instances:** a
    `config/partner/AGENTS.md` with the old header keeps its persona and loses the header; what
    was below it becomes a text proposal scoped to `partner` (authored as the Partner, so the
    human decides it). An installed role persona that still ends at the header is read up to
    it (`hx.persona.LEGACY_HEADER`). The scenario packs ship each worker as a `harness.json`
    with its `purpose` instead of a per-id `AGENTS.md`.
  - A worker is its `config/<id>/harness.json`. `templates/worker/` and
    `templates/contributor/` ship `harness.json` and `SUBAGENTS.md` only; the template
    `AGENTS.md` is gone. `SUBAGENTS.md` is no longer copied per id: the `SubagentStart` hook
    renders the shared `templates/worker/SUBAGENTS.md` (`templates/contributor/` for a Meta
    worker) with the worker's identity into each subagent's context file
    (`hx.persona.render_subagents`), and the Pi extension renders the same text. A
    `config/<id>/SUBAGENTS.md` still replaces it for that one id, and `hx doctor` warns
    `subagents:<id>` while one exists. **Existing instances:** the launch migration removes a
    `config/<id>/SUBAGENTS.md` that is a copy of a template (identity filled in or not,
    comments ignored) and keeps one that says something else.
  - Planned runs read the live sources (docs/prompt-runtime.md § Planned prompt sources):
    `hx compile ID --planned` reads `continuity/prompts/common.md`, the audience's
    `continuity/prompts/audiences/<audience>.md`, the `## Domain judgment` section of the
    persona (`personas/<role>/AGENTS.md`; the Partner's `config/partner/AGENTS.md`) for a
    worker, the Partner and a subagent, a worker's `harness.json` `purpose`, the Companion's
    `companion/roles/<role>.md`, and operator policy (`continuity/policy.md`,
    `continuity/policy/<audience>.md`), nothing else. The rest of the persona is the live
    session's mechanics (tmux, `/goal`, `## Tasks`), which the audience file replaces; compiling
    it in would also have pushed a planned worker's system prompt past the native launch's
    8000-byte initial budget (`hx.native_launch`), so a shipped role's planned prompt carries the
    same role text it did, plus its `purpose`. `continuity/prompts/roles/` and
    `continuity/prompts/companion-roles/` are gone: every sentence moved verbatim into a
    `## Domain judgment` section of `personas/<role>/AGENTS.md` (and `config/partner/AGENTS.md`)
    and a `## Retention judgment` section of `companion/roles/<role>.md`, so live workers and
    Companions read them too. Two persona lines the moved text contradicts changed: the backend
    persona's fixed migration, model, handler, client order is gone ("there is no universal
    order"), and the release persona's `Never run \`git push\`` reads "Push or publish only
    what the goal names in words". The `generic.md` fallbacks are gone: a role without a
    persona gets no domain section (its identity section names the role; `common.md` already
    keeps a missing capability explicit), and a Companion without a role file is refused, as
    live. `config/CLAUDE.md` and per-id `AGENTS.md` text are no longer compiled in, so a
    bundle has no `migration_review` and `hx.compile.strip_known_blocks` is gone; a rule for
    planned runs belongs in `continuity/policy.md`.

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
