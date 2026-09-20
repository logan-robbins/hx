# gtm-4: the deploy proof, docs verified against the adapters, an unscripted scenario

Read `goals/gtm-3.done.md` (yours), `handoff/orchestrator-to-gtm.md` (answers and the plan
change), any other `handoff/*-to-gtm.md`, spec 17.2–17.4, 11, 09.2.

## Build

1. **`packaging/e2e-deploy.sh <scratch>`**: the M10 proof for one machine, on top of
   `e2e-install.sh`: fresh `HOME`, install the wheel, then the full `hx install --root
   <scratch>/hx --from-user-config <scratch>/fake-user-claude` where `fake-user-claude/` is a
   directory you create holding a fake `.credentials.json` and a `settings.json` with the
   bypass acceptance key, standing in for the user's `~/.claude` (never the real one); `hx repo
   add <scratch>/product.git` where `product.git` is a bare repo you make from
   `tests/scenario/m8/repo/` with a `.claude/` directory added to prove the sparse checkout
   excludes it; assert: `seed/home/.credentials.json` copied, `config/claude.json` written with
   a bare version from the tested list, `config/repo.json` written, `repos/product.git` is a
   mirror, `wt/eng-001` (after `hx launch eng-001` with the fake `claude` on `PATH` and
   `HX_TMUX` on a private socket) has no `.claude/`, unit files rendered into the fresh HOME's
   `Library/LaunchAgents` (macOS) or `.config/systemd/user` (Linux) with `{HARNESS_ROOT}` and
   `{HX_BIN}` substituted, and the real `~/.claude` manifest unchanged. Gate: if `hx install`
   without `--skeleton-only` still exits 2, the script prints the gate and exits 0 with
   `SKIPPED (waiting on build-4)`, and the test marks itself skipped with that reason.
2. **Docs verified against the adapters.** Read `src/hx/skeleton/adapters/claude/install.sh` and
   `start.sh` and check every claim in `docs/two-worlds.md` and `docs/deploy.md` about what is
   written, copied, excluded, or launched (settings keys, the `instructionFiles` plugin key, what
   the home wipe removes, the argv, the env). Fix the docs, not the scripts; where the script
   is wrong against the spec, write it to `handoff/gtm-to-build.md`. List each claim checked.
3. **`tests/scenario/m8b/`**: a second, smaller scenario where nothing in the orders mentions a
   decision but `eng-001`'s order is ambiguous in one specific way you document in the README
   (so a correct Partner must reach `decision` on its own); expected boards as in m8; the pack
   test covers it.
4. `CHANGELOG.md` updated with what exists as of today.

## Done when

- `tools/milestone-check.sh` passes for `tests/guard`, `tests/packaging`, `tests/scenario`.
- `packaging/e2e-deploy.sh <scratch>` runs (PASS, or SKIPPED with the build-4 gate named);
  paste its last 15 lines.
- Committed path-scoped. `goals/gtm-4.done.md` written, with handoffs.
