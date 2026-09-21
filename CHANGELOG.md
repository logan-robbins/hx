# Changelog

All notable changes to hx are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Until 1.0.0 the public surface is the `hx` command set of `spec/08-hx-cli.md`, the instance
layout of `spec/03-layout.md`, and the JSON shapes in `CONTRACTS.md`. A breaking change to any
of those is a minor bump and gets a **Changed** entry saying what an existing instance must do.

## [Unreleased]

Pre-release. The build is tracked by milestone in `spec/13-build-order.md`; entries land here
as milestones are accepted.

### Added

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
