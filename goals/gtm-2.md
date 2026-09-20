# gtm-2: Packaging that installs (spec 17, M10 part 1) and the CI that proves it

Read `goals/gtm-1.done.md` (yours), `goals/build-1.done.md` if it exists yet (the build lane is
still on M0; `hx install --skeleton-only` and `hx doctor` already work from the shared tree),
`handoff/orchestrator-to-gtm.md` (answers to your five open questions), any other
`handoff/*-to-gtm.md`,
spec 17 again. From now on `src/hx/packaging/**` is also yours: the unit templates must ship
inside the wheel so `hx install` can render them; move them there from `packaging/` (keep
`packaging/` for the plan and scripts).

## Build

1. `packaging/e2e-install.sh`: in a scratch directory (argument), create a fresh `HOME`,
   `UV_TOOL_DIR`, `UV_TOOL_BIN_DIR`, `UV_CACHE_DIR` under it; `uv build` the wheel from this
   repo into the scratch dir; `uv tool install --python 3.14 <wheel>`; run
   `hx doctor` and `hx install --root <scratch>/hx --skeleton-only` from the installed tool;
   assert every skeleton file you authored in gtm-1 landed byte-identical; assert the fresh
   `HOME/.claude` does not exist afterwards and the real `~/.claude` manifest is unchanged
   (`tools/claude-home-hash.sh` before/after). Exit non-zero on any failure with the failing
   step named.
2. `tests/packaging/test_e2e_install.py` runs that script (skipped only if `uv` is absent, with
   the reason printed).
3. `tests/packaging/test_units.py`: render each unit template with a sample root and binary
   path (use the same substitution `hx install` will use: `{HARNESS_ROOT}`, `{HX_BIN}`), then
   `plutil -lint` on plists and an INI parse on systemd units; assert the heartbeat interval is
   900 s and both point at `hx up` / `hx heartbeat`.
4. `.github/workflows/ci.yml` finished: macOS and ubuntu jobs; install `tmux` and `uv`; create
   `.venv` the way ORCHESTRATION.md describes; record `.baseline/` from the runner's empty
   `HOME/.claude` before tests; run `tools/milestone-check.sh`; upload the wheel as an artifact.
   Validate the YAML parses (Python `tomllib` will not do; use a minimal check that it is valid
   YAML by structure, or `actionlint` if present, and say which).
5. `docs/deploy.md`, `docs/github-plan.md` (apply the answers in `handoff/orchestrator-to-gtm.md`:
   `spec/` and `tools/` ship, distribution name `hx-harness`), and `README.md` updated with the real commands and their real output from
   step 1 (copy the terminal text; no invented output).
6. Write `handoff/gtm-to-build.md` entries for what `hx install` steps 1, 2, 4, 5, 6 need from
   the package: the unit template paths and substitution keys, the tested-versions file path,
   and the `--from-user-config` seeding contract (which files are copied from the user's
   `~/.claude` into `seed/home`, read-only).

## Done when

- `packaging/e2e-install.sh <scratch>` passes end to end on this machine; paste its last 15
  lines in the done file.
- `tools/milestone-check.sh` passes for `tests/guard` and `tests/packaging`.
- Committed. `goals/gtm-2.done.md` written, with handoffs.
