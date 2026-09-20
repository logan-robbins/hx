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

- Instance skeleton texts (`src/hx/skeleton/`):
  - `companion/BASE.md` and `companion/roles/{partner,engineer,reviewer}.md` — the Companion's
    step-state schema, retention rules, seam policy, digest rules, and addendum absorption
    (spec 10).
  - `templates/work-item.md` (spec 06), plus `templates/order.md` and `templates/addendum.md`
    as worked examples of the two Partner-written file formats.
  - `PARTNER.md`, the Partner's initial memory document.
  - `config/CLAUDE.md`, the one globally loaded instruction file (spec 17.5).
  - `config/partner/{AGENTS.md,SUBAGENTS.md,harness.json}`, the only agent a fresh instance
    installs, plus `templates/worker/{AGENTS.md,SUBAGENTS.md,harness.json}` — the worker
    identity the Partner copies into `config/<id>/` to create an agent (spec 05, 17.2).
- Agent skills `src/hx/skills/hx-partner/SKILL.md` and `src/hx/skills/hx-worker/SKILL.md`,
  installed into agent homes only (spec 17.5).
- Boot and heartbeat units in `src/hx/packaging/`, shipped inside the wheel so `hx install` can
  render them with no checkout present: launchd plists for macOS, systemd user units and a
  900 s timer for Linux, templated on `{HARNESS_ROOT}` and `{HX_BIN}` (spec 17.2 step 5).
- `src/hx/packaging/tested-claude-versions.json`, the list `hx install` checks and `hx upgrade`
  consults before pinning a new Claude Code version (spec 17.2 step 1, 17.6). First entry:
  `2.1.278`.
- `packaging/e2e-install.sh`: the release check. Builds the wheel, asserts it carries every
  package-data file `hx install` needs, installs it with `uv tool install` into a `HOME` that
  did not exist a moment ago, runs the installed `hx doctor` and `hx install --skeleton-only`,
  asserts every skeleton file landed byte-identical and that a fresh instance holds only
  `partner`, and asserts that neither the fresh Claude home nor the real `~/.claude` was
  touched. Run as a test by `tests/packaging/test_e2e_install.py`.
- `tests/packaging/test_units.py`: renders each unit template the way `hx install` will, then
  lints the rendered output — `plutil -lint` where it exists, `plistlib` everywhere, an INI
  parse for systemd — and checks both platforms agree on the 900 s heartbeat and cover exactly
  `hx up` and `hx heartbeat`.
- `tests/packaging/test_ci_workflow.py`: validates `.github/workflows/ci.yml` with `actionlint`
  when present, otherwise a minimal structural YAML parser with its own negative tests, then
  asserts what CI must do.
- `docs/deploy.md`, `docs/two-worlds.md`, `docs/github-plan.md`, `README.md`, `LICENSE` (MIT),
  and this file.
- `tests/packaging/test_skeleton_texts.py`, which checks the shipped texts against the spec
  constraints: order-shaped examples carry `## Order`, `## Definition of done`, and a non-empty
  `### Checks` bash block; the work-item template keeps spec 06's sections, its standing
  instructions, and the five placeholders `CONTRACTS.md` pins; every `AGENTS.md` has exactly
  one `## UPDATES BELOW ONLY`; both `SKILL.md` files have valid frontmatter.

### Changed

- `docs/two-worlds.md` is now checked against `adapters/claude/install.sh` and `start.sh`
  rather than against the spec they implement: the settings file is documented key by key
  (`skipDangerousModePermissionPrompt`, `pluginConfigs["agents-md@builtin"].options.instructionFiles`,
  the seven `claudeMdExcludes` globs, `crossSessionInbound` for the Partner alone), the
  credential seeding says which single file is taken and names both refusals that stop a home
  without credentials from launching, the launch argv is quoted in full, and the pane log is
  described. A new section says plainly which parts are built today and which are build-4.
- `docs/deploy.md`: `--from-user-config` takes a **path**, and the doc says what it copies out
  of it — `.credentials.json`, and nothing else.
- `tests/scenario/m8/`: the M8 scenario pack (spec 13) — `chat.md` turn by turn, the Partner's
  own order and the two workers' orders with a real `after` chain, the addendum that answers
  the `decision`, both worker personas, the eight `expected/` board states, and a fixture repo
  with two tripwires that must never load in a harness session. `tests/scenario/test_m8_pack.py`
  parses every order with the function `hx dispatch` uses, checks the `after` graph is acyclic,
  and compares each expected board against what the real `hx board` prints for an instance
  built in that state.
- `tests/scenario/m8b/`: a second, smaller scenario where **nothing mentions a decision**.
  One order contradicts itself in a single documented way — `## Order` demands JSON and nothing
  else on stdout, criterion 3 of `## Definition of done` demands the human-readable line first
  — and its `### Checks` are deliberately neutral between the two readings, so no path
  satisfies the checks while dodging the question. `tests/scenario/test_m8b_pack.py` asserts
  the contradiction is still present in both halves, that the checks resolve neither, and that
  nothing the worker can see names the `decision` outcome.
- `tests/scenario/packlib.py`: the shared pack machinery — building an instance in a given
  observation state and reading the real `hx board` from it — so the two packs cannot drift.
- `packaging/e2e-deploy.sh`: the M10 deploy proof for one machine. Fresh `HOME`, the full
  `hx install --from-user-config <fake user Claude home>`, `hx repo add` against a bare product
  repo with its own `.claude/`, `hx launch` with the fake `claude` on a private tmux server,
  and the boot units rendered into that HOME's launchd or systemd directory. It asserts that
  only the credentials were taken from the fake user home, that the home is byte-identical
  afterwards, that the worktree has no `.claude/`, that no rendered unit holds an
  unsubstituted token, and that the real `~/.claude` is unchanged. While the full `hx install`
  is build-4 it prints its gate and exits 0 with `SKIPPED (waiting on build-4)`, and
  `tests/packaging/test_e2e_deploy.py` skips with that reason rather than passing silently.
- `docs/companion-eval.md`: the M7 plan — the recorded-log corpus and where it comes from, five
  seam points per task, the two metrics `hx metrics` records, the pass bar, and how a prompt
  change to `companion/BASE.md` or a role file is judged before and after on the same corpus.
- `.github/workflows/ci.yml`: job `test` on macOS and Linux with tmux, uv and Python 3.14,
  recording `.baseline/` from the runner's own empty `HOME/.claude` before any test runs and
  re-diffing it afterwards; job `package` running `packaging/e2e-install.sh` and uploading the
  wheel as an artifact. No secrets, `permissions: contents: read`.

[Unreleased]: https://github.com/autodev-team/hx/commits/main
