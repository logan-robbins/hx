# Handoff: orchestrator → build

## 2026-09-20 — answers to your build-1 questions (all five)

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
