# gtm-1: Skills, prompt and template texts, packaging units, docs, and the GitHub plan

Read `ORCHESTRATION.md`, `CONTRACTS.md`, then the whole spec `spec/HARNESS_SPEC.md` with
special care for 03, 05, 06, 07, 09, 10, 12, 14 ("to author" list), 17. You own the gtm paths
in ORCHESTRATION.md. The build lane is building the CLI in parallel; nothing you write in this
goal depends on it running.

The prior product is `/Users/loganrobbins/workspace/autodev/` (never read `website/`); its
skills `src/autodev/skills/autodev-gm` and `autodev-operator` and its prompt texts are
material to mine, not to keep (spec 17.5, AUTODEV-COMPARISON.md). Everything you write must
match the spec's mechanisms exactly: the human never runs hx and only talks to the Partner;
orders, addenda, personas, goals are files; the Partner dispatches itself; workers finish with
`hx complete <outcome>` and react to `HX-CHECK-FAILED`; no timeouts; every agent runs bypass.

## Author (all under `src/hx/skeleton/` unless noted)

1. `companion/BASE.md` and `companion/roles/{partner,engineer,reviewer}.md` per spec 10: the
   step-state schema, retention rules per role, the seam marker rule, the Digest rule with
   blocker-or-question-first for `blocked`/`decision`, the addendum absorption on resume.
2. `templates/work-item.md` exactly as spec 06 defines (frontmatter, `## Order` verbatim slot,
   standing instructions, `## Tasks`, `## Deliverables`, `## Commands`, `## Open decision`,
   `## Digest`), and `templates/order.md`, `templates/addendum.md` examples that pass spec 06.
3. `PARTNER.md` initial state doc; `config/CLAUDE.md` (the one CLAUDE.md, truly global);
   `config/partner/AGENTS.md` and `config/partner/SUBAGENTS.md`; an example worker
   `config/eng-001/{AGENTS.md,SUBAGENTS.md,harness.json}` with the `## UPDATES BELOW ONLY`
   header; the Partner's `harness.json` per spec 05.
4. `src/hx/skills/hx-partner/SKILL.md` and `src/hx/skills/hx-worker/SKILL.md` (spec 17.5,
   12, 06, 08): the Partner loop of spec 12 step by step, with the exact commands and files;
   the worker's lifecycle from the `/goal` pointer to `HX-COMPLETE`. Follow the current
   skill format from `code.claude.com/docs/en/skills` (frontmatter `name`, `description`) and
   note the URL in the done file.
5. `packaging/launchd/com.hx.up.plist`, `packaging/launchd/com.hx.heartbeat.plist`,
   `packaging/systemd/hx-up.service`, `packaging/systemd/hx-heartbeat.timer` + `.service`
   (heartbeat every 900 s), templated on `HARNESS_ROOT` and the hx binary path, per 17.2.
6. `packaging/tested-claude-versions.json`: `{"versions": ["<claude --version in your
   session>"]}` as the first entry of the list `hx upgrade` consults (17.6).
7. `docs/deploy.md` (the human's one-time setup from 17.2, then "talk to the Partner"),
   `docs/two-worlds.md` (17.3 table in prose, for the user asking how their own Claude stays
   untouched), `README.md`, `LICENSE` (MIT, copyright "autodev-team"), `CHANGELOG.md`.
8. `docs/github-plan.md`: the productization plan for publishing this repo: repository name and
   layout, release process (wheel via `uv build`, `uv tool install hx`), version pinning policy
   for Claude Code, the CI workflow you add at `.github/workflows/ci.yml` (pytest on macOS and
   Linux with tmux installed; the guard tests use a `.baseline/` recorded in CI from a fresh
   HOME), and what stays private. Do not create any remote or GitHub repository; this is a plan.

## Done when

- Every file above exists and each markdown file that the spec constrains has been checked line
  by line against its spec section; list the section per file in the done file.
- `tests/packaging/test_skeleton_texts.py` (yours): every `templates/order.md`-style example
  has `## Order`, `## Definition of done`, `### Checks` with a non-empty bash block; every
  `AGENTS.md` has exactly one `## UPDATES BELOW ONLY`; both SKILL.md files have valid
  frontmatter; the plist and unit files parse (`plutil -lint`, and a systemd-unit syntax check
  by parsing INI sections); tested-versions.json parses.
- `tools/milestone-check.sh` passes for `tests/guard` and `tests/packaging` (other lanes may
  be incomplete; report, do not fix).
- Everything committed with explicit paths; `goals/gtm-1.done.md` written per
  ORCHESTRATION.md, including any `handoff/gtm-to-*.md` entries.
