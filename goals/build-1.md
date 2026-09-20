# build-1: Milestone M0 (spec 13) plus the two standing guard rules

Read `ORCHESTRATION.md`, `CONTRACTS.md`, then spec sections 03, 04, 05, 06, 08, 09, 11, 13,
17.3–17.4 (`spec/NN-*.md`). You own the paths listed for the build lane.

## Build

1. `src/hx/__main__.py` and `src/hx/cli.py` with `main()`: subcommand dispatch for every
   command in spec 08 (unimplemented ones exit 2 with `hx: <cmd>: not implemented (build-N)`).
   One function resolves `HARNESS_ROOT` (env, else `~/hx`) and refuses, with the word
   `refuse` in the message and exit 1, any root that is or resolves inside the user's
   `~/.claude` (symlinks resolved). Every command calls it first. `hx doctor` prints the
   resolved root, python version, tmux version, the pinned binary from `config/claude.json`
   when present, and exits 0.
2. Layout creation (`hx install` step 3 in 17.2 only: the skeleton under a given
   `HARNESS_ROOT`; steps 1, 2, 4–6 come later). Copy `src/hx/skeleton/**` into the root.
   Put `adapters/claude/install.sh` and `adapters/claude/start.sh` in
   `src/hx/skeleton/adapters/claude/` and `config/models.json` in
   `src/hx/skeleton/config/`. The gtm lane owns `templates/`, `companion/`, `PARTNER.md`,
   `config/CLAUDE.md` under the skeleton and is writing them now; until they land, `hx install`
   tolerates their absence and `hx doctor` reports each missing one.
3. Validators and parsers as one module each: `config/models.json`, `config/<id>/harness.json`
   (spec 05), order files (spec 06: frontmatter `after`, `## Order`, `## Definition of done`
   with non-empty `### Checks` bash block), work-item filenames (the regex in 06), work-item
   frontmatter. Every malformed fixture is rejected with a message naming the file and the rule.
4. `hx board` text form and `--json` exactly per `CONTRACTS.md`, computing every invariant in
   spec 08 ("hx board invariants"); fields that need later milestones (`context_tokens`,
   `seams`, `session_alive`) are computed if the data exists and `null`/`false` otherwise.
5. `adapters/claude/install.sh`: writes `run/<id>/home/settings.json` with the hooks from
   spec 09 (each carrying `--id <id>`), instruction-files mode `claude-md`, `claudeMdExcludes`
   for the repo `CLAUDE.md`/`AGENTS.md`, `crossSessionInbound: accept`, and copies
   credentials plus the bypass-permissions acceptance from `seed/home/` (spec 11 Auth, 17.2).
   Refuses when `seed/home/` lacks credentials. Verify the exact settings keys against
   `code.claude.com/docs/en/settings` and `docs/en/hooks` and record the URLs you checked in
   the done file.
6. `adapters/claude/start.sh`: derives `run/<id>/persona.md` from `config/<id>/AGENTS.md`
   above `## UPDATES BELOW ONLY`, then launches exactly the argv in spec 17.4 in a tmux session
   named `<id>` with env `HARNESS_ID, HARNESS_ROOT, CLAUDE_CONFIG_DIR, DISABLE_AUTOUPDATER=1`
   set on the tmux session, cwd `wt/<id>` (`HARNESS_ROOT` for partner). No prompt argument,
   ever. Refuses a home without settings and credentials.
7. `tests/fakeclaude/claude`: records argv, env, and cwd to `$HARNESS_ROOT/run/<id>/fake-argv.json`,
   then reads stdin in a real tmux pane, appending every pasted line to
   `$HARNESS_ROOT/run/<id>/fake-input.log`, and can emit scripted hook payloads (used from M1).
8. Tests under `tests/` for every M0 pass criterion in spec 13, using pytest `tmp_path` roots and
   a real tmux server started with a private socket (`tmux -L hx-test-<pid>`), killed at teardown.

## Done when

- `tools/milestone-check.sh` passes: `tests/guard` (both files) and your tests.
- `python -m hx doctor` and `python -m hx board --json` work on a scratch root created by
  `python -m hx install --root <scratch> --skeleton-only`.
- Everything committed with explicit paths; `goals/build-1.done.md` written per
  ORCHESTRATION.md "Finishing a goal", including any `handoff/build-to-*.md` entries.
