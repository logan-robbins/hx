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
- Boot and heartbeat units in `packaging/`: launchd plists for macOS, systemd user units and a
  900 s timer for Linux, templated on `@HARNESS_ROOT@` and `@HX_BIN@` (spec 17.2).
- `packaging/tested-claude-versions.json`, the list `hx upgrade` consults before pinning a new
  Claude Code version (spec 17.6). First entry: `2.1.278`.
- `docs/deploy.md`, `docs/two-worlds.md`, `docs/github-plan.md`, `README.md`, `LICENSE` (MIT),
  and this file.
- `tests/packaging/test_skeleton_texts.py`, which checks the shipped texts against the spec
  constraints: order-shaped examples carry `## Order`, `## Definition of done`, and a non-empty
  `### Checks` bash block; every `AGENTS.md` has exactly one `## UPDATES BELOW ONLY`; both
  `SKILL.md` files have valid frontmatter; the launchd plists and systemd units parse; the
  tested-versions list parses.
- `.github/workflows/ci.yml`: pytest on macOS and Linux with tmux installed, recording a
  `.baseline/` in CI from a fresh `HOME` so the guard tests run there.

[Unreleased]: https://github.com/autodev-team/hx/commits/main
