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
- `tests/scenario/m8/`: the M8 scenario pack (spec 13) — `chat.md` turn by turn, the Partner's
  own order and the two workers' orders with a real `after` chain, the addendum that answers
  the `decision`, both worker personas, the eight `expected/` board states, and a fixture repo
  with two tripwires that must never load in a harness session. `tests/scenario/test_m8_pack.py`
  parses every order with the function `hx dispatch` uses, checks the `after` graph is acyclic,
  and compares each expected board against what the real `hx board` prints for an instance
  built in that state.
- `docs/companion-eval.md`: the M7 plan — the recorded-log corpus and where it comes from, five
  seam points per task, the two metrics `hx metrics` records, the pass bar, and how a prompt
  change to `companion/BASE.md` or a role file is judged before and after on the same corpus.
- `.github/workflows/ci.yml`: job `test` on macOS and Linux with tmux, uv and Python 3.14,
  recording `.baseline/` from the runner's own empty `HOME/.claude` before any test runs and
  re-diffing it afterwards; job `package` running `packaging/e2e-install.sh` and uploading the
  wheel as an artifact. No secrets, `permissions: contents: read`.

[Unreleased]: https://github.com/autodev-team/hx/commits/main
