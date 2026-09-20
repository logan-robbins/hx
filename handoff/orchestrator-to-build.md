# Handoff: orchestrator → build

## 2026-09-20 — answers to your build-1 questions (all five) — DONE 2026-09-20

0. Guard test: fixed on my side (option 2 plus a re-record). `tools/claude-home-hash.sh` now
   prunes `plugins/synced/` and `skills/synced/` (Claude's cloud sync wrote 208 files there
   during your goal) and hashes `known_marketplaces.json` with `lastUpdated` blanked. Baseline
   re-recorded; the test passes. Rerun it.
1. `NOT_IMPLEMENTED` numbering: keep exactly what you did (`build-<milestone+1>`).
2. `hx install` steps: my numbering was wrong, your reading is right (skeleton is 17.2 step 2;
   `--skeleton-only` required until the rest lands).
3. `crossSessionInbound: accept`: Partner home only, as the spec says. Correct.
4. `config/hx.json`: now defined in CONTRACTS.md as `{"hx_bin", "hook_bin"}`, absolute paths,
   with your fallback to `$HARNESS_ROOT/bin/hx` and `bin/hx-hook`. Read `hook_bin` (you already
   do) and also record `hx_bin`.
5. `hx doctor`: your approach is right (fail only on what the current milestone owns, `warn`
   for the rest, tighten to spec 08 as milestones land). One correction: the Python floor is
   3.14, not 3.12 (see the steering message in your pane and pyproject.toml).

**DONE 2026-09-20 (build lane).** All five applied in `goals/build-1`:
0. Guard reran clean: `tests/guard` 5 passed.
1. `hx.cli.NOT_IMPLEMENTED` keeps `build-<milestone+1>`; asserted per command in
   `tests/core/test_cli.py::test_unimplemented_commands_exit_2_with_the_build_goal`.
2. `hx install` requires `--skeleton-only`; plain `hx install` exits 2 naming it.
3. `crossSessionInbound: accept` is written to the Partner home only
   (`tests/core/test_adapter_install_sh.py::test_cross_session_inbound_is_the_partners_alone`).
4. `install.sh` reads `hook_bin` from `config/hx.json` and falls back to
   `$HARNESS_ROOT/bin/hx-hook`; `hx_bin` is read too, and `hx install` will record both when
   17.2 step 1 lands (it is not written in M0, which builds step 2 only).
5. `hx doctor` fails only on python/tmux/git/a broken pinned binary and warns for the rest;
   the Python floor is 3.14 (`hx.doctor.PYTHON_FLOOR`), and `pyproject.toml` agrees.
