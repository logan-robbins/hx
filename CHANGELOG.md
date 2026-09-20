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
- `tests/packaging/test_docs_match_reality.py`: builds a real instance and compares
  `docs/deploy.md`'s `$ hx doctor` block — 23 rows — against what `hx doctor` prints, so the
  doc cannot go stale silently. It compares `(status, name)` pairs rather than whole lines,
  because the detail column is paths and versions that differ per machine.
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

- **The Companion is a `claude -p` session**, not an API client. `provider: claude-cli` in both
  shipped `harness.json` files: the same pinned binary, the same `seed/token`, a bare config
  home at `run/<id>/companion-home` with no hooks, no skills and no CLAUDE.md, and **no
  tools**. A subscription-only instance gets a Companion with no API key. `companion/BASE.md`
  now opens with its output contract — one JSON object, the eleven keys in a table, empty forms
  spelled out, and what happens to a malformed answer.
- **Auth is one long-lived token per instance**, not a copy of the user's credentials.
  `claude setup-token` once, pasted into `$HARNESS_ROOT/seed/token` at mode 0600, exported as
  `CLAUDE_CODE_OAUTH_TOKEN` by `start.sh`. `--from-user-config` is gone. hx reads nothing from
  `~/.claude` on any platform — not the credentials file, not the macOS Keychain — and agent
  homes hold no credentials file at all.
- `docs/two-worlds.md` is checked against `adapters/claude/install.sh`, `start.sh`, `repo.py`,
  `push.py`, `upgrade.py` and `dispatch.py` rather than against the spec they implement, and
  now carries a table naming, claim by claim, the file each one is true in — nineteen rows, so
  a reader who wants to verify the isolation story can. The "what is built today" hedge is
  gone: the install path is built and the deploy proof runs it.
- `docs/deploy.md` is the real `hx install` transcript, including the exit-4 stop for the seed
  token and the exact two commands the human runs, the real rendered unit paths, the
  `launchctl` / `systemctl --user` lines, a healthy `hx doctor`, and an `hx upgrade` section
  with the real refusal and acceptance output.
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
  `e2e-install.sh`, and on Linux runs `systemd-analyze --user verify` over the rendered units
  — the only place a real systemd ever sees them. The `test` job runs the full suite directly
  rather than `tools/milestone-check.sh`, which now takes a lane name and would otherwise run
  the guard tests and nothing else.
